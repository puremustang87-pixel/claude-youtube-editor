# STATUS — ai-video-workbench

**The one page that says what's real.** Updated with every merge. If it isn't reflected here and merged to `main`, it isn't real yet.

_Last updated: 2026-07-23 04:35 UTC+1 · maintained by whoever merges_

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

## In review (open PRs)

- ✅ **PR #3 — durable range bake + job progress** · `codex/range-bake-jobs` · `e1458b6` — **CLEARED TO MERGE (owner's click)**
  - Executed audit: 36/37 pass on Linux (real ffmpeg range bake w/ A/V sync, immutable per-job artifacts, SIGTERM-ignoring-child cancel, restart admission); offset fix + atomic publish read-verified.
  - Codex's Windows discovery: os.kill(pid,0) is destructive on Windows — replaced with verified start-token probe.
  - F1 (SEV-3, test-only): the Windows-liveness test errors on Python 3.10/3.11 (pathlib dispatch via patched os.name); passes on CI's 3.12. **Required: 3-line test fix as first commit of the PR #4 branch.**

## Next up (not started)

1. **Merge PR #3** (owner), then Codex starts PR #4: courier inbox + generate jobs (docs/V1-PLAN.md) — first commit = the F1 test fix.
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
