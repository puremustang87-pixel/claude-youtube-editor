# Workbench UI — design specification

**Ground truth: `mockup.html`.** Open it in a browser; click scenes, hover takes, drag the playhead,
press the violet **?** button for in-place design rationale. This document is the contract behind it.
`design-tokens.css` is the palette/spacing/type system — copy it verbatim.

## Why the current UI feels "all over the place"

It is **tool-shaped**: one panel per backend capability (catalog, inspector, jobs, renders), each with
its own selection and its own state. The redesign is **task-shaped**: the editor's actual loop is

> select a scene → judge it → revise or replace → confirm in context → move on

so the entire window orbits ONE preview and ONE timeline with ONE selection.

## The five rules (do not violate)

1. **One selection model.** Clicking a scene anywhere (library card, timeline block) selects it
   everywhere: preview updates, takes drawer opens, inspector fills. Nothing else appears or moves.
   No second "current item" anywhere in the app.
2. **The preview is the app.** It is the largest element, always centered, never covered by panels.
   Everything else is chrome around it.
3. **State lives in the top bar, not in panels.** Save state (with etag), running jobs (count +
   progress), and Bake are chips/buttons in the header. No jobs panel on the main screen — the chip
   expands on click (popover) if detail is needed.
4. **Versions belong to the selection.** The takes drawer slides open between preview and timeline
   ONLY when a scene is selected. Takes never appear anywhere else. Hover a take → Compare / Promote.
5. **Words are the grid.** Transcript word boundaries render as fine violet ticks on the ruler; every
   timing edit (drag, trim, nudge) snaps to them by default (Alt = free). Snap buttons ("⇤ word",
   "word ⇥") sit next to the In/Out fields.

## Layout (CSS grid, fixed zones)

```
grid: 46px topbar / 1fr main / 26px footer
cols: 252px library / 1fr stage / 288px inspector

TOPBAR    wordmark · project · [Scenes|Cut] · master clock (center) · save chip · jobs chip · Preview bake · Bake
LIBRARY   [In timeline | Catalog] segmented · search · filter chips (engines + "Blocking bake") · scene cards · add-at-playhead
STAGE     preview frame (16:9, shadowed) → takes drawer (0↔118px slide) → timeline (ruler + 2 lanes + playhead)
INSPECTOR selection only: header (name/engine/status) · Timing (word-synced) · Props (from propsSchema) ·
          Generation spec (provenance) · actions (Revise primary · Preview bake ±2s · Render frame · Duplicate/Disable/Delete)
FOOTER    keyboard grammar, always visible
```

The **Cut workspace is a mode of the same shell** (segmented control in the top bar) — same timeline
strip, same selection grammar, same tokens. Do not give Cut its own visual language.

## Status system (one vocabulary, everywhere)

| status | color | meaning |
|---|---|---|
| approved | green | in the bake, human-confirmed |
| draft | amber | in the bake, not confirmed |
| generating | indigo, pulsing | a job is producing takes |
| planned | slate | placeholder, no take yet |
| blocked | red | would break/skip the bake (no asset, missing file) |

"⚠ Blocking bake" is a first-class library filter — the pre-bake checklist is a click, not a hunt.
Engine identity is a **color stripe/dot** (remotion indigo, fable violet, hyperframe cyan, media slate),
never a text label taking space.

## Interaction inventory

| act | where | behavior |
|---|---|---|
| Select | card or block | selects everywhere (rule 1) |
| Move / trim | block body / edges | drag; snaps to word ticks; Alt = free; live tc tooltip |
| Compare | take hover → Compare | preview enters A/B: chip top-right, click A/B or hold to flip |
| Promote | take hover → Promote | sets active take, rewrites derived asset, save chip → pending |
| Revise | inspector primary / drawer "+" | opens active take's spec prefilled + notes box → job → candidate appears in drawer |
| Range bake | inspector / B key | bakes scene ±2s, plays in preview |
| Add scene | library footer / catalog card | inserts at playhead on the correct lane |
| Disable / delete | inspector footer | disable = dashed 38% opacity block, stays in plan |
| Keyboard | global | Space play · I/O trim to playhead · ←→ word nudge · V takes · B range bake · D disable |

## Empty/edge states (make them designed, not accidental)

- No selection → inspector shows quiet hint, drawer closed.
- Scene without takes (planned/media) → drawer opens with only "＋ Revise…"/"Import take" slot;
  inspector shows a red "Blocking bake" section explaining exactly what's missing.
- Conflict on save (etag mismatch) → save chip turns red "Reload timeline · changed on disk"; the app
  never silently overwrites (server contract).
- Candidate takes → dashed amber border + "new" note until promoted or dismissed.

## Visual system

Everything in `design-tokens.css`. Essentials: near-black blue canvas (`#0a0c10`), one accent family
(indigo `#6366f1`, matching the repo's house brand), Inter for UI, JetBrains Mono for timecodes/ids,
4px spacing grid, radius 6/10/14, hairline borders `rgba(148,163,184,.09)`, one shadow for elevated
surfaces. Semantic colors only for status/engines. No gradients except media thumbnails and the
preview mock. If a control needs a border AND a background AND a shadow, it is too heavy.

## Implementation notes for the existing codebase

- Keep vanilla HTML/CSS/JS — the mockup already is. Lift its CSS wholesale; it uses no framework.
- `scene-editor.js`: collapse per-panel state into one `store = {scenes, sel, filter, playhead, jobs}`
  with a single `render()` pass (the mockup shows the pattern). Kill any second selection source.
- Word ticks: serve `edited-transcript.json` word starts through the existing project data endpoint;
  render once per zoom level.
- Takes drawer: reads `scene.versions[]` per the scene schema (../schemas/scene.schema.json);
  Promote = POST that sets `active` + derived `asset` and returns the new etag.
- A/B compare: two frame renders (active vs candidate) swapped in one `<img>`; no video pipeline
  needed for v1.
- Acceptance: every rule in "The five rules" holds; keyboard row fully works; blocking filter returns
  exactly the scenes bake would skip; no panel other than drawer/inspector changes on selection.
