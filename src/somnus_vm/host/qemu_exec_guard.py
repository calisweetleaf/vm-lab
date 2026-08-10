"""Descriptor-bound durability handoff guard for a daemon-owned QEMU child.

The daemon cannot durably journal a child PID before that child exists, while
an immediately executable long-lived child could become an unrecorded orphan
if the daemon dies in that interval.  This internal launcher closes that
window without reopening a storage pathname:

1. it receives the pinned executable plus the P3-owned overlay, base, and
   owner-marker descriptors;
2. it validates every descriptor against explicit expected facts and hashes
   the immutable base and owner marker before reporting readiness;
3. it arms ``PR_SET_PDEATHSIG(SIGKILL)`` and waits for the daemon to durably
   record the PID/start-time checkpoint;
4. immediately before ``execve`` it repeats the stable-metadata and immutable
   content fences, then makes *only* overlay and base inheritable by QEMU.

EOF, malformed input, parent death, descriptor drift, or content drift before
release exits without executing the target.  The positional protocol is
intentionally explicit and is consumed only by ``QemuRuntimeOwner``:

``parent_pid control_fd runtime_home executable_fd`` followed by the three
ordered descriptor specifications ``overlay``, ``base``, and ``owner-marker``.
Each specification is ``role fd dev ino uid gid mode nlink size blocks mtime_ns
ctime_ns access sha256``.  ``overlay`` has access ``rw`` and sha256 ``-``;
``base`` and ``owner-marker`` have access ``r`` and a complete SHA-256.  A
literal ``--`` terminates the specifications and begins the target argv.

This module is not a public command and does not establish QEMU identity, QMP
readiness, or VM truth by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import socket
import sys
from pathlib import Path
from typing import Final, Sequence

import fcntl
import stat

from somnus_vm.host.exec_guard import _arm_parent_death, _clear_parent_death


READY: Final[bytes] = b"SOMNUS_QEMU_EXEC_GUARD_READY_V1\n"
RELEASE: Final[bytes] = b"SOMNUS_QEMU_EXEC_GUARD_RELEASE_V1\n"
_MAX_CONTROL_BYTES: Final[int] = 128
_MAX_ARGUMENTS: Final[int] = 4_096
_MAX_ARGUMENT_BYTES: Final[int] = 64 * 1024
_MAX_COMMAND_BYTES: Final[int] = 1024 * 1024
_MAX_EXECUTABLE_BYTES: Final[int] = 512 * 1024 * 1024
_MAX_STORAGE_BYTES: Final[int] = 2**63 - 1
_HASH_CHUNK_BYTES: Final[int] = 1024 * 1024
_DESCRIPTOR_FIELDS: Final[int] = 14
_FAILURE_EXIT: Final[int] = 125


@dataclass(frozen=True)
class _DescriptorFacts:
    """Complete, immutable descriptor facts supplied by the daemon owner."""

    role: str
    fd: int
    dev: int
    ino: int
    uid: int
    gid: int
    mode: int
    nlink: int
    size: int
    blocks: int
    mtime_ns: int
    ctime_ns: int
    access_mode: int
    content_sha256: str | None


@dataclass(frozen=True)
class _ValidatedDescriptor:
    """A descriptor with the READY-time facts used for the release fence."""

    expected: _DescriptorFacts
    observed: _DescriptorFacts


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


def _strict_nonnegative_int(value: str, label: str, maximum: int) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise RuntimeError(f"{label} is invalid")
    parsed = int(value, 10)
    if parsed > maximum:
        raise RuntimeError(f"{label} is invalid")
    return parsed


def _strict_mode(value: str, label: str) -> int:
    if (
        not isinstance(value, str)
        or len(value) != 4
        or not value.isascii()
        or any(character not in "01234567" for character in value)
    ):
        raise RuntimeError(f"{label} is invalid")
    return int(value, 8)


def _strict_sha256(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeError(f"{label} is invalid")
    return value


def _validated_executable_fd(value: str, control_fd: int) -> int:
    descriptor = _strict_positive_int(value, "executable FD", 2**31 - 1)
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
    if not path.is_absolute() or path == Path("/") or path.resolve(strict=True) != path:
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


def _parse_access_mode(value: str, role: str) -> int:
    expected = {"overlay": "rw", "base": "r", "owner-marker": "r"}[role]
    if value != expected:
        raise RuntimeError(f"{role} access mode is invalid")
    return os.O_RDWR if value == "rw" else os.O_RDONLY


def _parse_descriptor(
    values: tuple[str, ...],
    *,
    required_role: str,
) -> _DescriptorFacts:
    if len(values) != _DESCRIPTOR_FIELDS:
        raise RuntimeError("storage descriptor specification is truncated")
    role = values[0]
    if role != required_role:
        raise RuntimeError("storage descriptor roles are not canonical")
    content_sha256: str | None
    if role == "overlay":
        if values[13] != "-":
            raise RuntimeError("overlay must not declare immutable content")
        content_sha256 = None
    else:
        content_sha256 = _strict_sha256(values[13], f"{role} SHA-256")
    facts = _DescriptorFacts(
        role=role,
        fd=_strict_positive_int(values[1], f"{role} FD", 2**31 - 1),
        dev=_strict_nonnegative_int(values[2], f"{role} device", 2**63 - 1),
        ino=_strict_positive_int(values[3], f"{role} inode", 2**63 - 1),
        uid=_strict_nonnegative_int(values[4], f"{role} UID", 2**31 - 1),
        gid=_strict_nonnegative_int(values[5], f"{role} GID", 2**31 - 1),
        mode=_strict_mode(values[6], f"{role} mode"),
        nlink=_strict_positive_int(values[7], f"{role} link count", 2**31 - 1),
        size=_strict_positive_int(values[8], f"{role} size", _MAX_STORAGE_BYTES),
        blocks=_strict_nonnegative_int(values[9], f"{role} blocks", _MAX_STORAGE_BYTES),
        mtime_ns=_strict_nonnegative_int(values[10], f"{role} mtime", _MAX_STORAGE_BYTES),
        ctime_ns=_strict_nonnegative_int(values[11], f"{role} ctime", _MAX_STORAGE_BYTES),
        access_mode=_parse_access_mode(values[12], role),
        content_sha256=content_sha256,
    )
    if facts.mode & ~0o777:
        raise RuntimeError(f"{role} mode is invalid")
    return facts


def _parse_storage_descriptors(
    values: tuple[str, ...],
    *,
    control_fd: int,
    executable_fd: int,
) -> tuple[_DescriptorFacts, _DescriptorFacts, _DescriptorFacts, tuple[str, ...]]:
    expected_prefix = _DESCRIPTOR_FIELDS * 3
    if len(values) <= expected_prefix or values[expected_prefix] != "--":
        raise RuntimeError("guard storage protocol is incomplete")
    overlay = _parse_descriptor(values[:_DESCRIPTOR_FIELDS], required_role="overlay")
    base = _parse_descriptor(
        values[_DESCRIPTOR_FIELDS : _DESCRIPTOR_FIELDS * 2], required_role="base"
    )
    marker = _parse_descriptor(
        values[_DESCRIPTOR_FIELDS * 2 : expected_prefix], required_role="owner-marker"
    )
    descriptors = (overlay, base, marker)
    numbers = (control_fd, executable_fd, *(item.fd for item in descriptors))
    if len(set(numbers)) != len(numbers):
        raise RuntimeError("guard descriptors must be distinct")
    return overlay, base, marker, values[expected_prefix + 1 :]


def _descriptor_access_mode(fd: int, role: str) -> int:
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError as exc:
        raise RuntimeError(f"{role} FD flags are unavailable") from exc
    return flags & os.O_ACCMODE


def _observed_facts(expected: _DescriptorFacts) -> _DescriptorFacts:
    try:
        details = os.fstat(expected.fd)
    except OSError as exc:
        raise RuntimeError(f"{expected.role} FD is unavailable") from exc
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"{expected.role} FD is not a regular file")
    return _DescriptorFacts(
        role=expected.role,
        fd=expected.fd,
        dev=details.st_dev,
        ino=details.st_ino,
        uid=details.st_uid,
        gid=details.st_gid,
        mode=stat.S_IMODE(details.st_mode),
        nlink=details.st_nlink,
        size=details.st_size,
        blocks=details.st_blocks,
        mtime_ns=details.st_mtime_ns,
        ctime_ns=details.st_ctime_ns,
        access_mode=_descriptor_access_mode(expected.fd, expected.role),
        content_sha256=expected.content_sha256,
    )


def _require_exact_facts(
    expected: _DescriptorFacts,
    observed: _DescriptorFacts,
    *,
    label: str,
) -> None:
    for field in (
        "fd",
        "dev",
        "ino",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
        "blocks",
        "mtime_ns",
        "ctime_ns",
        "access_mode",
    ):
        if getattr(expected, field) != getattr(observed, field):
            raise RuntimeError(f"{expected.role} {label} disagrees on {field}")


def _hash_fd(facts: _DescriptorFacts) -> str:
    """Hash the exact inherited regular descriptor without reopening a path."""

    digest = sha256()
    offset = 0
    while offset < facts.size:
        try:
            block = os.pread(facts.fd, min(_HASH_CHUNK_BYTES, facts.size - offset), offset)
        except OSError as exc:
            raise RuntimeError(f"{facts.role} FD cannot be read") from exc
        if not block:
            raise RuntimeError(f"{facts.role} FD changed while hashing")
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def _validate_descriptor(expected: _DescriptorFacts, *, prior: _DescriptorFacts | None) -> _ValidatedDescriptor:
    observed = _observed_facts(expected)
    _require_exact_facts(expected, observed, label="expected facts")
    if prior is not None:
        _require_exact_facts(prior, observed, label="READY-time facts")
    if expected.content_sha256 is not None:
        actual_hash = _hash_fd(observed)
        # A hash is meaningful only if fstat proves the complete preimage stayed
        # stable for the full read.  This second fstat also catches in-place
        # same-inode writes made while pread was streaming.
        completed = _observed_facts(expected)
        _require_exact_facts(observed, completed, label="hash-time facts")
        if actual_hash != expected.content_sha256:
            raise RuntimeError(f"{expected.role} content hash disagrees")
    return _ValidatedDescriptor(expected=expected, observed=observed)


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


def _set_exec_inheritance(
    *,
    control_fd: int,
    executable_fd: int,
    overlay_fd: int,
    base_fd: int,
    marker_fd: int,
) -> None:
    """Leave only QEMU's two reviewed storage descriptors inheritable."""

    try:
        for fd in (control_fd, executable_fd, marker_fd):
            os.set_inheritable(fd, False)
        for fd in (overlay_fd, base_fd):
            os.set_inheritable(fd, True)
    except OSError as exc:
        raise RuntimeError("guard could not set descriptor inheritance") from exc


