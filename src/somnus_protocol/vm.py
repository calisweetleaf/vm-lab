"""Pure VM declaration primitives shared across Somnus authority planes.

Source: promoted ``somnus_vm.contracts.vm`` v0.1 boundary
Composed: 2026-08-05
Purpose: Establishes strict, dependency-free VM intent and port schemas before
    the host-owned lifecycle record and secret migration are composed.
IO: None. Paths are validated lexical references; this module never touches
    the filesystem, network, processes, environment, or host/guest packages.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePath, PurePosixPath
from types import MappingProxyType
from typing import Final
from uuid import UUID, uuid4

from ._validation import (
    ProtocolValidationError,
    canonical_json,
    encode_datetime,
    require_bool,
    require_datetime,
    require_int,
    require_str,
    require_uuid,
    strict_object,
)
from .version import SCHEMA_VERSION, SchemaVersionError, require_schema_version


class VMContractError(ProtocolValidationError):
    """Raised when a VM protocol object violates its exact contract."""


class VMTransitionError(RuntimeError):
    """Raised when a lifecycle transition lacks authority or required evidence."""


class VMMigrationError(VMContractError):
    """Raised when an older record cannot be migrated without invented truth."""


_VM_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$"
)


def _vm_error(action: object) -> object:
    """Translate shared validation failures into the VM-domain error type."""

    try:
        return action()  # type: ignore[operator]
    except ProtocolValidationError as exc:
        raise VMContractError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class VMPorts:
    """Unique unprivileged host ports forwarded into one guest VM."""

    ssh: int
    agent: int
    vnc: int

    def __post_init__(self) -> None:
        """Reject coercible values, privileged ports, and role collisions."""

        values = (
            _vm_error(
                lambda: require_int(
                    self.ssh,
                    "ports.ssh",
                    minimum=1024,
                    maximum=65535,
                )
            ),
            _vm_error(
                lambda: require_int(
                    self.agent,
                    "ports.agent",
                    minimum=1024,
                    maximum=65535,
                )
            ),
            _vm_error(
                lambda: require_int(
                    self.vnc,
                    "ports.vnc",
                    minimum=1024,
                    maximum=65535,
                )
            ),
        )
        if len(set(values)) != len(values):
            raise VMContractError("ports: ssh, agent, and vnc must be unique")

    def to_dict(self) -> dict[str, int]:
        """Return the one canonical JSON-compatible port mapping."""

        return {"ssh": self.ssh, "agent": self.agent, "vnc": self.vnc}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "VMPorts":
        """Decode an exact mapping without coercion or ignored intent."""

        table = _vm_error(
            lambda: strict_object(
                payload,
                required={"ssh", "agent", "vnc"},
                optional=set(),
                label="ports",
            )
        )
        assert isinstance(table, dict)
        return cls(
            ssh=_vm_error(
                lambda: require_int(
                    table["ssh"],
                    "ports.ssh",
                    minimum=1024,
                    maximum=65535,
                )
            ),
            agent=_vm_error(
                lambda: require_int(
                    table["agent"],
                    "ports.agent",
                    minimum=1024,
                    maximum=65535,
                )
            ),
            vnc=_vm_error(
                lambda: require_int(
                    table["vnc"],
                    "ports.vnc",
                    minimum=1024,
                    maximum=65535,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class VMDefinition:
    """Immutable operator intent consumed by host-side QEMU planning.

    ``disk_path`` is a lexical storage reference. Requiring an absolute path
    prevents cwd-dependent intent, but existence, ownership, format, and
    backing-chain truth remain host/storage responsibilities.
    """

    name: str
    disk_path: str
    memory_mib: int = 4096
    vcpus: int = 2
    enable_kvm: bool = True

    def __post_init__(self) -> None:
        """Validate direct construction with the same strictness as decoding."""

        name = _vm_error(
            lambda: require_str(
                self.name,
                "definition.name",
                min_length=1,
                max_length=63,
            )
        )
        assert isinstance(name, str)
        if _VM_NAME_PATTERN.fullmatch(name) is None:
            raise VMContractError(
                "definition.name: use 1-63 letters, digits, '.', '_', or '-'"
            )
        disk_path = _vm_error(
            lambda: require_str(
                self.disk_path,
                "definition.disk_path",
                min_length=1,
                max_length=4096,
            )
        )
        assert isinstance(disk_path, str)
        if not PurePath(disk_path).is_absolute():
            raise VMContractError("definition.disk_path: must be absolute")
        _vm_error(
            lambda: require_int(
                self.memory_mib,
                "definition.memory_mib",
                minimum=256,
                maximum=1_048_576,
            )
        )
        _vm_error(
            lambda: require_int(
                self.vcpus,
                "definition.vcpus",
                minimum=1,
                maximum=256,
            )
        )
        _vm_error(lambda: require_bool(self.enable_kvm, "definition.enable_kvm"))

    def to_dict(self) -> dict[str, object]:
        """Return the v0-compatible canonical definition mapping."""

        return {
            "name": self.name,
            "disk_path": self.disk_path,
            "memory_mib": self.memory_mib,
            "vcpus": self.vcpus,
            "enable_kvm": self.enable_kvm,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "VMDefinition":
        """Decode an exact definition mapping without type coercion."""

        table = _vm_error(
            lambda: strict_object(
                payload,
                required={
                    "name",
                    "disk_path",
                    "memory_mib",
                    "vcpus",
                    "enable_kvm",
                },
                optional=set(),
                label="definition",
            )
        )
        assert isinstance(table, dict)
        return cls(
            name=_vm_error(
                lambda: require_str(
                    table["name"],
                    "definition.name",
                    min_length=1,
                    max_length=63,
                )
            ),
            disk_path=_vm_error(
                lambda: require_str(
                    table["disk_path"],
                    "definition.disk_path",
                    min_length=1,
                    max_length=4096,
                )
            ),
            memory_mib=_vm_error(
                lambda: require_int(
                    table["memory_mib"],
                    "definition.memory_mib",
                    minimum=256,
                    maximum=1_048_576,
                )
            ),
            vcpus=_vm_error(
                lambda: require_int(
                    table["vcpus"],
                    "definition.vcpus",
                    minimum=1,
                    maximum=256,
                )
            ),
            enable_kvm=_vm_error(
                lambda: require_bool(
                    table["enable_kvm"],
                    "definition.enable_kvm",
                )
            ),
        )


class VMState(str, Enum):
    """Observed lifecycle states for one persistent AIPC generation."""

    DECLARED = "declared"
    PROVISIONING = "provisioning"
    PROVISIONED = "provisioned"
    BOOTING = "booting"
    QMP_RUNNING = "qmp_running"
    GUEST_READY = "guest_ready"
    QUIESCING = "quiescing"
    QUIESCED = "quiesced"
    SNAPSHOTTING = "snapshotting"
    ROLLING_BACK = "rolling_back"
    RECONCILING = "reconciling"
    RECOVERING = "recovering"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    DESTROYED = "destroyed"

    # Python-level compatibility names. Legacy wire labels are migrated
    # explicitly because they did not prove these stronger observations.
    STARTING = "booting"
    RUNNING = "qmp_running"
    READY = "guest_ready"


class ObservationSource(str, Enum):
    """Measured authority behind a typed runtime observation.

    ``DAEMON`` is the only aggregate source: it means the control-plane owner
    joined the separately measured process, QMP, and guest-agent facts encoded
    by the observation profile.  Operator assertions are intentionally absent.
    """

    DAEMON = "daemon"
    QMP = "qmp"
    GUEST_AGENT = "guest_agent"
    PROCESS = "process"


ALLOWED_TRANSITIONS: Final[Mapping[VMState, frozenset[VMState]]] = (
    MappingProxyType(
        {
            VMState.DECLARED: frozenset(
                {VMState.PROVISIONING, VMState.ERROR, VMState.DESTROYED}
            ),
            VMState.PROVISIONING: frozenset(
                {VMState.PROVISIONED, VMState.ERROR}
            ),
            VMState.PROVISIONED: frozenset(
                {VMState.BOOTING, VMState.ERROR, VMState.DESTROYED}
            ),
            VMState.BOOTING: frozenset(
                {VMState.QMP_RUNNING, VMState.STOPPING, VMState.ERROR}
            ),
            VMState.QMP_RUNNING: frozenset(
                {
                    VMState.GUEST_READY,
                    VMState.RECONCILING,
                    VMState.STOPPING,
                    VMState.ERROR,
                }
            ),
            VMState.GUEST_READY: frozenset(
                {
                    VMState.QUIESCING,
                    VMState.RECONCILING,
                    VMState.STOPPING,
                    VMState.ERROR,
                }
            ),
            VMState.QUIESCING: frozenset(
                {VMState.QUIESCED, VMState.ERROR}
            ),
            VMState.QUIESCED: frozenset(
                {
                    VMState.GUEST_READY,
                    VMState.SNAPSHOTTING,
                    VMState.STOPPING,
                    VMState.ERROR,
                }
            ),
            VMState.SNAPSHOTTING: frozenset(
                {VMState.QUIESCED, VMState.ERROR}
            ),
            VMState.ROLLING_BACK: frozenset(
                {VMState.STOPPED, VMState.ERROR}
            ),
            VMState.RECONCILING: frozenset(
                {
                    VMState.QMP_RUNNING,
                    VMState.GUEST_READY,
                    VMState.STOPPED,
                    VMState.ERROR,
                }
            ),
            VMState.RECOVERING: frozenset(
                {
                    VMState.BOOTING,
                    VMState.STOPPED,
                    VMState.ERROR,
                    VMState.DESTROYED,
                }
            ),
            VMState.STOPPING: frozenset(
                {VMState.STOPPED, VMState.ERROR}
            ),
            VMState.STOPPED: frozenset(
                {
                    VMState.BOOTING,
                    VMState.ROLLING_BACK,
                    VMState.RECONCILING,
                    VMState.ERROR,
                    VMState.DESTROYED,
                }
            ),
            VMState.ERROR: frozenset(
                {
                    VMState.RECOVERING,
                    VMState.STOPPING,
                    VMState.STOPPED,
                    VMState.DESTROYED,
                }
            ),
            VMState.DESTROYED: frozenset(),
        }
    )
)

IDEMPOTENT_STATES: Final[frozenset[VMState]] = frozenset(
    {
        VMState.GUEST_READY,
        VMState.RECONCILING,
        VMState.RECOVERING,
        VMState.STOPPING,
        VMState.STOPPED,
    }
)

_ERROR_EVIDENCE: Final[frozenset[str]] = frozenset(
    {"cause_event_id", "error_code"}
)
_DESTROY_EVIDENCE: Final[frozenset[str]] = frozenset(
    {"operation_id", "ownership_verified", "resources_removed"}
)
_QMP_RUNNING_EVIDENCE: Final[frozenset[str]] = frozenset(
    {
        "boot_id",
        "observation_id",
        "qmp_greeting",
        "qmp_status",
        "qmp_uuid",
    }
)
_GUEST_READY_EVIDENCE: Final[frozenset[str]] = frozenset(
    {"agent_authenticated", "agent_ready", "boot_id", "observation_id"}
)
_QUIESCED_EVIDENCE: Final[frozenset[str]] = frozenset(
    {"guest_quiesced", "observation_id", "operation_id"}
)
_OBSERVED_STOPPED_EVIDENCE: Final[frozenset[str]] = frozenset(
    {"observation_id", "process_absent", "qmp_unreachable"}
)


# Evidence belongs to an edge, not merely its target.  For example a normal
# STOPPING -> STOPPED completion has a child exit status, while reconciliation
# establishes STOPPED by proving both process absence and QMP unreachability.
# Collapsing those into one target-level requirement would either reject valid
# recovery or accept a false success.
_TRANSITION_FACT_REQUIREMENTS: Final[
    Mapping[tuple[VMState, VMState], frozenset[str]]
] = MappingProxyType(
    {
        (VMState.DECLARED, VMState.PROVISIONING): frozenset({"operation_id"}),
        (VMState.DECLARED, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.DECLARED, VMState.DESTROYED): _DESTROY_EVIDENCE,
        (VMState.PROVISIONING, VMState.PROVISIONED): frozenset(
            {"image_sha256", "operation_id", "storage_id"}
        ),
        (VMState.PROVISIONING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.PROVISIONED, VMState.BOOTING): frozenset(
            {"boot_id", "operation_id"}
        ),
        (VMState.PROVISIONED, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.PROVISIONED, VMState.DESTROYED): _DESTROY_EVIDENCE,
        (VMState.BOOTING, VMState.QMP_RUNNING): _QMP_RUNNING_EVIDENCE,
        (VMState.BOOTING, VMState.STOPPING): frozenset({"operation_id"}),
        (VMState.BOOTING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.QMP_RUNNING, VMState.GUEST_READY): _GUEST_READY_EVIDENCE,
        (VMState.QMP_RUNNING, VMState.RECONCILING): frozenset(
            {"observation_id"}
        ),
        (VMState.QMP_RUNNING, VMState.STOPPING): frozenset({"operation_id"}),
        (VMState.QMP_RUNNING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.GUEST_READY, VMState.QUIESCING): frozenset(
            {"agent_authenticated", "operation_id"}
        ),
        (VMState.GUEST_READY, VMState.RECONCILING): frozenset(
            {"observation_id"}
        ),
        (VMState.GUEST_READY, VMState.STOPPING): frozenset({"operation_id"}),
        (VMState.GUEST_READY, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.QUIESCING, VMState.QUIESCED): frozenset(
            _QUIESCED_EVIDENCE
        ),
        (VMState.QUIESCING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.QUIESCED, VMState.GUEST_READY): _GUEST_READY_EVIDENCE,
        (VMState.QUIESCED, VMState.SNAPSHOTTING): frozenset(
            {"guest_quiesced", "operation_id", "snapshot_id"}
        ),
        (VMState.QUIESCED, VMState.STOPPING): frozenset({"operation_id"}),
        (VMState.QUIESCED, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.SNAPSHOTTING, VMState.QUIESCED): frozenset(
            {
                "guest_quiesced",
                "observation_id",
                "operation_id",
                "snapshot_committed",
                "snapshot_id",
            }
        ),
        (VMState.SNAPSHOTTING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.ROLLING_BACK, VMState.STOPPED): frozenset(
            {
                "operation_id",
                "observation_id",
                "process_absent",
                "qmp_unreachable",
                "rollback_verified",
                "snapshot_id",
            }
        ),
        (VMState.ROLLING_BACK, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.RECONCILING, VMState.QMP_RUNNING): (
            _QMP_RUNNING_EVIDENCE
        ),
        (VMState.RECONCILING, VMState.GUEST_READY): (
            _QMP_RUNNING_EVIDENCE | _GUEST_READY_EVIDENCE
        ),
        (VMState.RECONCILING, VMState.STOPPED): _OBSERVED_STOPPED_EVIDENCE,
        (VMState.RECONCILING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.RECOVERING, VMState.BOOTING): frozenset(
            {"boot_id", "operation_id"}
        ),
        (VMState.RECOVERING, VMState.STOPPED): _OBSERVED_STOPPED_EVIDENCE,
        (VMState.RECOVERING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.RECOVERING, VMState.DESTROYED): _DESTROY_EVIDENCE,
        (VMState.STOPPING, VMState.STOPPED): frozenset(
            {
                "observation_id",
                "operation_id",
                "process_absent",
                "process_exit",
                "qmp_unreachable",
            }
        ),
        (VMState.STOPPING, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.STOPPED, VMState.BOOTING): frozenset(
            {"boot_id", "operation_id"}
        ),
        (VMState.STOPPED, VMState.ROLLING_BACK): frozenset(
            {"operation_id", "snapshot_id", "vm_stopped"}
        ),
        (VMState.STOPPED, VMState.RECONCILING): frozenset(
            {"observation_id"}
        ),
        (VMState.STOPPED, VMState.ERROR): _ERROR_EVIDENCE,
        (VMState.STOPPED, VMState.DESTROYED): _DESTROY_EVIDENCE,
        (VMState.ERROR, VMState.RECOVERING): frozenset(
            {"cause_event_id", "operation_id"}
        ),
        (VMState.ERROR, VMState.STOPPING): frozenset({"operation_id"}),
        (VMState.ERROR, VMState.STOPPED): (
            _OBSERVED_STOPPED_EVIDENCE | {"cause_event_id"}
        ),
        (VMState.ERROR, VMState.DESTROYED): _DESTROY_EVIDENCE,
    }
)
TRANSITION_REQUIREMENTS: Final[
    Mapping[tuple[VMState, VMState], frozenset[str]]
] = MappingProxyType(
    {
        edge: requirements | {"intent_digest"}
        for edge, requirements in _TRANSITION_FACT_REQUIREMENTS.items()
    }
)

_LEGAL_EDGES: Final[frozenset[tuple[VMState, VMState]]] = frozenset(
    (previous, target)
    for previous, targets in ALLOWED_TRANSITIONS.items()
    for target in targets
)
if frozenset(TRANSITION_REQUIREMENTS) != _LEGAL_EDGES:
    raise RuntimeError(
        "VM transition and evidence tables must describe exactly the same edges"
    )

_MACHINE_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_.-]{0,63}$"
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_PROCESS_EXIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:exit:(?:0|[1-9][0-9]{0,2})|signal:[1-9][0-9]?)$"
)
_TRUE_EVIDENCE_FACTS: Final[frozenset[str]] = frozenset(
    {
        "agent_authenticated",
        "agent_ready",
        "guest_quiesced",
        "ownership_verified",
        "process_absent",
        "qmp_unreachable",
        "resources_removed",
        "rollback_verified",
        "snapshot_committed",
        "vm_stopped",
    }
)
_UUID_EVIDENCE_FACTS: Final[frozenset[str]] = frozenset(
    {
        "boot_id",
        "cause_event_id",
        "observation_id",
        "operation_id",
        "qmp_uuid",
        "snapshot_id",
        "storage_id",
    }
)
_KNOWN_EVIDENCE_FACTS: Final[frozenset[str]] = frozenset().union(
    *(requirements for requirements in TRANSITION_REQUIREMENTS.values())
)
_LEGACY_SECRET_NOTE: Final[str] = "legacy_agent_secret_reprovision_required"
_ALLOWED_MIGRATION_NOTES: Final[frozenset[str]] = frozenset(
    {_LEGACY_SECRET_NOTE}
)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _validate_evidence_fact(key: str, value: object) -> str:
    """Validate one closed-vocabulary physical transition fact.

    Boolean observations deliberately use the single wire value ``"true"``.
    Accepting arbitrary non-empty strings here would allow values such as
    ``"false"`` or ``"not checked"`` to satisfy a success gate.
    """

    if key not in _KNOWN_EVIDENCE_FACTS:
        raise VMContractError(f"transition evidence fact {key!r} is unsupported")
    if key in _TRUE_EVIDENCE_FACTS:
        return _vm_error(
            lambda: require_str(
                value,
                f"transition_evidence.facts.{key}",
                min_length=4,
                max_length=4,
                pattern=r"true",
            )
        )  # type: ignore[return-value]
    if key in _UUID_EVIDENCE_FACTS:
        parsed = _vm_error(
            lambda: require_uuid(value, f"transition_evidence.facts.{key}")
        )
        assert isinstance(parsed, UUID)
        return str(parsed)
    if key in {"image_sha256", "intent_digest"}:
        return _vm_error(
            lambda: require_str(
                value,
                f"transition_evidence.facts.{key}",
                min_length=64,
                max_length=64,
                pattern=_SHA256_PATTERN,
            )
        )  # type: ignore[return-value]
    if key == "error_code":
        return _vm_error(
            lambda: require_str(
                value,
                "transition_evidence.facts.error_code",
                min_length=1,
                max_length=64,
                pattern=_MACHINE_TOKEN_PATTERN,
            )
        )  # type: ignore[return-value]
    if key == "qmp_status":
        return _vm_error(
            lambda: require_str(
                value,
                "transition_evidence.facts.qmp_status",
                min_length=7,
                max_length=7,
                pattern=r"running",
            )
        )  # type: ignore[return-value]
    if key == "qmp_greeting":
        return _vm_error(
            lambda: require_str(
                value,
                "transition_evidence.facts.qmp_greeting",
                min_length=71,
                max_length=71,
                pattern=r"sha256:[0-9a-f]{64}",
            )
        )  # type: ignore[return-value]
    if key == "process_exit":
        text = _vm_error(
            lambda: require_str(
                value,
                "transition_evidence.facts.process_exit",
                min_length=6,
                max_length=10,
                pattern=_PROCESS_EXIT_PATTERN,
            )
        )
        assert isinstance(text, str)
        kind, number_text = text.split(":", 1)
        upper = 255 if kind == "exit" else 64
        number = int(number_text)
        if number > upper:
            raise VMContractError(
                f"transition_evidence.facts.process_exit {kind} value "
                f"must be at most {upper}"
            )
        return text
    raise VMContractError(
        f"transition evidence fact {key!r} lacks a protocol validator"
    )


def _optional_uuid(value: object, label: str) -> UUID | None:
    if value is None:
        return None
    return _vm_error(lambda: require_uuid(value, label))  # type: ignore[return-value]


def _optional_str(
    value: object,
    label: str,
    *,
    max_length: int = 4096,
) -> str | None:
    if value is None:
        return None
    return _vm_error(
        lambda: require_str(
            value,
            label,
            min_length=1,
            max_length=max_length,
        )
    )  # type: ignore[return-value]


def _validated_absolute_path(
    value: object,
    label: str,
    *,
    allow_empty: bool,
    unix_socket: bool = False,
) -> str:
    """Validate a lexical Linux path without touching the filesystem."""

    maximum = 103 if unix_socket else 4096
    text = _vm_error(
        lambda: require_str(
            value,
            label,
            min_length=0 if allow_empty else 1,
            max_length=maximum,
        )
    )
    assert isinstance(text, str)
    if not text:
        return text
    if len(text.encode("utf-8")) > maximum:
        raise VMContractError(
            f"{label} exceeds the {maximum}-byte encoded path limit"
        )
    if not PurePosixPath(text).is_absolute() or text == "/":
        raise VMContractError(f"{label} must be an absolute non-root POSIX path")
    components = text.split("/")[1:]
    if any(component in {"", ".", ".."} for component in components):
        raise VMContractError(
            f"{label} cannot contain empty, '.' or '..' path components"
        )
    if unix_socket and "," in text:
        raise VMContractError(f"{label} cannot contain the QEMU ',' delimiter")
    return text


def _intent_digest(
    *,
    vm_id: UUID,
    generation: int,
    definition: VMDefinition,
    ports: VMPorts,
    qmp_socket: str,
    pid_file: str,
    log_path: str,
    created_at: datetime,
    migration_source: str | None,
    migration_notes: tuple[str, ...],
) -> str:
    """Hash declaration, planned runtime, and migration provenance."""

    payload = {
        "vm_id": str(vm_id),
        "generation": generation,
        "definition": definition.to_dict(),
        "ports": ports.to_dict(),
        "qmp_socket": qmp_socket,
        "pid_file": pid_file,
        "log_path": log_path,
        "created_at": encode_datetime(created_at, "record.created_at"),
        "migration_source": migration_source,
        "migration_notes": list(migration_notes),
    }
    encoded = canonical_json(
        payload,
        "VM declaration identity",
        max_depth=3,
        max_items=32,
        max_string=4096,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _process_core_identity(
    process: "ProcessIdentity",
) -> tuple[int, int, str, str]:
    """Return the process identity fields stable across fresh observations."""

    return (
        process.pid,
        process.start_time_ticks,
        process.executable,
        process.executable_sha256,
    )


@dataclass(frozen=True, slots=True)
class VMResourceSpec:
    """Bounded compute resources requested for one AIPC."""

    memory_mib: int
    vcpus: int

    def __post_init__(self) -> None:
        _vm_error(
            lambda: require_int(
                self.memory_mib,
                "resources.memory_mib",
                minimum=256,
                maximum=1_048_576,
            )
        )
        _vm_error(
            lambda: require_int(
                self.vcpus,
                "resources.vcpus",
                minimum=1,
                maximum=256,
            )
        )

    def to_dict(self) -> dict[str, int]:
        return {"memory_mib": self.memory_mib, "vcpus": self.vcpus}

    @classmethod
    def from_dict(cls, payload: object) -> "VMResourceSpec":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {"memory_mib", "vcpus"},
                label="resources",
            )
        )
        assert isinstance(table, dict)
        return cls(
            memory_mib=_vm_error(
                lambda: require_int(
                    table["memory_mib"],
                    "resources.memory_mib",
                    minimum=256,
                    maximum=1_048_576,
                )
            ),
            vcpus=_vm_error(
                lambda: require_int(
                    table["vcpus"],
                    "resources.vcpus",
                    minimum=1,
                    maximum=256,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class StorageReference:
    """Pure reference to host-owned storage; never a filesystem owner."""

    storage_id: UUID
    path: str
    virtual_size_bytes: int
    format: str = "qcow2"
    backing_storage_id: UUID | None = None
    read_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "storage_id",
            _vm_error(
                lambda: require_uuid(self.storage_id, "storage.storage_id")
            ),
        )
        path = _vm_error(
            lambda: require_str(
                self.path,
                "storage.path",
                min_length=1,
                max_length=4096,
            )
        )
        assert isinstance(path, str)
        if not PurePath(path).is_absolute():
            raise VMContractError("storage.path: must be absolute")
        _vm_error(
            lambda: require_int(
                self.virtual_size_bytes,
                "storage.virtual_size_bytes",
                minimum=1,
                maximum=2**63 - 1,
            )
        )
        _vm_error(
            lambda: require_str(
                self.format,
                "storage.format",
                min_length=1,
                max_length=32,
                pattern=_MACHINE_TOKEN_PATTERN,
            )
        )
        object.__setattr__(
            self,
            "backing_storage_id",
            _optional_uuid(
                self.backing_storage_id,
                "storage.backing_storage_id",
            ),
        )
        if self.backing_storage_id == self.storage_id:
            raise VMContractError("storage cannot back itself")
        _vm_error(lambda: require_bool(self.read_only, "storage.read_only"))

    def to_dict(self) -> dict[str, object]:
        return {
            "storage_id": str(self.storage_id),
            "path": self.path,
            "virtual_size_bytes": self.virtual_size_bytes,
            "format": self.format,
            "backing_storage_id": (
                str(self.backing_storage_id)
                if self.backing_storage_id is not None
                else None
            ),
            "read_only": self.read_only,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "StorageReference":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {
                    "storage_id",
                    "path",
                    "virtual_size_bytes",
                    "format",
                    "backing_storage_id",
                    "read_only",
                },
                label="storage",
            )
        )
        assert isinstance(table, dict)
        return cls(
            storage_id=_vm_error(
                lambda: require_uuid(
                    table["storage_id"],
                    "storage.storage_id",
                )
            ),
            path=_vm_error(
                lambda: require_str(
                    table["path"],
                    "storage.path",
                    max_length=4096,
                )
            ),
            virtual_size_bytes=_vm_error(
                lambda: require_int(
                    table["virtual_size_bytes"],
                    "storage.virtual_size_bytes",
                    minimum=1,
                    maximum=2**63 - 1,
                )
            ),
            format=_vm_error(
                lambda: require_str(
                    table["format"],
                    "storage.format",
                    max_length=32,
                    pattern=_MACHINE_TOKEN_PATTERN,
                )
            ),
            backing_storage_id=_optional_uuid(
                table["backing_storage_id"],
                "storage.backing_storage_id",
            ),
            read_only=_vm_error(
                lambda: require_bool(
                    table["read_only"],
                    "storage.read_only",
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ImageProvenance:
    """Measured source identity for an image used to create AIPC storage."""

    image_id: UUID
    source: str
    sha256: str
    format: str
    virtual_size_bytes: int
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "image_id",
            _vm_error(lambda: require_uuid(self.image_id, "image.image_id")),
        )
        _vm_error(
            lambda: require_str(
                self.source,
                "image.source",
                min_length=1,
                max_length=4096,
            )
        )
        _vm_error(
            lambda: require_str(
                self.sha256,
                "image.sha256",
                min_length=64,
                max_length=64,
                pattern=_SHA256_PATTERN,
            )
        )
        _vm_error(
            lambda: require_str(
                self.format,
                "image.format",
                max_length=32,
                pattern=_MACHINE_TOKEN_PATTERN,
            )
        )
        _vm_error(
            lambda: require_int(
                self.virtual_size_bytes,
                "image.virtual_size_bytes",
                minimum=1,
                maximum=2**63 - 1,
            )
        )
        object.__setattr__(
            self,
            "created_at",
            _vm_error(
                lambda: require_datetime(self.created_at, "image.created_at")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "image_id": str(self.image_id),
            "source": self.source,
            "sha256": self.sha256,
            "format": self.format,
            "virtual_size_bytes": self.virtual_size_bytes,
            "created_at": encode_datetime(self.created_at, "image.created_at"),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ImageProvenance":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {
                    "image_id",
                    "source",
                    "sha256",
                    "format",
                    "virtual_size_bytes",
                    "created_at",
                },
                label="image",
            )
        )
        assert isinstance(table, dict)
        return cls(
            image_id=_vm_error(
                lambda: require_uuid(table["image_id"], "image.image_id")
            ),
            source=_vm_error(
                lambda: require_str(
                    table["source"],
                    "image.source",
                    max_length=4096,
                )
            ),
            sha256=_vm_error(
                lambda: require_str(
                    table["sha256"],
                    "image.sha256",
                    min_length=64,
                    max_length=64,
                    pattern=_SHA256_PATTERN,
                )
            ),
            format=_vm_error(
                lambda: require_str(
                    table["format"],
                    "image.format",
                    max_length=32,
                    pattern=_MACHINE_TOKEN_PATTERN,
                )
            ),
            virtual_size_bytes=_vm_error(
                lambda: require_int(
                    table["virtual_size_bytes"],
                    "image.virtual_size_bytes",
                    minimum=1,
                    maximum=2**63 - 1,
                )
            ),
            created_at=_vm_error(
                lambda: require_datetime(
                    table["created_at"],
                    "image.created_at",
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Observed process identity sufficient to reject PID-reuse assumptions."""

    pid: int
    start_time_ticks: int
    executable: str
    executable_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _vm_error(
            lambda: require_int(
                self.pid,
                "process.pid",
                minimum=2,
                maximum=2**31 - 1,
            )
        )
        _vm_error(
            lambda: require_int(
                self.start_time_ticks,
                "process.start_time_ticks",
                minimum=1,
                maximum=2**63 - 1,
            )
        )
        executable = _vm_error(
            lambda: require_str(
                self.executable,
                "process.executable",
                min_length=1,
                max_length=4096,
            )
        )
        assert isinstance(executable, str)
        _validated_absolute_path(
            executable,
            "process.executable",
            allow_empty=False,
        )
        _vm_error(
            lambda: require_str(
                self.executable_sha256,
                "process.executable_sha256",
                min_length=64,
                max_length=64,
                pattern=_SHA256_PATTERN,
            )
        )
        object.__setattr__(
            self,
            "observed_at",
            _vm_error(
                lambda: require_datetime(
                    self.observed_at,
                    "process.observed_at",
                )
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "start_time_ticks": self.start_time_ticks,
            "executable": self.executable,
            "executable_sha256": self.executable_sha256,
            "observed_at": encode_datetime(
                self.observed_at,
                "process.observed_at",
            ),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ProcessIdentity":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {
                    "pid",
                    "start_time_ticks",
                    "executable",
                    "executable_sha256",
                    "observed_at",
                },
                label="process",
            )
        )
        assert isinstance(table, dict)
        return cls(
            pid=_vm_error(
                lambda: require_int(
                    table["pid"],
                    "process.pid",
                    minimum=2,
                    maximum=2**31 - 1,
                )
            ),
            start_time_ticks=_vm_error(
                lambda: require_int(
                    table["start_time_ticks"],
                    "process.start_time_ticks",
                    minimum=1,
                    maximum=2**63 - 1,
                )
            ),
            executable=_vm_error(
                lambda: require_str(
                    table["executable"],
                    "process.executable",
                    max_length=4096,
                )
            ),
            executable_sha256=_vm_error(
                lambda: require_str(
                    table["executable_sha256"],
                    "process.executable_sha256",
                    min_length=64,
                    max_length=64,
                    pattern=_SHA256_PATTERN,
                )
            ),
            observed_at=_vm_error(
                lambda: require_datetime(
                    table["observed_at"],
                    "process.observed_at",
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class SnapshotReference:
    """Identity and lineage reference for one stopped-VM snapshot."""

    snapshot_id: UUID
    vm_id: UUID
    generation: int
    path: str
    created_at: datetime
    parent_snapshot_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "snapshot_id",
            _vm_error(
                lambda: require_uuid(
                    self.snapshot_id,
                    "snapshot.snapshot_id",
                )
            ),
        )
        object.__setattr__(
            self,
            "vm_id",
            _vm_error(lambda: require_uuid(self.vm_id, "snapshot.vm_id")),
        )
        _vm_error(
            lambda: require_int(
                self.generation,
                "snapshot.generation",
                minimum=0,
                maximum=2**63 - 1,
            )
        )
        path = _vm_error(
            lambda: require_str(
                self.path,
                "snapshot.path",
                min_length=1,
                max_length=4096,
            )
        )
        assert isinstance(path, str)
        if not PurePath(path).is_absolute():
            raise VMContractError("snapshot.path: must be absolute")
        object.__setattr__(
            self,
            "created_at",
            _vm_error(
                lambda: require_datetime(
                    self.created_at,
                    "snapshot.created_at",
                )
            ),
        )
        object.__setattr__(
            self,
            "parent_snapshot_id",
            _optional_uuid(
                self.parent_snapshot_id,
                "snapshot.parent_snapshot_id",
            ),
        )
        if self.parent_snapshot_id == self.snapshot_id:
            raise VMContractError("snapshot cannot be its own parent")

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": str(self.snapshot_id),
            "vm_id": str(self.vm_id),
            "generation": self.generation,
            "path": self.path,
            "created_at": encode_datetime(
                self.created_at,
                "snapshot.created_at",
            ),
            "parent_snapshot_id": (
                str(self.parent_snapshot_id)
                if self.parent_snapshot_id is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "SnapshotReference":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {
                    "snapshot_id",
                    "vm_id",
                    "generation",
                    "path",
                    "created_at",
                    "parent_snapshot_id",
                },
                label="snapshot",
            )
        )
        assert isinstance(table, dict)
        return cls(
            snapshot_id=_vm_error(
                lambda: require_uuid(
                    table["snapshot_id"],
                    "snapshot.snapshot_id",
                )
            ),
            vm_id=_vm_error(
                lambda: require_uuid(table["vm_id"], "snapshot.vm_id")
            ),
            generation=_vm_error(
                lambda: require_int(
                    table["generation"],
                    "snapshot.generation",
                    minimum=0,
                    maximum=2**63 - 1,
                )
            ),
            path=_vm_error(
                lambda: require_str(
                    table["path"],
                    "snapshot.path",
                    max_length=4096,
                )
            ),
            created_at=_vm_error(
                lambda: require_datetime(
                    table["created_at"],
                    "snapshot.created_at",
                )
            ),
            parent_snapshot_id=_optional_uuid(
                table["parent_snapshot_id"],
                "snapshot.parent_snapshot_id",
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """One typed external observation with a closed source/state profile.

    Raw QMP, guest-agent, and process measurements are deliberately narrower
    than a daemon aggregate.  Only a ``DAEMON`` observation combines them into
    an authoritative lifecycle observation suitable for state promotion.
    """

    observation_id: UUID
    vm_id: UUID
    boot_id: UUID | None
    generation: int
    intent_digest: str
    state: VMState
    source: ObservationSource
    observed_at: datetime
    process: ProcessIdentity | None = None
    qmp_status: str | None = None
    qmp_uuid: UUID | None = None
    qmp_greeting_sha256: str | None = None
    guest_ready: bool | None = None
    guest_quiesced: bool | None = None
    process_absent: bool | None = None
    qmp_unreachable: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_id",
            _vm_error(
                lambda: require_uuid(
                    self.observation_id,
                    "observation.observation_id",
                )
            ),
        )
        object.__setattr__(
            self,
            "vm_id",
            _vm_error(
                lambda: require_uuid(self.vm_id, "observation.vm_id")
            ),
        )
        object.__setattr__(
            self,
            "boot_id",
            _optional_uuid(self.boot_id, "observation.boot_id"),
        )
        _vm_error(
            lambda: require_int(
                self.generation,
                "observation.generation",
                minimum=0,
                maximum=2**63 - 1,
            )
        )
        _vm_error(
            lambda: require_str(
                self.intent_digest,
                "observation.intent_digest",
                min_length=64,
                max_length=64,
                pattern=_SHA256_PATTERN,
            )
        )
        if not isinstance(self.state, VMState):
            raise VMContractError("observation.state must be a VMState")
        if not isinstance(self.source, ObservationSource):
            raise VMContractError(
                "observation.source must be an ObservationSource"
            )
        object.__setattr__(
            self,
            "observed_at",
            _vm_error(
                lambda: require_datetime(
                    self.observed_at,
                    "observation.observed_at",
                )
            ),
        )
        if self.process is not None and not isinstance(
            self.process, ProcessIdentity
        ):
            raise VMContractError(
                "observation.process must be a ProcessIdentity or null"
            )
        if (
            self.process is not None
            and self.process.observed_at > self.observed_at
        ):
            raise VMContractError(
                "observation process identity cannot be measured later than "
                "the containing observation"
            )
        object.__setattr__(
            self,
            "qmp_status",
            _optional_str(
                self.qmp_status,
                "observation.qmp_status",
                max_length=64,
            ),
        )
        object.__setattr__(
            self,
            "qmp_uuid",
            _optional_uuid(self.qmp_uuid, "observation.qmp_uuid"),
        )
        object.__setattr__(
            self,
            "qmp_greeting_sha256",
            (
                _vm_error(
                    lambda: require_str(
                        self.qmp_greeting_sha256,
                        "observation.qmp_greeting_sha256",
                        min_length=64,
                        max_length=64,
                        pattern=_SHA256_PATTERN,
                    )
                )
                if self.qmp_greeting_sha256 is not None
                else None
            ),
        )
        for attribute in (
            "guest_ready",
            "guest_quiesced",
            "process_absent",
            "qmp_unreachable",
        ):
            value = getattr(self, attribute)
            if value is not None:
                _vm_error(
                    lambda value=value, attribute=attribute: require_bool(
                        value,
                        f"observation.{attribute}",
                    )
                )

        qmp_present = (
            self.qmp_status == "running"
            and self.qmp_uuid == self.vm_id
            and self.qmp_greeting_sha256 is not None
        )
        qmp_absent = (
            self.qmp_status is None
            and self.qmp_uuid is None
            and self.qmp_greeting_sha256 is None
        )
        no_absence_claim = (
            self.process_absent is None and self.qmp_unreachable is None
        )

        if self.source is ObservationSource.DAEMON:
            if self.state is VMState.QMP_RUNNING:
                valid = (
                    self.boot_id is not None
                    and self.process is not None
                    and qmp_present
                    and self.guest_ready is None
                    and self.guest_quiesced is None
                    and no_absence_claim
                )
            elif self.state is VMState.GUEST_READY:
                valid = (
                    self.boot_id is not None
                    and self.process is not None
                    and qmp_present
                    and self.guest_ready is True
                    and self.guest_quiesced is None
                    and no_absence_claim
                )
            elif self.state is VMState.QUIESCED:
                valid = (
                    self.boot_id is not None
                    and self.process is not None
                    and qmp_present
                    and self.guest_ready is None
                    and self.guest_quiesced is True
                    and no_absence_claim
                )
            elif self.state is VMState.STOPPED:
                valid = (
                    self.process is None
                    and qmp_absent
                    and self.guest_ready is None
                    and self.guest_quiesced is None
                    and self.process_absent is True
                    and self.qmp_unreachable is True
                )
            else:
                valid = False
        elif self.source is ObservationSource.QMP:
            valid = (
                self.state is VMState.QMP_RUNNING
                and self.boot_id is not None
                and self.process is None
                and qmp_present
                and self.guest_ready is None
                and self.guest_quiesced is None
                and no_absence_claim
            )
        elif self.source is ObservationSource.GUEST_AGENT:
            valid = (
                self.boot_id is not None
                and self.process is None
                and qmp_absent
                and no_absence_claim
                and (
                    (
                        self.state is VMState.GUEST_READY
                        and self.guest_ready is True
                        and self.guest_quiesced is None
                    )
                    or (
                        self.state is VMState.QUIESCED
                        and self.guest_ready is None
                        and self.guest_quiesced is True
                    )
                )
            )
        else:
            valid = (
                self.source is ObservationSource.PROCESS
                and self.state is VMState.BOOTING
                and self.boot_id is not None
                and self.process is not None
                and qmp_absent
                and self.guest_ready is None
                and self.guest_quiesced is None
                and no_absence_claim
            )
        if not valid:
            raise VMContractError(
                "observation source, state, and physical facts contradict the "
                "authoritative observation profile"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": str(self.observation_id),
            "vm_id": str(self.vm_id),
            "boot_id": str(self.boot_id) if self.boot_id is not None else None,
            "generation": self.generation,
            "intent_digest": self.intent_digest,
            "state": self.state.value,
            "source": self.source.value,
            "observed_at": encode_datetime(
                self.observed_at,
                "observation.observed_at",
            ),
            "process": self.process.to_dict() if self.process is not None else None,
            "qmp_status": self.qmp_status,
            "qmp_uuid": str(self.qmp_uuid) if self.qmp_uuid is not None else None,
            "qmp_greeting_sha256": self.qmp_greeting_sha256,
            "guest_ready": self.guest_ready,
            "guest_quiesced": self.guest_quiesced,
            "process_absent": self.process_absent,
            "qmp_unreachable": self.qmp_unreachable,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "RuntimeObservation":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {
                    "observation_id",
                    "vm_id",
                    "boot_id",
                    "generation",
                    "intent_digest",
                    "state",
                    "source",
                    "observed_at",
                    "process",
                    "qmp_status",
                    "qmp_uuid",
                    "qmp_greeting_sha256",
                    "guest_ready",
                    "guest_quiesced",
                    "process_absent",
                    "qmp_unreachable",
                },
                label="observation",
            )
        )
        assert isinstance(table, dict)
        process_payload = table["process"]
        try:
            state = VMState(table["state"])
            source = ObservationSource(table["source"])
        except (TypeError, ValueError) as exc:
            raise VMContractError(
                "observation state/source is unsupported"
            ) from exc
        return cls(
            observation_id=_vm_error(
                lambda: require_uuid(
                    table["observation_id"],
                    "observation.observation_id",
                )
            ),
            vm_id=_vm_error(
                lambda: require_uuid(table["vm_id"], "observation.vm_id")
            ),
            boot_id=_optional_uuid(table["boot_id"], "observation.boot_id"),
            generation=_vm_error(
                lambda: require_int(
                    table["generation"],
                    "observation.generation",
                    minimum=0,
                    maximum=2**63 - 1,
                )
            ),
            intent_digest=_vm_error(
                lambda: require_str(
                    table["intent_digest"],
                    "observation.intent_digest",
                    min_length=64,
                    max_length=64,
                    pattern=_SHA256_PATTERN,
                )
            ),
            state=state,
            source=source,
            observed_at=_vm_error(
                lambda: require_datetime(
                    table["observed_at"],
                    "observation.observed_at",
                )
            ),
            process=(
                ProcessIdentity.from_dict(process_payload)
                if process_payload is not None
                else None
            ),
            qmp_status=_optional_str(
                table["qmp_status"],
                "observation.qmp_status",
                max_length=64,
            ),
            qmp_uuid=_optional_uuid(
                table["qmp_uuid"],
                "observation.qmp_uuid",
            ),
            qmp_greeting_sha256=(
                _vm_error(
                    lambda: require_str(
                        table["qmp_greeting_sha256"],
                        "observation.qmp_greeting_sha256",
                        min_length=64,
                        max_length=64,
                        pattern=_SHA256_PATTERN,
                    )
                )
                if table["qmp_greeting_sha256"] is not None
                else None
            ),
            guest_ready=(
                _vm_error(
                    lambda: require_bool(
                        table["guest_ready"],
                        "observation.guest_ready",
                    )
                )
                if table["guest_ready"] is not None
                else None
            ),
            guest_quiesced=(
                _vm_error(
                    lambda: require_bool(
                        table["guest_quiesced"],
                        "observation.guest_quiesced",
                    )
                )
                if table["guest_quiesced"] is not None
                else None
            ),
            process_absent=(
                _vm_error(
                    lambda: require_bool(
                        table["process_absent"],
                        "observation.process_absent",
                    )
                )
                if table["process_absent"] is not None
                else None
            ),
            qmp_unreachable=(
                _vm_error(
                    lambda: require_bool(
                        table["qmp_unreachable"],
                        "observation.qmp_unreachable",
                    )
                )
                if table["qmp_unreachable"] is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class TransitionCause:
    """Stable causal record required whenever lifecycle enters ERROR."""

    code: str
    detail: str
    occurred_at: datetime
    event_id: UUID

    def __post_init__(self) -> None:
        _vm_error(
            lambda: require_str(
                self.code,
                "cause.code",
                min_length=1,
                max_length=64,
                pattern=_MACHINE_TOKEN_PATTERN,
            )
        )
        _vm_error(
            lambda: require_str(
                self.detail,
                "cause.detail",
                min_length=1,
                max_length=2048,
            )
        )
        object.__setattr__(
            self,
            "occurred_at",
            _vm_error(
                lambda: require_datetime(
                    self.occurred_at,
                    "cause.occurred_at",
                )
            ),
        )
        object.__setattr__(
            self,
            "event_id",
            _vm_error(lambda: require_uuid(self.event_id, "cause.event_id")),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "detail": self.detail,
            "occurred_at": encode_datetime(
                self.occurred_at,
                "cause.occurred_at",
            ),
            "event_id": str(self.event_id),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "TransitionCause":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {"code", "detail", "occurred_at", "event_id"},
                label="cause",
            )
        )
        assert isinstance(table, dict)
        return cls(
            code=_vm_error(
                lambda: require_str(
                    table["code"],
                    "cause.code",
                    max_length=64,
                    pattern=_MACHINE_TOKEN_PATTERN,
                )
            ),
            detail=_vm_error(
                lambda: require_str(
                    table["detail"],
                    "cause.detail",
                    max_length=2048,
                )
            ),
            occurred_at=_vm_error(
                lambda: require_datetime(
                    table["occurred_at"],
                    "cause.occurred_at",
                )
            ),
            event_id=_vm_error(
                lambda: require_uuid(table["event_id"], "cause.event_id")
            ),
        )


@dataclass(frozen=True, slots=True)
class TransitionEvidence:
    """Bounded physical facts authorizing one lifecycle transition."""

    evidence_id: UUID
    observed_at: datetime
    facts: Mapping[str, str]
    vm_id: UUID
    generation: int
    boot_id: UUID | None
    intent_digest: str
    observation: RuntimeObservation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _vm_error(
                lambda: require_uuid(
                    self.evidence_id,
                    "transition_evidence.evidence_id",
                )
            ),
        )
        object.__setattr__(
            self,
            "observed_at",
            _vm_error(
                lambda: require_datetime(
                    self.observed_at,
                    "transition_evidence.observed_at",
                )
            ),
        )
        object.__setattr__(
            self,
            "vm_id",
            _vm_error(
                lambda: require_uuid(
                    self.vm_id,
                    "transition_evidence.vm_id",
                )
            ),
        )
        _vm_error(
            lambda: require_int(
                self.generation,
                "transition_evidence.generation",
                minimum=0,
                maximum=2**63 - 1,
            )
        )
        object.__setattr__(
            self,
            "boot_id",
            _optional_uuid(
                self.boot_id,
                "transition_evidence.boot_id",
            ),
        )
        _vm_error(
            lambda: require_str(
                self.intent_digest,
                "transition_evidence.intent_digest",
                min_length=64,
                max_length=64,
                pattern=_SHA256_PATTERN,
            )
        )
        if not isinstance(self.facts, Mapping) or not self.facts:
            raise VMContractError(
                "transition_evidence.facts must be a non-empty object"
            )
        if len(self.facts) > 32:
            raise VMContractError(
                "transition_evidence.facts must contain at most 32 entries"
            )
        normalized: dict[str, str] = {}
        for key, value in self.facts.items():
            valid_key = _vm_error(
                lambda key=key: require_str(
                    key,
                    "transition_evidence fact key",
                    min_length=1,
                    max_length=64,
                    pattern=_MACHINE_TOKEN_PATTERN,
                )
            )
            assert isinstance(valid_key, str)
            valid_value = _validate_evidence_fact(valid_key, value)
            normalized[valid_key] = valid_value
        object.__setattr__(
            self,
            "facts",
            MappingProxyType(dict(sorted(normalized.items()))),
        )
        if self.observation is not None:
            if not isinstance(self.observation, RuntimeObservation):
                raise VMContractError(
                    "transition_evidence.observation must be a "
                    "RuntimeObservation or null"
                )
            if self.observation.observed_at != self.observed_at:
                raise VMContractError(
                    "transition evidence and bound observation timestamps "
                    "must match"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": str(self.evidence_id),
            "observed_at": encode_datetime(
                self.observed_at,
                "transition_evidence.observed_at",
            ),
            "facts": dict(self.facts),
            "vm_id": str(self.vm_id),
            "generation": self.generation,
            "boot_id": (
                str(self.boot_id) if self.boot_id is not None else None
            ),
            "intent_digest": self.intent_digest,
            "observation": (
                self.observation.to_dict()
                if self.observation is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "TransitionEvidence":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {
                    "evidence_id",
                    "observed_at",
                    "facts",
                    "vm_id",
                    "generation",
                    "boot_id",
                    "intent_digest",
                    "observation",
                },
                label="transition_evidence",
            )
        )
        assert isinstance(table, dict)
        facts = table["facts"]
        if not isinstance(facts, dict):
            raise VMContractError(
                "transition_evidence.facts must be an object"
            )
        return cls(
            evidence_id=_vm_error(
                lambda: require_uuid(
                    table["evidence_id"],
                    "transition_evidence.evidence_id",
                )
            ),
            observed_at=_vm_error(
                lambda: require_datetime(
                    table["observed_at"],
                    "transition_evidence.observed_at",
                )
            ),
            facts=facts,
            vm_id=_vm_error(
                lambda: require_uuid(
                    table["vm_id"],
                    "transition_evidence.vm_id",
                )
            ),
            generation=_vm_error(
                lambda: require_int(
                    table["generation"],
                    "transition_evidence.generation",
                    minimum=0,
                    maximum=2**63 - 1,
                )
            ),
            boot_id=_optional_uuid(
                table["boot_id"],
                "transition_evidence.boot_id",
            ),
            intent_digest=_vm_error(
                lambda: require_str(
                    table["intent_digest"],
                    "transition_evidence.intent_digest",
                    min_length=64,
                    max_length=64,
                    pattern=_SHA256_PATTERN,
                )
            ),
            observation=(
                RuntimeObservation.from_dict(table["observation"])
                if table["observation"] is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One immutable lifecycle transition and its observed evidence."""

    previous: VMState
    target: VMState
    evidence: TransitionEvidence
    cause: TransitionCause | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.previous, VMState) or not isinstance(
            self.target, VMState
        ):
            raise VMContractError(
                "transition previous and target must be VMState values"
            )
        if self.target not in ALLOWED_TRANSITIONS[self.previous]:
            raise VMTransitionError(
                f"illegal VM transition record: "
                f"{self.previous.value} -> {self.target.value}"
            )
        if not isinstance(self.evidence, TransitionEvidence):
            raise VMContractError(
                "transition evidence must be TransitionEvidence"
            )
        required = TRANSITION_REQUIREMENTS[(self.previous, self.target)]
        actual = frozenset(self.evidence.facts)
        missing = required - actual
        if missing:
            raise VMTransitionError(
                f"{self.previous.value} -> {self.target.value} "
                f"transition lacks evidence: "
                f"{', '.join(sorted(missing))}"
            )
        unexpected = actual - required
        if unexpected:
            raise VMTransitionError(
                f"{self.previous.value} -> {self.target.value} "
                f"transition contains inapplicable evidence: "
                f"{', '.join(sorted(unexpected))}"
            )
        if (
            self.evidence.facts["intent_digest"]
            != self.evidence.intent_digest
        ):
            raise VMTransitionError(
                "transition fact intent_digest does not match its evidence "
                "envelope"
            )
        if (
            "qmp_uuid" in required
            and self.evidence.facts["qmp_uuid"]
            != str(self.evidence.vm_id)
        ):
            raise VMTransitionError(
                "transition QMP UUID does not match its evidence VM identity"
            )
        if "boot_id" in required:
            if (
                self.evidence.boot_id is None
                or self.evidence.facts["boot_id"]
                != str(self.evidence.boot_id)
            ):
                raise VMTransitionError(
                    "transition boot_id fact does not match its evidence "
                    "envelope"
                )
        observation_required = "observation_id" in required
        observation = self.evidence.observation
        if observation_required:
            if observation is None:
                raise VMTransitionError(
                    f"{self.previous.value} -> {self.target.value} requires "
                    "a bound RuntimeObservation"
                )
            if self.evidence.facts["observation_id"] != str(
                observation.observation_id
            ):
                raise VMTransitionError(
                    "transition observation_id must match the bound observation"
                )
        elif observation is not None:
            raise VMTransitionError(
                "transition cannot attach an inapplicable RuntimeObservation"
            )
        if observation is not None:
            if (
                observation.vm_id != self.evidence.vm_id
                or observation.generation != self.evidence.generation
                or observation.boot_id != self.evidence.boot_id
                or observation.intent_digest != self.evidence.intent_digest
            ):
                raise VMTransitionError(
                    "transition observation identity does not match its "
                    "evidence envelope"
                )
            authoritative_targets = {
                VMState.QMP_RUNNING,
                VMState.GUEST_READY,
                VMState.QUIESCED,
                VMState.STOPPED,
            }
            if self.target in authoritative_targets:
                if (
                    observation.source is not ObservationSource.DAEMON
                    or observation.state is not self.target
                ):
                    raise VMTransitionError(
                        f"{self.target.value} promotion requires a matching "
                        "DAEMON aggregate observation"
                    )
            elif self.target is VMState.RECONCILING:
                if (
                    observation.source is not ObservationSource.DAEMON
                    or observation.state is self.previous
                ):
                    raise VMTransitionError(
                        "reconciliation requires a DAEMON aggregate "
                        "observation that conflicts with the recorded state"
                    )
            if "qmp_status" in required:
                if observation.qmp_status != self.evidence.facts["qmp_status"]:
                    raise VMTransitionError(
                        "QMP status fact does not match the bound observation"
                    )
                if str(observation.qmp_uuid) != self.evidence.facts["qmp_uuid"]:
                    raise VMTransitionError(
                        "QMP UUID fact does not match the bound observation"
                    )
                if (
                    f"sha256:{observation.qmp_greeting_sha256}"
                    != self.evidence.facts["qmp_greeting"]
                ):
                    raise VMTransitionError(
                        "QMP greeting digest does not match the bound observation"
                    )
            if "agent_ready" in required and observation.guest_ready is not True:
                raise VMTransitionError(
                    "guest readiness facts require a ready bound observation"
                )
            if (
                "guest_quiesced" in required
                and observation.guest_quiesced is not True
            ):
                raise VMTransitionError(
                    "guest quiesce fact requires a quiesced bound observation"
                )
            if (
                "process_absent" in required
                and observation.process_absent is not True
            ):
                raise VMTransitionError(
                    "process absence fact requires a stopped bound observation"
                )
            if (
                "qmp_unreachable" in required
                and observation.qmp_unreachable is not True
            ):
                raise VMTransitionError(
                    "QMP-unreachable fact requires a stopped bound observation"
                )
        if self.target is VMState.ERROR:
            if self.cause is None:
                raise VMTransitionError("ERROR transition requires a cause")
            if self.evidence.facts["error_code"] != self.cause.code:
                raise VMTransitionError(
                    "ERROR evidence code must match the causal record"
                )
            if self.evidence.facts["cause_event_id"] != str(
                self.cause.event_id
            ):
                raise VMTransitionError(
                    "ERROR evidence event must match the causal record"
                )
            if self.cause.occurred_at > self.evidence.observed_at:
                raise VMTransitionError(
                    "ERROR cause cannot occur after its transition evidence"
                )
        elif self.cause is not None:
            raise VMTransitionError(
                "non-ERROR transition cannot attach an error cause"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "previous": self.previous.value,
            "target": self.target.value,
            "evidence": self.evidence.to_dict(),
            "cause": self.cause.to_dict() if self.cause is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "TransitionRecord":
        table = _vm_error(
            lambda: strict_object(
                payload,
                {"previous", "target", "evidence", "cause"},
                label="transition",
            )
        )
        assert isinstance(table, dict)
        try:
            previous = VMState(table["previous"])
            target = VMState(table["target"])
        except (TypeError, ValueError) as exc:
            raise VMContractError(
                "transition previous/target state is unsupported"
            ) from exc
        return cls(
            previous=previous,
            target=target,
            evidence=TransitionEvidence.from_dict(table["evidence"]),
            cause=(
                TransitionCause.from_dict(table["cause"])
                if table["cause"] is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class VMRecord:
    """Versioned, secret-free lifecycle truth for one AIPC generation."""

    definition: VMDefinition
    ports: VMPorts
    vm_id: UUID = field(default_factory=uuid4)
    generation: int = 0
    boot_id: UUID | None = None
    state: VMState = VMState.DECLARED
    process: ProcessIdentity | None = None
    qmp_socket: str = ""
    pid_file: str = ""
    log_path: str = ""
    intent_digest: str = ""
    migration_source: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_error: TransitionCause | None = None
    transitions: tuple[TransitionRecord, ...] = ()
    migration_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.definition, VMDefinition):
            raise VMContractError(
                "record.definition must be a VMDefinition"
            )
        if not isinstance(self.ports, VMPorts):
            raise VMContractError("record.ports must be VMPorts")
        object.__setattr__(
            self,
            "vm_id",
            _vm_error(lambda: require_uuid(self.vm_id, "record.vm_id")),
        )
        object.__setattr__(
            self,
            "generation",
            _vm_error(
                lambda: require_int(
                    self.generation,
                    "record.generation",
                    minimum=0,
                    maximum=2**63 - 1,
                )
            ),
        )
        object.__setattr__(
            self,
            "boot_id",
            _optional_uuid(self.boot_id, "record.boot_id"),
        )
        if not isinstance(self.state, VMState):
            raise VMContractError("record.state must be a VMState")
        if self.process is not None and not isinstance(
            self.process, ProcessIdentity
        ):
            raise VMContractError(
                "record.process must be a ProcessIdentity or null"
            )
        object.__setattr__(
            self,
            "qmp_socket",
            _validated_absolute_path(
                self.qmp_socket,
                "record.qmp_socket",
                allow_empty=True,
                unix_socket=True,
            ),
        )
        for attribute in ("pid_file", "log_path"):
            object.__setattr__(
                self,
                attribute,
                _validated_absolute_path(
                    getattr(self, attribute),
                    f"record.{attribute}",
                    allow_empty=True,
                ),
            )
        object.__setattr__(
            self,
            "created_at",
            _vm_error(
                lambda: require_datetime(
                    self.created_at,
                    "record.created_at",
                )
            ),
        )
        object.__setattr__(
            self,
            "updated_at",
            _vm_error(
                lambda: require_datetime(
                    self.updated_at,
                    "record.updated_at",
                )
            ),
        )
        if self.updated_at < self.created_at:
            raise VMContractError(
                "record.updated_at cannot precede created_at"
            )
        if not isinstance(self.migration_notes, tuple):
            raise VMContractError(
                "record.migration_notes must be a tuple"
            )
        normalized_notes: list[str] = []
        for index, note in enumerate(self.migration_notes):
            normalized_notes.append(
                _vm_error(
                    lambda note=note, index=index: require_str(
                        note,
                        f"record.migration_notes[{index}]",
                        min_length=1,
                        max_length=128,
                        pattern=_MACHINE_TOKEN_PATTERN,
                    )
                )
            )
        if len(normalized_notes) > 16:
            raise VMContractError(
                "record.migration_notes must contain at most 16 entries"
            )
        if len(normalized_notes) != len(set(normalized_notes)):
            raise VMContractError(
                "record.migration_notes cannot contain duplicates"
            )
        unknown_notes = set(normalized_notes) - _ALLOWED_MIGRATION_NOTES
        if unknown_notes:
            raise VMContractError(
                "record contains unsupported migration notes: "
                f"{', '.join(sorted(unknown_notes))}"
            )
        object.__setattr__(self, "migration_notes", tuple(normalized_notes))
        if self.migration_source is not None:
            source = _vm_error(
                lambda: require_str(
                    self.migration_source,
                    "record.migration_source",
                    min_length=9,
                    max_length=9,
                    pattern=r"legacy_v0",
                )
            )
            object.__setattr__(self, "migration_source", source)
        if self.migration_source is None and self.migration_notes:
            raise VMContractError(
                "native record cannot carry legacy migration notes"
            )
        if (
            self.migration_source == "legacy_v0"
            and self.migration_notes != (_LEGACY_SECRET_NOTE,)
        ):
            raise VMContractError(
                "legacy_v0 record requires its exact security migration note"
            )
        expected_intent_digest = _intent_digest(
            vm_id=self.vm_id,
            generation=self.generation,
            definition=self.definition,
            ports=self.ports,
            qmp_socket=self.qmp_socket,
            pid_file=self.pid_file,
            log_path=self.log_path,
            created_at=self.created_at,
            migration_source=self.migration_source,
            migration_notes=self.migration_notes,
        )
        if not self.intent_digest:
            if self.transitions:
                raise VMContractError(
                    "historyful record cannot reconstruct a missing "
                    "intent_digest"
                )
            if self.migration_source is not None:
                raise VMContractError(
                    "migration provenance requires a pre-bound migrated "
                    "intent_digest"
                )
            object.__setattr__(self, "intent_digest", expected_intent_digest)
        else:
            _vm_error(
                lambda: require_str(
                    self.intent_digest,
                    "record.intent_digest",
                    min_length=64,
                    max_length=64,
                    pattern=_SHA256_PATTERN,
                )
            )
            if self.intent_digest != expected_intent_digest:
                raise VMContractError(
                    "record intent does not match its bound declaration digest"
                )
        if self.last_error is not None and not isinstance(
            self.last_error, TransitionCause
        ):
            raise VMContractError(
                "record.last_error must be a TransitionCause or null"
            )
        if self.state is VMState.ERROR and self.last_error is None:
            raise VMContractError("ERROR record requires a causal error record")
        if not isinstance(self.transitions, tuple) or any(
            not isinstance(item, TransitionRecord)
            for item in self.transitions
        ):
            raise VMContractError(
                "record.transitions must be a tuple of TransitionRecord"
            )
        if len(self.transitions) > 10_000:
            raise VMContractError(
                "record.transitions exceeds the bounded history limit"
            )
        if self.transitions:
            previous_time = self.created_at
            for transition in self.transitions:
                if transition.evidence.observed_at < previous_time:
                    raise VMContractError(
                        "record transition evidence is not chronological"
                    )
                previous_time = transition.evidence.observed_at
        for index in range(1, len(self.transitions)):
            if (
                self.transitions[index - 1].target
                is not self.transitions[index].previous
            ):
                raise VMContractError(
                    "record transition history is not contiguous"
                )
        if self.transitions and self.transitions[-1].target is not self.state:
            raise VMContractError(
                "record state does not match its last transition"
            )
        if self.transitions and self.updated_at != (
            self.transitions[-1].evidence.observed_at
        ):
            raise VMContractError(
                "record.updated_at must equal its last transition observation"
            )
        if not self.transitions:
            actual_notes = set(self.migration_notes)
            if (
                self.state is not VMState.DECLARED
                or actual_notes not in (set(), {_LEGACY_SECRET_NOTE})
            ):
                raise VMContractError(
                    "only DECLARED records may exist without transition history"
                )
            if self.last_error is not None:
                raise VMContractError(
                    "historyless DECLARED record cannot contain last_error"
                )
        else:
            origin = self.transitions[0].previous
            if (
                origin is not VMState.DECLARED
                or set(self.migration_notes)
                not in (set(), {_LEGACY_SECRET_NOTE})
            ):
                raise VMContractError(
                    "record transition history must originate from DECLARED"
                )

            latest_error: TransitionCause | None = None
            active_boot: UUID | None = None
            record_boot: UUID | None = None
            seen_boot_ids: set[UUID] = set()
            active_process: ProcessIdentity | None = None
            seen_process_cores: set[tuple[int, int, str, str]] = set()
            evidence_ids: set[UUID] = set()
            observation_ids: set[UUID] = set()
            cause_ids: set[UUID] = set()
            for transition in self.transitions:
                evidence = transition.evidence
                if evidence.evidence_id in evidence_ids:
                    raise VMContractError(
                        "record transition history reuses an evidence_id"
                    )
                evidence_ids.add(evidence.evidence_id)
                if (
                    evidence.vm_id != self.vm_id
                    or evidence.generation != self.generation
                    or evidence.intent_digest != self.intent_digest
                ):
                    raise VMContractError(
                        "record transition evidence references a different "
                        "VM identity, generation, or declaration intent"
                    )
                facts = evidence.facts
                if "qmp_uuid" in facts and facts["qmp_uuid"] != str(self.vm_id):
                    raise VMContractError(
                        "record QMP evidence references a different VM identity"
                    )
                observation = evidence.observation
                if observation is not None:
                    if observation.observation_id in observation_ids:
                        raise VMContractError(
                            "record transition history reuses an observation_id"
                        )
                    observation_ids.add(observation.observation_id)
                    if (
                        observation.vm_id != self.vm_id
                        or observation.generation != self.generation
                        or observation.intent_digest != self.intent_digest
                    ):
                        raise VMContractError(
                            "record observation references a different VM "
                            "identity, generation, or declaration intent"
                        )
                if transition.target is VMState.BOOTING:
                    next_boot = UUID(facts["boot_id"])
                    if next_boot in seen_boot_ids:
                        raise VMContractError(
                            "record transition history reuses a boot_id"
                        )
                    seen_boot_ids.add(next_boot)
                    active_boot = next_boot
                    record_boot = next_boot
                    active_process = None
                elif "boot_id" in facts:
                    observed_boot = UUID(facts["boot_id"])
                    if (
                        active_boot is None
                        or observed_boot != active_boot
                    ):
                        raise VMContractError(
                            "record transition evidence crosses boot identities"
                        )
                if observation is not None and observation.boot_id is not None:
                    observed_boot = observation.boot_id
                    if observation.state in {
                        VMState.QMP_RUNNING,
                        VMState.GUEST_READY,
                        VMState.QUIESCED,
                    }:
                        if active_boot is None:
                            if observed_boot in seen_boot_ids:
                                raise VMContractError(
                                    "record observation reuses a prior boot_id"
                                )
                            seen_boot_ids.add(observed_boot)
                            active_boot = observed_boot
                            record_boot = observed_boot
                        elif observed_boot != active_boot:
                            raise VMContractError(
                                "record observation crosses boot identities"
                            )
                    elif record_boot is None:
                        if observed_boot in seen_boot_ids:
                            raise VMContractError(
                                "record observation reuses a prior boot_id"
                            )
                        seen_boot_ids.add(observed_boot)
                        record_boot = observed_boot
                    elif observed_boot != record_boot:
                        raise VMContractError(
                            "record stopped observation crosses boot identities"
                        )
                if evidence.boot_id != record_boot:
                    raise VMContractError(
                        "record transition evidence boot identity does not "
                        "match its history"
                    )
                if observation is not None and observation.process is not None:
                    observed_process = observation.process
                    observed_core = _process_core_identity(observed_process)
                    if active_process is None:
                        if observed_core in seen_process_cores:
                            raise VMContractError(
                                "record observation replays a prior process "
                                "identity after absence or a new boot"
                            )
                        seen_process_cores.add(observed_core)
                    else:
                        if (
                            observed_core
                            != _process_core_identity(active_process)
                        ):
                            raise VMContractError(
                                "record observation changes process identity "
                                "inside one boot"
                            )
                        if (
                            observed_process.observed_at
                            <= active_process.observed_at
                        ):
                            raise VMContractError(
                                "record observation must provide a strictly "
                                "newer active process identity observation"
                            )
                    active_process = observed_process
                elif (
                    observation is not None
                    and observation.process_absent is True
                ):
                    active_process = None
                    active_boot = None
                if transition.target in {VMState.STOPPED, VMState.DESTROYED}:
                    active_process = None
                    active_boot = None
                if (
                    transition.previous is VMState.ERROR
                    and "cause_event_id" in facts
                ):
                    if (
                        latest_error is None
                        or facts["cause_event_id"] != str(latest_error.event_id)
                    ):
                        raise VMContractError(
                            "record recovery evidence references the wrong "
                            "or missing error cause"
                        )
                if transition.target is VMState.ERROR:
                    assert transition.cause is not None
                    if transition.cause.event_id in cause_ids:
                        raise VMContractError(
                            "distinct ERROR transitions cannot reuse a "
                            "TransitionCause event_id"
                        )
                    cause_ids.add(transition.cause.event_id)
                    latest_error = transition.cause

            if self.boot_id != record_boot:
                raise VMContractError(
                    "record.boot_id does not match transition history"
                )
            if latest_error is not None:
                if self.last_error != latest_error:
                    raise VMContractError(
                        "record.last_error does not match its latest ERROR cause"
                    )
            elif self.last_error is not None:
                raise VMContractError(
                    "record.last_error has no ERROR transition provenance"
                )
        if self.state in {
            VMState.DECLARED,
            VMState.PROVISIONING,
            VMState.PROVISIONED,
        } and self.boot_id is not None:
            raise VMContractError(
                f"{self.state.value} record cannot carry a boot_id"
            )
        requires_boot = self.state in {
            VMState.BOOTING,
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            VMState.QUIESCING,
            VMState.QUIESCED,
            VMState.SNAPSHOTTING,
        }
        if requires_boot and self.boot_id is None:
            raise VMContractError(
                f"{self.state.value} record requires a boot_id"
            )
        requires_process = self.state in {
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            VMState.QUIESCING,
            VMState.QUIESCED,
            VMState.SNAPSHOTTING,
        }
        if requires_process and self.process is None:
            raise VMContractError(
                f"{self.state.value} record requires an observed ProcessIdentity"
            )
        if self.transitions and active_process != self.process:
            raise VMContractError(
                "record process does not match its observation history"
            )
        no_process_states = {
            VMState.DECLARED,
            VMState.PROVISIONING,
            VMState.PROVISIONED,
            VMState.ROLLING_BACK,
            VMState.STOPPED,
            VMState.DESTROYED,
        }
        if self.state in no_process_states and self.process is not None:
            raise VMContractError(
                f"{self.state.value} record cannot retain a process identity"
            )
        if requires_process and not self.qmp_socket:
            raise VMContractError(
                f"{self.state.value} record requires a QMP socket reference"
            )

    @property
    def pid(self) -> int | None:
        """Compatibility projection; process identity remains the authority."""

        return self.process.pid if self.process is not None else None

    def with_planned_runtime(
        self,
        *,
        qmp_socket: str,
        pid_file: str,
        log_path: str,
    ) -> "VMRecord":
        """Seal lexical runtime references before lifecycle history begins."""

        if self.state is not VMState.DECLARED or self.transitions:
            raise VMTransitionError(
                "planned runtime references can change only on a historyless "
                "DECLARED record"
            )
        selected_qmp_socket = _validated_absolute_path(
            qmp_socket,
            "record.qmp_socket",
            allow_empty=False,
            unix_socket=True,
        )
        selected_pid_file = _validated_absolute_path(
            pid_file,
            "record.pid_file",
            allow_empty=False,
        )
        selected_log_path = _validated_absolute_path(
            log_path,
            "record.log_path",
            allow_empty=False,
        )
        selected_digest = _intent_digest(
            vm_id=self.vm_id,
            generation=self.generation,
            definition=self.definition,
            ports=self.ports,
            qmp_socket=selected_qmp_socket,
            pid_file=selected_pid_file,
            log_path=selected_log_path,
            created_at=self.created_at,
            migration_source=self.migration_source,
            migration_notes=self.migration_notes,
        )
        return replace(
            self,
            qmp_socket=selected_qmp_socket,
            pid_file=selected_pid_file,
            log_path=selected_log_path,
            intent_digest=selected_digest,
        )

    def transition(
        self,
        target: VMState,
        evidence: TransitionEvidence | None = None,
        *,
        cause: TransitionCause | None = None,
        boot_id: UUID | None = None,
    ) -> "VMRecord":
        """Return a new record after one evidenced transition.

        Protocol records are immutable values.  Defined idempotent repeats
        return ``self`` without appending a fabricated transition.
        """

        if not isinstance(target, VMState):
            raise VMTransitionError("target must be a VMState")
        if target is self.state:
            if target in IDEMPOTENT_STATES:
                if evidence is not None or cause is not None or boot_id is not None:
                    raise VMTransitionError(
                        f"idempotent {target.value} repeat cannot attach "
                        "ignored transition data"
                    )
                return self
            raise VMTransitionError(
                f"repeated {target.value} is not an idempotent operation"
            )
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise VMTransitionError(
                f"illegal VM transition: {self.state.value} -> {target.value}"
            )
        if evidence is None:
            raise VMTransitionError(
                f"{self.state.value} -> {target.value} requires evidence"
            )
        if evidence.observed_at < self.updated_at:
            raise VMTransitionError(
                "transition evidence predates the record's latest observation"
            )
        if (
            evidence.vm_id != self.vm_id
            or evidence.generation != self.generation
            or evidence.intent_digest != self.intent_digest
            or evidence.facts.get("intent_digest") != self.intent_digest
        ):
            raise VMTransitionError(
                "transition evidence is not bound to this VM identity, "
                "generation, and declaration intent"
            )
        if (
            self.state is VMState.DECLARED
            and target is VMState.PROVISIONING
            and not (self.qmp_socket and self.pid_file and self.log_path)
        ):
            raise VMTransitionError(
                "provisioning requires sealed qmp_socket, pid_file, and "
                "log_path references"
            )
        transition = TransitionRecord(
            previous=self.state,
            target=target,
            evidence=evidence,
            cause=cause,
        )
        observation = evidence.observation
        selected_boot = self.boot_id
        if target is VMState.BOOTING:
            selected_boot = _optional_uuid(boot_id, "transition.boot_id")
            if selected_boot is None:
                raise VMTransitionError(
                    "BOOTING transition requires a boot_id argument"
                )
            if evidence.facts["boot_id"] != str(selected_boot):
                raise VMTransitionError(
                    "BOOTING evidence boot_id must match the record boot_id"
                )
            if self.boot_id is not None and selected_boot == self.boot_id:
                raise VMTransitionError(
                    "a new boot must use a new boot_id"
                )
        else:
            supplied_boot = _optional_uuid(boot_id, "transition.boot_id")
            observed_active_boot = (
                observation.boot_id
                if observation is not None
                and observation.process is not None
                else None
            )
            if observed_active_boot is not None:
                if self.process is None:
                    selected_boot = observed_active_boot
                elif observed_active_boot != selected_boot:
                    raise VMTransitionError(
                        "active observation crosses the recorded boot identity"
                    )
                if (
                    supplied_boot is not None
                    and supplied_boot != observed_active_boot
                ):
                    raise VMTransitionError(
                        "boot_id argument does not match the active observation"
                    )
            elif supplied_boot is not None:
                if (
                    target not in {VMState.QMP_RUNNING, VMState.GUEST_READY}
                    or self.process is not None
                ):
                    raise VMTransitionError(
                        "boot_id argument is accepted only for BOOTING or "
                        "active reconciliation without an observed process"
                    )
                selected_boot = supplied_boot
            if (
                target in {VMState.QMP_RUNNING, VMState.GUEST_READY}
                and selected_boot is None
            ):
                raise VMTransitionError(
                    f"{target.value} reconciliation requires a boot identity"
                )
        if evidence.boot_id != selected_boot:
            raise VMTransitionError(
                "transition evidence boot identity does not match the record"
            )
        if "boot_id" in evidence.facts:
            if selected_boot is None or evidence.facts["boot_id"] != str(
                selected_boot
            ):
                raise VMTransitionError(
                    "transition evidence boot_id must match the record boot_id"
                )
        if "qmp_uuid" in evidence.facts and evidence.facts["qmp_uuid"] != str(
            self.vm_id
        ):
            raise VMTransitionError(
                "QMP UUID evidence must match the exact VM identity"
            )
        if self.state is VMState.ERROR and "cause_event_id" in evidence.facts:
            if (
                self.last_error is None
                or evidence.facts["cause_event_id"]
                != str(self.last_error.event_id)
            ):
                raise VMTransitionError(
                    "recovery evidence must reference the active error cause"
                )
        if evidence.observed_at < self.created_at:
            raise VMTransitionError(
                "transition evidence predates record creation"
            )
        if observation is not None and (
            observation.vm_id != self.vm_id
            or observation.generation != self.generation
            or observation.intent_digest != self.intent_digest
        ):
            raise VMTransitionError(
                "transition observation references a different VM identity "
                "generation, or declaration intent"
            )
        selected_process = self.process
        if target is VMState.BOOTING:
            selected_process = None
        if observation is not None and observation.process is not None:
            observed_process = observation.process
            if selected_process is not None:
                if (
                    _process_core_identity(observed_process)
                    != _process_core_identity(selected_process)
                ):
                    raise VMTransitionError(
                        "transition observation changes the active process "
                        "identity"
                    )
                if observed_process.observed_at <= selected_process.observed_at:
                    raise VMTransitionError(
                        "transition observation must provide a strictly newer "
                        "active process identity observation"
                    )
            selected_process = observed_process
        elif observation is not None and observation.process_absent is True:
            selected_process = None
        if target in {VMState.STOPPED, VMState.DESTROYED}:
            selected_process = None
        return replace(
            self,
            boot_id=selected_boot,
            state=target,
            process=selected_process,
            updated_at=evidence.observed_at,
            last_error=cause if target is VMState.ERROR else self.last_error,
            transitions=(*self.transitions, transition),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the strict current schema without any credential value."""

        return {
            "schema_version": SCHEMA_VERSION,
            "vm_id": str(self.vm_id),
            "generation": self.generation,
            "boot_id": str(self.boot_id) if self.boot_id is not None else None,
            "definition": self.definition.to_dict(),
            "ports": self.ports.to_dict(),
            "state": self.state.value,
            "process": self.process.to_dict() if self.process is not None else None,
            "qmp_socket": self.qmp_socket,
            "pid_file": self.pid_file,
            "log_path": self.log_path,
            "intent_digest": self.intent_digest,
            "migration_source": self.migration_source,
            "created_at": encode_datetime(
                self.created_at,
                "record.created_at",
            ),
            "updated_at": encode_datetime(
                self.updated_at,
                "record.updated_at",
            ),
            "last_error": (
                self.last_error.to_dict()
                if self.last_error is not None
                else None
            ),
            "transitions": [item.to_dict() for item in self.transitions],
            "migration_notes": list(self.migration_notes),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "VMRecord":
        """Decode current schema or migrate the exact promoted v0 mapping."""

        return _decode_vm_record(migrate_vm_record(payload))

    def new_generation(
        self,
        *,
        definition: VMDefinition | None = None,
        ports: VMPorts | None = None,
        new_vm_id: UUID | None = None,
    ) -> "VMRecord":
        """Create declared replacement identity after terminal destruction."""

        if self.state is not VMState.DESTROYED:
            raise VMTransitionError(
                "only a DESTROYED record can create a replacement generation"
            )
        if self.generation == 2**63 - 1:
            raise VMTransitionError("VM generation counter is exhausted")
        return VMRecord(
            definition=definition or self.definition,
            ports=ports or self.ports,
            vm_id=new_vm_id or self.vm_id,
            generation=self.generation + 1,
        )


_V1_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "vm_id",
        "generation",
        "boot_id",
        "definition",
        "ports",
        "state",
        "process",
        "qmp_socket",
        "pid_file",
        "log_path",
        "intent_digest",
        "migration_source",
        "created_at",
        "updated_at",
        "last_error",
        "transitions",
        "migration_notes",
    }
)

_LEGACY_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "vm_id",
        "definition",
        "ports",
        "agent_token",
        "state",
        "pid",
        "qmp_socket",
        "pid_file",
        "log_path",
        "created_at",
        "updated_at",
        "last_error",
    }
)


