#!/usr/bin/env python3
"""Fail closed on pytest and public lifecycle. Allow QEMU/matrix/operator work."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_io import emit, read_event  # noqa: E402

_PYTEST = re.compile(r"(^|[^\w-])(pytest|py\.test)([^\w-]|$)", re.IGNORECASE)
_LIFECYCLE = re.compile(
    r"(?:python(?:3)?\s+-m\s+somnus_vm|vm-lab)\s+(start|stop|destroy)\b",
    re.IGNORECASE,
)
_ERASE_RUNS = re.compile(
    r"\brm\b.*\btest/vm_lab/runs\b",
    re.IGNORECASE,
)


def main() -> int:
    event = read_event()
    command = str(event.get("command") or event.get("cmd") or "")
    if _PYTEST.search(command):
        emit(
            {
                "permission": "deny",
                "agent_message": "pytest is banned. Use a direct Python file that prints a terminal readout and writes JSON + Markdown artifacts.",
                "user_message": "Blocked pytest. This repo uses direct Python proof modules, not pytest.",
            }
        )
        return 0
    if _LIFECYCLE.search(command):
        emit(
            {
                "permission": "deny",
                "agent_message": "Public vm-lab start/stop/destroy does not exist until GATE-P4 and later gates close. Use scripts/run_gate_p4.py for the non-public matrix.",
                "user_message": "Blocked public lifecycle CLI. GATE-P4 is still the non-public matrix driver.",
            }
        )
        return 0
    if _ERASE_RUNS.search(command):
        emit(
            {
                "permission": "deny",
                "agent_message": "Do not erase test/vm_lab/runs evidence. Failed bundles stay.",
                "user_message": "Blocked deletion of sealed/failed run artifacts.",
            }
        )
        return 0
    emit({"permission": "allow"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
