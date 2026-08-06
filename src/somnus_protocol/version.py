"""Semantic protocol and persisted-schema version authority.

Source: PLAN.md P1-002/P1-007 and GATE-P1
Composed: 2026-08-05
Purpose: Keeps wire negotiation separate from persisted-record migrations so
    neither layer guesses compatibility from the other's version.
IO: None.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
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
    require_int,
    require_str,
    require_uuid,
    strict_object,
)


_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})"
)
_MAX_VERSION_COMPONENT: Final[int] = 65_535
_OFFER_FINGERPRINT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"sha256:[0-9a-f]{64}",
    re.ASCII,
)


class ProtocolVersionError(ProtocolValidationError):
    """Base class for malformed or unsupported protocol versions."""


class UnsupportedProtocolVersionError(ProtocolVersionError):
    """Raised when a valid version is outside the locally supported range."""


class ProtocolNegotiationError(ProtocolVersionError):
    """Raised when two explicit protocol ranges do not overlap."""


class SchemaVersionError(ProtocolValidationError):
    """Raised when a record names an unsupported schema version."""


@dataclass(frozen=True, slots=True, order=True)
class SemanticVersion:
    """A bounded release version in canonical ``major.minor.patch`` form."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        try:
            for label, value in (
                ("major", self.major),
                ("minor", self.minor),
                ("patch", self.patch),
            ):
                require_int(
                    value,
                    f"protocol version {label}",
                    minimum=0,
                    maximum=_MAX_VERSION_COMPONENT,
                )
        except ProtocolValidationError as exc:
            raise ProtocolVersionError(str(exc)) from exc

    @classmethod
    def parse(cls, value: object, label: str = "protocol_version") -> "SemanticVersion":
        """Parse one canonical release version without coercion."""

        if isinstance(value, cls):
            return value
        try:
            text = require_str(
                value,
                label,
                min_length=5,
                max_length=17,
                pattern=_VERSION_PATTERN,
            )
        except ProtocolValidationError as exc:
            raise ProtocolVersionError(str(exc)) from exc
        major, minor, patch = (int(component) for component in text.split("."))
        try:
            return cls(major, minor, patch)
        except ProtocolValidationError as exc:
            raise ProtocolVersionError(str(exc)) from exc

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class ProtocolVersionRange:
    """One contiguous compatible range within a single protocol major."""

    minimum: SemanticVersion
    maximum: SemanticVersion

    def __post_init__(self) -> None:
        minimum = SemanticVersion.parse(self.minimum, "minimum protocol version")
        maximum = SemanticVersion.parse(self.maximum, "maximum protocol version")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        if minimum.major != maximum.major:
            raise ProtocolVersionError(
                "A protocol version range cannot cross semantic major versions"
            )
        if minimum > maximum:
            raise ProtocolVersionError(
                "Minimum protocol version cannot exceed maximum protocol version"
            )

    @classmethod
    def from_values(
        cls,
        minimum: SemanticVersion | str,
        maximum: SemanticVersion | str,
    ) -> "ProtocolVersionRange":
        """Build a range from canonical strings or version objects."""

        return cls(
            SemanticVersion.parse(minimum, "minimum protocol version"),
            SemanticVersion.parse(maximum, "maximum protocol version"),
        )

    def contains(self, version: SemanticVersion | str) -> bool:
        """Return whether one version is explicitly inside this range."""

        parsed = SemanticVersion.parse(version)
        return (
            parsed.major == self.minimum.major
            and self.minimum <= parsed <= self.maximum
        )

    def negotiate(self, peer: "ProtocolVersionRange") -> SemanticVersion:
        """Select the highest version explicitly shared with ``peer``."""

        if not isinstance(peer, ProtocolVersionRange):
            raise TypeError("peer must be a ProtocolVersionRange")
        if self.minimum.major != peer.minimum.major:
            raise ProtocolNegotiationError(
                "Protocol major versions do not match: "
                f"local={self.minimum.major}, peer={peer.minimum.major}"
            )
        shared_minimum = max(self.minimum, peer.minimum)
        shared_maximum = min(self.maximum, peer.maximum)
        if shared_minimum > shared_maximum:
            raise ProtocolNegotiationError(
                "Protocol version ranges do not overlap: "
                f"local={self.minimum}-{self.maximum}, "
                f"peer={peer.minimum}-{peer.maximum}"
            )
        return shared_maximum


