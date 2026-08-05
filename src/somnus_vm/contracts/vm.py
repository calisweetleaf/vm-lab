"""Canonical virtual-machine records and lifecycle transitions.

Source: vm_lab.zip schemas and supervisor lineage
Integrated: 2026-08-04
Purpose: Replaces three conflicting VM state and record authorities with one
    serializable contract shared by the host control plane and guest bridge.
IO: Pure validation and JSON-compatible mapping; no filesystem or network IO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4


class VMContractError(ValueError):
    """Raised when a VM definition or persisted record violates the contract."""


class VMTransitionError(RuntimeError):
    """Raised when a lifecycle transition is not legal from the current state."""


class VMState(str, Enum):
    """Host-observable lifecycle states for one persistent VM process."""

    DECLARED = "declared"
    STARTING = "starting"
    RUNNING = "running"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


ALLOWED_TRANSITIONS: Final[dict[VMState, frozenset[VMState]]] = {
    VMState.DECLARED: frozenset({VMState.STARTING, VMState.STOPPED, VMState.ERROR}),
    VMState.STARTING: frozenset({VMState.RUNNING, VMState.ERROR, VMState.STOPPING}),
    VMState.RUNNING: frozenset({VMState.READY, VMState.STOPPING, VMState.ERROR}),
    VMState.READY: frozenset({VMState.RUNNING, VMState.STOPPING, VMState.ERROR}),
    VMState.STOPPING: frozenset({VMState.STOPPED, VMState.ERROR}),
    VMState.STOPPED: frozenset({VMState.STARTING, VMState.ERROR}),
    VMState.ERROR: frozenset({VMState.STARTING, VMState.STOPPING, VMState.STOPPED}),
}

_VM_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class VMPorts:
    """Unique host ports forwarded into one guest VM."""

    ssh: int
    agent: int
    vnc: int

    def __post_init__(self) -> None:
        """Validate port range and per-record uniqueness."""

        values = (self.ssh, self.agent, self.vnc)
        if any(port < 1024 or port > 65535 for port in values):
            raise VMContractError("VM host ports must be between 1024 and 65535")
        if len(set(values)) != len(values):
            raise VMContractError("SSH, agent, and VNC host ports must be unique")

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-compatible port mapping."""

        return {"ssh": self.ssh, "agent": self.agent, "vnc": self.vnc}


@dataclass(frozen=True, slots=True)
class VMDefinition:
    """Immutable operator intent used to construct a QEMU launch plan."""

    name: str
    disk_path: str
    memory_mib: int = 4096
    vcpus: int = 2
    enable_kvm: bool = True

    def __post_init__(self) -> None:
        """Reject names and resource values unsafe for a process boundary."""

        if not _VM_NAME_PATTERN.fullmatch(self.name):
            raise VMContractError(
                "VM name must be 1-63 characters and contain only letters, digits, '.', '_', or '-'"
            )
        disk = Path(self.disk_path).expanduser()
        if not disk.is_absolute():
            raise VMContractError("VM disk_path must be absolute")
        if self.memory_mib < 256 or self.memory_mib > 1_048_576:
            raise VMContractError("memory_mib must be between 256 and 1048576")
        if self.vcpus < 1 or self.vcpus > 256:
            raise VMContractError("vcpus must be between 1 and 256")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible definition mapping."""

        return {
            "name": self.name,
            "disk_path": self.disk_path,
            "memory_mib": self.memory_mib,
            "vcpus": self.vcpus,
            "enable_kvm": self.enable_kvm,
        }


@dataclass(slots=True)
class VMRecord:
    """Persisted process truth for one declared VM."""

    definition: VMDefinition
    ports: VMPorts
    agent_token: str
    vm_id: UUID = field(default_factory=uuid4)
    state: VMState = VMState.DECLARED
    pid: int | None = None
    qmp_socket: str = ""
    pid_file: str = ""
    log_path: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_error: str | None = None

    def __post_init__(self) -> None:
        """Validate security-sensitive persisted process fields."""

        if not self.agent_token:
            raise VMContractError("agent_token cannot be empty")
        if self.pid is not None and self.pid <= 1:
            raise VMContractError("pid must be greater than 1 when present")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise VMContractError("record timestamps must be timezone-aware")

    def transition(self, target: VMState, reason: str | None = None) -> None:
        """Move to a legal target state and update error context.

        Args:
            target: Lifecycle state to enter.
            reason: Error explanation when entering ERROR.

        Raises:
            VMTransitionError: If the current state cannot enter target.
        """

        if target == self.state:
            return
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise VMTransitionError(f"Illegal VM transition: {self.state.value} -> {target.value}")
        if target is VMState.ERROR and not reason:
            raise VMTransitionError("ERROR transitions require a reason")
        self.state = target
        self.last_error = reason if target is VMState.ERROR else None
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, object]:
        """Return the complete registry representation."""

        return {
            "vm_id": str(self.vm_id),
            "definition": self.definition.to_dict(),
            "ports": self.ports.to_dict(),
            "agent_token": self.agent_token,
            "state": self.state.value,
            "pid": self.pid,
            "qmp_socket": self.qmp_socket,
            "pid_file": self.pid_file,
            "log_path": self.log_path,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "VMRecord":
        """Rebuild and validate a record loaded from persistent storage.

        Args:
            payload: JSON-decoded registry object.

        Returns:
            Validated VMRecord instance.
        """

        try:
            definition_payload = payload["definition"]
            ports_payload = payload["ports"]
            if not isinstance(definition_payload, dict) or not isinstance(ports_payload, dict):
                raise VMContractError("definition and ports must be objects")
            enable_kvm = definition_payload["enable_kvm"]
            if not isinstance(enable_kvm, bool):
                raise VMContractError("definition.enable_kvm must be boolean")
            definition = VMDefinition(
                name=str(definition_payload["name"]),
                disk_path=str(definition_payload["disk_path"]),
                memory_mib=int(definition_payload["memory_mib"]),
                vcpus=int(definition_payload["vcpus"]),
                enable_kvm=enable_kvm,
            )
            ports = VMPorts(
                ssh=int(ports_payload["ssh"]),
                agent=int(ports_payload["agent"]),
                vnc=int(ports_payload["vnc"]),
            )
            pid_value = payload.get("pid")
            return cls(
                vm_id=UUID(str(payload["vm_id"])),
                definition=definition,
                ports=ports,
                agent_token=str(payload["agent_token"]),
                state=VMState(str(payload["state"])),
                pid=int(pid_value) if pid_value is not None else None,
                qmp_socket=str(payload.get("qmp_socket", "")),
                pid_file=str(payload.get("pid_file", "")),
                log_path=str(payload.get("log_path", "")),
                created_at=datetime.fromisoformat(str(payload["created_at"])),
                updated_at=datetime.fromisoformat(str(payload["updated_at"])),
                last_error=str(payload["last_error"]) if payload.get("last_error") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VMContractError(f"Invalid VM record: {exc}") from exc
