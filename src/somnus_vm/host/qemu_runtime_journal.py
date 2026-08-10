"""Closed P4 journal contract for one internal ``runtime.launch`` operation.

This module is deliberately narrower than a runtime owner.  It performs no
filesystem mutation, opens no SQLite connection, launches no process, speaks
no QMP, and exposes no public lifecycle operation.  It gives the daemon-owned
runtime composition root deterministic bytes and exact keyword arguments for
the existing registry transaction methods.

The journal closes two different parent-death handoff windows:

* the log guardian is durably observed before it is released to survive the
  daemon; and
* the QEMU exec guard is durably observed before the pinned target is
  released.

A checkpoint is evidence about one completed boundary, not permission to infer
the next boundary.  Replay therefore accepts only one frozen prefix, binds
every repeated identity to the original intent, and returns an explicit crash
class and recovery disposition without signaling or adopting anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Self
from uuid import UUID, uuid4

from somnus_protocol import VMRecord, VMState
from somnus_protocol._validation import (
    ProtocolValidationError,
    canonical_json,
)

from .qemu import (
    QemuLaunchPlan,
    QemuPlanError,
    validate_fd_bound_execution,
)
from .qemu_process import (
    ExecutableIdentity,
    ObservedProcessIdentity,
    QemuProcessError,
    command_sha256,
)
from .launch_authority import (
    DisposableLaunchReceipt,
    LaunchAuthorityError,
)
from .qmp_identity import (
    MIN_QMP_STABILIZATION_NS,
    QMP_IDENTITY_EVIDENCE_SCHEMA,
    QMP_IDENTITY_SCHEMA,
    QMPIdentityEvidence,
)
from .registry import (
    MAX_OPERATION_BYTES,
    DiskRecord,
    OperationCheckpoint,
    OperationRecord,
)


RUNTIME_LAUNCH_KIND: Final[str] = "runtime.launch"
RUNTIME_LAUNCH_INTENT_SCHEMA: Final[str] = (
    "somnus.runtime.launch.intent.v2"
)
RUNTIME_LAUNCH_RESOURCES_SCHEMA: Final[str] = (
    "somnus.runtime.launch.resources.v2"
)
RUNTIME_LAUNCH_CHECKPOINT_SCHEMA: Final[str] = (
    "somnus.runtime.launch.checkpoint.v1"
)
RUNTIME_LAUNCH_RESULT_SCHEMA: Final[str] = (
    "somnus.runtime.launch.result.v1"
)
MAX_RUNTIME_JOURNAL_BYTES: Final[int] = MAX_OPERATION_BYTES
MAX_RUNTIME_JSON_DEPTH: Final[int] = 16
MAX_RUNTIME_JSON_ITEMS: Final[int] = 8_192
MAX_RUNTIME_STRING_BYTES: Final[int] = 64 * 1024
MAX_RUNTIME_PATH_BYTES: Final[int] = 4_096

_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_.-]{0,63}$"
)
_KEY: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_IDEMPOTENCY_KEY: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_.:-]{1,128}$"
)

CHECKPOINT_ORDER: Final[tuple[str, ...]] = (
    "intent_persisted",
    "private_paths_prepared",
    "executable_pinned",
    "log_guard_spawned",
    "log_guard_released",
    "qemu_guard_spawned",
    "qemu_target_released",
    "process_observed",
    "pidfile_verified",
    "qmp_greeting_observed",
    "qmp_capabilities_negotiated",
    "qmp_identity_verified",
    "process_identity_recorded",
    "disk_observation_recorded",
    "lifecycle_committed",
    "completion_observed",
)

_PROCESS_FACT_KEYS: Final[frozenset[str]] = frozenset(
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


class RuntimeLaunchJournalError(ValueError):
    """Persisted launch facts do not form one coherent frozen prefix."""


class CrashClass(str, Enum):
    """The exact external-mutation window represented by a journal prefix."""

    NOT_PERSISTED = "not_persisted"
    PRE_SPAWN = "pre_spawn"
    LOG_GUARD_RELEASE_WINDOW = "log_guard_release_window"
    LOG_GUARD_DURABLE = "log_guard_durable"
    QEMU_GUARD_RELEASE_WINDOW = "qemu_guard_release_window"
    QEMU_TARGET_UNVERIFIED = "qemu_target_unverified"
    PROCESS_OBSERVED = "process_observed"
    PROCESS_PIDFILE_BOUND = "process_pidfile_bound"
    QMP_HANDSHAKE_PARTIAL = "qmp_handshake_partial"
    QMP_IDENTITY_VERIFIED = "qmp_identity_verified"
    RUNTIME_RECORDS_PARTIAL = "runtime_records_partial"
    LIFECYCLE_COMMITTED = "lifecycle_committed"
    COMPLETE = "complete"


class RecoveryDisposition(str, Enum):
    """The only safe next recovery family for one crash class."""

    NONE = "none"
    DROP_UNSTARTED_INTENT = "drop_unstarted_intent"
    CLEAN_PRIVATE_ARTIFACTS = "clean_private_artifacts"
    REVALIDATE_LOG_GUARD_OR_CLEAN = "revalidate_log_guard_or_clean"
    REVALIDATE_QEMU_TARGET_OR_CLEAN = "revalidate_qemu_target_or_clean"
    REVALIDATE_PROCESS_OR_CLEAN = "revalidate_process_or_clean"
    REVALIDATE_QMP_OR_CLEAN = "revalidate_qmp_or_clean"
    ADOPT_IF_STABLE_OR_CLEAN = "adopt_if_stable_or_clean"
    RECONCILE_DURABLE_RUNTIME = "reconcile_durable_runtime"
    FINALIZE_COMPLETION = "finalize_completion"
    # This is deliberately distinct from the ordinary revalidate/cleanup
    # dispositions.  It is durable authority that the recorded child could
    # not be proven absent or safely terminated; recovery must therefore keep
    # the exact process identity and checkpoint prefix for a later operator.
    ORPHAN_EMERGENCY = "orphan_emergency"


def _contains_forbidden_text(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cs"}
        for character in value
    )


def _strict_json_value(
    value: object,
    *,
    label: str,
    depth: int = 0,
    counter: list[int] | None = None,
) -> None:
    """Reject unbounded or representation-ambiguous internal JSON values."""

    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_RUNTIME_JSON_ITEMS:
        raise RuntimeLaunchJournalError(f"{label} has too many JSON items")
    if depth > MAX_RUNTIME_JSON_DEPTH:
        raise RuntimeLaunchJournalError(f"{label} exceeds the JSON depth limit")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if value < -(2**63) or value > 2**63 - 1:
            raise RuntimeLaunchJournalError(
                f"{label} contains an out-of-range integer"
            )
        return
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RuntimeLaunchJournalError(
                f"{label} contains invalid Unicode"
            ) from exc
        if (
            len(encoded) > MAX_RUNTIME_STRING_BYTES
            or _contains_forbidden_text(value)
        ):
            raise RuntimeLaunchJournalError(
                f"{label} contains an invalid or oversized string"
            )
        return
    if isinstance(value, list):
        for child in value:
            _strict_json_value(
                child,
                label=label,
                depth=depth + 1,
                counter=counter,
            )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not _KEY.fullmatch(key):
                raise RuntimeLaunchJournalError(
                    f"{label} contains an invalid object key"
                )
            _strict_json_value(
                child,
                label=label,
                depth=depth + 1,
                counter=counter,
            )
        return
    raise RuntimeLaunchJournalError(
        f"{label} contains a non-JSON or floating-point value"
    )


def _encode_json(
    value: object,
    label: str,
    *,
    maximum: int = MAX_RUNTIME_JOURNAL_BYTES,
) -> bytes:
    _strict_json_value(value, label=label)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RuntimeLaunchJournalError(
            f"{label} cannot be encoded as canonical JSON"
        ) from exc
    if not encoded or len(encoded) > maximum:
        raise RuntimeLaunchJournalError(
            f"{label} exceeds its persisted byte limit"
        )
    return encoded


def _decode_json_object(
    payload: bytes,
    label: str,
    *,
    maximum: int = MAX_RUNTIME_JOURNAL_BYTES,
) -> dict[str, object]:
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > maximum
    ):
        raise RuntimeLaunchJournalError(f"{label} bytes violate their bound")

    def unique_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, child in pairs:
            if key in output:
                raise RuntimeLaunchJournalError(
                    f"{label} contains a duplicate key"
                )
            output[key] = child
        return output

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                RuntimeLaunchJournalError(
                    f"{label} contains an invalid constant"
                )
            ),
        )
    except RuntimeLaunchJournalError:
        raise
    except (
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise RuntimeLaunchJournalError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeLaunchJournalError(f"{label} must be a JSON object")
    _strict_json_value(decoded, label=label)
    if _encode_json(decoded, label, maximum=maximum) != payload:
        raise RuntimeLaunchJournalError(f"{label} is not canonical JSON")
    return decoded


def canonical_evidence_sha256(value: object) -> str:
    """Hash one bounded JSON evidence object for a later checkpoint."""

    return hashlib.sha256(
        _encode_json(value, "runtime evidence")
    ).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(
    value: object,
    keys: frozenset[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise RuntimeLaunchJournalError(
            f"{label} must contain exactly {sorted(keys)}"
        )
    return value


def _strict_uuid(value: object, label: str) -> UUID:
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str) and len(value) <= 36:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise RuntimeLaunchJournalError(f"{label} must be a UUID") from exc
    else:
        raise RuntimeLaunchJournalError(f"{label} must be a UUID")
    if parsed.int == 0:
        raise RuntimeLaunchJournalError(f"{label} cannot be nil")
    return parsed


def _strict_int(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise RuntimeLaunchJournalError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


def _strict_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeLaunchJournalError(f"{label} must be boolean")
    return value


def _strict_token(value: object, label: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise RuntimeLaunchJournalError(
            f"{label} must be a bounded machine token"
        )
    return value


def _strict_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RuntimeLaunchJournalError(f"{label} must be a lowercase SHA256")
    return value


def _strict_path(value: object, label: str) -> str:
    if not isinstance(value, (str, os.PathLike)):
        raise RuntimeLaunchJournalError(
            f"{label} must be an absolute path"
        )
    try:
        text = os.fspath(value)
        path = PurePosixPath(text)
    except (TypeError, ValueError) as exc:
        raise RuntimeLaunchJournalError(f"{label} is invalid") from exc
    if (
        not isinstance(text, str)
        or not text.startswith("/")
        or text == "/"
        or text.startswith("//")
        or str(path) != text
        or _contains_forbidden_text(text)
    ):
        raise RuntimeLaunchJournalError(
            f"{label} must be a canonical absolute non-root path"
        )
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeLaunchJournalError(f"{label} is invalid") from exc
    if len(encoded) > MAX_RUNTIME_PATH_BYTES:
        raise RuntimeLaunchJournalError(f"{label} is too long")
    return text


def _strict_name(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 128
        or _contains_forbidden_text(value)
        or value != value.strip()
    ):
        raise RuntimeLaunchJournalError(f"{label} is invalid")
    return value


def _strict_argv(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise RuntimeLaunchJournalError(f"{label} must be an argv vector")
    argv = tuple(value)
    try:
        command_sha256(argv)
    except QemuProcessError as exc:
        raise RuntimeLaunchJournalError(f"{label} is invalid") from exc
    return argv


def _option_value(argv: tuple[str, ...], option: str) -> str:
    indexes = [
        index
        for index, value in enumerate(argv[:-1])
        if value == option
    ]
    if len(indexes) != 1:
        raise RuntimeLaunchJournalError(
            f"planned argv must contain exactly one {option}"
        )
    return argv[indexes[0] + 1]


@dataclass(frozen=True, slots=True)
class JournalExecutableIdentity:
    """Replay-safe copy of the pinned executable identity, with no fresh I/O."""

    path: str
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
        object.__setattr__(
            self,
            "path",
            _strict_path(self.path, "executable path"),
        )
        _strict_int(
            self.device_id,
            "executable device_id",
            minimum=1,
        )
        _strict_int(self.inode, "executable inode", minimum=1)
        _strict_int(
            self.owner_uid,
            "executable owner_uid",
            maximum=2**32 - 1,
        )
        mode = _strict_int(
            self.mode,
            "executable mode",
            maximum=0o7777,
        )
        if mode & 0o022 or mode & 0o111 == 0:
            raise RuntimeLaunchJournalError(
                "executable mode is writable by another principal or not executable"
            )
        if self.link_count != 1:
            raise RuntimeLaunchJournalError(
                "executable link_count must be one"
            )
        _strict_int(
            self.size_bytes,
            "executable size_bytes",
            minimum=1,
        )
        _strict_int(
            self.mtime_ns,
            "executable mtime_ns",
            maximum=2**63 - 1,
        )
        _strict_int(
            self.ctime_ns,
            "executable ctime_ns",
            maximum=2**63 - 1,
        )
        _strict_hash(self.sha256, "executable sha256")

    @classmethod
    def from_runtime(cls, value: ExecutableIdentity) -> Self:
        if not isinstance(value, ExecutableIdentity):
            raise TypeError("executable must be an ExecutableIdentity")
        return cls(
            path=os.fspath(value.path),
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

    @classmethod
    def from_dict(cls, value: object) -> Self:
        table = _strict_object(
            value,
            frozenset(
                {
                    "path",
                    "device_id",
                    "inode",
                    "owner_uid",
                    "mode",
                    "link_count",
                    "size_bytes",
                    "mtime_ns",
                    "ctime_ns",
                    "sha256",
                }
            ),
            "executable identity",
        )
        return cls(
            path=_strict_path(table["path"], "executable path"),
            device_id=_strict_int(
                table["device_id"],
                "executable device_id",
                minimum=1,
            ),
            inode=_strict_int(
                table["inode"],
                "executable inode",
                minimum=1,
            ),
            owner_uid=_strict_int(
                table["owner_uid"],
                "executable owner_uid",
                maximum=2**32 - 1,
            ),
            mode=_strict_int(
                table["mode"],
                "executable mode",
                maximum=0o7777,
            ),
            link_count=_strict_int(
                table["link_count"],
                "executable link_count",
                minimum=1,
                maximum=1,
            ),
            size_bytes=_strict_int(
                table["size_bytes"],
                "executable size_bytes",
                minimum=1,
            ),
            mtime_ns=_strict_int(
                table["mtime_ns"],
                "executable mtime_ns",
            ),
            ctime_ns=_strict_int(
                table["ctime_ns"],
                "executable ctime_ns",
            ),
            sha256=_strict_hash(table["sha256"], "executable sha256"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "device_id": self.device_id,
            "inode": self.inode,
            "owner_uid": self.owner_uid,
            "mode": self.mode,
            "link_count": self.link_count,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class RuntimeLaunchIntent:
    """All identities and cleanup resources fixed before external mutation."""

    operation_id: UUID
    launch_authority: DisposableLaunchReceipt
    vm_id: UUID
    generation: int
    vm_name: str
    memory_mib: int
    vcpus: int
    enable_kvm: bool
    boot_id: UUID
    disk_id: UUID
    disk_path: str
    disk_device_id: int
    disk_inode: int
    disk_virtual_size_bytes: int
    disk_chain_sha256: str
    process_record_id: UUID
    qmp_evidence_id: UUID
    lifecycle_evidence_id: UUID
    disk_observation_id: UUID
    idempotency_key: str
    planned_argv: tuple[str, ...]
    planned_command_sha256: str
    executed_argv: tuple[str, ...]
    executed_command_sha256: str
    executable: JournalExecutableIdentity
    qmp_socket: str
    pid_file: str
    serial_log_path: str
    qemu_log_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.launch_authority, DisposableLaunchReceipt):
            raise TypeError(
                "launch_authority must be a DisposableLaunchReceipt"
            )
        for field_name in (
            "operation_id",
            "vm_id",
            "boot_id",
            "disk_id",
            "process_record_id",
            "qmp_evidence_id",
            "lifecycle_evidence_id",
            "disk_observation_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _strict_uuid(getattr(self, field_name), field_name),
            )
        identities = (
            self.operation_id,
            self.launch_authority.permit_id,
            self.launch_authority.run_id,
            self.vm_id,
            self.boot_id,
            self.disk_id,
            self.process_record_id,
            self.qmp_evidence_id,
            self.lifecycle_evidence_id,
            self.disk_observation_id,
        )
        if len(set(identities)) != len(identities):
            raise RuntimeLaunchJournalError(
                "launch authority IDs cannot be reused across roles"
            )
        _strict_int(self.generation, "generation")
        object.__setattr__(
            self,
            "vm_name",
            _strict_name(self.vm_name, "VM name"),
        )
        _strict_int(
            self.memory_mib,
            "VM memory_mib",
            minimum=256,
            maximum=2**31 - 1,
        )
        _strict_int(
            self.vcpus,
            "VM vcpus",
            minimum=1,
            maximum=256,
        )
        if not isinstance(self.enable_kvm, bool):
            raise RuntimeLaunchJournalError("VM enable_kvm must be boolean")
        object.__setattr__(
            self,
            "disk_path",
            _strict_path(self.disk_path, "disk path"),
        )
        _strict_int(self.disk_device_id, "disk device_id", minimum=1)
        _strict_int(self.disk_inode, "disk inode", minimum=1)
        _strict_int(
            self.disk_virtual_size_bytes,
            "disk virtual_size_bytes",
            minimum=1,
        )
        _strict_hash(self.disk_chain_sha256, "disk chain_sha256")
        if (
            self.launch_authority.vm_id != self.vm_id
            or self.launch_authority.generation != self.generation
            or self.launch_authority.disk_id != self.disk_id
            or self.launch_authority.disk_path != self.disk_path
        ):
            raise RuntimeLaunchJournalError(
                "disposable launch authority disagrees with VM or disk identity"
            )
        if (
            not isinstance(self.idempotency_key, str)
            or not _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key)
        ):
            raise RuntimeLaunchJournalError("idempotency_key is invalid")
        planned = _strict_argv(self.planned_argv, "planned argv")
        executed = _strict_argv(self.executed_argv, "executed argv")
        object.__setattr__(self, "planned_argv", planned)
        object.__setattr__(self, "executed_argv", executed)
        planned_digest = _strict_hash(
            self.planned_command_sha256,
            "planned command_sha256",
        )
        executed_digest = _strict_hash(
            self.executed_command_sha256,
            "executed command_sha256",
        )
        if command_sha256(planned) != planned_digest:
            raise RuntimeLaunchJournalError(
                "planned command hash disagrees with planned argv"
            )
        if command_sha256(executed) != executed_digest:
            raise RuntimeLaunchJournalError(
                "executed command hash disagrees with executed argv"
            )
        try:
            validate_fd_bound_execution(planned, executed)
        except QemuPlanError as exc:
            raise RuntimeLaunchJournalError(
                "execution is not the exact fd-bound storage rewrite"
            ) from exc
        if not isinstance(self.executable, JournalExecutableIdentity):
            raise TypeError(
                "executable must be a JournalExecutableIdentity"
            )
        if executed[0] != self.executable.path:
            raise RuntimeLaunchJournalError(
                "executed argv[0] must be the pinned absolute executable"
            )
        if planned[0].startswith("/"):
            if planned[0] != executed[0]:
                raise RuntimeLaunchJournalError(
                    "an absolute plan command must preserve its executable path"
                )
        elif (
            "/" in planned[0]
            or not _TOKEN.fullmatch(planned[0])
            or planned == executed
        ):
            raise RuntimeLaunchJournalError(
                "a bare plan command must be resolved into a distinct absolute execution argv"
            )

        for field_name, label in (
            ("qmp_socket", "QMP socket"),
            ("pid_file", "pidfile"),
            ("serial_log_path", "serial log"),
            ("qemu_log_path", "QEMU log"),
        ):
            object.__setattr__(
                self,
                field_name,
                _strict_path(getattr(self, field_name), label),
            )
        paths = {
            self.disk_path,
            self.qmp_socket,
            self.pid_file,
            self.serial_log_path,
            self.qemu_log_path,
        }
        if len(paths) != 5:
            raise RuntimeLaunchJournalError(
                "disk, QMP, pidfile, and log paths must be distinct"
            )
        if (
            PurePosixPath(self.qmp_socket).parent
            != PurePosixPath(self.pid_file).parent
            or PurePosixPath(self.serial_log_path).parent
            != PurePosixPath(self.qemu_log_path).parent
        ):
            raise RuntimeLaunchJournalError(
                "runtime paths and log paths must each share one private owner root"
            )

        if _option_value(planned, "-uuid") != str(self.vm_id):
            raise RuntimeLaunchJournalError(
                "planned QEMU UUID disagrees with VM identity"
            )
        if _option_value(planned, "-name") != f"guest={self.vm_name}":
            raise RuntimeLaunchJournalError(
                "planned QEMU name disagrees with VM identity"
            )
        if _option_value(planned, "-m") != str(self.memory_mib):
            raise RuntimeLaunchJournalError(
                "planned QEMU memory disagrees with VM identity"
            )
        if _option_value(planned, "-smp") != (
            f"cpus={self.vcpus},sockets=1,dies=1,"
            f"cores={self.vcpus},threads=1"
        ):
            raise RuntimeLaunchJournalError(
                "planned QEMU topology disagrees with VM identity"
            )
        if self.enable_kvm:
            if planned.count("-enable-kvm") != 1:
                raise RuntimeLaunchJournalError(
                    "KVM VM intent lacks its planned accelerator"
                )
        elif "-enable-kvm" in planned:
            raise RuntimeLaunchJournalError(
                "TCG VM intent cannot contain a KVM accelerator"
            )
        if _option_value(planned, "-qmp") != (
            f"unix:{self.qmp_socket},server=on,wait=off"
        ):
            raise RuntimeLaunchJournalError(
                "planned QMP socket disagrees with journal resources"
            )
        if _option_value(planned, "-pidfile") != self.pid_file:
            raise RuntimeLaunchJournalError(
                "planned pidfile disagrees with journal resources"
            )
        if _option_value(planned, "-serial") != "stdio":
            raise RuntimeLaunchJournalError(
                "planned serial stream must remain supervised"
            )
        try:
            blockdev = json.loads(_option_value(planned, "-blockdev"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeLaunchJournalError(
                "planned blockdev is not valid JSON"
            ) from exc
        if blockdev != {
            "driver": "qcow2",
            "file": {"driver": "file", "filename": self.disk_path},
            "node-name": "somnus-disk",
        }:
            raise RuntimeLaunchJournalError(
                "planned blockdev disagrees with the owned P3 disk"
            )

        # Materialize both blobs here so invalid size or JSON cannot survive
        # object construction and fail only after an external mutation.
        self.intent_bytes()
        self.owned_resources_bytes()

    @classmethod
    def from_runtime(
        cls,
        record: VMRecord,
        disk: DiskRecord,
        plan: QemuLaunchPlan,
        executable: ExecutableIdentity,
        *,
        executed_argv: tuple[str, ...],
        launch_authority: DisposableLaunchReceipt,
        operation_id: UUID | None = None,
        boot_id: UUID | None = None,
        process_record_id: UUID | None = None,
        qmp_evidence_id: UUID | None = None,
        lifecycle_evidence_id: UUID | None = None,
        disk_observation_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> Self:
        """Freeze current typed owners and pre-generate all observation IDs."""

        if not isinstance(record, VMRecord):
            raise TypeError("record must be a VMRecord")
        if record.state not in {VMState.PROVISIONED, VMState.BOOTING}:
            raise RuntimeLaunchJournalError(
                "runtime launch requires a provisioned or booting VM record"
            )
        if not isinstance(disk, DiskRecord):
            raise TypeError("disk must be a DiskRecord")
        if (
            disk.vm_id != record.vm_id
            or disk.generation != record.generation
            or disk.path != record.definition.disk_path
            or not disk.active
            or not disk.materialized
            or disk.format != "qcow2"
            or disk.device_id is None
            or disk.inode is None
            or disk.virtual_size_bytes is None
            or disk.chain_sha256 is None
        ):
            raise RuntimeLaunchJournalError(
                "runtime launch requires the active materialized P3 qcow2 owner"
            )
        if not isinstance(plan, QemuLaunchPlan):
            raise TypeError("plan must be a QemuLaunchPlan")
        if not isinstance(launch_authority, DisposableLaunchReceipt):
            raise TypeError(
                "launch_authority must be a DisposableLaunchReceipt"
            )
        journal_executable = JournalExecutableIdentity.from_runtime(
            executable
        )
        selected_boot = (
            record.boot_id
            if boot_id is None and record.boot_id is not None
            else uuid4() if boot_id is None else _strict_uuid(boot_id, "boot_id")
        )
        if record.boot_id is not None and selected_boot != record.boot_id:
            raise RuntimeLaunchJournalError(
                "launch boot_id cannot replace the VM record boot identity"
            )
        selected_operation = (
            uuid4()
            if operation_id is None
            else _strict_uuid(operation_id, "operation_id")
        )
        selected_execution = executed_argv
        selected_key = (
            f"runtime.launch:{record.vm_id}:{record.generation}:{selected_boot}"
            if idempotency_key is None
            else idempotency_key
        )
        return cls(
            operation_id=selected_operation,
            launch_authority=launch_authority,
            vm_id=record.vm_id,
            generation=record.generation,
            vm_name=record.definition.name,
            memory_mib=record.definition.memory_mib,
            vcpus=record.definition.vcpus,
            enable_kvm=record.definition.enable_kvm,
            boot_id=selected_boot,
            disk_id=disk.disk_id,
            disk_path=disk.path,
            disk_device_id=disk.device_id,
            disk_inode=disk.inode,
            disk_virtual_size_bytes=disk.virtual_size_bytes,
            disk_chain_sha256=disk.chain_sha256,
            process_record_id=(
                uuid4()
                if process_record_id is None
                else _strict_uuid(process_record_id, "process_record_id")
            ),
            qmp_evidence_id=(
                uuid4()
                if qmp_evidence_id is None
                else _strict_uuid(qmp_evidence_id, "qmp_evidence_id")
            ),
            lifecycle_evidence_id=(
                uuid4()
                if lifecycle_evidence_id is None
                else _strict_uuid(
                    lifecycle_evidence_id,
                    "lifecycle_evidence_id",
                )
            ),
            disk_observation_id=(
                uuid4()
                if disk_observation_id is None
                else _strict_uuid(
                    disk_observation_id,
                    "disk_observation_id",
                )
            ),
            idempotency_key=selected_key,
            planned_argv=plan.argv,
            planned_command_sha256=plan.command_sha256,
            executed_argv=tuple(selected_execution),
            executed_command_sha256=command_sha256(
                tuple(selected_execution)
            ),
            executable=journal_executable,
            qmp_socket=os.fspath(plan.qmp_socket),
            pid_file=os.fspath(plan.pid_file),
            serial_log_path=os.fspath(plan.serial_log_path),
            qemu_log_path=os.fspath(plan.qemu_log_path),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": RUNTIME_LAUNCH_INTENT_SCHEMA,
            "kind": RUNTIME_LAUNCH_KIND,
            "operation_id": str(self.operation_id),
            "idempotency_key": self.idempotency_key,
            "launch_authority": self.launch_authority.to_dict(),
            "vm": {
                "vm_id": str(self.vm_id),
                "generation": self.generation,
                "name": self.vm_name,
                "memory_mib": self.memory_mib,
                "vcpus": self.vcpus,
                "enable_kvm": self.enable_kvm,
                "boot_id": str(self.boot_id),
            },
            "disk": {
                "disk_id": str(self.disk_id),
                "path": self.disk_path,
                "device_id": self.disk_device_id,
                "inode": self.disk_inode,
                "virtual_size_bytes": self.disk_virtual_size_bytes,
                "chain_sha256": self.disk_chain_sha256,
            },
            "ids": {
                "process_record_id": str(self.process_record_id),
                "qmp_evidence_id": str(self.qmp_evidence_id),
                "lifecycle_evidence_id": str(
                    self.lifecycle_evidence_id
                ),
                "disk_observation_id": str(self.disk_observation_id),
            },
            "plan": {
                "argv": list(self.planned_argv),
                "command_sha256": self.planned_command_sha256,
            },
            "execution": {
                "argv": list(self.executed_argv),
                "command_sha256": self.executed_command_sha256,
                "executable": self.executable.to_dict(),
            },
            "paths": {
                "qmp_socket": self.qmp_socket,
                "pid_file": self.pid_file,
                "serial_log_path": self.serial_log_path,
                "qemu_log_path": self.qemu_log_path,
            },
        }

    def owned_resources_dict(self) -> dict[str, object]:
        return {
            "schema": RUNTIME_LAUNCH_RESOURCES_SCHEMA,
            "kind": RUNTIME_LAUNCH_KIND,
            "operation_id": str(self.operation_id),
            "launch_authority": self.launch_authority.to_dict(),
            "vm_id": str(self.vm_id),
            "generation": self.generation,
            "boot_id": str(self.boot_id),
            "disk": {
                "disk_id": str(self.disk_id),
                "path": self.disk_path,
                "device_id": self.disk_device_id,
                "inode": self.disk_inode,
                "virtual_size_bytes": self.disk_virtual_size_bytes,
                "chain_sha256": self.disk_chain_sha256,
            },
            "process_record_id": str(self.process_record_id),
            "qmp_evidence_id": str(self.qmp_evidence_id),
            "lifecycle_evidence_id": str(self.lifecycle_evidence_id),
            "disk_observation_id": str(self.disk_observation_id),
            "paths": {
                "qmp_socket": self.qmp_socket,
                "pid_file": self.pid_file,
                "serial_log_path": self.serial_log_path,
                "qemu_log_path": self.qemu_log_path,
            },
        }

    def intent_bytes(self) -> bytes:
        return _encode_json(self.to_dict(), "runtime launch intent")

    def owned_resources_bytes(self) -> bytes:
        return _encode_json(
            self.owned_resources_dict(),
            "runtime launch owned resources",
        )

    @property
    def request_hash(self) -> str:
        return _sha256_bytes(self.intent_bytes())

    def begin_operation_kwargs(self) -> dict[str, object]:
        """Return the exact keyword contract for ``begin_operation``."""

        return {
            "operation_id": self.operation_id,
            "vm_id": self.vm_id,
            "kind": RUNTIME_LAUNCH_KIND,
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "intent": self.intent_bytes(),
            "owned_resources": self.owned_resources_bytes(),
            "status": "pending",
        }

    @classmethod
    def from_persisted(
        cls,
        intent: bytes,
        owned_resources: bytes,
    ) -> Self:
        table = _strict_object(
            _decode_json_object(intent, "runtime launch intent"),
            frozenset(
                {
                    "schema",
                    "kind",
                    "operation_id",
                    "idempotency_key",
                    "launch_authority",
                    "vm",
                    "disk",
                    "ids",
                    "plan",
                    "execution",
                    "paths",
                }
            ),
            "runtime launch intent",
        )
        if (
            table["schema"] != RUNTIME_LAUNCH_INTENT_SCHEMA
            or table["kind"] != RUNTIME_LAUNCH_KIND
        ):
            raise RuntimeLaunchJournalError(
                "runtime launch intent schema or kind is unsupported"
            )
        vm = _strict_object(
            table["vm"],
            frozenset(
                {
                    "vm_id",
                    "generation",
                    "name",
                    "memory_mib",
                    "vcpus",
                    "enable_kvm",
                    "boot_id",
                }
            ),
            "runtime launch VM identity",
        )
        disk = _strict_object(
            table["disk"],
            frozenset(
                {
                    "disk_id",
                    "path",
                    "device_id",
                    "inode",
                    "virtual_size_bytes",
                    "chain_sha256",
                }
            ),
            "runtime launch disk identity",
        )
        ids = _strict_object(
            table["ids"],
            frozenset(
                {
                    "process_record_id",
                    "qmp_evidence_id",
                    "lifecycle_evidence_id",
                    "disk_observation_id",
                }
            ),
            "runtime launch observation IDs",
        )
        plan = _strict_object(
            table["plan"],
            frozenset({"argv", "command_sha256"}),
            "runtime launch plan",
        )
        execution = _strict_object(
            table["execution"],
            frozenset({"argv", "command_sha256", "executable"}),
            "runtime launch execution",
        )
        paths = _strict_object(
            table["paths"],
            frozenset(
                {
                    "qmp_socket",
                    "pid_file",
                    "serial_log_path",
                    "qemu_log_path",
                }
            ),
            "runtime launch paths",
        )
        try:
            launch_authority = DisposableLaunchReceipt.from_dict(
                table["launch_authority"]
            )
        except LaunchAuthorityError as exc:
            raise RuntimeLaunchJournalError(
                "persisted disposable launch authority is invalid"
            ) from exc
        candidate = cls(
            operation_id=_strict_uuid(
                table["operation_id"],
                "operation_id",
            ),
            launch_authority=launch_authority,
            vm_id=_strict_uuid(vm["vm_id"], "vm_id"),
            generation=_strict_int(vm["generation"], "generation"),
            vm_name=_strict_name(vm["name"], "VM name"),
            memory_mib=_strict_int(
                vm["memory_mib"],
                "VM memory_mib",
                minimum=256,
                maximum=2**31 - 1,
            ),
            vcpus=_strict_int(
                vm["vcpus"],
                "VM vcpus",
                minimum=1,
                maximum=256,
            ),
            enable_kvm=_strict_bool(vm["enable_kvm"], "VM enable_kvm"),
            boot_id=_strict_uuid(vm["boot_id"], "boot_id"),
            disk_id=_strict_uuid(disk["disk_id"], "disk_id"),
            disk_path=_strict_path(disk["path"], "disk path"),
            disk_device_id=_strict_int(
                disk["device_id"],
                "disk device_id",
                minimum=1,
            ),
            disk_inode=_strict_int(
                disk["inode"],
                "disk inode",
                minimum=1,
            ),
            disk_virtual_size_bytes=_strict_int(
                disk["virtual_size_bytes"],
                "disk virtual_size_bytes",
                minimum=1,
            ),
            disk_chain_sha256=_strict_hash(
                disk["chain_sha256"],
                "disk chain_sha256",
            ),
            process_record_id=_strict_uuid(
                ids["process_record_id"],
                "process_record_id",
            ),
            qmp_evidence_id=_strict_uuid(
                ids["qmp_evidence_id"],
                "qmp_evidence_id",
            ),
            lifecycle_evidence_id=_strict_uuid(
                ids["lifecycle_evidence_id"],
                "lifecycle_evidence_id",
            ),
            disk_observation_id=_strict_uuid(
                ids["disk_observation_id"],
                "disk_observation_id",
            ),
            idempotency_key=(
                table["idempotency_key"]
                if isinstance(table["idempotency_key"], str)
                else ""
            ),
            planned_argv=_strict_argv(plan["argv"], "planned argv"),
            planned_command_sha256=_strict_hash(
                plan["command_sha256"],
                "planned command_sha256",
            ),
            executed_argv=_strict_argv(
                execution["argv"],
                "executed argv",
            ),
            executed_command_sha256=_strict_hash(
                execution["command_sha256"],
                "executed command_sha256",
            ),
            executable=JournalExecutableIdentity.from_dict(
                execution["executable"]
            ),
            qmp_socket=_strict_path(paths["qmp_socket"], "QMP socket"),
            pid_file=_strict_path(paths["pid_file"], "pidfile"),
            serial_log_path=_strict_path(
                paths["serial_log_path"],
                "serial log",
            ),
            qemu_log_path=_strict_path(
                paths["qemu_log_path"],
                "QEMU log",
            ),
        )
        if candidate.intent_bytes() != intent:
            raise RuntimeLaunchJournalError(
                "persisted runtime intent does not round-trip canonically"
            )
        decoded_resources = _decode_json_object(
            owned_resources,
            "runtime launch owned resources",
        )
        expected_resources = candidate.owned_resources_dict()
        if (
            decoded_resources != expected_resources
            or candidate.owned_resources_bytes() != owned_resources
        ):
            raise RuntimeLaunchJournalError(
                "owned resources disagree with runtime launch intent"
            )
        return candidate


@dataclass(frozen=True, slots=True)
class CheckpointWrite:
    """Exact arguments for ``RegistryTransaction.append_checkpoint``."""

    operation_id: UUID
    name: str
    ordinal: int
    payload: bytes

    def append_checkpoint_kwargs(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "name": self.name,
            "ordinal": self.ordinal,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class OperationStatusWrite:
    """Exact arguments for ``RegistryTransaction.set_operation_status``."""

    operation_id: UUID
    status: str
    result: bytes

    def set_operation_status_kwargs(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class JournalPidfileIdentity:
    """Replay-safe private pidfile identity, including its frozen intent path."""

    path: str
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
        object.__setattr__(
            self,
            "path",
            _strict_path(self.path, "pidfile path"),
        )
        _strict_int(
            self.pid,
            "pidfile PID",
            minimum=2,
            maximum=2**31 - 1,
        )
        _strict_int(self.device_id, "pidfile device_id", minimum=1)
        _strict_int(self.inode, "pidfile inode", minimum=1)
        _strict_int(
            self.owner_uid,
            "pidfile owner_uid",
            maximum=2**32 - 1,
        )
        mode = _strict_int(self.mode, "pidfile mode", maximum=0o7777)
        if mode & 0o077:
            raise RuntimeLaunchJournalError(
                "pidfile mode is not owner-private"
            )
        if self.link_count != 1:
            raise RuntimeLaunchJournalError(
                "pidfile link_count must be one"
            )
        _strict_int(
            self.size_bytes,
            "pidfile size_bytes",
            minimum=1,
            maximum=64,
        )
        _strict_int(self.mtime_ns, "pidfile mtime_ns")
        _strict_int(self.ctime_ns, "pidfile ctime_ns")


@dataclass(frozen=True, slots=True)
class JournalDirectoryIdentity:
    """Replay-safe private directory identity captured before child spawn."""

    path: str
    device_id: int
    inode: int
    owner_uid: int
    mode: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            _strict_path(self.path, "private directory path"),
        )
        _strict_int(self.device_id, "directory device_id", minimum=1)
        _strict_int(self.inode, "directory inode", minimum=1)
        _strict_int(
            self.owner_uid,
            "directory owner_uid",
            maximum=2**32 - 1,
        )
        _strict_int(
            self.mode,
            "directory mode",
            minimum=0o700,
            maximum=0o700,
        )


@dataclass(frozen=True, slots=True)
class JournalQMPSocketIdentity:
    """Replay-safe private QMP socket identity and frozen intent path."""

    path: str
    device_id: int
    inode: int
    uid: int
    mode: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            _strict_path(self.path, "QMP socket path"),
        )
        _strict_int(self.device_id, "QMP socket device_id", minimum=1)
        _strict_int(self.inode, "QMP socket inode", minimum=1)
        _strict_int(
            self.uid,
            "QMP socket uid",
            maximum=2**32 - 1,
        )
        if self.mode != 0o600:
            raise RuntimeLaunchJournalError(
                "QMP socket mode must be 0600"
            )


def _process_mapping(value: object, label: str) -> dict[str, object]:
    if isinstance(value, ObservedProcessIdentity):
        raw: object = value.to_dict()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise RuntimeLaunchJournalError(
            f"{label} must be an ObservedProcessIdentity or exact mapping"
        )
    table = _strict_object(raw, _PROCESS_FACT_KEYS, label)
    argv = _strict_argv(table["argv"], f"{label} argv")
    digest = _strict_hash(
        table["command_sha256"],
        f"{label} command_sha256",
    )
    if command_sha256(argv) != digest:
        raise RuntimeLaunchJournalError(
            f"{label} command hash disagrees with argv"
        )
    timestamp_value = table["observed_at"]
    if not isinstance(timestamp_value, str) or len(timestamp_value) > 64:
        raise RuntimeLaunchJournalError(
            f"{label} observed_at is invalid"
        )
    try:
        observed_at = datetime.fromisoformat(timestamp_value)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("timezone required")
        observed_at = observed_at.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeLaunchJournalError(
            f"{label} observed_at is invalid"
        ) from exc
    mode = _strict_int(
        table["executable_mode"],
        f"{label} executable_mode",
        maximum=0o7777,
    )
    if mode & 0o022 or mode & 0o111 == 0:
        raise RuntimeLaunchJournalError(
            f"{label} executable mode is unsafe"
        )
    if table["executable_link_count"] != 1:
        raise RuntimeLaunchJournalError(
            f"{label} executable_link_count must be one"
        )
    return {
        "pid": _strict_int(
            table["pid"],
            f"{label} pid",
            minimum=2,
            maximum=2**31 - 1,
        ),
        "start_time_ticks": _strict_int(
            table["start_time_ticks"],
            f"{label} start_time_ticks",
            minimum=1,
        ),
        "proc_device_id": _strict_int(
            table["proc_device_id"],
            f"{label} proc_device_id",
            minimum=1,
        ),
        "proc_inode": _strict_int(
            table["proc_inode"],
            f"{label} proc_inode",
            minimum=1,
        ),
        "process_owner_uid": _strict_int(
            table["process_owner_uid"],
            f"{label} process_owner_uid",
            maximum=2**32 - 1,
        ),
        "executable": _strict_path(
            table["executable"],
            f"{label} executable",
        ),
        "executable_device_id": _strict_int(
            table["executable_device_id"],
            f"{label} executable_device_id",
            minimum=1,
        ),
        "executable_inode": _strict_int(
            table["executable_inode"],
            f"{label} executable_inode",
            minimum=1,
        ),
        "executable_owner_uid": _strict_int(
            table["executable_owner_uid"],
            f"{label} executable_owner_uid",
            maximum=2**32 - 1,
        ),
        "executable_mode": mode,
        "executable_link_count": 1,
        "executable_size_bytes": _strict_int(
            table["executable_size_bytes"],
            f"{label} executable_size_bytes",
            minimum=1,
        ),
        "executable_mtime_ns": _strict_int(
            table["executable_mtime_ns"],
            f"{label} executable_mtime_ns",
        ),
        "executable_ctime_ns": _strict_int(
            table["executable_ctime_ns"],
            f"{label} executable_ctime_ns",
        ),
        "executable_sha256": _strict_hash(
            table["executable_sha256"],
            f"{label} executable_sha256",
        ),
        "argv": list(argv),
        "command_sha256": digest,
        "process_group_id": _strict_int(
            table["process_group_id"],
            f"{label} process_group_id",
            minimum=1,
            maximum=2**31 - 1,
        ),
        "session_id": _strict_int(
            table["session_id"],
            f"{label} session_id",
            minimum=1,
            maximum=2**31 - 1,
        ),
        "observed_at": observed_at.isoformat(),
    }


def _same_process(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return (
        left["pid"] == right["pid"]
        and left["start_time_ticks"] == right["start_time_ticks"]
    )


def _target_matches_intent(
    process: Mapping[str, object],
    intent: RuntimeLaunchIntent,
) -> None:
    executable = intent.executable
    comparisons = {
        "executable": executable.path,
        "executable_device_id": executable.device_id,
        "executable_inode": executable.inode,
        "executable_owner_uid": executable.owner_uid,
        "executable_mode": executable.mode,
        "executable_link_count": executable.link_count,
        "executable_size_bytes": executable.size_bytes,
        "executable_mtime_ns": executable.mtime_ns,
        "executable_ctime_ns": executable.ctime_ns,
        "executable_sha256": executable.sha256,
        "argv": list(intent.executed_argv),
        "command_sha256": intent.executed_command_sha256,
    }
    if any(process[key] != expected for key, expected in comparisons.items()):
        raise RuntimeLaunchJournalError(
            "observed QEMU process disagrees with pinned execution intent"
        )


def _bounded_text(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 4_096,
) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or _contains_forbidden_text(value)
    ):
        raise RuntimeLaunchJournalError(f"{label} is invalid")
    return value


def _qmp_domain_sha256(domain: bytes, value: object, label: str) -> str:
    try:
        encoded = canonical_json(
            value,
            label,
            max_depth=16,
            max_items=8_192,
            max_string=4_096,
            max_bytes=1024 * 1024,
        ).encode("utf-8")
    except (ProtocolValidationError, TypeError, ValueError) as exc:
        raise RuntimeLaunchJournalError(
            f"{label} cannot be encoded canonically"
        ) from exc
    return hashlib.sha256(domain + b"\0" + encoded).hexdigest()


def _normalize_qmp_identity_evidence(
    value: object,
    digest: object,
    intent: RuntimeLaunchIntent,
    prior: Mapping[str, dict[str, object]],
) -> tuple[dict[str, object], str]:
    """Validate Terra's full host-owned QMP evidence without fresh host I/O."""

    if isinstance(value, QMPIdentityEvidence):
        evidence = value.to_dict()
        supplied_digest = value.evidence_sha256
        if digest != supplied_digest:
            raise RuntimeLaunchJournalError(
                "QMP evidence digest disagrees with the typed evidence"
            )
    elif isinstance(value, Mapping):
        evidence = dict(value)
        supplied_digest = _strict_hash(
            digest,
            "QMP identity evidence SHA256",
        )
    else:
        raise RuntimeLaunchJournalError(
            "QMP identity evidence must be typed evidence or its exact mapping"
        )
    table = _strict_object(
        evidence,
        frozenset(
            {
                "first",
                "observation_id",
                "schema",
                "second",
                "snapshot",
                "stabilization_ns",
                "vm_id",
            }
        ),
        "QMP identity evidence",
    )
    if table["schema"] != QMP_IDENTITY_EVIDENCE_SCHEMA:
        raise RuntimeLaunchJournalError(
            "QMP identity evidence schema is unsupported"
        )
    observation_id = _strict_uuid(
        table["observation_id"],
        "QMP observation_id",
    )
    vm_id = _strict_uuid(table["vm_id"], "QMP evidence vm_id")
    if (
        observation_id != intent.qmp_evidence_id
        or vm_id != intent.vm_id
    ):
        raise RuntimeLaunchJournalError(
            "QMP evidence is not keyed by the pre-generated launch identities"
        )

    snapshot = _strict_object(
        table["snapshot"],
        frozenset(
            {
                "block",
                "cpus",
                "greeting_sha256",
                "name",
                "peer_pid",
                "qemu_package",
                "qemu_version",
                "qmp_capabilities",
                "schema",
                "status",
                "vm_id",
            }
        ),
        "QMP identity snapshot",
    )
    if snapshot["schema"] != QMP_IDENTITY_SCHEMA:
        raise RuntimeLaunchJournalError(
            "QMP identity snapshot schema is unsupported"
        )
    snapshot_vm = _strict_uuid(snapshot["vm_id"], "QMP snapshot vm_id")
    snapshot_name = _strict_name(snapshot["name"], "QMP snapshot name")
    peer_pid = _strict_int(
        snapshot["peer_pid"],
        "QMP snapshot peer_pid",
        minimum=2,
        maximum=2**31 - 1,
    )
    process = prior["process_observed"]["process"]
    assert isinstance(process, dict)
    greeting = prior["qmp_greeting_observed"]
    if (
        snapshot_vm != intent.vm_id
        or snapshot_name != intent.vm_name
        or peer_pid != process["pid"]
        or snapshot["greeting_sha256"] != greeting["greeting_sha256"]
    ):
        raise RuntimeLaunchJournalError(
            "QMP snapshot identity disagrees with process and greeting evidence"
        )
    greeting_digest = _strict_hash(
        snapshot["greeting_sha256"],
        "QMP snapshot greeting SHA256",
    )

    version_value = snapshot["qemu_version"]
    if (
        not isinstance(version_value, list)
        or len(version_value) != 3
    ):
        raise RuntimeLaunchJournalError(
            "QMP snapshot version is invalid"
        )
    version = [
        _strict_int(
            item,
            "QMP version component",
            maximum=2**31 - 1,
        )
        for item in version_value
    ]
    if version[:2] != [8, 2]:
        raise RuntimeLaunchJournalError(
            "QMP snapshot is outside the QEMU 8.2.x profile"
        )
    qemu_package = _bounded_text(
        snapshot["qemu_package"],
        "QMP package",
        maximum=4_096,
    )

    capabilities_value = snapshot["qmp_capabilities"]
    if not isinstance(capabilities_value, list):
        raise RuntimeLaunchJournalError(
            "QMP capability set must be a list"
        )
    capabilities = [
        _bounded_text(
            item,
            "QMP capability",
            minimum=1,
            maximum=128,
        )
        for item in capabilities_value
    ]
    if (
        capabilities != sorted(capabilities)
        or len(capabilities) != len(set(capabilities))
    ):
        raise RuntimeLaunchJournalError(
            "QMP capabilities are not a canonical semantic set"
        )
    capability_checkpoint = prior["qmp_capabilities_negotiated"]
    if capability_checkpoint["capabilities_sha256"] != (
        canonical_evidence_sha256(capabilities)
    ):
        raise RuntimeLaunchJournalError(
            "QMP capability checkpoint disagrees with identity evidence"
        )

    status = _strict_object(
        snapshot["status"],
        frozenset({"running", "status"}),
        "QMP status identity",
    )
    if status != {"running": True, "status": "running"}:
        raise RuntimeLaunchJournalError(
            "QMP identity evidence is not coherently running"
        )

    block = _strict_object(
        snapshot["block"],
        frozenset(
            {
                "device",
                "layers",
                "node_name",
                "qdev_id",
                "storage_chain_sha256",
            }
        ),
        "QMP block identity",
    )
    device = _bounded_text(
        block["device"],
        "QMP block device",
        maximum=256,
    )
    if (
        block["node_name"] != "somnus-disk"
        or block["qdev_id"] != "somnus-root-disk"
        or block["storage_chain_sha256"] != intent.disk_chain_sha256
    ):
        raise RuntimeLaunchJournalError(
            "QMP block owner disagrees with planned P3 storage"
        )
    storage_chain_sha256 = _strict_hash(
        block["storage_chain_sha256"],
        "QMP storage chain SHA256",
    )
    layers_value = block["layers"]
    if not isinstance(layers_value, list) or not 1 <= len(layers_value) <= 64:
        raise RuntimeLaunchJournalError("QMP block layers are invalid")
    layers: list[dict[str, object]] = []
    paths: set[str] = set()
    for raw_layer in layers_value:
        layer = _strict_object(
            raw_layer,
            frozenset(
                {
                    "device_id",
                    "format",
                    "inode",
                    "path",
                    "virtual_size_bytes",
                }
            ),
            "QMP block layer",
        )
        normalized_layer = {
            "device_id": _strict_int(
                layer["device_id"],
                "QMP block device_id",
                minimum=1,
            ),
            "format": _bounded_text(
                layer["format"],
                "QMP block format",
                minimum=1,
                maximum=32,
            ),
            "inode": _strict_int(
                layer["inode"],
                "QMP block inode",
                minimum=1,
            ),
            "path": _strict_path(
                layer["path"],
                "QMP block path",
            ),
            "virtual_size_bytes": _strict_int(
                layer["virtual_size_bytes"],
                "QMP block virtual_size_bytes",
                minimum=1,
            ),
        }
        if normalized_layer["format"] not in {"qcow2", "raw"}:
            raise RuntimeLaunchJournalError(
                "QMP block format is unsupported"
            )
        if normalized_layer["path"] in paths:
            raise RuntimeLaunchJournalError(
                "QMP block evidence reuses one layer path"
            )
        paths.add(str(normalized_layer["path"]))
        layers.append(normalized_layer)
    root_layer = layers[0]
    if root_layer != {
        "device_id": intent.disk_device_id,
        "format": "qcow2",
        "inode": intent.disk_inode,
        "path": intent.disk_path,
        "virtual_size_bytes": intent.disk_virtual_size_bytes,
    }:
        raise RuntimeLaunchJournalError(
            "QMP root layer disagrees with the owned runtime disk"
        )

    cpus_value = snapshot["cpus"]
    if (
        not isinstance(cpus_value, list)
        or len(cpus_value) != intent.vcpus
    ):
        raise RuntimeLaunchJournalError("QMP CPU identity set is invalid")
    cpus: list[dict[str, object]] = []
    process_threads: set[int] = set()
    qom_paths: set[str] = set()
    for expected_index, raw_cpu in enumerate(cpus_value):
        cpu = _strict_object(
            raw_cpu,
            frozenset(
                {
                    "core_id",
                    "cpu_index",
                    "die_id",
                    "node_id",
                    "process_thread_id",
                    "qom_path",
                    "socket_id",
                    "target",
                    "thread_id",
                }
            ),
            "QMP CPU identity",
        )
        cpu_index = _strict_int(
            cpu["cpu_index"],
            "QMP CPU index",
            maximum=255,
        )
        process_thread = _strict_int(
            cpu["process_thread_id"],
            "QMP CPU process thread ID",
            minimum=1,
            maximum=2**31 - 1,
        )
        qom_path = _bounded_text(
            cpu["qom_path"],
            "QMP CPU QOM path",
            minimum=1,
            maximum=1_024,
        )
        die_id = cpu["die_id"]
        node_id = cpu["node_id"]
        for optional, label in (
            (die_id, "QMP CPU die ID"),
            (node_id, "QMP CPU node ID"),
        ):
            if optional is not None:
                _strict_int(optional, label)
        normalized_cpu = {
            "core_id": _strict_int(cpu["core_id"], "QMP CPU core ID"),
            "cpu_index": cpu_index,
            "die_id": die_id,
            "node_id": node_id,
            "process_thread_id": process_thread,
            "qom_path": qom_path,
            "socket_id": _strict_int(
                cpu["socket_id"],
                "QMP CPU socket ID",
            ),
            "target": _bounded_text(
                cpu["target"],
                "QMP CPU target",
                minimum=1,
                maximum=32,
            ),
            "thread_id": _strict_int(
                cpu["thread_id"],
                "QMP CPU topology thread ID",
            ),
        }
        if (
            cpu_index != expected_index
            or normalized_cpu["target"] != "x86_64"
            or process_thread in process_threads
            or qom_path in qom_paths
        ):
            raise RuntimeLaunchJournalError(
                "QMP CPU identity set is not canonical"
            )
        process_threads.add(process_thread)
        qom_paths.add(qom_path)
        cpus.append(normalized_cpu)

    normalized_snapshot = {
        "block": {
            "device": device,
            "layers": layers,
            "node_name": "somnus-disk",
            "qdev_id": "somnus-root-disk",
            "storage_chain_sha256": storage_chain_sha256,
        },
        "cpus": cpus,
        "greeting_sha256": greeting_digest,
        "name": snapshot_name,
        "peer_pid": peer_pid,
        "qemu_package": qemu_package,
        "qemu_version": version,
        "qmp_capabilities": capabilities,
        "schema": QMP_IDENTITY_SCHEMA,
        "status": {"running": True, "status": "running"},
        "vm_id": str(snapshot_vm),
    }
    snapshot_sha256 = _qmp_domain_sha256(
        b"somnus.qmp-identity.v1",
        normalized_snapshot,
        "QMP identity snapshot",
    )

    samples: list[dict[str, object]] = []
    for label, raw_sample in (
        ("first", table["first"]),
        ("second", table["second"]),
    ):
        sample = _strict_object(
            raw_sample,
            frozenset(
                {
                    "command_ids",
                    "monotonic_ns",
                    "snapshot_sha256",
                }
            ),
            f"QMP identity {label} sample",
        )
        ids_value = sample["command_ids"]
        if not isinstance(ids_value, list) or len(ids_value) != 5:
            raise RuntimeLaunchJournalError(
                f"QMP identity {label} command IDs are invalid"
            )
        command_ids = [
            _strict_int(
                item,
                f"QMP identity {label} command ID",
                minimum=1,
            )
            for item in ids_value
        ]
        if command_ids != sorted(command_ids) or len(set(command_ids)) != 5:
            raise RuntimeLaunchJournalError(
                f"QMP identity {label} command IDs are not unique and ordered"
            )
        sample_digest = _strict_hash(
            sample["snapshot_sha256"],
            f"QMP identity {label} snapshot SHA256",
        )
        if sample_digest != snapshot_sha256:
            raise RuntimeLaunchJournalError(
                "QMP identity sample snapshot digest disagrees"
            )
        samples.append(
            {
                "command_ids": command_ids,
                "monotonic_ns": _strict_int(
                    sample["monotonic_ns"],
                    f"QMP identity {label} monotonic_ns",
                    minimum=1,
                ),
                "snapshot_sha256": sample_digest,
            }
        )
    first, second = samples
    stabilization_ns = _strict_int(
        table["stabilization_ns"],
        "QMP stabilization_ns",
        minimum=MIN_QMP_STABILIZATION_NS,
    )
    if (
        second["monotonic_ns"] - first["monotonic_ns"]
        != stabilization_ns
        or min(second["command_ids"]) <= max(first["command_ids"])
    ):
        raise RuntimeLaunchJournalError(
            "QMP identity samples do not prove stable ordered observations"
        )
    normalized_evidence = {
        "first": first,
        "observation_id": str(observation_id),
        "schema": QMP_IDENTITY_EVIDENCE_SCHEMA,
        "second": second,
        "snapshot": normalized_snapshot,
        "stabilization_ns": stabilization_ns,
        "vm_id": str(vm_id),
    }
    expected_digest = _qmp_domain_sha256(
        b"somnus.qmp-identity-evidence.v1",
        normalized_evidence,
        "QMP identity evidence",
    )
    if supplied_digest != expected_digest:
        raise RuntimeLaunchJournalError(
            "QMP identity evidence digest disagrees with its full payload"
        )
    if normalized_evidence != evidence:
        raise RuntimeLaunchJournalError(
            "QMP identity evidence payload is not canonical"
        )
    return normalized_evidence, supplied_digest


