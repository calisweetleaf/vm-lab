"""Physical P4/P5 proof for append-only disk and process-exit observations.

The tests use real SQLite files, transactions, advisory ownership, raw tamper
attempts, migration backups, and concurrent threads.  No mock, stub, fallback,
or lifecycle transition is accepted as runtime evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from somnus_protocol import (
    ProcessIdentity,
    TransitionEvidence,
    VMDefinition,
    VMPorts,
    VMRecord,
    VMState,
)
from somnus_vm.host.images import ImageManifest
from somnus_vm.host.registry import (
    P3_REGISTRY_SCHEMA_VERSION,
    P4_REGISTRY_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    DiskRecord,
    DiskRuntimeObservation,
    ProcessExitObservation,
    RegistryConflictError,
    RegistryMigrationError,
    RegistryMode,
    RegistryNotFoundError,
    SQLiteRegistry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_MANIFEST = (
    PROJECT_ROOT
    / "test"
    / "vm_lab"
    / "fixtures"
    / "images"
    / "ubuntu-minimal-noble-amd64-20260801.json"
)
QEMU_VERSION = "qemu-img version 8.2.2 (registry P4 fixture)"
BASE_TIME = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

P4_INDEXES = {
    "disk_identity_binding",
    "operation_identity_binding",
    "process_identity_binding",
    "disk_runtime_observation_order",
}
P4_TRIGGERS = {
    "disk_runtime_observation_owner_insert",
    "disk_runtime_observation_monotonic_insert",
    "disk_runtime_observation_immutable_update",
    "disk_runtime_observation_immutable_delete",
}
P5_INDEXES = {
    "process_identity_retirement_binding",
    "process_exit_per_identity",
    "process_exit_observation_order",
}
P5_TRIGGERS = {
    "process_exit_observation_owner_insert",
    "process_exit_observation_retire_insert",
    "process_exit_observation_immutable_update",
    "process_exit_observation_immutable_delete",
    "process_identity_active_insert",
    "process_identity_retirement_guard",
}
OBSERVATION_COLUMNS = {
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
PROCESS_EXIT_COLUMNS = {
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


@dataclass(frozen=True, slots=True)
class _Authority:
    vm: VMRecord
    disk: DiskRecord
    operation_id: UUID
    process_record_id: UUID
    process: ProcessIdentity
    process_observed_at: datetime


def _transition_evidence(
    record: VMRecord,
    *,
    seconds: int,
    facts: dict[str, str],
    boot_id: UUID | None = None,
) -> TransitionEvidence:
    return TransitionEvidence(
        evidence_id=uuid4(),
        observed_at=BASE_TIME + timedelta(seconds=seconds),
        facts={"intent_digest": record.intent_digest, **facts},
        vm_id=record.vm_id,
        generation=record.generation,
        boot_id=boot_id,
        intent_digest=record.intent_digest,
    )


def _booting_record(root: Path, name: str = "p4-registry") -> VMRecord:
    record = VMRecord(
        definition=VMDefinition(
            name=name,
            disk_path=str(root / f"{name}.qcow2"),
            memory_mib=4_096,
            vcpus=2,
            enable_kvm=False,
        ),
        ports=VMPorts(ssh=22_201, agent=23_201, vnc=5_901),
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    ).with_planned_runtime(
        qmp_socket=str(root / f"{name}.qmp"),
        pid_file=str(root / f"{name}.pid"),
        log_path=str(root / f"{name}.log"),
    )
    record = record.transition(
        VMState.PROVISIONING,
        _transition_evidence(
            record,
            seconds=1,
            facts={"operation_id": str(uuid4())},
        ),
    )
    record = record.transition(
        VMState.PROVISIONED,
        _transition_evidence(
            record,
            seconds=2,
            facts={
                "image_sha256": "a" * 64,
                "operation_id": str(uuid4()),
                "storage_id": str(uuid4()),
            },
        ),
    )
    boot_id = uuid4()
    return record.transition(
        VMState.BOOTING,
        _transition_evidence(
            record,
            seconds=3,
            facts={
                "boot_id": str(boot_id),
                "operation_id": str(uuid4()),
            },
            boot_id=boot_id,
        ),
        boot_id=boot_id,
    )


def _populate_authority(
    registry: SQLiteRegistry,
    root: Path,
) -> _Authority:
    manifest = ImageManifest.from_json(IMAGE_MANIFEST.read_bytes())
    vm = _booting_record(root)
    import_operation = uuid4()
    overlay_operation = uuid4()
    observation_operation = uuid4()
    process_record_id = uuid4()
    process_observed_at = datetime.now(timezone.utc) + timedelta(seconds=2)

    with registry.write_transaction() as transaction:
        transaction.insert_vm(vm)
        transaction.begin_operation(
            import_operation,
            None,
            "storage.import_base",
            f"import-{import_operation}",
            request_hash="1" * 64,
            status="completed",
        )
        transaction.register_base_image(
            manifest,
            root / "base.qcow2",
            device_id=11,
            inode=101,
            file_size_bytes=manifest.size_bytes,
            allocated_size_bytes=manifest.size_bytes,
            mode=0o444,
            qemu_img_version=QEMU_VERSION,
            chain_sha256="2" * 64,
            operation_id=import_operation,
        )
        transaction.begin_operation(
            overlay_operation,
            vm.vm_id,
            "storage.create_overlay",
            f"overlay-{overlay_operation}",
            request_hash="3" * 64,
            status="completed",
        )
        disk = transaction.claim_disk(
            vm.vm_id,
            vm.definition.disk_path,
            operation_id=overlay_operation,
        )
        disk = transaction.materialize_disk(
            disk.disk_id,
            ownership_token=disk.ownership_token,
            base_image_id=manifest.image_id,
            base_image_sha256=manifest.sha256,
            virtual_size_bytes=100 * 1024**3,
            format="qcow2",
            device_id=12,
            inode=102,
            file_size_bytes=196_616,
            allocated_size_bytes=200_704,
            chain_sha256="4" * 64,
            marker_sha256="5" * 64,
            qemu_img_version=QEMU_VERSION,
            operation_id=overlay_operation,
        )
        transaction.begin_operation(
            observation_operation,
            vm.vm_id,
            "runtime.observe_disk",
            f"observe-{observation_operation}",
            request_hash="6" * 64,
            status="running",
        )
        process = ProcessIdentity(
            pid=42_424,
            start_time_ticks=90_001,
            executable="/usr/bin/qemu-system-x86_64",
            executable_sha256="7" * 64,
            observed_at=process_observed_at,
        )
        transaction.record_process_identity(
            vm.vm_id,
            process,
            boot_id=vm.boot_id,
            process_record_id=process_record_id,
            operation_id=observation_operation,
        )

    return _Authority(
        vm=vm,
        disk=disk,
        operation_id=observation_operation,
        process_record_id=process_record_id,
        process=process,
        process_observed_at=process_observed_at,
    )


def _append(
    transaction: object,
    authority: _Authority,
    observed_at: datetime,
    *,
    observation_id: UUID | None = None,
    file_size_bytes: object = 262_144,
    allocated_size_bytes: object = 266_240,
    qemu_actual_size_bytes: object = 270_336,
    dirty: object = False,
    corrupt: object = False,
    quiesced: object = False,
    operation_id: UUID | None = None,
    vm_id: UUID | None = None,
    generation: object | None = None,
    boot_id: UUID | None | object = ...,
    process_record_id: UUID | None | object = ...,
) -> DiskRuntimeObservation:
    append = getattr(transaction, "append_disk_runtime_observation")
    selected_boot = authority.vm.boot_id if boot_id is ... else boot_id
    selected_process = (
        authority.process_record_id
        if process_record_id is ...
        else process_record_id
    )
    return append(
        authority.disk.disk_id,
        vm_id=authority.vm.vm_id if vm_id is None else vm_id,
        generation=(
            authority.vm.generation
            if generation is None
            else generation
        ),
        observed_at=observed_at,
        file_size_bytes=file_size_bytes,
        allocated_size_bytes=allocated_size_bytes,
        qemu_actual_size_bytes=qemu_actual_size_bytes,
        dirty=dirty,
        corrupt=corrupt,
        quiesced=quiesced,
        operation_id=(
            authority.operation_id
            if operation_id is None
            else operation_id
        ),
        observation_id=observation_id,
        boot_id=selected_boot,
        process_record_id=selected_process,
    )


def _downgrade_exact_v4_to_v3(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        for trigger in sorted(P4_TRIGGERS):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP INDEX disk_runtime_observation_order")
        connection.execute("DROP TABLE disk_runtime_observations")
        for index in sorted(P4_INDEXES - {"disk_runtime_observation_order"}):
            connection.execute(f"DROP INDEX {index}")
        connection.execute(
            f"PRAGMA user_version={P3_REGISTRY_SCHEMA_VERSION}"
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _downgrade_exact_v5_to_v4(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        for trigger in sorted(P5_TRIGGERS):
            connection.execute(f"DROP TRIGGER {trigger}")
        for index in sorted(P5_INDEXES):
            connection.execute(f"DROP INDEX {index}")
        connection.execute("DROP TABLE process_exit_observations")
        connection.execute(
            f"PRAGMA user_version={P4_REGISTRY_SCHEMA_VERSION}"
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _downgrade_exact_v3_to_v2(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE disks_v2(
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
                created_at TEXT NOT NULL,
                released_at TEXT,
                FOREIGN KEY(vm_id,generation)
                    REFERENCES vms(vm_id,generation) ON DELETE RESTRICT
            ) STRICT
            """
        )
        connection.execute(
            """
            INSERT INTO disks_v2(
                disk_id,vm_id,path,generation,ownership,active,operation_id,
                released_by_operation_id,created_at,released_at
            )
            SELECT
                disk_id,vm_id,path,generation,ownership,active,operation_id,
                released_by_operation_id,created_at,released_at
            FROM disks
            """
        )
        connection.execute("DROP TABLE disks")
        connection.execute("DROP TABLE base_images")
        connection.execute("ALTER TABLE disks_v2 RENAME TO disks")
        connection.execute(
            "CREATE UNIQUE INDEX active_disk_path "
            "ON disks(path) WHERE active=1"
        )
        connection.execute(
            "CREATE UNIQUE INDEX active_vm_disk "
            "ON disks(vm_id,generation) WHERE active=1"
        )
        connection.execute("PRAGMA user_version=2")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


