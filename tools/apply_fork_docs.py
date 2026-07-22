#!/usr/bin/env python3
"""apply_fork_docs.py — install the fork's staged skill docs into .claude/.

Why this exists: the fork's improvements were built in a sandboxed environment where
writing into .claude/ is permission-gated (it configures agent behavior). The updated
skill docs therefore ship staged under fork-docs/claude-skills/ and are installed by
YOU, on your machine, with this one command:

    python tools/apply_fork_docs.py            # show what will change, then install
    python tools/apply_fork_docs.py --dry-run  # only show what would change

What it does:
  - copies every file under fork-docs/claude-skills/ into .claude/skills/
  - backs up any file it overwrites to fork-docs/backups/<timestamp>/ first
  - never touches anything outside .claude/skills/

After it runs, the new /real-screencast skill and the updated fake-screencast,
make-tsx, clean-cut, and vidtsx-2d-generator docs are live for Claude Code.
Review the staged files first if you like — they are plain markdown.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "fork-docs" / "claude-skills"
TARGET = ROOT / ".claude" / "skills"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list actions without writing")
    args = ap.parse_args()

    if not STAGE.is_dir():
        print(f"nothing staged: {STAGE} not found")
        return 1

    staged = sorted(p for p in STAGE.rglob("*") if p.is_file())
    if not staged:
        print("nothing staged: fork-docs/claude-skills/ is empty")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = ROOT / "fork-docs" / "backups" / stamp
    planned = []
    for src in staged:
        rel = src.relative_to(STAGE)
        dst = TARGET / rel
        action = "update" if dst.exists() else "create"
        if action == "update" and dst.read_bytes() == src.read_bytes():
            action = "identical (skip)"
        planned.append((action, rel, src, dst))

    width = max(len(a) for a, *_ in planned)
    for action, rel, *_ in planned:
        print(f"  {action:<{width}}  .claude/skills/{rel}")

    todo = [(a, r, s, d) for a, r, s, d in planned if a != "identical (skip)"]
    if not todo:
        print("everything already installed — nothing to do")
        return 0
    if args.dry_run:
        print(f"\n--dry-run: {len(todo)} file(s) would be written")
        return 0

    for action, rel, src, dst in todo:
        if action == "update":
            bak = backup_root / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bak)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    if backup_root.exists():
        print(f"\nbackups of replaced files: {backup_root.relative_to(ROOT)}")
    print(f"installed {len(todo)} file(s) into .claude/skills/ — the fork's skills are live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
