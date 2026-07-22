# IMPROVEMENTS.md — what this fork changes, and why

This fork keeps upstream's discipline (QA gates, generated registry, brand contract) and removes
its four biggest adoption blockers. Everything is **additive and backward-compatible**: on the
original author's setup (NVIDIA + 59.94fps) every default behaves exactly as before.

## Install (2 commands after clone)

```bash
python tools/apply_fork_docs.py     # installs the updated + new skill docs into .claude/skills/
cd remotion && npm install && npm run gen
```

Why step 1 exists: this fork was built in a sandboxed agent environment where writing into
`.claude/` is permission-gated, so the skill-doc updates ship staged under `fork-docs/claude-skills/`.
The installer copies them into place (with backups of anything it replaces). Everything else in the
fork is already live in the tree.

## 1. The pipeline now runs on any machine and any frame rate

**The problem upstream:** `render_cuts.py` and `make_proxy.py` hardwired `-hwaccel cuda` +
`h264_nvenc`/`hevc_nvenc` with no fallback — the master render only worked on NVIDIA. The A/V-drift
re-stamp hardcoded `60000/1001`, and the cut editor hardcoded `1/59.94` frame-stepping — the whole
cut stage silently assumed the author's camera.

**What changed:**
- **NEW `tools/encoders.py`** — probes ffmpeg once and picks the best available encoder:
  H.264 `h264_nvenc → h264_videotoolbox → libx264`; HEVC `hevc_nvenc → hevc_videotoolbox → libx265 →
  libx264 (8-bit, loud warning)`. `-hwaccel cuda` is emitted only when NVENC is chosen. 10-bit
  main10 is kept wherever the encoder supports it. `CYE_ENCODER=<name>` forces a specific encoder.
  Also owns fps probing: `probe_fps()` returns the footage's exact `r_frame_rate` fraction.
- **`tools/render_cuts.py`** — all encodes + hwaccel flags now come from `encoders.py`. The final's
  CFR re-stamp uses the **probed source fps** (any camera, not just 59.94). The hard-won MPEG-TS
  drift fixes and their comments are preserved verbatim.
- **`tools/make_proxy.py`** — same treatment.
- **`tools/editor/`** — frame stepping is fps-aware: the server ffprobes the proxy and exposes fps;
  `?fps=` query param overrides; 59.94 remains the fallback.

**Proof (run in a GPU-less sandbox, so the fallback path is real):** synthetic 30fps and
60000/1001 clips + fabricated cuts.json, rendered through preview AND final modes. Results: correct
codecs (libx265 Main10 finals), output fps == source fps, and **A/V duration deltas of 0.6–0.7ms**
(budget: 50ms). `CYE_ENCODER=libx264` forcing verified.

## 2. Fake screencasts stop being hand-measured: capture → TSX codegen

**The problem upstream:** authoring a fake screencast meant hand-computing every click target as a
viewport fraction (pixel position ÷ image width), hand-placing cursor keyframes, then a
render-look-nudge-rerender loop per cursor move. The skill's own gotcha list says "each of these
cost a render to find." Meanwhile `capture_web.py` already drove real Playwright Chromium — and
threw away the element geometry Playwright knows.

**What changed:**
- **`tools/capture_web.py`** — while executing a choreography, now harvests each `click`/`fill`
  target's bounding-box center as viewport fractions into the manifest (`interactions[]`,
  `schema: capture_web/2`; old fields intact, Playwright still an optional import).
- **NEW `tools/gen_screencast.py`** — offline compiler: manifest → complete shot TSX. Emits PAGES
  (with the navigation-vs-filter transition **inferred from the URLs** — the skill's "single biggest
  tell", now computed from data), the CURSOR path (arrival 2 frames before each click, eased
  travel, no teleporting), CLICKS, all frame streams strictly monotonic and all fractions clamped.
  `--transcript` + `--cue-words` pins each page-change to a word's start time from
  `edited-transcript.json`. Without a transcript: evenly spaced cues with RETIME TODOs.
- **NEW `tools/fixtures/capture-demo-manifest.json`** — try it offline:
  `python tools/gen_screencast.py --manifest tools/fixtures/capture-demo-manifest.json --name Demo --out /tmp/Demo.gen.tsx`

**Proof:** generated output verified for monotonic frames, in-range fractions, exact
`Screencast`/`ScreencastPage`/`CursorKey` prop fidelity against `lib/screencast.tsx`, correct
word→frame math (ms/1000×fps), and legacy (v1 bare-list) manifests still compile.

## 3. Real screen recordings become a first-class citizen

**The problem upstream:** the pipeline could ONLY simulate screen content. For technical audiences
that's a trust problem (a coded clone proves nothing about speed or output), and for
latency/live-result beats it's simply impossible — upstream itself says "use a real recording" but
gave real recordings no path into the branded system.

