"""Direct proof for the dependency-free Somnus control protocol primitives.

Source: TASK-P1-001 and GATE-P1
Integrated: 2026-08-05
Purpose: Exercises strict serialization, exact negotiation transcripts,
    authenticated machine-data bags, closed public failures, and cold-import
    isolation at the package's direct consumed boundary.
IO: Uses one disposable subprocess and temporary directory for cold-import
    observation; production protocol modules remain IO-free.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol._validation import (  # noqa: E402
    PUBLIC_WIRE_MAX_BYTES,
    ProtocolValidationError,
    canonical_json,
    decode_json_object,
    encode_datetime,
    freeze_json,
    freeze_machine_json,
    require_datetime,
    require_int,
    require_uuid,
    strict_object,
    thaw_json,
    validate_json_value,
    validate_machine_json_value,
)
from somnus_protocol.control import (  # noqa: E402
    ERROR_CODE_REGISTRY,
    ERROR_EVIDENCE_REGISTRY,
    ControlRequest,
    ControlResponse,
    DiagnosticReference,
    ErrorCategory,
    ErrorCode,
    ErrorEnvelope,
    ErrorEvidence,
    ErrorEvidenceKind,
    PublicExitCode,
)
from somnus_protocol.events import ControlEvent  # noqa: E402
from somnus_protocol.version import (  # noqa: E402
    CURRENT_PROTOCOL_VERSION,
    ProtocolCapabilities,
    ProtocolNegotiation,
    ProtocolNegotiationError,
    ProtocolVersionError,
    ProtocolVersionRange,
    SchemaVersionError,
    SemanticVersion,
    UnsupportedProtocolVersionError,
    ensure_supported_protocol_version,
    negotiate_protocol_version,
    require_schema_version,
)


NIL_UUID = UUID(int=0)
REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
CORRELATION_ID = UUID("22222222-2222-4222-8222-222222222222")
OPERATION_ID = UUID("33333333-3333-4333-8333-333333333333")
VM_ID = UUID("44444444-4444-4444-8444-444444444444")
BOOT_ID = UUID("55555555-5555-4555-8555-555555555555")
EVENT_ID = UUID("66666666-6666-4666-8666-666666666666")
DIAGNOSTIC_ID = UUID("77777777-7777-4777-8777-777777777777")
NEGOTIATION_ID = UUID("88888888-8888-4888-8888-888888888888")
OTHER_NEGOTIATION_ID = UUID("99999999-9999-4999-8999-999999999999")
TIMESTAMP = datetime(2026, 8, 5, 12, 34, 56, 123456, tzinfo=timezone.utc)


def request_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol_version": "1.0.0",
        "request_id": str(REQUEST_ID),
        "correlation_id": str(CORRELATION_ID),
        "vm_id": str(VM_ID),
        "boot_id": str(BOOT_ID),
        "generation": 7,
        "timestamp": "2026-08-05T12:34:56.123456Z",
        "operation": "vm.status",
        "payload": {
            "include": ["runtime", "storage"],
            "observation": {"fresh": True},
        },
    }


def valid_error_payload(
    code: ErrorCode = ErrorCode.INVALID_REQUEST,
) -> dict[str, object]:
    specification = ERROR_CODE_REGISTRY[code]
    return {
        "schema_version": 1,
        "protocol_version": "1.0.0",
        "request_id": str(REQUEST_ID),
        "correlation_id": str(CORRELATION_ID),
        "operation_id": None,
        "vm_id": None,
        "boot_id": None,
        "generation": None,
        "timestamp": encode_datetime(TIMESTAMP),
        "ok": False,
        "code": code.value,
        "category": specification.category.value,
        "exit_code": int(specification.exit_code),
        "detail": specification.detail,
        "retryable": specification.retryable,
        "remediation": list(specification.remediation),
        "evidence": [],
        "details": {},
    }


class ValidationTests(unittest.TestCase):
    def test_exact_object_rejects_missing_unknown_and_non_object(self) -> None:
        self.assertEqual(
            strict_object({"a": 1, "b": 2}, {"a"}, {"b"}, "fixture"),
            {"a": 1, "b": 2},
        )
        with self.assertRaisesRegex(ProtocolValidationError, "missing required"):
            strict_object({}, {"a"}, label="fixture")
        with self.assertRaisesRegex(ProtocolValidationError, "unknown fields"):
            strict_object({"a": 1, "hidden": 2}, {"a"}, label="fixture")
        with self.assertRaisesRegex(ProtocolValidationError, "must be an object"):
            strict_object([], set(), label="fixture")

    def test_strict_scalars_timestamp_and_non_nil_uuid(self) -> None:
        class InvalidOffset(tzinfo):
            def utcoffset(self, value: datetime | None) -> object:
                return "not-a-timedelta"

        self.assertEqual(require_int(3, "count", minimum=0), 3)
        for value in (True, 3.0, "3"):
            with self.subTest(value=value):
                with self.assertRaises(ProtocolValidationError):
                    require_int(value, "count")
        self.assertEqual(
            require_datetime("2026-08-05T07:34:56.123456-05:00", "timestamp"),
            TIMESTAMP,
        )
        self.assertEqual(encode_datetime(TIMESTAMP), "2026-08-05T12:34:56.123456Z")
        with self.assertRaisesRegex(ProtocolValidationError, "UTC offset"):
            require_datetime("2026-08-05T12:34:56", "timestamp")
        for edge in (
            "9999-12-31T23:59:59.999999-23:59",
            "0001-01-01T00:00:00+23:59",
        ):
            with self.subTest(edge=edge):
                with self.assertRaisesRegex(
                    ProtocolValidationError, "normalized to UTC"
                ):
                    require_datetime(edge, "timestamp")
        with self.assertRaisesRegex(ProtocolValidationError, "valid UTC offset"):
            require_datetime(
                datetime(2026, 8, 5, 12, 34, 56, tzinfo=InvalidOffset()),
                "timestamp",
            )
        for nil in (NIL_UUID, str(NIL_UUID)):
            with self.subTest(nil=nil):
                with self.assertRaisesRegex(ProtocolValidationError, "nil UUID"):
                    require_uuid(nil, "identity")

    def test_bounded_json_rejects_all_floats_cycles_depth_and_total_items(self) -> None:
        self.assertEqual(
            validate_json_value({"a": [1, True, None, "x"]}, "fixture"),
            {"a": [1, True, None, "x"]},
        )
        for floating_point in (1.5, float("nan"), float("inf")):
            with self.subTest(floating_point=floating_point):
                with self.assertRaisesRegex(ProtocolValidationError, "floating-point"):
                    validate_json_value({"bad": floating_point}, "fixture")
        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(ProtocolValidationError, "cyclic"):
            validate_json_value(cyclic, "fixture")
        with self.assertRaisesRegex(ProtocolValidationError, "key must be a string"):
            validate_json_value({1: "coercion is forbidden"}, "fixture")
        with self.assertRaisesRegex(ProtocolValidationError, "depth"):
            validate_json_value({"a": {"b": {"c": 1}}}, "fixture", max_depth=2)
        with self.assertRaisesRegex(ProtocolValidationError, "item count"):
            validate_json_value({"a": [1, 2, 3]}, "fixture", max_items=3)

    def test_machine_bags_allow_semantics_but_require_ascii_canonical_keys(self) -> None:
        authenticated_data = {
            "credential_ref": "vault://guest/login",
            "header_count": 2,
            "content_sha256": "f" * 64,
            "raw_payload": "password=hunter2",
            "nested_items": [
                {"authorization_header": "Bearer x"},
                {"traceback_text": "Traceback (most recent call last):"},
            ],
        }
        self.assertIs(
            validate_machine_json_value(authenticated_data, "operation data"),
            authenticated_data,
        )
        frozen = freeze_machine_json(authenticated_data, "operation data")
        self.assertEqual(thaw_json(frozen), authenticated_data)
        for key in (
            "accessToken",
            "header-count",
            "naïve",
            "pаyload",  # Cyrillic 'a' is intentionally confusable.
            "double__underscore",
            "_leading",
            "trailing_",
            "1st_value",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ProtocolValidationError, "invalid format"):
                    validate_machine_json_value({key: True}, "operation data")

    def test_freeze_thaw_canonical_encoding_and_utf8_byte_limit(self) -> None:
        source = {"z": [1, {"b": 2}], "a": True}
        frozen = freeze_json(source, "fixture")
        source["z"].append(3)
        self.assertEqual(thaw_json(frozen), {"z": [1, {"b": 2}], "a": True})
        with self.assertRaises(TypeError):
            frozen["new"] = 1  # type: ignore[index]
        value = {"text": "é"}
        encoded = canonical_json(value)
        self.assertEqual(encoded, '{"text":"é"}')
        exact = len(encoded.encode("utf-8"))
        self.assertEqual(canonical_json(value, max_bytes=exact), encoded)
        with self.assertRaisesRegex(ProtocolValidationError, "encoded size"):
            canonical_json(value, max_bytes=exact - 1)

    def test_json_decoder_rejects_duplicate_keys_constants_and_oversize(self) -> None:
        self.assertEqual(decode_json_object('{"a":1}'), {"a": 1})
        with self.assertRaisesRegex(ProtocolValidationError, "duplicate key"):
            decode_json_object('{"a":1,"a":2}')
        with self.assertRaisesRegex(ProtocolValidationError, "invalid JSON constant"):
            decode_json_object('{"a":NaN}')
        with self.assertRaisesRegex(ProtocolValidationError, "encoded size"):
            decode_json_object('{"long":"abcdef"}', max_bytes=8)


class VersionTests(unittest.TestCase):
    def test_semantic_version_is_canonical_and_bounded(self) -> None:
        self.assertEqual(str(SemanticVersion.parse("12.34.56")), "12.34.56")
        for value in ("1.0", "01.0.0", "1.0.0-alpha", "1.0.0+build", True):
            with self.subTest(value=value):
                with self.assertRaises(ProtocolVersionError):
                    SemanticVersion.parse(value)

    def test_explicit_range_negotiation_selects_highest_shared_version(self) -> None:
        local = ProtocolVersionRange.from_values("1.0.0", "1.4.0")
        peer = ProtocolVersionRange.from_values("1.2.0", "1.3.0")
        self.assertEqual(
            negotiate_protocol_version(peer, local=local),
            SemanticVersion(1, 3, 0),
        )
        with self.assertRaises(ProtocolNegotiationError):
            local.negotiate(ProtocolVersionRange.from_values("1.5.0", "1.6.0"))

    def test_offer_and_exact_transcript_roundtrip_and_acceptance(self) -> None:
        initiator = ProtocolCapabilities(
            schema_version=1,
            negotiation_id=NEGOTIATION_ID,
            minimum_protocol_version="1.0.0",
            maximum_protocol_version="1.4.0",
        )
        responder = ProtocolCapabilities(
            schema_version=1,
            negotiation_id=NEGOTIATION_ID,
            minimum_protocol_version="1.2.0",
            maximum_protocol_version="1.3.0",
        )
        self.assertEqual(ProtocolCapabilities.from_json(initiator.to_json()), initiator)
        self.assertTrue(initiator.offer_fingerprint.startswith("sha256:"))
        transcript = initiator.negotiate(responder)
        self.assertEqual(
            ProtocolNegotiation.from_json(transcript.to_json()),
            transcript,
        )
        self.assertEqual(
            transcript.accept(initiator, responder),
            SemanticVersion(1, 3, 0),
        )
        self.assertEqual(
            transcript.initiator_offer_fingerprint,
            initiator.offer_fingerprint,
        )
        self.assertEqual(
            transcript.responder_offer_fingerprint,
            responder.offer_fingerprint,
        )

    def test_transcript_rejects_narrowing_mismatch_replay_and_downgrade(self) -> None:
        initiator = ProtocolCapabilities(
            1,
            NEGOTIATION_ID,
            "1.0.0",
            "1.4.0",
        )
        responder = ProtocolCapabilities(
            1,
            NEGOTIATION_ID,
            "1.2.0",
            "1.3.0",
        )
        transcript = ProtocolNegotiation.select(initiator, responder)

        narrowed = ProtocolCapabilities(
            1,
            NEGOTIATION_ID,
            "1.2.0",
            "1.2.0",
        )
        with self.assertRaisesRegex(ProtocolNegotiationError, "Responder offer"):
            transcript.accept(initiator, narrowed)

        mismatched_payload = transcript.to_dict()
        mismatched_payload["responder_offer_fingerprint"] = "sha256:" + ("0" * 64)
        mismatched = ProtocolNegotiation.from_dict(mismatched_payload)
        with self.assertRaisesRegex(ProtocolNegotiationError, "Responder offer"):
            mismatched.accept(initiator, responder)

        mutated_offer = responder.to_dict()
        mutated_offer["maximum_protocol_version"] = "1.2.0"
        with self.assertRaisesRegex(ProtocolNegotiationError, "fingerprint"):
            ProtocolCapabilities.from_dict(mutated_offer)

        other_id_offer = ProtocolCapabilities(
            1,
            OTHER_NEGOTIATION_ID,
            "1.2.0",
            "1.3.0",
        )
        with self.assertRaisesRegex(ProtocolNegotiationError, "different negotiation_id"):
            ProtocolNegotiation.select(initiator, other_id_offer)
        replay_initiator = ProtocolCapabilities(
            1,
            OTHER_NEGOTIATION_ID,
            "1.0.0",
            "1.4.0",
        )
        replay_responder = ProtocolCapabilities(
            1,
            OTHER_NEGOTIATION_ID,
            "1.2.0",
            "1.3.0",
        )
        with self.assertRaisesRegex(ProtocolNegotiationError, "transcript ID"):
            transcript.accept(replay_initiator, replay_responder)

        downgrade = ProtocolNegotiation(
            schema_version=1,
            negotiation_id=NEGOTIATION_ID,
            initiator_offer_fingerprint=initiator.offer_fingerprint,
            responder_offer_fingerprint=responder.offer_fingerprint,
            selected_protocol_version="1.2.0",
        )
        with self.assertRaisesRegex(ProtocolNegotiationError, "highest shared"):
            downgrade.accept(initiator, responder)

    def test_supported_protocol_schema_and_negotiation_ids_fail_loudly(self) -> None:
        self.assertEqual(
            ensure_supported_protocol_version("1.0.0"),
            CURRENT_PROTOCOL_VERSION,
        )
        with self.assertRaises(UnsupportedProtocolVersionError):
            ensure_supported_protocol_version("1.0.1")
        self.assertEqual(require_schema_version(1), 1)
        for value in (2, 0, True, "1"):
            with self.subTest(value=value):
                with self.assertRaises(SchemaVersionError):
                    require_schema_version(value)
        with self.assertRaisesRegex(ProtocolValidationError, "nil UUID"):
            ProtocolCapabilities(1, NIL_UUID, "1.0.0", "1.0.0")


class ControlEnvelopeTests(unittest.TestCase):
    def test_request_roundtrip_immutability_and_authenticated_machine_data(self) -> None:
        payload = request_payload()
        payload["payload"] = {
            "credential_ref": "vault://guest/login",
            "header_count": 2,
            "content_sha256": "a" * 64,
            "raw_payload": "Authorization: Bearer x",
        }
        request = ControlRequest.from_dict(payload)
        self.assertEqual(request.to_dict(), payload)
        self.assertEqual(ControlRequest.from_json(request.to_json()), request)
        with self.assertRaises(TypeError):
            request.payload["new"] = True  # type: ignore[index]

    def test_request_rejects_unknown_version_context_and_noncanonical_bag_keys(self) -> None:
        payload = request_payload()
        payload["hidden_operator_intent"] = True
        with self.assertRaisesRegex(ProtocolValidationError, "unknown fields"):
            ControlRequest.from_dict(payload)

        payload = request_payload()
        payload["protocol_version"] = "1.0.1"
        with self.assertRaises(UnsupportedProtocolVersionError):
            ControlRequest.from_dict(payload)

        payload = request_payload()
        payload["vm_id"] = None
        with self.assertRaisesRegex(ProtocolValidationError, "vm_id and generation"):
            ControlRequest.from_dict(payload)

        for key in ("accessToken", "pаyload", "header-count"):
            with self.subTest(key=key):
                payload = request_payload()
                payload["payload"] = {key: "value"}
                with self.assertRaisesRegex(ProtocolValidationError, "invalid format"):
                    ControlRequest.from_dict(payload)

    def test_request_item_and_exact_utf8_wire_boundaries(self) -> None:
        payload = request_payload()
        payload["payload"] = {f"entry_{index}": index for index in range(64)}
        self.assertEqual(len(ControlRequest.from_dict(payload).payload), 64)
        payload["payload"]["entry_64"] = 64
        with self.assertRaisesRegex(ProtocolValidationError, "item count"):
            ControlRequest.from_dict(payload)

        def sized_payload(target: int) -> dict[str, object]:
            candidate = request_payload()
            body = {
                f"block_{index:02d}": "x" * 4_096
                for index in range(15)
            }
            body["tail"] = ""
            candidate["payload"] = body
            baseline = len(
                canonical_json(
                    candidate,
                    "sizing fixture",
                    max_depth=6,
                    max_items=256,
                    max_string=4_096,
                ).encode("utf-8")
            )
            tail_size = target - baseline
            self.assertGreaterEqual(tail_size, 0)
            self.assertLessEqual(tail_size, 4_096)
            body["tail"] = "x" * tail_size
            return candidate

        exact = ControlRequest.from_dict(sized_payload(PUBLIC_WIRE_MAX_BYTES))
        encoded = exact.to_json()
        self.assertEqual(len(encoded.encode("utf-8")), PUBLIC_WIRE_MAX_BYTES)
        self.assertEqual(ControlRequest.from_json(encoded), exact)
        with self.assertRaisesRegex(ProtocolValidationError, "encoded size"):
            ControlRequest.from_dict(sized_payload(PUBLIC_WIRE_MAX_BYTES + 1))

    def test_all_control_identity_roles_reject_nil_uuid(self) -> None:
        for field_name in ("request_id", "correlation_id", "vm_id", "boot_id"):
            with self.subTest(field_name=field_name):
                payload = request_payload()
                payload[field_name] = str(NIL_UUID)
                with self.assertRaisesRegex(ProtocolValidationError, "nil UUID"):
                    ControlRequest.from_dict(payload)

        response = ControlResponse(
            schema_version=1,
            protocol_version=CURRENT_PROTOCOL_VERSION,
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            operation_id=OPERATION_ID,
            vm_id=None,
            boot_id=None,
            generation=None,
            timestamp=TIMESTAMP,
            result={},
        ).to_dict()
        response["operation_id"] = str(NIL_UUID)
        with self.assertRaisesRegex(ProtocolValidationError, "nil UUID"):
            ControlResponse.from_dict(response)

        event = self._event().to_dict()
        for field_name in ("event_id", "operation_id"):
            with self.subTest(field_name=field_name):
                mutated = dict(event)
                mutated[field_name] = str(NIL_UUID)
                with self.assertRaisesRegex(ProtocolValidationError, "nil UUID"):
                    ControlEvent.from_dict(mutated)

        with self.assertRaisesRegex(ProtocolValidationError, "nil UUID"):
            DiagnosticReference(NIL_UUID)

    def test_long_uuid_and_timestamp_are_bounded_before_parsing(self) -> None:
        payload = request_payload()
        payload["request_id"] = "a" * 10_000
        with self.assertRaisesRegex(ProtocolValidationError, "length"):
            ControlRequest.from_dict(payload)
        payload = request_payload()
        payload["timestamp"] = "2" * 10_000
        with self.assertRaisesRegex(ProtocolValidationError, "length"):
            ControlRequest.from_dict(payload)
        for edge in (
            "9999-12-31T23:59:59.999999-23:59",
            "0001-01-01T00:00:00+23:59",
        ):
            with self.subTest(edge=edge):
                payload = request_payload()
                payload["timestamp"] = edge
                with self.assertRaisesRegex(
                    ProtocolValidationError, "normalized to UTC"
                ):
                    ControlRequest.from_json(json.dumps(payload))

    def test_success_response_roundtrip_and_machine_result(self) -> None:
        response = ControlResponse(
            schema_version=1,
            protocol_version=CURRENT_PROTOCOL_VERSION,
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            operation_id=OPERATION_ID,
            vm_id=VM_ID,
            boot_id=BOOT_ID,
            generation=7,
            timestamp=TIMESTAMP,
            result={
                "credential_ref": "vault://guest/login",
                "header_count": 2,
                "raw_payload": "password=hunter2",
            },
        )
        self.assertTrue(response.ok)
        self.assertEqual(ControlResponse.from_json(response.to_json()), response)
        payload = response.to_dict()
        payload["ok"] = False
        with self.assertRaisesRegex(ProtocolValidationError, "ok=true"):
            ControlResponse.from_dict(payload)

    def test_closed_error_roundtrip_derives_every_public_phrase(self) -> None:
        evidence = ErrorEvidence(
            ErrorEvidenceKind.DEADLINE,
            DiagnosticReference(DIAGNOSTIC_ID),
        )
        envelope = ErrorEnvelope(
            schema_version=1,
            protocol_version=CURRENT_PROTOCOL_VERSION,
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            operation_id=OPERATION_ID,
            vm_id=VM_ID,
            boot_id=BOOT_ID,
            generation=7,
            timestamp=TIMESTAMP,
            code=ErrorCode.AGENT_UNAVAILABLE,
            category=ErrorCategory.UNAVAILABLE,
            exit_code=PublicExitCode.UNAVAILABLE,
            evidence=(evidence,),
        )
        specification = ERROR_CODE_REGISTRY[ErrorCode.AGENT_UNAVAILABLE]
        self.assertEqual(envelope.detail, specification.detail)
        self.assertEqual(envelope.retryable, specification.retryable)
        self.assertEqual(envelope.remediation, specification.remediation)
        self.assertEqual(
            evidence.summary,
            ERROR_EVIDENCE_REGISTRY[ErrorEvidenceKind.DEADLINE].summary,
        )
        self.assertEqual(
            evidence.to_dict()["reference"],
            f"diag:{DIAGNOSTIC_ID}",
        )
        self.assertEqual(ErrorEnvelope.from_json(envelope.to_json()), envelope)

    def test_error_registry_rejects_unknown_category_and_exit_mismatch(self) -> None:
        self.assertEqual(
            {specification.category for specification in ERROR_CODE_REGISTRY.values()},
            set(ErrorCategory),
        )
        unknown = valid_error_payload()
        unknown["code"] = "PROVIDER_X_QUOTA"
        with self.assertRaisesRegex(ProtocolValidationError, "not registered"):
            ErrorEnvelope.from_dict(unknown)
        wrong_category = valid_error_payload()
        wrong_category["category"] = ErrorCategory.EXECUTION.value
        with self.assertRaisesRegex(ProtocolValidationError, "requires category"):
            ErrorEnvelope.from_dict(wrong_category)
        wrong_exit = valid_error_payload()
        wrong_exit["exit_code"] = int(PublicExitCode.DOMAIN_FAILURE)
        with self.assertRaisesRegex(ProtocolValidationError, "requires exit_code"):
            ErrorEnvelope.from_dict(wrong_exit)

    def test_public_error_cannot_carry_arbitrary_prose_paths_or_details(self) -> None:
        mutations: list[tuple[str, object, str]] = [
            ("detail", "password is hunter2", "fixed public detail"),
            ("retryable", True, "requires retryable"),
            ("remediation", ["Use bearer x."], "fixed remediation"),
            ("details", {"benign_count": 1}, "details must be empty"),
            ("details", {"raw_payload": "secret"}, "details must be empty"),
        ]
        for field_name, value, message in mutations:
            with self.subTest(field_name=field_name, value=value):
                payload = valid_error_payload()
                payload[field_name] = value
                with self.assertRaisesRegex(ProtocolValidationError, message):
                    ErrorEnvelope.from_dict(payload)

        evidence_payload = valid_error_payload()
        evidence_payload["evidence"] = [
            {
                "kind": ErrorEvidenceKind.DEADLINE.value,
                "summary": "Bearer x",
                "reference": f"diag:{DIAGNOSTIC_ID}",
            }
        ]
        with self.assertRaisesRegex(ProtocolValidationError, "fixed public summary"):
            ErrorEnvelope.from_dict(evidence_payload)

        for reference in (
            "diagnostics/run-17",
            "diag:run-17/path",
            f"diag:{NIL_UUID}",
        ):
            with self.subTest(reference=reference):
                evidence_payload = valid_error_payload()
                evidence_payload["evidence"] = [
                    {
                        "kind": ErrorEvidenceKind.DEADLINE.value,
                        "summary": ERROR_EVIDENCE_REGISTRY[
                            ErrorEvidenceKind.DEADLINE
                        ].summary,
                        "reference": reference,
                    }
                ]
                with self.assertRaises(ProtocolValidationError):
                    ErrorEnvelope.from_dict(evidence_payload)

    def test_error_evidence_count_is_bounded(self) -> None:
        evidence = tuple(
            ErrorEvidence(
                ErrorEvidenceKind.STATE_OBSERVATION,
                DiagnosticReference(
                    UUID(f"{index + 1:08x}-0000-4000-8000-000000000001")
                ),
            )
            for index in range(16)
        )
        envelope = ErrorEnvelope(
            schema_version=1,
            protocol_version=CURRENT_PROTOCOL_VERSION,
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            operation_id=None,
            vm_id=None,
            boot_id=None,
            generation=None,
            timestamp=TIMESTAMP,
            code=ErrorCode.EXECUTION_FAILED,
            category=ErrorCategory.EXECUTION,
            exit_code=PublicExitCode.DOMAIN_FAILURE,
            evidence=evidence,
        )
        self.assertEqual(ErrorEnvelope.from_json(envelope.to_json()), envelope)
        with self.assertRaisesRegex(ProtocolValidationError, "more than 16"):
            ErrorEnvelope(
                schema_version=1,
                protocol_version=CURRENT_PROTOCOL_VERSION,
                request_id=REQUEST_ID,
                correlation_id=CORRELATION_ID,
                operation_id=None,
                vm_id=None,
                boot_id=None,
                generation=None,
                timestamp=TIMESTAMP,
                code=ErrorCode.EXECUTION_FAILED,
                category=ErrorCategory.EXECUTION,
                exit_code=PublicExitCode.DOMAIN_FAILURE,
                evidence=evidence + (evidence[0],),
            )

    @staticmethod
    def _event() -> ControlEvent:
        return ControlEvent(
            schema_version=1,
            protocol_version=CURRENT_PROTOCOL_VERSION,
            event_id=EVENT_ID,
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            operation_id=OPERATION_ID,
            vm_id=VM_ID,
            boot_id=BOOT_ID,
            generation=7,
            timestamp=TIMESTAMP,
            event_type="lifecycle.qmp_verified",
            sequence=4,
            data={
                "credential_ref": "vault://guest/login",
                "header_count": 2,
                "raw_payload": "Authorization: Bearer x",
            },
        )

    def test_control_event_roundtrip_scope_sequence_and_machine_data(self) -> None:
        event = self._event()
        self.assertEqual(ControlEvent.from_json(event.to_json()), event)
        payload = event.to_dict()
        payload["sequence"] = True
        with self.assertRaisesRegex(ProtocolValidationError, "must be an integer"):
            ControlEvent.from_dict(payload)
        payload = event.to_dict()
        payload["operation_id"] = None
        with self.assertRaisesRegex(ProtocolValidationError, "operation_id is required"):
            ControlEvent.from_dict(payload)
        payload = event.to_dict()
        payload["data"] = {"headerCount": 2}
        with self.assertRaisesRegex(ProtocolValidationError, "invalid format"):
            ControlEvent.from_dict(payload)


class ColdImportTests(unittest.TestCase):
    def test_public_root_exports_negotiation_diagnostics_and_observation(self) -> None:
        import somnus_protocol
        from somnus_protocol._validation import ProtocolValidationError
        from somnus_protocol.control import DiagnosticReference
        from somnus_protocol.version import ProtocolNegotiation
        from somnus_protocol.vm import RuntimeObservation

        expected = {
            "DiagnosticReference": DiagnosticReference,
            "ProtocolNegotiation": ProtocolNegotiation,
            "ProtocolValidationError": ProtocolValidationError,
            "RuntimeObservation": RuntimeObservation,
        }
        for name, canonical in expected.items():
            with self.subTest(name=name):
                self.assertIn(name, somnus_protocol.__all__)
                self.assertIs(getattr(somnus_protocol, name), canonical)

    def test_control_protocol_cold_import_has_no_host_guest_or_file_side_effect(self) -> None:
        script = "\n".join(
            [
                "import json, pathlib, sys, threading",
                f"sys.path.insert(0, {str(SOURCE_ROOT)!r})",
                "before_files = sorted(p.name for p in pathlib.Path.cwd().iterdir())",
                "before_threads = threading.active_count()",
                "import somnus_protocol._validation",
                "import somnus_protocol.version",
                "import somnus_protocol.control",
                "import somnus_protocol.events",
                "after_files = sorted(p.name for p in pathlib.Path.cwd().iterdir())",
                (
                    "forbidden = sorted(name for name in sys.modules "
                    "if name.startswith(('somnus_vm', 'somnus_guest', "
                    "'components', 'pydantic')))"
                ),
                (
                    "print(json.dumps({'before_files': before_files, "
                    "'after_files': after_files, 'before_threads': before_threads, "
                    "'after_threads': threading.active_count(), "
                    "'forbidden': forbidden}, sort_keys=True))"
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script],
                cwd=temporary,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["before_files"], result["after_files"])
        self.assertEqual(result["before_threads"], result["after_threads"])
        self.assertEqual(result["forbidden"], [])


def run_control_protocol_checks() -> str:
    """Run the complete control-protocol suite for the smoke consumer."""

    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise AssertionError(output.getvalue())
    return (
        f"round-tripped and adversarially bounded {result.testsRun} "
        "version/control/event protocol cases with a cold-import proof"
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
