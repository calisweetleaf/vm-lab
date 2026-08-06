"""Adversarial physical proof for the P3 storage authority boundary.

These tests use real files, SQLite transactions/triggers, qemu-img processes,
process death, and Linux parent-death signaling.  They never launch QEMU,
mount an image, replace a production path, or treat a skip/fallback as proof.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = Path(__file__).resolve().parent

for entry in (SOURCE_ROOT, TEST_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from somnus_protocol import ErrorCode  # noqa: E402
from somnus_vm.host.images import QemuImgBackend  # noqa: E402
from somnus_vm.host.registry import (  # noqa: E402
    OperationCheckpoint,
    OperationRecord,
    SQLiteRegistry,
)
from somnus_vm.host.service import (  # noqa: E402
    RegistryMutationService,
    RegistryServiceError,
    _operation_id,
)
from somnus_vm.host.storage import ImageStore  # noqa: E402
from test_storage_p3 import (  # noqa: E402
    BASE_CHECKPOINTS,
    CRASH_EXIT,
    FIXTURE_MANIFEST,
    FIXTURE_SOURCE,
    OVERLAY_CHECKPOINTS,
    QEMU_IMG,
    VIRTUAL_SIZE,
    _configuration,
    _crash_after_checkpoint,
    _declare,
    _error,
    _file_sha256,
    _import_base,
    _import_request,
    _manifest,
    _mode,
    _overlay_request,
    _peer,
    _source_identity,
)


SLEEP_BINARY = Path("/usr/bin/sleep")
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


def _operation_for(
    registry: SQLiteRegistry,
    request_id: object,
) -> OperationRecord:
    row = registry.find_idempotency(str(request_id))
    if row is None:
        raise AssertionError("storage idempotency row is absent")
    operation = registry.get_operation(row.operation_id)
    if operation is None:
        raise AssertionError("storage operation row is absent")
    return operation


def _checkpoint(
    registry: SQLiteRegistry,
    operation: OperationRecord,
    name: str,
) -> OperationCheckpoint:
    matches = tuple(
        item
        for item in registry.list_checkpoints(operation.operation_id)
        if item.name == name
    )
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {name!r} checkpoint, observed {len(matches)}"
        )
    return matches[0]


def _checkpoint_object(checkpoint: OperationCheckpoint) -> dict[str, object]:
    try:
        value = json.loads(checkpoint.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("checkpoint payload is not JSON") from exc
    if not isinstance(value, dict):
        raise AssertionError("checkpoint payload is not an object")
    return value


def _checkpoint_names(
    registry: SQLiteRegistry,
    operation: OperationRecord,
) -> tuple[str, ...]:
    return tuple(
        item.name
        for item in registry.list_checkpoints(operation.operation_id)
    )


def _assert_recovery_required(
    testcase: unittest.TestCase,
    registry: SQLiteRegistry,
    operation: OperationRecord,
) -> OperationRecord:
    refreshed = registry.get_operation(operation.operation_id)
    testcase.assertIsNotNone(refreshed)
    assert refreshed is not None
    testcase.assertEqual(refreshed.status, "recovery_required")
    testcase.assertNotEqual(refreshed.status, "failed")
    testcase.assertNotIn(
        "operation_failed",
        _checkpoint_names(registry, refreshed),
    )
    return refreshed


def _set_child_subreaper(enabled: bool) -> bool:
    """Set Linux subreaper state and return its previous value."""

    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if (
        libc.prctl(
            _PR_GET_CHILD_SUBREAPER,
            ctypes.byref(previous),
            0,
            0,
            0,
        )
        != 0
    ):
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, int(enabled), 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return bool(previous.value)


class StorageP3AdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        if sys.platform != "linux":
            raise AssertionError("P3 storage authority requires Linux")
        if not QEMU_IMG.is_file() or not os.access(QEMU_IMG, os.X_OK):
            raise AssertionError("/usr/bin/qemu-img is unavailable")
        if not SLEEP_BINARY.is_file() or not os.access(SLEEP_BINARY, os.X_OK):
            raise AssertionError("/usr/bin/sleep is unavailable")
        if not FIXTURE_SOURCE.is_file() or not FIXTURE_MANIFEST.is_file():
            raise AssertionError("the required P3 fixture or manifest is absent")
        self.source_before = _source_identity()
        manifest = _manifest()
        if (
            manifest.sha256 != self.source_before[-1]
            or manifest.size_bytes != self.source_before[2]
        ):
            raise AssertionError(
                "fixture bytes disagree with repository authority"
            )
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p3-adversarial-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()
        if _source_identity() != self.source_before:
            raise AssertionError("the immutable source fixture identity changed")

    def test_foreign_final_base_without_manifest_is_not_adopted(self) -> None:
        manifest = _manifest()
        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"

        with SQLiteRegistry.open(database) as registry:
            store = ImageStore(configuration, backend=QemuImgBackend("qemu-img"))
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=store,
            )
            foreign = configuration.storage.base_image
            foreign.write_bytes(b"foreign base claimant\n")
            os.chmod(foreign, 0o444)
            before = foreign.stat(follow_symlinks=False)
            before_hash = _file_sha256(foreign)

            request = _import_request(manifest)
            _error(
                service(request, _peer()),
                ErrorCode.RECOVERY_REQUIRED,
            )

            after = foreign.stat(follow_symlinks=False)
            self.assertEqual(
                (after.st_dev, after.st_ino, after.st_size, _mode(foreign)),
                (before.st_dev, before.st_ino, before.st_size, 0o444),
            )
            self.assertEqual(_file_sha256(foreign), before_hash)
            self.assertFalse(
                Path(f"{foreign}.manifest.json").exists()
            )
            self.assertEqual(registry.list_base_images(), ())
            self.assertEqual(registry.list_operations(), ())

    def test_foreign_final_overlay_without_marker_is_not_adopted(self) -> None:
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
            vm_id, generation, revision = _declare(service, "foreign-final")
            disk = registry.list_disks(vm_id=vm_id)[0]
            final_path = Path(disk.path)
            final_path.parent.mkdir(parents=True, mode=0o700)
            backend.create_overlay(
                final_path,
                configuration.storage.base_image,
                virtual_size_bytes=VIRTUAL_SIZE,
            )
            os.chmod(final_path, 0o600)
            before = final_path.stat(follow_symlinks=False)
            before_hash = _file_sha256(final_path)
            request = _overlay_request(
                vm_id,
                generation,
                revision,
                image_id,
            )

            _error(
                service(request, _peer()),
                ErrorCode.RECOVERY_REQUIRED,
            )

            operation = _operation_for(registry, request.request_id)
            _assert_recovery_required(self, registry, operation)
            after = final_path.stat(follow_symlinks=False)
            self.assertEqual(
                (after.st_dev, after.st_ino, after.st_size, _mode(final_path)),
                (before.st_dev, before.st_ino, before.st_size, 0o600),
            )
            self.assertEqual(_file_sha256(final_path), before_hash)
            self.assertFalse(Path(f"{final_path}.owner.json").exists())
            stored_disk = registry.get_disk(disk.disk_id)
            self.assertIsNotNone(stored_disk)
            assert stored_disk is not None
            self.assertFalse(stored_disk.materialized)

    def test_stage_symlink_rejection_does_not_chmod_victim(self) -> None:
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
            vm_id, generation, revision = _declare(service, "symlink-stage")
            disk = registry.list_disks(vm_id=vm_id)[0]
            request = _overlay_request(
                vm_id,
                generation,
                revision,
                image_id,
            )
            operation_id = _operation_id(request)
            final_path = Path(disk.path)
            stage = (
                final_path.parent
                / ".staging"
                / str(operation_id)
            )
            stage.mkdir(parents=True, mode=0o700)
            os.chmod(stage, 0o700)
            victim = self.root / "victim.bin"
            victim.write_bytes(b"must remain mode 0640 and unchanged\n")
            os.chmod(victim, 0o640)
            victim_before = victim.stat(follow_symlinks=False)
            victim_hash = _file_sha256(victim)
            candidate = stage / "aipc.qcow2"
            candidate.symlink_to(victim)

            _error(
                service(request, _peer()),
                ErrorCode.RECOVERY_REQUIRED,
            )

            operation = _operation_for(registry, request.request_id)
            _assert_recovery_required(self, registry, operation)
            victim_after = victim.stat(follow_symlinks=False)
            self.assertEqual(
                (
                    victim_after.st_dev,
                    victim_after.st_ino,
                    victim_after.st_size,
                    _mode(victim),
                ),
                (
                    victim_before.st_dev,
                    victim_before.st_ino,
                    victim_before.st_size,
                    0o640,
                ),
            )
            self.assertEqual(_file_sha256(victim), victim_hash)
            self.assertTrue(candidate.is_symlink())
            self.assertFalse(final_path.exists())
            self.assertFalse(Path(f"{final_path}.owner.json").exists())
            stored_disk = registry.get_disk(disk.disk_id)
            self.assertIsNotNone(stored_disk)
            assert stored_disk is not None
            self.assertFalse(stored_disk.materialized)

    def test_missing_checkpointed_base_candidate_is_not_recreated(self) -> None:
        manifest = _manifest()
        root = self.root / "missing-base"
        root.mkdir(mode=0o700)
        configuration = _configuration(root)
        database = root / "registry" / "registry.sqlite3"
        request = _import_request(manifest)
        crashed = _crash_after_checkpoint(
            root,
            database,
            request,
            "base_bytes_staged",
        )
        self.assertEqual(
            crashed.returncode,
            CRASH_EXIT,
            crashed.stdout + crashed.stderr,
        )

        with SQLiteRegistry.open(database) as registry:
            operation = _operation_for(registry, request.request_id)
            checkpoint = _checkpoint(
                registry,
                operation,
                "base_bytes_staged",
            )
            payload = _checkpoint_object(checkpoint)
            candidate = Path(str(payload["stage_path"]))
            self.assertTrue(candidate.is_file())
            candidate.unlink()
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration),
            )

            with self.assertRaises(RegistryServiceError) as caught:
                service.recover_incomplete_operations()
            self.assertIs(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)

            refreshed = _assert_recovery_required(
                self,
                registry,
                operation,
            )
            self.assertEqual(
                _checkpoint_names(registry, refreshed),
                BASE_CHECKPOINTS[:3],
            )
            self.assertFalse(candidate.exists())
            self.assertFalse(configuration.storage.base_image.exists())
            self.assertFalse(
                Path(
                    f"{configuration.storage.base_image}.manifest.json"
                ).exists()
            )
            self.assertEqual(registry.list_base_images(), ())

    def test_replaced_checkpointed_overlay_candidate_is_not_adopted(self) -> None:
        manifest = _manifest()
        root = self.root / "replaced-overlay"
        root.mkdir(mode=0o700)
        configuration = _configuration(root)
        database = root / "registry" / "registry.sqlite3"
        backend = QemuImgBackend("qemu-img")

        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration, backend=backend),
            )
            _, _, image_id = _import_base(service, manifest)
            vm_id, generation, revision = _declare(
                service,
                "replaced-checkpoint",
            )
            request = _overlay_request(
                vm_id,
                generation,
                revision,
                image_id,
            )
            disk_id = registry.list_disks(vm_id=vm_id)[0].disk_id

        crashed = _crash_after_checkpoint(
            root,
            database,
            request,
            "overlay_created",
        )
        self.assertEqual(
            crashed.returncode,
            CRASH_EXIT,
            crashed.stdout + crashed.stderr,
        )

        with SQLiteRegistry.open(database) as registry:
            operation = _operation_for(registry, request.request_id)
            checkpoint = _checkpoint(
                registry,
                operation,
                "overlay_created",
            )
            payload = _checkpoint_object(checkpoint)
            candidate = Path(str(payload["stage_path"]))
            self.assertTrue(candidate.is_file())
            original = candidate.with_name("original-checkpointed.qcow2")
            candidate.rename(original)
            backend.create_overlay(
                candidate,
                configuration.storage.base_image,
                virtual_size_bytes=VIRTUAL_SIZE,
            )
            os.chmod(candidate, 0o600)
            replacement = candidate.stat(follow_symlinks=False)
            original_details = original.stat(follow_symlinks=False)
            self.assertNotEqual(
                (replacement.st_dev, replacement.st_ino),
                (original_details.st_dev, original_details.st_ino),
            )
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration, backend=backend),
            )

            with self.assertRaises(RegistryServiceError) as caught:
                service.recover_incomplete_operations()
            self.assertIs(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)

            refreshed = _assert_recovery_required(
                self,
                registry,
                operation,
            )
            self.assertEqual(
                _checkpoint_names(registry, refreshed),
                OVERLAY_CHECKPOINTS[:3],
            )
            after = candidate.stat(follow_symlinks=False)
            self.assertEqual(
                (after.st_dev, after.st_ino),
                (replacement.st_dev, replacement.st_ino),
            )
            self.assertTrue(original.is_file())
            disk = registry.get_disk(disk_id)
            self.assertIsNotNone(disk)
            assert disk is not None
            self.assertFalse(disk.materialized)
            self.assertFalse(Path(disk.path).exists())
            self.assertFalse(Path(f"{disk.path}.owner.json").exists())

    def test_same_bytes_new_inode_registered_base_blocks_use_and_startup(
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
            vm_id, generation, revision = _declare(
                service,
                "replaced-base",
            )
            disk = registry.list_disks(vm_id=vm_id)[0]
            base_path = configuration.storage.base_image
            registered = registry.get_base_image(image_id)
            self.assertIsNotNone(registered)
            assert registered is not None
            original = base_path.with_name("registered-original.qcow2")
            base_path.rename(original)
            shutil.copyfile(original, base_path)
            os.chmod(base_path, 0o444)
            replacement = base_path.stat(follow_symlinks=False)
            self.assertNotEqual(
                (replacement.st_dev, replacement.st_ino),
                (registered.device_id, registered.inode),
            )
            self.assertEqual(_file_sha256(base_path), manifest.sha256)

            with self.assertRaises(RegistryServiceError) as startup_error:
                service.reconcile_storage_authority()
            self.assertIs(
                startup_error.exception.code,
                ErrorCode.RECOVERY_REQUIRED,
            )

            request = _overlay_request(
                vm_id,
                generation,
                revision,
                image_id,
            )
            _error(
                service(request, _peer()),
                ErrorCode.RECOVERY_REQUIRED,
            )
            operation = _operation_for(registry, request.request_id)
            _assert_recovery_required(self, registry, operation)
            stored_disk = registry.get_disk(disk.disk_id)
            self.assertIsNotNone(stored_disk)
            assert stored_disk is not None
            self.assertFalse(stored_disk.materialized)
            self.assertFalse(Path(stored_disk.path).exists())
            self.assertFalse(
                Path(f"{stored_disk.path}.owner.json").exists()
            )

    def test_post_publication_sqlite_conflict_is_never_terminal_failure(
        self,
    ) -> None:
        manifest = _manifest()
        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"

        with SQLiteRegistry.open(database) as registry:
            trigger_connection = sqlite3.connect(database)
            try:
                trigger_connection.execute(
                    "CREATE TRIGGER adversarial_block_base_registration "
                    "BEFORE INSERT ON base_images "
                    "BEGIN "
                    "SELECT RAISE(ABORT, 'adversarial registry conflict'); "
                    "END"
                )
                trigger_connection.commit()
            finally:
                trigger_connection.close()

            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration),
            )
            request = _import_request(manifest)
            _error(
                service(request, _peer()),
                ErrorCode.RECOVERY_REQUIRED,
            )

            operation = _operation_for(registry, request.request_id)
            refreshed = _assert_recovery_required(
                self,
                registry,
                operation,
            )
            self.assertEqual(
                _checkpoint_names(registry, refreshed),
                BASE_CHECKPOINTS[:5],
            )
            self.assertEqual(registry.list_base_images(), ())
            self.assertTrue(configuration.storage.base_image.is_file())
            self.assertEqual(
                _file_sha256(configuration.storage.base_image),
                manifest.sha256,
            )
            manifest_path = Path(
                f"{configuration.storage.base_image}.manifest.json"
            )
            self.assertEqual(
                manifest_path.read_bytes(),
                manifest.canonical_bytes,
            )
            self.assertEqual(_mode(configuration.storage.base_image), 0o444)
            self.assertEqual(_mode(manifest_path), 0o444)

    def test_parent_death_guard_kills_live_native_child(self) -> None:
        pid_path = self.root / "guarded-child.pid"
        native = SLEEP_BINARY.resolve(strict=True)
        child_program = r"""
