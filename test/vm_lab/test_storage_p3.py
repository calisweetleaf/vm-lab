"""Physical P3 storage proof against the real qemu-img boundary.

This module exercises only disposable, test-owned roots under ``/tmp``.  It
imports the already verified Ubuntu fixture through the daemon service, creates
real sparse qcow2 overlays, and terminates real child writer processes after
committed storage checkpoints.  It never launches QEMU, mounts an image, or
touches an operator AIPC disk.

No mock, stub, skip, fallback pass, or synthetic command result establishes
success.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = Path(__file__).resolve().parent
FIXTURE_SOURCE = Path("/tmp/vm-lab-p3-fixture-20260801.qcow2")
FIXTURE_MANIFEST = (
    PROJECT_ROOT
    / "test"
    / "vm_lab"
    / "fixtures"
    / "images"
    / "ubuntu-minimal-noble-amd64-20260801.json"
)
QEMU_IMG = Path("/usr/bin/qemu-img")
VIRTUAL_SIZE = 100 * 1024**3
CRASH_EXIT = 73

BASE_CHECKPOINTS = (
    "intent_persisted",
    "base_stage_prepared",
    "base_bytes_staged",
    "base_verified",
    "base_published",
    "base_registered",
    "completion_observed",
)
OVERLAY_CHECKPOINTS = (
    "intent_persisted",
    "overlay_stage_prepared",
    "overlay_created",
    "overlay_verified",
    "overlay_published",
    "disk_materialized",
    "completion_observed",
)

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol import (  # noqa: E402
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
    ErrorCode,
    ErrorEnvelope,
)
from somnus_vm.config import (  # noqa: E402
    AgentSettings,
    DaemonSettings,
    HostConfiguration,
    NetworkSettings,
    PolicySettings,
    PortRange,
    SecuritySettings,
    StorageSettings,
)
from somnus_vm.host.images import (  # noqa: E402
    ImageIntegrityError,
    ImageManifest,
    ImageManifestError,
    ImageOwnershipError,
    QemuImgBackend,
    QemuImgError,
)
from somnus_vm.host.registry import (  # noqa: E402
    PREVIOUS_REGISTRY_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    RegistryMode,
    SQLiteRegistry,
)
from somnus_vm.host.service import RegistryMutationService  # noqa: E402
from somnus_vm.host.storage import ImageStore  # noqa: E402
from somnus_vm.transport import PeerCredentials  # noqa: E402


def _configuration(root: Path) -> HostConfiguration:
    """Compose one isolated storage authority with no runnable QEMU binary."""

    root = root.resolve()
    return HostConfiguration(
        qemu_binary="qemu-system-must-not-run-in-p3",
        qemu_img_binary="qemu-img",
        enable_kvm=False,
        daemon=DaemonSettings(
            runtime_root=root / "run",
            control_socket=root / "run" / "control.sock",
            request_timeout_seconds=30.0,
        ),
        storage=StorageSettings(
            state_root=root / "state",
            image_root=root / "images",
            base_image=root / "images" / "base.qcow2",
            backup_root=root / "backup",
            log_root=root / "logs",
        ),
        network=NetworkSettings(
            bind_host="127.0.0.1",
            ssh_ports=PortRange(22_000, 22_063),
            agent_ports=PortRange(23_000, 23_063),
            vnc_ports=PortRange(5_900, 5_963),
        ),
        agent=AgentSettings(timeout_seconds=30.0),
        security=SecuritySettings(
            credential_provider="file",
            secret_root=root / "secrets",
            guest_agent_token_file=root / "secrets" / "agent.token",
        ),
        policy=PolicySettings(max_active_vms=1),
    )


def _manifest() -> ImageManifest:
    return ImageManifest.from_json(FIXTURE_MANIFEST.read_bytes())


def _peer() -> PeerCredentials:
    return PeerCredentials(
        pid=os.getpid(),
        uid=os.geteuid(),
        gid=os.getegid(),
    )


def _request(
    operation: str,
    payload: dict[str, object],
    *,
    vm_id: UUID | None = None,
    generation: int | None = None,
) -> ControlRequest:
    return ControlRequest(
        schema_version=CURRENT_SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=uuid4(),
        correlation_id=uuid4(),
        vm_id=vm_id,
        boot_id=None,
        generation=generation,
        timestamp=datetime.now(timezone.utc),
        operation=operation,
        payload=payload,
    )


def _success(
    response: ControlResponse | ErrorEnvelope,
) -> ControlResponse:
    if not isinstance(response, ControlResponse):
        raise AssertionError(
            f"expected success, received {response.to_dict()}"
        )
    return response


def _error(
    response: ControlResponse | ErrorEnvelope,
    code: ErrorCode,
) -> ErrorEnvelope:
    if not isinstance(response, ErrorEnvelope):
        raise AssertionError(
            f"expected {code.value}, received {response.to_dict()}"
        )
    if response.code is not code:
        raise AssertionError(
            f"expected {code.value}, received {response.code.value}"
        )
    return response


def _result(response: ControlResponse) -> dict[str, object]:
    value = json.loads(response.to_json())["result"]
    if not isinstance(value, dict):
        raise AssertionError("control result is not an object")
    return value


def _import_request(manifest: ImageManifest | None = None) -> ControlRequest:
    selected = _manifest() if manifest is None else manifest
    return _request(
        "storage.import_base",
        {
            "manifest": selected.to_dict(),
            "source_path": os.fspath(FIXTURE_SOURCE),
        },
    )


def _declare(
    service: RegistryMutationService,
    name: str,
) -> tuple[UUID, int, int]:
    response = _success(
        service(
            _request(
                "registry.declare",
                {"memory_mib": 4_096, "name": name, "vcpus": 2},
            ),
            _peer(),
        )
    )
    result = _result(response)
    return (
        UUID(str(result["vm_id"])),
        int(result["generation"]),
        int(result["revision"]),
    )


def _overlay_request(
    vm_id: UUID,
    generation: int,
    revision: int,
    base_image_id: UUID,
) -> ControlRequest:
    return _request(
        "storage.create_overlay",
        {
            "base_image_id": str(base_image_id),
            "expected_revision": revision,
            "virtual_size_bytes": VIRTUAL_SIZE,
        },
        vm_id=vm_id,
        generation=generation,
    )


def _import_base(
    service: RegistryMutationService,
    manifest: ImageManifest,
) -> tuple[ControlRequest, ControlResponse, UUID]:
    request = _import_request(manifest)
    response = _success(service(request, _peer()))
    image_id = UUID(str(_result(response)["registry_image_id"]))
    return request, response, image_id


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _source_identity() -> tuple[int, int, int, int, int, int, str]:
    details = FIXTURE_SOURCE.stat(follow_symlinks=False)
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_nlink,
        _mode(FIXTURE_SOURCE),
        _file_sha256(FIXTURE_SOURCE),
    )


def _children() -> tuple[int, ...]:
    path = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    if not path.is_file():
        return ()
    text = path.read_text(encoding="ascii").strip()
    return tuple(int(value) for value in text.split()) if text else ()


def _assert_private_roots(configuration: HostConfiguration) -> None:
    for path in (
        configuration.storage.state_root,
        configuration.storage.image_root,
    ):
        details = path.stat(follow_symlinks=False)
        if not stat.S_ISDIR(details.st_mode):
            raise AssertionError(f"storage root is not a directory: {path}")
        if stat.S_IMODE(details.st_mode) != 0o700:
            raise AssertionError(f"storage root is not private: {path}")
        if details.st_uid != os.geteuid():
            raise AssertionError(f"storage root has foreign owner: {path}")


def _assert_no_staging(root: Path) -> None:
    leftovers: list[Path] = []
    for directory in root.rglob(".staging"):
        leftovers.extend(path for path in directory.rglob("*"))
    if leftovers:
        raise AssertionError(f"storage staging was not settled: {leftovers}")


def _assert_no_runtime_materialization(root: Path) -> None:
    forbidden = tuple(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".log", ".pid", ".qmp"}
    )
    if forbidden:
        raise AssertionError(f"P3 created VM runtime artifacts: {forbidden}")


def _assert_marker(path: Path, disk: object) -> None:
    marker_path = Path(f"{path}.owner.json")
    if not marker_path.is_file() or _mode(marker_path) != 0o600:
        raise AssertionError("overlay marker is absent or not mode 0600")
    raw = marker_path.read_bytes()
    decoded = json.loads(raw)
    canonical = json.dumps(
        decoded,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if canonical != raw:
        raise AssertionError("overlay marker is not canonical JSON")
    if decoded["schema_version"] != 1:
        raise AssertionError("overlay marker schema is not version 1")
    expected = {
        "base_image_id": str(getattr(disk, "base_image_id")),
        "base_image_sha256": getattr(disk, "base_image_sha256"),
        "disk_id": str(getattr(disk, "disk_id")),
        "generation": getattr(disk, "generation"),
        "inode": getattr(disk, "inode"),
        "ownership_token": str(getattr(disk, "ownership_token")),
        "path": getattr(disk, "path"),
        "virtual_size_bytes": getattr(disk, "virtual_size_bytes"),
        "vm_id": str(getattr(disk, "vm_id")),
    }
    for key, value in expected.items():
        if decoded.get(key) != value:
            raise AssertionError(f"marker {key} disagrees with the registry")
    if hashlib.sha256(raw).hexdigest() != getattr(disk, "marker_sha256"):
        raise AssertionError("marker hash disagrees with the registry")


def _assert_overlay(
    backend: QemuImgBackend,
    configuration: HostConfiguration,
    manifest: ImageManifest,
    disk: object,
) -> None:
    path = Path(str(getattr(disk, "path")))
    if not path.is_file() or _mode(path) != 0o600:
        raise AssertionError("overlay is absent or not mode 0600")
    details = path.stat(follow_symlinks=False)
    if (
        not getattr(disk, "materialized")
        or getattr(disk, "format") != "qcow2"
        or getattr(disk, "virtual_size_bytes") != VIRTUAL_SIZE
        or (details.st_dev, details.st_ino)
        != (getattr(disk, "device_id"), getattr(disk, "inode"))
        or getattr(disk, "allocated_size_bytes") >= VIRTUAL_SIZE // 4
    ):
        raise AssertionError("overlay physical identity disagrees with registry")
    chain = backend.verify_overlay(
        path,
        configuration.storage.base_image,
        manifest,
        virtual_size_bytes=VIRTUAL_SIZE,
    )
    if (
        len(chain) != 2
        or chain[0].filename != path
        or chain[0].backing_filename != configuration.storage.base_image
        or chain[1].filename != configuration.storage.base_image
        or chain[1].backing_filename is not None
    ):
        raise AssertionError("overlay backing chain is not exact")
    _assert_marker(path, disk)


def _assert_current_registry_with_p3_storage(
    database: Path,
    registry: SQLiteRegistry,
) -> None:
    if (
        registry.status.schema_version != REGISTRY_SCHEMA_VERSION
        or registry.status.mode is not RegistryMode.READ_WRITE
    ):
        raise AssertionError(
            "registry is not writable at the current schema version"
        )
    connection = sqlite3.connect(database)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if version != REGISTRY_SCHEMA_VERSION or quick != "ok" or foreign:
        raise AssertionError(
            "current registry with P3 storage integrity failed: "
            f"{version}, {quick}, {foreign}"
        )


def _raw_disk_materialized(database: Path, disk_id: UUID) -> bool:
    """Observe the persisted bit when the public reader rejects a path alias."""

    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT materialized FROM disks WHERE disk_id = ?",
            (str(disk_id),),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] not in (0, 1):
        raise AssertionError("disk materialization record is absent or invalid")
    return bool(row[0])


def _create_v2_registry(root: Path, database: Path) -> tuple[UUID, UUID]:
    """Create one real P2 registry and leave its exact version-2 disk shape."""

    configuration = _configuration(root)
    with SQLiteRegistry.open(database) as registry:
        service = RegistryMutationService(registry, configuration)
        vm_id, _, _ = _declare(service, "migration-owner")
        disk = registry.list_disks(vm_id=vm_id)[0]
        disk_id = disk.disk_id

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        observation_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM disk_runtime_observations"
            ).fetchone()[0]
        )
        if observation_count != 0:
            raise AssertionError(
                "exact v2 fixture cannot discard P4 runtime observations"
            )
        process_exit_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM process_exit_observations"
            ).fetchone()[0]
        )
        if process_exit_count != 0:
            raise AssertionError(
                "exact v2 fixture cannot discard P5 process exit observations"
            )
        for trigger in (
            "process_exit_observation_owner_insert",
            "process_exit_observation_retire_insert",
            "process_exit_observation_immutable_update",
            "process_exit_observation_immutable_delete",
            "process_identity_active_insert",
            "process_identity_retirement_guard",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        for index in (
            "process_identity_retirement_binding",
            "process_exit_per_identity",
            "process_exit_observation_order",
        ):
            connection.execute(f"DROP INDEX {index}")
        connection.execute("DROP TABLE process_exit_observations")
        for trigger in (
            "disk_runtime_observation_owner_insert",
            "disk_runtime_observation_monotonic_insert",
            "disk_runtime_observation_immutable_update",
            "disk_runtime_observation_immutable_delete",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP INDEX disk_runtime_observation_order")
        connection.execute("DROP TABLE disk_runtime_observations")
        for index in (
            "disk_identity_binding",
            "operation_identity_binding",
            "process_identity_binding",
        ):
            connection.execute(f"DROP INDEX {index}")
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
        connection.execute(
            f"PRAGMA user_version={PREVIOUS_REGISTRY_SCHEMA_VERSION}"
        )
        connection.execute("COMMIT")
        self_check = str(
            connection.execute("PRAGMA quick_check").fetchone()[0]
        )
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        remaining_p4 = connection.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE name IN (
                'process_exit_observations',
                'process_exit_per_identity',
                'process_exit_observation_order',
                'process_exit_observation_owner_insert',
                'process_exit_observation_retire_insert',
                'process_exit_observation_immutable_update',
                'process_exit_observation_immutable_delete',
                'process_identity_active_insert',
                'process_identity_retirement_guard',
                'process_identity_retirement_binding',
                'disk_runtime_observations',
                'disk_runtime_observation_order',
                'disk_runtime_observation_owner_insert',
                'disk_runtime_observation_monotonic_insert',
                'disk_runtime_observation_immutable_update',
                'disk_runtime_observation_immutable_delete',
                'disk_identity_binding',
                'operation_identity_binding',
                'process_identity_binding'
            )
            """
        ).fetchall()
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    if self_check != "ok" or foreign or remaining_p4:
        raise AssertionError(
            "constructed version-2 registry is invalid: "
            f"{self_check}, {foreign}, {remaining_p4}"
        )
    return vm_id, disk_id


