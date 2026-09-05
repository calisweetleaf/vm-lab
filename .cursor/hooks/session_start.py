#!/usr/bin/env python3
"""Inject the one ready/active TASK.md unit. Do not dump the kernel."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_io import emit, read_event  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HEADING = re.compile(
    r"^## (ACTIVE|READY) — `([^`]+)`:\s*(.+)$",
    re.MULTILINE,
)
FIXTURE = Path("/tmp/vm-lab-p3-fixture-20260801.qcow2")


def _first_heading(text: str) -> tuple[str, str, str]:
    match = HEADING.search(text)
    if match is None:
        return ("NONE", "unknown", "TASK.md has no ACTIVE/READY unit")
    return (match.group(1), match.group(2), match.group(3).strip())


def _sota_status() -> str:
    path = ROOT / "SOTA_RUN.md"
    if not path.is_file():
        return "SOTA_RUN.md missing"
    status = "unknown"
    run_dir = "unknown"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- status:"):
            status = line.split(":", 1)[1].strip()
        elif line.startswith("- run_dir:"):
            run_dir = line.split(":", 1)[1].strip()
    return f"{status} @ {run_dir}"


def main() -> int:
    read_event()
    task = (ROOT / "TASK.md").read_text(encoding="utf-8") if (ROOT / "TASK.md").is_file() else ""
    state, ident, title = _first_heading(task)
    fixture = "present" if FIXTURE.is_file() else "ABSENT"
    cwd = os.getcwd()
    context = (
        "VM Lab operator pulse (code the unit; continuity is close-out):\n"
        f"- unit: {state} `{ident}` — {title}\n"
        f"- proof: {_sota_status()}\n"
        f"- fixture {FIXTURE}: {fixture}\n"
        f"- cwd: {cwd}\n"
        "- act: open the unit's owner files and implement. Do not write PROVENANCE/NOTEPAD first.\n"
        "- bans: no pytest, no public start/stop/destroy, no substitute fixture, no GATE-P4 claim from preflight."
    )
    emit({"additional_context": context})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
