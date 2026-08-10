"""Internal P4 QEMU runtime ownership and restart reconciliation.

This module is deliberately not a public control operation.  It composes the
validated P3 disk owner, the non-mutating QEMU planner, two parent-death
guardians, exact Linux process identity, QMP identity, the append-only runtime
journal, and protocol lifecycle records under the single registry writer.

The launch path has only two durable outcomes:

* a fully evidenced ``QMP_RUNNING`` runtime whose child and log guardian can be
  re-adopted after daemon restart; or
* a proved cleanup/failure record after exact pidfd-bound termination.

An ambiguous process, pathname replacement, pipe topology, or QMP peer never
becomes success and is never signalled or unlinked by numeric identity alone.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import select
import socket
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4, uuid5

from somnus_protocol.vm import (
    ObservationSource,
    RuntimeObservation,
    TransitionCause,
    TransitionEvidence,
    VMRecord,
    VMState,
)

from somnus_vm.config import HostConfiguration

from .qemu import (
    QEMU_BASE_FDSET_PATH,
    QEMU_OVERLAY_FDSET_PATH,
    QemuCommandBuilder,
    QemuLaunchPlan,
    bind_block_fds,
)
from .qemu_exec_guard import READY as QEMU_GUARD_READY
from .qemu_exec_guard import RELEASE as QEMU_GUARD_RELEASE
from .qemu_log_guard import READY as LOG_GUARD_READY
from .qemu_log_guard import RELEASE as LOG_GUARD_RELEASE
from .qemu_log_guard import RELEASED as LOG_GUARD_RELEASED
from .qemu_logs import prepare_private_directory
from .launch_authority import (
    DisposableLaunchPermit,
    LaunchAuthorityError,
)
from .qemu_process import (
    ExecutableIdentity,
    ObservedProcessIdentity,
    PidfileIdentity,
    ProcessIdentityMismatchError,
    ProcessUnavailableError,
    QemuProcessError,
    TerminationEvidence,
    inspect_executable,
    normalize_argv,
    observe_pidfile,
    observe_process,
    open_pinned_executable,
    prove_process_alive,
    revalidate_process,
    resolve_executable,
    terminate_owned_process,
)
from .qemu_runtime_journal import (
    RUNTIME_LAUNCH_KIND,
    RuntimeLaunchIntent,
    RuntimeLaunchJournal,
    RuntimeLaunchJournalError,
    canonical_evidence_sha256,
)
from .qmp import (
    QMPClient,
    QMPError,
    QMPEvent,
    QMPGreeting,
    QMPResponse,
    QMPSocketIdentity,
    QMPUnavailable,
)
from .qmp_identity import (
    QMPBlockContract,
    QMPBlockLayer,
    QMPBlockstatsNodeIdentity,
    QMPDiskRuntimeEvidence,
    QMPFdsetIdentity,
    QMPIdentityEvidence,
    QMPIdentityExpectation,
    QMPIdentitySample,
    QMPNamedBlockNodeIdentity,
    compose_qmp_identity_evidence,
    extract_qmp_disk_runtime_evidence,
    greeting_sha256,
    normalize_qmp_blockstats,
    normalize_qmp_fdsets,
    normalize_qmp_identity_sample,
    normalize_qmp_named_block_nodes,
    normalize_qmp_status,
)
from .registry import (
    DiskRecord,
    OperationRecord,
    RegistryMode,
    SQLiteRegistry,
)


_MODULE_LOG_GUARD: Final[str] = "somnus_vm.host.qemu_log_guard"
_MODULE_QEMU_GUARD: Final[str] = "somnus_vm.host.qemu_exec_guard"
_ACTIVE_VM_STATES: Final[frozenset[VMState]] = frozenset(
    {
        VMState.BOOTING,
        VMState.QMP_RUNNING,
        VMState.GUEST_READY,
        VMState.QUIESCING,
        VMState.QUIESCED,
        VMState.SNAPSHOTTING,
        VMState.ROLLING_BACK,
        VMState.RECONCILING,
        VMState.RECOVERING,
        VMState.STOPPING,
    }
)
_RUNTIME_FAILURE_CODE: Final[str] = "qemu.launch_recovery"
_PIPE_ACCESS_READ: Final[int] = os.O_RDONLY
_PIPE_ACCESS_WRITE: Final[int] = os.O_WRONLY
_RENAME_NOREPLACE: Final[int] = 1


class QemuRuntimeError(RuntimeError):
    """Base class for fail-closed internal QEMU runtime ownership failures."""


class QemuRuntimeRecoveryRequired(QemuRuntimeError):
    """Exact cleanup or adoption could not be proved."""


@dataclass(frozen=True, slots=True)
class QemuRuntimePolicy:
    """Bounded timing and log-retention policy for the internal runtime owner."""

    guard_timeout_seconds: float = 5.0
    qmp_timeout_seconds: float = 5.0
    stabilization_seconds: float = 0.5
    retry_interval_seconds: float = 0.025
    terminate_grace_seconds: float = 3.0
    terminate_kill_seconds: float = 3.0
    guardian_exit_seconds: float = 3.0
    log_max_bytes: int = 16 * 1024 * 1024
    log_backup_count: int = 4

    def __post_init__(self) -> None:
        for value, label, minimum, maximum in (
            (
                self.guard_timeout_seconds,
                "guard_timeout_seconds",
                0.05,
                60.0,
            ),
            (
                self.qmp_timeout_seconds,
                "qmp_timeout_seconds",
                0.05,
                60.0,
            ),
            (
                self.stabilization_seconds,
                "stabilization_seconds",
                0.5,
                60.0,
            ),
            (
                self.retry_interval_seconds,
                "retry_interval_seconds",
                0.001,
                1.0,
            ),
            (
                self.terminate_grace_seconds,
                "terminate_grace_seconds",
                0.0,
                60.0,
            ),
            (
                self.terminate_kill_seconds,
                "terminate_kill_seconds",
                0.0,
                60.0,
            ),
            (
                self.guardian_exit_seconds,
                "guardian_exit_seconds",
                0.0,
                60.0,
            ),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not minimum <= float(value) <= maximum
            ):
                raise QemuRuntimeError(f"{label} is outside the supported bound")
        if (
            not isinstance(self.log_max_bytes, int)
            or isinstance(self.log_max_bytes, bool)
            or not 4_096 <= self.log_max_bytes <= 1024**3
        ):
            raise QemuRuntimeError("log_max_bytes is outside the supported bound")
        if (
            not isinstance(self.log_backup_count, int)
            or isinstance(self.log_backup_count, bool)
            or not 1 <= self.log_backup_count <= 32
        ):
            raise QemuRuntimeError(
                "log_backup_count is outside the supported bound"
            )


@dataclass(frozen=True, slots=True)
class RuntimeLaunchResult:
    """Direct consumed-boundary evidence for one completed internal launch."""

    operation_id: UUID
    vm_id: UUID
    generation: int
    boot_id: UUID
    process: ObservedProcessIdentity
    log_guard: ObservedProcessIdentity
    qmp_evidence: QMPIdentityEvidence
    storage_graph_evidence: "_QMPStorageGraphEvidence"
    disk_evidence: QMPDiskRuntimeEvidence
    qmp_greeting: QMPGreeting
    qmp_responses: tuple[QMPResponse, ...]
    serial_log_path: Path
    qemu_log_path: Path
    events: tuple[QMPEvent, ...]

    def to_dict(self) -> dict[str, object]:
        """Return one complete canonicalizable launch-result projection.

        Normalized QMP evidence proves the semantic contract, while the
        greeting and ordered successful-response history retain the actual
        patch-level response shapes needed by the physical gate.  Paths remain
        evidence locations; their bytes and identities are sealed separately
        by the gate coordinator after the owning daemon releases them.
        """

        return {
            "operation_id": str(self.operation_id),
            "vm_id": str(self.vm_id),
            "generation": self.generation,
            "boot_id": str(self.boot_id),
            "process": self.process.to_dict(),
            "log_guard": self.log_guard.to_dict(),
            "qmp_evidence": self.qmp_evidence.to_dict(),
            "storage_graph_evidence": self.storage_graph_evidence.to_dict(),
            "disk_evidence": self.disk_evidence.to_dict(),
            "qmp_greeting": self.qmp_greeting.to_dict(),
            "qmp_responses": [
                response.to_dict() for response in self.qmp_responses
            ],
            "serial_log_path": os.fspath(self.serial_log_path),
            "qemu_log_path": os.fspath(self.qemu_log_path),
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryResult:
    """One durable restart decision for a previously journalled launch."""

    operation_id: UUID
    vm_id: UUID
    latest_checkpoint: str | None
    disposition: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact durable restart decision for gate evidence."""

        return {
            "operation_id": str(self.operation_id),
            "vm_id": str(self.vm_id),
            "latest_checkpoint": self.latest_checkpoint,
            "disposition": self.disposition,
        }


@dataclass(slots=True)
class _LiveRuntime:
    journal: RuntimeLaunchJournal
    process: ObservedProcessIdentity
    log_guard: ObservedProcessIdentity
    qmp: QMPClient
    qmp_evidence: QMPIdentityEvidence
    storage_graph_evidence: "_QMPStorageGraphEvidence"
    disk_evidence: QMPDiskRuntimeEvidence
    events: tuple[QMPEvent, ...]


@dataclass(slots=True)
class _RuntimeProbe:
    process: ObservedProcessIdentity
    log_guard: ObservedProcessIdentity
    pidfile: PidfileIdentity
    socket_identity: QMPSocketIdentity
    qmp: QMPClient
    qmp_evidence: QMPIdentityEvidence
    storage_graph_evidence: "_QMPStorageGraphEvidence"
    disk_evidence: QMPDiskRuntimeEvidence
    events: tuple[QMPEvent, ...]


@dataclass(frozen=True, slots=True)
class _LiveDiskFileEvidence:
    """QEMU-safe host observation of immutable live-overlay authority."""

    device_id: int
    inode: int
    owner_uid: int
    mode: int
    link_count: int
    file_size_bytes: int
    allocated_size_bytes: int
    marker_device_id: int
    marker_inode: int
    marker_owner_uid: int
    marker_mode: int
    marker_link_count: int
    marker_sha256: str

    @property
    def immutable_identity(self) -> tuple[object, ...]:
        return (
            self.device_id,
            self.inode,
            self.owner_uid,
            self.mode,
            self.link_count,
            self.marker_device_id,
            self.marker_inode,
            self.marker_owner_uid,
            self.marker_mode,
            self.marker_link_count,
            self.marker_sha256,
        )


