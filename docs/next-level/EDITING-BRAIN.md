# EDITING-BRAIN.md — the craft rulebook the plan-generator consumes

> **What this is.** A machine-applicable rulebook of professional video-editing and sound-design
> craft, written for a plan-generator that turns *(script + voiceover + b-roll folder)* into an
> **edit plan** (the `timeline.json` scene list + `sfx-plan.json` + a final-mix spec). Every rule is
> `WHEN (condition on a script beat / VO word-time / asset) → DO (parameterized action) → WHY (one
> line of craft) → SOURCE`. Numbers, not adjectives: durations in **seconds and frames**, levels in
> **dB / LUFS**, cut offsets in **frames**.
>
> **Comp contract (fixed).** 1920×1080, **30 fps**. 1 frame = **33.33 ms**. All frame counts below are
> at 30fps. (The v1 example masters render 4K60 for delivery; the *plan* is authored against the
> 1080p30 bake — `timeline.json.preview` is `{width:1920,height:1080,fps:30}`, and the v2 scene schema
> probes takes at `{w:1920,h:1080,fps:"30/1"}`.)
>
> **Voice (inherited, non-negotiable).** This EXTENDS the repo's shipped grammar; it must never
> contradict it:
> - **Pacing** = *content-aggressive + pause-natural*. Cut fluff/redundancy hard; keep **~0.45–0.5 s**
>   of natural breathing between speech runs. Do not crush silence. (`clean-cut` Cut policy.)
> - **SFX** = *function-first* (motion / tension / emphasis / snap), *library-first*, *felt-not-heard*,
>   density **~8–12 cues/min**, layered only on the **2–3 hero beats**, synced to the **visual beat AND
>   the word**, always **under the voice**. Calm/premium (Linear / Anthropic / Vercel), **NOT
>   MrBeast-loud**. Silence is part of the mix. (`suggest-sfx`, `brand.md` §10.)
> - **Visuals** = sync to words, never pre-empt; concept beats are full-screen cutaways; real UI/pages
>   for facts; motion is calm fade-and-rise, no bounce. (`make-tsx`, `brand.md` §6/§7.)
>
> **How to read a SOURCE tag.**
> `[cited: URL]` = anchored in a named authority (verified reachable at time of writing).
> `[industry-standard]` = a platform/standard-body number treated as settled across the field, source
> URL given; the *exact* figure is the field's consensus, not this author's invention.
> `[craft]` = this editor's professional synthesis / calibration — a judgment call, not a citable law.
> `[repo]` = already encoded in this repo's skills; restated here so the planner has one brain.
>
> **Frame-time helper (used everywhere).** For a narration cue at `t` seconds inside a scene whose
> `master_in_s = m`: `local_frame = round((t − m) × 30)`. Word start/end come from
> `edited-transcript.json` (ms, master clock). "On the word" = the visual/audio event's frame ==
> `round(word.start_ms/1000 × 30)`. [repo]

---

## 0. Inputs the planner is assumed to have (the condition vocabulary)

Rules below fire on these. If a field is missing, the rule that needs it degrades to its stated
fallback and raises a plan check (Section 6).

- **Script, beat-tagged.** Each beat has a `beat_type` ∈ `{hook, context, explain, demo, payoff, cta}`
  and a text span. If untagged, the planner classifies (heuristics in §1).
- **VO word-times** — `edited-transcript.json`: `words:[{text,start,end}]` in ms on the master clock.
  This is the spine; every cut, reveal, and SFX cue resolves to a word time. [repo]
- **Asset folder** — b-roll clips, images, screen recordings, plus GAP scenes (no asset yet). Each
  candidate asset carries (or the planner infers) `tags`/`nouns` it depicts, and `motion` (has camera
  or subject motion? has a discrete on-screen action?).
- **Scene schema (v2)** — the planner emits `timeline.json.shots[]` per `scene.schema.json`:
  `{id, type:cutaway|overlay, master_in_s, master_out_s, cue, engine, status:planned|generating|draft|approved,
  transition_in:{kind:cut|xfade, frames}, props, active, versions[]}`. A **GAP scene** =
  `status:"planned"`, `active:null`, engine `fable|hyperframe|remotion`, needing a generation
  spec (`provenance.spec.prompt` or comp+props). [repo, workbench scene schema]

---

## 1. Story pacing grammar

Beat-type durations are **targets for narration-driven tech/AI education**, not hard walls. The lever
is *cut fluff, keep breathing* — never pad a beat to hit a number, never crush a pause to hit one.

### 1.1 Beat-type target durations & jobs

| WHEN `beat_type` = | DO — target on-screen duration | job / note |
|---|---|---|
| **hook** | **2–8 s** total; the *promise/payoff-preview* must be spoken AND visible by **1.5 s** (≤ frame 45). Hard ceiling **10 s** before the first hard visual change. | Earn the click. State the value, show the destination. |
| **context** | **8–25 s**. If > 25 s, split or suspect fluff (flag). | Why-you-should-care. Shortest beat that still lands stakes. |
| **explain** | **15–45 s** per sub-idea; **one idea per beat**. Insert a visual change every 3–7 s (§1.3). | The teaching. Longest beats live here; keep them moving with cutaways/punch-ins, not filler. |
| **demo** | **10–40 s**; real screen content / real result. A demo that is pure proof (latency, live output) may run to **60 s** if motion is continuous. | Proof. Real UI/recording (`make-tsx` P3–P5). [repo] |
| **payoff** | **3–12 s**; the reveal lands ON the payoff word (§4 build-and-drop). | The "boom." Layered SFX + tightest sync live here. |
| **cta** | **5–15 s**; single ask. | One action. Overlay badge, not a full rebuild. |

