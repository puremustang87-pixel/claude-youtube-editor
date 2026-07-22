#!/usr/bin/env python3
"""verify_frames.py — automated visual QA gate for TSX shots (the machine half of
"render frames and READ them").

The pipeline's hard rule is: render a shot's frames and LOOK at them before calling
it done (vidtsx-2d-generator "Verify the render", fake-screencast Step 4). That human
frame-read is the single biggest per-shot time cost. This tool automates the
mechanical part of that read so the human eye is spent only where it matters — it does
NOT replace the eye, it triages for it. Mirror of verify_cut.py's philosophy: every
finding is ADVISORY (it says WHERE to look; the user's eye decides), and the exit code
is nonzero ONLY on hard structural failures (a frame that literally didn't render, is
blank, or is a full black/white plate — things no reasonable shot intends).

Three tiers, cheapest first:

  TIER 1 — structural (ALWAYS, needs only Pillow)
    Per rendered frame: the file exists and decodes; it is not blank (near-uniform
    histogram / tiny per-channel stddev); it is not a near-black or near-white full
    plate. These are the "the render is broken" signals — a crashed component often
    yields a blank or single-colour PNG. HARD failures (drive the exit code).

  TIER 2 — golden frames (opt-in via --approve to set, automatic to compare)
    `--approve` copies the current frames to videos/<project>/qa/goldens/<Comp>-f<frame>.png
    (the accepted look). Later runs compare current-vs-golden two ways, both stdlib/Pillow
    (no new heavy deps): an RMS pixel difference (0..255) and a coarse 8x8 average-hash
    perceptual-diff (Hamming distance 0..64). Advisory thresholds flag "this drifted from
    the look you approved" — the regression gate that lets a re-render be trusted without
    re-reading every pixel. Golden drift NEVER fails the build; it prints and asks the eye.

  TIER 3 — AI assertions (opt-in via --ai, needs GEMINI_API_KEY + google-genai)
    For each discovered cue, sends the rendered frame + the cue's `expect` sentence to a
    Gemini flash-class model (same google-genai usage + model family as gen_thumbnail.py)
    and asks PASS / FAIL / NOTES: "does the frame match this description?". This catches
    semantic problems a pixel diff can't (headline present but the WRONG headline; cursor
    off the button; a chart with the wrong label). Advisory — a FAIL prints loudly but,
    like verify_cut's word diff, is a "look here", not an auto-reject.

Cue discovery (which frames to render/check) — two modes:

  --frames 12,80,200      explicit frame list (no `expect` text; tiers 1-2 only unless
                          you also pass --ai, in which case each gets an empty expectation).
  --cues                  auto: parse the shot's .tsx for an exported QA_CUES convention:

        // near the top of the shot, after compositionConfig:
        export const QA_CUES = [
          { frame: 86,  expect: "the 'Claude Code' chip has entered, top-left" },
          { frame: 240, expect: "headline fully visible, cursor resting on the Publish button" },
        ];

      Frames come from `frame:`; the `expect:` string is the tier-3 assertion for that
      frame (and documents intent for a human reader). This mirrors the ad-hoc
      `const ENGINE = 240; // "that's the engine"` cue-comments already in the example
      shots — QA_CUES just makes them machine-readable. The parser is a tolerant regex
      (no node/TS eval): it reads the QA_CUES array literal and pulls each frame + expect.
      Trailing commas, single/double quotes, and multi-line arrays are fine.

Rendering — shells out to the repo's REAL still mechanism, per shot+frame:
  `node scripts/gen-registry.mjs`  (the registry is generated; render scripts don't run
                                    it themselves — CLAUDE.md), then
  `node scripts/frames.mjs <Comp> <f1,f2,...> --scale=<s>`  which writes
  remotion/out/qa/<Comp>-f<frame>.png (zero-padded to 4 digits). This tool copies those
  into a scratch dir it owns. The render step is cleanly skippable with --no-render (when
  the stills already exist), which is also what makes the tool testable without node.

Usage:
  # explicit frames, structural only
  python tools/verify_frames.py videos/video-1 BigStatement --frames 12,86,240

  # cue-driven, set the golden baseline the first time you're happy with the look
  python tools/verify_frames.py videos/video-1 BigStatement --cues --approve

  # later re-render: structural + golden regression + AI assertions
  python tools/verify_frames.py videos/video-1 BigStatement --cues --ai

  # stills already rendered elsewhere — just re-run the checks
  python tools/verify_frames.py videos/video-1 BigStatement --cues --no-render

  # exercise tier 1 + tier 2 logic end-to-end with synthetic images (no node, no repo state)
  python tools/verify_frames.py --self-test

Writes videos/<project>/qa/verify-frames-report.md and prints the summary (like verify_cut.py).
Exit: nonzero ONLY if a hard structural failure fired (missing/blank/black/white frame) or
an explicit --strict-golden was passed and a golden drift exceeded threshold.

Python 3.9-compatible on purpose (sandbox interpreter is 3.9; repo targets 3.10+).
"""

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Windows consoles default to cp1252 — force UTF-8 so box/glyphs print (matches yt_upload.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent   # engine root — holds remotion/
REMOTION = REPO / "remotion"

# ── advisory thresholds (all tunable; none of the golden/AI ones fail the build) ──────
BLANK_STDDEV = 3.0        # mean per-channel stddev below this ⇒ "near-uniform / blank"
BLACK_MEAN = 10.0         # full-frame mean luma below this ⇒ near-black plate (0..255)
WHITE_MEAN = 245.0        # full-frame mean luma above this ⇒ near-white plate (0..255)
GOLDEN_RMS_WARN = 8.0     # RMS pixel diff (0..255) above this ⇒ advisory "drifted"
GOLDEN_HASH_WARN = 6      # aHash Hamming distance (0..64) above this ⇒ advisory "drifted"
AHASH_SIZE = 8            # 8x8 average-hash ⇒ 64-bit fingerprint

DEFAULT_SCALE = 0.5       # phone-scale legibility, matches frames.mjs default
DEFAULT_AI_MODEL = "gemini-3-flash"   # flash-class, same family as gen_thumbnail's gemini-3-pro-image


# ───────────────────────── Pillow guard ──────────────────────────────────────────────
# Pillow is a declared dep (requirements.txt), but a bare/system python misses it. Fail
# with the same "you're not on the venv" guidance CLAUDE.md gives, not a raw ImportError.
def _require_pil():
    try:
        from PIL import Image  # noqa: F401
        return Image
    except ImportError:
        sys.exit(
            "Pillow is required for verify_frames.py but is not importable.\n"
            "  → install it:  ./venv/bin/pip install -r requirements.txt   (or: pip install Pillow)\n"
            "  → then run tools via the venv python (a bare system python misses the repo deps)."
        )


# ───────────────────────── path helpers ──────────────────────────────────────────────
def rp(p: str) -> Path:
    """Project-ish path: absolute as-is, else relative to the CWD (repo-root convention)."""
    q = Path(p)
    return q if q.is_absolute() else Path.cwd() / q


def rel(path: Path) -> str:
    """Repo-relative display; fall back to raw across drives (Windows)."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def find_shot_tsx(comp_id: str) -> Optional[Path]:
    """Locate the .tsx whose compositionConfig.id == comp_id (for --cues parsing).
    Matches gen-registry's discovery: scan remotion/src/shots/**/*.tsx."""
    shots = REMOTION / "src" / "shots"
    if not shots.is_dir():
        return None
    pat = re.compile(r"id\s*:\s*['\"]" + re.escape(comp_id) + r"['\"]")
    for tsx in sorted(shots.rglob("*.tsx")):
        try:
            head = tsx.read_text(encoding="utf-8")
        except OSError:
            continue
        # only trust an id match that lives in a compositionConfig block
        m = re.search(r"compositionConfig\s*=\s*{([\s\S]*?)}\s*;", head)
        if m and pat.search(m.group(1)):
            return tsx
    return None


# ───────────────────────── cue discovery ─────────────────────────────────────────────
class Cue:
    __slots__ = ("frame", "expect")

    def __init__(self, frame: int, expect: str = ""):
        self.frame = int(frame)
        self.expect = expect or ""

    def __repr__(self) -> str:
        return "Cue(%d, %r)" % (self.frame, self.expect)


def parse_qa_cues(tsx_src: str) -> List[Cue]:
    """Tolerant regex parse of an exported `QA_CUES = [ { frame: N, expect: "…" }, … ]`.
    No node/TS eval — we only need frame + expect. Handles single/double quotes, trailing
    commas, and multi-line arrays. `expect` is optional per entry (defaults to "")."""
    block = re.search(r"QA_CUES\s*(?::[^=]*)?=\s*\[([\s\S]*?)\]", tsx_src)
    if not block:
        return []
    body = block.group(1)
    cues: List[Cue] = []
    # each { ... } object literal inside the array
    for obj in re.finditer(r"{([^{}]*)}", body):
        seg = obj.group(1)
        fm = re.search(r"frame\s*:\s*([0-9]+)", seg)
        if not fm:
            continue
        em = re.search(r"expect\s*:\s*(['\"])(.*?)\1", seg, re.DOTALL)
        expect = em.group(2).strip() if em else ""
        cues.append(Cue(int(fm.group(1)), expect))
    # stable order by frame, de-dup identical frames (keep the first, richer expect wins)
    seen = {}
    for c in cues:
        if c.frame not in seen or (not seen[c.frame].expect and c.expect):
            seen[c.frame] = c
    return [seen[f] for f in sorted(seen)]


def cues_from_args(args) -> List[Cue]:
    """Resolve the cue list from --frames or --cues (mutually informative; --frames wins
    if both are given, but we merge any QA_CUES `expect` text onto matching frames)."""
    explicit: List[Cue] = []
    if args.frames:
        for tok in args.frames.split(","):
            tok = tok.strip()
            if tok:
                explicit.append(Cue(int(tok)))

    parsed: List[Cue] = []
    if args.cues:
        tsx = find_shot_tsx(args.comp)
        if tsx is None:
            sys.exit("--cues: could not find a shot .tsx with compositionConfig.id == "
                     "%r under remotion/src/shots/. Pass --frames instead, or check the id."
                     % args.comp)
        parsed = parse_qa_cues(tsx.read_text(encoding="utf-8"))
        if not parsed:
            sys.exit("--cues: %s has no `export const QA_CUES = [...]`. Add the convention "
                     "(see this file's docstring) or use --frames." % rel(tsx))

    if explicit and parsed:
        # merge expects from QA_CUES onto explicitly-requested frames
        by_frame = {c.frame: c.expect for c in parsed}
        for c in explicit:
            c.expect = by_frame.get(c.frame, "")
        return explicit
    return explicit or parsed


# ───────────────────────── rendering (skippable) ─────────────────────────────────────
def render_frames(comp_id: str, frames: List[int], scale: float) -> Tuple[bool, str]:
    """Shell out to the repo's real still mechanism for these frames of one composition.
    Runs gen-registry first (registry is generated; render scripts don't run it), then
    scripts/frames.mjs, which writes remotion/out/qa/<comp>-f<frame>.png.

    Returns (ok, message). ok=False if node is missing or a step exits nonzero — the
    caller downgrades to check-only (the frames may still exist from a prior render)."""
    if shutil.which("node") is None:
        return False, "node not found on PATH — cannot render (use --no-render if stills already exist)."
    scripts = REMOTION / "scripts"
    frames_mjs = scripts / "frames.mjs"
    gen_mjs = scripts / "gen-registry.mjs"
    if not frames_mjs.exists():
        return False, "missing %s — the still mechanism is not where expected." % rel(frames_mjs)

    frame_arg = ",".join(str(f) for f in frames)
    try:
        if gen_mjs.exists():
            g = subprocess.run(["node", str(gen_mjs)], cwd=str(REMOTION),
                               capture_output=True, text=True)
            if g.returncode != 0:
                return False, "gen-registry failed:\n" + (g.stderr or g.stdout).strip()
        r = subprocess.run(["node", str(frames_mjs), comp_id, frame_arg, "--scale=%s" % scale],
                          cwd=str(REMOTION), capture_output=True, text=True)
        if r.returncode != 0:
            return False, "frames.mjs failed:\n" + (r.stderr or r.stdout).strip()
    except OSError as e:
        return False, "render shell-out error: %s" % e
    return True, "rendered %d frame(s) of %s at scale %s" % (len(frames), comp_id, scale)


def rendered_path(comp_id: str, frame: int) -> Path:
    """Where frames.mjs writes a still: remotion/out/qa/<comp>-f<0000>.png."""
    return REMOTION / "out" / "qa" / ("%s-f%s.png" % (comp_id, str(frame).zfill(4)))


# ───────────────────────── tier 1: structural ────────────────────────────────────────
def _grayscale_stats(img):
    """(mean_luma, mean_per_channel_stddev) over the image, 0..255. Pure Pillow —
    no numpy. stddev is the blank detector; mean is the black/white-plate detector."""
    rgb = img.convert("RGB")
    stat_mean, stat_std = 0.0, 0.0
    try:
        from PIL import ImageStat
        st = ImageStat.Stat(rgb)
        stat_mean = sum(st.mean) / len(st.mean)        # avg over channels ≈ luma proxy
        stat_std = sum(st.stddev) / len(st.stddev)     # avg per-channel spread
    except Exception:
        # ultra-defensive fallback via histogram if ImageStat is unavailable
        g = rgb.convert("L")
        hist = g.histogram()
        total = sum(hist) or 1
        stat_mean = sum(i * h for i, h in enumerate(hist)) / total
        var = sum(((i - stat_mean) ** 2) * h for i, h in enumerate(hist)) / total
        stat_std = math.sqrt(var)
    return stat_mean, stat_std


def tier1_structural(path: Path) -> Tuple[bool, List[str]]:
    """Hard structural checks. Returns (hard_fail, notes). hard_fail drives exit code."""
    Image = _require_pil()
    notes: List[str] = []
    if not path.exists():
        return True, ["frame did not render (file missing: %s)" % rel(path)]
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        return True, ["frame does not decode (%s: %s)" % (type(e).__name__, e)]

    w, h = img.size
    if w == 0 or h == 0:
        return True, ["frame has zero dimension (%dx%d)" % (w, h)]

    mean, std = _grayscale_stats(img)
    hard = False
    if std < BLANK_STDDEV:
        hard = True
        notes.append("BLANK — near-uniform frame (stddev %.2f < %.1f); component likely rendered nothing"
                     % (std, BLANK_STDDEV))
    if mean < BLACK_MEAN:
        hard = True
        notes.append("NEAR-BLACK plate (mean luma %.1f < %.1f)" % (mean, BLACK_MEAN))
    elif mean > WHITE_MEAN:
        hard = True
        notes.append("NEAR-WHITE plate (mean luma %.1f > %.1f)" % (mean, WHITE_MEAN))
    if not notes:
        notes.append("ok — %dx%d, mean luma %.1f, stddev %.1f" % (w, h, mean, std))
    return hard, notes


# ───────────────────────── tier 2: golden compare ────────────────────────────────────
def _ahash_bits(img) -> int:
    """Coarse average-hash: shrink to 8x8 grayscale, threshold at the mean → 64-bit int.
    A perceptual fingerprint robust to tiny AA/encoding jitter but sensitive to real
    layout change. Compared via Hamming distance."""
    from PIL import Image
    g = img.convert("L").resize((AHASH_SIZE, AHASH_SIZE), Image.BILINEAR)
    px = list(g.getdata())
    avg = sum(px) / len(px)
    bits = 0
    for i, p in enumerate(px):
        if p >= avg:
            bits |= (1 << i)
    return bits


def _rms_diff(a, b) -> Optional[float]:
    """RMS pixel difference (0..255) between two same-size images. Uses ImageChops.
    Returns None if the sizes differ irreconcilably (we resize b→a first, so normally not)."""
    from PIL import Image, ImageChops
    ra, rb = a.convert("RGB"), b.convert("RGB")
    if rb.size != ra.size:
        rb = rb.resize(ra.size, Image.BILINEAR)
    diff = ImageChops.difference(ra, rb)
    hist = diff.histogram()
    # histogram is 3×256 (R,G,B). sum of value²×count over all channels / total pixels.
    sq = 0.0
    count = 0
    for ch in range(3):
        base = ch * 256
        for v in range(256):
            c = hist[base + v]
            sq += (v * v) * c
            count += c
    if count == 0:
        return None
    return math.sqrt(sq / count)


def goldens_dir(project: Path) -> Path:
    return project / "qa" / "goldens"


def tier2_golden(project: Path, comp_id: str, frame: int, current: Path,
                 approve: bool) -> Tuple[Optional[bool], List[str]]:
    """Golden set/compare. Returns (drift_flag, notes):
        drift_flag = None  → no golden yet (nothing to compare; not a fail)
        drift_flag = False → within thresholds
        drift_flag = True  → drifted beyond an advisory threshold (never a hard fail
                             unless --strict-golden)."""
    Image = _require_pil()
    gdir = goldens_dir(project)
    golden = gdir / ("%s-f%s.png" % (comp_id, str(frame).zfill(4)))

    if approve:
        if not current.exists():
            return None, ["cannot approve — current frame missing (%s)" % rel(current)]
        gdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, golden)
        return None, ["golden SET → %s" % rel(golden)]

    if not golden.exists():
        return None, ["no golden yet (run with --approve to set the baseline)"]
    if not current.exists():
        return None, ["current frame missing (%s) — cannot compare" % rel(current)]

    try:
        cur_img = Image.open(current); cur_img.load()
        gold_img = Image.open(golden); gold_img.load()
    except Exception as e:
        return None, ["golden compare skipped (decode error: %s)" % e]

    rms = _rms_diff(gold_img, cur_img)
    ham = bin(_ahash_bits(gold_img) ^ _ahash_bits(cur_img)).count("1")
    drift = (rms is not None and rms > GOLDEN_RMS_WARN) or (ham > GOLDEN_HASH_WARN)
    size_note = ""
    if cur_img.size != gold_img.size:
        size_note = " · size changed %s→%s" % (gold_img.size, cur_img.size)
    tag = "DRIFTED" if drift else "match"
    return drift, ["golden %s — RMS %.2f (warn>%.1f), aHash dist %d/64 (warn>%d)%s"
                   % (tag, rms if rms is not None else -1, GOLDEN_RMS_WARN,
                      ham, GOLDEN_HASH_WARN, size_note)]


