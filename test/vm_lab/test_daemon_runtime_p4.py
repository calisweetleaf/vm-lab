"""Physical P4 composition and public-membrane proofs.

The empty-registry proof starts the real daemon entry point, exchanges
canonical frames over its Unix socket, and inspects the resulting SQLite
registry while both configured executables are absent.  The runtime-owner
membrane proof uses real ``qemu-img`` storage materialization and the actual
registry service against an on-disk database, but configures no QEMU system
binary and makes no process, QMP, or running-VM claim.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol import (  # noqa: E402
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
    ErrorCode,
    ErrorEnvelope,
    TransitionEvidence,
    VMRecord,
    VMState,
)
from somnus_vm.client import UnixControlClient  # noqa: E402
from somnus_vm.config import load_configuration  # noqa: E402
from somnus_vm.daemon_runtime import (  # noqa: E402
    DaemonRuntimeContext,
    DaemonRuntimeHooks,
    run as run_daemon_runtime,
)
from somnus_vm.host.images import ImageManifest, ToolVersion  # noqa: E402
from somnus_vm.host.launch_authority import (  # noqa: E402
    DisposableLaunchReceipt,
    ProductionExclusion,
)
from somnus_vm.host.qemu import bind_block_fds, validate_fd_bound_execution  # noqa: E402
from somnus_vm.host.qemu_process import inspect_executable  # noqa: E402
from somnus_vm.host.qemu_runtime import (  # noqa: E402
    QemuRuntimeError,
    QemuRuntimeOwner,
)
from somnus_vm.host.qemu_runtime_journal import (  # noqa: E402
    RuntimeLaunchIntent,
    RuntimeLaunchJournal,
)
from somnus_vm.host.registry import RegistryMode, SQLiteRegistry  # noqa: E402
from somnus_vm.host.service import RegistryMutationService  # noqa: E402
from somnus_vm.transport import PeerCredentials, TransportError  # noqa: E402


_TIMEOUT_SECONDS = 20.0


def _runtime_hook_worker(config_path: Path, evidence_path: Path) -> int:
    """Run the real daemon composition with one Python-only activation."""

    def activation(context: DaemonRuntimeContext) -> None:
        evidence_path.write_text(
            json.dumps(
                {
                    "active_vm_ids": sorted(
                        str(item) for item in context.runtime.live_vm_ids
                    ),
                    "completed_reconciliation": [
                        item.disposition
                        for item in context.completed_reconciliation
                    ],
                    "incomplete_recovery": [
                        item.disposition
                        for item in context.incomplete_recovery
                    ],
                    "registry_mode": context.registry.status.mode.value,
                    "same_configuration": (
                        context.configuration
                        == load_configuration(config_path)
                    ),
                    "service_type": type(context.service).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

    return run_daemon_runtime(
        config_path,
        hooks=DaemonRuntimeHooks(activation=activation),
    )


def _configuration_file(
    root: Path,
    *,
    qemu_img_binary: Path | None = None,
    qemu_system_binary: Path | None = None,
) -> Path:
    selected_qemu_img = qemu_img_binary or (
        root / "qemu-img-does-not-exist"
    )
    path = root / "daemon.toml"
    path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "p4-daemon-runtime"',
                "",
                "[host]",
                f'qemu_binary = "{qemu_system_binary or (root / "qemu-does-not-exist")}"',
                f'qemu_img_binary = "{selected_qemu_img}"',
                "enable_kvm = false",
                "",
                "[daemon]",
                f'runtime_root = "{root / "runtime"}"',
                f'control_socket = "{root / "runtime" / "control.sock"}"',
                "request_timeout_seconds = 5.0",
                "",
                "[storage]",
                f'state_root = "{root / "state"}"',
                f'image_root = "{root / "images"}"',
                f'base_image = "{root / "images" / "base.qcow2"}"',
                f'backup_root = "{root / "backups"}"',
                f'log_root = "{root / "logs"}"',
                "",
                "[network]",
                'bind_host = "127.0.0.1"',
                "ssh_ports = [46000, 46063]",
                "agent_ports = [46100, 46163]",
                "vnc_ports = [6300, 6363]",
                "",
                "[agent]",
                "timeout_seconds = 5.0",
                "",
                "[security]",
                'credential_provider = "file"',
                f'secret_root = "{root / "secrets"}"',
                f'guest_agent_token_file = "{root / "secrets" / "agent.token"}"',
                "",
                "[policy]",
                "max_active_vms = 1",
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


def _request(
    operation: str,
    payload: dict[str, object] | None = None,
    *,
    vm_id: UUID | None = None,
    generation: int | None = None,
) -> ControlRequest:
    request = ControlRequest(
        schema_version=CURRENT_SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=uuid4(),
        correlation_id=uuid4(),
        vm_id=vm_id,
        boot_id=None,
        generation=generation,
        timestamp=datetime.now(timezone.utc),
        operation=operation,
        payload=payload or {},
    )
    # Exercise the canonical wire representation even when the actual service
    # is called directly for a deterministic mutation-boundary proof.
    return ControlRequest.from_json(request.to_json())


def _transition_evidence(
    record: VMRecord,
    facts: dict[str, str],
    *,
    boot_id: UUID | None = None,
) -> TransitionEvidence:
    return TransitionEvidence(
        evidence_id=uuid4(),
        observed_at=record.updated_at + timedelta(seconds=1),
        facts={"intent_digest": record.intent_digest, **facts},
        vm_id=record.vm_id,
        generation=record.generation,
        boot_id=boot_id,
        intent_digest=record.intent_digest,
    )


def _booting_intent(record: VMRecord, image_sha256: str) -> VMRecord:
    """Advance only through intent states; make no process or QMP claim."""

    selected = record.transition(
        VMState.PROVISIONING,
        _transition_evidence(
            record,
            {"operation_id": str(uuid4())},
        ),
    )
    selected = selected.transition(
        VMState.PROVISIONED,
        _transition_evidence(
            selected,
            {
                "image_sha256": image_sha256,
                "operation_id": str(uuid4()),
                "storage_id": str(uuid4()),
            },
        ),
    )
    boot_id = uuid4()
    return selected.transition(
        VMState.BOOTING,
        _transition_evidence(
            selected,
            {
                "boot_id": str(boot_id),
                "operation_id": str(uuid4()),
            },
            boot_id=boot_id,
        ),
        boot_id=boot_id,
    )


def _provisioned_intent(record: VMRecord, image_sha256: str) -> VMRecord:
    """Advance through the two storage-intent states without starting QEMU."""

    selected = record.transition(
        VMState.PROVISIONING,
        _transition_evidence(
            record,
            {"operation_id": str(uuid4())},
        ),
    )
    return selected.transition(
        VMState.PROVISIONED,
        _transition_evidence(
            selected,
            {
                "image_sha256": image_sha256,
                "operation_id": str(uuid4()),
                "storage_id": str(uuid4()),
            },
        ),
    )


def _create_base_manifest(
    root: Path,
    qemu_img: Path,
) -> tuple[Path, ImageManifest]:
    source = root / "source-base.qcow2"
    subprocess.run(
        [
            os.fspath(qemu_img),
            "create",
            "-f",
            "qcow2",
            os.fspath(source),
            "64M",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(
        subprocess.run(
            [
                os.fspath(qemu_img),
                "info",
                "--output=json",
                os.fspath(source),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    qemu_img_version = subprocess.run(
        [os.fspath(qemu_img), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[0]
    source_bytes = source.read_bytes()
    manifest = ImageManifest(
        schema_version=1,
        image_id=uuid4(),
        origin="local-vm-lab-p4-membrane-proof",
        sha256=hashlib.sha256(source_bytes).hexdigest(),
        size_bytes=len(source_bytes),
        format="qcow2",
        virtual_size_bytes=int(info["virtual-size"]),
        backing_chain=(),
        architecture="x86_64",
        os_family="ubuntu",
        os_release="24.04",
        os_variant="test",
        created_at=(
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ),
        tool_versions=(
            ToolVersion(
                name="qemu_img_creator",
                version=qemu_img_version,
            ),
        ),
        guest_payload_manifest=(),
    )
    return source, manifest


def _lease_rows(registry_path: Path, vm_id: UUID) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(
        f"file:{registry_path}?mode=ro",
        uri=True,
    )
    try:
        return tuple(
            connection.execute(
                "SELECT lease_id,vm_id,generation,role,host,port,active,"
                "operation_id,released_by_operation_id "
                "FROM port_leases WHERE vm_id=? ORDER BY role",
                (str(vm_id),),
            ).fetchall()
        )
    finally:
        connection.close()


class DaemonRuntimeP4Gate(unittest.TestCase):
    def _start(
        self,
        config_path: Path,
    ) -> tuple[subprocess.Popen[str], UnixControlClient, ControlResponse]:
        configuration = load_configuration(config_path)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "somnus_vm.daemon_runtime",
                "--config",
                os.fspath(config_path),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        client = UnixControlClient(
            configuration.daemon.control_socket,
            timeout_seconds=2.0,
            allowed_server_uids={os.geteuid()},
        )
        deadline = time.monotonic() + _TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                response = client.request(_request("registry.status"))
            except TransportError:
                time.sleep(0.02)
                continue
            if isinstance(response, ControlResponse):
                return process, client, response
            self.fail(f"registry.status failed during startup: {response!r}")
        process.kill()
        stdout, stderr = process.communicate(timeout=5.0)
        self.fail(
            "P4 daemon did not serve after empty-registry reconciliation"
            f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )

    def _stop(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5.0)
            self.fail(
                "P4 daemon did not complete ordered shutdown"
                f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn("Traceback", stderr)
        return stdout, stderr

    def test_empty_registry_is_lazy_and_public_lifecycle_remains_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-daemon-runtime-"
        ) as raw:
            root = Path(raw).resolve()
            config_path = _configuration_file(root)
            configuration = load_configuration(config_path)
            missing_qemu = Path(configuration.host.qemu_binary)
            missing_qemu_img = Path(configuration.host.qemu_img_binary)
            self.assertFalse(missing_qemu.exists())
            self.assertFalse(missing_qemu_img.exists())

            process, client, status = self._start(config_path)
            try:
                self.assertEqual(status.result["mode"], "read_write")
                self.assertTrue(
                    configuration.daemon.control_socket.is_socket()
                )

                rejected = client.request(_request("vm.start"))
                self.assertIsInstance(rejected, ErrorEnvelope)
                assert isinstance(rejected, ErrorEnvelope)
                self.assertIs(rejected.code, ErrorCode.OWNERSHIP_REJECTED)

                children_path = (
                    Path("/proc")
                    / str(process.pid)
                    / "task"
                    / str(process.pid)
                    / "children"
                )
                self.assertEqual(children_path.read_text().strip(), "")
                self.assertFalse(missing_qemu.exists())
                self.assertFalse(missing_qemu_img.exists())
            finally:
                self._stop(process)

            self.assertFalse(configuration.daemon.control_socket.exists())
            self.assertFalse(missing_qemu.exists())
            self.assertFalse(missing_qemu_img.exists())
            with SQLiteRegistry.open(
                configuration.storage.state_root / "registry.sqlite3",
                writer=False,
            ) as registry:
                self.assertIs(registry.status.mode, RegistryMode.READ_ONLY)
                self.assertEqual(registry.list_operations(), ())
                self.assertEqual(registry.list_vms(active_only=False), ())
                self.assertEqual(
                    registry.list_base_images(active_only=False),
                    (),
                )
                self.assertEqual(registry.list_disks(active_only=False), ())

    def test_python_only_activation_runs_inside_the_real_daemon_owner(
        self,
    ) -> None:
        """The physical-gate seam is composed but absent from public control."""

        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-daemon-hook-"
        ) as raw:
            root = Path(raw).resolve()
            config_path = _configuration_file(root)
            configuration = load_configuration(config_path)
            evidence_path = root / "activation.json"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            process = subprocess.Popen(
                [
                    sys.executable,
                    os.fspath(Path(__file__).resolve()),
                    "--runtime-hook-worker",
                    os.fspath(config_path),
                    os.fspath(evidence_path),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            client = UnixControlClient(
                configuration.daemon.control_socket,
                timeout_seconds=2.0,
                allowed_server_uids={os.geteuid()},
            )
            try:
                deadline = time.monotonic() + _TIMEOUT_SECONDS
                status: ControlResponse | None = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    if not evidence_path.is_file():
                        time.sleep(0.02)
                        continue
                    try:
                        candidate = client.request(
                            _request("registry.status")
                        )
                    except TransportError:
                        time.sleep(0.02)
                        continue
                    self.assertIsInstance(candidate, ControlResponse)
                    assert isinstance(candidate, ControlResponse)
                    status = candidate
                    break
                if status is None:
                    stdout, stderr = process.communicate(timeout=5.0)
                    self.fail(
                        "hooked daemon did not reach the authenticated listener"
                        f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
                    )
                self.assertEqual(status.result["mode"], "read_write")
                evidence = json.loads(
                    evidence_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    evidence,
                    {
                        "active_vm_ids": [],
                        "completed_reconciliation": [],
                        "incomplete_recovery": [],
                        "registry_mode": "read_write",
                        "same_configuration": True,
                        "service_type": "RegistryMutationService",
                    },
                )
                # The installed daemon parser has no hook or launch option.
                rejected = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "somnus_vm.daemon_runtime",
                        "--config",
                        os.fspath(config_path),
                        "--launch-vm",
                        str(uuid4()),
                    ],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("unrecognized arguments", rejected.stderr)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(
                        timeout=_TIMEOUT_SECONDS
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5.0)
                    self.fail(
                        "hooked daemon did not stop"
                        f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
                    )
            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn("Traceback", stderr)

    def test_runtime_owner_consumes_exact_p3_descriptors_before_spawn(
        self,
    ) -> None:
        qemu_img_value = shutil.which("qemu-img")
        qemu_system_value = shutil.which("qemu-system-x86_64")
        self.assertIsNotNone(
            qemu_img_value,
            "descriptor-bound P4 proof requires the real qemu-img owner",
        )
        self.assertIsNotNone(
            qemu_system_value,
            "descriptor-bound P4 proof requires the selected QEMU executable",
        )
        assert qemu_img_value is not None
        assert qemu_system_value is not None
        qemu_img = Path(qemu_img_value).resolve(strict=True)
        qemu_system = Path(qemu_system_value).resolve(strict=True)

        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-storage-bind-"
        ) as raw:
            root = Path(raw).resolve()
            configuration = load_configuration(
                _configuration_file(
                    root,
                    qemu_img_binary=qemu_img,
                    qemu_system_binary=qemu_system,
                )
            )
            source, manifest = _create_base_manifest(root, qemu_img)
            peer = PeerCredentials(
                pid=os.getpid(),
                uid=os.geteuid(),
                gid=os.getegid(),
            )
            registry_path = (
                configuration.storage.state_root / "registry.sqlite3"
            )
            with SQLiteRegistry.open(registry_path) as registry:
                service = RegistryMutationService(registry, configuration.host)
                imported = service(
                    _request(
                        "storage.import_base",
                        {
                            "manifest": manifest.to_dict(),
                            "source_path": os.fspath(source),
                        },
                    ),
                    peer,
                )
                self.assertIsInstance(imported, ControlResponse)
                assert isinstance(imported, ControlResponse)
                declared = service(
                    _request(
                        "registry.declare",
                        {
                            "memory_mib": 4_096,
                            "name": "descriptor-bound-aipc",
                            "vcpus": 2,
                        },
                    ),
                    peer,
                )
                self.assertIsInstance(declared, ControlResponse)
                assert isinstance(declared, ControlResponse)
                vm_id = UUID(str(declared.result["vm_id"]))
                generation = int(declared.result["generation"])
                revision = int(declared.result["revision"])
                leased = service(
                    _request(
                        "registry.lease",
                        {"expected_revision": revision},
                        vm_id=vm_id,
                        generation=generation,
                    ),
                    peer,
                )
                self.assertIsInstance(leased, ControlResponse)
                materialized = service(
                    _request(
                        "storage.create_overlay",
                        {
                            "base_image_id": str(
                                imported.result["registry_image_id"]
                            ),
                            "expected_revision": revision,
                            "virtual_size_bytes": 100 * 1024**3,
                        },
                        vm_id=vm_id,
                        generation=generation,
                    ),
                    peer,
                )
                self.assertIsInstance(materialized, ControlResponse)

                declared_owner = registry.get_vm(vm_id)
                self.assertIsNotNone(declared_owner)
                assert declared_owner is not None
                provisioned = _provisioned_intent(
                    declared_owner.record,
                    manifest.sha256,
                )
                with registry.write_transaction() as transaction:
                    transaction.compare_and_swap_vm(
                        vm_id,
                        declared_owner.revision,
                        provisioned,
                    )

                owner = QemuRuntimeOwner(registry, configuration.host)
                before = registry.get_vm(vm_id)
                with self.assertRaises(QemuRuntimeError):
                    owner.launch(vm_id, permit=None)  # type: ignore[arg-type]
                self.assertEqual(registry.get_vm(vm_id), before)
                record, _revision, disk, plan = owner._select_authority(vm_id)
                storage = owner._pin_runtime_storage(disk)
                try:
                    self.assertEqual(
                        fcntl.fcntl(
                            storage.overlay.descriptor,
                            fcntl.F_GETFL,
                        )
                        & os.O_ACCMODE,
                        os.O_RDWR,
                    )
                    self.assertEqual(
                        fcntl.fcntl(storage.base.descriptor, fcntl.F_GETFL)
                        & os.O_ACCMODE,
                        os.O_RDONLY,
                    )
                    self.assertEqual(
                        fcntl.fcntl(storage.marker.descriptor, fcntl.F_GETFL)
                        & os.O_ACCMODE,
                        os.O_RDONLY,
                    )
                    executed_argv = bind_block_fds(
                        plan,
                        overlay_fd=storage.overlay.descriptor,
                        base_fd=storage.base.descriptor,
                    )
                    validate_fd_bound_execution(plan.argv, executed_argv)
                    intent = RuntimeLaunchIntent.from_runtime(
                        record,
                        disk,
                        plan,
                        inspect_executable(os.fspath(qemu_system)),
                        executed_argv=executed_argv,
                        launch_authority=DisposableLaunchReceipt(
                            permit_id=uuid4(),
                            run_id=uuid4(),
                            vm_id=record.vm_id,
                            generation=record.generation,
                            disk_id=disk.disk_id,
                            disk_path=disk.path,
                            base_sha256=disk.base_image_sha256,
                            fixture_root=os.fspath(root),
                            fixture_device_id=root.stat().st_dev,
                            fixture_inode=root.stat().st_ino,
                            marker_sha256="5" * 64,
                            production_exclusions=(
                                ProductionExclusion(
                                    label="daemon-production-exclusion",
                                    path=root / "production-aipc.qcow2",
                                    device_id=root.stat().st_dev,
                                    inode=root.stat().st_ino + 1,
                                ),
                            ),
                        ),
                    )
                    journal = RuntimeLaunchJournal(intent)
                    journal, _ = journal.advance("intent_persisted")
                    runtime_details = (
                        configuration.daemon.runtime_root.parent
                    )
                    runtime_details.mkdir(
                        mode=0o700,
                        parents=True,
                        exist_ok=True,
                    )
                    configuration.daemon.runtime_root.mkdir(
                        mode=0o700,
                        parents=True,
                    )
                    configuration.storage.log_root.mkdir(
                        mode=0o700,
                        parents=True,
                    )
                    runtime_stat = configuration.daemon.runtime_root.stat(
                        follow_symlinks=False
                    )
                    log_stat = configuration.storage.log_root.stat(
                        follow_symlinks=False
                    )
                    journal, _ = journal.advance(
                        "private_paths_prepared",
                        {
                            "owner_uid": os.geteuid(),
                            "runtime_root_device_id": runtime_stat.st_dev,
                            "runtime_root_inode": runtime_stat.st_ino,
                            "runtime_root_mode": 0o700,
                            "log_root_device_id": log_stat.st_dev,
                            "log_root_inode": log_stat.st_ino,
                            "log_root_mode": 0o700,
                        },
                    )
                    journal, _ = journal.advance(
                        "executable_pinned",
                        {
                            "executable": intent.executable.to_dict(),
                            "storage": {
                                "overlay": storage.overlay.to_checkpoint_dict(),
                                "base": storage.base.to_checkpoint_dict(),
                                "owner_marker": (
                                    storage.marker.to_checkpoint_dict()
                                ),
                            },
                        },
                    )
                    pinned = journal.pinned_storage_authority()
                    self.assertEqual(
                        tuple(int(item["descriptor"]) for item in pinned),
                        storage.descriptors,
                    )
                    self.assertEqual(
                        tuple(
                            str(item["role"])
                            for item in pinned
                        ),
                        ("overlay", "base", "owner-marker"),
                    )
                finally:
                    storage.close()

    def test_booting_runtime_owner_rejects_public_cancel_atomically(
        self,
    ) -> None:
        qemu_img_value = shutil.which("qemu-img")
        self.assertIsNotNone(
            qemu_img_value,
            "physical materialized-disk proof requires qemu-img",
        )
        assert qemu_img_value is not None
        qemu_img = Path(qemu_img_value).resolve(strict=True)

        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-runtime-membrane-"
        ) as raw:
            root = Path(raw).resolve()
            config_path = _configuration_file(
                root,
                qemu_img_binary=qemu_img,
            )
            configuration = load_configuration(config_path)
            missing_qemu = Path(configuration.host.qemu_binary)
            source, manifest = _create_base_manifest(root, qemu_img)
            peer = PeerCredentials(
                pid=os.getpid(),
                uid=os.geteuid(),
                gid=os.getegid(),
            )
            registry_path = (
                configuration.storage.state_root / "registry.sqlite3"
            )

            with SQLiteRegistry.open(registry_path) as registry:
                service = RegistryMutationService(
                    registry,
                    configuration.host,
                )

                imported = service(
                    _request(
                        "storage.import_base",
                        {
                            "manifest": manifest.to_dict(),
                            "source_path": os.fspath(source),
                        },
                    ),
                    peer,
                )
                self.assertIsInstance(imported, ControlResponse)
                assert isinstance(imported, ControlResponse)

                declared = service(
                    _request(
                        "registry.declare",
                        {
                            "memory_mib": 4_096,
                            "name": "runtime-owned-aipc",
                            "vcpus": 2,
                        },
                    ),
                    peer,
                )
                self.assertIsInstance(declared, ControlResponse)
                assert isinstance(declared, ControlResponse)
                vm_id = UUID(str(declared.result["vm_id"]))
                generation = int(declared.result["generation"])
                declared_revision = int(declared.result["revision"])

                leased = service(
                    _request(
                        "registry.lease",
                        {"expected_revision": declared_revision},
                        vm_id=vm_id,
                        generation=generation,
                    ),
                    peer,
                )
                self.assertIsInstance(leased, ControlResponse)

                materialized = service(
                    _request(
                        "storage.create_overlay",
                        {
                            "base_image_id": str(
                                imported.result["registry_image_id"]
                            ),
                            "expected_revision": declared_revision,
                            "virtual_size_bytes": 100 * 1024**3,
                        },
                        vm_id=vm_id,
                        generation=generation,
                    ),
                    peer,
                )
                self.assertIsInstance(materialized, ControlResponse)
                assert isinstance(materialized, ControlResponse)

                declared_owner = registry.get_vm(vm_id)
                self.assertIsNotNone(declared_owner)
                assert declared_owner is not None
                booting = _booting_intent(
                    declared_owner.record,
                    manifest.sha256,
                )
                with registry.write_transaction() as transaction:
                    runtime_owner = transaction.compare_and_swap_vm(
                        vm_id,
                        declared_owner.revision,
                        booting,
                    )

                # BOOTING is a sealed launch intent, not fabricated runtime
                # observation: no process or QMP truth is attached.
                self.assertIs(runtime_owner.record.state, VMState.BOOTING)
                self.assertIsNone(runtime_owner.record.process)
                self.assertIsNotNone(runtime_owner.record.boot_id)

                before_vm = registry.get_vm(vm_id)
                before_disks = registry.list_disks(
                    vm_id=vm_id,
                    active_only=False,
                )
                before_operations = registry.list_operations()
                before_leases = _lease_rows(registry_path, vm_id)
                self.assertEqual(len(before_disks), 1)
                self.assertTrue(before_disks[0].active)
                self.assertTrue(before_disks[0].materialized)
                self.assertEqual(len(before_leases), 3)
                self.assertTrue(all(row[6] == 1 for row in before_leases))

                disk_path = Path(before_disks[0].path)
                marker_path = Path(str(materialized.result["marker_path"]))
                before_disk_identity = (
                    disk_path.stat(follow_symlinks=False),
                    hashlib.sha256(disk_path.read_bytes()).hexdigest(),
                )
                before_marker_identity = (
                    marker_path.stat(follow_symlinks=False),
                    hashlib.sha256(marker_path.read_bytes()).hexdigest(),
                )

                cancel_request = _request(
                    "registry.cancel",
                    {"expected_revision": runtime_owner.revision},
                    vm_id=vm_id,
                    generation=generation,
                )
                rejected = service(cancel_request, peer)
                self.assertIsInstance(rejected, ErrorEnvelope)
                assert isinstance(rejected, ErrorEnvelope)
                self.assertIs(rejected.code, ErrorCode.OWNERSHIP_REJECTED)

                self.assertEqual(registry.get_vm(vm_id), before_vm)
                self.assertEqual(
                    registry.list_disks(
                        vm_id=vm_id,
                        active_only=False,
                    ),
                    before_disks,
                )
                self.assertEqual(
                    _lease_rows(registry_path, vm_id),
                    before_leases,
                )
                self.assertEqual(
                    registry.list_operations(),
                    before_operations,
                )
                self.assertFalse(
                    any(
                        operation.kind == "registry.cancel"
                        for operation in registry.list_operations()
                    )
                )
                self.assertIsNone(
                    registry.find_idempotency(str(cancel_request.request_id))
                )
                self.assertIsNotNone(rejected.operation_id)
                assert rejected.operation_id is not None
                self.assertIsNone(
                    registry.get_operation(rejected.operation_id)
                )

                after_disk = disk_path.stat(follow_symlinks=False)
                after_marker = marker_path.stat(follow_symlinks=False)
                self.assertEqual(
                    (
                        after_disk.st_dev,
                        after_disk.st_ino,
                        after_disk.st_size,
                        hashlib.sha256(disk_path.read_bytes()).hexdigest(),
                    ),
                    (
                        before_disk_identity[0].st_dev,
                        before_disk_identity[0].st_ino,
                        before_disk_identity[0].st_size,
                        before_disk_identity[1],
                    ),
                )
                self.assertEqual(
                    (
                        after_marker.st_dev,
                        after_marker.st_ino,
                        after_marker.st_size,
                        hashlib.sha256(marker_path.read_bytes()).hexdigest(),
                    ),
                    (
                        before_marker_identity[0].st_dev,
                        before_marker_identity[0].st_ino,
                        before_marker_identity[0].st_size,
                        before_marker_identity[1],
                    ),
                )

            self.assertFalse(missing_qemu.exists())


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--runtime-hook-worker":
        raise SystemExit(
            _runtime_hook_worker(
                Path(sys.argv[2]).resolve(),
                Path(sys.argv[3]).resolve(),
            )
        )
    unittest.main()
