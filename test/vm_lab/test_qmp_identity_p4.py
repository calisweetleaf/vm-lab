"""Focused P4 proof for strict host-owned QMP identity composition."""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

from somnus_protocol._validation import freeze_json
from somnus_vm.host.qmp import QMPGreeting, QMPResponse, QMPVersion
from somnus_vm.host.qmp_identity import (
    MIN_QMP_STABILIZATION_NS,
    QMPBlockContract,
    QMPBlockLayer,
    QMPDiskRuntimeEvidence,
    QMPIdentityError,
    QMPIdentityExpectation,
    compose_qmp_identity_evidence,
    extract_qmp_disk_runtime_evidence,
    greeting_sha256,
    normalize_qmp_identity_sample,
    normalize_qmp_blockstats,
    normalize_qmp_fdsets,
    normalize_qmp_named_block_nodes,
    normalize_qmp_status,
)


_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_VM_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
_VIRTUAL_SIZE = 8 * 1024**3


class QMPIdentityP4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self.overlay = self.root / "aipc.qcow2"
        self.base = self.root / "base.qcow2"
        self.overlay.write_bytes(b"concrete-overlay-inode")
        self.base.write_bytes(b"concrete-base-inode")

        self._release_threads = threading.Event()
        self._thread_barrier = threading.Barrier(3)
        self._thread_ids: list[int] = []
        self._thread_ids_lock = threading.Lock()

        def vcpu_thread() -> None:
            native_id = threading.get_native_id()
            with self._thread_ids_lock:
                self._thread_ids.append(native_id)
            self._thread_barrier.wait(timeout=5.0)
            self._release_threads.wait(timeout=30.0)

        self._threads = [
            threading.Thread(
                target=vcpu_thread,
                name=f"concrete-vcpu-{index}",
                daemon=False,
            )
            for index in range(2)
        ]
        for thread in self._threads:
            thread.start()
        self._thread_barrier.wait(timeout=5.0)
        self.thread_ids = tuple(sorted(self._thread_ids))
        self.assertEqual(len(self.thread_ids), 2)

        overlay_stat = self.overlay.stat(follow_symlinks=False)
        base_stat = self.base.stat(follow_symlinks=False)
        self.block_contract = QMPBlockContract(
            layers=(
                QMPBlockLayer(
                    path=self.overlay,
                    format="qcow2",
                    virtual_size_bytes=_VIRTUAL_SIZE,
                    device_id=overlay_stat.st_dev,
                    inode=overlay_stat.st_ino,
                ),
                QMPBlockLayer(
                    path=self.base,
                    format="qcow2",
                    virtual_size_bytes=_VIRTUAL_SIZE,
                    device_id=base_stat.st_dev,
                    inode=base_stat.st_ino,
                ),
            ),
            storage_chain_sha256="1" * 64,
        )
        self.expectation = QMPIdentityExpectation(
            vm_id=_VM_ID,
            name="somnus-aipc",
            process_pid=os.getpid(),
            vcpus=2,
            block=self.block_contract,
        )
        self.greeting = QMPGreeting(
            version=QMPVersion(
                major=8,
                minor=2,
                micro=10,
                package="vm-lab-test",
            ),
            capabilities=("oob",),
        )

    def tearDown(self) -> None:
        self._release_threads.set()
        for thread in self._threads:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
        self._temporary.cleanup()

    def _image_payload(self) -> dict[str, object]:
        return {
            "filename": str(self.overlay),
            "format": "qcow2",
            "virtual-size": _VIRTUAL_SIZE,
            "actual-size": self.overlay.stat().st_size,
            "dirty-flag": False,
            "cluster-size": 65_536,
            "encrypted": False,
            "compressed": False,
            "backing-filename": str(self.base),
            "full-backing-filename": str(self.base),
            "backing-filename-format": "qcow2",
            "snapshots": [],
            "format-specific": {
                "type": "qcow2",
                "data": {
                    "compat": "1.1",
                    "extended-l2": False,
                    "lazy-refcounts": False,
                    "corrupt": False,
                    "refcount-bits": 16,
                    "bitmaps": [],
                    "compression-type": "zlib",
                },
            },
            "backing-image": {
                "filename": str(self.base),
                "format": "qcow2",
                "virtual-size": _VIRTUAL_SIZE,
                "actual-size": self.base.stat().st_size,
                "encrypted": False,
            },
        }

    def _block_payload(self) -> list[object]:
        return [
            {
                "device": "somnus-root-disk",
                "qdev": "somnus-root-disk",
                "type": "unknown",
                "removable": False,
                "locked": False,
                "io-status": "ok",
                "inserted": {
                    "file": str(self.overlay),
                    "node-name": "somnus-disk",
                    "ro": False,
                    "drv": "qcow2",
                    "backing_file": str(self.base),
                    "backing_file_depth": 1,
                    "encrypted": False,
                    "detect_zeroes": "off",
                    "bps": 0,
                    "bps_rd": 0,
                    "bps_wr": 0,
                    "iops": 0,
                    "iops_rd": 0,
                    "iops_wr": 0,
                    "bps_max": 0,
                    "bps_rd_max": 0,
                    "bps_wr_max": 0,
                    "iops_max": 0,
                    "iops_rd_max": 0,
                    "iops_wr_max": 0,
                    "iops_size": 0,
                    "cache": {
                        "writeback": True,
                        "direct": False,
                        "no-flush": False,
                    },
                    "write_threshold": 0,
                    "dirty-bitmaps": [],
                    "image": self._image_payload(),
                },
            }
        ]

    def _cpu_payload(self) -> list[object]:
        # Reverse wire order proves normalization is by the stable CPU index.
        return [
            {
                "cpu-index": index,
                "qom-path": f"/machine/unattached/device[{index}]",
                "thread-id": self.thread_ids[index],
                "props": {
                    "socket-id": 0,
                    "die-id": 0,
                    "core-id": index,
                    "thread-id": 0,
                },
                "target": "x86_64",
            }
            for index in reversed(range(2))
        ]

    @staticmethod
    def _response(
        command: str,
        command_id: int,
        value: object,
    ) -> QMPResponse:
        return QMPResponse(
            command=command,
            command_id=command_id,
            value=freeze_json(
                value,
                f"{command} concrete QMP value",
                max_depth=16,
                max_items=8_192,
                max_string=4_096,
            ),
        )

    def _sample(
        self,
        *,
        command_id: int,
        monotonic_ns: int,
        greeting: QMPGreeting | None = None,
        status: object | None = None,
        name: object | None = None,
        uuid: object | None = None,
        block: object | None = None,
        cpus: object | None = None,
        expectation: QMPIdentityExpectation | None = None,
    ):
        return normalize_qmp_identity_sample(
            expectation=expectation or self.expectation,
            greeting=greeting or self.greeting,
            peer_pid=(expectation or self.expectation).process_pid,
            status_response=self._response(
                "query-status",
                command_id,
                {"running": True, "status": "running"}
                if status is None
                else status,
            ),
            name_response=self._response(
                "query-name",
                command_id + 1,
                {"name": "somnus-aipc"} if name is None else name,
            ),
            uuid_response=self._response(
                "query-uuid",
                command_id + 2,
                {"UUID": str(_VM_ID)} if uuid is None else uuid,
            ),
            block_response=self._response(
                "query-block",
                command_id + 3,
                self._block_payload() if block is None else block,
            ),
            cpus_response=self._response(
                "query-cpus-fast",
                command_id + 4,
                self._cpu_payload() if cpus is None else cpus,
            ),
            monotonic_ns=monotonic_ns,
        )

    def test_concrete_qmp_values_form_stable_evidence_with_real_proc_threads(
        self,
    ) -> None:
        first_time = time.monotonic_ns()
        first = self._sample(command_id=2, monotonic_ns=first_time)
        second = self._sample(
            command_id=7,
            monotonic_ns=first_time + MIN_QMP_STABILIZATION_NS,
        )
        observation_id = uuid4()
        evidence = compose_qmp_identity_evidence(
            observation_id=observation_id,
            vm_id=_VM_ID,
            first=first,
            second=second,
        )

        self.assertEqual(evidence.observation_id, observation_id)
        self.assertEqual(
            evidence.stabilization_ns,
            MIN_QMP_STABILIZATION_NS,
        )
        self.assertEqual(first.snapshot, second.snapshot)
        self.assertRegex(first.snapshot.snapshot_sha256, _SHA256)
        self.assertRegex(evidence.evidence_sha256, _SHA256)
        self.assertEqual(
            [cpu.cpu_index for cpu in first.snapshot.cpus],
            [0, 1],
        )
        self.assertEqual(
            {cpu.process_thread_id for cpu in first.snapshot.cpus},
            set(self.thread_ids),
        )
        for thread_id in self.thread_ids:
            self.assertTrue(
                (
                    Path("/proc")
                    / str(os.getpid())
                    / "task"
                    / str(thread_id)
                    / "status"
                ).is_file()
            )
        self.assertEqual(
            first.snapshot.block.storage_chain_sha256,
            self.block_contract.storage_chain_sha256,
        )
        self.assertEqual(
            evidence.evidence_sha256,
            compose_qmp_identity_evidence(
                observation_id=observation_id,
                vm_id=_VM_ID,
                first=first,
                second=second,
            ).evidence_sha256,
        )

    def test_fd_bound_machine_truth_observers_and_qmp_filenames(self) -> None:
        """Normalize the exact fdset, node, and recursive graph contracts."""

        fdsets = normalize_qmp_fdsets(
            self._response(
                "query-fdsets",
                90,
                [
                    {
                        "fdset-id": 2,
                        "fds": [
                            {"fd": 12, "opaque": "somnus-base-ro"}
                        ],
                    },
                    {
                        "fdset-id": 1,
                        "fds": [
                            {"fd": 11, "opaque": "somnus-overlay-rw"}
                        ],
                    },
                ],
            )
        )
        self.assertEqual([entry.fdset_id for entry in fdsets], [1, 2])
        self.assertEqual([entry.fd for entry in fdsets], [11, 12])

        nodes = normalize_qmp_named_block_nodes(
            self._response(
                "query-named-block-nodes",
                91,
                [
                    {
                        "node-name": "somnus-disk",
                        "drv": "qcow2",
                        "ro": False,
                        "file": "/dev/fdset/1",
                        "encrypted": False,
                        "image": {"filename": "/dev/fdset/1"},
                    },
                    {
                        "node-name": "somnus-base-qcow2",
                        "drv": "qcow2",
                        "ro": True,
                        "file": "/dev/fdset/2",
                        "encrypted": False,
                    },
                    {
                        "node-name": "somnus-base-file",
                        "drv": "file",
                        "ro": True,
                        "file": "/dev/fdset/2",
                    },
                    {
                        "node-name": "somnus-overlay-file",
                        "drv": "file",
                        "ro": False,
                        "file": "/dev/fdset/1",
                    },
                ],
            )
        )
        self.assertEqual(
            {node.node_name for node in nodes},
            {
                "somnus-overlay-file",
                "somnus-base-file",
                "somnus-base-qcow2",
                "somnus-disk",
            },
        )

        blockstats = normalize_qmp_blockstats(
            self._response(
                "query-blockstats",
                92,
                [
                    {
                        "node-name": "somnus-disk",
                        "parent": {"node-name": "somnus-overlay-file"},
                        "backing": {
                            "node-name": "somnus-base-qcow2",
                            "parent": {"node-name": "somnus-base-file"},
                        },
                    }
                ],
            )
        )
        by_name = {node.node_name: node for node in blockstats}
        self.assertEqual(
            (by_name["somnus-disk"].parent_node, by_name["somnus-disk"].backing_node),
            ("somnus-overlay-file", "somnus-base-qcow2"),
        )
        self.assertEqual(
            by_name["somnus-base-qcow2"].parent_node,
            "somnus-base-file",
        )

        overlay_stat = self.overlay.stat(follow_symlinks=False)
        base_stat = self.base.stat(follow_symlinks=False)
        fd_contract = QMPBlockContract(
            layers=(
                QMPBlockLayer(
                    path=self.overlay,
                    format="qcow2",
                    virtual_size_bytes=_VIRTUAL_SIZE,
                    device_id=overlay_stat.st_dev,
                    inode=overlay_stat.st_ino,
                    qmp_filename="/dev/fdset/1",
                ),
                QMPBlockLayer(
                    path=self.base,
                    format="qcow2",
                    virtual_size_bytes=_VIRTUAL_SIZE,
                    device_id=base_stat.st_dev,
                    inode=base_stat.st_ino,
                    qmp_filename="/dev/fdset/2",
                ),
            ),
            storage_chain_sha256="1" * 64,
        )
        self.assertNotIn("qmp_filename", self.block_contract.layers[0].to_dict())
        self.assertEqual(
            fd_contract.layers[0].to_dict()["qmp_filename"],
            "/dev/fdset/1",
        )
        fd_expectation = replace(self.expectation, block=fd_contract)
        fd_block = self._block_payload()
        inserted = fd_block[0]["inserted"]
        inserted["file"] = "/dev/fdset/1"
        inserted["backing_file"] = "/dev/fdset/2"
        image = inserted["image"]
        image["filename"] = "/dev/fdset/1"
        image["backing-filename"] = "/dev/fdset/2"
        image["full-backing-filename"] = "/dev/fdset/2"
        image["backing-image"]["filename"] = "/dev/fdset/2"
        normalized = self._sample(
            command_id=94,
            monotonic_ns=time.monotonic_ns(),
            block=fd_block,
            expectation=fd_expectation,
        )
        self.assertEqual(normalized.snapshot.block.layers, fd_contract.layers)

    def test_greeting_digest_is_semantic_and_strictly_versioned(self) -> None:
        first = QMPGreeting(
            version=QMPVersion(major=8, minor=2, micro=10, package="pkg"),
            capabilities=("oob", "x-test"),
        )
        reordered = QMPGreeting(
            version=QMPVersion(major=8, minor=2, micro=10, package="pkg"),
            capabilities=("x-test", "oob"),
        )
        self.assertEqual(greeting_sha256(first), greeting_sha256(reordered))
        self.assertRegex(greeting_sha256(first), _SHA256)
        self.assertNotEqual(
            greeting_sha256(first),
            greeting_sha256(
                QMPGreeting(
                    version=QMPVersion(
                        major=8,
                        minor=2,
                        micro=10,
                        package="different",
                    ),
                    capabilities=("oob", "x-test"),
                )
            ),
        )
        with self.assertRaises(QMPIdentityError):
            greeting_sha256(
                QMPGreeting(
                    version=QMPVersion(
                        major=8,
                        minor=3,
                        micro=0,
                        package="unsupported",
                    ),
                    capabilities=(),
                )
            )

    def test_status_name_uuid_and_unknown_fields_fail_closed(self) -> None:
        now = time.monotonic_ns()
        bad_values = (
            {"status": {"running": 1, "status": "running"}},
            {
                "status": {
                    "running": True,
                    "status": "running",
                    "unexpected": False,
                }
            },
            {"status": {"running": False, "status": "running"}},
            {"name": {}},
            {"name": {"name": "another-vm"}},
            {"uuid": {"UUID": str(_VM_ID).upper()}},
            {"uuid": {"UUID": str(uuid4())}},
        )
        for override in bad_values:
            with self.subTest(override=override):
                with self.assertRaises(QMPIdentityError):
                    self._sample(
                        command_id=2,
                        monotonic_ns=now,
                        **override,
                    )

    def test_public_final_status_fence_is_strict_and_standalone(self) -> None:
        normalized = normalize_qmp_status(
            self._response(
                "query-status",
                77,
                {"running": True, "status": "running"},
            )
        )
        self.assertIs(normalized.running, True)
        self.assertEqual(normalized.status, "running")

        hostile = (
            QMPResponse(
                command="query-name",
                command_id=78,
                value=freeze_json(
                    {"running": True, "status": "running"},
                    "mismatched final status command",
                ),
            ),
            QMPResponse(
                command="query-status",
                command_id=True,
                value=freeze_json(
                    {"running": True, "status": "running"},
                    "boolean final status command id",
                ),
            ),
            self._response(
                "query-status",
                79,
                {"running": 1, "status": "running"},
            ),
            self._response(
                "query-status",
                80,
                {"running": False, "status": "running"},
            ),
            self._response(
                "query-status",
                81,
                {"running": True, "status": "paused"},
            ),
            self._response(
                "query-status",
                82,
                {
                    "running": True,
                    "status": "running",
                    "unexpected": False,
                },
            ),
            self._response(
                "query-status",
                83,
                [{"running": True, "status": "running"}],
            ),
        )
        for response in hostile:
            with self.subTest(response=response):
                with self.assertRaises(QMPIdentityError):
                    normalize_qmp_status(response)

    def test_block_identity_rejects_ambiguity_redirection_and_inode_drift(
        self,
    ) -> None:
        now = time.monotonic_ns()

        two_devices = self._block_payload()
        two_devices.append(copy.deepcopy(two_devices[0]))
        with self.assertRaises(QMPIdentityError):
            self._sample(
                command_id=2,
                monotonic_ns=now,
                block=two_devices,
            )

        wrong_qdev = self._block_payload()
        wrong_qdev[0]["qdev"] = "auto-generated-device"
        with self.assertRaises(QMPIdentityError):
            self._sample(
                command_id=2,
                monotonic_ns=now,
                block=wrong_qdev,
            )

        unknown_nested_field = self._block_payload()
        unknown_nested_field[0]["inserted"]["image"]["future-field"] = True
        with self.assertRaises(QMPIdentityError):
            self._sample(
                command_id=2,
                monotonic_ns=now,
                block=unknown_nested_field,
            )

        replacement = self.root / "replacement.qcow2"
        replacement.write_bytes(b"replacement-inode")
        os.replace(replacement, self.overlay)
        with self.assertRaises(QMPIdentityError):
            self._sample(command_id=2, monotonic_ns=now)

    def test_disk_runtime_evidence_is_qmp_measured_and_contract_bound(
        self,
    ) -> None:
        payload = self._block_payload()
        payload[0]["inserted"]["image"]["dirty-flag"] = True
        evidence = extract_qmp_disk_runtime_evidence(
            self._response("query-block", 41, payload),
            self.block_contract,
        )
        self.assertIsInstance(evidence, QMPDiskRuntimeEvidence)
        self.assertEqual(
            evidence.qemu_actual_size_bytes,
            self.overlay.stat().st_size,
        )
        self.assertIs(evidence.dirty, True)
        self.assertIs(evidence.corrupt, False)
        self.assertEqual(evidence.block.layers, self.block_contract.layers)
        self.assertEqual(
            evidence.block.storage_chain_sha256,
            self.block_contract.storage_chain_sha256,
        )
        self.assertEqual(
            evidence.to_dict()["qemu_actual_size_bytes"],
            self.overlay.stat().st_size,
        )

        absent_optional_flags = self._block_payload()
        image = absent_optional_flags[0]["inserted"]["image"]
        del image["dirty-flag"]
        del image["format-specific"]
        clean = extract_qmp_disk_runtime_evidence(
            self._response(
                "query-block",
                42,
                absent_optional_flags,
            ),
            self.block_contract,
        )
        self.assertIs(clean.dirty, False)
        self.assertIs(clean.corrupt, False)

    def test_disk_runtime_evidence_rejects_unmeasured_or_hostile_values(
        self,
    ) -> None:
        missing_actual_size = self._block_payload()
        del missing_actual_size[0]["inserted"]["image"]["actual-size"]
        with self.assertRaisesRegex(QMPIdentityError, "actual-size"):
            extract_qmp_disk_runtime_evidence(
                self._response(
                    "query-block",
                    51,
                    missing_actual_size,
                ),
                self.block_contract,
            )

        for value in (True, -1, 2**63, "1"):
            with self.subTest(actual_size=value):
                invalid_actual_size = self._block_payload()
                invalid_actual_size[0]["inserted"]["image"][
                    "actual-size"
                ] = value
                response = (
                    QMPResponse(
                        command="query-block",
                        command_id=52,
                        value=invalid_actual_size,
                    )
                    if value == 2**63
                    else self._response(
                        "query-block",
                        52,
                        invalid_actual_size,
                    )
                )
                with self.assertRaises(QMPIdentityError):
                    extract_qmp_disk_runtime_evidence(
                        response,
                        self.block_contract,
                    )

        for value in (0, 1, "false", None):
            with self.subTest(dirty_flag=value):
                invalid_dirty = self._block_payload()
                invalid_dirty[0]["inserted"]["image"]["dirty-flag"] = value
                with self.assertRaises(QMPIdentityError):
                    extract_qmp_disk_runtime_evidence(
                        self._response(
                            "query-block",
                            53,
                            invalid_dirty,
                        ),
                        self.block_contract,
                    )

        corrupt = self._block_payload()
        corrupt[0]["inserted"]["image"]["format-specific"]["data"][
            "corrupt"
        ] = True
        with self.assertRaisesRegex(QMPIdentityError, "corrupt"):
            extract_qmp_disk_runtime_evidence(
                self._response("query-block", 54, corrupt),
                self.block_contract,
            )

        foreign_overlay = self.root / "foreign.qcow2"
        foreign_overlay.write_bytes(b"foreign-overlay-inode")
        foreign_details = foreign_overlay.stat(follow_symlinks=False)
        mismatched_contract = QMPBlockContract(
            layers=(
                QMPBlockLayer(
                    path=foreign_overlay,
                    format="qcow2",
                    virtual_size_bytes=_VIRTUAL_SIZE,
                    device_id=foreign_details.st_dev,
                    inode=foreign_details.st_ino,
                ),
                self.block_contract.layers[1],
            ),
            storage_chain_sha256=self.block_contract.storage_chain_sha256,
        )
        with self.assertRaisesRegex(QMPIdentityError, "planned disk"):
            extract_qmp_disk_runtime_evidence(
                self._response(
                    "query-block",
                    55,
                    self._block_payload(),
                ),
                mismatched_contract,
            )

    def test_cpu_identity_rejects_topology_schema_and_foreign_thread(
        self,
    ) -> None:
        now = time.monotonic_ns()

        duplicate_index = self._cpu_payload()
        duplicate_index[0]["cpu-index"] = duplicate_index[1]["cpu-index"]
        with self.assertRaises(QMPIdentityError):
            self._sample(
                command_id=2,
                monotonic_ns=now,
                cpus=duplicate_index,
            )

        future_field = self._cpu_payload()
        future_field[0]["qom-type"] = "host-x86_64-cpu"
        with self.assertRaises(QMPIdentityError):
            self._sample(
                command_id=2,
                monotonic_ns=now,
                cpus=future_field,
            )

        wrong_topology = self._cpu_payload()
        wrong_topology[0]["props"]["socket-id"] = 1
        with self.assertRaises(QMPIdentityError):
            self._sample(
                command_id=2,
                monotonic_ns=now,
                cpus=wrong_topology,
            )

        foreign = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                "import time; time.sleep(30)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            foreign_thread = self._cpu_payload()
            foreign_thread[0]["thread-id"] = foreign.pid
            with self.assertRaises(QMPIdentityError):
                self._sample(
                    command_id=2,
                    monotonic_ns=now,
                    cpus=foreign_thread,
                )
        finally:
            foreign.terminate()
            foreign.wait(timeout=5.0)

    def test_evidence_rejects_short_changed_or_replayed_samples(self) -> None:
        first_time = time.monotonic_ns()
        first = self._sample(command_id=2, monotonic_ns=first_time)
        too_soon = self._sample(
            command_id=7,
            monotonic_ns=first_time + MIN_QMP_STABILIZATION_NS - 1,
        )
        with self.assertRaises(QMPIdentityError):
            compose_qmp_identity_evidence(
                observation_id=uuid4(),
                vm_id=_VM_ID,
                first=first,
                second=too_soon,
            )

        changed = self._sample(
            command_id=7,
            monotonic_ns=first_time + MIN_QMP_STABILIZATION_NS,
            greeting=QMPGreeting(
                version=QMPVersion(
                    major=8,
                    minor=2,
                    micro=10,
                    package="changed-package",
                ),
                capabilities=("oob",),
            ),
        )
        with self.assertRaises(QMPIdentityError):
            compose_qmp_identity_evidence(
                observation_id=uuid4(),
                vm_id=_VM_ID,
                first=first,
                second=changed,
            )

        replayed_ids = self._sample(
            command_id=2,
            monotonic_ns=first_time + MIN_QMP_STABILIZATION_NS,
        )
        with self.assertRaises(QMPIdentityError):
            compose_qmp_identity_evidence(
                observation_id=uuid4(),
                vm_id=_VM_ID,
                first=first,
                second=replayed_ids,
            )


if __name__ == "__main__":
    unittest.main()
