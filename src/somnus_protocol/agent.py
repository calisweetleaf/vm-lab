"""Pure authenticated guest-agent protocol values.

Source: promoted ``somnus_vm.contracts.agent`` v0.1 boundary
Integrated: 2026-08-05
Purpose: Defines strict, bounded, dependency-free guest health and result
    mappings without implementing authentication, transport, or guest IO.
IO: None.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import PurePosixPath
import re
from typing import TypeVar, cast

from ._validation import (
    ProtocolValidationError,
    canonical_json,
    decode_json_object,
    freeze_json,
    require_bool,
    require_int,
    require_str,
    strict_object,
    thaw_json,
    validate_json_value,
)


__all__ = [
    "AgentContractError",
    "AgentEndpoint",
    "AgentHealth",
    "CommandResult",
    "FileWriteResult",
]


_T = TypeVar("_T")
_HEALTH_STATE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_DETAILS_BYTES = 65_536
_MAX_COMMAND_OUTPUT_BYTES = 1_048_576
_MAX_GUEST_PATH_BYTES = 4_096
_MAX_FILE_WRITE_BYTES = 1_073_741_824

# These limits apply to the complete JSON document, not merely to the one
# field that dominates a schema.  Command output permits control characters;
# each may expand to a six-byte ``\\u0000`` escape in JSON, so its document
# ceiling deliberately accounts for that worst-case representation.
_MAX_HEALTH_JSON_BYTES = _MAX_DETAILS_BYTES + 1_024
_MAX_COMMAND_JSON_BYTES = (_MAX_COMMAND_OUTPUT_BYTES * 6) + 1_024
_MAX_FILE_WRITE_JSON_BYTES = (_MAX_GUEST_PATH_BYTES * 2) + 1_024
_HEALTH_JSON_MAX_DEPTH = 5
_HEALTH_JSON_MAX_ITEMS = 67
_COMMAND_JSON_MAX_DEPTH = 1
_COMMAND_JSON_MAX_ITEMS = 4
_FILE_WRITE_JSON_MAX_DEPTH = 1
_FILE_WRITE_JSON_MAX_ITEMS = 3


class AgentContractError(ProtocolValidationError):
    """Raised when an agent protocol value violates its bounded schema."""


def _agent_validation(function: Callable[..., _T], *args: object, **kwargs: object) -> _T:
    """Translate shared validation failures into the agent-domain exception."""

    try:
        return function(*args, **kwargs)
    except AgentContractError:
        raise
    except ProtocolValidationError as exc:
        raise AgentContractError(str(exc)) from exc


def _utf8_size(value: str, label: str) -> int:
    """Return the encoded size of one protocol string or reject invalid Unicode."""

    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise AgentContractError(f"{label} must contain valid UTF-8 text") from exc


def _health_state(value: object) -> str:
    """Validate one bounded machine-readable health state token."""

    state = _agent_validation(require_str, value, "AgentHealth.state", max_length=64)
    if not _HEALTH_STATE_PATTERN.fullmatch(state):
        raise AgentContractError(
            "AgentHealth.state must begin with a lowercase letter and contain only "
            "lowercase letters, digits, '.', '_', or '-'"
        )
    return state


def _immutable_details(value: object) -> Mapping[str, object]:
    """Validate, size-bound, and recursively freeze a health details object."""

    if type(value) is not dict:
        raise AgentContractError("AgentHealth.details must be an object")
    _agent_validation(
        validate_json_value,
        value,
        "AgentHealth.details",
        max_depth=4,
        max_items=64,
        max_string=4_096,
    )
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise AgentContractError("AgentHealth.details must be canonical JSON-compatible data") from exc
    if len(encoded) > _MAX_DETAILS_BYTES:
        raise AgentContractError(f"AgentHealth.details must not exceed {_MAX_DETAILS_BYTES} UTF-8 bytes")
    frozen = _agent_validation(
        freeze_json,
        value,
        "AgentHealth.details",
        max_depth=4,
        max_items=64,
        max_string=4_096,
    )
    if not isinstance(frozen, Mapping):
        raise AgentContractError("AgentHealth.details did not freeze to an object")
    return cast(Mapping[str, object], frozen)


def _details_dict(value: Mapping[str, object]) -> dict[str, object]:
    """Return a fresh mutable JSON mapping for wire serialization."""

    thawed = thaw_json(value)
    if type(thawed) is not dict:
        raise AgentContractError("AgentHealth.details invariant is not an object")
    return cast(dict[str, object], thawed)


def _command_output(value: object, label: str) -> str:
    """Validate one bounded command output string."""

    return _agent_validation(
        require_str,
        value,
        label,
        min_length=0,
        max_length=_MAX_COMMAND_OUTPUT_BYTES,
        allow_control_characters=True,
    )


def _guest_path(value: object) -> str:
    """Validate one canonical absolute guest file path."""

    path = _agent_validation(
        require_str,
        value,
        "FileWriteResult.path",
        max_length=_MAX_GUEST_PATH_BYTES,
    )
    if _utf8_size(path, "FileWriteResult.path") > _MAX_GUEST_PATH_BYTES:
        raise AgentContractError(
            f"FileWriteResult.path must not exceed {_MAX_GUEST_PATH_BYTES} UTF-8 bytes"
        )
    parsed = PurePosixPath(path)
    if not parsed.is_absolute() or path == "/" or path.startswith("//"):
        raise AgentContractError("FileWriteResult.path must be an absolute guest file path")
    if any(part in {".", ".."} for part in parsed.parts):
        raise AgentContractError("FileWriteResult.path must not contain traversal segments")
    if str(parsed) != path:
        raise AgentContractError("FileWriteResult.path must be canonical")
    return path


def _to_agent_json(
    value: object,
    label: str,
    *,
    max_bytes: int,
    max_depth: int,
    max_items: int,
    max_string: int,
) -> str:
    """Encode a fully validated agent value into deterministic canonical JSON."""

    return _agent_validation(
        canonical_json,
        value,
        label,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )


def _from_agent_json(
    value: object,
    label: str,
    *,
    max_bytes: int,
    max_depth: int,
    max_items: int,
    max_string: int,
) -> dict[str, object]:
    """Decode one bounded, duplicate-free JSON object for an agent schema."""

    return _agent_validation(
        decode_json_object,
        value,
        label,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )


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
    """Bounded liveness or readiness result returned by the guest agent."""

    healthy: bool
    state: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate direct construction and detach all mutable input state."""

        object.__setattr__(
            self,
            "healthy",
            _agent_validation(require_bool, self.healthy, "AgentHealth.healthy"),
        )
        object.__setattr__(self, "state", _health_state(self.state))
        object.__setattr__(self, "details", _immutable_details(self.details))

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible health mapping."""

        return {
            "healthy": self.healthy,
            "state": self.state,
            "details": _details_dict(self.details),
        }

    def to_json(self) -> str:
        """Encode this health observation as deterministic bounded JSON."""

        return _to_agent_json(
            self.to_dict(),
            "AgentHealth JSON",
            max_bytes=_MAX_HEALTH_JSON_BYTES,
            max_depth=_HEALTH_JSON_MAX_DEPTH,
            max_items=_HEALTH_JSON_MAX_ITEMS,
            max_string=4_096,
        )

    @classmethod
    def from_dict(cls, payload: object) -> "AgentHealth":
        """Decode one exact health mapping without coercion."""

        values = _agent_validation(
            strict_object,
            payload,
            required={"healthy", "state"},
            optional={"details"},
            label="AgentHealth",
        )
        return cls(
            healthy=_agent_validation(require_bool, values["healthy"], "AgentHealth.healthy"),
            state=_health_state(values["state"]),
            details=values.get("details", {}),
        )

    @classmethod
    def from_json(cls, payload: object) -> "AgentHealth":
        """Decode one exact bounded health JSON object without coercion."""

        return cls.from_dict(
            _from_agent_json(
                payload,
                "AgentHealth JSON",
                max_bytes=_MAX_HEALTH_JSON_BYTES,
                max_depth=_HEALTH_JSON_MAX_DEPTH,
                max_items=_HEALTH_JSON_MAX_ITEMS,
                max_string=4_096,
            )
        )


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded structured guest command execution result."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str

    def __post_init__(self) -> None:
        """Validate direct construction and command outcome consistency."""

        success = _agent_validation(require_bool, self.success, "CommandResult.success")
        exit_code = _agent_validation(
            require_int,
            self.exit_code,
            "CommandResult.exit_code",
            minimum=-255,
            maximum=255,
        )
        stdout = _command_output(self.stdout, "CommandResult.stdout")
        stderr = _command_output(self.stderr, "CommandResult.stderr")
        if success != (exit_code == 0):
            raise AgentContractError(
                "CommandResult.success must be true exactly when exit_code is zero"
            )
        if _utf8_size(stdout, "CommandResult.stdout") + _utf8_size(
            stderr, "CommandResult.stderr"
        ) > _MAX_COMMAND_OUTPUT_BYTES:
            raise AgentContractError(
                f"CommandResult stdout and stderr must not exceed {_MAX_COMMAND_OUTPUT_BYTES} combined UTF-8 bytes"
            )
        object.__setattr__(self, "success", success)
        object.__setattr__(self, "exit_code", exit_code)
        object.__setattr__(self, "stdout", stdout)
        object.__setattr__(self, "stderr", stderr)

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible command result mapping."""

        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    def to_json(self) -> str:
        """Encode this command result as deterministic bounded JSON."""

        return _to_agent_json(
            self.to_dict(),
            "CommandResult JSON",
            max_bytes=_MAX_COMMAND_JSON_BYTES,
            max_depth=_COMMAND_JSON_MAX_DEPTH,
            max_items=_COMMAND_JSON_MAX_ITEMS,
            max_string=_MAX_COMMAND_OUTPUT_BYTES,
        )

    @classmethod
    def from_dict(cls, payload: object) -> "CommandResult":
        """Decode one exact command result mapping without coercion."""

        values = _agent_validation(
            strict_object,
            payload,
            required={"success", "exit_code"},
            optional={"stdout", "stderr"},
            label="CommandResult",
        )
        return cls(
            success=_agent_validation(require_bool, values["success"], "CommandResult.success"),
            exit_code=_agent_validation(
                require_int,
                values["exit_code"],
                "CommandResult.exit_code",
                minimum=-255,
                maximum=255,
            ),
            stdout=_command_output(values.get("stdout", ""), "CommandResult.stdout"),
            stderr=_command_output(values.get("stderr", ""), "CommandResult.stderr"),
        )

    @classmethod
    def from_json(cls, payload: object) -> "CommandResult":
        """Decode one exact bounded command-result JSON object without coercion."""

        return cls.from_dict(
            _from_agent_json(
                payload,
                "CommandResult JSON",
                max_bytes=_MAX_COMMAND_JSON_BYTES,
                max_depth=_COMMAND_JSON_MAX_DEPTH,
                max_items=_COMMAND_JSON_MAX_ITEMS,
                max_string=_MAX_COMMAND_OUTPUT_BYTES,
            )
        )


