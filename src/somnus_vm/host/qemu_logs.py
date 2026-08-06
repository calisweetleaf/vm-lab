"""Bounded, private, redacted log storage for supervised QEMU streams.

QEMU itself never receives persistent log paths.  The runtime owner routes
serial stdout and QEMU stderr into a separate durable log guardian, which uses
this module to redact before bytes reach disk and to rotate within a fixed
retention bound.  This module does not launch QEMU or infer runtime truth.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Final, Iterable


MIN_LOG_BYTES: Final[int] = 64 * 1024
MAX_LOG_BYTES: Final[int] = 1024 * 1024 * 1024
MAX_BACKUP_COUNT: Final[int] = 32
MAX_FEED_BYTES: Final[int] = 1024 * 1024
MAX_LINE_BYTES: Final[int] = 64 * 1024
MAX_SECRET_BYTES: Final[int] = 4 * 1024
_REDACTED: Final[bytes] = b"<redacted>"
_OVERSIZED: Final[bytes] = (
    b"[somnus redacted one oversized unterminated log line]\n"
)
_ASSIGNMENT_PREFIX: Final[re.Pattern[bytes]] = re.compile(
    rb"""(?ix)
    (
        ["']?
        (?:
            password
            | passwd
            | (?:access[_-]?|refresh[_-]?|auth[_-]?)?token
            | (?:client[_-]?)?secret
            | api[_-]?key
        )
        ["']?
        \s*[:=]\s*
    )
    """
)
_AUTHORIZATION: Final[re.Pattern[bytes]] = re.compile(
    rb"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)([^\s,;]+)"
)
_URI_USERINFO: Final[re.Pattern[bytes]] = re.compile(
    rb"(?i)([a-z][a-z0-9+.-]{0,31}://[^/\s:@]+:)"
    rb"([^@/\s]+)(@)"
)


class QemuLogError(RuntimeError):
    """A private log boundary could not preserve its invariants."""


class _PathLease:
    """One persistent-inode advisory lock for a private log pathname."""

    def __init__(self, directory_fd: int, log_name: str) -> None:
        digest = hashlib.sha256(os.fsencode(log_name)).hexdigest()
        self._directory_fd = directory_fd
        self._name = f".somnus-log-{digest}.lock"
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise QemuLogError("private log ownership is already held")
        flags = (
            os.O_RDWR
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        created = False
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self._name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=self._directory_fd,
            )
            created = True
        except FileExistsError:
            try:
                descriptor = os.open(
                    self._name,
                    flags,
                    dir_fd=self._directory_fd,
                )
            except OSError as exc:
                raise QemuLogError(
                    "private log ownership lock is unsafe"
                ) from exc
        except OSError as exc:
            raise QemuLogError(
                "private log ownership lock cannot be created"
            ) from exc
        assert descriptor is not None
        try:
            if created:
                os.fchmod(descriptor, 0o600)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != os.geteuid()
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise QemuLogError(
                    "private log ownership lock failed validation"
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise QemuLogError(
                        "another process owns the private log"
                    ) from exc
                raise QemuLogError(
                    "private log ownership lock cannot be acquired"
                ) from exc
            entry = os.stat(
                self._name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            if (
                entry.st_dev != details.st_dev
                or entry.st_ino != details.st_ino
            ):
                raise QemuLogError(
                    "private log ownership lock changed during acquisition"
                )
            os.fsync(descriptor)
            if created:
                os.fsync(self._directory_fd)
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _strict_int(
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
        raise QemuLogError(f"{label} is outside the supported bound")
    return value


def _canonical_absolute_path(
    value: str | os.PathLike[str],
    label: str,
    *,
    must_exist: bool,
) -> Path:
    try:
        supplied = os.fspath(value)
        if not isinstance(supplied, str):
            raise TypeError
        path = Path(supplied)
        text = os.fspath(path)
    except (TypeError, ValueError) as exc:
        raise QemuLogError(f"{label} is invalid") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or supplied != text
        or PurePosixPath(text) != path
        or "\x00" in text
        or len(os.fsencode(path)) > 4_096
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise QemuLogError(
            f"{label} must be a canonical absolute non-root path"
        )
    try:
        resolved = path.resolve(strict=must_exist)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise QemuLogError(f"{label} cannot be resolved") from exc
    if resolved != path:
        raise QemuLogError(f"{label} must not contain a symlink")
    return path


def _validate_directory(
    details: os.stat_result,
    label: str,
    *,
    private: bool,
) -> None:
    mode = stat.S_IMODE(details.st_mode)
    if not stat.S_ISDIR(details.st_mode):
        raise QemuLogError(f"{label} is not a directory")
    if private:
        if details.st_uid != os.geteuid() or mode != 0o700:
            raise QemuLogError(
                f"{label} ownership or permissions are unsafe"
            )
        return
    writable = bool(mode & 0o022)
    trusted_sticky_root = details.st_uid == 0 and bool(mode & stat.S_ISVTX)
    if (
        details.st_uid not in {0, os.geteuid()}
        or (writable and not trusted_sticky_root)
    ):
        raise QemuLogError(f"{label} has an unsafe ancestor")


def prepare_private_directory(
    path: str | os.PathLike[str],
    label: str,
) -> Path:
    """Create or validate one euid-owned exact-mode-0700 directory.

    The walk is descriptor-relative and refuses symbolic links.  Missing
    descendants are created one at a time as 0700; an existing ancestor must
    be owned by root or the effective user and cannot be group/other writable
    except for a root-owned sticky directory such as ``/tmp``.
    """

    selected = _canonical_absolute_path(
        path,
        label,
        must_exist=False,
    )
    flags = (
        os.O_RDONLY
        | os.O_CLOEXEC
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open("/", flags)
    except OSError as exc:
        raise QemuLogError(f"{label} root cannot be inspected") from exc
    created_branch = False
    try:
        parts = selected.parts[1:]
        for index, component in enumerate(parts):
            final = index == len(parts) - 1
            created = False
            try:
                details = os.stat(
                    component,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    created = True
                    created_branch = True
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise QemuLogError(
                        f"{label} cannot be prepared"
                    ) from exc
                try:
                    details = os.stat(
                        component,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise QemuLogError(
                        f"{label} cannot be inspected after creation"
                    ) from exc
            except OSError as exc:
                raise QemuLogError(
                    f"{label} ancestor cannot be inspected"
                ) from exc
            private = final or created or created_branch
            _validate_directory(
                details,
                label,
                private=private,
            )
            child: int | None = None
            try:
                child = os.open(component, flags, dir_fd=descriptor)
                opened = os.fstat(child)
            except OSError as exc:
                if child is not None:
                    os.close(child)
                raise QemuLogError(
                    f"{label} changed during validation"
                ) from exc
            assert child is not None
            if (
                opened.st_dev != details.st_dev
                or opened.st_ino != details.st_ino
            ):
                os.close(child)
                raise QemuLogError(
                    f"{label} changed during validation"
                )
            _validate_directory(opened, label, private=private)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
    try:
        if selected.resolve(strict=True) != selected:
            raise QemuLogError(f"{label} must not contain a symlink")
    except OSError as exc:
        raise QemuLogError(f"{label} cannot be resolved") from exc
    return selected


class RedactedRotatingLog:
    """One line-bounded redactor backed by private rotating files."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_bytes: int = 8 * 1024 * 1024,
        backup_count: int = 4,
        explicit_secrets: Iterable[bytes] = (),
    ) -> None:
        self.max_bytes = _strict_int(
            max_bytes,
            "max_bytes",
            minimum=MIN_LOG_BYTES,
            maximum=MAX_LOG_BYTES,
        )
        self.backup_count = _strict_int(
            backup_count,
            "backup_count",
            minimum=1,
            maximum=MAX_BACKUP_COUNT,
        )
        self.path = _canonical_absolute_path(
            path,
            "log path",
            must_exist=False,
        )
        parent = prepare_private_directory(
            self.path.parent,
            "log root",
        )
        if self.path.parent != parent or self.path.name in {"", ".", ".."}:
            raise QemuLogError("log path must be an immediate log-root child")
        secrets: list[bytes] = []
        for value in explicit_secrets:
            if (
                not isinstance(value, bytes)
                or len(value) < 4
                or len(value) > MAX_SECRET_BYTES
                or b"\x00" in value
            ):
                raise QemuLogError("explicit secret is invalid")
            secrets.append(value)
        self._secrets = tuple(
            sorted(set(secrets), key=len, reverse=True)
        )
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self._directory_fd = os.open(parent, flags)
        except OSError as exc:
            raise QemuLogError("log root cannot be opened safely") from exc
        try:
            directory_details = os.fstat(self._directory_fd)
            path_details = parent.stat(follow_symlinks=False)
            _validate_directory(
                directory_details,
                "log root",
                private=True,
            )
            if (
                path_details.st_dev != directory_details.st_dev
                or path_details.st_ino != directory_details.st_ino
            ):
                raise QemuLogError("log root changed during validation")
        except BaseException:
            os.close(self._directory_fd)
            raise
        self._lease = _PathLease(self._directory_fd, self.path.name)
        self._descriptor: int | None = None
        self._size = 0
        self._pending = bytearray()
        self._dropping_oversized_line = False
        self._closed = False
        try:
            self._lease.acquire()
            self._validate_retention_set()
            if self._entry_exists(self.path.name):
                self._rotate()
            self._open_new()
        except BaseException:
            try:
                self._lease.release()
            finally:
                os.close(self._directory_fd)
            raise

    def _entry_name(self, generation: int) -> str:
        return (
            self.path.name
            if generation == 0
            else f"{self.path.name}.{generation}"
        )

    def _entry_stat(self, name: str) -> os.stat_result | None:
        try:
            details = os.stat(
                name,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise QemuLogError("log retention entry cannot be observed") from exc
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise QemuLogError(
                "log retention entry ownership or permissions are unsafe"
            )
        return details

    def _entry_exists(self, name: str) -> bool:
        return self._entry_stat(name) is not None

    def _validate_retention_set(self) -> None:
        for generation in range(self.backup_count + 1):
            self._entry_stat(self._entry_name(generation))
        prefix = f"{self.path.name}."
        changed = False
        try:
            entries = os.listdir(self._directory_fd)
        except OSError as exc:
            raise QemuLogError("log retention set cannot be listed") from exc
        for name in entries:
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            if not suffix or not suffix.isascii() or not suffix.isdigit():
                continue
            if suffix.startswith("0"):
                self._entry_stat(name)
                raise QemuLogError(
                    "log retention generation name is ambiguous"
                )
            generation = int(suffix, 10)
            if generation <= self.backup_count:
                continue
            self._entry_stat(name)
            try:
                os.unlink(name, dir_fd=self._directory_fd)
                changed = True
            except OSError as exc:
                raise QemuLogError(
                    "out-of-retention log cannot be retired"
                ) from exc
        if changed:
            try:
                os.fsync(self._directory_fd)
            except OSError as exc:
                raise QemuLogError(
                    "retention contraction could not be made durable"
                ) from exc

    def _validate_open_descriptor(self) -> os.stat_result:
        descriptor = self._descriptor
        if descriptor is None:
            raise QemuLogError("private log is not open")
        try:
            details = os.fstat(descriptor)
        except OSError as exc:
            raise QemuLogError("private log descriptor is unavailable") from exc
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise QemuLogError(
                "open private log ownership or permissions are unsafe"
            )
        entry = self._entry_stat(self.path.name)
        if (
            entry is None
            or entry.st_dev != details.st_dev
            or entry.st_ino != details.st_ino
        ):
            raise QemuLogError(
                "open private log no longer owns its path"
            )
        return details

    def _open_new(self) -> None:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.path.name,
                flags,
                0o600,
                dir_fd=self._directory_fd,
            )
            os.fchmod(descriptor, 0o600)
            details = os.fstat(descriptor)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise QemuLogError("private log cannot be created") from exc
        assert descriptor is not None
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size != 0
        ):
            os.close(descriptor)
            raise QemuLogError("created private log failed validation")
        self._descriptor = descriptor
        self._size = 0
        try:
            self._validate_open_descriptor()
            os.fsync(descriptor)
            os.fsync(self._directory_fd)
        except BaseException:
            self._descriptor = None
            os.close(descriptor)
            raise

    def _rotate(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            try:
                self._descriptor = descriptor
                self._validate_open_descriptor()
                os.fsync(descriptor)
            finally:
                self._descriptor = None
                os.close(descriptor)
        changed = False
        oldest = self._entry_name(self.backup_count)
        if self._entry_exists(oldest):
            try:
                os.unlink(oldest, dir_fd=self._directory_fd)
                changed = True
            except OSError as exc:
                raise QemuLogError("oldest log cannot be retired") from exc
        for generation in range(self.backup_count - 1, -1, -1):
            source = self._entry_name(generation)
            if not self._entry_exists(source):
                continue
            destination = self._entry_name(generation + 1)
            if self._entry_exists(destination):
                raise QemuLogError("log rotation destination is not empty")
            try:
                os.rename(
                    source,
                    destination,
                    src_dir_fd=self._directory_fd,
                    dst_dir_fd=self._directory_fd,
                )
                changed = True
            except OSError as exc:
                raise QemuLogError("private log rotation failed") from exc
            self._entry_stat(destination)
        if changed:
            try:
                os.fsync(self._directory_fd)
            except OSError as exc:
                raise QemuLogError(
                    "private log rotation could not be made durable"
                ) from exc
        self._size = 0

    def _write_all(self, payload: bytes) -> None:
        if not payload:
            return
        if len(payload) > self.max_bytes:
            raise QemuLogError("one redacted log record exceeds max_bytes")
        if self._descriptor is None:
            raise QemuLogError("private log is not open")
        if self._size and self._size + len(payload) > self.max_bytes:
            self._rotate()
            self._open_new()
        self._validate_open_descriptor()
        view = memoryview(payload)
        while view:
            try:
                written = os.write(self._descriptor, view)
            except OSError as exc:
                raise QemuLogError("private log write failed") from exc
            if written < 1:
                raise QemuLogError("private log write made no progress")
            view = view[written:]
            self._size += written
        details = self._validate_open_descriptor()
        if details.st_size != self._size:
            raise QemuLogError("private log size changed outside its owner")

    def _redact(self, payload: bytes) -> bytes:
        redacted = payload
        for secret in self._secrets:
            redacted = redacted.replace(secret, _REDACTED)
        redacted = self._redact_assignments(redacted)
        redacted = _AUTHORIZATION.sub(
            lambda match: match.group(1) + _REDACTED,
            redacted,
        )
        redacted = _URI_USERINFO.sub(
            lambda match: match.group(1) + _REDACTED + match.group(3),
            redacted,
        )
        return redacted

    @staticmethod
    def _redact_assignments(payload: bytes) -> bytes:
        """Conservatively remove assignment values, including quoted values."""

        output = bytearray()
        cursor = 0
        while True:
            match = _ASSIGNMENT_PREFIX.search(payload, cursor)
            if match is None:
                output.extend(payload[cursor:])
                return bytes(output)
            output.extend(payload[cursor:match.end()])
            value_start = match.end()
            if value_start >= len(payload):
                output.extend(_REDACTED)
                return bytes(output)
            quote = payload[value_start]
            if quote in {ord('"'), ord("'")}:
                value_end = value_start + 1
                escaped = False
                while value_end < len(payload):
                    character = payload[value_end]
                    value_end += 1
                    if escaped:
                        escaped = False
                    elif character == ord("\\"):
                        escaped = True
                    elif character == quote:
                        break
                else:
                    value_end = len(payload)
            else:
                value_end = value_start
                while (
                    value_end < len(payload)
                    and payload[value_end] not in b",;\r\n"
                ):
                    value_end += 1
            output.extend(_REDACTED)
            cursor = value_end

    def _persist_record(self, payload: bytes) -> None:
        redacted = self._redact(payload)
        if len(redacted) > self.max_bytes:
            redacted = _OVERSIZED
        self._write_all(redacted)

    def feed(self, payload: bytes) -> None:
        """Consume one bounded raw stream chunk and persist only redacted lines."""

        if self._closed:
            raise QemuLogError("cannot feed a closed log")
        if not isinstance(payload, bytes) or len(payload) > MAX_FEED_BYTES:
            raise QemuLogError("log feed payload is invalid")
        if not payload:
            return
        cursor = 0
        while cursor < len(payload):
            newline = payload.find(b"\n", cursor)
            terminal = len(payload) if newline < 0 else newline + 1
            segment = payload[cursor:terminal]
            cursor = terminal
            if self._dropping_oversized_line:
                if newline >= 0:
                    self._dropping_oversized_line = False
                continue
            self._pending.extend(segment)
            if len(self._pending) > MAX_LINE_BYTES:
                self._pending.clear()
                self._write_all(_OVERSIZED)
                self._dropping_oversized_line = newline < 0
                continue
            if newline >= 0:
                self._persist_record(bytes(self._pending))
                self._pending.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: BaseException | None = None
        try:
            if self._pending and not self._dropping_oversized_line:
                self._persist_record(bytes(self._pending))
        except BaseException as exc:
            failure = exc
        finally:
            descriptor = self._descriptor
            self._descriptor = None
            if descriptor is not None:
                try:
                    self._descriptor = descriptor
                    self._validate_open_descriptor()
                    os.fsync(descriptor)
                except BaseException as exc:
                    if failure is None:
                        failure = exc
                finally:
                    self._descriptor = None
                    os.close(descriptor)
            try:
                os.fsync(self._directory_fd)
            except BaseException as exc:
                if failure is None:
                    failure = exc
            finally:
                try:
                    self._lease.release()
                finally:
                    os.close(self._directory_fd)
        if failure is not None:
            raise failure

    def __enter__(self) -> "RedactedRotatingLog":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "MAX_BACKUP_COUNT",
    "MAX_LOG_BYTES",
    "MIN_LOG_BYTES",
    "QemuLogError",
    "RedactedRotatingLog",
    "prepare_private_directory",
]
