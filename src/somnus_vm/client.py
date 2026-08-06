"""Synchronous authenticated client for the local Somnus control daemon.

Source: PLAN.md P2 local client boundary
Integrated: 2026-08-05
Purpose: Sends one canonical request over one private Unix connection and
    returns exactly one identity-matched success or closed error envelope.
IO: One AF_UNIX connection per request.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterable
from pathlib import Path

from somnus_protocol import (
    ControlRequest,
    ControlResponse,
    ErrorEnvelope,
    ProtocolValidationError,
)
from somnus_protocol._validation import (
    PUBLIC_WIRE_MAX_DEPTH,
    PUBLIC_WIRE_MAX_ITEMS,
    PUBLIC_WIRE_MAX_STRING,
    decode_json_object,
)

from .daemon import ResponseEnvelope
from .transport import (
    DEFAULT_MAX_FRAME_BYTES,
    FrameProtocolError,
    PeerAuthenticationError,
    TransportError,
    TransportTimeout,
    TransportUnavailable,
    deadline_after,
    get_peer_credentials,
    normalize_socket_path,
    normalize_uid_allowlist,
    receive_single_frame,
    send_single_frame,
    validate_frame_limit,
    validate_timeout,
)


def _response_matches(
    request: ControlRequest,
    response: ResponseEnvelope,
) -> bool:
    return (
        response.schema_version == request.schema_version
        and response.protocol_version == request.protocol_version
        and response.request_id == request.request_id
        and response.correlation_id == request.correlation_id
        and response.vm_id == request.vm_id
        and response.boot_id == request.boot_id
        and response.generation == request.generation
    )


def _decode_response(payload: bytes) -> ResponseEnvelope:
    try:
        row = decode_json_object(
            payload,
            "control response",
            max_bytes=DEFAULT_MAX_FRAME_BYTES,
            max_depth=PUBLIC_WIRE_MAX_DEPTH,
            max_items=PUBLIC_WIRE_MAX_ITEMS,
            max_string=PUBLIC_WIRE_MAX_STRING,
        )
        ok = row.get("ok")
        if not isinstance(ok, bool):
            raise ProtocolValidationError(
                "control response ok discriminator must be boolean"
            )
        if ok:
            return ControlResponse.from_dict(row)
        return ErrorEnvelope.from_dict(row)
    except (
        ProtocolValidationError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
    ) as exc:
        raise FrameProtocolError(
            "daemon returned an invalid control envelope"
        ) from exc


class UnixControlClient:
    """One-request-per-connection synchronous local client."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 30.0,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        allowed_server_uids: Iterable[int] | None = None,
    ) -> None:
        self._socket_path = normalize_socket_path(socket_path)
        self._timeout_seconds = validate_timeout(timeout_seconds)
        self._max_frame_bytes = validate_frame_limit(max_frame_bytes)
        self._allowed_server_uids = normalize_uid_allowlist(
            allowed_server_uids
        )

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def request(
        self,
        request: ControlRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> ResponseEnvelope:
        """Send one request under one absolute connect/send/receive deadline."""

        if not isinstance(request, ControlRequest):
            raise TransportError("request must be a ControlRequest")
        timeout = (
            self._timeout_seconds
            if timeout_seconds is None
            else validate_timeout(timeout_seconds)
        )
        deadline = deadline_after(timeout)
        encoded = request.to_json().encode("utf-8")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TransportTimeout("transport deadline expired")
            connection.settimeout(remaining)
            try:
                connection.connect(os.fspath(self._socket_path))
            except TimeoutError as exc:
                raise TransportTimeout("transport deadline expired") from exc
            except OSError as exc:
                raise TransportUnavailable(
                    "local control daemon is unavailable"
                ) from exc

            server = get_peer_credentials(connection)
            if server.uid not in self._allowed_server_uids:
                raise PeerAuthenticationError(
                    "kernel-authenticated daemon uid is not authorized"
                )
            send_single_frame(
                connection,
                encoded,
                deadline=deadline,
                max_frame_bytes=self._max_frame_bytes,
            )
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError as exc:
                raise FrameProtocolError(
                    "request write-side half-close failed"
                ) from exc
            payload = receive_single_frame(
                connection,
                deadline=deadline,
                max_frame_bytes=self._max_frame_bytes,
                require_eof=True,
            )
        finally:
            connection.close()

        response = _decode_response(payload)
        if not _response_matches(request, response):
            raise FrameProtocolError(
                "daemon response identity does not match the request"
            )
        return response


__all__ = ["UnixControlClient"]
