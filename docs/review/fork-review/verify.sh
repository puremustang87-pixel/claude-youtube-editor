#!/usr/bin/env bash
# verify.sh — independent verification of the claude-youtube-editor fork.
# Run from the packet root:  bash verify.sh
# Requirements: python3 (3.9+), ffmpeg/ffprobe on PATH, node 18+ (step 6 only).
# Step 3 needs Pillow:  python3 -m pip install Pillow
set -uo pipefail
cd "$(dirname "$0")/repo"

PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
pass=0; fail=0
res() { if [ "$1" -eq 0 ]; then echo "PASS: $2"; pass=$((pass+1)); else echo "FAIL: $2"; fail=$((fail+1)); fi; }

echo "== 1/6 python syntax: all new + modified tools"
$PY -m py_compile tools/encoders.py tools/render_cuts.py tools/make_proxy.py \
  tools/capture_web.py tools/gen_screencast.py tools/verify_frames.py \
  tools/yt_upload.py tools/apply_fork_docs.py tools/editor/server.py
res $? "py_compile (9 files)"

echo; echo "== 2/6 encoder auto-detect on THIS machine (W1 portability core)"
$PY tools/encoders.py
res $? "encoders.py probe + ladder selection"

echo; echo "== 3/6 visual QA self-test — 16 assertions (W4; needs Pillow)"
$PY tools/verify_frames.py --self-test
res $? "verify_frames.py --self-test"

echo; echo "== 4/6 capture->TSX codegen from the shipped fixture, offline (W2)"
$PY tools/gen_screencast.py --manifest tools/fixtures/capture-demo-manifest.json \
  --name ReviewDemo --out /tmp/ReviewDemo.gen.tsx --fps 60
res $? "gen_screencast.py fixture compile"
if [ -f /tmp/ReviewDemo.gen.tsx ] && grep -q "Screencast" /tmp/ReviewDemo.gen.tsx \
   && grep -q "CURSOR" /tmp/ReviewDemo.gen.tsx && grep -q "enterAt" /tmp/ReviewDemo.gen.tsx; then
  res 0 "generated TSX contains engine identifiers (Screencast/CURSOR/enterAt)"
else
  res 1 "generated TSX contains engine identifiers (Screencast/CURSOR/enterAt)"
fi

echo; echo "== 5/6 skill-docs installer mapping (dry run; installs nothing)"
$PY tools/apply_fork_docs.py --dry-run
res $? "apply_fork_docs.py --dry-run (fork-docs/claude-skills -> .claude/skills)"

echo; echo "== 6/6 remotion registry + FULL typecheck (installs npm deps; ~1-2 min)"
( cd remotion && npm install --no-audit --no-fund --loglevel=error && npm run gen && npx tsc --noEmit )
res $? "npm run gen + npx tsc --noEmit (includes new realscreencast.tsx)"

echo
echo "==================================================="
echo " RESULT: $pass passed, $fail failed"
echo " Optional deeper checks (not automated here):"
echo "  - Live render at YOUR fps: record a 2-min clip, run the /clean-cut flow;"
echo "    then: ffprobe -v error -show_entries stream=duration -of csv master*.mp4"
echo "    (v:0 and a:0 must match within 50ms)"
echo "  - Upload preflight: python tools/yt_upload.py ... --dry-run [--strict]"
echo "  - Review changes.patch against the invariants in REVIEW.md"
echo "==================================================="
[ "$fail" -eq 0 ]
