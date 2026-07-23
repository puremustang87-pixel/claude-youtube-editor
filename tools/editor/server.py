"""Local video workbench server. Zero Python dependencies.

Usage: python tools/editor/server.py video-1 [port]
Then open http://localhost:8765

The project argument may be a name inside ``videos/`` (``video-1``), a path
relative to the repository (``videos/video-1``), or an absolute path.

Cut editor endpoints:
  GET  /api/data             cuts.json + proxy manifest
  POST /api/save             write cuts.json (previous version backed up)
  POST /api/render           render a tight/natural cut preview

Scene editor endpoints:
  GET  /api/scenes           Remotion catalog + bake-compatible timeline.json
  POST /api/scenes/save      validate and save timeline.json with a backup
  POST /api/scenes/still     render one Remotion frame for visual selection
  POST /api/scenes/render    render a selected Remotion composition
  POST /api/scenes/bake      bake the full visual timeline over the master
  POST /api/bake/range       bake a durable range-preview job
  POST /api/jobs/<id>/cancel cancel a verified local process group
  GET  /api/scenes/jobs      poll still/render/bake progress
  GET  /api/jobs             list durable job records
"""

from __future__ import annotations

import json
import io
import math
import mimetypes
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from hashlib import sha256
from datetime import datetime, timezone
from email.message import Message
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
EDITOR_DIR = Path(__file__).resolve().parent
REMOTION_DIR = ROOT / "remotion"
REMOTION_MANIFEST = REMOTION_DIR / "src" / "shots.manifest.json"

sys.path.insert(0, str(EDITOR_DIR))
from contracts import (  # noqa: E402
    derive_legacy_fields,
    migrate_timeline,
    new_uid,
    resolve_persisted_path,
    sha256_file,
    utc_now,
    validate_timeline,
)


def resolve_project(value: str) -> Path:
    """Resolve documented project names as well as explicit paths."""
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    direct = (ROOT / raw).resolve()
    nested = (ROOT / "videos" / raw).resolve()
    if direct.exists():
        return direct
    if nested.exists():
        return nested
    # New bare project names belong in videos/, matching videos/README.md.
    return direct if raw.parts and raw.parts[0] == "videos" else nested


PROJECT = resolve_project(sys.argv[1] if len(sys.argv) > 1 else "video-1")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
SESSION_TOKEN = os.environ.get("CYE_WORKBENCH_TOKEN") or secrets.token_urlsafe(24)
CLI_IMPORT_TOKEN = os.environ.get("CYE_CLI_IMPORT_TOKEN") or secrets.token_urlsafe(24)


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def range_label(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def range_output_path(from_s: float, to_s: float) -> Path:
    return project_write_path(
        PROJECT / "work" / "preview" / f"range-{range_label(from_s)}-{range_label(to_s)}.mp4"
    )


def project_write_path(path: Path) -> Path:
    """Resolve a write target and reject symlink/junction escapes."""
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(PROJECT.resolve(strict=False))
    except ValueError as exc:
        raise ApiError(
            400,
            "E_ASSET_OUTSIDE_PROJECT",
            "write path must stay inside the project",
        ) from exc
    return resolved


PROJECT_ARG = repo_relative(PROJECT)

# Reuse the one r_frame_rate parser from tools/encoders.py so frame stepping in
# the browser agrees with the render pipeline.
sys.path.insert(0, str(EDITOR_DIR.parent))
try:
    from encoders import probe_fps  # noqa: E402
except Exception:  # noqa: BLE001 - the editor should still boot without ffprobe
    probe_fps = None

CUTS = PROJECT / "work" / "analysis" / "cuts.json"
PROXY = PROJECT / "work" / "editor" / "proxy.mp4"
EDITOR_MANIFEST = PROJECT / "work" / "editor" / "manifest.json"
TIMELINE = PROJECT / "work" / "timeline.json"
JOBS_DIR = PROJECT / "work" / "jobs"
INBOX_DIR = PROJECT / "work" / "inbox"
EDITED_TRANSCRIPT = PROJECT / "work" / "edited-transcript.json"

MEDIA = {
    "/media/proxy.mp4": (PROXY, "video/mp4"),
    "/media/waveform.png": (PROJECT / "work" / "editor" / "waveform.png", "image/png"),
}
STATIC = {
    "/scene-editor.css": (EDITOR_DIR / "scene-editor.css", "text/css; charset=utf-8"),
    "/scene-editor.js": (EDITOR_DIR / "scene-editor.js", "text/javascript; charset=utf-8"),
}

render_state = {"running": False, "log": "", "ok": None}
scene_jobs = {
    "still": {"running": False, "log": "", "ok": None, "progress": 0, "url": None, "id": None, "job_id": None},
    "render": {"running": False, "log": "", "ok": None, "progress": 0, "id": None, "job_id": None},
    "bake": {"running": False, "log": "", "ok": None, "progress": 0, "output": None, "job_id": None},
}
job_lock = threading.RLock()
timeline_lock = threading.Lock()
active_processes: dict[str, subprocess.Popen] = {}
canceling_jobs: set[str] = set()

MAX_UPLOAD_BYTES = int(os.environ.get("CYE_MAX_UPLOAD_BYTES", 20 * 1024 * 1024 * 1024))
MAX_MULTIPART_FIELD_BYTES = 64 * 1024
MAX_JSON_BODY_BYTES = int(os.environ.get("CYE_MAX_JSON_BODY_BYTES", 8 * 1024 * 1024))
FFPROBE_TIMEOUT_S = max(1.0, float(os.environ.get("CYE_FFPROBE_TIMEOUT_S", 120)))
FFMPEG_TIMEOUT_S = max(1.0, float(os.environ.get("CYE_FFMPEG_TIMEOUT_S", 7200)))
MAX_CONCURRENT_CONFORMS = max(1, min(8, int(os.environ.get("CYE_MAX_CONCURRENT_CONFORMS", 2))))
conform_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONFORMS)
COURIER_POLL_SECONDS = max(0.1, float(os.environ.get("CYE_COURIER_POLL_SECONDS", 0.5)))
COURIER_SETTLE_SECONDS = max(0.0, float(os.environ.get("CYE_COURIER_SETTLE_SECONDS", 0.25)))


class ApiError(Exception):
    """An expected request failure with a stable machine-readable code."""

    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class LimitedRequestReader:
    """Read one Content-Length body without buffering a video in memory."""

    def __init__(self, stream, length: int):
        self.stream = stream
        self.remaining = length
        self.buffer = bytearray()

    def _read_chunk(self) -> bool:
        if self.remaining <= 0:
            return False
        chunk = self.stream.read(min(1 << 16, self.remaining))
        if not chunk:
            raise ApiError(400, "E_UPLOAD_TRUNCATED", "upload ended before Content-Length bytes arrived")
        self.remaining -= len(chunk)
        self.buffer.extend(chunk)
        return True

    def readline(self, limit: int = 64 * 1024) -> bytes:
        while True:
            marker = self.buffer.find(b"\n")
            if marker >= 0:
                if marker + 1 > limit:
                    raise ApiError(400, "E_MULTIPART_INVALID", "multipart header line is too long")
                value = bytes(self.buffer[: marker + 1])
                del self.buffer[: marker + 1]
                return value
            if len(self.buffer) > limit:
                raise ApiError(400, "E_MULTIPART_INVALID", "multipart header line is too long")
            if not self._read_chunk():
                value = bytes(self.buffer)
                self.buffer.clear()
                return value

    def read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            if not self._read_chunk():
                raise ApiError(400, "E_MULTIPART_INVALID", "multipart body is incomplete")
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def copy_until(self, marker: bytes, output, limit: int | None = None) -> int:
        written = 0
        while True:
            index = self.buffer.find(marker)
            if index >= 0:
                chunk = bytes(self.buffer[:index])
                if limit is not None and written + len(chunk) > limit:
                    raise ApiError(413, "E_FIELD_TOO_LARGE", "multipart text field is too large")
                output.write(chunk)
                written += len(chunk)
                del self.buffer[: index + len(marker)]
                return written

            safe = max(0, len(self.buffer) - len(marker) + 1)
            if safe:
                chunk = bytes(self.buffer[:safe])
                if limit is not None and written + len(chunk) > limit:
                    raise ApiError(413, "E_FIELD_TOO_LARGE", "multipart text field is too large")
                output.write(chunk)
                written += len(chunk)
                del self.buffer[:safe]
            if not self._read_chunk():
                raise ApiError(400, "E_MULTIPART_INVALID", "multipart boundary is missing")

    def discard(self) -> None:
        self.buffer.clear()
        while self.remaining > 0:
            chunk = self.stream.read(min(1 << 16, self.remaining))
            if not chunk:
                break
            self.remaining -= len(chunk)


def header_parameter(value: str, header: str, name: str) -> str | None:
    message = Message()
    message[header] = value
    result = message.get_param(name, header=header)
    return str(result) if result is not None else None


