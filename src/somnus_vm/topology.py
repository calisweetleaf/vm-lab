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
    ComponentSpec(
        "protocol_contracts",
        RuntimeBoundary.SHARED,
        Disposition.LIVE,
        "src/somnus_protocol",
        True,
        "Canonical pure, versioned VM, agent, control, and event authority",
    ),
    ComponentSpec(
        "protocol_compatibility",
        RuntimeBoundary.SHARED,
        Disposition.LIVE,
        "src/somnus_vm/contracts",
        True,
        "Historical import compatibility re-exports; owns no protocol schema",
    ),
    ComponentSpec(
        "host_planning_api",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/__init__.py",
        True,
        "Public facade restricted to non-mutating planning exports",
    ),
    ComponentSpec(
        "host_port_allocator",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/ports.py",
        True,
        "Ephemeral loopback availability observation without durable reservation",
    ),
    ComponentSpec(
        "host_qemu_plan_builder",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/qemu.py",
        True,
        "Shell-free structured QEMU argv planning without process ownership",
    ),
    ComponentSpec(
        "host_planner",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/planner.py",
        True,
        "Non-mutating composition of ports, VM intent, and QEMU launch plans",
    ),
    ComponentSpec(
        "unix_transport",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/transport.py",
        False,
        "Bounded AF_UNIX framing and kernel SO_PEERCRED authentication",
    ),
    ComponentSpec(
        "unix_control_client",
        RuntimeBoundary.OPERATOR,
        Disposition.LIVE,
        "src/somnus_vm/client.py",
        False,
        "Identity-matched one-request local control client",
    ),
    ComponentSpec(
        "unix_control_daemon",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/daemon.py",
        False,
        "Private AF_UNIX listener, peer authentication, and bounded dispatch shell",
    ),
    ComponentSpec(
        "daemon_runtime",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/daemon_runtime.py",
        False,
        "Explicit single-owner composition root for daemon, registry, and storage",
    ),
    ComponentSpec(
        "sqlite_registry",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/registry.py",
        False,
        "Single-writer schema-v3 SQLite authority and durable operation journal",
    ),
    ComponentSpec(
        "registry_mutation_service",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/service.py",
        False,
        "Authenticated serialized mutation, checkpoint, idempotency, and recovery owner",
    ),
    ComponentSpec(
        "qemu_img_provenance",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/images.py",
        False,
        "Manifest-bound qemu-img inspection, provenance, and primitive creation",
    ),
    ComponentSpec(
        "persistent_image_storage",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/storage.py",
        False,
        "Daemon-owned immutable-base and sparse-overlay publication with recovery",
    ),
    ComponentSpec(
        "native_exec_guard",
        RuntimeBoundary.HOST,
        Disposition.LIVE,
        "src/somnus_vm/host/exec_guard.py",
        False,
        "Internal Linux parent-death guard for daemon-owned qemu-img subprocesses",
    ),
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
