# Target architecture, next interactions, staged plan

## Architecture (smallest durable shape)

```
Browser (vanilla, one store, one render pass)
   Scenes | Cut · Takes drawer · jobs chip
        │  HTTP + session token · etag on timeline saves
        ▼
server.py ── validate/save timeline.json (atomic replace, backups + restore, lock)
   ├── jobs/      file-backed records = source of truth (in-memory index rebuilt on boot)
   │     ├── runner: remotion render/frames (--props from scene.props)
   │     ├── provider: fable       ┐ one interface:
   │     ├── provider: hyperframe  ┘ submit(spec) / poll(id) / fetch(url) / cancel(id)
   │     └── ingest: probe → conform comp-native → sha256 → version
   ├── asset store: videos/<p>/work/generated/<sceneId>/<vid>-<hash8>.mp4   [write-once]
   └── bake.py: range bake (--from/--to) · N overlays by z · cut|xfade at claimed overlaps
```

**Non-goals (deliberate):** no general N-track NLE, no nested sequences, no audio mixing in this
timeline. Captions/SFX keep their own plan files (`sfx-plan.json` works today). `timeline.json` stays
a **compositing map** and the bake contract survives unchanged — extension, not migration.

**Timeline model, smallest upgrade that covers the asks:** two semantic lanes stay; stacked overlays
= N overlay scenes ordered by `z`; alternates = `versions[]` on a scene (not parallel tracks);
transitions = `cut|xfade` owned by a scene edge (the xfade legitimizes exactly its own overlap).

**Typed Remotion props:** compositions export `propsSchema` (+ defaults); `gen-registry.mjs` harvests
it into `shots.manifest.json` (it already harvests metadata — the pattern exists); the inspector
renders controls generically; values live in `scene.props`; renders pass Remotion-native `--props`.
The UI never learns about TSX files. Schema-less comps just show no props panel.

**Fable/Hyperframe integration, courier-first:** implement the provider interface with courier
fulfillment — `submit` writes the spec into the job file and a watched `work/inbox/<job_id>/` dir;
any fulfiller (a human, or the Fable thread generating the clip) drops the mp4 there; the server
ingests → conforms → candidates → drawer. The workbench cannot tell courier from API, so a real
API adapter later swaps the fulfillment, not the product.

## The next three product interactions

1. **Takes Drawer** — select a scene → strip of versions + candidates, hover-scrub, A/B against
   active, one-click Promote. This IS select/compare/replace, and it's what immutable versions power.
2. **Revise-with-notes** — "Revise" opens the active take's `provenance.spec` prefilled + a notes
   box → submit = new job → candidate appears in the drawer with `parent_vid` lineage recorded.
3. **Range bake preview** — bake `[in−2s, out+2s]` (bake has `--end`; add `--from`) → a 5-second
   composited check in seconds, not a full-video bake. Fold word-snap into the same release.

## Staged plan

- **Slice 1 (build first — see specs/SLICE-1-SPEC.md):** immutable versions + takes drawer via the
  `media` engine only: import → ingest-conform → hash → version → compare → promote → range bake.
  No external APIs, no queue; proves schema, store, promote semantics, and the compare UX end-to-end.
- **Stage 2:** jobs on disk; migrate existing render jobs; cancel/retry/orphan-reap; jobs popover.
- **Stage 3:** Fable courier adapter (inbox watcher) → candidates → drawer. API fulfillment later.
- **Stage 4:** typed props inspector (registry harvest + `--props` render path).
- **Stage 5:** transitions (`cut|xfade`) + stacked overlays by `z` + backup restore UI.
- **Continuous:** burn down 01-FINDINGS.md; SEV-1 items land with Slices 1–2 (same files).
