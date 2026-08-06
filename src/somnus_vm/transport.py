"""Bounded Unix-domain framing and peer-authentication primitives.

Source: PLAN.md P2 local control-plane transport boundary
Integrated: 2026-08-05
Purpose: Carries exactly one canonical control envelope per local connection
    without parsing unbounded bytes or treating a socket pathname as daemon
    identity.
IO: Real AF_UNIX sockets and Linux ``SO_PEERCRED`` only.

The transport deliberately requires the sender to half-close its write side.
That gives the receiver an unambiguous end-of-request marker and lets it reject
trailing or duplicate frames before dispatch.  Framing failures never include
caller bytes in their public exception text.
"""

from __future__ import annotations

import math
import os
import socket
import struct
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from somnus_protocol._validation import PUBLIC_WIRE_MAX_BYTES


DEFAULT_MAX_FRAME_BYTES: Final[int] = PUBLIC_WIRE_MAX_BYTES
FRAME_HEADER_BYTES: Final[int] = 4
UNIX_PATH_MAX_BYTES: Final[int] = 107
_HEADER: Final[struct.Struct] = struct.Struct("!I")
_PEER_CREDENTIALS: Final[struct.Struct] = struct.Struct("3i")


class TransportError(RuntimeError):
    """Base class for closed, payload-free local transport failures."""


class TransportTimeout(TransportError):
    """Raised when an absolute transport deadline expires."""


class TransportUnavailable(TransportError):
    """Raised when the configured local daemon endpoint cannot be reached."""


class FrameProtocolError(TransportError):
    """Raised when a connection does not contain exactly one valid frame."""


class FrameTooLarge(FrameProtocolError):
    """Raised from the length prefix before an oversized body is read."""


class PeerAuthenticationError(TransportError):
    """Raised when kernel peer identity is unavailable or unauthorized."""


class DaemonOwnershipError(TransportError):
    """Raised when exclusive daemon/socket ownership cannot be established."""


@dataclass(frozen=True, slots=True)
class PeerCredentials:
    """Kernel-authenticated identity for one connected Unix-domain peer."""

    pid: int
    uid: int
    gid: int

    def __post_init__(self) -> None:
        for label, value in (
            ("pid", self.pid),
            ("uid", self.uid),
            ("gid", self.gid),
        ):
            minimum = 1 if label == "pid" else 0
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
            ):
                raise PeerAuthenticationError(
                    f"peer {label} is outside the kernel credential domain"
                )


