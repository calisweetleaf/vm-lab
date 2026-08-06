"""Crash-surviving redaction pump for supervised QEMU stdout and stderr.

The mutation daemon launches this internal guardian before QEMU.  Parent death
remains armed until a durable guardian checkpoint is acknowledged over an
inherited control socket.  After release, the guardian survives daemon death,
drains both QEMU pipes, redacts before persistence, and exits only after both
writers close or an explicit termination signal arrives.

It owns no VM state, QMP, disk, or lifecycle authority.
"""

from __future__ import annotations

import fcntl
import os
import selectors
import signal
import socket
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from types import FrameType
from typing import Final, Sequence

from somnus_vm.host.exec_guard import (
    _arm_parent_death,
    _clear_parent_death,
)
from somnus_vm.host.qemu_logs import (
    RedactedRotatingLog,
    prepare_private_directory,
)


READY: Final[bytes] = b"SOMNUS_QEMU_LOG_GUARD_READY_V1\n"
RELEASE: Final[bytes] = b"SOMNUS_QEMU_LOG_GUARD_RELEASE_V1\n"
RELEASED: Final[bytes] = b"SOMNUS_QEMU_LOG_GUARD_RELEASED_V1\n"
_MAX_CONTROL_BYTES: Final[int] = 128
_FAILURE_EXIT: Final[int] = 124
_STOP = False


def _stop(_signal: int, _frame: FrameType | None) -> None:
    global _STOP
    _STOP = True


def _positive_int(value: str, label: str, maximum: int) -> int:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
    ):
        raise RuntimeError(f"{label} is invalid")
    parsed = int(value, 10)
    if parsed < 1 or parsed > maximum:
        raise RuntimeError(f"{label} is invalid")
    return parsed


def _validated_pipe_fd(value: str, label: str) -> int:
    descriptor = _positive_int(value, label, 2**31 - 1)
    try:
        details = os.fstat(descriptor)
        access_mode = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    if not stat.S_ISFIFO(details.st_mode) or access_mode != os.O_RDONLY:
        raise RuntimeError(f"{label} must be the read-only end of a pipe")
    return descriptor


def _receive_release(control: socket.socket) -> None:
    payload = bytearray()
    while len(payload) <= _MAX_CONTROL_BYTES:
        block = control.recv(min(64, _MAX_CONTROL_BYTES + 1 - len(payload)))
        if not block:
            raise RuntimeError("log owner closed before durable release")
        payload.extend(block)
        if b"\n" in block:
            break
    if bytes(payload) != RELEASE:
        raise RuntimeError("log durable-release token is invalid")


def _bounded_log_path(value: str, root: Path, label: str) -> Path:
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} is invalid") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or path.parent != root
        or path.resolve(strict=False) != path
        or len(os.fsencode(path)) > 4_096
    ):
        raise RuntimeError(f"{label} must be an immediate canonical log child")
    return path


def _drain(
    serial_fd: int,
    qemu_fd: int,
    serial_log: RedactedRotatingLog,
    qemu_log: RedactedRotatingLog,
) -> None:
    selector = selectors.DefaultSelector()
    writers = {
        serial_fd: serial_log,
        qemu_fd: qemu_log,
    }
    failure: BaseException | None = None
    try:
        for descriptor in writers:
            os.set_blocking(descriptor, False)
            selector.register(descriptor, selectors.EVENT_READ)
        while selector.get_map() and not _STOP:
            for key, _ in selector.select(0.1):
                descriptor = int(key.fileobj)
                try:
                    payload = os.read(descriptor, 64 * 1024)
                except BlockingIOError:
                    continue
                if not payload:
                    selector.unregister(descriptor)
                    os.close(descriptor)
                    writers.pop(descriptor).close()
                    continue
                writers[descriptor].feed(payload)
    except BaseException as exc:
        failure = exc
    finally:
        try:
            selector.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
        for descriptor, writer in tuple(writers.items()):
            try:
                os.close(descriptor)
            except OSError as exc:
                if failure is None:
                    failure = exc
            try:
                writer.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
    if failure is not None:
        raise failure


def main(arguments: Sequence[str] | None = None) -> int:
    global _STOP
    _STOP = False
    argv = tuple(sys.argv[1:] if arguments is None else arguments)
    if len(argv) != 9:
        raise RuntimeError(
            "parent PID, control FD, two pipe FDs, log root, two log paths, "
            "max bytes, and backup count are required"
        )
    parent_pid = _positive_int(argv[0], "parent PID", 2**31 - 1)
    control_fd = _positive_int(argv[1], "control FD", 2**31 - 1)
    serial_fd = _validated_pipe_fd(argv[2], "serial pipe FD")
    qemu_fd = _validated_pipe_fd(argv[3], "QEMU pipe FD")
    if len({control_fd, serial_fd, qemu_fd}) != 3:
        raise RuntimeError("guardian descriptors must be distinct")
    serial_identity = os.fstat(serial_fd)
    qemu_identity = os.fstat(qemu_fd)
    if (
        serial_identity.st_dev,
        serial_identity.st_ino,
    ) == (
        qemu_identity.st_dev,
        qemu_identity.st_ino,
    ):
        raise RuntimeError("guardian streams must use distinct pipes")
    log_root = prepare_private_directory(argv[4], "log root")
    serial_path = _bounded_log_path(argv[5], log_root, "serial log")
    qemu_path = _bounded_log_path(argv[6], log_root, "QEMU log")
    if serial_path == qemu_path:
        raise RuntimeError("serial and QEMU logs must be distinct")
    max_bytes = _positive_int(argv[7], "max log bytes", 1024**3)
    backup_count = _positive_int(argv[8], "backup count", 32)
    try:
        control = socket.socket(fileno=control_fd)
    except OSError as exc:
        raise RuntimeError("guardian control FD is not a socket") from exc
    with ExitStack() as logs:
        with control:
            if (
                control.family != socket.AF_UNIX
                or control.type != socket.SOCK_STREAM
            ):
                raise RuntimeError(
                    "guardian control channel must be AF_UNIX stream"
                )
            _arm_parent_death(parent_pid)
            serial_log = logs.enter_context(
                RedactedRotatingLog(
                    serial_path,
                    max_bytes=max_bytes,
                    backup_count=backup_count,
                )
            )
            qemu_log = logs.enter_context(
                RedactedRotatingLog(
                    qemu_path,
                    max_bytes=max_bytes,
                    backup_count=backup_count,
                )
            )
            # READY means both private files have been opened, validated, and
            # durably materialized while parent death is still armed.
            control.sendall(READY)
            _receive_release(control)
            _clear_parent_death(parent_pid)
            # The acknowledgement is emitted only after the death signal has
            # been cleared. Once RELEASE was valid, failure to deliver the
            # observation cannot revoke it or strand QEMU without a drainer.
            try:
                control.sendall(RELEASED)
            except OSError:
                pass
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        _drain(serial_fd, qemu_fd, serial_log, qemu_log)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"somnus QEMU log guard failed: {type(exc).__name__}\n"
        os.write(2, message.encode("ascii", errors="replace")[:512])
        raise SystemExit(_FAILURE_EXIT)


__all__ = ["READY", "RELEASE", "RELEASED", "main"]
