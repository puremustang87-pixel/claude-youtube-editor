#!/usr/bin/env python3
"""
gen_screencast.py — compile a capture_web.py manifest into a tuned fake-screencast TSX shot.

This closes the loop that used to be a by-hand treadmill (see
.claude/skills/fake-screencast/SKILL.md): tools/capture_web.py already drives real
Playwright Chromium through a choreography and now records, per interaction, the
target element's CENTER as VIEWPORT FRACTIONS. This tool reads that manifest OFFLINE
(no playwright, no browser, stdlib only) and emits a complete shot file that imports
remotion/src/lib/screencast.tsx exactly as an authored shot would — the PAGES array,
the CURSOR keyframe path, and the CLICKS, all pre-computed so the human tunes rather
than measures.

What it computes for you (all the parts the SKILL says "cost a render to find"):
  - PAGES     from manifest pages (img / url / tabTitle / enterAt), with the
              navigation-vs-filter TRANSITION inferred from the URLs (path change =
              hard cut; same path + query = crossfade) — the SKILL's biggest realism
              tell, decided from data instead of by eye.
  - CURSOR    keyframes from the interaction fractions: the pointer holds, travels,
              and ARRIVES a couple of frames before each click (never teleports).
  - CLICKS    one ripple per navigating interaction, fired ~2 frames after the cursor
              arrives (the SKILL's convention), coincident with the page's enterAt.

Guarantees: cursor / click / page frame sequences are strictly monotonic, every
fraction is clamped to [0,1], and the emitted TSX uses the SAME component and prop
names the engine + example shot use (Screencast / ScreencastPage / CursorKey /
pages / cursor / clicks). It is a starting point: the header says so.

Timing (frame 0 = the shot's master_in_s, per the SKILL):
  --transcript videos/<p>/work/edited-transcript.json  +  --cue-words "models,flux"
      pins page-change N to the START of the Nth cue word (ms -> frames at --fps).
  Without a transcript, page changes are spaced evenly and each is flagged with a
  TODO so you retime them against the narration.

Usage:
  python tools/gen_screencast.py --manifest <manifest.json> --name ShotName \\
      --out remotion/src/shots/<project>/ShotName.gen.tsx [--fps 60] \\
      [--transcript videos/<p>/work/edited-transcript.json --cue-words "w1,w2,..."]

  # offline demo against the shipped fixture:
  python tools/gen_screencast.py --manifest tools/fixtures/capture-demo-manifest.json \\
      --name DemoWalkthrough --out /tmp/DemoWalkthrough.gen.tsx
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- tunables (baked into the numbers we emit; the header tells the human to tune) --
DEFAULT_FPS = 60
CLICK_LEAD = 2      # cursor ARRIVES this many frames before it clicks (SKILL convention)
TRAVEL = 16         # frames the pointer spends easing from its hold to the target
DEFAULT_HOLD_S = 2.2  # seconds each page holds when we have no transcript to sync to
TAIL_S = 2.5        # trailing seconds after the last click so the final page settles
START_X, START_Y = 0.5, 0.40  # neutral resting spot for the pointer on page 1


def rp(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def clamp01(v):
    if v is None:
        return None
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def pascal_case(name):
    """A valid TSX composition id: PascalCase, no hyphens/underscores (vidtsx rule)."""
    parts = re.split(r"[^0-9A-Za-z]+", str(name))
    out = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not out:
        out = "Screencast"
    if out[0].isdigit():
        out = "S" + out
    return out


# ---------------------------------------------------------------------------
# manifest loading — accept BOTH the new object shape and the legacy bare list
# ---------------------------------------------------------------------------
def load_manifest(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        # legacy capture_web v1: a bare list of page entries, no interactions
        return {"pages": data, "interactions": [], "viewport": None}
    if not isinstance(data, dict):
        raise SystemExit(f"manifest is neither a list nor an object: {path}")
    return {
        "pages": data.get("pages") or [],
        "interactions": data.get("interactions") or [],
        "viewport": data.get("viewport"),
    }


# ---------------------------------------------------------------------------
# transcript -> cue frames
# ---------------------------------------------------------------------------
_norm = re.compile(r"[^0-9a-z]+")


def _tok(s):
    return _norm.sub("", str(s).lower())


def load_words(path):
    """Word list from an AssemblyAI-shaped transcript: {'words':[{text,start,end}]}.

    start/end are MILLISECONDS (the repo's transcript convention — see cutlib.py)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("words") or []
    if isinstance(data, list):
        return data
    return []


def word_start_frame(words, cue, fps, search_from_idx=0):
    """First word at/after `search_from_idx` whose token == cue's token.

    Returns (frame, next_search_idx) or (None, search_from_idx) if not found.
    Frame = round(start_ms / 1000 * fps). Scanning forward keeps repeated cue
    words (e.g. two 'models') pinned to successive occurrences, in order."""
    target = _tok(cue)
    for j in range(search_from_idx, len(words)):
        if _tok(words[j].get("text", "")) == target:
            ms = float(words[j].get("start", 0))
            return int(round(ms / 1000.0 * fps)), j + 1
    return None, search_from_idx


# ---------------------------------------------------------------------------
# realism: navigation (hard cut) vs in-page filter (crossfade)
# ---------------------------------------------------------------------------
def _path_of(url):
    """Path portion of a URL label (before '?'), scheme-insensitive and trimmed."""
    u = str(url or "").strip()
    u = re.sub(r"^[a-zA-Z]+://", "", u)  # drop scheme if present
    return u.split("?", 1)[0].rstrip("/")


def classify_transition(prev_url, url):
    """SKILL rule: same path + (new) query => in-page filter => 'crossfade';
    a different path => navigation => 'cut'. Default 'cut' when unsure."""
    if _path_of(prev_url) and _path_of(prev_url) == _path_of(url):
        return "crossfade"
    return "cut"


# ---------------------------------------------------------------------------
# monotonic frame helper
# ---------------------------------------------------------------------------
class Mono:
    """Forces a strictly-increasing frame stream: each value is bumped to at
    least prev+1. Preserves ordering while never regressing or duplicating."""

    def __init__(self, start=-1):
        self.last = start

    def push(self, frame):
        f = int(round(frame))
        if f <= self.last:
            f = self.last + 1
        self.last = f
        return f


# ---------------------------------------------------------------------------
# the compile step
# ---------------------------------------------------------------------------
def build_plan(pages, interactions, fps, words, cue_words):
    """Return (page_dicts, cursor_keys, clicks, notes, total_frames).

    page_dicts: list of {img,url,tabTitle,enterAt,transition?,transitionFrames?}
    cursor_keys/clicks: monotonic frame sequences, fractions clamped to [0,1].
    notes: human-facing TODO strings the emitter turns into comments."""
    notes = []
    n = len(pages)
    if n == 0:
        raise SystemExit("manifest has no pages — nothing to compile")

    # --- 1) enterAt (cue) frame per page -----------------------------------
    hold_frames = max(1, int(round(DEFAULT_HOLD_S * fps)))
    enter = [0] * n
    search_idx = 0
    used_transcript = bool(words) and bool(cue_words)
    for i in range(1, n):
        cue = cue_words[i - 1] if (cue_words and i - 1 < len(cue_words)) else None
        frame = None
        if used_transcript and cue is not None:
            frame, search_idx = word_start_frame(words, cue, fps, search_idx)
            if frame is None:
                notes.append("page %d (%s): cue word %r not found in transcript "
                             "— spaced evenly, RETIME." % (i, pages[i].get("name", ""), cue))
        if frame is None:
            if not used_transcript:
                notes.append("page %d (%s): no transcript — enterAt spaced evenly, "
                             "RETIME to the narration cue." % (i, pages[i].get("name", "")))
            elif cue is None:
                # transcript supplied, but no cue word covers this page-change
                notes.append("page %d (%s): no --cue-words entry — enterAt spaced "
                             "evenly, RETIME (pass another cue word)."
                             % (i, pages[i].get("name", "")))
            frame = i * hold_frames
        enter[i] = frame

    # strictly increasing enterAts (a later cue word could precede an earlier one)
    m_page = Mono(-1)
    enter = [m_page.push(e) for e in enter]

    # --- 2) which interaction produced each page --------------------------
    # last click/fill (with real coords) whose after_shot == this page's name
    def target_for(page_name):
        best = None
        for it in interactions:
            if it.get("after_shot") != page_name:
                continue
            if it.get("type") not in ("click", "fill"):
                continue
            if it.get("cx") is None or it.get("cy") is None:
                continue
            best = it  # keep the LAST one before the shot
        return best

    # --- 3) cursor keyframes + clicks --------------------------------------
    cursor_nom = [(0, START_X, START_Y)]  # nominal (frame,x,y); page 1 resting spot
    click_nom = []
    prev_pos = (START_X, START_Y)
    for i in range(1, n):
        tgt = target_for(pages[i].get("name", ""))
        if tgt is None:
            notes.append("page %d (%s): no interaction geometry — no cursor move / "
                         "click emitted; add a CursorKey + CLICK by hand if it needs one."
                         % (i, pages[i].get("name", "")))
            continue
        tx, ty = clamp01(tgt["cx"]), clamp01(tgt["cy"])
        arrival = enter[i] - CLICK_LEAD          # cursor lands just before the click
        hold_f = arrival - TRAVEL                 # hold prev pos, then ease over TRAVEL
        # hold keyframe (keeps the pointer parked until it needs to move)
        cursor_nom.append((max(0, hold_f), prev_pos[0], prev_pos[1]))
        cursor_nom.append((arrival, tx, ty))      # arrival ON the target
        click_nom.append(enter[i])                # ripple = enterAt = arrival + CLICK_LEAD
        prev_pos = (tx, ty)

    # enforce strict monotonicity on the final streams
    m_cur = Mono(-1)
    cursor_keys = [(m_cur.push(f), clamp01(x), clamp01(y)) for (f, x, y) in cursor_nom]
    m_clk = Mono(-1)
    clicks = [m_clk.push(c) for c in click_nom]

    # --- 4) page dicts with inferred transitions ---------------------------
    page_dicts = []
    for i in range(n):
        p = pages[i]
        d = {
            "img": p.get("img") or _img_path(p),
            "url": p.get("url_label", p.get("url", "")),
            "tabTitle": p.get("title", p.get("tabTitle", "")),
            "enterAt": enter[i],
        }
        if i > 0:
            trans = classify_transition(page_dicts[i - 1]["url"], d["url"])
            d["transition"] = trans
            if trans == "crossfade":
                d["transitionFrames"] = 5
        page_dicts.append(d)

    # --- 5) total duration --------------------------------------------------
    tail = max(1, int(round(TAIL_S * fps)))
    last_event = max([enter[-1]] + clicks + [f for (f, _, _) in cursor_keys])
    total_frames = last_event + tail
    return page_dicts, cursor_keys, clicks, notes, total_frames


def _img_path(page):
    """staticFile-relative image path for a page. capture_web writes only a bare
    `file` (basename) + the shot's out_dir; join them so the TSX points at the real
    asset under Remotion's public root (media/). Falls back to the basename."""
    f = page.get("file") or ""
    return f  # emitter prepends the out_dir hint; see emit_tsx


# ---------------------------------------------------------------------------
# TSX emission — mirrors the SKILL's canonical shot + the example shots exactly
# ---------------------------------------------------------------------------
def _fr(v):
    """Format a fraction: short, deterministic, always a decimal in [0,1]."""
    return ("%.4f" % float(v)).rstrip("0").rstrip(".") or "0"


def emit_tsx(comp_id, page_dicts, cursor_keys, clicks, total_frames, fps,
             notes, import_path, img_prefix):
    L = []
    a = L.append
    a("// generated by gen_screencast.py — tune freely")
    a("// (cursor path + clicks are computed from captured element geometry; retime,")
    a("//  reposition, and add a ken-burns zoom on the payoff by hand — see")
    a("//  .claude/skills/fake-screencast/SKILL.md).")
    if notes:
        a("//")
        a("// TODO:")
        for nt in notes:
            a("//   - " + nt)
    a("import React from 'react';")
    a("import { Screencast, ScreencastPage, CursorKey } from '%s';" % import_path)
    a("")
    dur = round(total_frames / float(fps), 2)
    a("export const compositionConfig = { id: '%s', durationInSeconds: %s, fps: %d, "
      "width: 1920, height: 1080 };" % (comp_id, _num(dur), fps))
    a("")

    # PAGES
    a("const PAGES: ScreencastPage[] = [")
    n = len(page_dicts)
    for i, d in enumerate(page_dicts):
        img = d["img"]
        if img_prefix and img and "/" not in img:
            img = img_prefix.rstrip("/") + "/" + img
        fields = [
            "img: %s" % json.dumps(img),
            "url: %s" % json.dumps(d["url"]),
            "tabTitle: %s" % json.dumps(d["tabTitle"]),
            "enterAt: %d" % d["enterAt"],
        ]
        if "transition" in d:
            fields.append("transition: %s" % json.dumps(d["transition"]))
        if "transitionFrames" in d:
            fields.append("transitionFrames: %d" % d["transitionFrames"])
        line = "  { " + ", ".join(fields) + " },"
        if i == n - 1:
            line += ("  // TODO: add a ken-burns zoom on the payoff, e.g. "
                     "zoom: { from: 1, to: 1.4, fx: 0.5, fy: 0.5, range: [%d, %d] }"
                     % (d["enterAt"], min(total_frames, d["enterAt"] + int(fps))))
        elif d.get("transition") == "cut":
            line += "  // navigation -> hard cut + new path"
        elif d.get("transition") == "crossfade":
            line += "  // in-page filter -> crossfade + same path"
        a(line)
    a("];")
    a("")

    # CURSOR
    a("const CURSOR: CursorKey[] = [")
    if cursor_keys:
        for (f, x, y) in cursor_keys:
            a("  { frame: %d, x: %s, y: %s }," % (f, _fr(x), _fr(y)))
    else:
        a("  // no interaction geometry in the manifest — place keyframes by hand.")
    a("];")
    a("")

    # CLICKS
    if clicks:
        a("const CLICKS = [%s];" % ", ".join(str(c) for c in clicks))
    else:
        a("const CLICKS: number[] = []; // no clicks derived — add ripple frames by hand.")
    a("")

    # component — identical shape to the SKILL's canonical shot + example shots
    a("const %s: React.FC = () => <Screencast pages={PAGES} cursor={CURSOR} clicks={CLICKS} />;"
      % comp_id)
    a("export default %s;" % comp_id)
    a("")
    return "\n".join(L)


def _num(x):
    """Render a float without a trailing .0 (so durationInSeconds looks hand-written)."""
    if float(x) == int(x):
        return str(int(x))
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _import_path_for(out_path):
    """Relative import from the emitted shot to remotion/src/lib/screencast.

    Example shots live at remotion/src/shots/<x>/Foo.tsx and import
    '../../lib/screencast'. Compute it from the out path so an unusual location
    still resolves; default to the example convention when we can't."""
    lib = os.path.join(ROOT, "remotion", "src", "lib", "screencast")
    out_dir = os.path.dirname(os.path.abspath(rp(out_path)))
    # only a shot living UNDER remotion/src can import the lib by a clean relative
    # path; anywhere else (a scratch/demo path outside the tree) would relpath into
    # the repo's own absolute segments, which is not valid TSX — fall back to the
    # example-shot convention (shots/<x>/Foo.tsx -> ../../lib/screencast).
    src_root = os.path.join(ROOT, "remotion", "src")
    try:
        under_src = os.path.commonpath([os.path.abspath(out_dir), src_root]) == src_root
    except ValueError:
        under_src = False
    if not under_src:
        return "../../lib/screencast"
    try:
        rel = os.path.relpath(lib, out_dir).replace(os.sep, "/")
    except ValueError:
        return "../../lib/screencast"
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def parse_args(argv):
    opts = {"manifest": None, "name": None, "out": None, "fps": DEFAULT_FPS,
            "transcript": None, "cue_words": None, "img_prefix": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--manifest":
            opts["manifest"] = argv[i + 1]; i += 2
        elif a == "--name":
            opts["name"] = argv[i + 1]; i += 2
        elif a == "--out":
            opts["out"] = argv[i + 1]; i += 2
        elif a == "--fps":
            opts["fps"] = int(argv[i + 1]); i += 2
        elif a == "--transcript":
            opts["transcript"] = argv[i + 1]; i += 2
        elif a == "--cue-words":
            opts["cue_words"] = argv[i + 1]; i += 2
        elif a == "--img-prefix":
            opts["img_prefix"] = argv[i + 1]; i += 2
        else:
            raise SystemExit("unknown arg: %s" % a)
    return opts


def main():
    o = parse_args(sys.argv[1:])
    if not o["manifest"] or not o["name"]:
        raise SystemExit(
            "usage: gen_screencast.py --manifest <manifest.json> --name ShotName "
            "--out <path.gen.tsx> [--fps 60] [--transcript <t.json> --cue-words \"w1,w2\"] "
            "[--img-prefix projects/<p>/<svc>]")

    mf = load_manifest(rp(o["manifest"]))
    pages = mf["pages"]
    interactions = mf["interactions"]

    words = load_words(rp(o["transcript"])) if o["transcript"] else []
    cue_words = None
    if o["cue_words"]:
        cue_words = [w.strip() for w in o["cue_words"].split(",") if w.strip()]

    fps = o["fps"]
    comp_id = pascal_case(o["name"])

    # img_prefix: where the PNGs live under Remotion's public root. Prefer an
    # explicit flag; otherwise use the manifest's out_dir with any leading
    # "media/" stripped (staticFile paths are relative to media/).
    img_prefix = o["img_prefix"]
    if img_prefix is None:
        od = ""
        try:
            with open(rp(o["manifest"]), "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                od = raw.get("out_dir") or ""
        except Exception:
            od = ""
        od = od.replace("\\", "/")
        if od.startswith("media/"):
            od = od[len("media/"):]
        img_prefix = od

    page_dicts, cursor_keys, clicks, notes, total_frames = build_plan(
        pages, interactions, fps, words, cue_words)

    out_path = o["out"] or ("remotion/src/shots/generated/%s.gen.tsx" % comp_id)
    import_path = _import_path_for(out_path)
    tsx = emit_tsx(comp_id, page_dicts, cursor_keys, clicks, total_frames, fps,
                   notes, import_path, img_prefix)

    abs_out = rp(out_path)
    os.makedirs(os.path.dirname(abs_out), exist_ok=True)
    with open(abs_out, "w", encoding="utf-8") as f:
        f.write(tsx)

    try:
        shown = os.path.relpath(abs_out, ROOT)
    except ValueError:
        shown = abs_out
    print("wrote %s" % shown)
    print("  id=%s  pages=%d  cursorKeys=%d  clicks=%d  frames=%d (%.2fs @ %dfps)"
          % (comp_id, len(page_dicts), len(cursor_keys), len(clicks), total_frames,
             total_frames / float(fps), fps))
    if notes:
        print("  %d TODO(s) emitted as comments — retime/reposition, then render & look."
              % len(notes))
    print("  next: cd remotion && npm run gen && npm run studio   (then tune)")


if __name__ == "__main__":
    main()
