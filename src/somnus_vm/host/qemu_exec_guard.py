"""Durability handoff guard for a future daemon-owned QEMU child.

The daemon cannot durably journal a child PID before that child exists, while
an immediately executable long-lived child could become an unrecorded orphan
if the daemon dies in that interval.  This internal launcher closes that
window:

1. it arms ``PR_SET_PDEATHSIG(SIGKILL)``;
2. it reports a bounded readiness token over one inherited socket;
3. it waits while the daemon durably records the PID/start-time checkpoint;
4. only an exact release token disarms parent death and ``execve`` replaces
   the guard with the already validated target at the same PID/start time.

EOF, malformed input, or parent death before release exits without executing
the target.  This module is not a public command and does not establish QEMU
identity, QMP readiness, or VM truth by itself.
"""

from __future__ import annotations

import os
import socket
import stat
import sys
from pathlib import Path
from typing import Final, Sequence

from somnus_vm.host.exec_guard import (
    _arm_parent_death,
    _clear_parent_death,
)


READY: Final[bytes] = b"SOMNUS_QEMU_EXEC_GUARD_READY_V1\n"
RELEASE: Final[bytes] = b"SOMNUS_QEMU_EXEC_GUARD_RELEASE_V1\n"
_MAX_CONTROL_BYTES: Final[int] = 128
_MAX_ARGUMENTS: Final[int] = 4_096
_MAX_ARGUMENT_BYTES: Final[int] = 64 * 1024
_MAX_COMMAND_BYTES: Final[int] = 1024 * 1024
_MAX_EXECUTABLE_BYTES: Final[int] = 512 * 1024 * 1024
_FAILURE_EXIT: Final[int] = 125


def _strict_positive_int(value: str, label: str, maximum: int) -> int:
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


def _validated_executable_fd(value: str, control_fd: int) -> int:
    descriptor = _strict_positive_int(
        value,
        "executable FD",
        2**31 - 1,
    )
    if descriptor == control_fd:
        raise RuntimeError("control and executable FDs must be distinct")
    try:
        details = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeError("guarded executable FD is unavailable") from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_uid != 0
        or details.st_size < 1
        or details.st_size > _MAX_EXECUTABLE_BYTES
        or details.st_mode & 0o022
        or details.st_mode & 0o111 == 0
    ):
        raise RuntimeError("guarded executable permissions are unsafe")
    return descriptor


def _private_home(value: str) -> Path:
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("guard runtime home is invalid") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or path.resolve(strict=True) != path
    ):
        raise RuntimeError("guard runtime home must be canonical and absolute")
    details = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise RuntimeError("guard runtime home is not private")
    return path


def _target_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": os.fspath(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "TMPDIR": os.fspath(home),
        "TZ": "UTC",
        "XDG_RUNTIME_DIR": os.fspath(home),
    }


def _validated_target_argv(arguments: tuple[str, ...]) -> tuple[str, ...]:
    if not arguments or len(arguments) > _MAX_ARGUMENTS:
        raise RuntimeError("guarded argv count is invalid")
    total = 0
    for argument in arguments:
        if (
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            or any(
                (ord(character) < 32 and character != "\t")
                or ord(character) == 127
                for character in argument
            )
        ):
            raise RuntimeError("guarded argv contains an invalid argument")
        encoded = os.fsencode(argument)
        if len(encoded) > _MAX_ARGUMENT_BYTES:
            raise RuntimeError("guarded argv argument is too large")
        total += len(encoded) + 1
        if total > _MAX_COMMAND_BYTES:
            raise RuntimeError("guarded argv exceeds the supported bound")
    return arguments


def _receive_release(control: socket.socket) -> None:
    payload = bytearray()
    while len(payload) <= _MAX_CONTROL_BYTES:
        block = control.recv(min(64, _MAX_CONTROL_BYTES + 1 - len(payload)))
        if not block:
            raise RuntimeError("guard owner closed before durable release")
        payload.extend(block)
        if b"\n" in block:
            break
    if bytes(payload) != RELEASE:
        raise RuntimeError("guard durable-release token is invalid")


def main(arguments: Sequence[str] | None = None) -> int:
    argv = tuple(sys.argv[1:] if arguments is None else arguments)
    if len(argv) < 5:
        raise RuntimeError(
            "parent PID, control FD, runtime home, executable FD, and target argv are required"
        )
    expected_parent_pid = _strict_positive_int(
        argv[0],
        "parent PID",
        2**31 - 1,
    )
    control_fd = _strict_positive_int(
        argv[1],
        "control FD",
        2**31 - 1,
    )
    runtime_home = _private_home(argv[2])
    executable_fd = _validated_executable_fd(argv[3], control_fd)
    target_argv = _validated_target_argv(tuple(argv[4:]))

    try:
        control = socket.socket(fileno=control_fd)
    except OSError as exc:
        raise RuntimeError("guard control FD is not a socket") from exc
    with control:
        if control.family != socket.AF_UNIX or control.type != socket.SOCK_STREAM:
            raise RuntimeError("guard control channel must be an AF_UNIX stream")
        _arm_parent_death(expected_parent_pid)
        control.sendall(READY)
        _receive_release(control)
        _clear_parent_death(expected_parent_pid)
    os.umask(0o077)
    os.set_inheritable(executable_fd, False)
    os.execve(
        executable_fd,
        target_argv,
        _target_environment(runtime_home),
    )
    raise AssertionError("os.execve unexpectedly returned")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"somnus QEMU exec guard failed: {type(exc).__name__}\n"
        os.write(2, message.encode("ascii", errors="replace")[:512])
        raise SystemExit(_FAILURE_EXIT)


__all__ = ["main"]
