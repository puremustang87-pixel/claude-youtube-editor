# Migration: legacy timeline.json → v2.1 (and what never changes)

`cuts.json` is untouched by all of this. The migration tests assert it byte-for-byte.

## Read path (lazy, in-memory on load)

For each legacy shot lacking v2.1 fields:

1. Mint `scene_uid` (`scn_<ulid>`), persisted on first save — stable forever after.
2. `engine`: `remotion` if legacy `id` matches a registry composition; else `media` if `asset` set;
   else `remotion` default. `composition_id` = legacy `id` when engine=remotion.
3. `takes`: if a prior `versions[]` exists (interim schema), convert each version → take
   (`vid`→`take_uid` minted, keep sha256/provenance/probe; files stay where they are until touched).
   Else if `asset` points at an existing file: synthesize one take (`provenance.provider="media"`,
   `note:"migrated from legacy asset"`, profile `original`, hash computed lazily).
   Else: `takes: []`, `active_take_uid: null`, `status: "planned"`.
4. `status`: `approved` if enabled and previously baked (heuristic: file referenced by last bake
   manifest), else `draft` when it has a take, else `planned`.
5. Defaults: `fit:"hold"`, `z:0`, `transition_in:{kind:"cut"}`.

## Write path (every save)

- Derive and write legacy fields: `id` = `composition_id` (remotion) else `scene_uid`;
  `asset` = active take's `file` (omit when no takes — matching legacy behavior).
- Never rewrite take files; new artifacts only ever appear under the canonical namespace.

## Migration tests (required for slice approval)

1. Legacy timeline loads; every scene gains a stable `scene_uid`; second load reuses it (no churn).
2. Roundtrip: load → save with zero edits → bake-contract fields (`id`, `type`, `master_in_s`,
   `master_out_s`, `asset`, `enabled`) byte-equal to input (key order aside), additive fields only.
3. Two scenes sharing one `composition_id`: no identity collision; validator clean when takes agree,
   `E_LEGACY_ID_COLLISION` when they diverge.
4. Legacy `asset` outside the project → load succeeds with `E_ASSET_OUTSIDE_PROJECT` issued,
   mutation of that scene blocked until re-imported (never silently rewritten).
5. Interim `versions[]` docs convert losslessly to `takes[]`.
6. `cuts.json` byte-identical before/after any workbench session.
7. bake.py on a migrated-untouched project produces byte-identical segment plan to pre-migration.
