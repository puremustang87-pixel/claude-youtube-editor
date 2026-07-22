# OSS Integration Menu — Script-Driven Video Pipeline

**For:** the AI video workbench (an MIT-licensed, Remotion-based, script→VO→b-roll→auto-edit tool).
**Goal:** a license-checked menu of open-source tools we can **VENDOR/FORK**, **CALL-AS-CLI**, or **STEAL IDEAS** from.
**Method:** every repo line below was fetched live from the GitHub REST API on **2026-07-22** (stars / SPDX license / primary language / last push). Ambiguous licenses (GitHub `NOASSERTION`) were resolved by reading the raw `LICENSE` file — those resolutions are called out inline because two of them are landmines.

### How to read the verdict column
Our tool is **MIT**. So:
- **VENDOR / FORK-OK** — MIT / Apache-2.0 / BSD / ISC / Unlicense / MPL(file-level). We can copy code into our tree, fork, and ship it.
- **CALL-AS-CLI-ONLY** — GPL / AGPL. We may `subprocess` the installed binary as a separate process (arm's-length), but we must **not** vendor or statically link its code into our MIT tree, and we should not ship its binary bundled. User installs it themselves.
- **IDEAS-ONLY** — license is incompatible even for CLI bundling, or it's a non-commercial/custom license, or it's just a pattern worth stealing. Read it, reimplement the idea from scratch, cite nothing verbatim.

### Our pipeline stages (the plug-in points referenced throughout)
```
INGEST   drop folder: script.md + voiceover.wav/mp4 + b-roll/*.mp4 + music/sfx
ALIGN    script text ↔ VO audio → word/line timestamps (forced alignment)
PLAN     timestamps + b-roll → an edit plan / timeline.json (what shows when)
EXECUTE  render the cut (Remotion compositing + ffmpeg concat/trim)
SOUND    voice cleanup → ducking → SFX cues → music bed → loudness normalize
EXPORT   master render + interchange (OTIO/EDL) + upload
```
Today the repo already has: AssemblyAI (ASR word times) → energy-based cut planner (`tools/cutlib.py`) → Remotion render (`tools/render_cuts.py`) → ElevenLabs/RNNoise voice clean (`tools/clean_voice.py`) → ffmpeg SFX/music mix with `sidechaincompress` (`tools/mix_sfx.py`, `tools/mix_music.py`). The gaps this menu fills: **forced alignment of a KNOWN script**, **loudness normalization** (currently a TODO in `/assemble`), **cut-on-beat**, **b-roll auto-trimming/scoring**, and **pro-NLE interchange export**.

---

## Lane 1 — Silence / auto-cutting (VO cleanup)

### auto-editor (the reference)
- **Repo:** https://github.com/WyattBlue/auto-editor · ★4,587 · **Unlicense (public domain)** · **Nim** (not Python anymore) · pushed 2026-07-22 (daily-active).
- **What:** analyzes audio loudness (and optional motion) and cuts silence/dead-air automatically; exports to a cut video or to premiere/resolve/fcp XML + EDL.
- **Verdict:** **VENDOR/FORK-OK** (Unlicense = do anything). But note it's **Nim now** — you can't `import` it into Python; you'd fork the CLI or call the compiled binary. Its ideas (the loudness-threshold cutter + its EDL/XML exporters) are the reusable part.
- **Plugs into:** PLAN (as an alternative/cross-check cutter) and EXPORT (its XML/EDL emitters are a reference for our own interchange).
- **Effort:** **M** (CLI subprocess + parse its output) / **L** if forking the Nim.
- **Recommendation:** Keep it as the **reference cutter and a CLI escape hatch**, but we already have a *better* cutter for VO: ours is word-aware (snaps tails to the ASR token release, `cutlib.py`), auto-editor is energy-only. Don't replace ours; borrow its exporters.

### Better-for-VO alternatives (checked)
- **carykh/jumpcutter** — https://github.com/carykh/jumpcutter · ★3,149 · **MIT** · Python · pushed 2024-02 (stale). The original "jumpcut" script. **IDEAS-ONLY** — our planner already supersedes it.
- **bambax/Remsi** — https://github.com/bambax/Remsi · ★179 · **MIT** · Python · 2022 (dormant). Silence→ffmpeg one-liner. **IDEAS-ONLY.**
- **Verdict for the lane:** nothing beats our word-aware planner for *VO* cleanup. auto-editor stays as reference + CLI cross-check. **Do not adopt an energy-only cutter as the primary** — you already threw away that class of tool when you built `cutlib.py`.

---

## Lane 2 — Word-level / forced alignment (align a KNOWN script to a VO)

**The framing that matters:** you have the *script text* AND the *VO audio*. Aligning known text to audio is **forced alignment**, NOT transcription. AssemblyAI (which you already pay for) does *transcription* — it guesses the words, then you'd fuzzy-match script↔ASR-transcript. Forced alignment skips the guessing: it constrains the model to YOUR words and returns exact timestamps. It is **more accurate on names/jargon/numbers, deterministic, and free/local** — precisely where fuzzy-matching an ASR transcript drifts.

**When local forced alignment beats fuzzy-matching the AssemblyAI transcript:**
- The script has domain jargon, product names, code, or numbers ASR mis-hears (fuzzy match then mis-anchors).
- You need frame-accurate line-start times to trigger b-roll/overlays on a specific word.
- You want it offline / zero marginal cost / reproducible (no per-run API spend or nondeterminism).
- **Keep AssemblyAI when:** you *don't* have a clean script (improvised VO), or you need diarization/confidence for the QA gate (`analyze_cut.py` already uses ASR confidence). Best design: **AssemblyAI for the QA/confidence pass, forced alignment for the script-anchored timing.** They're complementary, not either/or.

### torchaudio forced_align  ← the pragmatic pick
- **Repo:** https://github.com/pytorch/audio · ★2,915 · **BSD-2-Clause** · Python · pushed 2026-07-21 (active, official PyTorch).
- **What:** `torchaudio.functional.forced_align` — a first-class CTC forced-alignment API, plus the **`MMS_FA`** multilingual alignment bundle (works across ~1000 languages). This is the same class of wav2vec2/CTC alignment whisperX wraps, but as a clean library call.
- **Verdict:** **VENDOR/FORK-OK.** BSD-2, `pip install torchaudio`, no Kaldi, no conda, no C compilation — **installs cleanly on Windows + Mac**, which is the whole ballgame for a cross-platform tool. Minimal deps (torch).
- **Plugs into:** ALIGN (the core new module: `script.md` + `vo.wav` → `alignment.json` with per-word start/end). Feeds PLAN.
- **Effort:** **M** (write the tokenizer→align→emit-JSON wrapper; ~150 lines around the documented API).
- **Recommendation:** **This is the one to build the ALIGN stage on.** Cleanest license, cross-platform, official, minimal deps.

### whisperX
- **Repo:** https://github.com/m-bain/whisperX · ★23,173 · **BSD-2-Clause** · Python · pushed 2026-07-13 (active).
- **What:** Whisper ASR + **wav2vec2 forced-phoneme alignment** for accurate word timestamps + VAD batching + diarization.
- **Verdict:** **VENDOR/FORK-OK** (BSD-2). Heavier deps (faster-whisper, pyannote, VAD). It transcribes *then* aligns; you can feed it your own text but it's built around ASR-first.
- **Plugs into:** ALIGN (heavier alternative) — best if you also want diarization or you *don't* always have a script.
- **Effort:** **M–L** (dependency weight; CUDA nice-to-have).
- **Recommendation:** **Fallback / power option.** If you want ONE library for both "have a script" and "no script," whisperX covers both. For the pure script-align case, torchaudio is lighter.

### stable-ts
- **Repo:** https://github.com/jianfch/stable-ts · ★2,276 · **MIT** · Python · **ARCHIVED 2026-05** (read-only now).
- **What:** Whisper wrapper with stabilized word timestamps + a real `align()` for known text.
- **Verdict:** **VENDOR/FORK-OK** (MIT) — but **archived**, so you own any maintenance. Great, ergonomic `align()` API.
- **Plugs into:** ALIGN. **Effort:** **S–M.**
- **Recommendation:** **IDEAS-ONLY given the archive** — its `align()` ergonomics are worth copying, but don't build a load-bearing stage on an abandoned repo when torchaudio gives you the same primitive maintained.

### aeneas
- **Repo:** https://github.com/readbeyond/aeneas · ★2,852 · **AGPL-3.0** · Python · pushed 2024-06 (dormant).
- **What:** text-fragment↔audio sync map (forced alignment) via eSpeak + DTW/MFCC. Great for *sentence/line-level* sync (it's the karaoke/audiobook workhorse), lighter than a neural aligner.
- **Verdict:** **CALL-AS-CLI-ONLY** (AGPL — never vendor; AGPL's network clause is toxic to a product). Also needs eSpeak + C extension compilation = **painful on Windows/Mac**.
- **Plugs into:** ALIGN (line-level). **Effort:** **L** (install friction) even as CLI.
- **Recommendation:** **Skip.** AGPL + install pain + you can get line-level for free by grouping torchaudio's word times.

### Montreal Forced Aligner (MFA)
- **Repo:** https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner · ★1,851 · **MIT** · Python · pushed 2026-07-11 (active).
- **What:** Kaldi-based, phoneme-accurate forced alignment. The gold standard for *linguistic research* precision.
- **Verdict:** MIT (**VENDOR-OK by license**) BUT ships on **Kaldi via conda** — it is a heavyweight conda-only install, hostile to a pip/Windows-friendly consumer tool.
- **Plugs into:** ALIGN (overkill). **Effort:** **L** (conda + Kaldi).
- **Recommendation:** **IDEAS-ONLY / skip for this product.** Precision you don't need at an install cost your users won't pay.

### gentle
- **Repo:** https://github.com/strob/gentle · ★1,703 · **MIT** · Python · pushed 2026-05 (the maintained fork; `lowerquality/gentle` redirects here).
- **What:** Kaldi-based forced aligner with a friendly JSON output + a web UI. The classic "align a transcript" tool videogrep-style projects use.
- **Verdict:** MIT (**VENDOR-OK by license**) but again **Kaldi build** = hard on Windows/Mac (usually run via Docker).
- **Plugs into:** ALIGN. **Effort:** **L** (Kaldi/Docker).
- **Recommendation:** **IDEAS-ONLY.** Its JSON alignment format is a good schema reference; don't take the Kaldi dependency.

**LANE VERDICT:** Build ALIGN on **torchaudio.forced_align** (BSD-2, pip, cross-platform). Keep **whisperX** as the no-script fallback. Everything Kaldi-based (MFA, gentle) and AGPL (aeneas) is IDEAS-ONLY for a consumer cross-platform tool. Use AssemblyAI's confidence/diarization for QA, not for timing.

---

## Lane 3 — Audio post (ducking, loudness, beats, denoise)

### ffmpeg `sidechaincompress` for VO ducking — RECIPES (you already have one!)
- **Not a repo — an ffmpeg filter.** ffmpeg is LGPL/GPL as a *binary you already subprocess*; filter recipes are not copyrightable know-how. **VENDOR-OK to use.**
- **Your current recipe** (`tools/mix_music.py`): music bed keyed by voice — `sidechaincompress=threshold=0.03:ratio=3:attack=15:release=450:makeup=1`, and SFX bus in `mix_sfx.py` — `threshold=0.15:ratio=2:attack=5:release=200:makeup=2`. These are already sound.
- **Reference recipes to steal (well-known, battle-tested):**
  - **Classic VO duck (music under speech):** split the voice, key the music: `[music][voice_key]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=300` → ratio 8 gives a firmer broadcast-style duck than your current 3 when you want music to *disappear* under speech.
  - **Two-stage:** duck with sidechaincompress, then a gentle `alimiter=limit=0.97` on the sum (you already do this — good).
  - **`acompressor` sidechain alt:** some builds prefer `acompressor` with an external sidechain input for tighter control of `knee`/`detection=rms`.
- **Plugs into:** SOUND (ducking). **Effort:** **S** (tuning knobs you already expose).
- **Recommendation:** Add a **`--duck-style broadcast|gentle`** preset pair to the mixers (ratio 8/attack 20 vs your current gentle 3). No new dependency.

### ffmpeg-normalize (loudness normalization) — fills a real gap
- **Repo:** https://github.com/slhck/ffmpeg-normalize · ★1,517 · **MIT** (GitHub shows `NOASSERTION`; the raw `LICENSE.md` is verbatim MIT — confirmed) · Python wrapper over ffmpeg · pushed 2026-07-10 (active).
- **What:** EBU R128 two-pass **`loudnorm`** normalization to a target LUFS/TP (e.g. **-14 LUFS / -1 dBTP** for YouTube), batch-friendly, with peak/RMS modes.
- **Verdict:** **VENDOR/FORK-OK** (MIT). Pure Python + ffmpeg, cross-platform, minimal deps.
- **Plugs into:** SOUND — the **final loudness stage** your `/assemble` currently marks TODO ("final loudness normalization is /assemble's job later" — see `mix_sfx.py`/`mix_music.py` docstrings). This is the tool that closes that TODO.
- **Effort:** **S** (import it, or call `loudnorm` two-pass directly — it's ~15 lines of ffmpeg either way).
- **Recommendation:** **Adopt for the -14 LUFS master normalize.** Either vendor the thin wrapper or copy its two-pass loudnorm logic (measure pass → apply pass with measured values). This is a top-5.

### Beat / onset detection for cut-on-beat — LICENSE MATTERS A LOT HERE
- **librosa** — https://github.com/librosa/librosa · ★8,513 · **ISC** (permissive, MIT-compatible) · Python · pushed 2026-07-21 (active). `librosa.beat.beat_track` (tempo + beat frames) and `librosa.onset.onset_detect`. **Verdict: VENDOR/FORK-OK.** The **only** vendor-safe beat/onset lib in this lane. Plugs into PLAN (snap b-roll cuts / SFX hits to beats) and SOUND. **Effort: S.** **Recommendation: use librosa for cut-on-beat.**
- **madmom** — https://github.com/CPJKU/madmom · ★1,678 · code is **BSD-style** (GitHub `NOASSERTION`; raw LICENSE = BSD for source) — **BUT its pretrained models are `CC BY-NC-SA 4.0`, explicitly NON-COMMERCIAL** (the LICENSE says: contact the author to use models "in a commercial product"; "pickled Processors fall into this category"). madmom's *accuracy* (its DBN downbeat tracker beats librosa) lives entirely in those models. **Verdict: IDEAS-ONLY / DO-NOT-VENDOR for a monetized product.** Using its beat models in videos you monetize on YouTube violates the NC clause. This is exactly the trap flagged. **Do not ship it.**
- **aubio** — https://github.com/aubio/aubio · ★3,726 · **GPL-3.0** · C (+ python wheels, also GPL) · pushed 2026-04. Fast onset/tempo/pitch. **Verdict: CALL-AS-CLI-ONLY** (GPL — don't `import` the GPL wheel into MIT code; shell out to the `aubioonset`/`aubiotrack` binaries if at all). **Recommendation: skip** — librosa gives you the same in-process for free.
- **essentia** (MTG) — https://github.com/MTG/essentia · ★3,646 · **AGPL-3.0** · C++. Excellent beat tracking. **Verdict: IDEAS-ONLY** (AGPL — worst-case for a product). **Skip.**
- **LANE-WITHIN-LANE VERDICT:** **librosa (ISC) for cut-on-beat.** madmom/aubio/essentia are more accurate but each has a disqualifying license for a monetized MIT tool.

### Voice denoise — DeepFilterNet vs RNNoise (you know DeepFilterNet from noeltock)
- **DeepFilterNet** — https://github.com/Rikorose/DeepFilterNet · ★4,488 · **Apache-2.0 OR MIT (dual)** (GitHub `NOASSERTION`; raw LICENSE = "licensed under either Apache-2.0 or MIT at your option" — confirmed) · Python/Rust · pushed 2024-10 (quiet but stable). State-of-the-art real-time deep noise suppression; **models are Apache/MIT too** (unlike madmom — safe to ship). **Verdict: VENDOR/FORK-OK.** **Plugs into:** SOUND — a **free/offline voice-cleanup method** alongside your ElevenLabs + RNNoise options in `clean_voice.py`. **Effort: M** (add a `--method deepfilternet` branch: `pip install deepfilternet`, run `enhance`, gain-match back like you already do). **Recommendation: add as the offline high-quality denoise** — it materially beats RNNoise on broadband/steady noise, and unlike ElevenLabs it's free and doesn't need network. Top-5 candidate.
- **RNNoise** — https://github.com/xiph/rnnoise · ★5,733 · **BSD-3-Clause** · C · pushed 2025-02. You already use it via ffmpeg `arnndn` + `tools/models/rnnoise/`. **Verdict: VENDOR/FORK-OK** (already effectively vendored via the ffmpeg filter + model files). Fast, lightweight, but weaker than DeepFilterNet on hard noise (your `clean_voice.py` docstring already says RNNoise only *partially* removes water noise). **Recommendation: keep as the ultra-light default; DeepFilterNet as the quality tier.**
- **GregorR/rnnoise-models** — https://github.com/GregorR/rnnoise-models · ★377 · **NO LICENSE FILE** · pushed 2018 (abandoned). The `.rnnn` model zoo (the `sh`/`bd` models your tool references). **Verdict: IDEAS-ONLY / license-risk** — **no license = all rights reserved by default.** If your `tools/models/rnnoise/*.rnnn` came from here, that's a licensing gap to resolve (see risk list). **Recommendation: verify model provenance; prefer models with explicit licenses or train your own.**
- **werman/noise-suppression-for-voice** — https://github.com/werman/noise-suppression-for-voice · ★6,713 · **GPL-3.0** · C++ · pushed 2026-05. RNNoise as a VST/LADSPA plugin (for live calls). **Verdict: CALL-AS-CLI/plugin-ONLY** (GPL). Not relevant to file-based post — **skip.**

### Stem separation (bonus for SOUND) — clean, MIT
- **demucs** — https://github.com/adefossez/demucs · ★2,930 (maintained; `facebookresearch/demucs` ★10,339 is **ARCHIVED**) · **MIT** · Python · pushed 2026-07-11. Separate vocals/music/drums. **Verdict: VENDOR/FORK-OK.** Useful if you ever get a VO with baked-in music you need to strip, or to extract a clean drum stem to beat-track. **Effort: M.** **Recommendation: nice-to-have, not core.**
- **spleeter** (Deezer) — https://github.com/deezer/spleeter · ★28,329 · **MIT** · pushed 2026-06. Faster/lighter stem split. **VENDOR/FORK-OK.** Same use case, lighter. **Recommendation: prefer spleeter if you add stem-split at all.**

---

## Lane 4 — Timeline / interchange / declarative compilers

### OpenTimelineIO (OTIO) — the pro-NLE escape hatch
- **Repo:** https://github.com/AcademySoftwareFoundation/OpenTimelineIO · ★1,926 · **Apache-2.0** · C++/Python · pushed 2026-07-14 (active, ASWF-governed — industry standard).
- **What:** the open interchange format + Python API for editorial timelines, with adapters to Premiere (FCP7 XML), Final Cut, Resolve (via FCPXML/EDL), AAF, CMX3600 EDL, etc.
- **Verdict:** **VENDOR/FORK-OK** (Apache-2.0). `pip install opentimelineio`, cross-platform.
- **Plugs into:** EXPORT — write a **`timeline.json → OTIO`** adapter so users can bounce your cut into Premiere/Resolve/FCP for finishing. This is *the* "pro escape hatch."
- **Effort:** **M.** Your `timeline.json` already has clip ids, source offsets, and in/out times (see `cutlib.py` segments + `manifest.json` offsets) — mapping those to OTIO `Clip`/`Gap`/`Track` with `available_range`/`source_range` is a direct, well-documented transform. The OTIO adapters then give you EDL/XML *for free*.
- **Recommendation:** **Build the OTIO export adapter — top-5.** Highest leverage-per-line in the whole menu: one adapter → every pro NLE, and it future-proofs you against "I need to hand this to an editor."

### LosslessCut (ideas: keyframe-accurate trim UX)
- **Repo:** https://github.com/mifi/lossless-cut · ★42,266 · **GPL-2.0** · TypeScript/Electron · pushed 2026-07-20 (very active).
- **What:** lossless keyframe-accurate cutting/merging UI over ffmpeg; superb segment-list + keyframe-snap UX.
- **Verdict:** **IDEAS-ONLY** (GPL-2.0 — can't vendor into MIT; it's an end-user app, not a lib anyway). **Steal the UX patterns** for your cut-editor (`tools/editor/`): keyframe snapping, segment list, `,`/`.` frame-stepping (you already have fps-aware stepping — LosslessCut's keyframe-snap-on-export is the next idea).
- **Plugs into:** EXECUTE/editor UX (patterns only). **Effort:** **M** (reimplement patterns). **Recommendation: mine it for the trim UI, vendor nothing.**

### Editly (declarative video-from-JSON — pattern for your compiler)
- **Repo:** https://github.com/mifi/editly · ★5,453 · **MIT** · TypeScript · pushed 2025-05 (maintenance mode).
- **What:** declarative CLI/JSON → video (clips, transitions, title layers, audio) via ffmpeg+canvas. The spec-driven-editing model you're already living.
- **Verdict:** **VENDOR/FORK-OK** (MIT). But you render via **Remotion**, which is more capable than editly's canvas model, so wholesale adoption is backward.
- **Plugs into:** PLAN (schema ideas for your `timeline.json`). **Effort:** **S** (read the JSON schema). **Recommendation: IDEAS for the declarative schema** (its `clips[]`/`layers[]`/`transitions` shape is a good sanity-check for your compiler's input format). Don't vendor.

### Remotion ecosystem (@remotion/media-utils, @remotion/transitions)
- **Repo:** https://github.com/remotion-dev/remotion · ★53,863 · **CUSTOM Remotion License** (source-available, NOT OSS; GitHub `NOASSERTION`) · TypeScript · pushed 2026-07-21 (very active).
- **License reality (read the raw LICENSE):** **free for individuals + non-profits + for-profit orgs with ≤3 employees**; **larger for-profit orgs need a paid company license.** You already depend on Remotion core, so you've already accepted this tier. The ecosystem packages (`@remotion/media-utils`, `@remotion/transitions`, `@remotion/paths`, etc.) ship under the **same license** — no *new* legal exposure beyond what core already imposes.
- **Verdict:** **VENDOR/FORK-OK *within the Remotion license tier you already accept*** (i.e., NOT MIT — this is a standing constraint, flagged in the risk list). Concretely usable:
  - **`@remotion/media-utils`** — `getAudioData`, `visualizeAudio`, `getWaveformPortion` → drive audiogram/waveform overlays and **audio-reactive** visuals from the VO. Plugs into EXECUTE.
  - **`@remotion/transitions`** — prebuilt slide/wipe/fade transitions between b-roll shots. Plugs into EXECUTE/PLAN.
- **Effort:** **S** (they're npm packages you already have access to). **Recommendation: import `@remotion/media-utils` + `@remotion/transitions` now** — zero net-new license cost, immediate b-roll transition + audiogram capability. Top-5.

---

## Lane 5 — B-roll / stock + SFX / music ASSET SOURCES (APIs + license reality for MONETIZED YouTube)

> The HARD-RULE URL tracing above is for GitHub repos. This lane is about **API + license reality**; each verdict is stated for the case that matters: **videos monetized on YouTube.** Canonical license pages cited.

| Source | API | License reality (monetized YouTube) | Attribution | Verdict |
|---|---|---|---|---|
| **Pexels** (video+photo) | Yes — free REST API, key required. `api.pexels.com/videos` | **Pexels License**: free for commercial use, no attribution *required*. Safe to monetize. Cannot resell unaltered / can't imply endorsement. | Appreciated, not required | **USE — best default for free b-roll.** Clean API, clean license. |
| **Pixabay** (video+photo+music) | Yes — free REST API, key required. `pixabay.com/api/videos/` | **Content License** (post-2019): free commercial use, no attribution required. **Caveat:** identifiable people/brands/logos may need releases; no "sole distribution." Music also under this license. | Not required | **USE — strong second source** (adds a music option too). |
| **Mixkit** | **No official public API** (browse/download only; some scrape) | **Mixkit License**: free for commercial use for most items, no attribution; some items restricted. | Not required (most) | **USE via manual/scrape ingest** — good quality, but no API = weaker for automation. Ideas: mirror chosen clips into the drop folder. |
| **Freesound** (SFX/field recordings) | **Yes** — APIv2, token auth (simple) + **OAuth2 required to download full-quality files**. `freesound.org/apiv2` | **Per-sound CC**: mix of **CC0** (free, safe), **CC-BY** (safe *if you attribute*), and some **CC-BY-NC** (NOT for monetized video). You MUST filter by license per query and honor attribution. | **Required for CC-BY** (must credit author + link) | **USE with a license filter** — only ingest CC0/CC-BY, auto-generate an attribution block, hard-exclude NC. See risk list. |
| **YouTube Audio Library** | **No public API** (UI-only in Studio) | Free for use in YouTube videos incl. monetized; some tracks require attribution (shown in UI). | Some tracks | **IDEAS-ONLY for automation** — no API means no clean programmatic ingest. Usable manually. |
| **Openverse** (Creative Commons aggregator) | **Yes** — `api.openverse.org/v1/` (audio+images; no key needed for basic use) | Aggregates CC/PD across sources; **same CC caveats** — filter to CC0/CC-BY/PDM, exclude NC/ND, verify at source. | Per-item (CC-BY) | **USE with license filter** — great for CC images/audio; treat as a CC search layer, verify each hit's license field. |
| **BBC Sound Effects** (bbcrewind) | Search UI + downloads; **no documented public API** | **RemArc / BBC Sound Effects license: personal, educational, and "internal/research" use ONLY. Commercial use requires a SEPARATE licence.** | N/A | **DO NOT USE for monetized YouTube** — the flagged trap. 33k+ great sounds, but the license bars monetized commercial use without a paid licence. **IDEAS/EXCLUDE.** |

- **freesound-python** — https://github.com/MTG/freesound-python · ★154 · **MIT** · Python · pushed 2025-12. Official-ish Freesound APIv2 client. **Verdict: VENDOR/FORK-OK.** **Plugs into:** INGEST (SFX fetch). **Effort: S.** **Recommendation: vendor it as the Freesound client, wrap with a CC-license filter.**
- **LANE VERDICT:** default b-roll = **Pexels + Pixabay** (both: free API, commercial-safe, no attribution). SFX/music = **Freesound (CC0/CC-BY only, with an attribution generator) + Pixabay music + Openverse**, all behind a **license-filter gate**. **Hard-exclude BBC Sound Effects and any CC-NC/CC-ND** from monetized projects. Build the attribution block as a first-class pipeline artifact.

---

## Lane 6 — Text-based / script-driven editing OSS (steal the patterns)

### videogrep — the canonical transcript→supercut tool
- **Repo:** https://github.com/antiboredom/videogrep · ★3,462 · **Anti-Capitalist License** (GitHub `NOASSERTION`; raw LICENSE = "anti-capitalist software… released for free use by individuals and organizations that do not operate by capitalist principles," restricted to non-profit / worker-owned orgs) · Python · pushed 2024-04.
- **What:** searches transcripts (srt/vtt / its own alignment) and cuts a supercut of matching phrases — the OG "edit by editing text." Directly relevant to your script-driven model.
- **Verdict:** **IDEAS-ONLY — and a genuine license landmine.** The Anti-Capitalist License **forbids use by for-profit companies that aren't worker-owned.** It is NOT MIT-compatible and NOT usable by a commercial workbench. **Read its approach, vendor nothing, cite nothing.** (See risk list — this is the sharpest trap in the menu.)
- **Plugs into:** PLAN (pattern only — "search transcript → assemble matching ranges → concat"). **Effort:** **M** (clean-room reimplement). **Recommendation: study, reimplement from scratch.**

### Subtitle Edit
- **Repo:** https://github.com/SubtitleEdit/subtitleedit · ★13,549 · **MIT** · C# · pushed 2026-07-21 (very active).
- **What:** the reference subtitle editor — sync, waveform, format conversion (SRT/VTT/ASS/EDL-adjacent), forced-align hooks. Enormous body of **subtitle timing + format-conversion** logic.
- **Verdict:** **VENDOR/FORK-OK** (MIT) — but it's C#/WinForms, so cross-language vendoring is impractical; take it as **format-handling reference** (its SRT/VTT parsers and time-code math are authoritative).
- **Plugs into:** ALIGN/EXPORT (subtitle emit + timecode formats). **Effort:** **M.** **Recommendation: IDEAS for subtitle/timecode formats**; if you add caption export, mirror its format handling.

### autocut
- **Repo:** https://github.com/mli/autocut · ★7,766 · **Apache-2.0** · Python · pushed 2024-10.
- **What:** transcribe → edit the *transcript* (delete lines in an md/srt) → it recuts the video to match. Exactly the "edit text = edit video" UX, and Apache (clean).
- **Verdict:** **VENDOR/FORK-OK** (Apache-2.0). Whisper-based; the *transcript-diff→cut* mechanism is the reusable gem.
- **Plugs into:** PLAN (its edited-transcript→cut-list logic mirrors your `edited-transcript.json` → cuts). **Effort:** **M.** **Recommendation: IDEAS/partial-vendor** — its "diff the edited transcript against the original to derive keep-ranges" is precisely your script-driven planning loop; clean license makes it safe to borrow code, not just ideas.

**LANE VERDICT:** the *pattern* (edit transcript → derive keep-ranges → concat) is what you want, and you already have it (`edited-transcript.json`). Take **autocut** (Apache) as the reusable reference. **videogrep is IDEAS-ONLY and license-toxic — do not vendor.** Subtitle Edit = format reference.

---

## Lane 7 — Wildcards (belong here, weren't on the list)

### PySceneDetect — b-roll auto-trimming (strongly recommended)
- **Repo:** https://github.com/Breakthrough/PySceneDetect · ★5,035 · **BSD-3-Clause** · Python · pushed 2026-07-22 (daily-active).
- **What:** shot/scene-boundary detection (content-aware + threshold) over any video; splits into scenes, exports scene lists / cut timecodes, can auto-split via ffmpeg.
- **Verdict:** **VENDOR/FORK-OK** (BSD-3). `pip install scenedetect`, cross-platform, minimal deps (OpenCV).
- **Justification / plugs into:** INGEST + PLAN. Your b-roll arrives as raw clips in a folder; PySceneDetect **auto-trims each b-roll clip into usable shots** (one action per shot) so the planner can pick a clean 3-second beat instead of a 40-second raw file. This is the missing "make b-roll plannable" step. **Effort: S–M.** **Recommendation: adopt — top-5.** Turns a dumb drop-folder into a shot library.

### An image/frame quality scorer — pick the best b-roll frame/shot
- **Option A — idealo/image-quality-assessment (NIMA):** https://github.com/idealo/image-quality-assessment · ★2,243 · **Apache-2.0** · Python · **ARCHIVED 2024**. NIMA aesthetic+technical scoring. **Verdict: IDEAS-ONLY / partial-vendor** (Apache but archived → you maintain it). 
- **Option B (preferred) — cheap in-house scorer:** compute sharpness (variance of Laplacian, OpenCV), exposure, and motion-energy per candidate frame — no model, no license issue, ~30 lines. Combine with PySceneDetect scene mid-frames.
- **Justification / plugs into:** PLAN — **score b-roll shots** so the planner auto-picks the sharpest/best-exposed shot for a beat and the best thumbnail-candidate frame (feeds your existing `gen_thumbnail.py`). **Effort: S** (in-house) / **M** (NIMA). **Recommendation: build the in-house Laplacian/exposure scorer** — avoids the archived-model risk and any license question. Wildcard win.

### CLIP — semantic b-roll↔script matching (the "which clip fits this line" brain)
- **Repo:** https://github.com/openai/CLIP · ★34,043 · **MIT** · Python · pushed 2026-03.
- **What:** image↔text similarity. Embed each b-roll shot (a keyframe) and each script line, then **match the most semantically relevant b-roll to each line of narration**.
- **Verdict:** **VENDOR/FORK-OK** (MIT). (Or use `open_clip` / a sentence-image model — all MIT/Apache.)
- **Justification / plugs into:** PLAN — this is the intelligent core of *script-driven* b-roll: "the VO says 'a busy trading floor' → surface the b-roll shot whose CLIP embedding is nearest." Turns asset selection from manual to automatic. **Effort: M.** **Recommendation: adopt for auto-b-roll-matching** — it's the difference between "aligned timeline" and "auto-*edited*." Strong wildcard; near-top-5.

---

## TOP 5 to integrate first (ranked by leverage ÷ effort)

| # | Tool | License | Verdict | Plugs into | Effort | Why it's #N |
|---|---|---|---|---|---|---|
| **1** | **torchaudio `forced_align`** — https://github.com/pytorch/audio | **BSD-2** | VENDOR/FORK-OK | **ALIGN** (new core stage) | **M** | Unlocks the whole premise: script↔VO frame-accurate timing, local/free/deterministic, cross-platform pip install, minimal deps. Everything downstream (PLAN, cut-on-beat, b-roll timing) keys off it. Nothing else is a prerequisite for this many features. |
| **2** | **@remotion/media-utils + @remotion/transitions** — https://github.com/remotion-dev/remotion | **Remotion custom (tier you already accept)** | VENDOR/FORK-OK *within existing tier* | **EXECUTE / PLAN** | **S** | Zero net-new dependency or license cost (you already ship Remotion). Instant b-roll transitions + audiogram/waveform-reactive overlays. Highest capability-per-hour. |
| **3** | **ffmpeg-normalize (loudnorm two-pass)** — https://github.com/slhck/ffmpeg-normalize | **MIT** | VENDOR/FORK-OK | **SOUND** (final master) | **S** | Closes the loudness-normalization TODO your own `/assemble` docstrings admit is missing. -14 LUFS / -1 dBTP = the difference between "sounds amateur" and "sounds broadcast" on YouTube. Tiny effort. |
| **4** | **OpenTimelineIO export adapter** — https://github.com/AcademySoftwareFoundation/OpenTimelineIO | **Apache-2.0** | VENDOR/FORK-OK | **EXPORT** | **M** | One `timeline.json → OTIO` adapter → Premiere/Resolve/FCP/EDL for free. The pro escape hatch that makes the tool safe to adopt ("I can always hand it to an editor"). Your timeline already has the fields OTIO needs. |
| **5** | **PySceneDetect** — https://github.com/Breakthrough/PySceneDetect | **BSD-3** | VENDOR/FORK-OK | **INGEST / PLAN** | **S–M** | Makes the b-roll drop-folder actually plannable: auto-trims raw clips into clean shots so the planner picks 3-second beats, not 40-second files. Cheap, cross-platform, daily-maintained. (Runner-up just below: **CLIP** for semantic b-roll↔line matching — adopt next; it turns "aligned" into "auto-edited.") |

**Honorable mentions (adopt in the next wave):** **CLIP** (MIT, semantic b-roll matching — the auto-edit brain), **DeepFilterNet** (Apache/MIT, free offline denoise beating RNNoise), **librosa** (ISC, cut-on-beat), **autocut** (Apache, transcript-diff→cut reference), **freesound-python** (MIT, SFX ingest with CC filter).

---

## ⚠️ LICENSE-RISK WARNING LIST (read before writing any code)

1. **madmom models = CC BY-NC-SA 4.0 (NON-COMMERCIAL).** The code is BSD, but every pretrained beat/downbeat model — the entire reason to use madmom — is non-commercial and its LICENSE explicitly says commercial use (incl. "pickled Processors") needs the author's permission. **You monetize YouTube videos → do NOT ship madmom's models.** Use **librosa (ISC)** for cut-on-beat instead. *(IDEAS-ONLY.)*

2. **videogrep = Anti-Capitalist License.** Forbids use by for-profit companies that are not worker-owned. **NOT usable and NOT MIT-compatible.** Study the transcript→supercut *pattern*, reimplement clean-room, vendor/copy **nothing**. *(IDEAS-ONLY.)*

3. **aubio (GPL-3.0), essentia (AGPL-3.0), pedalboard (GPL-3.0), werman noise-suppression (GPL-3.0), aeneas (AGPL-3.0), LosslessCut (GPL-2.0).** All copyleft — **never vendor into the MIT tree.** GPL tools may only be **subprocessed as separately-installed binaries** (and even that is a distribution question if you bundle them); AGPL (essentia, aeneas) is worst-case — avoid entirely. Prefer the permissive alternative in every case (librosa, torchaudio, DeepFilterNet, ffmpeg-normalize).

4. **Remotion is NOT open source — it's a custom source-available license.** Free only for individuals / non-profits / for-profit orgs **≤3 employees**; **larger for-profit orgs must buy a company license.** This is a *standing* constraint (core already imposes it) and it extends to all `@remotion/*` packages. If the workbench's owning entity exceeds the free tier, a Remotion company license is mandatory — independent of anything in this menu.

5. **auto-editor is Nim + Unlicense.** License is maximally permissive (public domain), but it's **not Python** — you cannot `import` it. Fork the Nim or subprocess the binary; treat its value as ideas + CLI, not a library.

6. **RNNoise model provenance (GregorR/rnnoise-models) = NO LICENSE.** No license file = **all rights reserved by default** (not free to redistribute). If your bundled `tools/models/rnnoise/*.rnnn` originated here, that's an **unresolved redistribution risk in the current repo.** Confirm each `.rnnn`'s origin/license, replace with explicitly-licensed models, or train your own. *(Action item, not just future risk.)*

7. **BBC Sound Effects = personal/educational/internal use only.** Commercial (monetized) use requires a **separate paid BBC licence.** **Exclude from monetized projects.** Same discipline for any **CC-NC / CC-ND** asset from Freesound / Openverse — filter them out at ingest; only CC0 / CC-BY (with attribution) are safe to monetize.

8. **stable-ts, facebookresearch/demucs, idealo/image-quality-assessment are ARCHIVED.** Licenses are fine (MIT/MIT/Apache) but they're read-only upstream — if you vendor, **you own all future maintenance.** Prefer the maintained equivalent (torchaudio for alignment, adefossez/demucs or spleeter for stems, an in-house scorer for quality).

9. **Attribution is a pipeline artifact, not an afterthought.** CC-BY assets (Freesound, Openverse, some Pixabay-people/Mixkit items) legally require credit. Build an **attribution manifest** that travels with each project and auto-emits a credits block in the video description; a missing credit on a monetized video is the actual license breach.

---
*All GitHub stars / licenses / languages / last-push dates fetched live from the GitHub REST API on 2026-07-22. `NOASSERTION` licenses were resolved by reading the raw LICENSE file and the resolution is noted inline (madmom, ffmpeg-normalize, DeepFilterNet, remotion, videogrep). Asset-source license claims cite the sources' canonical license pages (Pexels License, Pixabay Content License, Freesound APIv2 CC-per-sound, BBC Sound Effects/RemArc, Openverse CC aggregation).*