CURRENT_PROTOCOL_VERSION: Final[SemanticVersion] = SemanticVersion(1, 0, 0)
MINIMUM_PROTOCOL_VERSION: Final[SemanticVersion] = SemanticVersion(1, 0, 0)
SUPPORTED_PROTOCOL_RANGE: Final[ProtocolVersionRange] = ProtocolVersionRange(
    MINIMUM_PROTOCOL_VERSION,
    CURRENT_PROTOCOL_VERSION,
)
PROTOCOL_VERSION: Final[str] = str(CURRENT_PROTOCOL_VERSION)

SCHEMA_VERSION: Final[int] = 1
CURRENT_SCHEMA_VERSION: Final[int] = SCHEMA_VERSION


def negotiate_protocol_version(
    peer_minimum: ProtocolVersionRange | SemanticVersion | str,
    peer_maximum: SemanticVersion | str | None = None,
    *,
    local: ProtocolVersionRange = SUPPORTED_PROTOCOL_RANGE,
) -> SemanticVersion:
    """Negotiate the highest version present in both explicit ranges.

    A caller may pass a ready :class:`ProtocolVersionRange`, or pass peer
    minimum and maximum values separately.  Advertising only one "current"
    version is deliberately insufficient because it does not prove downgrade
    support.
    """

    if not isinstance(local, ProtocolVersionRange):
        raise TypeError("local must be a ProtocolVersionRange")
    if isinstance(peer_minimum, ProtocolVersionRange):
        if peer_maximum is not None:
            raise TypeError("peer_maximum must be omitted when passing a version range")
        peer = peer_minimum
    else:
        if peer_maximum is None:
            raise TypeError("peer_maximum is required when passing peer_minimum")
        peer = ProtocolVersionRange.from_values(peer_minimum, peer_maximum)
    return local.negotiate(peer)


def ensure_supported_protocol_version(
    value: object,
    label: str = "protocol_version",
) -> SemanticVersion:
    """Parse one version and require explicit local support."""

    parsed = SemanticVersion.parse(value, label)
    if not SUPPORTED_PROTOCOL_RANGE.contains(parsed):
        raise UnsupportedProtocolVersionError(
            f"{label} {parsed} is unsupported; "
            f"supported range is "
            f"{SUPPORTED_PROTOCOL_RANGE.minimum}-{SUPPORTED_PROTOCOL_RANGE.maximum}"
        )
    return parsed


def require_schema_version(
    value: object,
    label: str = "schema_version",
) -> int:
    """Require the one current schema version without future-version fallback."""

    try:
        parsed = require_int(value, label, minimum=0, maximum=2**31 - 1)
    except ProtocolValidationError as exc:
        raise SchemaVersionError(str(exc)) from exc
    if parsed != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{label} {parsed} is unsupported; expected {SCHEMA_VERSION}"
        )
    return parsed


