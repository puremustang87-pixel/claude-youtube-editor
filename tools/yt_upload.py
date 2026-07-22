#!/usr/bin/env python3
"""
yt_upload.py — upload a short or long-form video to YouTube from a declarative plan.

One tool for both formats: YouTube auto-classifies a Short by its shape (vertical, ≤3 min)
+ metadata — there is no separate Shorts endpoint. The tool uploads the file, sets
title/description/tags/category, sets the custom thumbnail, and sets privacy/schedule.

DRAFT MODE (default): uploads land as PRIVATE drafts; publish or schedule
them in YouTube Studio with one click. This also sidesteps the API's unaudited-project
restriction, which force-locks every upload from an unaudited project to private anyway.
(To later schedule public directly from the API, the Google Cloud project must pass YouTube's
one-time compliance audit; then set "privacy":"private" + a "publishAt" RFC3339 time.)

Setup (one-time, needs your browser — see tools/yt_upload_SETUP.md):
  1. Google Cloud project + enable "YouTube Data API v3".
  2. OAuth "Desktop app" credential → download as .youtube/client_secret.json.
  3. `python tools/yt_upload.py auth`  (opens a browser once; saves .youtube/token.json)

Usage:
  python tools/yt_upload.py auth
  python tools/yt_upload.py upload shorts/ch-3-honeypot/publish.json
  python tools/yt_upload.py upload <plan> --dry-run     # validate plan + preview + A/V gate, no API call
  python tools/yt_upload.py upload <plan> --strict      # abort if v:0/a:0 durations drift >50ms
  python tools/yt_upload.py whoami                        # confirm which channel is authorized

Last-gate A/V check: before upload the tool ffprobes the file and compares v:0 vs a:0 stream
durations (clean-cut's step-10 gate). It warns LOUDLY on a >50ms mismatch and aborts under
--strict; default is warn-and-continue, so existing behavior is unchanged.

Requires (real upload only; --dry-run needs none of these):
  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

Quota: standard = 10,000 units/day; an upload costs ~100 units (cut from 1,600 on 2025-12-04)
→ ~100 uploads/day. Some new projects start at 0 "Queries per day" and must request quota via
the YouTube API Services Audit & Quota Extension form (or use an older project that has 10,000).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Windows consoles default to cp1252 — force UTF-8 so box/✓ glyphs print
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent   # core/ — the engine root, holds .youtube/ creds
ROOT = REPO.parent                               # monorepo root — plan paths (video, description_file) are relative to this
YT_DIR = REPO / ".youtube"
CLIENT_SECRET = YT_DIR / "client_secret.json"
TOKEN = YT_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]  # .../youtube covers thumbnails.set + edits

TITLE_MAX, DESC_MAX, TAGS_CHARS_MAX = 100, 5000, 460
# common categories: 27 Education · 28 Science & Technology · 22 People & Blogs
DEFAULT_CATEGORY = 28

# Last-gate A/V sync preflight. clean-cut's step-10 rule: gate on v:0 duration == a:0
# duration. verify_cut's A/V budget GROWS along the timeline (±2s by mid-video) and masks
# a real accumulating drift, so the equal-duration check is the definitive one — yet this
# tool is the LAST gate before YouTube and never checked it. 50ms ≈ noticeable lip-sync slip.
AV_SYNC_TOLERANCE_S = 0.050


def rp(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if "description_file" in plan and "description" not in plan:
        plan["description"] = rp(plan["description_file"]).read_text(encoding="utf-8").strip()
    return plan


def validate(plan: dict) -> list[str]:
    errs = []
    if not plan.get("video"):
        errs.append("plan.video is required")
    elif not rp(plan["video"]).exists():
        errs.append(f"video not found: {plan['video']}")
    if not plan.get("title"):
        errs.append("plan.title is required")
    elif len(plan["title"]) > TITLE_MAX:
        errs.append(f"title is {len(plan['title'])} chars (max {TITLE_MAX})")
    if "|" in plan.get("title", "") or "<" in plan.get("title", "") or ">" in plan.get("title", ""):
        errs.append("title cannot contain < > | (YouTube rejects these)")
    if len(plan.get("description", "")) > DESC_MAX:
        errs.append(f"description is {len(plan['description'])} chars (max {DESC_MAX})")
    tags = plan.get("tags", [])
    if sum(len(t) for t in tags) + max(0, len(tags) - 1) > TAGS_CHARS_MAX:
        errs.append(f"tags exceed ~{TAGS_CHARS_MAX} total chars")
    thumb = plan.get("thumbnail")
    if thumb:
        tp = rp(thumb)
        if not tp.exists():
            errs.append(f"thumbnail not found: {thumb}")
        elif tp.stat().st_size > 2 * 1024 * 1024:
            errs.append(f"thumbnail is {tp.stat().st_size / 1e6:.1f} MB (YouTube max 2 MB) — use a JPG")
    if plan.get("publishAt") and plan.get("privacy", "private") != "private":
        errs.append("publishAt requires privacy=private (YouTube holds it private until that time)")
    return errs


def build_body(plan: dict) -> dict:
    status = {
        "privacyStatus": plan.get("privacy", "private"),
        "selfDeclaredMadeForKids": bool(plan.get("madeForKids", False)),
    }
    if plan.get("publishAt"):
        status["publishAt"] = plan["publishAt"]
    return {
        "snippet": {
            "title": plan["title"],
            "description": plan.get("description", ""),
            "tags": plan.get("tags", []),
            "categoryId": str(plan.get("categoryId", DEFAULT_CATEGORY)),
        },
        "status": status,
    }


def preview(plan: dict, body: dict) -> None:
    s, st = body["snippet"], body["status"]
    thumb = plan.get("thumbnail")
    print("── upload preview ─────────────────────────────")
    print(f"video      : {plan['video']}  ({rp(plan['video']).stat().st_size / 1e6:.1f} MB)")
    print(f"title      : {s['title']}  ({len(s['title'])}/{TITLE_MAX})")
    print(f"category   : {s['categoryId']}   privacy: {st['privacyStatus']}   kids: {st['selfDeclaredMadeForKids']}")
    print(f"publishAt  : {st.get('publishAt', '(none — stays private draft until you publish)')}")
    print(f"tags       : {', '.join(s['tags']) or '(none)'}")
    print(f"thumbnail  : {thumb or '(none)'}" + ("" if not thumb else f"  ({rp(thumb).stat().st_size / 1e3:.0f} KB)"))
    print(f"description: {len(s['description'])} chars")
    print("   " + "\n   ".join(s["description"].splitlines()[:4]) + (" …" if len(s['description'].splitlines()) > 4 else ""))
    print("───────────────────────────────────────────────")


def _stream_duration(path: Path, stream: str):
    """Duration (seconds, float) of ffprobe stream selector `stream` (e.g. 'v:0','a:0'),
    or None if ffprobe is missing, the stream is absent, or the duration is unreadable.
    Mirrors clean-cut step 10's `ffprobe -show_entries stream=duration` gate."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    txt = (out.stdout or "").strip().splitlines()
    if not txt or not txt[0].strip() or txt[0].strip().upper() == "N/A":
        return None
    try:
        return float(txt[0].strip())
    except ValueError:
        return None


