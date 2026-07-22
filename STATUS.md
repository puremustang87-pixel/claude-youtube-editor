# STATUS — ai-video-workbench

**The one page that says what's real.** Updated with every merge. If it isn't reflected here and merged to `main`, it isn't real yet.

_Last updated: 2026-07-22 07:10 UTC+1 · maintained by whoever merges_

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

## In review (open PRs)

- 🔍 **PR #1 — engine foundation slice** · branch `codex/fable5-engine-foundation` · commit `134f9bc`
  - Fable review: **posted** (COMMENT). Verdict: strong foundation, merge after fixes.
  - ✅ F1–F4 fixes pushed: atomic ETag save lock, cached legacy hashing, canonical take paths,
    and a self-contained clean-checkout test suite.
  - ✅ 15 tests pass; the two-writer race regression repeatedly returns one `200` and one `409`.
  - **Next action: Fable re-audits commit `134f9bc` → owner merges.**

## Next up (not started)

1. Next slice: multipart take import + conform pipeline + explicit promote endpoint.
2. Takes drawer in the inspector (compare/promote UI).
3. Remotion render-to-take with typed props; then Fable/Hyperframe courier adapters.
4. Script-driven pipeline N1 (ingest) — see `docs/next-level/NEXT-LEVEL.md`.

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