def validate_timeout(value: object, label: str = "timeout_seconds") -> float:
    """Return one finite, positive timeout without truthy coercion."""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TransportError(f"{label} must be a finite positive number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0 or normalized > 86_400.0:
        raise TransportError(
            f"{label} must be greater than zero and at most 86400 seconds"
        )
    return normalized


def validate_frame_limit(value: object) -> int:
    """Return a positive frame limit no larger than the canonical wire cap."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise TransportError("max_frame_bytes must be an integer")
    if value < 1 or value > PUBLIC_WIRE_MAX_BYTES:
        raise TransportError(
            "max_frame_bytes exceeds the canonical control-envelope limit"
        )
    return value


def normalize_uid_allowlist(
    values: Iterable[int] | None,
    *,
    default_uid: int | None = None,
) -> frozenset[int]:
    """Validate an exact kernel-UID allowlist.

    ``None`` selects exactly ``default_uid`` (or the current effective UID).
    An explicit iterable remains exact, including an intentionally empty set.
    """

    if values is None:
        selected = os.geteuid() if default_uid is None else default_uid
        values = (selected,)
    if isinstance(values, (str, bytes)):
        raise PeerAuthenticationError(
            "uid allowlist must be an integer iterable"
        )
    try:
        materialized = tuple(values)
    except TypeError as exc:
        raise PeerAuthenticationError(
            "uid allowlist must be an integer iterable"
        ) from exc
    for uid in materialized:
        if (
            not isinstance(uid, int)
            or isinstance(uid, bool)
            or uid < 0
            or uid > 2**32 - 1
        ):
            raise PeerAuthenticationError(
                "uid allowlist contains an invalid kernel uid"
            )
    return frozenset(materialized)


def normalize_socket_path(value: str | os.PathLike[str]) -> Path:
    """Validate one absolute, lexical AF_UNIX pathname.

    Resolution is intentionally not performed here: resolving an absent socket
    can hide a later symlink substitution.  The daemon separately validates and
    owns its private parent directory before binding.
    """

    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise TransportError("control socket path is invalid") from exc
    text = os.fspath(path)
    if not text or "\x00" in text:
        raise TransportError("control socket path is invalid")
    pure = PurePosixPath(text)
    if not pure.is_absolute() or pure == PurePosixPath("/"):
        raise TransportError("control socket path must be absolute and non-root")
    components = text.split("/")[1:]
    if any(component in {"", ".", ".."} for component in components):
        raise TransportError(
            "control socket path contains an ambiguous path component"
        )
    try:
        encoded = os.fsencode(text)
    except UnicodeEncodeError as exc:
        raise TransportError("control socket path is not filesystem encodable") from exc
    if len(encoded) > UNIX_PATH_MAX_BYTES:
        raise TransportError("control socket path exceeds the AF_UNIX byte limit")
    return path


def deadline_after(timeout_seconds: object) -> float:
    """Create one monotonic absolute deadline."""

    return time.monotonic() + validate_timeout(timeout_seconds)


def _remaining(deadline: float) -> float:
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
        raise TransportError("deadline must be a monotonic timestamp")
    remaining = float(deadline) - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0.0:
        raise TransportTimeout("transport deadline expired")
    return remaining


def _set_deadline(sock: socket.socket, deadline: float) -> None:
    sock.settimeout(_remaining(deadline))


def _receive_exact(
    sock: socket.socket,
    size: int,
    *,
    deadline: float,
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        _set_deadline(sock, deadline)
        try:
            chunk = sock.recv(remaining)
        except TimeoutError as exc:
            raise TransportTimeout("transport deadline expired") from exc
        except OSError as exc:
            raise FrameProtocolError("frame receive failed") from exc
        if not chunk:
            raise FrameProtocolError("frame ended before its declared length")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_single_frame(
    sock: socket.socket,
    payload: bytes,
    *,
    deadline: float,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> None:
    """Send exactly one bounded frame.

    The caller must invoke ``shutdown(SHUT_WR)`` after this function.  Keeping
    the half-close explicit prevents a helper from silently closing a socket
    that still owns the response read side.
    """

    limit = validate_frame_limit(max_frame_bytes)
    if not isinstance(payload, bytes):
        raise FrameProtocolError("frame payload must be bytes")
    size = len(payload)
    if size == 0:
        raise FrameProtocolError("frame payload cannot be empty")
    if size > limit:
        raise FrameTooLarge("frame length exceeds the configured byte limit")
    _set_deadline(sock, deadline)
    try:
        sock.sendall(_HEADER.pack(size) + payload)
    except TimeoutError as exc:
        raise TransportTimeout("transport deadline expired") from exc
    except OSError as exc:
        raise FrameProtocolError("frame send failed") from exc


def receive_single_frame(
    sock: socket.socket,
    *,
    deadline: float,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    require_eof: bool = True,
) -> bytes:
    """Receive one frame and, by default, require write-side EOF afterward."""

    limit = validate_frame_limit(max_frame_bytes)
    header = _receive_exact(sock, FRAME_HEADER_BYTES, deadline=deadline)
    (size,) = _HEADER.unpack(header)
    if size == 0:
        raise FrameProtocolError("frame payload cannot be empty")
    if size > limit:
        # The declared size is rejected before any caller-controlled body byte
        # is received or passed to JSON decoding.
        raise FrameTooLarge("frame length exceeds the configured byte limit")
    payload = _receive_exact(sock, size, deadline=deadline)
    if require_eof:
        _set_deadline(sock, deadline)
        try:
            trailing = sock.recv(1)
        except TimeoutError as exc:
            raise TransportTimeout("transport deadline expired") from exc
        except OSError as exc:
            raise FrameProtocolError("frame boundary check failed") from exc
        if trailing:
            raise FrameProtocolError(
                "connection contains trailing or duplicate frame data"
            )
    return payload


def get_peer_credentials(sock: socket.socket) -> PeerCredentials:
    """Read Linux kernel-authenticated ``pid, uid, gid`` for a peer."""

    if not hasattr(socket, "SO_PEERCRED"):
        raise PeerAuthenticationError(
            "kernel Unix peer credentials are unavailable"
        )
    try:
        raw = sock.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            _PEER_CREDENTIALS.size,
        )
    except OSError as exc:
        raise PeerAuthenticationError(
            "kernel Unix peer credentials are unavailable"
        ) from exc
    if len(raw) != _PEER_CREDENTIALS.size:
        raise PeerAuthenticationError(
            "kernel Unix peer credentials have an invalid size"
        )
    return PeerCredentials(*_PEER_CREDENTIALS.unpack(raw))


__all__ = [
    "DEFAULT_MAX_FRAME_BYTES",
    "DaemonOwnershipError",
    "FRAME_HEADER_BYTES",
    "FrameProtocolError",
    "FrameTooLarge",
    "PeerAuthenticationError",
    "PeerCredentials",
    "TransportError",
    "TransportTimeout",
    "TransportUnavailable",
    "deadline_after",
    "get_peer_credentials",
    "normalize_uid_allowlist",
    "normalize_socket_path",
    "receive_single_frame",
    "send_single_frame",
    "validate_frame_limit",
    "validate_timeout",
]
