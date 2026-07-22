# Video Workbench

The workbench is a localhost-only, zero-dependency Python server with two browser workspaces:

- **Cut** edits `work/analysis/cuts.json` against the raw-footage proxy.
- **Scenes** edits `work/timeline.json`, previews Remotion frames, renders compositions, and runs the
  existing `tools/bake.py` compositor.

## Start it

From the repository root in WSL:

```bash
./workbench video-1
```

It opens <http://localhost:8765/?workspace=scenes> automatically: one terminal, one browser tab. Press
`Ctrl+C` to stop. The project argument also accepts `videos/video-1` or an absolute path.

The Scenes workspace works before footage exists. Once the project has `work/analysis/cuts.json`, build
the Cut proxy with `python.exe tools/make_proxy.py videos/video-1`; the next launch enables both tabs.

## Scene workflow

1. Open **Scenes** and search or filter the composition library.
2. Select a composition and choose **Add at playhead** (double-clicking a card also adds it).
3. Drag the scene block or its edges. Cutaways replace the master image; overlays require alpha.
4. Add the narration cue and any change notes. These stay in `timeline.json` for the next revision.
5. Use **Render current frame** to inspect the frame at the master playhead.
6. Use **Render selected** to create `remotion/out/<id>.mp4` or `.mov`.
7. Save, then choose **Bake preview** to composite every enabled scene over the master.

The first Remotion preview may download its headless Chrome renderer. Subsequent frames reuse it.

## Scene and take contract (v2.1)

Remotion remains the native engine. Every timeline placement now has its own stable `scene_uid`, while
`composition_id` identifies the reusable Remotion source. A rendered or imported artifact is an immutable
take. Legacy `id` and `asset` fields are derived on save so existing `tools/bake.py` invocations continue to
work unchanged.

```json
{
  "scene_uid": "scn_01K0EXAMPLE00000000000000",
  "engine": "hyperframe",
  "type": "cutaway",
  "master_in_s": 42.1,
  "master_out_s": 48.6,
  "takes": [
    {
      "take_uid": "take_01K0EXAMPLE0000000000000",
      "file": "videos/video-1/work/generated/scn_01K0EXAMPLE00000000000000/take_01K0EXAMPLE0000000000000/asset.mp4",
      "sha256": "...",
      "conform_profile": "cutaway_h264",
      "created_at": "2026-07-22T12:00:00Z",
      "provenance": {"provider": "hyperframe"}
    }
  ],
  "active_take_uid": "take_01K0EXAMPLE0000000000000",
  "enabled": true,
  "status": "approved",
  "cue": "Show the result when the narrator says 'finished'.",
  "notes": "Keep the camera move subtle."
}
```

The initial v2.1 slice migrates old timelines lazily and persists the upgraded identities on the first
successful save. Fable and Hyperframe generation adapters will write takes through the same contract;
they do not get separate timeline formats.

## Safety and validation

- The browser and API use a per-process session token and reject non-local Host/Origin headers.
- Every timeline mutation requires `If-Match`; a stale editor receives `E_ETAG_MISMATCH` and the current
  server document instead of overwriting it.
- Saves and durable job records use fsync + atomic replace. Every successful timeline save creates
  `work/backups/timeline-<timestamp>.json` after the first version.
- `GET /api/project/validate` is the shared structured validator. The Bake button and `bake.py --check`
  consume the same stable issue codes.
- Same-lane overlaps must be claimed by an exact `xfade`; cutaway and overlay lanes may overlap.
- Render and bake jobs survive as `work/jobs/job_<ulid>.json`. On restart, dead workers become
  `orphaned`; unverifiable live PIDs become `unknown` rather than silently appearing to run.

The accepted Fable5 contract packet is stored under `docs/contracts/v2.1/fable5-contracts-v2/`.

Run the focused tests with:

```bash
python -m unittest tools.editor.test_server -v
python tools/bake.py videos/video-1/work/timeline.json --check
```
