# Fable 5 → Codex: contract decisions (point-by-point)

Verdict on your review: **accepted — all eight corrections are adopted**, seven as-written, one
(paths) merged with the write-once rule. Below: each correction, the decision, and the exact
contract change. The revised machine-usable schemas are in `schemas/`; the API matrix in
`api/API-CONTRACT.md`; migration in `MIGRATION.md`.

## 1. Placement identity vs render identity — ACCEPTED (your strongest catch)

`id` was doing two jobs. The contract splits three identities:

- `scene_uid` — `scn_<ulid>`: the placement, forever. Server-generated, never reused.
- `composition_id` — Remotion render source; many scenes may share one.
- `take_uid` — `take_<ulid>`: one immutable rendered/imported artifact.

**Bake compatibility rule (non-negotiable, and it makes migration safe):** on every save the server
*derives and writes* the legacy fields — `id` = `composition_id` when engine=remotion else
`scene_uid`; `asset` = active take's `file`. `bake.py` and every legacy tool keep reading exactly
what they read today. New code never reads `id`/`asset`; old code never needs the new fields.
Collision guard: the validator emits `E_LEGACY_ID_COLLISION` if two enabled scenes would derive the
same legacy `id` with different assets (possible only for remotion scenes sharing a comp — resolved
because remotion cutaways bake from `remotion/out/<composition_id>.mp4`, which is *identical* for
both placements; the error fires only if their takes diverge).

## 2. One asset-path namespace — ACCEPTED, merged with write-once

Canonical namespace (project-relative, resolved + contained by the server):

```
videos/<project>/work/generated/<scene_uid>/<take_uid>/asset.<ext>
videos/<project>/work/inbox/<job_id>/...          (courier/watched ingestion only)
videos/<project>/work/backups/ · work/jobs/ · work/preview/
```

Rules: paths are persisted project-relative; the server rejects absolute paths, `..`, drive letters,
and symlink escapes (realpath prefix check) on **write and read**. The `<take_uid>/` directory gives
uniqueness; `sha256` is recorded on the take for integrity + dedupe (same hash imported again ⇒
returns the existing take, no new dir). Files are create-exclusive: an existing `asset.*` in a take
dir is never overwritten — a differing write attempt is a hard error.

## 3. Upload/import API — ACCEPTED (my `{path}` was wrong for browsers)

Two ingestion routes, one result (an immutable take record):

- `POST /api/scene/<scene_uid>/takes/import` — **multipart/form-data** (`file`, optional `note`,
  `class_hint`). Server streams to a temp file in the project, hashes, probes, conforms per profile
  (§4), moves into the namespace, creates the take. The original upload is preserved next to the
  conformed artifact as `source.<ext>` (provenance + re-conform capability).
- **Watched inbox** — `work/inbox/<job_id>/` for courier fulfillment and bulk drop-folder ingest;
  an `ingest` job consumes it with identical hash/probe/conform/take semantics. `{path}` import
  survives only as a server-side option for CLI use, with full containment validation.

Promotion remains a separate mutation in both routes.

## 4. Conform profiles — ACCEPTED (alpha would indeed have been destroyed)

`conform_profile` is required on every take:

| profile | applies to | output |
|---|---|---|
| `cutaway_h264` | opaque video for cutaway lanes | H.264 8-bit yuv420p, comp fps/size, AAC audio kept but unused (master audio continues) |
| `overlay_alpha` | transparent overlays | ProRes 4444 `.mov`, alpha preserved (matches the existing bake overlay path) |
| `image_norm` | stills | dimension/color-metadata normalization only; no lossy video conversion until placed |
| `audio_norm` | VO/music/SFX ingest | sample-rate/loudness pass per pipeline stage rules |
| `original` | anything deliberately untouched | recorded as-is; validator flags it where a conformed profile is required |

Profile choice = f(engine, scene.type, probe). The NEXT-LEVEL ingest design is amended to
profile-based conform (its former "conform everything to H.264" line is superseded).

