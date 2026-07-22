# REVIEW PACKET — claude-youtube-editor fork

**Audience:** an automated/agent code reviewer (or a careful human).
**Request:** approve or reject this fork's changes before the owner builds on them.

- **Upstream base:** `github.com/hassancs91/claude-youtube-editor` @ `a8abff811d73cc85e1ecfe2e7308f64e0e234609` ("Initial import", 2026-07-17). MIT license.
- **Change size:** 24 files, **+3,128 / −39** lines (see `MANIFEST.txt`; exact diff in `changes.patch`).
- **Packet contents:** `repo/` (complete improved tree, no .git) · `changes.patch` (git apply-able onto the base) · `MANIFEST.txt` (file statuses + diffstat) · `verify.sh` (reproduce the verification yourself) · this file.

## Context in five lines

Upstream is a Claude Code-driven YouTube post-production pipeline (transcript-based cuts, Remotion TSX overlays synced to word timestamps, SFX, thumbnails, upload). A five-persona design review found it sound in mechanism but blocked for adoption by four things: it only rendered on the author's NVIDIA+59.94fps setup; its flagship "fake screencast" authoring required hand-measured cursor math; real screen recordings had no path into the branded system; and QA was a mandatory human frame-read with an upload step that verified nothing. This fork removes exactly those four blockers, additively.

## The four workstreams

### W1 — Machine/fps portability
- **Files:** `tools/encoders.py` (NEW, 279 ln), `tools/render_cuts.py` (+71/−mods), `tools/make_proxy.py`, `tools/editor/server.py` (+23), `tools/editor/index.html` (+17/−2)
- **Change:** all encoder + hwaccel decisions move into `encoders.py` (probe `ffmpeg -encoders` once; ladders H.264 `h264_nvenc→h264_videotoolbox→libx264`, HEVC `hevc_nvenc→hevc_videotoolbox→libx265→libx264+loud 8-bit warning`; `-hwaccel cuda` only when NVENC chosen; 10-bit kept where supported; `CYE_ENCODER=` override). The final mux's CFR re-stamp uses `probe_fps(source)` (exact `r_frame_rate` fraction) instead of hardcoded `60000/1001`. Editor frame-step resolves `?fps=` param → server-probed proxy fps → 59.94 default.
- **Evidence (executed in a GPU-less sandbox, i.e. the fallback path for real):** synthetic 30fps and 60000/1001 clips rendered through preview AND final modes → correct codecs (libx265 Main10 finals), output fps == source fps, A/V duration deltas **0.6–0.7 ms** (gate: 50 ms). `CYE_ENCODER=libx264` forcing verified. Upstream's MPEG-TS drift-fix logic and comments preserved verbatim.

### W2 — Capture → TSX codegen
- **Files:** `tools/capture_web.py` (+112, additive), `tools/gen_screencast.py` (NEW, 497 ln), `tools/fixtures/capture-demo-manifest.json` (NEW)
- **Change:** during Playwright choreography, each `click`/`fill` target's bounding-box center is recorded as viewport fractions into the manifest (`interactions[]`, `schema: capture_web/2`; v1 bare-list manifests still accepted by the generator). `gen_screencast.py` compiles a manifest into a complete shot TSX: PAGES with navigation-vs-filter transition inferred from URLs, eased CURSOR path arriving 2 frames before each CLICK, all frame streams strictly monotonic, fractions clamped [0,1]; `--transcript` + `--cue-words` pins page-changes to word start times (ms→frames); no transcript → evenly spaced cues with RETIME TODOs.
- **Evidence:** generated output assert-checked for monotonic frames, in-range fractions, exact `Screencast`/`ScreencastPage`/`CursorKey` identifier fidelity vs `remotion/src/lib/screencast.tsx`, word→frame math (1650ms → frame 99 @60fps), legacy-manifest acceptance. Reproduce: `verify.sh` step 4.

### W3 — Real recordings, first-class
- **Files:** `remotion/src/lib/realscreencast.tsx` (NEW, 270 ln), staged skill `fork-docs/claude-skills/real-screencast/SKILL.md`
- **Change:** `RealScreencast` component plays a real capture via `OffthreadVideo` (render-deterministic), muted by default (never fights master narration), optional wrap in the existing `WebBrowserFrame` chrome, fraction-based zoom keyframes (sorted+deduped → monotonic `interpolate` by construction), highlight rects (brand outline + dim), click-pulse rings. Uses `trimBefore`/`trimAfter` (correct API for the pinned Remotion 4.0.486; the older `startFrom`/`endAt` were renamed at 4.0.319).
- **Evidence:** registry discovery clean (demo shot found by `npm run gen`, then removed; baseline restored); **full `npx tsc --noEmit` passes** on the tree as shipped. Reproduce: `verify.sh` step 6.

