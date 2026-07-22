# The bridge — how this repo works

Three parties, one repo, branch protocol:

| branch | who | what |
|---|---|---|
| `main` | owner merges | canonical: fork base + docs (contracts, packets, reviews) |
| `codex/*` | Codex (working machine) | implementation slices — foundation slice, import+drawer, providers |
| `fable/*` | Fable 5 (Hyperagent thread) | audits with file:line findings, fixes, contract revisions |

Flow: Codex pushes a slice branch -> Fable pulls it, audits by EXECUTING it, pushes findings to a
fable/ branch -> owner merges what's agreed into main.

Reading order for a new reviewer: docs/review/SESSION-LOG.md (how we got here) ->
docs/contracts/v2.1/CONTRACT-DECISIONS.md (the agreed data/API contract) ->
docs/workbench-packet/ui-redesign/DESIGN-SPEC.md (the five UI rules; mockup.html is ground truth) ->
docs/next-level/NEXT-LEVEL.md (the script-driven pipeline + build order).
