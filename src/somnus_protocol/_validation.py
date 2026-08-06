"""Strict, dependency-free validation for Somnus protocol values.

Source: PLAN.md Phase P1 strict serialization and import-isolation contract
Composed: 2026-08-05
Purpose: Gives every protocol family one exact-key, strict-scalar, bounded-JSON,
    immutable-container, and canonical-encoding authority.
IO: None. This module performs no filesystem, network, process, environment,
    or logging work.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Final, Pattern
from uuid import UUID


DEFAULT_MAX_JSON_DEPTH: Final[int] = 4
DEFAULT_MAX_JSON_ITEMS: Final[int] = 64
DEFAULT_MAX_JSON_STRING: Final[int] = 4_096
DEFAULT_MAX_JSON_BYTES: Final[int] = 65_536

# One canonical control-wire contract. Nested schema fields may impose stricter
# limits, but no control/event/negotiation envelope may exceed these bounds.
PUBLIC_WIRE_MAX_BYTES: Final[int] = 65_536
PUBLIC_WIRE_MAX_DEPTH: Final[int] = 6
PUBLIC_WIRE_MAX_ITEMS: Final[int] = 256
PUBLIC_WIRE_MAX_STRING: Final[int] = 4_096

_MIN_JSON_INTEGER: Final[int] = -(2**63)
_MAX_JSON_INTEGER: Final[int] = 2**63 - 1
_MACHINE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*",
    re.ASCII,
)


class ProtocolValidationError(ValueError):
    """Raised when decoded data cannot be represented by the protocol."""


def _schema_keys(values: Iterable[str], label: str) -> frozenset[str]:
    """Normalize a developer-supplied key collection and reject ambiguity."""

    if isinstance(values, (str, bytes)):
        raise TypeError(f"{label} keys must be an iterable of strings, not a string")
    items = tuple(values)
    if any(not isinstance(item, str) or not item for item in items):
        raise TypeError(f"{label} keys must be non-empty strings")
    if len(items) != len(set(items)):
        raise TypeError(f"{label} keys contain duplicates")
    return frozenset(items)


def strict_object(
    payload: object,
    required: Iterable[str],
    optional: Iterable[str] = (),
    label: str = "object",
) -> dict[str, object]:
    """Return a shallow copy after enforcing one exact object schema.

    Required keys must all be present.  Keys outside the required and optional
    sets are rejected instead of being silently discarded.
    """

    if not isinstance(payload, dict):
        raise ProtocolValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in payload):
        raise ProtocolValidationError(f"{label} keys must be strings")
    required_keys = _schema_keys(required, f"{label} required")
    optional_keys = _schema_keys(optional, f"{label} optional")
    overlap = required_keys & optional_keys
    if overlap:
        raise TypeError(f"{label} schema repeats keys: {', '.join(sorted(overlap))}")
    actual_keys = frozenset(payload)
    missing = required_keys - actual_keys
    if missing:
        raise ProtocolValidationError(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )
    unknown = actual_keys - required_keys - optional_keys
    if unknown:
        raise ProtocolValidationError(
            f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return dict(payload)


def require_str(
    value: object,
    label: str,
    *,
    min_length: int = 1,
    max_length: int = DEFAULT_MAX_JSON_STRING,
    pattern: str | Pattern[str] | None = None,
    allow_control_characters: bool = False,
) -> str:
    """Require a bounded Unicode string and optionally a full-match pattern."""

    if not isinstance(min_length, int) or isinstance(min_length, bool) or min_length < 0:
        raise TypeError("min_length must be a non-negative integer")
    if (
        not isinstance(max_length, int)
        or isinstance(max_length, bool)
        or max_length < min_length
    ):
        raise TypeError("max_length must be an integer greater than or equal to min_length")
    if not isinstance(value, str):
        raise ProtocolValidationError(f"{label} must be a string")
    if len(value) < min_length or len(value) > max_length:
        raise ProtocolValidationError(
            f"{label} length must be between {min_length} and {max_length} characters"
        )
    if not allow_control_characters and any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in value
    ):
        raise ProtocolValidationError(f"{label} contains a control or surrogate character")
    if pattern is not None:
        if isinstance(pattern, str):
            try:
                expression = re.compile(pattern)
            except re.error as exc:
                raise TypeError(f"{label} validation pattern is invalid") from exc
        elif isinstance(pattern, re.Pattern):
            expression = pattern
        else:
            raise TypeError("pattern must be a string, compiled pattern, or None")
        if expression.fullmatch(value) is None:
            raise ProtocolValidationError(f"{label} has an invalid format")
    return value


def require_machine_key(value: object, label: str = "machine key") -> str:
    """Require one canonical lowercase ASCII ``snake_case`` JSON key.

    Generic control data is authenticated, bounded operation data rather than
    a public/log-safe projection.  This validator therefore prevents Unicode
    confusables and alternate spellings from hiding machine intent without
    guessing at the key's operation-specific semantics.
    """

    return require_str(
        value,
        label,
        min_length=1,
        max_length=128,
        pattern=_MACHINE_KEY_PATTERN,
    )


def require_int(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Require a strict integer, rejecting booleans and numeric coercion."""

    for bound, name in ((minimum, "minimum"), (maximum, "maximum")):
        if bound is not None and (
            not isinstance(bound, int) or isinstance(bound, bool)
        ):
            raise TypeError(f"{name} must be an integer or None")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise TypeError("minimum cannot exceed maximum")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolValidationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ProtocolValidationError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ProtocolValidationError(f"{label} must be at most {maximum}")
    return value


