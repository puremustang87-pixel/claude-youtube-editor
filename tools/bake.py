#!/usr/bin/env python3
"""
bake.py — hybrid ffmpeg assembler for the AI Video Editor (step 2 / step 5 preview).

Reads a timeline.json that maps Remotion shots onto the master cut and
bakes a flat preview:
  - master AUDIO plays throughout (the human cut is the spine);
  - 'cutaway' spans REPLACE the master video with the shot's mp4;
  - 'overlay' spans COMPOSITE an alpha shot (.mov, ProRes 4444) over the master;
  - everywhere else the master video passes through.

Method: split [0, end] into atomic segments at every shot boundary, render each
segment to an identically-encoded clip, concat them, then mux master audio 0..end.
Frame-accurate: per-segment frame counts come from rounded cumulative boundaries so
the total matches the audio exactly (no cumulative drift).

Usage:
  python tools/bake.py [videos/video-1/work/timeline.json] [--end SECONDS] [--keep]
  python tools/bake.py [videos/video-1/work/timeline.json] --check
"""
import json
import os
import subprocess
import sys
import shutil
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_DIR = os.path.join(ROOT, "tools", "editor")
sys.path.insert(0, EDITOR_DIR)
from contracts import migrate_timeline, validate_timeline  # noqa: E402


def proj(p):
    """Resolve PROJECT-data paths (timeline, master, preview out) relative to the CURRENT
    WORKING DIR — i.e. the type workspace you run from (longs/), where video-N lives — NOT
    relative to ROOT (=core/, the shared engine). Engine/library paths still use ROOT."""
    return p if os.path.isabs(p) else os.path.abspath(p)


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        sys.stderr.write("\nFFMPEG FAILED:\n  " + " ".join(cmd) + "\n" + r.stdout[-4000:] + "\n")
        raise SystemExit(1)
    return r.stdout


