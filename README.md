# claude-youtube-editor

**Record the talking head. [Claude Code](https://claude.com/claude-code) does the rest.**

An open-source pipeline that takes a raw recording all the way to a published YouTube video — the
cut, the visuals, the voice, the sound effects, the thumbnail, and the upload. No video editor. No
screen recording. Every screen moment you see is built as code ([Remotion](https://remotion.dev) TSX)
and composited over your cut.

> 📺 **Watch it edit a real video, end to end:** [<!-- VIDEO_URL -->](#)
> That video was made with this repo. The animations, the screencasts, the sound effects, and the
> thumbnail you clicked — all of it.

## The six steps

| # | Step | Skill | What happens |
|---|---|---|---|
| 1 | **The cut** | `/clean-cut` | AssemblyAI transcribes with tone + talking style, you author `cuts.json` from the transcript, out comes a clean master + word-level `edited-transcript.json` |
| 2 | **The visuals** | `/make-tsx` · `/fake-screencast` | Remotion shots built over the master, every reveal synced to the word times. Generated UI clones that simulate a screen recording — nothing captured |
| 3 | **The voice** | `/clean-audio` | diagnose the noise, isolate the voice, keep the levels |
| 4 | **The sound** | `/suggest-sfx` | function-first SFX landing on the exact word, from a library that grows with every video |
| 5 | **The packaging** | `/packaging` | one title × 3 thumbnail bets for YouTube's A/B test, rendered as real images |
| 6 | **The upload** | `tools/yt_upload.py` | checks itself for ghost speech + A/V drift, then uploads as a private draft |

Plus **`/brand-setup`** — makes all of it look like *your* channel, not the one it came from.
And **`/vidtsx-2d-generator`** — the low-level rules that keep a Remotion shot from crashing.

## What you record vs what it builds

**You record:** yourself, talking. That's the whole shot list.

**It builds:** every full-screen statement, diagram, UI walkthrough, browser and terminal mockup,
title card, and transition — as code, over your cut, synced to what you actually said.

## Quickstart

**Requirements:** [Claude Code](https://claude.com/claude-code) · Python 3.10+ · Node 18+ ·
`ffmpeg` + `ffprobe` on PATH.

```bash
git clone https://github.com/hassancs91/claude-youtube-editor
cd claude-youtube-editor

python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt   # Windows (./venv/bin/pip on macOS/Linux)

cd remotion && npm install && npm run gen && cd ..       # build the Remotion registry

cp .env.example .env                                     # add your keys

claude                                                   # open in Claude Code, then:
```

> **set up my brand** · **clean cut videos/video-1** · **add TSX beats to video-1** ·
> **suggest sfx for video-1** · **package this video**

## See it before you shoot anything

The repo ships **37 real shots** from a published video — the worked example of the kit. They render
standalone, no footage needed:

```bash
cd remotion && npm run studio
```

That's the fastest way to see what this produces, and reading a few is the fastest way to learn
`remotion/src/lib/` (the browser frame, the screencast engine, the VS Code shell, the motion kit).

## Use the video workbench

The local workbench now puts the human cut and visual scene plan in one browser UI:

```bash
./workbench video-1
```

That is the whole startup flow: one terminal and one browser tab. The launcher works from WSL,
opens the Scenes workspace automatically, and stays in the foreground until you press `Ctrl+C`.
It uses the Windows runtime from WSL when the repository has Windows-native Remotion dependencies.

For a checkout stored on the Windows drive and opened from WSL:

```bash
cd /mnt/c/path/to/claude-youtube-editor
./workbench video-1
```

You do not need a proxy to plan or preview scenes. Build one only after the project has real footage
and `cuts.json`: `python.exe tools/make_proxy.py videos/video-1`.

Use **Cut** for transcript decisions and **Scenes** to search the Remotion catalog, add a composition
at the playhead, drag or retime cutaway/overlay blocks, write cue and revision notes, render a frame,
render the selected composition, and bake the full preview. Scene saves now use the accepted v2.1
scene/take contract: stable placement IDs, immutable takes, atomic backups, stale-save ETags, and durable
job records. Legacy `id`/`asset` fields are derived so `tools/bake.py` remains compatible.

Fable, Hyperframe, Remotion, and imported media all converge on the same scene/take model instead of
maintaining separate timelines. See [`tools/editor/README.md`](tools/editor/README.md) and the accepted
contracts under [`docs/contracts/v2.1/`](docs/contracts/v2.1/) for the exact rules.

## Make it yours

The repo ships with a house brand — a calm, premium indigo look. It's real and you can keep it, but
**if you change nothing, your videos will look like someone else's channel.**

```
/brand-setup
```

It interviews you, rewrites `brand.md` + `remotion/src/brand.ts` + `remotion/src/fonts.ts` together,
checks your fonts exist and your palette is actually readable, and renders a proof card so you see
your brand before you build a video in it.

## What it costs (the honest part)

**This project is free and open source. The models are not.** You'll need:

| Service | For | Notes |
|---|---|---|
| **Claude Code** | driving the whole thing | paid plan or API credits |
| **AssemblyAI** | transcription (step 1) | free tier is enough to try it |
| **ElevenLabs** | voice isolation + SFX + music (steps 3–4) | paid. `/clean-audio` also has a **local RNNoise** method that needs no key |
| **Gemini** | thumbnails (step 5) | paid, and only if you render thumbnails |
| **YouTube** | upload (step 6) | free, OAuth — see `tools/yt_upload_SETUP.md` |

Nothing here is a subscription to me. These are your own keys, in your own `.env`.

## Roadmap

Open source, and I'm keeping it updated:

- [ ] **Transitions** — a proper transition library between beats
- [ ] **Effects** — zoom/punch-in, highlight, spotlight passes
- [ ] **Filters** — grade/look presets that respect the brand contract
- [x] **Integrated scene timeline** — select, retime, preview, render, and bake Remotion/asset scenes
- [ ] **Schema-driven scene controls** — edit typed Remotion props without touching TSX
- [ ] **Direct Hyperframe adapter** — submit/generate/poll inside the workbench instead of asset handoff
- [ ] A cuts.json time-remapper (so `edited-transcript.json` doesn't need a second transcription pass)

⭐ **Star the repo** if you want to see where it goes — that's genuinely how I decide what to build next.
Issues and PRs welcome. Tell me which step you want to go deep on.

## What's NOT in here

Being straight with you about the scope:

- **No script writer.** This edits video; it doesn't write it.
- **No footage.** The example's shots ship, its raw recording doesn't (`videos/` starts empty).
- **No face kit.** Thumbnails need reference photos of *your* face in `media/library/faces/` — you
  supply those. See that folder's README.
- **No CTR data.** `/packaging`'s rules ship, calibrated on a real channel's numbers; the numbers
  themselves don't. Once you have ~10 videos, `references/channel-calibration.md` walks you through
  building your own — and then your data beats the defaults.

## Repo layout

```
.claude/skills/   the 8 skills (this is where the editor actually lives)
tools/            Python: transcribe, cutlib, render_cuts, verify_cut, clean_voice, bake,
                  gen_sfx/mix_sfx, gen_music/mix_music, gen_thumbnail, yt_stats, yt_upload,
                  capture_web + the cut-editor UI (editor/) + rnnoise models
remotion/         the Remotion project — shared kits in src/lib/, src/shots/<project>/ one folder
                  per video; registry generated by `npm run gen`
media/library/    reusable assets: SFX + music clips (catalogued, loudness-normalized), logos, faces
media/projects/   media for one specific video — via staticFile()
videos/           your videos. Empty until you make one.
brand.md          the style contract — make it yours
```

## Who made this

I'm Hasan. I build things with AI and show how on
[YouTube](https://youtube.com/@learnwithhasan) — this repo is how those videos get made.

If you want to learn to build things like this yourself, that's what my course
[Build With AI](https://learnwithhasan.com/courses/build-with-ai-10/) is for. It's not required for
any of this — the repo is complete on its own, and always will be.

## License

MIT — see [LICENSE](LICENSE). Bundled SFX/music clips were generated by the repo author (ElevenLabs)
and are redistributed here; per-clip provenance is in `media/library/*/catalog.json`. Product logos
(Claude, VS Code) are the property of their respective owners.
