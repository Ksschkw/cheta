#!/usr/bin/env python3
"""File the drafted bug reports as GitHub issues on MystenLabs/MemWal.

The reports live in docs/bug-reports/. This script takes the H1 of each file as
the issue title and the remainder as the body, so the drafts stay the single
source of truth and nothing has to be re-typed into the GitHub UI.

    python scripts/file_bug_reports.py --dry-run
    python scripts/file_bug_reports.py

Requires the gh CLI, already authenticated. Issue numbers are printed so the
report index can be updated with the real URLs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "docs" / "bug-reports"
TARGET_REPO = "MystenLabs/MemWal"

# 04 is withdrawn in its own text: the behaviour it describes is documented by
# design, so filing it would be inaccurate. 06 is filed as a comment on the
# existing dedupe thread rather than as a separate issue, because that is where
# the maintainers are already discussing the same problem.
FILE_THESE = (
    "01-python-sdk-no-forget.md",
    "02-scoring-weights-unreachable.md",
    "03-recall-returns-no-timestamps.md",
    "05-python-default-server-url-mismatch.md",
)


def split_report(path: Path) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    body_start = 0
    for index, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = index + 1
            break
    if not title:
        raise ValueError(f"{path.name} has no H1 title")
    return title, "\n".join(lines[body_start:]).strip()


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="File the drafted bug reports.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args()

    if not args.dry_run and subprocess.run(
        ["gh", "auth", "status"], capture_output=True, check=False
    ).returncode != 0:
        print("[FAIL] gh is not authenticated. Run: gh auth login")
        return 1

    for name in FILE_THESE:
        path = REPORTS_DIR / name
        if not path.is_file():
            print(f"[FAIL] missing report: {name}")
            return 1
        title, body = split_report(path)
        print(f"[{'PLAN' if args.dry_run else 'FILE'}] {name}")
        print(f"        title: {title[:100]}")
        print(f"        body : {len(body)} chars, {body.count(chr(10)) + 1} lines")

        if args.dry_run:
            continue

        with (REPO_ROOT / "artifacts" / "issue-body.md").open("w", encoding="utf-8") as handle:
            handle.write(body)
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                TARGET_REPO,
                "--title",
                title,
                "--body-file",
                str(REPO_ROOT / "artifacts" / "issue-body.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"[FAIL] {result.stderr.strip()[:300]}")
            return 1
        print(f"        [OK] {result.stdout.strip()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
