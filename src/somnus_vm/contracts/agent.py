"""Authenticated host-to-guest protocol contracts.

Source: supervisor and digital-twin lineage in vm_lab.zip
Integrated: 2026-08-04
Purpose: Defines one endpoint and result authority so host and guest code cannot
    silently drift to incompatible URLs or response fields.
IO: Pure mapping validation; network IO belongs to host.agent_client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AgentContractError(ValueError):
    """Raised when a guest response violates the declared protocol."""


class AgentEndpoint(str, Enum):
    """Version-one localhost guest-agent endpoint surface."""

    HEALTH_LIVE = "/health/live"
    HEALTH_READY = "/health/ready"
    STATUS = "/status"
    STATS = "/stats"
    EXECUTE = "/execute"
    FILE_WRITE = "/files/write"
    SOFT_REBOOT = "/soft-reboot"


@dataclass(frozen=True, slots=True)
class AgentHealth:
    """Liveness or readiness result returned by the guest agent."""

    healthy: bool
    state: str
    details: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AgentHealth":
        """Validate a decoded health payload."""

        healthy = payload.get("healthy")
        state = payload.get("state")
        if not isinstance(healthy, bool) or not isinstance(state, str):
            raise AgentContractError("Health payload requires boolean healthy and string state")
        details = payload.get("details", {})
        if not isinstance(details, dict):
            raise AgentContractError("Health details must be an object")
        return cls(healthy=healthy, state=state, details=details)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Structured guest command execution result."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CommandResult":
        """Validate a decoded command response."""

        try:
            success = payload["success"]
            if not isinstance(success, bool):
                raise AgentContractError("Command success must be boolean")
            exit_code = payload["exit_code"]
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                raise AgentContractError("Command exit_code must be an integer")
            return cls(
                success=success,
                exit_code=exit_code,
                stdout=str(payload.get("stdout", "")),
                stderr=str(payload.get("stderr", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentContractError(f"Invalid command result: {exc}") from exc


@dataclass(frozen=True, slots=True)
class FileWriteResult:
    """Structured response for an authenticated guest file write."""

    success: bool
    path: str
    bytes_written: int

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "FileWriteResult":
        """Validate a decoded file-write response."""

        try:
            success = payload["success"]
            if not isinstance(success, bool):
                raise AgentContractError("File write success must be boolean")
            bytes_written = payload["bytes_written"]
            if not isinstance(bytes_written, int) or isinstance(bytes_written, bool) or bytes_written < 0:
                raise AgentContractError("File write bytes_written must be a non-negative integer")
            return cls(
                success=success,
                path=str(payload["path"]),
                bytes_written=bytes_written,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentContractError(f"Invalid file write result: {exc}") from exc
