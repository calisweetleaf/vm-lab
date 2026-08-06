"""Versioned control envelopes shared by host, guest, and operator clients.

Source: PLAN.md P1-003/P1-004 and docs/decisions/0005-errors-and-exit-codes.md
Composed: 2026-08-05
Purpose: Defines exact request, success, and bounded failure envelopes without
    granting authority to the bounded operation payload itself.
IO: None.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from ._validation import (
    PUBLIC_WIRE_MAX_BYTES,
    PUBLIC_WIRE_MAX_DEPTH,
    PUBLIC_WIRE_MAX_ITEMS,
    PUBLIC_WIRE_MAX_STRING,
    ProtocolValidationError,
    canonical_json,
    decode_json_object,
    encode_datetime,
    freeze_machine_json,
    freeze_json,
    require_bool,
    require_datetime,
    require_int,
    require_str,
    require_uuid,
    strict_object,
    thaw_json,
)
from .version import (
    CURRENT_PROTOCOL_VERSION,
    SCHEMA_VERSION,
    SemanticVersion,
    ensure_supported_protocol_version,
    require_schema_version,
)


_OPERATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*"
)
_MAX_GENERATION: Final[int] = 2**63 - 1
_MAX_OPERATION_LENGTH: Final[int] = 64
_MAX_EVIDENCE_ITEMS: Final[int] = 16
_MAX_FIXED_PUBLIC_TEXT: Final[int] = 256


class ErrorCategory(str, Enum):
    """Stable, machine-actionable public failure categories."""

    VALIDATION = "validation"
    PROTOCOL = "protocol"
    UNAVAILABLE = "unavailable"
    POLICY = "policy"
    OWNERSHIP = "ownership"
    CONFLICT = "conflict"
    PRECONDITION = "precondition"
    EXECUTION = "execution"
    RECOVERY = "recovery"
    INTERNAL = "internal"


class PublicExitCode(IntEnum):
    """ADR-0005 public CLI exit classes.

    These values classify a machine error for a future CLI adapter.  They do
    not report process success here and do not replace a consumer's observed
    exit status.
    """

    SUCCESS = 0
    DOMAIN_FAILURE = 1
    INVALID_INPUT = 2
    UNAVAILABLE = 3
    POLICY_REJECTION = 4
    RECOVERY_REQUIRED = 5


class ErrorCode(str, Enum):
    """Closed, provider-neutral public error-code registry."""

    INVALID_REQUEST = "INVALID_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    POLICY_REJECTED = "POLICY_REJECTED"
    OWNERSHIP_REJECTED = "OWNERSHIP_REJECTED"
    CONFLICT = "CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorEvidenceKind(str, Enum):
    """Closed diagnostic observation kinds permitted on public errors."""

    DEADLINE = "deadline"
    IDENTITY_MISMATCH = "identity_mismatch"
    STATE_OBSERVATION = "state_observation"
    POLICY_DECISION = "policy_decision"
    RECOVERY_RECORD = "recovery_record"
    DEPENDENCY_STATUS = "dependency_status"


@dataclass(frozen=True, slots=True)
class ErrorEvidenceSpec:
    """Fixed public summary assigned to one evidence kind."""

    summary: str


ERROR_EVIDENCE_REGISTRY: Final[
    Mapping[ErrorEvidenceKind, ErrorEvidenceSpec]
] = MappingProxyType(
    {
        ErrorEvidenceKind.DEADLINE: ErrorEvidenceSpec(
            "A required deadline expired."
        ),
        ErrorEvidenceKind.IDENTITY_MISMATCH: ErrorEvidenceSpec(
            "Observed identity did not match expected identity."
        ),
        ErrorEvidenceKind.STATE_OBSERVATION: ErrorEvidenceSpec(
            "A bounded state observation is available."
        ),
        ErrorEvidenceKind.POLICY_DECISION: ErrorEvidenceSpec(
            "A policy decision record is available."
        ),
        ErrorEvidenceKind.RECOVERY_RECORD: ErrorEvidenceSpec(
            "A recovery record is available."
        ),
        ErrorEvidenceKind.DEPENDENCY_STATUS: ErrorEvidenceSpec(
            "A dependency status record is available."
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ErrorCodeSpec:
    """Complete immutable public presentation assigned to one error code."""

    category: ErrorCategory
    exit_code: PublicExitCode
    detail: str
    retryable: bool
    remediation: tuple[str, ...]


ERROR_CODE_REGISTRY: Final[Mapping[ErrorCode, ErrorCodeSpec]] = MappingProxyType(
    {
        ErrorCode.INVALID_REQUEST: ErrorCodeSpec(
            ErrorCategory.VALIDATION,
            PublicExitCode.INVALID_INPUT,
            "The request is invalid.",
            False,
            ("Correct the request before submitting it again.",),
        ),
        ErrorCode.VALIDATION_ERROR: ErrorCodeSpec(
            ErrorCategory.VALIDATION,
            PublicExitCode.INVALID_INPUT,
            "The request failed validation.",
            False,
            ("Correct the invalid request fields before retrying.",),
        ),
        ErrorCode.PROTOCOL_ERROR: ErrorCodeSpec(
            ErrorCategory.PROTOCOL,
            PublicExitCode.INVALID_INPUT,
            "The protocol message is invalid or unsupported.",
            False,
            ("Use a supported protocol transcript before retrying.",),
        ),
        ErrorCode.AGENT_UNAVAILABLE: ErrorCodeSpec(
            ErrorCategory.UNAVAILABLE,
            PublicExitCode.UNAVAILABLE,
            "The authenticated guest agent is unavailable.",
            True,
            ("Restore authenticated guest-agent readiness and retry.",),
        ),
        ErrorCode.UNAVAILABLE: ErrorCodeSpec(
            ErrorCategory.UNAVAILABLE,
            PublicExitCode.UNAVAILABLE,
            "A required runtime dependency is unavailable.",
            True,
            ("Restore the required dependency and retry.",),
        ),
        ErrorCode.POLICY_REJECTED: ErrorCodeSpec(
            ErrorCategory.POLICY,
            PublicExitCode.POLICY_REJECTION,
            "The request is rejected by policy.",
            False,
            ("Change the request to satisfy policy before retrying.",),
        ),
        ErrorCode.OWNERSHIP_REJECTED: ErrorCodeSpec(
            ErrorCategory.OWNERSHIP,
            PublicExitCode.POLICY_REJECTION,
            "The request is outside the current ownership boundary.",
            False,
            ("Route the request to the component that owns the mutation.",),
        ),
        ErrorCode.CONFLICT: ErrorCodeSpec(
            ErrorCategory.CONFLICT,
            PublicExitCode.POLICY_REJECTION,
            "The request conflicts with current durable state.",
            False,
            ("Reconcile the conflicting state before retrying.",),
        ),
        ErrorCode.PRECONDITION_FAILED: ErrorCodeSpec(
            ErrorCategory.PRECONDITION,
            PublicExitCode.POLICY_REJECTION,
            "A required precondition is not satisfied.",
            True,
            ("Establish the required precondition before retrying.",),
        ),
        ErrorCode.EXECUTION_FAILED: ErrorCodeSpec(
            ErrorCategory.EXECUTION,
            PublicExitCode.DOMAIN_FAILURE,
            "The accepted operation failed.",
            False,
            ("Inspect the referenced diagnostic record before retrying.",),
        ),
        ErrorCode.RECOVERY_REQUIRED: ErrorCodeSpec(
            ErrorCategory.RECOVERY,
            PublicExitCode.RECOVERY_REQUIRED,
            "The accepted operation entered recovery.",
            False,
            ("Inspect recovery evidence before any further mutation.",),
        ),
        ErrorCode.INTERNAL_ERROR: ErrorCodeSpec(
            ErrorCategory.INTERNAL,
            PublicExitCode.DOMAIN_FAILURE,
            "The operation failed because of an internal error.",
            False,
            ("Inspect the referenced diagnostic record before retrying.",),
        ),
    }
)


def _error_code(value: object) -> ErrorCode:
    try:
        return value if isinstance(value, ErrorCode) else ErrorCode(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("error code is not registered") from exc


def _error_evidence_kind(value: object) -> ErrorEvidenceKind:
    try:
        return (
            value
            if isinstance(value, ErrorEvidenceKind)
            else ErrorEvidenceKind(value)
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError(
            "error evidence kind is not registered"
        ) from exc


def _error_category(value: object) -> ErrorCategory:
    try:
        return value if isinstance(value, ErrorCategory) else ErrorCategory(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("error category is unsupported") from exc


def _public_exit_code(value: object) -> PublicExitCode:
    try:
        return (
            value
            if isinstance(value, PublicExitCode)
            else PublicExitCode(require_int(value, "exit_code", minimum=0, maximum=5))
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("exit_code is unsupported") from exc


def _optional_uuid(value: object, label: str) -> UUID | None:
    if value is None:
        return None
    return require_uuid(value, label)


def _normalize_identity(
    *,
    schema_version: object,
    protocol_version: object,
    request_id: object,
    correlation_id: object,
    operation_id: object = None,
    vm_id: object = None,
    boot_id: object = None,
    generation: object = None,
    timestamp: object,
) -> tuple[
    int,
    SemanticVersion,
    UUID,
    UUID,
    UUID | None,
    UUID | None,
    UUID | None,
    int | None,
    datetime,
]:
    """Normalize common envelope identity without creating missing evidence."""

    normalized_schema = require_schema_version(schema_version)
    normalized_protocol = ensure_supported_protocol_version(protocol_version)
    normalized_request = require_uuid(request_id, "request_id")
    normalized_correlation = require_uuid(correlation_id, "correlation_id")
    normalized_operation = _optional_uuid(operation_id, "operation_id")
    normalized_vm = _optional_uuid(vm_id, "vm_id")
    normalized_boot = _optional_uuid(boot_id, "boot_id")
    if generation is None:
        normalized_generation = None
    else:
        normalized_generation = require_int(
            generation,
            "generation",
            minimum=0,
            maximum=_MAX_GENERATION,
        )
    if (normalized_vm is None) != (normalized_generation is None):
        raise ProtocolValidationError(
            "vm_id and generation must either both be present or both be null"
        )
    if normalized_boot is not None and normalized_vm is None:
        raise ProtocolValidationError("boot_id requires vm_id and generation")
    normalized_timestamp = require_datetime(timestamp, "timestamp")
    return (
        normalized_schema,
        normalized_protocol,
        normalized_request,
        normalized_correlation,
        normalized_operation,
        normalized_vm,
        normalized_boot,
        normalized_generation,
        normalized_timestamp,
    )


def _plain_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, (dict, MappingProxyType)):
        raise ProtocolValidationError(f"{label} must be an object")
    plain = thaw_json(value)
    if not isinstance(plain, dict):
        raise ProtocolValidationError(f"{label} must be an object")
    return plain


def _freeze_object(
    value: object,
    label: str,
    *,
    max_items: int = 64,
    max_string: int = 4_096,
) -> Mapping[str, object]:
    """Freeze bounded authenticated operation data.

    This function enforces JSON bounds and canonical ASCII machine keys only.
    The data is not a public/log-safe projection: operation-specific schemas
    own its semantics, and logging must never expose the bag wholesale.
    """

    plain = _plain_object(value, label)
    frozen = freeze_machine_json(
        plain,
        label,
        max_depth=4,
        max_items=max_items,
        max_string=max_string,
    )
    if not isinstance(frozen, MappingProxyType):
        raise AssertionError("Frozen JSON object did not produce a mapping")
    return frozen


def _envelope_wire_json(value: object, label: str) -> str:
    """Encode one control envelope under the exact shared 64-KiB contract."""

    return canonical_json(
        value,
        label,
        max_bytes=PUBLIC_WIRE_MAX_BYTES,
        max_depth=PUBLIC_WIRE_MAX_DEPTH,
        max_items=PUBLIC_WIRE_MAX_ITEMS,
        max_string=PUBLIC_WIRE_MAX_STRING,
    )


def _decode_envelope_wire(value: object, label: str) -> dict[str, object]:
    """Decode one control envelope under matching construction/encoder limits."""

    return decode_json_object(
        value,
        label,
        max_bytes=PUBLIC_WIRE_MAX_BYTES,
        max_depth=PUBLIC_WIRE_MAX_DEPTH,
        max_items=PUBLIC_WIRE_MAX_ITEMS,
        max_string=PUBLIC_WIRE_MAX_STRING,
    )


def _assert_envelope_wire(value: object, label: str) -> None:
    """Reject constructed envelopes that their matching decoder cannot accept."""

    _envelope_wire_json(value, label)


def _identity_dict(
    *,
    schema_version: int,
    protocol_version: SemanticVersion,
    request_id: UUID,
    correlation_id: UUID,
    operation_id: UUID | None,
    vm_id: UUID | None,
    boot_id: UUID | None,
    generation: int | None,
    timestamp: datetime,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "protocol_version": str(protocol_version),
        "request_id": str(request_id),
        "correlation_id": str(correlation_id),
        "operation_id": str(operation_id) if operation_id is not None else None,
        "vm_id": str(vm_id) if vm_id is not None else None,
        "boot_id": str(boot_id) if boot_id is not None else None,
        "generation": generation,
        "timestamp": encode_datetime(timestamp),
    }


@dataclass(frozen=True, slots=True)
class ControlRequest:
    """One exact, bounded request delivered to a control operation owner.

    ``payload`` is authenticated operation data, not a public/log-safe
    projection.  Its owning operation schema defines semantics; transport and
    logging layers must not expose this generic bag.
    """

    schema_version: int
    protocol_version: SemanticVersion
    request_id: UUID
    correlation_id: UUID
    vm_id: UUID | None
    boot_id: UUID | None
    generation: int | None
    timestamp: datetime
    operation: str
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        (
            schema_version,
            protocol_version,
            request_id,
            correlation_id,
            _,
            vm_id,
            boot_id,
            generation,
            timestamp,
        ) = _normalize_identity(
            schema_version=self.schema_version,
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            vm_id=self.vm_id,
            boot_id=self.boot_id,
            generation=self.generation,
            timestamp=self.timestamp,
        )
        operation = require_str(
            self.operation,
            "operation",
            min_length=1,
            max_length=_MAX_OPERATION_LENGTH,
            pattern=_OPERATION_PATTERN,
        )
        payload = _freeze_object(self.payload, "payload")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "protocol_version", protocol_version)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "vm_id", vm_id)
        object.__setattr__(self, "boot_id", boot_id)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "payload", payload)
        _assert_envelope_wire(self.to_dict(), "control request")

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible request mapping."""

        payload = _identity_dict(
            schema_version=self.schema_version,
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            operation_id=None,
            vm_id=self.vm_id,
            boot_id=self.boot_id,
            generation=self.generation,
            timestamp=self.timestamp,
        )
        payload.pop("operation_id")
        payload.update(
            {
                "operation": self.operation,
                "payload": thaw_json(self.payload),
            }
        )
        return payload

    def to_json(self) -> str:
        return _envelope_wire_json(self.to_dict(), "control request")

    @classmethod
    def from_dict(cls, payload: object) -> "ControlRequest":
        row = strict_object(
            payload,
            {
                "schema_version",
                "protocol_version",
                "request_id",
                "correlation_id",
                "vm_id",
                "boot_id",
                "generation",
                "timestamp",
                "operation",
                "payload",
            },
            label="control request",
        )
        return cls(
            schema_version=row["schema_version"],
            protocol_version=row["protocol_version"],
            request_id=row["request_id"],
            correlation_id=row["correlation_id"],
            vm_id=row["vm_id"],
            boot_id=row["boot_id"],
            generation=row["generation"],
            timestamp=row["timestamp"],
            operation=row["operation"],
            payload=_plain_object(row["payload"], "payload"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ControlRequest":
        return cls.from_dict(_decode_envelope_wire(payload, "control request"))


@dataclass(frozen=True, slots=True)
class ControlResponse:
    """One successful response; failures use :class:`ErrorEnvelope`.

    ``result`` is authenticated operation data, not a public/log-safe
    projection.  Its owning operation schema defines semantics; transport and
    logging layers must not expose this generic bag.
    """

    schema_version: int
    protocol_version: SemanticVersion
    request_id: UUID
    correlation_id: UUID
    operation_id: UUID | None
    vm_id: UUID | None
    boot_id: UUID | None
    generation: int | None
    timestamp: datetime
    result: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = _normalize_identity(
            schema_version=self.schema_version,
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            operation_id=self.operation_id,
            vm_id=self.vm_id,
            boot_id=self.boot_id,
            generation=self.generation,
            timestamp=self.timestamp,
        )
        result = _freeze_object(self.result, "result")
        for name, value in zip(
            (
                "schema_version",
                "protocol_version",
                "request_id",
                "correlation_id",
                "operation_id",
                "vm_id",
                "boot_id",
                "generation",
                "timestamp",
            ),
            normalized,
            strict=True,
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "result", result)
        _assert_envelope_wire(self.to_dict(), "control response")

    @property
    def ok(self) -> bool:
        return True

    def to_dict(self) -> dict[str, object]:
        payload = _identity_dict(
            schema_version=self.schema_version,
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            operation_id=self.operation_id,
            vm_id=self.vm_id,
            boot_id=self.boot_id,
            generation=self.generation,
            timestamp=self.timestamp,
        )
        payload.update({"ok": True, "result": thaw_json(self.result)})
        return payload

    def to_json(self) -> str:
        return _envelope_wire_json(self.to_dict(), "control response")

    @classmethod
    def from_dict(cls, payload: object) -> "ControlResponse":
        row = strict_object(
            payload,
            {
                "schema_version",
                "protocol_version",
                "request_id",
                "correlation_id",
                "operation_id",
                "vm_id",
                "boot_id",
                "generation",
                "timestamp",
                "ok",
                "result",
            },
            label="control response",
        )
        if not require_bool(row["ok"], "ok"):
            raise ProtocolValidationError("Successful control response requires ok=true")
        return cls(
            schema_version=row["schema_version"],
            protocol_version=row["protocol_version"],
            request_id=row["request_id"],
            correlation_id=row["correlation_id"],
            operation_id=row["operation_id"],
            vm_id=row["vm_id"],
            boot_id=row["boot_id"],
            generation=row["generation"],
            timestamp=row["timestamp"],
            result=_plain_object(row["result"], "result"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ControlResponse":
        return cls.from_dict(_decode_envelope_wire(payload, "control response"))


@dataclass(frozen=True, slots=True)
class DiagnosticReference:
    """Rigid locator for one private diagnostic record.

    The ``diag:`` UUID is an identifier, not a filesystem path, URL, secret,
    or proof of authorization.  The future diagnostic store owns resolution
    and access control.
    """

    diagnostic_id: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic_id",
            require_uuid(self.diagnostic_id, "diagnostic reference UUID"),
        )

    @classmethod
    def parse(cls, value: object) -> "DiagnosticReference":
        if isinstance(value, cls):
            return value
        text = require_str(
            value,
            "evidence.reference",
            min_length=41,
            max_length=41,
        )
        if not text.startswith("diag:"):
            raise ProtocolValidationError(
                "evidence.reference must use diag:<non-nil UUID>"
            )
        return cls(require_uuid(text[5:], "diagnostic reference UUID"))

    def __str__(self) -> str:
        return f"diag:{self.diagnostic_id}"


@dataclass(frozen=True, slots=True)
class ErrorEvidence:
    """One closed-kind pointer to a private diagnostic record."""

    kind: ErrorEvidenceKind
    reference: DiagnosticReference

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _error_evidence_kind(self.kind))
        object.__setattr__(
            self,
            "reference",
            DiagnosticReference.parse(self.reference),
        )

    @property
    def summary(self) -> str:
        return ERROR_EVIDENCE_REGISTRY[self.kind].summary

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "summary": self.summary,
            "reference": str(self.reference),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ErrorEvidence":
        row = strict_object(
            payload,
            {"kind", "summary", "reference"},
            label="error evidence",
        )
        kind = _error_evidence_kind(row["kind"])
        expected_summary = ERROR_EVIDENCE_REGISTRY[kind].summary
        summary = require_str(
            row["summary"],
            "evidence.summary",
            min_length=1,
            max_length=_MAX_FIXED_PUBLIC_TEXT,
        )
        if summary != expected_summary:
            raise ProtocolValidationError(
                f"evidence kind {kind.value} requires its fixed public summary"
            )
        return cls(kind=kind, reference=DiagnosticReference.parse(row["reference"]))


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """ADR-0005 public failure with no caller-controlled prose.

    Code, category, exit class, detail, retryability, remediation, and evidence
    summaries come from closed registries.  P1 ``details`` is always empty.
    Raw diagnostics remain outside this envelope and are referenced only by a
    rigid diagnostic UUID.
    """

    schema_version: int
    protocol_version: SemanticVersion
    request_id: UUID
    correlation_id: UUID
    operation_id: UUID | None
    vm_id: UUID | None
    boot_id: UUID | None
    generation: int | None
    timestamp: datetime
    code: ErrorCode
    category: ErrorCategory
    exit_code: PublicExitCode
    evidence: tuple[ErrorEvidence, ...] = ()
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = _normalize_identity(
            schema_version=self.schema_version,
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            operation_id=self.operation_id,
            vm_id=self.vm_id,
            boot_id=self.boot_id,
            generation=self.generation,
            timestamp=self.timestamp,
        )
        code = _error_code(self.code)
        category = _error_category(self.category)
        exit_code = _public_exit_code(self.exit_code)
        specification = ERROR_CODE_REGISTRY[code]
        if category is not specification.category:
            raise ProtocolValidationError(
                f"error code {code.value} requires category "
                f"{specification.category.value}"
            )
        if exit_code is not specification.exit_code:
            raise ProtocolValidationError(
                f"error code {code.value} requires exit_code "
                f"{int(specification.exit_code)}"
            )
        if isinstance(self.evidence, (str, bytes)) or not isinstance(
            self.evidence, Sequence
        ):
            raise ProtocolValidationError("evidence must be an array")
        if len(self.evidence) > _MAX_EVIDENCE_ITEMS:
            raise ProtocolValidationError(
                f"evidence contains more than {_MAX_EVIDENCE_ITEMS} observations"
            )
        evidence: tuple[ErrorEvidence, ...] = tuple(
            item if isinstance(item, ErrorEvidence) else ErrorEvidence.from_dict(item)
            for item in self.evidence
        )
        plain_details = _plain_object(self.details, "details")
        if plain_details:
            raise ProtocolValidationError(
                "details must be empty for every P1 error code"
            )
        details = freeze_json(
            plain_details,
            "details",
            max_depth=0,
            max_items=0,
            max_string=0,
        )
        if not isinstance(details, MappingProxyType):
            raise AssertionError("Frozen error details did not produce a mapping")
        for name, value in zip(
            (
                "schema_version",
                "protocol_version",
                "request_id",
                "correlation_id",
                "operation_id",
                "vm_id",
                "boot_id",
                "generation",
                "timestamp",
            ),
            normalized,
            strict=True,
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "exit_code", exit_code)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "details", details)
        _assert_envelope_wire(self.to_dict(), "error envelope")

    @property
    def specification(self) -> ErrorCodeSpec:
        return ERROR_CODE_REGISTRY[self.code]

    @property
    def ok(self) -> bool:
        return False

    @property
    def detail(self) -> str:
        return self.specification.detail

    @property
    def retryable(self) -> bool:
        return self.specification.retryable

    @property
    def remediation(self) -> tuple[str, ...]:
        return self.specification.remediation

    def to_dict(self) -> dict[str, object]:
        payload = _identity_dict(
            schema_version=self.schema_version,
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            operation_id=self.operation_id,
            vm_id=self.vm_id,
            boot_id=self.boot_id,
            generation=self.generation,
            timestamp=self.timestamp,
        )
        payload.update(
            {
                "ok": False,
                "code": self.code.value,
                "category": self.category.value,
                "exit_code": int(self.exit_code),
                "detail": self.detail,
                "retryable": self.retryable,
                "remediation": list(self.remediation),
                "evidence": [item.to_dict() for item in self.evidence],
                "details": thaw_json(self.details),
            }
        )
        return payload

    def to_json(self) -> str:
        return _envelope_wire_json(self.to_dict(), "error envelope")

    @classmethod
    def from_dict(cls, payload: object) -> "ErrorEnvelope":
        row = strict_object(
            payload,
            {
                "schema_version",
                "protocol_version",
                "request_id",
                "correlation_id",
                "operation_id",
                "vm_id",
                "boot_id",
                "generation",
                "timestamp",
                "ok",
                "code",
                "category",
                "exit_code",
                "detail",
                "retryable",
                "remediation",
                "evidence",
                "details",
            },
            label="error envelope",
        )
        if require_bool(row["ok"], "ok"):
            raise ProtocolValidationError("Error envelope requires ok=false")
        code = _error_code(row["code"])
        specification = ERROR_CODE_REGISTRY[code]
        detail = require_str(
            row["detail"],
            "detail",
            min_length=1,
            max_length=_MAX_FIXED_PUBLIC_TEXT,
        )
        if detail != specification.detail:
            raise ProtocolValidationError(
                f"error code {code.value} requires its fixed public detail"
            )
        retryable = require_bool(row["retryable"], "retryable")
        if retryable is not specification.retryable:
            raise ProtocolValidationError(
                f"error code {code.value} requires retryable="
                f"{str(specification.retryable).lower()}"
            )
        remediation = row["remediation"]
        evidence = row["evidence"]
        if not isinstance(remediation, list):
            raise ProtocolValidationError("remediation must be an array")
        remediation_steps = tuple(
            require_str(
                step,
                f"remediation[{index}]",
                min_length=1,
                max_length=_MAX_FIXED_PUBLIC_TEXT,
            )
            for index, step in enumerate(remediation)
        )
        if remediation_steps != specification.remediation:
            raise ProtocolValidationError(
                f"error code {code.value} requires its fixed remediation"
            )
        if not isinstance(evidence, list):
            raise ProtocolValidationError("evidence must be an array")
        return cls(
            schema_version=row["schema_version"],
            protocol_version=row["protocol_version"],
            request_id=row["request_id"],
            correlation_id=row["correlation_id"],
            operation_id=row["operation_id"],
            vm_id=row["vm_id"],
            boot_id=row["boot_id"],
            generation=row["generation"],
            timestamp=row["timestamp"],
            code=code,
            category=row["category"],
            exit_code=row["exit_code"],
            evidence=tuple(ErrorEvidence.from_dict(item) for item in evidence),
            details=_plain_object(row["details"], "details"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ErrorEnvelope":
        return cls.from_dict(_decode_envelope_wire(payload, "error envelope"))


def default_protocol_version() -> SemanticVersion:
    """Return the immutable current version for explicit constructor defaults."""

    return CURRENT_PROTOCOL_VERSION


def default_schema_version() -> int:
    return SCHEMA_VERSION


__all__ = [
    "ControlRequest",
    "ControlResponse",
    "ERROR_CODE_REGISTRY",
    "ERROR_EVIDENCE_REGISTRY",
    "DiagnosticReference",
    "ErrorCategory",
    "ErrorCode",
    "ErrorCodeSpec",
    "ErrorEnvelope",
    "ErrorEvidence",
    "ErrorEvidenceKind",
    "ErrorEvidenceSpec",
    "PublicExitCode",
    "default_protocol_version",
    "default_schema_version",
]