def preflight_av_sync(video: Path, strict: bool) -> None:
    """LAST gate before YouTube: ffprobe the file and compare v:0 vs a:0 stream durations.
    Warn LOUDLY when they differ by more than AV_SYNC_TOLERANCE_S; abort (exit) when --strict.
    Advisory by default so existing behavior is unchanged — a warn-and-continue.

    This is the exact gate clean-cut prescribes for the master (step 10): verify_cut's A/V
    budget grows along the timeline and hides accumulating drift, so equal v:0/a:0 duration is
    the definitive check. The uploader is the last place to catch a drifted deliverable."""
    vd = _stream_duration(video, "v:0")
    ad = _stream_duration(video, "a:0")
    if vd is None or ad is None:
        # ffprobe absent, or a stream missing (audio-less clip / image) — note, don't block.
        missing = []
        if vd is None:
            missing.append("v:0")
        if ad is None:
            missing.append("a:0")
        print("• A/V sync preflight: skipped (couldn't read %s duration — ffprobe missing or "
              "stream absent)." % ", ".join(missing))
        return

    delta = abs(vd - ad)
    if delta > AV_SYNC_TOLERANCE_S:
        print("!" * 63)
        print("! A/V DURATION MISMATCH — v:0 %.3fs vs a:0 %.3fs (Δ %.3fs > %.0fms tolerance)"
              % (vd, ad, delta, AV_SYNC_TOLERANCE_S * 1000))
        print("! Audio and video are drifting apart. clean-cut's rule: v:0 duration MUST equal")
        print("! a:0 duration. Fix upstream (the re-timing H.264 transcode in clean-cut step 10")
        print("! makes v:0==a:0) before publishing — do NOT ship a lip-sync-drifted master.")
        print("!" * 63)
        if strict:
            sys.exit("aborting (--strict): A/V duration mismatch exceeds tolerance.")
        print("  (continuing — pass --strict to make this abort the upload.)")
    else:
        print("• A/V sync preflight: OK (v:0 %.3fs ≈ a:0 %.3fs, Δ %.3fs)." % (vd, ad, delta))


