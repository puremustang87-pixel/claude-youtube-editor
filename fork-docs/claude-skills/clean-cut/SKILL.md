---
name: clean-cut
description: Step 1 of the AI Video Editor pipeline — turn raw talking-head footage into a clean master cut. Use when the user wants to "clean cut", "cut the raw footage", "remove filler / dead air / bad takes", "tighten the pacing", produce cuts.json, run the cut editor, or render a cleaned preview/master for a video-N project in this repo. Covers audio extraction, AssemblyAI transcription, authoring cuts.json (keeps/cuts/fluff categorized), the cut policy (content-aggressive, pause-natural ~0.5s), QA + review docs, the local cut-editor UI, tight/natural previews, the final 4K60 render, and producing edited-transcript.json as the handoff to /make-tsx. Not for building TSX overlays (that is /make-tsx) or the raw TSX authoring rules (that is vidtsx-2d-generator).
---

# clean-cut — the step-1 cut pipeline

Turn a project's raw clips (`videos/video-N/DJI_*.MP4`) into a **clean master** + **`edited-transcript.json`** (the word-level timing spine every later step anchors to). The single source of truth is **`videos/video-N/work/analysis/cuts.json`** — shared by Claude and the editor UI. Every tool lives in `tools/` and takes the project dir as its first arg.

**You (Claude) author the cuts by reading the transcript.** No separate LLM call. The tools handle audio, encoding, QA, and the editor; the judgment — what is a retake, a false start, filler, or fluff — is yours.

## Pipeline (run in order)

Let `P` = the project (e.g. `video-1`). Clip **id** = a short handle (`0233`); every artifact for a clip is named by that id (`0233.wav`, `0233.json`). The raw MP4 path is stored per-clip in cuts.json as `file`.

1. **Extract 16 kHz mono WAV per clip** → `P/work/audio/<id>.wav` (used for transcription + the RMS noise-floor / snap-to-audio tails). Not scripted — run ffmpeg per clip:
   `ffmpeg -i videos/video-1/DJI_...0233_D.MP4 -vn -ac 1 -ar 16000 videos/video-1/work/audio/0233.wav`
2. **Draft this video's keyterms → `P/work/keyterms.txt`** (do this before transcribing). Keyterms bias the recognizer toward this video's proper nouns / product / tech names so they aren't mangled (e.g. "Seedream" not "sea dream", "Cloudflare" not "cloud flare"). Accuracy here is load-bearing: the transcript text drives cut decisions AND `/make-tsx` greps it for phrases to time beats — a garbled term breaks both. From the video's topic/title, list the ~10–40 likely brand names, tools, tech, and jargon, one per line (blank lines and `#` comments ignored). **This is per-video — never hardcode terms in `transcribe.py`.** If you skip the file, transcription still runs (empty fallback), just with more errors on specialty words. The shape is one term per line:

   ```
   # tools + brands named in this video
   Claude Code
   Remotion
   AssemblyAI
   ElevenLabs
   Cloudflare
   ```
3. **Transcribe** (needs `ASSEMBLYAI_API_KEY` in `.env`; verbatim, keeps fillers; auto-loads `work/keyterms.txt`):
   `python tools/transcribe.py P` → `P/work/transcripts/<id>.json`. `--clips 0233` for one, `--force` to redo. It prints how many keyterms it loaded — a "none" line means you haven't drafted them.
4. **Readable take view** for analysis: `python tools/format_transcript.py P` → `P/work/analysis/takes-<id>.txt` (segments on >0.8s gaps, fillers tagged inline with timestamps).
5. **Author `cuts.json`** (see schema below) by reading `takes-*.txt`: mark every span as a keep or a categorized cut, add fluff suggestions and judgment-call flags.
6. **QA + review docs**:
   `python tools/analyze_cut.py P [--style tight]` → `qa-report.md` (internal dead-air, clipped-tail risks, tiny fragments, fluff, hard entries at cut joins, **ghost speech** = untranscribed energy riding inside a keep, low-confidence kept tokens). Ghost/hard-entry checks exist because a transcript diff CANNOT see a mistimed token (clipped word onset) or an untranscribed false start ("and it—") that survives the cut — only energy-vs-token cross-checks catch them (a careful listen caught both before these checks existed).
   `python tools/make_review.py P` → `review.md` (per-clip keep/cut table + estimated length per style).