def _crash_after_checkpoint(
    root: Path,
    database: Path,
    request: ControlRequest,
    checkpoint: str,
) -> subprocess.CompletedProcess[str]:
    request_path = root / "request.json"
    request_path.write_text(request.to_json(), encoding="utf-8")
    os.chmod(request_path, 0o600)
    child = r"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

from test_storage_p3 import _configuration
from somnus_protocol import ControlRequest
from somnus_vm.host.registry import SQLiteRegistry
from somnus_vm.host.service import RegistryMutationService
from somnus_vm.host.storage import ImageStore
from somnus_vm.transport import PeerCredentials

root = Path(sys.argv[3])
database = Path(sys.argv[4])
request = ControlRequest.from_dict(
    json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
)
target = sys.argv[6]

def terminate_after_commit(operation_id, checkpoint):
    if checkpoint == target:
        os._exit(73)

configuration = _configuration(root)
with SQLiteRegistry.open(database) as registry:
    service = RegistryMutationService(
        registry,
        configuration,
        checkpoint_observer=terminate_after_commit,
        image_store=ImageStore(configuration),
    )
    response = service(
        request,
        PeerCredentials(os.getpid(), os.geteuid(), os.getegid()),
    )
    print(response.to_json(), file=sys.stderr, flush=True)
