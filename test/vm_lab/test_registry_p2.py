"""Physical P2 proof for the SQLite VM registry authority.

These tests use real files, SQLite databases, advisory locks, and child
processes.  No mock establishes registry success.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from somnus_protocol import (
    SnapshotReference,
    TransitionEvidence,
    VMDefinition,
    VMPorts,
    VMRecord,
    VMState,
)
from somnus_vm.host.registry import (
    REGISTRY_SCHEMA_VERSION,
    RegistryConflictError,
    RegistryIntegrityError,
    RegistryMigrationError,
    RegistryMode,
    RegistryReadOnlyError,
    RegistryRevisionConflict,
    RegistryWriterBusyError,
    SQLiteRegistry,
)


def _canonical_record(record: VMRecord) -> tuple[bytes, str]:
    encoded = json.dumps(
        record.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return encoded, hashlib.sha256(encoded).hexdigest()


def _record(
    root: Path,
    name: str,
    *,
    ssh: int,
    agent: int,
    display: int,
    planned: bool = False,
) -> VMRecord:
    record = VMRecord(
        definition=VMDefinition(
            name=name,
            disk_path=str(root / f"{name}.qcow2"),
            memory_mib=4_096,
            vcpus=2,
            enable_kvm=False,
        ),
        ports=VMPorts(ssh=ssh, agent=agent, vnc=display),
    )
    if planned:
        record = record.with_planned_runtime(
            qmp_socket=str(root / f"{name}.qmp"),
            pid_file=str(root / f"{name}.pid"),
            log_path=str(root / f"{name}.log"),
        )
    return record


def _evidence(
    record: VMRecord,
    facts: dict[str, str],
) -> TransitionEvidence:
    return TransitionEvidence(
        evidence_id=uuid4(),
        observed_at=datetime.now(timezone.utc),
        facts={"intent_digest": record.intent_digest, **facts},
        vm_id=record.vm_id,
        generation=record.generation,
        boot_id=record.boot_id,
        intent_digest=record.intent_digest,
    )


def _create_v1(path: Path, record: VMRecord) -> None:
    encoded, digest = _canonical_record(record)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE registry_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;
        CREATE TABLE vms(
            vm_id TEXT PRIMARY KEY,
            normalized_name TEXT NOT NULL UNIQUE,
            qmp_socket TEXT UNIQUE,
            intent_digest TEXT NOT NULL,
            record_bytes BLOB NOT NULL,
            record_sha256 TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT;
        PRAGMA user_version=1;
        """
    )
    connection.execute(
        "INSERT INTO registry_meta(key,value) VALUES('journal_mode','delete')"
    )
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        "INSERT INTO vms VALUES(?,?,?,?,?,?,?,?,?)",
        (
            str(record.vm_id),
            record.definition.name.casefold(),
            record.qmp_socket or None,
            record.intent_digest,
            encoded,
            digest,
            0,
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


class RegistryP2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "nested" / "state"
        self.database = self.root / "registry.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_private_chain_measured_journal_and_read_only_reopen(self) -> None:
        self.assertFalse(self.root.exists())
        with SQLiteRegistry.open(self.database) as registry:
            self.assertEqual(registry.status.mode, RegistryMode.READ_WRITE)
            self.assertEqual(
                registry.status.schema_version,
                REGISTRY_SCHEMA_VERSION,
            )
            self.assertIn(registry.status.journal_mode, {"wal", "delete"})
            connection = sqlite3.connect(self.database)
            actual = connection.execute("PRAGMA journal_mode").fetchone()[0]
            stored = connection.execute(
                "SELECT value FROM registry_meta WHERE key='journal_mode'"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(actual, stored)
            self.assertEqual(actual, registry.status.journal_mode)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

        with SQLiteRegistry.open(self.database, writer=False) as reader:
            self.assertEqual(reader.status.mode, RegistryMode.READ_ONLY)
            with self.assertRaises(RegistryReadOnlyError):
                reader.write_transaction()

    def test_writer_lock_is_process_held_in_process_and_child(self) -> None:
        with SQLiteRegistry.open(self.database):
            with self.assertRaises(RegistryWriterBusyError):
                SQLiteRegistry.open(self.database)
            script = (
                "import sys\n"
                "from somnus_vm.host.registry import "
                "SQLiteRegistry,RegistryWriterBusyError\n"
                "try:\n"
                " SQLiteRegistry.open(sys.argv[1])\n"
                "except RegistryWriterBusyError:\n"
                " raise SystemExit(0)\n"
                "raise SystemExit(9)\n"
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path.cwd() / "src")
            result = subprocess.run(
                [sys.executable, "-c", script, str(self.database)],
                cwd=Path.cwd(),
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr,
            )
        with SQLiteRegistry.open(self.database) as reopened:
            self.assertEqual(reopened.status.mode, RegistryMode.READ_WRITE)

    def test_concurrent_child_readers_observe_committed_authority(self) -> None:
        record = _record(
            self.root,
            "readers",
            ssh=2_191,
            agent=9_191,
            display=5_891,
        )
        script = (
            "import sys\n"
            "from somnus_vm.host.registry import SQLiteRegistry\n"
            "with SQLiteRegistry.open(sys.argv[1],writer=False) as r:\n"
            " print(len(r.list_vms(active_only=True)))\n"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path.cwd() / "src")
        with SQLiteRegistry.open(self.database) as writer:
            with writer.write_transaction() as transaction:
                transaction.insert_vm(record)
            children = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(self.database)],
                    cwd=Path.cwd(),
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(16)
            ]
            results = [
                child.communicate(timeout=10)
                for child in children
            ]
            for child, (stdout, stderr) in zip(
                children,
                results,
                strict=True,
            ):
                self.assertEqual(
                    child.returncode,
                    0,
                    stdout + stderr,
                )
                self.assertEqual(stdout.strip(), "1")

    def test_policy_allows_two_active_vms_but_owners_cannot_collide(self) -> None:
        first = _record(
            self.root,
            "first",
            ssh=2_201,
            agent=9_201,
            display=5_901,
            planned=True,
        )
        second = _record(
            self.root,
            "second",
            ssh=2_202,
            agent=9_202,
            display=5_902,
            planned=True,
        )
        cross_role = _record(
            self.root,
            "third",
            ssh=9_201,
            agent=9_203,
            display=5_903,
            planned=True,
        )
        with SQLiteRegistry.open(self.database) as registry:
            with registry.write_transaction() as transaction:
                transaction.insert_vm(first)
                transaction.insert_vm(second)
                self.assertEqual(transaction.count_active_vms(), 2)
            self.assertEqual(len(registry.list_vms(active_only=True)), 2)
            with self.assertRaises(RegistryConflictError):
                with registry.write_transaction() as transaction:
                    transaction.insert_vm(cross_role)
            with registry.write_transaction() as transaction:
                first_lease = transaction.acquire_port_lease(
                    first.vm_id,
                    "ssh",
                    first.ports.ssh,
                )
                with self.assertRaises(RegistryConflictError):
                    transaction.acquire_port_lease(
                        second.vm_id,
                        "ssh",
                        first.ports.ssh,
                    )
                with self.assertRaises(RegistryConflictError):
                    transaction.acquire_port_lease(
                        second.vm_id,
                        "ssh",
                        second.ports.ssh,
                        host="::1",
                    )
                transaction.release_port_lease(first_lease.lease_id)

    def test_unbound_declare_intent_then_exact_vm_binding_and_recovery_rows(
        self,
    ) -> None:
        record = _record(
            self.root,
            "declared",
            ssh=2_211,
            agent=9_211,
            display=5_911,
        )
        operation_id = uuid4()
        request_hash = hashlib.sha256(b"declare").hexdigest()
        with SQLiteRegistry.open(self.database) as registry:
            with registry.write_transaction() as transaction:
                pending = transaction.begin_operation(
                    operation_id,
                    None,
                    "declare",
                    "declare:declared",
                    request_hash=request_hash,
                    intent=b'{"name":"declared"}',
                    owned_resources=b'{"disk":"pending"}',
                )
                self.assertIsNone(pending.vm_id)
                self.assertIsNone(pending.generation)
            with registry.write_transaction() as transaction:
                transaction.insert_vm(record)
                bound = transaction.bind_operation_vm(
                    operation_id,
                    record.vm_id,
                )
                self.assertEqual(bound.vm_id, record.vm_id)
                disk = transaction.claim_disk(
                    record.vm_id,
                    record.definition.disk_path,
                    operation_id=operation_id,
                )
                transaction.append_checkpoint(
                    operation_id,
                    "registry_committed",
                    ordinal=0,
                    payload=b'{"revision":0}',
                )
                completed = transaction.set_operation_status(
                    operation_id,
                    "completed",
                    result=b'{"vm_id":"committed"}',
                )
                self.assertEqual(completed.status, "completed")
            release_operation = uuid4()
            with registry.write_transaction() as transaction:
                transaction.begin_operation(
                    release_operation,
                    record.vm_id,
                    "destroy",
                    "destroy:declared",
                    request_hash=hashlib.sha256(b"destroy").hexdigest(),
                    intent=b'{"name":"declared"}',
                )
                released = transaction.release_disk(
                    disk.disk_id,
                    operation_id=release_operation,
                )
                self.assertEqual(
                    released.operation_id,
                    operation_id,
                )
                self.assertEqual(
                    released.released_by_operation_id,
                    release_operation,
                )
            replay = registry.find_idempotency("declare:declared")
            self.assertIsNotNone(replay)
            assert replay is not None
            self.assertEqual(replay.vm_id, record.vm_id)
            self.assertEqual(
                registry.list_checkpoints(operation_id)[0].name,
                "registry_committed",
            )
            with registry.write_transaction() as transaction:
                exact = transaction.begin_operation(
                    uuid4(),
                    None,
                    "declare",
                    "declare:declared",
                    request_hash=request_hash,
                    intent=b'{"name":"declared"}',
                    owned_resources=b'{"disk":"pending"}',
                )
                self.assertEqual(exact.operation_id, operation_id)
            with self.assertRaises(RegistryConflictError):
                with registry.write_transaction() as transaction:
                    transaction.begin_operation(
                        uuid4(),
                        None,
                        "declare",
                        "declare:declared",
                        request_hash="f" * 64,
                        intent=b'{"name":"other"}',
                    )

        with SQLiteRegistry.open(self.database, writer=False) as reader:
            recovered = reader.get_operation(operation_id)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered.result, b'{"vm_id":"committed"}')
            self.assertEqual(
                reader.list_checkpoints(operation_id)[0].payload,
                b'{"revision":0}',
            )

    def test_cas_is_append_only_and_preserves_destroyed_generation(self) -> None:
        declared = _record(
            self.root,
            "history",
            ssh=2_221,
            agent=9_221,
            display=5_921,
            planned=True,
        )
        provisioning = declared.transition(
            VMState.PROVISIONING,
            _evidence(
                declared,
                {"operation_id": str(uuid4())},
            ),
        )
        with SQLiteRegistry.open(self.database) as registry:
            with registry.write_transaction() as transaction:
                transaction.insert_vm(declared)
            with registry.write_transaction() as transaction:
                transaction.compare_and_swap_vm(
                    declared.vm_id,
                    0,
                    provisioning,
                )
            with self.assertRaisesRegex(
                RegistryConflictError,
                "append-only",
            ):
                with registry.write_transaction() as transaction:
                    transaction.compare_and_swap_vm(
                        declared.vm_id,
                        1,
                        declared,
                    )
            with self.assertRaises(RegistryRevisionConflict):
                with registry.write_transaction() as transaction:
                    transaction.compare_and_swap_vm(
                        declared.vm_id,
                        0,
                        provisioning,
                    )

        replacement_source = _record(
            self.root,
            "generation",
            ssh=2_231,
            agent=9_231,
            display=5_931,
        )
        destroyed = replacement_source.transition(
            VMState.DESTROYED,
            _evidence(
                replacement_source,
                {
                    "operation_id": str(uuid4()),
                    "ownership_verified": "true",
                    "resources_removed": "true",
                },
            ),
        )
        replacement = destroyed.new_generation()
        other_database = self.root / "generation.sqlite3"
        with SQLiteRegistry.open(other_database) as registry:
            with registry.write_transaction() as transaction:
                transaction.insert_vm(replacement_source)
            with registry.write_transaction() as transaction:
                transaction.compare_and_swap_vm(
                    replacement_source.vm_id,
                    0,
                    destroyed,
                )
            with registry.write_transaction() as transaction:
                current = transaction.compare_and_swap_vm(
                    replacement_source.vm_id,
                    1,
                    replacement,
                )
            self.assertEqual(current.record.generation, 1)
        connection = sqlite3.connect(other_database)
        history = connection.execute(
            "SELECT generation,active,revision FROM vms "
            "ORDER BY generation"
        ).fetchall()
        connection.close()
        self.assertEqual(history, [(0, 0, 1), (1, 1, 2)])

    def test_snapshot_event_disk_and_transaction_rollback_are_physical(
        self,
    ) -> None:
        record = _record(
            self.root,
            "resources",
            ssh=2_241,
            agent=9_241,
            display=5_941,
        )
        transition = record.transition(
            VMState.DESTROYED,
            _evidence(
                record,
                {
                    "operation_id": str(uuid4()),
                    "ownership_verified": "true",
                    "resources_removed": "true",
                },
            ),
        ).transitions[-1]
        snapshot = SnapshotReference(
            snapshot_id=uuid4(),
            vm_id=record.vm_id,
            generation=record.generation,
            path=str(self.root / "resources.snapshot.qcow2"),
            created_at=datetime.now(timezone.utc),
        )
        with SQLiteRegistry.open(self.database) as registry:
            with self.assertRaises(RuntimeError):
                with registry.write_transaction() as transaction:
                    transaction.insert_vm(record)
                    raise RuntimeError("force rollback")
            self.assertIsNone(registry.get_vm(record.vm_id))
            with registry.write_transaction() as transaction:
                transaction.insert_vm(record)
                transaction.claim_disk(
                    record.vm_id,
                    record.definition.disk_path,
                )
                event = transaction.append_lifecycle_event(
                    record.vm_id,
                    transition,
                )
                stored_snapshot = transaction.add_snapshot(snapshot)
            self.assertEqual(event.transition, transition)
            self.assertEqual(stored_snapshot.snapshot, snapshot)
        with SQLiteRegistry.open(self.database, writer=False) as reader:
            self.assertEqual(reader.status.mode, RegistryMode.READ_ONLY)
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM lifecycle_events"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM snapshots"
            ).fetchone()[0],
            1,
        )
        connection.close()

    def test_hash_or_materialized_tamper_enters_read_only_recovery(self) -> None:
        record = _record(
            self.root,
            "tamper",
            ssh=2_251,
            agent=9_251,
            display=5_951,
        )
        with SQLiteRegistry.open(self.database) as registry:
            with registry.write_transaction() as transaction:
                transaction.insert_vm(record)
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE vms SET normalized_name='forged' WHERE vm_id=?",
            (str(record.vm_id),),
        )
        connection.commit()
        connection.close()

        with SQLiteRegistry.open(self.database) as recovery:
            self.assertEqual(
                recovery.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
            self.assertFalse(recovery.status.integrity_ok)
            with self.assertRaises(RegistryReadOnlyError):
                recovery.write_transaction()
            copy = recovery.copy_for_recovery(
                self.root / "tampered-recovery.sqlite3"
            )
            self.assertTrue(copy.is_file())
            with self.assertRaises(RegistryConflictError):
                recovery.copy_for_recovery(copy)

    def test_corrupt_and_future_schema_are_never_overwritten(self) -> None:
        self.root.parent.mkdir(mode=0o700)
        self.root.mkdir(mode=0o700)
        corrupt = self.root / "corrupt.sqlite3"
        original = b"not-a-sqlite-database"
        corrupt.write_bytes(original)
        os.chmod(corrupt, 0o600)
        with SQLiteRegistry.open(corrupt) as recovery:
            self.assertEqual(
                recovery.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
            with self.assertRaises(RegistryWriterBusyError):
                SQLiteRegistry.open(corrupt)
            copy = recovery.copy_for_recovery(
                self.root / "corrupt.copy.sqlite3"
            )
            self.assertEqual(copy.read_bytes(), original)
        self.assertEqual(corrupt.read_bytes(), original)

        future = self.root / "future.sqlite3"
        connection = sqlite3.connect(future)
        connection.execute("PRAGMA user_version=99")
        connection.close()
        os.chmod(future, 0o600)
        with SQLiteRegistry.open(future) as recovery:
            self.assertEqual(recovery.status.schema_version, 99)
            self.assertEqual(
                recovery.status.mode,
                RegistryMode.READ_ONLY_RECOVERY,
            )
        connection = sqlite3.connect(future)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            99,
        )
        connection.close()

    def test_v1_migration_has_backup_and_failed_shape_rolls_back(self) -> None:
        self.root.parent.mkdir(mode=0o700)
        self.root.mkdir(mode=0o700)
        legacy = self.root / "legacy.sqlite3"
        record = _record(
            self.root,
            "legacy",
            ssh=2_261,
            agent=9_261,
            display=5_961,
        )
        _create_v1(legacy, record)
        with SQLiteRegistry.open(legacy) as migrated:
            self.assertEqual(
                migrated.status.schema_version,
                REGISTRY_SCHEMA_VERSION,
            )
            self.assertIsNotNone(migrated.status.migration_backup)
            assert migrated.status.migration_backup is not None
            self.assertTrue(migrated.status.migration_backup.is_file())
            self.assertEqual(migrated.get_vm(record.vm_id).record, record)

        invalid = self.root / "invalid-v1.sqlite3"
        connection = sqlite3.connect(invalid)
        connection.executescript(
            """
            CREATE TABLE registry_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) STRICT;
            CREATE TABLE vms(
                vm_id TEXT PRIMARY KEY,
                unexpected TEXT
            ) STRICT;
            PRAGMA user_version=1;
            """
        )
        connection.close()
        os.chmod(invalid, 0o600)
        with self.assertRaises(RegistryMigrationError) as caught:
            SQLiteRegistry.open(invalid)
        self.assertIsNotNone(caught.exception.backup_path)
        assert caught.exception.backup_path is not None
        self.assertTrue(caught.exception.backup_path.is_file())
        connection = sqlite3.connect(invalid)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("PRAGMA table_info(vms)").fetchall()[1][1],
            "unexpected",
        )
        connection.close()

        rollback = self.root / "rollback-v1.sqlite3"
        first = _record(
            self.root,
            "rollback-one",
            ssh=2_271,
            agent=9_271,
            display=5_971,
        )
        second = _record(
            self.root,
            "rollback-two",
            ssh=9_271,
            agent=9_272,
            display=5_972,
        )
        _create_v1(rollback, first)
        encoded, digest = _canonical_record(second)
        connection = sqlite3.connect(rollback)
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT INTO vms VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(second.vm_id),
                second.definition.name.casefold(),
                second.qmp_socket or None,
                second.intent_digest,
                encoded,
                digest,
                0,
                now,
                now,
            ),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(RegistryMigrationError) as rolled_back:
            SQLiteRegistry.open(rollback)
        self.assertIsNotNone(rolled_back.exception.backup_path)
        connection = sqlite3.connect(rollback)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            1,
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(vms)")
        }
        self.assertNotIn("generation", columns)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM vms").fetchone()[0],
            2,
        )
        connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
