#!/usr/bin/env python3
"""Allow edits, but shove the agent off continuity-first behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_io import emit, read_event  # noqa: E402

CONTINUITY = {
    "NOTEPAD.md",
    "PROVENANCE.md",
    "MEMORY.md",
    "SOTA_RUN.md",
    "STATE.md",
    "CONTEXT.md",
    "SCOPE.md",
}


def _path(event: dict) -> str:
    for key in ("path", "file_path", "target"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    inner = event.get("tool_input")
    if isinstance(inner, dict):
        for key in ("path", "file_path"):
            value = inner.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def main() -> int:
    event = read_event()
    path = _path(event).replace("\\", "/")
    name = Path(path).name
    if name in CONTINUITY:
        emit(
            {
                "permission": "allow",
                "agent_message": (
                    f"{name} is close-out, not the unit. If TASK.md is READY/ACTIVE, "
                    "edit the owner under src/somnus_vm or scripts/run_gate_p4.py and produce "
                    "consumed-boundary evidence first."
                ),
            }
        )
        return 0
    emit({"permission": "allow"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