def require_bool(value: object, label: str) -> bool:
    """Require an actual JSON boolean."""

    if not isinstance(value, bool):
        raise ProtocolValidationError(f"{label} must be a boolean")
    return value


def require_uuid(value: object, label: str) -> UUID:
    """Require a non-nil UUID or its canonical lowercase wire representation."""

    if isinstance(value, UUID):
        parsed = value
    else:
        text = require_str(value, label, min_length=36, max_length=36)
        try:
            parsed = UUID(text)
        except (AttributeError, ValueError) as exc:
            raise ProtocolValidationError(
                f"{label} must be a canonical UUID string"
            ) from exc
        if text != str(parsed):
            raise ProtocolValidationError(
                f"{label} must use canonical lowercase UUID form"
            )
    if parsed.int == 0:
        raise ProtocolValidationError(f"{label} must not be the nil UUID")
    return parsed


def require_datetime(value: object, label: str) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = require_str(value, label, min_length=19, max_length=40)
        candidate = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ProtocolValidationError(f"{label} must be an ISO-8601 timestamp") from exc
    else:
        raise ProtocolValidationError(f"{label} must be an ISO-8601 timestamp")
    try:
        offset = parsed.utcoffset()
    except (OverflowError, TypeError, ValueError) as exc:
        raise ProtocolValidationError(
            f"{label} must include a valid UTC offset"
        ) from exc
    if parsed.tzinfo is None or offset is None:
        raise ProtocolValidationError(f"{label} must include a UTC offset")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ProtocolValidationError(
            f"{label} cannot be normalized to UTC"
        ) from exc