def _decode_vm_record(payload: object) -> VMRecord:
    table = _vm_error(
        lambda: strict_object(
            payload,
            _V1_RECORD_KEYS,
            label="record",
        )
    )
    assert isinstance(table, dict)
    try:
        require_schema_version(table["schema_version"])
    except SchemaVersionError as exc:
        raise VMMigrationError(str(exc)) from exc
    try:
        state = VMState(table["state"])
    except (TypeError, ValueError) as exc:
        raise VMContractError("record.state is unsupported") from exc
    transitions_payload = table["transitions"]
    notes_payload = table["migration_notes"]
    if not isinstance(transitions_payload, list):
        raise VMContractError("record.transitions must be an array")
    if len(transitions_payload) > 10_000:
        raise VMContractError(
            "record.transitions exceeds the bounded history limit"
        )
    if not isinstance(notes_payload, list):
        raise VMContractError("record.migration_notes must be an array")
    if len(notes_payload) > 16:
        raise VMContractError(
            "record.migration_notes must contain at most 16 entries"
        )
    return VMRecord(
        definition=VMDefinition.from_dict(table["definition"]),
        ports=VMPorts.from_dict(table["ports"]),
        vm_id=_vm_error(
            lambda: require_uuid(table["vm_id"], "record.vm_id")
        ),
        generation=_vm_error(
            lambda: require_int(
                table["generation"],
                "record.generation",
                minimum=0,
                maximum=2**63 - 1,
            )
        ),
        boot_id=_optional_uuid(table["boot_id"], "record.boot_id"),
        state=state,
        process=(
            ProcessIdentity.from_dict(table["process"])
            if table["process"] is not None
            else None
        ),
        qmp_socket=_vm_error(
            lambda: require_str(
                table["qmp_socket"],
                "record.qmp_socket",
                min_length=0,
                max_length=4096,
            )
        ),
        pid_file=_vm_error(
            lambda: require_str(
                table["pid_file"],
                "record.pid_file",
                min_length=0,
                max_length=4096,
            )
        ),
        log_path=_vm_error(
            lambda: require_str(
                table["log_path"],
                "record.log_path",
                min_length=0,
                max_length=4096,
            )
        ),
        intent_digest=_vm_error(
            lambda: require_str(
                table["intent_digest"],
                "record.intent_digest",
                min_length=64,
                max_length=64,
                pattern=_SHA256_PATTERN,
            )
        ),
        migration_source=(
            _vm_error(
                lambda: require_str(
                    table["migration_source"],
                    "record.migration_source",
                    min_length=9,
                    max_length=9,
                    pattern=r"legacy_v0",
                )
            )
            if table["migration_source"] is not None
            else None
        ),
        created_at=_vm_error(
            lambda: require_datetime(
                table["created_at"],
                "record.created_at",
            )
        ),
        updated_at=_vm_error(
            lambda: require_datetime(
                table["updated_at"],
                "record.updated_at",
            )
        ),
        last_error=(
            TransitionCause.from_dict(table["last_error"])
            if table["last_error"] is not None
            else None
        ),
        transitions=tuple(
            TransitionRecord.from_dict(item)
            for item in transitions_payload
        ),
        migration_notes=tuple(
            _vm_error(
                lambda item=item, index=index: require_str(
                    item,
                    f"record.migration_notes[{index}]",
                    min_length=1,
                    max_length=128,
                    pattern=_MACHINE_TOKEN_PATTERN,
                )
            )
            for index, item in enumerate(notes_payload)
        ),
    )


