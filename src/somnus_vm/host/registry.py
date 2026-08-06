"""Single-writer, versioned SQLite authority for persistent VM state.

Source: PLAN.md P2-005 through P2-017, P3 storage authority, and P4 first unit
Integrated: 2026-08-05
Purpose: Owns normalized durable VM records, immutable disk materialization
    baselines, append-only disk runtime observations, and resumable operation
    intent without importing QEMU, sockets, CLI dispatch, or guest modules.
IO: One process-held advisory writer lock and one stdlib SQLite database.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import urllib.parse
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final
from uuid import UUID, uuid4, uuid5

from somnus_protocol import (
    ProcessIdentity,
    SnapshotReference,
    TransitionRecord,
    VMRecord,
    VMState,
)

from .images import ImageManifest, ImageManifestError


REGISTRY_SCHEMA_VERSION: Final[int] = 5
LEGACY_REGISTRY_SCHEMA_VERSION: Final[int] = 1
# Kept as the exported P2 compatibility version.  P3 callers already import
# this name when constructing exact version-2 migration fixtures.
PREVIOUS_REGISTRY_SCHEMA_VERSION: Final[int] = 2
P3_REGISTRY_SCHEMA_VERSION: Final[int] = 3
P4_REGISTRY_SCHEMA_VERSION: Final[int] = 4
DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5_000
DEFAULT_MAX_RECORD_BYTES: Final[int] = 1_048_576
MAX_OPERATION_BYTES: Final[int] = 262_144
MAX_EVENT_BYTES: Final[int] = 262_144
MAX_DISK_OBSERVATION_BYTES: Final[int] = 16_384
MAX_PROCESS_EXIT_OBSERVATION_BYTES: Final[int] = 16_384
_MACHINE_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_.-]{0,63}$"
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "pending",
        "running",
        "completed",
        "failed",
        "canceled",
        "recovery_required",
    }
)
_TERMINAL_OPERATION_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "canceled"}
)
_DISK_TOKEN_NAMESPACE: Final[UUID] = UUID(
    "4c1a13ae-aa6b-4bed-998f-eb0ce1227007"
)


class RegistryError(RuntimeError):
    """Base class for closed registry failures."""


class RegistryIntegrityError(RegistryError):
    """Raised when durable bytes, hashes, schema, or SQLite integrity disagree."""


class RegistryReadOnlyError(RegistryError):
    """Raised when mutation is attempted outside read-write mode."""


class RegistryWriterBusyError(RegistryError):
    """Raised when another process holds the only writer lock."""


class RegistryConflictError(RegistryError):
    """Raised when a normalized unique owner or idempotency fact conflicts."""


class RegistryRevisionConflict(RegistryConflictError):
    """Raised when compare-and-swap observes a different VM revision."""


class RegistryMigrationError(RegistryError):
    """Raised after a migration fails with its pre-migration backup preserved."""

    def __init__(self, message: str, *, backup_path: Path | None = None) -> None:
        super().__init__(message)
        self.backup_path = backup_path


class RegistryNotFoundError(RegistryError):
    """Raised when a required durable owner row does not exist."""


class RegistryMode(str, Enum):
    """Observed authority mode for one opened registry."""

    READ_WRITE = "read_write"
    READ_ONLY = "read_only"
    READ_ONLY_RECOVERY = "read_only_recovery"


@dataclass(frozen=True, slots=True)
class RegistryStatus:
    mode: RegistryMode
    schema_version: int | None
    journal_mode: str | None
    integrity_ok: bool
    issue: str | None = None
    migration_backup: Path | None = None


@dataclass(frozen=True, slots=True)
class StoredVM:
    record: VMRecord
    revision: int
    active: bool


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    operation_id: UUID
    vm_id: UUID | None
    generation: int | None
    request_hash: str
    status: str
    result: bytes
    result_sha256: str


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: UUID
    vm_id: UUID | None
    generation: int | None
    kind: str
    idempotency_key: str
    request_hash: str
    status: str
    intent: bytes
    intent_sha256: str
    owned_resources: bytes
    owned_resources_sha256: str
    result: bytes
    result_sha256: str
    latest_checkpoint: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PortLeaseRecord:
    lease_id: UUID
    vm_id: UUID
    generation: int
    role: str
    host: str
    port: int
    active: bool
    operation_id: UUID | None
    released_by_operation_id: UUID | None


@dataclass(frozen=True, slots=True)
class BaseImageRecord:
    image_id: UUID
    path: str
    content_sha256: str
    manifest: bytes
    manifest_sha256: str
    byte_size: int
    format: str
    virtual_size_bytes: int
    device_id: int
    inode: int
    file_size_bytes: int
    allocated_size_bytes: int
    mode: int
    qemu_img_version: str
    chain_sha256: str
    active: bool
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class DiskRecord:
    disk_id: UUID
    vm_id: UUID
    path: str
    generation: int
    ownership: str
    active: bool
    operation_id: UUID | None
    released_by_operation_id: UUID | None
    ownership_token: UUID
    materialized: bool
    base_image_id: UUID | None
    base_image_sha256: str | None
    virtual_size_bytes: int | None
    format: str | None
    device_id: int | None
    inode: int | None
    file_size_bytes: int | None
    allocated_size_bytes: int | None
    chain_sha256: str | None
    marker_sha256: str | None
    qemu_img_version: str | None
    materialized_by_operation_id: UUID | None


@dataclass(frozen=True, slots=True)
class LifecycleEventRecord:
    event_id: UUID
    vm_id: UUID
    sequence: int
    transition: TransitionRecord
    operation_id: UUID | None


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    snapshot: SnapshotReference
    active: bool
    operation_id: UUID | None


@dataclass(frozen=True, slots=True)
class ProcessIdentityRecord:
    process_record_id: UUID
    vm_id: UUID
    generation: int
    boot_id: UUID
    process: ProcessIdentity
    active: bool
    operation_id: UUID | None


@dataclass(frozen=True, slots=True)
class ProcessExitObservation:
    """Immutable proof that one exact recorded process was observed absent."""

    exit_evidence_id: UUID
    process_record_id: UUID
    vm_id: UUID
    generation: int
    boot_id: UUID
    process: ProcessIdentity
    original_operation_id: UUID
    observed_absent_at: datetime
    reason: str
    canonical_bytes: bytes
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class DiskRuntimeObservation:
    """One immutable observation of mutable qcow2 runtime truth.

    ``DiskRecord.file_size_bytes`` and ``allocated_size_bytes`` remain the P3
    materialization baseline.  Runtime growth is represented only by appending
    this record; it never rewrites the disk owner or the VM lifecycle record.
    """

    observation_id: UUID
    disk_id: UUID
    vm_id: UUID
    generation: int
    observed_at: datetime
    file_size_bytes: int
    allocated_size_bytes: int
    qemu_actual_size_bytes: int
    dirty: bool
    corrupt: bool
    boot_id: UUID | None
    process_record_id: UUID | None
    quiesced: bool
    canonical_bytes: bytes
    canonical_sha256: str
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class OperationCheckpoint:
    operation_id: UUID
    ordinal: int
    name: str
    payload: bytes
    payload_sha256: str
    created_at: datetime


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(value: object, label: str) -> UUID:
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str) and len(value) <= 36:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise RegistryConflictError(f"{label} must be a UUID") from exc
    else:
        raise RegistryConflictError(f"{label} must be a UUID")
    if parsed.int == 0:
        raise RegistryConflictError(f"{label} cannot be nil")
    return parsed


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not _MACHINE_TOKEN.fullmatch(value):
        raise RegistryConflictError(f"{label} must be a bounded machine token")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RegistryConflictError(f"{label} must be a lowercase SHA256")
    return value


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryConflictError(f"{label} must be boolean")
    return value


def _non_negative_int(
    value: object,
    label: str,
    *,
    positive: bool = False,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < (1 if positive else 0)
        or value > 2**63 - 1
    ):
        qualifier = "positive" if positive else "non-negative"
        raise RegistryConflictError(
            f"{label} must be a {qualifier} 64-bit integer"
        )
    return value


def _utc_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise RegistryConflictError(f"{label} must be a timezone-aware datetime")
    try:
        offset = value.utcoffset()
        if offset is None:
            raise RegistryConflictError(
                f"{label} must be a timezone-aware datetime"
            )
        return value.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RegistryConflictError(
            f"{label} cannot be normalized to UTC"
        ) from exc


def _canonical_json_bytes(value: object, label: str, maximum: int) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RegistryConflictError(f"{label} is not canonical JSON data") from exc
    if len(encoded) > maximum:
        raise RegistryConflictError(f"{label} exceeds its persisted byte limit")
    return encoded


def _canonical_payload(value: object, label: str) -> bytes:
    if not isinstance(value, bytes):
        raise RegistryConflictError(f"{label} must be canonical JSON bytes")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, child in pairs:
            if key in output:
                raise RegistryConflictError(f"{label} contains a duplicate key")
            output[key] = child
        return output

    try:
        decoded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                RegistryConflictError(f"{label} contains an invalid constant")
            ),
        )
    except RegistryConflictError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise RegistryConflictError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RegistryConflictError(f"{label} must contain a JSON object")
    canonical = _canonical_json_bytes(decoded, label, MAX_OPERATION_BYTES)
    if canonical != value:
        raise RegistryConflictError(f"{label} must use canonical JSON encoding")
    return canonical


def _encode_vm(record: VMRecord, maximum: int) -> tuple[bytes, str]:
    if not isinstance(record, VMRecord):
        raise RegistryConflictError("record must be a VMRecord")
    encoded = _canonical_json_bytes(record.to_dict(), "VM record", maximum)
    return encoded, hashlib.sha256(encoded).hexdigest()


def _decode_vm(
    encoded: object,
    expected_hash: object,
    maximum: int,
) -> VMRecord:
    if not isinstance(encoded, bytes) or len(encoded) > maximum:
        raise RegistryIntegrityError("stored VM bytes violate the configured bound")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        raise RegistryIntegrityError("stored VM hash is invalid")
    if hashlib.sha256(encoded).hexdigest() != expected_hash:
        raise RegistryIntegrityError("stored VM hash does not match its bytes")
    try:
        decoded = json.loads(encoded.decode("utf-8"))
        record = VMRecord.from_dict(decoded)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise RegistryIntegrityError("stored VM record cannot be decoded") from exc
    canonical, _ = _encode_vm(record, maximum)
    if canonical != encoded:
        raise RegistryIntegrityError("stored VM bytes are not canonical")
    return record


def _stored_vm_from_row(
    row: sqlite3.Row,
    maximum: int,
) -> StoredVM:
    record = _decode_vm(
        row["record_bytes"],
        row["record_sha256"],
        maximum,
    )
    if (
        row["vm_id"] != str(record.vm_id)
        or int(row["generation"]) != record.generation
        or row["normalized_name"] != record.definition.name.casefold()
        or row["qmp_socket"] != (record.qmp_socket or None)
        or int(row["ssh_port"]) != record.ports.ssh
        or int(row["agent_port"]) != record.ports.agent
        or int(row["display_port"]) != record.ports.vnc
        or row["intent_digest"] != record.intent_digest
    ):
        raise RegistryIntegrityError(
            "materialized VM authority disagrees with canonical bytes"
        )
    revision = row["revision"]
    active = row["active"]
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or active not in {0, 1}
    ):
        raise RegistryIntegrityError(
            "stored VM revision or activation flag is invalid"
        )
    _decode_timestamp(row["created_at"], "created_at")
    _decode_timestamp(row["updated_at"], "updated_at")
    return StoredVM(record, revision, bool(active))


def _canonical_path(value: str | Path, label: str) -> str:
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise RegistryConflictError(f"{label} is invalid") from exc
    text = os.fspath(path)
    if (
        not path.is_absolute()
        or path == Path("/")
        or "\x00" in text
        or len(text) > 4_096
        or PurePosixPath(text) != path
        or path.resolve(strict=False) != path
    ):
        raise RegistryConflictError(
            f"{label} must be a canonical absolute non-root path"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise RegistryConflictError(f"{label} contains a control character")
    return text


def _stored_path(value: object, label: str) -> str:
    """Decode a durable lexical path without following mutable host state."""

    if not isinstance(value, str):
        raise RegistryIntegrityError(f"{label} is not text")
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise RegistryIntegrityError(f"{label} is invalid") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or "\x00" in value
        or len(value) > 4_096
        or PurePosixPath(value) != path
        or os.path.normpath(value) != value
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise RegistryIntegrityError(
            f"{label} is not a canonical absolute non-root path"
        )
    return value


def _bounded_key(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or value[0].isspace()
        or value[-1].isspace()
        or any(
            ord(character) < 33
            or ord(character) == 127
            or character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
            for character in value
        )
    ):
        raise RegistryConflictError(f"{label} is invalid")
    return value


def _payload_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _verify_payload(
    payload: object,
    expected_hash: object,
    label: str,
) -> bytes:
    if not isinstance(payload, bytes) or len(payload) > MAX_OPERATION_BYTES:
        raise RegistryIntegrityError(f"stored {label} bytes violate their bound")
    if (
        not isinstance(expected_hash, str)
        or not _SHA256.fullmatch(expected_hash)
        or _payload_hash(payload) != expected_hash
    ):
        raise RegistryIntegrityError(f"stored {label} hash does not match")
    try:
        _canonical_payload(payload, label)
    except RegistryConflictError as exc:
        raise RegistryIntegrityError(
            f"stored {label} bytes are not canonical"
        ) from exc
    return payload


def _decode_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise RegistryIntegrityError(f"stored {label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RegistryIntegrityError(
            f"stored {label} timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegistryIntegrityError(
            f"stored {label} timestamp is not timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _optional_operation_id(value: UUID | str | None) -> str | None:
    return str(_uuid(value, "operation_id")) if value is not None else None


def _bound_vm_from_row(
    vm_id: object,
    generation: object,
) -> tuple[UUID | None, int | None]:
    if vm_id is None and generation is None:
        return None, None
    if vm_id is None or generation is None:
        raise RegistryIntegrityError(
            "stored operation VM binding is only partially populated"
        )
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
    ):
        raise RegistryIntegrityError(
            "stored operation generation is invalid"
        )
    return _uuid(vm_id, "vm_id"), generation


def _idempotency_from_row(row: sqlite3.Row) -> IdempotencyRecord:
    result = _verify_payload(
        row["result_bytes"],
        row["result_sha256"],
        "idempotency result",
    )
    status = _token(row["status"], "status")
    if status not in _OPERATION_STATUSES:
        raise RegistryIntegrityError("stored idempotency status is unsupported")
    vm_id, generation = _bound_vm_from_row(
        row["vm_id"],
        row["vm_generation"],
    )
    return IdempotencyRecord(
        key=str(row["idempotency_key"]),
        operation_id=_uuid(row["operation_id"], "operation_id"),
        vm_id=vm_id,
        generation=generation,
        request_hash=_hash(row["request_hash"], "request_hash"),
        status=status,
        result=result,
        result_sha256=str(row["result_sha256"]),
    )


def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
    intent = _verify_payload(
        row["intent_bytes"],
        row["intent_sha256"],
        "operation intent",
    )
    owned = _verify_payload(
        row["owned_resources"],
        row["owned_resources_sha256"],
        "operation owned_resources",
    )
    result = _verify_payload(
        row["result_bytes"],
        row["result_sha256"],
        "operation result",
    )
    latest = row["latest_checkpoint"]
    status = _token(row["status"], "operation status")
    if status not in _OPERATION_STATUSES:
        raise RegistryIntegrityError("stored operation status is unsupported")
    vm_id, generation = _bound_vm_from_row(
        row["vm_id"],
        row["vm_generation"],
    )
    created_at = _decode_timestamp(row["created_at"], "created_at")
    updated_at = _decode_timestamp(row["updated_at"], "updated_at")
    return OperationRecord(
        operation_id=_uuid(row["operation_id"], "operation_id"),
        vm_id=vm_id,
        generation=generation,
        kind=_token(row["kind"], "operation kind"),
        idempotency_key=_bounded_key(
            row["idempotency_key"],
            "idempotency_key",
        ),
        request_hash=_hash(row["request_hash"], "request_hash"),
        status=status,
        intent=intent,
        intent_sha256=str(row["intent_sha256"]),
        owned_resources=owned,
        owned_resources_sha256=str(row["owned_resources_sha256"]),
        result=result,
        result_sha256=str(row["result_sha256"]),
        latest_checkpoint=int(latest) if latest is not None else None,
        created_at=created_at,
        updated_at=updated_at,
    )


def _checkpoint_from_row(row: sqlite3.Row) -> OperationCheckpoint:
    payload = _verify_payload(
        row["payload_bytes"],
        row["payload_sha256"],
        "checkpoint payload",
    )
    ordinal = row["ordinal"]
    if (
        not isinstance(ordinal, int)
        or isinstance(ordinal, bool)
        or ordinal < 0
        or ordinal > 1_000_000
    ):
        raise RegistryIntegrityError("stored checkpoint ordinal is invalid")
    return OperationCheckpoint(
        operation_id=_uuid(row["operation_id"], "operation_id"),
        ordinal=ordinal,
        name=_token(row["name"], "checkpoint name"),
        payload=payload,
        payload_sha256=str(row["payload_sha256"]),
        created_at=_decode_timestamp(row["created_at"], "created_at"),
    )


def _base_image_from_row(row: sqlite3.Row) -> BaseImageRecord:
    manifest_bytes = _verify_payload(
        row["manifest_bytes"],
        row["manifest_sha256"],
        "base image manifest",
    )
    try:
        manifest = ImageManifest.from_json(manifest_bytes)
    except ImageManifestError as exc:
        raise RegistryIntegrityError(
            "stored base image manifest cannot be decoded"
        ) from exc
    numeric = {
        "byte_size": manifest.size_bytes,
        "virtual_size_bytes": manifest.virtual_size_bytes,
    }
    for key, expected in numeric.items():
        value = row[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value != expected
        ):
            raise RegistryIntegrityError(
                f"stored base image {key} disagrees with its manifest"
            )
    for key in (
        "device_id",
        "inode",
        "file_size_bytes",
        "allocated_size_bytes",
        "mode",
    ):
        value = row[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise RegistryIntegrityError(
                f"stored base image {key} is invalid"
            )
    if (
        row["image_id"] != str(manifest.image_id)
        or row["content_sha256"] != manifest.sha256
        or row["format"] != manifest.format
        or row["file_size_bytes"] != manifest.size_bytes
        or row["active"] not in {0, 1}
    ):
        raise RegistryIntegrityError(
            "materialized base image authority disagrees with its manifest"
        )
    path = _stored_path(row["path"], "stored base image path")
    _hash(row["content_sha256"], "stored base content hash")
    _hash(row["chain_sha256"], "stored base chain hash")
    operation_id = _uuid(row["operation_id"], "operation_id")
    _decode_timestamp(row["created_at"], "created_at")
    qemu_version = row["qemu_img_version"]
    if (
        not isinstance(qemu_version, str)
        or not qemu_version.startswith("qemu-img version ")
        or len(qemu_version) > 512
    ):
        raise RegistryIntegrityError(
            "stored qemu-img version is invalid"
        )
    return BaseImageRecord(
        image_id=manifest.image_id,
        path=path,
        content_sha256=manifest.sha256,
        manifest=manifest_bytes,
        manifest_sha256=str(row["manifest_sha256"]),
        byte_size=manifest.size_bytes,
        format=manifest.format,
        virtual_size_bytes=manifest.virtual_size_bytes,
        device_id=int(row["device_id"]),
        inode=int(row["inode"]),
        file_size_bytes=int(row["file_size_bytes"]),
        allocated_size_bytes=int(row["allocated_size_bytes"]),
        mode=int(row["mode"]),
        qemu_img_version=qemu_version,
        chain_sha256=str(row["chain_sha256"]),
        active=bool(row["active"]),
        operation_id=operation_id,
    )


def _disk_from_row(row: sqlite3.Row) -> DiskRecord:
    materialized = row["materialized"]
    active = row["active"]
    if materialized not in {0, 1} or active not in {0, 1}:
        raise RegistryIntegrityError(
            "stored disk materialization or activation flag is invalid"
        )
    material_fields = (
        "base_image_id",
        "base_image_sha256",
        "virtual_size_bytes",
        "format",
        "device_id",
        "inode",
        "file_size_bytes",
        "allocated_size_bytes",
        "chain_sha256",
        "marker_sha256",
        "qemu_img_version",
        "materialized_by_operation_id",
    )
    if bool(materialized):
        if any(row[key] is None for key in material_fields):
            raise RegistryIntegrityError(
                "materialized disk is missing measured authority"
            )
        for key in (
            "virtual_size_bytes",
            "device_id",
            "inode",
            "file_size_bytes",
            "allocated_size_bytes",
        ):
            value = row[key]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or (key == "virtual_size_bytes" and value < 1)
            ):
                raise RegistryIntegrityError(
                    f"materialized disk {key} is invalid"
                )
        _hash(row["base_image_sha256"], "disk base image hash")
        _hash(row["chain_sha256"], "disk backing-chain hash")
        _hash(row["marker_sha256"], "disk marker hash")
        if row["format"] != "qcow2":
            raise RegistryIntegrityError(
                "materialized disk format is unsupported"
            )
        version = row["qemu_img_version"]
        if (
            not isinstance(version, str)
            or not version.startswith("qemu-img version ")
            or len(version) > 512
        ):
            raise RegistryIntegrityError(
                "materialized disk qemu-img version is invalid"
            )
    elif any(row[key] is not None for key in material_fields):
        raise RegistryIntegrityError(
            "unmaterialized disk contains physical authority"
        )
    _decode_timestamp(row["created_at"], "created_at")
    if row["released_at"] is not None:
        _decode_timestamp(row["released_at"], "released_at")
    return DiskRecord(
        disk_id=_uuid(row["disk_id"], "disk_id"),
        vm_id=_uuid(row["vm_id"], "vm_id"),
        path=_stored_path(row["path"], "stored disk path"),
        generation=int(row["generation"]),
        ownership=_token(row["ownership"], "disk ownership"),
        active=bool(active),
        operation_id=(
            _uuid(row["operation_id"], "operation_id")
            if row["operation_id"] is not None
            else None
        ),
        released_by_operation_id=(
            _uuid(row["released_by_operation_id"], "operation_id")
            if row["released_by_operation_id"] is not None
            else None
        ),
        ownership_token=_uuid(row["ownership_token"], "ownership_token"),
        materialized=bool(materialized),
        base_image_id=(
            _uuid(row["base_image_id"], "base_image_id")
            if row["base_image_id"] is not None
            else None
        ),
        base_image_sha256=(
            str(row["base_image_sha256"])
            if row["base_image_sha256"] is not None
            else None
        ),
        virtual_size_bytes=(
            int(row["virtual_size_bytes"])
            if row["virtual_size_bytes"] is not None
            else None
        ),
        format=str(row["format"]) if row["format"] is not None else None,
        device_id=(
            int(row["device_id"]) if row["device_id"] is not None else None
        ),
        inode=int(row["inode"]) if row["inode"] is not None else None,
        file_size_bytes=(
            int(row["file_size_bytes"])
            if row["file_size_bytes"] is not None
            else None
        ),
        allocated_size_bytes=(
            int(row["allocated_size_bytes"])
            if row["allocated_size_bytes"] is not None
            else None
        ),
        chain_sha256=(
            str(row["chain_sha256"])
            if row["chain_sha256"] is not None
            else None
        ),
        marker_sha256=(
            str(row["marker_sha256"])
            if row["marker_sha256"] is not None
            else None
        ),
        qemu_img_version=(
            str(row["qemu_img_version"])
            if row["qemu_img_version"] is not None
            else None
        ),
        materialized_by_operation_id=(
            _uuid(row["materialized_by_operation_id"], "operation_id")
            if row["materialized_by_operation_id"] is not None
            else None
        ),
    )


def _process_identity_from_row(row: sqlite3.Row) -> ProcessIdentityRecord:
    generation = row["generation"]
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
    ):
        raise RegistryIntegrityError(
            "stored process identity generation is invalid"
        )
    if row["active"] not in {0, 1}:
        raise RegistryIntegrityError(
            "stored process identity active state is invalid"
        )
    payload = _verify_payload(
        row["identity_bytes"],
        row["identity_sha256"],
        "process identity",
    )
    try:
        process = ProcessIdentity.from_dict(
            json.loads(payload.decode("utf-8"))
        )
    except (
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise RegistryIntegrityError(
            "stored process identity cannot be decoded"
        ) from exc
    expected_payload = _canonical_json_bytes(
        process.to_dict(),
        "process identity",
        MAX_EVENT_BYTES,
    )
    if payload != expected_payload:
        raise RegistryIntegrityError(
            "stored process identity bytes are not canonical protocol data"
        )
    observed_at = _decode_timestamp(row["observed_at"], "observed_at")
    if (
        row["observed_at"] != observed_at.isoformat()
        or observed_at != process.observed_at
    ):
        raise RegistryIntegrityError(
            "materialized process identity disagrees with its bytes"
        )
    _decode_timestamp(row["created_at"], "created_at")
    return ProcessIdentityRecord(
        process_record_id=_uuid(
            row["process_record_id"],
            "process_record_id",
        ),
        vm_id=_uuid(row["vm_id"], "vm_id"),
        generation=int(generation),
        boot_id=_uuid(row["boot_id"], "boot_id"),
        process=process,
        active=bool(row["active"]),
        operation_id=(
            _uuid(row["operation_id"], "operation_id")
            if row["operation_id"] is not None
            else None
        ),
    )


def _process_exit_observation_payload(
    *,
    exit_evidence_id: UUID,
    process_record_id: UUID,
    vm_id: UUID,
    generation: int,
    boot_id: UUID,
    process: ProcessIdentity,
    original_operation_id: UUID,
    observed_absent_at: datetime,
    reason: str,
) -> dict[str, object]:
    return {
        "boot_id": str(boot_id),
        "exit_evidence_id": str(exit_evidence_id),
        "generation": generation,
        "observed_absent_at": observed_absent_at.isoformat(),
        "original_operation_id": str(original_operation_id),
        "process": process.to_dict(),
        "process_record_id": str(process_record_id),
        "reason": reason,
        "vm_id": str(vm_id),
    }


def _process_exit_observation_from_row(
    row: sqlite3.Row,
) -> ProcessExitObservation:
    generation = row["generation"]
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
    ):
        raise RegistryIntegrityError(
            "stored process exit generation is invalid"
        )
    exit_evidence_id = _uuid(
        row["exit_evidence_id"],
        "exit_evidence_id",
    )
    process_record_id = _uuid(
        row["process_record_id"],
        "process_record_id",
    )
    vm_id = _uuid(row["vm_id"], "vm_id")
    boot_id = _uuid(row["boot_id"], "boot_id")
    original_operation_id = _uuid(
        row["original_operation_id"],
        "original_operation_id",
    )
    reason = _token(row["reason"], "process exit reason")
    process_payload = _verify_payload(
        row["process_identity_bytes"],
        row["process_identity_sha256"],
        "process exit identity",
    )
    try:
        process = ProcessIdentity.from_dict(
            json.loads(process_payload.decode("utf-8"))
        )
    except (
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise RegistryIntegrityError(
            "stored process exit identity cannot be decoded"
        ) from exc
    expected_process_payload = _canonical_json_bytes(
        process.to_dict(),
        "process exit identity",
        MAX_EVENT_BYTES,
    )
    if process_payload != expected_process_payload:
        raise RegistryIntegrityError(
            "stored process exit identity is not canonical protocol data"
        )
    process_observed_at = _decode_timestamp(
        row["process_observed_at"],
        "process_observed_at",
    )
    observed_absent_at = _decode_timestamp(
        row["observed_absent_at"],
        "observed_absent_at",
    )
    if (
        row["process_observed_at"] != process_observed_at.isoformat()
        or row["observed_absent_at"] != observed_absent_at.isoformat()
        or process_observed_at != process.observed_at
        or observed_absent_at <= process_observed_at
    ):
        raise RegistryIntegrityError(
            "stored process exit chronology disagrees"
        )
    canonical = _verify_payload(
        row["canonical_bytes"],
        row["canonical_sha256"],
        "process exit observation",
    )
    if len(canonical) > MAX_PROCESS_EXIT_OBSERVATION_BYTES:
        raise RegistryIntegrityError(
            "stored process exit observation exceeds its byte limit"
        )
    expected = _canonical_json_bytes(
        _process_exit_observation_payload(
            exit_evidence_id=exit_evidence_id,
            process_record_id=process_record_id,
            vm_id=vm_id,
            generation=int(generation),
            boot_id=boot_id,
            process=process,
            original_operation_id=original_operation_id,
            observed_absent_at=observed_absent_at,
            reason=reason,
        ),
        "process exit observation",
        MAX_PROCESS_EXIT_OBSERVATION_BYTES,
    )
    if canonical != expected:
        raise RegistryIntegrityError(
            "process exit columns disagree with canonical bytes"
        )
    _decode_timestamp(row["created_at"], "created_at")
    return ProcessExitObservation(
        exit_evidence_id=exit_evidence_id,
        process_record_id=process_record_id,
        vm_id=vm_id,
        generation=int(generation),
        boot_id=boot_id,
        process=process,
        original_operation_id=original_operation_id,
        observed_absent_at=observed_absent_at,
        reason=reason,
        canonical_bytes=canonical,
        canonical_sha256=str(row["canonical_sha256"]),
    )


def _disk_runtime_observation_payload(
    *,
    observation_id: UUID,
    disk_id: UUID,
    vm_id: UUID,
    generation: int,
    observed_at: datetime,
    file_size_bytes: int,
    allocated_size_bytes: int,
    qemu_actual_size_bytes: int,
    dirty: bool,
    corrupt: bool,
    boot_id: UUID | None,
    process_record_id: UUID | None,
    quiesced: bool,
    operation_id: UUID,
) -> dict[str, object]:
    return {
        "allocated_size_bytes": allocated_size_bytes,
        "boot_id": str(boot_id) if boot_id is not None else None,
        "corrupt": corrupt,
        "dirty": dirty,
        "disk_id": str(disk_id),
        "file_size_bytes": file_size_bytes,
        "generation": generation,
        "observation_id": str(observation_id),
        "observed_at": observed_at.isoformat(),
        "operation_id": str(operation_id),
        "process_record_id": (
            str(process_record_id)
            if process_record_id is not None
            else None
        ),
        "qemu_actual_size_bytes": qemu_actual_size_bytes,
        "quiesced": quiesced,
        "vm_id": str(vm_id),
    }


def _disk_runtime_observation_from_row(
    row: sqlite3.Row,
) -> DiskRuntimeObservation:
    for label in ("dirty", "corrupt", "quiesced"):
        if row[label] not in {0, 1}:
            raise RegistryIntegrityError(
                f"stored disk runtime observation {label} is invalid"
            )
    for label in (
        "generation",
        "file_size_bytes",
        "allocated_size_bytes",
        "qemu_actual_size_bytes",
    ):
        value = row[label]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or (label == "file_size_bytes" and value < 1)
        ):
            raise RegistryIntegrityError(
                f"stored disk runtime observation {label} is invalid"
            )
    if (row["boot_id"] is None) != (row["process_record_id"] is None):
        raise RegistryIntegrityError(
            "stored disk runtime process and boot bindings are incomplete"
        )
    if bool(row["quiesced"]) and row["process_record_id"] is None:
        raise RegistryIntegrityError(
            "stored quiesced disk observation lacks process identity"
        )

    observation_id = _uuid(row["observation_id"], "observation_id")
    disk_id = _uuid(row["disk_id"], "disk_id")
    vm_id = _uuid(row["vm_id"], "vm_id")
    generation = int(row["generation"])
    observed_at = _decode_timestamp(row["observed_at"], "observed_at")
    if row["observed_at"] != observed_at.isoformat():
        raise RegistryIntegrityError(
            "stored disk observation timestamp is not canonical UTC"
        )
    boot_id = (
        _uuid(row["boot_id"], "boot_id")
        if row["boot_id"] is not None
        else None
    )
    process_record_id = (
        _uuid(row["process_record_id"], "process_record_id")
        if row["process_record_id"] is not None
        else None
    )
    operation_id = _uuid(row["operation_id"], "operation_id")
    canonical = _verify_payload(
        row["canonical_bytes"],
        row["canonical_sha256"],
        "disk runtime observation",
    )
    expected = _canonical_json_bytes(
        _disk_runtime_observation_payload(
            observation_id=observation_id,
            disk_id=disk_id,
            vm_id=vm_id,
            generation=generation,
            observed_at=observed_at,
            file_size_bytes=int(row["file_size_bytes"]),
            allocated_size_bytes=int(row["allocated_size_bytes"]),
            qemu_actual_size_bytes=int(row["qemu_actual_size_bytes"]),
            dirty=bool(row["dirty"]),
            corrupt=bool(row["corrupt"]),
            boot_id=boot_id,
            process_record_id=process_record_id,
            quiesced=bool(row["quiesced"]),
            operation_id=operation_id,
        ),
        "disk runtime observation",
        MAX_DISK_OBSERVATION_BYTES,
    )
    if canonical != expected:
        raise RegistryIntegrityError(
            "disk runtime observation columns disagree with canonical bytes"
        )
    _decode_timestamp(row["created_at"], "created_at")
    return DiskRuntimeObservation(
        observation_id=observation_id,
        disk_id=disk_id,
        vm_id=vm_id,
        generation=generation,
        observed_at=observed_at,
        file_size_bytes=int(row["file_size_bytes"]),
        allocated_size_bytes=int(row["allocated_size_bytes"]),
        qemu_actual_size_bytes=int(row["qemu_actual_size_bytes"]),
        dirty=bool(row["dirty"]),
        corrupt=bool(row["corrupt"]),
        boot_id=boot_id,
        process_record_id=process_record_id,
        quiesced=bool(row["quiesced"]),
        canonical_bytes=canonical,
        canonical_sha256=str(row["canonical_sha256"]),
        operation_id=operation_id,
    )


class _WriterLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> None:
        if not self.path.is_absolute() or self.path.resolve(strict=False) != self.path:
            raise RegistryWriterBusyError(
                "registry writer lock path must be canonical and absolute"
            )
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise RegistryWriterBusyError("registry writer lock is unavailable") from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise RegistryWriterBusyError("registry writer lock is unsafe")
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RegistryWriterBusyError(
                    "registry writer lock permissions are not private"
                )
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise RegistryWriterBusyError(
                        "another process holds the registry writer lock"
                    ) from exc
                raise
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def release(self) -> None:
        fd = self.fd
        self.fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _prepare_registry_parent(path: Path) -> None:
    """Create private missing descendants without following a path symlink."""

    parent = path.parent
    if not parent.is_absolute() or parent.resolve(strict=False) != parent:
        raise RegistryError(
            "registry parent must be canonical and absolute"
        )
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory_fd = os.open("/", directory_flags)
    except OSError as exc:
        raise RegistryError("filesystem root could not be inspected") from exc
    created_branch = False
    try:
        parts = parent.parts[1:]
        for index, component in enumerate(parts):
            final = index == len(parts) - 1
            created = False
            try:
                metadata = os.stat(
                    component,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=directory_fd)
                    created = True
                    created_branch = True
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise RegistryError(
                        "registry parent chain could not be created"
                    ) from exc
                metadata = os.stat(
                    component,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise RegistryError(
                    "registry parent chain could not be inspected"
                ) from exc
            mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISDIR(metadata.st_mode):
                raise RegistryError(
                    "registry parent chain contains a non-directory"
                )
            if created or created_branch or final:
                if metadata.st_uid != os.geteuid() or mode != 0o700:
                    raise RegistryError(
                        "registry state directories must be owner-only 0700"
                    )
            else:
                writable = mode & 0o022
                trusted_sticky_root = (
                    metadata.st_uid == 0
                    and bool(mode & stat.S_ISVTX)
                )
                if (
                    metadata.st_uid not in {0, os.geteuid()}
                    or (writable and not trusted_sticky_root)
                ):
                    raise RegistryError(
                        "registry parent chain contains an unsafe ancestor"
                    )
            try:
                next_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                raise RegistryError(
                    "registry parent chain changed during validation"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
    finally:
        os.close(directory_fd)


def _validate_existing_database(path: Path) -> None:
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            if candidate == path:
                raise RegistryIntegrityError(
                    "registry database metadata is unavailable"
                )
            continue
        except OSError as exc:
            raise RegistryIntegrityError(
                "registry database metadata is unavailable"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise RegistryIntegrityError(
                "registry database and sidecars must be "
                "owner-private regular files"
            )


def _database_uri(path: Path, mode: str) -> str:
    encoded = urllib.parse.quote(os.fspath(path), safe="/")
    return f"file:{encoded}?mode={mode}"


def _connect(
    path: Path,
    *,
    read_only: bool,
    busy_timeout_ms: int,
) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            _database_uri(path, "ro" if read_only else "rwc"),
            uri=True,
            timeout=busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
    except sqlite3.Error as exc:
        raise RegistryIntegrityError("registry database could not be opened") from exc
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    if read_only:
        connection.execute("PRAGMA query_only=ON")
    return connection


def _read_schema_version(connection: sqlite3.Connection) -> int:
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise RegistryIntegrityError(
            "registry schema version cannot be read"
        ) from exc


def _quick_check(connection: sqlite3.Connection) -> tuple[bool, str | None]:
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error:
        return False, "registry quick_check could not execute"
    messages = tuple(str(row[0]) for row in rows)
    if messages == ("ok",):
        return True, None
    return False, "registry quick_check failed"


def _measure_journal_mode(directory: Path) -> str:
    """Measure WAL on the actual state filesystem, otherwise choose DELETE."""

    probe = directory / f".journal-probe-{uuid4()}.sqlite3"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            probe,
            isolation_level=None,
            timeout=2.0,
        )
        os.chmod(probe, 0o600)
        selected = str(
            connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        ).lower()
        if selected != "wal":
            return "delete"
        connection.executescript(
            "BEGIN IMMEDIATE;"
            "CREATE TABLE measured(value INTEGER NOT NULL) STRICT;"
            "INSERT INTO measured(value) VALUES(1);"
            "COMMIT;"
        )
        if connection.execute("SELECT value FROM measured").fetchone()[0] != 1:
            return "delete"
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            return "delete"
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return "wal"
    except (OSError, sqlite3.Error):
        return "delete"
    finally:
        if connection is not None:
            connection.close()
        for candidate in (
            probe,
            Path(f"{probe}-wal"),
            Path(f"{probe}-shm"),
        ):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def _configure_journal(
    connection: sqlite3.Connection,
    expected: str,
) -> str:
    if expected not in {"wal", "delete"}:
        raise RegistryIntegrityError("registry journal mode metadata is invalid")
    try:
        selected = str(
            connection.execute(f"PRAGMA journal_mode={expected.upper()}").fetchone()[0]
        ).lower()
        connection.execute("PRAGMA synchronous=FULL")
        if selected == "wal":
            connection.execute("PRAGMA wal_autocheckpoint=1000")
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "registry journal mode could not be configured"
        ) from exc
    if selected != expected:
        raise RegistryIntegrityError(
            "registry filesystem no longer supports its measured journal mode"
        )
    return selected


def _stored_journal_mode(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute(
            "SELECT value FROM registry_meta WHERE key='journal_mode'"
        ).fetchone()
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "registry journal metadata is unavailable"
        ) from exc
    if row is None or str(row[0]).lower() not in {"wal", "delete"}:
        raise RegistryIntegrityError("registry journal metadata is invalid")
    return str(row[0]).lower()


_CURRENT_SCHEMA_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "registry_meta": frozenset({"key", "value"}),
    "vms": frozenset(
        {
            "vm_id",
            "generation",
            "normalized_name",
            "qmp_socket",
            "ssh_port",
            "agent_port",
            "display_port",
            "intent_digest",
            "record_bytes",
            "record_sha256",
            "revision",
            "active",
            "created_at",
            "updated_at",
        }
    ),
    "operations": frozenset(
        {
            "operation_id",
            "vm_id",
            "vm_generation",
            "kind",
            "idempotency_key",
            "request_hash",
            "status",
            "intent_bytes",
            "intent_sha256",
            "owned_resources",
            "owned_resources_sha256",
            "result_bytes",
            "result_sha256",
            "latest_checkpoint",
            "created_at",
            "updated_at",
        }
    ),
    "idempotency": frozenset(
        {
            "idempotency_key",
            "operation_id",
            "vm_id",
            "vm_generation",
            "request_hash",
            "status",
            "result_bytes",
            "result_sha256",
            "created_at",
            "updated_at",
        }
    ),
    "operation_checkpoints": frozenset(
        {
            "operation_id",
            "ordinal",
            "name",
            "payload_bytes",
            "payload_sha256",
            "created_at",
        }
    ),
    "port_leases": frozenset(
        {
            "lease_id",
            "vm_id",
            "generation",
            "role",
            "host",
            "port",
            "active",
            "operation_id",
            "released_by_operation_id",
            "created_at",
            "released_at",
        }
    ),
    "base_images": frozenset(
        {
            "image_id",
            "path",
            "content_sha256",
            "manifest_bytes",
            "manifest_sha256",
            "byte_size",
            "format",
            "virtual_size_bytes",
            "device_id",
            "inode",
            "file_size_bytes",
            "allocated_size_bytes",
            "mode",
            "qemu_img_version",
            "chain_sha256",
            "active",
            "operation_id",
            "created_at",
        }
    ),
    "disks": frozenset(
        {
            "disk_id",
            "vm_id",
            "path",
            "generation",
            "ownership",
            "active",
            "operation_id",
            "released_by_operation_id",
            "ownership_token",
            "materialized",
            "base_image_id",
            "base_image_sha256",
            "virtual_size_bytes",
            "format",
            "device_id",
            "inode",
            "file_size_bytes",
            "allocated_size_bytes",
            "chain_sha256",
            "marker_sha256",
            "qemu_img_version",
            "materialized_by_operation_id",
            "created_at",
            "released_at",
        }
    ),
    "lifecycle_events": frozenset(
        {
            "event_id",
            "vm_id",
            "generation",
            "sequence",
            "previous_state",
            "target_state",
            "event_bytes",
            "event_sha256",
            "operation_id",
            "created_at",
        }
    ),
    "snapshots": frozenset(
        {
            "snapshot_id",
            "vm_id",
            "generation",
            "path",
            "reference_bytes",
            "reference_sha256",
            "active",
            "operation_id",
            "created_at",
        }
    ),
    "process_identities": frozenset(
        {
            "process_record_id",
            "vm_id",
            "generation",
            "boot_id",
            "observed_at",
            "identity_bytes",
            "identity_sha256",
            "active",
            "operation_id",
            "created_at",
        }
    ),
    "process_exit_observations": frozenset(
        {
            "exit_evidence_id",
            "process_record_id",
            "vm_id",
            "generation",
            "boot_id",
            "process_observed_at",
            "process_identity_bytes",
            "process_identity_sha256",
            "original_operation_id",
            "observed_absent_at",
            "reason",
            "canonical_bytes",
            "canonical_sha256",
            "created_at",
        }
    ),
    "disk_runtime_observations": frozenset(
        {
            "observation_id",
            "disk_id",
            "vm_id",
            "generation",
            "observed_at",
            "file_size_bytes",
            "allocated_size_bytes",
            "qemu_actual_size_bytes",
            "dirty",
            "corrupt",
            "boot_id",
            "process_record_id",
            "quiesced",
            "canonical_bytes",
            "canonical_sha256",
            "operation_id",
            "created_at",
        }
    ),
}
_REQUIRED_SCHEMA_INDEXES: Final[frozenset[str]] = frozenset(
    {
        "active_vm_identity",
        "active_vm_name",
        "active_vm_qmp",
        "active_vm_ssh",
        "active_vm_agent",
        "active_vm_display",
        "active_port_owner",
        "active_vm_port_role",
        "active_base_path",
        "active_base_content",
        "active_base_inode",
        "disk_ownership_token",
        "active_disk_path",
        "active_vm_disk",
        "active_disk_inode",
        "active_snapshot_path",
        "active_process_per_vm",
        "disk_identity_binding",
        "operation_identity_binding",
        "process_identity_binding",
        "disk_runtime_observation_order",
        "process_identity_retirement_binding",
        "process_exit_per_identity",
        "process_exit_observation_order",
    }
)
_REQUIRED_SCHEMA_TRIGGERS: Final[frozenset[str]] = frozenset(
    {
        "active_vm_endpoint_insert",
        "active_vm_endpoint_update",
        "disk_runtime_observation_owner_insert",
        "disk_runtime_observation_monotonic_insert",
        "disk_runtime_observation_immutable_update",
        "disk_runtime_observation_immutable_delete",
        "process_exit_observation_owner_insert",
        "process_exit_observation_retire_insert",
        "process_exit_observation_immutable_update",
        "process_exit_observation_immutable_delete",
        "process_identity_active_insert",
        "process_identity_retirement_guard",
    }
)
_P4_SCHEMA_INDEXES: Final[frozenset[str]] = frozenset(
    {
        "disk_identity_binding",
        "operation_identity_binding",
        "process_identity_binding",
        "disk_runtime_observation_order",
    }
)
_P4_SCHEMA_TRIGGERS: Final[frozenset[str]] = frozenset(
    {
        "disk_runtime_observation_owner_insert",
        "disk_runtime_observation_monotonic_insert",
        "disk_runtime_observation_immutable_update",
        "disk_runtime_observation_immutable_delete",
    }
)
_P5_SCHEMA_INDEXES: Final[frozenset[str]] = frozenset(
    {
        "process_identity_retirement_binding",
        "process_exit_per_identity",
        "process_exit_observation_order",
    }
)
_P5_SCHEMA_TRIGGERS: Final[frozenset[str]] = frozenset(
    {
        "process_exit_observation_owner_insert",
        "process_exit_observation_retire_insert",
        "process_exit_observation_immutable_update",
        "process_exit_observation_immutable_delete",
        "process_identity_active_insert",
        "process_identity_retirement_guard",
    }
)


def _validate_current_schema(
    connection: sqlite3.Connection,
    *,
    include_process_exit_observations: bool = True,
) -> None:
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        for table, expected_columns in _CURRENT_SCHEMA_COLUMNS.items():
            if (
                table == "process_exit_observations"
                and not include_process_exit_observations
            ):
                continue
            if table not in tables:
                raise RegistryIntegrityError(
                    f"registry schema is missing table {table}"
                )
            actual_columns = {
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if actual_columns != expected_columns:
                raise RegistryIntegrityError(
                    f"registry schema columns disagree for {table}"
                )
        disk_columns = {
            str(row["name"]): row
            for row in connection.execute(
                "PRAGMA table_xinfo(disks)"
            ).fetchall()
        }
        if (
            int(disk_columns["ownership_token"]["notnull"]) != 1
            or int(disk_columns["materialized"]["notnull"]) != 1
        ):
            raise RegistryIntegrityError(
                "registry disk authority columns are nullable"
            )
        disk_schema_row = connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type='table' AND name='disks'"
        ).fetchone()
        if disk_schema_row is None or not isinstance(disk_schema_row["sql"], str):
            raise RegistryIntegrityError(
                "registry disk schema SQL is unavailable"
            )
        normalized_disk_sql = "".join(
            disk_schema_row["sql"].lower().split()
        )
        if (
            "materialized=0andbase_image_idisnull"
            not in normalized_disk_sql
            or "materialized=1andbase_image_idisnotnull"
            not in normalized_disk_sql
            or "materialized_by_operation_idisnotnull"
            not in normalized_disk_sql
        ):
            raise RegistryIntegrityError(
                "registry disk materialization constraint is absent"
            )
        observation_columns = {
            str(row["name"]): row
            for row in connection.execute(
                "PRAGMA table_xinfo(disk_runtime_observations)"
            ).fetchall()
        }
        for column in (
            "observation_id",
            "disk_id",
            "vm_id",
            "generation",
            "observed_at",
            "file_size_bytes",
            "allocated_size_bytes",
            "qemu_actual_size_bytes",
            "dirty",
            "corrupt",
            "quiesced",
            "canonical_bytes",
            "canonical_sha256",
            "operation_id",
            "created_at",
        ):
            if int(observation_columns[column]["notnull"]) != 1:
                raise RegistryIntegrityError(
                    "registry disk observation authority columns are nullable"
                )
        observation_schema_row = connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type='table' AND name='disk_runtime_observations'"
        ).fetchone()
        if (
            observation_schema_row is None
            or not isinstance(observation_schema_row["sql"], str)
        ):
            raise RegistryIntegrityError(
                "registry disk observation schema SQL is unavailable"
            )
        normalized_observation_sql = "".join(
            observation_schema_row["sql"].lower().split()
        )
        for required_fragment in (
            "boot_idisnullandprocess_record_idisnullandquiesced=0",
            "foreignkey(disk_id,vm_id,generation)referencesdisks("
            "disk_id,vm_id,generation)",
            "foreignkey(operation_id,vm_id,generation)referencesoperations("
            "operation_id,vm_id,vm_generation)",
            "foreignkey(process_record_id,vm_id,generation,boot_id)"
            "referencesprocess_identities("
            "process_record_id,vm_id,generation,boot_id)",
        ):
            if required_fragment not in normalized_observation_sql:
                raise RegistryIntegrityError(
                    "registry disk observation binding constraint is absent"
                )
        if include_process_exit_observations:
            exit_columns = {
                str(row["name"]): row
                for row in connection.execute(
                    "PRAGMA table_xinfo(process_exit_observations)"
                ).fetchall()
            }
            for column in _CURRENT_SCHEMA_COLUMNS[
                "process_exit_observations"
            ]:
                if int(exit_columns[column]["notnull"]) != 1:
                    raise RegistryIntegrityError(
                        "registry process exit authority columns are nullable"
                    )
            exit_schema_row = connection.execute(
                "SELECT sql FROM sqlite_schema "
                "WHERE type='table' AND name='process_exit_observations'"
            ).fetchone()
            if (
                exit_schema_row is None
                or not isinstance(exit_schema_row["sql"], str)
            ):
                raise RegistryIntegrityError(
                    "registry process exit schema SQL is unavailable"
                )
            normalized_exit_sql = "".join(
                exit_schema_row["sql"].lower().split()
            )
            for required_fragment in (
                "foreignkey(process_record_id,vm_id,generation,boot_id,"
                "original_operation_id)referencesprocess_identities("
                "process_record_id,vm_id,generation,boot_id,operation_id)",
                "foreignkey(original_operation_id,vm_id,generation)"
                "referencesoperations("
                "operation_id,vm_id,vm_generation)",
                "length(reason)between1and64",
            ):
                if required_fragment not in normalized_exit_sql:
                    raise RegistryIntegrityError(
                        "registry process exit binding constraint is absent"
                    )
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='index' AND sql IS NOT NULL"
            ).fetchall()
        }
        required_indexes = (
            _REQUIRED_SCHEMA_INDEXES
            if include_process_exit_observations
            else _REQUIRED_SCHEMA_INDEXES - _P5_SCHEMA_INDEXES
        )
        if not required_indexes.issubset(indexes):
            raise RegistryIntegrityError(
                "registry schema is missing authority indexes"
            )
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger'"
            ).fetchall()
        }
        required_triggers = (
            _REQUIRED_SCHEMA_TRIGGERS
            if include_process_exit_observations
            else _REQUIRED_SCHEMA_TRIGGERS - _P5_SCHEMA_TRIGGERS
        )
        if not required_triggers.issubset(triggers):
            raise RegistryIntegrityError(
                "registry schema is missing endpoint collision triggers"
            )
        trigger_sql = {
            str(row["name"]): "".join(str(row["sql"]).lower().split())
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_schema "
                "WHERE type='trigger' AND name IN (?,?,?,?)",
                tuple(sorted(_P4_SCHEMA_TRIGGERS)),
            ).fetchall()
        }
        required_trigger_fragments = {
            "disk_runtime_observation_owner_insert": (
                "disk.active=1",
                "disk.materialized=1",
                "operation.statusin('running','completed')",
                "process.active=1",
            ),
            "disk_runtime_observation_monotonic_insert": (
                "prior.observed_at>=new.observed_at",
            ),
            "disk_runtime_observation_immutable_update": (
                "beforeupdateondisk_runtime_observations",
                "raise(abort,'diskruntimeobservationsareappend-only')",
            ),
            "disk_runtime_observation_immutable_delete": (
                "beforedeleteondisk_runtime_observations",
                "raise(abort,'diskruntimeobservationsareappend-only')",
            ),
        }
        for trigger, fragments in required_trigger_fragments.items():
            if trigger not in trigger_sql or any(
                fragment not in trigger_sql[trigger]
                for fragment in fragments
            ):
                raise RegistryIntegrityError(
                    "registry disk observation trigger authority disagrees"
                )
        if include_process_exit_observations:
            p5_trigger_sql = {
                str(row["name"]): "".join(
                    str(row["sql"]).lower().split()
                )
                for row in connection.execute(
                    "SELECT name,sql FROM sqlite_schema "
                    "WHERE type='trigger' AND name IN (?,?,?,?,?,?)",
                    tuple(sorted(_P5_SCHEMA_TRIGGERS)),
                ).fetchall()
            }
            required_p5_fragments = {
                "process_exit_observation_owner_insert": (
                    "process.active=1",
                    "process.operation_id=new.original_operation_id",
                    "process.identity_bytes=new.process_identity_bytes",
                    "process.observed_at<new.observed_absent_at",
                ),
                "process_exit_observation_retire_insert": (
                    "afterinsertonprocess_exit_observations",
                    "updateprocess_identities",
                    "setactive=0",
                    "process_record_id=new.process_record_id",
                ),
                "process_exit_observation_immutable_update": (
                    "beforeupdateonprocess_exit_observations",
                    "raise(abort,'processexitobservationsareappend-only')",
                ),
                "process_exit_observation_immutable_delete": (
                    "beforedeleteonprocess_exit_observations",
                    "raise(abort,'processexitobservationsareappend-only')",
                ),
                "process_identity_active_insert": (
                    "beforeinsertonprocess_identities",
                    "new.active!=1",
                    "new.operation_idisnull",
                    "prior.observed_absent_at>=new.observed_at",
                ),
                "process_identity_retirement_guard": (
                    "beforeupdateonprocess_identities",
                    "old.active=1",
                    "new.active=0",
                    "fromprocess_exit_observations",
                ),
            }
            for trigger, fragments in required_p5_fragments.items():
                if trigger not in p5_trigger_sql or any(
                    fragment not in p5_trigger_sql[trigger]
                    for fragment in fragments
                ):
                    raise RegistryIntegrityError(
                        "registry process exit trigger authority disagrees"
                    )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RegistryIntegrityError(
                "registry contains a foreign-key ownership violation"
            )
    except RegistryIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "registry schema could not be validated"
        ) from exc


def _validate_p3_schema(connection: sqlite3.Connection) -> None:
    """Validate the exact promoted P3 authority before an additive migration."""

    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        if (
            "disk_runtime_observations" in tables
            or "process_exit_observations" in tables
        ):
            raise RegistryIntegrityError(
                "version 3 registry contains later partial authority"
            )
        for table, expected_columns in _CURRENT_SCHEMA_COLUMNS.items():
            if table in {
                "disk_runtime_observations",
                "process_exit_observations",
            }:
                continue
            if table not in tables:
                raise RegistryIntegrityError(
                    f"version 3 registry is missing table {table}"
                )
            actual_columns = {
                str(row["name"])
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if actual_columns != expected_columns:
                raise RegistryIntegrityError(
                    f"version 3 registry columns disagree for {table}"
                )
        disk_columns = {
            str(row["name"]): row
            for row in connection.execute(
                "PRAGMA table_xinfo(disks)"
            ).fetchall()
        }
        if (
            int(disk_columns["ownership_token"]["notnull"]) != 1
            or int(disk_columns["materialized"]["notnull"]) != 1
        ):
            raise RegistryIntegrityError(
                "version 3 disk authority columns are nullable"
            )
        disk_schema_row = connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type='table' AND name='disks'"
        ).fetchone()
        if disk_schema_row is None or not isinstance(disk_schema_row["sql"], str):
            raise RegistryIntegrityError(
                "version 3 disk schema SQL is unavailable"
            )
        normalized_disk_sql = "".join(
            disk_schema_row["sql"].lower().split()
        )
        if (
            "materialized=0andbase_image_idisnull"
            not in normalized_disk_sql
            or "materialized=1andbase_image_idisnotnull"
            not in normalized_disk_sql
            or "materialized_by_operation_idisnotnull"
            not in normalized_disk_sql
        ):
            raise RegistryIntegrityError(
                "version 3 disk materialization constraint is absent"
            )
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='index' AND sql IS NOT NULL"
            ).fetchall()
        }
        later_indexes = _P4_SCHEMA_INDEXES | _P5_SCHEMA_INDEXES
        expected_indexes = _REQUIRED_SCHEMA_INDEXES - later_indexes
        if not expected_indexes.issubset(indexes) or indexes & later_indexes:
            raise RegistryIntegrityError(
                "version 3 registry indexes disagree"
            )
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger'"
            ).fetchall()
        }
        later_triggers = _P4_SCHEMA_TRIGGERS | _P5_SCHEMA_TRIGGERS
        expected_triggers = _REQUIRED_SCHEMA_TRIGGERS - later_triggers
        if (
            not expected_triggers.issubset(triggers)
            or triggers & later_triggers
        ):
            raise RegistryIntegrityError(
                "version 3 registry triggers disagree"
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RegistryIntegrityError(
                "version 3 registry contains a foreign-key violation"
            )
    except RegistryIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "version 3 registry schema could not be validated"
        ) from exc


def _validate_p4_schema(connection: sqlite3.Connection) -> None:
    """Validate the exact promoted P4 shape before additive P5 migration."""

    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        if "process_exit_observations" in tables:
            raise RegistryIntegrityError(
                "version 4 registry contains partial P5 authority"
            )
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='index' AND sql IS NOT NULL"
            ).fetchall()
        }
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger'"
            ).fetchall()
        }
        if indexes & _P5_SCHEMA_INDEXES or triggers & _P5_SCHEMA_TRIGGERS:
            raise RegistryIntegrityError(
                "version 4 registry contains partial P5 schema objects"
            )
        _validate_current_schema(
            connection,
            include_process_exit_observations=False,
        )
    except RegistryIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "version 4 registry schema could not be validated"
        ) from exc


def _validate_persisted_rows(
    connection: sqlite3.Connection,
    *,
    max_record_bytes: int,
    include_disk_runtime_observations: bool = True,
    include_process_exit_observations: bool = True,
) -> None:
    """Validate durable hashes/canonical bytes without mutating the database."""

    try:
        for row in connection.execute("SELECT * FROM vms"):
            _stored_vm_from_row(row, max_record_bytes)
        for row in connection.execute("SELECT * FROM operations"):
            _operation_from_row(row)
        for row in connection.execute("SELECT * FROM idempotency"):
            _idempotency_from_row(row)
        for row in connection.execute("SELECT * FROM operation_checkpoints"):
            _checkpoint_from_row(row)
        for row in connection.execute("SELECT * FROM base_images"):
            _base_image_from_row(row)
        for row in connection.execute("SELECT * FROM disks"):
            _disk_from_row(row)
        for row in connection.execute("SELECT * FROM lifecycle_events"):
            payload = _verify_payload(
                row["event_bytes"],
                row["event_sha256"],
                "lifecycle event",
            )
            transition = TransitionRecord.from_dict(
                json.loads(payload.decode("utf-8"))
            )
            if (
                row["event_id"] != str(transition.evidence.evidence_id)
                or row["vm_id"] != str(transition.evidence.vm_id)
                or int(row["generation"])
                != transition.evidence.generation
                or row["previous_state"] != transition.previous.value
                or row["target_state"] != transition.target.value
            ):
                raise RegistryIntegrityError(
                    "materialized lifecycle event disagrees with its bytes"
                )
        for row in connection.execute("SELECT * FROM snapshots"):
            payload = _verify_payload(
                row["reference_bytes"],
                row["reference_sha256"],
                "snapshot reference",
            )
            snapshot = SnapshotReference.from_dict(
                json.loads(payload.decode("utf-8"))
            )
            if (
                row["snapshot_id"] != str(snapshot.snapshot_id)
                or row["vm_id"] != str(snapshot.vm_id)
                or int(row["generation"]) != snapshot.generation
                or row["path"] != snapshot.path
            ):
                raise RegistryIntegrityError(
                    "materialized snapshot disagrees with its bytes"
                )
        for row in connection.execute("SELECT * FROM process_identities"):
            _process_identity_from_row(row)
        if include_process_exit_observations:
            for row in connection.execute(
                "SELECT * FROM process_exit_observations "
                "ORDER BY observed_absent_at,exit_evidence_id"
            ):
                _process_exit_observation_from_row(row)
            process_exit_mismatch = connection.execute(
                """
                SELECT 1
                FROM process_exit_observations AS retirement
                LEFT JOIN process_identities AS process
                  ON process.process_record_id=retirement.process_record_id
                 AND process.vm_id=retirement.vm_id
                 AND process.generation=retirement.generation
                 AND process.boot_id=retirement.boot_id
                 AND process.operation_id=retirement.original_operation_id
                LEFT JOIN operations AS operation
                  ON operation.operation_id=retirement.original_operation_id
                 AND operation.vm_id=retirement.vm_id
                 AND operation.vm_generation=retirement.generation
                WHERE process.process_record_id IS NULL
                   OR process.active != 0
                   OR process.observed_at != retirement.process_observed_at
                   OR process.identity_bytes
                      != retirement.process_identity_bytes
                   OR process.identity_sha256
                      != retirement.process_identity_sha256
                   OR process.observed_at
                      >= retirement.observed_absent_at
                   OR operation.operation_id IS NULL
                LIMIT 1
                """
            ).fetchone()
            if process_exit_mismatch is not None:
                raise RegistryIntegrityError(
                    "process exit ownership or chronology disagrees"
                )
        if include_disk_runtime_observations:
            for row in connection.execute(
                "SELECT * FROM disk_runtime_observations "
                "ORDER BY disk_id,observed_at"
            ):
                _disk_runtime_observation_from_row(row)
            observation_mismatch = connection.execute(
                """
                SELECT 1
                FROM disk_runtime_observations AS observation
                LEFT JOIN disks AS disk
                  ON disk.disk_id=observation.disk_id
                 AND disk.vm_id=observation.vm_id
                 AND disk.generation=observation.generation
                LEFT JOIN operations AS operation
                  ON operation.operation_id=observation.operation_id
                 AND operation.vm_id=observation.vm_id
                 AND operation.vm_generation=observation.generation
                LEFT JOIN process_identities AS process
                  ON process.process_record_id=observation.process_record_id
                 AND process.vm_id=observation.vm_id
                 AND process.generation=observation.generation
                 AND process.boot_id=observation.boot_id
                WHERE disk.disk_id IS NULL
                   OR disk.materialized != 1
                   OR operation.operation_id IS NULL
                   OR (
                        observation.process_record_id IS NOT NULL
                        AND (
                            process.process_record_id IS NULL
                            OR process.observed_at > observation.observed_at
                        )
                   )
                   OR operation.created_at > observation.observed_at
                LIMIT 1
                """
            ).fetchone()
            if observation_mismatch is not None:
                raise RegistryIntegrityError(
                    "disk runtime observation ownership or chronology disagrees"
                )
        mismatch = connection.execute(
            """
            SELECT 1
            FROM operations AS operation
            LEFT JOIN idempotency AS replay
              ON replay.operation_id=operation.operation_id
            WHERE replay.operation_id IS NULL
               OR replay.idempotency_key != operation.idempotency_key
               OR replay.vm_id IS NOT operation.vm_id
               OR replay.vm_generation IS NOT operation.vm_generation
               OR replay.request_hash != operation.request_hash
               OR replay.status != operation.status
               OR replay.result_bytes != operation.result_bytes
               OR replay.result_sha256 != operation.result_sha256
            LIMIT 1
            """
        ).fetchone()
        if mismatch is not None:
            raise RegistryIntegrityError(
                "operation and idempotency authority disagree"
            )
        disk_mismatch = connection.execute(
            """
            SELECT 1
            FROM disks AS disk
            LEFT JOIN base_images AS base
              ON base.image_id=disk.base_image_id
            WHERE disk.materialized=1
              AND (
                base.image_id IS NULL
                OR base.active != 1
                OR disk.base_image_sha256 != base.content_sha256
                OR (
                    disk.device_id=base.device_id
                    AND disk.inode=base.inode
                )
              )
            LIMIT 1
            """
        ).fetchone()
        if disk_mismatch is not None:
            raise RegistryIntegrityError(
                "materialized disk disagrees with base-image authority"
            )
        storage_operation_mismatch = connection.execute(
            """
            SELECT 1
            FROM base_images AS base
            JOIN operations AS operation
              ON operation.operation_id=base.operation_id
            WHERE operation.kind != 'storage.import_base'
               OR operation.vm_id IS NOT NULL
               OR operation.vm_generation IS NOT NULL
            UNION ALL
            SELECT 1
            FROM disks AS disk
            JOIN operations AS operation
              ON operation.operation_id=disk.materialized_by_operation_id
            WHERE disk.materialized=1
              AND (
                operation.kind != 'storage.create_overlay'
                OR operation.vm_id IS NOT disk.vm_id
                OR operation.vm_generation IS NOT disk.generation
              )
            LIMIT 1
            """
        ).fetchone()
        if storage_operation_mismatch is not None:
            raise RegistryIntegrityError(
                "storage materialization operation has the wrong owner"
            )
        checkpoint_mismatch = connection.execute(
            """
            SELECT 1
            FROM operations AS operation
            LEFT JOIN (
                SELECT operation_id,COUNT(*) AS count_rows,
                       MIN(ordinal) AS minimum_ordinal,
                       MAX(ordinal) AS maximum_ordinal
                FROM operation_checkpoints
                GROUP BY operation_id
            ) AS checkpoints
              ON checkpoints.operation_id=operation.operation_id
            WHERE (
                operation.latest_checkpoint IS NULL
                AND COALESCE(checkpoints.count_rows,0) != 0
            ) OR (
                operation.latest_checkpoint IS NOT NULL
                AND (
                    checkpoints.minimum_ordinal != 0
                    OR checkpoints.maximum_ordinal
                       != operation.latest_checkpoint
                    OR checkpoints.count_rows
                       != operation.latest_checkpoint + 1
                )
            )
            LIMIT 1
            """
        ).fetchone()
        if checkpoint_mismatch is not None:
            raise RegistryIntegrityError(
                "operation checkpoint sequence is not contiguous"
            )
        operation_tables = [
            "port_leases",
            "disks",
            "lifecycle_events",
            "snapshots",
            "process_identities",
        ]
        if include_disk_runtime_observations:
            operation_tables.append("disk_runtime_observations")
        if include_process_exit_observations:
            operation_tables.append("process_exit_observations")
        for table in operation_tables:
            operation_columns = [
                (
                    "original_operation_id"
                    if table == "process_exit_observations"
                    else "operation_id"
                )
            ]
            if table in {"port_leases", "disks"}:
                operation_columns.append("released_by_operation_id")
            if table == "disks":
                operation_columns.append("materialized_by_operation_id")
            for operation_column in operation_columns:
                mismatch = connection.execute(
                    f"""
                    SELECT 1
                    FROM {table} AS resource
                    JOIN operations AS operation
                      ON operation.operation_id
                         =resource.{operation_column}
                    WHERE resource.{operation_column} IS NOT NULL
                      AND (
                        operation.vm_id IS NOT resource.vm_id
                        OR operation.vm_generation
                           IS NOT resource.generation
                      )
                    LIMIT 1
                    """
                ).fetchone()
                if mismatch is not None:
                    raise RegistryIntegrityError(
                        f"{table} operation binding disagrees "
                        "with its owner"
                    )
    except RegistryIntegrityError:
        raise
    except RegistryError as exc:
        raise RegistryIntegrityError(
            "registry persisted row semantics are invalid"
        ) from exc
    except (
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise RegistryIntegrityError(
            "registry persisted row values are invalid"
        ) from exc
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "registry persisted rows could not be validated"
        ) from exc


def _preflight_current_registry(
    connection: sqlite3.Connection,
    *,
    max_record_bytes: int,
) -> str:
    integrity_ok, issue = _quick_check(connection)
    if not integrity_ok:
        raise RegistryIntegrityError(issue or "registry quick_check failed")
    _validate_current_schema(connection)
    journal_mode = _stored_journal_mode(connection)
    try:
        observed_journal = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "registry journal mode could not be observed"
        ) from exc
    if observed_journal != journal_mode:
        raise RegistryIntegrityError(
            "registry journal metadata disagrees with SQLite"
        )
    _validate_persisted_rows(
        connection,
        max_record_bytes=max_record_bytes,
    )
    return journal_mode


def _preflight_p3_registry(
    connection: sqlite3.Connection,
    *,
    max_record_bytes: int,
) -> str:
    integrity_ok, issue = _quick_check(connection)
    if not integrity_ok:
        raise RegistryIntegrityError(
            issue or "version 3 registry quick_check failed"
        )
    _validate_p3_schema(connection)
    journal_mode = _stored_journal_mode(connection)
    try:
        observed_journal = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "version 3 registry journal mode could not be observed"
        ) from exc
    if observed_journal != journal_mode:
        raise RegistryIntegrityError(
            "version 3 registry journal metadata disagrees with SQLite"
        )
    _validate_persisted_rows(
        connection,
        max_record_bytes=max_record_bytes,
        include_disk_runtime_observations=False,
        include_process_exit_observations=False,
    )
    return journal_mode


def _preflight_p4_registry(
    connection: sqlite3.Connection,
    *,
    max_record_bytes: int,
) -> str:
    integrity_ok, issue = _quick_check(connection)
    if not integrity_ok:
        raise RegistryIntegrityError(
            issue or "version 4 registry quick_check failed"
        )
    _validate_p4_schema(connection)
    journal_mode = _stored_journal_mode(connection)
    try:
        observed_journal = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
    except sqlite3.Error as exc:
        raise RegistryIntegrityError(
            "version 4 registry journal mode could not be observed"
        ) from exc
    if observed_journal != journal_mode:
        raise RegistryIntegrityError(
            "version 4 registry journal metadata disagrees with SQLite"
        )
    _validate_persisted_rows(
        connection,
        max_record_bytes=max_record_bytes,
        include_process_exit_observations=False,
    )
    return journal_mode


def _backup_database(
    source: sqlite3.Connection,
    path: Path,
    version: int,
) -> Path:
    destination = path.with_name(
        f"{path.name}.pre-migration-v{version}-{uuid4()}.bak"
    )
    temporary = destination.with_name(f".{destination.name}.tmp")
    backup: sqlite3.Connection | None = None
    try:
        fd = os.open(
            temporary,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(fd)
        backup = sqlite3.connect(temporary, isolation_level=None)
        source.backup(backup)
        backup.close()
        backup = None
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
    except (OSError, sqlite3.Error) as exc:
        raise RegistryMigrationError(
            "pre-migration registry backup failed"
        ) from exc
    finally:
        if backup is not None:
            backup.close()
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class SQLiteRegistry:
    """One SQLite connection and, in write mode, the only process writer."""

    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection | None,
        status: RegistryStatus,
        writer_lock: _WriterLock | None,
        *,
        max_record_bytes: int,
    ) -> None:
        self.path = path
        self._connection = connection
        self._status = status
        self._writer_lock = writer_lock
        self._max_record_bytes = max_record_bytes
        self._mutex = threading.RLock()
        self._closed = False

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        writer: bool = True,
        lock_path: str | Path | None = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    ) -> "SQLiteRegistry":
        try:
            registry_path = Path(path)
        except (TypeError, ValueError) as exc:
            raise RegistryError("registry path is invalid") from exc
        if not registry_path.is_absolute() or registry_path == Path("/"):
            raise RegistryError("registry path must be absolute and non-root")
        if registry_path.resolve(strict=False) != registry_path:
            raise RegistryError("registry path must be canonical and symlink-free")
        if not isinstance(writer, bool):
            raise RegistryError("writer must be boolean")
        if (
            not isinstance(busy_timeout_ms, int)
            or isinstance(busy_timeout_ms, bool)
            or busy_timeout_ms < 1
            or busy_timeout_ms > 86_400_000
        ):
            raise RegistryError("busy_timeout_ms is outside the supported bound")
        if (
            not isinstance(max_record_bytes, int)
            or isinstance(max_record_bytes, bool)
            or max_record_bytes < 4_096
            or max_record_bytes > 16_777_216
        ):
            raise RegistryError("max_record_bytes is outside the supported bound")

        existed = registry_path.exists()
        if writer:
            _prepare_registry_parent(registry_path)
        elif not existed:
            raise RegistryNotFoundError("registry does not exist")
        if existed:
            _validate_existing_database(registry_path)

        try:
            selected_lock = (
                Path(lock_path)
                if lock_path is not None
                else registry_path.with_name(
                    f".{registry_path.name}.writer.lock"
                )
            )
        except (TypeError, ValueError) as exc:
            raise RegistryError("registry lock path is invalid") from exc
        writer_lock: _WriterLock | None = None
        if writer:
            _prepare_registry_parent(selected_lock)
            writer_lock = _WriterLock(selected_lock)
            writer_lock.acquire()

        connection: sqlite3.Connection | None = None
        try:
            if not existed:
                journal = _measure_journal_mode(registry_path.parent)
                connection = _connect(
                    registry_path,
                    read_only=False,
                    busy_timeout_ms=busy_timeout_ms,
                )
                os.chmod(registry_path, 0o600)
                _configure_journal(connection, journal)
                _create_schema(connection, journal)
                _preflight_current_registry(
                    connection,
                    max_record_bytes=max_record_bytes,
                )
                return cls(
                    registry_path,
                    connection,
                    RegistryStatus(
                        RegistryMode.READ_WRITE,
                        REGISTRY_SCHEMA_VERSION,
                        journal,
                        True,
                    ),
                    writer_lock,
                    max_record_bytes=max_record_bytes,
                )

            try:
                connection = _connect(
                    registry_path,
                    read_only=True,
                    busy_timeout_ms=busy_timeout_ms,
                )
                version = _read_schema_version(connection)
            except RegistryIntegrityError as exc:
                if connection is not None:
                    connection.close()
                    connection = None
                return cls(
                    registry_path,
                    None,
                    RegistryStatus(
                        RegistryMode.READ_ONLY_RECOVERY,
                        None,
                        None,
                        False,
                        str(exc),
                    ),
                    writer_lock,
                    max_record_bytes=max_record_bytes,
                )

            integrity_ok, integrity_issue = _quick_check(connection)
            if not integrity_ok:
                return cls(
                    registry_path,
                    connection,
                    RegistryStatus(
                        RegistryMode.READ_ONLY_RECOVERY,
                        version,
                        None,
                        False,
                        integrity_issue or "registry quick_check failed",
                    ),
                    writer_lock,
                    max_record_bytes=max_record_bytes,
                )

            if version == REGISTRY_SCHEMA_VERSION:
                try:
                    journal = _preflight_current_registry(
                        connection,
                        max_record_bytes=max_record_bytes,
                    )
                except RegistryIntegrityError as exc:
                    return cls(
                        registry_path,
                        connection,
                        RegistryStatus(
                            RegistryMode.READ_ONLY_RECOVERY,
                            version,
                            None,
                            False,
                            str(exc),
                        ),
                        writer_lock,
                        max_record_bytes=max_record_bytes,
                    )
                if not writer:
                    return cls(
                        registry_path,
                        connection,
                        RegistryStatus(
                            RegistryMode.READ_ONLY,
                            version,
                            journal,
                            True,
                        ),
                        None,
                        max_record_bytes=max_record_bytes,
                    )
                connection.close()
                connection = _connect(
                    registry_path,
                    read_only=False,
                    busy_timeout_ms=busy_timeout_ms,
                )
                _configure_journal(connection, journal)
                _preflight_current_registry(
                    connection,
                    max_record_bytes=max_record_bytes,
                )
                return cls(
                    registry_path,
                    connection,
                    RegistryStatus(
                        RegistryMode.READ_WRITE,
                        version,
                        journal,
                        True,
                    ),
                    writer_lock,
                    max_record_bytes=max_record_bytes,
                )

            if version in {
                LEGACY_REGISTRY_SCHEMA_VERSION,
                PREVIOUS_REGISTRY_SCHEMA_VERSION,
                P3_REGISTRY_SCHEMA_VERSION,
                P4_REGISTRY_SCHEMA_VERSION,
            }:
                if not writer:
                    return cls(
                        registry_path,
                        connection,
                        RegistryStatus(
                            RegistryMode.READ_ONLY_RECOVERY,
                            version,
                            None,
                            True,
                            "registry migration requires the writer owner",
                        ),
                        None,
                        max_record_bytes=max_record_bytes,
                    )
                backup_path = _backup_database(
                    connection,
                    registry_path,
                    version,
                )
                connection.close()
                journal = _measure_journal_mode(registry_path.parent)
                connection = _connect(
                    registry_path,
                    read_only=False,
                    busy_timeout_ms=busy_timeout_ms,
                )
                try:
                    _configure_journal(connection, journal)
                    if version == LEGACY_REGISTRY_SCHEMA_VERSION:
                        _migrate_v1_to_current(
                            connection,
                            journal_mode=journal,
                            backup_path=backup_path,
                            max_record_bytes=max_record_bytes,
                        )
                    elif version == PREVIOUS_REGISTRY_SCHEMA_VERSION:
                        _migrate_v2_to_v3(
                            connection,
                            backup_path=backup_path,
                            max_record_bytes=max_record_bytes,
                        )
                    elif version == P3_REGISTRY_SCHEMA_VERSION:
                        _migrate_v3_to_v4(
                            connection,
                            backup_path=backup_path,
                            max_record_bytes=max_record_bytes,
                        )
                    else:
                        _migrate_v4_to_v5(
                            connection,
                            backup_path=backup_path,
                            max_record_bytes=max_record_bytes,
                        )
                    _preflight_current_registry(
                        connection,
                        max_record_bytes=max_record_bytes,
                    )
                except RegistryMigrationError:
                    raise
                except BaseException as exc:
                    raise RegistryMigrationError(
                        "registry migration could not be verified",
                        backup_path=backup_path,
                    ) from exc
                return cls(
                    registry_path,
                    connection,
                    RegistryStatus(
                        RegistryMode.READ_WRITE,
                        REGISTRY_SCHEMA_VERSION,
                        journal,
                        True,
                        migration_backup=backup_path,
                    ),
                    writer_lock,
                    max_record_bytes=max_record_bytes,
                )

            return cls(
                registry_path,
                connection,
                RegistryStatus(
                    RegistryMode.READ_ONLY_RECOVERY,
                    version,
                    None,
                    True,
                    "registry schema version is unsupported",
                ),
                writer_lock,
                max_record_bytes=max_record_bytes,
            )
        except BaseException:
            try:
                if connection is not None:
                    connection.close()
            finally:
                if writer_lock is not None:
                    writer_lock.release()
            raise

    @property
    def status(self) -> RegistryStatus:
        return self._status

    def _require_connection(self) -> sqlite3.Connection:
        if self._closed or self._connection is None:
            raise RegistryError("registry is closed or unavailable")
        return self._connection

    def write_transaction(self) -> "RegistryTransaction":
        if self._status.mode is not RegistryMode.READ_WRITE:
            raise RegistryReadOnlyError("registry is not writable")
        return RegistryTransaction(self)

    def get_vm(self, vm_id: UUID | str) -> StoredVM | None:
        identity = _uuid(vm_id, "vm_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM vms WHERE vm_id=? "
                "ORDER BY active DESC,generation DESC LIMIT 1",
                (str(identity),),
            ).fetchone()
        return self._stored_vm(row) if row is not None else None

    def list_vms(self, active_only: bool = False) -> tuple[StoredVM, ...]:
        _bool(active_only, "active_only")
        query = (
            "SELECT current.* FROM vms AS current WHERE "
            + (
                "current.active=1 "
                if active_only
                else (
                    "current.active=1 OR ("
                    "NOT EXISTS(SELECT 1 FROM vms AS active "
                    "WHERE active.vm_id=current.vm_id AND active.active=1) "
                    "AND NOT EXISTS(SELECT 1 FROM vms AS newer "
                    "WHERE newer.vm_id=current.vm_id "
                    "AND newer.generation>current.generation)) "
                )
            )
            + "ORDER BY current.normalized_name,current.vm_id"
        )
        with self._mutex:
            rows = self._require_connection().execute(query).fetchall()
        return tuple(self._stored_vm(row) for row in rows)

    def _stored_vm(self, row: sqlite3.Row) -> StoredVM:
        return _stored_vm_from_row(row, self._max_record_bytes)

    def get_base_image(
        self,
        image_id: UUID | str,
    ) -> BaseImageRecord | None:
        selected = _uuid(image_id, "image_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM base_images WHERE image_id=?",
                (str(selected),),
            ).fetchone()
        return _base_image_from_row(row) if row is not None else None

    def list_base_images(
        self,
        *,
        active_only: bool = True,
    ) -> tuple[BaseImageRecord, ...]:
        _bool(active_only, "active_only")
        query = "SELECT * FROM base_images"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY image_id"
        with self._mutex:
            rows = self._require_connection().execute(query).fetchall()
        return tuple(_base_image_from_row(row) for row in rows)

    def get_disk(self, disk_id: UUID | str) -> DiskRecord | None:
        selected = _uuid(disk_id, "disk_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM disks WHERE disk_id=?",
                (str(selected),),
            ).fetchone()
        return _disk_from_row(row) if row is not None else None

    def list_disks(
        self,
        *,
        vm_id: UUID | str | None = None,
        active_only: bool = False,
        materialized_only: bool = False,
    ) -> tuple[DiskRecord, ...]:
        _bool(active_only, "active_only")
        _bool(materialized_only, "materialized_only")
        clauses: list[str] = []
        parameters: list[object] = []
        if vm_id is not None:
            clauses.append("vm_id=?")
            parameters.append(str(_uuid(vm_id, "vm_id")))
        if active_only:
            clauses.append("active=1")
        if materialized_only:
            clauses.append("materialized=1")
        query = "SELECT * FROM disks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY vm_id,generation,disk_id"
        with self._mutex:
            rows = self._require_connection().execute(
                query,
                tuple(parameters),
            ).fetchall()
        return tuple(_disk_from_row(row) for row in rows)

    def get_disk_runtime_observation(
        self,
        observation_id: UUID | str,
    ) -> DiskRuntimeObservation | None:
        selected = _uuid(observation_id, "observation_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM disk_runtime_observations "
                "WHERE observation_id=?",
                (str(selected),),
            ).fetchone()
        return (
            _disk_runtime_observation_from_row(row)
            if row is not None
            else None
        )

    def latest_disk_runtime_observation(
        self,
        disk_id: UUID | str,
    ) -> DiskRuntimeObservation | None:
        selected = _uuid(disk_id, "disk_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM disk_runtime_observations "
                "WHERE disk_id=? ORDER BY observed_at DESC LIMIT 1",
                (str(selected),),
            ).fetchone()
        return (
            _disk_runtime_observation_from_row(row)
            if row is not None
            else None
        )

    def list_disk_runtime_observations(
        self,
        *,
        disk_id: UUID | str | None = None,
        vm_id: UUID | str | None = None,
        generation: int | None = None,
        limit: int = 1_000,
    ) -> tuple[DiskRuntimeObservation, ...]:
        if generation is not None:
            selected_generation = _non_negative_int(
                generation,
                "generation",
            )
            if vm_id is None:
                raise RegistryConflictError(
                    "generation filtering requires vm_id"
                )
        else:
            selected_generation = None
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 10_000
        ):
            raise RegistryConflictError(
                "disk observation list limit is outside the supported bound"
            )
        clauses: list[str] = []
        parameters: list[object] = []
        if disk_id is not None:
            clauses.append("disk_id=?")
            parameters.append(str(_uuid(disk_id, "disk_id")))
        if vm_id is not None:
            clauses.append("vm_id=?")
            parameters.append(str(_uuid(vm_id, "vm_id")))
        if selected_generation is not None:
            clauses.append("generation=?")
            parameters.append(selected_generation)
        query = "SELECT * FROM disk_runtime_observations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at,observation_id LIMIT ?"
        parameters.append(limit)
        with self._mutex:
            rows = self._require_connection().execute(
                query,
                tuple(parameters),
            ).fetchall()
        return tuple(
            _disk_runtime_observation_from_row(row)
            for row in rows
        )

    def get_process_identity(
        self,
        process_record_id: UUID | str,
    ) -> ProcessIdentityRecord | None:
        selected = _uuid(process_record_id, "process_record_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM process_identities "
                "WHERE process_record_id=?",
                (str(selected),),
            ).fetchone()
        return (
            _process_identity_from_row(row)
            if row is not None
            else None
        )

    def get_active_process_identity(
        self,
        vm_id: UUID | str,
        *,
        generation: int,
        boot_id: UUID | str,
    ) -> ProcessIdentityRecord | None:
        selected_vm = _uuid(vm_id, "vm_id")
        selected_generation = _non_negative_int(
            generation,
            "generation",
        )
        selected_boot = _uuid(boot_id, "boot_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM process_identities "
                "WHERE vm_id=? AND generation=? AND boot_id=? AND active=1",
                (
                    str(selected_vm),
                    selected_generation,
                    str(selected_boot),
                ),
            ).fetchone()
        return (
            _process_identity_from_row(row)
            if row is not None
            else None
        )

    def list_process_identities(
        self,
        *,
        vm_id: UUID | str | None = None,
        generation: int | None = None,
        boot_id: UUID | str | None = None,
        active_only: bool = False,
        limit: int = 1_000,
    ) -> tuple[ProcessIdentityRecord, ...]:
        selected_vm = (
            _uuid(vm_id, "vm_id")
            if vm_id is not None
            else None
        )
        if generation is not None:
            selected_generation = _non_negative_int(
                generation,
                "generation",
            )
            if selected_vm is None:
                raise RegistryConflictError(
                    "process generation filtering requires vm_id"
                )
        else:
            selected_generation = None
        if boot_id is not None:
            selected_boot = _uuid(boot_id, "boot_id")
            if selected_vm is None or selected_generation is None:
                raise RegistryConflictError(
                    "process boot filtering requires VM generation"
                )
        else:
            selected_boot = None
        _bool(active_only, "active_only")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 10_000
        ):
            raise RegistryConflictError(
                "process identity list limit is outside the supported bound"
            )
        clauses: list[str] = []
        parameters: list[object] = []
        if selected_vm is not None:
            clauses.append("vm_id=?")
            parameters.append(str(selected_vm))
        if selected_generation is not None:
            clauses.append("generation=?")
            parameters.append(selected_generation)
        if selected_boot is not None:
            clauses.append("boot_id=?")
            parameters.append(str(selected_boot))
        if active_only:
            clauses.append("active=1")
        query = "SELECT * FROM process_identities"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at,process_record_id LIMIT ?"
        parameters.append(limit)
        with self._mutex:
            rows = self._require_connection().execute(
                query,
                tuple(parameters),
            ).fetchall()
        return tuple(_process_identity_from_row(row) for row in rows)

    def get_process_exit_observation(
        self,
        exit_evidence_id: UUID | str,
    ) -> ProcessExitObservation | None:
        selected = _uuid(exit_evidence_id, "exit_evidence_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM process_exit_observations "
                "WHERE exit_evidence_id=?",
                (str(selected),),
            ).fetchone()
        return (
            _process_exit_observation_from_row(row)
            if row is not None
            else None
        )

    def list_process_exit_observations(
        self,
        *,
        process_record_id: UUID | str | None = None,
        vm_id: UUID | str | None = None,
        generation: int | None = None,
        limit: int = 1_000,
    ) -> tuple[ProcessExitObservation, ...]:
        selected_process = (
            _uuid(process_record_id, "process_record_id")
            if process_record_id is not None
            else None
        )
        selected_vm = (
            _uuid(vm_id, "vm_id")
            if vm_id is not None
            else None
        )
        if generation is not None:
            selected_generation = _non_negative_int(
                generation,
                "generation",
            )
            if selected_vm is None:
                raise RegistryConflictError(
                    "process exit generation filtering requires vm_id"
                )
        else:
            selected_generation = None
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 10_000
        ):
            raise RegistryConflictError(
                "process exit list limit is outside the supported bound"
            )
        clauses: list[str] = []
        parameters: list[object] = []
        if selected_process is not None:
            clauses.append("process_record_id=?")
            parameters.append(str(selected_process))
        if selected_vm is not None:
            clauses.append("vm_id=?")
            parameters.append(str(selected_vm))
        if selected_generation is not None:
            clauses.append("generation=?")
            parameters.append(selected_generation)
        query = "SELECT * FROM process_exit_observations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_absent_at,exit_evidence_id LIMIT ?"
        parameters.append(limit)
        with self._mutex:
            rows = self._require_connection().execute(
                query,
                tuple(parameters),
            ).fetchall()
        return tuple(
            _process_exit_observation_from_row(row)
            for row in rows
        )

    def find_idempotency(self, key: str) -> IdempotencyRecord | None:
        selected = _bounded_key(key, "idempotency_key")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM idempotency WHERE idempotency_key=?",
                (selected,),
            ).fetchone()
        return _idempotency_from_row(row) if row is not None else None

    def get_operation(
        self,
        operation_id: UUID | str,
    ) -> OperationRecord | None:
        selected = _uuid(operation_id, "operation_id")
        with self._mutex:
            row = self._require_connection().execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (str(selected),),
            ).fetchone()
        return _operation_from_row(row) if row is not None else None

    def list_operations(
        self,
        *,
        vm_id: UUID | str | None = None,
        status: str | None = None,
        incomplete_only: bool = False,
        limit: int = 1_000,
    ) -> tuple[OperationRecord, ...]:
        selected_vm = (
            str(_uuid(vm_id, "vm_id")) if vm_id is not None else None
        )
        selected_status: str | None = None
        if status is not None:
            selected_status = _token(status, "operation status")
            if selected_status not in _OPERATION_STATUSES:
                raise RegistryConflictError(
                    "operation status is unsupported"
                )
        _bool(incomplete_only, "incomplete_only")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 10_000
        ):
            raise RegistryConflictError(
                "operation list limit is outside the supported bound"
            )
        clauses: list[str] = []
        parameters: list[object] = []
        if selected_vm is not None:
            clauses.append("vm_id=?")
            parameters.append(selected_vm)
        if selected_status is not None:
            clauses.append("status=?")
            parameters.append(selected_status)
        if incomplete_only:
            clauses.append(
                "status IN ('pending','running','recovery_required')"
            )
        query = "SELECT * FROM operations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at,operation_id LIMIT ?"
        parameters.append(limit)
        with self._mutex:
            rows = self._require_connection().execute(
                query,
                tuple(parameters),
            ).fetchall()
        return tuple(_operation_from_row(row) for row in rows)

    def list_checkpoints(
        self,
        operation_id: UUID | str,
    ) -> tuple[OperationCheckpoint, ...]:
        selected = _uuid(operation_id, "operation_id")
        with self._mutex:
            operation = self._require_connection().execute(
                "SELECT latest_checkpoint FROM operations "
                "WHERE operation_id=?",
                (str(selected),),
            ).fetchone()
            if operation is None:
                raise RegistryNotFoundError("operation does not exist")
            rows = self._require_connection().execute(
                "SELECT * FROM operation_checkpoints "
                "WHERE operation_id=? ORDER BY ordinal",
                (str(selected),),
            ).fetchall()
        checkpoints = tuple(_checkpoint_from_row(row) for row in rows)
        expected_ordinals = tuple(range(len(checkpoints)))
        if tuple(item.ordinal for item in checkpoints) != expected_ordinals:
            raise RegistryIntegrityError(
                "operation checkpoints are not contiguous"
            )
        latest = operation["latest_checkpoint"]
        expected_latest = checkpoints[-1].ordinal if checkpoints else None
        if latest != expected_latest:
            raise RegistryIntegrityError(
                "operation latest checkpoint metadata disagrees"
            )
        return checkpoints

    def copy_for_recovery(self, destination: str | Path) -> Path:
        try:
            selected = Path(destination)
        except (TypeError, ValueError) as exc:
            raise RegistryError("recovery destination is invalid") from exc
        if (
            not selected.is_absolute()
            or selected == Path("/")
            or selected.resolve(strict=False) != selected
            or selected == self.path
        ):
            raise RegistryError(
                "recovery destination must be canonical, absolute, and distinct"
            )
        _prepare_registry_parent(selected)
        if selected.exists():
            raise RegistryConflictError(
                "recovery destination already exists"
            )

        temporary = selected.with_name(f".{selected.name}.{uuid4()}.tmp")
        created: list[Path] = []
        try:
            with self._mutex:
                source = self._connection
                if source is not None:
                    backup_connection: sqlite3.Connection | None = None
                    try:
                        fd = os.open(
                            temporary,
                            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                            0o600,
                        )
                        os.close(fd)
                        created.append(temporary)
                        backup_connection = sqlite3.connect(
                            temporary,
                            isolation_level=None,
                        )
                        source.backup(backup_connection)
                        backup_connection.close()
                        backup_connection = None
                        with temporary.open("rb") as handle:
                            os.fsync(handle.fileno())
                        os.link(temporary, selected)
                        created.append(selected)
                        temporary.unlink()
                        created.remove(temporary)
                        self._fsync_parent(selected)
                        return selected
                    except (OSError, sqlite3.Error):
                        if backup_connection is not None:
                            backup_connection.close()
                        for candidate in tuple(created):
                            try:
                                candidate.unlink()
                            except FileNotFoundError:
                                pass
                            created.remove(candidate)

                raw_sources = [
                    self.path,
                    Path(f"{self.path}-wal"),
                    Path(f"{self.path}-shm"),
                ]
                raw_destinations = [
                    selected,
                    Path(f"{selected}-wal"),
                    Path(f"{selected}-shm"),
                ]
                if any(candidate.exists() for candidate in raw_destinations):
                    raise RegistryConflictError(
                        "recovery destination or sidecar already exists"
                    )
                staged: list[tuple[Path, Path]] = []
                for source_path, destination_path in zip(
                    raw_sources,
                    raw_destinations,
                    strict=True,
                ):
                    if not source_path.exists():
                        continue
                    stage = destination_path.with_name(
                        f".{destination_path.name}.{uuid4()}.tmp"
                    )
                    source_fd = os.open(
                        source_path,
                        os.O_RDONLY
                        | os.O_CLOEXEC
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                    destination_fd = os.open(
                        stage,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_CLOEXEC,
                        0o600,
                    )
                    try:
                        while True:
                            block = os.read(source_fd, 1024 * 1024)
                            if not block:
                                break
                            view = memoryview(block)
                            while view:
                                written = os.write(destination_fd, view)
                                view = view[written:]
                        os.fsync(destination_fd)
                    finally:
                        os.close(source_fd)
                        os.close(destination_fd)
                    created.append(stage)
                    staged.append((stage, destination_path))
                if not any(target == selected for _, target in staged):
                    raise RegistryNotFoundError(
                        "registry bytes are unavailable for recovery"
                    )
                for stage, destination_path in sorted(
                    staged,
                    key=lambda item: item[1] == selected,
                ):
                    os.link(stage, destination_path)
                    created.append(destination_path)
                for stage, _ in staged:
                    stage.unlink()
                    created.remove(stage)
                self._fsync_parent(selected)
                return selected
        except BaseException:
            for candidate in reversed(created):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            raise

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def close(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            try:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
            finally:
                if self._writer_lock is not None:
                    self._writer_lock.release()
                    self._writer_lock = None

    def __enter__(self) -> "SQLiteRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class RegistryTransaction(AbstractContextManager["RegistryTransaction"]):
    """One serialized ``BEGIN IMMEDIATE`` mutation boundary."""

    def __init__(self, registry: SQLiteRegistry) -> None:
        self._registry = registry
        self._active = False

    def __enter__(self) -> "RegistryTransaction":
        if self._active:
            raise RegistryError("registry transaction is already active")
        self._registry._mutex.acquire()
        try:
            self._registry._require_connection().execute("BEGIN IMMEDIATE")
        except BaseException:
            self._registry._mutex.release()
            raise
        self._active = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            connection = self._registry._require_connection()
            if exc_type is None:
                try:
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
            elif connection.in_transaction:
                connection.execute("ROLLBACK")
        finally:
            self._active = False
            self._registry._mutex.release()

    def _connection(self) -> sqlite3.Connection:
        if not self._active:
            raise RegistryError("registry transaction is not active")
        return self._registry._require_connection()

    @contextmanager
    def _atomic_step(self) -> Iterator[None]:
        """Keep one multi-statement method atomic if its caller catches failure."""

        connection = self._connection()
        name = f"registry_step_{uuid4().hex}"
        connection.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            connection.execute(f"ROLLBACK TO {name}")
            connection.execute(f"RELEASE {name}")
            raise
        else:
            connection.execute(f"RELEASE {name}")

    def insert_vm(self, record: VMRecord, *, active: bool = True) -> StoredVM:
        selected_active = _bool(active, "active")
        encoded, digest = _encode_vm(
            record, self._registry._max_record_bytes
        )
        try:
            self._connection().execute(
                "INSERT INTO vms("
                "vm_id,generation,normalized_name,qmp_socket,ssh_port,"
                "agent_port,display_port,intent_digest,record_bytes,"
                "record_sha256,revision,active,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (
                    str(record.vm_id),
                    record.generation,
                    record.definition.name.casefold(),
                    record.qmp_socket or None,
                    record.ports.ssh,
                    record.ports.agent,
                    record.ports.vnc,
                    record.intent_digest,
                    encoded,
                    digest,
                    int(selected_active),
                    _utc_text(),
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "VM identity, name, or QMP socket already has an owner"
            ) from exc
        return StoredVM(record, 0, selected_active)

    def compare_and_swap_vm(
        self,
        vm_id: UUID | str,
        expected_revision: int,
        record: VMRecord,
        *,
        active: bool | None = None,
    ) -> StoredVM:
        identity = _uuid(vm_id, "vm_id")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise RegistryConflictError("expected_revision must be non-negative")
        row = self._connection().execute(
            "SELECT * FROM vms WHERE vm_id=? "
            "ORDER BY active DESC,generation DESC LIMIT 1",
            (str(identity),),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("VM record does not exist")
        if int(row["revision"]) != expected_revision:
            raise RegistryRevisionConflict("VM revision compare-and-swap failed")
        current_generation = int(row["generation"])
        previous = self._registry._stored_vm(row).record
        if record.vm_id != identity:
            raise RegistryConflictError(
                "VM compare-and-swap cannot replace VM identity"
            )
        if record.generation not in {
            current_generation,
            current_generation + 1,
        }:
            raise RegistryConflictError(
                "VM generation must remain current or advance exactly once"
            )
        if record.generation == current_generation:
            if record.intent_digest != previous.intent_digest:
                raise RegistryConflictError(
                    "same-generation update cannot replace declaration intent"
                )
            previous_transitions = previous.transitions
            if (
                len(record.transitions) < len(previous_transitions)
                or record.transitions[: len(previous_transitions)]
                != previous_transitions
            ):
                raise RegistryConflictError(
                    "same-generation transition history must be append-only"
                )
            if (
                record.created_at != previous.created_at
                or (
                    previous.migration_source is not None
                    and record.migration_source
                    != previous.migration_source
                )
                or len(record.migration_notes)
                < len(previous.migration_notes)
                or record.migration_notes[
                    : len(previous.migration_notes)
                ]
                != previous.migration_notes
            ):
                raise RegistryConflictError(
                    "same-generation record lineage must be append-only"
                )
            for label, old_path, new_path in (
                (
                    "qmp_socket",
                    previous.qmp_socket,
                    record.qmp_socket,
                ),
                ("pid_file", previous.pid_file, record.pid_file),
                ("log_path", previous.log_path, record.log_path),
            ):
                if old_path and new_path != old_path:
                    raise RegistryConflictError(
                        f"same-generation {label} cannot be replaced"
                    )
        elif (
            previous.state is not VMState.DESTROYED
            or record.state is not VMState.DECLARED
            or record.transitions
        ):
            raise RegistryConflictError(
                "new generation requires destroyed history and a clean declaration"
            )
        selected_active = bool(row["active"]) if active is None else _bool(active, "active")
        encoded, digest = _encode_vm(
            record, self._registry._max_record_bytes
        )
        revision = expected_revision + 1
        if record.generation != current_generation:
            conflict = self._connection().execute(
                """
                SELECT 1 FROM vms AS owner
                WHERE (
                    owner.vm_id=? AND owner.generation=?
                ) OR (
                    owner.active=1
                    AND owner.vm_id!=?
                    AND (
                        owner.normalized_name=?
                        OR (
                            ? IS NOT NULL
                            AND owner.qmp_socket=?
                        )
                        OR ? IN (
                            owner.ssh_port,
                            owner.agent_port,
                            owner.display_port
                        )
                        OR ? IN (
                            owner.ssh_port,
                            owner.agent_port,
                            owner.display_port
                        )
                        OR ? IN (
                            owner.ssh_port,
                            owner.agent_port,
                            owner.display_port
                        )
                    )
                )
                LIMIT 1
                """,
                (
                    str(identity),
                    record.generation,
                    str(identity),
                    record.definition.name.casefold(),
                    record.qmp_socket or None,
                    record.qmp_socket or None,
                    record.ports.ssh,
                    record.ports.agent,
                    record.ports.vnc,
                ),
            ).fetchone()
            if conflict is not None:
                raise RegistryConflictError(
                    "new VM generation conflicts with an active owner"
                )
        try:
            if record.generation == current_generation:
                cursor = self._connection().execute(
                    "UPDATE vms SET normalized_name=?,qmp_socket=?,ssh_port=?,"
                    "agent_port=?,display_port=?,record_bytes=?,record_sha256=?,"
                    "revision=?,active=?,updated_at=? "
                    "WHERE vm_id=? AND generation=? AND revision=?",
                    (
                        record.definition.name.casefold(),
                        record.qmp_socket or None,
                        record.ports.ssh,
                        record.ports.agent,
                        record.ports.vnc,
                        encoded,
                        digest,
                        revision,
                        int(selected_active),
                        _utc_text(),
                        str(identity),
                        current_generation,
                        expected_revision,
                    ),
                )
            else:
                deactivated = self._connection().execute(
                    "UPDATE vms SET active=0,updated_at=? "
                    "WHERE vm_id=? AND generation=? AND revision=?",
                    (
                        _utc_text(),
                        str(identity),
                        current_generation,
                        expected_revision,
                    ),
                )
                if deactivated.rowcount != 1:
                    raise RegistryRevisionConflict(
                        "VM revision compare-and-swap failed"
                    )
                cursor = self._connection().execute(
                    "INSERT INTO vms("
                    "vm_id,generation,normalized_name,qmp_socket,ssh_port,"
                    "agent_port,display_port,intent_digest,record_bytes,"
                    "record_sha256,revision,active,created_at,updated_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(identity),
                        record.generation,
                        record.definition.name.casefold(),
                        record.qmp_socket or None,
                        record.ports.ssh,
                        record.ports.agent,
                        record.ports.vnc,
                        record.intent_digest,
                        encoded,
                        digest,
                        revision,
                        int(selected_active),
                        _utc_text(),
                        _utc_text(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "updated VM name or QMP socket conflicts with another owner"
            ) from exc
        if cursor.rowcount != 1:
            raise RegistryRevisionConflict("VM revision compare-and-swap failed")
        return StoredVM(record, revision, selected_active)

    def get_vm(self, vm_id: UUID | str) -> StoredVM | None:
        identity = _uuid(vm_id, "vm_id")
        row = self._connection().execute(
            "SELECT * FROM vms WHERE vm_id=? "
            "ORDER BY active DESC,generation DESC LIMIT 1",
            (str(identity),),
        ).fetchone()
        return self._registry._stored_vm(row) if row is not None else None

    def get_base_image(
        self,
        image_id: UUID | str,
    ) -> BaseImageRecord | None:
        selected = _uuid(image_id, "image_id")
        row = self._connection().execute(
            "SELECT * FROM base_images WHERE image_id=?",
            (str(selected),),
        ).fetchone()
        return _base_image_from_row(row) if row is not None else None

    def get_disk(self, disk_id: UUID | str) -> DiskRecord | None:
        selected = _uuid(disk_id, "disk_id")
        row = self._connection().execute(
            "SELECT * FROM disks WHERE disk_id=?",
            (str(selected),),
        ).fetchone()
        return _disk_from_row(row) if row is not None else None

    def get_disk_runtime_observation(
        self,
        observation_id: UUID | str,
    ) -> DiskRuntimeObservation | None:
        selected = _uuid(observation_id, "observation_id")
        row = self._connection().execute(
            "SELECT * FROM disk_runtime_observations "
            "WHERE observation_id=?",
            (str(selected),),
        ).fetchone()
        return (
            _disk_runtime_observation_from_row(row)
            if row is not None
            else None
        )

    def get_process_identity(
        self,
        process_record_id: UUID | str,
    ) -> ProcessIdentityRecord | None:
        selected = _uuid(process_record_id, "process_record_id")
        row = self._connection().execute(
            "SELECT * FROM process_identities "
            "WHERE process_record_id=?",
            (str(selected),),
        ).fetchone()
        return (
            _process_identity_from_row(row)
            if row is not None
            else None
        )

    def get_process_exit_observation(
        self,
        exit_evidence_id: UUID | str,
    ) -> ProcessExitObservation | None:
        selected = _uuid(exit_evidence_id, "exit_evidence_id")
        row = self._connection().execute(
            "SELECT * FROM process_exit_observations "
            "WHERE exit_evidence_id=?",
            (str(selected),),
        ).fetchone()
        return (
            _process_exit_observation_from_row(row)
            if row is not None
            else None
        )

    def count_active_vms(self) -> int:
        return int(
            self._connection()
            .execute("SELECT COUNT(*) FROM vms WHERE active=1")
            .fetchone()[0]
        )

    def _require_vm(self, vm_id: UUID | str) -> tuple[UUID, StoredVM]:
        identity = _uuid(vm_id, "vm_id")
        stored = self.get_vm(identity)
        if stored is None:
            raise RegistryNotFoundError("VM record does not exist")
        return identity, stored

    def _resource_operation(
        self,
        operation_id: UUID | str | None,
        vm_id: UUID,
        generation: int,
    ) -> str | None:
        selected = _optional_operation_id(operation_id)
        if selected is None:
            return None
        row = self._connection().execute(
            "SELECT vm_id,vm_generation FROM operations "
            "WHERE operation_id=?",
            (selected,),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("resource operation does not exist")
        bound_vm, bound_generation = _bound_vm_from_row(
            row["vm_id"],
            row["vm_generation"],
        )
        if bound_vm != vm_id or bound_generation != generation:
            raise RegistryConflictError(
                "resource operation is not bound to this VM generation"
            )
        return selected

    def acquire_port_lease(
        self,
        vm_id: UUID | str,
        role: str,
        port: int,
        *,
        host: str = "127.0.0.1",
        lease_id: UUID | str | None = None,
        operation_id: UUID | str | None = None,
    ) -> PortLeaseRecord:
        identity, stored = self._require_vm(vm_id)
        if not stored.active:
            raise RegistryConflictError(
                "inactive VM cannot acquire a port lease"
            )
        selected_role = _token(role, "port role")
        if selected_role not in {"ssh", "agent", "display"}:
            raise RegistryConflictError("port role is unsupported")
        if host != "127.0.0.1":
            raise RegistryConflictError(
                "current registry port leases require exact IPv4 loopback"
            )
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1_024 <= port <= 65_535
        ):
            raise RegistryConflictError("port must be between 1024 and 65535")
        expected_port = {
            "ssh": stored.record.ports.ssh,
            "agent": stored.record.ports.agent,
            "display": stored.record.ports.vnc,
        }[selected_role]
        if port != expected_port:
            raise RegistryConflictError(
                "port lease must match the VM declaration endpoint"
            )
        selected_lease = (
            uuid4() if lease_id is None else _uuid(lease_id, "lease_id")
        )
        selected_operation = self._resource_operation(
            operation_id,
            identity,
            stored.record.generation,
        )
        try:
            self._connection().execute(
                "INSERT INTO port_leases("
                "lease_id,vm_id,generation,role,host,port,active,"
                "operation_id,created_at"
                ") VALUES(?,?,?,?,?,?,1,?,?)",
                (
                    str(selected_lease),
                    str(identity),
                    stored.record.generation,
                    selected_role,
                    host,
                    port,
                    selected_operation,
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self._connection().execute(
                "SELECT vm_id,generation,role,host,port,active,operation_id "
                "FROM port_leases WHERE lease_id=?",
                (str(selected_lease),),
            ).fetchone()
            if (
                existing is not None
                and existing["vm_id"] == str(identity)
                and int(existing["generation"]) == stored.record.generation
                and existing["role"] == selected_role
                and existing["host"] == host
                and int(existing["port"]) == port
                and bool(existing["active"])
                and existing["operation_id"] == selected_operation
            ):
                return PortLeaseRecord(
                    lease_id=selected_lease,
                    vm_id=identity,
                    generation=stored.record.generation,
                    role=selected_role,
                    host=host,
                    port=port,
                    active=True,
                    operation_id=(
                        _uuid(selected_operation, "operation_id")
                        if selected_operation is not None
                        else None
                    ),
                    released_by_operation_id=None,
                )
            raise RegistryConflictError(
                "port, role, lease, or operation ownership conflicts"
            ) from exc
        return PortLeaseRecord(
            lease_id=selected_lease,
            vm_id=identity,
            generation=stored.record.generation,
            role=selected_role,
            host=host,
            port=port,
            active=True,
            operation_id=(
                _uuid(selected_operation, "operation_id")
                if selected_operation is not None
                else None
            ),
            released_by_operation_id=None,
        )

    def release_port_lease(
        self,
        lease_id: UUID | str,
        *,
        operation_id: UUID | str | None = None,
    ) -> PortLeaseRecord:
        identity = _uuid(lease_id, "lease_id")
        row = self._connection().execute(
            "SELECT vm_id,generation,role,host,port,active,operation_id,"
            "released_by_operation_id "
            "FROM port_leases WHERE lease_id=?",
            (str(identity),),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("port lease does not exist")
        selected_operation = self._resource_operation(
            operation_id,
            _uuid(row["vm_id"], "vm_id"),
            int(row["generation"]),
        )
        existing_release = row["released_by_operation_id"]
        if (
            not bool(row["active"])
            and selected_operation is not None
            and existing_release != selected_operation
        ):
            raise RegistryConflictError(
                "port release operation is already immutable"
            )
        if bool(row["active"]):
            self._connection().execute(
                "UPDATE port_leases SET active=0,released_at=?,"
                "released_by_operation_id=? "
                "WHERE lease_id=? AND active=1",
                (_utc_text(), selected_operation, str(identity)),
            )
            existing_release = selected_operation
        return PortLeaseRecord(
            lease_id=identity,
            vm_id=_uuid(row["vm_id"], "vm_id"),
            generation=int(row["generation"]),
            role=str(row["role"]),
            host=str(row["host"]),
            port=int(row["port"]),
            active=False,
            operation_id=(
                _uuid(row["operation_id"], "operation_id")
                if row["operation_id"] is not None
                else None
            ),
            released_by_operation_id=(
                _uuid(existing_release, "operation_id")
                if existing_release is not None
                else None
            ),
        )

    def register_base_image(
        self,
        manifest: ImageManifest,
        path: str | Path,
        *,
        device_id: int,
        inode: int,
        file_size_bytes: int,
        allocated_size_bytes: int,
        mode: int,
        qemu_img_version: str,
        chain_sha256: str,
        operation_id: UUID | str,
    ) -> BaseImageRecord:
        if not isinstance(manifest, ImageManifest):
            raise RegistryConflictError("manifest must be ImageManifest")
        selected_path = _canonical_path(path, "base image path")
        selected_operation = str(_uuid(operation_id, "operation_id"))
        operation = self._connection().execute(
            "SELECT vm_id,vm_generation,kind FROM operations "
            "WHERE operation_id=?",
            (selected_operation,),
        ).fetchone()
        if (
            operation is None
            or operation["vm_id"] is not None
            or operation["vm_generation"] is not None
            or operation["kind"] != "storage.import_base"
        ):
            raise RegistryConflictError(
                "base-image operation is not an unbound import owner"
            )
        numeric = {
            "device_id": device_id,
            "inode": inode,
            "file_size_bytes": file_size_bytes,
            "allocated_size_bytes": allocated_size_bytes,
            "mode": mode,
        }
        for label, value in numeric.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or (label in {"inode", "file_size_bytes"} and value < 1)
            ):
                raise RegistryConflictError(
                    f"base image {label} is outside the supported bound"
                )
        if file_size_bytes != manifest.size_bytes or mode != 0o444:
            raise RegistryConflictError(
                "base image measured bytes or mode disagree with its manifest"
            )
        if (
            not isinstance(qemu_img_version, str)
            or not qemu_img_version.startswith("qemu-img version ")
            or len(qemu_img_version) > 512
        ):
            raise RegistryConflictError("qemu-img version is invalid")
        selected_chain = _hash(chain_sha256, "base image chain hash")
        encoded = manifest.canonical_bytes
        manifest_hash = manifest.manifest_sha256
        try:
            self._connection().execute(
                "INSERT INTO base_images("
                "image_id,path,content_sha256,manifest_bytes,manifest_sha256,"
                "byte_size,format,virtual_size_bytes,device_id,inode,"
                "file_size_bytes,allocated_size_bytes,mode,qemu_img_version,"
                "chain_sha256,active,operation_id,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(manifest.image_id),
                    selected_path,
                    manifest.sha256,
                    encoded,
                    manifest_hash,
                    manifest.size_bytes,
                    manifest.format,
                    manifest.virtual_size_bytes,
                    device_id,
                    inode,
                    file_size_bytes,
                    allocated_size_bytes,
                    mode,
                    qemu_img_version,
                    selected_chain,
                    1,
                    selected_operation,
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self.get_base_image(manifest.image_id)
            if (
                existing is not None
                and existing.path == selected_path
                and existing.content_sha256 == manifest.sha256
                and existing.manifest == encoded
                and existing.device_id == device_id
                and existing.inode == inode
                and existing.file_size_bytes == file_size_bytes
                and existing.allocated_size_bytes == allocated_size_bytes
                and existing.mode == mode
                and existing.qemu_img_version == qemu_img_version
                and existing.chain_sha256 == selected_chain
                and existing.active
                and existing.operation_id == UUID(selected_operation)
            ):
                return existing
            raise RegistryConflictError(
                "base image identity, bytes, path, or inode already has an owner"
            ) from exc
        created = self.get_base_image(manifest.image_id)
        if created is None:
            raise RegistryIntegrityError(
                "base image registration was not observable"
            )
        return created

    def claim_disk(
        self,
        vm_id: UUID | str,
        path: str | Path,
        *,
        disk_id: UUID | str | None = None,
        generation: int = 0,
        ownership: str = "managed",
        active: bool = True,
        operation_id: UUID | str | None = None,
        ownership_token: UUID | str | None = None,
    ) -> DiskRecord:
        identity, stored = self._require_vm(vm_id)
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
            or generation > 2**63 - 1
            or generation != stored.record.generation
        ):
            raise RegistryConflictError(
                "disk generation must match the VM generation"
            )
        selected_path = _canonical_path(path, "disk path")
        if selected_path != _canonical_path(
            stored.record.definition.disk_path,
            "VM disk path",
        ):
            raise RegistryConflictError(
                "disk claim must match the VM declaration path"
            )
        selected_ownership = _token(ownership, "disk ownership")
        if selected_ownership not in {"managed", "imported", "external"}:
            raise RegistryConflictError("disk ownership is unsupported")
        selected_active = _bool(active, "active")
        if selected_active and not stored.active:
            raise RegistryConflictError(
                "inactive VM cannot acquire an active disk"
            )
        selected_disk = uuid4() if disk_id is None else _uuid(disk_id, "disk_id")
        selected_token = (
            uuid5(_DISK_TOKEN_NAMESPACE, str(selected_disk))
            if ownership_token is None
            else _uuid(ownership_token, "ownership_token")
        )
        selected_operation = self._resource_operation(
            operation_id,
            identity,
            stored.record.generation,
        )
        try:
            self._connection().execute(
                "INSERT INTO disks("
                "disk_id,vm_id,path,generation,ownership,active,operation_id,"
                "ownership_token,materialized,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(selected_disk),
                    str(identity),
                    selected_path,
                    generation,
                    selected_ownership,
                    int(selected_active),
                    selected_operation,
                    str(selected_token),
                    0,
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self.get_disk(selected_disk)
            if (
                existing is not None
                and existing.vm_id == identity
                and existing.path == selected_path
                and existing.generation == generation
                and existing.ownership == selected_ownership
                and existing.active is selected_active
                and existing.operation_id
                == (
                    UUID(selected_operation)
                    if selected_operation is not None
                    else None
                )
                and existing.ownership_token == selected_token
            ):
                return existing
            raise RegistryConflictError(
                "disk ID, token, or active path already has an owner"
            ) from exc
        created = self.get_disk(selected_disk)
        if created is None:
            raise RegistryIntegrityError("disk reservation was not observable")
        return created

    def materialize_disk(
        self,
        disk_id: UUID | str,
        *,
        ownership_token: UUID | str,
        base_image_id: UUID | str,
        base_image_sha256: str,
        virtual_size_bytes: int,
        format: str,
        device_id: int,
        inode: int,
        file_size_bytes: int,
        allocated_size_bytes: int,
        chain_sha256: str,
        marker_sha256: str,
        qemu_img_version: str,
        operation_id: UUID | str,
    ) -> DiskRecord:
        selected_disk = _uuid(disk_id, "disk_id")
        selected_token = _uuid(ownership_token, "ownership_token")
        selected_base = _uuid(base_image_id, "base_image_id")
        selected_base_hash = _hash(
            base_image_sha256,
            "base_image_sha256",
        )
        selected_chain = _hash(chain_sha256, "chain_sha256")
        selected_marker = _hash(marker_sha256, "marker_sha256")
        if format != "qcow2":
            raise RegistryConflictError("disk format is unsupported")
        numeric = {
            "virtual_size_bytes": virtual_size_bytes,
            "device_id": device_id,
            "inode": inode,
            "file_size_bytes": file_size_bytes,
            "allocated_size_bytes": allocated_size_bytes,
        }
        for label, value in numeric.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or (
                    label
                    in {"virtual_size_bytes", "inode", "file_size_bytes"}
                    and value < 1
                )
            ):
                raise RegistryConflictError(
                    f"disk {label} is outside the supported bound"
                )
        if (
            not isinstance(qemu_img_version, str)
            or not qemu_img_version.startswith("qemu-img version ")
            or len(qemu_img_version) > 512
        ):
            raise RegistryConflictError("qemu-img version is invalid")
        row = self._connection().execute(
            "SELECT * FROM disks WHERE disk_id=?",
            (str(selected_disk),),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("disk reservation does not exist")
        current = _disk_from_row(row)
        selected_operation = self._resource_operation(
            operation_id,
            current.vm_id,
            current.generation,
        )
        operation = self._connection().execute(
            "SELECT kind FROM operations WHERE operation_id=?",
            (selected_operation,),
        ).fetchone()
        if (
            selected_operation is None
            or operation is None
            or operation["kind"] != "storage.create_overlay"
        ):
            raise RegistryConflictError(
                "disk materialization operation has the wrong owner"
            )
        base = self.get_base_image(selected_base)
        if (
            base is None
            or not base.active
            or base.content_sha256 != selected_base_hash
            or (base.device_id, base.inode) == (device_id, inode)
        ):
            raise RegistryConflictError(
                "disk base-image authority is absent or aliased"
            )
        if (
            not current.active
            or current.ownership != "managed"
            or current.ownership_token != selected_token
        ):
            raise RegistryConflictError(
                "disk reservation ownership is inactive or mismatched"
            )
        if current.materialized:
            expected = (
                current.base_image_id == selected_base
                and current.base_image_sha256 == selected_base_hash
                and current.virtual_size_bytes == virtual_size_bytes
                and current.format == format
                and current.device_id == device_id
                and current.inode == inode
                and current.file_size_bytes == file_size_bytes
                and current.allocated_size_bytes == allocated_size_bytes
                and current.chain_sha256 == selected_chain
                and current.marker_sha256 == selected_marker
                and current.qemu_img_version == qemu_img_version
                and current.materialized_by_operation_id
                == UUID(selected_operation)
            )
            if expected:
                return current
            raise RegistryConflictError(
                "materialized disk authority is immutable"
            )
        try:
            cursor = self._connection().execute(
                "UPDATE disks SET "
                "materialized=1,base_image_id=?,base_image_sha256=?,"
                "virtual_size_bytes=?,format=?,device_id=?,inode=?,"
                "file_size_bytes=?,allocated_size_bytes=?,chain_sha256=?,"
                "marker_sha256=?,qemu_img_version=?,"
                "materialized_by_operation_id=? "
                "WHERE disk_id=? AND active=1 AND materialized=0",
                (
                    str(selected_base),
                    selected_base_hash,
                    virtual_size_bytes,
                    format,
                    device_id,
                    inode,
                    file_size_bytes,
                    allocated_size_bytes,
                    selected_chain,
                    selected_marker,
                    qemu_img_version,
                    selected_operation,
                    str(selected_disk),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "writable disk inode already has an active owner"
            ) from exc
        if cursor.rowcount != 1:
            raise RegistryConflictError(
                "disk materialization compare-and-swap failed"
            )
        materialized = self.get_disk(selected_disk)
        if materialized is None:
            raise RegistryIntegrityError(
                "materialized disk was not observable"
            )
        return materialized

    def release_disk(
        self,
        disk_id: UUID | str,
        *,
        operation_id: UUID | str | None = None,
    ) -> DiskRecord:
        identity = _uuid(disk_id, "disk_id")
        row = self._connection().execute(
            "SELECT * FROM disks WHERE disk_id=?",
            (str(identity),),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("disk claim does not exist")
        selected_operation = self._resource_operation(
            operation_id,
            _uuid(row["vm_id"], "vm_id"),
            int(row["generation"]),
        )
        existing_release = row["released_by_operation_id"]
        if (
            not bool(row["active"])
            and selected_operation is not None
            and existing_release != selected_operation
        ):
            raise RegistryConflictError(
                "disk release operation is already immutable"
            )
        if bool(row["active"]):
            self._connection().execute(
                "UPDATE disks SET active=0,released_at=?,"
                "released_by_operation_id=? "
                "WHERE disk_id=? AND active=1",
                (_utc_text(), selected_operation, str(identity)),
            )
            existing_release = selected_operation
        updated = self._connection().execute(
            "SELECT * FROM disks WHERE disk_id=?",
            (str(identity),),
        ).fetchone()
        if updated is None:
            raise RegistryIntegrityError("released disk was not observable")
        return _disk_from_row(updated)

    def append_lifecycle_event(
        self,
        vm_id: UUID | str,
        transition: TransitionRecord,
        *,
        event_id: UUID | str | None = None,
        sequence: int | None = None,
        operation_id: UUID | str | None = None,
    ) -> LifecycleEventRecord:
        identity, stored = self._require_vm(vm_id)
        if not isinstance(transition, TransitionRecord):
            raise RegistryConflictError(
                "transition must be a TransitionRecord"
            )
        evidence = transition.evidence
        if (
            evidence.vm_id != identity
            or evidence.generation != stored.record.generation
            or evidence.intent_digest != stored.record.intent_digest
        ):
            raise RegistryConflictError(
                "lifecycle event is not bound to the stored VM intent"
            )
        selected_event = (
            evidence.evidence_id
            if event_id is None
            else _uuid(event_id, "event_id")
        )
        if selected_event != evidence.evidence_id:
            raise RegistryConflictError(
                "event_id must equal transition evidence_id"
            )
        next_sequence = int(
            self._connection()
            .execute(
                "SELECT COALESCE(MAX(sequence)+1,0) "
                "FROM lifecycle_events WHERE vm_id=? AND generation=?",
                (str(identity), stored.record.generation),
            )
            .fetchone()[0]
        )
        selected_sequence = next_sequence if sequence is None else sequence
        if (
            not isinstance(selected_sequence, int)
            or isinstance(selected_sequence, bool)
            or selected_sequence < 0
        ):
            raise RegistryConflictError("event sequence must be non-negative")
        encoded = _canonical_json_bytes(
            transition.to_dict(),
            "lifecycle event",
            MAX_EVENT_BYTES,
        )
        digest = _payload_hash(encoded)
        selected_operation = self._resource_operation(
            operation_id,
            identity,
            stored.record.generation,
        )
        if selected_sequence != next_sequence:
            existing = self._connection().execute(
                "SELECT generation,sequence,event_bytes,event_sha256,"
                "operation_id FROM lifecycle_events WHERE event_id=?",
                (str(selected_event),),
            ).fetchone()
            if (
                existing is not None
                and int(existing["generation"]) == stored.record.generation
                and int(existing["sequence"]) == selected_sequence
                and existing["event_bytes"] == encoded
                and existing["event_sha256"] == digest
                and existing["operation_id"] == selected_operation
            ):
                return LifecycleEventRecord(
                    selected_event,
                    identity,
                    selected_sequence,
                    transition,
                    (
                        _uuid(selected_operation, "operation_id")
                        if selected_operation is not None
                        else None
                    ),
                )
            raise RegistryConflictError(
                "lifecycle event sequence must be contiguous"
            )
        try:
            self._connection().execute(
                "INSERT INTO lifecycle_events("
                "event_id,vm_id,generation,sequence,previous_state,target_state,"
                "event_bytes,event_sha256,operation_id,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(selected_event),
                    str(identity),
                    stored.record.generation,
                    selected_sequence,
                    transition.previous.value,
                    transition.target.value,
                    encoded,
                    digest,
                    selected_operation,
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = self._connection().execute(
                "SELECT generation,sequence,event_bytes,event_sha256,operation_id "
                "FROM lifecycle_events WHERE event_id=?",
                (str(selected_event),),
            ).fetchone()
            if (
                existing is not None
                and int(existing["generation"]) == stored.record.generation
                and int(existing["sequence"]) == selected_sequence
                and existing["event_bytes"] == encoded
                and existing["event_sha256"] == digest
                and existing["operation_id"] == selected_operation
            ):
                return LifecycleEventRecord(
                    selected_event,
                    identity,
                    selected_sequence,
                    transition,
                    (
                        _uuid(selected_operation, "operation_id")
                        if selected_operation is not None
                        else None
                    ),
                )
            raise RegistryConflictError(
                "lifecycle event ID or sequence conflicts"
            ) from exc
        return LifecycleEventRecord(
            selected_event,
            identity,
            selected_sequence,
            transition,
            (
                _uuid(selected_operation, "operation_id")
                if selected_operation is not None
                else None
            ),
        )

    def add_snapshot(
        self,
        snapshot: SnapshotReference,
        *,
        active: bool = True,
        operation_id: UUID | str | None = None,
    ) -> SnapshotRow:
        if not isinstance(snapshot, SnapshotReference):
            raise RegistryConflictError(
                "snapshot must be a SnapshotReference"
            )
        _, stored = self._require_vm(snapshot.vm_id)
        if snapshot.generation != stored.record.generation:
            raise RegistryConflictError(
                "snapshot generation does not match its VM"
            )
        selected_path = _canonical_path(snapshot.path, "snapshot path")
        selected_active = _bool(active, "active")
        if selected_active and not stored.active:
            raise RegistryConflictError(
                "inactive VM cannot add an active snapshot"
            )
        encoded = _canonical_json_bytes(
            snapshot.to_dict(),
            "snapshot reference",
            MAX_EVENT_BYTES,
        )
        digest = _payload_hash(encoded)
        selected_operation = self._resource_operation(
            operation_id,
            snapshot.vm_id,
            snapshot.generation,
        )
        try:
            self._connection().execute(
                "INSERT INTO snapshots("
                "snapshot_id,vm_id,generation,path,reference_bytes,"
                "reference_sha256,active,operation_id,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(snapshot.snapshot_id),
                    str(snapshot.vm_id),
                    snapshot.generation,
                    selected_path,
                    encoded,
                    digest,
                    int(selected_active),
                    selected_operation,
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "snapshot identity or active path conflicts"
            ) from exc
        return SnapshotRow(snapshot, selected_active, (
            _uuid(selected_operation, "operation_id")
            if selected_operation is not None
            else None
        ))

    def record_process_identity(
        self,
        vm_id: UUID | str,
        process: ProcessIdentity,
        *,
        boot_id: UUID | str,
        process_record_id: UUID | str | None = None,
        active: bool = True,
        operation_id: UUID | str | None = None,
    ) -> ProcessIdentityRecord:
        identity, stored = self._require_vm(vm_id)
        if not isinstance(process, ProcessIdentity):
            raise RegistryConflictError(
                "process must be a ProcessIdentity"
            )
        selected_boot = _uuid(boot_id, "boot_id")
        if stored.record.boot_id != selected_boot:
            raise RegistryConflictError(
                "process boot_id does not match the VM record"
            )
        selected_id = (
            uuid4()
            if process_record_id is None
            else _uuid(process_record_id, "process_record_id")
        )
        selected_active = _bool(active, "active")
        if not selected_active:
            raise RegistryConflictError(
                "new process identity must be active; "
                "retire it with exit evidence"
            )
        if selected_active and not stored.active:
            raise RegistryConflictError(
                "inactive VM cannot record an active process"
            )
        selected_operation = self._resource_operation(
            operation_id,
            identity,
            stored.record.generation,
        )
        if selected_operation is None:
            raise RegistryConflictError(
                "active process identity requires its original operation"
            )
        latest_exit = self._connection().execute(
            "SELECT observed_absent_at "
            "FROM process_exit_observations "
            "WHERE vm_id=? AND generation=? AND boot_id=? "
            "ORDER BY observed_absent_at DESC LIMIT 1",
            (
                str(identity),
                stored.record.generation,
                str(selected_boot),
            ),
        ).fetchone()
        if (
            latest_exit is not None
            and process.observed_at
            <= _decode_timestamp(
                latest_exit["observed_absent_at"],
                "observed_absent_at",
            )
        ):
            raise RegistryConflictError(
                "process identity is not newer than prior exit evidence"
            )
        encoded = _canonical_json_bytes(
            process.to_dict(),
            "process identity",
            MAX_EVENT_BYTES,
        )
        digest = _payload_hash(encoded)
        existing = self._connection().execute(
            "SELECT * FROM process_identities "
            "WHERE process_record_id=? OR ("
            "vm_id=? AND generation=? AND boot_id=? AND observed_at=?)",
            (
                str(selected_id),
                str(identity),
                stored.record.generation,
                str(selected_boot),
                process.observed_at.isoformat(),
            ),
        ).fetchone()
        if existing is not None:
            observed = _process_identity_from_row(existing)
            if observed == ProcessIdentityRecord(
                selected_id,
                identity,
                stored.record.generation,
                selected_boot,
                process,
                selected_active,
                (
                    _uuid(selected_operation, "operation_id")
                    if selected_operation is not None
                    else None
                ),
            ):
                return observed
            raise RegistryConflictError(
                "process identity observation conflicts"
            )
        if selected_active:
            active_owner = self._connection().execute(
                "SELECT process_record_id FROM process_identities "
                "WHERE vm_id=? AND generation=? AND active=1",
                (str(identity), stored.record.generation),
            ).fetchone()
            if active_owner is not None:
                raise RegistryConflictError(
                    "active process identity must be retired explicitly"
                )
        try:
            self._connection().execute(
                "INSERT INTO process_identities("
                "process_record_id,vm_id,generation,boot_id,observed_at,identity_bytes,"
                "identity_sha256,active,operation_id,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(selected_id),
                    str(identity),
                    stored.record.generation,
                    str(selected_boot),
                    process.observed_at.isoformat(),
                    encoded,
                    digest,
                    int(selected_active),
                    selected_operation,
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "process identity observation conflicts"
            ) from exc
        return ProcessIdentityRecord(
            selected_id,
            identity,
            stored.record.generation,
            selected_boot,
            process,
            selected_active,
            (
                _uuid(selected_operation, "operation_id")
                if selected_operation is not None
                else None
            ),
        )

    def retire_process_identity(
        self,
        process_record_id: UUID | str,
        vm_id: UUID | str,
        process: ProcessIdentity,
        *,
        generation: int,
        boot_id: UUID | str,
        original_operation_id: UUID | str,
        observed_absent_at: datetime,
        reason: str,
        exit_evidence_id: UUID | str | None = None,
    ) -> ProcessExitObservation:
        """Append exact absence evidence and retire only its active process."""

        selected_process_record = _uuid(
            process_record_id,
            "process_record_id",
        )
        selected_vm = _uuid(vm_id, "vm_id")
        selected_generation = _non_negative_int(
            generation,
            "generation",
        )
        selected_boot = _uuid(boot_id, "boot_id")
        selected_operation = _uuid(
            original_operation_id,
            "original_operation_id",
        )
        if not isinstance(process, ProcessIdentity):
            raise RegistryConflictError(
                "process must be a ProcessIdentity"
            )
        selected_absent_at = _utc_datetime(
            observed_absent_at,
            "observed_absent_at",
        )
        if selected_absent_at <= process.observed_at:
            raise RegistryConflictError(
                "process absence must be observed after its identity"
            )
        selected_reason = _token(reason, "process exit reason")
        selected_evidence = (
            uuid4()
            if exit_evidence_id is None
            else _uuid(exit_evidence_id, "exit_evidence_id")
        )
        process_bytes = _canonical_json_bytes(
            process.to_dict(),
            "process exit identity",
            MAX_EVENT_BYTES,
        )
        process_digest = _payload_hash(process_bytes)
        canonical = _canonical_json_bytes(
            _process_exit_observation_payload(
                exit_evidence_id=selected_evidence,
                process_record_id=selected_process_record,
                vm_id=selected_vm,
                generation=selected_generation,
                boot_id=selected_boot,
                process=process,
                original_operation_id=selected_operation,
                observed_absent_at=selected_absent_at,
                reason=selected_reason,
            ),
            "process exit observation",
            MAX_PROCESS_EXIT_OBSERVATION_BYTES,
        )
        candidate = ProcessExitObservation(
            exit_evidence_id=selected_evidence,
            process_record_id=selected_process_record,
            vm_id=selected_vm,
            generation=selected_generation,
            boot_id=selected_boot,
            process=process,
            original_operation_id=selected_operation,
            observed_absent_at=selected_absent_at,
            reason=selected_reason,
            canonical_bytes=canonical,
            canonical_sha256=_payload_hash(canonical),
        )

        rows = self._connection().execute(
            "SELECT * FROM process_exit_observations "
            "WHERE exit_evidence_id=? OR process_record_id=?",
            (
                str(selected_evidence),
                str(selected_process_record),
            ),
        ).fetchall()
        process_row = self._connection().execute(
            "SELECT * FROM process_identities WHERE process_record_id=?",
            (str(selected_process_record),),
        ).fetchone()
        if process_row is None:
            raise RegistryNotFoundError(
                "process identity does not exist"
            )
        recorded = _process_identity_from_row(process_row)
        exact_binding = (
            recorded.process_record_id == selected_process_record
            and recorded.vm_id == selected_vm
            and recorded.generation == selected_generation
            and recorded.boot_id == selected_boot
            and recorded.process == process
            and recorded.operation_id == selected_operation
        )
        if not exact_binding:
            raise RegistryConflictError(
                "process retirement does not match recorded authority"
            )
        if rows:
            if len(rows) != 1:
                raise RegistryConflictError(
                    "process exit evidence ownership conflicts"
                )
            observed = _process_exit_observation_from_row(rows[0])
            if observed == candidate and not recorded.active:
                return observed
            raise RegistryConflictError(
                "process exit evidence conflicts"
            )
        if not recorded.active:
            raise RegistryConflictError(
                "inactive process identity lacks matching exit evidence"
            )

        with self._atomic_step():
            try:
                self._connection().execute(
                    "INSERT INTO process_exit_observations("
                    "exit_evidence_id,process_record_id,vm_id,generation,"
                    "boot_id,process_observed_at,process_identity_bytes,"
                    "process_identity_sha256,original_operation_id,"
                    "observed_absent_at,reason,canonical_bytes,"
                    "canonical_sha256,created_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(selected_evidence),
                        str(selected_process_record),
                        str(selected_vm),
                        selected_generation,
                        str(selected_boot),
                        process.observed_at.isoformat(),
                        process_bytes,
                        process_digest,
                        str(selected_operation),
                        selected_absent_at.isoformat(),
                        selected_reason,
                        canonical,
                        candidate.canonical_sha256,
                        _utc_text(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RegistryConflictError(
                    "process exit conflicts with durable authority"
                ) from exc
            created_row = self._connection().execute(
                "SELECT * FROM process_exit_observations "
                "WHERE exit_evidence_id=?",
                (str(selected_evidence),),
            ).fetchone()
            retired_row = self._connection().execute(
                "SELECT * FROM process_identities "
                "WHERE process_record_id=?",
                (str(selected_process_record),),
            ).fetchone()
            if created_row is None or retired_row is None:
                raise RegistryIntegrityError(
                    "process retirement was not observable"
                )
            created = _process_exit_observation_from_row(created_row)
            retired = _process_identity_from_row(retired_row)
            if (
                created != candidate
                or retired.active
                or retired.process_record_id != selected_process_record
                or retired.vm_id != selected_vm
                or retired.generation != selected_generation
                or retired.boot_id != selected_boot
                or retired.process != process
                or retired.operation_id != selected_operation
            ):
                raise RegistryIntegrityError(
                    "process exit and exact retirement diverged"
                )
        return candidate

    def append_disk_runtime_observation(
        self,
        disk_id: UUID | str,
        *,
        vm_id: UUID | str,
        generation: int,
        observed_at: datetime,
        file_size_bytes: int,
        allocated_size_bytes: int,
        qemu_actual_size_bytes: int,
        dirty: bool,
        corrupt: bool,
        quiesced: bool,
        operation_id: UUID | str,
        observation_id: UUID | str | None = None,
        boot_id: UUID | str | None = None,
        process_record_id: UUID | str | None = None,
    ) -> DiskRuntimeObservation:
        """Append one measured disk state without mutating lifecycle authority."""

        selected_disk = _uuid(disk_id, "disk_id")
        selected_vm = _uuid(vm_id, "vm_id")
        selected_generation = _non_negative_int(generation, "generation")
        selected_time = _utc_datetime(observed_at, "observed_at")
        selected_file_size = _non_negative_int(
            file_size_bytes,
            "file_size_bytes",
            positive=True,
        )
        selected_allocation = _non_negative_int(
            allocated_size_bytes,
            "allocated_size_bytes",
        )
        selected_qemu_actual = _non_negative_int(
            qemu_actual_size_bytes,
            "qemu_actual_size_bytes",
        )
        selected_dirty = _bool(dirty, "dirty")
        selected_corrupt = _bool(corrupt, "corrupt")
        selected_quiesced = _bool(quiesced, "quiesced")
        selected_observation = (
            uuid4()
            if observation_id is None
            else _uuid(observation_id, "observation_id")
        )
        if (boot_id is None) != (process_record_id is None):
            raise RegistryConflictError(
                "boot_id and process_record_id must be supplied together"
            )
        if selected_quiesced and process_record_id is None:
            raise RegistryConflictError(
                "quiesced observation requires a process identity"
            )
        selected_boot = (
            _uuid(boot_id, "boot_id")
            if boot_id is not None
            else None
        )
        selected_process = (
            _uuid(process_record_id, "process_record_id")
            if process_record_id is not None
            else None
        )
        selected_operation = _uuid(operation_id, "operation_id")

        disk_row = self._connection().execute(
            "SELECT * FROM disks WHERE disk_id=?",
            (str(selected_disk),),
        ).fetchone()
        if disk_row is None:
            raise RegistryNotFoundError("disk reservation does not exist")
        disk = _disk_from_row(disk_row)
        if disk.vm_id != selected_vm or disk.generation != selected_generation:
            raise RegistryConflictError(
                "disk observation is not bound to the requested VM generation"
            )
        if not disk.active or not disk.materialized:
            raise RegistryConflictError(
                "disk observation requires an active materialized disk"
            )
        stored = self.get_vm(selected_vm)
        if (
            stored is None
            or not stored.active
            or stored.record.generation != selected_generation
        ):
            raise RegistryConflictError(
                "disk observation VM generation is not active"
            )
        lifecycle_quiesced = stored.record.state in {
            VMState.QUIESCED,
            VMState.SNAPSHOTTING,
        }
        if selected_quiesced is not lifecycle_quiesced:
            raise RegistryConflictError(
                "disk observation quiesced state does not match lifecycle truth"
            )

        process_time: datetime | None = None
        if selected_process is not None:
            if stored.record.boot_id != selected_boot:
                raise RegistryConflictError(
                    "disk observation boot_id does not match the VM record"
                )
            process_row = self._connection().execute(
                "SELECT * FROM process_identities "
                "WHERE process_record_id=?",
                (str(selected_process),),
            ).fetchone()
            if process_row is None:
                raise RegistryNotFoundError(
                    "disk observation process identity does not exist"
                )
            if (
                process_row["vm_id"] != str(selected_vm)
                or int(process_row["generation"]) != selected_generation
                or process_row["boot_id"] != str(selected_boot)
                or process_row["active"] != 1
            ):
                raise RegistryConflictError(
                    "disk observation process identity is not the active owner"
                )
            process_payload = _verify_payload(
                process_row["identity_bytes"],
                process_row["identity_sha256"],
                "process identity",
            )
            try:
                process = ProcessIdentity.from_dict(
                    json.loads(process_payload.decode("utf-8"))
                )
            except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
                raise RegistryIntegrityError(
                    "disk observation process identity cannot be decoded"
                ) from exc
            process_time = process.observed_at
            if (
                process_row["observed_at"] != process_time.isoformat()
                or process_time > selected_time
            ):
                raise RegistryConflictError(
                    "disk observation predates its process identity"
                )

        operation_row = self._connection().execute(
            "SELECT * FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        if operation_row is None:
            raise RegistryNotFoundError(
                "disk observation operation does not exist"
            )
        operation = _operation_from_row(operation_row)
        if (
            operation.vm_id != selected_vm
            or operation.generation != selected_generation
            or operation.status not in {"running", "completed"}
        ):
            raise RegistryConflictError(
                "disk observation operation is not an executing owner"
            )
        if operation.created_at > selected_time:
            raise RegistryConflictError(
                "disk observation predates its operation provenance"
            )

        payload = _disk_runtime_observation_payload(
            observation_id=selected_observation,
            disk_id=selected_disk,
            vm_id=selected_vm,
            generation=selected_generation,
            observed_at=selected_time,
            file_size_bytes=selected_file_size,
            allocated_size_bytes=selected_allocation,
            qemu_actual_size_bytes=selected_qemu_actual,
            dirty=selected_dirty,
            corrupt=selected_corrupt,
            boot_id=selected_boot,
            process_record_id=selected_process,
            quiesced=selected_quiesced,
            operation_id=selected_operation,
        )
        encoded = _canonical_json_bytes(
            payload,
            "disk runtime observation",
            MAX_DISK_OBSERVATION_BYTES,
        )
        digest = _payload_hash(encoded)
        candidate = DiskRuntimeObservation(
            observation_id=selected_observation,
            disk_id=selected_disk,
            vm_id=selected_vm,
            generation=selected_generation,
            observed_at=selected_time,
            file_size_bytes=selected_file_size,
            allocated_size_bytes=selected_allocation,
            qemu_actual_size_bytes=selected_qemu_actual,
            dirty=selected_dirty,
            corrupt=selected_corrupt,
            boot_id=selected_boot,
            process_record_id=selected_process,
            quiesced=selected_quiesced,
            canonical_bytes=encoded,
            canonical_sha256=digest,
            operation_id=selected_operation,
        )
        existing = self._connection().execute(
            "SELECT * FROM disk_runtime_observations "
            "WHERE observation_id=?",
            (str(selected_observation),),
        ).fetchone()
        if existing is not None:
            observed = _disk_runtime_observation_from_row(existing)
            if observed == candidate:
                return observed
            raise RegistryConflictError(
                "disk runtime observation ID conflicts"
            )
        latest = self._connection().execute(
            "SELECT observed_at FROM disk_runtime_observations "
            "WHERE disk_id=? ORDER BY observed_at DESC LIMIT 1",
            (str(selected_disk),),
        ).fetchone()
        if (
            latest is not None
            and selected_time
            <= _decode_timestamp(latest["observed_at"], "observed_at")
        ):
            raise RegistryConflictError(
                "disk runtime observations must be strictly increasing"
            )
        try:
            self._connection().execute(
                "INSERT INTO disk_runtime_observations("
                "observation_id,disk_id,vm_id,generation,observed_at,"
                "file_size_bytes,allocated_size_bytes,"
                "qemu_actual_size_bytes,dirty,corrupt,boot_id,"
                "process_record_id,quiesced,canonical_bytes,"
                "canonical_sha256,operation_id,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(selected_observation),
                    str(selected_disk),
                    str(selected_vm),
                    selected_generation,
                    selected_time.isoformat(),
                    selected_file_size,
                    selected_allocation,
                    selected_qemu_actual,
                    int(selected_dirty),
                    int(selected_corrupt),
                    (
                        str(selected_boot)
                        if selected_boot is not None
                        else None
                    ),
                    (
                        str(selected_process)
                        if selected_process is not None
                        else None
                    ),
                    int(selected_quiesced),
                    encoded,
                    digest,
                    str(selected_operation),
                    _utc_text(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "disk runtime observation conflicts with durable authority"
            ) from exc
        created = self.get_disk_runtime_observation(selected_observation)
        if created is None:
            raise RegistryIntegrityError(
                "disk runtime observation was not observable"
            )
        return created

    def begin_operation(
        self,
        operation_id: UUID | str,
        vm_id: UUID | str | None,
        kind: str,
        idempotency_key: str,
        *,
        request_hash: str,
        intent: bytes = b"{}",
        owned_resources: bytes = b"{}",
        status: str = "pending",
    ) -> OperationRecord:
        selected_operation = _uuid(operation_id, "operation_id")
        identity: UUID | None
        generation: int | None
        if vm_id is None:
            identity = None
            generation = None
        else:
            identity, stored = self._require_vm(vm_id)
            generation = stored.record.generation
        selected_kind = _token(kind, "operation kind")
        selected_key = _bounded_key(idempotency_key, "idempotency_key")
        selected_hash = _hash(request_hash, "request_hash")
        selected_status = _token(status, "operation status")
        if selected_status not in _OPERATION_STATUSES:
            raise RegistryConflictError("operation status is unsupported")
        selected_intent = _canonical_payload(intent, "operation intent")
        selected_owned = _canonical_payload(
            owned_resources,
            "operation owned_resources",
        )
        empty = b"{}"
        existing = self.find_idempotency(selected_key)
        if existing is not None:
            operation = self._connection().execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (str(existing.operation_id),),
            ).fetchone()
            if (
                existing.request_hash != selected_hash
                or operation is None
                or operation["kind"] != selected_kind
                or operation["intent_bytes"] != selected_intent
                or operation["owned_resources"] != selected_owned
                or (
                    identity is not None
                    and (
                        existing.vm_id != identity
                        or existing.generation != generation
                    )
                )
            ):
                raise RegistryConflictError(
                    "idempotency key was used for different intent"
                )
            return _operation_from_row(operation)
        now = _utc_text()
        try:
            self._connection().execute(
                "INSERT INTO operations("
                "operation_id,vm_id,vm_generation,kind,idempotency_key,"
                "request_hash,status,"
                "intent_bytes,intent_sha256,owned_resources,"
                "owned_resources_sha256,result_bytes,result_sha256,"
                "latest_checkpoint,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (
                    str(selected_operation),
                    str(identity) if identity is not None else None,
                    generation,
                    selected_kind,
                    selected_key,
                    selected_hash,
                    selected_status,
                    selected_intent,
                    _payload_hash(selected_intent),
                    selected_owned,
                    _payload_hash(selected_owned),
                    empty,
                    _payload_hash(empty),
                    now,
                    now,
                ),
            )
            self._connection().execute(
                "INSERT INTO idempotency("
                "idempotency_key,operation_id,vm_id,vm_generation,"
                "request_hash,status,"
                "result_bytes,result_sha256,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    selected_key,
                    str(selected_operation),
                    str(identity) if identity is not None else None,
                    generation,
                    selected_hash,
                    selected_status,
                    empty,
                    _payload_hash(empty),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "operation ID or idempotency key conflicts"
            ) from exc
        row = self._connection().execute(
            "SELECT * FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        assert row is not None
        return _operation_from_row(row)

    def bind_operation_vm(
        self,
        operation_id: UUID | str,
        vm_id: UUID | str,
    ) -> OperationRecord:
        selected_operation = _uuid(operation_id, "operation_id")
        identity, stored = self._require_vm(vm_id)
        generation = stored.record.generation
        row = self._connection().execute(
            "SELECT * FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("operation does not exist")
        operation = _operation_from_row(row)
        if operation.vm_id is not None:
            if (
                operation.vm_id == identity
                and operation.generation == generation
            ):
                return operation
            raise RegistryConflictError(
                "operation already has a different VM binding"
            )
        now = _utc_text()
        with self._atomic_step():
            operation_cursor = self._connection().execute(
                "UPDATE operations SET vm_id=?,vm_generation=?,updated_at=? "
                "WHERE operation_id=? AND vm_id IS NULL "
                "AND vm_generation IS NULL",
                (
                    str(identity),
                    generation,
                    now,
                    str(selected_operation),
                ),
            )
            cursor = self._connection().execute(
                "UPDATE idempotency SET vm_id=?,vm_generation=?,updated_at=? "
                "WHERE operation_id=? AND vm_id IS NULL "
                "AND vm_generation IS NULL",
                (
                    str(identity),
                    generation,
                    now,
                    str(selected_operation),
                ),
            )
            if operation_cursor.rowcount != 1 or cursor.rowcount != 1:
                raise RegistryIntegrityError(
                    "operation idempotency binding is missing or divergent"
                )
        updated = self._connection().execute(
            "SELECT * FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        assert updated is not None
        return _operation_from_row(updated)

    def append_checkpoint(
        self,
        operation_id: UUID | str,
        name: str,
        *,
        ordinal: int,
        payload: bytes = b"{}",
    ) -> OperationCheckpoint:
        selected_operation = _uuid(operation_id, "operation_id")
        selected_name = _token(name, "checkpoint name")
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 0
            or ordinal > 1_000_000
        ):
            raise RegistryConflictError(
                "checkpoint ordinal is outside the supported bound"
            )
        selected_payload = _canonical_payload(
            payload,
            "checkpoint payload",
        )
        digest = _payload_hash(selected_payload)
        operation = self._connection().execute(
            "SELECT latest_checkpoint FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        if operation is None:
            raise RegistryNotFoundError("operation does not exist")
        expected = (
            0
            if operation["latest_checkpoint"] is None
            else int(operation["latest_checkpoint"]) + 1
        )
        if ordinal != expected:
            existing = self._connection().execute(
                "SELECT * FROM operation_checkpoints "
                "WHERE operation_id=? AND ordinal=?",
                (str(selected_operation), ordinal),
            ).fetchone()
            if (
                existing is not None
                and existing["name"] == selected_name
                and existing["payload_bytes"] == selected_payload
                and existing["payload_sha256"] == digest
            ):
                return OperationCheckpoint(
                    selected_operation,
                    ordinal,
                    selected_name,
                    selected_payload,
                    digest,
                    _decode_timestamp(existing["created_at"], "created_at"),
                )
            raise RegistryConflictError(
                "checkpoint ordinal is not the next deterministic step"
            )
        now = _utc_text()
        try:
            with self._atomic_step():
                self._connection().execute(
                    "INSERT INTO operation_checkpoints("
                    "operation_id,ordinal,name,payload_bytes,payload_sha256,"
                    "created_at) VALUES(?,?,?,?,?,?)",
                    (
                        str(selected_operation),
                        ordinal,
                        selected_name,
                        selected_payload,
                        digest,
                        now,
                    ),
                )
                cursor = self._connection().execute(
                    "UPDATE operations SET latest_checkpoint=?,updated_at=? "
                    "WHERE operation_id=?",
                    (ordinal, now, str(selected_operation)),
                )
                if cursor.rowcount != 1:
                    raise RegistryIntegrityError(
                        "checkpoint operation disappeared during append"
                    )
        except sqlite3.IntegrityError as exc:
            raise RegistryConflictError(
                "checkpoint name or ordinal conflicts"
            ) from exc
        return OperationCheckpoint(
            selected_operation,
            ordinal,
            selected_name,
            selected_payload,
            digest,
            _decode_timestamp(now, "created_at"),
        )

    def set_operation_status(
        self,
        operation_id: UUID | str,
        status: str,
        *,
        result: bytes = b"{}",
    ) -> OperationRecord:
        selected_operation = _uuid(operation_id, "operation_id")
        selected_status = _token(status, "operation status")
        if selected_status not in _OPERATION_STATUSES:
            raise RegistryConflictError("operation status is unsupported")
        selected_result = _canonical_payload(result, "operation result")
        row = self._connection().execute(
            "SELECT * FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        if row is None:
            raise RegistryNotFoundError("operation does not exist")
        current = str(row["status"])
        if current in _TERMINAL_OPERATION_STATUSES:
            if current == selected_status and row["result_bytes"] == selected_result:
                return _operation_from_row(row)
            raise RegistryConflictError(
                "terminal operation status cannot be rewritten"
            )
        allowed = {
            "pending": {
                "running",
                "completed",
                "failed",
                "canceled",
                "recovery_required",
            },
            "running": {
                "completed",
                "failed",
                "canceled",
                "recovery_required",
            },
            "recovery_required": {
                "running",
                "completed",
                "failed",
                "canceled",
            },
        }
        if selected_status != current and selected_status not in allowed.get(
            current, set()
        ):
            raise RegistryConflictError(
                "operation status transition is not legal"
            )
        digest = _payload_hash(selected_result)
        now = _utc_text()
        with self._atomic_step():
            operation_cursor = self._connection().execute(
                "UPDATE operations SET status=?,result_bytes=?,"
                "result_sha256=?,updated_at=? WHERE operation_id=?",
                (
                    selected_status,
                    selected_result,
                    digest,
                    now,
                    str(selected_operation),
                ),
            )
            replay_cursor = self._connection().execute(
                "UPDATE idempotency SET status=?,result_bytes=?,"
                "result_sha256=?,updated_at=? WHERE operation_id=?",
                (
                    selected_status,
                    selected_result,
                    digest,
                    now,
                    str(selected_operation),
                ),
            )
            if operation_cursor.rowcount != 1 or replay_cursor.rowcount != 1:
                raise RegistryIntegrityError(
                    "operation result and idempotency replay diverged"
                )
        updated = self._connection().execute(
            "SELECT * FROM operations WHERE operation_id=?",
            (str(selected_operation),),
        ).fetchone()
        assert updated is not None
        return _operation_from_row(updated)

    def find_idempotency(self, key: str) -> IdempotencyRecord | None:
        selected = _bounded_key(key, "idempotency_key")
        row = self._connection().execute(
            "SELECT * FROM idempotency WHERE idempotency_key=?",
            (selected,),
        ).fetchone()
        return _idempotency_from_row(row) if row is not None else None


def _create_normalized_tables(connection: sqlite3.Connection) -> None:
    for statement in (
        """
        CREATE TABLE operations(
            operation_id TEXT PRIMARY KEY,
            vm_id TEXT,
            vm_generation INTEGER,
            kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            intent_bytes BLOB NOT NULL,
            intent_sha256 TEXT NOT NULL,
            owned_resources BLOB NOT NULL,
            owned_resources_sha256 TEXT NOT NULL,
            result_bytes BLOB NOT NULL,
            result_sha256 TEXT NOT NULL,
            latest_checkpoint INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(latest_checkpoint IS NULL OR latest_checkpoint >= 0),
            CHECK(
                (vm_id IS NULL AND vm_generation IS NULL)
                OR (vm_id IS NOT NULL AND vm_generation IS NOT NULL)
            ),
            FOREIGN KEY(vm_id,vm_generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE idempotency(
            idempotency_key TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL UNIQUE
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            vm_id TEXT,
            vm_generation INTEGER,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            result_bytes BLOB NOT NULL,
            result_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(
                (vm_id IS NULL AND vm_generation IS NULL)
                OR (vm_id IS NOT NULL AND vm_generation IS NOT NULL)
            ),
            FOREIGN KEY(vm_id,vm_generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE operation_checkpoints(
            operation_id TEXT NOT NULL
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
            name TEXT NOT NULL,
            payload_bytes BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(operation_id,ordinal),
            UNIQUE(operation_id,name)
        ) STRICT
        """,
        """
        CREATE TABLE port_leases(
            lease_id TEXT PRIMARY KEY,
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            role TEXT NOT NULL,
            host TEXT NOT NULL CHECK(host = '127.0.0.1'),
            port INTEGER NOT NULL CHECK(port BETWEEN 1024 AND 65535),
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            released_by_operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            released_at TEXT,
            FOREIGN KEY(vm_id,generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX active_port_owner
            ON port_leases(host,port) WHERE active=1
        """,
        """
        CREATE UNIQUE INDEX active_vm_port_role
            ON port_leases(vm_id,role) WHERE active=1
        """,
        """
        CREATE TABLE base_images(
            image_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            manifest_bytes BLOB NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK(byte_size > 0),
            format TEXT NOT NULL CHECK(format = 'qcow2'),
            virtual_size_bytes INTEGER NOT NULL
                CHECK(virtual_size_bytes > 0),
            device_id INTEGER NOT NULL CHECK(device_id >= 0),
            inode INTEGER NOT NULL CHECK(inode > 0),
            file_size_bytes INTEGER NOT NULL CHECK(file_size_bytes > 0),
            allocated_size_bytes INTEGER NOT NULL
                CHECK(allocated_size_bytes >= 0),
            mode INTEGER NOT NULL CHECK(mode = 292),
            qemu_img_version TEXT NOT NULL,
            chain_sha256 TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            operation_id TEXT NOT NULL
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX active_base_path
            ON base_images(path) WHERE active=1
        """,
        """
        CREATE UNIQUE INDEX active_base_content
            ON base_images(content_sha256) WHERE active=1
        """,
        """
        CREATE UNIQUE INDEX active_base_inode
            ON base_images(device_id,inode) WHERE active=1
        """,
        """
        CREATE TABLE disks(
            disk_id TEXT PRIMARY KEY,
            vm_id TEXT NOT NULL,
            path TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            ownership TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            released_by_operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            ownership_token TEXT NOT NULL,
            materialized INTEGER NOT NULL CHECK(materialized IN (0,1)),
            base_image_id TEXT
                REFERENCES base_images(image_id) ON DELETE RESTRICT,
            base_image_sha256 TEXT,
            virtual_size_bytes INTEGER
                CHECK(virtual_size_bytes IS NULL OR virtual_size_bytes > 0),
            format TEXT CHECK(format IS NULL OR format = 'qcow2'),
            device_id INTEGER CHECK(device_id IS NULL OR device_id >= 0),
            inode INTEGER CHECK(inode IS NULL OR inode > 0),
            file_size_bytes INTEGER
                CHECK(file_size_bytes IS NULL OR file_size_bytes > 0),
            allocated_size_bytes INTEGER
                CHECK(
                    allocated_size_bytes IS NULL
                    OR allocated_size_bytes >= 0
                ),
            chain_sha256 TEXT,
            marker_sha256 TEXT,
            qemu_img_version TEXT,
            materialized_by_operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            released_at TEXT,
            CHECK(
                (
                    materialized=0
                    AND base_image_id IS NULL
                    AND base_image_sha256 IS NULL
                    AND virtual_size_bytes IS NULL
                    AND format IS NULL
                    AND device_id IS NULL
                    AND inode IS NULL
                    AND file_size_bytes IS NULL
                    AND allocated_size_bytes IS NULL
                    AND chain_sha256 IS NULL
                    AND marker_sha256 IS NULL
                    AND qemu_img_version IS NULL
                    AND materialized_by_operation_id IS NULL
                )
                OR
                (
                    materialized=1
                    AND base_image_id IS NOT NULL
                    AND base_image_sha256 IS NOT NULL
                    AND virtual_size_bytes IS NOT NULL
                    AND format IS NOT NULL
                    AND device_id IS NOT NULL
                    AND inode IS NOT NULL
                    AND file_size_bytes IS NOT NULL
                    AND allocated_size_bytes IS NOT NULL
                    AND chain_sha256 IS NOT NULL
                    AND marker_sha256 IS NOT NULL
                    AND qemu_img_version IS NOT NULL
                    AND materialized_by_operation_id IS NOT NULL
                )
            ),
            FOREIGN KEY(vm_id,generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX active_disk_path
            ON disks(path) WHERE active=1
        """,
        """
        CREATE UNIQUE INDEX disk_ownership_token
            ON disks(ownership_token)
        """,
        """
        CREATE UNIQUE INDEX active_vm_disk
            ON disks(vm_id,generation) WHERE active=1
        """,
        """
        CREATE UNIQUE INDEX active_disk_inode
            ON disks(device_id,inode)
            WHERE active=1 AND materialized=1
        """,
        """
        CREATE TABLE lifecycle_events(
            event_id TEXT PRIMARY KEY,
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            sequence INTEGER NOT NULL CHECK(sequence >= 0),
            previous_state TEXT NOT NULL,
            target_state TEXT NOT NULL,
            event_bytes BLOB NOT NULL,
            event_sha256 TEXT NOT NULL,
            operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            UNIQUE(vm_id,generation,sequence),
            FOREIGN KEY(vm_id,generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE snapshots(
            snapshot_id TEXT PRIMARY KEY,
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            path TEXT NOT NULL,
            reference_bytes BLOB NOT NULL,
            reference_sha256 TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(vm_id,generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX active_snapshot_path
            ON snapshots(path) WHERE active=1
        """,
        """
        CREATE TABLE process_identities(
            process_record_id TEXT PRIMARY KEY,
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            boot_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            identity_bytes BLOB NOT NULL,
            identity_sha256 TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            operation_id TEXT
                REFERENCES operations(operation_id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL,
            UNIQUE(vm_id,generation,boot_id,observed_at),
            FOREIGN KEY(vm_id,generation)
                REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX active_process_per_vm
            ON process_identities(vm_id,generation) WHERE active=1
        """,
    ):
        connection.execute(statement)
    _create_disk_runtime_observation_schema(connection)
    _create_process_exit_observation_schema(connection)


def _create_disk_runtime_observation_schema(
    connection: sqlite3.Connection,
) -> None:
    """Create the P4 append-only disk observation authority.

    The composite indexes are deliberate foreign-key parents.  They bind an
    observation to the exact disk, operation, and optional process identity
    rather than merely proving that each standalone identifier exists.
    """

    for statement in (
        """
        CREATE UNIQUE INDEX disk_identity_binding
            ON disks(disk_id,vm_id,generation)
        """,
        """
        CREATE UNIQUE INDEX operation_identity_binding
            ON operations(operation_id,vm_id,vm_generation)
        """,
        """
        CREATE UNIQUE INDEX process_identity_binding
            ON process_identities(
                process_record_id,vm_id,generation,boot_id
            )
        """,
        """
        CREATE TABLE disk_runtime_observations(
            observation_id TEXT PRIMARY KEY,
            disk_id TEXT NOT NULL,
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            observed_at TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL CHECK(file_size_bytes > 0),
            allocated_size_bytes INTEGER NOT NULL
                CHECK(allocated_size_bytes >= 0),
            qemu_actual_size_bytes INTEGER NOT NULL
                CHECK(qemu_actual_size_bytes >= 0),
            dirty INTEGER NOT NULL CHECK(dirty IN (0,1)),
            corrupt INTEGER NOT NULL CHECK(corrupt IN (0,1)),
            boot_id TEXT,
            process_record_id TEXT,
            quiesced INTEGER NOT NULL CHECK(quiesced IN (0,1)),
            canonical_bytes BLOB NOT NULL,
            canonical_sha256 TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(
                (
                    boot_id IS NULL
                    AND process_record_id IS NULL
                    AND quiesced=0
                )
                OR (
                    boot_id IS NOT NULL
                    AND process_record_id IS NOT NULL
                )
            ),
            FOREIGN KEY(disk_id,vm_id,generation)
                REFERENCES disks(disk_id,vm_id,generation)
                ON DELETE RESTRICT,
            FOREIGN KEY(operation_id,vm_id,generation)
                REFERENCES operations(operation_id,vm_id,vm_generation)
                ON DELETE RESTRICT,
            FOREIGN KEY(process_record_id,vm_id,generation,boot_id)
                REFERENCES process_identities(
                    process_record_id,vm_id,generation,boot_id
                )
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX disk_runtime_observation_order
            ON disk_runtime_observations(disk_id,observed_at)
        """,
        """
        CREATE TRIGGER disk_runtime_observation_owner_insert
        BEFORE INSERT ON disk_runtime_observations
        WHEN NOT EXISTS(
            SELECT 1
            FROM disks AS disk
            WHERE disk.disk_id=NEW.disk_id
              AND disk.vm_id=NEW.vm_id
              AND disk.generation=NEW.generation
              AND disk.active=1
              AND disk.materialized=1
        )
        OR NOT EXISTS(
            SELECT 1
            FROM operations AS operation
            WHERE operation.operation_id=NEW.operation_id
              AND operation.vm_id=NEW.vm_id
              AND operation.vm_generation=NEW.generation
              AND operation.status IN ('running','completed')
        )
        OR (
            NEW.process_record_id IS NOT NULL
            AND NOT EXISTS(
                SELECT 1
                FROM process_identities AS process
                WHERE process.process_record_id=NEW.process_record_id
                  AND process.vm_id=NEW.vm_id
                  AND process.generation=NEW.generation
                  AND process.boot_id=NEW.boot_id
                  AND process.active=1
                  AND process.observed_at<=NEW.observed_at
            )
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'disk runtime observation owner is not active'
            );
        END
        """,
        """
        CREATE TRIGGER disk_runtime_observation_monotonic_insert
        BEFORE INSERT ON disk_runtime_observations
        WHEN EXISTS(
            SELECT 1
            FROM disk_runtime_observations AS prior
            WHERE prior.disk_id=NEW.disk_id
              AND prior.observed_at>=NEW.observed_at
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'disk runtime observations must be strictly increasing'
            );
        END
        """,
        """
        CREATE TRIGGER disk_runtime_observation_immutable_update
        BEFORE UPDATE ON disk_runtime_observations
        BEGIN
            SELECT RAISE(
                ABORT,
                'disk runtime observations are append-only'
            );
        END
        """,
        """
        CREATE TRIGGER disk_runtime_observation_immutable_delete
        BEFORE DELETE ON disk_runtime_observations
        BEGIN
            SELECT RAISE(
                ABORT,
                'disk runtime observations are append-only'
            );
        END
        """,
    ):
        connection.execute(statement)


def _create_process_exit_observation_schema(
    connection: sqlite3.Connection,
) -> None:
    """Create P5 exact process-retirement and append-only exit authority."""

    for statement in (
        """
        CREATE UNIQUE INDEX process_identity_retirement_binding
            ON process_identities(
                process_record_id,vm_id,generation,boot_id,operation_id
            )
        """,
        """
        CREATE TABLE process_exit_observations(
            exit_evidence_id TEXT PRIMARY KEY,
            process_record_id TEXT NOT NULL,
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            boot_id TEXT NOT NULL,
            process_observed_at TEXT NOT NULL,
            process_identity_bytes BLOB NOT NULL,
            process_identity_sha256 TEXT NOT NULL,
            original_operation_id TEXT NOT NULL,
            observed_absent_at TEXT NOT NULL,
            reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 64),
            canonical_bytes BLOB NOT NULL,
            canonical_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(
                process_record_id,vm_id,generation,boot_id,
                original_operation_id
            )
                REFERENCES process_identities(
                    process_record_id,vm_id,generation,boot_id,operation_id
                )
                ON DELETE RESTRICT,
            FOREIGN KEY(original_operation_id,vm_id,generation)
                REFERENCES operations(
                    operation_id,vm_id,vm_generation
                )
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX process_exit_per_identity
            ON process_exit_observations(process_record_id)
        """,
        """
        CREATE UNIQUE INDEX process_exit_observation_order
            ON process_exit_observations(
                vm_id,generation,boot_id,observed_absent_at
            )
        """,
        """
        CREATE TRIGGER process_exit_observation_owner_insert
        BEFORE INSERT ON process_exit_observations
        WHEN NOT EXISTS(
            SELECT 1
            FROM process_identities AS process
            WHERE process.process_record_id=NEW.process_record_id
              AND process.vm_id=NEW.vm_id
              AND process.generation=NEW.generation
              AND process.boot_id=NEW.boot_id
              AND process.operation_id=NEW.original_operation_id
              AND process.observed_at=NEW.process_observed_at
              AND process.identity_bytes=NEW.process_identity_bytes
              AND process.identity_sha256=NEW.process_identity_sha256
              AND process.active=1
              AND process.observed_at<NEW.observed_absent_at
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'process exit observation owner is not the active identity'
            );
        END
        """,
        """
        CREATE TRIGGER process_identity_active_insert
        BEFORE INSERT ON process_identities
        WHEN NEW.active!=1
        OR NEW.operation_id IS NULL
        OR EXISTS(
            SELECT 1
            FROM process_exit_observations AS prior
            WHERE prior.vm_id=NEW.vm_id
              AND prior.generation=NEW.generation
              AND prior.boot_id=NEW.boot_id
              AND prior.observed_absent_at>=NEW.observed_at
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'process identity requires active operation-bound authority'
            );
        END
        """,
        """
        CREATE TRIGGER process_identity_retirement_guard
        BEFORE UPDATE ON process_identities
        WHEN
            NEW.process_record_id IS NOT OLD.process_record_id
            OR NEW.vm_id IS NOT OLD.vm_id
            OR NEW.generation IS NOT OLD.generation
            OR NEW.boot_id IS NOT OLD.boot_id
            OR NEW.observed_at IS NOT OLD.observed_at
            OR NEW.identity_bytes IS NOT OLD.identity_bytes
            OR NEW.identity_sha256 IS NOT OLD.identity_sha256
            OR NEW.operation_id IS NOT OLD.operation_id
            OR NEW.created_at IS NOT OLD.created_at
            OR NOT (
                OLD.active=1
                AND NEW.active=0
                AND EXISTS(
                    SELECT 1
                    FROM process_exit_observations AS retirement
                    WHERE retirement.process_record_id=OLD.process_record_id
                      AND retirement.vm_id=OLD.vm_id
                      AND retirement.generation=OLD.generation
                      AND retirement.boot_id=OLD.boot_id
                      AND retirement.original_operation_id=OLD.operation_id
                      AND retirement.process_observed_at=OLD.observed_at
                      AND retirement.process_identity_bytes=OLD.identity_bytes
                      AND retirement.process_identity_sha256=OLD.identity_sha256
                )
            )
        BEGIN
            SELECT RAISE(
                ABORT,
                'process identity retirement requires exact exit evidence'
            );
        END
        """,
        """
        CREATE TRIGGER process_exit_observation_retire_insert
        AFTER INSERT ON process_exit_observations
        BEGIN
            UPDATE process_identities
            SET active=0
            WHERE process_record_id=NEW.process_record_id
              AND vm_id=NEW.vm_id
              AND generation=NEW.generation
              AND boot_id=NEW.boot_id
              AND operation_id=NEW.original_operation_id
              AND observed_at=NEW.process_observed_at
              AND identity_bytes=NEW.process_identity_bytes
              AND identity_sha256=NEW.process_identity_sha256
              AND active=1;
        END
        """,
        """
        CREATE TRIGGER process_exit_observation_immutable_update
        BEFORE UPDATE ON process_exit_observations
        BEGIN
            SELECT RAISE(
                ABORT,
                'process exit observations are append-only'
            );
        END
        """,
        """
        CREATE TRIGGER process_exit_observation_immutable_delete
        BEFORE DELETE ON process_exit_observations
        BEGIN
            SELECT RAISE(
                ABORT,
                'process exit observations are append-only'
            );
        END
        """,
    ):
        connection.execute(statement)


def _require_p5_process_operation_bindings(
    connection: sqlite3.Connection,
) -> None:
    if connection.execute(
        "SELECT 1 FROM process_identities "
        "WHERE active=1 AND operation_id IS NULL LIMIT 1"
    ).fetchone() is not None:
        raise RegistryIntegrityError(
            "active legacy process identity lacks original operation authority"
        )


def _create_vm_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE vms(
            vm_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK(generation >= 0),
            normalized_name TEXT NOT NULL,
            qmp_socket TEXT,
            ssh_port INTEGER NOT NULL CHECK(ssh_port BETWEEN 1024 AND 65535),
            agent_port INTEGER NOT NULL CHECK(agent_port BETWEEN 1024 AND 65535),
            display_port INTEGER NOT NULL
                CHECK(display_port BETWEEN 1024 AND 65535),
            intent_digest TEXT NOT NULL,
            record_bytes BLOB NOT NULL,
            record_sha256 TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0),
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(vm_id,generation),
            CHECK(
                ssh_port != agent_port
                AND ssh_port != display_port
                AND agent_port != display_port
            )
        ) STRICT
        """
    )
    for statement in (
        "CREATE UNIQUE INDEX active_vm_identity "
        "ON vms(vm_id) WHERE active=1",
        "CREATE UNIQUE INDEX active_vm_name "
        "ON vms(normalized_name) WHERE active=1",
        "CREATE UNIQUE INDEX active_vm_qmp "
        "ON vms(qmp_socket) WHERE active=1 AND qmp_socket IS NOT NULL",
        "CREATE UNIQUE INDEX active_vm_ssh "
        "ON vms(ssh_port) WHERE active=1",
        "CREATE UNIQUE INDEX active_vm_agent "
        "ON vms(agent_port) WHERE active=1",
        "CREATE UNIQUE INDEX active_vm_display "
        "ON vms(display_port) WHERE active=1",
        """
        CREATE TRIGGER active_vm_endpoint_insert
        BEFORE INSERT ON vms
        WHEN NEW.active=1 AND EXISTS(
            SELECT 1 FROM vms AS owner
            WHERE owner.active=1
            AND (
                NEW.ssh_port IN (
                    owner.ssh_port,owner.agent_port,owner.display_port
                )
                OR NEW.agent_port IN (
                    owner.ssh_port,owner.agent_port,owner.display_port
                )
                OR NEW.display_port IN (
                    owner.ssh_port,owner.agent_port,owner.display_port
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT,'active VM endpoint already has an owner');
        END
        """,
        """
        CREATE TRIGGER active_vm_endpoint_update
        BEFORE UPDATE OF ssh_port,agent_port,display_port,active ON vms
        WHEN NEW.active=1 AND EXISTS(
            SELECT 1 FROM vms AS owner
            WHERE owner.active=1
            AND NOT (
                owner.vm_id=OLD.vm_id
                AND owner.generation=OLD.generation
            )
            AND (
                NEW.ssh_port IN (
                    owner.ssh_port,owner.agent_port,owner.display_port
                )
                OR NEW.agent_port IN (
                    owner.ssh_port,owner.agent_port,owner.display_port
                )
                OR NEW.display_port IN (
                    owner.ssh_port,owner.agent_port,owner.display_port
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT,'active VM endpoint already has an owner');
        END
        """,
    ):
        connection.execute(statement)


def _create_schema(
    connection: sqlite3.Connection,
    journal_mode: str,
) -> None:
    if journal_mode not in {"wal", "delete"}:
        raise RegistryIntegrityError("measured journal mode is invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE registry_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) STRICT
            """
        )
        _create_vm_table(connection)
        _create_normalized_tables(connection)
        connection.execute(
            "INSERT INTO registry_meta(key,value) "
            "VALUES('journal_mode',?)",
            (journal_mode,),
        )
        connection.execute(
            f"PRAGMA user_version={REGISTRY_SCHEMA_VERSION}"
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _migrate_v1_to_current(
    connection: sqlite3.Connection,
    *,
    journal_mode: str,
    backup_path: Path,
    max_record_bytes: int,
) -> None:
    legacy_columns = frozenset(
        {
            "vm_id",
            "normalized_name",
            "qmp_socket",
            "intent_digest",
            "record_bytes",
            "record_sha256",
            "revision",
            "created_at",
            "updated_at",
        }
    )
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        if tables - {"registry_meta", "vms"}:
            raise RegistryMigrationError(
                "legacy registry contains unrecognized authority tables",
                backup_path=backup_path,
            )
        if not {"registry_meta", "vms"}.issubset(tables):
            raise RegistryMigrationError(
                "legacy registry schema is incomplete",
                backup_path=backup_path,
            )
        actual_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(vms)").fetchall()
        }
        if actual_columns != legacy_columns:
            raise RegistryMigrationError(
                "legacy VM schema is not the supported version 1 shape",
                backup_path=backup_path,
            )
        legacy_rows = connection.execute(
            "SELECT * FROM vms ORDER BY vm_id"
        ).fetchall()
        decoded_rows: list[tuple[sqlite3.Row, VMRecord]] = []
        for row in legacy_rows:
            record = _decode_vm(
                row["record_bytes"],
                row["record_sha256"],
                max_record_bytes,
            )
            if (
                str(row["vm_id"]) != str(record.vm_id)
                or row["normalized_name"] != record.definition.name.casefold()
                or row["intent_digest"] != record.intent_digest
            ):
                raise RegistryMigrationError(
                    "legacy VM materialized identity disagrees with its bytes",
                    backup_path=backup_path,
                )
            decoded_rows.append((row, record))

        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ALTER TABLE vms RENAME TO vms_legacy")
        _create_vm_table(connection)
        for row, record in decoded_rows:
            connection.execute(
                "INSERT INTO vms("
                "vm_id,generation,normalized_name,qmp_socket,ssh_port,"
                "agent_port,display_port,intent_digest,record_bytes,"
                "record_sha256,revision,active,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(record.vm_id),
                    record.generation,
                    record.definition.name.casefold(),
                    record.qmp_socket or None,
                    record.ports.ssh,
                    record.ports.agent,
                    record.ports.vnc,
                    record.intent_digest,
                    row["record_bytes"],
                    row["record_sha256"],
                    int(row["revision"]),
                    1,
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        connection.execute("DROP TABLE vms_legacy")
        _create_normalized_tables(connection)
        connection.execute(
            "INSERT INTO registry_meta(key,value) VALUES('journal_mode',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (journal_mode,),
        )
        connection.execute(
            f"PRAGMA user_version={REGISTRY_SCHEMA_VERSION}"
        )
        connection.execute("COMMIT")
    except RegistryMigrationError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except BaseException as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RegistryMigrationError(
            "registry migration from version 1 failed",
            backup_path=backup_path,
        ) from exc


def _migrate_v2_to_v3(
    connection: sqlite3.Connection,
    *,
    backup_path: Path,
    max_record_bytes: int,
) -> None:
    """Advance P2 through P3/P4 and exact P5 process retirement authority."""

    expected_v2_disk_columns = frozenset(
        {
            "disk_id",
            "vm_id",
            "path",
            "generation",
            "ownership",
            "active",
            "operation_id",
            "released_by_operation_id",
            "created_at",
            "released_at",
        }
    )
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
        }
        if "base_images" in tables or "disks" not in tables:
            raise RegistryMigrationError(
                "version 2 storage schema is not the supported shape",
                backup_path=backup_path,
            )
        actual_disk_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(disks)").fetchall()
        }
        if actual_disk_columns != expected_v2_disk_columns:
            raise RegistryMigrationError(
                "version 2 disk schema is not the supported shape",
                backup_path=backup_path,
            )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE base_images(
                image_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                manifest_bytes BLOB NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL CHECK(byte_size > 0),
                format TEXT NOT NULL CHECK(format = 'qcow2'),
                virtual_size_bytes INTEGER NOT NULL
                    CHECK(virtual_size_bytes > 0),
                device_id INTEGER NOT NULL CHECK(device_id >= 0),
                inode INTEGER NOT NULL CHECK(inode > 0),
                file_size_bytes INTEGER NOT NULL CHECK(file_size_bytes > 0),
                allocated_size_bytes INTEGER NOT NULL
                    CHECK(allocated_size_bytes >= 0),
                mode INTEGER NOT NULL CHECK(mode = 292),
                qemu_img_version TEXT NOT NULL,
                chain_sha256 TEXT NOT NULL,
                active INTEGER NOT NULL CHECK(active IN (0,1)),
                operation_id TEXT NOT NULL
                    REFERENCES operations(operation_id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL
            ) STRICT
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX active_base_path "
            "ON base_images(path) WHERE active=1"
        )
        connection.execute(
            "CREATE UNIQUE INDEX active_base_content "
            "ON base_images(content_sha256) WHERE active=1"
        )
        connection.execute(
            "CREATE UNIQUE INDEX active_base_inode "
            "ON base_images(device_id,inode) WHERE active=1"
        )
        connection.execute(
            """
            CREATE TABLE disks_v3(
                disk_id TEXT PRIMARY KEY,
                vm_id TEXT NOT NULL,
                path TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK(generation >= 0),
                ownership TEXT NOT NULL,
                active INTEGER NOT NULL CHECK(active IN (0,1)),
                operation_id TEXT
                    REFERENCES operations(operation_id) ON DELETE RESTRICT,
                released_by_operation_id TEXT
                    REFERENCES operations(operation_id) ON DELETE RESTRICT,
                ownership_token TEXT NOT NULL,
                materialized INTEGER NOT NULL CHECK(materialized IN (0,1)),
                base_image_id TEXT
                    REFERENCES base_images(image_id) ON DELETE RESTRICT,
                base_image_sha256 TEXT,
                virtual_size_bytes INTEGER
                    CHECK(virtual_size_bytes IS NULL OR virtual_size_bytes > 0),
                format TEXT CHECK(format IS NULL OR format = 'qcow2'),
                device_id INTEGER CHECK(device_id IS NULL OR device_id >= 0),
                inode INTEGER CHECK(inode IS NULL OR inode > 0),
                file_size_bytes INTEGER
                    CHECK(file_size_bytes IS NULL OR file_size_bytes > 0),
                allocated_size_bytes INTEGER
                    CHECK(
                        allocated_size_bytes IS NULL
                        OR allocated_size_bytes >= 0
                    ),
                chain_sha256 TEXT,
                marker_sha256 TEXT,
                qemu_img_version TEXT,
                materialized_by_operation_id TEXT
                    REFERENCES operations(operation_id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                released_at TEXT,
                CHECK(
                    (
                        materialized=0
                        AND base_image_id IS NULL
                        AND base_image_sha256 IS NULL
                        AND virtual_size_bytes IS NULL
                        AND format IS NULL
                        AND device_id IS NULL
                        AND inode IS NULL
                        AND file_size_bytes IS NULL
                        AND allocated_size_bytes IS NULL
                        AND chain_sha256 IS NULL
                        AND marker_sha256 IS NULL
                        AND qemu_img_version IS NULL
                        AND materialized_by_operation_id IS NULL
                    )
                    OR
                    (
                        materialized=1
                        AND base_image_id IS NOT NULL
                        AND base_image_sha256 IS NOT NULL
                        AND virtual_size_bytes IS NOT NULL
                        AND format IS NOT NULL
                        AND device_id IS NOT NULL
                        AND inode IS NOT NULL
                        AND file_size_bytes IS NOT NULL
                        AND allocated_size_bytes IS NOT NULL
                        AND chain_sha256 IS NOT NULL
                        AND marker_sha256 IS NOT NULL
                        AND qemu_img_version IS NOT NULL
                        AND materialized_by_operation_id IS NOT NULL
                    )
                ),
                FOREIGN KEY(vm_id,generation)
                    REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
            ) STRICT
            """
        )
        rows = connection.execute(
            "SELECT * FROM disks ORDER BY disk_id"
        ).fetchall()
        for row in rows:
            disk_id = _uuid(row["disk_id"], "disk_id")
            token = uuid5(_DISK_TOKEN_NAMESPACE, str(disk_id))
            connection.execute(
                """
                INSERT INTO disks_v3(
                    disk_id,vm_id,path,generation,ownership,active,
                    operation_id,released_by_operation_id,ownership_token,
                    materialized,base_image_id,base_image_sha256,
                    virtual_size_bytes,format,device_id,inode,file_size_bytes,
                    allocated_size_bytes,chain_sha256,marker_sha256,
                    qemu_img_version,materialized_by_operation_id,
                    created_at,released_at
                ) VALUES(?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,NULL,NULL,NULL,
                         NULL,NULL,NULL,NULL,NULL,NULL,?,?)
                """,
                (
                    row["disk_id"],
                    row["vm_id"],
                    row["path"],
                    row["generation"],
                    row["ownership"],
                    row["active"],
                    row["operation_id"],
                    row["released_by_operation_id"],
                    str(token),
                    row["created_at"],
                    row["released_at"],
                ),
            )
        connection.execute("DROP TABLE disks")
        connection.execute("ALTER TABLE disks_v3 RENAME TO disks")
        connection.execute(
            "CREATE UNIQUE INDEX active_disk_path "
            "ON disks(path) WHERE active=1"
        )
        connection.execute(
            "CREATE UNIQUE INDEX disk_ownership_token "
            "ON disks(ownership_token)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX active_vm_disk "
            "ON disks(vm_id,generation) WHERE active=1"
        )
        connection.execute(
            "CREATE UNIQUE INDEX active_disk_inode "
            "ON disks(device_id,inode) "
            "WHERE active=1 AND materialized=1"
        )
        _create_disk_runtime_observation_schema(connection)
        _require_p5_process_operation_bindings(connection)
        _create_process_exit_observation_schema(connection)
        connection.execute(f"PRAGMA user_version={REGISTRY_SCHEMA_VERSION}")
        integrity_ok, issue = _quick_check(connection)
        if not integrity_ok:
            raise RegistryIntegrityError(
                issue or "migrated registry quick_check failed"
            )
        _validate_current_schema(connection)
        _validate_persisted_rows(
            connection,
            max_record_bytes=max_record_bytes,
        )
        connection.execute("COMMIT")
    except RegistryMigrationError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except BaseException as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RegistryMigrationError(
            "registry migration from version 2 failed",
            backup_path=backup_path,
        ) from exc


def _migrate_v3_to_v4(
    connection: sqlite3.Connection,
    *,
    backup_path: Path,
    max_record_bytes: int,
) -> None:
    """Advance P3 through append-only P4 disk and P5 process observations."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        _preflight_p3_registry(
            connection,
            max_record_bytes=max_record_bytes,
        )
        _create_disk_runtime_observation_schema(connection)
        _require_p5_process_operation_bindings(connection)
        _create_process_exit_observation_schema(connection)
        connection.execute(f"PRAGMA user_version={REGISTRY_SCHEMA_VERSION}")
        integrity_ok, issue = _quick_check(connection)
        if not integrity_ok:
            raise RegistryIntegrityError(
                issue or "migrated registry quick_check failed"
            )
        _validate_current_schema(connection)
        _validate_persisted_rows(
            connection,
            max_record_bytes=max_record_bytes,
        )
        connection.execute("COMMIT")
    except BaseException as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RegistryMigrationError(
            "registry migration from version 3 failed",
            backup_path=backup_path,
        ) from exc


def _migrate_v4_to_v5(
    connection: sqlite3.Connection,
    *,
    backup_path: Path,
    max_record_bytes: int,
) -> None:
    """Add exact append-only process retirement without rewriting P4 facts."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        _preflight_p4_registry(
            connection,
            max_record_bytes=max_record_bytes,
        )
        _require_p5_process_operation_bindings(connection)
        _create_process_exit_observation_schema(connection)
        connection.execute(f"PRAGMA user_version={REGISTRY_SCHEMA_VERSION}")
        integrity_ok, issue = _quick_check(connection)
        if not integrity_ok:
            raise RegistryIntegrityError(
                issue or "migrated registry quick_check failed"
            )
        _validate_current_schema(connection)
        _validate_persisted_rows(
            connection,
            max_record_bytes=max_record_bytes,
        )
        connection.execute("COMMIT")
    except BaseException as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RegistryMigrationError(
            "registry migration from version 4 failed",
            backup_path=backup_path,
        ) from exc


Integrity = RegistryIntegrityError
ReadOnly = RegistryReadOnlyError
WriterBusy = RegistryWriterBusyError
Conflict = RegistryConflictError
RevisionConflict = RegistryRevisionConflict
Migration = RegistryMigrationError
NotFound = RegistryNotFoundError


__all__ = [
    "BaseImageRecord",
    "Conflict",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_MAX_RECORD_BYTES",
    "DiskRecord",
    "DiskRuntimeObservation",
    "IdempotencyRecord",
    "Integrity",
    "LEGACY_REGISTRY_SCHEMA_VERSION",
    "LifecycleEventRecord",
    "MAX_EVENT_BYTES",
    "MAX_DISK_OBSERVATION_BYTES",
    "MAX_PROCESS_EXIT_OBSERVATION_BYTES",
    "MAX_OPERATION_BYTES",
    "Migration",
    "NotFound",
    "OperationCheckpoint",
    "OperationRecord",
    "PortLeaseRecord",
    "PREVIOUS_REGISTRY_SCHEMA_VERSION",
    "P3_REGISTRY_SCHEMA_VERSION",
    "P4_REGISTRY_SCHEMA_VERSION",
    "ProcessExitObservation",
    "ProcessIdentityRecord",
    "ReadOnly",
    "REGISTRY_SCHEMA_VERSION",
    "RegistryConflictError",
    "RegistryError",
    "RegistryIntegrityError",
    "RegistryMigrationError",
    "RegistryMode",
    "RegistryNotFoundError",
    "RegistryReadOnlyError",
    "RegistryRevisionConflict",
    "RegistryStatus",
    "RegistryTransaction",
    "RegistryWriterBusyError",
    "RevisionConflict",
    "SQLiteRegistry",
    "SnapshotRow",
    "StoredVM",
    "WriterBusy",
]
