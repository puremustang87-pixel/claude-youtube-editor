# Validation issue codes (stable contract)

`GET /api/project/validate` → `{ "etag": "...", "issues": [ { "code", "severity": "E"|"W",
"scene_uid"?, "message", "data"? } ] }`

The library "Blocking bake" filter, scene badges, the Bake button state, `bake.py --check`, and CI
all consume THIS response. The browser computes nothing itself. Codes are append-only: never renumber
or repurpose; deprecate by leaving documented.

## Errors (bake button disabled while any E exists on an enabled scene)

| code | fires when |
|---|---|
| `E_NO_ACTIVE_TAKE` | enabled non-remotion scene has no `active_take_uid` |
| `E_ASSET_MISSING` | active take's `file` does not exist on disk |
| `E_ASSET_OUTSIDE_PROJECT` | persisted path fails containment (absolute, `..`, drive letter, symlink escape) |
| `E_ASSET_NONCANONICAL` | non-legacy active take is outside `work/generated/<scene_uid>/<take_uid>/asset.<ext>` |
| `E_ASSET_UNCONFORMED` | take profile is `original` where the scene type requires a conformed profile |
| `E_PROFILE_MISMATCH` | overlay scene whose active take is not `overlay_alpha` (or has no alpha), or cutaway with alpha-only artifact |
| `E_COMP_NOT_FOUND` | engine=remotion and `composition_id` not in the generated registry |
| `E_SPAN_INVALID` | `master_out_s <= master_in_s`, negative, or beyond master duration |
| `E_OVERLAP_UNCLAIMED` | same-lane overlap between enabled scenes not claimed by an `xfade` transition |
| `E_DURATION_MISMATCH` | `fit: "error"` and active take duration differs from slot beyond tolerance (50 ms) |
| `E_LEGACY_ID_COLLISION` | two enabled scenes derive the same legacy `id` with diverging assets |
| `E_ETAG_MISMATCH` | (mutation responses only) stale `If-Match` |

## Warnings (shown in the Blocking-bake filter as "bake output may differ"; never disable the button)

| code | fires when |
|---|---|
| `W_SCENE_GENERATING` | a generate/render job for the scene is queued/running — bake would use the previous take |
| `W_SCENE_DISABLED` | scene is disabled and will be skipped |
| `W_DURATION_MISMATCH` | fit is hold/cut/stretch and take duration differs from slot > 250 ms |
| `W_RENDER_STALE` | remotion out file older than the shot TSX or props (re-render advised) |
| `W_JOB_ORPHANED` | an orphaned/unknown job references this scene — needs human action |