import os
import subprocess
import sys
import time
from pathlib import Path

source_root = sys.argv[1]
pid_path = Path(sys.argv[2])
native = Path(sys.argv[3]).resolve(strict=True)
environment = os.environ.copy()
environment["PYTHONPATH"] = source_root
child = subprocess.Popen(
    [
        sys.executable,
        "-B",
        "-m",
        "somnus_vm.host.exec_guard",
        str(os.getpid()),
        str(native),
        "30",
    ],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
    env=environment,
)
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    if child.poll() is not None:
        raise SystemExit(91)
    try:
        observed = Path(f"/proc/{child.pid}/exe").resolve(strict=True)
    except FileNotFoundError:
        time.sleep(0.01)
        continue
    if observed == native:
        pid_path.write_text(f"{child.pid}\n", encoding="ascii")
        os._exit(0)
    time.sleep(0.01)
try:
    child.kill()
finally:
    child.wait(timeout=2.0)
raise SystemExit(92)
"""
        previous_subreaper = _set_child_subreaper(True)
        child_pid: int | None = None
        reaped_status: int | None = None
        try:
            environment = {
                "HOME": os.environ.get("HOME", "/tmp"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": os.fspath(SOURCE_ROOT),
            }
            parent = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    child_program,
                    os.fspath(SOURCE_ROOT),
                    os.fspath(pid_path),
                    os.fspath(native),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, stderr = parent.communicate(timeout=10.0)
            self.assertEqual(
                parent.returncode,
                0,
                stdout + stderr,
            )
            self.assertTrue(
                pid_path.is_file(),
                "intermediate parent never observed the native child",
            )
            child_pid = int(pid_path.read_text(encoding="ascii").strip())

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    waited_pid, status = os.waitpid(
                        child_pid,
                        os.WNOHANG,
                    )
                except ChildProcessError:
                    time.sleep(0.01)
                    continue
                if waited_pid == child_pid:
                    reaped_status = status
                    break
                time.sleep(0.01)

            if reaped_status is None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    _, reaped_status = os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass
                self.fail(
                    "guarded native child remained live after parent death"
                )

            self.assertTrue(os.WIFSIGNALED(reaped_status))
            self.assertEqual(
                os.WTERMSIG(reaped_status),
                signal.SIGKILL,
            )
        finally:
            if child_pid is not None and reaped_status is None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass
            _set_child_subreaper(previous_subreaper)


if __name__ == "__main__":
    unittest.main(verbosity=2)