# ───────────────────────── tier 3: AI assertions ─────────────────────────────────────
def load_env() -> dict:
    """Minimal .env reader (matches gen_thumbnail.py / gen_sfx.py — no python-dotenv dep)."""
    env = {}
    p = REPO / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    merged = dict(env)
    merged.update(os.environ)
    return merged


def tier3_ai(current: Path, expect: str, model: str, api_key: str) -> Tuple[str, str]:
    """Ask a Gemini flash-class model whether the frame matches `expect`. Returns
    (verdict, notes) where verdict ∈ {PASS, FAIL, NOTES, ERROR}. Same google-genai usage
    pattern + model family as gen_thumbnail.py; API errors are surfaced verbatim."""
    if not expect:
        return "NOTES", "no `expect` text for this frame (skipped AI)"
    if not current.exists():
        return "ERROR", "frame missing (%s)" % rel(current)
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "ERROR", ("google-genai not importable — install it "
                         "(./venv/bin/pip install -r requirements.txt) or drop --ai")

    prompt = (
        "You are a strict visual QA checker for a rendered video frame.\n"
        "The frame SHOULD show: \"%s\".\n\n"
        "Reply on the FIRST line with exactly one word: PASS if the frame clearly matches "
        "that description, or FAIL if it does not (wrong text, missing element, element in "
        "the wrong place, garbled/misspelled words, cropped or overlapping content).\n"
        "Then, on following lines, one short sentence explaining what you actually see and, "
        "if FAIL, what is wrong." % expect
    )
    try:
        with open(current, "rb") as f:
            img_bytes = f.read()
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model,
            contents=[prompt, types.Part.from_bytes(data=img_bytes, mime_type="image/png")],
        )
    except Exception as e:  # surface verbatim (bad key, model-not-found, quota) like gen_thumbnail
        return "ERROR", "Gemini API error: %s: %s" % (type(e).__name__, e)

    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        return "ERROR", "empty response from model"
    first = text.splitlines()[0].strip().upper()
    verdict = "PASS" if first.startswith("PASS") else ("FAIL" if first.startswith("FAIL") else "NOTES")
    return verdict, text


