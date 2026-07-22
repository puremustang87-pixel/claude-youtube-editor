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
  GET  /api/scenes/jobs      poll still/render/bake progress
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from hashlib import sha256
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
EDITOR_DIR = Path(__file__).resolve().parent
REMOTION_DIR = ROOT / "remotion"
REMOTION_MANIFEST = REMOTION_DIR / "src" / "shots.manifest.json"

sys.path.insert(0, str(EDITOR_DIR))
from contracts import (  # noqa: E402
    derive_legacy_fields,
    migrate_timeline,
    new_uid,
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


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


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
    "still": {"running": False, "log": "", "ok": None, "url": None, "id": None, "job_id": None},
    "render": {"running": False, "log": "", "ok": None, "id": None, "job_id": None},
    "bake": {"running": False, "log": "", "ok": None, "output": None, "job_id": None},
}
job_lock = threading.Lock()
timeline_lock = threading.Lock()


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


def read_jobs() -> list[dict]:
    records = []
    if not JOBS_DIR.exists():
        return records
    for path in sorted(JOBS_DIR.glob("job_*.json"), reverse=True):
        try:
            record = read_json(path)
            if isinstance(record, dict):
                records.append(record)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return records


def process_start_token(pid: int) -> str | None:
    """Return a PID-reuse-safe token on Linux/WSL and Windows."""
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
            return f"{pid}:{fields[21]}"
        except (OSError, IndexError):
            return None
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
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def reconcile_jobs() -> list[dict]:
    """Mark interrupted local jobs honestly after a server restart."""
    records = read_jobs()
    for record in records:
        if record.get("state") not in {"queued", "submitted", "running"}:
            continue
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
            continue
        record["updated_at"] = utc_now()
        record["completed_at"] = record["updated_at"]
        write_job(record)
    return read_jobs()


def create_job_record(kind: str, command: list[str], cwd: Path, initial: dict) -> dict:
    job_id = new_uid("job")
    now = utc_now()
    durable_kind = "bake" if kind == "bake" else "render"
    spec = {"command": command, "cwd": repo_relative(cwd), "ui_kind": kind}
    input_bytes = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = []
    output_dir = repo_relative(REMOTION_DIR / "out")
    if kind == "render" and initial.get("id"):
        expected = [f"remotion/out/{initial['id']}.mp4"]
    if kind == "bake" and initial.get("output"):
        expected = [str(initial["output"])]
        output_dir = str(Path(str(initial["output"])).parent).replace("\\", "/")
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
    write_job(record)
    return record


def run_process_job(kind: str, command: list[str], cwd: Path, job_id: str, **initial) -> None:
    state = scene_jobs[kind]
    state.update(running=True, log="", ok=None, job_id=job_id, **initial)
    record = next((item for item in read_jobs() if item.get("job_id") == job_id), None)
    try:
        env = os.environ.copy()
        if command and command[0] == "node" and "--use-system-ca" not in env.get("NODE_OPTIONS", ""):
            env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --use-system-ca").strip()
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(cwd), encoding="utf-8", errors="replace",
            env=env,
        )
        if record is not None:
            now = utc_now()
            record.update({
                "state": "running", "message": "running", "pid": proc.pid,
                "start_token": process_start_token(proc.pid), "started_at": now, "updated_at": now,
            })
            write_job(record)
        assert proc.stdout is not None
        for line in proc.stdout:
            state["log"] += line
            if len(state["log"]) > 30000:
                state["log"] = state["log"][-30000:]
        proc.wait()
        state["ok"] = proc.returncode == 0
        if record is not None:
            now = utc_now()
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
        if record is not None:
            now = utc_now()
            record.update({
                "state": "failed", "message": str(exc), "updated_at": now, "completed_at": now,
                "error": {"code": "E_PROCESS_START", "message": str(exc)},
            })
            write_job(record)
    finally:
        state["running"] = False
        with job_lock:
            pass


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


