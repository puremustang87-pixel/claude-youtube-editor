"""Create a voiceover-first workbench project without changing the bake contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


class BootstrapError(RuntimeError):
    """A user-actionable project bootstrap failure."""


def _run(command: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError(f"command timed out: {command[0]}") from exc
    except OSError as exc:
        raise BootstrapError(f"{command[0]} is required for project bootstrap") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-4000:]
        raise BootstrapError(f"{command[0]} failed:\n{detail}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("._-") or "asset"


def _repo_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def probe(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"ffprobe returned invalid data for {path.name}") from exc
    streams = document.get("streams") if isinstance(document.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    raw_duration = (document.get("format") or {}).get("duration")
    if raw_duration is None and isinstance(video, dict):
        raw_duration = video.get("duration")
    if raw_duration is None and isinstance(audio, dict):
        raw_duration = audio.get("duration")
    try:
        duration = max(0.0, float(raw_duration or 0))
    except (TypeError, ValueError):
        duration = 0.0
    value = {
        "duration_s": round(duration, 6),
        "has_video": isinstance(video, dict),
        "has_audio": isinstance(audio, dict),
    }
    if isinstance(video, dict):
        try:
            video_duration = float(video.get("duration") or duration)
        except (TypeError, ValueError):
            video_duration = duration
        value.update({
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0"),
            "video_codec": str(video.get("codec_name") or ""),
            "pix_fmt": str(video.get("pix_fmt") or ""),
            "video_duration_s": round(max(0.0, video_duration), 6),
        })
    if isinstance(audio, dict):
        try:
            audio_duration = float(audio.get("duration") or duration)
        except (TypeError, ValueError):
            audio_duration = duration
        value.update({
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
            "audio_codec": str(audio.get("codec_name") or ""),
            "audio_duration_s": round(max(0.0, audio_duration), 6),
        })
    return value


def _media_class(path: Path, media_probe: dict) -> str | None:
    extension = path.suffix.lower()
    if extension in VIDEO_EXTENSIONS and media_probe["has_video"]:
        return "video"
    if extension in IMAGE_EXTENSIONS and media_probe["has_video"]:
        return "image"
    if extension in AUDIO_EXTENSIONS and media_probe["has_audio"]:
        return "audio"
    return None


def _conform_asset(source: Path, destination: Path, media_class: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.stem}.{os.getpid()}{destination.suffix}")
    if media_class == "video":
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
            "-vf", (
                "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
            "-fps_mode", "cfr", str(partial),
        ]
    elif media_class == "image":
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-frames:v", "1",
            "-vf", (
                "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black"
            ),
            "-compression_level", "6", str(partial),
        ]
    else:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:a:0", "-vn",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(partial),
        ]
    try:
        _run(command)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def ingest_assets(project: Path, assets_dir: Path, root: Path = ROOT) -> dict:
    catalog_path = project / "work" / "assets.json"
    existing = {}
    if catalog_path.is_file():
        try:
            loaded = json.loads(catalog_path.read_text(encoding="utf-8"))
            existing = {
                str(item.get("sha256")): item
                for item in loaded.get("assets", [])
                if isinstance(item, dict) and item.get("sha256")
            }
        except (OSError, ValueError):
            raise BootstrapError("work/assets.json is not valid JSON")

    for source in sorted(path for path in assets_dir.rglob("*") if path.is_file()):
        if source.suffix.lower() not in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS:
            print(f"  assets: skipped unsupported file {source.name}")
            continue
        digest = _sha256(source)
        original_name = source.name
        if digest in existing:
            aliases = existing[digest].setdefault("aliases", [])
            if original_name != existing[digest].get("original_name") and original_name not in aliases:
                aliases.append(original_name)
            continue
        source_probe = probe(source)
        media_class = _media_class(source, source_probe)
        if media_class is None:
            print(f"  assets: skipped unprobeable media {source.name}")
            continue
        suffix = {"video": ".mp4", "image": ".png", "audio": ".wav"}[media_class]
        destination = (
            project / "work" / "assets" / media_class
            / f"{digest[:8]}-{_slug(source.stem)}{suffix}"
        )
        _conform_asset(source, destination, media_class)
        existing[digest] = {
            "sha256": digest,
            "class": media_class,
            "original_name": original_name,
            "aliases": [],
            "file": _repo_relative(destination, root),
            "source_probe": source_probe,
            "probe": probe(destination),
        }
        print(f"  assets: {original_name} -> {media_class}")

    assets = sorted(existing.values(), key=lambda item: (item["class"], item["original_name"].lower()))
    catalog = {
        "schema_version": 1,
        "project": project.name,
        "assets": assets,
    }
    _write_json(catalog_path, catalog)
    return catalog


def _make_master(vo_path: Path, destination: Path, duration: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.stem}.{os.getpid()}.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=#fffef7:s=1920x1080:r=30",
        "-i", str(vo_path),
        "-filter_complex",
        (
            "[0:v]drawbox=x=0:y=0:w=iw:h=18:color=#6366F1:t=fill,"
            "drawbox=x=0:y=18:w=iw/3:h=8:color=#9b7cc4:t=fill,"
            "drawbox=x=2*iw/3:y=18:w=iw/3:h=8:color=#4db8a8:t=fill,"
            "format=yuv420p[v]"
        ),
        "-map", "[v]", "-map", "1:a:0", "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(partial),
    ]
    try:
        _run(command)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def _transcribe(project: Path, vo_copy: Path, root: Path) -> None:
    if not os.environ.get("ASSEMBLYAI_API_KEY"):
        print("  transcript: skipped (ASSEMBLYAI_API_KEY is not set)")
        return
    relative_project = _repo_relative(project, root)
    command = [
        sys.executable, str(root / "tools" / "transcribe.py"), relative_project,
        "--clips", vo_copy.stem, "--outdir", "_bootstrap_transcript",
    ]
    print("  transcript: AssemblyAI key found; transcribing VO")
    result = _run(command, timeout=900)
    if result.stdout.strip():
        print(result.stdout.strip())
    generated = project / "work" / "_bootstrap_transcript" / f"{vo_copy.stem}.json"
    if not generated.is_file():
        raise BootstrapError("transcribe.py completed without producing a VO transcript")
    os.replace(generated, project / "work" / "edited-transcript.json")
    shutil.rmtree(generated.parent, ignore_errors=True)


def bootstrap_project(
    name: str,
    vo: Path,
    assets: Path | None = None,
    *,
    root: Path = ROOT,
    transcribe: bool = True,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise BootstrapError("project name may contain only letters, numbers, dot, dash, and underscore")
    vo = vo.expanduser().resolve()
    if not vo.is_file():
        raise BootstrapError(f"voiceover file was not found: {vo}")
    if vo.suffix.lower() != ".wav":
        raise BootstrapError("--vo must point to a .wav file")
    if assets is not None:
        assets = assets.expanduser().resolve()
        if not assets.is_dir():
            raise BootstrapError(f"assets folder was not found: {assets}")

    project = root / "videos" / name
    work = project / "work"
    vo_hash = _sha256(vo)
    metadata_path = work / "project.json"
    metadata = None
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BootstrapError("existing work/project.json is not valid JSON") from exc
        old_hash = ((metadata.get("master") or {}).get("source_sha256"))
        if old_hash and old_hash != vo_hash:
            raise BootstrapError(
                f"project '{name}' already uses a different voiceover; choose a new project name"
            )
    elif project.exists() and any(project.iterdir()):
        raise BootstrapError(
            f"project '{name}' already exists and was not created by --new-project"
        )

    for folder in (
        work / "audio", work / "assets", work / "preview", work / "analysis",
        project / "output", project / "packaging", project / "script",
    ):
        folder.mkdir(parents=True, exist_ok=True)

    vo_copy = work / "audio" / _slug(vo.name)
    if not vo_copy.is_file() or _sha256(vo_copy) != vo_hash:
        shutil.copy2(vo, vo_copy)
    vo_probe = probe(vo_copy)
    if not vo_probe["has_audio"] or vo_probe["duration_s"] <= 0:
        raise BootstrapError("voiceover WAV does not contain a usable audio stream")

    master = work / "master.mp4"
    if not master.is_file():
        _make_master(vo_copy, master, vo_probe["duration_s"])
    master_probe = probe(master)
    if not master_probe["has_video"] or not master_probe["has_audio"]:
        raise BootstrapError("VO-synth master is missing its video or audio stream")
    if abs(master_probe["duration_s"] - vo_probe["duration_s"]) > 0.05:
        raise BootstrapError("VO-synth master audio and video duration do not match")
    if abs(master_probe["video_duration_s"] - master_probe["audio_duration_s"]) > 0.05:
        raise BootstrapError("VO-synth master v:0 and a:0 duration do not match")

    if metadata is None:
        metadata = {
            "schema_version": 1,
            "mode": "vo_script",
            "master": {
                "kind": "vo_synth",
                "file": _repo_relative(master, root),
                "source_audio": _repo_relative(vo_copy, root),
                "source_sha256": vo_hash,
                "base_track": {"kind": "brand_slate"},
            },
        }
        _write_json(metadata_path, metadata)

    timeline_path = work / "timeline.json"
    if not timeline_path.exists():
        _write_json(timeline_path, {
            "master": _repo_relative(master, root),
            "master_fps": 30,
            "remotion_out": "remotion/out",
            "shots": [],
            "preview": {
                "end_s": round(vo_probe["duration_s"], 3),
                "out": _repo_relative(project / "output" / f"{name}-preview.mp4", root),
                "width": 1920,
                "height": 1080,
                "fps": 30,
            },
            "editor": {"version": 2.1},
        })

    if assets is not None:
        ingest_assets(project, assets, root)
    elif not (work / "assets.json").exists():
        _write_json(work / "assets.json", {
            "schema_version": 1, "project": name, "assets": [],
        })

    if transcribe and not (work / "edited-transcript.json").exists():
        _transcribe(project, vo_copy, root)

    print(f"Created VO project: {project}")
    print(f"  master: {master}")
    print(f"  timeline: {timeline_path}")
    return project
