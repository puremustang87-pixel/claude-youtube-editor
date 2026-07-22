# PIPELINE-DESIGN.md — the drop-folder → approved-plan → finished-cut pipeline

**The owner's dream, in his words:** *"I got a folder with assets and the tool takes this folder and
starts working… it needs to check the script and based on that create a plan, approved by me, and
start to create everything — cuts etc. I need a real professional editor and brain."*

This spec makes that literal. Drop a messy folder → the tool ingests and classifies it (no AI) →
transcribes the voiceover and aligns it to the script → drafts an **edit plan** (the brain) → the
owner **approves** it scene-by-scene → an **execution compiler** turns the approved plan into the
existing `timeline.json` + `sfx-plan.json` and bakes a preview for scene-by-scene revision.

It is **additive** over the existing repo and the workbench packet. It writes two new artifacts
(`work/assets.json`, `work/edit-plan.json`) and reuses every existing tool: `transcribe.py`,
`bake.py`, `mix_sfx.py`, `mix_music.py`, the kit (`ImageRevealShot` etc.), the scene/job schemas, the
provider/courier system, and the make-tsx / suggest-sfx flows. **Nothing in `bake.py` changes** (a
one-line `--from` mirror is the only touch, and Slice 1 of the packet already asks for it).

---

## 0. Modes (the primary one and its sibling)

| mode | trigger | master | brain source of timing |
|---|---|---|---|
| **`vo_script`** (primary) | drop folder has a **script + VO** | **synthesized**: VO audio + a base video track (see §6) | VO transcript aligned to the script (`script-map.json`) |
| `talking_head` (sibling) | drop folder has raw camera + (optional) script | the `clean-cut` master | `edited-transcript.json` (existing) |

The two modes share the **same edit-plan schema, the same approval gate, and the same execution
compiler**. `talking_head` already has a front half (`clean-cut` → master + `edited-transcript.json`);
this pipeline gives it the same plan/approve/compile back half. Everything below is written for
`vo_script` and notes where `talking_head` differs.

**Detection is mechanical, not clever:** if ingest classifies ≥1 `script` **and** ≥1 `voiceover` and
**no** long talking-head camera clip → `vo_script`. If a long camera clip dominates → `talking_head`.
Ambiguous → ask once in the top bar (a chip, not a modal).

---

## 1. Drop-folder contract

```
videos/<project>/
├─ drop/                         ← the owner dumps everything here, in any shape
│  ├─ script.md | script.docx | *.txt      the script (or notes)
│  ├─ vo/ or *.wav *.mp3                    voiceover, possibly multiple takes
│  ├─ broll/ clips/ *.mp4 *.mov             camera clips, screen captures
│  ├─ images/ *.png *.jpg                   stills
│  ├─ music/ *.mp3                          music (optional; library beds also exist)
│  └─ brand/ logos/ *.svg *.png             brand assets (optional)
└─ work/                         ← the tool's output (created for you; see below)
```

**The contract is: there is no required contract.** A single flat folder of mixed files works.
Everything the owner might do to add signal is **honored but never required**:

- **Numbered prefixes** (`03-dashboard-pan.mp4`) → parsed into `order_hint` (a soft ordering nudge).
- **Folder names** (`broll/`, `dashboard/`, `product/`) → become **tags** on every file inside.
- **Filename tokens** (`dashboard-pan` → `["dashboard","pan"]`) → tags for the matcher.
- **A `vo/` folder or `-vo`/`-narration` in the name** → a strong voiceover hint (still cross-checked
  by the audio classifier; a mislabeled file is corrected, not trusted).

The owner is never forced to rename, sort, or tag. Conventions add *signal*; their absence just makes
the classifier lean harder on the probe evidence and, at worst, surfaces a one-click confirm chip.

**The script may be born inside the tool.** Topic → AI drafts a script → the draft is written to
`drop/script.md` in exactly the shape an externally-authored script would take. So the folder
contract is the single entry point whether the script came from the tool or from the owner's laptop.
A script authored elsewhere and dropped in works with zero extra steps.

