"""Physical P4 proof that writable qcow2 observations do not rewrite P3 truth.

The test creates a real P3-owned overlay from the pinned disposable fixture,
writes guest-like bytes with the real ``qemu-io`` 8.2.2 process, and proves
that the daemon preserves immutable disk ownership while accepting only an
append-only runtime observation for allocation growth.  It launches no VM and
makes no QMP or guest-readiness claim.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = Path(__file__).resolve().parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_storage_p3 import (  # noqa: E402
    FIXTURE_MANIFEST,
    FIXTURE_SOURCE,
    QEMU_IMG,
    VIRTUAL_SIZE,
    _configuration,
    _declare,
    _import_base,
    _manifest,
    _overlay_request,
    _peer,
    _source_identity,
    _success,
)
from somnus_vm.host.images import QemuImgBackend  # noqa: E402
from somnus_vm.host.registry import SQLiteRegistry  # noqa: E402
from somnus_vm.host.service import (  # noqa: E402
    RegistryMutationService,
    RegistryServiceError,
)
from somnus_vm.host.storage import ImageStore  # noqa: E402


QEMU_IO = Path("/usr/bin/qemu-io")


class StorageRuntimeP4Tests(unittest.TestCase):
    def setUp(self) -> None:
        for binary in (QEMU_IMG, QEMU_IO):
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise AssertionError(f"required physical tool is absent: {binary}")
        if not FIXTURE_SOURCE.is_file() or not FIXTURE_MANIFEST.is_file():
            raise AssertionError("the pinned P3 fixture authority is absent")
        self.source_before = _source_identity()
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-runtime-disk-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()
        if _source_identity() != self.source_before:
            raise AssertionError("the immutable source fixture identity changed")

    def test_runtime_growth_is_append_only_but_structure_stays_exact(self) -> None:
        configuration = _configuration(self.root)
        database = self.root / "registry" / "registry.sqlite3"
        backend = QemuImgBackend("qemu-img")
        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(
                registry,
                configuration,
                image_store=ImageStore(configuration, backend=backend),
            )
            _, _, base_image_id = _import_base(service, _manifest())
            vm_id, generation, revision = _declare(
                service,
                "runtime-disk-owner",
            )
            _success(
                service(
                    _overlay_request(
                        vm_id,
                        generation,
                        revision,
                        base_image_id,
                    ),
                    _peer(),
                )
            )
            disk = registry.list_disks(vm_id=vm_id)[0]
            self.assertTrue(disk.materialized)
            path = Path(disk.path)
            immutable_baseline = (
                disk.device_id,
                disk.inode,
                disk.file_size_bytes,
                disk.allocated_size_bytes,
                disk.chain_sha256,
                disk.marker_sha256,
                disk.ownership_token,
            )

            write = subprocess.run(
                (
                    os.fspath(QEMU_IO),
                    "-f",
                    "qcow2",
                    "-c",
                    "write -P 0x5a 67108864 1048576",
                    os.fspath(path),
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=30.0,
            )
            self.assertEqual(write.returncode, 0, write.stderr)
            details = path.stat(follow_symlinks=False)
            chain = backend.inspect(path)
            self.assertEqual(len(chain), 2)
            self.assertGreater(
                details.st_blocks * 512,
                int(disk.allocated_size_bytes),
            )

            # Without a P4 observation, allocation drift is still suspicious
            # under the P3-only authority contract.
            with self.assertRaises(RegistryServiceError):
                service.reconcile_storage_authority()
            # Runtime startup first proves the live owner through process/QMP.
            # The generic storage pass must then leave that writable overlay
            # alone rather than invoking qemu-img against an open guest disk.
            self.assertEqual(
                service.reconcile_storage_authority(
                    exclude_live_vm_ids={vm_id}
                ),
                1,
            )

            operation_id = uuid4()
            with registry.write_transaction() as transaction:
                transaction.begin_operation(
                    operation_id,
                    vm_id,
                    "runtime.launch",
                    f"runtime.disk:{operation_id}",
                    request_hash=hashlib.sha256(
                        f"runtime.disk:{operation_id}".encode("ascii")
                    ).hexdigest(),
                    intent=b"{}",
                    owned_resources=b"{}",
                )
                transaction.set_operation_status(
                    operation_id,
                    "running",
                    result=b"{}",
                )
                observed_at = datetime.now(timezone.utc)
                observation = transaction.append_disk_runtime_observation(
                    disk.disk_id,
                    vm_id=vm_id,
                    generation=generation,
                    observed_at=observed_at,
                    file_size_bytes=details.st_size,
                    allocated_size_bytes=details.st_blocks * 512,
                    qemu_actual_size_bytes=chain[0].actual_size_bytes,
                    dirty=chain[0].dirty,
                    corrupt=chain[0].corrupt,
                    quiesced=False,
                    operation_id=operation_id,
                )

            self.assertEqual(
                service.reconcile_storage_authority(),
                2,
            )
            current = registry.get_disk(disk.disk_id)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(
                (
                    current.device_id,
                    current.inode,
                    current.file_size_bytes,
                    current.allocated_size_bytes,
                    current.chain_sha256,
                    current.marker_sha256,
                    current.ownership_token,
                ),
                immutable_baseline,
            )
            self.assertEqual(
                registry.latest_disk_runtime_observation(disk.disk_id),
                observation,
            )

            # Runtime mutability never permits a changed virtual capacity or
            # backing authority to normalize into success.
            resize = subprocess.run(
                (
                    os.fspath(QEMU_IMG),
                    "resize",
                    "--shrink",
                    os.fspath(path),
                    str(VIRTUAL_SIZE - 1024**3),
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=30.0,
            )
            self.assertEqual(resize.returncode, 0, resize.stderr)
            with self.assertRaises(RegistryServiceError):
                service.reconcile_storage_authority()


if __name__ == "__main__":
    unittest.main(verbosity=2)