@dataclass(frozen=True, slots=True)
class _QMPPeerStorageDescriptor:
    """One QEMU-internal fdset descriptor bound back to a P3 inode."""

    role: str
    fd: int
    device_id: int
    inode: int
    access_mode: str

    def to_dict(self) -> dict[str, object]:
        return {
            "access_mode": self.access_mode,
            "device_id": self.device_id,
            "fd": self.fd,
            "inode": self.inode,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class _QMPStorageGraphEvidence:
    """Normalized fdset, block-node, edge, and peer-/proc machine truth."""

    peer_pid: int
    fdsets: tuple[QMPFdsetIdentity, ...]
    named_nodes: tuple[QMPNamedBlockNodeIdentity, ...]
    blockstats: tuple[QMPBlockstatsNodeIdentity, ...]
    peer_descriptors: tuple[_QMPPeerStorageDescriptor, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "somnus.qmp-storage-graph-evidence.v1",
            "peer_pid": self.peer_pid,
            "fdsets": [
                {
                    "fd": item.fd,
                    "fdset_id": item.fdset_id,
                    "opaque": item.opaque,
                }
                for item in self.fdsets
            ],
            "named_nodes": [
                {
                    "driver": item.driver,
                    "node_name": item.node_name,
                    "reported_file": item.reported_file,
                    "read_only": item.read_only,
                }
                for item in self.named_nodes
            ],
            "blockstats": [
                {
                    "backing_node": item.backing_node,
                    "node_name": item.node_name,
                    "parent_node": item.parent_node,
                }
                for item in self.blockstats
            ],
            "peer_descriptors": [
                item.to_dict() for item in self.peer_descriptors
            ],
        }

    @property
    def evidence_sha256(self) -> str:
        return canonical_evidence_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class _PinnedStorageFile:
    """One open storage authority carried unchanged into the exec guard."""

    role: str
    descriptor: int
    stat_result: os.stat_result
    access_mode: str
    content_sha256: str | None

    def __post_init__(self) -> None:
        if self.role not in {"overlay", "base", "owner-marker"}:
            raise QemuRuntimeError("pinned storage role is invalid")
        if (
            not isinstance(self.descriptor, int)
            or isinstance(self.descriptor, bool)
            or self.descriptor < 3
        ):
            raise QemuRuntimeError("pinned storage descriptor is invalid")
        if self.access_mode not in {"r", "rw"}:
            raise QemuRuntimeError("pinned storage access mode is invalid")
        if self.role == "overlay":
            if self.access_mode != "rw" or self.content_sha256 is not None:
                raise QemuRuntimeError("pinned overlay authority is incoherent")
        elif (
            self.access_mode != "r"
            or not isinstance(self.content_sha256, str)
            or len(self.content_sha256) != 64
        ):
            raise QemuRuntimeError(
                "pinned immutable storage authority lacks a digest"
            )

    def guard_argv(self) -> tuple[str, ...]:
        """Serialize the exact descriptor authority for the exec guard."""

        details = self.stat_result
        blocks = getattr(details, "st_blocks", None)
        if not isinstance(blocks, int) or blocks < 0:
            raise QemuRuntimeRecoveryRequired(
                f"{self.role} allocation cannot be serialized"
            )
        return (
            self.role,
            str(self.descriptor),
            str(details.st_dev),
            str(details.st_ino),
            str(details.st_uid),
            str(details.st_gid),
            format(stat.S_IMODE(details.st_mode), "04o"),
            str(details.st_nlink),
            str(details.st_size),
            str(blocks),
            str(details.st_mtime_ns),
            str(details.st_ctime_ns),
            self.access_mode,
            "-" if self.content_sha256 is None else self.content_sha256,
        )

    def to_checkpoint_dict(self) -> dict[str, object]:
        """Return durable non-secret authority facts for launch replay."""

        details = self.stat_result
        return {
            "role": self.role,
            "descriptor": self.descriptor,
            "device_id": details.st_dev,
            "inode": details.st_ino,
            "owner_uid": details.st_uid,
            "owner_gid": details.st_gid,
            "mode": stat.S_IMODE(details.st_mode),
            "link_count": details.st_nlink,
            "size_bytes": details.st_size,
            "allocated_size_bytes": int(details.st_blocks) * 512,
            "mtime_ns": details.st_mtime_ns,
            "ctime_ns": details.st_ctime_ns,
            "access_mode": self.access_mode,
            "content_sha256": self.content_sha256,
        }


@dataclass(slots=True)
class _PinnedRuntimeStorage:
    """Overlay, immutable base, and owner marker pinned as one launch unit."""

    overlay: _PinnedStorageFile
    base: _PinnedStorageFile
    marker: _PinnedStorageFile
    _closed: bool = False

    @property
    def descriptors(self) -> tuple[int, int, int]:
        """Return overlay, base, and marker descriptors in guard role order."""

        return (
            self.overlay.descriptor,
            self.base.descriptor,
            self.marker.descriptor,
        )

    def close(self) -> None:
        """Close every parent-held descriptor exactly once."""

        if self._closed:
            return
        for item in (self.overlay, self.base, self.marker):
            try:
                os.close(item.descriptor)
            except OSError:
                pass
        self._closed = True


@dataclass(frozen=True, slots=True)
class _ExitProof:
    """Exact process identity plus the first durable absence observation."""

    process: ObservedProcessIdentity
    observed_absent_at: datetime
    termination: TerminationEvidence | None


@dataclass(frozen=True, slots=True)
class _PipeEntry:
    fd: int
    identity: tuple[int, int]
    access_mode: int


CheckpointObserver = Callable[[UUID, str], None]


def _utc_now_after(floor: datetime) -> datetime:
    now = datetime.now(timezone.utc)
    return now if now > floor else floor + timedelta(microseconds=1)


def _strict_uuid(value: UUID | str, label: str) -> UUID:
    try:
        selected = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise QemuRuntimeError(f"{label} is invalid") from exc
    if selected.int == 0:
        raise QemuRuntimeError(f"{label} cannot be nil")
    return selected


def _stable_storage_metadata(details: os.stat_result) -> tuple[int, ...]:
    """Return the complete metadata fence used around descriptor hashing."""

    blocks = getattr(details, "st_blocks", None)
    if not isinstance(blocks, int) or blocks < 0:
        raise QemuRuntimeRecoveryRequired(
            "pinned storage allocation metadata is unavailable"
        )
    return (
        details.st_dev,
        details.st_ino,
        details.st_uid,
        details.st_gid,
        details.st_mode,
        details.st_nlink,
        details.st_size,
        blocks,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _sha256_pinned_descriptor(
    descriptor: int,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[os.stat_result, str]:
    """Hash one pinned regular file without changing its shared file offset."""

    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size < 1
        or before.st_size > maximum_bytes
    ):
        raise QemuRuntimeRecoveryRequired(
            f"{label} size or file type is outside the launch authority"
        )
    digest = sha256()
    offset = 0
    while offset < before.st_size:
        try:
            block = os.pread(
                descriptor,
                min(1024 * 1024, before.st_size - offset),
                offset,
            )
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                f"{label} cannot be hashed from its pinned descriptor"
            ) from exc
        if not block:
            raise QemuRuntimeRecoveryRequired(
                f"{label} ended before its observed size"
            )
        digest.update(block)
        offset += len(block)
    after = os.fstat(descriptor)
    if (
        offset != before.st_size
        or _stable_storage_metadata(before)
        != _stable_storage_metadata(after)
    ):
        raise QemuRuntimeRecoveryRequired(
            f"{label} changed while its pinned descriptor was hashed"
        )
    return after, digest.hexdigest()


def _open_pinned_storage_path(
    path: Path,
    *,
    label: str,
    writable: bool,
) -> tuple[int, os.stat_result]:
    """Open one canonical storage path and bind its directory entry to the FD."""

    flags = (
        (os.O_RDWR if writable else os.O_RDONLY)
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise QemuRuntimeRecoveryRequired(
            f"{label} cannot be opened as a pinned descriptor"
        ) from exc
    try:
        details = os.fstat(descriptor)
        named = path.lstat()
        access = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
        expected_access = os.O_RDWR if writable else os.O_RDONLY
        if (
            descriptor < 3
            or not stat.S_ISREG(details.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or details.st_dev != named.st_dev
            or details.st_ino != named.st_ino
            or access != expected_access
        ):
            raise QemuRuntimeRecoveryRequired(
                f"{label} pathname, file type, or access mode is unsafe"
            )
        _stable_storage_metadata(details)
        return descriptor, details
    except BaseException:
        os.close(descriptor)
        raise


def _guard_environment(runtime_home: Path) -> dict[str, str]:
    return {
        "HOME": os.fspath(runtime_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": os.fspath(runtime_home),
        "TZ": "UTC",
        "XDG_RUNTIME_DIR": os.fspath(runtime_home),
    }


def _directory_facts(runtime_root: Path, log_root: Path) -> dict[str, object]:
    runtime = runtime_root.lstat()
    logs = log_root.lstat()
    return {
        "owner_uid": os.geteuid(),
        "runtime_root_device_id": runtime.st_dev,
        "runtime_root_inode": runtime.st_ino,
        "runtime_root_mode": stat.S_IMODE(runtime.st_mode),
        "log_root_device_id": logs.st_dev,
        "log_root_inode": logs.st_ino,
        "log_root_mode": stat.S_IMODE(logs.st_mode),
    }


def _require_absent(path: Path, label: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise QemuRuntimeError(f"{label} cannot be inspected") from exc
    raise QemuRuntimeRecoveryRequired(
        f"{label} already exists without current runtime ownership"
    )


def _rename_noreplace(
    directory_fd: int,
    source_name: str,
    destination_name: str,
) -> None:
    """Atomically capture one directory entry without overwriting another."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise QemuRuntimeRecoveryRequired(
            "renameat2 is required for no-replace runtime cleanup"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        directory_fd,
        os.fsencode(destination_name),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    code = ctypes.get_errno()
    if code == errno.ENOENT:
        raise FileNotFoundError(source_name)
    if code == errno.EEXIST:
        raise FileExistsError(destination_name)
    raise OSError(code, os.strerror(code), source_name)


def _receive_exact(
    control: socket.socket,
    expected: bytes,
    *,
    timeout_seconds: float,
    label: str,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    payload = bytearray()
    while len(payload) < len(expected):
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise QemuRuntimeError(f"{label} timed out")
        readable, _, exceptional = select.select(
            (control,),
            (),
            (control,),
            remaining,
        )
        if exceptional:
            raise QemuRuntimeError(f"{label} control channel failed")
        if not readable:
            raise QemuRuntimeError(f"{label} timed out")
        try:
            block = control.recv(len(expected) - len(payload))
        except OSError as exc:
            raise QemuRuntimeError(f"{label} receive failed") from exc
        if not block:
            raise QemuRuntimeError(f"{label} ended before acknowledgement")
        payload.extend(block)
    if bytes(payload) != expected:
        raise QemuRuntimeError(f"{label} acknowledgement is invalid")
    try:
        extra = control.recv(1, socket.MSG_DONTWAIT | socket.MSG_PEEK)
    except BlockingIOError:
        extra = b""
    except OSError as exc:
        raise QemuRuntimeError(f"{label} control revalidation failed") from exc
    if extra:
        raise QemuRuntimeError(f"{label} acknowledgement has trailing bytes")


def _send_exact(control: socket.socket, payload: bytes, label: str) -> None:
    try:
        control.sendall(payload)
    except OSError as exc:
        raise QemuRuntimeError(f"{label} release failed") from exc


def _wait_for_target_exec(
    process: subprocess.Popen[bytes],
    guard: ObservedProcessIdentity,
    executable: ExecutableIdentity,
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
    retry_seconds: float,
) -> ObservedProcessIdentity:
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise QemuRuntimeError(
                "QEMU exec guard exited before the target was observed"
            )
        try:
            observed = observe_process(
                guard.pid,
                expected_executable_identity=executable,
                expected_argv=argv,
            )
            if observed.start_time_ticks != guard.start_time_ticks:
                raise QemuRuntimeError(
                    "QEMU exec changed the guarded process start time"
                )
            return observed
        except (ProcessIdentityMismatchError, ProcessUnavailableError) as exc:
            last_error = exc
            time.sleep(retry_seconds)
    raise QemuRuntimeError(
        "QEMU target did not replace the durable exec guard before deadline"
    ) from last_error


def _caused_by_missing_path(error: BaseException) -> bool:
    """Return whether one typed failure is rooted in an absent path."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, FileNotFoundError):
            return True
        if isinstance(current, OSError) and current.errno == errno.ENOENT:
            return True
        current = current.__cause__ or current.__context__
    return False


def _wait_for_pidfile(
    process: ObservedProcessIdentity,
    path: str | os.PathLike[str],
    *,
    expected_uid: int,
    timeout_seconds: float,
    retry_seconds: float,
) -> PidfileIdentity:
    """Wait only for absent/incomplete pidfile bytes under exact process proof.

    Identity, ownership, mode, link, pathname, and process mismatches remain
    immediate failures.  The only retryable states are a path that QEMU has
    not created yet or a safely observed file whose payload is not complete.
    """

    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while True:
        revalidate_process(process)
        try:
            return observe_pidfile(
                process,
                path,
                expected_uid=expected_uid,
            )
        except ProcessIdentityMismatchError:
            raise
        except QemuProcessError as exc:
            if (
                not _caused_by_missing_path(exc)
                and str(exc) != "pidfile payload is invalid"
            ):
                raise
            last_error = exc

        revalidate_process(process)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(retry_seconds, remaining))
    raise QemuRuntimeError(
        "QEMU pidfile was not ready before deadline"
    ) from last_error


def _open_qmp(
    socket_path: Path,
    process: ObservedProcessIdentity,
    policy: QemuRuntimePolicy,
) -> tuple[QMPClient, QMPGreeting]:
    deadline = time.monotonic() + policy.qmp_timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        revalidate_process(process)
        client = QMPClient(
            socket_path,
            timeout_seconds=policy.qmp_timeout_seconds,
            allowed_server_uids={process.process_owner_uid},
        )
        remaining = deadline - time.monotonic()
        try:
            greeting = client.connect(
                timeout_seconds=max(0.001, min(remaining, 1.0))
            )
        except QMPUnavailable as exc:
            client.close()
            last_error = exc
            time.sleep(policy.retry_interval_seconds)
            continue
        except QMPError:
            client.close()
            raise
        peer = client.peer_credentials
        if peer.pid != process.pid or peer.uid != process.process_owner_uid:
            client.close()
            raise QemuRuntimeError(
                "QMP peer credentials do not match the observed QEMU"
            )
        revalidate_process(process)
        return client, greeting
    raise QemuRuntimeError("QMP endpoint did not become available") from last_error


def _observe_qmp_storage_graph(
    client: QMPClient,
    process: ObservedProcessIdentity,
    contract: QMPBlockContract,
) -> _QMPStorageGraphEvidence:
    """Bind QEMU's fdsets and four-node graph to the P3-owned inodes."""

    revalidate_process(process)
    if (
        client.peer_credentials.pid != process.pid
        or len(contract.layers) != 2
    ):
        raise QemuRuntimeRecoveryRequired(
            "QMP storage graph is not bound to the expected QEMU and chain"
        )
    fdsets = normalize_qmp_fdsets(client.query_fdsets())
    named_nodes = normalize_qmp_named_block_nodes(
        client.query_named_block_nodes()
    )
    blockstats = normalize_qmp_blockstats(client.query_blockstats())
    peer_descriptors: list[_QMPPeerStorageDescriptor] = []
    for fdset, layer, role, expected_access in (
        (fdsets[0], contract.layers[0], "overlay", os.O_RDWR),
        (fdsets[1], contract.layers[1], "base", os.O_RDONLY),
    ):
        fd_path = Path("/proc") / str(process.pid) / "fd" / str(fdset.fd)
        try:
            details = fd_path.stat()
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "QMP fdset descriptor cannot be bound through the peer process"
            ) from exc
        flags = _parse_fd_flags(
            Path("/proc")
            / str(process.pid)
            / "fdinfo"
            / str(fdset.fd)
        )
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != layer.device_id
            or details.st_ino != layer.inode
            or flags & os.O_ACCMODE != expected_access
        ):
            raise QemuRuntimeRecoveryRequired(
                "QMP fdset descriptor is not the P3-owned storage authority"
            )
        peer_descriptors.append(
            _QMPPeerStorageDescriptor(
                role=role,
                fd=fdset.fd,
                device_id=details.st_dev,
                inode=details.st_ino,
                access_mode="rw" if expected_access == os.O_RDWR else "r",
            )
        )
    revalidate_process(process)
    return _QMPStorageGraphEvidence(
        peer_pid=process.pid,
        fdsets=fdsets,
        named_nodes=named_nodes,
        blockstats=blockstats,
        peer_descriptors=tuple(peer_descriptors),
    )


def _collect_identity_sample(
    client: QMPClient,
    greeting: QMPGreeting,
    expectation: QMPIdentityExpectation,
) -> tuple[
    QMPIdentitySample,
    QMPResponse,
]:
    status = client.query_status()
    name = client.query_name()
    uuid_response = client.query_uuid()
    block = client.query_block()
    cpus = client.query_cpus_fast()
    return (
        normalize_qmp_identity_sample(
            expectation=expectation,
            greeting=greeting,
            peer_pid=client.peer_credentials.pid,
            status_response=status,
            name_response=name,
            uuid_response=uuid_response,
            block_response=block,
            cpus_response=cpus,
            monotonic_ns=time.monotonic_ns(),
        ),
        block,
    )


def _dangerous_events(events: tuple[QMPEvent, ...]) -> tuple[QMPEvent, ...]:
    # P4 publishes one launch identity, not an event-state interpreter.  Any
    # asynchronous state/device event inside the identity window can make the
    # two samples or the final status fence stale.  Later phases may introduce
    # an explicit event transition table; until then, every event invalidates
    # publication rather than being silently classified as harmless.
    return tuple(events)


def _stabilize_identity(
    client: QMPClient,
    greeting: QMPGreeting,
    expectation: QMPIdentityExpectation,
    process: ObservedProcessIdentity,
    observation_id: UUID,
    policy: QemuRuntimePolicy,
) -> tuple[
    QMPIdentityEvidence,
    QMPDiskRuntimeEvidence,
    _QMPStorageGraphEvidence,
    tuple[QMPEvent, ...],
]:
    events: list[QMPEvent] = list(client.drain_events())
    if _dangerous_events(tuple(events)):
        raise QemuRuntimeError(
            "QMP emitted an event before identity stabilization"
        )
    first_graph = _observe_qmp_storage_graph(
        client,
        process,
        expectation.block,
    )
    first, _ = _collect_identity_sample(client, greeting, expectation)
    deadline = time.monotonic() + policy.stabilization_seconds
    while time.monotonic() < deadline:
        revalidate_process(process)
        observed = client.drain_events()
        events.extend(observed)
        if _dangerous_events(observed):
            raise QemuRuntimeError(
                "QMP emitted an event during identity stabilization"
            )
        remaining = deadline - time.monotonic()
        if remaining > 0.0:
            time.sleep(min(policy.retry_interval_seconds, remaining))
    second, block_response = _collect_identity_sample(
        client,
        greeting,
        expectation,
    )
    second_graph = _observe_qmp_storage_graph(
        client,
        process,
        expectation.block,
    )
    if first_graph != second_graph:
        raise QemuRuntimeRecoveryRequired(
            "QMP fdset or block graph changed during identity stabilization"
        )
    evidence = compose_qmp_identity_evidence(
        observation_id=observation_id,
        vm_id=expectation.vm_id,
        first=first,
        second=second,
    )
    disk_evidence = extract_qmp_disk_runtime_evidence(
        block_response,
        expectation.block,
    )
    final_status = client.query_status()
    normalize_qmp_status(final_status)
    trailing = client.drain_events()
    events.extend(trailing)
    if _dangerous_events(trailing):
        raise QemuRuntimeError(
            "QMP emitted an event before lifecycle commit"
        )
    revalidate_process(process)
    return evidence, disk_evidence, second_graph, tuple(events)


def _parse_fd_flags(path: Path) -> int:
    try:
        payload = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise QemuRuntimeRecoveryRequired(
            "process descriptor flags cannot be observed"
        ) from exc
    flags: list[int] = []
    for line in payload.splitlines():
        if line.startswith("flags:"):
            try:
                flags.append(int(line.split(":", 1)[1].strip(), 8))
            except ValueError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "process descriptor flags are malformed"
                ) from exc
    if len(flags) != 1:
        raise QemuRuntimeRecoveryRequired(
            "process descriptor flags are incomplete"
        )
    return flags[0]


def _pipe_table(process: ObservedProcessIdentity) -> tuple[_PipeEntry, ...]:
    revalidate_process(process)
    root = Path("/proc") / str(process.pid)
    try:
        names = tuple((root / "fd").iterdir())
    except OSError as exc:
        raise QemuRuntimeRecoveryRequired(
            "process descriptor table cannot be listed"
        ) from exc
    entries: list[_PipeEntry] = []
    for path in names:
        if not path.name.isdigit():
            continue
        try:
            details = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "process descriptor cannot be observed"
            ) from exc
        if not stat.S_ISFIFO(details.st_mode):
            continue
        flags = _parse_fd_flags(root / "fdinfo" / path.name)
        entries.append(
            _PipeEntry(
                fd=int(path.name),
                identity=(details.st_dev, details.st_ino),
                access_mode=flags & os.O_ACCMODE,
            )
        )
    revalidate_process(process)
    return tuple(entries)


