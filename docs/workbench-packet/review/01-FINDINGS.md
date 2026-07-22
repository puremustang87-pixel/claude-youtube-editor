# Findings by severity

Scope: reviewed from the handoff brief + deep knowledge of the underlying fork (the reviewer built
the layer this workbench extends). Items marked **[verify]** need the actual `repo/` code to confirm
line-level — the mechanism is asserted, the exact code path isn't.

## SEV-1 — data loss / corruption

**1.1 Two writers, no coordination — `tools/editor/server.py` + `.claude/skills/make-tsx/SKILL.md`.**
The make-tsx skill's step 6 writes `timeline.json`; the Scenes workspace writes it too. Last-writer-
wins with a stale browser tab = silent clobber of either side's work.
**Fix:** etag on load (hash of file), checked on save → 409 + diff view; advisory lockfile
(`work/.timeline.lock`, pid+mtime) that the skill docs instruct Claude Code to respect.

**1.2 Mutable `asset` paths — timeline contract + `bake.py`.**
Regenerating over the same file: (a) corrupts an in-flight bake/ffmpeg read silently; (b) poisons
every timeline backup that references the path (restore history lies); (c) makes A/B impossible.
**Fix:** write-once, hash-named version files under `work/generated/<sceneId>/<vid>-<hash8>.mp4`;
`asset` becomes a derived pointer to the active version (keeps bake contract). See scene.schema.json.

**1.3 In-memory jobs + subprocesses — `server.py`.**
Server restart orphans node renders still writing `remotion/out/<id>.mp4`; resubmission double-writes
the same output path concurrently → corrupt artifact composited without error. No cancel path.
**Fix:** file-backed job records (job.schema.json), per-job output dirs, boot-time orphan reaping
(pid + start-time match), kill-process-group cancel.

## SEV-2 — correctness / security

**2.1 External assets are unconformed — `bake.py` ingestion.**
VFR / 10-bit / yuv444 / odd-fps generated files stutter or starve decoders — this exact failure is
documented in the repo for footage (clean-cut notes: 10-bit HEVC → OffthreadVideo duplicated frames).
**Fix:** ingest = probe → conform to comp-native (1080p, comp fps, 8-bit H.264, aac) → hash the
conformed file → only then is it a version. Raw external files never become `asset` directly.

**2.2 Duration-mismatch policy undefined — `bake.py`.**
Asset 6.13s in a 6.5s slot: freeze, cut, or error? Make it explicit per scene:
`fit: hold|cut|stretch|error` (default `hold`). Silent freeze-frames are how AI tells creep in.

**2.3 Client-supplied `asset` path reaches ffmpeg — `server.py` → `bake.py`.** [verify]
Must reject absolute paths, `..`, drive letters, symlink escapes (realpath prefix check against
`videos/<p>/work/` and `media/`).

**2.4 Localhost server with no origin defense — `server.py`.** [verify bind + headers]
Any webpage can POST to `http://localhost:8765` (DNS rebinding / drive-by). Stakes are now "run
render jobs + rewrite timelines". **Fix (~30 lines):** bind 127.0.0.1, per-session token printed at
startup and embedded by the served page, check Origin/Host.

**2.5 Backups without restore — `server.py` + UI.** [verify]
Save-time backups exist; recovery is a manual file operation done mid-panic. Surface list + diff +
one-click restore in the UI, or the backups are write-only insurance.

## SEV-3 — design debt to catch now

**3.1 Overlap rejection vs future transitions.** A crossfade REQUIRES overlap; give overlap ownership
to `transition_in: {kind:"xfade", frames}` in the schema now so the validator doesn't break later.
**3.2 Seconds-typed timing betrays the word-sync soul.** Snap all timing edits to
`edited-transcript.json` word boundaries by default (Alt = free). The data is already on disk.
**3.3 Flat catalog** fine at 38 comps; add usage-recency sort before 200.
**3.4 Status is a workflow state machine rendered as a dropdown.** "What's blocking my bake?" must be
one filter chip (adopted in the redesign).

## Must-verify list once `repo/` is provided

Atomic save (`os.replace` vs truncate-write) · subprocess arg-list vs shell-string (comp ids are
user-adjacent input) · job dict thread-safety under the threaded HTTP server · bake segment
fenceposts with adjacent disabled scenes · Windows path joins in project resolution · whether
`test_server.py` covers any concurrency case (suspected: no).
