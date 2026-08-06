"""Linux process-identity and exact-termination primitives for P4.

This module does not launch QEMU and does not infer VM state.  It provides the
physical process half of the future daemon-owned QEMU boundary: canonical
executable selection, bounded ``/proc`` observation, argv identity, pidfile
verification, PID-reuse rejection, and signal-after-revalidation termination.
QMP UUID/status evidence remains an independent requirement before a process
can become VM truth.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import signal
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Final

from somnus_protocol import ProcessIdentity


MAX_ARGV_ITEMS: Final[int] = 4_096
MAX_ARG_BYTES: Final[int] = 64 * 1024
MAX_COMMAND_BYTES: Final[int] = 1024 * 1024
MAX_EXECUTABLE_BYTES: Final[int] = 512 * 1024 * 1024
MAX_PROC_BYTES: Final[int] = 1024 * 1024
MAX_PATH_BYTES: Final[int] = 4_096
_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_PID_TEXT: Final[re.Pattern[bytes]] = re.compile(rb"^[1-9][0-9]{0,9}\n?$")


class QemuProcessError(RuntimeError):
    """Base class for fail-closed P4 process evidence failures."""


class ProcessUnavailableError(QemuProcessError):
    """The selected PID cannot be observed as a live userspace process."""


class ProcessIdentityMismatchError(QemuProcessError):
    """The selected PID exists but does not match the recorded owner."""


class ProcessTerminationError(QemuProcessError):
    """An exactly identified pidfd-bound process could not be proven terminated."""


@dataclass(frozen=True, slots=True)
class PidfileIdentity:
    """Stable identity of one private pidfile bound to an observed process."""

    path: Path
    pid: int
    device_id: int
    inode: int
    owner_uid: int
    mode: int
    link_count: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int

    def __post_init__(self) -> None:
        _canonical_absolute_path(self.path, "pidfile", must_exist=True)
        _strict_positive_int(self.pid, "pidfile PID", maximum=2**31 - 1)
        _strict_positive_int(
            self.device_id,
            "pidfile device_id",
            maximum=2**63 - 1,
        )
        _strict_positive_int(
            self.inode,
            "pidfile inode",
            maximum=2**63 - 1,
        )
        if (
            not isinstance(self.owner_uid, int)
            or isinstance(self.owner_uid, bool)
            or self.owner_uid < 0
            or self.owner_uid > 2**32 - 1
        ):
            raise QemuProcessError("pidfile owner_uid is invalid")
        if (
            not isinstance(self.mode, int)
            or isinstance(self.mode, bool)
            or self.mode < 0
            or self.mode > 0o7777
            or self.mode & 0o077
        ):
            raise QemuProcessError("pidfile mode is not owner-private")
        if self.link_count != 1:
            raise QemuProcessError("pidfile link_count must be one")
        _strict_positive_int(
            self.size_bytes,
            "pidfile size_bytes",
            maximum=64,
        )
        for value, label in (
            (self.mtime_ns, "pidfile mtime_ns"),
            (self.ctime_ns, "pidfile ctime_ns"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > 2**127 - 1
            ):
                raise QemuProcessError(f"{label} is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": os.fspath(self.path),
            "pid": self.pid,
            "device_id": self.device_id,
            "inode": self.inode,
            "owner_uid": self.owner_uid,
            "mode": self.mode,
            "link_count": self.link_count,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
        }


def _strict_positive_int(
    value: object,
    label: str,
    *,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > maximum
    ):
        raise QemuProcessError(f"{label} is outside the supported bound")
    return value


def _strict_timeout(value: object, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0.0 <= float(value) <= 3_600.0
    ):
        raise QemuProcessError(f"{label} is outside the supported bound")
    return float(value)


def _canonical_absolute_path(
    value: str | os.PathLike[str],
    label: str,
    *,
    must_exist: bool,
) -> Path:
    try:
        path = Path(value)
        text = os.fspath(path)
    except (TypeError, ValueError) as exc:
        raise QemuProcessError(f"{label} is invalid") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or PurePosixPath(text) != path
        or "\x00" in text
        or len(os.fsencode(path)) > MAX_PATH_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise QemuProcessError(
            f"{label} must be a canonical absolute non-root path"
        )
    try:
        resolved = path.resolve(strict=must_exist)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise QemuProcessError(f"{label} cannot be resolved") from exc
    if resolved != path:
        raise QemuProcessError(f"{label} must not contain a symlink")
    return path


def _trusted_owner_ids() -> frozenset[int]:
    # Long-lived VM authority cannot execute bytes mutable by another process
    # running as the daemon uid.  The P4 host binary must therefore come from
    # the root-owned system software boundary.
    return frozenset({0})


def _validate_trusted_ancestors(path: Path) -> None:
    allowed_uids = _trusted_owner_ids()
    for parent in path.parents:
        try:
            details = parent.stat(follow_symlinks=False)
        except OSError as exc:
            raise QemuProcessError(
                "executable ancestor cannot be observed"
            ) from exc
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid not in allowed_uids
            or details.st_mode & 0o022
        ):
            raise QemuProcessError(
                "executable ancestor ownership or permissions are unsafe"
            )


def resolve_executable(binary: str) -> Path:
    """Resolve one immutable-enough executable selection for observation.

    A bare program name is resolved through ``PATH`` once.  A path-bearing
    value must already be canonical and absolute; caller-controlled relative
    paths and symlink aliases are never accepted.
    """

    if (
        not isinstance(binary, str)
        or not binary
        or "\x00" in binary
        or any(ord(character) < 32 or ord(character) == 127 for character in binary)
    ):
        raise QemuProcessError("executable selection is invalid")
    if "/" in binary or "\\" in binary:
        if "\\" in binary:
            raise QemuProcessError(
                "executable path must be canonical and absolute"
            )
        selected = _canonical_absolute_path(
            binary,
            "executable path",
            must_exist=True,
        )
    else:
        located = shutil.which(binary)
        if located is None:
            raise QemuProcessError("executable is unavailable")
        selected = _canonical_absolute_path(
            os.fspath(Path(located).resolve(strict=True)),
            "executable path",
            must_exist=True,
        )
    try:
        details = selected.stat(follow_symlinks=False)
    except OSError as exc:
        raise QemuProcessError("executable cannot be observed") from exc
    _validate_trusted_ancestors(selected)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_uid not in _trusted_owner_ids()
        or details.st_size < 1
        or details.st_size > MAX_EXECUTABLE_BYTES
        or details.st_mode & 0o022
        or not os.access(selected, os.X_OK)
    ):
        raise QemuProcessError("executable ownership or permissions are unsafe")
    return selected


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    """Stable pre-launch identity for one trusted executable inode."""

    path: Path
    device_id: int
    inode: int
    owner_uid: int
    mode: int
    link_count: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    sha256: str

    def __post_init__(self) -> None:
        _canonical_absolute_path(
            self.path,
            "executable path",
            must_exist=True,
        )
        _strict_positive_int(
            self.device_id,
            "executable device_id",
            maximum=2**63 - 1,
        )
        _strict_positive_int(
            self.inode,
            "executable inode",
            maximum=2**63 - 1,
        )
        if (
            not isinstance(self.owner_uid, int)
            or isinstance(self.owner_uid, bool)
            or self.owner_uid < 0
            or self.owner_uid > 2**32 - 1
        ):
            raise QemuProcessError("executable owner_uid is invalid")
        if self.owner_uid not in _trusted_owner_ids():
            raise QemuProcessError("executable owner_uid is not trusted")
        if (
            not isinstance(self.mode, int)
            or isinstance(self.mode, bool)
            or self.mode < 0
            or self.mode > 0o7777
            or self.mode & 0o022
            or self.mode & 0o111 == 0
        ):
            raise QemuProcessError("executable mode is invalid")
        if self.link_count != 1:
            raise QemuProcessError("executable link_count must be one")
        _strict_positive_int(
            self.size_bytes,
            "executable size_bytes",
            maximum=MAX_EXECUTABLE_BYTES,
        )
        for value, label in (
            (self.mtime_ns, "executable mtime_ns"),
            (self.ctime_ns, "executable ctime_ns"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > 2**127 - 1
            ):
                raise QemuProcessError(f"{label} is invalid")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(
            self.sha256
        ):
            raise QemuProcessError("executable sha256 is invalid")


def _inspect_open_executable(
    selected: Path,
    descriptor: int,
) -> ExecutableIdentity:
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_EXECUTABLE_BYTES:
                raise QemuProcessError(
                    "executable exceeds the supported bound"
                )
            digest.update(block)
        after = os.fstat(descriptor)
        try:
            path_details = selected.stat(follow_symlinks=False)
        except OSError as exc:
            raise QemuProcessError(
                "executable path cannot be re-observed"
            ) from exc
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in _trusted_owner_ids()
            or before.st_size < 1
            or before.st_size > MAX_EXECUTABLE_BYTES
            or before.st_mode & 0o022
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_uid != after.st_uid
            or before.st_mode != after.st_mode
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or total != after.st_size
            or path_details.st_dev != after.st_dev
            or path_details.st_ino != after.st_ino
            or path_details.st_uid != after.st_uid
            or path_details.st_mode != after.st_mode
            or path_details.st_size != after.st_size
            or path_details.st_mtime_ns != after.st_mtime_ns
            or path_details.st_ctime_ns != after.st_ctime_ns
        ):
            raise QemuProcessError(
                "executable identity changed during inspection"
            )
        return ExecutableIdentity(
            path=selected,
            device_id=after.st_dev,
            inode=after.st_ino,
            owner_uid=after.st_uid,
            mode=stat.S_IMODE(after.st_mode),
            link_count=after.st_nlink,
            size_bytes=after.st_size,
            mtime_ns=after.st_mtime_ns,
            ctime_ns=after.st_ctime_ns,
            sha256=digest.hexdigest(),
        )
    except OSError as exc:
        raise QemuProcessError("executable cannot be inspected") from exc


def open_pinned_executable(binary: str) -> tuple[ExecutableIdentity, int]:
    """Return a verified identity and caller-owned FD pinning that inode."""

    selected = resolve_executable(binary)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(selected, flags)
    except OSError as exc:
        raise QemuProcessError("executable cannot be opened safely") from exc
    try:
        identity = _inspect_open_executable(selected, descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return identity, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def inspect_executable(binary: str) -> ExecutableIdentity:
    """Hash and bind the selected executable before a future launch."""

    identity, descriptor = open_pinned_executable(binary)
    os.close(descriptor)
    return identity


def normalize_argv(
    executable: Path,
    arguments: tuple[str, ...],
) -> tuple[str, ...]:
    """Return one bounded full argv beginning with the canonical executable."""

    try:
        executable_text = os.fspath(executable)
    except TypeError as exc:
        raise QemuProcessError("executable is invalid") from exc
    selected = resolve_executable(executable_text)
    if not isinstance(arguments, tuple) or len(arguments) > MAX_ARGV_ITEMS - 1:
        raise QemuProcessError("process arguments violate the supported bound")
    normalized: list[str] = [os.fspath(selected)]
    total = len(os.fsencode(selected))
    for argument in arguments:
        if (
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            or any(
                ord(character) < 32 and character not in {"\t"}
                for character in argument
            )
        ):
            raise QemuProcessError("process argv contains an invalid argument")
        encoded = os.fsencode(argument)
        if len(encoded) > MAX_ARG_BYTES:
            raise QemuProcessError("process argv argument is too large")
        total += len(encoded) + 1
        if total > MAX_COMMAND_BYTES:
            raise QemuProcessError("process argv exceeds the supported bound")
        normalized.append(argument)
    return tuple(normalized)


def command_sha256(argv: tuple[str, ...]) -> str:
    """Hash an exact argv using canonical length-unambiguous JSON bytes."""

    if not isinstance(argv, tuple) or not argv:
        raise QemuProcessError("process argv must be a non-empty tuple")
    if len(argv) > MAX_ARGV_ITEMS:
        raise QemuProcessError("process argv has too many entries")
    for argument in argv:
        if (
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            or any(
                (ord(character) < 32 and character != "\t")
                or ord(character) == 127
                for character in argument
            )
            or len(os.fsencode(argument)) > MAX_ARG_BYTES
        ):
            raise QemuProcessError("process argv contains an invalid argument")
    encoded = json.dumps(
        list(argv),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(encoded) > MAX_COMMAND_BYTES:
        raise QemuProcessError("process argv exceeds the supported bound")
    return hashlib.sha256(encoded).hexdigest()


def _read_bounded(path: Path, label: str, maximum: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError as exc:
        raise ProcessUnavailableError(f"{label} is unavailable") from exc
    except OSError as exc:
        raise QemuProcessError(f"{label} cannot be opened") from exc
    try:
        payload = bytearray()
        while len(payload) <= maximum:
            block = os.read(descriptor, min(64 * 1024, maximum + 1 - len(payload)))
            if not block:
                return bytes(payload)
            payload.extend(block)
        raise QemuProcessError(f"{label} exceeds the supported bound")
    except OSError as exc:
        raise QemuProcessError(f"{label} cannot be read") from exc
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _ProcStat:
    pid: int
    state: str
    process_group_id: int
    session_id: int
    start_time_ticks: int


@dataclass(frozen=True, slots=True)
class _ProcOwner:
    device_id: int
    inode: int
    owner_uid: int


def _observe_proc_owner(pid: int) -> _ProcOwner:
    try:
        details = Path(f"/proc/{pid}").stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ProcessUnavailableError(
            "process directory is unavailable"
        ) from exc
    except OSError as exc:
        raise QemuProcessError(
            "process directory cannot be observed"
        ) from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_dev < 1
        or details.st_ino < 1
        or details.st_uid != os.geteuid()
    ):
        raise ProcessIdentityMismatchError(
            "process directory is not owned by the daemon uid"
        )
    return _ProcOwner(
        device_id=details.st_dev,
        inode=details.st_ino,
        owner_uid=details.st_uid,
    )


def _parse_proc_stat(pid: int) -> _ProcStat:
    raw = _read_bounded(
        Path(f"/proc/{pid}/stat"),
        "process stat",
        64 * 1024,
    )
    marker = raw.rfind(b") ")
    first_space = raw.find(b" ")
    if first_space < 1 or marker <= first_space:
        raise QemuProcessError("process stat shape is invalid")
    try:
        observed_pid = int(raw[:first_space])
        fields = raw[marker + 2 :].split()
        state = fields[0].decode("ascii")
        process_group_id = int(fields[2])
        session_id = int(fields[3])
        start_time_ticks = int(fields[19])
    except (IndexError, ValueError, UnicodeDecodeError) as exc:
        raise QemuProcessError("process stat fields are invalid") from exc
    if observed_pid != pid or len(state) != 1:
        raise ProcessIdentityMismatchError("process stat identity changed")
    return _ProcStat(
        pid=pid,
        state=state,
        process_group_id=process_group_id,
        session_id=session_id,
        start_time_ticks=start_time_ticks,
    )


def _read_proc_argv(pid: int) -> tuple[str, ...]:
    raw = _read_bounded(
        Path(f"/proc/{pid}/cmdline"),
        "process command line",
        MAX_PROC_BYTES,
    )
    if not raw or not raw.endswith(b"\x00"):
        raise QemuProcessError("process command line is incomplete")
    chunks = raw[:-1].split(b"\x00")
    if not chunks or len(chunks) > MAX_ARGV_ITEMS or any(not item for item in chunks):
        raise QemuProcessError("process command line shape is invalid")
    try:
        argv = tuple(os.fsdecode(item) for item in chunks)
    except UnicodeError as exc:
        raise QemuProcessError("process command line cannot be decoded") from exc
    if any("\x00" in item for item in argv):
        raise QemuProcessError("process command line contains a null")
    return argv


def _observe_process_executable(
    pid: int,
    expected: ExecutableIdentity | None = None,
) -> ExecutableIdentity:
    proc_exe = Path(f"/proc/{pid}/exe")
    try:
        selected_text = os.readlink(proc_exe)
    except FileNotFoundError as exc:
        raise ProcessUnavailableError("process executable is unavailable") from exc
    except OSError as exc:
        raise QemuProcessError("process executable cannot be resolved") from exc
    if selected_text.endswith(" (deleted)"):
        raise ProcessIdentityMismatchError("process executable was deleted")
    selected = _canonical_absolute_path(
        selected_text,
        "process executable",
        must_exist=True,
    )
    _validate_trusted_ancestors(selected)
    try:
        descriptor = os.open(proc_exe, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError as exc:
        raise ProcessUnavailableError("process executable is unavailable") from exc
    except OSError as exc:
        raise QemuProcessError("process executable cannot be opened") from exc
    try:
        if expected is None:
            return _inspect_open_executable(selected, descriptor)
        if selected != expected.path:
            raise ProcessIdentityMismatchError(
                "process executable path changed"
            )
        before = os.fstat(descriptor)
        try:
            path_details = selected.stat(follow_symlinks=False)
        except OSError as exc:
            raise QemuProcessError(
                "process executable path cannot be re-observed"
            ) from exc
        after = os.fstat(descriptor)
        for details in (before, path_details, after):
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_dev != expected.device_id
                or details.st_ino != expected.inode
                or details.st_uid != expected.owner_uid
                or stat.S_IMODE(details.st_mode) != expected.mode
                or details.st_nlink != expected.link_count
                or details.st_size != expected.size_bytes
                or details.st_mtime_ns != expected.mtime_ns
                or details.st_ctime_ns != expected.ctime_ns
            ):
                raise ProcessIdentityMismatchError(
                    "process executable no longer matches the recorded inode"
                )
        return expected
    except OSError as exc:
        raise QemuProcessError("process executable cannot be observed") from exc
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class ObservedProcessIdentity:
    """One stable Linux process observation, stronger than a PID."""

    pid: int
    start_time_ticks: int
    proc_device_id: int
    proc_inode: int
    process_owner_uid: int
    executable: Path
    executable_device_id: int
    executable_inode: int
    executable_owner_uid: int
    executable_mode: int
    executable_link_count: int
    executable_size_bytes: int
    executable_mtime_ns: int
    executable_ctime_ns: int
    executable_sha256: str
    argv: tuple[str, ...]
    command_sha256: str
    process_group_id: int
    session_id: int
    observed_at: datetime

    def __post_init__(self) -> None:
        _strict_positive_int(self.pid, "pid", maximum=2**31 - 1)
        _strict_positive_int(
            self.start_time_ticks,
            "start_time_ticks",
            maximum=2**63 - 1,
        )
        _strict_positive_int(
            self.proc_device_id,
            "proc_device_id",
            maximum=2**63 - 1,
        )
        _strict_positive_int(
            self.proc_inode,
            "proc_inode",
            maximum=2**63 - 1,
        )
        if self.process_owner_uid != os.geteuid():
            raise QemuProcessError(
                "process_owner_uid must match the daemon uid"
            )
        _canonical_absolute_path(
            self.executable,
            "executable",
            must_exist=True,
        )
        _strict_positive_int(
            self.executable_device_id,
            "executable_device_id",
            maximum=2**63 - 1,
        )
        _strict_positive_int(
            self.executable_inode,
            "executable_inode",
            maximum=2**63 - 1,
        )
        ExecutableIdentity(
            path=self.executable,
            device_id=self.executable_device_id,
            inode=self.executable_inode,
            owner_uid=self.executable_owner_uid,
            mode=self.executable_mode,
            link_count=self.executable_link_count,
            size_bytes=self.executable_size_bytes,
            mtime_ns=self.executable_mtime_ns,
            ctime_ns=self.executable_ctime_ns,
            sha256=self.executable_sha256,
        )
        if not isinstance(self.executable_sha256, str) or not _SHA256.fullmatch(
            self.executable_sha256
        ):
            raise QemuProcessError("executable_sha256 is invalid")
        if command_sha256(self.argv) != self.command_sha256:
            raise QemuProcessError("command_sha256 disagrees with argv")
        _strict_positive_int(
            self.process_group_id,
            "process_group_id",
            maximum=2**31 - 1,
        )
        _strict_positive_int(
            self.session_id,
            "session_id",
            maximum=2**31 - 1,
        )
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise QemuProcessError("observed_at must be timezone-aware")
        object.__setattr__(
            self,
            "observed_at",
            self.observed_at.astimezone(timezone.utc),
        )

    def to_protocol_identity(self) -> ProcessIdentity:
        """Project the host observation into the canonical shared core."""

        return ProcessIdentity(
            pid=self.pid,
            start_time_ticks=self.start_time_ticks,
            executable=os.fspath(self.executable),
            executable_sha256=self.executable_sha256,
            observed_at=self.observed_at,
        )

    def to_executable_identity(self) -> ExecutableIdentity:
        """Project the stable executable facts for fast revalidation."""

        return ExecutableIdentity(
            path=self.executable,
            device_id=self.executable_device_id,
            inode=self.executable_inode,
            owner_uid=self.executable_owner_uid,
            mode=self.executable_mode,
            link_count=self.executable_link_count,
            size_bytes=self.executable_size_bytes,
            mtime_ns=self.executable_mtime_ns,
            ctime_ns=self.executable_ctime_ns,
            sha256=self.executable_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "start_time_ticks": self.start_time_ticks,
            "proc_device_id": self.proc_device_id,
            "proc_inode": self.proc_inode,
            "process_owner_uid": self.process_owner_uid,
            "executable": os.fspath(self.executable),
            "executable_device_id": self.executable_device_id,
            "executable_inode": self.executable_inode,
            "executable_owner_uid": self.executable_owner_uid,
            "executable_mode": self.executable_mode,
            "executable_link_count": self.executable_link_count,
            "executable_size_bytes": self.executable_size_bytes,
            "executable_mtime_ns": self.executable_mtime_ns,
            "executable_ctime_ns": self.executable_ctime_ns,
            "executable_sha256": self.executable_sha256,
            "argv": list(self.argv),
            "command_sha256": self.command_sha256,
            "process_group_id": self.process_group_id,
            "session_id": self.session_id,
            "observed_at": self.observed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ObservedProcessIdentity":
        """Rehydrate exact journal evidence without normalizing ambiguity."""

        expected_keys = frozenset(
            {
                "pid",
                "start_time_ticks",
                "proc_device_id",
                "proc_inode",
                "process_owner_uid",
                "executable",
                "executable_device_id",
                "executable_inode",
                "executable_owner_uid",
                "executable_mode",
                "executable_link_count",
                "executable_size_bytes",
                "executable_mtime_ns",
                "executable_ctime_ns",
                "executable_sha256",
                "argv",
                "command_sha256",
                "process_group_id",
                "session_id",
                "observed_at",
            }
        )
        if (
            not isinstance(payload, dict)
            or frozenset(payload) != expected_keys
            or any(not isinstance(key, str) for key in payload)
        ):
            raise QemuProcessError(
                "observed process payload must contain the exact schema"
            )
        executable = payload["executable"]
        argv = payload["argv"]
        observed_at = payload["observed_at"]
        if (
            not isinstance(executable, str)
            or not isinstance(argv, list)
            or any(not isinstance(argument, str) for argument in argv)
            or not isinstance(observed_at, str)
        ):
            raise QemuProcessError("observed process payload types are invalid")
        try:
            parsed_time = datetime.fromisoformat(observed_at)
        except ValueError as exc:
            raise QemuProcessError(
                "observed process timestamp is invalid"
            ) from exc
        candidate = cls(
            pid=payload["pid"],  # type: ignore[arg-type]
            start_time_ticks=payload["start_time_ticks"],  # type: ignore[arg-type]
            proc_device_id=payload["proc_device_id"],  # type: ignore[arg-type]
            proc_inode=payload["proc_inode"],  # type: ignore[arg-type]
            process_owner_uid=payload["process_owner_uid"],  # type: ignore[arg-type]
            executable=Path(executable),
            executable_device_id=payload["executable_device_id"],  # type: ignore[arg-type]
            executable_inode=payload["executable_inode"],  # type: ignore[arg-type]
            executable_owner_uid=payload["executable_owner_uid"],  # type: ignore[arg-type]
            executable_mode=payload["executable_mode"],  # type: ignore[arg-type]
            executable_link_count=payload["executable_link_count"],  # type: ignore[arg-type]
            executable_size_bytes=payload["executable_size_bytes"],  # type: ignore[arg-type]
            executable_mtime_ns=payload["executable_mtime_ns"],  # type: ignore[arg-type]
            executable_ctime_ns=payload["executable_ctime_ns"],  # type: ignore[arg-type]
            executable_sha256=payload["executable_sha256"],  # type: ignore[arg-type]
            argv=tuple(argv),
            command_sha256=payload["command_sha256"],  # type: ignore[arg-type]
            process_group_id=payload["process_group_id"],  # type: ignore[arg-type]
            session_id=payload["session_id"],  # type: ignore[arg-type]
            observed_at=parsed_time,
        )
        if candidate.to_dict() != payload:
            raise QemuProcessError(
                "observed process payload is not canonical"
            )
        return candidate


def observe_process(
    pid: int,
    *,
    expected_executable: Path | None = None,
    expected_executable_identity: ExecutableIdentity | None = None,
    expected_argv: tuple[str, ...] | None = None,
) -> ObservedProcessIdentity:
    """Observe a live process twice and reject identity drift."""

    selected_pid = _strict_positive_int(pid, "pid", maximum=2**31 - 1)
    pidfd = _open_pidfd(selected_pid)
    try:
        owner_before = _observe_proc_owner(selected_pid)
        before = _parse_proc_stat(selected_pid)
        if before.state == "Z":
            raise ProcessUnavailableError("process is a zombie")
        if (
            expected_executable_identity is not None
            and not isinstance(expected_executable_identity, ExecutableIdentity)
        ):
            raise TypeError(
                "expected_executable_identity must be ExecutableIdentity or None"
            )
        executable_identity = _observe_process_executable(
            selected_pid,
            expected_executable_identity,
        )
        argv = _read_proc_argv(selected_pid)
        after = _parse_proc_stat(selected_pid)
        owner_after = _observe_proc_owner(selected_pid)
        if _pidfd_has_exited(pidfd):
            raise ProcessUnavailableError(
                "process exited during observation"
            )
    finally:
        os.close(pidfd)
    if (
        after.state == "Z"
        or owner_before != owner_after
        or before.start_time_ticks != after.start_time_ticks
        or before.process_group_id != after.process_group_id
        or before.session_id != after.session_id
    ):
        raise ProcessIdentityMismatchError(
            "process identity changed during observation"
        )
    if expected_executable is not None:
        selected_executable = _canonical_absolute_path(
            expected_executable,
            "expected executable",
            must_exist=True,
        )
        if executable_identity.path != selected_executable:
            raise ProcessIdentityMismatchError(
                "process executable does not match the expected owner"
            )
    if expected_argv is not None and argv != expected_argv:
        raise ProcessIdentityMismatchError(
            "process argv does not match the expected owner"
        )
    return ObservedProcessIdentity(
        pid=selected_pid,
        start_time_ticks=after.start_time_ticks,
        proc_device_id=owner_after.device_id,
        proc_inode=owner_after.inode,
        process_owner_uid=owner_after.owner_uid,
        executable=executable_identity.path,
        executable_device_id=executable_identity.device_id,
        executable_inode=executable_identity.inode,
        executable_owner_uid=executable_identity.owner_uid,
        executable_mode=executable_identity.mode,
        executable_link_count=executable_identity.link_count,
        executable_size_bytes=executable_identity.size_bytes,
        executable_mtime_ns=executable_identity.mtime_ns,
        executable_ctime_ns=executable_identity.ctime_ns,
        executable_sha256=executable_identity.sha256,
        argv=argv,
        command_sha256=command_sha256(argv),
        process_group_id=after.process_group_id,
        session_id=after.session_id,
        observed_at=datetime.now(timezone.utc),
    )


def revalidate_process(
    expected: ObservedProcessIdentity,
) -> ObservedProcessIdentity:
    """Re-observe one recorded process and require every stable fact."""

    if not isinstance(expected, ObservedProcessIdentity):
        raise TypeError("expected must be ObservedProcessIdentity")
    current = observe_process(
        expected.pid,
        expected_executable=expected.executable,
        expected_executable_identity=expected.to_executable_identity(),
        expected_argv=expected.argv,
    )
    if (
        current.start_time_ticks != expected.start_time_ticks
        or current.proc_device_id != expected.proc_device_id
        or current.proc_inode != expected.proc_inode
        or current.process_owner_uid != expected.process_owner_uid
        or current.executable_device_id != expected.executable_device_id
        or current.executable_inode != expected.executable_inode
        or current.executable_owner_uid != expected.executable_owner_uid
        or current.executable_mode != expected.executable_mode
        or current.executable_link_count != expected.executable_link_count
        or current.executable_size_bytes != expected.executable_size_bytes
        or current.executable_mtime_ns != expected.executable_mtime_ns
        or current.executable_ctime_ns != expected.executable_ctime_ns
        or current.executable_sha256 != expected.executable_sha256
        or current.command_sha256 != expected.command_sha256
        or current.process_group_id != expected.process_group_id
        or current.session_id != expected.session_id
    ):
        raise ProcessIdentityMismatchError(
            "live process no longer matches recorded identity"
        )
    return current


def require_executable_match(
    expected: ExecutableIdentity,
    process: ObservedProcessIdentity,
) -> None:
    """Require a running process to use the pre-launch executable inode."""

    if not isinstance(expected, ExecutableIdentity):
        raise TypeError("expected must be ExecutableIdentity")
    if not isinstance(process, ObservedProcessIdentity):
        raise TypeError("process must be ObservedProcessIdentity")
    if (
        process.executable != expected.path
        or process.executable_device_id != expected.device_id
        or process.executable_inode != expected.inode
        or process.executable_owner_uid != expected.owner_uid
        or process.executable_mode != expected.mode
        or process.executable_link_count != expected.link_count
        or process.executable_size_bytes != expected.size_bytes
        or process.executable_mtime_ns != expected.mtime_ns
        or process.executable_ctime_ns != expected.ctime_ns
        or process.executable_sha256 != expected.sha256
    ):
        raise ProcessIdentityMismatchError(
            "running executable does not match pre-launch identity"
        )


def read_pidfile(
    path: str | os.PathLike[str],
    *,
    expected_uid: int | None = None,
) -> int:
    """Read one private, regular, unique pidfile without following links."""

    selected = _canonical_absolute_path(path, "pidfile", must_exist=True)
    uid = os.geteuid() if expected_uid is None else expected_uid
    if (
        not isinstance(uid, int)
        or isinstance(uid, bool)
        or uid < 0
        or uid > 2**32 - 1
    ):
        raise QemuProcessError("expected_uid is invalid")
    parent_flags = (
        os.O_RDONLY
        | os.O_CLOEXEC
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(selected.parent, parent_flags)
    except OSError as exc:
        raise QemuProcessError(
            "pidfile parent cannot be opened safely"
        ) from exc
    try:
        parent_before = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_before.st_mode)
            or parent_before.st_uid != os.geteuid()
            or stat.S_IMODE(parent_before.st_mode) != 0o700
        ):
            raise QemuProcessError(
                "pidfile parent ownership or permissions are unsafe"
            )
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(
                selected.name,
                flags,
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise QemuProcessError(
                "pidfile cannot be opened safely"
            ) from exc
        try:
            details = os.fstat(descriptor)
            payload = os.read(descriptor, 64)
            trailing = os.read(descriptor, 1)
            after = os.fstat(descriptor)
            try:
                path_details = os.stat(
                    selected.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise QemuProcessError(
                    "pidfile path cannot be re-observed"
                ) from exc
            parent_after = os.fstat(parent_descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != uid
                or stat.S_IMODE(details.st_mode) & 0o077
                or trailing
                or details.st_dev != after.st_dev
                or details.st_ino != after.st_ino
                or details.st_size != after.st_size
                or details.st_mtime_ns != after.st_mtime_ns
                or details.st_ctime_ns != after.st_ctime_ns
                or details.st_size != len(payload)
                or path_details.st_dev != after.st_dev
                or path_details.st_ino != after.st_ino
                or path_details.st_size != after.st_size
                or path_details.st_mtime_ns != after.st_mtime_ns
                or path_details.st_ctime_ns != after.st_ctime_ns
                or parent_before.st_dev != parent_after.st_dev
                or parent_before.st_ino != parent_after.st_ino
                or parent_before.st_uid != parent_after.st_uid
                or parent_before.st_mode != parent_after.st_mode
            ):
                raise QemuProcessError(
                    "pidfile ownership or identity is unsafe"
                )
        except OSError as exc:
            raise QemuProcessError(
                "pidfile cannot be observed"
            ) from exc
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    if not _PID_TEXT.fullmatch(payload):
        raise QemuProcessError("pidfile payload is invalid")
    parsed = int(payload.strip())
    return _strict_positive_int(parsed, "pidfile PID", maximum=2**31 - 1)


def verify_pidfile(
    expected: ObservedProcessIdentity,
    path: str | os.PathLike[str],
    *,
    expected_uid: int | None = None,
) -> int:
    """Require one pidfile to name the exact already-observed process."""

    if not isinstance(expected, ObservedProcessIdentity):
        raise TypeError("expected must be ObservedProcessIdentity")
    pid = read_pidfile(path, expected_uid=expected_uid)
    if pid != expected.pid:
        raise ProcessIdentityMismatchError(
            "pidfile PID does not match the observed child"
        )
    revalidate_process(expected)
    return pid


def observe_pidfile(
    expected: ObservedProcessIdentity,
    path: str | os.PathLike[str],
    *,
    expected_uid: int | None = None,
) -> PidfileIdentity:
    """Bind verified pidfile contents to one stable pathname inode."""

    if not isinstance(expected, ObservedProcessIdentity):
        raise TypeError("expected must be ObservedProcessIdentity")
    selected = _canonical_absolute_path(path, "pidfile", must_exist=True)
    try:
        before = selected.lstat()
    except OSError as exc:
        raise QemuProcessError("pidfile cannot be observed") from exc
    pid = verify_pidfile(expected, selected, expected_uid=expected_uid)
    try:
        after = selected.lstat()
    except OSError as exc:
        raise QemuProcessError("pidfile path cannot be re-observed") from exc
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise QemuProcessError("pidfile identity changed during observation")
    revalidate_process(expected)
    return PidfileIdentity(
        path=selected,
        pid=pid,
        device_id=after.st_dev,
        inode=after.st_ino,
        owner_uid=after.st_uid,
        mode=stat.S_IMODE(after.st_mode),
        link_count=after.st_nlink,
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )


def _open_pidfd(pid: int) -> int:
    opener = getattr(os, "pidfd_open", None)
    if opener is None:
        raise ProcessTerminationError(
            "pidfd_open is required for exact process signaling"
        )
    try:
        return opener(pid, 0)
    except ProcessLookupError as exc:
        raise ProcessUnavailableError(
            "owned process exited before pidfd binding"
        ) from exc
    except OSError as exc:
        raise ProcessTerminationError(
            "owned process cannot be bound to a pidfd"
        ) from exc


def _pidfd_has_exited(pidfd: int) -> bool:
    poller = select.poll()
    poller.register(
        pidfd,
        select.POLLIN | select.POLLHUP | select.POLLERR,
    )
    return bool(poller.poll(0))


def prove_process_alive(
    expected: ObservedProcessIdentity,
) -> ObservedProcessIdentity:
    """Fence one exact process identity with a non-readable pidfd.

    A successful ``/proc`` observation alone can race task exit.  This helper
    binds the numeric PID first, revalidates the complete immutable process
    identity around two zero-time pidfd polls, and returns only while the
    original task is still non-readable.  It never signals the task.
    """

    if not isinstance(expected, ObservedProcessIdentity):
        raise TypeError("expected must be ObservedProcessIdentity")
    pidfd = _open_pidfd(expected.pid)
    try:
        current = revalidate_process(expected)
        if _pidfd_has_exited(pidfd):
            raise ProcessUnavailableError(
                "owned process exited during the liveness fence"
            )
        current = revalidate_process(current)
        if _pidfd_has_exited(pidfd):
            raise ProcessUnavailableError(
                "owned process exited before the liveness fence completed"
            )
        return current
    finally:
        os.close(pidfd)


def _send_pidfd_signal(pidfd: int, selected_signal: signal.Signals) -> bool:
    sender = getattr(signal, "pidfd_send_signal", None)
    if sender is None:
        raise ProcessTerminationError(
            "pidfd_send_signal is required for exact process signaling"
        )
    try:
        sender(pidfd, selected_signal, None, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise ProcessTerminationError(
            f"{selected_signal.name} could not be delivered through pidfd"
        ) from exc


def _wait_pidfd_termination(
    pidfd: int,
    timeout_seconds: float,
) -> bool:
    poller = select.poll()
    poller.register(
        pidfd,
        select.POLLIN | select.POLLHUP | select.POLLERR,
    )
    timeout_milliseconds = min(
        int(timeout_seconds * 1_000),
        2**31 - 1,
    )
    if timeout_seconds > 0.0 and timeout_milliseconds == 0:
        timeout_milliseconds = 1
    events = poller.poll(timeout_milliseconds)
    return bool(events)


@dataclass(frozen=True, slots=True)
class TerminationEvidence:
    """Observed result of signaling one revalidated process through pidfd."""

    pid: int
    start_time_ticks: int
    pidfd_used: bool
    sigterm_sent: bool
    sigkill_sent: bool
    exited: bool
    observed_at: datetime


def terminate_owned_process(
    expected: ObservedProcessIdentity,
    *,
    graceful_timeout_seconds: float = 2.0,
    kill_timeout_seconds: float = 2.0,
) -> TerminationEvidence:
    """Signal only an exact pidfd-bound session leader and prove termination."""

    if not isinstance(expected, ObservedProcessIdentity):
        raise TypeError("expected must be ObservedProcessIdentity")
    graceful = _strict_timeout(
        graceful_timeout_seconds,
        "graceful timeout",
    )
    forced = _strict_timeout(kill_timeout_seconds, "kill timeout")
    pidfd = _open_pidfd(expected.pid)
    try:
        # Binding precedes validation: if the numeric PID was already reused,
        # validation fails before this pidfd is ever allowed to signal it.  If
        # validation succeeds, later PID reuse cannot redirect the pidfd.
        current = revalidate_process(expected)
        if (
            current.process_group_id != current.pid
            or current.session_id != current.pid
        ):
            raise ProcessIdentityMismatchError(
                "owned child is not an isolated process-group/session leader"
            )
        sigterm_sent = _send_pidfd_signal(pidfd, signal.SIGTERM)
        exited = _wait_pidfd_termination(
            pidfd,
            graceful,
        )
        sigkill_sent = False
        if not exited:
            forced_deadline = time.monotonic() + forced
            if _pidfd_has_exited(pidfd):
                exited = True
            else:
                try:
                    # The still-unready pidfd proves the original task remains
                    # bound.  Fast metadata/argv/session revalidation precedes
                    # force; an unobservable transition is never signaled.
                    revalidate_process(expected)
                except ProcessUnavailableError:
                    remaining = max(0.0, forced_deadline - time.monotonic())
                    exited = _wait_pidfd_termination(pidfd, remaining)
                    if not exited:
                        raise ProcessIdentityMismatchError(
                            "owned process became unobservable before escalation"
                        )
                if not exited:
                    if _pidfd_has_exited(pidfd):
                        exited = True
                    else:
                        sigkill_sent = _send_pidfd_signal(
                            pidfd,
                            signal.SIGKILL,
                        )
                        remaining = max(
                            0.0,
                            forced_deadline - time.monotonic(),
                        )
                        exited = _wait_pidfd_termination(
                            pidfd,
                            remaining,
                        )
        if not exited:
            raise ProcessTerminationError(
                "owned process could not be proven terminated"
            )
    finally:
        os.close(pidfd)
    return TerminationEvidence(
        pid=expected.pid,
        start_time_ticks=expected.start_time_ticks,
        pidfd_used=True,
        sigterm_sent=sigterm_sent,
        sigkill_sent=sigkill_sent,
        exited=True,
        observed_at=datetime.now(timezone.utc),
    )


__all__ = [
    "ExecutableIdentity",
    "ObservedProcessIdentity",
    "PidfileIdentity",
    "ProcessIdentityMismatchError",
    "ProcessTerminationError",
    "ProcessUnavailableError",
    "QemuProcessError",
    "TerminationEvidence",
    "command_sha256",
    "inspect_executable",
    "normalize_argv",
    "observe_process",
    "observe_pidfile",
    "open_pinned_executable",
    "prove_process_alive",
    "read_pidfile",
    "require_executable_match",
    "resolve_executable",
    "revalidate_process",
    "terminate_owned_process",
    "verify_pidfile",
]