---

## 2. Classifier (ffprobe + stdlib first — NO AI)

`tools/ingest_drop.py` walks `drop/`, hashes every file (sha256 = identity, dedupe), probes it, and
writes `work/assets.json` (schema: `assets.schema.json`). **No model is called.** The evidence and the
verdict both go in the catalog so a human — or a smarter classifier later — can audit every call.

### The decision rules (evidence → class)

| class | the tells (all from ffprobe / stdlib) |
|---|---|
| **script** | text file; `heading_count>0` OR scene markers (`## Scene`, `HOOK:`, `[b-roll]`) OR `prose_ratio` high with structure |
| **notes** | text file; low structure, fragments, bullet dumps |
| **voiceover** | audio; **mono or dual-mono** (`stereo_correlation≈1`), **speech-band energy** (300–3400 Hz dominant), **has speech-gap silences** (`silence_ratio` moderate), long |
| **music** | audio; **true stereo** (`stereo_correlation<0.9`), **wide spectral spread**, **continuous** (low `silence_ratio`) |
| **broll** | video; camera-native res/fps (e.g. 3840×2160@59.94), **motion_score high** |
| **screencast** | video; **exact desktop/window resolution**, whole-integer low fps (30/60), **motion_score low**, often long static stretches |
| **image** | still raster; no alpha (or large) |
| **brand_asset** | still; **has alpha** AND small/vector, or lives in `brand/`/`logos/` |
| **unknown** | below the confidence floor — never guessed silently |

### Confidence tiers (the anti-guessing rule)