def _log_guard_stream_fds(
    guard: ObservedProcessIdentity,
    intent: RuntimeLaunchIntent,
    policy: QemuRuntimePolicy,
) -> tuple[int, int]:
    argv = guard.argv
    python = os.fspath(resolve_executable(argv[0]))
    expected_prefix = (python, "-I", "-m", _MODULE_LOG_GUARD)
    if argv[:4] != expected_prefix or len(argv) != 13:
        raise QemuRuntimeRecoveryRequired(
            "log guardian argv is not the runtime-owned command"
        )
    if (
        argv[4] == ""
        or argv[8] != str(Path(intent.serial_log_path).parent)
        or argv[9] != intent.serial_log_path
        or argv[10] != intent.qemu_log_path
        or argv[11] != str(policy.log_max_bytes)
        or argv[12] != str(policy.log_backup_count)
    ):
        raise QemuRuntimeRecoveryRequired(
            "log guardian argv disagrees with the launch journal"
        )
    try:
        serial_fd = int(argv[6])
        qemu_fd = int(argv[7])
    except ValueError as exc:
        raise QemuRuntimeRecoveryRequired(
            "log guardian stream descriptors are invalid"
        ) from exc
    if serial_fd < 3 or qemu_fd < 3 or serial_fd == qemu_fd:
        raise QemuRuntimeRecoveryRequired(
            "log guardian stream descriptors are ambiguous"
        )
    return serial_fd, qemu_fd


def _verify_pipe_topology(
    journal: RuntimeLaunchJournal,
    log_guard: ObservedProcessIdentity,
    qemu: ObservedProcessIdentity,
    policy: QemuRuntimePolicy,
) -> None:
    serial_identity, qemu_identity = journal.log_pipe_identities()
    serial_fd, qemu_fd = _log_guard_stream_fds(
        log_guard,
        journal.intent,
        policy,
    )
    guardian_entries = _pipe_table(log_guard)
    qemu_entries = _pipe_table(qemu)

    relevant_guardian = tuple(
        entry
        for entry in guardian_entries
        if entry.identity in {serial_identity, qemu_identity}
    )
    relevant_qemu = tuple(
        entry
        for entry in qemu_entries
        if entry.identity in {serial_identity, qemu_identity}
    )
    expected_guardian = {
        (serial_fd, serial_identity, _PIPE_ACCESS_READ),
        (qemu_fd, qemu_identity, _PIPE_ACCESS_READ),
    }
    expected_qemu = {
        (1, serial_identity, _PIPE_ACCESS_WRITE),
        (2, qemu_identity, _PIPE_ACCESS_WRITE),
    }
    observed_guardian = {
        (entry.fd, entry.identity, entry.access_mode)
        for entry in relevant_guardian
    }
    observed_qemu = {
        (entry.fd, entry.identity, entry.access_mode) for entry in relevant_qemu
    }
    if observed_guardian != expected_guardian or observed_qemu != expected_qemu:
        raise QemuRuntimeRecoveryRequired(
            "guardian/QEMU pipe topology is not the durably owned topology"
        )


def _verify_guardian_pipe_topology(
    journal: RuntimeLaunchJournal,
    log_guard: ObservedProcessIdentity,
    policy: QemuRuntimePolicy,
) -> None:
    """Prove the exact durable read endpoints before signaling a guardian."""

    serial_identity, qemu_identity = journal.log_pipe_identities()
    serial_fd, qemu_fd = _log_guard_stream_fds(
        log_guard,
        journal.intent,
        policy,
    )
    relevant = tuple(
        entry
        for entry in _pipe_table(log_guard)
        if entry.identity in {serial_identity, qemu_identity}
    )
    observed = {
        (entry.fd, entry.identity, entry.access_mode) for entry in relevant
    }
    expected = {
        (serial_fd, serial_identity, _PIPE_ACCESS_READ),
        (qemu_fd, qemu_identity, _PIPE_ACCESS_READ),
    }
    if observed != expected:
        raise QemuRuntimeRecoveryRequired(
            "log guardian pipe topology is not the durably owned topology"
        )


def _verify_qemu_guard_topology(
    journal: RuntimeLaunchJournal,
    guard: ObservedProcessIdentity,
) -> None:
    """Prove the unreleased guard, descriptor authority, and pipe wiring."""

    intent = journal.intent
    argv = guard.argv
    python = os.fspath(resolve_executable(argv[0]))
    try:
        storage = journal.pinned_storage_authority()
    except RuntimeLaunchJournalError as exc:
        raise QemuRuntimeRecoveryRequired(
            "QEMU guard has no durable storage authority"
        ) from exc
    storage_tokens: list[str] = []
    storage_fds: list[int] = []
    for item in storage:
        allocated_size = int(item["allocated_size_bytes"])
        if allocated_size % 512:
            raise QemuRuntimeRecoveryRequired(
                "durable storage allocation is not block aligned"
            )
        descriptor = int(item["descriptor"])
        storage_fds.append(descriptor)
        digest = item["content_sha256"]
        storage_tokens.extend(
            (
                str(item["role"]),
                str(descriptor),
                str(item["device_id"]),
                str(item["inode"]),
                str(item["owner_uid"]),
                str(item["owner_gid"]),
                format(int(item["mode"]), "04o"),
                str(item["link_count"]),
                str(item["size_bytes"]),
                str(allocated_size // 512),
                str(item["mtime_ns"]),
                str(item["ctime_ns"]),
                str(item["access_mode"]),
                "-" if digest is None else str(digest),
            )
        )
    target_offset = 8 + len(storage_tokens) + 1
    if (
        len(argv) != target_offset + len(intent.executed_argv)
        or argv[:4] != (python, "-I", "-m", _MODULE_QEMU_GUARD)
        or argv[6] != str(Path(intent.qmp_socket).parent)
        or tuple(argv[8 : 8 + len(storage_tokens)]) != tuple(storage_tokens)
        or argv[target_offset - 1] != "--"
        or tuple(argv[target_offset:]) != intent.executed_argv
    ):
        raise QemuRuntimeRecoveryRequired(
            "QEMU exec guard argv is not the journaled invocation"
        )
    try:
        control_fd = int(argv[5])
        executable_fd = int(argv[7])
    except ValueError as exc:
        raise QemuRuntimeRecoveryRequired(
            "QEMU exec guard descriptors are malformed"
        ) from exc
    descriptors = (control_fd, executable_fd, *storage_fds)
    if any(descriptor < 3 for descriptor in descriptors) or len(
        set(descriptors)
    ) != len(descriptors):
        raise QemuRuntimeRecoveryRequired(
            "QEMU exec guard descriptors are ambiguous"
        )

    serial_identity, qemu_identity = journal.log_pipe_identities()
    relevant = tuple(
        entry
        for entry in _pipe_table(guard)
        if entry.identity in {serial_identity, qemu_identity}
    )
    observed = {
        (entry.fd, entry.identity, entry.access_mode) for entry in relevant
    }
    expected = {
        (1, serial_identity, _PIPE_ACCESS_WRITE),
        (2, qemu_identity, _PIPE_ACCESS_WRITE),
    }
    if observed != expected:
        raise QemuRuntimeRecoveryRequired(
            "QEMU exec guard output pipes are not durably owned"
        )

    executable_path = (
        Path("/proc") / str(guard.pid) / "fd" / str(executable_fd)
    )
    try:
        details = executable_path.stat()
        target = executable_path.readlink()
    except OSError as exc:
        raise QemuRuntimeRecoveryRequired(
            "QEMU exec guard target descriptor cannot be observed"
        ) from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_dev != intent.executable.device_id
        or details.st_ino != intent.executable.inode
        or target != Path(intent.executable.path)
        or _parse_fd_flags(
            Path("/proc")
            / str(guard.pid)
            / "fdinfo"
            / str(executable_fd)
        )
        & os.O_ACCMODE
        != os.O_RDONLY
    ):
        raise QemuRuntimeRecoveryRequired(
            "QEMU exec guard target descriptor is not the pinned executable"
        )

    for item in storage:
        descriptor = int(item["descriptor"])
        descriptor_path = Path("/proc") / str(guard.pid) / "fd" / str(descriptor)
        try:
            details = descriptor_path.stat()
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "QEMU exec guard storage descriptor cannot be observed"
            ) from exc
        expected_access = (
            os.O_RDWR if item["access_mode"] == "rw" else os.O_RDONLY
        )
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != int(item["device_id"])
            or details.st_ino != int(item["inode"])
            or details.st_uid != int(item["owner_uid"])
            or details.st_gid != int(item["owner_gid"])
            or stat.S_IMODE(details.st_mode) != int(item["mode"])
            or details.st_nlink != int(item["link_count"])
            or details.st_size != int(item["size_bytes"])
            or int(details.st_blocks) * 512
            != int(item["allocated_size_bytes"])
            or details.st_mtime_ns != int(item["mtime_ns"])
            or details.st_ctime_ns != int(item["ctime_ns"])
            or _parse_fd_flags(
                Path("/proc")
                / str(guard.pid)
                / "fdinfo"
                / str(descriptor)
            )
            & os.O_ACCMODE
            != expected_access
        ):
            raise QemuRuntimeRecoveryRequired(
                "QEMU exec guard storage authority changed after checkpoint"
            )
    revalidate_process(guard)


def _same_process_core(
    left: ObservedProcessIdentity,
    right: ObservedProcessIdentity,
) -> bool:
    return (
        left.pid == right.pid
        and left.start_time_ticks == right.start_time_ticks
        and left.executable == right.executable
        and left.executable_sha256 == right.executable_sha256
        and left.command_sha256 == right.command_sha256
    )


def _checkpoint_names(journal: RuntimeLaunchJournal) -> frozenset[str]:
    return frozenset(checkpoint.name for checkpoint in journal.checkpoints)


def _journal_executable(intent: RuntimeLaunchIntent) -> ExecutableIdentity:
    value = intent.executable
    return ExecutableIdentity(
        path=Path(value.path),
        device_id=value.device_id,
        inode=value.inode,
        owner_uid=value.owner_uid,
        mode=value.mode,
        link_count=value.link_count,
        size_bytes=value.size_bytes,
        mtime_ns=value.mtime_ns,
        ctime_ns=value.ctime_ns,
        sha256=value.sha256,
    )