def _checkpoint_facts(
    intent: RuntimeLaunchIntent,
    name: str,
    facts: object,
    prior: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    """Normalize one exact fact schema and bind it to all earlier evidence."""

    raw = dict(facts) if isinstance(facts, Mapping) else facts
    if name == "private_paths_prepared":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "owner_uid",
                    "runtime_root_device_id",
                    "runtime_root_inode",
                    "runtime_root_mode",
                    "log_root_device_id",
                    "log_root_inode",
                    "log_root_mode",
                }
            ),
            name,
        )
        runtime_identity = (
            _strict_int(
                table["runtime_root_device_id"],
                "runtime root device_id",
                minimum=1,
            ),
            _strict_int(
                table["runtime_root_inode"],
                "runtime root inode",
                minimum=1,
            ),
        )
        log_identity = (
            _strict_int(
                table["log_root_device_id"],
                "log root device_id",
                minimum=1,
            ),
            _strict_int(
                table["log_root_inode"],
                "log root inode",
                minimum=1,
            ),
        )
        if runtime_identity == log_identity:
            raise RuntimeLaunchJournalError(
                "runtime and log roots cannot physically alias"
            )
        return {
            "owner_uid": _strict_int(
                table["owner_uid"],
                "owner_uid",
                maximum=2**32 - 1,
            ),
            "runtime_root_device_id": runtime_identity[0],
            "runtime_root_inode": runtime_identity[1],
            "runtime_root_mode": _strict_int(
                table["runtime_root_mode"],
                "runtime root mode",
                minimum=0o700,
                maximum=0o700,
            ),
            "log_root_device_id": log_identity[0],
            "log_root_inode": log_identity[1],
            "log_root_mode": _strict_int(
                table["log_root_mode"],
                "log root mode",
                minimum=0o700,
                maximum=0o700,
            ),
        }
    if name == "executable_pinned":
        table = _strict_object(
            raw,
            frozenset({"executable", "storage"}),
            name,
        )
        executable = JournalExecutableIdentity.from_dict(
            table["executable"]
        )
        if executable != intent.executable:
            raise RuntimeLaunchJournalError(
                "pinned executable checkpoint disagrees with launch intent"
            )
        storage_table = _strict_object(
            table["storage"],
            frozenset({"overlay", "base", "owner_marker"}),
            "pinned storage",
        )
        normalized_storage: dict[str, dict[str, object]] = {}
        descriptors: set[int] = set()
        identities: set[tuple[int, int]] = set()
        specifications = (
            ("overlay", "overlay", "rw", 0o600, False),
            ("base", "base", "r", 0o444, True),
            ("owner_marker", "owner-marker", "r", 0o600, True),
        )
        for key, role, access_mode, mode, immutable in specifications:
            descriptor = _strict_object(
                storage_table[key],
                frozenset(
                    {
                        "role",
                        "descriptor",
                        "device_id",
                        "inode",
                        "owner_uid",
                        "owner_gid",
                        "mode",
                        "link_count",
                        "size_bytes",
                        "allocated_size_bytes",
                        "mtime_ns",
                        "ctime_ns",
                        "access_mode",
                        "content_sha256",
                    }
                ),
                f"pinned {role}",
            )
            if (
                descriptor["role"] != role
                or descriptor["access_mode"] != access_mode
            ):
                raise RuntimeLaunchJournalError(
                    f"pinned {role} role or access mode is invalid"
                )
            descriptor_number = _strict_int(
                descriptor["descriptor"],
                f"pinned {role} descriptor",
                minimum=3,
                maximum=2**31 - 1,
            )
            device_id = _strict_int(
                descriptor["device_id"],
                f"pinned {role} device_id",
                minimum=1,
            )
            inode = _strict_int(
                descriptor["inode"],
                f"pinned {role} inode",
                minimum=1,
            )
            content_sha256: str | None
            if immutable:
                content_sha256 = _strict_hash(
                    descriptor["content_sha256"],
                    f"pinned {role} content_sha256",
                )
            elif descriptor["content_sha256"] is not None:
                raise RuntimeLaunchJournalError(
                    "pinned overlay cannot claim immutable content"
                )
            else:
                content_sha256 = None
            normalized = {
                "role": role,
                "descriptor": descriptor_number,
                "device_id": device_id,
                "inode": inode,
                "owner_uid": _strict_int(
                    descriptor["owner_uid"],
                    f"pinned {role} owner_uid",
                    maximum=2**32 - 1,
                ),
                "owner_gid": _strict_int(
                    descriptor["owner_gid"],
                    f"pinned {role} owner_gid",
                    maximum=2**32 - 1,
                ),
                "mode": _strict_int(
                    descriptor["mode"],
                    f"pinned {role} mode",
                    minimum=mode,
                    maximum=mode,
                ),
                "link_count": _strict_int(
                    descriptor["link_count"],
                    f"pinned {role} link_count",
                    minimum=1,
                    maximum=1,
                ),
                "size_bytes": _strict_int(
                    descriptor["size_bytes"],
                    f"pinned {role} size_bytes",
                    minimum=1,
                ),
                "allocated_size_bytes": _strict_int(
                    descriptor["allocated_size_bytes"],
                    f"pinned {role} allocated_size_bytes",
                ),
                "mtime_ns": _strict_int(
                    descriptor["mtime_ns"],
                    f"pinned {role} mtime_ns",
                ),
                "ctime_ns": _strict_int(
                    descriptor["ctime_ns"],
                    f"pinned {role} ctime_ns",
                ),
                "access_mode": access_mode,
                "content_sha256": content_sha256,
            }
            if descriptor_number in descriptors:
                raise RuntimeLaunchJournalError(
                    "pinned storage descriptors cannot alias"
                )
            identity = (device_id, inode)
            if identity in identities:
                raise RuntimeLaunchJournalError(
                    "pinned storage physical identities cannot alias"
                )
            descriptors.add(descriptor_number)
            identities.add(identity)
            normalized_storage[key] = normalized
        if (
            normalized_storage["overlay"]["device_id"]
            != intent.disk_device_id
            or normalized_storage["overlay"]["inode"] != intent.disk_inode
        ):
            raise RuntimeLaunchJournalError(
                "pinned overlay disagrees with launch intent"
            )
        return {
            "executable": executable.to_dict(),
            "storage": normalized_storage,
        }
    if name == "log_guard_spawned":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "process",
                    "serial_pipe_device_id",
                    "serial_pipe_inode",
                    "qemu_pipe_device_id",
                    "qemu_pipe_inode",
                }
            ),
            name,
        )
        process = _process_mapping(table["process"], f"{name} process")
        serial_pipe = (
            _strict_int(
                table["serial_pipe_device_id"],
                "serial pipe device_id",
                minimum=1,
            ),
            _strict_int(
                table["serial_pipe_inode"],
                "serial pipe inode",
                minimum=1,
            ),
        )
        qemu_pipe = (
            _strict_int(
                table["qemu_pipe_device_id"],
                "QEMU pipe device_id",
                minimum=1,
            ),
            _strict_int(
                table["qemu_pipe_inode"],
                "QEMU pipe inode",
                minimum=1,
            ),
        )
        if serial_pipe == qemu_pipe:
            raise RuntimeLaunchJournalError(
                "serial and QEMU log pipes must have distinct identities"
            )
        return {
            "process": process,
            "serial_pipe_device_id": serial_pipe[0],
            "serial_pipe_inode": serial_pipe[1],
            "qemu_pipe_device_id": qemu_pipe[0],
            "qemu_pipe_inode": qemu_pipe[1],
        }
    if name == "qemu_guard_spawned":
        table = _strict_object(raw, frozenset({"process"}), name)
        process = _process_mapping(table["process"], f"{name} process")
        log_guard = prior["log_guard_spawned"]["process"]
        assert isinstance(log_guard, dict)
        if process["pid"] == log_guard["pid"]:
            raise RuntimeLaunchJournalError(
                "log guardian and QEMU guard cannot reuse one PID"
            )
        return {"process": process}
    if name == "log_guard_released":
        table = _strict_object(
            raw,
            frozenset({"pid", "start_time_ticks"}),
            name,
        )
        guard = prior["log_guard_spawned"]["process"]
        assert isinstance(guard, dict)
        normalized = {
            "pid": _strict_int(
                table["pid"],
                "log guardian pid",
                minimum=2,
                maximum=2**31 - 1,
            ),
            "start_time_ticks": _strict_int(
                table["start_time_ticks"],
                "log guardian start_time_ticks",
                minimum=1,
            ),
        }
        if not _same_process(normalized, guard):
            raise RuntimeLaunchJournalError(
                "released log guardian is not the durably observed guardian"
            )
        return normalized
    if name == "qemu_target_released":
        table = _strict_object(
            raw,
            frozenset({"pid", "start_time_ticks"}),
            name,
        )
        guard = prior["qemu_guard_spawned"]["process"]
        assert isinstance(guard, dict)
        normalized = {
            "pid": _strict_int(
                table["pid"],
                "QEMU guard pid",
                minimum=2,
                maximum=2**31 - 1,
            ),
            "start_time_ticks": _strict_int(
                table["start_time_ticks"],
                "QEMU guard start_time_ticks",
                minimum=1,
            ),
        }
        if not _same_process(normalized, guard):
            raise RuntimeLaunchJournalError(
                "released QEMU target is not the durably observed exec guard"
            )
        return normalized
    if name == "process_observed":
        table = _strict_object(raw, frozenset({"process"}), name)
        process = _process_mapping(table["process"], "QEMU process")
        guard = prior["qemu_guard_spawned"]["process"]
        assert isinstance(guard, dict)
        if not _same_process(process, guard):
            raise RuntimeLaunchJournalError(
                "QEMU exec changed PID or process start time"
            )
        _target_matches_intent(process, intent)
        return {"process": process}
    if name == "pidfile_verified":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "pidfile_pid",
                    "device_id",
                    "inode",
                    "owner_uid",
                    "mode",
                    "link_count",
                    "size_bytes",
                    "mtime_ns",
                    "ctime_ns",
                }
            ),
            name,
        )
        process = prior["process_observed"]["process"]
        assert isinstance(process, dict)
        identity = JournalPidfileIdentity(
            path=intent.pid_file,
            pid=_strict_int(
                table["pidfile_pid"],
                "pidfile PID",
                minimum=2,
                maximum=2**31 - 1,
            ),
            device_id=_strict_int(
                table["device_id"],
                "pidfile device_id",
                minimum=1,
            ),
            inode=_strict_int(
                table["inode"],
                "pidfile inode",
                minimum=1,
            ),
            owner_uid=_strict_int(
                table["owner_uid"],
                "pidfile owner_uid",
                maximum=2**32 - 1,
            ),
            mode=_strict_int(
                table["mode"],
                "pidfile mode",
                maximum=0o7777,
            ),
            link_count=_strict_int(
                table["link_count"],
                "pidfile link_count",
                minimum=1,
                maximum=1,
            ),
            size_bytes=_strict_int(
                table["size_bytes"],
                "pidfile size_bytes",
                minimum=1,
                maximum=64,
            ),
            mtime_ns=_strict_int(
                table["mtime_ns"],
                "pidfile mtime_ns",
            ),
            ctime_ns=_strict_int(
                table["ctime_ns"],
                "pidfile ctime_ns",
            ),
        )
        if identity.pid != process["pid"]:
            raise RuntimeLaunchJournalError(
                "QEMU pidfile PID disagrees with observed process identity"
            )
        return {
            "pidfile_pid": identity.pid,
            "device_id": identity.device_id,
            "inode": identity.inode,
            "owner_uid": identity.owner_uid,
            "mode": identity.mode,
            "link_count": identity.link_count,
            "size_bytes": identity.size_bytes,
            "mtime_ns": identity.mtime_ns,
            "ctime_ns": identity.ctime_ns,
        }
    if name == "qmp_greeting_observed":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "qmp_evidence_id",
                    "peer_pid",
                    "peer_uid",
                    "greeting_sha256",
                    "qmp_socket_device_id",
                    "qmp_socket_inode",
                    "qmp_socket_mode",
                }
            ),
            name,
        )
        process = prior["process_observed"]["process"]
        assert isinstance(process, dict)
        normalized = {
            "qmp_evidence_id": str(
                _strict_uuid(
                    table["qmp_evidence_id"],
                    "qmp_evidence_id",
                )
            ),
            "peer_pid": _strict_int(
                table["peer_pid"],
                "QMP peer PID",
                minimum=2,
                maximum=2**31 - 1,
            ),
            "peer_uid": _strict_int(
                table["peer_uid"],
                "QMP peer UID",
                maximum=2**32 - 1,
            ),
            "greeting_sha256": _strict_hash(
                table["greeting_sha256"],
                "QMP greeting SHA256",
            ),
            "qmp_socket_device_id": _strict_int(
                table["qmp_socket_device_id"],
                "QMP socket device_id",
                minimum=1,
            ),
            "qmp_socket_inode": _strict_int(
                table["qmp_socket_inode"],
                "QMP socket inode",
                minimum=1,
            ),
            "qmp_socket_mode": _strict_int(
                table["qmp_socket_mode"],
                "QMP socket mode",
                minimum=0o600,
                maximum=0o600,
            ),
        }
        if (
            normalized["qmp_evidence_id"] != str(intent.qmp_evidence_id)
            or normalized["peer_pid"] != process["pid"]
            or normalized["peer_uid"] != process["process_owner_uid"]
            or normalized["qmp_socket_mode"] != 0o600
        ):
            raise RuntimeLaunchJournalError(
                "QMP greeting is not bound to the intended process"
            )
        return normalized
    if name == "qmp_capabilities_negotiated":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "qmp_evidence_id",
                    "peer_pid",
                    "capabilities_sha256",
                }
            ),
            name,
        )
        greeting = prior["qmp_greeting_observed"]
        normalized = {
            "qmp_evidence_id": str(
                _strict_uuid(
                    table["qmp_evidence_id"],
                    "qmp_evidence_id",
                )
            ),
            "peer_pid": _strict_int(
                table["peer_pid"],
                "QMP peer PID",
                minimum=2,
                maximum=2**31 - 1,
            ),
            "capabilities_sha256": _strict_hash(
                table["capabilities_sha256"],
                "QMP capabilities SHA256",
            ),
        }
        if (
            normalized["qmp_evidence_id"]
            != greeting["qmp_evidence_id"]
            or normalized["peer_pid"] != greeting["peer_pid"]
        ):
            raise RuntimeLaunchJournalError(
                "QMP capability negotiation changed peer or evidence identity"
            )
        return normalized
    if name == "qmp_identity_verified":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "evidence",
                    "evidence_sha256",
                    "storage_graph",
                    "storage_graph_sha256",
                }
            ),
            name,
        )
        evidence, evidence_sha256 = _normalize_qmp_identity_evidence(
            table["evidence"],
            table["evidence_sha256"],
            intent,
            prior,
        )
        storage_graph = _decode_json_object(
            _encode_json(table["storage_graph"], "QMP storage graph"),
            "QMP storage graph",
        )
        graph_table = _strict_object(
            storage_graph,
            frozenset(
                {
                    "schema",
                    "peer_pid",
                    "fdsets",
                    "named_nodes",
                    "blockstats",
                    "peer_descriptors",
                }
            ),
            "QMP storage graph",
        )
        process = prior["process_observed"]["process"]
        assert isinstance(process, dict)
        if (
            graph_table["schema"]
            != "somnus.qmp-storage-graph-evidence.v1"
            or graph_table["peer_pid"] != process["pid"]
            or not isinstance(graph_table["fdsets"], list)
            or len(graph_table["fdsets"]) != 2
            or not isinstance(graph_table["named_nodes"], list)
            or len(graph_table["named_nodes"]) != 4
            or not isinstance(graph_table["blockstats"], list)
            or len(graph_table["blockstats"]) != 4
            or not isinstance(graph_table["peer_descriptors"], list)
            or len(graph_table["peer_descriptors"]) != 2
        ):
            raise RuntimeLaunchJournalError(
                "QMP storage graph aggregate is not canonical"
            )
        expected_fdsets = (
            (1, "somnus-overlay-rw"),
            (2, "somnus-base-ro"),
        )
        qmp_fds: list[int] = []
        for index, expected in enumerate(expected_fdsets):
            fdset = _strict_object(
                graph_table["fdsets"][index],
                frozenset({"fd", "fdset_id", "opaque"}),
                f"QMP storage fdset[{index}]",
            )
            if (fdset["fdset_id"], fdset["opaque"]) != expected:
                raise RuntimeLaunchJournalError(
                    "QMP storage fdset roles are not canonical"
                )
            qmp_fds.append(
                _strict_int(
                    fdset["fd"],
                    f"QMP storage fdset[{index}] fd",
                    maximum=2**31 - 1,
                )
            )
        if len(set(qmp_fds)) != 2:
            raise RuntimeLaunchJournalError(
                "QMP storage fdset descriptors cannot alias"
            )
        expected_nodes = {
            "somnus-overlay-file": ("file", False, "/dev/fdset/1"),
            "somnus-base-file": ("file", True, "/dev/fdset/2"),
            "somnus-base-qcow2": ("qcow2", True, None),
            "somnus-disk": ("qcow2", False, None),
        }
        observed_nodes: dict[str, tuple[object, object, str]] = {}
        for index, value in enumerate(graph_table["named_nodes"]):
            node = _strict_object(
                value,
                frozenset(
                    {
                        "driver",
                        "node_name",
                        "reported_file",
                        "read_only",
                    }
                ),
                f"QMP storage node[{index}]",
            )
            name = str(node["node_name"])
            reported_file = str(node["reported_file"])
            expected = expected_nodes.get(name)
            if (
                expected is None
                or not reported_file
                or (expected[2] is not None and reported_file != expected[2])
            ):
                raise RuntimeLaunchJournalError(
                    "QMP storage named-node file role is invalid"
                )
            observed_nodes[name] = (
                node["driver"],
                node["read_only"],
                expected[2],
            )
        if observed_nodes != expected_nodes:
            raise RuntimeLaunchJournalError(
                "QMP storage named-node graph is not canonical"
            )
        expected_edges = {
            "somnus-overlay-file": (None, None),
            "somnus-base-file": (None, None),
            "somnus-base-qcow2": ("somnus-base-file", None),
            "somnus-disk": (
                "somnus-overlay-file",
                "somnus-base-qcow2",
            ),
        }
        observed_edges: dict[str, tuple[object, object]] = {}
        for index, value in enumerate(graph_table["blockstats"]):
            node = _strict_object(
                value,
                frozenset({"backing_node", "node_name", "parent_node"}),
                f"QMP storage edge[{index}]",
            )
            observed_edges[str(node["node_name"])] = (
                node["parent_node"],
                node["backing_node"],
            )
        if observed_edges != expected_edges:
            raise RuntimeLaunchJournalError(
                "QMP storage blockstats graph is not canonical"
            )
        pinned = prior["executable_pinned"]["storage"]
        assert isinstance(pinned, dict)
        for index, (role, key, access) in enumerate(
            (
                ("overlay", "overlay", "rw"),
                ("base", "base", "r"),
            )
        ):
            descriptor = _strict_object(
                graph_table["peer_descriptors"][index],
                frozenset(
                    {"access_mode", "device_id", "fd", "inode", "role"}
                ),
                f"QMP peer descriptor[{index}]",
            )
            authority = pinned[key]
            assert isinstance(authority, dict)
            if (
                descriptor["role"] != role
                or descriptor["access_mode"] != access
                or descriptor["fd"] != qmp_fds[index]
                or descriptor["device_id"] != authority["device_id"]
                or descriptor["inode"] != authority["inode"]
            ):
                raise RuntimeLaunchJournalError(
                    "QMP peer descriptor is not the pinned storage authority"
                )
        storage_graph_sha256 = _strict_hash(
            table["storage_graph_sha256"],
            "QMP storage graph SHA256",
        )
        if canonical_evidence_sha256(storage_graph) != storage_graph_sha256:
            raise RuntimeLaunchJournalError(
                "QMP storage graph hash is invalid"
            )
        return {
            "evidence": evidence,
            "evidence_sha256": evidence_sha256,
            "storage_graph": storage_graph,
            "storage_graph_sha256": storage_graph_sha256,
        }
    if name == "process_identity_recorded":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "process_record_id",
                    "process_identity_sha256",
                }
            ),
            name,
        )
        process = prior["process_observed"]["process"]
        assert isinstance(process, dict)
        reduced = {
            "pid": process["pid"],
            "start_time_ticks": process["start_time_ticks"],
            "executable": process["executable"],
            "executable_sha256": process["executable_sha256"],
            "observed_at": process["observed_at"],
        }
        normalized = {
            "process_record_id": str(
                _strict_uuid(
                    table["process_record_id"],
                    "process_record_id",
                )
            ),
            "process_identity_sha256": _strict_hash(
                table["process_identity_sha256"],
                "process identity SHA256",
            ),
        }
        if (
            normalized["process_record_id"] != str(intent.process_record_id)
            or normalized["process_identity_sha256"]
            != canonical_evidence_sha256(reduced)
        ):
            raise RuntimeLaunchJournalError(
                "durable process identity disagrees with observed QEMU"
            )
        return normalized
    if name == "disk_observation_recorded":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "disk_observation_id",
                    "disk_id",
                    "process_record_id",
                    "canonical_sha256",
                }
            ),
            name,
        )
        normalized = {
            "disk_observation_id": str(
                _strict_uuid(
                    table["disk_observation_id"],
                    "disk_observation_id",
                )
            ),
            "disk_id": str(
                _strict_uuid(table["disk_id"], "disk_id")
            ),
            "process_record_id": str(
                _strict_uuid(
                    table["process_record_id"],
                    "process_record_id",
                )
            ),
            "canonical_sha256": _strict_hash(
                table["canonical_sha256"],
                "disk observation SHA256",
            ),
        }
        if (
            normalized["disk_observation_id"]
            != str(intent.disk_observation_id)
            or normalized["disk_id"] != str(intent.disk_id)
            or normalized["process_record_id"]
            != str(intent.process_record_id)
        ):
            raise RuntimeLaunchJournalError(
                "disk observation disagrees with launch authority"
            )
        return normalized
    if name == "lifecycle_committed":
        table = _strict_object(
            raw,
            frozenset(
                {
                    "lifecycle_evidence_id",
                    "qmp_evidence_id",
                    "process_record_id",
                    "state",
                    "canonical_sha256",
                }
            ),
            name,
        )
        normalized = {
            "lifecycle_evidence_id": str(
                _strict_uuid(
                    table["lifecycle_evidence_id"],
                    "lifecycle_evidence_id",
                )
            ),
            "qmp_evidence_id": str(
                _strict_uuid(
                    table["qmp_evidence_id"],
                    "qmp_evidence_id",
                )
            ),
            "process_record_id": str(
                _strict_uuid(
                    table["process_record_id"],
                    "process_record_id",
                )
            ),
            "state": _strict_token(table["state"], "lifecycle state"),
            "canonical_sha256": _strict_hash(
                table["canonical_sha256"],
                "lifecycle evidence SHA256",
            ),
        }
        if (
            normalized["lifecycle_evidence_id"]
            != str(intent.lifecycle_evidence_id)
            or normalized["qmp_evidence_id"] != str(intent.qmp_evidence_id)
            or normalized["process_record_id"]
            != str(intent.process_record_id)
            or normalized["state"] != VMState.QMP_RUNNING.value
        ):
            raise RuntimeLaunchJournalError(
                "lifecycle checkpoint disagrees with verified runtime identities"
            )
        return normalized
    raise RuntimeLaunchJournalError(
        f"{name!r} does not accept a generic fact payload"
    )


