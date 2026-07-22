"""Build the editor's 720p proxy of the RAW footage (all clips concatenated),
plus a timeline waveform image and a manifest with clip offsets.

Usage: python tools/make_proxy.py video-1
Writes: <project>/work/editor/proxy.mp4, waveform.png, manifest.json
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from encoders import EncodePlan, plan_h264

# 720p H.264 editor proxy. Encoder + hwaccel come from encoders.py: nvenc where
# present (the old h264_nvenc path, unchanged), else videotoolbox / libx264.
# Force one with CYE_ENCODER=<name>.
PROXY_PLAN: EncodePlan = plan_h264("proxy")


def encode_part(src: Path, out: Path) -> None:
    # hwaccel_in is empty on non-NVIDIA, so decode falls back to software cleanly.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *PROXY_PLAN.hwaccel_in, "-i", str(src),
         "-map", "0:0", "-map", "0:1", "-vf", "scale=1280:-2,format=yuv420p",
         *PROXY_PLAN.video_args,
         "-c:a", "aac", "-b:a", "160k", str(out)],
        check=True,
    )


def duration_of(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def main() -> None:
    project = Path(__file__).resolve().parent.parent / sys.argv[1]
    cuts_path = project / "work" / "analysis" / "cuts.json"
    if not cuts_path.exists():
        raise SystemExit(
            f"Project is not ready for a cut proxy: {cuts_path} does not exist.\n"
            "You can use the Scenes workspace now with:  ./workbench video-1\n"
            "Build a proxy only after importing footage and creating cuts.json."
        )
    data = json.loads(cuts_path.read_text(encoding="utf-8"))
    out_dir = project / "work" / "editor"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"proxy encoder: {PROXY_PLAN.encoder}"
          + (" +hwaccel cuda" if PROXY_PLAN.hwaccel_in else ""))

    by_id = {c["id"]: c for c in data["clips"]}
    parts = [(cid, project / by_id[cid]["file"], out_dir / f"part-{cid}.mp4") for cid in data["clip_order"]]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda p: encode_part(p[1], p[2]), parts))
    print("parts encoded")

    list_file = out_dir / "list.txt"
    list_file.write_text("\n".join(f"file '{p[2].as_posix()}'" for p in parts), encoding="utf-8")
    proxy = out_dir / "proxy.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(list_file), "-c", "copy", str(proxy)], check=True)

    offset, manifest = 0.0, []
    for cid, _, part in parts:
        d = duration_of(part)
        manifest.append({"id": cid, "offset": round(offset, 3), "duration": round(d, 3)})
        offset += d
    (out_dir / "manifest.json").write_text(
        json.dumps({"parts": manifest, "total": round(offset, 3)}, indent=1), encoding="utf-8")

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(proxy), "-filter_complex",
         "aformat=channel_layouts=mono,showwavespic=s=8192x140:colors=#5b8dd6",
         "-frames:v", "1", str(out_dir / "waveform.png")],
        check=True,
    )
    print(f"proxy ready: {proxy} ({offset:.0f}s)")


if __name__ == "__main__":
    main()
