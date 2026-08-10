"""Bounded QEMU Machine Protocol client over one private Unix socket.

Source: PLAN.md P4-008 through P4-013
Integrated: 2026-08-05
Purpose: Negotiates one QMP session, correlates monotonic command IDs, and
    separates asynchronous QMP events from command responses.
IO: One canonical private AF_UNIX path and one background reader thread.

This module is protocol machinery only.  A successful greeting, capability
negotiation, or query response is not QEMU process identity, VM readiness, or
lifecycle truth.  The P4 process owner must correlate these observations with
the owned child, pidfile, executable, command hash, and declared VM UUID.
"""

from __future__ import annotations

import errno
import math
import os
import re
import select
import socket
import stat
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from somnus_protocol._validation import (
    ProtocolValidationError,
    canonical_json,
    decode_json_object,
    freeze_json,
    require_int,
    require_str,
    strict_object,
    thaw_json,
    validate_json_value,
)

from ..transport import (
    PeerCredentials,
    TransportError,
    get_peer_credentials,
    normalize_socket_path,
    normalize_uid_allowlist,
)


DEFAULT_QMP_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_QMP_MAX_MESSAGE_BYTES: Final[int] = 1_048_576
DEFAULT_QMP_MAX_DEPTH: Final[int] = 16
DEFAULT_QMP_MAX_ITEMS: Final[int] = 8_192
DEFAULT_QMP_MAX_STRING: Final[int] = 262_144
DEFAULT_QMP_MAX_EVENTS: Final[int] = 1_024
DEFAULT_QMP_MAX_RESPONSES: Final[int] = 4_096

_MAX_QMP_MESSAGE_BYTES: Final[int] = 16_777_216
_MAX_QMP_DEPTH: Final[int] = 64
_MAX_QMP_ITEMS: Final[int] = 65_536
_MAX_QMP_STRING: Final[int] = 1_048_576
_MAX_QMP_EVENTS: Final[int] = 65_536
_MAX_QMP_RESPONSES: Final[int] = 65_536
_MAX_COMMAND_ID: Final[int] = 2**63 - 1
_READ_CHUNK_BYTES: Final[int] = 65_536
_READER_POLL_SECONDS: Final[float] = 0.1

_COMMAND_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z][a-z0-9_-]*",
    re.ASCII,
)
_CAPABILITY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9_.-]*",
    re.ASCII,
)
_ERROR_CLASS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9_.-]*",
    re.ASCII,
)
_EVENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Z][A-Z0-9_]*",
    re.ASCII,
)

CORE_QMP_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "query-status",
        "query-name",
        "query-uuid",
        "query-block",
        "query-cpus-fast",
        "system_powerdown",
        "quit",
    }
)

# This is the reviewed block-graph membrane required by P4-010 and the later
# P9 snapshot gate.  Presence here grants only QMP transport representation;
# operation-specific ownership, quiesce, transaction, and chain policy remain
# outside this client.
BLOCK_GRAPH_QMP_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "query-blockstats",
        "query-fdsets",
        "query-named-block-nodes",
        "query-block-jobs",
        "blockdev-add",
        "blockdev-del",
        "blockdev-snapshot",
        "blockdev-snapshot-sync",
        "block-commit",
        "block-stream",
        "block-job-cancel",
        "block-job-complete",
        "block-job-dismiss",
        "block-job-finalize",
        "transaction",
    }
)

ALLOWED_QMP_COMMANDS: Final[frozenset[str]] = (
    CORE_QMP_COMMANDS | BLOCK_GRAPH_QMP_COMMANDS
)

_NO_ARGUMENT_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "query-blockstats",
        "query-fdsets",
        "query-status",
        "query-name",
        "query-uuid",
        "query-block",
        "query-cpus-fast",
        "query-named-block-nodes",
        "query-block-jobs",
        "system_powerdown",
        "quit",
    }
)


class QMPError(RuntimeError):
    """Base class for bounded QMP failures."""


class QMPValidationError(QMPError):
    """Raised before I/O when local QMP intent is invalid."""


class QMPUnavailable(QMPError):
    """Raised when the private QMP endpoint cannot be reached safely."""


class QMPTimeout(QMPError):
    """Raised when one absolute QMP operation deadline expires."""


class QMPProtocolError(QMPError):
    """Raised when the peer violates the bounded QMP wire contract."""


class QMPHandshakeError(QMPProtocolError):
    """Raised when greeting or mandatory capability negotiation fails."""


class QMPStateError(QMPError):
    """Raised when an operation is invalid for the client session state."""


class QMPCommandError(QMPError):
    """One strict QMP error response correlated to an issued command."""

    def __init__(
        self,
        *,
        command: str,
        command_id: int,
        error_class: str,
        description: str,
        data: object,
    ) -> None:
        super().__init__(
            f"QMP command {command!r} failed with class {error_class!r}"
        )
        self.command = command
        self.command_id = command_id
        self.error_class = error_class
        self.description = description
        self.data = data


