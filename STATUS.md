# STATUS — ai-video-workbench

**The one page that says what's real.** Updated with every merge. If it isn't reflected here and merged to `main`, it isn't real yet.

_Last updated: 2026-07-23 02:15 UTC+1 · maintained by whoever merges_

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
- ✅ **PR #1 — engine foundation slice** — merged. Identity split, migration, immutable takes, etag+atomic saves, durable jobs, validator, security.

## In review (open PRs)

- ✅ **PR #2 — take ingestion + Takes drawer** · branch `codex/take-ingest-drawer` · `92ddc8b` — **CLEARED TO MERGE (owner's click)**
  - Fable executed audit: 23/23 tests pass (FFmpeg conform/alpha/VFR genuinely ran), independent traversal probe rejected all 4 vectors, concurrency verified (conform runs outside the lock).
  - Multipart parsing safe by construction · ProRes-4444 overlays + H.264 cutaways with post-conform re-probe · dedup + canonical namespace + atomic promote. **Zero bugs found.**
  - Non-blocking notes for later: cuts.json save lock (still deferred); move conform to the job queue when providers land.

## Next up (not started)

1. **Merge PR #2** (owner), then Codex starts: provider/courier adapters (Fable/Hyperframe) on the job+take contract.
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
