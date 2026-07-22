"""Portable ffmpeg encoder + fps helpers — dependency-free (stdlib only).

The rest of the pipeline used to hardwire NVIDIA: `-hwaccel cuda` on every input
and h264_nvenc / hevc_nvenc on every output, with a hardcoded 60000/1001 frame
rate for the drift re-stamp. That breaks anyone on Mac / AMD / Intel, or shooting
at 24 / 25 / 30 fps. This module makes the encode choices machine-aware while
keeping the *intent* of the old settings byte-for-byte on an NVIDIA box.

What it does:
  * probe `ffmpeg -encoders` ONCE (cached) to see what this machine can do;
  * pick an encoder off a preference ladder per codec family:
        H.264 : h264_nvenc > h264_videotoolbox > libx264
        HEVC  : hevc_nvenc > hevc_videotoolbox > libx265 > libx264 (loud 8-bit warn)
  * only emit `-hwaccel cuda` input args when an nvenc encoder was actually chosen;
  * translate one quality intent (visually-lossless-ish master, fast small preview)
    into the right knobs for each encoder family (nvenc p4/p5 + -cq, x264/x265
    -preset + -crf, videotoolbox -q:v), keeping 10-bit main10 only where the chosen
    encoder supports it (drops to 8-bit with a warning otherwise);
  * honor CYE_ENCODER=<name> to force a specific encoder for BOTH ladders.

Also exposes probe_fps(path) -> exact fraction string ("60000/1001", "30/1") and
fps_float(path) -> float, both from ffprobe's r_frame_rate.

Python 3.9-compatible on purpose (the sandbox that exercises the fallback path runs
3.9); no match statements, `from __future__ import annotations` for modern hints.
"""

from __future__ import annotations

import os
import subprocess
import sys
from fractions import Fraction
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

# ---- encoder ladders (most-preferred first) --------------------------------
# H.264 for previews/proxies; HEVC for the 10-bit master. libx264 is the final
# HEVC fallback (8-bit) so the pipeline still delivers *something* on a box with
# neither nvenc, videotoolbox, nor libx265.
H264_LADDER = ("h264_nvenc", "h264_videotoolbox", "libx264")
HEVC_LADDER = ("hevc_nvenc", "hevc_videotoolbox", "libx265", "libx264")

# families we know how to drive; anything a user forces via CYE_ENCODER that we
# don't recognize is driven with a conservative generic mapping.
_NVENC = ("h264_nvenc", "hevc_nvenc")
_VIDEOTOOLBOX = ("h264_videotoolbox", "hevc_videotoolbox")
_X26X = ("libx264", "libx265")


def _warn(msg: str) -> None:
    print("[encoders] " + msg, file=sys.stderr)