def _migrate_legacy_vm_record(table: dict[str, object]) -> dict[str, object]:
    definition = VMDefinition.from_dict(table["definition"])
    ports = VMPorts.from_dict(table["ports"])
    vm_id = _vm_error(lambda: require_uuid(table["vm_id"], "record.vm_id"))
    token = _vm_error(
        lambda: require_str(
            table["agent_token"],
            "record.agent_token",
            min_length=1,
            max_length=4096,
            allow_control_characters=True,
        )
    )
    assert token  # validated, intentionally not copied into canonical output
    state_value = _vm_error(
        lambda: require_str(
            table["state"],
            "record.state",
            min_length=1,
            max_length=32,
        )
    )
    legacy_states = {
        "declared",
        "starting",
        "running",
        "ready",
        "stopping",
        "stopped",
        "error",
    }
    if state_value in legacy_states and state_value != "declared":
        raise VMMigrationError(
            f"legacy state {state_value!r} is unverified; P2 "
            "reconciliation/import is required"
        )
    if state_value != "declared":
        raise VMMigrationError(
            f"legacy state {state_value!r} is corrupt or unsupported"
        )
    pid = table["pid"]
    if pid is not None:
        raise VMMigrationError(
            "legacy DECLARED record with process state is unverified; P2 "
            "reconciliation/import is required"
        )
    qmp_socket = _vm_error(
        lambda: require_str(
            table["qmp_socket"],
            "record.qmp_socket",
            min_length=0,
            max_length=4096,
        )
    )
    pid_file = _vm_error(
        lambda: require_str(
            table["pid_file"],
            "record.pid_file",
            min_length=0,
            max_length=4096,
        )
    )
    log_path = _vm_error(
        lambda: require_str(
            table["log_path"],
            "record.log_path",
            min_length=0,
            max_length=4096,
        )
    )
    created_at = _vm_error(
        lambda: require_datetime(table["created_at"], "record.created_at")
    )
    updated_at = _vm_error(
        lambda: require_datetime(table["updated_at"], "record.updated_at")
    )
    if table["last_error"] is not None:
        raise VMMigrationError(
            "legacy DECLARED record with last_error is unverified; P2 "
            "reconciliation/import is required"
        )
    migration_notes = (_LEGACY_SECRET_NOTE,)
    intent_digest = _intent_digest(
        vm_id=vm_id,
        generation=0,
        definition=definition,
        ports=ports,
        qmp_socket=qmp_socket,
        pid_file=pid_file,
        log_path=log_path,
        created_at=created_at,
        migration_source="legacy_v0",
        migration_notes=migration_notes,
    )
    record = VMRecord(
        definition=definition,
        ports=ports,
        vm_id=vm_id,
        state=VMState.DECLARED,
        process=None,
        qmp_socket=qmp_socket,
        pid_file=pid_file,
        log_path=log_path,
        intent_digest=intent_digest,
        migration_source="legacy_v0",
        created_at=created_at,
        updated_at=updated_at,
        last_error=None,
        migration_notes=migration_notes,
    )
    return record.to_dict()