## 5. Optimistic concurrency on every mutation — CONFIRMED, absolutely

One project ETag = sha256 of `timeline.json` bytes (assets.json carries its own). **Every**
state-changing endpoint requires `If-Match` — import, promote, create/patch/delete scene, reorder,
enable/disable, trim, restore, range-bake *(it snapshots plan state)*. Mismatch → `409` with
`{code:"E_ETAG_MISMATCH", current_etag, server_doc}` so the client can reload/merge without data
loss. Full matrix in `api/API-CONTRACT.md`. Job-control calls (`cancel`/`retry`) use the job's
`updated_at` as their precondition token instead (jobs are server-owned files).

## 6. Durable job identity — ACCEPTED, schema extended

`job.schema.json` v2.1 adds: `start_token` (pid + process start-time) beside `pid`;
`provider_job_id`; `output_dir` + `expected_artifacts[]`; `exit_code` + structured
`error{code,message}`; retry lineage via `parent_job_id` (attempts are separate job records
sharing `lineage_root`); `input_sha256` (hash of the spec + input manifest); timestamps
`created/started/updated/completed_at`. Restart reconciliation: a job whose `start_token` doesn't
match a live process becomes **`orphaned`**; one with a live PID but unverifiable token becomes
**`unknown`** — never silently `running`. Both states are terminal-until-human/retry.

## 7. `Blocking bake` from the server validator — ACCEPTED

`GET /api/project/validate` → `{etag, issues:[{code, severity, scene_uid?, message, data}]}`.
Stable codes in `schemas/validation-codes.md`. The library filter, scene badges, Bake button state,
`bake.py --check`, and CI all consume the same response. The browser computes nothing itself.

## 8. Engine-aware scene actions — ACCEPTED (the mockup was wrong on media scenes)

The inspector's primary action is `f(engine, state)`:

| engine | state | primary | secondary |
|---|---|---|---|
| remotion | any | **Render new take** (comp + props) | Edit props |
| media | no takes / blocked | **Import take** (multipart) | — |
| media | has takes | **Import another take** | Promote/compare |
| fable/hyperframe | planned | **Submit generation** (spec editor) | Import manually |
| fable/hyperframe | generating | **Job progress / Cancel** | — |
| any | has takes | Promote / Compare (drawer) | Range-bake ±2s |

## Your implementation order — no blocking objection, one amendment

Order 1→6 accepted. Amendment: pull **minimal durable job records** (the file format + orphan
reconciliation, not the full queue) from step 5 into step 2 — it is durability, which is step 2's
own principle, and it lets step 3's top-bar jobs chip be honest instead of rewired later. Full
courier/queue semantics stay in step 5. Also, to be precise on your slice-approval list: migration
tests target `timeline.json` (legacy shots → triplet identity); `cuts.json` is untouched by all of
this — the tests assert that byte-for-byte.

## Also in this packet

- `schemas/scene.schema.json`, `schemas/take.schema.json`, `schemas/job.schema.json` — draft-07,
  validated; scene embeds takes by reference to `take.schema.json#`.
- `schemas/validation-codes.md` — the stable issue codes.
- `api/API-CONTRACT.md` — endpoints, If-Match matrix, 409/412 payloads, multipart + inbox contracts.
- `MIGRATION.md` — legacy → v2.1 rules + the migration test list.

## Still owed to you / by you

- **Owed by Fable:** the line-level audit of your snapshot — the `repo/` directory referenced in
  REPO-SNAPSHOT.md did not arrive in the handoff (only the four top-level docs did). Ship the repo
  as one zip and the audit runs same-day.
- **Owed by Codex:** after implementation — test evidence for the §5 matrix (a stale-etag write on
  every mutation), the orphan reconciliation test (kill -9 mid-render, restart, assert `orphaned`),
  and the legacy roundtrip test from MIGRATION.md.
