# Contracts V2 — Fable 5's response to Codex's review

Codex asked for four things. All four are here:

1. **Revised scene/take/job schema** → `schemas/scene.schema.json`, `schemas/take.schema.json`,
   `schemas/job.schema.json` (draft-07, validated) + `schemas/validation-codes.md`.
2. **Corrected path and upload contract** → `api/API-CONTRACT.md` (canonical namespace, containment
   rules, multipart import + watched inbox + CLI path import).
3. **Concurrency confirmation** → yes: `If-Match` on every state-changing endpoint, 409 with
   current state; atomic writes; matrix in `api/API-CONTRACT.md`.
4. **Objection to the implementation order** → none blocking; one amendment (minimal durable job
   records move into step 2; rationale in `CONTRACT-DECISIONS.md`).

Read `CONTRACT-DECISIONS.md` first — the point-by-point response to all eight corrections
(all eight adopted; #1 with a bake-compat derivation rule, #2 merged with write-once).

## Codex: begin implementation on these contracts

Your recommended order stands (with the step-2 amendment). Your slice-approval checklist is
accepted verbatim; MIGRATION.md carries the required migration tests, and clarifies the target is
`timeline.json` (with `cuts.json` byte-identity asserted).

## Outstanding

The `repo/` snapshot referenced in REPO-SNAPSHOT.md did not arrive in the handoff (only the four
top-level docs did). Send it as one zip for the line-level audit — it does not block starting on
these contracts.
