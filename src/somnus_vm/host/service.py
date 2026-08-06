"""Single-owner P2 registry operation service.

This module is deliberately narrower than the future VM lifecycle API.  It
proves durable declaration ownership, endpoint leasing, optimistic registry
updates, cancellation, idempotency, and checkpoint recovery without launching
QEMU, creating disks, or accepting caller-asserted machine observations.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Final
from uuid import UUID, uuid5

from somnus_protocol import (
    CURRENT_PROTOCOL_VERSION,
    ERROR_CODE_REGISTRY,
    SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
    ErrorCode,
    ErrorEnvelope,
    VMContractError,
    VMDefinition,
    VMPorts,
    VMRecord,
    VMState,
)

from ..config import HostConfiguration
from ..transport import PeerCredentials
from .registry import (
    BaseImageRecord,
    IdempotencyRecord,
    OperationCheckpoint,
    OperationRecord,
    RegistryConflictError,
    RegistryIntegrityError,
    RegistryMode,
    RegistryNotFoundError,
    RegistryReadOnlyError,
    RegistryRevisionConflict,
    SQLiteRegistry,
    StoredVM,
)
from .images import (
    ImageError,
    ImageIntegrityError,
    ImageManifest,
    ImageManifestError,
    ImageOwnershipError,
    QemuImgError,
    canonical_path,
)
from .storage import (
    ImageStore,
    OverlayCreateIntent,
    RegisteredBase,
    RegisteredOverlay,
    StorageResumeState,
)

_OPERATION_NAMESPACE: Final[UUID] = UUID("ed1da265-bf4c-48f1-b729-785875ef2576")
_VM_NAMESPACE: Final[UUID] = UUID("bd7fe3f0-c76e-4c29-9f32-d377022f34f2")
_RESOURCE_NAMESPACE: Final[UUID] = UUID("5f2b576d-caca-44e3-b378-4c6b4bf283c7")

_MUTATING_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "registry.declare",
        "registry.lease",
        "registry.update",
        "registry.cancel",
        "storage.import_base",
        "storage.create_overlay",
    }
)
_STORAGE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"storage.import_base", "storage.create_overlay"}
)
_READ_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"registry.status", "registry.get", "registry.list"}
)
_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "canceled"}
)
_MAX_CLOCK_SKEW: Final[timedelta] = timedelta(minutes=5)
_MAX_LIST_RESULTS: Final[int] = 32
_STORAGE_CHECKPOINTS: Final[dict[str, tuple[str, ...]]] = {
    "storage.import_base": (
        "intent_persisted",
        "base_stage_prepared",
        "base_bytes_staged",
        "base_verified",
        "base_published",
        "base_registered",
        "completion_observed",
    ),
    "storage.create_overlay": (
        "intent_persisted",
        "overlay_stage_prepared",
        "overlay_created",
        "overlay_verified",
        "overlay_published",
        "disk_materialized",
        "completion_observed",
    ),
}


class RegistryServiceError(RuntimeError):
    """Expected closed service failure mapped to a registered public code."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class _PreparedOperation:
    request: ControlRequest
    operation_id: UUID
    vm_id: UUID | None
    request_hash: str
    intent: bytes
    owned_resources: bytes