- **high (≥0.85)** → filed silently.
- **medium (0.6–0.85)** → filed, but a **soft confirm chip** shows in the library ("looks like b-roll —
  ✓ / change").
- **low (<0.6)** → `class` is usually `unknown`; a **required one-click confirm chip** appears in the
  library with the runner-up class as the one-tap alternative (`class_runner_up`). **The tool never
  silently commits a low-confidence class.** This is the same "surface, don't guess" ethic as the
  packet's etag-conflict and blocking-bake rules.

Confirm chips live **in the library card** (packet UI rule 3: no new panels; state as chips). Tapping
one flips `class` and `status`, appends any owner tag, and re-runs the matcher for affected scenes.

### Conform on ingest (reuse the packet's ingest-conform)

Every `broll`/`screencast` clip is **conformed to comp-native** (1080p, comp fps, 8-bit H.264, aac) the
same way `SLICE-1-SPEC.md`'s import path does it — probe → conform if not native → content-addressed
(`work/library/conformed/<sha8>.mp4`). This is what lets a clip drop straight into `timeline.json`
later without starving OffthreadVideo (the CLAUDE.md footage rule). Stills and audio are not conformed;
text is copied to its pipeline home.

**Filing convention (auto, not owner-facing):**
`work/library/<class>/<sha8>-<slug><ext>`, plus the VO copied to `work/audio/<id>.wav` (16 kHz mono,
the shape `transcribe.py` expects) and the script copied to `work/script/script.md`.

---

## 3. Script ⇄ VO alignment → `script-map.json`

The plan needs to know **where each script beat lands on the VO timeline**. That comes from
transcribing the VO and fuzzy-aligning it to the script.

### Step 3a — transcribe the VO through the EXISTING `transcribe.py`

The picked VO take is already at `work/audio/<id>.wav`. **The script is the perfect keyterm source:**
`tools/ingest_drop.py` extracts proper nouns / product names / tech terms from the script (capitalized
multi-word tokens, code-fenced identifiers, `Title Case` runs) and writes them to `work/keyterms.txt`
— exactly the file `transcribe.py` already auto-loads. Then:

```
python tools/transcribe.py <project> --clips <vo-id>
```

→ `work/transcripts/<vo-id>.json` (AssemblyAI word-level: `words[].{text,start,end}` in **ms**, plus
`confidence`). This is unchanged tooling; we just feed it a script-derived keyterm list, which is the
single biggest accuracy lever for the alignment that follows.

### Step 3b — align script beats to transcript spans (stdlib difflib; rapidfuzz optional)

`tools/align_script.py` (new, stdlib-only) tokenizes both the script and the transcript into normalized
word streams and runs a **token-level alignment** (`difflib.SequenceMatcher`; `rapidfuzz` used if
importable for speed, never required). For each **script beat** (a paragraph, or a sentence group per
the brain's granularity), it finds the matching transcript run and reads off `[start_ms, end_ms]` from
the aligned transcript words, plus an `align_confidence` (matched-token ratio).

Output — **`work/script-map.json`**:

```jsonc
{
  "project": "video-7",
  "vo_take_sha": "…", "transcript": "work/transcripts/vo-a.json",
  "beats": [
    { "para_index": 0, "text": "It is 4am and the render still is not done.",
      "start_ms": 0, "end_ms": 6200, "align_confidence": 0.94,
      "matched_words": [0, 14] }
  ]
}
```

### The hard cases (designed, not hand-waved)

- **VO deviates from the script (ad-libs).** Unmatched transcript runs between two matched beats are
  attached to the **preceding** beat's span (the narration kept talking) and flagged
  `vo_off_script` — the beat's `align_confidence` drops and the plan raises a warning. The owner sees
  it at the gate; nothing is silently dropped.
- **Multiple VO takes.** Each take is transcribed. Selection policy (brain-tunable, sane default):
  **per-section, pick the take whose aligned run is longest and highest-confidence for that section**;
  if takes are whole-video alternates, pick the single best-aligned one as `primary_vo` and keep the
  rest as `vo_take` roles (available as candidate audio, not used). The chosen span records its
  `vo_take_sha`, so a plan can mix takes across sections and the compiler knows which audio to cut.
- **Retakes (same section twice in one file).** The aligner collapses duplicate matched runs to the
  **last clean one** (the winning take) — the same "keep the winning take" instinct `clean-cut`
  applies to talking-head retakes, here applied to VO.
- **Low-confidence alignment.** Any beat under the floor keeps its best span but is marked
  `alignment_low_confidence`; at the gate its In/Out is a **nudgeable, word-snapped span** (packet UI
  rule 5 — word ticks are the grid) so the owner fixes it in two drags.

`talking_head` mode skips 3a/3b: it already has `edited-transcript.json`, and beats are aligned to it
the same way (the aligner takes either transcript file).

---

## 4. THE EDIT PLAN — `work/edit-plan.json` (the centerpiece)

`tools/plan_edit.py` (new) reads the script, `script-map.json`, `assets.json`, `brand.md`, and the
**editing brain** (`brain/EDITING-BRAIN.md` — authored by a separate expert; the planner *consumes*
its rules for pacing, coverage, treatment-selection, and sound taste, and **does not invent them**).
It emits `work/edit-plan.json` (schema: `editplan.schema.json`) and regenerates a human-readable
`work/edit-plan.md`.

> **Why also `edit-plan.md`?** `make-tsx` already reads `videos/<project>/work/edit-plan.md` as its
> source of truth for what to build. Regenerating the `.md` from the approved `.json` means the
> existing make-tsx flow lights up for free on any `remotion_beat` scene — no new integration.

### One scene per beat. Each scene carries:

1. **`beat`** — the script text (verbatim) + `script_ref` (for round-tripping edits) + a short title.
2. **`vo_span`** — `[start_ms, end_ms]` from `script-map.json` (+ `align_confidence`, `vo_take_sha`).
   The scene's on-timeline duration is this span; cutaways are placed **inside** it.
3. **`intent`** — `hook | explain | demo | payoff | cta | transition | recap`, inferred from position +
   script cues + brain rules. Drives treatment and sound.
4. **`coverage`** — **ranked candidate assets**, best first. The matcher scores each catalog asset:

   ```
   score =  w1 · token_overlap(beat.text tokens, asset filename+folder+tags)
          + w2 · duration_fit(asset.dur, vo_span.dur)
          + w3 · class_fit(intent → wants clip|image|screencast?)
   ```

   (weights live in the brain; sane defaults documented in `plan_edit.py`.) Each entry has legible
   `reasons` for the gate (`"tag:dashboard matches beat"`, `"dur 6.2s fits 5.8s span"`). The top pick
   is `chosen:true`; the rest are **alternates the owner can swap to** — and they become **candidate
   takes** on the scene downstream (packet takes-drawer).
5. **`treatment`** — HOW the beat is realized. Exactly one `kind`:
   - `broll_cutaway` — a conformed clip replaces the base video for the span.
   - `image_kenburns` — a still via the kit's **`ImageRevealShot`** (slow scale+opacity = the existing
     "ken burns"); the `kenburns` field carries the move.
   - `screencast` — a screen-capture clip, or a `fake-screencast` TSX beat.
   - `remotion_beat` — an authored kit shot (statement / diagram / real-UI clone) built by **make-tsx**;
     records `comp` + `props`.
   - **`generate` — the GAP (first-class).** No matching asset → the scene is `status:planned` with a
     **prefilled generation `prompt` derived from `beat.text` + intent + brand**, and a
     `provider_hint` (`remotion | hyperframe | either`). This is the exact hook the owner described:
     *"scene 5 needs something → I select the scene, add a prompt → Remotion or Hyperframe or both
     create."* The plan doesn't generate anything; it **produces the spec** the existing
     provider/courier system consumes (see §6).
   - `base_only` — pure-narration transition; the base track carries it (no cutaway).
6. **`sound`** — intent, not a mix: a `music_section` (`intro/build/steady/breath/outro/silence`) and
   `sfx[]` in the **existing suggest-sfx function-first grammar** (`motion/tension/emphasis/snap`,
   word- or moment-anchored, `layer`/`optional`). The compiler turns these into `sfx-plan.json` events.
7. **`pacing`** — `energy` + `min_hold_ms` + note; the brain decides cut density inside a span.

### GAPS are first-class citizens

A gap is not an error state — it is a planned scene awaiting a take. It renders at the gate as a scene
card with a **"generate" chip** and the editable prompt, sits in the timeline as a `planned` (slate)
block, and shows in the **"⚠ Blocking bake" filter** (packet status system) until fulfilled. Approving a
plan with open gaps is allowed; the compiler just leaves those scenes `planned` and the owner fills
them from the takes drawer (Revise-with-notes → provider/courier → candidate appears). **The plan's
gaps are literally the input to the already-designed generation system.**

### Append-only revisions (mirrors the versions philosophy)

The plan is versioned like assets are: each **frozen approval** writes the current plan to
`work/plan-revs/edit-plan.rev-N.json` and bumps `rev`. `parent_rev` records lineage. You can always
diff rev 2 → rev 3 and see exactly what the owner changed. Nothing is overwritten.

---

## 5. Approval gate — the ONE new full-screen mode

**This is the one new full-screen surface the packet's UI rules permit, and here is the justification:**
the five rules orbit *one scene selection* over *one preview + one timeline* for **per-scene revision**.
Plan approval is a different verb — a **sequential, whole-video read-through** where the owner judges
the *plan* before any bake exists. Forcing it into the single-selection editor would either (a) add a
second selection model (violates rule 1) or (b) add a panel (violates rule 3). So it is a **distinct
mode**, entered from a top-bar chip ("Review plan — 23 scenes"), and it **exits into the normal editor
shell** the moment the plan is approved. It borrows the shell's tokens, status colors, and word-grid;
it introduces no new visual language.

### What the owner sees (scene-by-scene review)

A vertical list of **scene cards**, one per beat, each showing:

- the **script beat text** (what will be narrated) + the VO span as a mini-waveform slice;
- the **chosen asset thumbnail** (or the "generate" chip + editable prompt for a GAP), with the ranked
  **alternates one click away** (swap = re-chooses `treatment.asset_sha`, marked in `edited_fields`);
- the **treatment** (b-roll / image+kenburns / screencast / Remotion beat / GAP) as a labeled chip;
- the **sound intent** (music section + SFX function chips);
- status color per the packet vocabulary (planned=slate, ready=amber-ish "draft", blocked=red).

### The gate mechanics

- **Per-scene**: approve ✓, edit (swap asset, nudge the word-snapped span, retype a prompt, change
  treatment), or reject → re-plan just that scene.
- **Wholesale**: "Approve all" for the confident majority; the owner still eyeballs the reds/slates.
- **Freezing**: "Approve plan" flips `status:approved`, stamps `approved_at`, **freezes rev N**
  (append-only), and unlocks the execution compiler. Re-opening the plan for edits forks rev N+1.
- This is the **same hard-gate ethic** as `clean-cut`'s cut audit and `suggest-sfx`'s cue-sheet audit:
  no execution before an explicit human yes.

---

## 6. Execution compiler — approved plan → the existing pipeline

`tools/compile_plan.py` (new) reads the **approved** `edit-plan.json` (rev N) and emits the artifacts
the existing tools already consume. It **orchestrates**, it does not re-implement bake/mix/render.

### 6a. The VO-mode master (designed carefully — this is the crux)

`bake.py` today assumes a real `master` A/V file: it **muxes `master` audio** (`-map 1:a:0`) over the
video, and **any timeline gap not covered by a cutaway plays the master video** (pass-through
segments). In `vo_script` mode there is **no talking-head master video.** So the compiler
**synthesizes the master before bake** so the contract holds unchanged:

> **`master.mp4` = [VO audio track] + [base video track], full VO length, comp-native (1080p, comp
> fps, 8-bit H.264, aac).** The plan records this as `master.kind:"vo_synth"` (vs `recorded_cut` for
> `talking_head` mode).

The **base track** (`master.base_track.kind`) is what shows through wherever no cutaway covers the
timeline. Options, brain-selectable, default first:

1. **`brand_slate`** (default) — a branded holding card rendered once (a `BrandBg`/`BrandProof`-style
   Remotion still or short loop). Clean, on-brand, and it means an un-covered gap looks intentional.
2. **`primary_broll`** — a chosen wide/calm clip (the asset that earned the `base_track_candidate`
   role) held/looped under the whole video; cutaways punch over it.
3. **`color`** — flat brand color (minimal).
4. **`ambient_loop`** — a slow texture loop.

The compiler builds this with plain ffmpeg (loop/hold the base video to the VO duration, mux the VO as
`a:0`) and points `timeline.json["master"]` at it. **`bake.py` then runs completely unmodified** — it
sees a normal master with audio, cutaways replace video on beats, gaps show the base track, master
(VO) audio rides underneath the whole way. This is the smallest change that preserves the contract:
**no bake extension is required.** (If the owner later wants the base track to *vary* per section
rather than be one clip, that becomes a `base_only` scene per section — still just cutaways over the
synthesized master, still no bake change.)

> **Why synthesize rather than extend `bake.py`?** The packet is explicit that `timeline.json` stays a
> compositing map and the bake contract survives as *extension, not migration*. Synthesizing the
> master keeps 100% of that contract (audio mux, pass-through video, frame-exact concat, the drift
> fixes) and adds zero risk to the most battle-hardened tool in the repo. `talking_head` mode skips
> 6a entirely — its master is the `clean-cut` output.

### 6b. Plan scenes → `timeline.json` scenes (packet `scene.schema.json`)

Each plan scene compiles to a `timeline.json` shot obeying `scene.schema.json`:

| plan field | → timeline.json (`scene.schema.json`) |
|---|---|
| `id` | `id` (verbatim) |
| treatment `broll_cutaway`/`image_kenburns`/`screencast`/`remotion_beat` | `type:"cutaway"` (concept beats cut away; overlays reserved for persistent badges per make-tsx P) |
| `vo_span.start_ms/end_ms` (÷1000) | `master_in_s` / `master_out_s` |
| `treatment.engine` | `engine` (`media`/`remotion`/`fable`/`hyperframe`) |
| chosen asset's conformed file → an **imported take** | `versions[]` append (provider `media`), `active`, derived `asset` |
| `remotion_beat` `comp` + `props` | `props` (Remotion `--props` path) — the take is the rendered shot |
| `treatment.fit` | `fit` |
| `status` (planned/ready/approved) | `status` (planned→`planned`; ready+take→`draft`; owner-approved→`draft` until bake-confirmed) |
| `beat.title`/notes | `cue` / `notes` |

Media cutaways are **placed on beat boundaries** (the vo_span is the boundary; the brain may inset the
cut a few frames so it lands on a word per make-tsx P1). Generation GAPS compile to a `planned` scene
with **no active version** and a job spec staged into `work/inbox/<job_id>/` (job schema) so the
provider/courier fills it — identical to the packet's Stage 3 courier flow.

### 6c. Sound pass (reuse suggest-sfx + music tooling verbatim)

- **SFX:** the compiler flattens every scene's `sound.sfx[]` into one `work/sfx-plan.json` in the
  **exact existing shape** (`events[].{at_s, sfx_id, gain_db, optional, cue}`), rebasing each cue's
  anchor to **absolute master `at_s`** (word anchor → transcript ms; `on:scene_in` → span start;
  `on:reveal` → the shot's reveal frame). Library-first `sfx_id` resolution and the short-form gain
  calibration are inherited from `suggest-sfx`. Then `tools/mix_sfx.py work/sfx-plan.json` mixes it
  over the bake (gentle sidechain duck already built in). **The suggest-sfx user-audit gate still
  applies** — the compiler prints the cue sheet; sound is the one place the pipeline pauses again by
  design, because a bad SFX is worse than none.
- **Music bed + sidechain ducking:** the plan's video-level `music` (bed_id, gains, duck) compiles to
  a `tools/mix_music.py --bed <id> --base <sfx-mixed-preview> --duck <db>` call. That tool already
  lays a continuous bed and **sidechain-ducks it under the VO+SFX bus** (`sidechaincompress`) — exactly
  the ducking the owner wants, already implemented. Per-scene `music_section` is advisory shaping the
  owner can tune in the audition, not a separate track.

### 6d. Compile → preview → hand to the workbench

The compiler's end state:

1. `master.mp4` synthesized (6a), `timeline.json` written (6b), `sfx-plan.json` written (6c).
2. `remotion_beat` scenes rendered via `render-all.mjs` (make-tsx path); GAP scenes left `planned`.
3. `python tools/bake.py <project>/work/timeline.json` → composited preview (VO under, cutaways on
   beats, base track in gaps).
4. `mix_sfx.py` → SFX preview; `mix_music.py` → bed-mixed preview.
5. The result opens in the **redesigned workbench** for **scene-by-scene revision**: select any scene →
   takes drawer (swap the chosen asset for an alternate, or Revise-with-notes a GAP → provider/courier
   → candidate) → range-bake ±2s → promote → re-bake. This is the packet's core loop, now *fed by the
   plan* instead of hand-built.

---

## 7. `assets.json` catalog (the durable ingest index)

Schema: `assets.schema.json`. One entry per **sha256** (identity + dedupe: same bytes = one asset,
extra paths recorded as `aliases`). Each entry: `class` + `confidence` + `confidence_tier` + runner-up,
the raw `signals` (the ffprobe/stdlib evidence, kept for auditability), auto-derived `tags` +
`order_hint`, `status` (`new|filed|used|rejected`), `filed_to`, the `conformed` rendition for clips,
`roles`, and **`referenced_by`** (which scenes use it — drives `used` and safe-delete). It is written by
`ingest_drop.py`, updated by the library confirm-chip endpoint, and read by the matcher in §4. It is
the ingest counterpart to the packet's version store: content-addressed, provenance-carrying,
non-destructive.

---

## 8. Slice plan (each testable, each with an acceptance demo)

### Slice A — Ingest + Classify + Catalog (NO AI, no alignment, no plan)

**Build:** `tools/ingest_drop.py` (walk → hash → probe → classify → conform clips → write
`assets.json`), the classifier heuristics, and the **library confirm-chip** endpoint + UI (medium/low
tiers). Reuses the packet's ingest-conform + path-safety.

**Tests (`tools/test_ingest.py`):**
1. Dedupe: two identical files → one asset, second path in `aliases`.
2. Class calls: a mono speech WAV → `voiceover`; a stereo music mp3 → `music`; a 3840×2160@59.94
   high-motion clip → `broll`; a 1920×1080@30 low-motion desktop-res clip → `screencast`; a
   heading-structured `.md` → `script`; a bullet dump → `notes`; a transparent PNG in `logos/` →
   `brand_asset`.
3. Confidence tiers: a borderline audio file lands `medium` and emits a confirm chip payload; a weird
   file lands `unknown`/`low` and is **never** silently classed.
4. Convention parsing: `03-dashboard-pan.mp4` → `order_hint:3`, tags `["dashboard","pan"]` + folder tag.
5. Conform: a 10-bit VFR clip → `conformed.probe.conformed`-equivalent comp-native, duration ±50ms
   (mirrors SLICE-1 test 4).
6. Path safety: a `../` or absolute-outside-drop path in the tree → rejected (SLICE-1 test 5 ethic).
7. `assets.json` validates against `assets.schema.json`.

**Acceptance demo (3 min):** drop a mixed folder (script, 2 VO takes, 3 clips, 2 images, a logo,
a music mp3) → `assets.json` appears → library shows correctly-classed cards, one medium-confidence
clip showing a confirm chip → tap it → class flips and persists. **Zero AI, zero API.**

### Slice B — Align + Plan + Approval gate

**Build:** `align_script.py` (keyterms from script → `transcribe.py` → `script-map.json`),
`plan_edit.py` (matcher + treatment/sound/pacing from `EDITING-BRAIN.md` → `edit-plan.json` +
`edit-plan.md`), and the **plan-approval full-screen mode** (§5).

**Tests (`tools/test_plan.py`):**
1. Alignment: a script + a clean VO of it → every beat gets a span; total spans cover the VO; ad-lib
   run → `vo_off_script` warning on the right beat.
2. Multi-take: two VO takes → `primary_vo` chosen by longest+confidence; per-section pick recorded.
3. Matcher: a beat mentioning "dashboard" ranks the `dashboard/` clip first with legible reasons;
   duration-fit tie-breaks two same-tag clips.
4. GAP: a beat with no matching asset → `treatment.kind:generate`, non-empty `prompt` derived from the
   beat, `status:planned`, and a `no_coverage` warning.
5. Revisions: approve → `rev` bumps, `plan-revs/edit-plan.rev-0.json` written; edit + re-approve →
   rev 1 with `parent_rev:0`; rev 0 untouched on disk.
6. Gate freeze: approving flips `status:approved` + `approved_at`; per-scene edits recorded in
   `edited_fields`.
7. `edit-plan.json` validates against `editplan.schema.json`; `edit-plan.md` regenerated and parseable
   by make-tsx's reader.

**Acceptance demo (5 min):** on Slice A's project, run align + plan → open the approval mode → read
23 scene cards, swap one chosen asset for its alternate, nudge one low-confidence span to word ticks,
retype one GAP prompt → "Approve plan" → `edit-plan.json` is `approved`, rev 0 frozen.

### Slice C — Execution compiler + sound pass

**Build:** `compile_plan.py` (synthesize VO master 6a → `timeline.json` 6b → `sfx-plan.json` 6c → bake
→ mix), wiring GAP scenes to the provider/courier + jobs.

**Tests (`tools/test_compile.py`):**
1. VO master: synthesized `master.mp4` has `v:0`≈`a:0` duration (±50ms) and VO as `a:0`; base track
   fills full length.
2. Timeline mapping: each ready plan scene → a valid `scene.schema.json` shot; ms→s correct; chosen
   conformed clip appears as `versions[0]` with derived `asset`; GAP → `planned`, no `active`.
3. Bake: `bake.py` on the compiled timeline runs **unmodified**, `v:0==a:0`, cutaways land on their
   spans, gaps show the base track (frame check at a gap and at a cutaway).
4. SFX: `sfx-plan.json` matches the existing shape; `mix_sfx.py --print` cue sheet is legible; a
   story-critical cue measures ≥+4 dB (suggest-sfx audibility check).
5. Music: `mix_music.py --bed … --duck …` produces a bed that ducks under the VO (RMS lower under
   speech than in gaps).
6. GAP fulfillment: a courier drop into `work/inbox/<job_id>/` → ingest-conform → candidate on the
   scene → promote → re-bake shows it.

**Acceptance demo (7 min):** approve the Slice B plan → `compile_plan.py` → watch the composited,
SFX'd, bed-mixed preview: VO narration throughout, b-roll/images/Remotion beats on their beats, brand
slate in the two gaps, one GAP scene still a slate placeholder → select that GAP in the workbench →
Revise-with-notes → courier drops a clip → promote → range-bake → the gap is filled. **The owner
dropped a folder and got a professionally cut video with one approval and a few taps.**

---

## 9. What is reused vs. new (nothing is thrown away)

**Reused unchanged:** `transcribe.py` (+ `keyterms.txt` contract), `bake.py` (one-line `--from` from
SLICE-1), `mix_sfx.py`, `mix_music.py` (sidechaincompress ducking), the kit (`ImageRevealShot`,
`BrandBg`, VS Code / browser clones), `render-all.mjs`, the make-tsx + suggest-sfx flows and their
audit gates, `scene.schema.json`, `job.schema.json`, the provider/courier + inbox + jobs system, the
ingest-conform + path-safety + etag machinery, the five UI rules and status vocabulary.

**New (small, additive):** `ingest_drop.py` + `assets.json`/`assets.schema.json`; `align_script.py` +
`script-map.json`; `plan_edit.py` + `edit-plan.json`/`editplan.schema.json` (+ regenerated
`edit-plan.md`); `compile_plan.py`; the confirm-chip endpoint; the plan-approval full-screen mode. The
brain rulebook (`brain/EDITING-BRAIN.md`) is authored separately and *consumed*, not written here.

---

## Appendix — data-flow at a glance

```
drop/ ──ingest_drop.py──▶ assets.json  (class+conform+tags, NO AI)
                              │
script ──keyterms──▶ transcribe.py ──▶ transcripts/*.json
                              │
                    align_script.py ──▶ script-map.json  (beat → [ms,ms])
                              │
  assets.json + script-map + EDITING-BRAIN.md + brand.md
                              ▼
                    plan_edit.py ──▶ edit-plan.json  ( + edit-plan.md )
                              │            (scenes: beat·span·intent·coverage·treatment·sound·pacing; GAPS first-class)
                              ▼
              ┌── APPROVAL GATE (the one new full-screen mode) ──┐
              │  per-scene ✓ / edit / reject · wholesale · freeze rev N (append-only)
              └───────────────────────┬──────────────────────────┘
                                      ▼  (approved)
                    compile_plan.py
                       ├─ synthesize VO master  = VO audio + base track   → bake contract holds
                       ├─ timeline.json (scene.schema.json)               → bake.py UNMODIFIED
                       ├─ sfx-plan.json (suggest-sfx shape)               → mix_sfx.py
                       ├─ music (bed+duck)                                → mix_music.py (sidechain)
                       └─ GAP scenes → provider/courier + jobs            → candidates → takes drawer
                                      ▼
                    composited + SFX'd + bed-mixed preview
                                      ▼
              redesigned workbench: select scene → takes drawer → revise/promote → range-bake
```
