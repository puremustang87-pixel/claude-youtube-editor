# THOUGHTS — Fable 5's candid reasoning on this whole project

Written for review — by the owner and by Codex. This is the judgment layer behind the specs: what I
believe, what I'm betting on, what worries me, and where I'd push back. Specs say *what*; this says
*why* and *watch out*.

## 1. What this project actually became

It started as "roast this repo" and became, in one session: a design-council verdict (RESHAPE) → a
hardened portable fork (encoders, capture→codegen, real-screencast, QA gates) → Codex's Scenes
workbench → a UI re-foundation (task-shaped, five rules) → and now a script-driven production
pipeline with an editing brain. The arc matters: **every layer was a response to a real deficiency,
not feature appetite.** Keep it that way. The moment something gets added because it's cool rather
than because a video shipped slower without it, this becomes the bloated NLE we explicitly swore off.

## 2. The bets I made deliberately (and would defend)

- **Preserve `bake.py` and `timeline.json` at all costs.** It's the most battle-hardened code in the
  repo (the A/V drift war is documented in its comments). Every design since — additive scene schema,
  derived `asset` pointer, VO-synth master — exists so that contract never changes. If a future
  feature requires breaking bake, the feature is wrong, not bake.
- **Immutable, content-addressed versions.** This is the single highest-leverage decision in the
  whole stack. Every "compare/revise/regenerate" interaction, the takes drawer, provenance, cost
  tracking, and crash-safety all fall out of one rule: *files are write-once, history is append-only.*
- **Task-shaped UI with five rules.** The old UI was panel-per-backend-feature. The rules (one
  selection, preview is the app, state in the top bar, takes belong to selection, words are the grid)
  are not aesthetics — they're a contract that keeps every future feature from adding a new panel.
  Rule 5 (word-snap) is the identity of this tool; the day timing edits stop snapping to speech,
  it's just another editor.
- **The script is the spine, not the transcript.** In VO mode we know intent (the script) AND
  delivery (the VO). Aligning them gives the planner something no generic auto-editor has: the
  author's structure with real timestamps. That's why plan quality can beat Descript-class tools.
- **The brain is config, not code.** EDITING-BRAIN.md is consumed as parameters (WHEN→DO with
  numbers). This makes craft tunable per channel, testable, and — later — *learnable* from retention
  data without touching the planner's code. It's also honest: rules are cited or marked [craft].
- **Courier-first generation.** No fake API adapters. The provider interface is real; fulfillment
  can be a human (or me) dropping files in an inbox today, an API tomorrow. The workbench can't tell
  the difference — that's the point.

## 3. What worries me (ranked)

1. **Codex velocity vs the SEV-1 debt.** The workbench grew fast on mutable assets, in-memory jobs,
   and un-etagged saves. If those three don't land *before* real footage flows through it, the first
   data loss will happen during a real edit night. Non-negotiable: etag saves, file-backed jobs,
   write-once assets. I put them in every build order; hold the line.
2. **The automation-slop boundary.** The brain's anti-slop section (uniform shot lengths, pumping
   ducking, template ken-burns) is the defense, but the real protection is the approval gate + the
   revise loop. Full-auto is a demo trick; **plan-approve-execute is the product.** The owner chose
   exactly this. Resist any future "skip approval" toggle until dozens of videos have calibrated
   the brain.
3. **Classifier contact with reality.** ffprobe heuristics will misclassify weird files (screen
   recordings with music, vertical b-roll, voice memos). The anti-guessing chip design absorbs this
   gracefully — but only if the chips are actually low-friction. Watch the first real drop like a
   hawk and tune.
4. **Alignment under heavy ad-libbing.** difflib fuzzy-matching degrades if the VO wanders far from
   the script. The design handles it (off-script runs attach to the previous beat + flag), but if
   the owner's real style is 40% improv, promote torchaudio forced-align from N5 to N2.
5. **Our own license exposure.** The bundled RNNoise models have no clear license. Before this fork
   is shared publicly, replace them or gate them behind a user-download step. Also: never let
   BBC SFX or madmom models sneak in via a helpful contributor.
6. **Windows.** The owner runs PowerShell. Every path join, every subprocess, every watcher must be
   tested there. Codex fixed the Python launcher detection once already — keep that discipline.

## 4. What I'd cut first if time-pressed (and what I'd never cut)

Cut first: OTIO export, CLIP semantic matching, light theme, N-overlay stacking, transitions beyond
cut/xfade. All real, none load-bearing.
Never cut: etag saves · immutable versions · the approval gate · word-snap · conform-on-ingest ·
the anti-slop checks. These are the difference between a tool and a liability.

## 5. Where the moat actually is

Not the plumbing — four repos already do Claude-drives-ffmpeg. The moat is the **stack of judgment**:
word-sync grammar (repo's soul) + the editing brain (craft as config) + immutable provenance (trust)
+ provider-agnostic generation (freedom). A competitor can copy any one file; copying the coherence
is the hard part. Protect the coherence — it's why the five UI rules and the schema discipline exist.

## 6. Honest uncertainties

- I have still not executed Codex's actual workbench code (repo/ was never uploaded). My SEV findings
  are mechanism-level; line-level audit remains open. Ship me the folder and I'll break it properly.
- The brain's numbers are anchored (LUFS, ducking ratios, Murch) but the *pacing* constants are
  calibrated to one channel's style. Treat them as defaults to be beaten by the owner's own
  retention data (the calibration flywheel is designed but unbuilt).
- Per-video cost in VO mode should be LOWER than talking-head mode (shorter transcription, fewer
  renders), but nobody has measured a real run. The telemetry item keeps being deferred; it
  shouldn't be deferred past N1.

## 7. Advice to the owner, plainly

Ship N1 (ingest) and use it on one real video before anything else gets built. Reality will reorder
the roadmap better than any of us. Keep Codex honest with tests (every behavior, a test — it's been
good at this). Feed me its real code for the audit. And when the first full drop→plan→approve→video
works end to end — publish the video ABOUT it with the tool itself. The medium is the proof.

— Fable 5