def _set_guard_cloexec(*, control_fd: int, executable_fd: int, overlay_fd: int, base_fd: int, marker_fd: int) -> None:
    """Close every guard authority across any unintended intermediate exec."""

    try:
        for fd in (control_fd, executable_fd, overlay_fd, base_fd, marker_fd):
            os.set_inheritable(fd, False)
    except OSError as exc:
        raise RuntimeError("guard could not close descriptor inheritance") from exc


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate inherited authority, await durable release, and exec QEMU.

    Args:
        arguments: Guard protocol arguments, or process arguments when omitted.

    Returns:
        Never returns after successful ``execve``; otherwise raises or exits.
    """
    argv = tuple(sys.argv[1:] if arguments is None else arguments)
    if len(argv) < 4 + _DESCRIPTOR_FIELDS * 3 + 2:
        raise RuntimeError("guard descriptor protocol and target argv are required")
    expected_parent_pid = _strict_positive_int(argv[0], "parent PID", 2**31 - 1)
    control_fd = _strict_positive_int(argv[1], "control FD", 2**31 - 1)
    runtime_home = _private_home(argv[2])
    executable_fd = _validated_executable_fd(argv[3], control_fd)
    overlay, base, marker, raw_target_argv = _parse_storage_descriptors(
        argv[4:], control_fd=control_fd, executable_fd=executable_fd
    )
    target_argv = _validated_target_argv(raw_target_argv)
    initial_overlay = _validate_descriptor(overlay, prior=None)
    initial_base = _validate_descriptor(base, prior=None)
    initial_marker = _validate_descriptor(marker, prior=None)

    try:
        control = socket.socket(fileno=control_fd)
    except OSError as exc:
        raise RuntimeError("guard control FD is not a socket") from exc
    with control:
        if control.family != socket.AF_UNIX or control.type != socket.SOCK_STREAM:
            raise RuntimeError("guard control channel must be an AF_UNIX stream")
        _set_guard_cloexec(
            control_fd=control_fd,
            executable_fd=executable_fd,
            overlay_fd=overlay.fd,
            base_fd=base.fd,
            marker_fd=marker.fd,
        )
        _arm_parent_death(expected_parent_pid)
        control.sendall(READY)
        _receive_release(control)
        _validate_descriptor(overlay, prior=initial_overlay.observed)
        _validate_descriptor(base, prior=initial_base.observed)
        _validate_descriptor(marker, prior=initial_marker.observed)
        _set_exec_inheritance(
            control_fd=control_fd,
            executable_fd=executable_fd,
            overlay_fd=overlay.fd,
            base_fd=base.fd,
            marker_fd=marker.fd,
        )
        _clear_parent_death(expected_parent_pid)
    os.umask(0o077)
    os.execve(executable_fd, target_argv, _target_environment(runtime_home))
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


__all__ = ["READY", "RELEASE", "main"]