raise SystemExit(91)
"""
    environment = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": os.fspath(SOURCE_ROOT),
    }
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-c",
            child,
            str(SOURCE_ROOT),
            str(TEST_ROOT),
            str(root),
            str(database),
            str(request_path),
            checkpoint,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _recover(
    root: Path,
    database: Path,
    request: ControlRequest,
    expected: tuple[str, ...],
    checkpoint_index: int,
) -> tuple[SQLiteRegistry, RegistryMutationService, object]:
    registry = SQLiteRegistry.open(database)
    configuration = _configuration(root)
    service = RegistryMutationService(
        registry,
        configuration,
        image_store=ImageStore(configuration),
    )
    idempotency = registry.find_idempotency(str(request.request_id))
    if idempotency is None:
        registry.close()
        raise AssertionError("storage idempotency row is absent after crash")
    operation = registry.get_operation(idempotency.operation_id)
    if operation is None:
        registry.close()
        raise AssertionError("storage operation row is absent after crash")
    names = tuple(
        item.name for item in registry.list_checkpoints(operation.operation_id)
    )
    if names != expected[: checkpoint_index + 1]:
        registry.close()
        raise AssertionError(
            f"wrong crash checkpoint prefix: {names!r}"
        )
    recovered = service.recover_incomplete_operations()
    terminal_before = checkpoint_index == len(expected) - 1
    if recovered != (0 if terminal_before else 1):
        registry.close()
        raise AssertionError(
            f"wrong recovery count at {expected[checkpoint_index]}: {recovered}"
        )
    completed = registry.get_operation(operation.operation_id)
    if completed is None or completed.status != "completed":
        registry.close()
        raise AssertionError("storage operation did not recover to completed")
    completed_names = tuple(
        item.name for item in registry.list_checkpoints(completed.operation_id)
    )
    if completed_names != expected:
        registry.close()
        raise AssertionError(
            f"storage recovery produced wrong checkpoints: {completed_names!r}"
        )
    replay = _success(service(request, _peer()))
    if replay.operation_id != completed.operation_id:
        registry.close()
        raise AssertionError("storage replay changed operation identity")
    if _result(replay) != json.loads(completed.result):
        registry.close()
        raise AssertionError("storage replay changed durable result bytes")
    for payload, digest in (
        (completed.intent, completed.intent_sha256),
        (completed.owned_resources, completed.owned_resources_sha256),
        (completed.result, completed.result_sha256),
    ):
        if hashlib.sha256(payload).hexdigest() != digest:
            registry.close()
            raise AssertionError("storage journal hash disagrees with bytes")
    return registry, service, completed


class StorageP3Tests(unittest.TestCase):
    def setUp(self) -> None:
        if not QEMU_IMG.is_file() or not os.access(QEMU_IMG, os.X_OK):
            raise AssertionError("/usr/bin/qemu-img is unavailable")
        if not FIXTURE_SOURCE.is_file() or not FIXTURE_MANIFEST.is_file():
            raise AssertionError("the required P3 fixture or manifest is absent")
        self.source_before = _source_identity()
        manifest = _manifest()
        if (
            manifest.sha256 != self.source_before[-1]
            or manifest.size_bytes != self.source_before[2]
        ):
            raise AssertionError("fixture bytes disagree with repository authority")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p3-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()
        if _source_identity() != self.source_before:
            raise AssertionError("the immutable source fixture identity changed")

    def test_manifest_and_real_backend_reject_ambiguous_authority(self) -> None:
        manifest = _manifest()
        backend = QemuImgBackend("qemu-img")
        self.assertEqual(backend.binary, QEMU_IMG)
        self.assertTrue(backend.version.startswith("qemu-img version 8.2.2"))
        chain = backend.verify_base(FIXTURE_SOURCE, manifest)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].filename, FIXTURE_SOURCE)
        self.assertIsNone(chain[0].backing_filename)

        duplicate = FIXTURE_MANIFEST.read_bytes().replace(
            b'{"architecture":"x86_64",',
            (
                b'{"architecture":"x86_64",'
                b'"architecture":"x86_64",'
            ),
            1,
        )
        with self.assertRaises(ImageManifestError):
            ImageManifest.from_json(duplicate)

        for key, value in (
            ("unexpected", "rejected"),
            ("format", "raw"),
            ("sha256", manifest.sha256.upper()),
            ("size_bytes", True),
            ("virtual_size_bytes", 0),
        ):
            hostile = manifest.to_dict()
            hostile[key] = value
            with self.subTest(field=key):
                with self.assertRaises(ImageManifestError):
                    ImageManifest.from_dict(hostile)

        wrong = manifest.to_dict()
        wrong["sha256"] = "0" * 64
        wrong_manifest = ImageManifest.from_dict(wrong)
        with self.assertRaises(ImageIntegrityError):
            backend.verify_base(FIXTURE_SOURCE, wrong_manifest)

        absolute_backend = QemuImgBackend(os.fspath(QEMU_IMG))
        self.assertEqual(absolute_backend.binary, backend.binary)
        self.assertEqual(absolute_backend.version, backend.version)
        with self.assertRaises(QemuImgError):
            QemuImgBackend("./qemu-img")
        binary_link = self.root / "qemu-img-link"
        binary_link.symlink_to(QEMU_IMG)
        with self.assertRaises(QemuImgError):
            QemuImgBackend(os.fspath(binary_link))
        binary_link.unlink()
        with self.assertRaises(QemuImgError):
            QemuImgBackend("qemu-img-definitely-absent")

        symlink = self.root / "fixture-link.qcow2"
        symlink.symlink_to(FIXTURE_SOURCE)
        with self.assertRaises(ImageOwnershipError):
            backend.verify_base(symlink, manifest)
        symlink.unlink()

        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"
        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration),
            )
            extra = _manifest().to_dict()
            extra["unexpected"] = "rejected"
            _error(
                service(
                    _request(
                        "storage.import_base",
                        {
                            "manifest": extra,
                            "source_path": os.fspath(FIXTURE_SOURCE),
                        },
                    ),
                    _peer(),
                ),
                ErrorCode.VALIDATION_ERROR,
            )
            source_link = self.root / "source-link.qcow2"
            source_link.symlink_to(FIXTURE_SOURCE)
            _error(
                service(
                    _request(
                        "storage.import_base",
                        {
                            "manifest": manifest.to_dict(),
                            "source_path": os.fspath(source_link),
                        },
                    ),
                    _peer(),
                ),
                ErrorCode.VALIDATION_ERROR,
            )
            source_link.unlink()
            self.assertEqual(registry.list_base_images(), ())
            self.assertEqual(registry.list_operations(), ())
            _assert_current_registry_with_p3_storage(database, registry)

    def test_v2_registry_migrates_to_current_with_storage_constraints(
        self,
    ) -> None:
        database = self.root / "registry" / "legacy-v2.sqlite3"
        vm_id, disk_id = _create_v2_registry(self.root, database)

        with SQLiteRegistry.open(database) as migrated:
            _assert_current_registry_with_p3_storage(database, migrated)
            self.assertIsNotNone(migrated.status.migration_backup)
            assert migrated.status.migration_backup is not None
            backup = migrated.status.migration_backup
            self.assertTrue(backup.is_file())
            self.assertEqual(_mode(backup), 0o600)
            disk = migrated.get_disk(disk_id)
            self.assertIsNotNone(disk)
            assert disk is not None
            self.assertEqual(disk.vm_id, vm_id)
            self.assertFalse(disk.materialized)
            self.assertNotEqual(disk.ownership_token.int, 0)

        backup_connection = sqlite3.connect(backup)
        try:
            self.assertEqual(
                int(
                    backup_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                ),
                PREVIOUS_REGISTRY_SCHEMA_VERSION,
            )
            backup_columns = {
                str(row[1])
                for row in backup_connection.execute(
                    "PRAGMA table_info(disks)"
                ).fetchall()
            }
            self.assertNotIn("ownership_token", backup_columns)
            self.assertNotIn("materialized", backup_columns)
            self.assertEqual(
                backup_connection.execute(
                    "SELECT disk_id,vm_id FROM disks"
                ).fetchall(),
                [(str(disk_id), str(vm_id))],
            )
        finally:
            backup_connection.close()

        connection = sqlite3.connect(database)
        try:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                REGISTRY_SCHEMA_VERSION,
            )
            columns = {
                str(row[1]): row
                for row in connection.execute(
                    "PRAGMA table_info(disks)"
                ).fetchall()
            }
            self.assertEqual(int(columns["ownership_token"][3]), 1)
            ownership_token, materialized = connection.execute(
                "SELECT ownership_token,materialized "
                "FROM disks WHERE disk_id=?",
                (str(disk_id),),
            ).fetchone()
            self.assertNotEqual(UUID(str(ownership_token)).int, 0)
            self.assertEqual(materialized, 0)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE disks SET materialized=1 WHERE disk_id=?",
                    (str(disk_id),),
                )
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    "SELECT materialized FROM disks WHERE disk_id=?",
                    (str(disk_id),),
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                connection.execute("PRAGMA quick_check").fetchone(),
                ("ok",),
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
        finally:
            connection.close()

    def test_service_imports_one_base_and_materializes_two_sparse_overlays(
        self,
    ) -> None:
        manifest = _manifest()
        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"
        backend = QemuImgBackend("qemu-img")
        children_before = _children()

        with SQLiteRegistry.open(database) as registry:
            store = ImageStore(configuration, backend=backend)
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=store,
            )
            _, imported, image_id = _import_base(service, manifest)
            imported_result = _result(imported)
            self.assertEqual(image_id, manifest.image_id)
            self.assertEqual(
                imported_result["manifest_sha256"],
                manifest.manifest_sha256,
            )
            base = registry.get_base_image(image_id)
            self.assertIsNotNone(base)
            assert base is not None
            self.assertEqual(base.content_sha256, manifest.sha256)
            self.assertEqual(base.manifest, manifest.canonical_bytes)
            self.assertEqual(base.mode, 0o444)
            self.assertEqual(_mode(configuration.storage.base_image), 0o444)
            self.assertEqual(
                _mode(Path(f"{configuration.storage.base_image}.manifest.json")),
                0o444,
            )
            self.assertEqual(
                _file_sha256(configuration.storage.base_image),
                manifest.sha256,
            )

            disks = []
            for index in range(2):
                vm_id, generation, revision = _declare(
                    service,
                    f"overlay-{index}",
                )
                request = _overlay_request(
                    vm_id,
                    generation,
                    revision,
                    image_id,
                )
                response = _success(service(request, _peer()))
                result = _result(response)
                self.assertEqual(result["virtual_size_bytes"], VIRTUAL_SIZE)
                self.assertTrue(result["materialized"])
                rows = registry.list_disks(
                    vm_id=vm_id,
                    active_only=True,
                    materialized_only=True,
                )
                self.assertEqual(len(rows), 1)
                disk = rows[0]
                _assert_overlay(
                    backend,
                    configuration,
                    manifest,
                    disk,
                )
                disks.append(disk)

            identities = {
                (base.device_id, base.inode),
                *((disk.device_id, disk.inode) for disk in disks),
            }
            self.assertEqual(len(identities), 3)
            self.assertNotEqual(disks[0].path, disks[1].path)
            self.assertNotEqual(
                disks[0].ownership_token,
                disks[1].ownership_token,
            )
            self.assertEqual(len(registry.list_base_images()), 1)
            self.assertEqual(
                len(registry.list_disks(materialized_only=True)),
                2,
            )
            _assert_current_registry_with_p3_storage(database, registry)
            _assert_private_roots(configuration)

        self.assertEqual(_children(), children_before)
        _assert_no_staging(self.root)
        _assert_no_runtime_materialization(self.root)

    def test_symlink_and_hardlink_aliases_never_gain_storage_authority(
        self,
    ) -> None:
        manifest = _manifest()
        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"
        backend = QemuImgBackend("qemu-img")

        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration, backend=backend),
            )
            _, _, image_id = _import_base(service, manifest)
            base_path = configuration.storage.base_image
            base_identity = base_path.stat(follow_symlinks=False)

            symlink_vm, symlink_generation, symlink_revision = _declare(
                service,
                "symlink-owner",
            )
            symlink_disk = registry.list_disks(vm_id=symlink_vm)[0]
            symlink_path = Path(symlink_disk.path)
            symlink_path.parent.mkdir(parents=True, mode=0o700)
            symlink_path.symlink_to(base_path)
            _error(
                service(
                    _overlay_request(
                        symlink_vm,
                        symlink_generation,
                        symlink_revision,
                        image_id,
                    ),
                    _peer(),
                ),
                ErrorCode.RECOVERY_REQUIRED,
            )
            self.assertTrue(symlink_path.is_symlink())
            self.assertFalse(
                _raw_disk_materialized(database, symlink_disk.disk_id)
            )
            self.assertFalse(Path(f"{symlink_path}.owner.json").exists())
            symlink_path.unlink()

        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration, backend=backend),
            )
            self.assertFalse(
                registry.get_disk(symlink_disk.disk_id).materialized
            )
            self.assertEqual(service.recover_incomplete_operations(), 1)
            recovered_symlink_disk = registry.get_disk(symlink_disk.disk_id)
            self.assertTrue(recovered_symlink_disk.materialized)
            _assert_overlay(
                backend,
                configuration,
                manifest,
                recovered_symlink_disk,
            )

            hard_vm, hard_generation, hard_revision = _declare(
                service,
                "hardlink-owner",
            )
            hard_disk = registry.list_disks(vm_id=hard_vm)[0]
            hard_path = Path(hard_disk.path)
            hard_path.parent.mkdir(parents=True, mode=0o700)
            os.link(base_path, hard_path)
            self.assertEqual(
                hard_path.stat(follow_symlinks=False).st_ino,
                base_path.stat(follow_symlinks=False).st_ino,
            )
            _error(
                service(
                    _overlay_request(
                        hard_vm,
                        hard_generation,
                        hard_revision,
                        image_id,
                    ),
                    _peer(),
                ),
                ErrorCode.RECOVERY_REQUIRED,
            )
            self.assertFalse(registry.get_disk(hard_disk.disk_id).materialized)
            self.assertFalse(Path(f"{hard_path}.owner.json").exists())
            hard_path.unlink()
            settled_base = base_path.stat(follow_symlinks=False)
            self.assertEqual(settled_base.st_nlink, 1)
            self.assertEqual(
                (settled_base.st_dev, settled_base.st_ino),
                (base_identity.st_dev, base_identity.st_ino),
            )
            self.assertEqual(_file_sha256(base_path), manifest.sha256)
            self.assertEqual(service.recover_incomplete_operations(), 1)
            recovered_hardlink_disk = registry.get_disk(hard_disk.disk_id)
            self.assertTrue(recovered_hardlink_disk.materialized)
            _assert_overlay(
                backend,
                configuration,
                manifest,
                recovered_hardlink_disk,
            )
            self.assertEqual(registry.list_operations(incomplete_only=True), ())
            _assert_current_registry_with_p3_storage(database, registry)

        _assert_no_staging(self.root)
        _assert_no_runtime_materialization(self.root)

    def test_base_import_recovers_after_every_persisted_checkpoint(self) -> None:
        manifest = _manifest()
        for index, checkpoint in enumerate(BASE_CHECKPOINTS):
            with self.subTest(checkpoint=checkpoint):
                with tempfile.TemporaryDirectory(
                    prefix=f"b{index}-",
                    dir=self.root,
                ) as temporary:
                    root = Path(temporary).resolve()
                    configuration = _configuration(root)
                    database = root / "registry" / "registry.sqlite3"
                    request = _import_request(manifest)
                    crashed = _crash_after_checkpoint(
                        root,
                        database,
                        request,
                        checkpoint,
                    )
                    self.assertEqual(
                        crashed.returncode,
                        CRASH_EXIT,
                        crashed.stdout + crashed.stderr,
                    )
                    registry, _, _ = _recover(
                        root,
                        database,
                        request,
                        BASE_CHECKPOINTS,
                        index,
                    )
                    try:
                        _assert_current_registry_with_p3_storage(
                            database,
                            registry,
                        )
                        base = registry.get_base_image(manifest.image_id)
                        self.assertIsNotNone(base)
                        assert base is not None
                        self.assertEqual(base.content_sha256, manifest.sha256)
                        self.assertEqual(base.manifest, manifest.canonical_bytes)
                        self.assertEqual(base.mode, 0o444)
                        self.assertEqual(
                            _mode(configuration.storage.base_image),
                            0o444,
                        )
                        self.assertEqual(
                            _file_sha256(configuration.storage.base_image),
                            manifest.sha256,
                        )
                        self.assertEqual(len(registry.list_base_images()), 1)
                        self.assertEqual(registry.list_disks(), ())
                    finally:
                        registry.close()
                    _assert_no_staging(root)
                    _assert_no_runtime_materialization(root)

    def test_overlay_recovers_after_every_persisted_checkpoint(self) -> None:
        manifest = _manifest()
        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"
        backend = QemuImgBackend("qemu-img")

        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration, backend=backend),
            )
            _, _, image_id = _import_base(service, manifest)

        recovered_disks = []
        for index, checkpoint in enumerate(OVERLAY_CHECKPOINTS):
            with self.subTest(checkpoint=checkpoint):
                with SQLiteRegistry.open(database) as registry:
                    service = RegistryMutationService(
                        registry,
                        configuration,
                        image_store=ImageStore(configuration),
                    )
                    vm_id, generation, revision = _declare(
                        service,
                        f"recovery-{index}",
                    )
                    request = _overlay_request(
                        vm_id,
                        generation,
                        revision,
                        image_id,
                    )

                crashed = _crash_after_checkpoint(
                    self.root,
                    database,
                    request,
                    checkpoint,
                )
                self.assertEqual(
                    crashed.returncode,
                    CRASH_EXIT,
                    crashed.stdout + crashed.stderr,
                )
                registry, _, _ = _recover(
                    self.root,
                    database,
                    request,
                    OVERLAY_CHECKPOINTS,
                    index,
                )
                try:
                    _assert_current_registry_with_p3_storage(
                        database,
                        registry,
                    )
                    disks = registry.list_disks(
                        vm_id=vm_id,
                        active_only=True,
                        materialized_only=True,
                    )
                    self.assertEqual(len(disks), 1)
                    disk = disks[0]
                    _assert_overlay(
                        backend,
                        configuration,
                        manifest,
                        disk,
                    )
                    recovered_disks.append(disk)
                finally:
                    registry.close()
                _assert_no_staging(self.root)
                _assert_no_runtime_materialization(self.root)

        identities = {
            (disk.device_id, disk.inode)
            for disk in recovered_disks
        }
        self.assertEqual(len(recovered_disks), len(OVERLAY_CHECKPOINTS))
        self.assertEqual(len(identities), len(OVERLAY_CHECKPOINTS))
        with SQLiteRegistry.open(database) as registry:
            self.assertEqual(
                len(registry.list_disks(materialized_only=True)),
                len(OVERLAY_CHECKPOINTS),
            )
            self.assertEqual(len(registry.list_base_images()), 1)
            _assert_current_registry_with_p3_storage(database, registry)


if __name__ == "__main__":
    unittest.main(verbosity=2)
