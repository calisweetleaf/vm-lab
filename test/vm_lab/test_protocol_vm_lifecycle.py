"""Direct P1 proof for the canonical VM lifecycle protocol authority.

Source: PLAN.md P1-005 through P1-012 and GATE-P1
Integrated: 2026-08-05
Purpose: Exercises every lifecycle edge, its exact physical-evidence contract,
    immutable records, strict schema decoding, and the one supported v0 record
    migration without importing a host or guest authority.
IO: Reads the checked-in v0 fixture and launches one cold import subprocess.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
FIXTURE = PROJECT_ROOT / "test/vm_lab/fixtures/protocol/v0_vm_record.json"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol.vm import (
    ALLOWED_TRANSITIONS,
    IDEMPOTENT_STATES,
    TRANSITION_REQUIREMENTS,
    ImageProvenance,
    ObservationSource,
    ProcessIdentity,
    RuntimeObservation,
    SnapshotReference,
    StorageReference,
    TransitionCause,
    TransitionEvidence,
    TransitionRecord,
    VMContractError,
    VMDefinition,
    VMMigrationError,
    VMPorts,
    VMRecord,
    VMResourceSpec,
    VMState,
    VMTransitionError,
    migrate_vm_record,
)


BASE_TIME = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
VM_ID = UUID("11111111-1111-4111-8111-111111111111")
BOOT_ID = UUID("22222222-2222-4222-8222-222222222222")
NEXT_BOOT_ID = UUID("33333333-3333-4333-8333-333333333333")
CAUSE_ID = UUID("44444444-4444-4444-8444-444444444444")


def _uuid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


FACT_VALUES: dict[str, str] = {
    "agent_authenticated": "true",
    "agent_ready": "true",
    "boot_id": str(BOOT_ID),
    "cause_event_id": str(CAUSE_ID),
    "error_code": "qmp.connection_lost",
    "guest_quiesced": "true",
    "image_sha256": "a" * 64,
    "intent_digest": "e" * 64,
    "observation_id": _uuid(1),
    "operation_id": _uuid(2),
    "ownership_verified": "true",
    "process_absent": "true",
    "process_exit": "exit:0",
    "qmp_greeting": "sha256:" + ("c" * 64),
    "qmp_status": "running",
    "qmp_unreachable": "true",
    "qmp_uuid": str(VM_ID),
    "resources_removed": "true",
    "rollback_verified": "true",
    "snapshot_committed": "true",
    "snapshot_id": _uuid(3),
    "storage_id": _uuid(4),
    "vm_stopped": "true",
}


def _expect_rejected(
    function: object,
    *expected: type[BaseException],
) -> BaseException:
    if not callable(function):
        raise AssertionError("expected a callable")
    accepted = expected or (VMContractError, VMTransitionError)
    try:
        function()
    except accepted as exc:
        return exc
    raise AssertionError(
        "expected rejection through " + ", ".join(item.__name__ for item in accepted)
    )


def _cause(
    *,
    event_id: UUID = CAUSE_ID,
    when: datetime = BASE_TIME,
) -> TransitionCause:
    return TransitionCause(
        code="qmp.connection_lost",
        detail="QMP connection closed before a verified response",
        occurred_at=when,
        event_id=event_id,
    )


def _process(
    *,
    when: datetime = BASE_TIME,
    pid: int = 4242,
    start_time_ticks: int = 9001,
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        start_time_ticks=start_time_ticks,
        executable="/usr/bin/qemu-system-x86_64",
        executable_sha256="b" * 64,
        observed_at=when,
    )


def _facts(
    previous: VMState,
    target: VMState,
    **overrides: str,
) -> dict[str, str]:
    values = {
        key: FACT_VALUES[key]
        for key in TRANSITION_REQUIREMENTS[(previous, target)]
    }
    values.update(overrides)
    return values


def _runtime_observation(
    previous: VMState,
    target: VMState,
    second: int,
    *,
    record: VMRecord | None = None,
) -> RuntimeObservation:
    """Build the exact daemon aggregate required by an observed edge."""

    observed_at = BASE_TIME + timedelta(seconds=second)
    vm_id = record.vm_id if record is not None else VM_ID
    generation = record.generation if record is not None else 0
    boot_id = record.boot_id if record is not None else BOOT_ID
    intent_digest = (
        record.intent_digest
        if record is not None
        else FACT_VALUES["intent_digest"]
    )
    process = (
        replace(record.process, observed_at=observed_at)
        if record is not None and record.process is not None
        else _process(when=observed_at)
    )
    observation_id = UUID(_uuid(1_000 + second))

    if target is VMState.RECONCILING:
        # Reconciliation is authorized by a typed observation contradicting
        # the recorded state, never by a generic operator assertion.
        target = (
            VMState.QMP_RUNNING
            if previous is VMState.STOPPED
            else VMState.STOPPED
        )

    if target is VMState.STOPPED:
        return RuntimeObservation(
            observation_id=observation_id,
            vm_id=vm_id,
            boot_id=boot_id,
            generation=generation,
            intent_digest=intent_digest,
            state=VMState.STOPPED,
            source=ObservationSource.DAEMON,
            observed_at=observed_at,
            process_absent=True,
            qmp_unreachable=True,
        )
    if target not in {
        VMState.QMP_RUNNING,
        VMState.GUEST_READY,
        VMState.QUIESCED,
    }:
        raise AssertionError(f"no observation profile for target {target.value}")
    return RuntimeObservation(
        observation_id=observation_id,
        vm_id=vm_id,
        boot_id=boot_id,
        generation=generation,
        intent_digest=intent_digest,
        state=target,
        source=ObservationSource.DAEMON,
        observed_at=observed_at,
        process=process,
        qmp_status="running",
        qmp_uuid=vm_id,
        qmp_greeting_sha256="c" * 64,
        guest_ready=True if target is VMState.GUEST_READY else None,
        guest_quiesced=True if target is VMState.QUIESCED else None,
    )


def _evidence(
    previous: VMState,
    target: VMState,
    second: int,
    *,
    record: VMRecord | None = None,
    observation: RuntimeObservation | None = None,
    **overrides: str,
) -> TransitionEvidence:
    required = TRANSITION_REQUIREMENTS[(previous, target)]
    if "observation_id" in required and observation is None:
        observation = _runtime_observation(
            previous,
            target,
            second,
            record=record,
        )
    facts = _facts(
        previous,
        target,
        intent_digest=(
            record.intent_digest
            if record is not None
            else FACT_VALUES["intent_digest"]
        ),
    )
    if observation is not None:
        facts["observation_id"] = str(observation.observation_id)
        if "boot_id" in required:
            if observation.boot_id is None:
                raise AssertionError("observed edge requires a boot identity")
            facts["boot_id"] = str(observation.boot_id)
        if "qmp_status" in required:
            facts.update(
                {
                    "qmp_status": str(observation.qmp_status),
                    "qmp_uuid": str(observation.qmp_uuid),
                    "qmp_greeting": (
                        f"sha256:{observation.qmp_greeting_sha256}"
                    ),
                }
            )
    facts.update(overrides)
    evidence_boot_id = (
        observation.boot_id
        if observation is not None
        else (
            UUID(facts["boot_id"])
            if "boot_id" in facts
            else (record.boot_id if record is not None else None)
        )
    )
    return TransitionEvidence(
        evidence_id=UUID(_uuid(100 + second)),
        observed_at=BASE_TIME + timedelta(seconds=second),
        facts=facts,
        vm_id=(record.vm_id if record is not None else VM_ID),
        generation=(record.generation if record is not None else 0),
        boot_id=evidence_boot_id,
        intent_digest=(
            record.intent_digest
            if record is not None
            else FACT_VALUES["intent_digest"]
        ),
        observation=observation,
    )


def _record_evidence(
    record: VMRecord,
    target: VMState,
    second: int,
    **overrides: str,
) -> TransitionEvidence:
    return _evidence(
        record.state,
        target,
        second,
        record=record,
        **overrides,
    )


def _transition_record(
    previous: VMState,
    target: VMState,
    second: int = 1,
) -> TransitionRecord:
    cause = _cause(when=BASE_TIME) if target is VMState.ERROR else None
    return TransitionRecord(
        previous=previous,
        target=target,
        evidence=_evidence(previous, target, second),
        cause=cause,
    )


def _definition() -> VMDefinition:
    return VMDefinition(
        name="persistent-aipc",
        disk_path="/var/lib/somnus/aipc.qcow2",
        memory_mib=4096,
        vcpus=2,
        enable_kvm=False,
    )


def _ports() -> VMPorts:
    return VMPorts(ssh=2222, agent=9901, vnc=5900)


def _declared_record() -> VMRecord:
    return VMRecord(
        definition=_definition(),
        ports=_ports(),
        vm_id=VM_ID,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    ).with_planned_runtime(
        qmp_socket="/run/somnus/aipc.qmp",
        pid_file="/run/somnus/aipc.pid",
        log_path="/var/log/somnus/aipc.log",
    )


def _booting_record() -> VMRecord:
    record = _declared_record()
    record = record.transition(
        VMState.PROVISIONING,
        _record_evidence(record, VMState.PROVISIONING, 1),
    )
    record = record.transition(
        VMState.PROVISIONED,
        _record_evidence(record, VMState.PROVISIONED, 2),
    )
    return record.transition(
        VMState.BOOTING,
        _record_evidence(
            record,
            VMState.BOOTING,
            3,
            boot_id=str(BOOT_ID),
        ),
        boot_id=BOOT_ID,
    )


def check_value_schema_roundtrips() -> None:
    """Round-trip every VM-family value schema with immutable semantics."""

    resources = VMResourceSpec(memory_mib=4096, vcpus=2)
    storage = StorageReference(
        storage_id=UUID(_uuid(10)),
        path="/var/lib/somnus/disks/aipc.qcow2",
        virtual_size_bytes=100 * 1024**3,
        format="qcow2",
        backing_storage_id=UUID(_uuid(11)),
        read_only=False,
    )
    image = ImageProvenance(
        image_id=UUID(_uuid(12)),
        source="/var/lib/somnus/images/ubuntu-24.04.qcow2",
        sha256="d" * 64,
        format="qcow2",
        virtual_size_bytes=100 * 1024**3,
        created_at=BASE_TIME,
    )
    process = _process()
    snapshot = SnapshotReference(
        snapshot_id=UUID(_uuid(13)),
        vm_id=VM_ID,
        generation=7,
        path="/var/lib/somnus/snapshots/aipc-7.qcow2",
        created_at=BASE_TIME,
        parent_snapshot_id=UUID(_uuid(14)),
    )
    observation = RuntimeObservation(
        observation_id=UUID(_uuid(15)),
        vm_id=VM_ID,
        boot_id=BOOT_ID,
        generation=7,
        intent_digest=FACT_VALUES["intent_digest"],
        state=VMState.QMP_RUNNING,
        source=ObservationSource.DAEMON,
        observed_at=BASE_TIME,
        process=process,
        qmp_status="running",
        qmp_uuid=VM_ID,
        qmp_greeting_sha256="c" * 64,
    )
    cause = _cause()
    evidence = _evidence(VMState.DECLARED, VMState.ERROR, 1)
    transition = TransitionRecord(
        previous=VMState.DECLARED,
        target=VMState.ERROR,
        evidence=evidence,
        cause=cause,
    )

    roundtrips = (
        (VMResourceSpec, resources),
        (StorageReference, storage),
        (ImageProvenance, image),
        (ProcessIdentity, process),
        (SnapshotReference, snapshot),
        (RuntimeObservation, observation),
        (TransitionCause, cause),
        (TransitionEvidence, evidence),
        (TransitionRecord, transition),
    )
    for schema, value in roundtrips:
        encoded = value.to_dict()
        assert schema.from_dict(encoded) == value
        with_unknown = {**encoded, "future_field": "hidden intent"}
        _expect_rejected(
            lambda schema=schema, value=with_unknown: schema.from_dict(value)
        )
        try:
            setattr(value, next(iter(encoded)), object())
        except (FrozenInstanceError, AttributeError, TypeError):
            pass
        else:
            raise AssertionError(f"{schema.__name__} remained mutable")

    _expect_rejected(lambda: VMResourceSpec(memory_mib=True, vcpus=2))
    _expect_rejected(
        lambda: StorageReference.from_dict(
            {**storage.to_dict(), "virtual_size_bytes": "107374182400"}
        )
    )
    _expect_rejected(
        lambda: ImageProvenance.from_dict(
            {**image.to_dict(), "sha256": "D" * 64}
        )
    )
    _expect_rejected(
        lambda: ProcessIdentity(
            pid=4242,
            start_time_ticks=9001,
            executable="qemu-system-x86_64",
            executable_sha256="b" * 64,
            observed_at=BASE_TIME,
        )
    )
    _expect_rejected(
        lambda: ProcessIdentity.from_dict(
            {
                key: value
                for key, value in process.to_dict().items()
                if key != "executable_sha256"
            }
        )
    )
    _expect_rejected(
        lambda: ProcessIdentity.from_dict(
            {**process.to_dict(), "executable_sha256": "B" * 64}
        )
    )
    _expect_rejected(
        lambda: SnapshotReference.from_dict(
            {**snapshot.to_dict(), "generation": 7.0}
        )
    )
    _expect_rejected(
        lambda: RuntimeObservation.from_dict(
            {**observation.to_dict(), "guest_ready": 1}
        )
    )

    source_facts = {"operation_id": _uuid(20)}
    frozen = TransitionEvidence(
        UUID(_uuid(21)),
        BASE_TIME,
        source_facts,
        vm_id=VM_ID,
        generation=0,
        boot_id=None,
        intent_digest=FACT_VALUES["intent_digest"],
    )
    source_facts["operation_id"] = _uuid(22)
    assert frozen.facts["operation_id"] == _uuid(20)
    try:
        frozen.facts["operation_id"] = _uuid(23)  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("transition facts remained mutable")


def check_complete_transition_authority() -> None:
    """Exercise every legal and illegal state pair and each evidence field."""

    legal_edges = {
        (previous, target)
        for previous, targets in ALLOWED_TRANSITIONS.items()
        for target in targets
    }
    assert set(TRANSITION_REQUIREMENTS) == legal_edges

    for previous, target in sorted(
        legal_edges, key=lambda edge: (edge[0].value, edge[1].value)
    ):
        transition = _transition_record(previous, target)
        assert TransitionRecord.from_dict(transition.to_dict()) == transition

        required = TRANSITION_REQUIREMENTS[(previous, target)]
        for missing in required:
            incomplete = _facts(previous, target)
            del incomplete[missing]
            if not incomplete:
                # TransitionEvidence itself requires at least one fact.  Use a
                # known but inapplicable fact so the edge still fails closed.
                incomplete["agent_ready"] = "true"
            evidence = TransitionEvidence(
                UUID(_uuid(30)),
                BASE_TIME + timedelta(seconds=1),
                incomplete,
                vm_id=VM_ID,
                generation=0,
                boot_id=(
                    UUID(incomplete["boot_id"])
                    if "boot_id" in incomplete
                    else None
                ),
                intent_digest=FACT_VALUES["intent_digest"],
            )
            _expect_rejected(
                lambda previous=previous,
                target=target,
                evidence=evidence: TransitionRecord(
                    previous,
                    target,
                    evidence,
                    _cause() if target is VMState.ERROR else None,
                ),
                VMTransitionError,
            )

        extra_key = next(
            key for key in FACT_VALUES if key not in required
        )
        evidence = TransitionEvidence(
            UUID(_uuid(31)),
            BASE_TIME + timedelta(seconds=1),
            {**_facts(previous, target), extra_key: FACT_VALUES[extra_key]},
            vm_id=VM_ID,
            generation=0,
            boot_id=(
                BOOT_ID if "boot_id" in required else None
            ),
            intent_digest=FACT_VALUES["intent_digest"],
        )
        _expect_rejected(
            lambda previous=previous,
            target=target,
            evidence=evidence: TransitionRecord(
                previous,
                target,
                evidence,
                _cause() if target is VMState.ERROR else None,
            ),
            VMTransitionError,
        )

    for previous in VMState:
        for target in VMState:
            if (previous, target) in legal_edges:
                continue
            _expect_rejected(
                lambda previous=previous, target=target: TransitionRecord(
                    previous,
                    target,
                    TransitionEvidence(
                        UUID(_uuid(32)),
                        BASE_TIME,
                        {"operation_id": _uuid(33)},
                        vm_id=VM_ID,
                        generation=0,
                        boot_id=None,
                        intent_digest=FACT_VALUES["intent_digest"],
                    ),
                ),
                VMTransitionError,
            )

    invalid_facts = (
        {"unknown_fact": "true"},
        {"agent_ready": "false"},
        {"operation_id": "not-a-uuid"},
        {"image_sha256": "A" * 64},
        {"qmp_status": "paused"},
        {"qmp_greeting": "QMP"},
        {
            "process_identity": (
                "pid:1;start:1;exe_sha256:" + ("a" * 64)
            )
        },
        {"process_exit": "exit:256"},
        {"process_exit": "signal:65"},
    )
    for facts in invalid_facts:
        _expect_rejected(
            lambda facts=facts: TransitionEvidence(
                UUID(_uuid(34)),
                BASE_TIME,
                facts,
                vm_id=VM_ID,
                generation=0,
                boot_id=None,
                intent_digest=FACT_VALUES["intent_digest"],
            )
        )

    missing_cause = _evidence(VMState.DECLARED, VMState.ERROR, 1)
    _expect_rejected(
        lambda: TransitionRecord(
            VMState.DECLARED,
            VMState.ERROR,
            missing_cause,
        ),
        VMTransitionError,
    )
    later_cause = _cause(when=BASE_TIME + timedelta(seconds=2))
    _expect_rejected(
        lambda: TransitionRecord(
            VMState.DECLARED,
            VMState.ERROR,
            missing_cause,
            later_cause,
        ),
        VMTransitionError,
    )


def check_runtime_observation_authority() -> None:
    """Reject contradictory observations and bind promotion to exact identity."""

    _expect_rejected(lambda: ObservationSource("operator"), ValueError)
    process = _process()
    qmp_running = RuntimeObservation(
        observation_id=UUID(_uuid(2_001)),
        vm_id=VM_ID,
        boot_id=BOOT_ID,
        generation=0,
        intent_digest=FACT_VALUES["intent_digest"],
        state=VMState.QMP_RUNNING,
        source=ObservationSource.DAEMON,
        observed_at=BASE_TIME,
        process=process,
        qmp_status="running",
        qmp_uuid=VM_ID,
        qmp_greeting_sha256="c" * 64,
    )
    guest_ready = RuntimeObservation(
        observation_id=UUID(_uuid(2_002)),
        vm_id=VM_ID,
        boot_id=BOOT_ID,
        generation=0,
        intent_digest=FACT_VALUES["intent_digest"],
        state=VMState.GUEST_READY,
        source=ObservationSource.GUEST_AGENT,
        observed_at=BASE_TIME,
        guest_ready=True,
    )
    stopped = RuntimeObservation(
        observation_id=UUID(_uuid(2_003)),
        vm_id=VM_ID,
        boot_id=BOOT_ID,
        generation=0,
        intent_digest=FACT_VALUES["intent_digest"],
        state=VMState.STOPPED,
        source=ObservationSource.DAEMON,
        observed_at=BASE_TIME,
        process_absent=True,
        qmp_unreachable=True,
    )
    process_only = RuntimeObservation(
        observation_id=UUID(_uuid(2_004)),
        vm_id=VM_ID,
        boot_id=BOOT_ID,
        generation=0,
        intent_digest=FACT_VALUES["intent_digest"],
        state=VMState.BOOTING,
        source=ObservationSource.PROCESS,
        observed_at=BASE_TIME,
        process=process,
    )
    raw_qmp = RuntimeObservation(
        observation_id=UUID(_uuid(2_005)),
        vm_id=VM_ID,
        boot_id=BOOT_ID,
        generation=0,
        intent_digest=FACT_VALUES["intent_digest"],
        state=VMState.QMP_RUNNING,
        source=ObservationSource.QMP,
        observed_at=BASE_TIME,
        qmp_status="running",
        qmp_uuid=VM_ID,
        qmp_greeting_sha256="c" * 64,
    )
    for observation in (
        qmp_running,
        guest_ready,
        stopped,
        process_only,
        raw_qmp,
    ):
        assert RuntimeObservation.from_dict(
            observation.to_dict()
        ) == observation

    hostile_observations = (
        lambda: replace(qmp_running, process=None),
        lambda: replace(qmp_running, qmp_status="paused"),
        lambda: replace(qmp_running, guest_ready=True),
        lambda: replace(guest_ready, process=process),
        lambda: replace(guest_ready, qmp_status="running"),
        lambda: replace(stopped, process=process),
        lambda: replace(stopped, process_absent=False),
        lambda: replace(
            stopped,
            qmp_status="running",
            qmp_uuid=VM_ID,
            qmp_greeting_sha256="c" * 64,
        ),
        lambda: replace(process_only, process=None),
        lambda: replace(
            process_only,
            qmp_status="running",
            qmp_uuid=VM_ID,
            qmp_greeting_sha256="c" * 64,
        ),
    )
    for hostile in hostile_observations:
        _expect_rejected(hostile, VMContractError)

    booting = _booting_record()
    raw_qmp_at_transition = replace(
        raw_qmp,
        observation_id=UUID(_uuid(2_010)),
        observed_at=BASE_TIME + timedelta(seconds=4),
    )
    raw_evidence = _evidence(
        VMState.BOOTING,
        VMState.QMP_RUNNING,
        4,
        record=booting,
        observation=raw_qmp_at_transition,
    )
    _expect_rejected(
        lambda: booting.transition(VMState.QMP_RUNNING, raw_evidence),
        VMTransitionError,
    )

    aggregate_evidence = _record_evidence(
        booting,
        VMState.QMP_RUNNING,
        4,
    )
    assert aggregate_evidence.observation is not None
    incoherent_envelopes = (
        replace(
            aggregate_evidence,
            vm_id=UUID("99999999-9999-4999-8999-999999999999"),
        ),
        replace(aggregate_evidence, generation=1),
        replace(aggregate_evidence, boot_id=NEXT_BOOT_ID),
        replace(aggregate_evidence, intent_digest="f" * 64),
        replace(
            aggregate_evidence,
            facts={
                **aggregate_evidence.facts,
                "intent_digest": "f" * 64,
            },
        ),
        replace(
            aggregate_evidence,
            observation=replace(
                aggregate_evidence.observation,
                intent_digest="f" * 64,
            ),
        ),
    )
    for incoherent in incoherent_envelopes:
        _expect_rejected(
            lambda incoherent=incoherent: TransitionRecord(
                VMState.BOOTING,
                VMState.QMP_RUNNING,
                incoherent,
            ),
            VMTransitionError,
        )
    mismatched_id = replace(
        aggregate_evidence,
        facts={
            **aggregate_evidence.facts,
            "observation_id": _uuid(2_099),
        },
    )
    _expect_rejected(
        lambda: booting.transition(VMState.QMP_RUNNING, mismatched_id),
        VMTransitionError,
    )
    later_observation = replace(
        aggregate_evidence.observation,
        observed_at=BASE_TIME + timedelta(seconds=5),
    )
    _expect_rejected(
        lambda: replace(
            aggregate_evidence,
            observation=later_observation,
        ),
        VMContractError,
    )
    other_vm = UUID("99999999-9999-4999-8999-999999999999")
    wrong_identity = replace(
        aggregate_evidence.observation,
        vm_id=other_vm,
        qmp_uuid=other_vm,
    )
    wrong_identity_evidence = _evidence(
        VMState.BOOTING,
        VMState.QMP_RUNNING,
        4,
        record=booting,
        observation=wrong_identity,
    )
    _expect_rejected(
        lambda: booting.transition(
            VMState.QMP_RUNNING,
            wrong_identity_evidence,
        ),
        VMTransitionError,
    )
    wrong_generation = replace(
        aggregate_evidence.observation,
        generation=1,
    )
    wrong_generation_evidence = _evidence(
        VMState.BOOTING,
        VMState.QMP_RUNNING,
        4,
        record=booting,
        observation=wrong_generation,
    )
    _expect_rejected(
        lambda: booting.transition(
            VMState.QMP_RUNNING,
            wrong_generation_evidence,
        ),
        VMTransitionError,
    )


def check_intent_and_process_binding() -> None:
    """Bind immutable intent, runtime paths, and the full QEMU process identity."""

    unplanned = VMRecord(
        _definition(),
        _ports(),
        vm_id=VM_ID,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )
    original_digest = unplanned.intent_digest
    planned = unplanned.with_planned_runtime(
        qmp_socket="/run/somnus/aipc.qmp",
        pid_file="/run/somnus/aipc.pid",
        log_path="/var/log/somnus/aipc.log",
    )
    assert planned.intent_digest != original_digest
    assert planned.qmp_socket == "/run/somnus/aipc.qmp"

    path_attacks = (
        {
            "qmp_socket": "relative.qmp",
            "pid_file": "/run/somnus/aipc.pid",
            "log_path": "/var/log/somnus/aipc.log",
        },
        {
            "qmp_socket": "/run/somnus/../escape.qmp",
            "pid_file": "/run/somnus/aipc.pid",
            "log_path": "/var/log/somnus/aipc.log",
        },
        {
            "qmp_socket": "/run/somnus/aipc.qmp,server=off",
            "pid_file": "/run/somnus/aipc.pid",
            "log_path": "/var/log/somnus/aipc.log",
        },
        {
            "qmp_socket": "/" + ("é" * 52),
            "pid_file": "/run/somnus/aipc.pid",
            "log_path": "/var/log/somnus/aipc.log",
        },
        {
            "qmp_socket": "/run/somnus/aipc.qmp",
            "pid_file": "relative.pid",
            "log_path": "/var/log/somnus/aipc.log",
        },
        {
            "qmp_socket": "/run/somnus/aipc.qmp",
            "pid_file": "/run/somnus/aipc.pid",
            "log_path": "../relative.log",
        },
    )
    for hostile_paths in path_attacks:
        _expect_rejected(
            lambda hostile_paths=hostile_paths: unplanned.with_planned_runtime(
                **hostile_paths
            ),
            VMContractError,
        )
    _expect_rejected(
        lambda: replace(
            unplanned,
            qmp_socket="/run/somnus/direct-replace.qmp",
        ),
        VMContractError,
    )

    history = planned.transition(
        VMState.PROVISIONING,
        _record_evidence(planned, VMState.PROVISIONING, 1),
    )
    assert all(
        transition.evidence.facts["intent_digest"] == history.intent_digest
        for transition in history.transitions
    )
    changed_definition = replace(_definition(), memory_mib=8192)
    changed_ports = VMPorts(ssh=2223, agent=9901, vnc=5900)
    intent_mutations = (
        {"definition": changed_definition},
        {"ports": changed_ports},
        {"vm_id": UUID("88888888-8888-4888-8888-888888888888")},
        {"generation": 1},
        {"qmp_socket": "/run/somnus/other.qmp"},
        {"pid_file": "/run/somnus/other.pid"},
        {"log_path": "/var/log/somnus/other.log"},
        {"created_at": BASE_TIME - timedelta(seconds=1)},
    )
    for mutation in intent_mutations:
        _expect_rejected(
            lambda mutation=mutation: replace(history, **mutation),
            VMContractError,
        )
    _expect_rejected(
        lambda: replace(history, intent_digest=""),
        VMContractError,
    )
    recomputed = VMRecord(
        definition=changed_definition,
        ports=history.ports,
        vm_id=history.vm_id,
        generation=history.generation,
        qmp_socket=history.qmp_socket,
        pid_file=history.pid_file,
        log_path=history.log_path,
        created_at=history.created_at,
        updated_at=history.created_at,
    )
    _expect_rejected(
        lambda: replace(
            history,
            definition=changed_definition,
            intent_digest=recomputed.intent_digest,
        ),
        VMContractError,
    )
    _expect_rejected(
        lambda: history.with_planned_runtime(
            qmp_socket="/run/somnus/new.qmp",
            pid_file="/run/somnus/new.pid",
            log_path="/var/log/somnus/new.log",
        ),
        VMTransitionError,
    )

    running = _booting_record()
    _expect_rejected(
        lambda: replace(running, process=_process()),
        VMContractError,
    )
    qmp_evidence = _record_evidence(running, VMState.QMP_RUNNING, 4)
    assert qmp_evidence.observation is not None
    running = running.transition(VMState.QMP_RUNNING, qmp_evidence)
    assert running.process == qmp_evidence.observation.process
    assert running.pid == 4242
    assert running.process is not None
    assert running.process.executable_sha256 == "b" * 64
    _expect_rejected(
        lambda: replace(
            running,
            process=replace(running.process, start_time_ticks=9002),
        ),
        VMContractError,
    )
    changed_process = replace(running.process, start_time_ticks=9002)
    contradictory_ready = replace(
        _runtime_observation(
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            5,
            record=running,
        ),
        process=changed_process,
    )
    changed_process_evidence = _evidence(
        VMState.QMP_RUNNING,
        VMState.GUEST_READY,
        5,
        record=running,
        observation=contradictory_ready,
    )
    _expect_rejected(
        lambda: running.transition(
            VMState.GUEST_READY,
            changed_process_evidence,
        ),
        VMTransitionError,
    )
    legacy_pid_wire = running.to_dict()
    legacy_pid_wire["pid"] = running.pid
    del legacy_pid_wire["process"]
    _expect_rejected(
        lambda: VMRecord.from_dict(legacy_pid_wire),
        VMContractError,
        VMMigrationError,
    )


def check_error_cause_uniqueness() -> None:
    """Reject event-id reuse across distinct ERROR transitions."""

    record = _declared_record()
    first_cause = _cause()
    record = record.transition(
        VMState.ERROR,
        _record_evidence(record, VMState.ERROR, 1),
        cause=first_cause,
    )
    record = record.transition(
        VMState.RECOVERING,
        _record_evidence(record, VMState.RECOVERING, 2),
    )
    reused_cause = _cause(
        event_id=first_cause.event_id,
        when=BASE_TIME + timedelta(seconds=2),
    )
    _expect_rejected(
        lambda: record.transition(
            VMState.ERROR,
            _record_evidence(record, VMState.ERROR, 3),
            cause=reused_cause,
        ),
        VMContractError,
    )

    distinct_id = UUID(_uuid(2_500))
    distinct_cause = _cause(
        event_id=distinct_id,
        when=BASE_TIME + timedelta(seconds=2),
    )
    second_error = record.transition(
        VMState.ERROR,
        _record_evidence(
            record,
            VMState.ERROR,
            3,
            cause_event_id=str(distinct_id),
        ),
        cause=distinct_cause,
    )
    assert second_error.last_error == distinct_cause


def check_history_replay_resistance() -> None:
    """Reject boot, observation, and process replay across one generation."""

    record = _booting_record()
    first_qmp_evidence = _record_evidence(
        record,
        VMState.QMP_RUNNING,
        4,
    )
    assert first_qmp_evidence.observation is not None
    first_observation_id = first_qmp_evidence.observation.observation_id
    record = record.transition(VMState.QMP_RUNNING, first_qmp_evidence)
    assert record.process is not None
    first_core = (
        record.process.pid,
        record.process.start_time_ticks,
        record.process.executable,
        record.process.executable_sha256,
    )
    first_process_observed_at = record.process.to_dict()["observed_at"]

    equal_time_observation = replace(
        _runtime_observation(
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            5,
            record=record,
        ),
        process=record.process,
    )
    _expect_rejected(
        lambda: record.transition(
            VMState.GUEST_READY,
            _evidence(
                VMState.QMP_RUNNING,
                VMState.GUEST_READY,
                5,
                record=record,
                observation=equal_time_observation,
            ),
        ),
        VMTransitionError,
    )

    fresh_process = replace(
        record.process,
        observed_at=BASE_TIME + timedelta(seconds=5),
    )
    repeated_observation = replace(
        _runtime_observation(
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            5,
            record=record,
        ),
        observation_id=first_observation_id,
        process=fresh_process,
    )
    _expect_rejected(
        lambda: record.transition(
            VMState.GUEST_READY,
            _evidence(
                VMState.QMP_RUNNING,
                VMState.GUEST_READY,
                5,
                record=record,
                observation=repeated_observation,
            ),
        ),
        VMContractError,
    )

    fresh_observation = replace(
        _runtime_observation(
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            5,
            record=record,
        ),
        process=fresh_process,
    )
    record = record.transition(
        VMState.GUEST_READY,
        _evidence(
            VMState.QMP_RUNNING,
            VMState.GUEST_READY,
            5,
            record=record,
            observation=fresh_observation,
        ),
    )
    assert record.process is not None
    assert record.process.observed_at == BASE_TIME + timedelta(seconds=5)
    assert (
        record.process.pid,
        record.process.start_time_ticks,
        record.process.executable,
        record.process.executable_sha256,
    ) == first_core

    equal_time_wire = record.to_dict()
    process_wire = equal_time_wire["process"]
    transitions_wire = equal_time_wire["transitions"]
    assert isinstance(process_wire, dict)
    assert isinstance(transitions_wire, list)
    latest_transition_wire = transitions_wire[-1]
    assert isinstance(latest_transition_wire, dict)
    evidence_wire = latest_transition_wire["evidence"]
    assert isinstance(evidence_wire, dict)
    observation_wire = evidence_wire["observation"]
    assert isinstance(observation_wire, dict)
    observation_process_wire = observation_wire["process"]
    assert isinstance(observation_process_wire, dict)
    process_wire["observed_at"] = first_process_observed_at
    observation_process_wire["observed_at"] = first_process_observed_at
    _expect_rejected(
        lambda: VMRecord.from_dict(equal_time_wire),
        VMContractError,
    )

    record = record.transition(
        VMState.STOPPING,
        _record_evidence(record, VMState.STOPPING, 6),
    )
    record = record.transition(
        VMState.STOPPED,
        _record_evidence(record, VMState.STOPPED, 7),
    )
    record = record.transition(
        VMState.BOOTING,
        _record_evidence(
            record,
            VMState.BOOTING,
            8,
            boot_id=str(NEXT_BOOT_ID),
        ),
        boot_id=NEXT_BOOT_ID,
    )

    replayed_process = _process(
        when=BASE_TIME + timedelta(seconds=9),
    )
    replayed_process_observation = replace(
        _runtime_observation(
            VMState.BOOTING,
            VMState.QMP_RUNNING,
            9,
            record=record,
        ),
        process=replayed_process,
    )
    _expect_rejected(
        lambda: record.transition(
            VMState.QMP_RUNNING,
            _evidence(
                VMState.BOOTING,
                VMState.QMP_RUNNING,
                9,
                record=record,
                observation=replayed_process_observation,
            ),
        ),
        VMContractError,
    )

    second_process = _process(
        when=BASE_TIME + timedelta(seconds=9),
        pid=4243,
        start_time_ticks=9002,
    )
    second_process_observation = replace(
        _runtime_observation(
            VMState.BOOTING,
            VMState.QMP_RUNNING,
            9,
            record=record,
        ),
        process=second_process,
    )
    record = record.transition(
        VMState.QMP_RUNNING,
        _evidence(
            VMState.BOOTING,
            VMState.QMP_RUNNING,
            9,
            record=record,
            observation=second_process_observation,
        ),
    )
    record = record.transition(
        VMState.STOPPING,
        _record_evidence(record, VMState.STOPPING, 10),
    )
    record = record.transition(
        VMState.STOPPED,
        _record_evidence(record, VMState.STOPPED, 11),
    )
    _expect_rejected(
        lambda: record.transition(
            VMState.BOOTING,
            _record_evidence(
                record,
                VMState.BOOTING,
                12,
                boot_id=str(BOOT_ID),
            ),
            boot_id=BOOT_ID,
        ),
        VMContractError,
    )


def check_immutable_record_lifecycle() -> None:
    """Drive a real record value through provision, boot, snapshot, and destroy."""

    record = _declared_record()
    assert VMRecord.from_dict(record.to_dict()) == record
    assert record.intent_digest != FACT_VALUES["intent_digest"]
    try:
        record.state = VMState.ERROR  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("VMRecord remained mutable")
    _expect_rejected(
        lambda: replace(record, state=VMState.PROVISIONED),
        VMContractError,
    )

    record = record.transition(
        VMState.PROVISIONING,
        _record_evidence(record, VMState.PROVISIONING, 1),
    )
    record = record.transition(
        VMState.PROVISIONED,
        _record_evidence(record, VMState.PROVISIONED, 2),
    )
    record = record.transition(
        VMState.BOOTING,
        _record_evidence(
            record,
            VMState.BOOTING,
            3,
            boot_id=str(BOOT_ID),
        ),
        boot_id=BOOT_ID,
    )
    record = record.transition(
        VMState.QMP_RUNNING,
        _record_evidence(
            record,
            VMState.QMP_RUNNING,
            4,
            boot_id=str(BOOT_ID),
            qmp_uuid=str(VM_ID),
        ),
    )
    assert record.process is not None
    assert record.pid == record.process.pid == 4242
    record = record.transition(
        VMState.GUEST_READY,
        _record_evidence(
            record,
            VMState.GUEST_READY,
            5,
            boot_id=str(BOOT_ID),
        ),
    )
    assert record.transition(VMState.GUEST_READY) is record
    _expect_rejected(
        lambda: record.transition(
            VMState.GUEST_READY,
            _evidence(
                VMState.QMP_RUNNING,
                VMState.GUEST_READY,
                6,
                record=record,
                boot_id=str(BOOT_ID),
            ),
        ),
        VMTransitionError,
    )
    record = record.transition(
        VMState.QUIESCING,
        _record_evidence(record, VMState.QUIESCING, 6),
    )
    record = record.transition(
        VMState.QUIESCED,
        _record_evidence(record, VMState.QUIESCED, 7),
    )
    record = record.transition(
        VMState.SNAPSHOTTING,
        _record_evidence(record, VMState.SNAPSHOTTING, 8),
    )
    record = record.transition(
        VMState.QUIESCED,
        _record_evidence(record, VMState.QUIESCED, 9),
    )
    record = record.transition(
        VMState.STOPPING,
        _record_evidence(record, VMState.STOPPING, 10),
    )
    assert record.transition(VMState.STOPPING) is record
    record = record.transition(
        VMState.STOPPED,
        _record_evidence(record, VMState.STOPPED, 11),
    )
    assert record.pid is None
    assert record.transition(VMState.STOPPED) is record
    record = record.transition(
        VMState.ROLLING_BACK,
        _record_evidence(record, VMState.ROLLING_BACK, 12),
    )
    record = record.transition(
        VMState.STOPPED,
        _record_evidence(record, VMState.STOPPED, 13),
    )
    record = record.transition(
        VMState.DESTROYED,
        _record_evidence(record, VMState.DESTROYED, 14),
    )
    assert record.state is VMState.DESTROYED
    assert record.pid is None
    assert VMRecord.from_dict(record.to_dict()) == record
    for target in VMState:
        _expect_rejected(
            lambda target=target: record.transition(target),
            VMTransitionError,
        )

    replacement = record.new_generation()
    assert replacement.state is VMState.DECLARED
    assert replacement.vm_id == record.vm_id
    assert replacement.generation == record.generation + 1
    assert not replacement.transitions
    assert replacement.boot_id is None
    _expect_rejected(
        lambda: replacement.new_generation(),
        VMTransitionError,
    )

    wrong_boot = _declared_record()
    wrong_boot = wrong_boot.transition(
        VMState.PROVISIONING,
        _record_evidence(wrong_boot, VMState.PROVISIONING, 1),
    )
    wrong_boot = wrong_boot.transition(
        VMState.PROVISIONED,
        _record_evidence(wrong_boot, VMState.PROVISIONED, 2),
    )
    _expect_rejected(
        lambda: wrong_boot.transition(
            VMState.BOOTING,
            _record_evidence(
                wrong_boot,
                VMState.BOOTING,
                3,
                boot_id=str(BOOT_ID),
            ),
            boot_id=NEXT_BOOT_ID,
        ),
        VMTransitionError,
    )


def check_error_recovery_and_reconciliation() -> None:
    """Bind ERROR to causes and prove repeat-safe recovery/reconciliation."""

    record = _declared_record()
    cause = _cause()
    record = record.transition(
        VMState.ERROR,
        _record_evidence(record, VMState.ERROR, 1),
        cause=cause,
    )
    assert record.last_error == cause
    record = record.transition(
        VMState.RECOVERING,
        _record_evidence(record, VMState.RECOVERING, 2),
    )
    assert record.last_error == cause
    assert record.transition(VMState.RECOVERING) is record

    stopped = _declared_record()
    stopped = stopped.transition(
        VMState.PROVISIONING,
        _record_evidence(stopped, VMState.PROVISIONING, 1),
    )
    stopped = stopped.transition(
        VMState.PROVISIONED,
        _record_evidence(stopped, VMState.PROVISIONED, 2),
    )
    stopped = stopped.transition(
        VMState.BOOTING,
        _record_evidence(
            stopped,
            VMState.BOOTING,
            3,
            boot_id=str(BOOT_ID),
        ),
        boot_id=BOOT_ID,
    )
    stopped = stopped.transition(
        VMState.QMP_RUNNING,
        _record_evidence(
            stopped,
            VMState.QMP_RUNNING,
            4,
            boot_id=str(BOOT_ID),
            qmp_uuid=str(VM_ID),
        ),
    )
    stopped = stopped.transition(
        VMState.RECONCILING,
        _record_evidence(stopped, VMState.RECONCILING, 5),
    )
    assert stopped.process is None
    assert stopped.transition(VMState.RECONCILING) is stopped
    stopped = stopped.transition(
        VMState.STOPPED,
        _record_evidence(stopped, VMState.STOPPED, 6),
    )
    assert stopped.pid is None


def check_v0_migration() -> None:
    """Migrate only the exact v0 shape without carrying its plaintext secret."""

    legacy = json.loads(FIXTURE.read_text(encoding="utf-8"))
    first = migrate_vm_record(legacy)
    second = migrate_vm_record(legacy)
    assert first == second
    assert migrate_vm_record(first) == first
    serialized = json.dumps(first, sort_keys=True)
    assert "agent_token" not in first
    assert "legacy-fixture-token-never-use" not in serialized
    assert first["schema_version"] == 1
    assert first["state"] == "declared"
    assert first["migration_notes"] == [
        "legacy_agent_secret_reprovision_required"
    ]
    migrated = VMRecord.from_dict(legacy)
    assert migrated.to_dict() == first
    assert migrated.migration_source == "legacy_v0"
    native_same_identity = VMRecord(
        definition=migrated.definition,
        ports=migrated.ports,
        vm_id=migrated.vm_id,
        qmp_socket=migrated.qmp_socket,
        pid_file=migrated.pid_file,
        log_path=migrated.log_path,
        created_at=migrated.created_at,
        updated_at=migrated.updated_at,
    )
    assert native_same_identity.intent_digest != migrated.intent_digest
    _expect_rejected(
        lambda: VMRecord.from_dict(
            {**first, "migration_notes": []}
        ),
        VMContractError,
        VMMigrationError,
    )
    _expect_rejected(
        lambda: replace(migrated, migration_notes=()),
        VMContractError,
    )
    _expect_rejected(
        lambda: replace(
            migrated,
            migration_notes=(),
            intent_digest="",
        ),
        VMContractError,
    )
    _expect_rejected(
        lambda: VMRecord.from_dict(
            {
                **native_same_identity.to_dict(),
                "migration_notes": [
                    "legacy_agent_secret_reprovision_required"
                ],
            }
        ),
        VMContractError,
        VMMigrationError,
    )
    _expect_rejected(
        lambda: replace(
            native_same_identity,
            migration_notes=(
                "legacy_agent_secret_reprovision_required",
            ),
            intent_digest="",
        ),
        VMContractError,
    )

    for unverified_state in (
        "starting",
        "running",
        "ready",
        "stopping",
        "stopped",
        "error",
    ):
        rejected = _expect_rejected(
            lambda unverified_state=unverified_state: migrate_vm_record(
                {**legacy, "state": unverified_state}
            ),
            VMMigrationError,
        )
        assert "P2 reconciliation/import" in str(rejected)
    _expect_rejected(
        lambda: migrate_vm_record({**legacy, "state": "future-state"}),
        VMMigrationError,
    )
    _expect_rejected(
        lambda: migrate_vm_record({**legacy, "pid": 4242}),
        VMMigrationError,
    )
    _expect_rejected(
        lambda: migrate_vm_record({**legacy, "unknown": True}),
        VMMigrationError,
    )
    _expect_rejected(
        lambda: migrate_vm_record(
            {
                key: value
                for key, value in legacy.items()
                if key != "ports"
            }
        ),
        VMMigrationError,
    )
    _expect_rejected(
        lambda: migrate_vm_record({**first, "schema_version": 2}),
        VMMigrationError,
    )
    _expect_rejected(
        lambda: migrate_vm_record({**first, "schema_version": "1"}),
        VMMigrationError,
    )
    _expect_rejected(
        lambda: VMRecord.from_dict({**first, "future_field": True}),
        VMMigrationError,
        VMContractError,
    )

    secret_legacy_error = {
        **legacy,
        "state": "error",
        "last_error": "operator-secret-must-not-cross-migration",
    }
    rejection = _expect_rejected(
        lambda: migrate_vm_record(secret_legacy_error),
        VMMigrationError,
    )
    assert "operator-secret-must-not-cross-migration" not in str(rejection)
    declared_error = {
        **legacy,
        "last_error": "operator-secret-must-not-cross-migration",
    }
    rejection = _expect_rejected(
        lambda: migrate_vm_record(declared_error),
        VMMigrationError,
    )
    assert "P2 reconciliation/import" in str(rejection)
    assert "operator-secret-must-not-cross-migration" not in str(rejection)


def check_import_isolation() -> None:
    """Prove the protocol import does not activate host, guest, or donor code."""

    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import json,sys;"
                f"sys.path.insert(0,{str(SOURCE_ROOT)!r});"
                "import somnus_protocol.vm;"
                "print(json.dumps(sorted(name for name in sys.modules "
                "if name.startswith(('somnus_vm','somnus_guest','components')))))"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == []


def run_vm_lifecycle_checks() -> str:
    check_value_schema_roundtrips()
    check_complete_transition_authority()
    check_runtime_observation_authority()
    check_intent_and_process_binding()
    check_error_cause_uniqueness()
    check_history_replay_resistance()
    check_immutable_record_lifecycle()
    check_error_recovery_and_reconciliation()
    check_v0_migration()
    check_import_isolation()
    return (
        "round-tripped every canonical VM schema; exhaustively enforced all "
        "lifecycle edges and typed evidence; rejected contradictory runtime "
        "observations, intent/history drift, process-identity substitution, "
        "nonconsecutive boot/process/observation replay, unsafe QMP paths, "
        "duplicate error causes, migration-provenance tampering, and "
        "unverified v0 states; "
        "proved immutable records, exact DECLARED migration, terminal "
        "destruction/new generation, idempotent operations, and cold import "
        "isolation"
    )


if __name__ == "__main__":
    print(run_vm_lifecycle_checks())