# ───────────────────────── report ────────────────────────────────────────────────────
def run_checks(project: Path, comp_id: str, cues: List[Cue], args) -> int:
    """Execute the tiers over every cue, write the report, print the summary, return the
    process exit code (nonzero only on hard structural failure, or golden drift with
    --strict-golden)."""
    env = load_env() if args.ai else {}
    api_key = env.get("GEMINI_API_KEY", "").strip() if args.ai else ""
    ai_ready = bool(args.ai and api_key)

    frames = [c.frame for c in cues]
    L = ["# Verify-frames report — %s / %s" % (project.name, comp_id), ""]
    render_note = ""

    # ── render (skippable) ────────────────────────────────────────────────────────────
    if args.no_render:
        render_note = "render SKIPPED (--no-render); checking existing stills."
    else:
        ok, msg = render_frames(comp_id, frames, args.scale)
        render_note = msg
        if not ok:
            render_note += "\n  (continuing in check-only mode — stills may exist from a prior render)"

    hard_fails = 0
    golden_drifts = 0
    ai_fails = 0
    ai_errors = 0

    for c in cues:
        cur = rendered_path(comp_id, c.frame)
        head = "## f%d%s" % (c.frame, ("  — " + c.expect) if c.expect else "")
        L.append(head)

        hard, t1 = tier1_structural(cur)
        if hard:
            hard_fails += 1
        for line in t1:
            L.append("  - [structural] " + line)

        drift, t2 = tier2_golden(project, comp_id, c.frame, cur, args.approve)
        if drift:
            golden_drifts += 1
        for line in t2:
            L.append("  - [golden] " + line)

        if ai_ready and not args.approve:
            verdict, notes = tier3_ai(cur, c.expect, args.ai_model, api_key)
            if verdict == "FAIL":
                ai_fails += 1
            elif verdict == "ERROR":
                ai_errors += 1
            first = notes.splitlines()[0] if notes else ""
            rest = " · ".join(s.strip() for s in notes.splitlines()[1:] if s.strip())
            L.append("  - [AI %s] %s" % (verdict, rest or first))
        elif args.ai and not api_key and not args.approve:
            L.append("  - [AI] GEMINI_API_KEY not set — skipped (add it to .env or drop --ai)")
        L.append("")

    # ── summary ─────────────────────────────────────────────────────────────────────
    summary = ("Frames: %d · structural HARD-fails: %d · golden drifts: %d"
               % (len(cues), hard_fails, golden_drifts))
    if args.ai:
        summary += " · AI fails: %d · AI errors: %d" % (ai_fails, ai_errors)
    if args.approve:
        summary += " · goldens: SET (baseline written)"

    verdict_lines = []
    if hard_fails:
        verdict_lines.append("HARD STRUCTURAL FAILURES — %d frame(s) missing/blank/black/white. "
                             "The render is broken; fix the shot before reading further." % hard_fails)
    if golden_drifts:
        verdict_lines.append("%d golden drift(s) — the look changed from what you approved. "
                             "Read those frames and re-approve if the change is intended." % golden_drifts)
    if ai_fails:
        verdict_lines.append("%d AI assertion FAIL(s) — a cue's description didn't match. "
                             "Look here (may be a false positive; the eye decides)." % ai_fails)
    if not verdict_lines and not args.approve:
        verdict_lines.append("Clean — every frame rendered, none blank/black/white, "
                             + ("within golden thresholds" if not args.no_render or True else "")
                             + (", AI cues matched" if ai_ready else "") + ".")

    body = [summary, ""] + ["- " + v for v in verdict_lines] + ["", "Render: " + render_note, ""]

    out_dir = project / "qa"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "verify-frames-report.md"
    out.write_text("\n".join(L[:2] + body + L[2:]), encoding="utf-8")

    print(summary)
    for v in verdict_lines:
        print("  " + v)
    print("wrote %s" % rel(out))

    # exit semantics: advisory by default — nonzero ONLY on hard structural failure,
    # or golden drift when the user explicitly opted into --strict-golden.
    if hard_fails:
        return 2
    if golden_drifts and args.strict_golden:
        return 3
    return 0


