#!/usr/bin/env python3
"""
capture_web.py — state-capture screencasts for video beats.

Drives a real browser (Playwright Chromium) through a choreography spec and saves
per-step SCREENSHOTS + a manifest.json. The shots are replayed by
remotion/src/lib/screencast.tsx (fake-screencast pages) in sync with narration —
we capture states, not video.

Two page sources, freely mixed:
  - any local/remote URL (a running app, e.g. your own app at 127.0.0.1:8000);
  - REAL VS Code via `code serve-web`: the tool boots
    the server, polls 127.0.0.1 (NOT localhost) until ready, and opens the
    workbench with ?folder=<path>. Give it a server_data_dir: the tool pre-seeds
    settings there (no welcome tab / no trust prompt), and pair it with a
    browser_profile so browser-side workbench state persists across runs.

Spec (JSON):
{
  "viewport": [1920, 1080],
  "out_dir": "media/projects/video-1/myapp",           // repo-relative (Remotion's public root is media/)
  "base_url": "http://127.0.0.1:8000",                 // optional; goto may be relative
  "browser_profile": ".../work/pw-profile",            // optional persistent Chromium profile
  "serve_web": {                                       // optional: boot VS Code serve-web
    "folder": "path/to/the-project-to-show",           // repo-relative or absolute
    "port": 9400,
    "server_data_dir": "videos/video-1/work/vscode-sdd",
    "settings": {"workbench.colorTheme": "Default Dark Modern"},  // merged into User/settings.json
    "goto_workbench": true                             // open it as the page (default true)
  },
  "steps": [
    {"goto": "/"},
    {"wait": 800},                                     // ms
    {"wait_for": "css=.report-table"},
    {"click": "text=Export PDF"},
    {"fill": ["#id_prompt", "some text"]},
    {"press": "Control+Enter"},
    {"scroll": 400},                                   // px down from top
    {"eval": "document.querySelector('.x').remove()"},
    {"shot": "dashboard-initial", "url_label": "yourapp.com/reports", "title": "Reports"}
  ]
}

Every "shot" saves <out_dir>/<name>.png and appends a manifest entry
{name, file, url_label, title} -> <out_dir>/manifest.json, ready to map onto
ScreencastPage objects (img/url/tabTitle) in the shot TSX.

While it drives the choreography it ALSO harvests, for every interaction step
(click / fill / scroll), the target element's on-screen geometry: Playwright
knows each element's exact bounding box, so we record its CENTER as VIEWPORT
FRACTIONS (cx, cy in 0..1 — the same coordinate convention the screencast engine
uses for cursor keys), the viewport size, and which captured shot the interaction
sits between. This lands in the manifest as a new top-level shape:

  {"pages": [<the old shot entries>], "interactions": [<per-step geometry>]}

so tools/gen_screencast.py can compile the manifest straight into a tuned TSX
shot (cursor path + clicks) with no by-hand fraction math. The manifest stays
BACKWARD-COMPATIBLE: `pages` holds exactly the objects the tool always wrote, and
any old consumer that json.load()s a list still works via the compatibility notes
below (see write_manifest). Interaction harvesting is best-effort — if an element
has no box (detached / off-screen) the entry is still recorded with null coords,
and the whole feature is skipped cleanly when playwright isn't installed, exactly
as capture itself is.

Usage:
  venv/Scripts/python.exe tools/capture_web.py --spec <spec.json> [--headed] [--keep-server]
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rp(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def _clamp01(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def element_center_fraction(page, selector, vw, vh):
    """Center of `selector`'s bounding box as VIEWPORT FRACTIONS (cx, cy in 0..1).

    Returns (cx, cy) or (None, None) when the element is missing or has no box
    (detached / display:none / zero-size). Best-effort: any Playwright hiccup
    degrades to (None, None) rather than aborting the capture — the geometry is a
    convenience for gen_screencast.py, never a reason to lose a screenshot.

    The image the screencast engine paints fills the page region fit-to-width and
    top-aligned, so a viewport-fraction center maps 1:1 onto a cursor keyframe.
    """
    try:
        el = page.query_selector(selector)
        if el is None:
            return None, None
        box = el.bounding_box()  # {x, y, width, height} in CSS px, or None
    except Exception:
        return None, None
    if not box or not vw or not vh:
        return None, None
    cx = (box["x"] + box["width"] / 2.0) / float(vw)
    cy = (box["y"] + box["height"] / 2.0) / float(vh)
    return _clamp01(cx), _clamp01(cy)


def wait_http(url, timeout_s=60):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def start_serve_web(cfg):
    port = int(cfg.get("port", 9400))
    folder = rp(cfg["folder"])
    cmd = ["code", "serve-web", "--port", str(port),
           "--without-connection-token", "--accept-server-license-terms"]
    sdd = cfg.get("server_data_dir")
    if sdd:
        sdd = rp(sdd)
        os.makedirs(sdd, exist_ok=True)
        cmd += ["--server-data-dir", sdd]
        # pre-seed user settings so the workbench comes up clean (no welcome
        # tab, no trust prompt) before the first boot
        user_dir = os.path.join(sdd, "data", "User")
        os.makedirs(user_dir, exist_ok=True)
        sp = os.path.join(user_dir, "settings.json")
        settings = {"workbench.startupEditor": "none",
                    "security.workspace.trust.enabled": False,
                    "workbench.tips.enabled": False}
        settings.update(cfg.get("settings", {}))
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as f:
                cur = json.load(f)
            cur.update(settings)
            settings = cur
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=1)
    print(f"serve-web: {' '.join(cmd)}")
    # `code` is a .cmd shim on Windows: run through cmd.exe with the args intact
    # (Popen(list, shell=True) would hand the args to cmd.exe, not to code).
    if os.name == "nt":
        cmd = ["cmd", "/c"] + cmd
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    if not wait_http(base, 90):
        proc.terminate()
        raise SystemExit(f"serve-web did not come up on {base}")
    # workbench URL: URI-path form ?folder=/d:/path (leading slash + lowercase
    # drive). Plain d:/path or D:/path loses the drive letter and the folder
    # opens as an error item (probed 2026-07-11).
    p = folder.replace(os.sep, "/")
    if len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[1:]
    wb = f"{base}/?folder={p}"
    print(f"serve-web ready -> {wb}")
    return proc, wb


def write_manifest(path, pages, interactions, viewport, out_dir):
    """Write the manifest in the dual shape:

        {"pages": [...old shot entries...], "interactions": [...], "viewport": [w,h], ...}

    `pages` is byte-for-byte the list the tool has always produced (same keys:
    name/file/url_label/title), just nested under a "pages" key so the new
    "interactions" array can live beside it. gen_screencast.py reads either this
    object OR a bare legacy list (a plain list is treated as pages-only), so old
    manifests still compile. Anyone who used to `json.load()` the old list should
    read `data["pages"]` when the loaded value is a dict — a one-line guard.
    """
    doc = {
        "pages": pages,
        "interactions": interactions,
        "viewport": list(viewport),
        "out_dir": out_dir,
        "schema": "capture_web/2",  # v1 was a bare list of page entries
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)


def main():
    args = sys.argv[1:]
    headed = "--headed" in args
    keep_server = "--keep-server" in args
    args = [a for a in args if a not in ("--headed", "--keep-server")]
    spec_path = None
    if "--spec" in args:
        i = args.index("--spec")
        spec_path = args[i + 1]
    if not spec_path:
        raise SystemExit("usage: capture_web.py --spec <spec.json> [--headed] [--keep-server]")

    with open(rp(spec_path), "r", encoding="utf-8") as f:
        spec = json.load(f)

    out_dir = rp(spec["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    vw, vh = spec.get("viewport", [1920, 1080])
    base_url = spec.get("base_url", "")

    server = None
    workbench = None
    if spec.get("serve_web"):
        server, workbench = start_serve_web(spec["serve_web"])

    manifest = []          # old shape: one entry per shot (unchanged)
    interactions = []      # new: per-step geometry, tagged with the shots it sits between
    last_shot = None       # name of the most recent shot captured so far
    pending = []           # interactions recorded since last_shot, awaiting their after_shot
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            # a persistent profile keeps browser-side workbench state (dismissed
            # panels etc.) across runs; plain launch when no profile is given
            profile = spec.get("browser_profile")
            vp = {"width": vw, "height": vh}
            dsf = spec.get("device_scale", 1)
            if profile:
                browser = pw.chromium.launch_persistent_context(
                    rp(profile), headless=not headed, viewport=vp, device_scale_factor=dsf)
                page = browser.pages[0] if browser.pages else browser.new_page()
            else:
                browser = pw.chromium.launch(headless=not headed)
                page = browser.new_page(viewport=vp, device_scale_factor=dsf)
            if workbench and spec["serve_web"].get("goto_workbench", True):
                page.goto(workbench)
                page.wait_for_timeout(4000)  # workbench boot

            for i, step in enumerate(spec.get("steps", [])):
                if "goto" in step:
                    url = step["goto"]
                    if url.startswith("/") and base_url:
                        url = base_url + url
                    page.goto(url)
                elif "wait" in step:
                    page.wait_for_timeout(int(step["wait"]))
                elif "wait_for" in step:
                    page.wait_for_selector(step["wait_for"], timeout=30000)
                elif "click" in step:
                    sel = step["click"]
                    # harvest the target geometry BEFORE the click navigates away
                    # (afterwards the element may be detached and box-less)
                    cx, cy = element_center_fraction(page, sel, vw, vh)
                    if step.get("optional"):
                        try:
                            page.click(sel, timeout=int(step.get("timeout", 4000)))
                        except Exception:
                            print(f"  (optional click skipped: {sel})")
                            cx = cy = None
                    else:
                        page.click(sel)
                    pending.append({"index": i, "type": "click", "selector": sel,
                                    "cx": cx, "cy": cy, "viewport": [vw, vh],
                                    "before_shot": last_shot, "after_shot": None})
                elif "fill" in step:
                    sel, val = step["fill"]
                    cx, cy = element_center_fraction(page, sel, vw, vh)
                    page.fill(sel, val)
                    pending.append({"index": i, "type": "fill", "selector": sel,
                                    "value": val, "cx": cx, "cy": cy, "viewport": [vw, vh],
                                    "before_shot": last_shot, "after_shot": None})
                elif "press" in step:
                    page.keyboard.press(step["press"])
                elif "scroll" in step:
                    y = int(step["scroll"])
                    page.evaluate(f"window.scrollTo(0, {y})")
                    # no target element for a scroll; record the scroll px (the
                    # engine's page.scroll is image-px translateY) as a hint, but no
                    # cursor target (cx/cy null so it is never treated as a click)
                    pending.append({"index": i, "type": "scroll", "selector": None,
                                    "scroll_px": y, "cx": None, "cy": None,
                                    "viewport": [vw, vh],
                                    "before_shot": last_shot, "after_shot": None})
                elif "eval" in step:
                    page.evaluate(step["eval"])
                elif "shot" in step:
                    name = step["shot"]
                    fn = os.path.join(out_dir, f"{name}.png")
                    page.screenshot(path=fn)
                    manifest.append({"name": name, "file": os.path.basename(fn),
                                     "url_label": step.get("url_label", ""),
                                     "title": step.get("title", "")})
                    # every interaction since the previous shot RESULTED IN this
                    # page: tag it so gen_screencast.py can fire the click just
                    # before this page's enterAt
                    for it in pending:
                        it["after_shot"] = name
                    interactions.extend(pending)
                    pending = []
                    last_shot = name
                    print(f"  shot [{len(manifest):02d}] {name}.png")
                else:
                    raise SystemExit(f"unknown step {i}: {step}")
            browser.close()
    finally:
        if server and not keep_server:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # interactions after the final shot never got an after_shot; keep them anyway
    interactions.extend(pending)

    mf = os.path.join(out_dir, "manifest.json")
    write_manifest(mf, manifest, interactions, [vw, vh], spec.get("out_dir", ""))
    try:
        shown = os.path.relpath(out_dir, ROOT)
    except ValueError:  # out_dir on a different drive than the repo
        shown = out_dir
    print(f"done: {len(manifest)} shot(s), {len(interactions)} interaction(s) "
          f"-> {shown}  (+ manifest.json)")


if __name__ == "__main__":
    main()
