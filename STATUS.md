# STATUS — ai-video-workbench

**The one page that says what's real.** Updated with every merge. If it isn't reflected here and merged to `main`, it isn't real yet.

_Last updated: 2026-07-23 05:30 UTC+1 · V1.0 code complete pending final merge_

## How this repo works (the 10-second version)

Nothing reaches `main` without an owner merge. Codex builds on `codex/*` branches and opens PRs; Fable reviews each PR (runs the code, posts findings with file:line); the owner merges. CI runs the tests on every push — a green check neither agent can fake.

| branch | who | role |
|---|---|---|
| `main` | owner merges only | canonical truth |
| `codex/*` | Codex (builder) | implementation slices |
| `fable/*` | Fable (auditor/architect) | audits, fixes, contract revisions |

## Merged to `main`

- ✅ **Docs bridge** (`1d17a67`) — contracts v2.1, UI redesign packet, next-level pipeline, reviews. See `docs/README-BRIDGE.md`.
- ✅ **Control Kit** (`d778c34`, `e170d66`) — STATUS.md, PR template, CI on every push.
- ✅ **PR #1 — engine foundation slice** — merged.
- ✅ **PR #2 — take ingestion + Takes drawer** — merged. 23/23 tests, multipart import, conform profiles, promote.
- ✅ **PR #3 — durable range bake + job progress** — merged (`526aaf6`).
- ✅ **PR #4 — courier inbox + generate jobs** — merged. The "scene needs something → prompt → clip becomes a take" loop.

## In review (open PRs)

- ✅ **PR #5 — VO project bootstrap** · `codex/vo-project-bootstrap` · `a21ec20` — **CLEARED TO MERGE (owner says "merge PR #5") — completes V1.0**
  - Fable ran the owner's literal first command: scaffold ✓, VO-synth master at exact A/V parity ✓, assets classified+cataloged ✓, bake --check issues:[] ✓. 41/41 tests.
  - Only un-executed path: live AssemblyAI call (owner's key; ruler ticks covered by mocked test).

## Next up (not started)

1. **Owner: "merge PR #5" → V1.0 COMPLETE.** Then on the machine: `git pull` and
   `./workbench --new-project video-1 --vo vo.wav --assets ./my-assets` — first real edit.
2. PR #6 (reserved): first-video hardening + deferred nits (cuts.json save lock, courier poller logging).
3. v1.1 backlog: docs/next-level/NEXT-LEVEL.md (auto-planning, editing brain, HyperFrames adapter, typed props).
2. Hyperframe generation + richer scene editing (typed Remotion props).
3. Script-driven pipeline N1 (ingest) — see `docs/next-level/NEXT-LEVEL.md`.
4. Sweep the deferred `cuts.json` save lock when the Cut workspace is next touched.

## Standing risks (do not forget)

- ⚠️ **RNNoise model license** unresolved — resolve before this repo goes public (`docs/review/THOUGHTS.md`).
- ⚠️ Repo is a **public** fork — no secrets, tokens, or footage in commits.
- ⚠️ Remotion is source-available — free ≤3 people, license beyond.

## Verify it yourself (any time, no agent needed)

```bash
git clone https://github.com/puremustang87-pixel/claude-youtube-editor
cd claude-youtube-editor && git checkout codex/fable5-engine-foundation
npm --prefix remotion install && npm --prefix remotion run gen
python tools/editor/test_server.py -v               # expect: 15 tests, OK
```