def start_scene_job(kind: str, command: list[str], cwd: Path, **initial) -> str | None:
    with job_lock:
        if any(job["running"] for job in scene_jobs.values()):
            return None
        record = create_job_record(kind, command, cwd, initial)
        job_id = record["job_id"]
        scene_jobs[kind].update(running=True, log="queued...\n", ok=None, job_id=job_id, **initial)
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
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

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
            if errors:
                self.send_json({
                    "error": "timeline validation failed",
                    "errors": errors,
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

    def send_file_ranged(self, path: Path, ctype: str):
        if not path.exists():
            self.send_json({"error": f"{path.name} not found"}, 404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                start = int(a) if a else max(0, size - int(b))
                if a and b:
                    end = min(int(b), size - 1)
            except ValueError:
                self.send_json({"error": "invalid byte range"}, 416)
                return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if rng:
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
            body = json.loads(self.rfile.read(length) or b"{}")
            return body if isinstance(body, dict) else None
        except (ValueError, json.JSONDecodeError):
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
                    "error": "cut workspace is not prepared",
                    "hint": f"run: python tools/make_proxy.py {PROJECT_ARG}",
                }, 404)
                return
            self.send_json({
                "cuts": read_json(CUTS),
                "manifest": read_json(EDITOR_MANIFEST),
                "fps": proxy_fps(),
            })
        elif path == "/api/render/status":
            self.send_json(render_state)
        elif path == "/api/project":
            timeline = load_timeline()
            etag = timeline_etag()
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
            etag = timeline_etag()
            self.send_json({"etag": etag, "issues": project_validation()}, headers={"ETag": f'"{etag}"'})
        elif path == "/api/scenes":
            catalog = remotion_catalog()
            timeline = load_timeline()
            issues = project_validation(timeline)
            warnings = []
            if not catalog:
                warnings.append("Remotion catalog is empty; run npm run gen in remotion/")
            if not PROXY.exists():
                warnings.append(f"Proxy missing; run python tools/make_proxy.py {PROJECT_ARG}")
            etag = timeline_etag()
            self.send_json({
                "project": PROJECT.name,
                "project_path": str(PROJECT),
                "timeline_path": str(TIMELINE),
                "timeline_exists": TIMELINE.exists(),
                "duration": project_duration(),
                "timeline": timeline,
                "etag": etag,
                "compositions": catalog,
                "issues": issues,
                "warnings": warnings,
            }, headers={"ETag": f'"{etag}"'})
        elif path == "/api/scenes/jobs":
            self.send_json(scene_jobs)
        elif path == "/api/jobs":
            self.send_json({"jobs": reconcile_jobs()})
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
            job_id = start_scene_job("still", command, REMOTION_DIR, id=scene_id, url=url)
            if not job_id:
                self.send_json({"error": "another scene job is already running"}, 409)
                return
            self.send_json({"started": True, "job_id": job_id, "url": url, "frame": frame})
            return

        if path == "/api/scenes/render":
            if not self.require_timeline_match():
                return
            scene_id = str(body.get("id", ""))
            catalog_ids = {str(item.get("id")) for item in remotion_catalog()}
            if scene_id not in catalog_ids:
                self.send_json({"error": "unknown Remotion composition"}, 400)
                return
            scale = body.get("scale", 1)
            if scale not in (0.5, 1, 2):
                self.send_json({"error": "scale must be 0.5, 1, or 2"}, 400)
                return
            command = ["node", "scripts/render-all.mjs", f"--scale={scale}", scene_id]
            job_id = start_scene_job("render", command, REMOTION_DIR, id=scene_id)
            if not job_id:
                self.send_json({"error": "another scene job is already running"}, 409)
                return
            self.send_json({"started": True, "job_id": job_id})
            return

        if path == "/api/scenes/bake":
            if not self.require_timeline_match():
                return
            if not TIMELINE.exists():
                self.send_json({"error": "save the scene timeline before baking"}, 400)
                return
            issues = project_validation()
            blocking = [item for item in issues if item.get("severity") == "E"]
            if blocking:
                self.send_json({
                    "error": "bake blocked by project validation",
                    "issues": blocking,
                }, 409)
                return
            output = load_timeline().get("preview", {}).get("out")
            command = [sys.executable, str(ROOT / "tools" / "bake.py"), str(TIMELINE)]
            job_id = start_scene_job("bake", command, ROOT, output=output)
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
    print(f"Video workbench for {PROJECT}  ->  http://localhost:{PORT}")
    print(f"Session token: {SESSION_TOKEN}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
