"""Focused P4 proof for the internal durable ``runtime.launch`` journal.

The proof consumes real canonical VM/registry contracts, a real SQLite
registry, root-owned executable identity, real filesystem inode identities,
and Terra's immutable QMP evidence types.  It launches no QEMU and makes no
public lifecycle claim.  No mock, stub, fallback, or raw SQLite mutation is
used.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from somnus_protocol import (
    TransitionEvidence,
    VMDefinition,
    VMPorts,
    VMRecord,
    VMState,
)
from somnus_vm.config import load_configuration
from somnus_vm.host.qemu import (
    QemuCommandBuilder,
    bind_block_fds,
    validate_fd_bound_execution,
)
from somnus_vm.host.qemu_process import (
    command_sha256,
    inspect_executable,
    resolve_executable,
)
from somnus_vm.host.qemu_runtime_journal import (
    CHECKPOINT_ORDER,
    CrashClass,
    RecoveryDisposition,
    RuntimeLaunchIntent,
    RuntimeLaunchJournal,
    RuntimeLaunchJournalError,
    canonical_evidence_sha256,
)
from somnus_vm.host.qmp_identity import (
    MIN_QMP_STABILIZATION_NS,
    QMPBlockIdentity,
    QMPBlockLayer,
    QMPCPUIdentity,
    QMPIdentityEvidence,
    QMPIdentitySample,
    QMPIdentitySnapshot,
    QMPStatusIdentity,
)
from somnus_vm.host.registry import (
    DiskRecord,
    OperationCheckpoint,
    OperationRecord,
    SQLiteRegistry,
)


BASE_TIME = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)
VM_ID = UUID("c7266074-5bcc-4a7c-9ea7-9e8e9b3312a0")
SYSTEM_EXECUTABLE = resolve_executable("sleep")


def _write_configuration(root: Path) -> Path:
    path = root / "host.toml"
    path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "p4-runtime-journal"',
                "",
                "[host]",
                f'state_dir = "{root / "state"}"',
                f'runtime_dir = "{root / "runtime"}"',
                f'base_image = "{root / "images/base.qcow2"}"',
                'qemu_binary = "sleep"',
                'qemu_img_binary = "qemu-img"',
                "enable_kvm = false",
                "ssh_ports = [45000, 45127]",
                "agent_ports = [45200, 45327]",
                "vnc_ports = [6200, 6327]",
                "agent_timeout_seconds = 2.0",
                "",
                "[components]",
                "guest_runtime_candidates = false",
                "operator_shell = false",
                "native_tools = false",
                "file_processing = false",
                "digital_twin_lineage = false",
                "quarantined_runtime = false",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


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


def _booting_record(
    disk_path: Path,
    plan: object,
) -> VMRecord:
    record = VMRecord(
        definition=VMDefinition(
            name="journal-aipc",
            disk_path=os.fspath(disk_path),
            memory_mib=4_096,
            vcpus=2,
            enable_kvm=False,
        ),
        ports=VMPorts(ssh=45_001, agent=45_201, vnc=6_201),
        vm_id=VM_ID,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    ).with_planned_runtime(
        qmp_socket=os.fspath(plan.qmp_socket),
        pid_file=os.fspath(plan.pid_file),
        log_path=os.fspath(plan.serial_log_path),
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
                "image_sha256": "1" * 64,
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


def _process_fact(
    intent: RuntimeLaunchIntent,
    *,
    pid: int,
    start_time_ticks: int,
    argv: tuple[str, ...],
    target: bool,
    seconds: int,
) -> dict[str, object]:
    executable = intent.executable
    selected_argv = intent.executed_argv if target else argv
    return {
        "pid": pid,
        "start_time_ticks": start_time_ticks,
        "proc_device_id": 700 + pid,
        "proc_inode": 800 + pid,
        "process_owner_uid": os.geteuid(),
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
        "argv": list(selected_argv),
        "command_sha256": command_sha256(selected_argv),
        "process_group_id": pid,
        "session_id": pid,
        "observed_at": (BASE_TIME + timedelta(seconds=seconds)).isoformat(),
    }


class _Authority:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.disk_path = root / "state" / "vms" / "journal-aipc.qcow2"
        self.disk_path.parent.mkdir(mode=0o700, parents=True)
        self.disk_path.write_bytes(b"physical-P3-owner\n")
        self.disk_path.chmod(0o600)
        configuration = load_configuration(_write_configuration(root)).host
        configuration.daemon.runtime_root.mkdir(mode=0o700, parents=True)
        configuration.storage.log_root.mkdir(mode=0o700, parents=True)
        bare = VMRecord(
            definition=VMDefinition(
                name="journal-aipc",
                disk_path=os.fspath(self.disk_path),
                memory_mib=4_096,
                vcpus=2,
                enable_kvm=False,
            ),
            ports=VMPorts(ssh=45_001, agent=45_201, vnc=6_201),
            vm_id=VM_ID,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
        self.plan = QemuCommandBuilder(configuration).build(bare)
        self.record = _booting_record(self.disk_path, self.plan)
        details = self.disk_path.stat(follow_symlinks=False)
        self.disk = DiskRecord(
            disk_id=uuid4(),
            vm_id=self.record.vm_id,
            path=os.fspath(self.disk_path),
            generation=self.record.generation,
            ownership="managed",
            active=True,
            operation_id=uuid4(),
            released_by_operation_id=None,
            ownership_token=uuid4(),
            materialized=True,
            base_image_id=uuid4(),
            base_image_sha256="2" * 64,
            virtual_size_bytes=100 * 1024**3,
            format="qcow2",
            device_id=details.st_dev,
            inode=details.st_ino,
            file_size_bytes=details.st_size,
            allocated_size_bytes=details.st_blocks * 512,
            chain_sha256="3" * 64,
            marker_sha256="4" * 64,
            qemu_img_version="qemu-img version 8.2.2",
            materialized_by_operation_id=uuid4(),
        )
        self.intent = RuntimeLaunchIntent.from_runtime(
            self.record,
            self.disk,
            self.plan,
            inspect_executable(os.fspath(SYSTEM_EXECUTABLE)),
            executed_argv=(
                os.fspath(SYSTEM_EXECUTABLE),
                *bind_block_fds(
                    self.plan,
                    overlay_fd=101,
                    base_fd=102,
                )[1:],
            ),
        )
        self.log_process = _process_fact(
            self.intent,
            pid=41_001,
            start_time_ticks=51_001,
            argv=(self.intent.executable.path, "log-guardian"),
            target=False,
            seconds=4,
        )
        self.qemu_guard = _process_fact(
            self.intent,
            pid=41_002,
            start_time_ticks=51_002,
            argv=(self.intent.executable.path, "qemu-exec-guard"),
            target=False,
            seconds=5,
        )
        self.process = _process_fact(
            self.intent,
            pid=41_002,
            start_time_ticks=51_002,
            argv=self.intent.executed_argv,
            target=True,
            seconds=6,
        )
        self.qmp_evidence = self._qmp_evidence()
        runtime_details = configuration.daemon.runtime_root.stat(
            follow_symlinks=False
        )
        log_details = configuration.storage.log_root.stat(
            follow_symlinks=False
        )
        serial_read, serial_write = os.pipe()
        qemu_read, qemu_write = os.pipe()
        try:
            serial_pipe = os.fstat(serial_read)
            qemu_pipe = os.fstat(qemu_read)
        finally:
            for descriptor in (
                serial_read,
                serial_write,
                qemu_read,
                qemu_write,
            ):
                os.close(descriptor)
        pidfile = Path(self.intent.pid_file)
        pidfile.write_text(
            f"{self.process['pid']}\n",
            encoding="ascii",
        )
        pidfile.chmod(0o600)
        pidfile_details = pidfile.stat(follow_symlinks=False)
        qmp_socket_path = Path(self.intent.qmp_socket)
        qmp_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            qmp_socket.bind(os.fspath(qmp_socket_path))
            qmp_socket_path.chmod(0o600)
            qmp_socket_details = qmp_socket_path.stat(
                follow_symlinks=False
            )
        finally:
            qmp_socket.close()
        self.facts: dict[str, dict[str, object]] = {
            "private_paths_prepared": {
                "owner_uid": os.geteuid(),
                "runtime_root_device_id": runtime_details.st_dev,
                "runtime_root_inode": runtime_details.st_ino,
                "runtime_root_mode": 0o700,
                "log_root_device_id": log_details.st_dev,
                "log_root_inode": log_details.st_ino,
                "log_root_mode": 0o700,
            },
            "executable_pinned": {
                "executable": self.intent.executable.to_dict(),
            },
            "log_guard_spawned": {
                "process": self.log_process,
                "serial_pipe_device_id": serial_pipe.st_dev,
                "serial_pipe_inode": serial_pipe.st_ino,
                "qemu_pipe_device_id": qemu_pipe.st_dev,
                "qemu_pipe_inode": qemu_pipe.st_ino,
            },
            "log_guard_released": {
                "pid": self.log_process["pid"],
                "start_time_ticks": self.log_process["start_time_ticks"],
            },
            "qemu_guard_spawned": {"process": self.qemu_guard},
            "qemu_target_released": {
                "pid": self.qemu_guard["pid"],
                "start_time_ticks": self.qemu_guard["start_time_ticks"],
            },
            "process_observed": {"process": self.process},
            "pidfile_verified": {
                "pidfile_pid": self.process["pid"],
                "device_id": pidfile_details.st_dev,
                "inode": pidfile_details.st_ino,
                "owner_uid": pidfile_details.st_uid,
                "mode": stat.S_IMODE(pidfile_details.st_mode),
                "link_count": pidfile_details.st_nlink,
                "size_bytes": pidfile_details.st_size,
                "mtime_ns": pidfile_details.st_mtime_ns,
                "ctime_ns": pidfile_details.st_ctime_ns,
            },
            "qmp_greeting_observed": {
                "qmp_evidence_id": str(self.intent.qmp_evidence_id),
                "peer_pid": self.process["pid"],
                "peer_uid": self.process["process_owner_uid"],
                "greeting_sha256": self.qmp_evidence.first.snapshot.greeting_sha256,
                "qmp_socket_device_id": qmp_socket_details.st_dev,
                "qmp_socket_inode": qmp_socket_details.st_ino,
                "qmp_socket_mode": stat.S_IMODE(
                    qmp_socket_details.st_mode
                ),
            },
            "qmp_capabilities_negotiated": {
                "qmp_evidence_id": str(self.intent.qmp_evidence_id),
                "peer_pid": self.process["pid"],
                "capabilities_sha256": canonical_evidence_sha256(
                    list(self.qmp_evidence.first.snapshot.qmp_capabilities)
                ),
            },
            "qmp_identity_verified": {
                "evidence": self.qmp_evidence,
                "evidence_sha256": self.qmp_evidence.evidence_sha256,
            },
            "process_identity_recorded": {
                "process_record_id": str(self.intent.process_record_id),
                "process_identity_sha256": canonical_evidence_sha256(
                    {
                        "pid": self.process["pid"],
                        "start_time_ticks": self.process[
                            "start_time_ticks"
                        ],
                        "executable": self.process["executable"],
                        "executable_sha256": self.process[
                            "executable_sha256"
                        ],
                        "observed_at": self.process["observed_at"],
                    }
                ),
            },
            "disk_observation_recorded": {
                "disk_observation_id": str(
                    self.intent.disk_observation_id
                ),
                "disk_id": str(self.intent.disk_id),
                "process_record_id": str(self.intent.process_record_id),
                "canonical_sha256": canonical_evidence_sha256(
                    {
                        "disk_id": str(self.intent.disk_id),
                        "observation_id": str(
                            self.intent.disk_observation_id
                        ),
                    }
                ),
            },
            "lifecycle_committed": {
                "lifecycle_evidence_id": str(
                    self.intent.lifecycle_evidence_id
                ),
                "qmp_evidence_id": str(self.intent.qmp_evidence_id),
                "process_record_id": str(self.intent.process_record_id),
                "state": VMState.QMP_RUNNING.value,
                "canonical_sha256": canonical_evidence_sha256(
                    {
                        "evidence_id": str(
                            self.intent.lifecycle_evidence_id
                        ),
                        "state": VMState.QMP_RUNNING.value,
                    }
                ),
            },
        }

    def _qmp_evidence(self) -> QMPIdentityEvidence:
        layer = QMPBlockLayer(
            path=self.disk_path,
            format="qcow2",
            virtual_size_bytes=self.intent.disk_virtual_size_bytes,
            device_id=self.intent.disk_device_id,
            inode=self.intent.disk_inode,
        )
        block = QMPBlockIdentity(
            device="",
            qdev_id="somnus-root-disk",
            node_name="somnus-disk",
            storage_chain_sha256=self.intent.disk_chain_sha256,
            layers=(layer,),
        )
        cpus = tuple(
            QMPCPUIdentity(
                cpu_index=index,
                qom_path=f"/machine/unattached/device[{index}]",
                process_thread_id=threading.get_native_id() + index,
                target="x86_64",
                socket_id=0,
                core_id=index,
                thread_id=0,
                die_id=0,
                node_id=0,
            )
            for index in range(self.intent.vcpus)
        )
        snapshot = QMPIdentitySnapshot(
            vm_id=self.intent.vm_id,
            name=self.intent.vm_name,
            peer_pid=int(self.process["pid"]),
            qemu_version=(8, 2, 2),
            qemu_package="vm-lab-p4",
            qmp_capabilities=(),
            greeting_sha256="5" * 64,
            status=QMPStatusIdentity(running=True, status="running"),
            block=block,
            cpus=cpus,
        )
        first = QMPIdentitySample(
            snapshot=snapshot,
            monotonic_ns=1_000_000_000,
            command_ids=(1, 2, 3, 4, 5),
        )
        second = QMPIdentitySample(
            snapshot=snapshot,
            monotonic_ns=1_000_000_000 + MIN_QMP_STABILIZATION_NS,
            command_ids=(6, 7, 8, 9, 10),
        )
        return QMPIdentityEvidence(
            observation_id=self.intent.qmp_evidence_id,
            vm_id=self.intent.vm_id,
            first=first,
            second=second,
        )

    def full_journal(self) -> RuntimeLaunchJournal:
        state = RuntimeLaunchJournal(self.intent)
        for name in CHECKPOINT_ORDER:
            state, _write = state.advance(name, self.facts.get(name))
        return state


class QemuRuntimeJournalP4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-runtime-journal-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()
        self.authority = _Authority(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_intent_pregenerates_unique_authority_and_distinguishes_execution(
        self,
    ) -> None:
        intent = self.authority.intent
        identities = {
            intent.operation_id,
            intent.vm_id,
            intent.boot_id,
            intent.disk_id,
            intent.process_record_id,
            intent.qmp_evidence_id,
            intent.lifecycle_evidence_id,
            intent.disk_observation_id,
        }
        self.assertEqual(len(identities), 8)
        self.assertEqual(intent.planned_argv[0], "sleep")
        self.assertEqual(intent.executed_argv[0], os.fspath(SYSTEM_EXECUTABLE))
        self.assertNotEqual(intent.planned_argv, intent.executed_argv)
        self.assertEqual(
            validate_fd_bound_execution(
                intent.planned_argv,
                intent.executed_argv,
            ),
            (101, 102),
        )
        self.assertEqual(
            intent.planned_command_sha256,
            command_sha256(intent.planned_argv),
        )
        self.assertEqual(
            intent.executed_command_sha256,
            command_sha256(intent.executed_argv),
        )
        persisted = RuntimeLaunchIntent.from_persisted(
            intent.intent_bytes(),
            intent.owned_resources_bytes(),
        )
        self.assertEqual(persisted, intent)
        self.assertLessEqual(len(intent.intent_bytes()), 262_144)
        self.assertLessEqual(len(intent.owned_resources_bytes()), 262_144)

    def test_real_registry_consumes_writes_and_replays_full_qmp_evidence(
        self,
    ) -> None:
        database = self.root / "registry" / "registry.sqlite3"
        state = RuntimeLaunchJournal(self.authority.intent)
        state, intent_checkpoint = state.advance("intent_persisted")
        with SQLiteRegistry.open(database) as registry:
            with registry.write_transaction() as transaction:
                transaction.insert_vm(self.authority.record)
                transaction.begin_operation(
                    **state.begin_operation_kwargs()
                )
                transaction.append_checkpoint(
                    **intent_checkpoint.append_checkpoint_kwargs()
                )
                transaction.set_operation_status(
                    **state.running_status_write().set_operation_status_kwargs()
                )
            for name in CHECKPOINT_ORDER[1:-1]:
                state, checkpoint = state.advance(
                    name,
                    self.authority.facts[name],
                )
                with registry.write_transaction() as transaction:
                    transaction.append_checkpoint(
                        **checkpoint.append_checkpoint_kwargs()
                    )
            state, completion = state.advance("completion_observed")
            with registry.write_transaction() as transaction:
                transaction.append_checkpoint(
                    **completion.append_checkpoint_kwargs()
                )
                transaction.set_operation_status(
                    **state.completion_status_write().set_operation_status_kwargs()
                )
            operation = registry.get_operation(
                self.authority.intent.operation_id
            )
            checkpoints = registry.list_checkpoints(
                self.authority.intent.operation_id
            )
            self.assertIsInstance(operation, OperationRecord)
            assert operation is not None
            replay = RuntimeLaunchJournal.replay(operation, checkpoints)
        self.assertEqual(replay.latest_checkpoint, "completion_observed")
        self.assertEqual(replay.crash_class, CrashClass.COMPLETE)
        self.assertEqual(
            replay.recovery_disposition,
            RecoveryDisposition.NONE,
        )
        qmp_payload = json.loads(
            checkpoints[
                CHECKPOINT_ORDER.index("qmp_identity_verified")
            ].payload
        )["facts"]
        self.assertEqual(
            qmp_payload["evidence"],
            self.authority.qmp_evidence.to_dict(),
        )
        self.assertEqual(
            qmp_payload["evidence_sha256"],
            self.authority.qmp_evidence.evidence_sha256,
        )

    def test_every_prefix_has_one_explicit_crash_recovery_disposition(
        self,
    ) -> None:
        full = self.authority.full_journal()
        for count in range(1, len(CHECKPOINT_ORDER) + 1):
            with self.subTest(latest=CHECKPOINT_ORDER[count - 1]):
                prefix = RuntimeLaunchJournal(
                    full.intent,
                    full.checkpoints[:count],
                )
                write = prefix.recovery_status_write(
                    "daemon_restart",
                    cleanup_state="unknown",
                )
                result = json.loads(write.result)
                self.assertEqual(
                    result["crash_class"],
                    prefix.crash_class.value,
                )
                self.assertEqual(
                    result["recovery_disposition"],
                    prefix.recovery_disposition.value,
                )
                self.assertNotEqual(
                    prefix.crash_class,
                    CrashClass.NOT_PERSISTED,
                )

    def test_observed_process_access_is_typed_restricted_and_copy_safe(
        self,
    ) -> None:
        full = self.authority.full_journal()
        log_facts = self.authority.facts["log_guard_spawned"]
        expected_pipes = (
            (
                int(log_facts["serial_pipe_device_id"]),
                int(log_facts["serial_pipe_inode"]),
            ),
            (
                int(log_facts["qemu_pipe_device_id"]),
                int(log_facts["qemu_pipe_inode"]),
            ),
        )
        self.assertEqual(full.log_pipe_identities(), expected_pipes)
        self.assertNotEqual(*full.log_pipe_identities())
        expected = {
            "log_guard_spawned": self.authority.log_process,
            "qemu_guard_spawned": self.authority.qemu_guard,
            "process_observed": self.authority.process,
        }
        for checkpoint_name, payload in expected.items():
            with self.subTest(checkpoint=checkpoint_name):
                first = full.observed_process_at(checkpoint_name)
                second = full.observed_process_at(checkpoint_name)
                self.assertEqual(first.to_dict(), payload)
                self.assertEqual(second, first)
                self.assertIsNot(second, first)
                exported = first.to_dict()
                exported["pid"] = 99_999
                self.assertEqual(
                    full.observed_process_at(checkpoint_name).pid,
                    payload["pid"],
                )
                with self.assertRaises(FrozenInstanceError):
                    first.pid = 99_999  # type: ignore[misc]

        before_process = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("process_observed")
            ],
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "process checkpoint is absent",
        ):
            before_process.observed_process_at("process_observed")
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "available only",
        ):
            full.observed_process_at("pidfile_verified")
        before_log_guard = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("log_guard_spawned")
            ],
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "pipe identities are absent",
        ):
            before_log_guard.log_pipe_identities()

    def test_pidfile_and_qmp_socket_accessors_are_unlink_safe_and_frozen(
        self,
    ) -> None:
        full = self.authority.full_journal()
        runtime_root, log_root = full.private_directory_identities()
        path_facts = self.authority.facts["private_paths_prepared"]
        self.assertEqual(
            (runtime_root.device_id, runtime_root.inode, runtime_root.mode),
            (
                path_facts["runtime_root_device_id"],
                path_facts["runtime_root_inode"],
                path_facts["runtime_root_mode"],
            ),
        )
        self.assertEqual(
            (log_root.device_id, log_root.inode, log_root.mode),
            (
                path_facts["log_root_device_id"],
                path_facts["log_root_inode"],
                path_facts["log_root_mode"],
            ),
        )
        runtime_details = Path(runtime_root.path).lstat()
        self.assertEqual(
            (runtime_details.st_dev, runtime_details.st_ino),
            (runtime_root.device_id, runtime_root.inode),
        )
        before_private_paths = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("private_paths_prepared")
            ],
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "not yet durable",
        ):
            before_private_paths.private_directory_identities()

        pidfile = full.pidfile_identity()
        pidfile_again = full.pidfile_identity()
        pidfile_facts = self.authority.facts["pidfile_verified"]
        self.assertEqual(pidfile.path, self.authority.intent.pid_file)
        self.assertEqual(pidfile.pid, pidfile_facts["pidfile_pid"])
        self.assertEqual(pidfile.device_id, pidfile_facts["device_id"])
        self.assertEqual(pidfile.inode, pidfile_facts["inode"])
        self.assertEqual(pidfile.owner_uid, pidfile_facts["owner_uid"])
        self.assertEqual(pidfile.mode, 0o600)
        self.assertEqual(pidfile.link_count, 1)
        self.assertEqual(pidfile_again, pidfile)
        self.assertIsNot(pidfile_again, pidfile)
        current_pidfile = Path(pidfile.path).lstat()
        self.assertEqual(
            (current_pidfile.st_dev, current_pidfile.st_ino),
            (pidfile.device_id, pidfile.inode),
        )
        with self.assertRaises(FrozenInstanceError):
            pidfile.inode = 1  # type: ignore[misc]

        qmp_socket = full.qmp_socket_identity()
        qmp_socket_again = full.qmp_socket_identity()
        greeting_facts = self.authority.facts["qmp_greeting_observed"]
        self.assertEqual(qmp_socket.path, self.authority.intent.qmp_socket)
        self.assertEqual(
            qmp_socket.device_id,
            greeting_facts["qmp_socket_device_id"],
        )
        self.assertEqual(
            qmp_socket.inode,
            greeting_facts["qmp_socket_inode"],
        )
        self.assertEqual(qmp_socket.uid, greeting_facts["peer_uid"])
        self.assertEqual(qmp_socket.mode, 0o600)
        self.assertEqual(qmp_socket_again, qmp_socket)
        self.assertIsNot(qmp_socket_again, qmp_socket)
        current_socket = Path(qmp_socket.path).lstat()
        self.assertEqual(
            (current_socket.st_dev, current_socket.st_ino),
            (qmp_socket.device_id, qmp_socket.inode),
        )
        with self.assertRaises(FrozenInstanceError):
            qmp_socket.inode = 1  # type: ignore[misc]

        before_pidfile = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("pidfile_verified")
            ],
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "identity is absent",
        ):
            before_pidfile.pidfile_identity()
        before_greeting = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("qmp_greeting_observed")
            ],
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "socket identity is absent",
        ):
            before_greeting.qmp_socket_identity()

    def test_skipped_reused_out_of_order_and_incoherent_facts_fail_closed(
        self,
    ) -> None:
        state = RuntimeLaunchJournal(self.authority.intent)
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "next checkpoint",
        ):
            state.advance(
                "private_paths_prepared",
                self.authority.facts["private_paths_prepared"],
            )
        state, _ = state.advance("intent_persisted")
        state, _ = state.advance(
            "private_paths_prepared",
            self.authority.facts["private_paths_prepared"],
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "next checkpoint",
        ):
            state.advance(
                "private_paths_prepared",
                self.authority.facts["private_paths_prepared"],
            )
        state, _ = state.advance(
            "executable_pinned",
            self.authority.facts["executable_pinned"],
        )
        aliased_pipes = dict(
            self.authority.facts["log_guard_spawned"]
        )
        aliased_pipes["qemu_pipe_device_id"] = aliased_pipes[
            "serial_pipe_device_id"
        ]
        aliased_pipes["qemu_pipe_inode"] = aliased_pipes[
            "serial_pipe_inode"
        ]
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "distinct identities",
        ):
            state.advance("log_guard_spawned", aliased_pipes)
        state, _ = state.advance(
            "log_guard_spawned",
            self.authority.facts["log_guard_spawned"],
        )
        bad_release = dict(self.authority.facts["log_guard_released"])
        bad_release["pid"] = int(bad_release["pid"]) + 1
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "not the durably observed guardian",
        ):
            state.advance("log_guard_released", bad_release)

        full = self.authority.full_journal()
        before_pidfile = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("pidfile_verified")
            ],
        )
        public_pidfile = dict(
            self.authority.facts["pidfile_verified"]
        )
        public_pidfile["mode"] = 0o644
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "owner-private",
        ):
            before_pidfile.advance("pidfile_verified", public_pidfile)
        linked_pidfile = dict(
            self.authority.facts["pidfile_verified"]
        )
        linked_pidfile["link_count"] = 2
        with self.assertRaises(RuntimeLaunchJournalError):
            before_pidfile.advance("pidfile_verified", linked_pidfile)
        before_greeting = RuntimeLaunchJournal(
            full.intent,
            full.checkpoints[
                : CHECKPOINT_ORDER.index("qmp_greeting_observed")
            ],
        )
        public_socket = dict(
            self.authority.facts["qmp_greeting_observed"]
        )
        public_socket["qmp_socket_mode"] = 0o660
        with self.assertRaises(RuntimeLaunchJournalError):
            before_greeting.advance(
                "qmp_greeting_observed",
                public_socket,
            )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "skipped, reused, or out of order",
        ):
            RuntimeLaunchJournal(
                full.intent,
                (
                    full.checkpoints[0],
                    replace(full.checkpoints[2], ordinal=1),
                ),
            )

    def test_replay_rejects_tampered_full_qmp_evidence_even_with_new_hash(
        self,
    ) -> None:
        database = self.root / "tamper" / "registry.sqlite3"
        state = RuntimeLaunchJournal(self.authority.intent)
        state, first = state.advance("intent_persisted")
        with SQLiteRegistry.open(database) as registry:
            with registry.write_transaction() as transaction:
                transaction.insert_vm(self.authority.record)
                transaction.begin_operation(**state.begin_operation_kwargs())
                transaction.append_checkpoint(
                    **first.append_checkpoint_kwargs()
                )
                transaction.set_operation_status(
                    **state.running_status_write().set_operation_status_kwargs()
                )
            for name in CHECKPOINT_ORDER[1:12]:
                state, write = state.advance(
                    name,
                    self.authority.facts[name],
                )
                with registry.write_transaction() as transaction:
                    transaction.append_checkpoint(
                        **write.append_checkpoint_kwargs()
                    )
            operation = registry.get_operation(
                self.authority.intent.operation_id
            )
            checkpoints = list(
                registry.list_checkpoints(
                    self.authority.intent.operation_id
                )
            )
        assert operation is not None
        index = CHECKPOINT_ORDER.index("qmp_identity_verified")
        payload = json.loads(checkpoints[index].payload)
        payload["facts"]["evidence"]["snapshot"]["vm_id"] = str(uuid4())
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        checkpoints[index] = replace(
            checkpoints[index],
            payload=encoded,
            payload_sha256=canonical_evidence_sha256(
                json.loads(encoded)
            ),
        )
        with self.assertRaises(RuntimeLaunchJournalError):
            RuntimeLaunchJournal.replay(operation, checkpoints)

    def test_reused_ids_command_drift_and_unproven_terminal_failure_reject(
        self,
    ) -> None:
        intent = self.authority.intent
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "cannot be reused",
        ):
            replace(
                intent,
                qmp_evidence_id=intent.process_record_id,
            )
        drifted = (
            intent.executed_argv[0],
            *intent.executed_argv[1:-1],
            "changed",
        )
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "fd-bound storage rewrite",
        ):
            replace(
                intent,
                executed_argv=drifted,
                executed_command_sha256=command_sha256(drifted),
            )
        state = RuntimeLaunchJournal(intent)
        state, _ = state.advance("intent_persisted")
        with self.assertRaisesRegex(
            RuntimeLaunchJournalError,
            "proven absence or exit",
        ):
            state.terminal_status_write(
                "failed",
                reason="launch_failed",
                cleanup_state="orphaned",
            )


if __name__ == "__main__":
    unittest.main()
