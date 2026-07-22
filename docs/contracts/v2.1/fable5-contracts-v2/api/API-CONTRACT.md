# API contract v2.1 — endpoints, concurrency, ingestion

Server binds `127.0.0.1`, requires the per-session token (printed at startup, embedded by the served
page) on every request, and checks `Origin`/`Host`.

## Concurrency model

- Project ETag = sha256 of `timeline.json` bytes; returned by every read; **required via `If-Match`
  on every state-changing request** (below). Mismatch → `409` `{code:"E_ETAG_MISMATCH",
  current_etag, server_doc}` — client reloads/merges; the server never silently overwrites.
- `assets.json` carries its own etag with the same rules.
- Job control uses the job's `updated_at` as precondition token (`If-Unmodified-Since` semantics);
  jobs are server-owned files.
- All JSON writes are atomic: temp file + fsync + `os.replace`, backup written first.

## Endpoint × If-Match matrix

| endpoint | method | If-Match | effect |
|---|---|---|---|
| `/api/project` | GET | — | full doc + etag + validate summary |
| `/api/project/validate` | GET | — | issues[] per validation-codes.md |
| `/api/timeline` | PUT | ✅ | whole-doc save (editor bulk ops) |
| `/api/scene` | POST | ✅ | create scene (server mints `scene_uid`) |
| `/api/scene/<scene_uid>` | PATCH | ✅ | timing/enable/status/fit/z/transition/props/notes |
| `/api/scene/<scene_uid>` | DELETE | ✅ | remove placement (takes' files remain until vacuum) |
| `/api/scene/<scene_uid>/takes/import` | POST multipart | ✅ | upload → hash → probe → conform(profile) → take record (dedupe by sha256) |
| `/api/scene/<scene_uid>/takes/<take_uid>/promote` | POST | ✅ | set active, derive legacy `asset`/`id`, status planned→draft |
| `/api/scene/<scene_uid>/revise` | POST | ✅ | engine-aware: remotion → render job; fable/hyperframe → generate job (spec); media → 400 pointing at import |
| `/api/bake/range` | POST | ✅ | `{from_s,to_s}` → bake job into `work/preview/` |
| `/api/project/restore` | POST | ✅ | restore a named backup (current state backed up first) |
| `/api/jobs` | GET | — | list (rebuilt from files) |
| `/api/jobs/<job_id>/cancel` | POST | job token | kill process group / provider cancel |
| `/api/jobs/<job_id>/retry` | POST | job token | new attempt record, `parent_job_id` lineage |

## Ingestion contracts

**Multipart import** (browser drag-and-drop): `file` field + optional `note`, `class_hint`.
Server: stream → temp in project → sha256 (dedupe: existing hash returns existing take, 200 not 201)
→ ffprobe → conform per profile table (take.schema.json) → move to
`videos/<p>/work/generated/<scene_uid>/<take_uid>/asset.<ext>` (create-exclusive; original preserved
as `source.<ext>`) → append take → derive legacy fields → new etag returned.

**Watched inbox** (courier + drop-folder): files under `videos/<p>/work/inbox/<job_id>/` are
consumed by an `ingest` job with identical hash/probe/conform/take semantics; candidates attach to
the owning job (`awaiting_pick` when >1); picking promotes.

**Server-side path import** (CLI only): `{path}` accepted from local tools, never from the browser;
full containment validation; same pipeline.

## Path containment (applies to every read AND write of a persisted path)

Reject: absolute paths, `..` segments, drive letters, symlink escapes (realpath must be inside the
project root, or `media/` for library references). Violations are `E_ASSET_OUTSIDE_PROJECT` at
validate time and `400` at mutation time.
