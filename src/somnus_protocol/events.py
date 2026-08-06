"""Strict progress and lifecycle event envelopes.

Source: PLAN.md P1-003/P1-009 and the target somnus_protocol package boundary
Composed: 2026-08-05
Purpose: Carries ordered, correlated observations without owning their source,
    persistence, or transport.
IO: None.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from uuid import UUID

from ._validation import (
    ProtocolValidationError,
    require_int,
    require_str,
    require_uuid,
    strict_object,
    thaw_json,
)
from .control import (
    _assert_envelope_wire,
    _decode_envelope_wire,
    _envelope_wire_json,
    _freeze_object,
    _identity_dict,
    _normalize_identity,
    _plain_object,
)
from .version import SemanticVersion


_EVENT_TYPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*"
)
_MAX_EVENT_TYPE_LENGTH: Final[int] = 96
_MAX_EVENT_SEQUENCE: Final[int] = 2**63 - 1


@dataclass(frozen=True, slots=True)
class ControlEvent:
    """One event in an operation-scoped strictly ordered sequence.

    ``sequence`` is unique and strictly increasing within each non-null
    ``operation_id`` domain.  This immutable value validates the scope and the
    scalar; the future persistent event owner must enforce monotonicity across
    stored records.  ``data`` is authenticated operation data, not a
    public/log-safe projection; its event-specific schema owns semantics and
    logging layers must not expose the generic bag.
    """

    schema_version: int
    protocol_version: SemanticVersion
    event_id: UUID
    request_id: UUID
    correlation_id: UUID
    operation_id: UUID
    vm_id: UUID | None
    boot_id: UUID | None
    generation: int | None
    timestamp: datetime
    event_type: str
    sequence: int
    data: Mapping[str, object] = field(default_factory=dict)

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
        if normalized[4] is None:
            raise ProtocolValidationError(
                "ControlEvent.operation_id is required for sequence ordering"
            )
        event_id = require_uuid(self.event_id, "event_id")
        event_type = require_str(
            self.event_type,
            "event_type",
            min_length=1,
            max_length=_MAX_EVENT_TYPE_LENGTH,
            pattern=_EVENT_TYPE_PATTERN,
        )
        sequence = require_int(
            self.sequence,
            "sequence",
            minimum=0,
            maximum=_MAX_EVENT_SEQUENCE,
        )
        data = _freeze_object(self.data, "event data")
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
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "data", data)
        _assert_envelope_wire(self.to_dict(), "control event")

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
                "event_id": str(self.event_id),
                "event_type": self.event_type,
                "sequence": self.sequence,
                "data": thaw_json(self.data),
            }
        )
        return payload

    def to_json(self) -> str:
        return _envelope_wire_json(self.to_dict(), "control event")

    @classmethod
    def from_dict(cls, payload: object) -> "ControlEvent":
        row = strict_object(
            payload,
            {
                "schema_version",
                "protocol_version",
                "event_id",
                "request_id",
                "correlation_id",
                "operation_id",
                "vm_id",
                "boot_id",
                "generation",
                "timestamp",
                "event_type",
                "sequence",
                "data",
            },
            label="control event",
        )
        return cls(
            schema_version=row["schema_version"],
            protocol_version=row["protocol_version"],
            event_id=row["event_id"],
            request_id=row["request_id"],
            correlation_id=row["correlation_id"],
            operation_id=row["operation_id"],
            vm_id=row["vm_id"],
            boot_id=row["boot_id"],
            generation=row["generation"],
            timestamp=row["timestamp"],
            event_type=row["event_type"],
            sequence=row["sequence"],
            data=_plain_object(row["data"], "event data"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ControlEvent":
        return cls.from_dict(_decode_envelope_wire(payload, "control event"))


__all__ = ["ControlEvent"]