**What changed:**
- **NEW `remotion/src/lib/realscreencast.tsx`** — `RealScreencast` plays a real capture
  (render-safe `OffthreadVideo`, correct `trimBefore`/`trimAfter` for Remotion ≥4.0.319, **muted by
  default** so it never fights the master voice) inside the existing branded browser chrome or a
  bare kit card, with fraction-based **zoom** keyframes (monotonic by construction), **highlight**
  rects (brand outline + backdrop dim), and **clickPulse** rings. Same coordinate conventions as
  `screencast.tsx`.
- **NEW skill `/real-screencast`** (staged; installed by `apply_fork_docs.py`) — the decision gate
  (real = proof, fake = idealized walkthrough), recording tips, media placement rules, a complete
  worked example shot, and the QA loop.

**Proof:** registry discovery verified (`npm run gen` found a demo shot using it, 39 shots, zero
warnings; demo then removed, clean 38 baseline restored). Every import cross-checked against the
pinned Remotion 4.0.486 API.

## 4. QA gets machine eyes, and the upload gate actually gates

**The problem upstream:** every TSX shot required a human to render frames and READ them — the
single biggest per-video time cost. And the last gate before YouTube (`yt_upload.py`) never checked
the one thing upstream's own docs call definitive: `v:0 duration == a:0 duration`.

**What changed:**
- **NEW `tools/verify_frames.py`** — three-tier visual QA, advisory like `verify_cut.py`:
  1. **Structural** (always): decodes, not blank, not black/white plate.
  2. **Golden frames**: `--approve` snapshots approved stills to `videos/<p>/qa/goldens/`;
     re-renders compare RMS + perceptual hash and flag drift (`--strict-golden` to fail).
  3. **AI assertions** (`--ai`, needs `GEMINI_API_KEY`): each cue frame checked against its
     `expect` text via Gemini.
  Shots declare their own cues: `export const QA_CUES = [{ frame, expect }]` — machine-readable
  intent replacing ad-hoc `const CUE = 240; // "…"` comments. A stochastic bad render now fails a
  check instead of consuming your attention.
- **`tools/yt_upload.py`** — preflight ffprobe compares v:0 vs a:0 before upload: loud warning at
  >50ms drift, `--strict` aborts, default unchanged (warn-and-continue). Runs in `--dry-run` too,
  so it's CI-gateable.

**Proof:** `verify_frames.py --self-test` → 16/16 assertions pass (blank/black detection, golden
set/compare/drift, cue parsing). Upload preflight exercised against real ffmpeg-made media:
matched → OK; 300ms drift → loud warn (default) and abort (`--strict`); <50ms → OK; no audio →
graceful skip.

## Compatibility guarantees

- NVIDIA + 59.94fps setups: identical encoder flags, identical defaults, no behavior change.
- All new capabilities are opt-in (`--cues`, `--ai`, `--strict`, `CYE_ENCODER`, new tools/skills).
- No new hard dependencies. Optional: Playwright (live capture only), Pillow (verify_frames tiers
  1–2), google-genai (tier 3 — already in requirements.txt).

## Known limitations (honest list)

- videotoolbox args are correct per Apple's encoder docs but were not exercised (no macOS in the
  build sandbox). NVENC path unchanged from upstream. CRF↔CQ mappings are quality-intent
  equivalents, not byte-parity.
- `gen_screencast.py` deliberately does NOT auto-generate the ken-burns zoom (focal intent can't be
  guessed) — it emits a TODO on the payoff page. Scroll steps are recorded but left as data.
- TSX was verified by registry discovery + source-level API cross-check against Remotion 4.0.486;
  run `cd remotion && npx tsc --noEmit` after `npm install` as a belt-and-braces check.
- `verify_frames.py` golden thresholds are advisory defaults; calibrate after your first few
  `--approve` cycles. The AI tier needs one live run to confirm your account's flash model id.

## What's next (the rest of the roadmap)

- **Motion-grammar brand divergence** — `/brand-setup` 2.0 that interviews for easing, rhythm, and
  transition vocabulary, not just palette/fonts (today every fork shares upstream's motion
  silhouette).
- **CTR/retention flywheel** — `yt_stats.py` → per-video calibration log → packaging priors +
  `cuts.json` pacing knobs that learn the channel's rhythm.
- **Shorts/localization emitters** — `uiScale` 9:16 reflow of screen beats; string-table extraction
  for localized re-renders.
- **Single-transcription verify** — local Whisper option for `verify_cut.py`'s second pass (kills
  the second paid AssemblyAI call per iteration).
- **Per-video cost/time telemetry** — log API spend + wall-clock per stage; publish real numbers.
- **Disclosure stance** — a deliberate policy (badge or description line) for simulated screencasts;
  platform rules on synthetic content are tightening, and retrofitting after a callout is expensive.