def encode_datetime(value: object, label: str = "timestamp") -> str:
    """Return the one canonical protocol timestamp representation."""

    parsed = require_datetime(value, label)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validated_limit(value: object, label: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise TypeError(f"{label} must be an integer of at least {minimum}")
    return value


def validate_json_value(
    value: object,
    label: str,
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_items: int = DEFAULT_MAX_JSON_ITEMS,
    max_string: int = DEFAULT_MAX_JSON_STRING,
) -> object:
    """Validate an acyclic, globally bounded plain-JSON value.

    ``max_items`` is a whole-tree budget for array elements and object entries,
    not a per-container allowance.  Only plain dictionaries and lists are
    accepted at the wire boundary; tuples, custom mappings, dataclasses, UUIDs,
    datetime objects, and floating-point values must first be encoded by their
    owning schema.  Floats are rejected rather than allowing different
    implementations to disagree about decimal precision or canonical spelling.
    """

    max_depth = _validated_limit(max_depth, "max_depth", 0)
    max_items = _validated_limit(max_items, "max_items", 0)
    max_string = _validated_limit(max_string, "max_string", 0)
    remaining = max_items
    ancestors: set[int] = set()

    def walk(item: object, path: str, depth: int) -> None:
        nonlocal remaining
        if depth > max_depth:
            raise ProtocolValidationError(f"{path} exceeds maximum JSON depth {max_depth}")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, int):
            if item < _MIN_JSON_INTEGER or item > _MAX_JSON_INTEGER:
                raise ProtocolValidationError(f"{path} integer is outside signed 64-bit range")
            return
        if isinstance(item, float):
            raise ProtocolValidationError(
                f"{path} must not contain floating-point values"
            )
        if isinstance(item, str):
            require_str(
                item,
                path,
                min_length=0,
                max_length=max_string,
                allow_control_characters=True,
            )
            if any(unicodedata.category(character) == "Cs" for character in item):
                raise ProtocolValidationError(f"{path} contains a surrogate character")
            return
        if isinstance(item, (list, dict)):
            identity = id(item)
            if identity in ancestors:
                raise ProtocolValidationError(f"{path} contains a cyclic JSON value")
            ancestors.add(identity)
            try:
                if isinstance(item, list):
                    remaining -= len(item)
                    if remaining < 0:
                        raise ProtocolValidationError(
                            f"{label} exceeds maximum JSON item count {max_items}"
                        )
                    for index, child in enumerate(item):
                        walk(child, f"{path}[{index}]", depth + 1)
                else:
                    remaining -= len(item)
                    if remaining < 0:
                        raise ProtocolValidationError(
                            f"{label} exceeds maximum JSON item count {max_items}"
                        )
                    for key, child in item.items():
                        require_str(key, f"{path} key", min_length=1, max_length=max_string)
                        walk(child, f"{path}.{key}", depth + 1)
            finally:
                ancestors.remove(identity)
            return
        raise ProtocolValidationError(
            f"{path} contains non-JSON value of type {type(item).__name__}"
        )

    walk(value, label, 0)
    return value