@dataclass(frozen=True, slots=True)
class QMPVersion:
    """Strict version tuple from the QMP greeting."""

    major: int
    minor: int
    micro: int
    package: str

    def to_dict(self) -> dict[str, object]:
        return {
            "major": self.major,
            "micro": self.micro,
            "minor": self.minor,
            "package": self.package,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict(), "QMP version")

    @property
    def canonical_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


@dataclass(frozen=True, slots=True)
class QMPGreeting:
    """Validated QMP greeting; not process identity or readiness."""

    version: QMPVersion
    capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "capabilities": list(self.capabilities),
            "version": self.version.to_dict(),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict(), "QMP greeting")

    @property
    def canonical_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


@dataclass(frozen=True, slots=True)
class QMPResponse:
    """One ID-correlated successful command response."""

    command: str
    command_id: int
    value: object

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "command_id": self.command_id,
            "value": thaw_json(self.value),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict(), "QMP response")

    @property
    def canonical_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


@dataclass(frozen=True, slots=True)
class QMPEvent:
    """One asynchronous event separated from command responses."""

    name: str
    data: object
    timestamp_seconds: int
    timestamp_microseconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "data": thaw_json(self.data),
            "name": self.name,
            "timestamp_microseconds": self.timestamp_microseconds,
            "timestamp_seconds": self.timestamp_seconds,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict(), "QMP event")

    @property
    def canonical_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


@dataclass(frozen=True, slots=True)
class QMPSocketIdentity:
    """Stable pathname identity observed across the authenticated QMP connect."""

    device_id: int
    inode: int
    uid: int
    mode: int

    def to_dict(self) -> dict[str, object]:
        return {
            "device_id": self.device_id,
            "inode": self.inode,
            "mode": self.mode,
            "uid": self.uid,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict(), "QMP socket identity")

    @property
    def canonical_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


@dataclass(slots=True)
class _PendingResponse:
    command: str
    ready: threading.Event
    response: "_IncomingResponse | None" = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _IncomingResponse:
    command_id: int
    value: object | None
    error_class: str | None
    error_description: str | None
    error_data: object | None


class _ReaderStopped(Exception):
    """Internal non-failure used to stop the background reader."""