def main():
    args = sys.argv[1:]
    keep = "--keep" in args
    args = [a for a in args if a != "--keep"]
    check_only = "--check" in args
    args = [a for a in args if a != "--check"]
    end_override = None
    if "--end" in args:
        i = args.index("--end")
        end_override = float(args[i + 1])
        del args[i:i + 2]
    tl_path = proj(args[0]) if args else proj(os.path.join("video-1", "work", "timeline.json"))

    with open(tl_path, "r", encoding="utf-8") as f:
        tl = json.load(f)

    project = Path(tl_path).resolve().parent.parent
    manifest_path = Path(ROOT) / "remotion" / "src" / "shots.manifest.json"
    try:
        catalog_items = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        catalog_items = []
    catalog = {str(item.get("id")): item for item in catalog_items if isinstance(item, dict) and item.get("id")}
    tl = migrate_timeline(tl, set(catalog), Path(ROOT), project)
    issues = validate_timeline(tl, catalog, Path(ROOT), project)
    if check_only:
        print(json.dumps({"issues": issues}, indent=2, ensure_ascii=False))
        raise SystemExit(1 if any(item.get("severity") == "E" for item in issues) else 0)
    blocking = [item for item in issues if item.get("severity") == "E"]
    if blocking:
        summary = "\n".join(f"  {item['code']}: {item['message']}" for item in blocking)
        raise SystemExit(f"bake blocked by validation:\n{summary}\nrun with --check for structured details")

    master = proj(tl["master"])                                     # project data -> CWD
    out_dir = os.path.join(ROOT, tl.get("remotion_out", "remotion/out"))  # engine -> ROOT
    pv = tl["preview"]
    END = end_override if end_override is not None else float(pv["end_s"])
    W, H, FPS = int(pv["width"]), int(pv["height"]), int(pv["fps"])
    out_path = proj(pv["out"])                                      # project data -> CWD
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Resolve each shot to its rendered file. Remotion shots use the historical
    # remotion/out/<id> convention. Other engines (Hyperframe exports, stock
    # media, real captures) may provide an explicit `asset` path. Disabled
    # scenes stay in the editable plan but are intentionally absent from a bake.
    shots = []
    for s in tl["shots"]:
        if not s.get("enabled", True):
            continue
        a = max(0.0, float(s["master_in_s"]))
        b = min(END, float(s["master_out_s"]))
        if b <= a:
            continue  # entirely past the preview window
        ext = ".mov" if s["type"] == "overlay" else ".mp4"
        f = proj(s["asset"]) if s.get("asset") else os.path.join(out_dir, s["id"] + ext)
        if not os.path.exists(f):
            hint = (f"export the {s.get('engine', 'external')} scene to its asset path"
                    if s.get("asset") else f"render it first: npm run render / render-all {s['id']}")
            raise SystemExit(f"missing rendered shot: {f} ({hint})")
        shots.append({"id": s["id"], "type": s["type"], "in": a, "out": b, "file": f})

    cutaways = [s for s in shots if s["type"] == "cutaway"]
    overlays = [s for s in shots if s["type"] == "overlay"]

    # atomic segment boundaries
    bounds = {0.0, END}
    for s in shots:
        bounds.add(s["in"])
        bounds.add(s["out"])
    bounds = sorted(b for b in bounds if 0.0 <= b <= END)

    scratch = os.path.join(os.path.dirname(out_path), "_bake_tmp")
    if os.path.exists(scratch):
        shutil.rmtree(scratch)
    os.makedirs(scratch)

    seg_files = []
    print(f"master={os.path.relpath(master, ROOT)}  end={END}s  {W}x{H}@{FPS}")
    print("segments:")
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < 1e-4:
            continue
        # exact frame count from rounded cumulative boundaries (no drift)
        n = round(b * FPS) - round(a * FPS)
        if n <= 0:
            continue
        dur = n / FPS
        seg = os.path.join(scratch, f"seg_{i:03d}.mp4")

        cut = next((c for c in cutaways if c["in"] <= a + 1e-6 and a < c["out"] - 1e-6), None)
        ov = next((o for o in overlays if o["in"] <= a + 1e-6 and b <= o["out"] + 1e-6), None)

        common_vf = f"scale={W}:{H}:force_original_aspect_ratio=disable,fps={FPS},tpad=stop_mode=clone:stop_duration=1,format=yuv420p"

        if cut:
            off = a - cut["in"]
            kind = f"cutaway:{cut['id']} @+{off:.2f}s"
            cmd = ["ffmpeg", "-y", "-ss", f"{off:.4f}", "-i", cut["file"],
                   "-vf", common_vf, "-frames:v", str(n), "-an",
                   "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", seg]
        elif ov:
            off = a - ov["in"]
            kind = f"master+overlay:{ov['id']} @+{off:.2f}s"
            fc = (f"[0:v]scale={W}:{H},fps={FPS},format=yuv420p[bg];"
                  f"[1:v]scale={W}:{H},fps={FPS}[ov];"
                  f"[bg][ov]overlay=0:0:format=auto,"
                  f"tpad=stop_mode=clone:stop_duration=1,format=yuv420p[v]")
            cmd = ["ffmpeg", "-y", "-ss", f"{a:.4f}", "-i", master,
                   "-ss", f"{off:.4f}", "-i", ov["file"],
                   "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-an",
                   "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", seg]
        else:
            kind = "master"
            cmd = ["ffmpeg", "-y", "-ss", f"{a:.4f}", "-i", master,
                   "-vf", common_vf, "-frames:v", str(n), "-an",
                   "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", seg]

        print(f"  [{a:6.2f}-{b:6.2f}] {n:4d}f  {kind}")
        run(cmd)
        seg_files.append(seg)

    # concat (re-encode) + mux master audio 0..END
    listf = os.path.join(scratch, "segs.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for s in seg_files:
            f.write(f"file '{s.replace(os.sep, '/')}'\n")

    print("concat + master audio -> " + os.path.relpath(out_path, ROOT))
    run(["ffmpeg", "-y",
         "-f", "concat", "-safe", "0", "-i", listf,
         "-i", master,
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k",
         "-t", f"{END:.4f}", "-movflags", "+faststart", out_path])

    if not keep:
        shutil.rmtree(scratch)
    dur = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", out_path]).strip()
    print(f"done -> {os.path.relpath(out_path, ROOT)}  ({dur}s)")


if __name__ == "__main__":
    main()