CheckpointObserver = Callable[[UUID, str], None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json_bytes(value: object) -> bytes:
    """Encode bounded internal journal data deterministically.

    The public control schema already excludes non-JSON values and floating
    point values.  This second encoder owns persisted operation data and keeps
    recovery independent from Python mapping order.
    """

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RegistryServiceError(ErrorCode.VALIDATION_ERROR) from exc
    if not encoded or len(encoded) > 1_048_576:
        raise RegistryServiceError(ErrorCode.VALIDATION_ERROR)
    return encoded


def _decode_json_object(payload: bytes) -> dict[str, object]:
    if not isinstance(payload, bytes) or not payload or len(payload) > 1_048_576:
        raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("invalid constant")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
    if not isinstance(decoded, dict):
        raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
    return decoded


def _plain_payload(request: ControlRequest) -> dict[str, object]:
    payload = request.to_dict()["payload"]
    if not isinstance(payload, dict):
        raise RegistryServiceError(ErrorCode.INVALID_REQUEST)
    return payload


def _exact_payload(
    request: ControlRequest,
    *,
    required: frozenset[str],
) -> dict[str, object]:
    payload = _plain_payload(request)
    if frozenset(payload) != required:
        raise RegistryServiceError(ErrorCode.INVALID_REQUEST)
    return payload


def _strict_int(
    value: object,
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
        raise RegistryServiceError(ErrorCode.VALIDATION_ERROR)
    return value


def _operation_id(request: ControlRequest) -> UUID:
    return uuid5(
        _OPERATION_NAMESPACE,
        f"{request.request_id}:{request.operation}",
    )


def _declared_vm_id(request: ControlRequest) -> UUID:
    return uuid5(_VM_NAMESPACE, str(request.request_id))


def _resource_id(vm_id: UUID, generation: int, kind: str) -> UUID:
    return uuid5(_RESOURCE_NAMESPACE, f"{vm_id}:{generation}:{kind}")


def _request_hash(request: ControlRequest) -> str:
    return sha256(request.to_json().encode("utf-8")).hexdigest()


def _error_envelope(
    request: ControlRequest,
    code: ErrorCode,
    *,
    operation_id: UUID | None = None,
) -> ErrorEnvelope:
    specification = ERROR_CODE_REGISTRY[code]
    return ErrorEnvelope(
        schema_version=SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=request.request_id,
        correlation_id=request.correlation_id,
        operation_id=operation_id,
        vm_id=request.vm_id,
        boot_id=request.boot_id,
        generation=request.generation,
        timestamp=_utc_now(),
        code=code,
        category=specification.category,
        exit_code=specification.exit_code,
        evidence=(),
        details={},
    )


def _success_envelope(
    request: ControlRequest,
    result: Mapping[str, object],
    *,
    operation_id: UUID | None = None,
) -> ControlResponse:
    return ControlResponse(
        schema_version=SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=request.request_id,
        correlation_id=request.correlation_id,
        operation_id=operation_id,
        vm_id=request.vm_id,
        boot_id=request.boot_id,
        generation=request.generation,
        timestamp=_utc_now(),
        result=result,
    )


class RegistryMutationService:
    """Serialize authenticated control requests through one durable registry."""

    def __init__(
        self,
        registry: SQLiteRegistry,
        configuration: HostConfiguration,
        *,
        checkpoint_observer: CheckpointObserver | None = None,
        image_store: ImageStore | None = None,
    ) -> None:
        if not isinstance(registry, SQLiteRegistry):
            raise TypeError("registry must be SQLiteRegistry")
        if not isinstance(configuration, HostConfiguration):
            raise TypeError("configuration must be HostConfiguration")
        if checkpoint_observer is not None and not callable(checkpoint_observer):
            raise TypeError("checkpoint_observer must be callable")
        if image_store is not None and not isinstance(image_store, ImageStore):
            raise TypeError("image_store must be ImageStore")
        self._registry = registry
        self._configuration = configuration
        self._checkpoint_observer = checkpoint_observer
        self._image_store = image_store
        self._mutation_lock = threading.RLock()

    @property
    def registry(self) -> SQLiteRegistry:
        return self._registry

    def __call__(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
    ) -> ControlResponse | ErrorEnvelope:
        """Handle one kernel-authenticated control request.

        The transport has already authenticated ``peer``.  Its identity is
        persisted with mutating intent for audit but never reflected publicly.
        """

        if not isinstance(request, ControlRequest):
            raise TypeError("request must be ControlRequest")
        if not isinstance(peer, PeerCredentials):
            raise TypeError("peer must be PeerCredentials")
        try:
            self._validate_request_time(request)
            if request.operation in _READ_OPERATIONS:
                return self._handle_read(request)
            if request.operation not in _MUTATING_OPERATIONS:
                raise RegistryServiceError(ErrorCode.OWNERSHIP_REJECTED)
            with self._mutation_lock:
                return self._handle_mutation(request, peer)
        except RegistryServiceError as exc:
            operation_id = (
                _operation_id(request)
                if request.operation in _MUTATING_OPERATIONS
                else None
            )
            return _error_envelope(
                request,
                exc.code,
                operation_id=operation_id,
            )
        except (ImageManifestError, ImageOwnershipError):
            return _error_envelope(
                request,
                ErrorCode.VALIDATION_ERROR,
                operation_id=(
                    _operation_id(request)
                    if request.operation in _MUTATING_OPERATIONS
                    else None
                ),
            )
        except (ImageIntegrityError, QemuImgError, ImageError):
            return _error_envelope(
                request,
                ErrorCode.EXECUTION_FAILED,
                operation_id=(
                    _operation_id(request)
                    if request.operation in _MUTATING_OPERATIONS
                    else None
                ),
            )
        except RegistryRevisionConflict:
            return _error_envelope(
                request,
                ErrorCode.CONFLICT,
                operation_id=_operation_id(request),
            )
        except RegistryNotFoundError:
            return _error_envelope(
                request,
                ErrorCode.PRECONDITION_FAILED,
                operation_id=(
                    _operation_id(request)
                    if request.operation in _MUTATING_OPERATIONS
                    else None
                ),
            )
        except (RegistryIntegrityError, RegistryReadOnlyError):
            return _error_envelope(
                request,
                ErrorCode.RECOVERY_REQUIRED,
                operation_id=(
                    _operation_id(request)
                    if request.operation in _MUTATING_OPERATIONS
                    else None
                ),
            )
        except RegistryConflictError:
            return _error_envelope(
                request,
                ErrorCode.CONFLICT,
                operation_id=(
                    _operation_id(request)
                    if request.operation in _MUTATING_OPERATIONS
                    else None
                ),
            )

    def _validate_request_time(self, request: ControlRequest) -> None:
        delta = _utc_now() - request.timestamp
        if delta > _MAX_CLOCK_SKEW or delta < -_MAX_CLOCK_SKEW:
            raise RegistryServiceError(ErrorCode.PROTOCOL_ERROR)

    def _handle_read(
        self,
        request: ControlRequest,
    ) -> ControlResponse | ErrorEnvelope:
        if request.operation == "registry.status":
            self._require_unbound_request(request)
            _exact_payload(request, required=frozenset())
            status = self._registry.status
            result: dict[str, object] = {
                "integrity_ok": status.integrity_ok,
                "issue": status.issue,
                "journal_mode": status.journal_mode,
                "mode": status.mode.value,
                "schema_version": status.schema_version,
            }
            return _success_envelope(request, result)

        if request.operation == "registry.get":
            self._require_bound_request(request)
            _exact_payload(request, required=frozenset())
            stored = self._registry.get_vm(request.vm_id)
            if stored is None or stored.record.generation != request.generation:
                raise RegistryServiceError(ErrorCode.PRECONDITION_FAILED)
            return _success_envelope(
                request,
                self._stored_vm_result(stored, include_record=True),
            )

        self._require_unbound_request(request)
        payload = _exact_payload(
            request,
            required=frozenset({"limit"}),
        )
        limit = _strict_int(
            payload["limit"],
            minimum=1,
            maximum=_MAX_LIST_RESULTS,
        )
        rows = self._registry.list_vms(active_only=False)[:limit]
        return _success_envelope(
            request,
            {
                "items": [
                    self._stored_vm_result(row, include_record=False)
                    for row in rows
                ],
                "limit": limit,
            },
        )

    @staticmethod
    def _require_unbound_request(request: ControlRequest) -> None:
        if (
            request.vm_id is not None
            or request.generation is not None
            or request.boot_id is not None
        ):
            raise RegistryServiceError(ErrorCode.INVALID_REQUEST)

    @staticmethod
    def _require_bound_request(request: ControlRequest) -> None:
        if (
            request.vm_id is None
            or request.generation is None
            or request.boot_id is not None
        ):
            raise RegistryServiceError(ErrorCode.INVALID_REQUEST)

    @staticmethod
    def _require_pre_runtime_owner(stored: StoredVM) -> None:
        """Keep public metadata authority outside the live runtime boundary."""

        if stored.record.state is not VMState.DECLARED:
            raise RegistryServiceError(ErrorCode.OWNERSHIP_REJECTED)

    @staticmethod
    def _stored_vm_result(
        stored: StoredVM,
        *,
        include_record: bool,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "active": stored.active,
            "generation": stored.record.generation,
            "name": stored.record.definition.name,
            "revision": stored.revision,
            "state": stored.record.state.value,
            "vm_id": str(stored.record.vm_id),
        }
        if include_record:
            result["record"] = stored.record.to_dict()
        return result

    def _handle_mutation(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
    ) -> ControlResponse | ErrorEnvelope:
        if self._registry.status.mode is not RegistryMode.READ_WRITE:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)

        request_digest = _request_hash(request)
        existing = self._find_idempotency(str(request.request_id))
        if existing is not None:
            return self._replay_idempotent(request, existing, request_digest)
        if any(
            operation.kind in _STORAGE_OPERATIONS
            and operation.status != "completed"
            for operation in self._registry.list_operations()
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)

        if request.operation == "registry.declare":
            prepared = self._prepare_declare(request, peer, request_digest)
        elif request.operation == "storage.import_base":
            prepared = self._prepare_base_import(
                request,
                peer,
                request_digest,
            )
        elif request.operation == "storage.create_overlay":
            prepared = self._prepare_overlay_create(
                request,
                peer,
                request_digest,
            )
        else:
            prepared = self._prepare_bound(request, peer, request_digest)
        with self._registry.write_transaction() as transaction:
            transaction.begin_operation(
                prepared.operation_id,
                (
                    None
                    if request.operation
                    in {"registry.declare", "storage.import_base"}
                    else prepared.vm_id
                ),
                request.operation,
                str(request.request_id),
                request_hash=prepared.request_hash,
                intent=prepared.intent,
                owned_resources=prepared.owned_resources,
                status="pending",
            )
            transaction.append_checkpoint(
                prepared.operation_id,
                "intent_persisted",
                ordinal=0,
                payload=prepared.intent,
            )
            transaction.set_operation_status(
                prepared.operation_id,
                "running",
                result=b"{}",
            )
        try:
            self._observe(prepared.operation_id, "intent_persisted")
        except Exception:
            if prepared.request.operation in _STORAGE_OPERATIONS:
                operation = self._registry.get_operation(prepared.operation_id)
                if operation is not None and operation.status != "completed":
                    self._set_storage_recovery_required(operation)
            raise

        try:
            result = self._execute_resource_stage(prepared)
            self._finalize(prepared, result)
        except Exception as exc:
            if prepared.request.operation in _STORAGE_OPERATIONS:
                operation = self._registry.get_operation(prepared.operation_id)
                if operation is not None and operation.status != "completed":
                    self._set_storage_recovery_required(operation)
                elif operation is not None:
                    raise
                if isinstance(
                    exc,
                    (
                        ImageError,
                        RegistryConflictError,
                        RegistryIntegrityError,
                        RegistryNotFoundError,
                        RegistryReadOnlyError,
                        RegistryRevisionConflict,
                        RegistryServiceError,
                    ),
                ):
                    raise RegistryServiceError(
                        ErrorCode.RECOVERY_REQUIRED
                    ) from exc
                raise
            if isinstance(exc, RegistryRevisionConflict):
                self._record_expected_failure(prepared, ErrorCode.CONFLICT)
                raise
            if isinstance(exc, RegistryNotFoundError):
                self._record_expected_failure(
                    prepared,
                    ErrorCode.PRECONDITION_FAILED,
                )
                raise
            if isinstance(exc, RegistryConflictError):
                self._record_expected_failure(prepared, ErrorCode.CONFLICT)
                raise
            if isinstance(exc, RegistryServiceError):
                self._record_expected_failure(prepared, exc.code)
                raise
            raise

        return _success_envelope(
            request,
            result,
            operation_id=prepared.operation_id,
        )

    def _prepare_declare(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
        request_digest: str,
    ) -> _PreparedOperation:
        self._require_unbound_request(request)
        payload = _exact_payload(
            request,
            required=frozenset({"memory_mib", "name", "vcpus"}),
        )
        name = payload["name"]
        if not isinstance(name, str):
            raise RegistryServiceError(ErrorCode.VALIDATION_ERROR)
        memory_mib = _strict_int(
            payload["memory_mib"],
            minimum=256,
            maximum=1_048_576,
        )
        vcpus = _strict_int(payload["vcpus"], minimum=1, maximum=256)
        vm_id = _declared_vm_id(request)
        ports = self._select_ports()
        disk_path = (
            self._configuration.storage.state_root
            / "vms"
            / str(vm_id)
            / "storage"
            / "aipc.qcow2"
        )
        try:
            definition = VMDefinition(
                name=name,
                disk_path=str(disk_path),
                memory_mib=memory_mib,
                vcpus=vcpus,
                enable_kvm=self._configuration.enable_kvm,
            )
            record = VMRecord(
                definition=definition,
                ports=ports,
                vm_id=vm_id,
                generation=0,
                created_at=request.timestamp,
                updated_at=request.timestamp,
            ).with_planned_runtime(
                qmp_socket=str(
                    self._configuration.daemon.runtime_root / f"{vm_id}.qmp"
                ),
                pid_file=str(
                    self._configuration.daemon.runtime_root / f"{vm_id}.pid"
                ),
                log_path=str(
                    self._configuration.storage.log_root / f"{vm_id}.log"
                ),
            )
        except VMContractError as exc:
            raise RegistryServiceError(ErrorCode.VALIDATION_ERROR) from exc

        operation_id = _operation_id(request)
        owned = {
            "disk_id": str(_resource_id(vm_id, 0, "disk")),
            "lease_ids": {
                role: str(_resource_id(vm_id, 0, f"port_{role}"))
                for role in ("agent", "display", "ssh")
            },
            "record": record.to_dict(),
        }
        intent = {
            "peer": {"gid": peer.gid, "pid": peer.pid, "uid": peer.uid},
            "request": request.to_dict(),
        }
        return _PreparedOperation(
            request=request,
            operation_id=operation_id,
            vm_id=vm_id,
            request_hash=request_digest,
            intent=_canonical_json_bytes(intent),
            owned_resources=_canonical_json_bytes(owned),
        )

    def _prepare_base_import(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
        request_digest: str,
    ) -> _PreparedOperation:
        self._require_unbound_request(request)
        payload = _exact_payload(
            request,
            required=frozenset({"manifest", "source_path"}),
        )
        try:
            manifest = ImageManifest.from_dict(payload["manifest"])
        except ImageManifestError:
            raise
        if self._registry.list_base_images(active_only=True):
            raise RegistryServiceError(ErrorCode.CONFLICT)
        for candidate in (
            self._configuration.storage.base_image,
            Path(f"{self._configuration.storage.base_image}.manifest.json"),
        ):
            try:
                os.lstat(candidate)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RegistryServiceError(
                    ErrorCode.RECOVERY_REQUIRED
                ) from exc
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        source_value = payload["source_path"]
        if not isinstance(source_value, str):
            raise RegistryServiceError(ErrorCode.VALIDATION_ERROR)
        try:
            source_path = canonical_path(
                source_value,
                "base-image import source",
                must_exist=True,
            )
        except ImageOwnershipError as exc:
            raise RegistryServiceError(ErrorCode.VALIDATION_ERROR) from exc
        operation_id = _operation_id(request)
        owned = {
            "base_path": os.fspath(self._configuration.storage.base_image),
            "manifest": manifest.to_dict(),
            "manifest_path": (
                f"{self._configuration.storage.base_image}.manifest.json"
            ),
            "source_path": os.fspath(source_path),
        }
        intent = {
            "peer": {"gid": peer.gid, "pid": peer.pid, "uid": peer.uid},
            "request": request.to_dict(),
        }
        return _PreparedOperation(
            request=request,
            operation_id=operation_id,
            vm_id=None,
            request_hash=request_digest,
            intent=_canonical_json_bytes(intent),
            owned_resources=_canonical_json_bytes(owned),
        )

    def _prepare_overlay_create(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
        request_digest: str,
    ) -> _PreparedOperation:
        self._require_bound_request(request)
        payload = _exact_payload(
            request,
            required=frozenset(
                {
                    "base_image_id",
                    "expected_revision",
                    "virtual_size_bytes",
                }
            ),
        )
        expected_revision = _strict_int(
            payload["expected_revision"],
            minimum=0,
            maximum=2**63 - 1,
        )
        virtual_size = _strict_int(
            payload["virtual_size_bytes"],
            minimum=1024**3,
            maximum=16 * 1024**4,
        )
        try:
            base_image_id = self._uuid_value(payload["base_image_id"])
        except RegistryServiceError as exc:
            raise RegistryServiceError(ErrorCode.VALIDATION_ERROR) from exc
        assert request.vm_id is not None
        assert request.generation is not None
        stored = self._registry.get_vm(request.vm_id)
        if (
            stored is None
            or stored.record.generation != request.generation
            or not stored.active
        ):
            raise RegistryServiceError(ErrorCode.PRECONDITION_FAILED)
        self._require_pre_runtime_owner(stored)
        if stored.revision != expected_revision:
            raise RegistryServiceError(ErrorCode.CONFLICT)
        base = self._registry.get_base_image(base_image_id)
        if base is None or not base.active:
            raise RegistryServiceError(ErrorCode.PRECONDITION_FAILED)
        try:
            manifest = ImageManifest.from_json(base.manifest)
        except ImageManifestError as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        disk_id = _resource_id(
            request.vm_id,
            request.generation,
            "disk",
        )
        disk = self._registry.get_disk(disk_id)
        if (
            disk is None
            or not disk.active
            or disk.vm_id != request.vm_id
            or disk.generation != request.generation
            or disk.path != stored.record.definition.disk_path
        ):
            raise RegistryServiceError(ErrorCode.PRECONDITION_FAILED)
        if disk.materialized:
            raise RegistryServiceError(ErrorCode.CONFLICT)
        operation_id = _operation_id(request)
        created_at = request.timestamp.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        owned = {
            "base_manifest": manifest.to_dict(),
            "disk_id": str(disk.disk_id),
            "expected_revision": expected_revision,
            "intent": {
                "base_image_id": str(base.image_id),
                "base_image_sha256": base.content_sha256,
                "created_at": created_at,
                "disk_id": str(disk.disk_id),
                "final_path": disk.path,
                "generation": request.generation,
                "operation_id": str(operation_id),
                "ownership_token": str(disk.ownership_token),
                "virtual_size_bytes": virtual_size,
                "vm_id": str(request.vm_id),
            },
        }
        intent = {
            "peer": {"gid": peer.gid, "pid": peer.pid, "uid": peer.uid},
            "request": request.to_dict(),
        }
        return _PreparedOperation(
            request=request,
            operation_id=operation_id,
            vm_id=request.vm_id,
            request_hash=request_digest,
            intent=_canonical_json_bytes(intent),
            owned_resources=_canonical_json_bytes(owned),
        )

    def _prepare_bound(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
        request_digest: str,
    ) -> _PreparedOperation:
        self._require_bound_request(request)
        payload = _exact_payload(
            request,
            required=frozenset({"expected_revision"}),
        )
        expected_revision = _strict_int(
            payload["expected_revision"],
            minimum=0,
            maximum=2**63 - 1,
        )
        assert request.vm_id is not None
        assert request.generation is not None
        stored = self._registry.get_vm(request.vm_id)
        if (
            stored is None
            or stored.record.generation != request.generation
            or not stored.active
        ):
            raise RegistryServiceError(ErrorCode.PRECONDITION_FAILED)
        self._require_pre_runtime_owner(stored)
        if stored.revision != expected_revision:
            raise RegistryServiceError(ErrorCode.CONFLICT)
        owned = {
            "disk_id": str(
                _resource_id(
                    request.vm_id,
                    request.generation,
                    "disk",
                )
            ),
            "expected_revision": expected_revision,
            "lease_ids": {
                role: str(
                    _resource_id(
                        request.vm_id,
                        request.generation,
                        f"port_{role}",
                    )
                )
                for role in ("agent", "display", "ssh")
            },
            "record": stored.record.to_dict(),
        }
        intent = {
            "peer": {"gid": peer.gid, "pid": peer.pid, "uid": peer.uid},
            "request": request.to_dict(),
        }
        return _PreparedOperation(
            request=request,
            operation_id=_operation_id(request),
            vm_id=request.vm_id,
            request_hash=request_digest,
            intent=_canonical_json_bytes(intent),
            owned_resources=_canonical_json_bytes(owned),
        )

    def _select_ports(self) -> VMPorts:
        used: set[int] = set()
        for stored in self._registry.list_vms(active_only=True):
            used.update(
                {
                    stored.record.ports.ssh,
                    stored.record.ports.agent,
                    stored.record.ports.vnc,
                }
            )

        def choose(values: range) -> int:
            for candidate in values:
                if candidate not in used:
                    used.add(candidate)
                    return candidate
            raise RegistryServiceError(ErrorCode.CONFLICT)

        return VMPorts(
            ssh=choose(self._configuration.network.ssh_ports.values()),
            agent=choose(self._configuration.network.agent_ports.values()),
            vnc=choose(self._configuration.network.vnc_ports.values()),
        )

    def _execute_resource_stage(
        self,
        prepared: _PreparedOperation,
    ) -> dict[str, object]:
        owned = _decode_json_object(prepared.owned_resources)
        operation = prepared.request.operation
        if operation == "registry.declare":
            result = self._stage_declare(prepared, owned)
        elif operation == "registry.lease":
            result = self._stage_lease(prepared, owned)
        elif operation == "registry.update":
            result = self._stage_update(prepared, owned)
        elif operation == "registry.cancel":
            result = self._stage_cancel(prepared, owned)
        elif operation == "storage.import_base":
            result = self._stage_base_import(prepared, owned)
        elif operation == "storage.create_overlay":
            result = self._stage_overlay_create(prepared, owned)
        else:
            raise RegistryServiceError(ErrorCode.OWNERSHIP_REJECTED)
        if operation not in _STORAGE_OPERATIONS:
            self._observe(prepared.operation_id, "resource_state_committed")
        return result

    def _stage_declare(
        self,
        prepared: _PreparedOperation,
        owned: dict[str, object],
    ) -> dict[str, object]:
        record = self._owned_record(owned, prepared.vm_id)
        disk_id = self._owned_uuid(owned, "disk_id")
        with self._registry.write_transaction() as transaction:
            stored = transaction.insert_vm(record, active=True)
            transaction.bind_operation_vm(
                prepared.operation_id,
                prepared.vm_id,
            )
            transaction.claim_disk(
                prepared.vm_id,
                record.definition.disk_path,
                disk_id=disk_id,
                generation=record.generation,
                ownership="managed",
                active=True,
                operation_id=prepared.operation_id,
            )
            result = self._stored_vm_result(stored, include_record=False)
            transaction.append_checkpoint(
                prepared.operation_id,
                "resource_state_committed",
                ordinal=1,
                payload=_canonical_json_bytes(result),
            )
        return result

    def _stage_lease(
        self,
        prepared: _PreparedOperation,
        owned: dict[str, object],
    ) -> dict[str, object]:
        expected = self._owned_revision(owned)
        lease_ids = self._owned_lease_ids(owned)
        with self._registry.write_transaction() as transaction:
            stored = transaction.get_vm(prepared.vm_id)
            self._require_revision(stored, prepared.request, expected)
            self._require_pre_runtime_owner(stored)
            record = stored.record
            leases = (
                transaction.acquire_port_lease(
                    prepared.vm_id,
                    "ssh",
                    record.ports.ssh,
                    host=self._configuration.network.bind_host,
                    lease_id=lease_ids["ssh"],
                    operation_id=prepared.operation_id,
                ),
                transaction.acquire_port_lease(
                    prepared.vm_id,
                    "agent",
                    record.ports.agent,
                    host=self._configuration.network.bind_host,
                    lease_id=lease_ids["agent"],
                    operation_id=prepared.operation_id,
                ),
                transaction.acquire_port_lease(
                    prepared.vm_id,
                    "display",
                    record.ports.vnc,
                    host=self._configuration.network.bind_host,
                    lease_id=lease_ids["display"],
                    operation_id=prepared.operation_id,
                ),
            )
            result = self._stored_vm_result(stored, include_record=True)
            result["leases"] = [
                {
                    "host": lease.host,
                    "lease_id": str(lease.lease_id),
                    "port": lease.port,
                    "role": lease.role,
                }
                for lease in leases
            ]
            transaction.append_checkpoint(
                prepared.operation_id,
                "resource_state_committed",
                ordinal=1,
                payload=_canonical_json_bytes(result),
            )
        return result

    def _stage_update(
        self,
        prepared: _PreparedOperation,
        owned: dict[str, object],
    ) -> dict[str, object]:
        expected = self._owned_revision(owned)
        record = self._owned_record(owned, prepared.vm_id)
        with self._registry.write_transaction() as transaction:
            current = transaction.get_vm(prepared.vm_id)
            self._require_revision(current, prepared.request, expected)
            self._require_pre_runtime_owner(current)
            stored = transaction.compare_and_swap_vm(
                prepared.vm_id,
                expected,
                record,
                active=True,
            )
            result = self._stored_vm_result(stored, include_record=False)
            transaction.append_checkpoint(
                prepared.operation_id,
                "resource_state_committed",
                ordinal=1,
                payload=_canonical_json_bytes(result),
            )
        return result

    def _stage_cancel(
        self,
        prepared: _PreparedOperation,
        owned: dict[str, object],
    ) -> dict[str, object]:
        expected = self._owned_revision(owned)
        record = self._owned_record(owned, prepared.vm_id)
        disk_id = self._owned_uuid(owned, "disk_id")
        lease_ids = self._owned_lease_ids(owned)
        with self._registry.write_transaction() as transaction:
            current = transaction.get_vm(prepared.vm_id)
            self._require_revision(current, prepared.request, expected)
            self._require_pre_runtime_owner(current)
            for lease_id in lease_ids.values():
                try:
                    transaction.release_port_lease(
                        lease_id,
                        operation_id=prepared.operation_id,
                    )
                except RegistryNotFoundError:
                    # A declaration can be canceled before its explicit lease
                    # operation.  Absence is the exact desired released state.
                    pass
            transaction.release_disk(
                disk_id,
                operation_id=prepared.operation_id,
            )
            stored = transaction.compare_and_swap_vm(
                prepared.vm_id,
                expected,
                record,
                active=False,
            )
            result = self._stored_vm_result(stored, include_record=False)
            transaction.append_checkpoint(
                prepared.operation_id,
                "resource_state_committed",
                ordinal=1,
                payload=_canonical_json_bytes(result),
            )
        return result

    def _require_image_store(self) -> ImageStore:
        store = self._image_store
        if store is None:
            store = ImageStore(self._configuration)
            self._image_store = store
        return store

    @staticmethod
    def _registered_base(
        record: BaseImageRecord,
        manifest: ImageManifest,
    ) -> RegisteredBase:
        if (
            manifest.canonical_bytes != record.manifest
            or manifest.image_id != record.image_id
            or manifest.sha256 != record.content_sha256
            or manifest.manifest_sha256 != record.manifest_sha256
            or manifest.size_bytes != record.byte_size
            or manifest.format != record.format
            or manifest.virtual_size_bytes != record.virtual_size_bytes
        ):
            raise ImageIntegrityError(
                "base-image registry facts are internally inconsistent"
            )
        return RegisteredBase(
            manifest=manifest,
            path=Path(record.path),
            device_id=record.device_id,
            inode=record.inode,
            file_size_bytes=record.file_size_bytes,
            allocated_size_bytes=record.allocated_size_bytes,
            mode=record.mode,
            qemu_img_version=record.qemu_img_version,
            chain_sha256=record.chain_sha256,
        )

    def _append_storage_checkpoint(
        self,
        prepared: _PreparedOperation,
        name: str,
        payload: Mapping[str, object],
    ) -> None:
        order = _STORAGE_CHECKPOINTS.get(prepared.request.operation)
        if order is None or name not in order:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        checkpoints = self._operation_checkpoints(prepared.operation_id)
        encoded = _canonical_json_bytes(dict(payload))
        existing = next(
            (item for item in checkpoints if item.name == name),
            None,
        )
        if existing is not None:
            if (
                existing.ordinal != order.index(name)
                or existing.payload != encoded
                or existing.payload_sha256 != sha256(encoded).hexdigest()
            ):
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
            return
        expected_ordinal = len(checkpoints)
        if expected_ordinal >= len(order) or order[expected_ordinal] != name:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        with self._registry.write_transaction() as transaction:
            transaction.append_checkpoint(
                prepared.operation_id,
                name,
                ordinal=expected_ordinal,
                payload=encoded,
            )
        self._observe(prepared.operation_id, name)

    def _storage_resume_state(
        self,
        operation_id: UUID,
    ) -> StorageResumeState:
        return StorageResumeState(
            tuple(
                checkpoint.name
                for checkpoint in self._operation_checkpoints(operation_id)
            )
        )

    def _stage_base_import(
        self,
        prepared: _PreparedOperation,
        owned: dict[str, object],
    ) -> dict[str, object]:
        if prepared.vm_id is not None or frozenset(owned) != frozenset(
            {"base_path", "manifest", "manifest_path", "source_path"}
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        try:
            manifest = ImageManifest.from_dict(owned["manifest"])
        except ImageManifestError as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        source_path = owned["source_path"]
        if not isinstance(source_path, str):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        store = self._require_image_store()
        if (
            owned["base_path"] != os.fspath(store.base_path)
            or owned["manifest_path"] != os.fspath(store.base_manifest_path)
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        evidence = store.import_base(
            prepared.operation_id,
            source_path,
            manifest,
            lambda name, payload: self._append_storage_checkpoint(
                prepared,
                name,
                payload,
            ),
            resume_state=self._storage_resume_state(prepared.operation_id),
        )
        with self._registry.write_transaction() as transaction:
            registered = transaction.register_base_image(
                evidence.manifest,
                evidence.path,
                device_id=evidence.device_id,
                inode=evidence.inode,
                file_size_bytes=evidence.file_size_bytes,
                allocated_size_bytes=evidence.allocated_size_bytes,
                mode=evidence.mode,
                qemu_img_version=evidence.qemu_img_version,
                chain_sha256=evidence.chain_sha256,
                operation_id=prepared.operation_id,
            )
        result = evidence.to_dict()
        result["registry_image_id"] = str(registered.image_id)
        self._append_storage_checkpoint(
            prepared,
            "base_registered",
            result,
        )
        return result

    @classmethod
    def _owned_overlay_intent(
        cls,
        value: object,
    ) -> OverlayCreateIntent:
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {
                "base_image_id",
                "base_image_sha256",
                "created_at",
                "disk_id",
                "final_path",
                "generation",
                "operation_id",
                "ownership_token",
                "virtual_size_bytes",
                "vm_id",
            }
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        try:
            return OverlayCreateIntent(
                operation_id=cls._uuid_value(value["operation_id"]),
                vm_id=cls._uuid_value(value["vm_id"]),
                generation=_strict_int(
                    value["generation"],
                    minimum=0,
                    maximum=2**63 - 1,
                ),
                disk_id=cls._uuid_value(value["disk_id"]),
                ownership_token=cls._uuid_value(value["ownership_token"]),
                final_path=Path(str(value["final_path"])),
                base_image_id=cls._uuid_value(value["base_image_id"]),
                base_image_sha256=str(value["base_image_sha256"]),
                virtual_size_bytes=_strict_int(
                    value["virtual_size_bytes"],
                    minimum=1024**3,
                    maximum=16 * 1024**4,
                ),
                created_at=str(value["created_at"]),
            )
        except (ImageOwnershipError, ValueError, TypeError) as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc

    def _stage_overlay_create(
        self,
        prepared: _PreparedOperation,
        owned: dict[str, object],
    ) -> dict[str, object]:
        if prepared.vm_id is None or frozenset(owned) != frozenset(
            {"base_manifest", "disk_id", "expected_revision", "intent"}
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        try:
            manifest = ImageManifest.from_dict(owned["base_manifest"])
        except ImageManifestError as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        intent = self._owned_overlay_intent(owned["intent"])
        if (
            intent.operation_id != prepared.operation_id
            or intent.vm_id != prepared.vm_id
            or str(intent.disk_id) != owned["disk_id"]
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        stored = self._registry.get_vm(prepared.vm_id)
        expected_revision = self._owned_revision(owned)
        self._require_revision(stored, prepared.request, expected_revision)
        base = self._registry.get_base_image(intent.base_image_id)
        if base is None or not base.active:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        store = self._require_image_store()
        registered_base = self._registered_base(base, manifest)
        evidence = store.create_overlay(
            intent,
            registered_base,
            lambda name, payload: self._append_storage_checkpoint(
                prepared,
                name,
                payload,
            ),
            resume_state=self._storage_resume_state(prepared.operation_id),
        )
        with self._registry.write_transaction() as transaction:
            materialized = transaction.materialize_disk(
                intent.disk_id,
                ownership_token=intent.ownership_token,
                base_image_id=intent.base_image_id,
                base_image_sha256=intent.base_image_sha256,
                virtual_size_bytes=intent.virtual_size_bytes,
                format="qcow2",
                device_id=evidence.device_id,
                inode=evidence.inode,
                file_size_bytes=evidence.file_size_bytes,
                allocated_size_bytes=evidence.allocated_size_bytes,
                chain_sha256=evidence.chain_sha256,
                marker_sha256=evidence.marker_sha256,
                qemu_img_version=evidence.qemu_img_version,
                operation_id=prepared.operation_id,
            )
        result = evidence.to_dict()
        result["materialized"] = materialized.materialized
        result["marker_sha256"] = evidence.marker_sha256
        self._append_storage_checkpoint(
            prepared,
            "disk_materialized",
            result,
        )
        return result

    @staticmethod
    def _require_revision(
        stored: StoredVM | None,
        request: ControlRequest,
        expected: int,
    ) -> None:
        if (
            stored is None
            or stored.record.vm_id != request.vm_id
            or stored.record.generation != request.generation
        ):
            raise RegistryNotFoundError("VM generation does not exist")
        if not stored.active:
            raise RegistryConflictError("VM generation is inactive")
        if stored.revision != expected:
            raise RegistryRevisionConflict(
                "VM revision compare-and-swap failed"
            )

    @staticmethod
    def _owned_record(
        owned: dict[str, object],
        expected_vm_id: UUID,
    ) -> VMRecord:
        try:
            record = VMRecord.from_dict(owned["record"])
        except (KeyError, VMContractError, TypeError, ValueError) as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        if record.vm_id != expected_vm_id:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        return record

    @staticmethod
    def _owned_uuid(owned: dict[str, object], key: str) -> UUID:
        try:
            value = owned[key]
            if not isinstance(value, str):
                raise ValueError(key)
            parsed = UUID(value)
        except (KeyError, ValueError, TypeError) as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        if parsed.int == 0:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        return parsed

    @staticmethod
    def _owned_revision(owned: dict[str, object]) -> int:
        try:
            return _strict_int(
                owned["expected_revision"],
                minimum=0,
                maximum=2**63 - 1,
            )
        except KeyError as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc

    @classmethod
    def _owned_lease_ids(
        cls,
        owned: dict[str, object],
    ) -> dict[str, UUID]:
        leases = owned.get("lease_ids")
        if not isinstance(leases, dict) or frozenset(leases) != frozenset(
            {"agent", "display", "ssh"}
        ):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        return {
            role: cls._uuid_value(value)
            for role, value in leases.items()
        }

    @staticmethod
    def _uuid_value(value: object) -> UUID:
        if not isinstance(value, str):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        if parsed.int == 0:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        return parsed

    def _find_idempotency(self, key: str) -> IdempotencyRecord | None:
        finder = getattr(self._registry, "find_idempotency", None)
        if callable(finder):
            return finder(key)
        with self._registry.write_transaction() as transaction:
            return transaction.find_idempotency(key)

    def _replay_idempotent(
        self,
        request: ControlRequest,
        existing: IdempotencyRecord,
        request_digest: str,
    ) -> ControlResponse | ErrorEnvelope:
        if existing.request_hash != request_digest:
            raise RegistryServiceError(ErrorCode.CONFLICT)
        if existing.status not in _TERMINAL_STATUSES:
            self.recover_operation(existing.operation_id)
            refreshed = self._find_idempotency(existing.key)
            if refreshed is None:
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
            existing = refreshed
        result = _decode_json_object(existing.result)
        if existing.status == "completed":
            return _success_envelope(
                request,
                result,
                operation_id=existing.operation_id,
            )
        error_code = result.get("error_code")
        try:
            code = ErrorCode(error_code)
        except (TypeError, ValueError) as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        return _error_envelope(
            request,
            code,
            operation_id=existing.operation_id,
        )

    def _record_expected_failure(
        self,
        prepared: _PreparedOperation,
        code: ErrorCode,
    ) -> None:
        result = {"error_code": code.value}
        ordinal = len(self._operation_checkpoints(prepared.operation_id))
        with self._registry.write_transaction() as transaction:
            transaction.append_checkpoint(
                prepared.operation_id,
                "operation_failed",
                ordinal=ordinal,
                payload=_canonical_json_bytes(result),
            )
            transaction.set_operation_status(
                prepared.operation_id,
                "failed",
                result=_canonical_json_bytes(result),
            )
        self._observe(prepared.operation_id, "operation_failed")

    def _finalize(
        self,
        prepared: _PreparedOperation,
        result: dict[str, object],
    ) -> None:
        encoded = _canonical_json_bytes(result)
        ordinal = len(self._operation_checkpoints(prepared.operation_id))
        if prepared.request.operation in _STORAGE_OPERATIONS:
            order = _STORAGE_CHECKPOINTS[prepared.request.operation]
            if ordinal >= len(order) or order[ordinal] != "completion_observed":
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        with self._registry.write_transaction() as transaction:
            transaction.append_checkpoint(
                prepared.operation_id,
                "completion_observed",
                ordinal=ordinal,
                payload=encoded,
            )
            transaction.set_operation_status(
                prepared.operation_id,
                "completed",
                result=encoded,
            )
        self._observe(prepared.operation_id, "completion_observed")

    def _observe(self, operation_id: UUID, checkpoint: str) -> None:
        observer = self._checkpoint_observer
        if observer is not None:
            observer(operation_id, checkpoint)

    def recover_incomplete_operations(self) -> int:
        """Resume every nonterminal P2 operation before accepting mutations."""

        with self._mutation_lock:
            lister = getattr(self._registry, "list_operations", None)
            if not callable(lister):
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
            operations = lister()
            recovered = 0
            for operation in operations:
                if (
                    operation.kind in _STORAGE_OPERATIONS
                    and operation.status != "completed"
                ) or operation.status not in _TERMINAL_STATUSES:
                    self._recover_operation_record(operation)
                    recovered += 1
            remaining = lister(incomplete_only=True)
            unresolved_storage = tuple(
                operation
                for operation in lister()
                if operation.kind in _STORAGE_OPERATIONS
                and operation.status != "completed"
            )
            if remaining or unresolved_storage:
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
            return recovered

    def reconcile_storage_authority(
        self,
        *,
        exclude_live_vm_ids: Collection[UUID] = (),
    ) -> int:
        """Re-observe every P3 image owner before accepting control work.

        Journal recovery proves that every durable mutation reached a terminal
        state.  It does not prove that an administrator, filesystem fault, or
        stale mount did not replace the bytes while the daemon was stopped.
        This pass therefore treats the registry as the expected authority and
        independently measures the configured base plus every non-live
        materialized disk.  Runtime recovery must first prove which writable
        overlays are live and pass those VM IDs here; this method never invokes
        ``qemu-img`` against an excluded live overlay.  A declared-but-
        unmaterialized active disk must own no physical image or marker.

        The image backend remains lazy when the registry contains no physical
        storage authority.  That preserves P2-only operation on hosts where
        ``qemu-img`` is intentionally absent while still failing closed as soon
        as a base or overlay has been registered.
        """

        try:
            excluded = frozenset(exclude_live_vm_ids)
        except TypeError as exc:
            raise TypeError(
                "exclude_live_vm_ids must be a finite UUID collection"
            ) from exc
        if any(
            not isinstance(vm_id, UUID) or vm_id.int == 0
            for vm_id in excluded
        ):
            raise TypeError(
                "exclude_live_vm_ids must contain only nonnil UUID values"
            )

        with self._mutation_lock:
            try:
                bases = self._registry.list_base_images(active_only=False)
                disks = self._registry.list_disks(active_only=False)
                materialized = tuple(disk for disk in disks if disk.materialized)
                reconciled_disks = tuple(
                    disk for disk in materialized if disk.vm_id not in excluded
                )
                materialized_paths = {disk.path for disk in materialized}

                for disk in disks:
                    if (
                        disk.active
                        and not disk.materialized
                        and disk.path not in materialized_paths
                    ):
                        self._require_unmaterialized_path_absent(Path(disk.path))

                referenced_base_ids = {
                    disk.base_image_id
                    for disk in materialized
                    if disk.base_image_id is not None
                }
                authoritative_bases = tuple(
                    base
                    for base in bases
                    if base.active or base.image_id in referenced_base_ids
                )
                if not authoritative_bases:
                    for candidate in (
                        self._configuration.storage.base_image,
                        Path(
                            f"{self._configuration.storage.base_image}"
                            ".manifest.json"
                        ),
                    ):
                        try:
                            os.lstat(candidate)
                        except FileNotFoundError:
                            continue
                        except OSError as exc:
                            raise ImageOwnershipError(
                                "unregistered base-image path cannot be observed"
                            ) from exc
                        raise ImageOwnershipError(
                            "configured base-image path has no registry authority"
                        )
                if not authoritative_bases and not reconciled_disks:
                    return 0

                store = self._require_image_store()
                base_authorities: dict[UUID, RegisteredBase] = {}
                for base in authoritative_bases:
                    manifest = ImageManifest.from_json(base.manifest)
                    authority = self._registered_base(base, manifest)
                    store.verify_registered_base(authority)
                    base_authorities[base.image_id] = authority

                for disk in reconciled_disks:
                    if (
                        disk.base_image_id is None
                        or disk.base_image_sha256 is None
                        or disk.virtual_size_bytes is None
                        or disk.format != "qcow2"
                        or disk.device_id is None
                        or disk.inode is None
                        or disk.file_size_bytes is None
                        or disk.allocated_size_bytes is None
                        or disk.chain_sha256 is None
                        or disk.marker_sha256 is None
                        or disk.qemu_img_version is None
                        or disk.materialized_by_operation_id is None
                    ):
                        raise ImageIntegrityError(
                            "materialized disk lacks complete physical authority"
                        )
                    base_authority = base_authorities.get(disk.base_image_id)
                    if base_authority is None:
                        raise ImageIntegrityError(
                            "materialized disk references an unverified base image"
                        )
                    runtime_observation = (
                        self._registry.latest_disk_runtime_observation(
                            disk.disk_id
                        )
                    )
                    store.verify_registered_overlay(
                        RegisteredOverlay(
                            vm_id=disk.vm_id,
                            generation=disk.generation,
                            disk_id=disk.disk_id,
                            ownership_token=disk.ownership_token,
                            operation_id=disk.materialized_by_operation_id,
                            path=Path(disk.path),
                            base_image_id=disk.base_image_id,
                            base_image_sha256=disk.base_image_sha256,
                            virtual_size_bytes=disk.virtual_size_bytes,
                            device_id=disk.device_id,
                            inode=disk.inode,
                            file_size_bytes=disk.file_size_bytes,
                            allocated_size_bytes=disk.allocated_size_bytes,
                            mode=0o600,
                            qemu_img_version=disk.qemu_img_version,
                            chain_sha256=disk.chain_sha256,
                            marker_sha256=disk.marker_sha256,
                        ),
                        base_authority,
                        mutable_runtime=runtime_observation is not None,
                    )
                return len(authoritative_bases) + len(reconciled_disks)
            except (
                ImageError,
                RegistryConflictError,
                RegistryIntegrityError,
                OSError,
            ) as exc:
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc

    @staticmethod
    def _require_unmaterialized_path_absent(path: Path) -> None:
        """Reject any physical claimant for an active reservation."""

        for candidate in (path, Path(f"{path}.owner.json")):
            try:
                os.lstat(candidate)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ImageOwnershipError(
                    "unmaterialized disk path cannot be observed"
                ) from exc
            raise ImageOwnershipError(
                "unmaterialized disk reservation has a physical claimant"
            )

    def recover_operation(self, operation_id: UUID) -> None:
        """Resume one exact operation from durable intent and checkpoints."""

        with self._mutation_lock:
            getter = getattr(self._registry, "get_operation", None)
            if not callable(getter):
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
            operation = getter(operation_id)
            if operation is None:
                raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
            if operation.status not in _TERMINAL_STATUSES:
                self._recover_operation_record(operation)

    def _recover_operation_record(self, operation: OperationRecord) -> None:
        if operation.kind not in _MUTATING_OPERATIONS:
            self._mark_recovery_required(operation)
            return
        checkpoints = self._operation_checkpoints(operation.operation_id)
        if not checkpoints or checkpoints[0].name != "intent_persisted":
            self._mark_recovery_required(operation)
            return
        if tuple(item.ordinal for item in checkpoints) != tuple(
            range(len(checkpoints))
        ):
            self._mark_recovery_required(operation)
            return
        intent = _decode_json_object(operation.intent)
        request_payload = intent.get("request")
        if not isinstance(request_payload, dict):
            self._mark_recovery_required(operation)
            return
        try:
            request = ControlRequest.from_dict(request_payload)
        except Exception as exc:
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED) from exc
        expected_operation_id = _operation_id(request)
        if (
            expected_operation_id != operation.operation_id
            or _request_hash(request) != operation.request_hash
            or str(request.request_id) != operation.idempotency_key
            or request.operation != operation.kind
        ):
            self._mark_recovery_required(operation)
            return
        if request.operation == "registry.declare":
            vm_id: UUID | None = _declared_vm_id(request)
        elif request.operation == "storage.import_base":
            vm_id = None
        else:
            vm_id = request.vm_id
        if vm_id is None and request.operation != "storage.import_base":
            self._mark_recovery_required(operation)
            return
        prepared = _PreparedOperation(
            request=request,
            operation_id=operation.operation_id,
            vm_id=vm_id,
            request_hash=operation.request_hash,
            intent=operation.intent,
            owned_resources=operation.owned_resources,
        )

        latest = checkpoints[-1]
        if request.operation in _STORAGE_OPERATIONS:
            order = _STORAGE_CHECKPOINTS[request.operation]
            names = tuple(item.name for item in checkpoints)
            if names != order[: len(names)]:
                self._mark_recovery_required(operation)
                return
            if latest.name == "completion_observed":
                result = _decode_json_object(latest.payload)
                with self._registry.write_transaction() as transaction:
                    transaction.set_operation_status(
                        operation.operation_id,
                        "completed",
                        result=_canonical_json_bytes(result),
                    )
                return
            try:
                result = self._execute_resource_stage(prepared)
                self._finalize(prepared, result)
            except (
                ImageError,
                RegistryConflictError,
                RegistryNotFoundError,
                RegistryRevisionConflict,
                RegistryServiceError,
            ):
                refreshed = self._registry.get_operation(operation.operation_id)
                if refreshed is not None and refreshed.status != "completed":
                    self._set_storage_recovery_required(refreshed)
                elif refreshed is not None:
                    raise
            return
        if latest.ordinal == 0:
            try:
                result = self._execute_resource_stage(prepared)
            except (
                RegistryConflictError,
                RegistryNotFoundError,
                RegistryRevisionConflict,
                RegistryServiceError,
            ):
                self._mark_recovery_required(operation)
                return
            self._finalize(prepared, result)
            return
        if latest.ordinal == 1:
            if latest.name == "operation_failed":
                result = _decode_json_object(latest.payload)
                with self._registry.write_transaction() as transaction:
                    transaction.set_operation_status(
                        operation.operation_id,
                        "failed",
                        result=_canonical_json_bytes(result),
                    )
                return
            if latest.name != "resource_state_committed":
                self._mark_recovery_required(operation)
                return
            result = _decode_json_object(latest.payload)
            self._finalize(prepared, result)
            return
        if latest.ordinal == 2 and latest.name == "completion_observed":
            result = _decode_json_object(latest.payload)
            with self._registry.write_transaction() as transaction:
                transaction.set_operation_status(
                    operation.operation_id,
                    "completed",
                    result=_canonical_json_bytes(result),
                )
            return
        self._mark_recovery_required(operation)

    def _operation_checkpoints(
        self,
        operation_id: UUID,
    ) -> tuple[OperationCheckpoint, ...]:
        lister = getattr(self._registry, "list_checkpoints", None)
        if not callable(lister):
            raise RegistryServiceError(ErrorCode.RECOVERY_REQUIRED)
        return lister(operation_id)

    def _mark_recovery_required(self, operation: OperationRecord) -> None:
        result = {"error_code": ErrorCode.RECOVERY_REQUIRED.value}
        checkpoints = self._operation_checkpoints(operation.operation_id)
        if (
            operation.status == "recovery_required"
            and checkpoints
            and checkpoints[-1].name == "recovery_blocked"
        ):
            return
        with self._registry.write_transaction() as transaction:
            current = getattr(transaction, "get_operation", lambda _: None)(
                operation.operation_id
            )
            latest = (
                operation.latest_checkpoint
                if current is None
                else current.latest_checkpoint
            )
            ordinal = 0 if latest is None else latest + 1
            transaction.append_checkpoint(
                operation.operation_id,
                "recovery_blocked",
                ordinal=ordinal,
                payload=_canonical_json_bytes(result),
            )
            transaction.set_operation_status(
                operation.operation_id,
                "recovery_required",
                result=_canonical_json_bytes(result),
            )
        self._observe(operation.operation_id, "recovery_blocked")

    def _set_storage_recovery_required(
        self,
        operation: OperationRecord,
    ) -> None:
        """Retain the last physical checkpoint so storage can resume exactly."""

        if operation.kind not in _STORAGE_OPERATIONS:
            self._mark_recovery_required(operation)
            return
        result = {"error_code": ErrorCode.RECOVERY_REQUIRED.value}
        with self._registry.write_transaction() as transaction:
            transaction.set_operation_status(
                operation.operation_id,
                "recovery_required",
                result=_canonical_json_bytes(result),
            )
        self._observe(operation.operation_id, "recovery_required")


__all__ = [
    "CheckpointObserver",
    "RegistryMutationService",
    "RegistryServiceError",
]