def _bounded_int(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise QMPValidationError(
            f"{label} must be an integer between {minimum} and {maximum}"
        )
    return value


def _bounded_timeout(value: object, label: str = "timeout_seconds") -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise QMPValidationError(f"{label} must be a finite positive number")
    normalized = float(value)
    if (
        not math.isfinite(normalized)
        or normalized <= 0.0
        or normalized > 86_400.0
    ):
        raise QMPValidationError(
            f"{label} must be greater than zero and at most 86400 seconds"
        )
    return normalized


def _deadline_after(timeout_seconds: object) -> float:
    return time.monotonic() + _bounded_timeout(timeout_seconds)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0.0:
        raise QMPTimeout("QMP operation deadline expired")
    return remaining


def _validate_command_name(value: object) -> str:
    if not isinstance(value, str) or _COMMAND_PATTERN.fullmatch(value) is None:
        raise QMPValidationError("QMP command name is invalid")
    if value not in ALLOWED_QMP_COMMANDS:
        raise QMPValidationError("QMP command is outside the reviewed allowlist")
    return value


def _validate_arguments(
    command: str,
    arguments: Mapping[str, object] | None,
    *,
    max_depth: int,
    max_items: int,
    max_string: int,
) -> dict[str, object] | None:
    if arguments is None:
        if command not in _NO_ARGUMENT_COMMANDS:
            raise QMPValidationError(
                "block-graph QMP commands require an explicit arguments object"
            )
        return None
    if not isinstance(arguments, Mapping):
        raise QMPValidationError("QMP command arguments must be a mapping")
    if command in _NO_ARGUMENT_COMMANDS and len(arguments) != 0:
        raise QMPValidationError(
            "this QMP command does not accept caller-supplied arguments"
        )
    if any(not isinstance(key, str) or not key for key in arguments):
        raise QMPValidationError(
            "QMP command argument keys must be non-empty strings"
        )
    materialized = dict(arguments)
    try:
        validate_json_value(
            materialized,
            "QMP command arguments",
            max_depth=max_depth,
            max_items=max_items,
            max_string=max_string,
        )
    except ProtocolValidationError as exc:
        raise QMPValidationError("QMP command arguments are invalid") from exc
    return materialized


def _observe_private_socket(
    path: Path,
    allowed_uids: frozenset[int],
) -> QMPSocketIdentity:
    """Require one canonical, same-owner, private Unix socket."""

    parent = path.parent
    try:
        if parent.resolve(strict=True) != parent:
            raise QMPUnavailable(
                "QMP socket parent traverses a symbolic link"
            )
        parent_details = parent.lstat()
    except FileNotFoundError as exc:
        raise QMPUnavailable("QMP socket parent is unavailable") from exc
    except OSError as exc:
        raise QMPUnavailable("QMP socket parent cannot be observed") from exc
    if (
        not stat.S_ISDIR(parent_details.st_mode)
        or stat.S_ISLNK(parent_details.st_mode)
        or parent_details.st_uid not in allowed_uids
        or stat.S_IMODE(parent_details.st_mode) != 0o700
    ):
        raise QMPUnavailable(
            "QMP socket parent is not a private authorized directory"
        )

    try:
        if path.resolve(strict=True) != path:
            raise QMPUnavailable("QMP socket path is not canonical")
        details = path.lstat()
    except FileNotFoundError as exc:
        raise QMPUnavailable("QMP socket is unavailable") from exc
    except OSError as exc:
        raise QMPUnavailable("QMP socket cannot be observed") from exc
    mode = stat.S_IMODE(details.st_mode)
    if (
        not stat.S_ISSOCK(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid not in allowed_uids
        or mode != 0o600
    ):
        raise QMPUnavailable(
            "QMP endpoint is not a private authorized Unix socket"
        )
    return QMPSocketIdentity(
        device_id=details.st_dev,
        inode=details.st_ino,
        uid=details.st_uid,
        mode=mode,
    )


class QMPClient:
    """Persistent, thread-safe QMP protocol client with one reader owner."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = DEFAULT_QMP_TIMEOUT_SECONDS,
        max_message_bytes: int = DEFAULT_QMP_MAX_MESSAGE_BYTES,
        max_depth: int = DEFAULT_QMP_MAX_DEPTH,
        max_items: int = DEFAULT_QMP_MAX_ITEMS,
        max_string: int = DEFAULT_QMP_MAX_STRING,
        max_events: int = DEFAULT_QMP_MAX_EVENTS,
        max_responses: int = DEFAULT_QMP_MAX_RESPONSES,
        max_response_history: int | None = None,
        allowed_server_uids: Iterable[int] | None = None,
    ) -> None:
        try:
            self._socket_path = normalize_socket_path(socket_path)
            self._allowed_server_uids = normalize_uid_allowlist(
                allowed_server_uids
            )
        except TransportError as exc:
            raise QMPValidationError("QMP socket configuration is invalid") from exc
        self._timeout_seconds = _bounded_timeout(timeout_seconds)
        self._max_message_bytes = _bounded_int(
            max_message_bytes,
            "max_message_bytes",
            minimum=64,
            maximum=_MAX_QMP_MESSAGE_BYTES,
        )
        self._max_depth = _bounded_int(
            max_depth,
            "max_depth",
            minimum=3,
            maximum=_MAX_QMP_DEPTH,
        )
        self._max_items = _bounded_int(
            max_items,
            "max_items",
            minimum=8,
            maximum=_MAX_QMP_ITEMS,
        )
        self._max_string = _bounded_int(
            max_string,
            "max_string",
            minimum=16,
            maximum=_MAX_QMP_STRING,
        )
        self._max_events = _bounded_int(
            max_events,
            "max_events",
            minimum=1,
            maximum=_MAX_QMP_EVENTS,
        )
        if max_response_history is not None:
            if max_responses != DEFAULT_QMP_MAX_RESPONSES:
                raise QMPValidationError(
                    "max_responses and max_response_history cannot both be set"
                )
            max_responses = max_response_history
        self._max_responses = _bounded_int(
            max_responses,
            "max_responses",
            minimum=1,
            maximum=_MAX_QMP_RESPONSES,
        )

        self._state_lock = threading.RLock()
        self._event_ready = threading.Condition(self._state_lock)
        self._send_lock = threading.Lock()
        self._stop_reader = threading.Event()
        self._socket: socket.socket | None = None
        self._socket_identity: QMPSocketIdentity | None = None
        self._peer_credentials: PeerCredentials | None = None
        self._greeting: QMPGreeting | None = None
        self._state = "new"
        self._fatal_error: BaseException | None = None
        self._next_command_id = 1
        self._pending: dict[int, _PendingResponse] = {}
        self._events: deque[QMPEvent] = deque()
        self._responses: deque[QMPResponse] = deque(maxlen=self._max_responses)
        self._buffer = bytearray()
        self._reader: threading.Thread | None = None

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._state == "ready" and self._fatal_error is None

    @property
    def greeting(self) -> QMPGreeting:
        with self._state_lock:
            if self._greeting is None:
                raise QMPStateError("QMP greeting is unavailable")
            return self._greeting

    @property
    def peer_credentials(self) -> PeerCredentials:
        with self._state_lock:
            if self._peer_credentials is None:
                raise QMPStateError("QMP peer credentials are unavailable")
            return self._peer_credentials

    @property
    def socket_identity(self) -> QMPSocketIdentity:
        """Return the exact private socket inode bound during connection."""

        with self._state_lock:
            if self._socket_identity is None:
                raise QMPStateError("QMP socket identity is unavailable")
            return self._socket_identity

    @property
    def pending_event_count(self) -> int:
        with self._state_lock:
            return len(self._events)

    @property
    def response_history(self) -> tuple[QMPResponse, ...]:
        """Return a bounded, arrival-ordered copy of successful responses.

        The reader owns insertion order.  The returned tuple and every nested
        response value are immutable, so callers can seal the result without
        retaining a mutable alias to the live client.
        """

        with self._state_lock:
            return tuple(self._responses)

    def _record_response(
        self,
        command: str,
        response: _IncomingResponse,
    ) -> QMPResponse:
        if response.error_class is not None:
            raise QMPProtocolError(
                "only successful QMP responses can enter response history"
            )
        recorded = QMPResponse(
            command=command,
            command_id=response.command_id,
            value=response.value,
        )
        with self._state_lock:
            self._responses.append(recorded)
        return recorded

    def _allocate_command_id(self) -> int:
        with self._state_lock:
            if self._next_command_id > _MAX_COMMAND_ID:
                raise QMPStateError("QMP command ID space is exhausted")
            command_id = self._next_command_id
            self._next_command_id += 1
            return command_id

    def _encode_command(
        self,
        command: str,
        command_id: int,
        arguments: dict[str, object] | None,
    ) -> bytes:
        row: dict[str, object] = {
            "execute": command,
            "id": command_id,
        }
        if arguments is not None:
            row["arguments"] = arguments
        try:
            payload = canonical_json(
                row,
                "QMP command",
                max_depth=self._max_depth,
                max_items=self._max_items,
                max_string=self._max_string,
                max_bytes=self._max_message_bytes,
            ).encode("utf-8")
        except (ProtocolValidationError, UnicodeError) as exc:
            raise QMPValidationError(
                "QMP command exceeds the configured wire bounds"
            ) from exc
        return payload + b"\r\n"

    def _send_bytes(self, payload: bytes, deadline: float) -> None:
        connection = self._socket
        if connection is None:
            raise QMPStateError("QMP socket is not connected")
        view = memoryview(payload)
        sent = 0
        while sent < len(view):
            timeout = _remaining(deadline)
            try:
                _, writable, exceptional = select.select(
                    (),
                    (connection,),
                    (connection,),
                    timeout,
                )
            except (OSError, ValueError) as exc:
                raise QMPProtocolError("QMP send readiness failed") from exc
            if exceptional:
                raise QMPProtocolError("QMP socket entered an exceptional state")
            if not writable:
                raise QMPTimeout("QMP operation deadline expired")
            try:
                count = connection.send(view[sent:])
            except BlockingIOError:
                continue
            except OSError as exc:
                raise QMPProtocolError("QMP command send failed") from exc
            if count <= 0:
                raise QMPProtocolError("QMP command send made no progress")
            sent += count

    def _extract_line(self) -> bytes | None:
        delimiter = self._buffer.find(b"\n")
        if delimiter < 0:
            if len(self._buffer) > self._max_message_bytes:
                # Permit only a possible CR following an exactly max-sized
                # payload while waiting for its LF.
                if not (
                    len(self._buffer) == self._max_message_bytes + 1
                    and self._buffer[-1] == 0x0D
                ):
                    raise QMPProtocolError(
                        "QMP message exceeds the configured byte limit"
                    )
            return None
        payload_end = (
            delimiter - 1
            if delimiter > 0 and self._buffer[delimiter - 1] == 0x0D
            else delimiter
        )
        if payload_end > self._max_message_bytes:
            raise QMPProtocolError(
                "QMP message exceeds the configured byte limit"
            )
        payload = bytes(self._buffer[:payload_end])
        del self._buffer[: delimiter + 1]
        if not payload:
            raise QMPProtocolError("QMP message cannot be empty")
        return payload

    def _receive_line(self, deadline: float | None) -> bytes:
        while True:
            payload = self._extract_line()
            if payload is not None:
                return payload
            if self._stop_reader.is_set() and deadline is None:
                raise _ReaderStopped
            connection = self._socket
            if connection is None:
                if self._stop_reader.is_set():
                    raise _ReaderStopped
                raise QMPProtocolError("QMP socket closed unexpectedly")
            timeout = (
                _remaining(deadline)
                if deadline is not None
                else _READER_POLL_SECONDS
            )
            try:
                readable, _, exceptional = select.select(
                    (connection,),
                    (),
                    (connection,),
                    timeout,
                )
            except (OSError, ValueError) as exc:
                if self._stop_reader.is_set():
                    raise _ReaderStopped from exc
                raise QMPProtocolError("QMP receive readiness failed") from exc
            if exceptional:
                raise QMPProtocolError("QMP socket entered an exceptional state")
            if not readable:
                if deadline is not None:
                    raise QMPTimeout("QMP operation deadline expired")
                continue
            try:
                chunk = connection.recv(_READ_CHUNK_BYTES)
            except BlockingIOError:
                continue
            except OSError as exc:
                if self._stop_reader.is_set():
                    raise _ReaderStopped from exc
                raise QMPProtocolError("QMP receive failed") from exc
            if not chunk:
                if self._stop_reader.is_set():
                    raise _ReaderStopped
                if self._buffer:
                    raise QMPProtocolError("QMP message ended before newline")
                raise QMPProtocolError("QMP peer closed the session unexpectedly")
            self._buffer.extend(chunk)

    def _decode_object(self, payload: bytes, label: str) -> dict[str, object]:
        try:
            return decode_json_object(
                payload,
                label,
                max_bytes=self._max_message_bytes,
                max_depth=self._max_depth,
                max_items=self._max_items,
                max_string=self._max_string,
            )
        except (
            ProtocolValidationError,
            UnicodeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise QMPProtocolError(f"{label} is invalid") from exc

    def _parse_greeting(self, payload: bytes) -> QMPGreeting:
        try:
            row = strict_object(
                self._decode_object(payload, "QMP greeting"),
                {"QMP"},
                label="QMP greeting",
            )
            qmp = strict_object(
                row["QMP"],
                {"version", "capabilities"},
                label="QMP greeting body",
            )
            version = strict_object(
                qmp["version"],
                {"qemu", "package"},
                label="QMP greeting version",
            )
            qemu = strict_object(
                version["qemu"],
                {"major", "minor", "micro"},
                label="QMP greeting qemu version",
            )
            major = require_int(
                qemu["major"],
                "QMP major version",
                minimum=0,
                maximum=2**31 - 1,
            )
            minor = require_int(
                qemu["minor"],
                "QMP minor version",
                minimum=0,
                maximum=2**31 - 1,
            )
            micro = require_int(
                qemu["micro"],
                "QMP micro version",
                minimum=0,
                maximum=2**31 - 1,
            )
            package = require_str(
                version["package"],
                "QMP package",
                min_length=0,
                max_length=min(self._max_string, 4_096),
            )
            capability_values = qmp["capabilities"]
            if not isinstance(capability_values, list):
                raise ProtocolValidationError(
                    "QMP greeting capabilities must be a list"
                )
            capabilities: list[str] = []
            for index, value in enumerate(capability_values):
                capabilities.append(
                    require_str(
                        value,
                        f"QMP capability {index}",
                        min_length=1,
                        max_length=min(self._max_string, 128),
                        pattern=_CAPABILITY_PATTERN,
                    )
                )
            if len(capabilities) != len(set(capabilities)):
                raise ProtocolValidationError(
                    "QMP greeting capabilities contain duplicates"
                )
        except (
            QMPProtocolError,
            ProtocolValidationError,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            raise QMPHandshakeError("QMP greeting shape is invalid") from exc
        return QMPGreeting(
            version=QMPVersion(
                major=major,
                minor=minor,
                micro=micro,
                package=package,
            ),
            capabilities=tuple(capabilities),
        )

    def _parse_event(self, row: dict[str, object]) -> QMPEvent:
        try:
            selected = strict_object(
                row,
                {"event", "timestamp"},
                {"data"},
                label="QMP event",
            )
            name = require_str(
                selected["event"],
                "QMP event name",
                min_length=1,
                max_length=min(self._max_string, 128),
                pattern=_EVENT_PATTERN,
            )
            timestamp = strict_object(
                selected["timestamp"],
                {"seconds", "microseconds"},
                label="QMP event timestamp",
            )
            seconds = require_int(
                timestamp["seconds"],
                "QMP event seconds",
                minimum=0,
                maximum=_MAX_COMMAND_ID,
            )
            microseconds = require_int(
                timestamp["microseconds"],
                "QMP event microseconds",
                minimum=0,
                maximum=999_999,
            )
            data = freeze_json(
                selected.get("data", {}),
                "QMP event data",
                max_depth=self._max_depth,
                max_items=self._max_items,
                max_string=self._max_string,
            )
        except (
            ProtocolValidationError,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            raise QMPProtocolError("QMP event shape is invalid") from exc
        return QMPEvent(
            name=name,
            data=data,
            timestamp_seconds=seconds,
            timestamp_microseconds=microseconds,
        )

    def _parse_response(self, row: dict[str, object]) -> _IncomingResponse:
        has_return = "return" in row
        has_error = "error" in row
        if has_return == has_error:
            raise QMPProtocolError(
                "QMP response must contain exactly one return or error member"
            )
        try:
            selected = strict_object(
                row,
                {"id", "return" if has_return else "error"},
                label="QMP response",
            )
            command_id = require_int(
                selected["id"],
                "QMP response id",
                minimum=1,
                maximum=_MAX_COMMAND_ID,
            )
            if has_return:
                value = freeze_json(
                    selected["return"],
                    "QMP response value",
                    max_depth=self._max_depth,
                    max_items=self._max_items,
                    max_string=self._max_string,
                )
                return _IncomingResponse(
                    command_id=command_id,
                    value=value,
                    error_class=None,
                    error_description=None,
                    error_data=None,
                )
            error = strict_object(
                selected["error"],
                {"class", "desc"},
                {"data"},
                label="QMP error",
            )
            error_class = require_str(
                error["class"],
                "QMP error class",
                min_length=1,
                max_length=min(self._max_string, 128),
                pattern=_ERROR_CLASS_PATTERN,
            )
            description = require_str(
                error["desc"],
                "QMP error description",
                min_length=0,
                max_length=min(self._max_string, 4_096),
            )
            data = freeze_json(
                error.get("data", {}),
                "QMP error data",
                max_depth=self._max_depth,
                max_items=self._max_items,
                max_string=self._max_string,
            )
            return _IncomingResponse(
                command_id=command_id,
                value=None,
                error_class=error_class,
                error_description=description,
                error_data=data,
            )
        except (
            ProtocolValidationError,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            raise QMPProtocolError("QMP response shape is invalid") from exc

    def _parse_message(
        self,
        payload: bytes,
    ) -> QMPEvent | _IncomingResponse:
        row = self._decode_object(payload, "QMP message")
        if "event" in row:
            return self._parse_event(row)
        if "return" in row or "error" in row:
            return self._parse_response(row)
        raise QMPProtocolError("QMP message has an unexpected top-level shape")

    def _queue_event(self, event: QMPEvent) -> None:
        with self._event_ready:
            if len(self._events) >= self._max_events:
                raise QMPProtocolError("QMP event queue capacity was exceeded")
            self._events.append(event)
            self._event_ready.notify_all()

    @staticmethod
    def _command_error(
        command: str,
        response: _IncomingResponse,
    ) -> QMPCommandError:
        assert response.error_class is not None
        assert response.error_description is not None
        return QMPCommandError(
            command=command,
            command_id=response.command_id,
            error_class=response.error_class,
            description=response.error_description,
            data=response.error_data,
        )

    def _fail_session(self, error: BaseException) -> None:
        with self._event_ready:
            if self._state == "closed":
                return
            if self._fatal_error is None:
                self._fatal_error = error
            selected = self._fatal_error
            self._state = "failed"
            pending = tuple(self._pending.values())
            self._pending.clear()
            self._event_ready.notify_all()
        for waiter in pending:
            waiter.error = selected
            waiter.ready.set()
        connection = self._socket
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _dispatch(self, message: QMPEvent | _IncomingResponse) -> None:
        if isinstance(message, QMPEvent):
            self._queue_event(message)
            return
        with self._state_lock:
            pending = self._pending.pop(message.command_id, None)
        if pending is None:
            raise QMPProtocolError(
                "QMP response ID does not match an outstanding command"
            )
        if message.error_class is None:
            self._record_response(pending.command, message)
        pending.response = message
        pending.ready.set()

    def _reader_loop(self) -> None:
        try:
            while not self._stop_reader.is_set():
                payload = self._receive_line(None)
                self._dispatch(self._parse_message(payload))
        except _ReaderStopped:
            return
        except QMPError as exc:
            self._fail_session(exc)
        except Exception as exc:
            # This is an intentional total thread-boundary fence.  The reader
            # has no caller stack in which an unexpected stdlib/runtime error
            # can surface; allowing it to die would leave pending commands
            # waiting forever and could make a dead QMP peer look live.  Keep
            # the failure terminal and convert it to protocol evidence.  The
            # original exception remains attached as ``__cause__``.
            error = QMPProtocolError("unexpected QMP reader failure")
            error.__cause__ = exc
            self._fail_session(error)

    def _connect_socket(self, deadline: float) -> PeerCredentials:
        before = _observe_private_socket(
            self._socket_path,
            self._allowed_server_uids,
        )
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = connection
        try:
            connection.settimeout(_remaining(deadline))
            connection.connect(os.fspath(self._socket_path))
            peer = get_peer_credentials(connection)
            if peer.uid not in self._allowed_server_uids:
                raise QMPUnavailable(
                    "kernel-authenticated QMP peer uid is not authorized"
                )
            after = _observe_private_socket(
                self._socket_path,
                self._allowed_server_uids,
            )
            if before != after or peer.uid != after.uid:
                raise QMPUnavailable(
                    "QMP socket identity changed during connection"
                )
            connection.setblocking(False)
        except QMPError:
            raise
        except TransportError as exc:
            raise QMPUnavailable(
                "QMP peer credentials are unavailable"
            ) from exc
        except TimeoutError as exc:
            raise QMPTimeout("QMP operation deadline expired") from exc
        except OSError as exc:
            raise QMPUnavailable("QMP endpoint is unavailable") from exc
        self._socket_identity = after
        return peer

    def connect(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPGreeting:
        """Connect, validate the greeting, and negotiate capabilities.

        One absolute deadline covers path observation, connect, greeting
        framing, command send, interleaved events, and capability response.
        """

        timeout = (
            self._timeout_seconds
            if timeout_seconds is None
            else _bounded_timeout(timeout_seconds)
        )
        deadline = _deadline_after(timeout)
        with self._state_lock:
            if self._state != "new":
                raise QMPStateError("QMP client is single-use")
            self._state = "connecting"
        try:
            peer = self._connect_socket(deadline)
            greeting = self._parse_greeting(self._receive_line(deadline))
            command_id = self._allocate_command_id()
            self._send_bytes(
                self._encode_command(
                    "qmp_capabilities",
                    command_id,
                    None,
                ),
                deadline,
            )
            while True:
                incoming = self._parse_message(self._receive_line(deadline))
                if isinstance(incoming, QMPEvent):
                    self._queue_event(incoming)
                    continue
                if incoming.command_id != command_id:
                    raise QMPHandshakeError(
                        "QMP capability response ID is mismatched"
                    )
                if incoming.error_class is not None:
                    raise QMPHandshakeError(
                        "QMP capability negotiation was rejected"
                    ) from self._command_error("qmp_capabilities", incoming)
                if thaw_json(incoming.value) != {}:
                    raise QMPHandshakeError(
                        "QMP capability response must return an empty object"
                    )
                self._record_response("qmp_capabilities", incoming)
                break
            with self._state_lock:
                self._peer_credentials = peer
                self._greeting = greeting
                self._state = "ready"
                reader = threading.Thread(
                    target=self._reader_loop,
                    name=f"qmp-reader-{peer.pid}",
                    daemon=True,
                )
                self._reader = reader
                reader.start()
            return greeting
        except QMPError as exc:
            self._fail_session(exc)
            self._close_socket()
            raise
        except Exception as exc:
            # Connection setup owns the socket and handshake state.  This
            # total cleanup fence is required for an unexpected thread,
            # decoder, or runtime failure: close the authenticated endpoint,
            # publish terminal failure to the client, and never return a
            # partially negotiated session as machine truth.
            error = QMPProtocolError("QMP connection setup failed")
            self._fail_session(error)
            self._close_socket()
            raise error from exc

    def _ensure_ready(self) -> None:
        if self._fatal_error is not None:
            raise self._fatal_error
        if self._state != "ready":
            raise QMPStateError("QMP capability negotiation is incomplete")

    def execute(
        self,
        command: str,
        arguments: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPResponse:
        """Execute one allowlisted command under one absolute deadline."""

        selected = _validate_command_name(command)
        materialized = _validate_arguments(
            selected,
            arguments,
            max_depth=self._max_depth,
            max_items=self._max_items,
            max_string=self._max_string,
        )
        timeout = (
            self._timeout_seconds
            if timeout_seconds is None
            else _bounded_timeout(timeout_seconds)
        )
        deadline = _deadline_after(timeout)
        if not self._send_lock.acquire(timeout=_remaining(deadline)):
            raise QMPTimeout("QMP operation deadline expired")
        pending: _PendingResponse | None = None
        command_id: int | None = None
        try:
            with self._state_lock:
                self._ensure_ready()
                command_id = self._allocate_command_id()
            encoded = self._encode_command(
                selected,
                command_id,
                materialized,
            )
            pending = _PendingResponse(
                command=selected,
                ready=threading.Event(),
            )
            with self._state_lock:
                self._ensure_ready()
                self._pending[command_id] = pending
            try:
                self._send_bytes(encoded, deadline)
            except QMPError as exc:
                with self._state_lock:
                    self._pending.pop(command_id, None)
                self._fail_session(exc)
                raise
            except Exception as exc:
                # ``_send_bytes`` normally translates transport failures into
                # QMPError.  Retain this total cleanup fence for an
                # unexpected send/runtime failure so the pending command is
                # removed and the session cannot be mistaken for a live QMP
                # authority.  The original exception is retained as cause.
                with self._state_lock:
                    self._pending.pop(command_id, None)
                error = QMPProtocolError("QMP command send failed")
                self._fail_session(error)
                raise error from exc
        finally:
            self._send_lock.release()

        assert pending is not None
        assert command_id is not None
        remaining = deadline - time.monotonic()
        if (
            remaining <= 0.0
            and not pending.ready.is_set()
        ) or not pending.ready.wait(max(0.0, remaining)):
            with self._state_lock:
                self._pending.pop(command_id, None)
            error = QMPTimeout("QMP operation deadline expired")
            self._fail_session(error)
            raise error
        if pending.error is not None:
            raise pending.error
        response = pending.response
        if response is None:
            error = QMPProtocolError("QMP response dispatch was incomplete")
            self._fail_session(error)
            raise error
        if response.error_class is not None:
            raise self._command_error(selected, response)
        return QMPResponse(
            command=selected,
            command_id=response.command_id,
            value=response.value,
        )

    def next_event(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPEvent:
        """Return the next asynchronous event under one absolute deadline."""

        timeout = (
            self._timeout_seconds
            if timeout_seconds is None
            else _bounded_timeout(timeout_seconds)
        )
        deadline = _deadline_after(timeout)
        with self._event_ready:
            while not self._events:
                if self._fatal_error is not None:
                    raise self._fatal_error
                if self._state not in {"connecting", "ready"}:
                    raise QMPStateError("QMP event stream is unavailable")
                self._event_ready.wait(timeout=_remaining(deadline))
            return self._events.popleft()

    def drain_events(self) -> tuple[QMPEvent, ...]:
        """Atomically return and clear the current asynchronous event queue."""

        with self._event_ready:
            events = tuple(self._events)
            self._events.clear()
            return events

    def query_status(self, *, timeout_seconds: float | None = None) -> QMPResponse:
        return self.execute("query-status", timeout_seconds=timeout_seconds)

    def query_name(self, *, timeout_seconds: float | None = None) -> QMPResponse:
        return self.execute("query-name", timeout_seconds=timeout_seconds)

    def query_uuid(self, *, timeout_seconds: float | None = None) -> QMPResponse:
        return self.execute("query-uuid", timeout_seconds=timeout_seconds)

    def query_block(self, *, timeout_seconds: float | None = None) -> QMPResponse:
        return self.execute("query-block", timeout_seconds=timeout_seconds)

    def query_fdsets(self, *, timeout_seconds: float | None = None) -> QMPResponse:
        """Return QEMU's exact inherited storage fdset inventory."""

        return self.execute("query-fdsets", timeout_seconds=timeout_seconds)

    def query_named_block_nodes(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPResponse:
        """Return the named block graph without caller-supplied arguments."""

        return self.execute(
            "query-named-block-nodes",
            timeout_seconds=timeout_seconds,
        )

    def query_blockstats(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPResponse:
        """Return recursive blockstats graph edges without arguments."""

        return self.execute("query-blockstats", timeout_seconds=timeout_seconds)

    def query_cpus_fast(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPResponse:
        return self.execute("query-cpus-fast", timeout_seconds=timeout_seconds)

    def system_powerdown(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPResponse:
        return self.execute("system_powerdown", timeout_seconds=timeout_seconds)

    def quit(self, *, timeout_seconds: float | None = None) -> QMPResponse:
        return self.execute("quit", timeout_seconds=timeout_seconds)

    def execute_block_graph(
        self,
        command: str,
        arguments: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> QMPResponse:
        if command not in BLOCK_GRAPH_QMP_COMMANDS:
            raise QMPValidationError(
                "QMP command is outside the block-graph allowlist"
            )
        return self.execute(
            command,
            arguments,
            timeout_seconds=timeout_seconds,
        )

    def _close_socket(self) -> None:
        connection = self._socket
        if connection is None:
            return
        self._socket = None
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    def close(self) -> None:
        """Stop the reader, fail pending calls, and close the session."""

        error = QMPStateError("QMP client was closed")
        with self._event_ready:
            if self._state == "closed":
                return
            self._state = "closed"
            self._stop_reader.set()
            pending = tuple(self._pending.values())
            self._pending.clear()
            self._event_ready.notify_all()
        for waiter in pending:
            waiter.error = error
            waiter.ready.set()
        self._close_socket()
        reader = self._reader
        if (
            reader is not None
            and reader is not threading.current_thread()
            and reader.is_alive()
        ):
            reader.join(timeout=2.0)

    def __enter__(self) -> "QMPClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "ALLOWED_QMP_COMMANDS",
    "BLOCK_GRAPH_QMP_COMMANDS",
    "CORE_QMP_COMMANDS",
    "DEFAULT_QMP_MAX_DEPTH",
    "DEFAULT_QMP_MAX_EVENTS",
    "DEFAULT_QMP_MAX_RESPONSES",
    "DEFAULT_QMP_MAX_ITEMS",
    "DEFAULT_QMP_MAX_MESSAGE_BYTES",
    "DEFAULT_QMP_MAX_STRING",
    "DEFAULT_QMP_TIMEOUT_SECONDS",
    "QMPClient",
    "QMPCommandError",
    "QMPError",
    "QMPEvent",
    "QMPGreeting",
    "QMPHandshakeError",
    "QMPProtocolError",
    "QMPResponse",
    "QMPStateError",
    "QMPTimeout",
    "QMPUnavailable",
    "QMPValidationError",
    "QMPVersion",
]
