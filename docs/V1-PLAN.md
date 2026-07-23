# V1.0 PLAN — "the owner edits a real video today"

Definition of Done v1.0: the owner can take a project folder (script + voiceover + b-roll/images),
get it into the workbench, place scenes, import or request generated takes, compare/promote,
range-preview, bake the full video, verify, and upload. Manual placement is FINE for v1.0 —
the auto-planner is v1.1.

**Working rules (unchanged):** one slice = one PR, opened non-draft when CI is green and the PR
template is answered. Fable executes an audit on every PR; owner merges. No scope creep: anything
not listed here goes to v1.1. If a contract question blocks you >30 minutes, open a GitHub issue
titled `Q: ...` for Fable instead of guessing.

## Slice order (each its own PR, in this order)

### PR #3 — Range bake + render/bake jobs polish  (SMALL)
- `bake.py --from SECONDS` (mirror of `--end`); output to `work/preview/range-<a>-<b>.mp4`.
- `POST /api/bake/range {from_s,to_s}` (If-Match; durable job record; progress; cancel).
- UI: "Preview bake ±2s" on the selected scene (per the mockup inspector) + job chip progress.
- Tests: range duration ±0.1s, v:0==a:0 ±50ms, job cancel kills the process group, orphan
  reconciliation still holds.

### PR #4 — Courier inbox + generate jobs  (MEDIUM — this is the "scene 5 needs something" loop)
- Provider interface per docs/contracts/v2.1 job schema: submit(spec)/poll/cancel with
  **courier fulfillment**: a generate job creates `work/inbox/<job_id>/` + writes the job file
  (state=submitted, spec={prompt, provider_hint, duration_s}).
- A poller (server-side, mtime-based, no new deps) ingests files landing in that inbox dir:
  probe → conform (reuse PR #2 pipeline) → attach as job candidates → state=awaiting_pick.
- Picking a candidate promotes it to a scene take (existing take record shape, provenance
  carries the spec + job_id).
- UI: engine-aware "Revise → submit generation" on fable/hyperframe scenes (spec editor:
  prompt + duration prefilled from the scene cue); candidates appear in the Takes drawer.
- Tests: job lifecycle to awaiting_pick, inbox ingest conforms + dedupes, pick=promote,
  cancel before fulfillment, second file arriving after pick becomes another candidate.

### PR #5 — VO project bootstrap  (MEDIUM — makes the owner's real workflow work)
- `./workbench --new-project <name> --vo path/to/vo.wav [--assets path/to/folder]`:
  creates `videos/<name>/`, copies VO to `work/audio/`, builds the **VO-synth master**
  = VO audio + base video track (brand slate default) via plain ffmpeg so `bake.py` runs
  UNMODIFIED (design: docs/next-level/PIPELINE-DESIGN.md, master.kind=vo_synth).
- If `--assets` given: each media file → probe → conform → filed under the project with a
  simple `work/assets.json` entry (hash, class by extension+probe, original name). No AI,
  no classifier heuristics beyond video/image/audio — that's v1.1 N1.
- If ASSEMBLYAI_API_KEY present: run existing transcribe.py on the VO → edited-transcript.json
  → word ticks appear in the timeline ruler. If absent: skip with a clear message.
- Tests: project scaffold correct, vo_synth master v:0==a:0, bake consumes it, assets conform
  + land in catalog, idempotent re-run.

### PR #6 — First-video hardening  (SMALL, after the owner's first real edit)
- Whatever the first real video surfaces. Reserved: do not pre-build.
- Known deferred nit to include here: `cuts.json` save lock (server.py:786).

## Explicitly v1.1+ (do NOT build now)
Auto edit-plan generation + approval gate (N3), editing-brain planner, drop-folder classifier
heuristics, typed Remotion props, transitions/xfade, OTIO export, CTR flywheel, Shorts emitter.

## The owner's first-video path (after PR #5 merges)
1. `./workbench --new-project video-1 --vo vo.wav --assets ./my-assets`
2. Open workbench → Scenes → place scenes on word ticks → import takes from the drawer
   (or Revise→generate via courier; Fable can fulfill inbox requests).
3. Promote → range-preview each beat → full bake → `verify_frames`/`verify_cut` gates →
   `yt_upload.py --strict`.