### W4 — Machine QA + a real last gate
- **Files:** `tools/verify_frames.py` (NEW, 755 ln), `tools/yt_upload.py` (+82, additive), fixtures
- **Change:** three-tier advisory visual QA mirroring upstream's `verify_cut.py` philosophy — tier 1 structural (decodes, not blank/black/white), tier 2 golden frames (`--approve` baseline; RMS + perceptual-hash drift, `--strict-golden` to fail), tier 3 optional `--ai` Gemini assertions against a `QA_CUES = [{frame, expect}]` convention shots can export. `yt_upload.py` gains a preflight: ffprobe v:0 vs a:0 durations, loud warn >50ms, `--strict` aborts, default behavior unchanged; also runs under `--dry-run`.
- **Evidence:** `--self-test` = 16/16 assertions; upload gate exercised against real ffmpeg-generated media (clean → OK; 300ms drift → warn/continue by default, abort under `--strict`; <50ms → OK; no audio stream → graceful skip). Reproduce: `verify.sh` step 3 (+ upload gate by inspection or live).

### Docs
- `CLAUDE.md` updated directly (skill table row, layout, machine-adaptive rendering convention, QA and upload notes).
- **Why `fork-docs/` exists:** the build environment write-protects the `.claude/` tree (agent-config injection guard), so updated skill docs ship staged under `fork-docs/claude-skills/` with `tools/apply_fork_docs.py` (dry-run + backups) to install them into `.claude/skills/` on the owner's machine. Reviewer check: confirm staged files == intended edits of their `.claude/skills/` counterparts (they are full-file copies of upstream's originals + surgical insertions; `verify.sh` step 5 shows the mapping).
- `IMPROVEMENTS.md` (repo root): the owner-facing changelog with limitations and the remaining roadmap.

## Invariants claimed — review against these

1. **Upstream-identical defaults on the author's setup:** on NVIDIA + 59.94fps footage, encoder selections and flags match upstream's exact values (`h264_nvenc -preset p4 -rc vbr -cq 30/29`, `hevc_nvenc -preset p5 … -cq 19`, `-hwaccel cuda`).
2. **Additive only:** no existing CLI flag, function, file, or behavior removed. New behaviors are opt-in (`--cues`, `--ai`, `--strict`, `--strict-golden`, `CYE_ENCODER`, `?fps=`, new tools/skills).
3. **No new hard dependencies:** stdlib-only for `encoders.py`/`gen_screencast.py`/`apply_fork_docs.py`; Pillow (verify_frames tiers 1–2), google-genai (tier 3), Playwright (live capture) are optional and import-guarded; google-genai was already in upstream's `requirements.txt`.
4. **Upstream conventions honored:** hard-won A/V drift fixes and comments preserved; scratch-dir rules; media placement rules; advisory-QA philosophy; generated-registry workflow.

## Where to focus review (highest-risk diffs, in order)

1. **`tools/render_cuts.py`** — the re-stamp `-r <probed_fps>` placement (before `-i`, final mux only; preview mux unchanged) and that the MPEG-TS concat path is untouched.
2. **`tools/encoders.py`** — the NVENC-cq ↔ x264/x265-crf quality mappings (intent-equivalent, not byte-parity: preview crf 23/28, final crf 16/18) and the 10-bit pix_fmt selections per encoder.
3. **`tools/capture_web.py`** — manifest schema v2 wrapping (`{pages, interactions, viewport, schema}` vs v1 bare list): confirm the only in-repo consumer (`gen_screencast.py`) accepts both, and that Playwright stays lazily imported.
4. **`remotion/src/lib/realscreencast.tsx`** — Remotion API usage (`OffthreadVideo`, `trimBefore`/`trimAfter` in frames), rules-of-hooks ordering, monotonic interpolate construction, the explicit-region-dims gotcha (not `inset:0` under the transformed browser frame).
5. **`tools/yt_upload.py`** — confirm the 82-line addition changes nothing by default (docstring, `strict=False` parameter, preflight call, `--strict` flag).

## Known limitations (disclosed, not hidden)

- videotoolbox arguments are per Apple's encoder docs but unexercised (no macOS in the build sandbox); NVENC path is unchanged upstream code.
- CRF↔CQ mappings are quality-intent equivalents; byte-parity with NVENC output is not claimed on non-NVIDIA machines.
- `gen_screencast.py` intentionally does not auto-generate ken-burns zooms (focal intent isn't guessable) — emits a TODO; scroll steps recorded as data only.
- `verify_frames.py` golden thresholds (RMS 8.0, hash 6) are advisory defaults pending real-footage calibration; tier-3 needs one live Gemini call to confirm the account's flash model id.
- Multi-fps projects: the final re-stamp uses the first clip's fps (upstream already assumes uniform-fps footage).
- The build sandbox ran Python 3.9; all code is 3.9-compatible while the repo targets 3.10+ (superset — fine).

## Provenance

Built 2026-07-22 by an AI agent team (four parallel builder agents + an orchestrator that integrated, verified, and packaged), in a CPU-only Linux sandbox with ffmpeg/ffprobe, Node 24, Python 3.9. Every evidence claim above was actually executed there; nothing in the evidence sections is projected or assumed. The full typecheck (`npm install && npx tsc --noEmit`) was executed and passed on the exact tree in `repo/`.

## Verdict requested from the reviewer

APPROVE if: `verify.sh` passes on your machine, the five focus-diffs read clean, and the invariants hold against `changes.patch`.
REJECT (with specifics) if any invariant is violated — the owner will route findings back to the build agent for fixes.