- **WHY:** tech-education retention is won by *information density with breathing room*, not by any
  single ideal length; over-long beats without a visual change are the #1 drop point.
  [industry-standard — YouTube retention practice; e.g. creator-education analyses at
  https://backlinko.com/youtube-ranking-factors and https://blog.frame.io/ ] + [craft calibration]

### 1.2 Where the hook payoff must land

- **WHEN** the video starts → **DO** place the hook's promise (the "you'll get X / here's the thing")
  so its VO **ends by 8 s** and a matching **visual is on screen by frame 45 (1.5 s)**. Never open on a
  static title card held > 1.5 s with no motion. **WHY:** the first ~5–15 s decides the retention
  curve's baseline; a cold, motionless open bleeds viewers before the value is stated.
  [industry-standard — https://backlinko.com/youtube-ranking-factors] + [craft]
- **WHEN** the script has a "results/finished thing" that is the video's whole promise → **DO** flash
  a **0.5–1.0 s** preview of it inside the hook (a "cold-open of the destination"), then cut back to
  setup. **WHY:** a concrete future-state image is a stronger retention hook than a verbal promise.
  [craft]

### 1.3 Pattern-interrupt cadence (visual change for retention)

- **WHEN** any beat runs longer than the cadence window → **DO** introduce a **visual change every
  4–7 s** for `explain`/`context`, every **6–10 s** for `demo` (a demo's own on-screen motion counts as
  the change). A "visual change" = any of: cut to b-roll cutaway, cut back to base track, punch-in,
  new full-screen TSX beat, a scored reveal, or a screen scroll/zoom move. **WHY:** a fresh visual
  every few seconds resets attention; motionless stretches are where the retention graph dips.
  [industry-standard — retention-editing practice; https://blog.frame.io/ , https://backlinko.com/youtube-ranking-factors]
  The **specific 4–7 s number is [craft]** calibrated to this channel's calm register (fast enough to
  hold, slow enough to not feel frantic — a MrBeast channel would run 2–4 s; we deliberately do not).
- **HARD RULE (never violate):** a single held frame (talking head OR one b-roll clip OR one image)
  with **no motion and no cut for > 8 s** is a defect → the planner must insert a punch-in, a drift, a
  cutaway, or a cut. **WHY:** > 8 s of visual stasis is the strongest single predictor of a drop in
  narration video. [craft, from retention practice above]
- **Anti-metronome:** the cadence is a *ceiling on stasis*, not a beat grid. Do **not** cut on a fixed
  interval; vary interrupt spacing by ±40% and always motivate the change by the narration (§7 A1). [craft]

### 1.4 Punch-in vs cut-away

- **WHEN** the narration stays on the *same subject/idea* but needs re-energizing (emphasis line,
  "here's the key part") and the base track is the talking head → **DO** a **punch-in**: scale the base
  frame **1.08–1.15×** (never > 1.20×) over the emphasis, hold, then release on the next idea. Land the
  push so it *arrives* on the emphasized word (end the scale ramp at the word). **WHY:** a punch-in
  says "lean in" without leaving the speaker; > 1.2× on a 1080p source turns soft. [craft]
  ([repo] real-screencast uses the same ceiling: 1080p capture "will look soft when zoomed" — keep pushes modest.)
- **WHEN** the narration names a *thing, place, product, or action* the viewer should SEE → **DO** cut
  **away** to b-roll/image/screen of that noun (see §3 name-match). **WHY:** show the noun, don't just
  say it. [repo make-tsx P1/P3–P5]
- **WHEN** two consecutive emphasis moments are < 3 s apart → **DO** punch-in on the first, cut-away on
  the second (or vice-versa) — do not punch-in twice in a row. **WHY:** repeated identical pushes read
  as a tic (§7 A5). [craft]

---

## 2. Cut placement rules

All offsets are **frames @30fps**. "Word start/end" from `edited-transcript.json`.

### 2.1 Cut relative to word boundaries

- **WHEN** cutting the master to a new scene (cutaway) on a spoken cue → **DO** place the video cut
  **2–4 frames (≈67–133 ms) BEFORE** the target word's `start`. **WHY:** the eye is slower than the
  ear; landing the picture a hair early makes the image feel like it *causes* the word, not chases it.
  [craft] (the repo's screencast rule is the same instinct: "put the click a few frames **before** the
  word so the *result* lands on the word" — [repo fake-screencast]).
- **WHEN** cutting BACK to the base track at the end of a cutaway → **DO** cut on the **first frame of a
  short pause** (a gap ≥ 0.25 s in the transcript), not mid-word. **WHY:** returning to the speaker on
  a breath is invisible; returning mid-word is a bump. [repo clean-cut pause grammar] + [craft]
- **NEVER-CUT ZONE (hard):** never place a cut **inside an emphasized/stressed word** or within
  **2 frames** of a word's `start` or `end` where that word carries the sentence's emphasis (the
  payoff noun, a named product, a number). Snap the cut to the nearest pause or the pre-roll of the
  *next* word. **WHY:** a cut mid-emphasis clips the very word the beat exists to deliver.
  [craft; aligns with clean-cut's snap-to-audio tails that protect word releases — [repo]]
- **WHEN** the transcript shows the word onset is soft (breath/plosive lead-in) → **DO** keep a
  **head lead-in of 3–6 frames** before the word on the incoming scene's audio bed so the word isn't
  born clipped. **WHY:** clipped onsets read as sloppy; the repo's `head` knob (0.11–0.19 s) exists for
  exactly this. [repo clean-cut style params]

### 2.2 J-cuts and L-cuts (split edits) for narration + b-roll

A **split edit** = audio and video cut at different times. **J-cut** = the audio of the *next* scene
starts *before* its picture (audio leads); **L-cut** = the audio of the current scene continues *under*
the next scene's picture (audio lags). [cited: https://en.wikipedia.org/wiki/Split_edit]

- **WHEN** a b-roll cutaway is about to appear and its own scene carries diegetic/UI sound (a click, a
  typing sound, an app chime) → **DO** J-cut that sound in **3–6 frames (100–200 ms) before** the
  picture cut. **WHY:** ear-before-eye pulls the viewer into the new shot; it feels intentional, not
  abrupt. [cited: split edit] + [craft offset]
- **WHEN** cutting from a b-roll cutaway BACK to the talking head → **DO** L-cut: let the b-roll's
  ambience/sound tail **3–8 frames** *under* the returning talking-head picture, then fade it. **WHY:**
  an L-cut smooths the seam so the return doesn't "pop." [cited: split edit] + [craft]
- **WHEN** narration is continuous over a series of b-roll shots (the common tech-explainer case, VO on
  top, no diegetic audio) → **DO** treat the VO as the *base audio* that spans all cutaways
  (never cut the VO at picture boundaries); the picture cuts float over one unbroken narration bed.
  This is a global L-cut of the VO over everything. **WHY:** VO continuity is what makes a montage of
  clips read as one thought; cutting the VO at every picture change chops the sentence. [repo — master
  audio always continues under a cutaway] + [craft]
- **Split-edit magnitude cap:** keep audio/video offsets between **3 and 12 frames** for this calm
  register; > 12 frames of pre-lap starts to feel like a trailer. [craft]

### 2.3 Cut-on-action (match on action) for b-roll

- **WHEN** a b-roll clip contains a discrete motion (a hand reaches, a door opens, an object moves, a
  UI element slides) and the next shot continues or answers that motion → **DO** cut **during** the
  motion, at the frame the motion is ~50–70% through its arc, so the movement continues across the
  cut. **WHY:** a cut hidden inside motion is invisible — the eye tracks the movement across the edit.
  [cited: https://en.wikipedia.org/wiki/Match_on_action , https://en.wikipedia.org/wiki/Continuity_editing]
- **WHEN** two shots of the *same subject* would cut together with the camera < 30° different in angle
  or framing → **DO NOT** cut them together (it reads as a jump cut); either change angle/size by a
  clear step, insert a cutaway between them, or use one continuous shot. **WHY:** the **30-degree rule**
  — a too-similar cut looks like a glitch in time, not an edit.
  [cited: https://en.wikipedia.org/wiki/Continuity_editing (30-degree rule) , https://en.wikipedia.org/wiki/Jump_cut]

### 2.4 Scene-change vs in-scene cut grammar

- **WHEN** moving to a NEW section/topic (a `beat_type` boundary, esp. hook→context, explain→demo,
  →payoff) → **DO** mark a **scene change**: allow a full-screen cutaway or a section card, and this is
  the natural home for a `transition_in.kind:"xfade"` (crossfade) or a scored transition (whoosh, §4).
  Crossfade length **5–10 frames** (default 5). **WHY:** a section boundary is the one place a soft
  transition reads as structure, not indecision. [repo scene schema `transition_in`; make-tsx cutaway]
- **WHEN** cutting *within* the same idea (base↔cutaway, cutaway↔cutaway on the same topic) → **DO**
  use a **hard cut** (`transition_in.kind:"cut"`), no dissolve. **WHY:** in-scene dissolves feel like
  a screensaver; hard cuts keep energy. [craft; continuity-editing convention of invisible hard cuts]
- **WHEN** the plan would place an `xfade` whose overlap window covers any word's `start..end` → **DO**
  shift the xfade so its overlap sits entirely in a pause, or shorten it. **WHY:** a dissolve blurring
  the frame under a key word muddies both. [craft] (checked in §6).

---

## 3. B-roll coverage rules

B-roll = supplemental footage intercut with the base track to cover/illustrate the narration; in
documentary practice it's cut to *support what the subject said*.
[cited: https://en.wikipedia.org/wiki/B-roll]

### 3.1 Cutaway length

- **WHEN** inserting a b-roll cutaway → **DO** hold it **1.5–6.0 s** (frames **45–180**). Minimum **1.2 s
  (36 f)** for a single-noun illustration; below that it flickers. **WHY:** under ~1.2 s the viewer
  can't read the shot; over ~6 s a *single* b-roll clip becomes wallpaper (see 3.3). [craft; consistent
  with retention cadence §1.3]
- **HARD RULE:** **no cutaway > 6.0 s without a narration-motivated change inside it** (a new noun
  named, a punch-in/zoom move, a scored beat, or a cut to a *different* cutaway). **WHY:** a >6 s static
  clip with the VO having moved on is the definition of wallpaper b-roll (§7 A2). [craft] (checked §6.)
- **WHEN** a demo/screen recording is genuine proof with continuous motion → **DO** allow up to **60 s**
  (see §1.1) but require a zoom/highlight move at least every **8 s**. **WHY:** proof earns length only
  if it keeps revealing. [repo real-screencast] + [craft]

### 3.2 How long before returning to the base track

- **WHEN** a stretch of narration is covered entirely by cutaways → **DO** return to the base track
  (talking head) at least once every **12–18 s**, and always at a section boundary or a personal/
  opinion line ("I think", "my take"). **WHY:** the presenter is the trust anchor; disappearing for
  too long makes the video feel like a faceless slideshow. [craft; documentary A-roll/B-roll balance —
  https://en.wikipedia.org/wiki/B-roll] 
- **WHEN** returning to the base track → **DO** hold the talking head **≥ 2.0 s (60 f)** before the next
  cutaway. **WHY:** a re-appearance shorter than ~2 s reads as a flicker, not a re-grounding. [craft]

### 3.3 Images with Ken Burns (reuse the repo's drift/zoom vocabulary)

Vocabulary is the repo's, do not invent a new one: **`drift`** = the constant slow "alive" push
(default **0.01–0.02**) applied to any held image/page so it's never a frozen frame; **`zoom`** =
`{from, to, fx, fy, range:[startFrame, endFrame]}`, a ken-burns push with `fx`/`fy` the **image-fraction
focal point**, whose `range` **ends ON the payoff word**. [repo fake-screencast / real-screencast]

- **WHEN** placing a still image → **DO** always apply `drift` (0.01–0.02) for its whole life; **never**
  show a still with zero motion. **WHY:** a truly static image on a moving-video timeline reads as a
  freeze/bug. [repo — "a beat of drift, never a frozen frame"]
- **WHEN** an image is on screen ≥ 2.5 s → **DO** add a Ken Burns `zoom`, `from:1.0 to:1.15–1.30`
  (**hard ceiling `to ≤ 1.4`**), `range` spanning most of the hold and **ending on the beat's payoff
  word**. Pick `fx,fy` on the meaningful part of the image (the face, the result, the headline).
  **WHY:** a slow directed push turns a static asset into a shot with intent and lands the emphasis on
  the word. [repo — example `zoom:{from:1.0,to:1.4,...}` "ken-burns onto the payoff card"; end range on
  payoff word] + [craft ceiling]
- **Ken-burns duration & rate:** target push rate **~0.03–0.10× per second** (e.g. 1.0→1.2 over 2–3 s).
  Image hold **2.5–6.0 s**. **WHY:** faster than ~0.12×/s feels like a lurch; slower than ~0.02×/s is
  indistinguishable from drift. [craft]
- **HARD anti-slop:** do **NOT** apply the *same* zoom direction + *same* rate to every image. Vary
  direction (in/out, and the `fx,fy` focal corner) and rate per image; and not every image gets a big
  zoom — some get drift only. **WHY:** identical Ken Burns on every image is the single most obvious
  auto-edit tell (§7 A6). [craft]

### 3.4 Name-match: b-roll must land ON the noun (no wallpaper)

- **HARD RULE:** every b-roll cutaway and image must **depict a noun/subject spoken within ±1.0 s (±30
  f)** of the picture cut (the asset's `tags`/`nouns` must intersect the words in that window). If no
  asset name-matches the beat, the slot becomes a **GAP scene** with a generation prompt (§6), not a
  generic clip. **WHY:** the repo already believes reveals land ON words; b-roll that doesn't match the
  narration is "wallpaper" — decoration that dilutes rather than illustrates. [repo make-tsx P1/P2;
  brand.md §10 sync] + [craft]
- **WHEN** the narration names a specific product / service / page / config → **DO** cover it with the
  **real UI / real page**, not abstract b-roll (TSX clone or screenshot; highlight the exact line).
  **WHY:** facts get real surfaces; generic stock over a real claim looks evasive. [repo make-tsx
  P3/P4/P5]
- **WHEN** two adjacent cutaways would depict the *same* noun → **DO** collapse to one (or differentiate
  the second by angle/scale). **WHY:** repeating the same illustration wastes a cut and reads as filler.
  [craft]

---

## 4. Sound design grammar

Extends `suggest-sfx` + `brand.md` §10. Function-first (**motion=whoosh, tension=riser,
emphasis=impact/pop, snap=click**), library-first, density **8–12 cues/min**, layer only the **2–3 hero
beats**, everything else single, **under the voice**, silence is a tool. [repo]

### 4.1 Music-bed structure

The bed is the *final-mix* layer (`tools/gen_music.py` + `tools/mix_music.py`), separate from the SFX
pass. These rules tell the planner where a bed should live and where it must not. [repo brand.md §10]

- **WHEN** the intro/hook begins → **DO** start the music bed at **t=0 or on the first hook word**, fade
  in over **0.5–1.0 s**. **WHY:** the bed sets tone from frame one; a late bed feels bolted on. [craft]
- **HARD RULE:** the bed **must never enter or change mid-word.** Snap any bed entrance/section change
  to a **pause ≥ 0.3 s** or a hard scene boundary. **WHY:** a swell blooming under a syllable is the
  clearest "the music was pasted on later" tell. [craft] (checked §6.)
- **WHEN** a section boundary is also an energy change (context→explain, →demo) → **DO** transition the
  bed *at the boundary*: either a **sub-section variation** or a short **duck-to-silence (0.3–0.6 s)**
  then re-enter. **WHY:** bed changes on structure read as intentional scoring. [craft]
- **WHEN** approaching a **payoff** reveal → **DO** place a **riser** (`riser-soft`, gentle, NO
  cymbal-urgency) starting **1.0–2.0 s** before the reveal word, its energy peaking **on** the reveal
  frame; the bed may dip **−3 dB** into the riser and resolve after. **WHY:** "something is coming" then
  release is the core build-and-drop; it's where sound earns its keep. [repo brand.md §10 build-and-drop]
- **WHEN** a section ends into a hard topic change or a serious/quiet line → **DO** consider **ending
  the bed** (fade out 0.4–0.8 s) and running that beat **dry** (see 4.5). **WHY:** taking the music away
  is louder than any swell. [repo — "silence is part of the mix"]
- **WHEN** a scene transition is scored → **DO** allow a **sting/impact** (`impact-soft`) landing **on
  the first frame of the new scene**, optionally preceded by a **whoosh** (see 4.3). Reserve stings for
  section boundaries, not in-scene cuts. **WHY:** a sting punctuates structure; on every cut it becomes
  noise (§7 A4). [repo]

### 4.2 Ducking spec (VO over music — the sidechain contract)

Ducking = reduce the music level by the presence of the voice; the duck engages **as soon as the voice
starts.** [cited: https://en.wikipedia.org/wiki/Ducking]. Implemented as sidechain compression on the
music bus keyed by the VO. [cited: https://en.wikipedia.org/wiki/Dynamic_range_compression]

| Param | Value | Why |
|---|---|---|
| **Target music level under speech** | **−18 to −22 LUFS** (≈ **12–15 dB below** the VO), i.e. music sits ~**−12 dB** relative to dialog when the VO is present | Music must be *felt*, never *fight* the words. [industry-standard VoD dialog/music practice] + [craft] |
| **Music level in VO gaps** | rise to **−14 to −16 LUFS** (the "up" state) | The bed breathes up between sentences so it doesn't feel dead. [craft] |
| **Sidechain ratio** | **4:1 to 8:1** | Enough to move the bed decisively without pumping. [cited: compression basics] + [craft] |
| **Threshold** | set so a normal VO word triggers ≈ **10–14 dB** of gain reduction | Consistent, audible duck keyed to speech. [craft] |
| **Attack** | **40–80 ms** (fast enough to catch the word onset, not so fast it clicks) | Duck must be down *before* the word is loud, without a transient click. [craft; ducking engages at speech onset — cited above] |
| **Release** | **250–400 ms** | Bed recovers smoothly in the gap between words; faster than ~200 ms pumps, slower than ~500 ms sounds sluggish. [craft] |
| **Hold** (if available) | **80–150 ms** | Prevents the bed flapping up during tiny intra-word gaps. [craft] |
| **Knee** | soft | Gentle onset of ducking = calm register, not a gate. [cited: soft knee — compression basics] |

- **HARD RULE:** if measured music-under-VO is **< 10 dB** below the VO → too loud (masks words); if
  **> 18 dB** below → the bed is inaudible, cut it or raise it. **WHY:** the whole point of a bed is to
  be present-but-subordinate. [craft] (checked §6.)
- **Anti-pump:** if the bed's level is visibly oscillating with the syllable rate → release too fast /
  ratio too high; lengthen release toward 400 ms and drop ratio toward 4:1. **WHY:** audible pumping is
  the amateur-mix tell (§7 A3). [craft]

### 4.3 SFX placement (extends suggest-sfx)

All cues **sync to the visual beat AND often the exact word** (off-by-100 ms reads as sloppy — use real
frame/word times). [repo]

- **Whoosh on transitions.** **WHEN** a section-boundary scene change or a full-screen cutaway
  enters → **DO** place `whoosh-soft` so its *body* leads into the cut and its tail lands **on the cut
  frame** (start the whoosh **6–10 f before** the picture cut). One whoosh per transition, not per cut.
  **WHY:** the whoosh carries one shot into the next; on every cut it's noise. [repo brand.md §10 motion]
- **UI ticks on screencast interactions.** **WHEN** a screen recording / fake-screencast shows a
  discrete interaction (click, toggle, send, type) → **DO** place `ui-click-soft` / `ui-toggle-on` /
  `ui-send` on the interaction's frame; fire it **~2 f BEFORE** the on-screen result appears, aligned to
  the noun naming it. A 3–4-click sequence = **ONE** gesture (one approved cue-group), not four cues.
  **WHY:** small, satisfying, alive; sync to the action or it reads mistimed. [repo suggest-sfx +
  fake-screencast "click a few frames before the word"]
- **Sub-drop / impact on reveals.** **WHEN** a **payoff** reveal lands → **DO** place `impact-soft` /
  `impact-deep-soft` (long tail) **on the reveal frame = the payoff word**, optionally layered under a
  preceding riser (4.1). **WHY:** "this moment matters," landing as the new shot/word arrives. [repo
  brand.md §10 emphasis]
- **Signature motif.** **WHEN** a recurring UI element appears (e.g. the "Free Image Generator" toggle)
  → **DO** reuse the SAME clip (`ui-toggle-on`) every time as a sonic through-line. **WHY:** one
  recurring sound is branding; a new sound each time is noise. [repo brand.md §10 signature motif]
- **Density gate.** **WHEN** authoring the cue list → **DO** keep **8–12 core cues/min**; everything past
  that is `"optional": true`. **WHY:** more is not better; the same few foundational sounds do the heavy
  lifting. [repo]
- **Layer only the hero beats.** **WHEN** a beat is one of the **2–3 biggest moments** → **DO** layer
  (riser→impact, or whoosh→pop) as two events at the same/adjacent `at_s` that sum; **everywhere else,
  one sound and silence between.** **WHY:** build-and-drop is where SFX earn their keep; layering
  everything flattens the dynamics. [repo brand.md §10]

### 4.4 SFX levels (inherited, restated as the contract)

Library clips are loudness-normalized to **~−20 LUFS** with a **−1.5 dBFS** peak ceiling; VO is
**~−17 LUFS**; a cue's `gain_db` sets how far *under* the voice it sits. [repo]

| Cue class | `gain_db` (long-form) | note |
|---|---|---|
| Payoff impact / hero | **−5 to −6** | the loudest SFX allowed, still under the voice |
| Transition whoosh | **−6 to −8** | carries the cut, doesn't announce it |
| Bed/texture SFX | **−9 to −11** | felt-not-heard |
| Percussive transients (knock/stamp/snap/keys) | **+3 to +5 dB above** the table value | they hit the −1.5 dBFS peak *before* the −20 LUFS loudness target, so they catalog ~3–5 dB quiet [repo transient gotcha] |

- **HARD RULE:** **no SFX cue peaks above the VO.** Verify with an RMS-diff at the cue window: a
  story-critical cue should add **≥ +4 dB** over voice-only; texture **+1–3 dB**. A cue that measures
  **+0 dB under continuous speech** is masked — accept felt-not-heard or cut it, do **not** chase it
  with gain (a pause would make it spike). [repo audibility check]
- **No static/glitch textures under narration.** **WHEN** an error/delete/glitch moment happens *while
  the VO is talking* → **DO** use silence or ONE clean mechanical snap, never sustained static/glitch.
  Save glitch textures for gaps where the voice is silent. **WHY:** glitch reads as NOISE over the
  voice at any gain. [repo brand.md §10]

### 4.5 Silence as a tool (when NO music/SFX)

- **WHEN** a line is the emotional/serious core, a personal admission, or the single most important
  claim → **DO** run it **dry** (bed faded out 0.4–0.8 s before, no SFX). **WHY:** removing sound
  focuses the ear on the words; contrast is the point. [repo — silence is part of the mix] + [craft]
- **WHEN** just *before* a payoff reveal → **DO** leave a **0.3–0.6 s beat of near-silence** (bed ducked
  low, no SFX) immediately before the riser/impact. **WHY:** the drop hits harder after a hush; the
  script's "wait a few minutes … *boom*" pattern needs the pause. [repo brand.md §10] + [craft]
- **NEVER wall-to-wall.** **WHEN** the plan has SFX or a bed audible in **> 90%** of the runtime → **DO**
  flag it and open gaps. **WHY:** constant sound = no dynamics; the calm register depends on space.
  [repo] (checked §6.)

---

## 5. Mix + delivery spec (for YouTube)

### 5.1 Loudness / peak targets

- **Integrated loudness target: −14 LUFS** (mix to this; **acceptable band −13 to −15 LUFS**). **WHY:**
  YouTube normalizes playback toward ~**−14 LUFS** — mixing hotter than that gets turned *down* on
  playback (losing your headroom advantage and often adding perceived limiting artifacts); mixing much
  quieter gets left alone but sounds weak next to normalized neighbors. [industry-standard — YouTube
  loudness normalization; grounded in the LUFS/ITU-R BS.1770 metering standard,
  https://en.wikipedia.org/wiki/LUFS ; platform target widely published, e.g. loudness-standards
  references at https://www.izotope.com/en/learn/loudness-standards-for-different-streaming-platforms.html ]
  - Contrast, for provenance: **broadcast EBU R128 targets −23 LUFS** with **true peak ≤ −1 dBTP** — the
    same measurement standard, a *quieter* target than streaming. [cited: https://en.wikipedia.org/wiki/EBU_R_128]
- **True peak: ≤ −1.0 dBTP** (use **−1.5 dBTP** for extra safety before lossy encode). **WHY:** inter-
  sample peaks and AAC encoding can push a 0 dBFS master into clipping on playback; −1 dBTP is the R128
  ceiling and the safe streaming ceiling. [cited: https://en.wikipedia.org/wiki/EBU_R_128 (−1 dBTP)]
- **Loudness range (LRA):** keep it modest for a talking-head narration mix — **~5–8 LU**. **WHY:** VoD
  spoken-word wants consistency; a huge dynamic range means the viewer rides the volume knob. [cited:
  LRA concept, https://en.wikipedia.org/wiki/EBU_R_128] + [craft target]

### 5.2 Relative level anchors (the mix hierarchy)

- **Dialog (VO) is the anchor** and sits at the top of the mix: **−16 to −12 LUFS short-term** while
  speaking, so the *integrated* lands near −14 with music/SFX filling the rest. **WHY:** in narration
  video the voice is the show; everything else is referenced to it. [repo — "the voice is the show"] +
  [craft]
- **Music bed:** **−12 dB relative to dialog** under speech (per §4.2), rising in gaps. [craft]
- **SFX:** peaks **never above the VO**; per the §4.4 table, hero cues loudest at ~−5/−6 dB rel. to
  clip-norm, i.e. clearly under the voice. [repo]
- **HARD RULE:** at no instant does music or an SFX cue exceed the VO's short-term level while the VO is
  speaking. **WHY:** a single moment of music-over-voice breaks the "premium/calm" contract instantly.
  [repo] (checked §6.)

### 5.3 Master chain order (with conservative parameter ranges)

Process in this order — cleanup before shaping before control. [cited: standard signal-chain / mixing
practice, https://en.wikipedia.org/wiki/Dynamic_range_compression]

1. **De-noise / de-hum (VO only).** Broadband noise reduction **6–12 dB** of attenuation on the noise
   floor; hum notch at 50/60 Hz if present. **WHY:** remove floor before you compress (a compressor
   raises the floor). Do this gently — over-reduction gives a "underwater" artifact. [craft; repo has a
   `clean-audio` step]
2. **High-pass / EQ (VO).** HPF at **80–100 Hz** (roll off rumble/plosive energy); gentle presence lift
   **+2–4 dB around 3–5 kHz** for intelligibility; tame harsh sibilance ~**6–8 kHz** with a de-esser
   (**2–4 dB** reduction) if needed. **WHY:** clarity and consistency before dynamics. [craft; EQ before
   compression is standard]
3. **Compress (VO).** Ratio **2:1–4:1**, threshold for **3–6 dB** gain reduction on peaks, attack
   **10–30 ms**, release **80–200 ms**, soft knee, make-up gain to restore level. **WHY:** even out the
   VO so it reads consistently under normalization; conservative ratio keeps it natural. [cited:
   attack/release/ratio/knee/make-up — compression basics]
4. **Bus / music sidechain duck.** Apply §4.2 ducking on the music bus keyed by the (compressed) VO.
   **WHY:** duck against the *finished* voice level. [cited: ducking]
5. **Limit (master).** Brick-wall limiter, ceiling **−1.0 dBTP** (**−1.5** for safety), attack short
   (**1–5 ms**), release **50–100 ms**, driven to land integrated at **−14 LUFS**. Target only **1–3 dB**
   of gain reduction on peaks — the limiter catches transients, it does not do the loudness work.
   **WHY:** a limiter is a high-ratio, short-attack compressor for the ceiling; pushing it hard pumps
   and squashes. [cited: "a limiter is a compressor with a high ratio and short attack time",
   https://en.wikipedia.org/wiki/Dynamic_range_compression]

- **Conservatism rule:** if the master needs **> 4–5 dB** of limiter gain reduction to reach −14 LUFS,
  the mix underneath is too quiet — raise the pre-limiter level, don't crush. **WHY:** loudness comes
  from the balance, not the limiter. [craft]

---

## 6. The reviewable-plan heuristics (planner self-QA)

Run **all** checks before showing the owner. Each = `id · condition (on the plan/schema) · fix`. A
failing HARD check blocks the plan; a SOFT check surfaces as a flag with a default. This is the same
audit-gate discipline the repo uses for `cuts.json` / `sfx-plan.json`. [repo]

1. **HOOK-VISUAL (hard).** A motion-bearing visual is on screen by **frame 45 (1.5 s)** and the hook
   promise VO ends by **8 s**. *Fix:* pull the first cutaway/preview earlier, or add a hook beat. (§1.2)
2. **NO-WALLPAPER (hard).** No cutaway/image `master_out_s − master_in_s > 6.0 s` without an internal
   narration-motivated change (new named noun, zoom move, scored beat, or sub-cut). *Fix:* split the
   scene, add a Ken Burns `zoom`, or cut to a different asset. (§3.1)
3. **NAME-MATCH (hard).** Every cutaway/image depicts a noun spoken within **±1.0 s** of its cut (asset
   `tags`∩ words in window ≠ ∅). *Fix:* re-time the cut, swap the asset, or convert the slot to a GAP
   scene. (§3.4)
4. **GAP-HAS-PROMPT (hard).** Every scene with `status:"planned"` / no `active` take has a generation
   spec (`provenance.spec.prompt` for fable/hyperframe, or comp+`props` for remotion). *Fix:* write the
   prompt from the beat's noun + brand look, or delete the empty slot. (§0, workbench schema)
5. **CUT-OFF-WORD (hard).** No scene `master_in_s`/`master_out_s` and no `xfade` overlap falls **inside**
   an emphasized word (within 2 f of a payoff-noun/number/product word's start or end). *Fix:* snap the
   boundary to the nearest pause. (§2.1, §2.4)
6. **BED-NOT-MID-WORD (hard).** The music bed entrance and every bed section-change land in a pause
   **≥ 0.3 s** or a hard scene boundary — never under a word. *Fix:* snap the bed event to the nearest
   qualifying gap. (§4.1)
7. **VOICE-ON-TOP (hard).** At no instant does music or an SFX cue exceed the VO short-term level while
   the VO speaks; music-under-VO is **10–18 dB** below the VO; no SFX `gain_db` implies a peak over the
   voice. *Fix:* lower the offender / deepen the duck. (§4.2, §4.4, §5.2)
8. **NO-STATIC-FRAME (hard).** No single held frame (base, one clip, or one image) with no motion and no
   cut for **> 8 s**; every still image has `drift ≥ 0.01`. *Fix:* add punch-in/drift/zoom or a cut. (§1.3, §3.3)
9. **CADENCE (soft).** A visual change occurs on average every **4–7 s** in `explain`/`context` (6–10 s
   in `demo`), **and** interrupt spacing varies by ≥ ±40% (not a fixed grid). *Flag:* stretches that
   drift over the ceiling, or a suspiciously uniform grid. (§1.3, §7 A1)
10. **A-ROLL-RETURN (soft).** The base track returns at least every **12–18 s** and on every personal/
    opinion line; each return holds **≥ 2.0 s**. *Flag:* long faceless stretches. (§3.2)
11. **SFX-DENSITY & SILENCE (soft).** Core (non-optional) SFX density **≤ 12/min**; audible sound
    (bed+SFX) covers **< 90%** of runtime (silence exists); layering used on **≤ 3** beats. *Flag:*
    over-dense or wall-to-wall plans. (§4.3, §4.5, §7 A4)
12. **KEN-BURNS-VARIETY (soft).** Across all images, zoom **direction and rate are not identical**, and
    not every image has a large zoom (some drift-only); no `zoom.to > 1.4`. *Flag:* uniform Ken Burns.
    (§3.3, §7 A6)
13. **LOUDNESS-TARGET (hard, at render).** Rendered integrated loudness **−13 to −15 LUFS**, true peak
    **≤ −1.0 dBTP**, limiter GR **≤ 4–5 dB**. *Fix:* re-balance, re-limit. (§5)
14. **TRANSITION-GRAMMAR (soft).** `xfade` used only at section boundaries (in-scene cuts are `cut`);
    xfade `frames` **5–10**; a scored sting/whoosh only on section changes, not every cut. *Flag:*
    dissolves inside an idea, whoosh-on-every-cut. (§2.4, §4.1, §4.3)

> Target the "must-pass" set at **8–12**; checks 1–8 are the non-negotiable core, 9–14 tune polish.

---

## 7. Anti-slop list — the tells that make auto-edited video feel machine-made

Each = **tell · detection rule (on the plan) · fix**. These are the failure modes an automated editor
falls into; the planner must actively avoid them. (Ranked by how loudly each screams "a robot cut
this.")

- **A1 — Uniform shot lengths.** *Tell:* every scene is ~the same duration; the timeline looks like a
  ruler. *Detect:* coefficient of variation of scene durations **< 0.25**, or > 60% of cuts fall on a
  fixed interval (±0.3 s). *Fix:* let content drive length — short punchy cutaways next to longer holds;
  target CV **≥ 0.35**. **WHY:** real edits breathe with the content; uniformity is the loudest tell.
  [craft]
- **A2 — Wallpaper b-roll.** *Tell:* generic clips that don't match the words, held too long. *Detect:*
  NAME-MATCH (§6.3) fails, or NO-WALLPAPER (§6.2) fails. *Fix:* name-match every cutaway to a ±1 s noun;
  split/zoom anything > 6 s; convert unmatched slots to GAP scenes. **WHY:** b-roll must illustrate the
  narration, not fill the frame. [repo make-tsx P1; craft]
- **A3 — Over-ducked music pumping.** *Tell:* the bed audibly "breathes"/pumps with every syllable.
  *Detect:* sidechain release **< 200 ms** or ratio **> 8:1**, or measured bed level oscillating at the
  syllable rate. *Fix:* release toward **300–400 ms**, ratio toward **4:1**, add **80–150 ms** hold.
  **WHY:** pumping is the instant amateur-mix tell. [craft; §4.2]
- **A4 — SFX on every cut.** *Tell:* a whoosh/click on literally every edit; wall-to-wall sound.
  *Detect:* SFX cue count ≈ cut count, or audible sound covers **> 90%** of runtime, or > 12 core
  cues/min. *Fix:* score only section transitions, reveals, and interactions; density **8–12/min**; let
  silence exist. **WHY:** sound with no space has no dynamics; "felt-not-heard" dies. [repo; §4.3/§4.5]
- **A5 — Endless punch-ins / zoom on the talking head.** *Tell:* every talking-head segment slowly
  zooms; the same push repeats as a tic. *Detect:* > 60% of base-track segments have a scale ramp, or
  the same punch-in scale/direction repeats back-to-back. *Fix:* punch in only on genuine emphasis;
  alternate punch-in with cutaway; vary scale. **WHY:** motion loses meaning if it's constant. [craft;
  §1.4]
- **A6 — Ken Burns on every image at the same rate/direction.** *Tell:* every still zooms in at an
  identical speed. *Detect:* KEN-BURNS-VARIETY (§6.12) fails — identical `zoom` direction+rate across
  images. *Fix:* vary direction, rate, and focal `fx,fy`; leave some images drift-only; cap `to ≤ 1.4`.
  **WHY:** identical Ken Burns is a dead giveaway of a template applied blindly. [craft; §3.3]
- **A7 — Beat-grid cutting everything to the music.** *Tell:* every cut lands exactly on a music beat
  regardless of the narration. *Detect:* > 70% of cuts snap to the music grid AND ignore word
  boundaries / motivated moments. *Fix:* cut to the *narration and action* first; let music-synced cuts
  be an occasional flourish on montage beats, not the rule. **WHY:** narration video is driven by the
  words, not the BPM; grid-cutting fights the sentence. [craft; §1.3 anti-metronome, §2]
- **A8 — Frozen frames.** *Tell:* a still image or paused clip sits perfectly motionless. *Detect:*
  NO-STATIC-FRAME (§6.8) fails (drift missing or > 8 s stasis). *Fix:* `drift ≥ 0.01` on every still;
  never hold a dead frame. **WHY:** stillness on a video timeline reads as a bug/freeze. [repo; §3.3]
- **A9 — Cuts that clip words / mistimed cues.** *Tell:* words start clipped, SFX lands ~100 ms off.
  *Detect:* CUT-OFF-WORD (§6.5) fails; SFX `at_s` not aligned to a real word/frame time. *Fix:* snap all
  cuts and cues to `edited-transcript.json` word times; picture cut 2–4 f before the word, SFX on the
  visual beat. **WHY:** off-by-100 ms is the difference between "pro" and "auto." [repo suggest-sfx sync]
- **A10 — Trailer drama in a calm channel.** *Tell:* cymbal-urgency risers, trailer-slam impacts, "funny"
  click+whoosh gags, hyped everything. *Detect:* SFX palette includes urgency-riser/slam types, or
  layered hero cues on > 3 beats. *Fix:* take the pro's *structure*, not the drama — soft risers, soft
  impacts, sparse layering. **WHY:** the brand is Linear/Anthropic-calm, NOT MrBeast; wrong drama breaks
  the channel voice. [repo brand.md §10]

---

## Sources (verified reachable at time of writing, 2026-07)

**Film-editing theory & continuity (cited):**
- Walter Murch — biography, *In the Blink of an Eye* / "most respected film editor and sound designer":
  https://en.wikipedia.org/wiki/Walter_Murch — Murch's **Rule of Six** (the priority order an editor
  weighs a cut by: **Emotion 51% · Story 23% · Rhythm 10% · Eye-trace 7% · Two-dimensional plane of
  screen 5% · Three-dimensional space of continuity 4%**) is his central editing doctrine from that
  book; used here as the rationale that *emotion/story motivation outranks mechanical continuity* — cut
  because the beat needs it, not because a rule says so.
- Continuity editing (**30-degree rule**, 180-degree rule, match-on-action, eyeline match, establishing
  shot): https://en.wikipedia.org/wiki/Continuity_editing
- Match on action / cut-on-action: https://en.wikipedia.org/wiki/Match_on_action
- Split edit (**J-cut / L-cut** definitions): https://en.wikipedia.org/wiki/Split_edit
- Jump cut (why too-similar / grid cuts read as glitches): https://en.wikipedia.org/wiki/Jump_cut
- B-roll (supplemental footage cut to support the narration; documentary coverage convention; *Grammar
  of the Shot* ref): https://en.wikipedia.org/wiki/B-roll

**Sound design & mix engineering (cited):**
- Ducking (level of one signal reduced by presence of another; engages as the voice starts; combats
  masking): https://en.wikipedia.org/wiki/Ducking
- Dynamic range compression (threshold/ratio/attack/release/knee/make-up gain; **limiter = high-ratio,
  short-attack compressor**): https://en.wikipedia.org/wiki/Dynamic_range_compression
- EBU R128 (broadcast **−23 LUFS** target; **true peak ≤ −1 dBTP**; loudness range concept):
  https://en.wikipedia.org/wiki/EBU_R_128
- LUFS / LKFS (loudness measurement, ITU-R BS.1770; used for streaming normalization):
  https://en.wikipedia.org/wiki/LUFS

**Platform / retention (industry-standard; exact figures are field consensus, not this author's):**
- YouTube plays back normalized to **~−14 LUFS integrated** — streaming loudness-standard references:
  https://www.izotope.com/en/learn/loudness-standards-for-different-streaming-platforms.html ;
  https://www.masteringthemix.com/blogs/learn/how-loud-should-i-master-my-songs-for-youtube-spotify-and-apple-music
- YouTube retention practice (strong first ~15 s, frequent visual change / pattern interrupts) —
  creator-education analyses: https://backlinko.com/youtube-ranking-factors ; https://blog.frame.io/
  (The **exact** cadence numbers — 4–7 s visual change, > 8 s stasis is a defect — are this author's
  **[craft]** calibration to this channel's calm register, informed by that practice.)

**Repo grammar this rulebook extends ([repo]):**
- `.claude/skills/clean-cut/SKILL.md` (Cut policy: content-aggressive + pause-natural, ~0.45–0.5 s
  breathing; style knobs; snap-to-audio tails).
- `.claude/skills/suggest-sfx/SKILL.md` (function-first, library-first, density 8–12/min, layer 2–3
  heroes, audibility RMS-diff, transient gain gotcha, sync to visual beat + word).
- `.claude/skills/make-tsx/SKILL.md` + `.claude/skills/*screencast*` (sync-to-words, don't pre-empt,
  real UI/pages, `drift`/`zoom` Ken-Burns vocabulary, click 2 f before result).
- `brand.md` §6/§7/§10 (calm/premium motion, centered framing, SFX taste contract, levels: VO ~−17
  LUFS, clips ~−20 LUFS, gain table).
- `fable5-workbench-packet/schemas/scene.schema.json` (the v2 scene/plan fields these checks reference:
  `type`, `cue`, `status`, `transition_in`, `props`, `versions[].provenance.spec`).

> **The one-sentence brain:** cut for emotion and story first and continuity second (Murch); land every
> picture, reveal, and sound on a real word-time; keep it moving but never uniform; keep sound felt-not-
> heard and under the voice; deliver at −14 LUFS / −1 dBTP; and treat every "template applied to
> everything" as slop to be broken up.