class QemuRuntimeOwner:
    """Single-daemon owner for internal QEMU launch, adoption, and cleanup."""

    def __init__(
        self,
        registry: SQLiteRegistry,
        configuration: HostConfiguration,
        *,
        policy: QemuRuntimePolicy | None = None,
        checkpoint_observer: CheckpointObserver | None = None,
    ) -> None:
        if not isinstance(registry, SQLiteRegistry):
            raise TypeError("registry must be SQLiteRegistry")
        if not isinstance(configuration, HostConfiguration):
            raise TypeError("configuration must be HostConfiguration")
        if checkpoint_observer is not None and not callable(checkpoint_observer):
            raise TypeError("checkpoint_observer must be callable")
        self._registry = registry
        self._configuration = configuration
        self._policy = policy or QemuRuntimePolicy()
        self._checkpoint_observer = checkpoint_observer
        self._lock = threading.RLock()
        self._live: dict[UUID, _LiveRuntime] = {}

    @property
    def live_vm_ids(self) -> frozenset[UUID]:
        with self._lock:
            return frozenset(self._live)

    def _observe_checkpoint(
        self,
        operation_id: UUID,
        checkpoint: str,
    ) -> None:
        observer = self._checkpoint_observer
        if observer is not None:
            observer(operation_id, checkpoint)

    def _append_checkpoint(
        self,
        journal: RuntimeLaunchJournal,
        name: str,
        facts: Mapping[str, object] | None = None,
    ) -> RuntimeLaunchJournal:
        updated, write = journal.advance(name, facts)
        with self._registry.write_transaction() as transaction:
            transaction.append_checkpoint(**write.append_checkpoint_kwargs())
        self._observe_checkpoint(journal.intent.operation_id, name)
        return updated

    def _complete_launch(
        self,
        journal: RuntimeLaunchJournal,
    ) -> RuntimeLaunchJournal:
        """Atomically publish the final checkpoint and completed result."""

        if journal.latest_checkpoint == "lifecycle_committed":
            updated, checkpoint = journal.advance("completion_observed")
            status = updated.completion_status_write()
            with self._registry.write_transaction() as transaction:
                transaction.append_checkpoint(
                    **checkpoint.append_checkpoint_kwargs()
                )
                transaction.set_operation_status(
                    **status.set_operation_status_kwargs()
                )
            self._observe_checkpoint(
                journal.intent.operation_id,
                "completion_observed",
            )
            return updated
        if journal.latest_checkpoint == "completion_observed":
            operation = self._require_operation(journal.intent.operation_id)
            if operation.status != "completed":
                with self._registry.write_transaction() as transaction:
                    transaction.set_operation_status(
                        **journal.completion_status_write().set_operation_status_kwargs()
                    )
            return journal
        raise QemuRuntimeRecoveryRequired(
            "runtime completion requires a committed lifecycle"
        )

    def _select_authority(
        self,
        vm_id: UUID,
    ) -> tuple[VMRecord, int, DiskRecord, QemuLaunchPlan]:
        stored = self._registry.get_vm(vm_id)
        if (
            stored is None
            or not stored.active
            or stored.record.state is not VMState.PROVISIONED
        ):
            raise QemuRuntimeError(
                "runtime launch requires one active provisioned VM"
            )
        for candidate in self._registry.list_vms(active_only=True):
            if (
                candidate.record.vm_id != vm_id
                and candidate.record.state in _ACTIVE_VM_STATES
            ):
                raise QemuRuntimeError(
                    "one-active-AIPC policy rejects another live runtime"
                )
        disks = tuple(
            disk
            for disk in self._registry.list_disks(
                vm_id=vm_id,
                active_only=True,
                materialized_only=True,
            )
            if disk.path == stored.record.definition.disk_path
            and disk.generation == stored.record.generation
        )
        if len(disks) != 1:
            raise QemuRuntimeError(
                "runtime launch requires exactly one active P3-owned overlay"
            )
        disk = disks[0]
        plan = QemuCommandBuilder(self._configuration).build(stored.record)
        return stored.record, stored.revision, disk, plan

    def _pin_runtime_storage(
        self,
        disk: DiskRecord,
    ) -> _PinnedRuntimeStorage:
        """Pin the exact P3 overlay, immutable base, and owner marker."""

        current = self._registry.get_disk(disk.disk_id)
        if current != disk:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk authority changed before descriptor pinning"
            )
        if (
            disk.base_image_id is None
            or disk.base_image_sha256 is None
            or disk.marker_sha256 is None
            or disk.device_id is None
            or disk.inode is None
            or disk.file_size_bytes is None
            or disk.allocated_size_bytes is None
        ):
            raise QemuRuntimeRecoveryRequired(
                "runtime disk lacks complete P3 descriptor authority"
            )
        base = self._registry.get_base_image(disk.base_image_id)
        if (
            base is None
            or not base.active
            or base.content_sha256 != disk.base_image_sha256
            or base.format != "qcow2"
            or base.mode != 0o444
        ):
            raise QemuRuntimeRecoveryRequired(
                "runtime base authority is unavailable or mutable"
            )

        overlay_path = Path(disk.path)
        base_path = Path(base.path)
        marker_path = Path(f"{disk.path}.owner.json")
        overlay_fd = base_fd = marker_fd = -1
        try:
            overlay_fd, overlay_details = _open_pinned_storage_path(
                overlay_path,
                label="runtime overlay",
                writable=True,
            )
            base_fd, base_details = _open_pinned_storage_path(
                base_path,
                label="immutable runtime base",
                writable=False,
            )
            marker_fd, marker_details = _open_pinned_storage_path(
                marker_path,
                label="runtime owner marker",
                writable=False,
            )
            identities = {
                (overlay_details.st_dev, overlay_details.st_ino),
                (base_details.st_dev, base_details.st_ino),
                (marker_details.st_dev, marker_details.st_ino),
            }
            if len(identities) != 3:
                raise QemuRuntimeRecoveryRequired(
                    "runtime storage descriptors alias across authority roles"
                )
            if (
                overlay_details.st_dev != disk.device_id
                or overlay_details.st_ino != disk.inode
                or overlay_details.st_uid != os.geteuid()
                or stat.S_IMODE(overlay_details.st_mode) != 0o600
                or overlay_details.st_nlink != 1
                or overlay_details.st_size != disk.file_size_bytes
                or int(overlay_details.st_blocks) * 512
                != disk.allocated_size_bytes
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime overlay descriptor disagrees with P3 ownership"
                )
            if (
                base_details.st_dev != base.device_id
                or base_details.st_ino != base.inode
                or base_details.st_uid != os.geteuid()
                or stat.S_IMODE(base_details.st_mode) != base.mode
                or base_details.st_nlink != 1
                or base_details.st_size != base.file_size_bytes
                or int(base_details.st_blocks) * 512
                != base.allocated_size_bytes
            ):
                raise QemuRuntimeRecoveryRequired(
                    "immutable base descriptor disagrees with registry authority"
                )
            if (
                marker_details.st_uid != os.geteuid()
                or stat.S_IMODE(marker_details.st_mode) != 0o600
                or marker_details.st_nlink != 1
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime owner marker descriptor is unsafe"
                )

            base_details, base_digest = _sha256_pinned_descriptor(
                base_fd,
                label="immutable runtime base",
                maximum_bytes=base.byte_size,
            )
            if base_digest != base.content_sha256:
                raise QemuRuntimeRecoveryRequired(
                    "immutable base descriptor hash disagrees with registry authority"
                )
            marker_details, marker_digest = _sha256_pinned_descriptor(
                marker_fd,
                label="runtime owner marker",
                maximum_bytes=65_536,
            )
            if marker_digest != disk.marker_sha256:
                raise QemuRuntimeRecoveryRequired(
                    "runtime owner marker hash disagrees with registry authority"
                )

            for path, details, label in (
                (overlay_path, overlay_details, "runtime overlay"),
                (base_path, base_details, "immutable runtime base"),
                (marker_path, marker_details, "runtime owner marker"),
            ):
                try:
                    named = path.lstat()
                except OSError as exc:
                    raise QemuRuntimeRecoveryRequired(
                        f"{label} pathname disappeared after descriptor pinning"
                    ) from exc
                if _stable_storage_metadata(named) != _stable_storage_metadata(
                    details
                ):
                    raise QemuRuntimeRecoveryRequired(
                        f"{label} pathname changed around descriptor pinning"
                    )
            if (
                self._registry.get_disk(disk.disk_id) != disk
                or self._registry.get_base_image(base.image_id) != base
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime storage rows changed around descriptor pinning"
                )

            return _PinnedRuntimeStorage(
                overlay=_PinnedStorageFile(
                    role="overlay",
                    descriptor=overlay_fd,
                    stat_result=overlay_details,
                    access_mode="rw",
                    content_sha256=None,
                ),
                base=_PinnedStorageFile(
                    role="base",
                    descriptor=base_fd,
                    stat_result=base_details,
                    access_mode="r",
                    content_sha256=base_digest,
                ),
                marker=_PinnedStorageFile(
                    role="owner-marker",
                    descriptor=marker_fd,
                    stat_result=marker_details,
                    access_mode="r",
                    content_sha256=marker_digest,
                ),
            )
        except BaseException:
            for descriptor in (overlay_fd, base_fd, marker_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise

    def _block_contract(
        self,
        intent: RuntimeLaunchIntent,
        disk: DiskRecord,
    ) -> QMPBlockContract:
        if disk.base_image_id is None:
            raise QemuRuntimeError("runtime disk has no registered base image")
        base = self._registry.get_base_image(disk.base_image_id)
        if (
            base is None
            or not base.active
            or disk.base_image_sha256 != base.content_sha256
            or base.format != "qcow2"
        ):
            raise QemuRuntimeError("runtime disk base image is unavailable")
        return QMPBlockContract(
            layers=(
                QMPBlockLayer(
                    path=Path(intent.disk_path),
                    format="qcow2",
                    virtual_size_bytes=int(intent.disk_virtual_size_bytes),
                    device_id=int(intent.disk_device_id),
                    inode=int(intent.disk_inode),
                    qmp_filename=QEMU_OVERLAY_FDSET_PATH,
                ),
                QMPBlockLayer(
                    path=Path(base.path),
                    format=base.format,
                    virtual_size_bytes=base.virtual_size_bytes,
                    device_id=base.device_id,
                    inode=base.inode,
                    qmp_filename=QEMU_BASE_FDSET_PATH,
                ),
            ),
            storage_chain_sha256=intent.disk_chain_sha256,
        )

    @staticmethod
    def _read_live_marker(
        marker_path: Path,
        expected_sha256: str,
    ) -> tuple[os.stat_result, str]:
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(marker_path, flags)
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk marker cannot be opened"
            ) from exc
        digest = sha256()
        total = 0
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size < 1
                or before.st_size > 65_536
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime disk marker ownership is unsafe"
                )
            while total <= 65_536:
                block = os.read(descriptor, min(65_537 - total, 65_536))
                if not block:
                    break
                digest.update(block)
                total += len(block)
            after = os.fstat(descriptor)
            if (
                total > 65_536
                or total != after.st_size
                or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime disk marker changed during observation"
                )
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk marker could not be read"
            ) from exc
        finally:
            os.close(descriptor)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk marker hash disagrees with registry authority"
            )
        return after, actual_sha256

    def _observe_live_disk(
        self,
        intent: RuntimeLaunchIntent,
        disk: DiskRecord,
    ) -> _LiveDiskFileEvidence:
        """Observe immutable live-overlay facts without invoking ``qemu-img``."""

        if (
            disk.disk_id != intent.disk_id
            or disk.vm_id != intent.vm_id
            or disk.generation != intent.generation
            or disk.path != intent.disk_path
            or disk.ownership != "managed"
            or not disk.active
            or not disk.materialized
            or disk.operation_id is None
            or disk.materialized_by_operation_id is None
            or disk.base_image_id is None
            or disk.base_image_sha256 is None
            or disk.format != "qcow2"
            or disk.device_id != intent.disk_device_id
            or disk.inode != intent.disk_inode
            or disk.virtual_size_bytes != intent.disk_virtual_size_bytes
            or disk.chain_sha256 != intent.disk_chain_sha256
            or disk.marker_sha256 is None
            or disk.qemu_img_version is None
        ):
            raise QemuRuntimeRecoveryRequired(
                "runtime disk registry authority changed"
            )
        if disk.ownership_token.int == 0:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk ownership token is invalid"
            )
        # This validates the immutable base row and produces the same block
        # contract later normalized against QMP without running qemu-img on the
        # live writable overlay.
        self._block_contract(intent, disk)

        path = Path(intent.disk_path)
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk cannot be opened for identity observation"
            ) from exc
        try:
            before = os.fstat(descriptor)
            try:
                named_before = path.lstat()
            except OSError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "runtime disk pathname cannot be observed"
                ) from exc
            if (
                not stat.S_ISREG(before.st_mode)
                or not stat.S_ISREG(named_before.st_mode)
                or before.st_dev != named_before.st_dev
                or before.st_ino != named_before.st_ino
                or before.st_dev != intent.disk_device_id
                or before.st_ino != intent.disk_inode
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime disk pathname or ownership is unsafe"
                )
            after = os.fstat(descriptor)
            try:
                named_after = path.lstat()
            except OSError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "runtime disk pathname disappeared during observation"
                ) from exc
            if (
                after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_uid != before.st_uid
                or stat.S_IMODE(after.st_mode) != stat.S_IMODE(before.st_mode)
                or after.st_nlink != before.st_nlink
                or named_after.st_dev != after.st_dev
                or named_after.st_ino != after.st_ino
                or named_after.st_uid != after.st_uid
                or stat.S_IMODE(named_after.st_mode)
                != stat.S_IMODE(after.st_mode)
                or named_after.st_nlink != after.st_nlink
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime disk identity changed during observation"
                )
        finally:
            os.close(descriptor)

        marker_path = Path(f"{intent.disk_path}.owner.json")
        marker, marker_digest = self._read_live_marker(
            marker_path,
            disk.marker_sha256,
        )
        # Re-observe the live disk after reading its marker so path replacement
        # cannot splice an authentic marker onto a different overlay.
        try:
            final = path.lstat()
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk pathname disappeared after marker observation"
            ) from exc
        if (
            final.st_dev != after.st_dev
            or final.st_ino != after.st_ino
            or final.st_uid != after.st_uid
            or stat.S_IMODE(final.st_mode) != stat.S_IMODE(after.st_mode)
            or final.st_nlink != after.st_nlink
        ):
            raise QemuRuntimeRecoveryRequired(
                "runtime disk identity changed around marker observation"
            )
        blocks = getattr(final, "st_blocks", None)
        if not isinstance(blocks, int) or blocks < 0:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk allocation cannot be observed"
            )
        return _LiveDiskFileEvidence(
            device_id=final.st_dev,
            inode=final.st_ino,
            owner_uid=final.st_uid,
            mode=stat.S_IMODE(final.st_mode),
            link_count=final.st_nlink,
            file_size_bytes=final.st_size,
            allocated_size_bytes=blocks * 512,
            marker_device_id=marker.st_dev,
            marker_inode=marker.st_ino,
            marker_owner_uid=marker.st_uid,
            marker_mode=stat.S_IMODE(marker.st_mode),
            marker_link_count=marker.st_nlink,
            marker_sha256=marker_digest,
        )

    @staticmethod
    def _revalidate_qmp_socket_path(
        path: Path,
        expected: QMPSocketIdentity,
    ) -> None:
        try:
            details = path.lstat()
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "QMP socket pathname cannot be revalidated"
            ) from exc
        if (
            not stat.S_ISSOCK(details.st_mode)
            or details.st_dev != expected.device_id
            or details.st_ino != expected.inode
            or details.st_uid != expected.uid
            or stat.S_IMODE(details.st_mode) != expected.mode
        ):
            raise QemuRuntimeRecoveryRequired(
                "QMP socket pathname no longer names the authenticated endpoint"
            )

    def _measure_runtime_disk(
        self,
        journal: RuntimeLaunchJournal,
        disk: DiskRecord,
        client: QMPClient,
    ) -> tuple[_LiveDiskFileEvidence, QMPDiskRuntimeEvidence, tuple[QMPEvent, ...]]:
        """Bracket one QMP disk measurement with exact host authority."""

        current = self._registry.get_disk(journal.intent.disk_id)
        if current != disk:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk row changed before append-only observation"
            )
        before = self._observe_live_disk(journal.intent, disk)
        response = client.query_block()
        evidence = extract_qmp_disk_runtime_evidence(
            response,
            self._block_contract(journal.intent, disk),
        )
        events = client.drain_events()
        if _dangerous_events(events):
            raise QemuRuntimeRecoveryRequired(
                "QMP emitted an event during the live disk measurement"
            )
        after = self._observe_live_disk(journal.intent, disk)
        if before.immutable_identity != after.immutable_identity:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk identity changed around the QMP measurement"
            )
        return after, evidence, events

    def _final_runtime_fence(
        self,
        journal: RuntimeLaunchJournal,
        disk: DiskRecord,
        process: ObservedProcessIdentity,
        log_guard: ObservedProcessIdentity,
        pidfile: PidfileIdentity,
        client: QMPClient,
    ) -> tuple[QMPEvent, ...]:
        """Perform the last aggregate observation before lifecycle CAS."""

        current = self._registry.get_disk(journal.intent.disk_id)
        if current != disk:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk row changed before lifecycle publication"
            )
        before = self._observe_live_disk(journal.intent, disk)
        current_pidfile = observe_pidfile(
            process,
            journal.intent.pid_file,
            expected_uid=process.process_owner_uid,
        )
        if current_pidfile != pidfile:
            raise QemuRuntimeRecoveryRequired(
                "pidfile identity changed before lifecycle publication"
            )
        self._revalidate_qmp_socket_path(
            Path(journal.intent.qmp_socket),
            client.socket_identity,
        )
        process = prove_process_alive(process)
        log_guard = prove_process_alive(log_guard)
        _verify_pipe_topology(
            journal,
            log_guard,
            process,
            self._policy,
        )
        status = normalize_qmp_status(client.query_status())
        if not status.running or status.status != "running":
            raise QemuRuntimeRecoveryRequired(
                "QMP status is not running at the lifecycle fence"
            )
        events = client.drain_events()
        if _dangerous_events(events):
            raise QemuRuntimeRecoveryRequired(
                "QMP emitted an event during the final lifecycle fence"
            )
        after = self._observe_live_disk(journal.intent, disk)
        if before.immutable_identity != after.immutable_identity:
            raise QemuRuntimeRecoveryRequired(
                "runtime disk identity changed during the lifecycle fence"
            )
        current_pidfile = observe_pidfile(
            process,
            journal.intent.pid_file,
            expected_uid=process.process_owner_uid,
        )
        if current_pidfile != pidfile:
            raise QemuRuntimeRecoveryRequired(
                "pidfile identity changed at the lifecycle commit boundary"
            )
        self._revalidate_qmp_socket_path(
            Path(journal.intent.qmp_socket),
            client.socket_identity,
        )
        log_guard = prove_process_alive(log_guard)
        process = prove_process_alive(process)
        _verify_pipe_topology(
            journal,
            log_guard,
            process,
            self._policy,
        )
        # The final non-readable pidfd observations follow every potentially
        # stale external sample and are immediately followed by the SQLite CAS.
        prove_process_alive(log_guard)
        prove_process_alive(process)
        return events

    def _begin_launch(
        self,
        record: VMRecord,
        revision: int,
        intent: RuntimeLaunchIntent,
    ) -> tuple[RuntimeLaunchJournal, VMRecord]:
        journal = RuntimeLaunchJournal(intent)
        journal, checkpoint = journal.advance("intent_persisted")
        when = _utc_now_after(record.updated_at)
        boot_evidence = TransitionEvidence(
            evidence_id=uuid4(),
            observed_at=when,
            facts={
                "intent_digest": record.intent_digest,
                "boot_id": str(intent.boot_id),
                "operation_id": str(intent.operation_id),
            },
            vm_id=record.vm_id,
            generation=record.generation,
            boot_id=intent.boot_id,
            intent_digest=record.intent_digest,
        )
        booting = record.transition(
            VMState.BOOTING,
            boot_evidence,
            boot_id=intent.boot_id,
        )
        transition = booting.transitions[-1]
        with self._registry.write_transaction() as transaction:
            transaction.begin_operation(**journal.begin_operation_kwargs())
            transaction.append_checkpoint(
                **checkpoint.append_checkpoint_kwargs()
            )
            transaction.set_operation_status(
                **journal.running_status_write().set_operation_status_kwargs()
            )
            transaction.append_lifecycle_event(
                record.vm_id,
                transition,
                operation_id=intent.operation_id,
            )
            transaction.compare_and_swap_vm(
                record.vm_id,
                revision,
                booting,
            )
        self._observe_checkpoint(intent.operation_id, "intent_persisted")
        return journal, booting

    def _spawn_log_guard(
        self,
        intent: RuntimeLaunchIntent,
        runtime_root: Path,
        python: Path,
        python_identity: ExecutableIdentity,
        serial_read: int,
        qemu_read: int,
    ) -> tuple[
        subprocess.Popen[bytes],
        socket.socket,
        ObservedProcessIdentity,
    ]:
        parent_control, child_control = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        argv = (
            os.fspath(python),
            "-I",
            "-m",
            _MODULE_LOG_GUARD,
            str(os.getpid()),
            str(child_control.fileno()),
            str(serial_read),
            str(qemu_read),
            os.fspath(Path(intent.serial_log_path).parent),
            intent.serial_log_path,
            intent.qemu_log_path,
            str(self._policy.log_max_bytes),
            str(self._policy.log_backup_count),
        )
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=None,
                cwd=runtime_root,
                env=_guard_environment(runtime_root),
                close_fds=True,
                pass_fds=(child_control.fileno(), serial_read, qemu_read),
                start_new_session=True,
                restore_signals=True,
                umask=0o077,
            )
        except BaseException:
            parent_control.close()
            child_control.close()
            raise
        child_control.close()
        try:
            _receive_exact(
                parent_control,
                LOG_GUARD_READY,
                timeout_seconds=self._policy.guard_timeout_seconds,
                label="log guardian READY",
            )
            observed = observe_process(
                process.pid,
                expected_executable_identity=python_identity,
                expected_argv=argv,
            )
            return process, parent_control, observed
        except BaseException:
            parent_control.close()
            self._terminate_local(observe=None, process=process)
            raise

    def _spawn_qemu_guard(
        self,
        intent: RuntimeLaunchIntent,
        runtime_root: Path,
        python: Path,
        python_identity: ExecutableIdentity,
        executable_fd: int,
        storage: _PinnedRuntimeStorage,
        serial_write: int,
        qemu_write: int,
    ) -> tuple[
        subprocess.Popen[bytes],
        socket.socket,
        ObservedProcessIdentity,
    ]:
        parent_control, child_control = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        argv = (
            os.fspath(python),
            "-I",
            "-m",
            _MODULE_QEMU_GUARD,
            str(os.getpid()),
            str(child_control.fileno()),
            os.fspath(runtime_root),
            str(executable_fd),
            *storage.overlay.guard_argv(),
            *storage.base.guard_argv(),
            *storage.marker.guard_argv(),
            "--",
            *intent.executed_argv,
        )
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=serial_write,
                stderr=qemu_write,
                cwd=runtime_root,
                env=_guard_environment(runtime_root),
                close_fds=True,
                pass_fds=(
                    child_control.fileno(),
                    executable_fd,
                    *storage.descriptors,
                ),
                start_new_session=True,
                restore_signals=True,
                umask=0o077,
            )
        except BaseException:
            parent_control.close()
            child_control.close()
            raise
        child_control.close()
        try:
            _receive_exact(
                parent_control,
                QEMU_GUARD_READY,
                timeout_seconds=self._policy.guard_timeout_seconds,
                label="QEMU exec guard READY",
            )
            observed = observe_process(
                process.pid,
                expected_executable_identity=python_identity,
                expected_argv=argv,
            )
            return process, parent_control, observed
        except BaseException:
            parent_control.close()
            self._terminate_local(observe=None, process=process)
            raise

    def _terminate_local(
        self,
        *,
        observe: ObservedProcessIdentity | None,
        process: subprocess.Popen[bytes] | None,
    ) -> None:
        if observe is not None:
            try:
                terminate_owned_process(
                    observe,
                    graceful_timeout_seconds=self._policy.terminate_grace_seconds,
                    kill_timeout_seconds=self._policy.terminate_kill_seconds,
                )
                return
            except (ProcessUnavailableError, ProcessIdentityMismatchError):
                return
        if process is not None and process.poll() is None:
            # This fallback is only for a direct child that failed before a
            # durable/typed observation was possible.  It never runs on restart.
            process.terminate()
            try:
                process.wait(self._policy.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(self._policy.terminate_kill_seconds)

    def launch(
        self,
        vm_id: UUID | str,
        *,
        permit: DisposableLaunchPermit,
    ) -> RuntimeLaunchResult:
        """Launch one validated disposable/runtime VM through the P4 boundary.

        No public CLI or control operation calls this method.  The caller owns
        the external authority decision for physical QEMU execution and must
        present one explicit, one-shot, non-production fixture permit.
        """

        identity = _strict_uuid(vm_id, "vm_id")
        if not isinstance(permit, DisposableLaunchPermit):
            raise QemuRuntimeError(
                "runtime launch requires a disposable launch permit"
            )
        with self._lock:
            if self._registry.status.mode is not RegistryMode.READ_WRITE:
                raise QemuRuntimeRecoveryRequired(
                    "runtime launch requires the registry writer"
                )
            if self._live:
                raise QemuRuntimeError(
                    "one-active-AIPC policy already has a live runtime"
                )

            record, revision, disk, plan = self._select_authority(identity)
            if disk.base_image_sha256 is None:
                raise QemuRuntimeError(
                    "runtime launch disk lacks immutable base-image identity"
                )
            try:
                launch_authority = permit.revalidate_and_consume(
                    self._configuration,
                    vm_id=record.vm_id,
                    generation=record.generation,
                    disk_id=disk.disk_id,
                    disk_path=disk.path,
                    base_sha256=disk.base_image_sha256,
                )
            except LaunchAuthorityError as exc:
                raise QemuRuntimeError(
                    "disposable launch authority was rejected"
                ) from exc
            executable_fd = -1
            storage: _PinnedRuntimeStorage | None = None
            try:
                executable, executable_fd = open_pinned_executable(
                    self._configuration.qemu_binary
                )
                storage = self._pin_runtime_storage(disk)
                bound_argv = bind_block_fds(
                    plan,
                    overlay_fd=storage.overlay.descriptor,
                    base_fd=storage.base.descriptor,
                )
                executed_argv = normalize_argv(
                    executable.path,
                    tuple(bound_argv[1:]),
                )
                intent = RuntimeLaunchIntent.from_runtime(
                    record,
                    disk,
                    plan,
                    executable,
                    executed_argv=executed_argv,
                    launch_authority=launch_authority,
                )
            except BaseException:
                if executable_fd >= 0:
                    os.close(executable_fd)
                if storage is not None:
                    storage.close()
                raise
            journal: RuntimeLaunchJournal | None = None
            log_process: subprocess.Popen[bytes] | None = None
            qemu_process: subprocess.Popen[bytes] | None = None
            log_observed: ObservedProcessIdentity | None = None
            qemu_guard_observed: ObservedProcessIdentity | None = None
            qemu_observed: ObservedProcessIdentity | None = None
            log_control: socket.socket | None = None
            qemu_control: socket.socket | None = None
            qmp: QMPClient | None = None
            serial_read = serial_write = qemu_read = qemu_write = -1
            try:
                journal, booting = self._begin_launch(
                    record,
                    revision,
                    intent,
                )
                runtime_root = prepare_private_directory(
                    self._configuration.daemon.runtime_root,
                    "runtime root",
                )
                log_root = prepare_private_directory(
                    self._configuration.storage.log_root,
                    "log root",
                )
                _require_absent(Path(intent.qmp_socket), "QMP socket")
                _require_absent(Path(intent.pid_file), "QEMU pidfile")
                journal = self._append_checkpoint(
                    journal,
                    "private_paths_prepared",
                    _directory_facts(runtime_root, log_root),
                )
                journal = self._append_checkpoint(
                    journal,
                    "executable_pinned",
                    {
                        "executable": intent.executable.to_dict(),
                        "storage": {
                            "overlay": storage.overlay.to_checkpoint_dict(),
                            "base": storage.base.to_checkpoint_dict(),
                            "owner_marker": storage.marker.to_checkpoint_dict(),
                        },
                    },
                )

                python = resolve_executable(sys.executable)
                python_identity = inspect_executable(os.fspath(python))
                serial_read, serial_write = os.pipe2(os.O_CLOEXEC)
                qemu_read, qemu_write = os.pipe2(os.O_CLOEXEC)
                serial_pipe = os.fstat(serial_read)
                qemu_pipe = os.fstat(qemu_read)
                (
                    log_process,
                    log_control,
                    log_observed,
                ) = self._spawn_log_guard(
                    intent,
                    runtime_root,
                    python,
                    python_identity,
                    serial_read,
                    qemu_read,
                )
                os.close(serial_read)
                serial_read = -1
                os.close(qemu_read)
                qemu_read = -1
                journal = self._append_checkpoint(
                    journal,
                    "log_guard_spawned",
                    {
                        "process": log_observed,
                        "serial_pipe_device_id": serial_pipe.st_dev,
                        "serial_pipe_inode": serial_pipe.st_ino,
                        "qemu_pipe_device_id": qemu_pipe.st_dev,
                        "qemu_pipe_inode": qemu_pipe.st_ino,
                    },
                )
                _send_exact(log_control, LOG_GUARD_RELEASE, "log guardian")
                _receive_exact(
                    log_control,
                    LOG_GUARD_RELEASED,
                    timeout_seconds=self._policy.guard_timeout_seconds,
                    label="log guardian RELEASED",
                )
                revalidate_process(log_observed)
                journal = self._append_checkpoint(
                    journal,
                    "log_guard_released",
                    {
                        "pid": log_observed.pid,
                        "start_time_ticks": log_observed.start_time_ticks,
                    },
                )
                log_control.close()
                log_control = None

                (
                    qemu_process,
                    qemu_control,
                    qemu_guard_observed,
                ) = self._spawn_qemu_guard(
                    intent,
                    runtime_root,
                    python,
                    python_identity,
                    executable_fd,
                    storage,
                    serial_write,
                    qemu_write,
                )
                storage.close()
                storage = None
                os.close(executable_fd)
                executable_fd = -1
                os.close(serial_write)
                serial_write = -1
                os.close(qemu_write)
                qemu_write = -1
                journal = self._append_checkpoint(
                    journal,
                    "qemu_guard_spawned",
                    {"process": qemu_guard_observed},
                )
                _send_exact(qemu_control, QEMU_GUARD_RELEASE, "QEMU exec guard")
                qemu_control.shutdown(socket.SHUT_WR)
                journal = self._append_checkpoint(
                    journal,
                    "qemu_target_released",
                    {
                        "pid": qemu_guard_observed.pid,
                        "start_time_ticks": qemu_guard_observed.start_time_ticks,
                    },
                )
                qemu_observed = _wait_for_target_exec(
                    qemu_process,
                    qemu_guard_observed,
                    executable,
                    intent.executed_argv,
                    timeout_seconds=self._policy.guard_timeout_seconds,
                    retry_seconds=self._policy.retry_interval_seconds,
                )
                qemu_control.close()
                qemu_control = None
                journal = self._append_checkpoint(
                    journal,
                    "process_observed",
                    {"process": qemu_observed},
                )

                pidfile = _wait_for_pidfile(
                    qemu_observed,
                    intent.pid_file,
                    expected_uid=qemu_observed.process_owner_uid,
                    timeout_seconds=self._policy.guard_timeout_seconds,
                    retry_seconds=self._policy.retry_interval_seconds,
                )
                journal = self._append_checkpoint(
                    journal,
                    "pidfile_verified",
                    {
                        "pidfile_pid": pidfile.pid,
                        "device_id": pidfile.device_id,
                        "inode": pidfile.inode,
                        "owner_uid": pidfile.owner_uid,
                        "mode": pidfile.mode,
                        "link_count": pidfile.link_count,
                        "size_bytes": pidfile.size_bytes,
                        "mtime_ns": pidfile.mtime_ns,
                        "ctime_ns": pidfile.ctime_ns,
                    },
                )
                qmp, greeting = _open_qmp(
                    Path(intent.qmp_socket),
                    qemu_observed,
                    self._policy,
                )
                socket_identity = qmp.socket_identity
                greeting_digest = greeting_sha256(greeting)
                journal = self._append_checkpoint(
                    journal,
                    "qmp_greeting_observed",
                    {
                        "qmp_evidence_id": str(intent.qmp_evidence_id),
                        "peer_pid": qmp.peer_credentials.pid,
                        "peer_uid": qmp.peer_credentials.uid,
                        "greeting_sha256": greeting_digest,
                        "qmp_socket_device_id": socket_identity.device_id,
                        "qmp_socket_inode": socket_identity.inode,
                        "qmp_socket_mode": socket_identity.mode,
                    },
                )
                journal = self._append_checkpoint(
                    journal,
                    "qmp_capabilities_negotiated",
                    {
                        "qmp_evidence_id": str(intent.qmp_evidence_id),
                        "peer_pid": qmp.peer_credentials.pid,
                        "capabilities_sha256": canonical_evidence_sha256(
                            list(greeting.capabilities)
                        ),
                    },
                )
                block_contract = self._block_contract(intent, disk)
                expectation = QMPIdentityExpectation(
                    vm_id=intent.vm_id,
                    name=intent.vm_name,
                    process_pid=qemu_observed.pid,
                    vcpus=intent.vcpus,
                    block=block_contract,
                )
                (
                    qmp_evidence,
                    disk_evidence,
                    storage_graph_evidence,
                    events,
                ) = _stabilize_identity(
                    qmp,
                    greeting,
                    expectation,
                    qemu_observed,
                    intent.qmp_evidence_id,
                    self._policy,
                )
                _verify_pipe_topology(
                    journal,
                    log_observed,
                    qemu_observed,
                    self._policy,
                )
                journal = self._append_checkpoint(
                    journal,
                    "qmp_identity_verified",
                    {
                        "evidence": qmp_evidence,
                        "evidence_sha256": qmp_evidence.evidence_sha256,
                        "storage_graph": storage_graph_evidence.to_dict(),
                        "storage_graph_sha256": (
                            storage_graph_evidence.evidence_sha256
                        ),
                    },
                )
                (
                    journal,
                    running_record,
                    disk_evidence,
                    publication_events,
                ) = self._publish_runtime(
                    journal,
                    booting,
                    disk,
                    qemu_observed,
                    log_observed,
                    pidfile,
                    qmp,
                    greeting_digest,
                )
                events = (*events, *publication_events)
                journal = self._complete_launch(journal)
                live = _LiveRuntime(
                    journal=journal,
                    process=qemu_observed,
                    log_guard=log_observed,
                    qmp=qmp,
                    qmp_evidence=qmp_evidence,
                    storage_graph_evidence=storage_graph_evidence,
                    disk_evidence=disk_evidence,
                    events=events,
                )
                self._live[running_record.vm_id] = live
                qmp = None
                return RuntimeLaunchResult(
                    operation_id=intent.operation_id,
                    vm_id=intent.vm_id,
                    generation=intent.generation,
                    boot_id=intent.boot_id,
                    process=qemu_observed,
                    log_guard=log_observed,
                    qmp_evidence=qmp_evidence,
                    storage_graph_evidence=storage_graph_evidence,
                    disk_evidence=disk_evidence,
                    qmp_greeting=greeting,
                    qmp_responses=qmp.response_history,
                    serial_log_path=Path(intent.serial_log_path),
                    qemu_log_path=Path(intent.qemu_log_path),
                    events=events,
                )
            except BaseException as exc:
                if qmp is not None:
                    qmp.close()
                durable_names = (
                    _checkpoint_names(journal)
                    if journal is not None
                    else frozenset()
                )
                if "qemu_guard_spawned" not in durable_names:
                    self._terminate_local(
                        observe=qemu_observed or qemu_guard_observed,
                        process=qemu_process,
                    )
                if "log_guard_spawned" not in durable_names:
                    self._terminate_local(
                        observe=log_observed,
                        process=log_process,
                    )
                if journal is not None:
                    try:
                        self._recover_operation(
                            self._require_operation(intent.operation_id)
                        )
                    except BaseException as recovery_exc:
                        raise QemuRuntimeRecoveryRequired(
                            "launch failed and exact recovery did not complete"
                        ) from recovery_exc
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if isinstance(exc, QemuRuntimeError):
                    raise
                raise QemuRuntimeError("internal QEMU launch failed") from exc
            finally:
                for control in (log_control, qemu_control):
                    if control is not None:
                        control.close()
                for descriptor in (
                    executable_fd,
                    serial_read,
                    serial_write,
                    qemu_read,
                    qemu_write,
                ):
                    if descriptor >= 0:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                if storage is not None:
                    storage.close()

    def _publish_runtime(
        self,
        journal: RuntimeLaunchJournal,
        booting: VMRecord,
        disk: DiskRecord,
        process: ObservedProcessIdentity,
        log_guard: ObservedProcessIdentity,
        pidfile: PidfileIdentity,
        client: QMPClient,
        greeting_digest: str,
    ) -> tuple[
        RuntimeLaunchJournal,
        VMRecord,
        QMPDiskRuntimeEvidence,
        tuple[QMPEvent, ...],
    ]:
        intent = journal.intent
        protocol_process = process.to_protocol_identity()
        process_hash = canonical_evidence_sha256(protocol_process.to_dict())
        updated, process_checkpoint = journal.advance(
            "process_identity_recorded",
            {
                "process_record_id": str(intent.process_record_id),
                "process_identity_sha256": process_hash,
            },
        )
        with self._registry.write_transaction() as transaction:
            transaction.record_process_identity(
                intent.vm_id,
                protocol_process,
                boot_id=intent.boot_id,
                process_record_id=intent.process_record_id,
                active=True,
                operation_id=intent.operation_id,
            )
            transaction.append_checkpoint(
                **process_checkpoint.append_checkpoint_kwargs()
            )
        journal = updated
        self._observe_checkpoint(intent.operation_id, "process_identity_recorded")

        details, disk_evidence, disk_events = self._measure_runtime_disk(
            journal,
            disk,
            client,
        )
        observed_at = _utc_now_after(protocol_process.observed_at)
        # The registry owns the canonical observation digest.  Build the row and
        # bind the journal checkpoint to the returned immutable digest inside
        # the same transaction.
        with self._registry.write_transaction() as transaction:
            disk_observation = transaction.append_disk_runtime_observation(
                disk.disk_id,
                vm_id=intent.vm_id,
                generation=intent.generation,
                observed_at=observed_at,
                file_size_bytes=details.file_size_bytes,
                allocated_size_bytes=details.allocated_size_bytes,
                qemu_actual_size_bytes=disk_evidence.qemu_actual_size_bytes,
                dirty=disk_evidence.dirty,
                corrupt=disk_evidence.corrupt,
                quiesced=False,
                operation_id=intent.operation_id,
                observation_id=intent.disk_observation_id,
                boot_id=intent.boot_id,
                process_record_id=intent.process_record_id,
            )
            updated, disk_checkpoint = journal.advance(
                "disk_observation_recorded",
                {
                    "disk_observation_id": str(intent.disk_observation_id),
                    "disk_id": str(intent.disk_id),
                    "process_record_id": str(intent.process_record_id),
                    "canonical_sha256": disk_observation.canonical_sha256,
                },
            )
            transaction.append_checkpoint(
                **disk_checkpoint.append_checkpoint_kwargs()
            )
        journal = updated
        self._observe_checkpoint(intent.operation_id, "disk_observation_recorded")

        lifecycle_time = _utc_now_after(observed_at)
        runtime_observation = RuntimeObservation(
            observation_id=intent.qmp_evidence_id,
            vm_id=intent.vm_id,
            boot_id=intent.boot_id,
            generation=intent.generation,
            intent_digest=booting.intent_digest,
            state=VMState.QMP_RUNNING,
            source=ObservationSource.DAEMON,
            observed_at=lifecycle_time,
            process=protocol_process,
            qmp_status="running",
            qmp_uuid=intent.vm_id,
            qmp_greeting_sha256=greeting_digest,
        )
        lifecycle_evidence = TransitionEvidence(
            evidence_id=intent.lifecycle_evidence_id,
            observed_at=lifecycle_time,
            facts={
                "intent_digest": booting.intent_digest,
                "boot_id": str(intent.boot_id),
                "observation_id": str(intent.qmp_evidence_id),
                "qmp_greeting": f"sha256:{greeting_digest}",
                "qmp_status": "running",
                "qmp_uuid": str(intent.vm_id),
            },
            vm_id=intent.vm_id,
            generation=intent.generation,
            boot_id=intent.boot_id,
            intent_digest=booting.intent_digest,
            observation=runtime_observation,
        )
        running = booting.transition(
            VMState.QMP_RUNNING,
            lifecycle_evidence,
        )
        lifecycle_hash = canonical_evidence_sha256(
            lifecycle_evidence.to_dict()
        )
        updated, lifecycle_checkpoint = journal.advance(
            "lifecycle_committed",
            {
                "lifecycle_evidence_id": str(intent.lifecycle_evidence_id),
                "qmp_evidence_id": str(intent.qmp_evidence_id),
                "process_record_id": str(intent.process_record_id),
                "state": VMState.QMP_RUNNING.value,
                "canonical_sha256": lifecycle_hash,
            },
        )
        stored = self._registry.get_vm(intent.vm_id)
        if stored is None or stored.record != booting:
            raise QemuRuntimeRecoveryRequired(
                "BOOTING record changed before lifecycle publication"
            )
        final_events = self._final_runtime_fence(
            journal,
            disk,
            process,
            log_guard,
            pidfile,
            client,
        )
        with self._registry.write_transaction() as transaction:
            durable_process = transaction.get_process_identity(
                intent.process_record_id
            )
            if (
                durable_process is None
                or durable_process.process_record_id
                != intent.process_record_id
                or durable_process.vm_id != intent.vm_id
                or durable_process.generation != intent.generation
                or durable_process.boot_id != intent.boot_id
                or durable_process.process != protocol_process
                or not durable_process.active
                or durable_process.operation_id != intent.operation_id
            ):
                raise QemuRuntimeRecoveryRequired(
                    "durable active process authority changed before lifecycle CAS"
                )
            durable_disk_observation = (
                transaction.get_disk_runtime_observation(
                    intent.disk_observation_id
                )
            )
            if durable_disk_observation != disk_observation:
                raise QemuRuntimeRecoveryRequired(
                    "durable disk observation changed before lifecycle CAS"
                )
            transaction.compare_and_swap_vm(
                intent.vm_id,
                stored.revision,
                running,
            )
            transaction.append_lifecycle_event(
                intent.vm_id,
                running.transitions[-1],
                operation_id=intent.operation_id,
            )
            transaction.append_checkpoint(
                **lifecycle_checkpoint.append_checkpoint_kwargs()
            )
        journal = updated
        self._observe_checkpoint(intent.operation_id, "lifecycle_committed")
        return journal, running, disk_evidence, (*disk_events, *final_events)

    def _require_operation(self, operation_id: UUID) -> OperationRecord:
        operation = self._registry.get_operation(operation_id)
        if operation is None:
            raise QemuRuntimeRecoveryRequired(
                "runtime operation disappeared from the registry"
            )
        return operation

    def _mark_recovery_required(
        self,
        journal: RuntimeLaunchJournal,
        reason: str,
        *,
        cleanup_state: str = "unknown",
    ) -> None:
        write = journal.recovery_status_write(
            reason,
            cleanup_state=cleanup_state,
        )
        with self._registry.write_transaction() as transaction:
            transaction.set_operation_status(
                **write.set_operation_status_kwargs()
            )

    def _mark_cleaned(
        self,
        journal: RuntimeLaunchJournal,
        *,
        child_spawned: bool,
        reason: str,
        qemu_exit: _ExitProof | None,
    ) -> None:
        intent = journal.intent
        process_recorded = "process_identity_recorded" in _checkpoint_names(
            journal
        )
        protocol_exit_process = None
        if process_recorded:
            if qemu_exit is None:
                raise QemuRuntimeRecoveryRequired(
                    "active process authority cannot retire without exact exit proof"
                )
            journal_process = journal.observed_process_at("process_observed")
            if qemu_exit.process != journal_process:
                raise QemuRuntimeRecoveryRequired(
                    "exit proof is not bound to the journaled QEMU process"
                )
            protocol_exit_process = qemu_exit.process.to_protocol_identity()
        stored = self._registry.get_vm(intent.vm_id)
        status_write = journal.terminal_status_write(
            "failed",
            reason=reason,
            cleanup_state="exit_observed" if child_spawned else "not_spawned",
        )
        with self._registry.write_transaction() as transaction:
            if process_recorded:
                assert qemu_exit is not None
                assert protocol_exit_process is not None
                durable_process = transaction.get_process_identity(
                    intent.process_record_id
                )
                if (
                    durable_process is None
                    or durable_process.process_record_id
                    != intent.process_record_id
                    or durable_process.vm_id != intent.vm_id
                    or durable_process.generation != intent.generation
                    or durable_process.boot_id != intent.boot_id
                    or durable_process.process != protocol_exit_process
                    or durable_process.operation_id != intent.operation_id
                    or not durable_process.active
                ):
                    raise QemuRuntimeRecoveryRequired(
                        "active process authority changed before exact retirement"
                    )
                transaction.retire_process_identity(
                    intent.process_record_id,
                    intent.vm_id,
                    protocol_exit_process,
                    generation=intent.generation,
                    boot_id=intent.boot_id,
                    original_operation_id=intent.operation_id,
                    observed_absent_at=qemu_exit.observed_absent_at,
                    reason="runtime_cleanup",
                    exit_evidence_id=uuid5(
                        intent.operation_id,
                        "runtime-process-exit",
                    ),
                )
            if stored is not None and stored.record.state in {
                VMState.BOOTING,
                VMState.QMP_RUNNING,
            }:
                when = _utc_now_after(stored.record.updated_at)
                cause_id = uuid4()
                cause = TransitionCause(
                    code=_RUNTIME_FAILURE_CODE,
                    detail="P4 runtime launch was cleaned during exact recovery",
                    occurred_at=when,
                    event_id=cause_id,
                )
                evidence = TransitionEvidence(
                    evidence_id=uuid4(),
                    observed_at=when,
                    facts={
                        "intent_digest": stored.record.intent_digest,
                        "error_code": _RUNTIME_FAILURE_CODE,
                        "cause_event_id": str(cause_id),
                    },
                    vm_id=stored.record.vm_id,
                    generation=stored.record.generation,
                    boot_id=stored.record.boot_id,
                    intent_digest=stored.record.intent_digest,
                )
                failed = stored.record.transition(
                    VMState.ERROR,
                    evidence,
                    cause=cause,
                )
                transaction.append_lifecycle_event(
                    intent.vm_id,
                    failed.transitions[-1],
                    operation_id=intent.operation_id,
                )
                transaction.compare_and_swap_vm(
                    intent.vm_id,
                    stored.revision,
                    failed,
                )
            transaction.set_operation_status(
                **status_write.set_operation_status_kwargs()
            )

    def _prove_process_absence(
        self,
        expected: ObservedProcessIdentity,
    ) -> _ExitProof | None:
        """Return exact original-task absence or prove the task still matches."""

        try:
            current = observe_process(expected.pid)
        except ProcessUnavailableError:
            return _ExitProof(
                process=expected,
                observed_absent_at=datetime.now(timezone.utc),
                termination=None,
            )
        if (
            current.start_time_ticks != expected.start_time_ticks
            or current.proc_device_id != expected.proc_device_id
            or current.proc_inode != expected.proc_inode
        ):
            # The recorded kernel task is gone.  The current numeric PID, if
            # any, is a different task and is deliberately left untouched.
            return _ExitProof(
                process=expected,
                observed_absent_at=current.observed_at,
                termination=None,
            )
        try:
            revalidate_process(expected)
        except ProcessUnavailableError:
            return _ExitProof(
                process=expected,
                observed_absent_at=datetime.now(timezone.utc),
                termination=None,
            )
        except ProcessIdentityMismatchError as exc:
            # The same kernel task execed or otherwise drifted.  It may still
            # own the disk, so neither absence nor signal authority exists.
            raise QemuRuntimeRecoveryRequired(
                "recorded process task remains live with a changed identity"
            ) from exc
        return None

    def _terminate_replayed(
        self,
        expected: ObservedProcessIdentity,
    ) -> _ExitProof:
        absent = self._prove_process_absence(expected)
        if absent is not None:
            return absent
        termination = terminate_owned_process(
            expected,
            graceful_timeout_seconds=self._policy.terminate_grace_seconds,
            kill_timeout_seconds=self._policy.terminate_kill_seconds,
        )
        return _ExitProof(
            process=expected,
            observed_absent_at=termination.observed_at,
            termination=termination,
        )

    def _settle_log_guard(
        self,
        journal: RuntimeLaunchJournal,
        expected: ObservedProcessIdentity,
    ) -> _ExitProof:
        """Allow EOF drain, then signal only the exact guardian and pipe set."""

        deadline = time.monotonic() + self._policy.guardian_exit_seconds
        while True:
            absent = self._prove_process_absence(expected)
            if absent is not None:
                return absent
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            time.sleep(min(self._policy.retry_interval_seconds, remaining))
        _verify_guardian_pipe_topology(
            journal,
            expected,
            self._policy,
        )
        return self._terminate_replayed(expected)

    def _capture_cleanup_paths(
        self,
        journal: RuntimeLaunchJournal,
        qemu: ObservedProcessIdentity | None,
    ) -> tuple[PidfileIdentity | None, QMPSocketIdentity | None]:
        intent = journal.intent
        pidfile: PidfileIdentity | None = None
        qmp_socket: QMPSocketIdentity | None = None
        if qemu is None:
            return pidfile, qmp_socket

        pid_path = Path(intent.pid_file)
        try:
            pid_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "QEMU pidfile cannot be inspected for cleanup"
            ) from exc
        else:
            pidfile = observe_pidfile(
                qemu,
                pid_path,
                expected_uid=qemu.process_owner_uid,
            )

        socket_path = Path(intent.qmp_socket)
        try:
            socket_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "QMP socket cannot be inspected for cleanup"
            ) from exc
        else:
            client, _ = _open_qmp(socket_path, qemu, self._policy)
            try:
                qmp_socket = client.socket_identity
            finally:
                client.close()
        return pidfile, qmp_socket

    def _probe_live_runtime(
        self,
        journal: RuntimeLaunchJournal,
        qemu: ObservedProcessIdentity,
    ) -> _RuntimeProbe:
        """Freshly prove the complete process/QMP/disk/guardian aggregate."""

        intent = journal.intent
        qemu = revalidate_process(qemu)
        pidfile = _wait_for_pidfile(
            qemu,
            intent.pid_file,
            expected_uid=qemu.process_owner_uid,
            timeout_seconds=self._policy.guard_timeout_seconds,
            retry_seconds=self._policy.retry_interval_seconds,
        )
        names = _checkpoint_names(journal)
        if "pidfile_verified" in names:
            persisted_pidfile = journal.pidfile_identity()
            if (
                pidfile.pid != persisted_pidfile.pid
                or pidfile.device_id != persisted_pidfile.device_id
                or pidfile.inode != persisted_pidfile.inode
                or pidfile.owner_uid != persisted_pidfile.owner_uid
                or pidfile.mode != persisted_pidfile.mode
                or pidfile.link_count != persisted_pidfile.link_count
                or pidfile.size_bytes != persisted_pidfile.size_bytes
                or pidfile.mtime_ns != persisted_pidfile.mtime_ns
                or pidfile.ctime_ns != persisted_pidfile.ctime_ns
            ):
                raise QemuRuntimeRecoveryRequired(
                    "pidfile identity changed after its durable checkpoint"
                )

        qmp, greeting = _open_qmp(
            Path(intent.qmp_socket),
            qemu,
            self._policy,
        )
        try:
            socket_identity = qmp.socket_identity
            if "qmp_greeting_observed" in names:
                persisted_socket = journal.qmp_socket_identity()
                if (
                    socket_identity.device_id != persisted_socket.device_id
                    or socket_identity.inode != persisted_socket.inode
                    or socket_identity.uid != persisted_socket.uid
                    or socket_identity.mode != persisted_socket.mode
                ):
                    raise QemuRuntimeRecoveryRequired(
                        "QMP socket identity changed after its durable checkpoint"
                    )
            disk = self._registry.get_disk(intent.disk_id)
            if (
                disk is None
                or not disk.active
                or not disk.materialized
                or disk.vm_id != intent.vm_id
                or disk.generation != intent.generation
                or disk.path != intent.disk_path
            ):
                raise QemuRuntimeRecoveryRequired(
                    "live QEMU disk authority is no longer the P3 owner"
                )
            expectation = QMPIdentityExpectation(
                vm_id=intent.vm_id,
                name=intent.vm_name,
                process_pid=qemu.pid,
                vcpus=intent.vcpus,
                block=self._block_contract(intent, disk),
            )
            (
                evidence,
                disk_evidence,
                storage_graph_evidence,
                events,
            ) = _stabilize_identity(
                qmp,
                greeting,
                expectation,
                qemu,
                uuid4(),
                self._policy,
            )
            log_guard = journal.observed_process_at("log_guard_spawned")
            log_guard = prove_process_alive(log_guard)
            _verify_pipe_topology(
                journal,
                log_guard,
                qemu,
                self._policy,
            )
            _, disk_evidence, disk_events = self._measure_runtime_disk(
                journal,
                disk,
                qmp,
            )
            final_events = self._final_runtime_fence(
                journal,
                disk,
                qemu,
                log_guard,
                pidfile,
                qmp,
            )
            return _RuntimeProbe(
                process=prove_process_alive(qemu),
                log_guard=log_guard,
                pidfile=pidfile,
                socket_identity=socket_identity,
                qmp=qmp,
                qmp_evidence=evidence,
                storage_graph_evidence=storage_graph_evidence,
                disk_evidence=disk_evidence,
                events=(*events, *disk_events, *final_events),
            )
        except BaseException:
            qmp.close()
            raise

    def _open_runtime_parent(
        self,
        path: Path,
        expected: object,
    ) -> int:
        if (
            path.parent != self._configuration.daemon.runtime_root
            or os.fspath(path.parent) != getattr(expected, "path")
        ):
            raise QemuRuntimeRecoveryRequired(
                "runtime artifact escapes the configured private root"
            )
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path.parent, flags)
        except OSError as exc:
            raise QemuRuntimeRecoveryRequired(
                "runtime artifact parent cannot be opened safely"
            ) from exc
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_dev != getattr(expected, "device_id")
                or details.st_ino != getattr(expected, "inode")
                or details.st_uid != getattr(expected, "owner_uid")
                or stat.S_IMODE(details.st_mode) != getattr(expected, "mode")
            ):
                raise QemuRuntimeRecoveryRequired(
                    "runtime artifact parent ownership is unsafe"
                )
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _retire_runtime_entry(
        self,
        path: Path,
        expected: object | None,
        *,
        operation_id: UUID,
        socket_entry: bool,
        parent_identity: object,
    ) -> None:
        if expected is None:
            raise QemuRuntimeRecoveryRequired(
                "runtime entry exists without captured retirement identity"
            )
        retired_name = f".somnus-retired-{operation_id}-{path.name}"
        if len(os.fsencode(retired_name)) > 240:
            raise QemuRuntimeRecoveryRequired(
                "runtime retirement name exceeds the supported bound"
            )

        def matches(
            details: os.stat_result,
            *,
            require_original_ctime: bool = False,
        ) -> bool:
            common = (
                details.st_dev == getattr(expected, "device_id")
                and details.st_ino == getattr(expected, "inode")
                and stat.S_IMODE(details.st_mode) == getattr(expected, "mode")
            )
            if socket_entry:
                return (
                    common
                    and stat.S_ISSOCK(details.st_mode)
                    and details.st_uid == getattr(expected, "uid")
                )
            return (
                common
                and stat.S_ISREG(details.st_mode)
                and details.st_uid == getattr(expected, "owner_uid")
                and details.st_nlink == getattr(expected, "link_count")
                and details.st_size == getattr(expected, "size_bytes")
                and details.st_mtime_ns == getattr(expected, "mtime_ns")
                and (
                    not require_original_ctime
                    or details.st_ctime_ns == getattr(expected, "ctime_ns")
                )
            )

        descriptor = self._open_runtime_parent(path, parent_identity)
        try:
            try:
                source = os.stat(
                    path.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                source = None
            except OSError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "runtime entry cannot be inspected during retirement"
                ) from exc
            try:
                retired = os.stat(
                    retired_name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                retired = None
            except OSError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "retired runtime entry cannot be inspected"
                ) from exc

            if retired is not None:
                if source is not None or not matches(retired):
                    raise QemuRuntimeRecoveryRequired(
                        "runtime retirement namespace is ambiguous"
                    )
                os.fsync(descriptor)
                return
            if source is None:
                return
            if not matches(source, require_original_ctime=True):
                raise QemuRuntimeRecoveryRequired(
                    "runtime pathname was replaced; foreign entry was not retired"
                )
            try:
                _rename_noreplace(
                    descriptor,
                    path.name,
                    retired_name,
                )
            except OSError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "owned runtime entry could not be atomically retired"
                ) from exc
            try:
                captured = os.stat(
                    retired_name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "captured runtime entry cannot be revalidated"
                ) from exc
            if not matches(captured):
                try:
                    _rename_noreplace(
                        descriptor,
                        retired_name,
                        path.name,
                    )
                except OSError as restore_exc:
                    raise QemuRuntimeRecoveryRequired(
                        "foreign runtime entry was captured and could not be restored"
                    ) from restore_exc
                raise QemuRuntimeRecoveryRequired(
                    "foreign runtime entry was captured and restored"
                )
            # Retain the exact tiny tombstone rather than reintroducing an
            # unverifiable name-to-unlink race.  The canonical runtime pathname
            # is free, the evidence is idempotent, and no foreign entry is ever
            # deleted.
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _cleanup_runtime_paths(
        self,
        journal: RuntimeLaunchJournal,
        transient_pidfile: PidfileIdentity | None,
        transient_socket: QMPSocketIdentity | None,
    ) -> None:
        names = _checkpoint_names(journal)
        if "private_paths_prepared" not in names:
            # No runtime pathname mutation was legal before this checkpoint.
            # Inspecting a canonical path here would grant authority over an
            # unrelated entry that merely happens to use the planned name.
            return
        runtime_parent, _ = journal.private_directory_identities()
        persisted_pidfile = (
            journal.pidfile_identity()
            if "pidfile_verified" in names
            else None
        )
        persisted_socket = (
            journal.qmp_socket_identity()
            if "qmp_greeting_observed" in names
            else None
        )
        if (
            persisted_pidfile is not None
            and transient_pidfile is not None
            and (
                persisted_pidfile.device_id != transient_pidfile.device_id
                or persisted_pidfile.inode != transient_pidfile.inode
            )
        ):
            raise QemuRuntimeRecoveryRequired(
                "captured pidfile differs from durable pidfile identity"
            )
        if (
            persisted_socket is not None
            and transient_socket is not None
            and (
                persisted_socket.device_id != transient_socket.device_id
                or persisted_socket.inode != transient_socket.inode
            )
        ):
            raise QemuRuntimeRecoveryRequired(
                "captured QMP socket differs from durable socket identity"
            )
        pid_path = Path(journal.intent.pid_file)
        socket_path = Path(journal.intent.qmp_socket)
        try:
            pid_present = pid_path.lstat()
        except FileNotFoundError:
            pid_present = None
        try:
            socket_present = socket_path.lstat()
        except FileNotFoundError:
            socket_present = None
        selected_pidfile = persisted_pidfile or transient_pidfile
        selected_socket = persisted_socket or transient_socket
        if pid_present is not None or selected_pidfile is not None:
            self._retire_runtime_entry(
                pid_path,
                selected_pidfile,
                operation_id=journal.intent.operation_id,
                socket_entry=False,
                parent_identity=runtime_parent,
            )
        if socket_present is not None or selected_socket is not None:
            self._retire_runtime_entry(
                socket_path,
                selected_socket,
                operation_id=journal.intent.operation_id,
                socket_entry=True,
                parent_identity=runtime_parent,
            )

    def _resolve_replayed_qemu(
        self,
        journal: RuntimeLaunchJournal,
    ) -> tuple[ObservedProcessIdentity | None, _ExitProof | None]:
        """Resolve a journaled guard/target without ever signaling ambiguity."""

        names = _checkpoint_names(journal)
        if "process_observed" in names:
            expected = journal.observed_process_at("process_observed")
            absent = self._prove_process_absence(expected)
            return (None, absent) if absent is not None else (expected, None)
        if "qemu_guard_spawned" not in names:
            return None, None

        guard = journal.observed_process_at("qemu_guard_spawned")
        try:
            current = observe_process(guard.pid)
        except ProcessUnavailableError:
            return None, None
        if (
            current.start_time_ticks != guard.start_time_ticks
            or current.proc_device_id != guard.proc_device_id
            or current.proc_inode != guard.proc_inode
        ):
            # The original guard/target task is gone; the reused PID is foreign.
            return None, None
        try:
            revalidate_process(guard)
        except ProcessUnavailableError:
            return None, None
        except ProcessIdentityMismatchError:
            try:
                candidate = observe_process(
                    guard.pid,
                    expected_executable_identity=_journal_executable(
                        journal.intent
                    ),
                    expected_argv=journal.intent.executed_argv,
                )
            except ProcessUnavailableError:
                return None, None
            except ProcessIdentityMismatchError as exc:
                raise QemuRuntimeRecoveryRequired(
                    "QEMU guard task changed into an unowned executable"
                ) from exc
            if (
                candidate.start_time_ticks != guard.start_time_ticks
                or candidate.proc_device_id != guard.proc_device_id
                or candidate.proc_inode != guard.proc_inode
            ):
                return None, None
            return candidate, None
        else:
            # The target was never observed to exec.  Exact pidfd termination of
            # the still-Python guard cannot signal a foreign QEMU.
            _verify_qemu_guard_topology(journal, guard)
            self._terminate_replayed(guard)
            return None, None

    def _recover_operation(
        self,
        operation: OperationRecord,
    ) -> RuntimeRecoveryResult:
        journal = RuntimeLaunchJournal.replay(
            operation,
            self._registry.list_checkpoints(operation.operation_id),
        )
        if journal.latest_checkpoint in {
            "lifecycle_committed",
            "completion_observed",
        }:
            try:
                live = self._adopt(journal)
            except BaseException:
                # Adoption is optional; exact cleanup remains a valid P4
                # recovery outcome.  Cleanup below still refuses ambiguity.
                pass
            else:
                journal = self._complete_launch(journal)
                self._live[journal.intent.vm_id] = live
                return RuntimeRecoveryResult(
                    operation_id=journal.intent.operation_id,
                    vm_id=journal.intent.vm_id,
                    latest_checkpoint=journal.latest_checkpoint,
                    disposition="adopted",
                )

        child_spawned = journal.latest_checkpoint not in {
            None,
            "intent_persisted",
            "private_paths_prepared",
            "executable_pinned",
        }
        ambiguous: BaseException | None = None
        names = _checkpoint_names(journal)
        qemu: ObservedProcessIdentity | None = None
        qemu_exit: _ExitProof | None = None
        try:
            qemu, qemu_exit = self._resolve_replayed_qemu(journal)
        except BaseException as exc:
            ambiguous = exc
        transient_pidfile: PidfileIdentity | None = None
        transient_socket: QMPSocketIdentity | None = None
        if qemu is not None and ambiguous is None:
            try:
                probe = self._probe_live_runtime(journal, qemu)
                transient_pidfile = probe.pidfile
                transient_socket = probe.socket_identity
                probe.qmp.close()
                qemu_exit = self._terminate_replayed(qemu)
            except BaseException as exc:
                ambiguous = exc
        if "log_guard_spawned" in names:
            log_guard = journal.observed_process_at("log_guard_spawned")
            try:
                self._settle_log_guard(journal, log_guard)
            except BaseException as exc:
                ambiguous = ambiguous or exc
        if ambiguous is not None:
            self._mark_recovery_required(
                journal,
                "exact_child_cleanup_unproved",
                cleanup_state="orphaned",
            )
            raise QemuRuntimeRecoveryRequired(
                "runtime child cleanup remains ambiguous"
            ) from ambiguous
        try:
            self._cleanup_runtime_paths(
                journal,
                transient_pidfile,
                transient_socket,
            )
        except BaseException as exc:
            self._mark_recovery_required(
                journal,
                "runtime_path_cleanup_unproved",
            )
            raise QemuRuntimeRecoveryRequired(
                "runtime path cleanup remains ambiguous"
            ) from exc
        self._mark_cleaned(
            journal,
            child_spawned=child_spawned,
            reason="recovered_by_exact_cleanup",
            qemu_exit=qemu_exit,
        )
        return RuntimeRecoveryResult(
            operation_id=journal.intent.operation_id,
            vm_id=journal.intent.vm_id,
            latest_checkpoint=journal.latest_checkpoint,
            disposition="cleaned",
        )

    def _adopt(self, journal: RuntimeLaunchJournal) -> _LiveRuntime:
        intent = journal.intent
        qemu = journal.observed_process_at("process_observed")
        probe = self._probe_live_runtime(journal, qemu)
        try:
            stored = self._registry.get_vm(intent.vm_id)
            durable_process = self._registry.get_process_identity(
                intent.process_record_id
            )
            durable_disk = self._registry.get_disk_runtime_observation(
                intent.disk_observation_id
            )
            protocol_process = probe.process.to_protocol_identity()
            if (
                stored is None
                or stored.record.state is not VMState.QMP_RUNNING
                or stored.record.boot_id != intent.boot_id
                or stored.record.process is None
                or stored.record.process.pid != probe.process.pid
                or stored.record.process.start_time_ticks
                != probe.process.start_time_ticks
                or stored.record.process.executable_sha256
                != probe.process.executable_sha256
                or durable_process is None
                or durable_process.process_record_id
                != intent.process_record_id
                or durable_process.vm_id != intent.vm_id
                or durable_process.generation != intent.generation
                or durable_process.boot_id != intent.boot_id
                or durable_process.process != protocol_process
                or not durable_process.active
                or durable_process.operation_id != intent.operation_id
                or durable_disk is None
                or durable_disk.observation_id
                != intent.disk_observation_id
                or durable_disk.disk_id != intent.disk_id
                or durable_disk.vm_id != intent.vm_id
                or durable_disk.generation != intent.generation
                or durable_disk.boot_id != intent.boot_id
                or durable_disk.process_record_id
                != intent.process_record_id
                or durable_disk.operation_id != intent.operation_id
            ):
                raise QemuRuntimeRecoveryRequired(
                    "registry runtime identity disagrees with live QEMU"
                )
            return _LiveRuntime(
                journal=journal,
                process=probe.process,
                log_guard=probe.log_guard,
                qmp=probe.qmp,
                qmp_evidence=probe.qmp_evidence,
                storage_graph_evidence=probe.storage_graph_evidence,
                disk_evidence=probe.disk_evidence,
                events=probe.events,
            )
        except BaseException:
            probe.qmp.close()
            raise

    def _record_completed_runtime_loss(
        self,
        journal: RuntimeLaunchJournal,
        qemu_exit: _ExitProof,
    ) -> RuntimeRecoveryResult:
        """Correct current VM truth without rewriting historical launch success."""

        intent = journal.intent
        expected = journal.observed_process_at("process_observed")
        if qemu_exit.process != expected:
            raise QemuRuntimeRecoveryRequired(
                "completed runtime exit is not the journaled QEMU"
            )
        names = _checkpoint_names(journal)
        if "log_guard_spawned" in names:
            self._settle_log_guard(
                journal,
                journal.observed_process_at("log_guard_spawned"),
            )
        self._cleanup_runtime_paths(journal, None, None)

        stored = self._registry.get_vm(intent.vm_id)
        if (
            stored is None
            or stored.record.state is not VMState.QMP_RUNNING
            or stored.record.boot_id != intent.boot_id
        ):
            raise QemuRuntimeRecoveryRequired(
                "completed runtime loss disagrees with current VM state"
            )
        protocol_process = expected.to_protocol_identity()
        when = _utc_now_after(
            max(stored.record.updated_at, qemu_exit.observed_absent_at)
        )
        cause_id = uuid4()
        cause = TransitionCause(
            code=_RUNTIME_FAILURE_CODE,
            detail="Completed P4 runtime process exit was observed during restart",
            occurred_at=when,
            event_id=cause_id,
        )
        evidence = TransitionEvidence(
            evidence_id=uuid4(),
            observed_at=when,
            facts={
                "intent_digest": stored.record.intent_digest,
                "error_code": _RUNTIME_FAILURE_CODE,
                "cause_event_id": str(cause_id),
                "process_record_id": str(intent.process_record_id),
            },
            vm_id=intent.vm_id,
            generation=intent.generation,
            boot_id=intent.boot_id,
            intent_digest=stored.record.intent_digest,
        )
        failed = stored.record.transition(
            VMState.ERROR,
            evidence,
            cause=cause,
        )
        with self._registry.write_transaction() as transaction:
            durable = transaction.get_process_identity(
                intent.process_record_id
            )
            if (
                durable is None
                or durable.vm_id != intent.vm_id
                or durable.generation != intent.generation
                or durable.boot_id != intent.boot_id
                or durable.process != protocol_process
                or durable.operation_id != intent.operation_id
                or not durable.active
            ):
                raise QemuRuntimeRecoveryRequired(
                    "completed runtime active process authority is inconsistent"
                )
            transaction.retire_process_identity(
                intent.process_record_id,
                intent.vm_id,
                protocol_process,
                generation=intent.generation,
                boot_id=intent.boot_id,
                original_operation_id=intent.operation_id,
                observed_absent_at=qemu_exit.observed_absent_at,
                reason="completed_runtime_exit",
                exit_evidence_id=uuid5(
                    intent.operation_id,
                    "completed-runtime-process-exit",
                ),
            )
            transaction.compare_and_swap_vm(
                intent.vm_id,
                stored.revision,
                failed,
            )
            transaction.append_lifecycle_event(
                intent.vm_id,
                failed.transitions[-1],
                operation_id=intent.operation_id,
            )
        return RuntimeRecoveryResult(
            operation_id=intent.operation_id,
            vm_id=intent.vm_id,
            latest_checkpoint=journal.latest_checkpoint,
            disposition="runtime_exit_observed",
        )

    def recover_incomplete_launches(self) -> tuple[RuntimeRecoveryResult, ...]:
        """Settle every internal launch before generic service recovery."""

        with self._lock:
            outcomes: list[RuntimeRecoveryResult] = []
            for operation in self._registry.list_operations(
                incomplete_only=True
            ):
                if operation.kind != RUNTIME_LAUNCH_KIND:
                    continue
                outcomes.append(self._recover_operation(operation))
            return tuple(outcomes)

    def reconcile_completed_launches(
        self,
    ) -> tuple[RuntimeRecoveryResult, ...]:
        """Re-adopt completed QMP-running children before live-disk checks."""

        with self._lock:
            outcomes: list[RuntimeRecoveryResult] = []
            for operation in self._registry.list_operations(status="completed"):
                if operation.kind != RUNTIME_LAUNCH_KIND:
                    continue
                journal = RuntimeLaunchJournal.replay(
                    operation,
                    self._registry.list_checkpoints(operation.operation_id),
                )
                if journal.intent.vm_id in self._live:
                    continue
                stored = self._registry.get_vm(journal.intent.vm_id)
                if (
                    stored is None
                    or stored.record.state is not VMState.QMP_RUNNING
                    or stored.record.boot_id != journal.intent.boot_id
                ):
                    continue
                try:
                    live = self._adopt(journal)
                except BaseException as adoption_error:
                    expected = journal.observed_process_at(
                        "process_observed"
                    )
                    absent = self._prove_process_absence(expected)
                    if absent is None:
                        raise QemuRuntimeRecoveryRequired(
                            "completed runtime is live but cannot be re-adopted"
                        ) from adoption_error
                    outcomes.append(
                        self._record_completed_runtime_loss(
                            journal,
                            absent,
                        )
                    )
                else:
                    self._live[journal.intent.vm_id] = live
                    outcomes.append(
                        RuntimeRecoveryResult(
                            operation_id=journal.intent.operation_id,
                            vm_id=journal.intent.vm_id,
                            latest_checkpoint=journal.latest_checkpoint,
                            disposition="adopted",
                        )
                    )
            return tuple(outcomes)

    def shutdown(self) -> None:
        """Release daemon-local QMP sessions without stopping persistent VMs."""

        with self._lock:
            live = tuple(self._live.values())
            self._live.clear()
        failures: list[BaseException] = []
        for runtime in live:
            try:
                runtime.qmp.close()
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise QemuRuntimeError(
                "one or more adopted QMP sessions could not be closed"
            ) from failures[0]


__all__ = [
    "QemuRuntimeError",
    "QemuRuntimeOwner",
    "QemuRuntimePolicy",
    "QemuRuntimeRecoveryRequired",
    "RuntimeLaunchResult",
    "RuntimeRecoveryResult",
]