def parse_multipart_upload(handler, staging: Path) -> tuple[Path, str, dict[str, str]]:
    """Stream one multipart file into a project-local staging directory."""
    content_type = handler.headers.get("Content-Type", "")
    boundary_text = header_parameter(content_type, "content-type", "boundary")
    try:
        content_length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ApiError(400, "E_UPLOAD_LENGTH", "invalid Content-Length") from exc
    if not boundary_text or not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(415, "E_MULTIPART_REQUIRED", "take import requires multipart/form-data")
    if content_length <= 0:
        raise ApiError(400, "E_UPLOAD_EMPTY", "upload body is empty")
    if content_length > MAX_UPLOAD_BYTES:
        raise ApiError(413, "E_UPLOAD_TOO_LARGE", f"upload exceeds {MAX_UPLOAD_BYTES} bytes")

    try:
        boundary = boundary_text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ApiError(400, "E_MULTIPART_INVALID", "multipart boundary must be ASCII") from exc
    if not boundary or len(boundary) > 200 or b"\r" in boundary or b"\n" in boundary:
        raise ApiError(400, "E_MULTIPART_INVALID", "multipart boundary is invalid")

    reader = LimitedRequestReader(handler.rfile, content_length)
    first = reader.readline()
    if first.rstrip(b"\r\n") != b"--" + boundary:
        raise ApiError(400, "E_MULTIPART_INVALID", "multipart body does not start with its boundary")

    upload_path = None
    upload_name = ""
    fields: dict[str, str] = {}
    delimiter = b"\r\n--" + boundary
    finished = False
    while not finished:
        headers: dict[str, str] = {}
        while True:
            line = reader.readline()
            if line in {b"\r\n", b"\n"}:
                break
            if not line:
                raise ApiError(400, "E_MULTIPART_INVALID", "multipart part headers are incomplete")
            key, separator, value = line.decode("iso-8859-1").partition(":")
            if not separator:
                raise ApiError(400, "E_MULTIPART_INVALID", "multipart part header is malformed")
            headers[key.strip().lower()] = value.strip()

        disposition = headers.get("content-disposition", "")
        field_name = header_parameter(disposition, "content-disposition", "name")
        filename = header_parameter(disposition, "content-disposition", "filename")
        if not field_name:
            raise ApiError(400, "E_MULTIPART_INVALID", "multipart part is missing a name")

        if field_name == "file":
            if upload_path is not None:
                raise ApiError(400, "E_MULTIPART_INVALID", "only one file field is allowed")
            suffix = Path(filename or "upload.bin").suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ".bin"
            upload_path = staging / f"upload{suffix}"
            upload_name = Path(filename or f"upload{suffix}").name
            with upload_path.open("xb") as output:
                reader.copy_until(delimiter, output)
        else:
            output = io.BytesIO()
            reader.copy_until(delimiter, output, MAX_MULTIPART_FIELD_BYTES)
            fields[field_name] = output.getvalue().decode("utf-8", errors="replace")

        trailer = reader.read_exact(2)
        if trailer == b"--":
            finished = True
            if reader.buffer.startswith(b"\r\n"):
                del reader.buffer[:2]
        elif trailer != b"\r\n":
            raise ApiError(400, "E_MULTIPART_INVALID", "multipart boundary trailer is invalid")

    reader.discard()
    if upload_path is None or not upload_path.is_file() or upload_path.stat().st_size == 0:
        raise ApiError(400, "E_UPLOAD_EMPTY", "multipart file field is missing or empty")
    return upload_path, upload_name, fields


def _fraction_value(value: object) -> float:
    raw = str(value or "").strip()
    if not raw or raw == "0/0":
        return 0.0
    try:
        return float(Fraction(raw))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_media(path: Path) -> dict:
    command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=FFPROBE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise ApiError(504, "E_MEDIA_PROBE_TIMEOUT", "ffprobe timed out while reading the uploaded file") from exc
    except OSError as exc:
        raise ApiError(503, "E_FFMPEG_MISSING", "ffprobe is required to import takes") from exc
    if result.returncode != 0:
        raise ApiError(400, "E_MEDIA_PROBE", "ffprobe could not read the uploaded file", result.stderr[-2000:])
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ApiError(500, "E_MEDIA_PROBE", "ffprobe returned invalid JSON") from exc
    streams = document.get("streams") if isinstance(document.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video, dict):
        raise ApiError(400, "E_MEDIA_UNSUPPORTED", "this slice accepts video takes; no video stream was found")
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    pix_fmt = str(video.get("pix_fmt", "")).lower()
    average_fps = str(video.get("avg_frame_rate") or "0/0")
    nominal_fps = str(video.get("r_frame_rate") or "0/0")
    fps = average_fps if _fraction_value(average_fps) > 0 else nominal_fps
    cfr = (
        _fraction_value(average_fps) > 0
        and _fraction_value(nominal_fps) > 0
        and abs(_fraction_value(average_fps) - _fraction_value(nominal_fps)) < 0.001
    )
    duration_value = video.get("duration") or (document.get("format") or {}).get("duration") or 0
    try:
        duration = max(0.0, float(duration_value))
    except (TypeError, ValueError):
        duration = 0.0
    alpha = (
        pix_fmt.startswith("yuva")
        or pix_fmt.startswith("gbrap")
        or pix_fmt in {"rgba", "argb", "bgra", "abgr"}
        or str((video.get("tags") or {}).get("alpha_mode", "")) == "1"
    )
    return {
        "w": int(video.get("width") or 0),
        "h": int(video.get("height") or 0),
        "fps": fps,
        "cfr": cfr,
        "dur_s": round(duration, 6),
        "alpha": alpha,
        "video_codec": str(video.get("codec_name", "")),
        "pix_fmt": pix_fmt,
        "audio_codec": str(audio.get("codec_name", "")) if isinstance(audio, dict) else "",
    }


def _public_probe(probe: dict, conformed: bool) -> dict:
    return {
        "w": probe["w"], "h": probe["h"], "fps": probe["fps"],
        "dur_s": probe["dur_s"], "alpha": probe["alpha"], "conformed": conformed,
    }


def conform_take(source: Path, staging: Path, scene: dict, timeline: dict) -> tuple[Path, str, dict]:
    """Return a comp-native immutable artifact staged beside its source."""
    source_probe = probe_media(source)
    preview = timeline.get("preview") if isinstance(timeline.get("preview"), dict) else {}
    target_w = max(1, int(preview.get("width") or 1920))
    target_h = max(1, int(preview.get("height") or 1080))
    target_fps = max(1, int(preview.get("fps") or 30))
    fps_matches = abs(_fraction_value(source_probe["fps"]) - target_fps) < 0.001

    if scene.get("type") == "overlay":
        if not source_probe["alpha"]:
            raise ApiError(400, "E_PROFILE_MISMATCH", "overlay takes must contain an alpha channel")
        profile = "overlay_alpha"
        artifact = staging / "asset.mov"
        native = (
            source.suffix.lower() == ".mov"
            and source_probe["video_codec"] == "prores"
            and source_probe["pix_fmt"].startswith("yuva")
            and source_probe["w"] == target_w and source_probe["h"] == target_h
            and fps_matches and source_probe["cfr"]
        )
        filter_graph = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            f"fps={target_fps},format=yuva444p10le"
        )
        codec_args = ["-an", "-c:v", "prores_ks", "-profile:v", "4", "-alpha_bits", "16"]
    else:
        profile = "cutaway_h264"
        artifact = staging / "asset.mp4"
        native = (
            source.suffix.lower() == ".mp4"
            and source_probe["video_codec"] == "h264"
            and source_probe["pix_fmt"] in {"yuv420p", "yuvj420p"}
            and source_probe["w"] == target_w and source_probe["h"] == target_h
            and fps_matches and source_probe["cfr"]
            and source_probe["audio_codec"] in {"", "aac"}
        )
        filter_graph = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={target_fps},format=yuv420p"
        )
        codec_args = [
            "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
        ]

    if native:
        shutil.copy2(source, artifact)
    else:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:v:0", "-vf", filter_graph,
            *codec_args, "-fps_mode", "cfr",
        ]
        if source_probe["dur_s"] > 0:
            command.extend(["-t", f"{source_probe['dur_s']:.6f}"])
        command.append(str(artifact))
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=FFMPEG_TIMEOUT_S
            )
        except subprocess.TimeoutExpired as exc:
            raise ApiError(504, "E_CONFORM_TIMEOUT", "ffmpeg timed out while conforming the uploaded take") from exc
        except OSError as exc:
            raise ApiError(503, "E_FFMPEG_MISSING", "ffmpeg is required to conform takes") from exc
        if result.returncode != 0 or not artifact.is_file():
            raise ApiError(422, "E_CONFORM_FAILED", "ffmpeg could not conform the uploaded take", result.stderr[-4000:])

    output_probe = probe_media(artifact)
    if output_probe["w"] != target_w or output_probe["h"] != target_h:
        raise ApiError(500, "E_CONFORM_FAILED", "conformed take has the wrong dimensions")
    if abs(_fraction_value(output_probe["fps"]) - target_fps) >= 0.001:
        raise ApiError(500, "E_CONFORM_FAILED", "conformed take has the wrong frame rate")
    if profile == "overlay_alpha" and not output_probe["alpha"]:
        raise ApiError(500, "E_CONFORM_FAILED", "conformed overlay lost its alpha channel")
    return artifact, profile, _public_probe(output_probe, not native)