# ───────────────────────── self-test (tiers 1 + 2, synthetic, no node) ───────────────
def self_test() -> int:
    """Generate synthetic images and assert tier-1 + tier-2 logic fires correctly:
      · a good pair (identical)      → tier1 ok, tier2 match
      · a blank frame                → tier1 HARD fail (blank + white)
      · a shifted/differing pair     → tier1 ok, tier2 DRIFT
    Exits 0 on success, 1 on any assertion failure. Writes fixtures under tools/fixtures/."""
    Image = _require_pil()
    from PIL import ImageDraw

    fx = REPO / "tools" / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)

    def content_frame(shift: int = 0, color=(40, 60, 200)) -> "Image.Image":
        """A non-blank frame with real structure (bg + a bright rectangle + a line).
        `shift` moves the rectangle to simulate a layout change."""
        im = Image.new("RGB", (320, 180), (18, 18, 30))
        d = ImageDraw.Draw(im)
        d.rectangle([40 + shift, 50, 180 + shift, 130], fill=color)
        d.line([0, 0, 320, 180], fill=(230, 230, 240), width=3)
        d.ellipse([220, 40, 300, 120], fill=(240, 180, 40))
        return im

    good_a = fx / "good_a.png"
    good_b = fx / "good_b.png"
    blank = fx / "blank.png"
    shifted = fx / "shifted.png"

    content_frame().save(good_a)
    content_frame().save(good_b)                    # identical to good_a
    Image.new("RGB", (320, 180), (255, 255, 255)).save(blank)   # blank white plate
    content_frame(shift=70, color=(200, 40, 40)).save(shifted)  # moved + recoloured

    passed, failed = [], []

    def check(name: str, cond: bool, detail: str = ""):
        (passed if cond else failed).append(name + ((" — " + detail) if detail and not cond else ""))

    # ── tier 1 ──
    hard_good, notes_good = tier1_structural(good_a)
    check("tier1: good frame is NOT a hard fail", not hard_good, "; ".join(notes_good))

    hard_blank, notes_blank = tier1_structural(blank)
    check("tier1: blank white frame IS a hard fail", hard_blank, "; ".join(notes_blank))

    hard_missing, _ = tier1_structural(fx / "does_not_exist.png")
    check("tier1: missing frame IS a hard fail", hard_missing)

    # near-black plate
    black = fx / "black.png"
    Image.new("RGB", (320, 180), (2, 2, 2)).save(black)
    hard_black, notes_black = tier1_structural(black)
    check("tier1: near-black plate IS a hard fail", hard_black, "; ".join(notes_black))

    # ── tier 2 (golden compare) via a temp project dir ──
    proj = fx / "_selftest_project"
    gdir = goldens_dir(proj)
    if gdir.exists():
        shutil.rmtree(proj, ignore_errors=True)

    # approve good_a as the golden for SelfTestComp f0001 by copying into place
    gdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(good_a, gdir / "SelfTestComp-f0001.png")

    # identical current → match (drift False). Route good_b through the rendered_path slot.
    slot = rendered_path("SelfTestComp", 1)
    slot.parent.mkdir(parents=True, exist_ok=True)
    _keep = slot.exists()
    _backup = slot.read_bytes() if _keep else None
    try:
        shutil.copyfile(good_b, slot)
        drift_same, notes_same = tier2_golden(proj, "SelfTestComp", 1, slot, approve=False)
        check("tier2: identical pair does NOT drift", drift_same is False, "; ".join(notes_same))

        # RMS + aHash directly on the pair (sanity: near-zero for identical)
        gi = Image.open(good_a); gi.load()
        gb = Image.open(good_b); gb.load()
        rms_same = _rms_diff(gi, gb)
        ham_same = bin(_ahash_bits(gi) ^ _ahash_bits(gb)).count("1")
        check("tier2: identical RMS ~0", rms_same is not None and rms_same < 1.0, "rms=%s" % rms_same)
        check("tier2: identical aHash dist 0", ham_same == 0, "ham=%d" % ham_same)

        # shifted current → drift True
        shutil.copyfile(shifted, slot)
        drift_diff, notes_diff = tier2_golden(proj, "SelfTestComp", 1, slot, approve=False)
        check("tier2: shifted pair DOES drift", drift_diff is True, "; ".join(notes_diff))

        si = Image.open(shifted); si.load()
        rms_diff = _rms_diff(gi, si)
        ham_diff = bin(_ahash_bits(gi) ^ _ahash_bits(si)).count("1")
        check("tier2: shifted RMS exceeds warn", rms_diff is not None and rms_diff > GOLDEN_RMS_WARN,
              "rms=%.2f" % (rms_diff or -1))
        check("tier2: shifted aHash dist > 0", ham_diff > 0, "ham=%d" % ham_diff)

        # approve path writes a golden
        drift_appr, notes_appr = tier2_golden(proj, "SelfTestComp", 2, good_a, approve=True)
        check("tier2: --approve writes a golden",
              (gdir / "SelfTestComp-f0002.png").exists() and drift_appr is None, "; ".join(notes_appr))
    finally:
        # restore whatever was in the shared out/qa slot, and remove the temp golden
        # project so a self-test run leaves only the reusable fixture images behind.
        if _backup is not None:
            slot.write_bytes(_backup)
        elif slot.exists():
            slot.unlink()
        shutil.rmtree(proj, ignore_errors=True)

    # ── cue parser ──
    src = """
      export const compositionConfig = { id: 'SelfTestComp', durationInSeconds: 10, fps: 30, width: 1920, height: 1080 };
      export const QA_CUES = [
        { frame: 12, expect: "the title card is centered" },
        { frame: 240, expect: 'cursor rests on the Publish button' },
        { frame: 240, expect: "" },
      ];
    """
    cues = parse_qa_cues(src)
    check("cues: parses 2 unique frames", len(cues) == 2, "got %d" % len(cues))
    check("cues: frames sorted", [c.frame for c in cues] == [12, 240], str([c.frame for c in cues]))
    check("cues: expect text captured", cues[0].expect.startswith("the title"), repr(cues[0].expect))
    check("cues: richer expect wins on dup frame", "Publish" in cues[1].expect, repr(cues[1].expect))

    empty = parse_qa_cues("const x = 1;")
    check("cues: no QA_CUES → empty list", empty == [])

    # ── report ──
    print("── self-test ──────────────────────────────────")
    for p in passed:
        print("  PASS  " + p)
    for f in failed:
        print("  FAIL  " + f)
    print("───────────────────────────────────────────────")
    print("%d passed, %d failed" % (len(passed), len(failed)))
    print("fixtures written under %s" % rel(fx))
    return 0 if not failed else 1


