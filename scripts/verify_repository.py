#!/usr/bin/env python3
"""Verify the VM Lab living-repository and preservation contracts.

This check is intentionally standard-library-only. It validates the navigation
and continuity surfaces that ordinary runtime tests do not own; the v0.1 smoke
harness remains the behavioral authority for the promoted Python package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_FILES = (ROOT / "TOPOLOGY.md", ROOT / "ARCHITECTURE_MAP.md")
FINGERPRINT_PATH = ROOT / ".sovereign" / "topology_fingerprint.txt"

REQUIRED_PATHS = (
    "AGENTS.md",
    "filetree.md",
    "ARCHITECTURE_MAP.md",
    "TOPOLOGY.md",
    "FAILURE_GRAMMAR.md",
    "CONTEXT.md",
    "MEMORY.md",
    "TASK.md",
    "PLAN.md",
    "PROVENANCE.md",
    "README.md",
    "SCOPE.md",
    "SNAPSHOT.md",
    "SOTA_RUN.md",
    "source-manifest.json",
    "snapshots/v0.1/manifest.json",
    ".codex/skills/vm-lab/SKILL.md",
    ".sovereign/session_state.json",
    ".sovereign/golden_paths.json",
    ".sovereign/PROVENANCE.md",
    ".sovereign/topology_fingerprint.txt",
    ".github/copilot-instructions.md",
    ".github/workflows/repository-integrity.yml",
    ".pre-commit-config.yaml",
)

LINKED_DOCS = (
    "AGENTS.md",
    "filetree.md",
    "ARCHITECTURE_MAP.md",
    "TOPOLOGY.md",
    "FAILURE_GRAMMAR.md",
    "CONTEXT.md",
    "MEMORY.md",
    "TASK.md",
    ".codex/skills/vm-lab/SKILL.md",
    ".github/copilot-instructions.md",
)

REQUIRED_AGENTS_LINKS = (
    "filetree.md",
    "ARCHITECTURE_MAP.md",
    "TOPOLOGY.md",
    "FAILURE_GRAMMAR.md",
    "CONTEXT.md",
    "MEMORY.md",
    "TASK.md",
    "PLAN.md",
    "PROVENANCE.md",
)

LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
ANCHOR_PATTERN = re.compile(r"`([^`\n]+):(\d+)`")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Check:
    """One repository-integrity observation."""

    name: str
    ok: bool
    detail: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def topology_fingerprint() -> str:
    """Return the canonical hash of topology plus traversal map."""

    digest = hashlib.sha256()
    for index, path in enumerate(TOPOLOGY_FILES):
        if index:
            digest.update(b"\0")
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def check_required_paths() -> list[Check]:
    """Return presence and non-empty checks for the living repository kit."""

    checks: list[Check] = []
    for relative in REQUIRED_PATHS:
        path = ROOT / relative
        checks.append(
            Check(
                f"required:{relative}",
                path.exists() and (path.is_dir() or path.stat().st_size > 0),
                "present and non-empty" if path.exists() else "missing",
            )
        )
    return checks


def _link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    target = unquote(target.split("#", 1)[0])
    return (source.parent / target).resolve()


def check_markdown_links() -> list[Check]:
    """Return containment and existence checks for local Markdown targets."""

    checks: list[Check] = []
    for relative in LINKED_DOCS:
        source = ROOT / relative
        if not source.is_file():
            continue
        for raw_target in LINK_PATTERN.findall(source.read_text(encoding="utf-8")):
            target = _link_target(source, raw_target)
            if target is None:
                continue
            try:
                target.relative_to(ROOT)
                contained = True
            except ValueError:
                contained = False
            checks.append(
                Check(
                    f"link:{relative}->{raw_target}",
                    contained and target.exists(),
                    str(target.relative_to(ROOT)) if contained else "target escapes repository",
                )
            )
    return checks


def check_architecture_anchors() -> list[Check]:
    """Return file-range checks for every architecture-map line anchor."""

    checks: list[Check] = []
    source = ROOT / "ARCHITECTURE_MAP.md"
    for raw_path, raw_line in ANCHOR_PATTERN.findall(source.read_text(encoding="utf-8")):
        target = ROOT / raw_path
        line = int(raw_line)
        exists = target.is_file()
        line_count = len(target.read_text(encoding="utf-8").splitlines()) if exists else 0
        checks.append(
            Check(
                f"anchor:{raw_path}:{line}",
                exists and 1 <= line <= line_count,
                f"line_count={line_count}",
            )
        )
    return checks


def check_entry_contract() -> list[Check]:
    """Return checks for entry links, task bounds, and critical invariants."""

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    task = (ROOT / "TASK.md").read_text(encoding="utf-8")
    checks = [
        Check(
            f"agents-link:{target}",
            f"]({target})" in agents,
            "linked from AGENTS.md",
        )
        for target in REQUIRED_AGENTS_LINKS
    ]
    active_count = len(re.findall(r"^## ACTIVE\b", task, flags=re.MULTILINE))
    ready_count = len(re.findall(r"^## READY\b", task, flags=re.MULTILINE))
    valid_task_state = active_count == 1 and ready_count <= 1 or active_count == 0 and ready_count == 1
    checks.append(
        Check(
            "task:bounded-state",
            valid_task_state,
            f"active_count={active_count}, ready_count={ready_count}",
        )
    )
    for phrase in (
        "No VM capability exists publicly until external machine truth proves it.",
        "ports_reserved",
        "candidate",
        "quarantine",
    ):
        checks.append(Check(f"agents-contract:{phrase}", phrase in agents, "required invariant"))
    return checks


def check_json_state() -> list[Check]:
    """Return schema and topology-coherence checks for sovereign state."""

    checks: list[Check] = []
    expected = topology_fingerprint()
    try:
        session = json.loads((ROOT / ".sovereign/session_state.json").read_text())
        checks.extend(
            [
                Check("session:schema", session.get("schema_version") == 1, "schema_version=1"),
                Check("session:workspace", session.get("workspace") == ".", "workspace must be portable"),
                Check(
                    "session:topology-hash",
                    session.get("topology_hash") == expected,
                    f"expected={expected}",
                ),
                Check(
                    "session:open-tasks",
                    isinstance(session.get("open_tasks"), list),
                    "open_tasks must be a list",
                ),
            ]
        )
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(Check("session:json", False, str(exc)))

    try:
        golden = json.loads((ROOT / ".sovereign/golden_paths.json").read_text())
        paths = golden.get("paths", {})
        checks.extend(
            [
                Check("golden:schema", golden.get("schema_version") == 1, "schema_version=1"),
                Check(
                    "golden:required-routes",
                    {"entry", "implementation", "failure", "promotion"} <= set(paths),
                    f"routes={sorted(paths)}",
                ),
            ]
        )
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(Check("golden:json", False, str(exc)))

    actual_file = FINGERPRINT_PATH.read_text().strip() if FINGERPRINT_PATH.is_file() else ""
    checks.append(
        Check(
            "fingerprint:current",
            SHA256_PATTERN.fullmatch(actual_file) is not None and actual_file == expected,
            f"expected={expected}, actual={actual_file or 'missing'}",
        )
    )
    return checks


def check_source_preservation() -> list[Check]:
    """Return exact-path, uniqueness, and hash checks for all source files."""

    checks: list[Check] = []
    try:
        manifest = json.loads((ROOT / "source-manifest.json").read_text())
        files = manifest["files"]
        checks.append(Check("source:file-count", len(files) == 33, f"count={len(files)}"))
        destinations: set[str] = set()
        for row in files:
            relative = row["preserved_path"]
            target = ROOT / relative
            unique = relative not in destinations
            destinations.add(relative)
            actual = _sha256(target) if target.is_file() else "missing"
            checks.append(
                Check(
                    f"source:{relative}",
                    unique and actual == row["sha256"],
                    f"unique={unique}, sha256={actual}",
                )
            )
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        checks.append(Check("source:manifest", False, str(exc)))
    return checks


def check_automation_contract() -> list[Check]:
    """Return trigger and command checks for local and GitHub automation."""

    workflow = (ROOT / ".github/workflows/repository-integrity.yml").read_text()
    precommit = (ROOT / ".pre-commit-config.yaml").read_text()
    return [
        Check("ci:push", "push:" in workflow, "push trigger"),
        Check("ci:pull-request", "pull_request:" in workflow, "pull request trigger"),
        Check("ci:manual", "workflow_dispatch:" in workflow, "manual trigger"),
        Check("ci:integrity", "scripts/verify_repository.py" in workflow, "structural check"),
        Check("ci:smoke", "test/vm_lab/smoke.py" in workflow, "runtime check"),
        Check("precommit:integrity", "scripts/verify_repository.py" in precommit, "local check"),
    ]


def run() -> list[Check]:
    """Run all integrity checks without short-circuiting."""

    return [
        *check_required_paths(),
        *check_markdown_links(),
        *check_architecture_anchors(),
        *check_entry_contract(),
        *check_json_state(),
        *check_source_preservation(),
        *check_automation_contract(),
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the verifier CLI and return its process exit status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument(
        "--write-fingerprint",
        action="store_true",
        help="write the current topology fingerprint before verification",
    )
    args = parser.parse_args(argv)

    if args.write_fingerprint:
        FINGERPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        FINGERPRINT_PATH.write_text(f"{topology_fingerprint()}\n", encoding="utf-8")

    checks = run()
    failures = [check for check in checks if not check.ok]
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "summary": {"pass": len(checks) - len(failures), "fail": len(failures)},
                    "checks": [
                        {"name": check.name, "ok": check.ok, "detail": check.detail}
                        for check in checks
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for check in failures:
            print(f"FAIL {check.name}: {check.detail}", file=sys.stderr)
        status = "PASS" if not failures else "FAIL"
        print(f"{status}: {len(checks) - len(failures)} passed, {len(failures)} failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