def proxy_fps() -> str:
    if probe_fps is None or not PROXY.exists():
        return ""
    try:
        return probe_fps(PROXY)
    except Exception:  # noqa: BLE001
        return ""


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def timeline_etag() -> str:
    """Return the strong ETag for the exact persisted timeline bytes."""
    if TIMELINE.exists():
        return sha256(TIMELINE.read_bytes()).hexdigest()
    fallback = json.dumps(default_timeline(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(fallback).hexdigest()


def project_duration() -> float:
    manifest = read_json(EDITOR_MANIFEST, {}) or {}
    try:
        duration = float(manifest.get("total", 0))
        return duration if duration > 0 else 60.0
    except (TypeError, ValueError):
        return 60.0


def remotion_catalog() -> list[dict]:
    data = read_json(REMOTION_MANIFEST, []) or []
    return data if isinstance(data, list) else []


def default_master() -> Path:
    preferred = [
        PROJECT / "output" / "master-tight.mp4",
        PROJECT / "output" / "master-natural.mp4",
        PROJECT / "reference" / "master.mp4",
        PROJECT / "output" / "preview-tight.mp4",
        PROJECT / "output" / "preview-natural.mp4",
    ]
    for path in preferred:
        if path.exists():
            return path
    reference = PROJECT / "reference"
    if reference.exists():
        found = sorted(reference.glob("*.mp4"))
        if found:
            return found[0]
    return preferred[0]


def default_timeline() -> dict:
    duration = project_duration()
    return {
        "master": repo_relative(default_master()),
        "master_fps": 60,
        "remotion_out": "remotion/out",
        "shots": [],
        "preview": {
            "end_s": round(duration, 3),
            "out": repo_relative(PROJECT / "output" / f"{PROJECT.name}-preview.mp4"),
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "editor": {"version": 1},
    }


def load_timeline() -> dict:
    timeline = read_json(TIMELINE, None)
    if not isinstance(timeline, dict):
        timeline = default_timeline()
    defaults = default_timeline()
    timeline.setdefault("master", defaults["master"])
    timeline.setdefault("master_fps", defaults["master_fps"])
    timeline.setdefault("remotion_out", "remotion/out")
    timeline.setdefault("shots", [])
    preview = timeline.setdefault("preview", {})
    for key, value in defaults["preview"].items():
        preview.setdefault(key, value)
    timeline.setdefault("editor", {"version": 1})
    catalog_ids = {str(item.get("id")) for item in remotion_catalog() if item.get("id")}
    return migrate_timeline(timeline, catalog_ids, ROOT, PROJECT)


def timeline_snapshot() -> tuple[dict, str]:
    """Read one timeline document and its matching persisted-byte ETag."""
    with timeline_lock:
        timeline = load_timeline()
        etag = timeline_etag()
    return timeline, etag


def transcript_word_ticks(path: Path = EDITED_TRANSCRIPT) -> list[dict]:
    """Return transcript word boundaries in the master clock for the scene ruler."""
    document = read_json(path, {})
    words = document.get("words") if isinstance(document, dict) else None
    if not isinstance(words, list):
        return []
    ticks = []
    for word in words:
        if not isinstance(word, dict):
            continue
        try:
            start_s = max(0.0, float(word["start"]) / 1000)
            end_s = max(start_s, float(word.get("end", word["start"])) / 1000)
        except (KeyError, TypeError, ValueError):
            continue
        ticks.append({
            "text": str(word.get("text") or ""),
            "start_s": round(start_s, 3),
            "end_s": round(end_s, 3),
        })
    return ticks


def take_immutability_errors(current: dict, proposed: dict) -> list[str]:
    """Keep take creation server-owned and reject client rewrites."""
    current_scenes = {
        str(scene.get("scene_uid")): scene
        for scene in current.get("shots", [])
        if isinstance(scene, dict) and scene.get("scene_uid")
    }
    proposed_scenes = {
        str(scene.get("scene_uid")): scene
        for scene in proposed.get("shots", [])
        if isinstance(scene, dict) and scene.get("scene_uid")
    }
    errors = []
    for scene_uid, new_scene in proposed_scenes.items():
        if scene_uid not in current_scenes and new_scene.get("takes"):
            errors.append(
                f"takes for new scene {scene_uid} must be created through an import or render endpoint"
            )
    for scene_uid, old_scene in current_scenes.items():
        new_scene = proposed_scenes.get(scene_uid)
        if new_scene is None:
            continue  # Scene deletion is allowed; immutable files remain for later vacuuming.
        old_takes = old_scene.get("takes", []) if isinstance(old_scene.get("takes"), list) else []
        new_takes = new_scene.get("takes", []) if isinstance(new_scene.get("takes"), list) else []
        if new_takes != old_takes:
            errors.append(
                f"take history for scene {scene_uid} is immutable and server-owned"
            )
    return errors


def normalize_timeline(payload: object) -> tuple[dict | None, list[str], list[str]]:
    """Return a v2.1 timeline plus legacy-compatible error/warning text."""
    if not isinstance(payload, dict):
        return None, ["timeline must be a JSON object"], []

    shots = payload.get("shots")
    if not isinstance(shots, list):
        return None, ["timeline.shots must be an array"], []

    catalog_items = remotion_catalog()
    catalog = {str(item.get("id")): item for item in catalog_items if item.get("id")}
    out = migrate_timeline(payload, set(catalog), ROOT, PROJECT)
    normalized = []
    for raw in out["shots"]:
        scene = dict(raw)
        engine = str(scene.get("engine", "remotion")).strip().lower() or "remotion"
        scene_type = str(scene.get("type", "cutaway")).strip().lower()
        try:
            start = round(float(scene.get("master_in_s")), 3)
            end = round(float(scene.get("master_out_s")), 3)
        except (TypeError, ValueError):
            start, end = scene.get("master_in_s"), scene.get("master_out_s")
        scene.update({
            "engine": engine,
            "type": scene_type,
            "master_in_s": start,
            "master_out_s": end,
            "enabled": bool(scene.get("enabled", True)),
        })
        normalized.append(derive_legacy_fields(scene))

    def sort_key(item):
        try:
            return float(item.get("master_in_s", 0)), float(item.get("master_out_s", 0)), str(item.get("scene_uid", ""))
        except (TypeError, ValueError):
            return float("inf"), float("inf"), str(item.get("scene_uid", ""))

    normalized.sort(key=sort_key)

    preview = out.get("preview")
    if not isinstance(preview, dict):
        preview = {}
    defaults = default_timeline()
    clean_preview = dict(preview)
    for key, value in defaults["preview"].items():
        clean_preview.setdefault(key, value)
    try:
        clean_preview["end_s"] = round(float(clean_preview["end_s"]), 3)
        clean_preview["width"] = int(clean_preview["width"])
        clean_preview["height"] = int(clean_preview["height"])
        clean_preview["fps"] = int(clean_preview["fps"])
    except (TypeError, ValueError):
        pass

    out["shots"] = normalized
    out["preview"] = clean_preview
    out.setdefault("master", defaults["master"])
    out.setdefault("master_fps", defaults["master_fps"])
    out.setdefault("remotion_out", "remotion/out")
    editor = out.get("editor") if isinstance(out.get("editor"), dict) else {}
    editor.update({"version": 2.1, "updated_at": datetime.now(timezone.utc).isoformat()})
    out["editor"] = editor
    duration = project_duration() if EDITOR_MANIFEST.exists() else None
    issues = validate_timeline(out, catalog, ROOT, PROJECT, duration)
    save_blocking = {
        "E_SCENE_SCHEMA", "E_SPAN_INVALID", "E_OVERLAP_UNCLAIMED",
        "E_COMP_NOT_FOUND", "E_LEGACY_ID_COLLISION", "E_ASSET_OUTSIDE_PROJECT",
        "E_ASSET_NONCANONICAL",
    }
    errors = [item["message"] for item in issues if item["code"] in save_blocking]
    warnings = [item["message"] for item in issues if item["severity"] == "W"]
    return out, list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def project_validation(timeline: dict | None = None) -> list[dict]:
    document = timeline if timeline is not None else load_timeline()
    catalog = {str(item.get("id")): item for item in remotion_catalog() if item.get("id")}
    duration = project_duration() if EDITOR_MANIFEST.exists() else None
    return validate_timeline(document, catalog, ROOT, PROJECT, duration)


def backup_and_write(path: Path, data: dict, folder_name: str, prefix: str) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backups = path.parent / folder_name
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backups / f"{prefix}-{stamp}.json"
        shutil.copy2(path, backup)
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return repo_relative(backup) if backup else None


def write_job(record: dict) -> None:
    """Persist one job atomically; job files are the durable source of truth."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = JOBS_DIR / f"{record['job_id']}.json"
    payload = (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=JOBS_DIR, prefix=f".{record['job_id']}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def write_json_atomic(path: Path, value: dict) -> None:
    """Write server-owned JSON without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def read_jobs() -> list[dict]:
    records = []
    if not JOBS_DIR.exists():
        return records
    for path in JOBS_DIR.glob("job_*.json"):
        try:
            record = read_json(path)
            if isinstance(record, dict):
                records.append(record)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    records.sort(
        key=lambda record: (str(record.get("created_at", "")), str(record.get("job_id", ""))),
        reverse=True,
    )
    return records


def read_job(job_id: str) -> dict | None:
    if not re.fullmatch(r"job_[A-Za-z0-9]{8,32}", job_id):
        return None
    try:
        record = read_json(JOBS_DIR / f"{job_id}.json")
        return record if isinstance(record, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def find_scene(timeline: dict, scene_uid: str) -> dict | None:
    return next((
        scene for scene in timeline.get("shots", [])
        if isinstance(scene, dict) and scene.get("scene_uid") == scene_uid
    ), None)


def allocate_take_dir(scene_uid: str) -> tuple[str, Path]:
    for _ in range(4):
        take_uid = new_uid("take")
        destination = project_write_path(PROJECT / "work" / "generated" / scene_uid / take_uid)
        try:
            destination.mkdir(parents=True, exist_ok=False)
            return take_uid, destination
        except FileExistsError:
            continue
    raise ApiError(500, "E_TAKE_ID_COLLISION", "could not allocate a take id")


def create_generate_job(scene_uid: str, provider: str, spec: dict) -> dict:
    job_id = new_uid("job")
    now = utc_now()
    inbox = project_write_path(INBOX_DIR / job_id)
    inbox.mkdir(parents=True, exist_ok=False)
    immutable_spec = {
        "prompt": spec["prompt"],
        "provider_hint": spec["provider_hint"],
        "duration_s": spec["duration_s"],
    }
    input_bytes = json.dumps(
        {"scene_uid": scene_uid, "provider": provider, "spec": immutable_spec},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    record = {
        "job_id": job_id,
        "kind": "generate",
        "project": repo_relative(PROJECT),
        "scene_uid": scene_uid,
        "provider": provider,
        "spec": immutable_spec,
        "input_sha256": sha256(input_bytes).hexdigest(),
        "state": "submitted",
        "progress": 0,
        "message": "waiting for courier fulfillment",
        "attempt": 1,
        "max_attempts": 2,
        "parent_job_id": None,
        "lineage_root": job_id,
        "pid": None,
        "start_token": None,
        "provider_job_id": None,
        "output_dir": repo_relative(inbox),
        "expected_artifacts": [],
        "candidates": [],
        "resolved_candidate_count": 0,
        "inbox_files": {},
        "exit_code": None,
        "error": None,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    try:
        write_job(record)
    except Exception:
        shutil.rmtree(inbox, ignore_errors=True)
        raise
    return record


def attach_courier_candidate(
    record: dict,
    inbox_file: Path,
    source: Path,
    artifact: Path,
    profile: str,
    probe: dict,
    digest: str,
    file_stamp: dict,
) -> dict:
    """Attach one conformed courier result while job_lock is held."""
    scene_uid = str(record["scene_uid"])
    timeline = load_timeline()
    scene = find_scene(timeline, scene_uid)
    if scene is None:
        raise ApiError(404, "E_SCENE_NOT_FOUND", "courier job scene was not found")
    takes = scene.get("takes") if isinstance(scene.get("takes"), list) else []
    scene["takes"] = takes
    take = next((
        item for item in takes
        if isinstance(item, dict) and secrets.compare_digest(str(item.get("sha256", "")), digest)
    ), None)
    final_dir = None
    if take is None:
        take_uid, final_dir = allocate_take_dir(scene_uid)
        source_suffix = source.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", source_suffix):
            source_suffix = ".bin"
        artifact_suffix = artifact.suffix.lower()
        final_source = final_dir / f"source{source_suffix}"
        final_artifact = final_dir / f"asset{artifact_suffix}"
        try:
            os.replace(source, final_source)
            os.replace(artifact, final_artifact)
            take = {
                "take_uid": take_uid,
                "file": repo_relative(final_artifact),
                "source_file": repo_relative(final_source),
                "sha256": digest,
                "created_at": utc_now(),
                "conform_profile": profile,
                "probe": probe,
                "provenance": {
                    "provider": record["provider"],
                    "job_id": record["job_id"],
                    "spec": dict(record["spec"]),
                },
            }
            takes.append(take)
            normalized, errors, _warnings = normalize_timeline(timeline)
            if errors or normalized is None:
                raise ApiError(400, "E_TIMELINE_INVALID", "courier take could not be attached", errors)
            backup_and_write(TIMELINE, normalized, "backups", "timeline")
            timeline = normalized
            scene = find_scene(timeline, scene_uid)
        except Exception:
            shutil.rmtree(final_dir, ignore_errors=True)
            raise

    picked = bool(scene and scene.get("active_take_uid") == take["take_uid"])
    candidates = record.setdefault("candidates", [])
    existing_candidate = next((
        item for item in candidates
        if isinstance(item, dict) and secrets.compare_digest(str(item.get("sha256", "")), digest)
    ), None)
    if existing_candidate is None:
        candidates.append({
            "take_uid": take["take_uid"],
            "file": take["file"],
            "sha256": digest,
            "picked": picked,
            "inbox_file": inbox_file.name,
            "inbox_mtime_ns": file_stamp["mtime_ns"],
            "inbox_size": file_stamp["size"],
        })
    record.setdefault("inbox_files", {})[inbox_file.name] = file_stamp
    now = utc_now()
    unresolved = max(0, len(candidates) - int(record.get("resolved_candidate_count", 0)))
    if unresolved:
        record.update({
            "state": "awaiting_pick",
            "progress": 1,
            "message": f"{unresolved} candidate(s) awaiting pick",
            "completed_at": None,
        })
    else:
        record.update({
            "state": "succeeded",
            "progress": 1,
            "message": "fulfilled candidate is already active",
            "completed_at": now,
        })
    record.update({"updated_at": now, "error": None})
    write_job(record)
    return record


def poll_courier_job(record: dict) -> dict:
    if record.get("kind") != "generate" or record.get("state") == "canceled":
        return record
    inbox, outside = resolve_persisted_path(ROOT, PROJECT, str(record.get("output_dir", "")))
    if outside or inbox is None or not inbox.is_dir():
        return record
    for inbox_file in sorted(inbox.iterdir(), key=lambda path: path.name):
        if inbox_file.name.startswith(".") or not inbox_file.is_file():
            continue
        try:
            stat = inbox_file.stat()
        except OSError:
            continue
        if time.time() - stat.st_mtime < COURIER_SETTLE_SECONDS:
            continue
        file_stamp = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
        if record.get("inbox_files", {}).get(inbox_file.name) == file_stamp:
            continue
        temp_root = project_write_path(PROJECT / "work" / "tmp")
        temp_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=temp_root, prefix=".courier-ingest-") as folder:
                staging = Path(folder)
                suffix = inbox_file.suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                    suffix = ".bin"
                source = staging / f"source{suffix}"
                shutil.copy2(inbox_file, source)
                timeline = load_timeline()
                scene = find_scene(timeline, str(record.get("scene_uid", "")))
                if scene is None:
                    raise ApiError(404, "E_SCENE_NOT_FOUND", "courier job scene was not found")
                with conform_slots:
                    artifact, profile, probe = conform_take(source, staging, scene, timeline)
                digest = sha256_file(artifact)
                with job_lock:
                    current = read_job(str(record["job_id"]))
                    if current is None or current.get("state") == "canceled":
                        return current or record
                    with timeline_lock:
                        record = attach_courier_candidate(
                            current, inbox_file, source, artifact, profile, probe, digest, file_stamp
                        )
        except (ApiError, OSError) as error:
            with job_lock:
                current = read_job(str(record["job_id"])) or record
                if current.get("state") != "canceled":
                    current.setdefault("inbox_files", {})[inbox_file.name] = file_stamp
                    current.update({
                        "message": f"courier file rejected: {error}",
                        "error": {"code": getattr(error, "code", "E_COURIER_INGEST"), "message": str(error)},
                        "updated_at": utc_now(),
                    })
                    write_job(current)
                record = current
    return record


def poll_courier_jobs() -> list[dict]:
    records = read_jobs()
    for index, record in enumerate(records):
        if record.get("kind") == "generate" and record.get("state") != "canceled":
            records[index] = poll_courier_job(record)
        else:
            records[index] = reconcile_job_record(record)
    return records


def courier_poller() -> None:
    while True:
        try:
            poll_courier_jobs()
        except Exception:  # noqa: BLE001 - one bad drop must not stop the watcher
            pass
        time.sleep(COURIER_POLL_SECONDS)


class CourierProvider:
    """File-backed provider implementing submit(spec), poll(job), and cancel(job)."""

    def __init__(self, scene_uid: str, provider: str):
        self.scene_uid = scene_uid
        self.provider = provider

    def submit(self, spec: dict) -> dict:
        return create_generate_job(self.scene_uid, self.provider, spec)

    def poll(self, record: dict) -> dict:
        return poll_courier_job(record)

    def cancel(self, record: dict) -> dict:
        now = utc_now()
        record.update({
            "state": "canceled",
            "message": "canceled before courier fulfillment",
            "updated_at": now,
            "completed_at": now,
            "error": None,
        })
        write_job(record)
        return record


def linux_proc_stat_fields(pid: int) -> list[str] | None:
    """Return /proc stat fields 3..N without being confused by spaces in comm."""
    proc_stat = Path(f"/proc/{pid}/stat")
    if not proc_stat.exists():
        return None
    try:
        _, suffix = proc_stat.read_text(encoding="utf-8").rsplit(")", 1)
        fields = suffix.split()
        return fields if len(fields) >= 20 else None
    except (OSError, ValueError):
        return None


def process_start_token(pid: int) -> str | None:
    """Return a PID-reuse-safe token on Linux/WSL and Windows."""
    proc_fields = linux_proc_stat_fields(pid)
    if proc_fields is not None:
        return f"{pid}:{proc_fields[19]}"
    if os.name == "nt":
        command = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; $p.StartTime.ToUniversalTime().ToFileTimeUtc()",
        ]
        try:
            start = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
            return f"{pid}:{start}" if start else None
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        start = subprocess.check_output(
            ["ps", "-o", "lstart=", "-p", str(pid)], text=True, stderr=subprocess.DEVNULL, timeout=3
        ).strip()
        return f"{pid}:{start}" if start else None
    except (OSError, subprocess.SubprocessError):
        return None


def pid_is_alive(pid: int) -> bool:
    proc_fields = linux_proc_stat_fields(pid)
    if proc_fields is not None:
        return proc_fields[0] != "Z"
    if os.name == "nt":
        return process_start_token(pid) is not None
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def reconcile_job_record(record: dict) -> dict:
    """Mark one interrupted local job honestly after a server restart."""
    if record.get("kind") == "generate":
        return record
    if record.get("state") not in {"queued", "submitted", "running"}:
        return record
    job_id = record.get("job_id")
    with job_lock:
        owned_here = job_id in active_processes or any(
            state.get("running") and state.get("job_id") == job_id for state in scene_jobs.values()
        )
    if owned_here:
        return record
    pid = record.get("pid")
    current_token = process_start_token(pid) if isinstance(pid, int) and pid_is_alive(pid) else None
    expected = record.get("start_token")
    if not isinstance(pid, int) or not pid_is_alive(pid) or (current_token and expected and current_token != expected):
        record["state"] = "orphaned"
        record["message"] = "worker is no longer running; retry requires human action"
    elif current_token is None or expected is None:
        record["state"] = "unknown"
        record["message"] = "worker PID exists but its start token cannot be verified"
    else:
        return record
    record["updated_at"] = utc_now()
    record["completed_at"] = record["updated_at"]
    write_job(record)
    return record


def reconcile_jobs() -> list[dict]:
    return [reconcile_job_record(record) for record in read_jobs()]


def reconcile_job(job_id: str) -> dict | None:
    record = read_job(job_id)
    return reconcile_job_record(record) if record else None


def job_artifact_path(job_id: str, published_output: str) -> Path:
    suffix = Path(published_output).suffix or ".bin"
    return project_write_path(JOBS_DIR / job_id / f"output{suffix}")


def capture_job_artifact(job_id: str, published_output: str, *, prefer_hardlink: bool = False) -> Path:
    """Snapshot a published output into the job's immutable artifact directory."""
    source, outside = resolve_persisted_path(ROOT, PROJECT, published_output)
    if outside or source is None or not source.is_file():
        raise OSError(f"published job artifact was not found: {published_output}")
    destination = job_artifact_path(job_id, published_output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.tmp")
    try:
        if prefer_hardlink:
            try:
                os.link(source, temp_path)
            except OSError:
                shutil.copy2(source, temp_path)
        else:
            shutil.copy2(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return destination


def cleanup_bake_scratch(published_output: str | None, pid: int | None) -> None:
    if not published_output or not isinstance(pid, int):
        return
    output, outside = resolve_persisted_path(ROOT, PROJECT, published_output)
    if outside or output is None:
        return
    scratch = project_write_path(output.parent / f"_bake_tmp_{pid}")
    if scratch.is_dir():
        shutil.rmtree(scratch, ignore_errors=True)


def create_job_record(kind: str, command: list[str], cwd: Path, initial: dict, job_id: str | None = None) -> dict:
    job_id = job_id or new_uid("job")
    now = utc_now()
    durable_kind = "bake" if kind == "bake" else "render"
    spec = {
        "command": command,
        "cwd": repo_relative(cwd),
        "ui_kind": kind,
        "timeline_etag": initial.get("timeline_etag"),
    }
    expected = []
    output_dir = repo_relative(REMOTION_DIR / "out")
    if initial.get("output"):
        artifact = job_artifact_path(job_id, str(initial["output"]))
        expected = [repo_relative(artifact)]
        output_dir = repo_relative(artifact.parent)
        spec["published_output"] = str(initial["output"])
    input_bytes = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record = {
        "job_id": job_id,
        "kind": durable_kind,
        "project": PROJECT_ARG,
        "provider": "remotion",
        "spec": spec,
        "input_sha256": sha256(input_bytes).hexdigest(),
        "state": "queued",
        "progress": 0,
        "message": "queued",
        "attempt": 1,
        "max_attempts": 2,
        "parent_job_id": None,
        "lineage_root": job_id,
        "pid": None,
        "start_token": None,
        "provider_job_id": None,
        "output_dir": output_dir,
        "expected_artifacts": expected,
        "exit_code": None,
        "error": None,
        "created_at": now,
        "started_at": None,
        "updated_at": now,
        "completed_at": None,
    }
    if isinstance(initial.get("range"), dict):
        record["range"] = dict(initial["range"])
    write_job(record)
    return record


def update_job_progress(job_id: str, progress: float, message: str) -> None:
    with job_lock:
        record = read_job(job_id)
        if not record or record.get("state") != "running" or job_id in canceling_jobs:
            return
        record["progress"] = max(float(record.get("progress", 0)), min(1.0, max(0.0, progress)))
        record["message"] = message or "running"
        record["updated_at"] = utc_now()
        write_job(record)


def run_process_job(kind: str, command: list[str], cwd: Path, job_id: str, **initial) -> None:
    state = scene_jobs[kind]
    state.update(running=True, log="", ok=None, progress=0, job_id=job_id, **initial)
    proc = None
    proc_token = None
    worker_stopped = True
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if command and command[0] == "node" and "--use-system-ca" not in env.get("NODE_OPTIONS", ""):
            env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --use-system-ca").strip()
        process_group_options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(cwd), encoding="utf-8", errors="replace",
            env=env, **process_group_options,
        )
        worker_stopped = False
        with job_lock:
            active_processes[job_id] = proc
            proc_token = process_start_token(proc.pid)
            record = read_job(job_id)
            if record is not None and record.get("state") != "canceled":
                now = utc_now()
                record.update({
                    "state": "running", "message": "running", "pid": proc.pid,
                    "progress": 0.01, "start_token": proc_token,
                    "started_at": now, "updated_at": now,
                })
                write_job(record)
                state["progress"] = 0.01
        assert proc.stdout is not None
        for line in proc.stdout:
            state["log"] += line
            if len(state["log"]) > 30000:
                state["log"] = state["log"][-30000:]
            progress_match = re.match(r"^PROGRESS\s+([0-9]*\.?[0-9]+)(?:\s+(.*))?$", line.strip())
            if progress_match:
                progress = min(1.0, max(0.0, float(progress_match.group(1))))
                message = (progress_match.group(2) or "running").strip()
                state["progress"] = progress
                update_job_progress(job_id, progress, message)
        proc.wait()
        if proc.returncode == 0 and initial.get("output"):
            update_job_progress(job_id, 0.99, "publishing immutable artifact")
            capture_job_artifact(job_id, str(initial["output"]), prefer_hardlink=kind == "bake")
        with job_lock:
            active_processes.pop(job_id, None)
            record = read_job(job_id)
            if record is not None:
                now = utc_now()
                if job_id in canceling_jobs:
                    state["ok"] = False
                    record.update({"exit_code": proc.returncode, "updated_at": now})
                elif record.get("state") in {"canceled", "unknown", "orphaned"}:
                    state["ok"] = False
                    record.update({"exit_code": proc.returncode, "updated_at": now, "completed_at": now})
                else:
                    state["ok"] = proc.returncode == 0
                    record.update({
                        "state": "succeeded" if proc.returncode == 0 else "failed",
                        "progress": 1 if proc.returncode == 0 else record.get("progress", 0),
                        "message": "completed" if proc.returncode == 0 else "process exited with an error",
                        "exit_code": proc.returncode,
                        "error": None if proc.returncode == 0 else {"code": "E_PROCESS_EXIT", "message": state["log"][-2000:]},
                        "updated_at": now,
                        "completed_at": now,
                    })
                write_job(record)
    except Exception as exc:  # noqa: BLE001
        state["log"] += f"\n{type(exc).__name__}: {exc}\n"
        state["ok"] = False
        if proc is not None:
            if proc.poll() is not None:
                worker_stopped = True
            elif proc_token:
                worker_stopped = terminate_process_group(proc.pid, proc_token)
                try:
                    proc.wait(timeout=2)
                    worker_stopped = True
                except subprocess.TimeoutExpired:
                    worker_stopped = False
        with job_lock:
            record = read_job(job_id)
            if record is not None:
                now = utc_now()
                if job_id in canceling_jobs:
                    record.update({"exit_code": proc.returncode if proc else None, "updated_at": now})
                elif record.get("state") not in {"canceled", "unknown", "orphaned"}:
                    if worker_stopped:
                        record.update({
                            "state": "failed", "message": str(exc), "updated_at": now, "completed_at": now,
                            "error": {"code": "E_PROCESS_EXEC", "message": str(exc)},
                        })
                    else:
                        record.update({
                            "state": "unknown", "message": "worker cleanup could not be confirmed",
                            "updated_at": now, "completed_at": now,
                            "error": {"code": "E_PROCESS_CLEANUP", "message": str(exc)},
                        })
                write_job(record)
    finally:
        with job_lock:
            if proc is None or worker_stopped:
                active_processes.pop(job_id, None)
            else:
                active_processes[job_id] = proc
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
        cleanup_bake_scratch(initial.get("output") if kind == "bake" else None, proc.pid if proc else None)
        state["running"] = False


def process_identity_matches(pid: int, expected_token: str) -> bool:
    return pid_is_alive(pid) and process_start_token(pid) == expected_token


def process_group_exists(pgid: int) -> bool:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            fields = linux_proc_stat_fields(int(entry.name))
            if fields is None:
                continue
            try:
                state, member_group = fields[0], int(fields[2])
            except (IndexError, ValueError):
                continue
            if member_group == pgid and state != "Z":
                return True
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process_group(pid: int, expected_token: str) -> bool:
    """Terminate one verified process tree/group and confirm the old worker is gone."""
    if not process_identity_matches(pid, expected_token):
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0 and process_identity_matches(pid, expected_token):
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and process_identity_matches(pid, expected_token):
            time.sleep(0.02)
        return not process_identity_matches(pid, expected_token)
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    if pgid != pid or not process_identity_matches(pid, expected_token):
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not process_group_exists(pgid):
            return True
        time.sleep(0.02)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and process_group_exists(pgid):
        time.sleep(0.02)
    return not process_group_exists(pgid)


def cancel_process_job(job_id: str, supplied_token: str | None) -> dict:
    with job_lock:
        record = read_job(job_id)
        if record is None:
            raise ApiError(404, "E_JOB_NOT_FOUND", "job was not found")
        token = (supplied_token or "").strip().strip('"')
        if not token:
            raise ApiError(428, "E_JOB_TOKEN_REQUIRED", "job updated_at token is required")
        if not secrets.compare_digest(token, str(record.get("updated_at", ""))):
            raise ApiError(409, "E_JOB_TOKEN_MISMATCH", "job changed since it was loaded", {"job": record})
        if record.get("state") == "canceled":
            return record
        if record.get("state") not in {"queued", "submitted", "running"}:
            if record.get("kind") == "generate" and record.get("state") == "awaiting_pick":
                pass
            else:
                raise ApiError(409, "E_JOB_NOT_RUNNING", "only an active job can be canceled", {"job": record})
        if record.get("kind") == "generate":
            provider = CourierProvider(str(record.get("scene_uid", "")), str(record.get("provider", "")))
            canceled = provider.cancel(record)
            scene_uid = str(canceled.get("scene_uid", ""))
            with timeline_lock:
                timeline = load_timeline()
                scene = find_scene(timeline, scene_uid)
                if scene and scene.get("status") == "generating":
                    scene["status"] = "draft" if scene.get("active_take_uid") else "planned"
                    normalized, errors, _warnings = normalize_timeline(timeline)
                    if not errors and normalized is not None:
                        backup_and_write(TIMELINE, normalized, "backups", "timeline")
            return canceled
        pid = record.get("pid")
        expected_token = record.get("start_token")
        current_token = process_start_token(pid) if isinstance(pid, int) and pid_is_alive(pid) else None
        if not isinstance(pid, int) or not expected_token or current_token != expected_token:
            raise ApiError(409, "E_JOB_IDENTITY_MISMATCH", "job worker identity could not be verified")
        if os.name != "nt":
            try:
                if os.getpgid(pid) != pid:
                    raise ApiError(409, "E_JOB_GROUP_UNVERIFIED", "job does not own a dedicated process group")
            except ProcessLookupError as exc:
                raise ApiError(409, "E_JOB_IDENTITY_MISMATCH", "job worker is no longer running") from exc
        canceling_jobs.add(job_id)
        record.update({"message": "canceling", "updated_at": utc_now()})
        write_job(record)
        for state in scene_jobs.values():
            if state.get("job_id") == job_id:
                state["ok"] = False
    try:
        terminated = terminate_process_group(pid, expected_token)
    except (OSError, subprocess.SubprocessError):
        terminated = False
    with job_lock:
        canceling_jobs.discard(job_id)
        record = read_job(job_id) or record
        now = utc_now()
        if not terminated:
            if record.get("state") in {"queued", "submitted", "running"}:
                record.update({
                    "state": "unknown", "message": "cancellation could not be confirmed",
                    "updated_at": now, "completed_at": now,
                    "error": {"code": "E_JOB_CANCEL_FAILED", "message": "worker may still be running"},
                })
                write_job(record)
            raise ApiError(500, "E_JOB_CANCEL_FAILED", "job cancellation could not be confirmed", {"job": record})
        if record.get("state") not in {"queued", "submitted", "running"}:
            raise ApiError(409, "E_JOB_NOT_RUNNING", "job finished before cancellation completed", {"job": record})
        record.update({
            "state": "canceled", "message": "canceled by user", "updated_at": now,
            "completed_at": now, "error": None,
        })
        write_job(record)
        return record


def run_cut_render(style: str) -> None:
    render_state.update(running=True, log=f"rendering {style} preview...\n", ok=None)
    try:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "render_cuts.py"), PROJECT_ARG,
             "--style", style, "--mode", "preview"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(ROOT),
            encoding="utf-8", errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            render_state["log"] += line
        proc.wait()
        render_state.update(running=False, ok=proc.returncode == 0)
    except Exception as exc:  # noqa: BLE001
        render_state.update(running=False, ok=False, log=render_state["log"] + str(exc))


def start_scene_job(
    kind: str,
    command: list[str],
    cwd: Path,
    *,
    timeline_document: dict | None = None,
    **initial,
) -> str | None:
    with job_lock:
        if any(job["running"] for job in scene_jobs.values()):
            return None
        durable_records = reconcile_jobs()
        if any(
            record.get("kind") != "generate"
            and record.get("state") in {"queued", "submitted", "running"}
            for record in durable_records
        ):
            return None
        if any(
            record.get("state") == "unknown"
            and isinstance(record.get("pid"), int)
            and pid_is_alive(record["pid"])
            for record in durable_records
        ):
            return None
        if any(
            isinstance(record.get("pid"), int)
            and record.get("start_token")
            and process_identity_matches(record["pid"], record["start_token"])
            for record in durable_records
        ):
            return None
        job_id = new_uid("job")
        command = list(command)
        if timeline_document is not None:
            snapshot_path = project_write_path(JOBS_DIR / job_id / "timeline.json")
            write_json_atomic(snapshot_path, timeline_document)
            command = [str(snapshot_path) if value == "{timeline}" else value for value in command]
            initial["timeline_snapshot"] = repo_relative(snapshot_path)
        record = create_job_record(kind, command, cwd, initial, job_id=job_id)
        scene_jobs[kind].update(running=True, log="queued...\n", ok=None, progress=0, job_id=job_id, **initial)
        thread = threading.Thread(
            target=run_process_job, args=(kind, command, cwd, job_id), kwargs=initial, daemon=True
        )
        thread.start()
        return job_id


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, obj, code=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_api_error(self, error: ApiError) -> None:
        payload = {"code": error.code, "error": error.message}
        if error.details:
            payload["details"] = error.details
        self.send_json(payload, error.status)

    def send_index(self) -> None:
        """Serve the UI with a per-process API token and a same-origin fetch shim."""
        try:
            source = (EDITOR_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            self.send_json({"error": "index.html not found"}, 404)
            return
        shim = f"""<meta name=\"workbench-token\" content=\"{SESSION_TOKEN}\">
<script>
(() => {{
  const nativeFetch = window.fetch.bind(window);
  const token = document.querySelector('meta[name=\"workbench-token\"]').content;
  window.workbenchToken = token;
  window.fetch = (input, init = {{}}) => {{
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin === location.origin && url.pathname.startsWith('/api/')) {{
      const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined));
      headers.set('X-Workbench-Token', token);
      init = {{...init, headers}};
    }}
    return nativeFetch(input, init);
  }};
}})();
</script>"""
        source = source.replace("</head>", shim + "\n</head>", 1)
        body = source.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorize_api(self, path: str) -> bool:
        if path == "/api/health":
            return True
        host = self.headers.get("Host", "")
        allowed_hosts = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        if host not in allowed_hosts:
            self.send_json({"error": "invalid Host header"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{value}" for value in allowed_hosts}:
            self.send_json({"error": "invalid Origin header"}, 403)
            return False
        if not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), SESSION_TOKEN):
            self.send_json({"error": "invalid workbench session token"}, 401)
            return False
        return True

    def authorize_take_media(self) -> bool:
        host = self.headers.get("Host", "")
        allowed_hosts = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        if host not in allowed_hosts:
            self.send_json({"error": "invalid Host header"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{value}" for value in allowed_hosts}:
            self.send_json({"error": "invalid Origin header"}, 403)
            return False
        token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if not secrets.compare_digest(token, SESSION_TOKEN):
            self.send_json({"error": "invalid workbench session token"}, 401)
            return False
        return True

    def require_timeline_match(self) -> bool:
        current = timeline_etag()
        supplied = self.headers.get("If-Match")
        if not supplied:
            self.send_json({
                "code": "E_ETAG_REQUIRED",
                "error": "If-Match is required for timeline mutations",
                "current_etag": current,
                "server_doc": load_timeline(),
            }, 428, {"ETag": f'"{current}"'})
            return False
        candidate = supplied.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        candidate = candidate.strip('"')
        if candidate != current:
            self.send_json({
                "code": "E_ETAG_MISMATCH",
                "error": "timeline changed since it was loaded",
                "current_etag": current,
                "server_doc": load_timeline(),
            }, 409, {"ETag": f'"{current}"'})
            return False
        return True

    def save_timeline_document(self, value) -> None:
        timeline, errors, warnings = normalize_timeline(value)
        with timeline_lock:
            if not self.require_timeline_match():
                return
            immutable_errors = take_immutability_errors(load_timeline(), timeline) if timeline else []
            if errors or immutable_errors:
                self.send_json({
                    "code": "E_TAKE_IMMUTABLE" if immutable_errors else "E_TIMELINE_INVALID",
                    "error": "timeline validation failed",
                    "errors": errors + immutable_errors,
                    "warnings": warnings,
                    "issues": project_validation(timeline) if timeline else [],
                }, 400)
                return
            assert timeline is not None
            backup = backup_and_write(TIMELINE, timeline, "backups", "timeline")
            etag = timeline_etag()
            issues = project_validation(timeline)
        self.send_json({
            "saved": True,
            "backup": backup,
            "timeline": timeline,
            "etag": etag,
            "issues": issues,
            "warnings": warnings,
        }, headers={"ETag": f'"{etag}"'})

    @staticmethod
    def find_scene(timeline: dict, scene_uid: str) -> dict | None:
        return find_scene(timeline, scene_uid)

    def handle_scene_revise(self, scene_uid: str, body: dict) -> None:
        prompt = str(body.get("prompt", "")).strip()
        provider_hint = str(body.get("provider_hint", "")).strip().lower()
        try:
            duration_s = float(body.get("duration_s"))
        except (TypeError, ValueError):
            self.send_json({"code": "E_GENERATE_SPEC", "error": "duration_s must be a number"}, 400)
            return
        if not prompt or len(prompt) > 4000:
            self.send_json({
                "code": "E_GENERATE_SPEC",
                "error": "prompt must be between 1 and 4000 characters",
            }, 400)
            return
        if not math.isfinite(duration_s) or duration_s <= 0 or duration_s > 600:
            self.send_json({
                "code": "E_GENERATE_SPEC",
                "error": "duration_s must be greater than 0 and no more than 600",
            }, 400)
            return
        if provider_hint not in {"fable", "hyperframe", "either"}:
            self.send_json({
                "code": "E_GENERATE_SPEC",
                "error": "provider_hint must be fable, hyperframe, or either",
            }, 400)
            return

        with timeline_lock:
            if not self.require_timeline_match():
                return
            timeline = load_timeline()
            scene = self.find_scene(timeline, scene_uid)
            if scene is None:
                self.send_json({"code": "E_SCENE_NOT_FOUND", "error": "scene was not found"}, 404)
                return
            provider = str(scene.get("engine", ""))
            if provider not in {"fable", "hyperframe"}:
                self.send_json({
                    "code": "E_ENGINE_ACTION",
                    "error": "generation is available only for fable or hyperframe scenes",
                }, 400)
                return
            if provider_hint not in {provider, "either"}:
                self.send_json({
                    "code": "E_GENERATE_SPEC",
                    "error": "provider_hint must match the selected scene engine or be either",
                }, 400)
                return
            scene["status"] = "generating"
            normalized, errors, warnings = normalize_timeline(timeline)
            if errors or normalized is None:
                self.send_json({
                    "code": "E_TIMELINE_INVALID",
                    "error": "generation job could not be attached to this timeline",
                    "errors": errors,
                    "warnings": warnings,
                }, 400)
                return
            spec = {
                "prompt": prompt,
                "provider_hint": provider_hint,
                "duration_s": round(duration_s, 3),
            }
            record = None
            try:
                record = CourierProvider(scene_uid, provider).submit(spec)
                backup = backup_and_write(TIMELINE, normalized, "backups", "timeline")
            except (ApiError, OSError) as error:
                if record is not None:
                    (JOBS_DIR / f"{record['job_id']}.json").unlink(missing_ok=True)
                    shutil.rmtree(INBOX_DIR / record["job_id"], ignore_errors=True)
                self.send_api_error(
                    error if isinstance(error, ApiError)
                    else ApiError(500, "E_GENERATE_SUBMIT", "generation job could not be submitted", str(error))
                )
                return
            etag = timeline_etag()
            saved_scene = self.find_scene(normalized, scene_uid)
            issues = project_validation(normalized)
        self.send_json({
            "submitted": True,
            "job": record,
            "job_id": record["job_id"],
            "scene": saved_scene,
            "timeline": normalized,
            "backup": backup,
            "etag": etag,
            "issues": issues,
            "warnings": warnings,
        }, 202, {"ETag": f'"{etag}"'})

    def handle_take_import(self, scene_uid: str) -> None:
        # Reject stale editors before they send a large body, then check the
        # same precondition again when the conformed artifact is committed.
        with timeline_lock:
            if not self.require_timeline_match():
                return
            initial_timeline = load_timeline()
            initial_scene = self.find_scene(initial_timeline, scene_uid)
            if initial_scene is None:
                self.send_json({"code": "E_SCENE_NOT_FOUND", "error": "scene was not found"}, 404)
                return
            scene_snapshot = json.loads(json.dumps(initial_scene))

        temp_root = project_write_path(PROJECT / "work" / "tmp")
        temp_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=temp_root, prefix=".take-import-") as folder:
                staging = Path(folder)
                content_type = self.headers.get("Content-Type", "")
                if content_type.lower().startswith("multipart/form-data"):
                    source, original_name, fields = parse_multipart_upload(self, staging)
                elif content_type.lower().startswith("application/json"):
                    body = self.read_body()
                    supplied_cli_token = self.headers.get("X-Workbench-CLI-Token", "")
                    if not secrets.compare_digest(supplied_cli_token, CLI_IMPORT_TOKEN):
                        raise ApiError(403, "E_PATH_IMPORT_FORBIDDEN", "server-side path import is CLI-only")
                    if body is None or not isinstance(body.get("path"), str):
                        raise ApiError(400, "E_PATH_REQUIRED", "CLI path import requires a path")
                    resolved, outside = resolve_persisted_path(ROOT, PROJECT, body["path"])
                    if outside or resolved is None or not resolved.is_file():
                        raise ApiError(400, "E_ASSET_OUTSIDE_PROJECT", "import path must be an existing project or media file")
                    suffix = resolved.suffix.lower()
                    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                        suffix = ".bin"
                    source = staging / f"upload{suffix}"
                    shutil.copy2(resolved, source)
                    original_name = resolved.name
                    fields = {
                        "note": str(body.get("note", "")),
                        "class_hint": str(body.get("class_hint", "")),
                    }
                else:
                    raise ApiError(415, "E_MULTIPART_REQUIRED", "take import requires multipart/form-data")

                note = fields.get("note", "").strip()
                class_hint = fields.get("class_hint", "").strip()
                if len(note) > 4000:
                    raise ApiError(400, "E_NOTE_TOO_LONG", "take note must be 4000 characters or fewer")
                if len(class_hint) > 128:
                    raise ApiError(400, "E_CLASS_HINT_INVALID", "class_hint must be 128 characters or fewer")

                with conform_slots:
                    artifact, profile, probe = conform_take(source, staging, scene_snapshot, initial_timeline)
                digest = sha256_file(artifact)
                self.commit_take_import(
                    scene_uid, source, original_name, artifact, digest,
                    profile, probe, note, class_hint,
                )
        except ApiError as error:
            self.send_api_error(error)
        except OSError as error:
            self.send_api_error(ApiError(500, "E_IMPORT_IO", "take import failed while writing files", str(error)))

    def commit_take_import(
        self,
        scene_uid: str,
        source: Path,
        original_name: str,
        artifact: Path,
        digest: str,
        profile: str,
        probe: dict,
        note: str,
        class_hint: str,
    ) -> None:
        with timeline_lock:
            if not self.require_timeline_match():
                return
            timeline = load_timeline()
            scene = self.find_scene(timeline, scene_uid)
            if scene is None:
                self.send_json({"code": "E_SCENE_NOT_FOUND", "error": "scene was not found"}, 404)
                return

            takes = scene.get("takes") if isinstance(scene.get("takes"), list) else []
            scene["takes"] = takes
            existing = next((
                take for take in takes
                if isinstance(take, dict) and secrets.compare_digest(str(take.get("sha256", "")), digest)
            ), None)
            if existing is not None:
                etag = timeline_etag()
                self.send_json({
                    "created": False,
                    "deduped": True,
                    "take": existing,
                    "scene": scene,
                    "timeline": timeline,
                    "etag": etag,
                }, headers={"ETag": f'"{etag}"'})
                return

            final_dir = None
            for _ in range(4):
                take_uid = new_uid("take")
                candidate = project_write_path(
                    PROJECT / "work" / "generated" / scene_uid / take_uid
                )
                try:
                    candidate.mkdir(parents=True, exist_ok=False)
                    final_dir = candidate
                    break
                except FileExistsError:
                    continue
            if final_dir is None:
                self.send_json({"code": "E_TAKE_ID_COLLISION", "error": "could not allocate a take id"}, 500)
                return

            source_suffix = source.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", source_suffix):
                source_suffix = ".bin"
            artifact_suffix = artifact.suffix.lower()
            final_source = final_dir / f"source{source_suffix}"
            final_artifact = final_dir / f"asset{artifact_suffix}"
            timeline_written = False
            try:
                os.replace(source, final_source)
                os.replace(artifact, final_artifact)
                provenance = {
                    "provider": "media",
                    "spec": {"original_filename": original_name},
                }
                if class_hint:
                    provenance["spec"]["class_hint"] = class_hint
                if note:
                    provenance["note"] = note
                take = {
                    "take_uid": take_uid,
                    "file": repo_relative(final_artifact),
                    "source_file": repo_relative(final_source),
                    "sha256": digest,
                    "created_at": utc_now(),
                    "conform_profile": profile,
                    "probe": probe,
                    "provenance": provenance,
                }
                takes.append(take)
                normalized, errors, warnings = normalize_timeline(timeline)
                if errors or normalized is None:
                    raise ApiError(400, "E_TIMELINE_INVALID", "take could not be attached to this timeline", errors)
                backup = backup_and_write(TIMELINE, normalized, "backups", "timeline")
                timeline_written = True
                etag = timeline_etag()
                saved_scene = self.find_scene(normalized, scene_uid)
                issues = project_validation(normalized)
            except Exception:
                if not timeline_written:
                    shutil.rmtree(final_dir, ignore_errors=True)
                raise

        self.send_json({
            "created": True,
            "deduped": False,
            "take": take,
            "scene": saved_scene,
            "timeline": normalized,
            "backup": backup,
            "etag": etag,
            "issues": issues,
            "warnings": warnings,
        }, 201, {"ETag": f'"{etag}"'})

    def handle_take_promote(self, scene_uid: str, take_uid: str) -> None:
        with timeline_lock:
            if not self.require_timeline_match():
                return
            timeline = load_timeline()
            scene = self.find_scene(timeline, scene_uid)
            if scene is None:
                self.send_json({"code": "E_SCENE_NOT_FOUND", "error": "scene was not found"}, 404)
                return
            take = next((
                item for item in scene.get("takes", [])
                if isinstance(item, dict) and item.get("take_uid") == take_uid
            ), None)
            if take is None:
                self.send_json({"code": "E_TAKE_NOT_FOUND", "error": "take was not found"}, 404)
                return
            scene["active_take_uid"] = take_uid
            if scene.get("status") in {"planned", "generating"}:
                scene["status"] = "draft"
            derive_legacy_fields(scene)
            normalized, errors, warnings = normalize_timeline(timeline)
            if errors or normalized is None:
                self.send_json({
                    "code": "E_TIMELINE_INVALID",
                    "error": "take could not be promoted",
                    "errors": errors,
                    "warnings": warnings,
                }, 400)
                return
            backup = backup_and_write(TIMELINE, normalized, "backups", "timeline")
            etag = timeline_etag()
            saved_scene = self.find_scene(normalized, scene_uid)
            issues = project_validation(normalized)
        with job_lock:
            for record in read_jobs():
                if record.get("kind") == "generate" and any(
                    isinstance(candidate, dict) and candidate.get("take_uid") == take_uid
                    for candidate in record.get("candidates", [])
                ):
                    for candidate in record.get("candidates", []):
                        if isinstance(candidate, dict):
                            candidate["picked"] = candidate.get("take_uid") == take_uid
                    now = utc_now()
                    record.update({
                        "state": "succeeded",
                        "progress": 1,
                        "message": "candidate picked and promoted",
                        "resolved_candidate_count": len(record.get("candidates", [])),
                        "updated_at": now,
                        "completed_at": now,
                        "error": None,
                    })
                    write_job(record)
        self.send_json({
            "promoted": True,
            "take": take,
            "scene": saved_scene,
            "timeline": normalized,
            "backup": backup,
            "etag": etag,
            "issues": issues,
            "warnings": warnings,
        }, headers={"ETag": f'"{etag}"'})

    def send_take_media(self, path: str) -> None:
        match = re.fullmatch(r"/media/take/(scn_[A-Za-z0-9]{8,32})/(take_[A-Za-z0-9]{8,32})", path)
        if not match:
            self.send_json({"error": "invalid take preview path"}, 400)
            return
        scene_uid, take_uid = match.groups()
        scene = self.find_scene(load_timeline(), scene_uid)
        take = next((
            item for item in (scene or {}).get("takes", [])
            if isinstance(item, dict) and item.get("take_uid") == take_uid
        ), None)
        resolved, outside = resolve_persisted_path(ROOT, PROJECT, take.get("file") if take else "")
        if take is None or outside or resolved is None or not resolved.is_file():
            self.send_json({"error": "take media was not found"}, 404)
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_file_ranged(resolved, content_type)

    def send_job_media(self, path: str) -> None:
        match = re.fullmatch(r"/media/job/(job_[A-Za-z0-9]{8,32})", path)
        record = read_job(match.group(1)) if match else None
        if not record or record.get("state") != "succeeded":
            self.send_json({"error": "job output is not available"}, 409 if record else 404)
            return
        artifacts = record.get("expected_artifacts", []) if record else []
        artifact = artifacts[0] if artifacts else ""
        resolved, outside = resolve_persisted_path(ROOT, PROJECT, artifact)
        if outside or resolved is None or not resolved.is_file():
            self.send_json({"error": "job output was not found"}, 404)
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_file_ranged(resolved, content_type)

    def send_master_media(self) -> None:
        timeline, _ = timeline_snapshot()
        resolved, outside = resolve_persisted_path(ROOT, PROJECT, timeline.get("master", ""))
        if outside or resolved is None or not resolved.is_file():
            self.send_json({"error": "master media was not found"}, 404)
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.send_file_ranged(resolved, content_type)

    def send_file_ranged(self, path: Path, ctype: str):
        if not path.exists():
            self.send_json({"error": f"{path.name} not found"}, 404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        partial = False
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                if not a and not b:
                    raise ValueError
                if "," in b:
                    raise ValueError
                if a:
                    start = int(a)
                    if start < 0 or start >= size:
                        raise ValueError
                else:
                    suffix = int(b)
                    if suffix <= 0:
                        raise ValueError
                    start = max(0, size - suffix)
                if a and b:
                    end = min(int(b), size - 1)
                if end < start:
                    raise ValueError
                partial = True
            except ValueError:
                self.send_json(
                    {"error": "invalid or unsatisfiable byte range"},
                    416,
                    {"Content-Range": f"bytes */{size}"},
                )
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(1 << 20, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionAbortedError, BrokenPipeError):
                    return
                remaining -= len(chunk)

    def read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > MAX_JSON_BODY_BYTES:
                self.close_connection = True
                return None
            raw = self.rfile.read(length)
            if len(raw) != length:
                self.close_connection = True
                return None
            body = json.loads(raw or b"{}")
            return body if isinstance(body, dict) else None
        except (ValueError, json.JSONDecodeError):
            self.close_connection = True
            return None

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            self.send_index()
        elif path == "/api/health":
            self.send_json({"ok": True, "service": "video-workbench"})
        elif path.startswith("/api/") and not self.authorize_api(path):
            return
        elif path in STATIC:
            self.send_file_ranged(*STATIC[path])
        elif path == "/api/data":
            if not CUTS.exists() or not EDITOR_MANIFEST.exists():
                self.send_json({
                    "prepared": False,
                    "error": "cut workspace is not prepared",
                    "hint": f"run: python tools/make_proxy.py {PROJECT_ARG}",
                })
                return
            self.send_json({
                "prepared": True,
                "cuts": read_json(CUTS),
                "manifest": read_json(EDITOR_MANIFEST),
                "fps": proxy_fps(),
            })
        elif path == "/api/render/status":
            self.send_json(render_state)
        elif path == "/api/project":
            timeline, etag = timeline_snapshot()
            issues = project_validation(timeline)
            self.send_json({
                "project": PROJECT.name,
                "project_path": str(PROJECT),
                "timeline": timeline,
                "etag": etag,
                "validation": {
                    "blocking": sum(item["severity"] == "E" for item in issues),
                    "warnings": sum(item["severity"] == "W" for item in issues),
                },
            }, headers={"ETag": f'"{etag}"'})
        elif path == "/api/project/validate":
            timeline, etag = timeline_snapshot()
            self.send_json({"etag": etag, "issues": project_validation(timeline)}, headers={"ETag": f'"{etag}"'})
        elif path == "/api/scenes":
            catalog = remotion_catalog()
            timeline, etag = timeline_snapshot()
            issues = project_validation(timeline)
            warnings = []
            if not catalog:
                warnings.append("Remotion catalog is empty; run npm run gen in remotion/")
            self.send_json({
                "project": PROJECT.name,
                "project_path": str(PROJECT),
                "timeline_path": str(TIMELINE),
                "timeline_exists": TIMELINE.exists(),
                "duration": float(timeline.get("preview", {}).get("end_s") or project_duration()),
                "timeline": timeline,
                "etag": etag,
                "compositions": catalog,
                "issues": issues,
                "warnings": warnings,
                "word_ticks": transcript_word_ticks(),
            }, headers={"ETag": f'"{etag}"'})
        elif path == "/api/scenes/jobs":
            self.send_json(scene_jobs)
        elif path == "/api/jobs":
            self.send_json({"jobs": poll_courier_jobs()})
        elif re.fullmatch(r"/api/jobs/job_[A-Za-z0-9]{8,32}", path):
            job = reconcile_job(path.rsplit("/", 1)[-1])
            if job and job.get("kind") == "generate":
                job = CourierProvider(
                    str(job.get("scene_uid", "")), str(job.get("provider", ""))
                ).poll(job)
            self.send_json({"job": job} if job else {"error": "job was not found"}, 200 if job else 404)
        elif path.startswith("/media/take/"):
            if not self.authorize_take_media():
                return
            self.send_take_media(path)
        elif path == "/media/master":
            if not self.authorize_take_media():
                return
            self.send_master_media()
        elif path.startswith("/media/job/"):
            if not self.authorize_take_media():
                return
            self.send_job_media(path)
        elif path.startswith("/media/remotion-preview/"):
            name = Path(path).name
            if name != path.rsplit("/", 1)[-1] or not name.endswith(".png"):
                self.send_json({"error": "invalid preview path"}, 400)
                return
            self.send_file_ranged(REMOTION_DIR / "out" / "qa" / name, "image/png")
        elif path in MEDIA:
            self.send_file_ranged(*MEDIA[path])
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if not self.authorize_api(path):
            return
        import_match = re.fullmatch(
            r"/api/scene/(scn_[A-Za-z0-9]{8,32})/takes/import", path
        )
        if import_match:
            self.handle_take_import(import_match.group(1))
            return
        revise_match = re.fullmatch(
            r"/api/scene/(scn_[A-Za-z0-9]{8,32})/revise", path
        )
        if revise_match:
            body = self.read_body()
            if body is None:
                self.send_json({"error": "invalid JSON body"}, 400)
                return
            self.handle_scene_revise(revise_match.group(1), body)
            return
        promote_match = re.fullmatch(
            r"/api/scene/(scn_[A-Za-z0-9]{8,32})/takes/(take_[A-Za-z0-9]{8,32})/promote", path
        )
        if promote_match:
            if self.read_body() is None:
                self.send_json({"error": "invalid JSON body"}, 400)
                return
            self.handle_take_promote(*promote_match.groups())
            return
        cancel_match = re.fullmatch(r"/api/jobs/(job_[A-Za-z0-9]{8,32})/cancel", path)
        if cancel_match:
            if self.read_body() is None:
                self.send_json({"error": "invalid JSON body"}, 400)
                return
            try:
                record = cancel_process_job(
                    cancel_match.group(1),
                    self.headers.get("If-Match") or self.headers.get("If-Unmodified-Since"),
                )
                self.send_json({"canceled": True, "job": record})
            except ApiError as error:
                self.send_api_error(error)
            return
        body = self.read_body()
        if body is None:
            self.send_json({"error": "invalid JSON body"}, 400)
            return
        if path == "/api/save":
            data = body.get("cuts")
            if not isinstance(data, dict) or "clips" not in data:
                self.send_json({"error": "invalid cuts payload"}, 400)
                return
            backup = backup_and_write(CUTS, data, "backups", "cuts")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            changes = body.get("changes") or []
            if changes:
                log = PROJECT / "work" / "analysis" / "changes.log"
                with open(log, "a", encoding="utf-8") as handle:
                    for change in changes:
                        handle.write(f"{stamp} {change}\n")
            self.send_json({"saved": True, "backup": backup})
            return

        if path == "/api/render":
            if render_state["running"]:
                self.send_json({"error": "cut render already running"}, 409)
                return
            style = body.get("style", "tight")
            if style not in {"tight", "natural"}:
                self.send_json({"error": "style must be tight or natural"}, 400)
                return
            threading.Thread(target=run_cut_render, args=(style,), daemon=True).start()
            self.send_json({"started": True})
            return

        if path == "/api/bake/range":
            try:
                from_s = float(body.get("from_s"))
                to_s = float(body.get("to_s"))
            except (TypeError, ValueError):
                self.send_json({"error": "from_s and to_s must be numbers"}, 400)
                return
            if not math.isfinite(from_s) or not math.isfinite(to_s) or from_s < 0 or to_s <= from_s:
                self.send_json({"error": "range must satisfy 0 <= from_s < to_s"}, 400)
                return
            with timeline_lock:
                if not self.require_timeline_match():
                    return
                if not TIMELINE.exists():
                    self.send_json({"error": "save the scene timeline before baking"}, 400)
                    return
                timeline = load_timeline()
                preview_end = float(timeline.get("preview", {}).get("end_s", 0))
                if to_s > preview_end + 1e-6:
                    self.send_json({"error": f"to_s must be <= preview end ({preview_end:g})"}, 400)
                    return
                blocking = [item for item in project_validation(timeline) if item.get("severity") == "E"]
                if blocking:
                    self.send_json({"error": "bake blocked by project validation", "issues": blocking}, 409)
                    return
                from_s = round(from_s, 6)
                to_s = round(to_s, 6)
                output = range_output_path(from_s, to_s)
                timeline_etag_value = timeline_etag()
                command = [
                    sys.executable, str(ROOT / "tools" / "bake.py"), "{timeline}",
                    "--from", str(from_s), "--end", str(to_s),
                ]
                job_id = start_scene_job(
                    "bake", command, ROOT, timeline_document=timeline,
                    output=repo_relative(output), range={"from_s": from_s, "to_s": to_s},
                    timeline_etag=timeline_etag_value,
                )
            if not job_id:
                self.send_json({"error": "another scene job is already running"}, 409)
                return
            self.send_json({
                "started": True,
                "job_id": job_id,
                "output": repo_relative(output),
                "media_url": f"/media/job/{job_id}",
            }, 202)
            return

        if path == "/api/scenes/save":
            self.save_timeline_document(body.get("timeline"))
            return

        if path == "/api/scenes/still":
            if not self.require_timeline_match():
                return
            scene_id = str(body.get("id", ""))
            catalog = {str(item.get("id")): item for item in remotion_catalog()}
            if scene_id not in catalog:
                self.send_json({"error": "unknown Remotion composition"}, 400)
                return
            try:
                frame = int(body.get("frame", 0))
            except (TypeError, ValueError):
                self.send_json({"error": "frame must be an integer"}, 400)
                return
            last = max(0, round(float(catalog[scene_id]["durationInSeconds"]) * float(catalog[scene_id]["fps"])) - 1)
            frame = max(0, min(frame, last))
            filename = f"{scene_id}-f{frame:04d}.png"
            url = f"/media/remotion-preview/{filename}"
            command = ["node", "scripts/frames.mjs", scene_id, str(frame), "--scale=0.5"]
            job_id = start_scene_job(
                "still", command, REMOTION_DIR, id=scene_id, url=url,
                output=repo_relative(REMOTION_DIR / "out" / "qa" / filename),
            )
            if not job_id:
                self.send_json({"error": "another scene job is already running"}, 409)
                return
            self.send_json({"started": True, "job_id": job_id, "url": url, "frame": frame})
            return

        if path == "/api/scenes/render":
            if not self.require_timeline_match():
                return
            scene_id = str(body.get("id", ""))
            catalog = {str(item.get("id")): item for item in remotion_catalog()}
            if scene_id not in catalog:
                self.send_json({"error": "unknown Remotion composition"}, 400)
                return
            scale = body.get("scale", 1)
            if scale not in (0.5, 1, 2):
                self.send_json({"error": "scale must be 0.5, 1, or 2"}, 400)
                return
            command = ["node", "scripts/render-all.mjs", f"--scale={scale}", scene_id]
            extension = ".mov" if catalog[scene_id].get("transparent") else ".mp4"
            job_id = start_scene_job(
                "render", command, REMOTION_DIR, id=scene_id,
                output=repo_relative(REMOTION_DIR / "out" / f"{scene_id}{extension}"),
            )
            if not job_id:
                self.send_json({"error": "another scene job is already running"}, 409)
                return
            self.send_json({"started": True, "job_id": job_id})
            return

        if path == "/api/scenes/bake":
            with timeline_lock:
                if not self.require_timeline_match():
                    return
                if not TIMELINE.exists():
                    self.send_json({"error": "save the scene timeline before baking"}, 400)
                    return
                timeline = load_timeline()
                blocking = [item for item in project_validation(timeline) if item.get("severity") == "E"]
                if blocking:
                    self.send_json({
                        "error": "bake blocked by project validation",
                        "issues": blocking,
                    }, 409)
                    return
                output = timeline.get("preview", {}).get("out")
                command = [sys.executable, str(ROOT / "tools" / "bake.py"), "{timeline}"]
                job_id = start_scene_job(
                    "bake", command, ROOT, timeline_document=timeline,
                    output=output, timeline_etag=timeline_etag(),
                )
            if not job_id:
                self.send_json({"error": "another scene job is already running"}, 409)
                return
            self.send_json({"started": True, "job_id": job_id, "output": output})
            return

        self.send_json({"error": "not found"}, 404)

    def do_PUT(self):
        path = unquote(urlparse(self.path).path)
        if not self.authorize_api(path):
            return
        if path != "/api/timeline":
            self.send_json({"error": "not found"}, 404)
            return
        body = self.read_body()
        if body is None:
            self.send_json({"error": "invalid JSON body"}, 400)
            return
        self.save_timeline_document(body.get("timeline", body))


if __name__ == "__main__":
    reconcile_jobs()
    threading.Thread(target=courier_poller, daemon=True).start()
    print(f"Video workbench for {PROJECT}  ->  http://localhost:{PORT}")
    print(f"Session token: {SESSION_TOKEN}")
    print(f"CLI import token: {CLI_IMPORT_TOKEN}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