_CRASH_BY_COUNT: Final[tuple[CrashClass, ...]] = (
    CrashClass.NOT_PERSISTED,
    CrashClass.PRE_SPAWN,
    CrashClass.PRE_SPAWN,
    CrashClass.PRE_SPAWN,
    CrashClass.LOG_GUARD_RELEASE_WINDOW,
    CrashClass.LOG_GUARD_DURABLE,
    CrashClass.QEMU_GUARD_RELEASE_WINDOW,
    CrashClass.QEMU_TARGET_UNVERIFIED,
    CrashClass.PROCESS_OBSERVED,
    CrashClass.PROCESS_PIDFILE_BOUND,
    CrashClass.QMP_HANDSHAKE_PARTIAL,
    CrashClass.QMP_HANDSHAKE_PARTIAL,
    CrashClass.QMP_IDENTITY_VERIFIED,
    CrashClass.RUNTIME_RECORDS_PARTIAL,
    CrashClass.RUNTIME_RECORDS_PARTIAL,
    CrashClass.LIFECYCLE_COMMITTED,
    CrashClass.COMPLETE,
)

_RECOVERY_BY_COUNT: Final[tuple[RecoveryDisposition, ...]] = (
    RecoveryDisposition.NONE,
    RecoveryDisposition.DROP_UNSTARTED_INTENT,
    RecoveryDisposition.CLEAN_PRIVATE_ARTIFACTS,
    RecoveryDisposition.CLEAN_PRIVATE_ARTIFACTS,
    RecoveryDisposition.REVALIDATE_LOG_GUARD_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_LOG_GUARD_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_QEMU_TARGET_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_QEMU_TARGET_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_PROCESS_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_PROCESS_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_QMP_OR_CLEAN,
    RecoveryDisposition.REVALIDATE_QMP_OR_CLEAN,
    RecoveryDisposition.ADOPT_IF_STABLE_OR_CLEAN,
    RecoveryDisposition.RECONCILE_DURABLE_RUNTIME,
    RecoveryDisposition.RECONCILE_DURABLE_RUNTIME,
    RecoveryDisposition.RECONCILE_DURABLE_RUNTIME,
    RecoveryDisposition.FINALIZE_COMPLETION,
)


