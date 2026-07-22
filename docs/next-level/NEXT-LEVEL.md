# NEXT-LEVEL — the script-driven AI video workbench

The owner's vision, verbatim: *"I got a folder with assets and the tool takes this folder and starts
working… it checks the script, creates a plan approved by me, and starts to create everything — cuts
etc. I need a real professional editor and brain."*

This folder is the complete design for that. Three expert workstreams, one integration map, one build
order. It composes with (does not replace) the workbench packet already being built
(`fable5-workbench-packet/`: UI five rules, scene/job schemas, Slice-1 immutable takes).

## The flow

```
topic idea ──AI──▶ script ──▶ record VO + gather assets ──▶ DROP the folder
                                                              │
                        ┌─────────────────────────────────────┘
                        ▼  (~60s, offline)
     INGEST: hash → classify (ffprobe heuristics, confirm-chips when unsure)
             → conform comp-native → catalog (assets.json) → keyterms from script
                        ▼
     ALIGN: transcribe VO (existing transcribe.py) → fuzzy-align script beats
            → script-map.json (every beat gets [start_ms, end_ms])
                        ▼
     PLAN: beats × catalog × EDITING-BRAIN rules → edit-plan.json
           coverage per scene · treatment · sound intent · GAPs w/ prefilled prompts
                        ▼
     APPROVAL GATE (the one new full-screen mode) — owner approves/edits per scene
                        ▼
     COMPILE: plan → timeline.json scenes (packet schema) · VO-synth master
              (bake.py runs UNMODIFIED) · sfx-plan → existing mixers · ducked music bed
                        ▼
     WORKBENCH loop: revise scenes, takes drawer, per-scene generation
     ("scene 5 needs something" → prompt → Remotion / Hyperframe / Fable provider)
                        ▼
     bake → verify gates → package → upload (all existing)
```

## The three deliverables in this folder

1. **`PIPELINE-DESIGN.md`** (+ `editplan.schema.json`, `assets.schema.json`, both validated draft-07)
   — drop-folder contract (no required structure; conventions add signal), the classifier with an
   anti-guessing rule, script⇄VO alignment, the edit-plan schema, the approval gate, and the
   execution compiler. Load-bearing decision: **VO-mode master is synthesized** ([VO audio] + base
   video track) so `bake.py` never changes.
2. **`OSS-INTEGRATIONS.md`** — 30+ GitHub tools, licenses read from raw LICENSE files, each mapped to
   a pipeline seam with VENDOR / CLI-ONLY / IDEAS verdicts and effort sizes.
3. **`EDITING-BRAIN.md`** — the professional editor + sound designer distilled into 610 lines of
   machine-applicable rules (WHEN → DO → WHY → SOURCE, numbers not vibes): pacing grammar, cut
   placement in frames, b-roll coverage, ducking spec, −14 LUFS delivery chain, 14 plan self-QA
   checks, 10 anti-slop tells with detection rules. The planner consumes this file as config.

## Integration map (stage × what plugs in)

| Stage | Design | OSS to use | Brain rules |
|---|---|---|---|
| Ingest | PIPELINE-DESIGN §1-2, assets.schema | PySceneDetect (BSD-3) to pre-trim long b-roll into shots | — |
| Align | §3, script-map.json | torchaudio `forced_align` (BSD-2) when fuzzy-match confidence is low; difflib default | — |
| Plan | §4, editplan.schema | CLIP (MIT, later) for semantic asset↔beat matching | ALL of §1-3 + §6 self-QA before showing the owner |
| Approve | §5 (the one new mode) | — | §6 checks rendered as per-scene badges |
| Compile | §6 | @remotion/transitions + media-utils; ffmpeg sidechain (already in mix_music.py) | §2 cut offsets, §3 coverage, §4 ducking numbers |
| Sound/mix | §6 | **ffmpeg-normalize (MIT)** — closes the −14 LUTS/LUFS gap the mixers' TODOs admit | §4-5 (ducking contract, VOICE-ON-TOP, delivery spec) |
| Export | — | OpenTimelineIO (Apache-2) adapter: timeline.json → Premiere/Resolve escape hatch | — |

## Build order for Codex (after the current UI + Slice-1 work)

- **N1 — Ingest slice** (PIPELINE-DESIGN Slice A): `tools/ingest_drop.py`, classifier, assets.json,
  conform reuse, library "New" cards + confirm chips. Testable offline, no AI.
- **N2 — Align slice** (Slice B first half): keyterms-from-script, transcribe, `align_script.py`,
  script-map.json + word-snap data everywhere.
- **N3 — Plan + approval gate** (Slice B second half): plan generator reading EDITING-BRAIN.md,
  edit-plan.json rev history, the approval mode, §6 self-QA badges.
- **N4 — Compiler + sound pass** (Slice C): plan → timeline scenes + VO-synth master + sfx/music
  compile with the brain's ducking numbers + ffmpeg-normalize to −14 LUFS.
- **N5 — Polish/leverage**: PySceneDetect pre-trim, torchaudio alignment upgrade, OTIO export,
  CLIP matching.

## Action items & license landmines (from live LICENSE reads)

- **⚠ OUR OWN REPO RISK:** the bundled RNNoise model files (`tools/models/rnnoise/`) likely trace to
  a repo with **no license** (all rights reserved) — resolve before public distribution: replace,
  get clarity, or make them a user-supplied download.
- **madmom**: pretrained beat models are **CC BY-NC** — disqualified for monetized videos. Use
  librosa (ISC) for cut-on-beat.
- **videogrep**: Anti-Capitalist License — ideas only, vendor nothing.
- **BBC Sound Effects**: personal/educational only — exclude from monetized videos; prefer
  Freesound (per-file CC checks) / Pixabay / Mixkit with attribution handling in the asset catalog.
- **Remotion**: source-available; free ≤3 people, license required beyond — standing constraint,
  already known.

## Acceptance (the end-to-end demo that proves it)

Drop a folder containing `script.md`, `vo.wav`, six b-roll clips, three images, one music track →
60s later the library shows classed, conformed, tagged cards → alignment maps 12 script beats to VO
spans → the plan proposes coverage for 10 beats, flags 2 GAPs with prefilled generation prompts,
passes the brain's self-QA → owner approves with one per-scene edit → compile produces
timeline.json + VO-synth master + ducked bed at −14 LUFS → workbench opens for the revise loop →
range-bake any scene in seconds.
