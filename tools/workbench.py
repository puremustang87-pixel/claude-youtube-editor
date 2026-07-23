"""One-command launcher for the local video workbench.

Works from Windows, macOS, Linux, and WSL. It starts the localhost server in
the current terminal and opens exactly one browser tab. Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from project_bootstrap import BootstrapError, bootstrap_project

ROOT = Path(__file__).resolve().parent.parent


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def resolve_project(value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    direct = (ROOT / raw).resolve()
    nested = (ROOT / "videos" / raw).resolve()
    if direct.exists():
        return direct
    if nested.exists():
        return nested
    return direct if raw.parts and raw.parts[0] == "videos" else nested


def is_workbench(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=0.7) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("service") == "video-workbench"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def open_browser(url: str) -> None:
    if is_wsl():
        # Use the Windows default browser directly from WSL; no PowerShell and
        # no second terminal are needed.
        for command in (
            ["wslview", url],
            ["cmd.exe", "/c", "start", "", url],
            ["explorer.exe", url],
        ):
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except FileNotFoundError:
                continue
        print(f"Open {url}")
        return
    if not webbrowser.open_new_tab(url):
        print(f"Open {url}")


def open_when_ready(base_url: str, page_url: str) -> None:
    for _ in range(80):
        if is_workbench(base_url):
            open_browser(page_url)
            return
        time.sleep(0.1)
    print(f"The browser did not open automatically. Open {page_url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the AI video workbench and open one browser tab.")
    parser.add_argument("project", nargs="?", default="video-1", help="project name/path (default: video-1)")
    parser.add_argument("--new-project", metavar="NAME", help="create and open a VO-first project")
    parser.add_argument("--vo", type=Path, help="voiceover WAV for --new-project")
    parser.add_argument("--assets", type=Path, help="optional media folder for --new-project")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="start the server without opening a browser")
    args = parser.parse_args()

    if args.new_project:
        if args.project != "video-1":
            parser.error("do not provide a positional project with --new-project")
        if args.vo is None:
            parser.error("--new-project requires --vo")
        try:
            project = bootstrap_project(args.new_project, args.vo, args.assets)
        except BootstrapError as exc:
            parser.error(str(exc))
        args.project = args.new_project
    elif args.vo is not None or args.assets is not None:
        parser.error("--vo and --assets require --new-project")
    else:
        project = resolve_project(args.project)

    port = args.port
    base_url = f"http://127.0.0.1:{port}"
    page_url = base_url + "/?workspace=scenes"
    if is_workbench(base_url):
        print(f"Workbench is already running at {page_url}")
        if not args.no_open:
            open_browser(page_url)
        return 0
    if not port_available(port):
        for candidate in range(port + 1, port + 21):
            if port_available(candidate):
                port = candidate
                break
        else:
            print(f"No available localhost port found near {args.port}.", file=sys.stderr)
            return 1
        base_url = f"http://127.0.0.1:{port}"
        page_url = base_url + "/?workspace=scenes"

    cuts = project / "work" / "analysis" / "cuts.json"
    proxy = project / "work" / "editor" / "proxy.mp4"

    print("AI Video Workbench")
    print(f"  project: {project}")
    print(f"  page:    {page_url}")
    if not cuts.exists():
        print("  mode:    Scenes only (no cuts.json yet)")
    elif not proxy.exists():
        print(f"  note:    Cut data found; build its proxy later with python3 tools/make_proxy.py {args.project}")
    else:
        print("  mode:    Cut + Scenes")
    print("  stop:    Ctrl+C")

    if not args.no_open:
        threading.Thread(target=open_when_ready, args=(base_url, page_url), daemon=True).start()

    command = [sys.executable, str(ROOT / "tools" / "editor" / "server.py"), args.project, str(port)]
    try:
        return subprocess.call(command, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