@dataclass(frozen=True, slots=True)
class RuntimeLaunchJournal:
    """Immutable ordered checkpoint state plus deterministic replay validation."""

    intent: RuntimeLaunchIntent
    checkpoints: tuple[CheckpointWrite, ...] = ()
    durable_status: str | None = None
    durable_cleanup_state: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, RuntimeLaunchIntent):
            raise TypeError("intent must be a RuntimeLaunchIntent")
        if not isinstance(self.checkpoints, tuple):
            raise TypeError("checkpoints must be a tuple")
        if len(self.checkpoints) > len(CHECKPOINT_ORDER):
            raise RuntimeLaunchJournalError(
                "runtime launch has too many checkpoints"
            )
        prior: dict[str, dict[str, object]] = {}
        for ordinal, checkpoint in enumerate(self.checkpoints):
            if not isinstance(checkpoint, CheckpointWrite):
                raise TypeError("checkpoint must be a CheckpointWrite")
            expected = CHECKPOINT_ORDER[ordinal]
            if (
                checkpoint.operation_id != self.intent.operation_id
                or checkpoint.ordinal != ordinal
                or checkpoint.name != expected
            ):
                raise RuntimeLaunchJournalError(
                    "runtime launch checkpoints are skipped, reused, or out of order"
                )
            facts = self._validate_payload(checkpoint, prior)
            prior[checkpoint.name] = facts
        if self.durable_status is not None and self.durable_status not in {
            "pending",
            "running",
            "completed",
            "failed",
            "canceled",
            "recovery_required",
        }:
            raise RuntimeLaunchJournalError(
                "durable operation status is unsupported"
            )
        if (
            self.durable_cleanup_state is not None
            and self.durable_cleanup_state
            not in {
                "none",
                "not_spawned",
                "exit_observed",
                "unknown",
                "orphaned",
            }
        ):
            raise RuntimeLaunchJournalError(
                "durable cleanup state is unsupported"
            )

    @property
    def latest_checkpoint(self) -> str | None:
        return self.checkpoints[-1].name if self.checkpoints else None

    @property
    def next_checkpoint(self) -> str | None:
        if len(self.checkpoints) == len(CHECKPOINT_ORDER):
            return None
        return CHECKPOINT_ORDER[len(self.checkpoints)]

    @property
    def crash_class(self) -> CrashClass:
        return _CRASH_BY_COUNT[len(self.checkpoints)]

    @property
    def recovery_disposition(self) -> RecoveryDisposition:
        if self.durable_status in {"completed", "failed", "canceled"}:
            return RecoveryDisposition.NONE
        if (
            self.durable_status == "recovery_required"
            and self.durable_cleanup_state == "orphaned"
        ):
            return RecoveryDisposition.ORPHAN_EMERGENCY
        return _RECOVERY_BY_COUNT[len(self.checkpoints)]

    def begin_operation_kwargs(self) -> dict[str, object]:
        return self.intent.begin_operation_kwargs()

    def _prior_facts(self) -> dict[str, dict[str, object]]:
        prior: dict[str, dict[str, object]] = {}
        for checkpoint in self.checkpoints:
            facts = self._validate_payload(checkpoint, prior)
            prior[checkpoint.name] = facts
        return prior

    def pinned_storage_authority(
        self,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        """Return the canonical overlay/base/marker guard authority.

        Descriptor numbers are meaningful only for the exact unreleased guard
        process recorded by this journal.  The complete metadata and immutable
        hashes remain durable so restart recovery can reject a guard whose
        inherited authority differs from the launch checkpoint.
        """

        facts = self._prior_facts().get("executable_pinned")
        if facts is None:
            raise RuntimeLaunchJournalError(
                "pinned storage authority is not yet durable"
            )
        storage = facts.get("storage")
        if not isinstance(storage, dict):
            raise RuntimeLaunchJournalError(
                "pinned storage authority is absent"
            )
        values: list[dict[str, object]] = []
        for key in ("overlay", "base", "owner_marker"):
            item = storage.get(key)
            if not isinstance(item, dict):
                raise RuntimeLaunchJournalError(
                    "pinned storage authority is incomplete"
                )
            values.append(dict(item))
        return values[0], values[1], values[2]

    def private_directory_identities(
        self,
    ) -> tuple[JournalDirectoryIdentity, JournalDirectoryIdentity]:
        """Return the frozen runtime-root and log-root identities."""

        facts = self._prior_facts().get("private_paths_prepared")
        if facts is None:
            raise RuntimeLaunchJournalError(
                "private directory identity is not yet durable"
            )
        owner_uid = int(facts["owner_uid"])
        runtime = JournalDirectoryIdentity(
            path=str(PurePosixPath(self.intent.qmp_socket).parent),
            device_id=int(facts["runtime_root_device_id"]),
            inode=int(facts["runtime_root_inode"]),
            owner_uid=owner_uid,
            mode=int(facts["runtime_root_mode"]),
        )
        logs = JournalDirectoryIdentity(
            path=str(PurePosixPath(self.intent.serial_log_path).parent),
            device_id=int(facts["log_root_device_id"]),
            inode=int(facts["log_root_inode"]),
            owner_uid=owner_uid,
            mode=int(facts["log_root_mode"]),
        )
        return runtime, logs

    def observed_process_at(
        self,
        checkpoint_name: str,
    ) -> ObservedProcessIdentity:
        """Return a fresh typed copy of one validated process observation.

        Only checkpoints that durably carry a complete
        :class:`ObservedProcessIdentity` are readable through this boundary.
        Release, pidfile, and QMP checkpoints intentionally cannot be
        projected into process identity.  The returned frozen value is rebuilt
        from the already-validated canonical checkpoint facts, so callers
        never need to decode the journal's private JSON representation.
        """

        allowed = {
            "log_guard_spawned",
            "qemu_guard_spawned",
            "process_observed",
        }
        if checkpoint_name not in allowed:
            raise RuntimeLaunchJournalError(
                "process evidence is available only at log_guard_spawned, "
                "qemu_guard_spawned, or process_observed"
            )
        facts = self._prior_facts().get(checkpoint_name)
        if facts is None:
            raise RuntimeLaunchJournalError(
                f"{checkpoint_name} process checkpoint is absent"
            )
        process = facts.get("process")
        if not isinstance(process, dict):
            raise RuntimeLaunchJournalError(
                f"{checkpoint_name} has no complete process evidence"
            )
        try:
            return ObservedProcessIdentity.from_dict(dict(process))
        except QemuProcessError as exc:
            raise RuntimeLaunchJournalError(
                f"{checkpoint_name} process evidence cannot be rehydrated"
            ) from exc

    def log_pipe_identities(
        self,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        """Return ``(serial, qemu)`` durable pipe ``(device, inode)`` pairs."""

        facts = self._prior_facts().get("log_guard_spawned")
        if facts is None:
            raise RuntimeLaunchJournalError(
                "log_guard_spawned pipe identities are absent"
            )
        keys = (
            "serial_pipe_device_id",
            "serial_pipe_inode",
            "qemu_pipe_device_id",
            "qemu_pipe_inode",
        )
        if any(
            not isinstance(facts.get(key), int)
            or isinstance(facts.get(key), bool)
            for key in keys
        ):
            raise RuntimeLaunchJournalError(
                "log guardian has no complete pipe identity evidence"
            )
        serial = (
            int(facts["serial_pipe_device_id"]),
            int(facts["serial_pipe_inode"]),
        )
        qemu = (
            int(facts["qemu_pipe_device_id"]),
            int(facts["qemu_pipe_inode"]),
        )
        if (
            serial[0] < 1
            or serial[1] < 1
            or qemu[0] < 1
            or qemu[1] < 1
            or serial == qemu
        ):
            raise RuntimeLaunchJournalError(
                "log guardian pipe identity evidence is incoherent"
            )
        return serial, qemu

    def pidfile_identity(self) -> JournalPidfileIdentity:
        """Return validated unlink-safe pidfile identity after its checkpoint."""

        facts = self._prior_facts().get("pidfile_verified")
        if facts is None:
            raise RuntimeLaunchJournalError(
                "pidfile_verified identity is absent"
            )
        required = {
            "pidfile_pid",
            "device_id",
            "inode",
            "owner_uid",
            "mode",
            "link_count",
            "size_bytes",
            "mtime_ns",
            "ctime_ns",
        }
        if set(facts) != required:
            raise RuntimeLaunchJournalError(
                "pidfile_verified has incomplete identity evidence"
            )
        try:
            return JournalPidfileIdentity(
                path=self.intent.pid_file,
                pid=facts["pidfile_pid"],  # type: ignore[arg-type]
                device_id=facts["device_id"],  # type: ignore[arg-type]
                inode=facts["inode"],  # type: ignore[arg-type]
                owner_uid=facts["owner_uid"],  # type: ignore[arg-type]
                mode=facts["mode"],  # type: ignore[arg-type]
                link_count=facts["link_count"],  # type: ignore[arg-type]
                size_bytes=facts["size_bytes"],  # type: ignore[arg-type]
                mtime_ns=facts["mtime_ns"],  # type: ignore[arg-type]
                ctime_ns=facts["ctime_ns"],  # type: ignore[arg-type]
            )
        except (RuntimeLaunchJournalError, TypeError, ValueError) as exc:
            raise RuntimeLaunchJournalError(
                "pidfile_verified identity cannot be rehydrated"
            ) from exc

    def qmp_socket_identity(self) -> JournalQMPSocketIdentity:
        """Return validated unlink-safe QMP socket identity after greeting."""

        facts = self._prior_facts().get("qmp_greeting_observed")
        if facts is None:
            raise RuntimeLaunchJournalError(
                "qmp_greeting_observed socket identity is absent"
            )
        required = {
            "qmp_evidence_id",
            "peer_pid",
            "peer_uid",
            "greeting_sha256",
            "qmp_socket_device_id",
            "qmp_socket_inode",
            "qmp_socket_mode",
        }
        if set(facts) != required:
            raise RuntimeLaunchJournalError(
                "qmp_greeting_observed has incomplete socket identity evidence"
            )
        try:
            return JournalQMPSocketIdentity(
                path=self.intent.qmp_socket,
                device_id=facts["qmp_socket_device_id"],  # type: ignore[arg-type]
                inode=facts["qmp_socket_inode"],  # type: ignore[arg-type]
                uid=facts["peer_uid"],  # type: ignore[arg-type]
                mode=facts["qmp_socket_mode"],  # type: ignore[arg-type]
            )
        except (RuntimeLaunchJournalError, TypeError, ValueError) as exc:
            raise RuntimeLaunchJournalError(
                "qmp_greeting_observed socket identity cannot be rehydrated"
            ) from exc

    def _checkpoint_payload(
        self,
        name: str,
        ordinal: int,
        facts: object,
    ) -> bytes:
        if name == "intent_persisted":
            if facts not in (None, {}, MappingProxyType({})):
                raise RuntimeLaunchJournalError(
                    "intent_persisted facts are derived, not caller supplied"
                )
            return self.intent.intent_bytes()
        if name == "completion_observed":
            if facts not in (None, {}, MappingProxyType({})):
                raise RuntimeLaunchJournalError(
                    "completion result is derived, not caller supplied"
                )
            provisional = RuntimeLaunchJournal(
                self.intent,
                self.checkpoints,
                self.durable_status,
            )
            return provisional.result_bytes(
                "completed",
                reason="launch_completed",
                cleanup_state="none",
                latest_override=name,
            )
        normalized = _checkpoint_facts(
            self.intent,
            name,
            facts,
            self._prior_facts(),
        )
        return _encode_json(
            {
                "schema": RUNTIME_LAUNCH_CHECKPOINT_SCHEMA,
                "kind": RUNTIME_LAUNCH_KIND,
                "operation_id": str(self.intent.operation_id),
                "vm_id": str(self.intent.vm_id),
                "generation": self.intent.generation,
                "boot_id": str(self.intent.boot_id),
                "checkpoint": name,
                "ordinal": ordinal,
                "facts": normalized,
            },
            f"{name} checkpoint",
        )

    def _validate_payload(
        self,
        checkpoint: CheckpointWrite,
        prior: Mapping[str, dict[str, object]],
    ) -> dict[str, object]:
        if checkpoint.name == "intent_persisted":
            if checkpoint.payload != self.intent.intent_bytes():
                raise RuntimeLaunchJournalError(
                    "intent checkpoint bytes disagree with operation intent"
                )
            return {}
        if checkpoint.name == "completion_observed":
            expected = self.result_bytes(
                "completed",
                reason="launch_completed",
                cleanup_state="none",
                latest_override="completion_observed",
            )
            if checkpoint.payload != expected:
                raise RuntimeLaunchJournalError(
                    "completion checkpoint disagrees with canonical result"
                )
            return {}
        table = _strict_object(
            _decode_json_object(
                checkpoint.payload,
                f"{checkpoint.name} checkpoint",
            ),
            frozenset(
                {
                    "schema",
                    "kind",
                    "operation_id",
                    "vm_id",
                    "generation",
                    "boot_id",
                    "checkpoint",
                    "ordinal",
                    "facts",
                }
            ),
            f"{checkpoint.name} checkpoint",
        )
        if (
            table["schema"] != RUNTIME_LAUNCH_CHECKPOINT_SCHEMA
            or table["kind"] != RUNTIME_LAUNCH_KIND
            or table["operation_id"] != str(self.intent.operation_id)
            or table["vm_id"] != str(self.intent.vm_id)
            or table["generation"] != self.intent.generation
            or table["boot_id"] != str(self.intent.boot_id)
            or table["checkpoint"] != checkpoint.name
            or table["ordinal"] != checkpoint.ordinal
        ):
            raise RuntimeLaunchJournalError(
                f"{checkpoint.name} checkpoint changed launch identity"
            )
        normalized = _checkpoint_facts(
            self.intent,
            checkpoint.name,
            table["facts"],
            prior,
        )
        expected = _encode_json(
            {**table, "facts": normalized},
            f"{checkpoint.name} checkpoint",
        )
        if expected != checkpoint.payload:
            raise RuntimeLaunchJournalError(
                f"{checkpoint.name} checkpoint facts are not canonical"
            )
        return normalized

    def advance(
        self,
        name: str,
        facts: Mapping[str, object] | None = None,
    ) -> tuple[Self, CheckpointWrite]:
        """Append exactly the next boundary and return its registry write."""

        expected = self.next_checkpoint
        if expected is None:
            raise RuntimeLaunchJournalError(
                "runtime launch journal is already complete"
            )
        if name != expected:
            raise RuntimeLaunchJournalError(
                f"next checkpoint is {expected!r}, not {name!r}"
            )
        ordinal = len(self.checkpoints)
        write = CheckpointWrite(
            operation_id=self.intent.operation_id,
            name=name,
            ordinal=ordinal,
            payload=self._checkpoint_payload(name, ordinal, facts),
        )
        updated = RuntimeLaunchJournal(
            self.intent,
            (*self.checkpoints, write),
            self.durable_status,
        )
        return updated, write

    def running_status_write(self) -> OperationStatusWrite:
        if not self.checkpoints:
            raise RuntimeLaunchJournalError(
                "operation cannot run before intent_persisted"
            )
        return OperationStatusWrite(
            self.intent.operation_id,
            "running",
            b"{}",
        )

    def result_dict(
        self,
        status: str,
        *,
        reason: str,
        cleanup_state: str,
        latest_override: str | None = None,
    ) -> dict[str, object]:
        selected_status = _strict_token(status, "result status")
        if selected_status not in {
            "completed",
            "failed",
            "canceled",
            "recovery_required",
        }:
            raise RuntimeLaunchJournalError(
                "result status is unsupported"
            )
        selected_reason = _strict_token(reason, "result reason")
        selected_cleanup = _strict_token(
            cleanup_state,
            "cleanup state",
        )
        if selected_cleanup not in {
            "none",
            "not_spawned",
            "exit_observed",
            "unknown",
            "orphaned",
        }:
            raise RuntimeLaunchJournalError(
                "cleanup_state is unsupported"
            )
        if selected_status == "completed":
            if (
                len(self.checkpoints) < len(CHECKPOINT_ORDER) - 1
                or selected_cleanup != "none"
            ):
                raise RuntimeLaunchJournalError(
                    "completion requires lifecycle_committed and no cleanup"
                )
        elif selected_status in {"failed", "canceled"}:
            if selected_cleanup not in {"not_spawned", "exit_observed"}:
                raise RuntimeLaunchJournalError(
                    "terminal failure requires proven absence or exit"
                )
        latest = (
            latest_override
            if latest_override is not None
            else self.latest_checkpoint or "none"
        )
        qmp_status = (
            "running"
            if len(self.checkpoints)
            > CHECKPOINT_ORDER.index("qmp_identity_verified")
            else "unknown"
        )
        crash_count = (
            CHECKPOINT_ORDER.index(latest) + 1
            if latest in CHECKPOINT_ORDER
            else len(self.checkpoints)
        )
        crash_class = _CRASH_BY_COUNT[crash_count]
        disposition = _RECOVERY_BY_COUNT[crash_count]
        if (
            selected_status == "recovery_required"
            and selected_cleanup == "orphaned"
        ):
            disposition = RecoveryDisposition.ORPHAN_EMERGENCY
        if selected_status in {"completed", "failed", "canceled"}:
            disposition = RecoveryDisposition.NONE
        return {
            "schema": RUNTIME_LAUNCH_RESULT_SCHEMA,
            "kind": RUNTIME_LAUNCH_KIND,
            "status": selected_status,
            "operation_id": str(self.intent.operation_id),
            "vm_id": str(self.intent.vm_id),
            "generation": self.intent.generation,
            "boot_id": str(self.intent.boot_id),
            "disk_id": str(self.intent.disk_id),
            "process_record_id": str(self.intent.process_record_id),
            "qmp_evidence_id": str(self.intent.qmp_evidence_id),
            "lifecycle_evidence_id": str(
                self.intent.lifecycle_evidence_id
            ),
            "disk_observation_id": str(
                self.intent.disk_observation_id
            ),
            "latest_checkpoint": latest,
            "crash_class": crash_class.value,
            "recovery_disposition": disposition.value,
            "reason": selected_reason,
            "cleanup_state": selected_cleanup,
            "qmp_status": qmp_status,
        }

    def result_bytes(
        self,
        status: str,
        *,
        reason: str,
        cleanup_state: str,
        latest_override: str | None = None,
    ) -> bytes:
        return _encode_json(
            self.result_dict(
                status,
                reason=reason,
                cleanup_state=cleanup_state,
                latest_override=latest_override,
            ),
            "runtime launch result",
        )

    def completion_status_write(self) -> OperationStatusWrite:
        if self.latest_checkpoint != "completion_observed":
            raise RuntimeLaunchJournalError(
                "completion status requires completion_observed"
            )
        return OperationStatusWrite(
            self.intent.operation_id,
            "completed",
            self.checkpoints[-1].payload,
        )

    def recovery_status_write(
        self,
        reason: str,
        *,
        cleanup_state: str = "unknown",
    ) -> OperationStatusWrite:
        if not self.checkpoints:
            raise RuntimeLaunchJournalError(
                "recovery requires a durable intent checkpoint"
            )
        return OperationStatusWrite(
            self.intent.operation_id,
            "recovery_required",
            self.result_bytes(
                "recovery_required",
                reason=reason,
                cleanup_state=cleanup_state,
            ),
        )

    def terminal_status_write(
        self,
        status: str,
        *,
        reason: str,
        cleanup_state: str,
    ) -> OperationStatusWrite:
        if status not in {"failed", "canceled"}:
            raise RuntimeLaunchJournalError(
                "terminal_status_write accepts failed or canceled"
            )
        return OperationStatusWrite(
            self.intent.operation_id,
            status,
            self.result_bytes(
                status,
                reason=reason,
                cleanup_state=cleanup_state,
            ),
        )

    @classmethod
    def replay(
        cls,
        operation: OperationRecord,
        checkpoints: Sequence[OperationCheckpoint],
    ) -> Self:
        """Validate one registry operation and reconstruct its exact prefix."""

        if not isinstance(operation, OperationRecord):
            raise TypeError("operation must be an OperationRecord")
        if not isinstance(checkpoints, Sequence):
            raise TypeError("checkpoints must be a sequence")
        for payload, digest, label in (
            (operation.intent, operation.intent_sha256, "operation intent"),
            (
                operation.owned_resources,
                operation.owned_resources_sha256,
                "operation owned_resources",
            ),
            (operation.result, operation.result_sha256, "operation result"),
        ):
            if (
                not isinstance(payload, bytes)
                or not isinstance(digest, str)
                or _sha256_bytes(payload) != digest
            ):
                raise RuntimeLaunchJournalError(
                    f"{label} hash disagrees with persisted bytes"
                )
        intent = RuntimeLaunchIntent.from_persisted(
            operation.intent,
            operation.owned_resources,
        )
        if (
            operation.kind != RUNTIME_LAUNCH_KIND
            or operation.operation_id != intent.operation_id
            or operation.vm_id != intent.vm_id
            or operation.generation != intent.generation
            or operation.idempotency_key != intent.idempotency_key
            or operation.request_hash != intent.request_hash
        ):
            raise RuntimeLaunchJournalError(
                "operation row disagrees with runtime launch intent"
            )
        writes: list[CheckpointWrite] = []
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, OperationCheckpoint):
                raise TypeError(
                    "checkpoint must be an OperationCheckpoint"
                )
            if (
                checkpoint.operation_id != intent.operation_id
                or _sha256_bytes(checkpoint.payload)
                != checkpoint.payload_sha256
            ):
                raise RuntimeLaunchJournalError(
                    "checkpoint identity or payload hash disagrees"
                )
            writes.append(
                CheckpointWrite(
                    operation_id=checkpoint.operation_id,
                    name=checkpoint.name,
                    ordinal=checkpoint.ordinal,
                    payload=checkpoint.payload,
                )
            )
        if not writes:
            raise RuntimeLaunchJournalError(
                "persisted runtime launch has no intent checkpoint"
            )
        durable_cleanup_state: str | None = None
        if operation.status == "recovery_required":
            result_table = _strict_object(
                _decode_json_object(
                    operation.result,
                    "runtime launch operation result",
                ),
                frozenset(
                    {
                        "schema",
                        "kind",
                        "status",
                        "operation_id",
                        "vm_id",
                        "generation",
                        "boot_id",
                        "disk_id",
                        "process_record_id",
                        "qmp_evidence_id",
                        "lifecycle_evidence_id",
                        "disk_observation_id",
                        "latest_checkpoint",
                        "crash_class",
                        "recovery_disposition",
                        "reason",
                        "cleanup_state",
                        "qmp_status",
                    }
                ),
                "runtime launch operation result",
            )
            durable_cleanup_state = _strict_token(
                result_table["cleanup_state"],
                "cleanup state",
            )
        state = cls(
            intent,
            tuple(writes),
            operation.status,
            durable_cleanup_state,
        )
        if operation.latest_checkpoint != len(writes) - 1:
            raise RuntimeLaunchJournalError(
                "operation latest_checkpoint disagrees with replay prefix"
            )
        state._validate_operation_result(operation)
        return state

    def _validate_operation_result(self, operation: OperationRecord) -> None:
        if operation.status in {"pending", "running"}:
            if operation.result != b"{}":
                raise RuntimeLaunchJournalError(
                    "pending or running launch result must remain empty"
                )
            return
        if operation.status == "completed":
            if (
                self.latest_checkpoint != "completion_observed"
                or operation.result != self.checkpoints[-1].payload
            ):
                raise RuntimeLaunchJournalError(
                    "completed launch lacks its exact completion result"
                )
            return
        table = _strict_object(
            _decode_json_object(
                operation.result,
                "runtime launch operation result",
            ),
            frozenset(
                {
                    "schema",
                    "kind",
                    "status",
                    "operation_id",
                    "vm_id",
                    "generation",
                    "boot_id",
                    "disk_id",
                    "process_record_id",
                    "qmp_evidence_id",
                    "lifecycle_evidence_id",
                    "disk_observation_id",
                    "latest_checkpoint",
                    "crash_class",
                    "recovery_disposition",
                    "reason",
                    "cleanup_state",
                    "qmp_status",
                }
            ),
            "runtime launch operation result",
        )
        cleanup = _strict_token(
            table["cleanup_state"],
            "cleanup state",
        )
        expected_disposition = self.recovery_disposition
        if (
            operation.status == "recovery_required"
            and cleanup == "orphaned"
        ):
            expected_disposition = RecoveryDisposition.ORPHAN_EMERGENCY
        if (
            table["schema"] != RUNTIME_LAUNCH_RESULT_SCHEMA
            or table["kind"] != RUNTIME_LAUNCH_KIND
            or table["status"] != operation.status
            or table["operation_id"] != str(self.intent.operation_id)
            or table["vm_id"] != str(self.intent.vm_id)
            or table["generation"] != self.intent.generation
            or table["boot_id"] != str(self.intent.boot_id)
            or table["disk_id"] != str(self.intent.disk_id)
            or table["process_record_id"]
            != str(self.intent.process_record_id)
            or table["qmp_evidence_id"]
            != str(self.intent.qmp_evidence_id)
            or table["lifecycle_evidence_id"]
            != str(self.intent.lifecycle_evidence_id)
            or table["disk_observation_id"]
            != str(self.intent.disk_observation_id)
            or table["latest_checkpoint"]
            != (self.latest_checkpoint or "none")
            or table["crash_class"] != self.crash_class.value
            or table["recovery_disposition"]
            != expected_disposition.value
        ):
            raise RuntimeLaunchJournalError(
                "operation result disagrees with replayed launch state"
            )
        _strict_token(table["reason"], "result reason")
        if (
            operation.status in {"failed", "canceled"}
            and cleanup not in {"not_spawned", "exit_observed"}
        ):
            raise RuntimeLaunchJournalError(
                "terminal result has not proven child absence or exit"
            )
        expected_qmp = (
            "running"
            if len(self.checkpoints)
            > CHECKPOINT_ORDER.index("qmp_identity_verified")
            else "unknown"
        )
        if table["qmp_status"] != expected_qmp:
            raise RuntimeLaunchJournalError(
                "operation result QMP status disagrees with checkpoints"
            )


__all__ = [
    "CHECKPOINT_ORDER",
    "CrashClass",
    "JournalDirectoryIdentity",
    "JournalExecutableIdentity",
    "JournalPidfileIdentity",
    "JournalQMPSocketIdentity",
    "MAX_RUNTIME_JOURNAL_BYTES",
    "OperationStatusWrite",
    "CheckpointWrite",
    "RUNTIME_LAUNCH_KIND",
    "RecoveryDisposition",
    "RuntimeLaunchIntent",
    "RuntimeLaunchJournal",
    "RuntimeLaunchJournalError",
    "canonical_evidence_sha256",
]