def get_creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not CLIENT_SECRET.exists():
            sys.exit(f"missing {CLIENT_SECRET} — see tools/yt_upload_SETUP.md (step 2)")
        creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES).run_local_server(port=0)
    YT_DIR.mkdir(exist_ok=True)
    TOKEN.write_text(creds.to_json())
    return creds


def service():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=get_creds())


def do_upload(plan: dict, strict: bool = False) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    # last gate before YouTube — v:0 vs a:0 duration (clean-cut step 10). Runs after the
    # plan validated + previewed, right before bytes go up. Advisory unless --strict.
    preflight_av_sync(rp(plan["video"]), strict)

    yt = service()
    body = build_body(plan)
    media = MediaFileUpload(str(rp(plan["video"])), chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    print("uploading… (resumable)")
    resp = None
    retries = 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                print(f"  {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                time.sleep(2 ** retries)
                continue
            raise
    vid = resp["id"]
    print(f"✓ uploaded (private draft): https://studio.youtube.com/video/{vid}/edit")

    thumb = plan.get("thumbnail")
    if thumb:
        try:
            from googleapiclient.http import MediaFileUpload as MFU
            yt.thumbnails().set(videoId=vid, media_body=MFU(str(rp(thumb)))).execute()
            print(f"• thumbnail API call sent: {thumb}")
            print("  ⚠ SHORTS CAVEAT: for a vertical ≤3-min video YouTube usually IGNORES this —")
            print("    the call returns success but the cover stays blank. If Studio shows")
            print("    'change the thumbnail in the YouTube mobile app', set the cover in the")
            print("    YouTube MOBILE APP (upload the .jpg). Desktop Studio + API can't do Shorts covers.")
        except HttpError as e:
            print(f"! thumbnail upload failed ({e.resp.status}) — set it in the YouTube mobile app.")
    print(f"\nNext: open Studio, review, then Publish or Schedule.\n  https://studio.youtube.com/video/{vid}/edit")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="one-time browser OAuth")
    sub.add_parser("whoami", help="print the authorized channel")
    up = sub.add_parser("upload", help="upload a video from a publish.json plan")
    up.add_argument("plan")
    up.add_argument("--dry-run", action="store_true", help="validate + preview, no API call")
    up.add_argument("--strict", action="store_true",
                    help="abort if v:0/a:0 durations differ by >50ms (default: warn + continue)")
    args = ap.parse_args()

    if args.cmd == "auth":
        get_creds()
        print(f"✓ authorized — token saved to {TOKEN.relative_to(REPO)}")
        return
    if args.cmd == "whoami":
        ch = service().channels().list(part="snippet", mine=True).execute()
        it = ch.get("items", [])
        print(it[0]["snippet"]["title"] if it else "(no channel on this account)")
        return

    plan_path = rp(args.plan)
    if not plan_path.exists():
        sys.exit(f"plan not found: {args.plan}")
    plan = load_plan(plan_path)
    errs = validate(plan)
    body = build_body(plan)
    if errs:
        print("PLAN ERRORS:")
        for e in errs:
            print(f"  ✗ {e}")
        sys.exit(1)
    preview(plan, body)
    if args.dry_run:
        # run the A/V gate in dry-run too, so it can be validated / CI-gated without an upload
        preflight_av_sync(rp(plan["video"]), args.strict)
        print("dry-run OK — plan is valid. Remove --dry-run to upload.")
        return
    do_upload(plan, args.strict)


if __name__ == "__main__":
    main()
