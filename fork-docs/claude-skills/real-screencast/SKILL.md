---
name: real-screencast
description: Wrap a REAL screen capture (.mp4) in the same branded, word-synced chrome the fake screencast uses — a real recording with the kit's browser frame (or a bare rounded card), a ken-burns zoom that leans into the payoff, a brand-colored highlight rect over a result, and click-pulse rings for emphasis, all played render-safe via OffthreadVideo with the capture audio muted so the master cut's voice is never fought. Use when a beat needs GENUINE PROOF that something actually ran — live results appearing, real latency/speed, streaming output, a state you cannot fake credibly — and you have (or will record) an actual screen capture: "show the real recording", "use my screen capture", "prove the latency", "drop in the mp4 of the live run", "wrap this capture in the brand chrome". Built on remotion/src/lib/realscreencast.tsx. This is a technique WITHIN step 2 (make-tsx): defer timeline/render/bake orchestration to make-tsx and raw crash-free TSX rules to vidtsx-2d-generator. Not for idealized walkthroughs or unreachable/resolution-perfect UI (use fake-screencast), and not for a single static page clone (use WebBrowserFrame directly).
---

# real-screencast — a real .mp4 capture, in the branded system

Take an actual screen recording and flow it through the *same* branded, word-synced pipeline the fake screencast uses: persistent browser chrome with a URL bar, a slow **ken-burns zoom** that leans into the payoff exactly as it's named, a **brand-colored highlight** rect over the result, **click-pulse** rings for emphasis, and a constant brand backdrop. Render-safe by construction (`OffthreadVideo`, not `<Video>`) and **muted by default** so the capture's own audio never fights the master cut's narration. This is the counterpart to `fake-screencast`: that skill *simulates* a recording from screenshots; this one plays the real thing and layers the same moves on top.

This is a beat-building technique inside **`make-tsx`** (step 2). Use `make-tsx` for the timeline.json / render / bake mechanics and the sync-to-words principles; use **`vidtsx-2d-generator`** for the low-level rules that keep a Remotion file from crashing (frame-based only, monotonic `interpolate`, `Easing.bezier`, no `useState`/`useEffect`). The shot you write here follows those rules.

## When this is the right tool (decision gate)

Faking is a *choice* now, not the only mode. Pick by one question: **does the audience have to trust it actually ran?**

- **Real screencast (this skill)** — the point is **proof**: real latency, a live result appearing, streaming tokens, a real error, throughput a technical audience would (rightly) not believe from a coded clone. A coded clone proves nothing about speed or output; a real capture does. You have the recording or can make one.
- **Fake screencast (`fake-screencast`)** — an *idealized* walkthrough: where things are, the steps, a resolution-perfect UI, an unreachable/edge state, or a path that's easier to stage from screenshots than to record. Best when the beat is about *the steps*, not raw speed/output.
- **Single static page clone** — one page, no navigation/cursor/proof (e.g. a pricing page you just highlight/scroll): use `WebBrowserFrame` from `lib/browser.tsx` directly. Don't reach for the recording machinery.

Trust rationale, in one line: **a coded clone can show anything, so it proves nothing about speed or output — a real capture is the only thing that earns "it actually ran this fast."**

## Step 1 — record and place the capture

Record the real thing. Tips (honest about what matters and what doesn't):

- **Capture at the comp resolution or higher.** Author shots at 1920×1080; a 1080p (or 4K) capture stays crisp, especially once a zoom pushes in. A sub-1080p capture will look soft when zoomed.
- **fps mismatch is fine.** `OffthreadVideo` resamples to the composition's fps, so a 60fps or 25fps capture plays correctly in a 30fps comp. Don't re-encode just to match fps.
- **Trim in the shot, not in an NLE.** Don't open a video editor to cut the clip — pass `startFromSec` / `endAtSec` and the component trims the source (via Remotion `trimBefore`/`trimAfter`, in frames). Keep the raw capture whole and reproducible.
- **Let the capture's audio be.** It's muted by default; you don't need to strip it. The master cut's voice carries the beat.
- **One clean take of the real moment.** If the proof is latency, record the actual wait — don't speed it up in an editor (use `playbackRate` in the shot if a beat genuinely needs it, and say so).

Place it per the CLAUDE.md media rules — it's generated FOR ONE video, so it lives under the project, never in `media/library/` (that's cross-video reusable assets only):

```
media/projects/<project>/captures/<name>.mp4
```

