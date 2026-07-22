# Fable 5 → Codex handback: workbench review + UI redesign

Response to the "integrated AI video workbench" review request (START-HERE-FABLE5.md).
Everything requested is here, plus a full UI/UX redesign — the owner's verdict on the current UI
was that it is scattered; the redesign is the fix and takes priority.

## Read in this order

| # | File | What it is |
|---|---|---|
| 1 | `ui-redesign/mockup.html` | **Open in any browser.** The redesigned workbench, interactive, with mock data. Press the violet **?** for design rationale in place. This is ground truth for the UI. |
| 2 | `ui-redesign/DESIGN-SPEC.md` | The contract behind the mockup: the five rules, layout grid, status system, interaction inventory, implementation notes for the existing vanilla JS codebase. |
| 3 | `ui-redesign/design-tokens.css` | Palette / spacing / type tokens. Copy verbatim. |
| 4 | `review/01-FINDINGS.md` | Severity-graded findings (SEV-1 data loss → SEV-3 design debt), with file references and fixes. |
| 5 | `review/02-ARCHITECTURE-AND-PLAN.md` | Target architecture, provider interface, non-goals, the next three product interactions, staged implementation plan. |
| 6 | `schemas/scene.schema.json` · `schemas/job.schema.json` · `schemas/examples.json` | The canonical cross-engine scene/version schema and the job schema, as machine-usable JSON Schema. |
| 7 | `specs/SLICE-1-SPEC.md` | The first vertical slice (immutable takes + drawer + range bake), written to be built directly. |
| 8 | `COPY-THIS-PROMPT-CODEX.txt` | Paste this into Codex to kick off implementation. |

## The one-paragraph verdict

The workbench direction is right and the timeline.json/bake contract should be preserved — no
migration is justified. Three things must change before building further: (1) the UI must be rebuilt
task-shaped around one selection model (see mockup — this is the owner's top priority), (2) assets
must become immutable, content-addressed versions (the current mutable `asset` path is the packet's
biggest data-loss risk), and (3) jobs must move from memory to files. The scene schema here is
additive over the existing bake contract — bake.py keeps reading the same fields it reads today.

## Build order

1. UI shell redesign per `ui-redesign/` (the five rules are the acceptance test).
2. `specs/SLICE-1-SPEC.md` (brings the scene schema + takes drawer to life).
3. SEV-1 fixes from `review/01-FINDINGS.md` (etag saves, file-backed jobs) — can land with 1–2.
4. Then: Fable courier adapter, typed props, transitions (staged plan in `review/02-…`).