def validate_machine_json_value(
    value: object,
    label: str,
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_items: int = DEFAULT_MAX_JSON_ITEMS,
    max_string: int = DEFAULT_MAX_JSON_STRING,
) -> object:
    """Validate bounded JSON with recursive canonical ASCII machine keys.

    Values are deliberately not content-filtered.  They remain authenticated
    operation data whose operation-specific schema owns meaning and whose
    logging/projection owner must treat them as potentially sensitive.
    """

    validate_json_value(
        value,
        label,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )

    def walk(item: object, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                require_machine_key(key, f"{path} key")
                walk(child, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")

    walk(value, label)
    return value


def freeze_json(
    value: object,
    label: str,
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_items: int = DEFAULT_MAX_JSON_ITEMS,
    max_string: int = DEFAULT_MAX_JSON_STRING,
) -> object:
    """Validate and recursively freeze JSON containers for immutable records."""

    validate_json_value(
        value,
        label,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )

    def freeze(item: object) -> object:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(value)


def freeze_machine_json(
    value: object,
    label: str,
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_items: int = DEFAULT_MAX_JSON_ITEMS,
    max_string: int = DEFAULT_MAX_JSON_STRING,
) -> object:
    """Validate machine-keyed JSON and recursively freeze its containers."""

    validate_machine_json_value(
        value,
        label,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )

    def freeze(item: object) -> object:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(value)


def thaw_json(value: object) -> object:
    """Return a fresh plain-JSON representation of a frozen protocol value."""

    ancestors: set[int] = set()

    def thaw(item: object, path: str) -> object:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in ancestors:
                raise ProtocolValidationError(f"{path} contains a cyclic mapping")
            ancestors.add(identity)
            try:
                return {
                    key: thaw(child, f"{path}.{key}")
                    for key, child in item.items()
                }
            finally:
                ancestors.remove(identity)
        if isinstance(item, (tuple, list)):
            identity = id(item)
            if identity in ancestors:
                raise ProtocolValidationError(f"{path} contains a cyclic sequence")
            ancestors.add(identity)
            try:
                return [
                    thaw(child, f"{path}[{index}]")
                    for index, child in enumerate(item)
                ]
            finally:
                ancestors.remove(identity)
        return item

    return thaw(value, "JSON value")


def canonical_json(
    value: object,
    label: str = "payload",
    *,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_items: int = DEFAULT_MAX_JSON_ITEMS,
    max_string: int = DEFAULT_MAX_JSON_STRING,
    max_bytes: int | None = None,
) -> str:
    """Encode one validated JSON value deterministically.

    ``max_bytes`` is optional so protocol families with larger explicitly
    reviewed budgets are not silently constrained by the control-envelope
    default.  Every control/event/negotiation encoder supplies its exact family
    limit explicitly.
    """

    plain = thaw_json(value)
    validate_json_value(
        plain,
        label,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )
    encoded = json.dumps(
        plain,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if max_bytes is not None:
        max_bytes = _validated_limit(max_bytes, "max_bytes", 1)
        try:
            size = len(encoded.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ProtocolValidationError(f"{label} must be valid Unicode") from exc
        if size > max_bytes:
            raise ProtocolValidationError(
                f"{label} exceeds maximum encoded size {max_bytes}"
            )
    return encoded


def decode_json_object(
    value: object,
    label: str = "payload",
    *,
    max_bytes: int = DEFAULT_MAX_JSON_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_items: int = DEFAULT_MAX_JSON_ITEMS,
    max_string: int = DEFAULT_MAX_JSON_STRING,
) -> dict[str, object]:
    """Decode a bounded JSON object while rejecting duplicate keys and constants."""

    max_bytes = _validated_limit(max_bytes, "max_bytes", 1)
    if isinstance(value, bytes):
        encoded = value
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolValidationError(f"{label} must be valid UTF-8") from exc
    elif isinstance(value, str):
        text = value
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProtocolValidationError(f"{label} must be valid Unicode") from exc
    else:
        raise ProtocolValidationError(f"{label} must be JSON text or UTF-8 bytes")
    if len(encoded) > max_bytes:
        raise ProtocolValidationError(f"{label} exceeds maximum encoded size {max_bytes}")

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, child in pairs:
            if key in output:
                raise ProtocolValidationError(f"{label} contains duplicate key {key!r}")
            output[key] = child
        return output

    def reject_constant(constant: str) -> object:
        raise ProtocolValidationError(f"{label} contains invalid JSON constant {constant}")

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except ProtocolValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ProtocolValidationError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ProtocolValidationError(f"{label} must decode to an object")
    validate_json_value(
        decoded,
        label,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
    )
    return decoded


__all__ = [
    "DEFAULT_MAX_JSON_BYTES",
    "DEFAULT_MAX_JSON_DEPTH",
    "DEFAULT_MAX_JSON_ITEMS",
    "DEFAULT_MAX_JSON_STRING",
    "PUBLIC_WIRE_MAX_BYTES",
    "PUBLIC_WIRE_MAX_DEPTH",
    "PUBLIC_WIRE_MAX_ITEMS",
    "PUBLIC_WIRE_MAX_STRING",
    "ProtocolValidationError",
    "canonical_json",
    "decode_json_object",
    "encode_datetime",
    "freeze_json",
    "freeze_machine_json",
    "require_bool",
    "require_datetime",
    "require_int",
    "require_machine_key",
    "require_str",
    "require_uuid",
    "strict_object",
    "thaw_json",
    "validate_json_value",
    "validate_machine_json_value",
]