Reference it as the `staticFile`-relative path the component expects: `projects/<project>/captures/<name>.mp4`. Raw captures are git-ignored (footage never goes to git) — the reproducible pipeline is the shot TSX + timeline, not the .mp4.

## Step 2 — the library API (`remotion/src/lib/realscreencast.tsx`)

`<RealScreencast src startFromSec endAtSec playbackRate muted chrome url tabTitle favicon zoom highlights clickPulses box glow accent appearAt uiScale />`.

**Coordinates are FRACTIONS** (0..1) so they survive any resize of the video box — same convention as `screencast.tsx`:
- **zoom `cx`/`cy` → fraction of the VIDEO** (focal point of the push; the layer recenters so that point holds its place as it scales).
- **highlight `x`/`y`/`w`/`h` → fraction of the VIDEO** (the rect over the footage).
- **clickPulse `x`/`y` → fraction of the VIDEO** (where the ring blooms).

Props:
- `src` (required) — staticFile-relative path to the capture, e.g. `projects/<project>/captures/x.mp4`.
- `startFromSec?` / `endAtSec?` — trim the *source* footage in seconds (converted to frames at comp fps).
- `playbackRate?` (default `1`), `muted?` (**default `true`** — leave it unless the capture audio IS the proof and the master is silent there).
- `chrome?` — `"browser"` (default) wraps the capture in `WebBrowserFrame` with `url` / `tabTitle` / `favicon` passed through; `"none"` renders it bare in the kit's standard rounded-corner + shadow card.
- `zoom?: {frame, cx, cy, scale}[]` — `scale: 1` = fit (no push). **Frames are re-sorted internally and duplicate-frame keys dropped**, so the `interpolate` inputs are monotonic by construction no matter how you list them.
- `highlights?: {fromFrame, toFrame, x, y, w, h}[]` — brand-colored rounded outline + a subtle backdrop dim over the rest of the frame, fading in/out on its window.
- `clickPulses?: {frame, x, y}[]` — expanding brand-accent rings for click emphasis.
- `box?` (default `{x:60, y:63, w:1800, h:954}`), `glow?` (backdrop glow, default `COLORS.signal`), `accent?` (highlight + ring color, default `COLORS.accent`), `appearAt?`, `uiScale?` (forwarded to the browser chrome).

## Step 3 — timing + coordinates

- **Frame 0 = the shot's `master_in_s`.** For a narration cue at `t` seconds: `frame = round((t − master_in_s) × fps)`. Find the word in `videos/<project>/work/edited-transcript.json` — it carries word-level **start/end in ms**, so `t = start_ms / 1000` and `frame = round((start_ms/1000 − master_in_s) × fps)`.
- **Put the *result* on the word.** End a `zoom` push and start a `highlight` on the frame the payoff word is spoken (or a few frames before, so the box has landed by the word). Fire a `clickPulse` ~2f before the action's result appears in the capture, on the noun that names it.
- **Find a fraction from the capture.** Element's pixel position ÷ capture width/height ≈ the video fraction (the video fills its layer, `objectFit: cover`). Set the zoom focal / highlight rect / pulse there, then **render and look** (Step 4) — nudge the fraction until it sits right.
- **`startFromSec`/`endAtSec` select which slice of the capture plays** while the shot's own frames advance from 0. Trim so the real moment (the wait, the result) lines up with the narration beat; don't trim in an editor.

## Gotchas (each of these costs a render to find)

- **Zero-height containing block.** `WebBrowserFrame` wraps children in a `transform`ed div, which becomes the containing block for absolute descendants and collapses to 0 height. The component already gives the video layer **explicit region dims** (`box.w` × `box.h − chromeH()`), not `inset:0` — keep that if you extend it.
- **Use `OffthreadVideo`, never `<Video>` / `<Html5Video>` for renders.** `<Video>` seeks a real `<video>` element and produces non-deterministic frames under the bulk renderer; `OffthreadVideo` extracts the exact frame with FFmpeg. The component already does this.
- **Trim props are FRAMES, in seconds-facing clothing.** You pass `startFromSec`/`endAtSec` (seconds); the component multiplies by fps and rounds to integer frames for Remotion's `trimBefore`/`trimAfter`. Don't pass frames to the seconds props.
- **Zoom recenters as it scales.** `cx`/`cy` is both the transform-origin AND a translate target, so the focal point stays put as it pushes in. If a push drifts off the result, move `cx`/`cy`, not the box.
- **Capture audio is muted by default on purpose.** If you flip `muted={false}`, you're mixing the capture's audio under the master voice — only do that when the capture audio IS the proof and the master is silent there, and spot-check the mix.
- **Render `--scale=1` for the preview** (1080p, correct for `bake.py`); re-render `--scale=2` for the 4K60 final. A higher-res capture pays off here.