7. **Editor proxy** (once): `python tools/make_proxy.py P` → `P/work/editor/{proxy.mp4, waveform.png, manifest.json}` (720p concat of raw clips + per-clip offsets).
8. **Previews** (render BOTH, user picks): `python tools/render_cuts.py P --style tight --mode preview` and `--style natural` → `P/output/preview-<style>.mp4` (720p H.264 — encoder auto-picked per machine by `tools/encoders.py`: NVENC → VideoToolbox → libx264; `CYE_ENCODER=<name>` forces one).
8.5. **Machine verification of the render (MANDATORY after every preview render, before
   showing the user).** Extract the preview's WAV → `transcribe.py P --clips preview
   --force` → `python tools/verify_cut.py P` → `verify-report.md`. A second ASR pass
   over the RENDER, diffed against the intended kept tokens: EXTRA words = untranscribed
   ghosts that rode along (false starts glued to word tails — invisible to the raw
   transcript, and energy heuristics can't tell them from word releases); MISSING words
   = clipped/dropped; plus interior-pause anomalies and low-confidence rendered tokens.
   Born in testing: a mistimed ASR token clipped a word onset ('slash dot
   env' → '...env') and a ghost 'and it—' survived to the render; a careful listen caught
   both, now these tools do. Treat every finding as "listen here": explain each one or
   fix it — don't declare the cut good while the report has unexplained lines.
9. **USER AUDIT** — this is a hard gate, same as the plan step. Open the editor: `python tools/editor/server.py P` → http://localhost:8765. User drags keep/cut edges, adds cuts (I/O + C), compares raw vs edited playback; Save rewrites cuts.json (backup to `work/analysis/backups/`, appended to `changes.log`); Render button re-runs a preview. Iterate until approved.
10. **Final master**: `python tools/render_cuts.py P --style <chosen> --mode final` → `P/output/master-<style>.mp4` (4K 10-bit HEVC at the footage's native fps — hevc_nvenc where present, else videotoolbox/libx265; falls back to 8-bit H.264 with a loud warning only when no HEVC encoder exists). Two MANDATORY post-render steps:
   - **A/V duration gate:** `ffprobe -show_entries stream=duration` on v:0 vs a:0 — they MUST be equal. verify_cut's A/V budget GROWS along the timeline (±2s by mid-video) and masks a real accumulating drift; the equal-duration check is the definitive one. (See the drift note under Notes.) This same gate now runs as an automatic preflight inside `tools/yt_upload.py` before any upload — warn-and-continue by default, `--strict` aborts — so a drifted deliverable can't reach YouTube silently.
   - **Playable/handoff transcode:** the 10-bit HEVC master won't play in most players or the IDE preview, and the HEVC final stamps frames ~0.1% fast on 59.94fps footage (render_cuts.py now also re-stamps the final at the probed source fps automatically — any fps, not just 59.94 — so this transcode is the playable handoff plus a belt-and-braces re-time). Produce an 8-bit H.264 that fixes both by re-timing to true CFR: `ffmpeg -r <src_fps> -i master-<style>.mp4 -c:v libx264 -crf 19 -pix_fmt yuv420p -c:a aac master-<style>-h264.mp4` — the source fps BEFORE `-i` re-stamps every frame (no frame loss) so v:0==a:0. This is the file the user reviews AND the comp-native source downstream steps use.
11. **Handoff spine — `edited-transcript.json`**: word times in the FINAL master timeline. Simplest robust path (what video-1 used): extract the master's WAV and `transcribe.py` it, then normalize to `{words:[{text,start,end}...]}` in ms. (A cuts.json time-remapper is the planned alternative.) This file is what `/make-tsx` reads to sync visuals to speech.

Do steps 1–4 and 7 once; loop 5→6→8→9 until the cut is approved; then 10–11.

## cuts.json schema (what you author)

```jsonc
{
  "project": "video-1",
  "clip_order": ["0232", "0233", "0234", "0235"],   // concat order (assume filename order)
  "clips": [{
    "id": "0233",
    "file": "DJI_20260707121304_0233_D.MP4",         // raw MP4, relative to the project dir
    "duration": 245.3,
    "keeps": [ { "s": 7.32, "e": 13.13, "text": "...", "gap": {"d":0.89,"t":"silence"} } ],
    "cuts":  [ { "s": 2.18, "e": 5.04, "cat": "retake", "text": "...", "note": "why" } ],
    "fluff_suggestions": [ { "s": 40.1, "e": 44.0, "text": "...", "crit": "restated-idea",
                            "note": "...", "status": "suggested" } ]   // or "auto_applied"
  }],
  "styles": { "tight": {…}, "natural": {…} },         // timing knobs, below
  "flags":  [ { "id": 1, "clip": "0233", "at": "00:30", "issue": "...", "default": "keep both" } ]
}
```

- `cat` ∈ `retake | false_start | filler | long_pause | dead_air`. Times are raw seconds within that clip.
- **Keeps are never deleted.** A fluff span with `status:"auto_applied"` just *hides* the keeps it covers (undo = flip back to `"suggested"`). Reserve `auto_applied` for high-confidence fluff; leave the rest `"suggested"` (suggest-only — the renderer never drops suggested fluff).
- `flags` = judgment calls surfaced to the user with a `default`.

## Cut policy (how to decide)

Calibrated against a hand-made reference cut (the creator's own CapCut edit). **The target pacing = content-aggressive + pause-natural.** The big retention lever is cutting fluff and redundancy, NOT crushing silence — keep ~0.45–0.5s of natural breathing between runs. So:

- **Always cut:** retakes/false starts (keep the winning take, cut the rest — note which supersedes which), stumbles, dead air, and clear fillers.
- **Suggest, don't auto-remove (usually):** fluff — preamble that delays the payoff, evaluative asides, restated ideas. Categorize each (`crit`) and let the user decide in review.
- **Pauses:** compress but don't flatten. The two styles both target natural breathing; `tight` lands mid-flow pauses punchy, `natural` gives more room. Section ends / spots after a removed retake get a **soft landing** (more tail) so they breathe.
- Default to rendering **both** `tight` and `natural` and letting the user pick.

This is the ground truth for the channel's pacing — content-aggressive on fluff, natural on pauses.

### Style params (in `styles`, general knobs — no per-video constants)
`internal_gap` (split keeps into speech-run atoms at pauses ≥ this) · `min_tail`/`max_tail` (snap-to-audio tail range after a word) · `head` (lead-in before an atom) · `margin` (dB over noise floor that counts as "decayed") · `soft_gap` (a following gap ≥ this = section end → soft landing) · `soft_max_tail`/`soft_margin` (the softer landing).
Reference values that matched the reference cut: tight `{internal_gap:0.4, min_tail:0.14, max_tail:0.4, head:0.11, soft_gap:1.2, soft_max_tail:0.6, soft_margin:3.0}`; natural bumps `min_tail:0.26, max_tail:0.45, head:0.19`.

## Notes

- The cut engine (`tools/cutlib.py`) does word-aware segmentation, per-clip 10th-percentile noise floor, and snap-to-audio tails so word releases aren't clipped — you don't hand-tune tail padding, you tune the style knobs.
- `render_cuts.py` re-encodes (stream copy is only keyframe-accurate); previews are 720p, final is 4K60 10-bit. It cuts segments VIDEO-ONLY and builds the audio separately as one sample-exact stream matched to each segment's actual frame count (lesson learned: per-segment AAC + `concat -c copy` accumulates ~15-20ms of lip-sync drift PER CUT ≈ 1s over 34 segments). `verify_cut.py --style <s>` checks A/V drift automatically — run it on every render.
- **A/V drift, part 2 — the audio fix was not enough.** Even with sample-exact audio, the VIDEO side drifts on 59.94fps footage: an mp4 segment's container tacks ~one extra frame (~16ms) of padding onto its last sample when `-t` cuts mid-frame, and `concat -c copy` of mp4s ACCUMULATES it (~0.8s over ~98 cuts) while the audio has none. FIXED in render_cuts.py: the video segments are concatenated through an **MPEG-TS intermediate** (no per-file trailing gap → frame-exact, non-accumulating). A **residual** remains — the HEVC final stamps PTS ~0.1% fast (~0.3s short; `r_frame_rate` stays right but the PTS span runs short), an hevc/TS quirk a stream copy can't fix — corrected at delivery by the re-timing H.264 transcode in step 10. **The trap:** verify_cut's growing A/V budget hides both; ALWAYS gate on `v:0 duration == a:0 duration`.
- Footage handed to Remotion (`media/projects/footage/ch-N.mp4`) must be transcoded comp-native (1080×1920@30, 8-bit H.264, `-c:a copy`): 10-bit 4K60 HEVC starves OffthreadVideo's decoder during render → duplicated frames → visible stutter + perceived desync.
- **Always verify before handing off:** skim `review.md` / `qa-report.md`, and watch (or at least scrub) a preview — don't declare a cut good from the numbers alone.

Handoff: an approved master + `edited-transcript.json` → **`/make-tsx`** (build the visual beats) and the rest of steps 2–5.
