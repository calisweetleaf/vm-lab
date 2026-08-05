"""Explicit runtime-boundary and disposition registry.

Source: complete vm_lab.zip topology audit
Integrated: 2026-08-04
Purpose: Makes live, candidate, cold, lineage, and quarantine placement
    machine-readable so no dormant component can leak into VM boot by import.
IO: Filesystem existence checks only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RuntimeBoundary(str, Enum):
    """Physical execution authority for a component."""

    HOST = "host"
    GUEST = "guest"
    OPERATOR = "operator"
    SHARED = "shared"


class Disposition(str, Enum):
    """Promotion state assigned during reconstitution."""

    LIVE = "live"
    CANDIDATE = "candidate"
    COLD = "cold"
    LINEAGE = "lineage"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One preserved or active VM lab component."""

    name: str
    boundary: RuntimeBoundary
    disposition: Disposition
    relative_path: str
    boot_import: bool
    reason: str


COMPONENTS: tuple[ComponentSpec, ...] = (
    ComponentSpec("vm_contracts", RuntimeBoundary.SHARED, Disposition.LIVE, "src/somnus_vm/contracts", True, "Single VM and agent protocol authority"),
    ComponentSpec("host_planning", RuntimeBoundary.HOST, Disposition.LIVE, "src/somnus_vm/host", True, "Read-only ports and fully structured QEMU planning"),
    ComponentSpec("guest_bootstrap", RuntimeBoundary.GUEST, Disposition.LIVE, "src/somnus_vm/guest/bootstrap.py", False, "Separate in-guest one-shot entrypoint"),
    ComponentSpec("candidate_contracts", RuntimeBoundary.SHARED, Disposition.CANDIDATE, "components/contracts", False, "Original mixed schema lineage preserved outside the wheel"),
    ComponentSpec("image_manager", RuntimeBoundary.HOST, Disposition.CANDIDATE, "components/host/vm_image_manager.py", False, "Valuable image lineage requiring real-image validation"),
    ComponentSpec("guest_runtime", RuntimeBoundary.GUEST, Disposition.CANDIDATE, "components/guest/runtime", False, "Memory, ASPS, ACE, and cache preserved outside host boot"),
    ComponentSpec("guest_agent", RuntimeBoundary.GUEST, Disposition.CANDIDATE, "components/guest/agent/digital_twin.py", False, "Likely agent lineage; protocol and lock repair required"),
    ComponentSpec("operator_shell", RuntimeBoundary.OPERATOR, Disposition.CANDIDATE, "components/operator/ai_advanced_shell.py", False, "Monolith preserved; must not own QEMU"),
    ComponentSpec("operator_host_api", RuntimeBoundary.OPERATOR, Disposition.CANDIDATE, "components/operator/ai_action_orchestrator.py", False, "Client and result lineage pending separation from embedded supervisor ownership"),
    ComponentSpec("native_tools", RuntimeBoundary.OPERATOR, Disposition.CANDIDATE, "components/operator/native_tools", False, "Lazy adapters required before registration"),
    ComponentSpec("file_processors", RuntimeBoundary.GUEST, Disposition.COLD, "extras/file_processing/processors", False, "Useful ingress processors, separately activated"),
    ComponentSpec("file_sovereignty", RuntimeBoundary.GUEST, Disposition.COLD, "extras/file_processing/sovereignty.py", False, "Policy lineage retained; old validator requires rewrite"),
    ComponentSpec("file_processing_legacy", RuntimeBoundary.GUEST, Disposition.QUARANTINE, "extras/file_processing/legacy", False, "Disconnected contracts and unsafe eager dependencies"),
    ComponentSpec("legacy_supervisor", RuntimeBoundary.HOST, Disposition.LINEAGE, "archive/lineage/host/vm_supervisor.py", False, "Retained evidence; unsafe PID, network, snapshot, and scaling semantics"),
    ComponentSpec("legacy_bootstrap", RuntimeBoundary.GUEST, Disposition.LINEAGE, "archive/lineage/guest/vm_bootstrap.py", False, "Original installer retained beside the safe promoted rewrite"),
    ComponentSpec("lineage_docs", RuntimeBoundary.SHARED, Disposition.LINEAGE, "docs/lineage", False, "Historical architecture intent, not current verification"),
    ComponentSpec("legacy_orchestration", RuntimeBoundary.HOST, Disposition.QUARANTINE, "quarantine/runtime", False, "Simulations, duplicate authorities, and missing integrations"),
    ComponentSpec("synthetic_benchmark", RuntimeBoundary.SHARED, Disposition.QUARANTINE, "quarantine/tests/benchmark_vm_system.py", False, "Sleep timings are not VM evidence"),
    ComponentSpec("stale_evidence", RuntimeBoundary.SHARED, Disposition.QUARANTINE, "quarantine/evidence", False, "Reports for absent surfaces retained without promotion"),
)


def validate_topology(project_root: str | Path) -> list[str]:
    """Return relative paths missing from the declared topology."""

    root = Path(project_root).resolve()
    return [spec.relative_path for spec in COMPONENTS if not (root / spec.relative_path).exists()]