@lru_cache(maxsize=1)
def available_encoders() -> frozenset:
    """Set of encoder names ffmpeg reports (probed once, then cached).

    Parses `ffmpeg -hide_banner -encoders`; each encoder row looks like
    ` V....D h264_nvenc  NVIDIA NVENC H.264 encoder`. We take column 2 of every
    row after the `------` separator. Returns an empty set (never raises) if
    ffmpeg can't be run, so callers degrade instead of crashing.
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:  # pragma: no cover
        _warn("could not probe `ffmpeg -encoders` (%s); assuming none available" % e)
        return frozenset()
    names = set()
    seen_sep = False
    for line in r.stdout.splitlines():
        if not seen_sep:
            if set(line.strip()) == {"-"}:  # the ` ------` separator row
                seen_sep = True
            continue
        parts = line.split()
        # rows are: <flags> <name> <description...>; flags start with V/A/S
        if len(parts) >= 2 and parts[0] and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def _env_override() -> Optional[str]:
    v = os.environ.get("CYE_ENCODER", "").strip()
    return v or None


def _pick(ladder: Tuple[str, ...], have: frozenset) -> str:
    forced = _env_override()
    if forced:
        if forced in have:
            return forced
        _warn(
            "CYE_ENCODER=%s not available on this machine (%d encoders detected); "
            "falling back to the ladder %s"
            % (forced, len(have), "/".join(ladder))
        )
    for enc in ladder:
        if enc in have:
            return enc
    # nothing on the ladder exists — return the last (software) rung and let
    # ffmpeg surface the real error; better than picking something bogus.
    _warn("none of %s available; using %s and hoping" % ("/".join(ladder), ladder[-1]))
    return ladder[-1]


def _family(enc: str) -> str:
    if enc in _NVENC:
        return "nvenc"
    if enc in _VIDEOTOOLBOX:
        return "videotoolbox"
    if enc in _X26X:
        return "x26x"
    return "generic"


# ---- quality intents -------------------------------------------------------
# Two intents, matching the two old ENC entries in render_cuts.py:
#   "preview"  -> old h264_nvenc p4 -cq 30  (fast, small, 720p)
#   "final"    -> old hevc_nvenc p5 -cq 19 main10 p010le (visually-lossless master)
#   "proxy"    -> old h264_nvenc p4 -cq 29  (make_proxy.py; 720p editor proxy)
# CRF numbers are picked so software output lands at roughly the same perceptual
# quality as the nvenc -cq the author tuned (nvenc CQ and x264/5 CRF are NOT the
# same scale; these are the widely-used rough equivalents, erring toward quality).
_INTENT = {
    "preview": {"nvenc_preset": "p4", "cq": "30", "crf_264": "23", "crf_265": "28",
                "sw_preset": "veryfast", "vt_q": "55", "ten_bit": False},
    "proxy":   {"nvenc_preset": "p4", "cq": "29", "crf_264": "23", "crf_265": "28",
                "sw_preset": "veryfast", "vt_q": "55", "ten_bit": False},
    "final":   {"nvenc_preset": "p5", "cq": "19", "crf_264": "16", "crf_265": "18",
                "sw_preset": "medium", "vt_q": "45", "ten_bit": True},
}


def _video_quality_args(enc: str, intent: str, want_10bit: bool) -> Tuple[List[str], bool]:
    """Encoder-specific quality knobs. Returns (args, got_10bit).

    `want_10bit` asks for main10; we honor it only where the chosen encoder can,
    and warn + fall back to 8-bit otherwise. `got_10bit` tells the caller whether
    the pixel format ended up 10-bit (it uses that to pick p010le vs yuv420p, and
    to warn when a 10-bit master silently became 8-bit)."""
    spec = _INTENT[intent]
    fam = _family(enc)
    ten = bool(want_10bit and spec.get("ten_bit"))
    args: List[str] = ["-c:v", enc]

    if fam == "nvenc":
        args += ["-preset", spec["nvenc_preset"], "-rc", "vbr", "-cq", spec["cq"], "-b:v", "0"]
        if enc == "hevc_nvenc" and ten:
            args += ["-profile:v", "main10", "-pix_fmt", "p010le"]
            return args, True
        return args, False

    if fam == "videotoolbox":
        # videotoolbox has no CRF; -q:v 0..100 (higher = better). No CQ/VBR knobs.
        args += ["-q:v", spec["vt_q"]]
        if enc == "hevc_videotoolbox" and ten:
            # Apple HEVC supports 10-bit via main10 profile + p010le.
            args += ["-profile:v", "main10", "-pix_fmt", "p010le"]
            return args, True
        return args, False

    if fam == "x26x":
        crf = spec["crf_265"] if enc == "libx265" else spec["crf_264"]
        args += ["-preset", spec["sw_preset"], "-crf", crf]
        if enc == "libx265" and ten:
            args += ["-pix_fmt", "yuv420p10le"]
            return args, True
        if enc == "libx264" and ten:
            # libx264 CAN do 10-bit, but only if the build's libx264 is compiled
            # for it AND we're here as the HEVC fallback — in that case we've
            # already warned the master will be 8-bit H.264. Keep it 8-bit for
            # maximum compatibility rather than gambling on a 10-bit x264 build.
            return args, False
        return args, False

    # generic / unknown forced encoder: try CRF, no fancy pixfmt assumptions.
    args += ["-crf", spec["crf_264"]]
    return args, False


# ---- public selection API --------------------------------------------------
class EncodePlan:
    """A concrete encode plan for one output.

    Attributes:
      encoder     chosen encoder name (e.g. "libx264")
      hwaccel_in  input args to place BEFORE `-i` (only ["-hwaccel","cuda"] for nvenc,
                  else []). Emit these exactly once per input.
      video_args  output args: `-c:v <enc>` plus its quality knobs (and pixfmt).
      is_nvenc    whether the encoder is an nvenc one (drives bsf/annexb choices).
      is_hevc     whether the encoded stream is HEVC (h265) vs H.264.
      ten_bit     whether the video ended up 10-bit.
    """

    def __init__(self, encoder: str, intent: str, want_10bit: bool):
        self.encoder = encoder
        self.intent = intent
        va, got10 = _video_quality_args(encoder, intent, want_10bit)
        self.video_args: List[str] = va
        self.ten_bit: bool = got10
        self.is_nvenc: bool = encoder in _NVENC
        self.is_hevc: bool = encoder in ("hevc_nvenc", "hevc_videotoolbox", "libx265")
        self.hwaccel_in: List[str] = ["-hwaccel", "cuda"] if self.is_nvenc else []
        if want_10bit and not got10:
            _warn(
                "10-bit main10 requested but chosen encoder %s can't (or won't) "
                "do it here; the master will be 8-bit." % encoder
            )

    def bitstream_filter(self) -> str:
        """The MP4->AnnexB bsf for the MPEG-TS intermediate concat."""
        return "hevc_mp4toannexb" if self.is_hevc else "h264_mp4toannexb"

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "EncodePlan(%s, intent=%s, 10bit=%s)" % (self.encoder, self.intent, self.ten_bit)


def plan_h264(intent: str) -> EncodePlan:
    """Encode plan for an H.264 output (previews / proxies). Never 10-bit."""
    enc = _pick(H264_LADDER, available_encoders())
    return EncodePlan(enc, intent, want_10bit=False)


def plan_hevc(intent: str, want_10bit: bool = True) -> EncodePlan:
    """Encode plan for an HEVC output (the 10-bit master). Falls back down the
    HEVC ladder; if it lands on libx264 the output is 8-bit H.264 (warned)."""
    enc = _pick(HEVC_LADDER, available_encoders())
    if enc == "libx264":
        _warn(
            "no HEVC encoder available (hevc_nvenc / hevc_videotoolbox / libx265); "
            "the FINAL master will be 8-bit H.264 (libx264), not 10-bit HEVC."
        )
    return EncodePlan(enc, intent, want_10bit=want_10bit)


# ---- fps helpers -----------------------------------------------------------
def probe_fps(path) -> str:
    """Exact frame-rate as ffprobe's r_frame_rate fraction string, e.g.
    "60000/1001" or "30/1". Raises if ffprobe can't read it — callers that want a
    safe default should catch and fall back."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    val = r.stdout.strip()
    if not val or val == "0/0":
        raise ValueError("no r_frame_rate for %s (got %r)" % (path, val))
    # normalize "30" -> "30/1" so callers always get a proper fraction string
    if "/" not in val:
        val = val + "/1"
    return val


def fps_float(path) -> float:
    """probe_fps() as a float (e.g. 59.94005994...)."""
    return float(Fraction(probe_fps(path)))


if __name__ == "__main__":  # tiny self-report / CLI probe
    have = available_encoders()
    forced = _env_override()
    print("ffmpeg encoders detected: %d" % len(have))
    if forced:
        print("CYE_ENCODER override: %s (available: %s)" % (forced, forced in have))
    hp = plan_h264("preview")
    fp = plan_hevc("final")
    print("H.264 preview ->", hp.encoder, hp.video_args, "hwaccel:", hp.hwaccel_in)
    print("HEVC final    ->", fp.encoder, fp.video_args, "10bit:", fp.ten_bit,
          "hwaccel:", fp.hwaccel_in)
    for p in sys.argv[1:]:
        try:
            print("fps(%s) = %s (%.5f)" % (p, probe_fps(p), fps_float(p)))
        except Exception as e:  # noqa: BLE001
            print("fps(%s): ERROR %s" % (p, e))
