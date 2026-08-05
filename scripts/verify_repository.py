#!/usr/bin/env python3
"""Verify the VM Lab living-repository and preservation contracts.

This check is intentionally standard-library-only. It validates the navigation
and continuity surfaces that ordinary runtime tests do not own; the v0.1 smoke
harness remains the behavioral authority for the promoted Python package.

Modified: 2026-08-05
Modified by: daeron and Codex
Justification: Phase P0 requires one direct machine validator for the execution
    DAG, task state, gates, and evidence. Extending the repository verifier
    preserves one structural authority instead of creating a parallel planner.
Provenance: PROVENANCE.md, Phase P0 execution-baseline session.
Files: scripts/verify_repository.py
"""

from __future__ import annotations

import argparse
from collections import Counter
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
    "STATE.md",
    "PLAN.md",
    "PROVENANCE.md",
    "README.md",
    "SCOPE.md",
    "SNAPSHOT.md",
    "SOTA_RUN.md",
    "source-manifest.json",
    "snapshots/v0.1/manifest.json",
    "docs/plan-index.json",
    "docs/PLATFORM_MATRIX.md",
    "docs/DISPOSABLE_FIXTURE_POLICY.md",
    "docs/decisions/0001-single-mutation-owner.md",
    "docs/decisions/0002-sqlite-registry.md",
    "docs/decisions/0003-package-authority-boundaries.md",
    "docs/decisions/0004-stopped-vm-rollback.md",
    "docs/decisions/0005-errors-and-exit-codes.md",
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
    "STATE.md",
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
PLAN_ID_PATTERN = re.compile(
    r"^- \[[ x]\] `((?:INV|BASE|DONE|EXEC|TEST)-\d{3}|P\d+-\d{3}|GATE-P\d+)`",
    re.MULTILINE,
)
PLAN_ITEM_PATTERN = re.compile(r"^- \[([ x])\] `(P\d+-\d{3})`", re.MULTILINE)
PHASE_HEADING_PATTERN = re.compile(r"^## \d+\. Phase (P\d+) — (.+)$", re.MULTILINE)
CURRENT_TASK_PATTERN = re.compile(
    r"^## (ACTIVE|READY)\b.*`TASK-(P\d+)-\d{3}`",
    re.MULTILINE,
)
EXPECTED_PHASES = {f"P{phase}" for phase in range(19)}
EXPECTED_EXECUTION_ORDER = tuple(
    [*(f"P{phase}" for phase in range(17)), "P18", "P17"]
)
PHASE_STATUSES = {"pending", "ready", "active", "blocked", "complete"}
ITEM_STATUSES = {"pending", "blocked", "complete"}
TOP_LEVEL_PLAN_KEYS = {
    "schema_version",
    "plan_path",
    "execution_order",
    "current_phase",
    "baseline_evidence",
    "phases",
}
PHASE_PLAN_KEYS = {
    "id",
    "title",
    "status",
    "depends_on",
    "gate_id",
    "owner_files",
    "items",
    "evidence",
    "blocker",
}
ITEM_PLAN_KEYS = {"id", "status", "evidence", "blocker"}


class PlanIndexError(ValueError):
    """Raised when JSON structure would conceal execution-plan intent."""


@dataclass(frozen=True, slots=True)
class Check:
    """One repository-integrity observation."""

    name: str
    ok: bool
    detail: str


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while rejecting duplicate keys."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PlanIndexError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _string_list(value: object) -> list[str] | None:
    """Return a string list when the complete value has the required shape."""

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def _path_observation(root: Path, raw_path: str) -> tuple[bool, str]:
    """Return containment and existence evidence for one repository path."""

    repository = root.resolve()
    target = (repository / raw_path).resolve()
    contained = target.is_relative_to(repository)
    exists = target.exists()
    if not contained:
        return False, "path escapes repository"
    return exists, f"{raw_path}: {'present' if exists else 'missing'}"


def _phase_sections(plan: str) -> dict[str, tuple[str, dict[str, bool]]]:
    """Return phase titles and item completion states parsed from PLAN.md."""

    matches = list(PHASE_HEADING_PATTERN.finditer(plan))
    sections: dict[str, tuple[str, dict[str, bool]]] = {}
    for index, match in enumerate(matches):
        phase_id, title = match.groups()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(plan)
        items = {
            item_id: marker == "x"
            for marker, item_id in PLAN_ITEM_PATTERN.findall(plan[match.end() : end])
        }
        sections[phase_id] = (title, items)
    return sections


