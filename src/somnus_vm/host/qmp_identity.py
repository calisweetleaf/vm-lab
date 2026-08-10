"""Strict QEMU 8.2 identity observations composed from public QMP results.

This module is deliberately narrower than a VM lifecycle owner.  It validates
and normalizes the five QMP query results needed to identify one already-owned
QEMU process, binds the reported block graph to storage-owned inode evidence,
proves reported vCPU threads belong to that process through ``/proc``, and
produces immutable, domain-separated evidence digests.

It does not connect to QMP, launch QEMU, mutate the registry, or claim VM
readiness.  A future aggregate must still combine this evidence with the
pidfd-bound process observation and perform the final event/status fence before
constructing a canonical ``RuntimeObservation``.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from uuid import UUID

from somnus_protocol._validation import (
    ProtocolValidationError,
    canonical_json,
    thaw_json,
    validate_json_value,
)

from .qmp import QMPGreeting, QMPProtocolError, QMPResponse, QMPVersion


QMP_IDENTITY_SCHEMA: Final[str] = "somnus.qmp-identity.v1"
QMP_IDENTITY_EVIDENCE_SCHEMA: Final[str] = (
    "somnus.qmp-identity-evidence.v1"
)
QMP_GREETING_SCHEMA: Final[str] = "somnus.qmp-greeting.v1"
QMP_ROOT_BLOCK_NODE: Final[str] = "somnus-disk"
QMP_ROOT_BLOCK_DEVICE: Final[str] = "somnus-root-disk"
QMP_X86_TARGET: Final[str] = "x86_64"
MIN_QMP_STABILIZATION_NS: Final[int] = 500_000_000

_MAX_COMMAND_ID: Final[int] = 2**63 - 1
_MAX_JSON_ITEMS: Final[int] = 8_192
_MAX_JSON_STRING: Final[int] = 4_096
_MAX_JSON_BYTES: Final[int] = 1_048_576
_MAX_PATH_BYTES: Final[int] = 4_096
_MAX_PROC_STATUS_BYTES: Final[int] = 65_536
_MAX_VCPUS: Final[int] = 256
_MAX_IMAGE_LAYERS: Final[int] = 64
_MAX_INTEGER: Final[int] = 2**63 - 1
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}", re.ASCII)
_CAPABILITY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9_.-]*",
    re.ASCII,
)
_QOM_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/(?:[A-Za-z0-9_.:@+-]+(?:\[[0-9]+\])?)"
    r"(?:/(?:[A-Za-z0-9_.:@+-]+(?:\[[0-9]+\])?))*",
    re.ASCII,
)
_MACHINE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._-]{1,63}",
    re.ASCII,
)
_NODE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9._-]{0,127}",
    re.ASCII,
)

QMP_OVERLAY_FDSET_ID: Final[int] = 1
QMP_BASE_FDSET_ID: Final[int] = 2
QMP_OVERLAY_FDSET_OPAQUE: Final[str] = "somnus-overlay-rw"
QMP_BASE_FDSET_OPAQUE: Final[str] = "somnus-base-ro"
QMP_OVERLAY_FILE_NODE: Final[str] = "somnus-overlay-file"
QMP_BASE_FILE_NODE: Final[str] = "somnus-base-file"
QMP_BASE_FORMAT_NODE: Final[str] = "somnus-base-qcow2"

_BLOCK_INFO_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "device",
        "inserted",
        "locked",
        "qdev",
        "removable",
        "type",
    }
)
_BLOCK_INFO_OPTIONAL: Final[frozenset[str]] = frozenset(
    {"io-status", "tray_open"}
)
_BLOCK_DEVICE_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "backing_file_depth",
        "bps",
        "bps_rd",
        "bps_wr",
        "cache",
        "detect_zeroes",
        "drv",
        "encrypted",
        "file",
        "image",
        "iops",
        "iops_rd",
        "iops_wr",
        "node-name",
        "ro",
        "write_threshold",
    }
)
_BLOCK_DEVICE_OPTIONAL: Final[frozenset[str]] = frozenset(
    {
        "backing_file",
        "bps_max",
        "bps_max_length",
        "bps_rd_max",
        "bps_rd_max_length",
        "bps_wr_max",
        "bps_wr_max_length",
        "dirty-bitmaps",
        "group",
        "iops_max",
        "iops_max_length",
        "iops_rd_max",
        "iops_rd_max_length",
        "iops_size",
        "iops_wr_max",
        "iops_wr_max_length",
    }
)
_BLOCK_COUNTER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "bps",
        "bps_rd",
        "bps_wr",
        "iops",
        "iops_rd",
        "iops_wr",
        "write_threshold",
    }
)
_BLOCK_OPTIONAL_COUNTER_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "bps_max",
        "bps_max_length",
        "bps_rd_max",
        "bps_rd_max_length",
        "bps_wr_max",
        "bps_wr_max_length",
        "iops_max",
        "iops_max_length",
        "iops_rd_max",
        "iops_rd_max_length",
        "iops_size",
        "iops_wr_max",
        "iops_wr_max_length",
    }
)
_IMAGE_REQUIRED: Final[frozenset[str]] = frozenset(
    {"filename", "format", "virtual-size"}
)
_IMAGE_OPTIONAL: Final[frozenset[str]] = frozenset(
    {
        "actual-size",
        "backing-filename",
        "backing-filename-format",
        "backing-image",
        "cluster-size",
        "compressed",
        "dirty-flag",
        "encrypted",
        "format-specific",
        "full-backing-filename",
        "snapshots",
    }
)
_CPU_REQUIRED: Final[frozenset[str]] = frozenset(
    {"cpu-index", "props", "qom-path", "target", "thread-id"}
)
_CPU_PROP_REQUIRED: Final[frozenset[str]] = frozenset(
    {"core-id", "socket-id", "thread-id"}
)
_CPU_PROP_OPTIONAL: Final[frozenset[str]] = frozenset(
    {"die-id", "node-id"}
)


class QMPIdentityError(QMPProtocolError):
    """A QMP reply cannot establish the exact planned process identity."""


def _identity_error(label: str) -> QMPIdentityError:
    return QMPIdentityError(f"{label} is invalid")


def _strict_object(
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _identity_error(label)
    keys = frozenset(value)
    if any(not isinstance(key, str) for key in value):
        raise _identity_error(label)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise _identity_error(f"{label} field set")
    return value


def _strict_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise _identity_error(label)
    return value


def _strict_int(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = _MAX_INTEGER,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise _identity_error(label)
    return value


def _strict_string(
    value: object,
    label: str,
    *,
    minimum: int = 1,
    maximum: int = _MAX_JSON_STRING,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) < minimum
        or len(value) > maximum
        or any(
            ord(character) < 32
            or ord(character) == 127
            or unicodedata.category(character) == "Cs"
            for character in value
        )
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise _identity_error(label)
    return value


def _strict_sha256(value: object, label: str) -> str:
    selected = _strict_string(value, label, minimum=64, maximum=64)
    if _SHA256_PATTERN.fullmatch(selected) is None:
        raise _identity_error(label)
    return selected


def _strict_uuid(value: object, label: str) -> UUID:
    if isinstance(value, UUID):
        selected = value
    elif isinstance(value, str):
        try:
            selected = UUID(value)
        except ValueError as exc:
            raise _identity_error(label) from exc
        if str(selected) != value:
            raise _identity_error(label)
    else:
        raise _identity_error(label)
    if selected.int == 0:
        raise _identity_error(label)
    return selected


def _strict_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise _identity_error(label)
    try:
        path = Path(value)
        text = os.fspath(path)
    except (TypeError, ValueError) as exc:
        raise _identity_error(label) from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or PurePosixPath(text) != path
        or "\x00" in text
        or len(os.fsencode(path)) > _MAX_PATH_BYTES
        or any(
            ord(character) < 32
            or ord(character) == 127
            or unicodedata.category(character) == "Cs"
            for character in text
        )
    ):
        raise _identity_error(label)
    try:
        resolved = path.resolve(strict=True)
        details = path.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise _identity_error(label) from exc
    if resolved != path or not stat.S_ISREG(details.st_mode):
        raise _identity_error(label)
    return path


def _domain_sha256(domain: bytes, value: object, label: str) -> str:
    try:
        encoded = canonical_json(
            value,
            label,
            max_depth=16,
            max_items=_MAX_JSON_ITEMS,
            max_string=_MAX_JSON_STRING,
            max_bytes=_MAX_JSON_BYTES,
        ).encode("utf-8")
    except (ProtocolValidationError, TypeError, ValueError) as exc:
        raise QMPIdentityError(f"{label} cannot be encoded canonically") from exc
    return hashlib.sha256(domain + b"\0" + encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class QMPBlockLayer:
    """Storage-owned path and inode identity for one expected image layer."""

    path: Path
    format: str
    virtual_size_bytes: int
    device_id: int
    inode: int
    qmp_filename: str | None = None

    def __post_init__(self) -> None:
        selected = _strict_path(self.path, "block layer path")
        selected_format = _strict_string(
            self.format,
            "block layer format",
            maximum=32,
        )
        if selected_format not in {"qcow2", "raw"}:
            raise _identity_error("block layer format")
        _strict_int(
            self.virtual_size_bytes,
            "block layer virtual_size_bytes",
            minimum=1,
        )
        _strict_int(self.device_id, "block layer device_id", minimum=1)
        _strict_int(self.inode, "block layer inode", minimum=1)
        if self.qmp_filename is not None:
            _strict_string(
                self.qmp_filename,
                "block layer QMP filename",
                maximum=_MAX_PATH_BYTES,
            )
        details = selected.stat(follow_symlinks=False)
        if details.st_dev != self.device_id or details.st_ino != self.inode:
            raise QMPIdentityError(
                "block layer inode disagrees with storage-owned evidence"
            )
        object.__setattr__(self, "path", selected)
        object.__setattr__(self, "format", selected_format)

    def to_dict(self) -> dict[str, object]:
        encoded = {
            "device_id": self.device_id,
            "format": self.format,
            "inode": self.inode,
            "path": os.fspath(self.path),
            "virtual_size_bytes": self.virtual_size_bytes,
        }
        if self.qmp_filename is not None and self.qmp_filename != os.fspath(
            self.path
        ):
            encoded["qmp_filename"] = self.qmp_filename
        return encoded


@dataclass(frozen=True, slots=True)
class QMPFdsetIdentity:
    """One exact QEMU inherited storage fdset and its opaque role."""

    fdset_id: int
    opaque: str
    fd: int


@dataclass(frozen=True, slots=True)
class QMPNamedBlockNodeIdentity:
    """One normalized node in the four-node fd-bound storage graph."""

    node_name: str
    driver: str
    read_only: bool
    reported_file: str


@dataclass(frozen=True, slots=True)
class QMPBlockstatsNodeIdentity:
    """One recursive query-blockstats node edge observation."""

    node_name: str
    parent_node: str | None
    backing_node: str | None


@dataclass(frozen=True, slots=True)
class QMPBlockContract:
    """Exact single-root block attachment expected from the hardened plan."""

    layers: tuple[QMPBlockLayer, ...]
    storage_chain_sha256: str
    node_name: str = QMP_ROOT_BLOCK_NODE
    qdev_id: str = QMP_ROOT_BLOCK_DEVICE

    def __post_init__(self) -> None:
        layers = tuple(self.layers)
        if (
            not layers
            or len(layers) > _MAX_IMAGE_LAYERS
            or any(not isinstance(layer, QMPBlockLayer) for layer in layers)
            or len({layer.path for layer in layers}) != len(layers)
        ):
            raise _identity_error("block contract layers")
        if layers[0].format != "qcow2":
            raise QMPIdentityError("planned root block layer must be qcow2")
        if self.node_name != QMP_ROOT_BLOCK_NODE:
            raise QMPIdentityError("planned root block node is not canonical")
        if self.qdev_id != QMP_ROOT_BLOCK_DEVICE:
            raise QMPIdentityError("planned root block qdev is not canonical")
        _strict_sha256(
            self.storage_chain_sha256,
            "storage chain sha256",
        )
        object.__setattr__(self, "layers", layers)


@dataclass(frozen=True, slots=True)
class QMPIdentityExpectation:
    """Canonical VM/process/storage facts against which QMP is compared."""

    vm_id: UUID
    name: str
    process_pid: int
    vcpus: int
    block: QMPBlockContract
    target: str = QMP_X86_TARGET

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "vm_id",
            _strict_uuid(self.vm_id, "expected vm_id"),
        )
        name = _strict_string(
            self.name,
            "expected VM name",
            maximum=63,
            pattern=_MACHINE_NAME_PATTERN,
        )
        object.__setattr__(self, "name", name)
        _strict_int(
            self.process_pid,
            "expected process pid",
            minimum=1,
            maximum=2**31 - 1,
        )
        _strict_int(
            self.vcpus,
            "expected vcpus",
            minimum=1,
            maximum=_MAX_VCPUS,
        )
        if not isinstance(self.block, QMPBlockContract):
            raise _identity_error("expected block contract")
        if self.target != QMP_X86_TARGET:
            raise QMPIdentityError("only the QEMU 8.2 x86_64 profile is supported")


@dataclass(frozen=True, slots=True)
class QMPStatusIdentity:
    running: bool
    status: str

    def __post_init__(self) -> None:
        if self.running is not True or self.status != "running":
            raise QMPIdentityError("QMP status is not coherently running")

    def to_dict(self) -> dict[str, object]:
        return {"running": self.running, "status": self.status}


@dataclass(frozen=True, slots=True)
class QMPBlockIdentity:
    device: str
    qdev_id: str
    node_name: str
    storage_chain_sha256: str
    layers: tuple[QMPBlockLayer, ...]

    def __post_init__(self) -> None:
        _strict_string(
            self.device,
            "QMP block device",
            minimum=0,
            maximum=256,
        )
        if self.qdev_id != QMP_ROOT_BLOCK_DEVICE:
            raise QMPIdentityError("QMP block qdev does not match the plan")
        if self.node_name != QMP_ROOT_BLOCK_NODE:
            raise QMPIdentityError("QMP block node does not match the plan")
        _strict_sha256(
            self.storage_chain_sha256,
            "QMP storage chain sha256",
        )
        layers = tuple(self.layers)
        if (
            not layers
            or len(layers) > _MAX_IMAGE_LAYERS
            or any(not isinstance(layer, QMPBlockLayer) for layer in layers)
            or len({layer.path for layer in layers}) != len(layers)
            or layers[0].format != "qcow2"
        ):
            raise _identity_error("QMP block layers")
        object.__setattr__(self, "layers", layers)

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "layers": [layer.to_dict() for layer in self.layers],
            "node_name": self.node_name,
            "qdev_id": self.qdev_id,
            "storage_chain_sha256": self.storage_chain_sha256,
        }


@dataclass(frozen=True, slots=True)
class QMPDiskRuntimeEvidence:
    """Mutable root-disk facts from one fully normalized ``query-block``.

    ``block`` retains the exact immutable path/inode/chain binding established
    by :class:`QMPBlockContract`.  The remaining fields are QMP-owned runtime
    measurements suitable for the append-only disk observation boundary; no
    value is inferred from host allocation or a concurrent ``qemu-img`` call.
    """

    block: QMPBlockIdentity
    qemu_actual_size_bytes: int
    dirty: bool
    corrupt: bool

    def __post_init__(self) -> None:
        if not isinstance(self.block, QMPBlockIdentity):
            raise _identity_error("QMP disk runtime block identity")
        _strict_int(
            self.qemu_actual_size_bytes,
            "QMP disk runtime actual size",
        )
        _strict_bool(self.dirty, "QMP disk runtime dirty flag")
        corrupt = _strict_bool(
            self.corrupt,
            "QMP disk runtime corrupt flag",
        )
        if corrupt:
            raise QMPIdentityError(
                "QMP disk runtime evidence cannot accept corruption"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "block": self.block.to_dict(),
            "corrupt": self.corrupt,
            "dirty": self.dirty,
            "qemu_actual_size_bytes": self.qemu_actual_size_bytes,
        }


@dataclass(frozen=True, slots=True)
class QMPCPUIdentity:
    cpu_index: int
    qom_path: str
    process_thread_id: int
    target: str
    socket_id: int
    core_id: int
    thread_id: int
    die_id: int | None
    node_id: int | None

    def __post_init__(self) -> None:
        _strict_int(
            self.cpu_index,
            "QMP CPU index",
            maximum=_MAX_VCPUS - 1,
        )
        _strict_string(
            self.qom_path,
            "QMP CPU QOM path",
            maximum=1_024,
            pattern=_QOM_PATH_PATTERN,
        )
        _strict_int(
            self.process_thread_id,
            "QMP CPU process thread ID",
            minimum=1,
            maximum=2**31 - 1,
        )
        if self.target != QMP_X86_TARGET:
            raise _identity_error("QMP CPU target")
        for value, label in (
            (self.socket_id, "QMP CPU socket ID"),
            (self.core_id, "QMP CPU core ID"),
            (self.thread_id, "QMP CPU topology thread ID"),
        ):
            _strict_int(value, label)
        for value, label in (
            (self.die_id, "QMP CPU die ID"),
            (self.node_id, "QMP CPU node ID"),
        ):
            if value is not None:
                _strict_int(value, label)

    def to_dict(self) -> dict[str, object]:
        return {
            "core_id": self.core_id,
            "cpu_index": self.cpu_index,
            "die_id": self.die_id,
            "node_id": self.node_id,
            "process_thread_id": self.process_thread_id,
            "qom_path": self.qom_path,
            "socket_id": self.socket_id,
            "target": self.target,
            "thread_id": self.thread_id,
        }


@dataclass(frozen=True, slots=True)
class QMPIdentitySnapshot:
    """One normalized semantic identity sampled from five QMP commands."""

    vm_id: UUID
    name: str
    peer_pid: int
    qemu_version: tuple[int, int, int]
    qemu_package: str
    qmp_capabilities: tuple[str, ...]
    greeting_sha256: str
    status: QMPStatusIdentity
    block: QMPBlockIdentity
    cpus: tuple[QMPCPUIdentity, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "vm_id",
            _strict_uuid(self.vm_id, "snapshot vm_id"),
        )
        _strict_string(
            self.name,
            "snapshot name",
            maximum=63,
            pattern=_MACHINE_NAME_PATTERN,
        )
        _strict_int(
            self.peer_pid,
            "snapshot peer pid",
            minimum=1,
            maximum=2**31 - 1,
        )
        version = tuple(self.qemu_version)
        if (
            len(version) != 3
            or any(
                not isinstance(item, int)
                or isinstance(item, bool)
                or item < 0
                or item > 2**31 - 1
                for item in version
            )
            or version[0:2] != (8, 2)
        ):
            raise QMPIdentityError("snapshot QEMU version is unsupported")
        object.__setattr__(self, "qemu_version", version)
        _strict_string(
            self.qemu_package,
            "snapshot QEMU package",
            minimum=0,
            maximum=4_096,
        )
        try:
            capabilities = tuple(self.qmp_capabilities)
        except TypeError as exc:
            raise _identity_error("snapshot QMP capabilities") from exc
        if (
            any(
                not isinstance(capability, str)
                or len(capability) > 128
                or _CAPABILITY_PATTERN.fullmatch(capability) is None
                for capability in capabilities
            )
            or capabilities != tuple(sorted(capabilities))
            or len(set(capabilities)) != len(capabilities)
        ):
            raise _identity_error("snapshot QMP capabilities")
        object.__setattr__(self, "qmp_capabilities", capabilities)
        _strict_sha256(self.greeting_sha256, "snapshot greeting sha256")
        if not isinstance(self.status, QMPStatusIdentity):
            raise _identity_error("snapshot status")
        if not isinstance(self.block, QMPBlockIdentity):
            raise _identity_error("snapshot block")
        cpus = tuple(self.cpus)
        if (
            not cpus
            or len(cpus) > _MAX_VCPUS
            or any(not isinstance(cpu, QMPCPUIdentity) for cpu in cpus)
            or tuple(cpu.cpu_index for cpu in cpus) != tuple(range(len(cpus)))
        ):
            raise _identity_error("snapshot cpus")
        object.__setattr__(self, "cpus", cpus)

    def to_dict(self) -> dict[str, object]:
        return {
            "block": self.block.to_dict(),
            "cpus": [cpu.to_dict() for cpu in self.cpus],
            "greeting_sha256": self.greeting_sha256,
            "name": self.name,
            "peer_pid": self.peer_pid,
            "qemu_package": self.qemu_package,
            "qemu_version": list(self.qemu_version),
            "qmp_capabilities": list(self.qmp_capabilities),
            "schema": QMP_IDENTITY_SCHEMA,
            "status": self.status.to_dict(),
            "vm_id": str(self.vm_id),
        }

    @property
    def snapshot_sha256(self) -> str:
        return _domain_sha256(
            b"somnus.qmp-identity.v1",
            self.to_dict(),
            "QMP identity snapshot",
        )


@dataclass(frozen=True, slots=True)
class QMPIdentitySample:
    """One time-positioned command set retaining QMP correlation IDs."""

    snapshot: QMPIdentitySnapshot
    monotonic_ns: int
    command_ids: tuple[int, int, int, int, int]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, QMPIdentitySnapshot):
            raise _identity_error("QMP identity sample snapshot")
        _strict_int(
            self.monotonic_ns,
            "QMP identity sample monotonic_ns",
            minimum=1,
        )
        command_ids = tuple(self.command_ids)
        if (
            len(command_ids) != 5
            or len(set(command_ids)) != 5
            or any(
                not isinstance(command_id, int)
                or isinstance(command_id, bool)
                or command_id < 1
                or command_id > _MAX_COMMAND_ID
                for command_id in command_ids
            )
            or command_ids != tuple(sorted(command_ids))
        ):
            raise _identity_error("QMP identity sample command IDs")
        object.__setattr__(self, "command_ids", command_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "command_ids": list(self.command_ids),
            "monotonic_ns": self.monotonic_ns,
            "snapshot_sha256": self.snapshot.snapshot_sha256,
        }


@dataclass(frozen=True, slots=True)
class QMPIdentityEvidence:
    """Two stable observations keyed by the future RuntimeObservation ID."""

    observation_id: UUID
    vm_id: UUID
    first: QMPIdentitySample
    second: QMPIdentitySample

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_id",
            _strict_uuid(self.observation_id, "QMP observation_id"),
        )
        object.__setattr__(
            self,
            "vm_id",
            _strict_uuid(self.vm_id, "QMP evidence vm_id"),
        )
        if not isinstance(self.first, QMPIdentitySample) or not isinstance(
            self.second,
            QMPIdentitySample,
        ):
            raise _identity_error("QMP identity evidence samples")
        if (
            self.first.snapshot.vm_id != self.vm_id
            or self.second.snapshot.vm_id != self.vm_id
        ):
            raise QMPIdentityError("QMP evidence samples target another VM")
        if self.first.snapshot != self.second.snapshot:
            raise QMPIdentityError(
                "QMP identity changed during the stabilization interval"
            )
        if self.stabilization_ns < MIN_QMP_STABILIZATION_NS:
            raise QMPIdentityError(
                "QMP identity stabilization interval is too short"
            )
        if min(self.second.command_ids) <= max(self.first.command_ids):
            raise QMPIdentityError(
                "QMP identity command IDs are not monotonically separated"
            )

    @property
    def stabilization_ns(self) -> int:
        return self.second.monotonic_ns - self.first.monotonic_ns

    def to_dict(self) -> dict[str, object]:
        return {
            "first": self.first.to_dict(),
            "observation_id": str(self.observation_id),
            "schema": QMP_IDENTITY_EVIDENCE_SCHEMA,
            "second": self.second.to_dict(),
            "snapshot": self.first.snapshot.to_dict(),
            "stabilization_ns": self.stabilization_ns,
            "vm_id": str(self.vm_id),
        }

    @property
    def evidence_sha256(self) -> str:
        return _domain_sha256(
            b"somnus.qmp-identity-evidence.v1",
            self.to_dict(),
            "QMP identity evidence",
        )


def greeting_sha256(greeting: QMPGreeting) -> str:
    """Return one order-independent semantic digest for a QEMU 8.2 greeting."""

    if not isinstance(greeting, QMPGreeting) or not isinstance(
        greeting.version,
        QMPVersion,
    ):
        raise _identity_error("QMP greeting")
    version = greeting.version
    for value, label in (
        (version.major, "QMP greeting major"),
        (version.minor, "QMP greeting minor"),
        (version.micro, "QMP greeting micro"),
    ):
        _strict_int(value, label, maximum=2**31 - 1)
    if (version.major, version.minor) != (8, 2):
        raise QMPIdentityError("QMP greeting is outside the QEMU 8.2.x profile")
    package = _strict_string(
        version.package,
        "QMP greeting package",
        minimum=0,
        maximum=4_096,
    )
    try:
        capabilities = tuple(greeting.capabilities)
    except TypeError as exc:
        raise _identity_error("QMP greeting capabilities") from exc
    if (
        len(capabilities) > 256
        or any(
            not isinstance(capability, str)
            or len(capability) > 128
            or _CAPABILITY_PATTERN.fullmatch(capability) is None
            for capability in capabilities
        )
        or len(set(capabilities)) != len(capabilities)
    ):
        raise _identity_error("QMP greeting capabilities")
    payload = {
        "capabilities": sorted(capabilities),
        "schema": QMP_GREETING_SCHEMA,
        "version": {
            "major": version.major,
            "micro": version.micro,
            "minor": version.minor,
            "package": package,
        },
    }
    return _domain_sha256(
        b"somnus.qmp-greeting.v1",
        payload,
        "QMP greeting identity",
    )


def _response_value(response: QMPResponse, command: str) -> object:
    if not isinstance(response, QMPResponse):
        raise _identity_error(f"{command} response")
    if response.command != command:
        raise QMPIdentityError(f"{command} response command is mismatched")
    _strict_int(
        response.command_id,
        f"{command} response command ID",
        minimum=1,
        maximum=_MAX_COMMAND_ID,
    )
    try:
        plain = thaw_json(response.value)
        validate_json_value(
            plain,
            f"{command} response",
            max_depth=16,
            max_items=_MAX_JSON_ITEMS,
            max_string=_MAX_JSON_STRING,
        )
    except (ProtocolValidationError, TypeError, ValueError, RecursionError) as exc:
        raise _identity_error(f"{command} response value") from exc
    return plain


def normalize_qmp_fdsets(response: QMPResponse) -> tuple[QMPFdsetIdentity, ...]:
    """Normalize the exact overlay/base fdsets inherited by QEMU."""

    value = _response_value(response, "query-fdsets")
    if not isinstance(value, list) or len(value) != 2:
        raise QMPIdentityError("query-fdsets must report exactly two fdsets")
    selected: list[QMPFdsetIdentity] = []
    expected = {
        QMP_OVERLAY_FDSET_ID: QMP_OVERLAY_FDSET_OPAQUE,
        QMP_BASE_FDSET_ID: QMP_BASE_FDSET_OPAQUE,
    }
    for index, item in enumerate(value):
        row = _strict_object(
            item,
            required=frozenset({"fdset-id", "fds"}),
            label=f"query-fdsets entry[{index}]",
        )
        fdset_id = _strict_int(
            row["fdset-id"],
            f"query-fdsets entry[{index}] fdset-id",
            minimum=1,
            maximum=2**31 - 1,
        )
        if fdset_id not in expected or fdset_id in {
            item.fdset_id for item in selected
        }:
            raise QMPIdentityError("query-fdsets IDs are not exactly 1 and 2")
        fds = row["fds"]
        if not isinstance(fds, list) or len(fds) != 1:
            raise QMPIdentityError("each query-fdsets entry requires one fd")
        fd_row = _strict_object(
            fds[0],
            required=frozenset({"fd", "opaque"}),
            label=f"query-fdsets entry[{index}] fd",
        )
        opaque = _strict_string(
            fd_row["opaque"],
            f"query-fdsets entry[{index}] opaque",
            maximum=128,
        )
        if opaque != expected[fdset_id]:
            raise QMPIdentityError("query-fdsets opaque role is unexpected")
        fd = _strict_int(
            fd_row["fd"],
            f"query-fdsets entry[{index}] fd",
            minimum=0,
            maximum=2**31 - 1,
        )
        selected.append(QMPFdsetIdentity(fdset_id, opaque, fd))
    if len({entry.fd for entry in selected}) != 2:
        raise QMPIdentityError("query-fdsets descriptors are duplicated")
    return tuple(sorted(selected, key=lambda entry: entry.fdset_id))


def normalize_qmp_named_block_nodes(
    response: QMPResponse,
) -> tuple[QMPNamedBlockNodeIdentity, ...]:
    """Normalize the exact four-node fd-bound qcow2 graph inventory."""

    value = _response_value(response, "query-named-block-nodes")
    if not isinstance(value, list) or len(value) != 4:
        raise QMPIdentityError(
            "query-named-block-nodes must report exactly four nodes"
        )
    selected: dict[str, QMPNamedBlockNodeIdentity] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise QMPIdentityError(
                f"query-named-block-nodes entry[{index}] must be an object"
            )
        row = dict(item)
        if not {"node-name", "drv", "ro", "file"}.issubset(row):
            raise QMPIdentityError(
                "query-named-block-nodes entry lacks stable required fields"
            )
        node_name = _strict_string(
            row["node-name"],
            f"query-named-block-nodes entry[{index}] node-name",
            maximum=128,
            pattern=_NODE_NAME_PATTERN,
        )
        if node_name in selected:
            raise QMPIdentityError("named block node names are duplicated")
        driver = _strict_string(
            row["drv"],
            f"query-named-block-nodes {node_name} driver",
            maximum=32,
        )
        read_only = _strict_bool(
            row["ro"],
            f"query-named-block-nodes {node_name} ro",
        )
        reported_file = _strict_string(
            row["file"],
            f"query-named-block-nodes {node_name} file",
            maximum=_MAX_PATH_BYTES,
        )
        selected[node_name] = QMPNamedBlockNodeIdentity(
            node_name=node_name,
            driver=driver,
            read_only=read_only,
            reported_file=reported_file,
        )
    expected_names = {
        QMP_OVERLAY_FILE_NODE,
        QMP_BASE_FILE_NODE,
        QMP_BASE_FORMAT_NODE,
        QMP_ROOT_BLOCK_NODE,
    }
    if set(selected) != expected_names:
        raise QMPIdentityError("named block node inventory is not canonical")
    overlay = selected[QMP_OVERLAY_FILE_NODE]
    base_file = selected[QMP_BASE_FILE_NODE]
    base = selected[QMP_BASE_FORMAT_NODE]
    root = selected[QMP_ROOT_BLOCK_NODE]
    if (
        overlay.driver != "file"
        or overlay.read_only
        or overlay.reported_file != "/dev/fdset/1"
        or base_file.driver != "file"
        or not base_file.read_only
        or base_file.reported_file != "/dev/fdset/2"
        or base.driver != "qcow2"
        or not base.read_only
        or root.driver != "qcow2"
        or root.read_only
    ):
        raise QMPIdentityError("named block node inventory or roles are invalid")
    return tuple(selected[name] for name in sorted(selected))


def normalize_qmp_blockstats(
    response: QMPResponse,
) -> tuple[QMPBlockstatsNodeIdentity, ...]:
    """Normalize recursive blockstats parent/backing edges for the graph."""

    value = _response_value(response, "query-blockstats")
    if not isinstance(value, list) or len(value) != 1:
        raise QMPIdentityError("query-blockstats must report one root graph")
    selected: dict[str, QMPBlockstatsNodeIdentity] = {}
    active: set[str] = set()

    def visit(item: object, label: str) -> str:
        if not isinstance(item, Mapping) or "node-name" not in item:
            raise QMPIdentityError(f"{label} lacks a stable node-name")
        row = dict(item)
        node_name = _strict_string(
            row["node-name"],
            f"{label} node-name",
            maximum=128,
            pattern=_NODE_NAME_PATTERN,
        )
        if node_name in active:
            raise QMPIdentityError("query-blockstats graph contains a cycle")
        if node_name in selected:
            raise QMPIdentityError("query-blockstats nodes are duplicated")
        active.add(node_name)
        parent = row.get("parent")
        backing = row.get("backing")
        parent_name = visit(parent, f"{label} parent") if parent is not None else None
        backing_name = (
            visit(backing, f"{label} backing")
            if backing is not None
            else None
        )
        active.remove(node_name)
        selected[node_name] = QMPBlockstatsNodeIdentity(
            node_name=node_name,
            parent_node=parent_name,
            backing_node=backing_name,
        )
        return node_name

    root_name = visit(value[0], "query-blockstats root")
    expected = {
        QMP_ROOT_BLOCK_NODE: (QMP_OVERLAY_FILE_NODE, QMP_BASE_FORMAT_NODE),
        QMP_BASE_FORMAT_NODE: (QMP_BASE_FILE_NODE, None),
        QMP_OVERLAY_FILE_NODE: (None, None),
        QMP_BASE_FILE_NODE: (None, None),
    }
    if root_name != QMP_ROOT_BLOCK_NODE or set(selected) != set(expected):
        raise QMPIdentityError("query-blockstats graph inventory is not canonical")
    for node_name, edges in expected.items():
        node = selected[node_name]
        if (node.parent_node, node.backing_node) != edges:
            raise QMPIdentityError("query-blockstats graph edges are invalid")
    return tuple(selected[name] for name in sorted(selected))


def normalize_qmp_status(response: QMPResponse) -> QMPStatusIdentity:
    """Strictly normalize one final ``query-status`` fence response.

    This public parser is intentionally independent of five-command identity
    sampling so the lifecycle aggregate can issue a last status query
    immediately before commit without duplicating or weakening the QEMU 8.2
    response contract.
    """

    row = _strict_object(
        _response_value(response, "query-status"),
        required=frozenset({"running", "status"}),
        label="query-status result",
    )
    running = _strict_bool(row["running"], "query-status running")
    status = _strict_string(
        row["status"],
        "query-status status",
        maximum=64,
    )
    return QMPStatusIdentity(running=running, status=status)


def _normalize_name(response: QMPResponse, expected: str) -> str:
    row = _strict_object(
        _response_value(response, "query-name"),
        required=frozenset({"name"}),
        label="query-name result",
    )
    name = _strict_string(
        row["name"],
        "query-name name",
        maximum=63,
        pattern=_MACHINE_NAME_PATTERN,
    )
    if name != expected:
        raise QMPIdentityError("QMP name does not match the planned VM")
    return name


def _normalize_uuid(response: QMPResponse, expected: UUID) -> UUID:
    row = _strict_object(
        _response_value(response, "query-uuid"),
        required=frozenset({"UUID"}),
        label="query-uuid result",
    )
    selected = _strict_uuid(row["UUID"], "query-uuid UUID")
    if selected != expected:
        raise QMPIdentityError("QMP UUID does not match the planned VM")
    return selected


def _validate_current_layer(layer: QMPBlockLayer, label: str) -> None:
    selected = _strict_path(layer.path, label)
    details = selected.stat(follow_symlinks=False)
    if details.st_dev != layer.device_id or details.st_ino != layer.inode:
        raise QMPIdentityError(
            "QMP-reported block path no longer matches storage-owned evidence"
        )


def _expected_qmp_filename(layer: QMPBlockLayer) -> str:
    """Return the wire filename while keeping host path authority separate."""

    return layer.qmp_filename or os.fspath(layer.path)


def _validate_format_specific(
    value: object,
    expected: QMPBlockLayer,
    label: str,
) -> bool:
    if expected.format != "qcow2":
        raise QMPIdentityError(
            "format-specific metadata is unsupported for this block layer"
        )
    row = _strict_object(
        value,
        required=frozenset({"data", "type"}),
        label=label,
    )
    if row["type"] != "qcow2":
        raise QMPIdentityError("image format-specific type is mismatched")
    data = _strict_object(
        row["data"],
        required=frozenset(
            {"compat", "compression-type", "refcount-bits"}
        ),
        optional=frozenset(
            {
                "bitmaps",
                "corrupt",
                "data-file",
                "data-file-raw",
                "encrypt",
                "extended-l2",
                "lazy-refcounts",
            }
        ),
        label=f"{label} data",
    )
    _strict_string(data["compat"], f"{label} compat", maximum=32)
    _strict_int(
        data["refcount-bits"],
        f"{label} refcount-bits",
        minimum=1,
        maximum=64,
    )
    compression = _strict_string(
        data["compression-type"],
        f"{label} compression-type",
        maximum=16,
    )
    if compression not in {"zlib", "zstd"}:
        raise _identity_error(f"{label} compression-type")
    for field_name in ("extended-l2", "lazy-refcounts"):
        if field_name in data:
            _strict_bool(data[field_name], f"{label} {field_name}")
    corrupt = (
        _strict_bool(data["corrupt"], f"{label} corrupt")
        if "corrupt" in data
        else False
    )
    if corrupt:
        raise QMPIdentityError("QMP image metadata reports corruption")
    if "data-file" in data or "data-file-raw" in data:
        raise QMPIdentityError(
            "QMP image has an unplanned external qcow2 data file"
        )
    if "encrypt" in data:
        raise QMPIdentityError("QMP image has unplanned encryption metadata")
    if "bitmaps" in data:
        bitmaps = data["bitmaps"]
        if not isinstance(bitmaps, list) or bitmaps:
            raise QMPIdentityError(
                "QMP image has unplanned persistent bitmap metadata"
            )
    return corrupt


def _validate_image(
    value: object,
    layers: tuple[QMPBlockLayer, ...],
    index: int,
) -> None:
    label = f"query-block image[{index}]"
    row = _strict_object(
        value,
        required=_IMAGE_REQUIRED,
        optional=_IMAGE_OPTIONAL,
        label=label,
    )
    expected = layers[index]
    _validate_current_layer(expected, f"{label} path")
    filename = _strict_string(
        row["filename"],
        f"{label} filename",
        maximum=_MAX_PATH_BYTES,
    )
    if filename != _expected_qmp_filename(expected):
        raise QMPIdentityError("QMP image filename is not the planned layer")
    image_format = _strict_string(
        row["format"],
        f"{label} format",
        maximum=32,
    )
    if image_format != expected.format:
        raise QMPIdentityError("QMP image format is not the planned format")
    virtual_size = _strict_int(
        row["virtual-size"],
        f"{label} virtual-size",
        minimum=1,
    )
    if virtual_size != expected.virtual_size_bytes:
        raise QMPIdentityError(
            "QMP image virtual size disagrees with storage evidence"
        )
    if "actual-size" in row:
        _strict_int(row["actual-size"], f"{label} actual-size")
    if "dirty-flag" in row:
        _strict_bool(row["dirty-flag"], f"{label} dirty-flag")
    if "cluster-size" in row:
        _strict_int(
            row["cluster-size"],
            f"{label} cluster-size",
            minimum=1,
        )
    if "encrypted" in row and _strict_bool(
        row["encrypted"],
        f"{label} encrypted",
    ):
        raise QMPIdentityError("QMP image is unexpectedly encrypted")
    if "compressed" in row:
        _strict_bool(row["compressed"], f"{label} compressed")
    if "snapshots" in row:
        snapshots = row["snapshots"]
        if not isinstance(snapshots, list) or snapshots:
            raise QMPIdentityError("QMP image has unplanned internal snapshots")
    if "format-specific" in row:
        _validate_format_specific(
            row["format-specific"],
            expected,
            f"{label} format-specific",
        )

    has_backing = index + 1 < len(layers)
    backing_keys = frozenset(
        {
            "backing-filename",
            "backing-filename-format",
            "backing-image",
            "full-backing-filename",
        }
    )
    if has_backing:
        if not backing_keys.issubset(row):
            raise QMPIdentityError("QMP image backing chain is incomplete")
        backing = layers[index + 1]
        for field_name in ("backing-filename", "full-backing-filename"):
            path = _strict_string(
                row[field_name],
                f"{label} {field_name}",
                maximum=_MAX_PATH_BYTES,
            )
            if path != _expected_qmp_filename(backing):
                raise QMPIdentityError(
                    "QMP image backing path disagrees with storage evidence"
                )
        if row["backing-filename-format"] != backing.format:
            raise QMPIdentityError(
                "QMP image backing format disagrees with storage evidence"
            )
        _validate_image(row["backing-image"], layers, index + 1)
    elif backing_keys & frozenset(row):
        raise QMPIdentityError("QMP image reports an unexpected backing layer")


def _normalize_block(
    response: QMPResponse,
    contract: QMPBlockContract,
) -> QMPBlockIdentity:
    value = _response_value(response, "query-block")
    if not isinstance(value, list) or len(value) != 1:
        raise QMPIdentityError(
            "query-block must report exactly one virtual block device"
        )
    row = _strict_object(
        value[0],
        required=_BLOCK_INFO_REQUIRED,
        optional=_BLOCK_INFO_OPTIONAL,
        label="query-block device",
    )
    device = _strict_string(
        row["device"],
        "query-block device name",
        minimum=0,
        maximum=256,
    )
    qdev = _strict_string(
        row["qdev"],
        "query-block qdev",
        maximum=256,
    )
    if qdev != contract.qdev_id:
        raise QMPIdentityError("QMP qdev does not match the hardened plan")
    if row["type"] != "unknown":
        raise QMPIdentityError("QMP compatibility block type is unexpected")
    if _strict_bool(row["removable"], "query-block removable"):
        raise QMPIdentityError("planned root block device is removable")
    if _strict_bool(row["locked"], "query-block locked"):
        raise QMPIdentityError("planned root block device is media-locked")
    if "tray_open" in row:
        raise QMPIdentityError("non-removable root block reports a tray")
    if "io-status" in row and row["io-status"] != "ok":
        raise QMPIdentityError("QMP root block I/O status is not ok")

    inserted = _strict_object(
        row["inserted"],
        required=_BLOCK_DEVICE_REQUIRED,
        optional=_BLOCK_DEVICE_OPTIONAL,
        label="query-block inserted device",
    )
    top = contract.layers[0]
    top_path = _strict_string(
        inserted["file"],
        "query-block inserted file",
        maximum=_MAX_PATH_BYTES,
    )
    if top_path != _expected_qmp_filename(top):
        raise QMPIdentityError("QMP inserted file is not the planned disk")
    if inserted["node-name"] != contract.node_name:
        raise QMPIdentityError("QMP block node name is not canonical")
    if inserted["drv"] != "qcow2":
        raise QMPIdentityError("QMP root block driver is not qcow2")
    if _strict_bool(inserted["ro"], "query-block read-only"):
        raise QMPIdentityError("QMP root block is unexpectedly read-only")
    if _strict_bool(inserted["encrypted"], "query-block encrypted"):
        raise QMPIdentityError("QMP root block is unexpectedly encrypted")
    if inserted["detect_zeroes"] not in {"off", "on", "unmap"}:
        raise _identity_error("query-block detect_zeroes")
    depth = _strict_int(
        inserted["backing_file_depth"],
        "query-block backing_file_depth",
        maximum=_MAX_IMAGE_LAYERS - 1,
    )
    if depth != len(contract.layers) - 1:
        raise QMPIdentityError(
            "QMP backing depth disagrees with storage-owned chain"
        )
    if depth:
        if "backing_file" not in inserted:
            raise QMPIdentityError("QMP backing file is missing")
        backing_path = _strict_string(
            inserted["backing_file"],
            "query-block backing_file",
            maximum=_MAX_PATH_BYTES,
        )
        if backing_path != _expected_qmp_filename(contract.layers[1]):
            raise QMPIdentityError(
                "QMP backing file disagrees with storage-owned chain"
            )
    elif "backing_file" in inserted:
        raise QMPIdentityError("QMP reports an unexpected backing file")
    for field_name in _BLOCK_COUNTER_FIELDS:
        _strict_int(
            inserted[field_name],
            f"query-block {field_name}",
        )
    for field_name in _BLOCK_OPTIONAL_COUNTER_FIELDS & frozenset(inserted):
        _strict_int(
            inserted[field_name],
            f"query-block {field_name}",
        )
    cache = _strict_object(
        inserted["cache"],
        required=frozenset({"direct", "no-flush", "writeback"}),
        label="query-block cache",
    )
    for field_name in ("direct", "no-flush", "writeback"):
        _strict_bool(cache[field_name], f"query-block cache {field_name}")
    if "group" in inserted:
        raise QMPIdentityError("QMP root block has an unplanned throttle group")
    if "dirty-bitmaps" in inserted:
        bitmaps = inserted["dirty-bitmaps"]
        if not isinstance(bitmaps, list) or bitmaps:
            raise QMPIdentityError(
                "QMP root block has unplanned dirty bitmap state"
            )
    _validate_image(inserted["image"], contract.layers, 0)
    for index, layer in enumerate(contract.layers):
        _validate_current_layer(layer, f"block contract layer[{index}]")
    return QMPBlockIdentity(
        device=device,
        qdev_id=qdev,
        node_name=contract.node_name,
        storage_chain_sha256=contract.storage_chain_sha256,
        layers=contract.layers,
    )


def extract_qmp_disk_runtime_evidence(
    response: QMPResponse,
    contract: QMPBlockContract,
) -> QMPDiskRuntimeEvidence:
    """Extract strict live root-disk facts from a correlated QMP response.

    Full block normalization runs first, so the measurement cannot be detached
    from the single planned qdev, node name, backing chain, canonical paths, or
    storage-owned inodes.  QEMU's top-image ``actual-size`` is mandatory here
    even though it remains optional for identity-only sampling.  Per the QAPI
    optional-field contract, an absent ``dirty-flag`` means ``False``; a
    present value must be a real boolean.  Corruption is exposed only as the
    proven ``False`` result of validated qcow2 format-specific metadata.
    """

    if not isinstance(contract, QMPBlockContract):
        raise TypeError("contract must be QMPBlockContract")
    block = _normalize_block(response, contract)
    value = _response_value(response, "query-block")
    if not isinstance(value, list) or len(value) != 1:
        raise QMPIdentityError(
            "query-block must report exactly one virtual block device"
        )
    row = _strict_object(
        value[0],
        required=_BLOCK_INFO_REQUIRED,
        optional=_BLOCK_INFO_OPTIONAL,
        label="query-block device",
    )
    inserted = _strict_object(
        row["inserted"],
        required=_BLOCK_DEVICE_REQUIRED,
        optional=_BLOCK_DEVICE_OPTIONAL,
        label="query-block inserted device",
    )
    image = _strict_object(
        inserted["image"],
        required=_IMAGE_REQUIRED,
        optional=_IMAGE_OPTIONAL,
        label="query-block image[0]",
    )
    if "actual-size" not in image:
        raise QMPIdentityError(
            "QMP root image actual-size is required for runtime evidence"
        )
    actual_size = _strict_int(
        image["actual-size"],
        "query-block image[0] actual-size",
    )
    dirty = (
        _strict_bool(
            image["dirty-flag"],
            "query-block image[0] dirty-flag",
        )
        if "dirty-flag" in image
        else False
    )
    corrupt = (
        _validate_format_specific(
            image["format-specific"],
            contract.layers[0],
            "query-block image[0] format-specific",
        )
        if "format-specific" in image
        else False
    )
    return QMPDiskRuntimeEvidence(
        block=block,
        qemu_actual_size_bytes=actual_size,
        dirty=dirty,
        corrupt=corrupt,
    )


def _read_task_identity(process_pid: int, thread_id: int) -> None:
    path = Path("/proc") / str(process_pid) / "task" / str(thread_id) / "status"
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise QMPIdentityError(
            "QMP vCPU thread is not owned by the measured process"
        ) from exc
    try:
        block = os.read(descriptor, _MAX_PROC_STATUS_BYTES + 1)
    except OSError as exc:
        raise QMPIdentityError("QMP vCPU thread status cannot be read") from exc
    finally:
        os.close(descriptor)
    if len(block) > _MAX_PROC_STATUS_BYTES:
        raise QMPIdentityError("QMP vCPU thread status exceeds the bound")
    try:
        text = block.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QMPIdentityError("QMP vCPU thread status is not UTF-8") from exc

    def values(field: str) -> list[str]:
        prefix = f"{field}:\t"
        return [
            line[len(prefix) :]
            for line in text.splitlines()
            if line.startswith(prefix)
        ]

    pid_values = values("Pid")
    tgid_values = values("Tgid")
    if pid_values != [str(thread_id)] or tgid_values != [str(process_pid)]:
        raise QMPIdentityError(
            "QMP vCPU thread does not belong to the measured process"
        )


def _normalize_cpus(
    response: QMPResponse,
    expectation: QMPIdentityExpectation,
) -> tuple[QMPCPUIdentity, ...]:
    value = _response_value(response, "query-cpus-fast")
    if not isinstance(value, list) or len(value) != expectation.vcpus:
        raise QMPIdentityError("QMP vCPU count disagrees with the plan")
    selected: list[QMPCPUIdentity] = []
    for index, item in enumerate(value):
        row = _strict_object(
            item,
            required=_CPU_REQUIRED,
            label=f"query-cpus-fast cpu[{index}]",
        )
        cpu_index = _strict_int(
            row["cpu-index"],
            f"query-cpus-fast cpu[{index}] index",
            maximum=expectation.vcpus - 1,
        )
        qom_path = _strict_string(
            row["qom-path"],
            f"query-cpus-fast cpu[{index}] qom-path",
            maximum=1_024,
            pattern=_QOM_PATH_PATTERN,
        )
        process_thread_id = _strict_int(
            row["thread-id"],
            f"query-cpus-fast cpu[{index}] process thread-id",
            minimum=1,
            maximum=2**31 - 1,
        )
        target = _strict_string(
            row["target"],
            f"query-cpus-fast cpu[{index}] target",
            maximum=32,
        )
        if target != expectation.target:
            raise QMPIdentityError("QMP vCPU target disagrees with the plan")
        props = _strict_object(
            row["props"],
            required=_CPU_PROP_REQUIRED,
            optional=_CPU_PROP_OPTIONAL,
            label=f"query-cpus-fast cpu[{index}] props",
        )
        socket_id = _strict_int(
            props["socket-id"],
            f"query-cpus-fast cpu[{index}] socket-id",
        )
        core_id = _strict_int(
            props["core-id"],
            f"query-cpus-fast cpu[{index}] core-id",
        )
        thread_id = _strict_int(
            props["thread-id"],
            f"query-cpus-fast cpu[{index}] topology thread-id",
        )
        die_id = (
            None
            if "die-id" not in props
            else _strict_int(
                props["die-id"],
                f"query-cpus-fast cpu[{index}] die-id",
            )
        )
        node_id = (
            None
            if "node-id" not in props
            else _strict_int(
                props["node-id"],
                f"query-cpus-fast cpu[{index}] node-id",
            )
        )
        if (
            socket_id != 0
            or core_id != cpu_index
            or thread_id != 0
            or die_id not in {None, 0}
            or node_id not in {None, 0}
        ):
            raise QMPIdentityError(
                "QMP vCPU topology disagrees with the explicit SMP plan"
            )
        _read_task_identity(expectation.process_pid, process_thread_id)
        selected.append(
            QMPCPUIdentity(
                cpu_index=cpu_index,
                qom_path=qom_path,
                process_thread_id=process_thread_id,
                target=target,
                socket_id=socket_id,
                core_id=core_id,
                thread_id=thread_id,
                die_id=die_id,
                node_id=node_id,
            )
        )
    cpus = tuple(sorted(selected, key=lambda cpu: cpu.cpu_index))
    if tuple(cpu.cpu_index for cpu in cpus) != tuple(
        range(expectation.vcpus)
    ):
        raise QMPIdentityError("QMP vCPU indices are incomplete or duplicated")
    if len({cpu.process_thread_id for cpu in cpus}) != len(cpus):
        raise QMPIdentityError("QMP vCPU host thread IDs are duplicated")
    if len({cpu.qom_path for cpu in cpus}) != len(cpus):
        raise QMPIdentityError("QMP vCPU QOM paths are duplicated")
    return cpus


def normalize_qmp_identity_sample(
    *,
    expectation: QMPIdentityExpectation,
    greeting: QMPGreeting,
    peer_pid: int,
    status_response: QMPResponse,
    name_response: QMPResponse,
    uuid_response: QMPResponse,
    block_response: QMPResponse,
    cpus_response: QMPResponse,
    monotonic_ns: int,
) -> QMPIdentitySample:
    """Normalize one coherent five-command QMP observation.

    The caller owns command ordering and event fencing.  The returned sample
    retains all five correlation IDs so two samples can later prove they came
    from distinct, monotonically ordered command sets.
    """

    if not isinstance(expectation, QMPIdentityExpectation):
        raise TypeError("expectation must be QMPIdentityExpectation")
    selected_peer_pid = _strict_int(
        peer_pid,
        "QMP peer pid",
        minimum=1,
        maximum=2**31 - 1,
    )
    if selected_peer_pid != expectation.process_pid:
        raise QMPIdentityError(
            "QMP peer PID does not match the measured QEMU process"
        )
    digest = greeting_sha256(greeting)
    status = normalize_qmp_status(status_response)
    name = _normalize_name(name_response, expectation.name)
    vm_id = _normalize_uuid(uuid_response, expectation.vm_id)
    block = _normalize_block(block_response, expectation.block)
    cpus = _normalize_cpus(cpus_response, expectation)
    responses = (
        status_response,
        name_response,
        uuid_response,
        block_response,
        cpus_response,
    )
    command_ids = tuple(response.command_id for response in responses)
    if len(set(command_ids)) != len(command_ids):
        raise QMPIdentityError("QMP identity command IDs are duplicated")
    version = greeting.version
    capabilities = tuple(sorted(greeting.capabilities))
    snapshot = QMPIdentitySnapshot(
        vm_id=vm_id,
        name=name,
        peer_pid=selected_peer_pid,
        qemu_version=(version.major, version.minor, version.micro),
        qemu_package=version.package,
        qmp_capabilities=capabilities,
        greeting_sha256=digest,
        status=status,
        block=block,
        cpus=cpus,
    )
    return QMPIdentitySample(
        snapshot=snapshot,
        monotonic_ns=_strict_int(
            monotonic_ns,
            "QMP identity sample monotonic_ns",
            minimum=1,
        ),
        command_ids=command_ids,  # type: ignore[arg-type]
    )


def compose_qmp_identity_evidence(
    *,
    observation_id: UUID,
    vm_id: UUID,
    first: QMPIdentitySample,
    second: QMPIdentitySample,
) -> QMPIdentityEvidence:
    """Bind two stable samples to the future RuntimeObservation identifier."""

    return QMPIdentityEvidence(
        observation_id=observation_id,
        vm_id=vm_id,
        first=first,
        second=second,
    )


__all__ = [
    "MIN_QMP_STABILIZATION_NS",
    "QMP_BASE_FDSET_ID",
    "QMP_BASE_FDSET_OPAQUE",
    "QMP_BASE_FILE_NODE",
    "QMP_BASE_FORMAT_NODE",
    "QMPBlockContract",
    "QMPBlockIdentity",
    "QMPBlockLayer",
    "QMPBlockstatsNodeIdentity",
    "QMPDiskRuntimeEvidence",
    "QMPFdsetIdentity",
    "QMPCPUIdentity",
    "QMPIdentityError",
    "QMPIdentityEvidence",
    "QMPIdentityExpectation",
    "QMPIdentitySample",
    "QMPIdentitySnapshot",
    "QMPNamedBlockNodeIdentity",
    "QMP_OVERLAY_FDSET_ID",
    "QMP_OVERLAY_FDSET_OPAQUE",
    "QMP_OVERLAY_FILE_NODE",
    "QMPStatusIdentity",
    "QMP_IDENTITY_EVIDENCE_SCHEMA",
    "QMP_IDENTITY_SCHEMA",
    "QMP_ROOT_BLOCK_DEVICE",
    "QMP_ROOT_BLOCK_NODE",
    "QMP_X86_TARGET",
    "compose_qmp_identity_evidence",
    "extract_qmp_disk_runtime_evidence",
    "greeting_sha256",
    "normalize_qmp_status",
    "normalize_qmp_identity_sample",
    "normalize_qmp_blockstats",
    "normalize_qmp_fdsets",
    "normalize_qmp_named_block_nodes",
]