class RegistryP4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-registry-p4-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()
        self.database = self.root / "state" / "registry.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_process_exit_is_exact_atomic_idempotent_recovery_authority(
        self,
    ) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            recorded = registry.get_process_identity(
                authority.process_record_id
            )
            self.assertIsNotNone(recorded)
            assert recorded is not None
            self.assertTrue(recorded.active)
            self.assertEqual(recorded.process, authority.process)
            self.assertEqual(
                registry.get_active_process_identity(
                    authority.vm.vm_id,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                ),
                recorded,
            )
            self.assertEqual(
                registry.list_process_identities(active_only=True),
                (recorded,),
            )

            rolled_back_evidence = uuid4()
            with self.assertRaisesRegex(RuntimeError, "force rollback"):
                with registry.write_transaction() as transaction:
                    transaction.retire_process_identity(
                        authority.process_record_id,
                        authority.vm.vm_id,
                        authority.process,
                        generation=authority.vm.generation,
                        boot_id=authority.vm.boot_id,
                        original_operation_id=authority.operation_id,
                        observed_absent_at=(
                            authority.process_observed_at
                            + timedelta(seconds=1)
                        ),
                        reason="process_absent",
                        exit_evidence_id=rolled_back_evidence,
                    )
                    raise RuntimeError("force rollback")
            self.assertIsNone(
                registry.get_process_exit_observation(
                    rolled_back_evidence
                )
            )
            after_rollback = registry.get_process_identity(
                authority.process_record_id
            )
            self.assertIsNotNone(after_rollback)
            assert after_rollback is not None
            self.assertTrue(after_rollback.active)

            exit_evidence_id = uuid4()
            observed_absent_at = (
                authority.process_observed_at + timedelta(seconds=2)
            )
            with registry.write_transaction() as transaction:
                self.assertEqual(
                    transaction.get_process_identity(
                        authority.process_record_id
                    ),
                    after_rollback,
                )
                exit_observation = transaction.retire_process_identity(
                    authority.process_record_id,
                    authority.vm.vm_id,
                    authority.process,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                    original_operation_id=authority.operation_id,
                    observed_absent_at=observed_absent_at,
                    reason="process_absent",
                    exit_evidence_id=exit_evidence_id,
                )
                replay = transaction.retire_process_identity(
                    authority.process_record_id,
                    authority.vm.vm_id,
                    authority.process,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                    original_operation_id=authority.operation_id,
                    observed_absent_at=observed_absent_at,
                    reason="process_absent",
                    exit_evidence_id=exit_evidence_id,
                )
                self.assertEqual(
                    transaction.get_process_exit_observation(
                        exit_evidence_id
                    ),
                    exit_observation,
                )
            self.assertIsInstance(
                exit_observation,
                ProcessExitObservation,
            )
            self.assertEqual(replay, exit_observation)
            self.assertEqual(
                registry.get_process_exit_observation(exit_evidence_id),
                exit_observation,
            )
            self.assertEqual(
                registry.list_process_exit_observations(
                    process_record_id=authority.process_record_id,
                ),
                (exit_observation,),
            )
            retired = registry.get_process_identity(
                authority.process_record_id
            )
            self.assertIsNotNone(retired)
            assert retired is not None
            self.assertFalse(retired.active)
            self.assertIsNone(
                registry.get_active_process_identity(
                    authority.vm.vm_id,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                )
            )
            self.assertEqual(
                registry.list_process_identities(
                    vm_id=authority.vm.vm_id,
                    generation=authority.vm.generation,
                    active_only=True,
                ),
                (),
            )
            self.assertEqual(
                registry.list_process_identities(
                    vm_id=authority.vm.vm_id,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                ),
                (retired,),
            )
            equal_time_process = ProcessIdentity(
                pid=authority.process.pid + 1,
                start_time_ticks=(
                    authority.process.start_time_ticks + 1
                ),
                executable=authority.process.executable,
                executable_sha256=(
                    authority.process.executable_sha256
                ),
                observed_at=observed_absent_at,
            )
            with self.assertRaisesRegex(
                RegistryConflictError,
                "not newer than prior exit evidence",
            ):
                with registry.write_transaction() as transaction:
                    transaction.record_process_identity(
                        authority.vm.vm_id,
                        equal_time_process,
                        boot_id=authority.vm.boot_id,
                        process_record_id=uuid4(),
                        operation_id=authority.operation_id,
                    )
            decoded = json.loads(exit_observation.canonical_bytes)
            self.assertEqual(
                decoded,
                {
                    "boot_id": str(authority.vm.boot_id),
                    "exit_evidence_id": str(exit_evidence_id),
                    "generation": authority.vm.generation,
                    "observed_absent_at": observed_absent_at.isoformat(),
                    "original_operation_id": str(authority.operation_id),
                    "process": authority.process.to_dict(),
                    "process_record_id": str(
                        authority.process_record_id
                    ),
                    "reason": "process_absent",
                    "vm_id": str(authority.vm.vm_id),
                },
            )
            self.assertEqual(
                hashlib.sha256(
                    exit_observation.canonical_bytes
                ).hexdigest(),
                exit_observation.canonical_sha256,
            )
            with self.assertRaises(FrozenInstanceError):
                exit_observation.reason = "forged"  # type: ignore[misc]

        with SQLiteRegistry.open(self.database) as reopened:
            with reopened.write_transaction() as transaction:
                replay_after_reopen = transaction.retire_process_identity(
                    authority.process_record_id,
                    authority.vm.vm_id,
                    authority.process,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                    original_operation_id=authority.operation_id,
                    observed_absent_at=observed_absent_at,
                    reason="process_absent",
                    exit_evidence_id=exit_evidence_id,
                )
            self.assertEqual(replay_after_reopen, exit_observation)

    def test_process_retirement_rejects_foreign_stale_and_bulk_replacement(
        self,
    ) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            later_process = ProcessIdentity(
                pid=authority.process.pid + 1,
                start_time_ticks=authority.process.start_time_ticks + 1,
                executable=authority.process.executable,
                executable_sha256=authority.process.executable_sha256,
                observed_at=(
                    authority.process_observed_at + timedelta(seconds=1)
                ),
            )
            with registry.write_transaction() as transaction:
                with self.assertRaisesRegex(
                    RegistryConflictError,
                    "retired explicitly",
                ):
                    transaction.record_process_identity(
                        authority.vm.vm_id,
                        later_process,
                        boot_id=authority.vm.boot_id,
                        process_record_id=uuid4(),
                        operation_id=authority.operation_id,
                    )
                with self.assertRaisesRegex(
                    RegistryConflictError,
                    "retire it with exit evidence",
                ):
                    transaction.record_process_identity(
                        authority.vm.vm_id,
                        later_process,
                        boot_id=authority.vm.boot_id,
                        process_record_id=uuid4(),
                        operation_id=authority.operation_id,
                        active=False,
                    )
            still_active = registry.get_process_identity(
                authority.process_record_id
            )
            self.assertIsNotNone(still_active)
            assert still_active is not None
            self.assertTrue(still_active.active)
            self.assertEqual(registry.list_process_exit_observations(), ())

            forged_process = ProcessIdentity(
                pid=authority.process.pid,
                start_time_ticks=authority.process.start_time_ticks + 1,
                executable=authority.process.executable,
                executable_sha256=authority.process.executable_sha256,
                observed_at=authority.process.observed_at,
            )
            valid_absent_at = (
                authority.process_observed_at + timedelta(seconds=2)
            )
            attempts = (
                (
                    RegistryNotFoundError,
                    {
                        "process_record_id": uuid4(),
                    },
                ),
                (
                    RegistryConflictError,
                    {"vm_id": uuid4()},
                ),
                (
                    RegistryConflictError,
                    {"generation": authority.vm.generation + 1},
                ),
                (
                    RegistryConflictError,
                    {"boot_id": uuid4()},
                ),
                (
                    RegistryConflictError,
                    {"original_operation_id": uuid4()},
                ),
                (
                    RegistryConflictError,
                    {"process": forged_process},
                ),
                (
                    RegistryConflictError,
                    {
                        "observed_absent_at":
                            authority.process_observed_at
                    },
                ),
                (
                    RegistryConflictError,
                    {"reason": "NOT A MACHINE TOKEN"},
                ),
            )
            for expected_error, overrides in attempts:
                values: dict[str, object] = {
                    "process_record_id": authority.process_record_id,
                    "vm_id": authority.vm.vm_id,
                    "process": authority.process,
                    "generation": authority.vm.generation,
                    "boot_id": authority.vm.boot_id,
                    "original_operation_id": authority.operation_id,
                    "observed_absent_at": valid_absent_at,
                    "reason": "process_absent",
                    "exit_evidence_id": uuid4(),
                }
                values.update(overrides)
                with self.subTest(overrides=overrides):
                    with self.assertRaises(expected_error):
                        with registry.write_transaction() as transaction:
                            transaction.retire_process_identity(
                                values["process_record_id"],
                                values["vm_id"],
                                values["process"],
                                generation=values["generation"],
                                boot_id=values["boot_id"],
                                original_operation_id=values[
                                    "original_operation_id"
                                ],
                                observed_absent_at=values[
                                    "observed_absent_at"
                                ],
                                reason=values["reason"],
                                exit_evidence_id=values[
                                    "exit_evidence_id"
                                ],
                            )
            final_active = registry.get_process_identity(
                authority.process_record_id
            )
            self.assertIsNotNone(final_active)
            assert final_active is not None
            self.assertTrue(final_active.active)
            self.assertEqual(registry.list_process_exit_observations(), ())

    def test_concurrent_process_retirement_has_one_exact_winner(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            observed_absent_at = (
                authority.process_observed_at + timedelta(seconds=1)
            )
            barrier = threading.Barrier(2)
            outcome_lock = threading.Lock()
            outcomes: list[tuple[str, object]] = []

            def retire(exit_evidence_id: UUID) -> None:
                barrier.wait(timeout=5)
                try:
                    with registry.write_transaction() as transaction:
                        observed = transaction.retire_process_identity(
                            authority.process_record_id,
                            authority.vm.vm_id,
                            authority.process,
                            generation=authority.vm.generation,
                            boot_id=authority.vm.boot_id,
                            original_operation_id=authority.operation_id,
                            observed_absent_at=observed_absent_at,
                            reason="process_absent",
                            exit_evidence_id=exit_evidence_id,
                        )
                    outcome: tuple[str, object] = ("ok", observed)
                except RegistryConflictError as exc:
                    outcome = ("conflict", exc)
                with outcome_lock:
                    outcomes.append(outcome)

            threads = [
                threading.Thread(target=retire, args=(uuid4(),)),
                threading.Thread(target=retire, args=(uuid4(),)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())

            self.assertEqual(
                sorted(label for label, _ in outcomes),
                ["conflict", "ok"],
            )
            exits = registry.list_process_exit_observations()
            self.assertEqual(len(exits), 1)
            retired = registry.get_process_identity(
                authority.process_record_id
            )
            self.assertIsNotNone(retired)
            assert retired is not None
            self.assertFalse(retired.active)

    def test_process_exit_sql_guards_and_canonical_tamper_recovery(
        self,
    ) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)

        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE process_identities SET active=0 "
                    "WHERE process_record_id=?",
                    (str(authority.process_record_id),),
                )
            connection.rollback()
        finally:
            connection.close()

        with SQLiteRegistry.open(self.database) as registry:
            exit_evidence_id = uuid4()
            with registry.write_transaction() as transaction:
                transaction.retire_process_identity(
                    authority.process_record_id,
                    authority.vm.vm_id,
                    authority.process,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                    original_operation_id=authority.operation_id,
                    observed_absent_at=(
                        authority.process_observed_at
                        + timedelta(seconds=1)
                    ),
                    reason="process_absent",
                    exit_evidence_id=exit_evidence_id,
                )

        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE process_exit_observations SET reason='forged' "
                    "WHERE exit_evidence_id=?",
                    (str(exit_evidence_id),),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM process_exit_observations "
                    "WHERE exit_evidence_id=?",
                    (str(exit_evidence_id),),
                )
            connection.rollback()
            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_schema "
                "WHERE type='trigger' AND "
                "name='process_exit_observation_immutable_update'"
            ).fetchone()[0]
            connection.execute(
                "DROP TRIGGER "
                "process_exit_observation_immutable_update"
            )
            forged = b"{}"
            connection.execute(
                "UPDATE process_exit_observations "
                "SET canonical_bytes=?,canonical_sha256=? "
                "WHERE exit_evidence_id=?",
                (
                    forged,
                    hashlib.sha256(forged).hexdigest(),
                    str(exit_evidence_id),
                ),
            )
            connection.execute(str(trigger_sql))
            connection.commit()
        finally:
            connection.close()

        with SQLiteRegistry.open(self.database) as recovery:
            self.assertEqual(
                recovery.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
            self.assertFalse(recovery.status.integrity_ok)
            self.assertIn(
                "process exit",
                recovery.status.issue or "",
            )

    def test_schema_record_canonical_bytes_and_immutable_p3_baseline(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            baseline = registry.get_disk(authority.disk.disk_id)
            before_vm = registry.get_vm(authority.vm.vm_id)
            self.assertIsNotNone(baseline)
            self.assertIsNotNone(before_vm)
            observed_at = authority.process_observed_at + timedelta(seconds=1)
            observation_id = uuid4()
            with registry.write_transaction() as transaction:
                observation = _append(
                    transaction,
                    authority,
                    observed_at,
                    observation_id=observation_id,
                )
                replay = _append(
                    transaction,
                    authority,
                    observed_at,
                    observation_id=observation_id,
                )
                process_unbound = _append(
                    transaction,
                    authority,
                    observed_at + timedelta(microseconds=1),
                    observation_id=uuid4(),
                    boot_id=None,
                    process_record_id=None,
                    dirty=True,
                )
            self.assertEqual(replay, observation)
            self.assertEqual(
                registry.get_disk_runtime_observation(observation_id),
                observation,
            )
            self.assertEqual(
                registry.latest_disk_runtime_observation(
                    authority.disk.disk_id
                ),
                process_unbound,
            )
            self.assertEqual(
                registry.list_disk_runtime_observations(
                    disk_id=authority.disk.disk_id
                ),
                (observation, process_unbound),
            )
            self.assertEqual(
                registry.list_disk_runtime_observations(
                    vm_id=authority.vm.vm_id,
                    generation=authority.vm.generation,
                ),
                (observation, process_unbound),
            )
            decoded = json.loads(observation.canonical_bytes)
            self.assertEqual(decoded["observation_id"], str(observation_id))
            self.assertEqual(decoded["disk_id"], str(authority.disk.disk_id))
            self.assertEqual(decoded["vm_id"], str(authority.vm.vm_id))
            self.assertEqual(
                decoded["process_record_id"],
                str(authority.process_record_id),
            )
            self.assertFalse(decoded["quiesced"])
            self.assertEqual(
                hashlib.sha256(observation.canonical_bytes).hexdigest(),
                observation.canonical_sha256,
            )
            with self.assertRaises(FrozenInstanceError):
                observation.file_size_bytes = 1  # type: ignore[misc]

            after_disk = registry.get_disk(authority.disk.disk_id)
            after_vm = registry.get_vm(authority.vm.vm_id)
            self.assertEqual(after_disk, baseline)
            self.assertEqual(after_vm, before_vm)
            assert after_disk is not None
            self.assertEqual(after_disk.file_size_bytes, 196_616)
            self.assertNotEqual(
                observation.file_size_bytes,
                after_disk.file_size_bytes,
            )

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                REGISTRY_SCHEMA_VERSION,
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(disk_runtime_observations)"
                ).fetchall()
            }
            process_exit_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(process_exit_observations)"
                ).fetchall()
            }
            indexes = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='index'"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='trigger'"
                ).fetchall()
            }
            self.assertEqual(columns, OBSERVATION_COLUMNS)
            self.assertEqual(process_exit_columns, PROCESS_EXIT_COLUMNS)
            self.assertTrue((P4_INDEXES | P5_INDEXES).issubset(indexes))
            self.assertTrue((P4_TRIGGERS | P5_TRIGGERS).issubset(triggers))
            self.assertEqual(
                connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
        finally:
            connection.close()

    def test_constraints_transactions_and_no_forged_lifecycle(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            before_vm = registry.get_vm(authority.vm.vm_id)
            first_time = authority.process_observed_at + timedelta(seconds=1)

            with registry.write_transaction() as transaction:
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        vm_id=uuid4(),
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        generation=authority.vm.generation + 1,
                    )
                for field, value in (
                    ("file_size_bytes", 0),
                    ("allocated_size_bytes", -1),
                    ("qemu_actual_size_bytes", True),
                    ("dirty", 1),
                    ("corrupt", "false"),
                    ("quiesced", 0),
                ):
                    with self.subTest(field=field):
                        with self.assertRaises(RegistryConflictError):
                            _append(
                                transaction,
                                authority,
                                first_time,
                                **{field: value},
                            )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        boot_id=None,
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        quiesced=True,
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        process_record_id=None,
                        quiesced=True,
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        boot_id=None,
                        process_record_id=None,
                        quiesced=True,
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        boot_id=uuid4(),
                    )
                with self.assertRaises(RegistryNotFoundError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        process_record_id=uuid4(),
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        authority.process_observed_at
                        - timedelta(microseconds=1),
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        datetime(2026, 8, 5, 12, 0),
                    )
                with self.assertRaises(RegistryNotFoundError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        operation_id=uuid4(),
                    )

                unbound_operation = uuid4()
                transaction.begin_operation(
                    unbound_operation,
                    None,
                    "runtime.observe_disk",
                    f"unbound-{unbound_operation}",
                    request_hash="8" * 64,
                    status="running",
                )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        operation_id=unbound_operation,
                    )
                pending_operation = uuid4()
                transaction.begin_operation(
                    pending_operation,
                    authority.vm.vm_id,
                    "runtime.observe_disk",
                    f"pending-{pending_operation}",
                    request_hash="9" * 64,
                    status="pending",
                )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        operation_id=pending_operation,
                    )
                first_id = uuid4()
                first = _append(
                    transaction,
                    authority,
                    first_time,
                    observation_id=first_id,
                    dirty=True,
                    corrupt=True,
                )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        observation_id=uuid4(),
                    )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        first_time,
                        observation_id=first_id,
                        file_size_bytes=first.file_size_bytes + 1,
                    )

                retirement_time = first_time + timedelta(seconds=1)
                second_process_time = retirement_time + timedelta(
                    microseconds=1
                )
                second_process_id = uuid4()
                second_process = ProcessIdentity(
                    pid=42_425,
                    start_time_ticks=90_002,
                    executable="/usr/bin/qemu-system-x86_64",
                    executable_sha256="a" * 64,
                    observed_at=second_process_time,
                )
                transaction.retire_process_identity(
                    authority.process_record_id,
                    authority.vm.vm_id,
                    authority.process,
                    generation=authority.vm.generation,
                    boot_id=authority.vm.boot_id,
                    original_operation_id=authority.operation_id,
                    observed_absent_at=retirement_time,
                    reason="replaced",
                    exit_evidence_id=uuid4(),
                )
                transaction.record_process_identity(
                    authority.vm.vm_id,
                    second_process,
                    boot_id=authority.vm.boot_id,
                    process_record_id=second_process_id,
                    operation_id=authority.operation_id,
                )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        second_process_time + timedelta(seconds=1),
                    )
                second = _append(
                    transaction,
                    authority,
                    second_process_time + timedelta(seconds=1),
                    process_record_id=second_process_id,
                )
            self.assertEqual(
                registry.list_disk_runtime_observations(
                    disk_id=authority.disk.disk_id
                ),
                (first, second),
            )
            self.assertEqual(registry.get_vm(authority.vm.vm_id), before_vm)

            rolled_back_id = uuid4()
            with self.assertRaisesRegex(RuntimeError, "force rollback"):
                with registry.write_transaction() as transaction:
                    _append(
                        transaction,
                        authority,
                        second.observed_at + timedelta(seconds=1),
                        observation_id=rolled_back_id,
                        process_record_id=second.process_record_id,
                    )
                    raise RuntimeError("force rollback")
            self.assertIsNone(
                registry.get_disk_runtime_observation(rolled_back_id)
            )

            with registry.write_transaction() as transaction:
                transaction.release_disk(
                    authority.disk.disk_id,
                    operation_id=authority.operation_id,
                )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        authority,
                        second.observed_at + timedelta(seconds=2),
                        process_record_id=second.process_record_id,
                    )
                replacement = transaction.claim_disk(
                    authority.vm.vm_id,
                    authority.vm.definition.disk_path,
                    disk_id=uuid4(),
                    operation_id=authority.operation_id,
                )
                replacement_authority = _Authority(
                    vm=authority.vm,
                    disk=replacement,
                    operation_id=authority.operation_id,
                    process_record_id=second.process_record_id
                    or authority.process_record_id,
                    process=second_process,
                    process_observed_at=second.observed_at,
                )
                with self.assertRaises(RegistryConflictError):
                    _append(
                        transaction,
                        replacement_authority,
                        second.observed_at + timedelta(seconds=2),
                    )

        with SQLiteRegistry.open(self.database) as reopened:
            self.assertEqual(reopened.status.mode, RegistryMode.READ_WRITE)
            self.assertEqual(
                len(
                    reopened.list_disk_runtime_observations(
                        disk_id=authority.disk.disk_id
                    )
                ),
                2,
            )

    def test_append_only_sql_triggers_and_canonical_tamper_recovery(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            with registry.write_transaction() as transaction:
                observation = _append(
                    transaction,
                    authority,
                    authority.process_observed_at + timedelta(seconds=1),
                )

        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE disk_runtime_observations "
                    "SET file_size_bytes=file_size_bytes+1 "
                    "WHERE observation_id=?",
                    (str(observation.observation_id),),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM disk_runtime_observations "
                    "WHERE observation_id=?",
                    (str(observation.observation_id),),
                )
            connection.rollback()

            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_schema "
                "WHERE type='trigger' "
                "AND name='disk_runtime_observation_immutable_update'"
            ).fetchone()[0]
            connection.execute(
                "DROP TRIGGER disk_runtime_observation_immutable_update"
            )
            forged = b"{}"
            connection.execute(
                "UPDATE disk_runtime_observations "
                "SET canonical_bytes=?,canonical_sha256=? "
                "WHERE observation_id=?",
                (
                    forged,
                    hashlib.sha256(forged).hexdigest(),
                    str(observation.observation_id),
                ),
            )
            connection.execute(str(trigger_sql))
            connection.commit()
        finally:
            connection.close()

        with SQLiteRegistry.open(self.database) as recovery:
            self.assertEqual(
                recovery.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
            self.assertFalse(recovery.status.integrity_ok)
            self.assertIn(
                "canonical bytes",
                recovery.status.issue or "",
            )

    def test_v4_to_v5_backup_migration_and_partial_shape_rollback(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            baseline_process = registry.get_process_identity(
                authority.process_record_id
            )
        _downgrade_exact_v5_to_v4(self.database)

        invalid = self.root / "state" / "invalid-v4.sqlite3"
        shutil.copy2(self.database, invalid)
        os.chmod(invalid, 0o600)
        unbound = self.root / "state" / "unbound-v4.sqlite3"
        shutil.copy2(self.database, unbound)
        os.chmod(unbound, 0o600)
        invalid_connection = sqlite3.connect(invalid)
        try:
            invalid_connection.execute(
                "CREATE TABLE process_exit_observations("
                "unexpected TEXT"
                ") STRICT"
            )
            invalid_connection.commit()
        finally:
            invalid_connection.close()
        unbound_connection = sqlite3.connect(unbound)
        try:
            unbound_connection.execute(
                "UPDATE process_identities SET operation_id=NULL "
                "WHERE process_record_id=? AND active=1",
                (str(authority.process_record_id),),
            )
            unbound_connection.commit()
        finally:
            unbound_connection.close()

        with SQLiteRegistry.open(self.database, writer=False) as reader:
            self.assertEqual(
                reader.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
            self.assertEqual(
                reader.status.schema_version,
                P4_REGISTRY_SCHEMA_VERSION,
            )
            self.assertIn(
                "migration requires the writer owner",
                reader.status.issue or "",
            )

        with SQLiteRegistry.open(self.database) as migrated:
            self.assertEqual(
                migrated.status.schema_version,
                REGISTRY_SCHEMA_VERSION,
            )
            self.assertEqual(migrated.status.mode, RegistryMode.READ_WRITE)
            backup = migrated.status.migration_backup
            self.assertIsNotNone(backup)
            assert backup is not None
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                migrated.get_process_identity(
                    authority.process_record_id
                ),
                baseline_process,
            )
            self.assertEqual(
                migrated.list_process_exit_observations(),
                (),
            )

        backup_connection = sqlite3.connect(backup)
        try:
            self.assertEqual(
                int(
                    backup_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                P4_REGISTRY_SCHEMA_VERSION,
            )
            tables = {
                str(row[0])
                for row in backup_connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ).fetchall()
            }
            self.assertNotIn("process_exit_observations", tables)
        finally:
            backup_connection.close()

        with self.assertRaises(RegistryMigrationError) as caught:
            SQLiteRegistry.open(invalid)
        self.assertIsNotNone(caught.exception.backup_path)
        assert caught.exception.backup_path is not None
        self.assertTrue(caught.exception.backup_path.is_file())
        rollback_connection = sqlite3.connect(invalid)
        try:
            self.assertEqual(
                int(
                    rollback_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                P4_REGISTRY_SCHEMA_VERSION,
            )
            columns = {
                str(row[1])
                for row in rollback_connection.execute(
                    "PRAGMA table_info(process_exit_observations)"
                ).fetchall()
            }
            self.assertEqual(columns, {"unexpected"})
        finally:
            rollback_connection.close()

        with self.assertRaisesRegex(
            RegistryMigrationError,
            "version 4 failed",
        ) as unbound_caught:
            SQLiteRegistry.open(unbound)
        self.assertIsNotNone(unbound_caught.exception.backup_path)
        assert unbound_caught.exception.backup_path is not None
        self.assertTrue(
            unbound_caught.exception.backup_path.is_file()
        )
        unbound_connection = sqlite3.connect(unbound)
        try:
            self.assertEqual(
                int(
                    unbound_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                P4_REGISTRY_SCHEMA_VERSION,
            )
            self.assertIsNone(
                unbound_connection.execute(
                    "SELECT operation_id FROM process_identities "
                    "WHERE process_record_id=? AND active=1",
                    (str(authority.process_record_id),),
                ).fetchone()[0]
            )
            self.assertIsNone(
                unbound_connection.execute(
                    "SELECT 1 FROM sqlite_schema "
                    "WHERE type='table' "
                    "AND name='process_exit_observations'"
                ).fetchone()
            )
        finally:
            unbound_connection.close()

    def test_v3_to_v5_backup_migration_and_failed_shape_rollback(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            baseline = registry.get_disk(authority.disk.disk_id)
        _downgrade_exact_v5_to_v4(self.database)
        _downgrade_exact_v4_to_v3(self.database)

        invalid = self.root / "state" / "invalid-v3.sqlite3"
        shutil.copy2(self.database, invalid)
        os.chmod(invalid, 0o600)
        invalid_connection = sqlite3.connect(invalid)
        try:
            invalid_connection.execute(
                "ALTER TABLE disks ADD COLUMN unexpected TEXT"
            )
            invalid_connection.commit()
        finally:
            invalid_connection.close()

        with SQLiteRegistry.open(self.database, writer=False) as reader:
            self.assertEqual(
                reader.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
            self.assertEqual(
                reader.status.schema_version,
                P3_REGISTRY_SCHEMA_VERSION,
            )
            self.assertIn(
                "migration requires the writer owner",
                reader.status.issue or "",
            )

        with SQLiteRegistry.open(self.database) as migrated:
            self.assertEqual(
                migrated.status.schema_version,
                REGISTRY_SCHEMA_VERSION,
            )
            self.assertEqual(migrated.status.mode, RegistryMode.READ_WRITE)
            self.assertIsNotNone(migrated.status.migration_backup)
            backup = migrated.status.migration_backup
            assert backup is not None
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                migrated.get_disk(authority.disk.disk_id),
                baseline,
            )
            self.assertEqual(
                migrated.list_disk_runtime_observations(),
                (),
            )
            with migrated.write_transaction() as transaction:
                appended = _append(
                    transaction,
                    authority,
                    authority.process_observed_at + timedelta(seconds=1),
                )
            self.assertEqual(
                migrated.latest_disk_runtime_observation(
                    authority.disk.disk_id
                ),
                appended,
            )

        backup_connection = sqlite3.connect(backup)
        try:
            self.assertEqual(
                int(
                    backup_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                P3_REGISTRY_SCHEMA_VERSION,
            )
            tables = {
                str(row[0])
                for row in backup_connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ).fetchall()
            }
            self.assertNotIn("disk_runtime_observations", tables)
            self.assertNotIn("process_exit_observations", tables)
        finally:
            backup_connection.close()

        with self.assertRaises(RegistryMigrationError) as caught:
            SQLiteRegistry.open(invalid)
        self.assertIsNotNone(caught.exception.backup_path)
        assert caught.exception.backup_path is not None
        self.assertTrue(caught.exception.backup_path.is_file())
        rollback_connection = sqlite3.connect(invalid)
        try:
            self.assertEqual(
                int(
                    rollback_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                P3_REGISTRY_SCHEMA_VERSION,
            )
            columns = {
                str(row[1])
                for row in rollback_connection.execute(
                    "PRAGMA table_info(disks)"
                ).fetchall()
            }
            tables = {
                str(row[0])
                for row in rollback_connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("unexpected", columns)
            self.assertNotIn("disk_runtime_observations", tables)
        finally:
            rollback_connection.close()

    def test_exact_v2_migration_advances_atomically_through_v5(self) -> None:
        legacy = self.root / "state" / "legacy-v2.sqlite3"
        with SQLiteRegistry.open(legacy) as registry:
            authority = _populate_authority(registry, self.root)
        _downgrade_exact_v5_to_v4(legacy)
        _downgrade_exact_v4_to_v3(legacy)
        _downgrade_exact_v3_to_v2(legacy)

        with SQLiteRegistry.open(legacy) as migrated:
            self.assertEqual(
                migrated.status.schema_version,
                REGISTRY_SCHEMA_VERSION,
            )
            self.assertEqual(migrated.status.mode, RegistryMode.READ_WRITE)
            self.assertIsNotNone(migrated.status.migration_backup)
            backup = migrated.status.migration_backup
            assert backup is not None
            disk = migrated.get_disk(authority.disk.disk_id)
            self.assertIsNotNone(disk)
            assert disk is not None
            self.assertEqual(disk.vm_id, authority.vm.vm_id)
            self.assertFalse(disk.materialized)
            self.assertNotEqual(disk.ownership_token.int, 0)
            self.assertEqual(
                migrated.list_disk_runtime_observations(),
                (),
            )

        backup_connection = sqlite3.connect(backup)
        try:
            self.assertEqual(
                int(
                    backup_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                2,
            )
            columns = {
                str(row[1])
                for row in backup_connection.execute(
                    "PRAGMA table_info(disks)"
                ).fetchall()
            }
            self.assertNotIn("ownership_token", columns)
            self.assertNotIn("materialized", columns)
            tables = {
                str(row[0])
                for row in backup_connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ).fetchall()
            }
            self.assertNotIn("disk_runtime_observations", tables)
            self.assertNotIn("process_exit_observations", tables)
        finally:
            backup_connection.close()

    def test_concurrent_append_serializes_and_rejects_equal_time_replay(self) -> None:
        with SQLiteRegistry.open(self.database) as registry:
            authority = _populate_authority(registry, self.root)
            timestamp = authority.process_observed_at + timedelta(seconds=1)
            barrier = threading.Barrier(2)
            result_lock = threading.Lock()
            outcomes: list[tuple[str, object]] = []

            def append(observation_id: UUID) -> None:
                barrier.wait(timeout=5)
                try:
                    with registry.write_transaction() as transaction:
                        observed = _append(
                            transaction,
                            authority,
                            timestamp,
                            observation_id=observation_id,
                        )
                    outcome: tuple[str, object] = ("ok", observed)
                except RegistryConflictError as exc:
                    outcome = ("conflict", exc)
                with result_lock:
                    outcomes.append(outcome)

            threads = [
                threading.Thread(target=append, args=(uuid4(),)),
                threading.Thread(target=append, args=(uuid4(),)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())

            self.assertEqual(
                sorted(label for label, _ in outcomes),
                ["conflict", "ok"],
            )
            observations = registry.list_disk_runtime_observations(
                disk_id=authority.disk.disk_id
            )
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0].observed_at, timestamp)
            with registry.write_transaction() as transaction:
                later = _append(
                    transaction,
                    authority,
                    timestamp + timedelta(microseconds=1),
                )
            self.assertEqual(
                registry.latest_disk_runtime_observation(
                    authority.disk.disk_id
                ),
                later,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