def check_plan_index(root: Path = ROOT) -> list[Check]:
    """Validate the machine phase DAG, task state, gates, owners, and evidence."""

    checks: list[Check] = []
    repository = root.resolve()
    index_path = repository / "docs" / "plan-index.json"
    try:
        raw_payload = json.loads(
            index_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (
        OSError,
        json.JSONDecodeError,
        PlanIndexError,
        UnicodeError,
    ) as exc:
        return [Check("plan-index:load", False, f"{type(exc).__name__}: {exc}")]

    if not isinstance(raw_payload, dict):
        return [Check("plan-index:root-object", False, "top level must be an object")]
    payload: dict[str, object] = raw_payload
    unknown_top = sorted(set(payload) - TOP_LEVEL_PLAN_KEYS)
    checks.append(
        Check(
            "plan-index:top-level-fields",
            not unknown_top,
            f"unknown={unknown_top}",
        )
    )
    checks.append(
        Check(
            "plan-index:schema",
            payload.get("schema_version") == 1,
            f"schema_version={payload.get('schema_version')!r}",
        )
    )

    plan_path = payload.get("plan_path")
    if not isinstance(plan_path, str):
        checks.append(Check("plan-index:plan-path", False, "plan_path must be a string"))
        return checks
    plan_path_ok, plan_path_detail = _path_observation(repository, plan_path)
    checks.append(Check("plan-index:plan-path", plan_path_ok, plan_path_detail))
    if not plan_path_ok:
        return checks
    try:
        plan = (repository / plan_path).read_text(encoding="utf-8")
        task = (repository / "TASK.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        checks.append(Check("plan-index:source-read", False, f"{type(exc).__name__}: {exc}"))
        return checks

    identifiers = PLAN_ID_PATTERN.findall(plan)
    identifier_counts = Counter(identifiers)
    duplicate_identifiers = sorted(
        identifier for identifier, count in identifier_counts.items() if count > 1
    )
    checks.extend(
        [
            Check(
                "plan-index:plan-depth",
                len(identifiers) >= 250,
                f"identifier_count={len(identifiers)}",
            ),
            Check(
                "plan-index:unique-plan-ids",
                not duplicate_identifiers,
                f"duplicates={duplicate_identifiers}",
            ),
        ]
    )
    plan_ids = set(identifiers)
    completed_plan_ids = set(re.findall(r"^- \[x\] `([^`]+)`", plan, re.MULTILINE))
    plan_sections = _phase_sections(plan)
    checks.append(
        Check(
            "plan-index:plan-phases",
            set(plan_sections) == EXPECTED_PHASES,
            f"phases={sorted(plan_sections)}",
        )
    )

    order = _string_list(payload.get("execution_order"))
    order_ok = order is not None and tuple(order) == EXPECTED_EXECUTION_ORDER
    checks.append(
        Check(
            "plan-index:execution-order",
            order_ok,
            f"order={order}",
        )
    )
    order_positions = {phase_id: index for index, phase_id in enumerate(order or [])}

    raw_phases = payload.get("phases")
    if not isinstance(raw_phases, list):
        checks.append(Check("plan-index:phases-shape", False, "phases must be a list"))
        return checks
    phase_rows = [row for row in raw_phases if isinstance(row, dict)]
    checks.append(
        Check(
            "plan-index:phases-shape",
            len(phase_rows) == len(raw_phases),
            f"objects={len(phase_rows)}, rows={len(raw_phases)}",
        )
    )
    phase_ids = [row.get("id") for row in phase_rows]
    phase_id_strings = [phase_id for phase_id in phase_ids if isinstance(phase_id, str)]
    duplicate_phases = sorted(
        phase_id for phase_id, count in Counter(phase_id_strings).items() if count > 1
    )
    checks.extend(
        [
            Check(
                "plan-index:phase-ids",
                set(phase_id_strings) == EXPECTED_PHASES and len(phase_id_strings) == 19,
                f"phase_ids={phase_id_strings}",
            ),
            Check(
                "plan-index:unique-phase-ids",
                not duplicate_phases,
                f"duplicates={duplicate_phases}",
            ),
        ]
    )

    phase_statuses: dict[str, str] = {}
    current_candidates: list[str] = []
    for row_number, row in enumerate(phase_rows):
        phase_id = row.get("id")
        label = phase_id if isinstance(phase_id, str) else f"row-{row_number}"
        unknown_phase = sorted(set(row) - PHASE_PLAN_KEYS)
        checks.append(
            Check(
                f"plan-index:{label}:fields",
                not unknown_phase,
                f"unknown={unknown_phase}",
            )
        )
        if not isinstance(phase_id, str) or phase_id not in EXPECTED_PHASES:
            checks.append(Check(f"plan-index:{label}:id", False, f"id={phase_id!r}"))
            continue

        status = row.get("status")
        status_ok = isinstance(status, str) and status in PHASE_STATUSES
        checks.append(
            Check(
                f"plan-index:{phase_id}:status",
                status_ok,
                f"status={status!r}",
            )
        )
        if status_ok:
            phase_statuses[phase_id] = status
            if status in {"ready", "active"}:
                current_candidates.append(phase_id)
        blocker = row.get("blocker")
        checks.append(
            Check(
                f"plan-index:{phase_id}:blocker",
                status != "blocked" or isinstance(blocker, str) and bool(blocker.strip()),
                f"blocker={blocker!r}",
            )
        )

        title = row.get("title")
        expected_title = plan_sections.get(phase_id, ("", {}))[0]
        checks.append(
            Check(
                f"plan-index:{phase_id}:title",
                isinstance(title, str) and title == expected_title,
                f"expected={expected_title!r}, actual={title!r}",
            )
        )

        dependencies = _string_list(row.get("depends_on"))
        dependencies_ok = dependencies is not None
        if dependencies is not None:
            dependencies_ok = (
                len(dependencies) == len(set(dependencies))
                and phase_id not in dependencies
                and all(dependency in EXPECTED_PHASES for dependency in dependencies)
                and all(
                    order_positions.get(dependency, 10_000)
                    < order_positions.get(phase_id, -1)
                    for dependency in dependencies
                )
            )
            phase_position = order_positions.get(phase_id)
            if phase_position is not None and phase_position > 0 and order is not None:
                dependencies_ok = dependencies_ok and order[phase_position - 1] in dependencies
            if phase_position == 0:
                dependencies_ok = dependencies_ok and not dependencies
        checks.append(
            Check(
                f"plan-index:{phase_id}:dependencies",
                dependencies_ok,
                f"depends_on={dependencies}",
            )
        )

        gate_id = row.get("gate_id")
        expected_gate = f"GATE-{phase_id}"
        gate_ok = gate_id == expected_gate and expected_gate in plan_ids
        checks.append(
            Check(
                f"plan-index:{phase_id}:gate",
                gate_ok,
                f"expected={expected_gate}, actual={gate_id!r}",
            )
        )

        owners = _string_list(row.get("owner_files"))
        checks.append(
            Check(
                f"plan-index:{phase_id}:owners-shape",
                owners is not None and bool(owners),
                f"owner_files={owners}",
            )
        )
        for owner_index, owner in enumerate(owners or []):
            owner_ok, owner_detail = _path_observation(repository, owner)
            checks.append(
                Check(
                    f"plan-index:{phase_id}:owner-{owner_index}",
                    owner_ok,
                    owner_detail,
                )
            )

        phase_evidence = _string_list(row.get("evidence"))
        phase_evidence_ok = phase_evidence is not None and (
            status != "complete" or bool(phase_evidence)
        )
        checks.append(
            Check(
                f"plan-index:{phase_id}:evidence-shape",
                phase_evidence_ok,
                f"evidence={phase_evidence}",
            )
        )
        for evidence_index, evidence in enumerate(phase_evidence or []):
            evidence_ok, evidence_detail = _path_observation(repository, evidence)
            checks.append(
                Check(
                    f"plan-index:{phase_id}:evidence-{evidence_index}",
                    evidence_ok,
                    evidence_detail,
                )
            )

        raw_items = row.get("items")
        if not isinstance(raw_items, list):
            checks.append(
                Check(f"plan-index:{phase_id}:items-shape", False, "items must be a list")
            )
            continue
        item_rows = [item for item in raw_items if isinstance(item, dict)]
        indexed_item_ids = [
            item.get("id") for item in item_rows if isinstance(item.get("id"), str)
        ]
        expected_items = plan_sections.get(phase_id, ("", {}))[1]
        checks.append(
            Check(
                f"plan-index:{phase_id}:items",
                len(item_rows) == len(raw_items)
                and set(indexed_item_ids) == set(expected_items)
                and len(indexed_item_ids) == len(set(indexed_item_ids)),
                f"indexed={indexed_item_ids}, expected={sorted(expected_items)}",
            )
        )
        all_items_complete = bool(item_rows)
        for item_number, item in enumerate(item_rows):
            item_id = item.get("id")
            item_label = item_id if isinstance(item_id, str) else f"item-{item_number}"
            unknown_item = sorted(set(item) - ITEM_PLAN_KEYS)
            checks.append(
                Check(
                    f"plan-index:{phase_id}:{item_label}:fields",
                    not unknown_item,
                    f"unknown={unknown_item}",
                )
            )
            item_status = item.get("status")
            item_status_ok = isinstance(item_status, str) and item_status in ITEM_STATUSES
            all_items_complete = all_items_complete and item_status == "complete"
            checks.append(
                Check(
                    f"plan-index:{phase_id}:{item_label}:status",
                    item_status_ok,
                    f"status={item_status!r}",
                )
            )
            item_blocker = item.get("blocker")
            checks.append(
                Check(
                    f"plan-index:{phase_id}:{item_label}:blocker",
                    item_status != "blocked"
                    or isinstance(item_blocker, str)
                    and bool(item_blocker.strip()),
                    f"blocker={item_blocker!r}",
                )
            )
            item_evidence = _string_list(item.get("evidence"))
            item_evidence_ok = item_evidence is not None and (
                item_status != "complete" or bool(item_evidence)
            )
            checks.append(
                Check(
                    f"plan-index:{phase_id}:{item_label}:evidence-shape",
                    item_evidence_ok,
                    f"evidence={item_evidence}",
                )
            )
            for evidence_index, evidence in enumerate(item_evidence or []):
                evidence_ok, evidence_detail = _path_observation(repository, evidence)
                checks.append(
                    Check(
                        f"plan-index:{phase_id}:{item_label}:evidence-{evidence_index}",
                        evidence_ok,
                        evidence_detail,
                    )
                )
            if isinstance(item_id, str) and item_id in expected_items:
                plan_complete = expected_items[item_id]
                checks.append(
                    Check(
                        f"plan-index:{phase_id}:{item_id}:plan-status",
                        plan_complete == (item_status == "complete"),
                        f"plan_complete={plan_complete}, index_status={item_status!r}",
                    )
                )

        gate_complete = expected_gate in completed_plan_ids
        checks.append(
            Check(
                f"plan-index:{phase_id}:completion",
                (status == "complete") == gate_complete
                and (status != "complete" or all_items_complete and bool(phase_evidence)),
                (
                    f"status={status!r}, gate_complete={gate_complete}, "
                    f"all_items_complete={all_items_complete}"
                ),
            )
        )

    current_phase = payload.get("current_phase")
    checks.append(
        Check(
            "plan-index:one-current-phase",
            len(current_candidates) == 1
            and current_phase == current_candidates[0],
            f"current_phase={current_phase!r}, candidates={current_candidates}",
        )
    )
    current_task_matches = CURRENT_TASK_PATTERN.findall(task)
    task_phases = [phase for _, phase in current_task_matches]
    checks.append(
        Check(
            "plan-index:task-agreement",
            len(task_phases) == 1 and current_phase == task_phases[0],
            f"task_phases={task_phases}, current_phase={current_phase!r}",
        )
    )

    baseline_evidence = _string_list(payload.get("baseline_evidence"))
    checks.append(
        Check(
            "plan-index:baseline-evidence-shape",
            baseline_evidence is not None and bool(baseline_evidence),
            f"baseline_evidence={baseline_evidence}",
        )
    )
    for evidence_index, evidence in enumerate(baseline_evidence or []):
        evidence_ok, evidence_detail = _path_observation(repository, evidence)
        checks.append(
            Check(
                f"plan-index:baseline-evidence-{evidence_index}",
                evidence_ok,
                evidence_detail,
            )
        )

    completed_done = sorted(
        identifier for identifier in completed_plan_ids if identifier.startswith("DONE-")
    )
    p15_complete = phase_statuses.get("P15") == "complete"
    checks.append(
        Check(
            "plan-index:no-false-physical-completion",
            not completed_done or p15_complete,
            f"completed_done={completed_done}, p15_complete={p15_complete}",
        )
    )
    return checks


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
        *check_plan_index(),
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the verifier CLI and return its process exit status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="validate only the execution-plan index and its live owners",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="alternate repository root for --plan-only fixture validation",
    )
    parser.add_argument(
        "--write-fingerprint",
        action="store_true",
        help="write the current topology fingerprint before verification",
    )
    args = parser.parse_args(argv)

    if args.root is not None and not args.plan_only:
        parser.error("--root is valid only with --plan-only")
    if args.plan_only and args.write_fingerprint:
        parser.error("--write-fingerprint cannot be combined with --plan-only")

    if args.write_fingerprint:
        FINGERPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        FINGERPRINT_PATH.write_text(f"{topology_fingerprint()}\n", encoding="utf-8")

    checks = check_plan_index(args.root or ROOT) if args.plan_only else run()
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
