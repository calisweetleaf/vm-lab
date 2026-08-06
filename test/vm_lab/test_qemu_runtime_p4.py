"""Physical non-QEMU proofs for P4 runtime ownership composition.

These checks exercise real Linux directory entries, regular files, Unix
sockets, renameat2 no-replace semantics, SQLite writer ownership, and pidfds.
They deliberately do not launch QEMU or claim GATE-P4 physical lifecycle
completion.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = Path(__file__).resolve().parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from somnus_vm.config import load_configuration  # noqa: E402
from somnus_vm.host.qemu_process import (  # noqa: E402
    PidfileIdentity,
    ProcessIdentityMismatchError,
    ProcessUnavailableError,
    observe_process,
    prove_process_alive,
    resolve_executable,
    terminate_owned_process,
)
from somnus_vm.host.qemu_runtime import (  # noqa: E402
    QemuRuntimeOwner,
    QemuRuntimeRecoveryRequired,
    _wait_for_pidfile,
)
from somnus_vm.host.qemu_runtime_journal import (  # noqa: E402
    JournalDirectoryIdentity,
)
from somnus_vm.host.qmp import QMPSocketIdentity  # noqa: E402
from somnus_vm.host.registry import SQLiteRegistry  # noqa: E402


def _write_configuration(root: Path) -> Path:
    path = root / "host.toml"
    path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "p4-runtime-owner"',
                "",
                "[host]",
                f'state_dir = "{root / "state"}"',
                f'runtime_dir = "{root / "runtime"}"',
                f'base_image = "{root / "images/base.qcow2"}"',
                'qemu_binary = "sleep"',
                'qemu_img_binary = "qemu-img"',
                "enable_kvm = false",
                "ssh_ports = [46000, 46127]",
                "agent_ports = [46200, 46327]",
                "vnc_ports = [6400, 6527]",
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


class QemuRuntimeP4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-runtime-owner-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()
        self.configuration = load_configuration(
            _write_configuration(self.root)
        ).host
        self.configuration.daemon.runtime_root.mkdir(
            mode=0o700,
            parents=True,
        )
        os.chmod(self.configuration.daemon.runtime_root, 0o700)
        self.registry = SQLiteRegistry.open(
            self.root / "registry" / "registry.sqlite3"
        )
        self.owner = QemuRuntimeOwner(
            self.registry,
            self.configuration,
        )
        runtime = self.configuration.daemon.runtime_root.lstat()
        self.runtime_identity = JournalDirectoryIdentity(
            path=os.fspath(self.configuration.daemon.runtime_root),
            device_id=runtime.st_dev,
            inode=runtime.st_ino,
            owner_uid=runtime.st_uid,
            mode=0o700,
        )

    def tearDown(self) -> None:
        self.owner.shutdown()
        self.registry.close()
        self.temporary.cleanup()

    def test_pidfd_liveness_fence_rejects_a_proven_exit(self) -> None:
        executable = resolve_executable("sleep")
        process = subprocess.Popen(
            (os.fspath(executable), "30"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        observed = observe_process(process.pid)
        try:
            fenced = prove_process_alive(observed)
            self.assertEqual(fenced.pid, observed.pid)
            self.assertEqual(
                fenced.start_time_ticks,
                observed.start_time_ticks,
            )
            terminate_owned_process(
                observed,
                graceful_timeout_seconds=1.0,
                kill_timeout_seconds=1.0,
            )
            process.wait(timeout=2.0)
            with self.assertRaises(ProcessUnavailableError):
                prove_process_alive(observed)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2.0)

    def test_pidfile_readiness_waits_only_for_absent_or_incomplete_bytes(
        self,
    ) -> None:
        pidfile = self.configuration.daemon.runtime_root / "delayed.pid"
        script = (
            "import os,sys,time;"
            "time.sleep(0.10);"
            "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);"
            "time.sleep(0.10);"
            "os.write(fd,f'{os.getpid()}\\n'.encode('ascii'));"
            "os.fsync(fd);"
            "os.close(fd);"
            "time.sleep(30)"
        )
        process = subprocess.Popen(
            (sys.executable, "-c", script, os.fspath(pidfile)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        observed = observe_process(process.pid)
        try:
            identity = _wait_for_pidfile(
                observed,
                pidfile,
                expected_uid=observed.process_owner_uid,
                timeout_seconds=2.0,
                retry_seconds=0.01,
            )
            self.assertEqual(identity.pid, observed.pid)
            self.assertEqual(identity.mode, 0o600)

            wrong = self.configuration.daemon.runtime_root / "wrong.pid"
            wrong.write_text(f"{observed.pid + 1}\n", encoding="ascii")
            os.chmod(wrong, 0o600)
            with self.assertRaises(ProcessIdentityMismatchError):
                _wait_for_pidfile(
                    observed,
                    wrong,
                    expected_uid=observed.process_owner_uid,
                    timeout_seconds=1.0,
                    retry_seconds=0.01,
                )
        finally:
            if process.poll() is None:
                terminate_owned_process(
                    observed,
                    graceful_timeout_seconds=1.0,
                    kill_timeout_seconds=1.0,
                )
            process.wait(timeout=2.0)

    def test_pidfile_retirement_is_no_replace_retained_and_idempotent(
        self,
    ) -> None:
        operation_id = uuid4()
        path = self.configuration.daemon.runtime_root / "owned.pid"
        path.write_bytes(b"4242\n")
        os.chmod(path, 0o600)
        details = path.lstat()
        expected = PidfileIdentity(
            path=path,
            pid=4242,
            device_id=details.st_dev,
            inode=details.st_ino,
            owner_uid=details.st_uid,
            mode=0o600,
            link_count=1,
            size_bytes=details.st_size,
            mtime_ns=details.st_mtime_ns,
            ctime_ns=details.st_ctime_ns,
        )
        self.owner._retire_runtime_entry(  # noqa: SLF001
            path,
            expected,
            operation_id=operation_id,
            socket_entry=False,
            parent_identity=self.runtime_identity,
        )
        retired = path.with_name(
            f".somnus-retired-{operation_id}-{path.name}"
        )
        self.assertFalse(path.exists())
        self.assertEqual(retired.read_bytes(), b"4242\n")
        self.assertEqual(retired.lstat().st_ino, details.st_ino)

        # Exact replay observes the deterministic retained tombstone.
        self.owner._retire_runtime_entry(  # noqa: SLF001
            path,
            expected,
            operation_id=operation_id,
            socket_entry=False,
            parent_identity=self.runtime_identity,
        )
        path.write_bytes(b"foreign\n")
        os.chmod(path, 0o600)
        with self.assertRaises(QemuRuntimeRecoveryRequired):
            self.owner._retire_runtime_entry(  # noqa: SLF001
                path,
                expected,
                operation_id=operation_id,
                socket_entry=False,
                parent_identity=self.runtime_identity,
            )
        self.assertEqual(path.read_bytes(), b"foreign\n")
        self.assertEqual(retired.read_bytes(), b"4242\n")

    def test_qmp_socket_retirement_captures_the_exact_inode_without_unlink(
        self,
    ) -> None:
        operation_id = uuid4()
        path = self.configuration.daemon.runtime_root / "owned.qmp"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(os.fspath(path))
        os.chmod(path, 0o600)
        details = path.lstat()
        expected = QMPSocketIdentity(
            device_id=details.st_dev,
            inode=details.st_ino,
            uid=details.st_uid,
            mode=0o600,
        )
        try:
            self.owner._retire_runtime_entry(  # noqa: SLF001
                path,
                expected,
                operation_id=operation_id,
                socket_entry=True,
                parent_identity=self.runtime_identity,
            )
            retired = path.with_name(
                f".somnus-retired-{operation_id}-{path.name}"
            )
            self.assertFalse(path.exists())
            self.assertTrue(retired.exists())
            self.assertEqual(retired.lstat().st_ino, details.st_ino)
            self.owner._retire_runtime_entry(  # noqa: SLF001
                path,
                expected,
                operation_id=operation_id,
                socket_entry=True,
                parent_identity=self.runtime_identity,
            )
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