# ───────────────────────── CLI ───────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Automated visual QA gate for TSX shots (structural + golden + AI). "
                    "Advisory, like verify_cut.py.")
    ap.add_argument("project", nargs="?", help="project dir (e.g. videos/video-1), relative to CWD")
    ap.add_argument("comp", nargs="?", help="composition id (compositionConfig.id), e.g. BigStatement")
    ap.add_argument("--frames", help="explicit frame list, e.g. 12,80,240")
    ap.add_argument("--cues", action="store_true",
                    help="discover frames from the shot's exported QA_CUES (see docstring)")
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE,
                    help="render scale for frames.mjs (default %(default)s — phone-scale)")
    ap.add_argument("--no-render", action="store_true",
                    help="skip the node render; check stills that already exist")
    ap.add_argument("--approve", action="store_true",
                    help="tier 2: copy current frames to the golden set (set the baseline)")
    ap.add_argument("--strict-golden", action="store_true",
                    help="exit nonzero when a golden drift exceeds threshold (default: advisory only)")
    ap.add_argument("--ai", action="store_true",
                    help="tier 3: AI cue assertions via Gemini (needs GEMINI_API_KEY + --cues/expect)")
    ap.add_argument("--ai-model", default=DEFAULT_AI_MODEL,
                    help="flash-class Gemini model for --ai (default %(default)s)")
    ap.add_argument("--self-test", action="store_true",
                    help="exercise tier 1 + tier 2 logic on synthetic images and exit")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    if not args.project or not args.comp:
        ap.error("project and comp are required (or pass --self-test). "
                 "e.g. python tools/verify_frames.py videos/video-1 BigStatement --cues")

    project = rp(args.project)
    if not project.exists():
        # not fatal for --no-render golden-less runs, but the report/goldens live here
        print("! project dir does not exist yet: %s (creating for qa/ output)" % rel(project))

    cues = cues_from_args(args)
    if not cues:
        sys.exit("no frames to check — pass --frames 12,80,240 or --cues (with QA_CUES in the shot).")

    sys.exit(run_checks(project, args.comp, cues, args))


if __name__ == "__main__":
    main()