def migrate_vm_record(payload: object) -> dict[str, object]:
    """Return strict current schema or migrate the exact unversioned v0 shape."""

    if not isinstance(payload, dict):
        raise VMMigrationError("record must be an object")
    if "schema_version" in payload:
        try:
            require_schema_version(payload["schema_version"])
        except SchemaVersionError as exc:
            raise VMMigrationError(str(exc)) from exc
        return _decode_vm_record(payload).to_dict()
    try:
        table = strict_object(
            payload,
            _LEGACY_RECORD_KEYS,
            label="legacy record",
        )
    except ProtocolValidationError as exc:
        raise VMMigrationError(str(exc)) from exc
    return _migrate_legacy_vm_record(table)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "IDEMPOTENT_STATES",
    "ImageProvenance",
    "ObservationSource",
    "ProcessIdentity",
    "RuntimeObservation",
    "SnapshotReference",
    "StorageReference",
    "TRANSITION_REQUIREMENTS",
    "TransitionCause",
    "TransitionEvidence",
    "TransitionRecord",
    "VMContractError",
    "VMDefinition",
    "VMMigrationError",
    "VMPorts",
    "VMRecord",
    "VMResourceSpec",
    "VMState",
    "VMTransitionError",
    "migrate_vm_record",
    "utc_now",
]
