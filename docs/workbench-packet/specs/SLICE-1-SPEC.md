# Slice 1 — immutable takes + Takes Drawer + range bake (media engine)

**Goal:** prove the version schema, the write-once asset store, promote semantics, and the compare
UX end-to-end — with zero external APIs and no job queue. One day of work, fully testable.

**Non-goals:** Fable/Hyperframe adapters (Stage 3), typed props (Stage 4), transitions (Stage 5).

## Data

Adopt `schemas/scene.schema.json`. Loader migration: a v1 shot lacking v2 fields gets
`engine:"remotion" (or "media" if asset set and no comp match), status:"draft" (approved if enabled
and previously baked — else draft), versions:[] and active:null` — derived in memory, written on
first save. `asset` becomes derived the moment `versions[]` is non-empty.

Asset store: `videos/<p>/work/generated/<sceneId>/<vid>-<sha8>.mp4`, write-once (create-exclusive;
existing hash = dedupe hit, reuse the version).

## Server (tools/editor/server.py)

| Endpoint | Behavior |
|---|---|
| `POST /api/scene/<id>/takes/import` | body: `{path}` (file already on disk, e.g. dragged into a watched dir, or absolute-from-picker). Steps: validate path (realpath inside project or media/) → ffprobe → **conform** if not comp-native (1080p, comp fps, 8-bit H.264, aac) → sha256 of conformed file → move into asset store → append version `{vid: next, provenance:{provider:"media", note}}` → return updated scene + etag. |
| `POST /api/scene/<id>/takes/<vid>/promote` | set `active=vid`, rewrite derived `asset`, `status`→`draft` if was `planned`. Atomic save (os.replace) + backup + new etag. |
| `GET /api/timeline` | body + `etag` (sha256 of file bytes). |
| `PUT /api/timeline` | requires `If-Match: <etag>`; mismatch → **409** with server copy so the client can diff. Never silent overwrite. |
| `POST /api/bake/range` | `{from_s, to_s}` → run `bake.py --from <a> --end <b>` into `work/preview/range-<a>-<b>.mp4`, return path when done. |

## bake.py

Add `--from SECONDS` (mirror of existing `--end`): segments before `from` are dropped, master audio
muxed for `[from, end]`. Keep every existing behavior; `--from 0` ≡ today.

## UI (per ui-redesign/DESIGN-SPEC.md)

Takes drawer (slide 0↔118px between preview and timeline): take cards from `scene.versions[]`
(tag=vid, engine dot, cost, note on title), active ring + badge, candidate = dashed amber;
hover → Compare / Promote. "＋ Import take" card for media/planned scenes. Promote flow updates the
save chip to pending until PUT succeeds. A/B compare v1: swap two rendered stills (or first frames)
in the preview; video A/B later.

## Tests (tools/editor/test_server.py — all must pass)

1. Append-only invariant: importing twice yields v1, v2; nothing rewritten.
2. Hash dedupe: importing identical bytes returns the existing version, no new file.
3. Promote updates `active` + derived `asset` atomically; timeline backup written; etag changes.
4. Conform: a synthetic VFR/10-bit input (ffmpeg testsrc, `-pix_fmt yuv420p10le`) is transcoded to
   comp-native; `probe.conformed=true`; duration preserved ±50ms.
5. Path safety: `../`, absolute-outside-project, drive-letter paths → 400.
6. Etag conflict: PUT with stale etag → 409, file unchanged on disk.
7. Range bake: `--from 40 --end 52` output exists, duration ≈ 12s ±0.1, v:0==a:0 ±50ms.
8. v1 timeline loads: legacy shots gain defaults in memory; bake fields untouched on save.

## Acceptance demo (manual, 3 minutes)

Drop two different mp4s onto a `media` scene → two takes appear → compare → promote v2 → range-bake
±2s → watch the composited preview → check `timeline.json`: derived asset points at the v2 hash file,
backups exist, both hash files on disk, neither ever overwritten.