@dataclass(frozen=True, slots=True)
class FileWriteResult:
    """Bounded structured response for an authenticated guest file write."""

    success: bool
    path: str
    bytes_written: int

    def __post_init__(self) -> None:
        """Validate direct construction and atomic-write result consistency."""

        success = _agent_validation(require_bool, self.success, "FileWriteResult.success")
        path = _guest_path(self.path)
        bytes_written = _agent_validation(
            require_int,
            self.bytes_written,
            "FileWriteResult.bytes_written",
            minimum=0,
            maximum=_MAX_FILE_WRITE_BYTES,
        )
        if not success and bytes_written != 0:
            raise AgentContractError(
                "FileWriteResult.bytes_written must be zero when success is false"
            )
        object.__setattr__(self, "success", success)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "bytes_written", bytes_written)

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible file-write result mapping."""

        return {
            "success": self.success,
            "path": self.path,
            "bytes_written": self.bytes_written,
        }

    def to_json(self) -> str:
        """Encode this atomic-write observation as deterministic bounded JSON."""

        return _to_agent_json(
            self.to_dict(),
            "FileWriteResult JSON",
            max_bytes=_MAX_FILE_WRITE_JSON_BYTES,
            max_depth=_FILE_WRITE_JSON_MAX_DEPTH,
            max_items=_FILE_WRITE_JSON_MAX_ITEMS,
            max_string=_MAX_GUEST_PATH_BYTES,
        )

    @classmethod
    def from_dict(cls, payload: object) -> "FileWriteResult":
        """Decode one exact file-write result mapping without coercion."""

        values = _agent_validation(
            strict_object,
            payload,
            required={"success", "path", "bytes_written"},
            label="FileWriteResult",
        )
        return cls(
            success=_agent_validation(
                require_bool, values["success"], "FileWriteResult.success"
            ),
            path=_guest_path(values["path"]),
            bytes_written=_agent_validation(
                require_int,
                values["bytes_written"],
                "FileWriteResult.bytes_written",
                minimum=0,
                maximum=_MAX_FILE_WRITE_BYTES,
            ),
        )

    @classmethod
    def from_json(cls, payload: object) -> "FileWriteResult":
        """Decode one exact bounded file-write JSON object without coercion."""

        return cls.from_dict(
            _from_agent_json(
                payload,
                "FileWriteResult JSON",
                max_bytes=_MAX_FILE_WRITE_JSON_BYTES,
                max_depth=_FILE_WRITE_JSON_MAX_DEPTH,
                max_items=_FILE_WRITE_JSON_MAX_ITEMS,
                max_string=_MAX_GUEST_PATH_BYTES,
            )
        )