@dataclass(frozen=True, slots=True)
class ProtocolCapabilities:
    """One immutable protocol-range offer in an exact negotiation transcript.

    The canonical SHA-256 fingerprint detects transcript mismatch; it does not
    authenticate the offer.  Authentication and replay storage belong to the
    future transport owner.
    """

    schema_version: int
    negotiation_id: UUID
    minimum_protocol_version: SemanticVersion
    maximum_protocol_version: SemanticVersion

    def __post_init__(self) -> None:
        schema_version = require_schema_version(
            self.schema_version,
            "capabilities schema_version",
        )
        negotiation_id = require_uuid(
            self.negotiation_id,
            "capabilities negotiation_id",
        )
        supported = ProtocolVersionRange.from_values(
            self.minimum_protocol_version,
            self.maximum_protocol_version,
        )
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "negotiation_id", negotiation_id)
        object.__setattr__(
            self,
            "minimum_protocol_version",
            supported.minimum,
        )
        object.__setattr__(
            self,
            "maximum_protocol_version",
            supported.maximum,
        )
        self._wire_json()

    @property
    def supported_range(self) -> ProtocolVersionRange:
        return ProtocolVersionRange(
            self.minimum_protocol_version,
            self.maximum_protocol_version,
        )

    @classmethod
    def local_offer(cls, negotiation_id: UUID | str) -> "ProtocolCapabilities":
        """Return the current package's offer for a caller-owned transcript ID."""

        return cls(
            schema_version=SCHEMA_VERSION,
            negotiation_id=negotiation_id,
            minimum_protocol_version=SUPPORTED_PROTOCOL_RANGE.minimum,
            maximum_protocol_version=SUPPORTED_PROTOCOL_RANGE.maximum,
        )

    def _fingerprint_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "negotiation_id": str(self.negotiation_id),
            "minimum_protocol_version": str(self.minimum_protocol_version),
            "maximum_protocol_version": str(self.maximum_protocol_version),
        }

    @property
    def offer_fingerprint(self) -> str:
        canonical = canonical_json(
            self._fingerprint_payload(),
            "protocol offer fingerprint input",
            max_bytes=PUBLIC_WIRE_MAX_BYTES,
            max_depth=PUBLIC_WIRE_MAX_DEPTH,
            max_items=PUBLIC_WIRE_MAX_ITEMS,
            max_string=PUBLIC_WIRE_MAX_STRING,
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    def to_dict(self) -> dict[str, object]:
        payload = self._fingerprint_payload()
        payload["offer_fingerprint"] = self.offer_fingerprint
        return payload

    def _wire_json(self) -> str:
        return canonical_json(
            self.to_dict(),
            "protocol capabilities",
            max_bytes=PUBLIC_WIRE_MAX_BYTES,
            max_depth=PUBLIC_WIRE_MAX_DEPTH,
            max_items=PUBLIC_WIRE_MAX_ITEMS,
            max_string=PUBLIC_WIRE_MAX_STRING,
        )

    def to_json(self) -> str:
        return self._wire_json()

    @classmethod
    def from_dict(cls, payload: object) -> "ProtocolCapabilities":
        row = strict_object(
            payload,
            {
                "schema_version",
                "negotiation_id",
                "minimum_protocol_version",
                "maximum_protocol_version",
                "offer_fingerprint",
            },
            label="protocol capabilities",
        )
        offer = cls(
            schema_version=row["schema_version"],
            negotiation_id=row["negotiation_id"],
            minimum_protocol_version=row["minimum_protocol_version"],
            maximum_protocol_version=row["maximum_protocol_version"],
        )
        fingerprint = require_str(
            row["offer_fingerprint"],
            "offer_fingerprint",
            min_length=71,
            max_length=71,
            pattern=_OFFER_FINGERPRINT_PATTERN,
        )
        if fingerprint != offer.offer_fingerprint:
            raise ProtocolNegotiationError(
                "Protocol capability offer fingerprint does not match its fields"
            )
        return offer

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ProtocolCapabilities":
        return cls.from_dict(
            decode_json_object(
                payload,
                "protocol capabilities",
                max_bytes=PUBLIC_WIRE_MAX_BYTES,
                max_depth=PUBLIC_WIRE_MAX_DEPTH,
                max_items=PUBLIC_WIRE_MAX_ITEMS,
                max_string=PUBLIC_WIRE_MAX_STRING,
            )
        )

    def negotiate(
        self,
        responder_offer: "ProtocolCapabilities",
    ) -> "ProtocolNegotiation":
        """Commit this initiator offer and one responder offer to a selection."""

        return ProtocolNegotiation.select(self, responder_offer)


@dataclass(frozen=True, slots=True)
class ProtocolNegotiation:
    """Claimed selection committing to both exact offers.

    A decoded value is not accepted merely because its schema and hashes are
    valid.  ``accept`` requires both original offers and rechecks transcript
    identity, both fingerprints, and the highest shared version.  Hashes detect
    mismatch but do not provide authentication.
    """

    schema_version: int
    negotiation_id: UUID
    initiator_offer_fingerprint: str
    responder_offer_fingerprint: str
    selected_protocol_version: SemanticVersion

    def __post_init__(self) -> None:
        schema_version = require_schema_version(
            self.schema_version,
            "negotiation schema_version",
        )
        negotiation_id = require_uuid(
            self.negotiation_id,
            "negotiation_id",
        )
        initiator = require_str(
            self.initiator_offer_fingerprint,
            "initiator_offer_fingerprint",
            min_length=71,
            max_length=71,
            pattern=_OFFER_FINGERPRINT_PATTERN,
        )
        responder = require_str(
            self.responder_offer_fingerprint,
            "responder_offer_fingerprint",
            min_length=71,
            max_length=71,
            pattern=_OFFER_FINGERPRINT_PATTERN,
        )
        selected = SemanticVersion.parse(
            self.selected_protocol_version,
            "selected protocol version",
        )
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "negotiation_id", negotiation_id)
        object.__setattr__(self, "initiator_offer_fingerprint", initiator)
        object.__setattr__(self, "responder_offer_fingerprint", responder)
        object.__setattr__(self, "selected_protocol_version", selected)
        self._wire_json()

    @classmethod
    def select(
        cls,
        initiator_offer: ProtocolCapabilities,
        responder_offer: ProtocolCapabilities,
    ) -> "ProtocolNegotiation":
        """Select the highest version shared by two exact same-ID offers."""

        if not isinstance(initiator_offer, ProtocolCapabilities):
            raise TypeError("initiator_offer must be ProtocolCapabilities")
        if not isinstance(responder_offer, ProtocolCapabilities):
            raise TypeError("responder_offer must be ProtocolCapabilities")
        if initiator_offer.negotiation_id != responder_offer.negotiation_id:
            raise ProtocolNegotiationError(
                "Protocol offers have different negotiation_id values"
            )
        selected = initiator_offer.supported_range.negotiate(
            responder_offer.supported_range
        )
        return cls(
            schema_version=initiator_offer.schema_version,
            negotiation_id=initiator_offer.negotiation_id,
            initiator_offer_fingerprint=initiator_offer.offer_fingerprint,
            responder_offer_fingerprint=responder_offer.offer_fingerprint,
            selected_protocol_version=selected,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "negotiation_id": str(self.negotiation_id),
            "initiator_offer_fingerprint": self.initiator_offer_fingerprint,
            "responder_offer_fingerprint": self.responder_offer_fingerprint,
            "selected_protocol_version": str(self.selected_protocol_version),
        }

    def _wire_json(self) -> str:
        return canonical_json(
            self.to_dict(),
            "protocol negotiation",
            max_bytes=PUBLIC_WIRE_MAX_BYTES,
            max_depth=PUBLIC_WIRE_MAX_DEPTH,
            max_items=PUBLIC_WIRE_MAX_ITEMS,
            max_string=PUBLIC_WIRE_MAX_STRING,
        )

    def to_json(self) -> str:
        return self._wire_json()

    @classmethod
    def from_dict(cls, payload: object) -> "ProtocolNegotiation":
        row = strict_object(
            payload,
            {
                "schema_version",
                "negotiation_id",
                "initiator_offer_fingerprint",
                "responder_offer_fingerprint",
                "selected_protocol_version",
            },
            label="protocol negotiation",
        )
        return cls(
            schema_version=row["schema_version"],
            negotiation_id=row["negotiation_id"],
            initiator_offer_fingerprint=row["initiator_offer_fingerprint"],
            responder_offer_fingerprint=row["responder_offer_fingerprint"],
            selected_protocol_version=row["selected_protocol_version"],
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ProtocolNegotiation":
        return cls.from_dict(
            decode_json_object(
                payload,
                "protocol negotiation",
                max_bytes=PUBLIC_WIRE_MAX_BYTES,
                max_depth=PUBLIC_WIRE_MAX_DEPTH,
                max_items=PUBLIC_WIRE_MAX_ITEMS,
                max_string=PUBLIC_WIRE_MAX_STRING,
            )
        )

    def accept(
        self,
        initiator_offer: ProtocolCapabilities,
        responder_offer: ProtocolCapabilities,
    ) -> SemanticVersion:
        """Accept only the exact highest-selection transcript for both offers."""

        if not isinstance(initiator_offer, ProtocolCapabilities):
            raise TypeError("initiator_offer must be ProtocolCapabilities")
        if not isinstance(responder_offer, ProtocolCapabilities):
            raise TypeError("responder_offer must be ProtocolCapabilities")
        if (
            self.negotiation_id != initiator_offer.negotiation_id
            or self.negotiation_id != responder_offer.negotiation_id
        ):
            raise ProtocolNegotiationError(
                "Negotiation transcript ID does not match both original offers"
            )
        if self.initiator_offer_fingerprint != initiator_offer.offer_fingerprint:
            raise ProtocolNegotiationError(
                "Initiator offer fingerprint does not match the original offer"
            )
        if self.responder_offer_fingerprint != responder_offer.offer_fingerprint:
            raise ProtocolNegotiationError(
                "Responder offer fingerprint does not match the original offer"
            )
        expected = initiator_offer.supported_range.negotiate(
            responder_offer.supported_range
        )
        if self.selected_protocol_version != expected:
            raise ProtocolNegotiationError(
                "Negotiation did not select the highest shared protocol version"
            )
        return self.selected_protocol_version


__all__ = [
    "CURRENT_PROTOCOL_VERSION",
    "CURRENT_SCHEMA_VERSION",
    "MINIMUM_PROTOCOL_VERSION",
    "PROTOCOL_VERSION",
    "ProtocolCapabilities",
    "ProtocolNegotiation",
    "ProtocolNegotiationError",
    "ProtocolVersionError",
    "ProtocolVersionRange",
    "SCHEMA_VERSION",
    "SUPPORTED_PROTOCOL_RANGE",
    "SchemaVersionError",
    "SemanticVersion",
    "UnsupportedProtocolVersionError",
    "ensure_supported_protocol_version",
    "negotiate_protocol_version",
    "require_schema_version",
]