## The worked example — copy this choreography

A realistic ~15s proof beat: the real playground capture in browser chrome, a click pulse on "run" as it's clicked, a ken-burns zoom leaning into the result as the latency number is spoken, and a highlight boxing that result. This is the reference implementation — start from it and retime the frames to your cue.

```tsx
import React from 'react';
import { COLORS } from '../../brand';
import { RealScreencast, ZoomKey, HighlightRect, ClickPulse } from '../../lib/realscreencast';

// =============================================================================
// PROOF beat — the real live run, in the branded browser chrome (P: genuine
// proof of latency, not a coded clone). Master span ~120.0–135.0.
//   click "Run" 123.2 -> (123.2-120)*30 ≈ f96
//   result lands + "in under two hundred milliseconds" 126.0 -> f180
// =============================================================================
export const compositionConfig = { id: 'LatencyProof', durationInSeconds: 15, fps: 30, width: 1920, height: 1080 };

// hold fit while the prompt is set up, then push into the result (right-center)
const ZOOM: ZoomKey[] = [
  { frame: 0, cx: 0.5, cy: 0.5, scale: 1 },
  { frame: 150, cx: 0.5, cy: 0.5, scale: 1 },     // still fit as the run fires
  { frame: 180, cx: 0.66, cy: 0.44, scale: 1.6 }, // lean into the result on the latency word
];
// box the result region once we're zoomed in, and hold it through the payoff
const HIGHLIGHTS: HighlightRect[] = [
  { fromFrame: 186, toFrame: 300, x: 0.52, y: 0.30, w: 0.34, h: 0.26 },
];
// pulse the Run button ~2f before the result starts streaming in the capture
const CLICKS: ClickPulse[] = [
  { frame: 96, x: 0.28, y: 0.62 },
];

const LatencyProof: React.FC = () => (
  <RealScreencast
    src="projects/video-1/captures/latency-demo.mp4"
    startFromSec={2}          // skip 2s of setup at the head of the capture
    endAtSec={17}             // play a 15s slice (matches the comp duration)
    chrome="browser"
    url="localhost:5173/playground"
    tabTitle="Playground · Live"
    zoom={ZOOM}
    highlights={HIGHLIGHTS}
    clickPulses={CLICKS}
    glow={COLORS.signal}
    accent={COLORS.accent}
    appearAt={2}
  />
);
export default LatencyProof;
```

For a bare capture with no browser (e.g. a full-screen terminal run), drop the chrome and let the rounded card + shadow do the framing:

```tsx
<RealScreencast src="projects/video-1/captures/build.mp4" chrome="none"
  zoom={[{ frame: 0, cx: 0.5, cy: 0.5, scale: 1 }, { frame: 120, cx: 0.5, cy: 0.8, scale: 1.4 }]} />
```

## Step 4 — verify, then hand back to make-tsx

- **Render + screenshot at EACH cue** (never one still): `node remotion/scripts/render-all.mjs --scale=1 <Id>`, then pull frames with ffmpeg at the **click** frame, the **zoom payoff** frame, and the **highlight** frame (`ffmpeg -ss <t> -i out/<id>.mp4 -frames:v 1 f.jpg`). Confirm the pulse lands on the button, the zoom frames the right result, the highlight boxes the right thing, and the trimmed slice lines up with the narration. Iterate the fractions/frames and re-render.
- **Machine-triage first**: `python tools/verify_frames.py videos/<project> <CompId> --cues` renders every `QA_CUES` frame and runs structural + golden-frame checks (`--ai` to assert each cue's `expect` semantically). `--approve` once the look is right.
- **Confirm the capture actually resolves.** If the .mp4 isn't in `media/projects/<project>/captures/` yet, the render will show a black video layer — that's the signal to record/place it (or leave a clearly-noted `pending_recording` slot in `timeline.json` per `make-tsx`).
- Then follow `make-tsx` for the rest: update `timeline.json` (swap/retime the span, drop any `pending_recording` note), `python tools/bake.py`, and **spot-check composited frames** of the shot over the real master at the beat + both boundaries.
- If you improve the component (a caption layer, a cursor path over the footage, per-region dim), promote it back into `lib/realscreencast.tsx` so future videos inherit it.

**Read `remotion/src/lib/realscreencast.tsx` before building one** — it's the engine, and its props are the documentation for every move above.
