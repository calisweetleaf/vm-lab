"""Consumed-boundary P3 proof through the real Unix daemon and client.

Only disposable ``/tmp`` roots and the pinned Ubuntu fixture are used.  The
test invokes real ``qemu-img`` but never launches QEMU, mounts an image, or
touches a configured operator AIPC.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
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
)
from somnus_vm.client import UnixControlClient  # noqa: E402
from somnus_vm.config import load_configuration  # noqa: E402
from somnus_vm.host.registry import RegistryMode, SQLiteRegistry  # noqa: E402
from somnus_vm.transport import TransportError  # noqa: E402
from test_storage_p3 import (  # noqa: E402
    FIXTURE_SOURCE,
    QEMU_IMG,
    VIRTUAL_SIZE,
    _manifest,
)


_TIMEOUT_SECONDS = 30.0


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


def _configuration_file(root: Path) -> Path:
    path = root / "daemon.toml"
    path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "p3-daemon-gate"',
                "",
                "[host]",
                'qemu_binary = "qemu-system-must-not-run-in-p3"',
                f'qemu_img_binary = "{QEMU_IMG}"',
                "enable_kvm = false",
                "",
                "[daemon]",
                f'runtime_root = "{root / "run"}"',
                f'control_socket = "{root / "run" / "control.sock"}"',
                "request_timeout_seconds = 30.0",
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
                "ssh_ports = [24000, 24063]",
                "agent_ports = [25000, 25063]",
                "vnc_ports = [6100, 6163]",
                "",
                "[agent]",
                "timeout_seconds = 30.0",
                "",
                "[security]",
                'credential_provider = "file"',
                f'secret_root = "{root / "secrets"}"',
                "guest_agent_token_file = "
                f'"{root / "secrets" / "agent.token"}"',
                "",
                "[policy]",
                "max_active_vms = 2",
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


class DaemonStorageP3Gate(unittest.TestCase):
    def _start(
        self,
        config_path: Path,
    ) -> tuple[subprocess.Popen[str], UnixControlClient]:
        configuration = load_configuration(config_path)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "somnus_vm.daemon_runtime",
                "--config",
                str(config_path),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        client = UnixControlClient(
            configuration.daemon.control_socket,
            timeout_seconds=5.0,
            allowed_server_uids={os.geteuid()},
        )
        deadline = time.monotonic() + _TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                response = client.request(_request("registry.status", {}))
            except TransportError:
                time.sleep(0.02)
                continue
            if isinstance(response, ControlResponse):
                return process, client
        process.kill()
        stdout, stderr = process.communicate(timeout=5.0)
        self.fail(
            "P3 daemon did not bind after physical reconciliation"
            f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )

    def _stop(self, process: subprocess.Popen[str]) -> None:
        process.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5.0)
            self.fail(
                "P3 daemon ignored SIGTERM"
                f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn("Traceback", stderr)

    @staticmethod
    def _success(response: object) -> ControlResponse:
        if not isinstance(response, ControlResponse):
            raise AssertionError(f"expected success, received {response!r}")
        return response

    def test_real_daemon_materializes_and_reconciles_two_overlays(self) -> None:
        self.assertTrue(FIXTURE_SOURCE.is_file())
        self.assertTrue(QEMU_IMG.is_file())
        manifest = _manifest()
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p3-daemon-"
        ) as raw:
            root = Path(raw).resolve()
            config_path = _configuration_file(root)
            configuration = load_configuration(config_path)
            process, client = self._start(config_path)
            try:
                imported = self._success(
                    client.request(
                        _request(
                            "storage.import_base",
                            {
                                "manifest": manifest.to_dict(),
                                "source_path": os.fspath(FIXTURE_SOURCE),
                            },
                        )
                    )
                )
                image_id = UUID(str(imported.result["registry_image_id"]))
                declared: list[tuple[UUID, int, int]] = []
                for name in ("daemon-aipc-a", "daemon-aipc-b"):
                    response = self._success(
                        client.request(
                            _request(
                                "registry.declare",
                                {
                                    "memory_mib": 4_096,
                                    "name": name,
                                    "vcpus": 2,
                                },
                            )
                        )
                    )
                    declared.append(
                        (
                            UUID(str(response.result["vm_id"])),
                            int(response.result["generation"]),
                            int(response.result["revision"]),
                        )
                    )
                for vm_id, generation, revision in declared:
                    self._success(
                        client.request(
                            _request(
                                "storage.create_overlay",
                                {
                                    "base_image_id": str(image_id),
                                    "expected_revision": revision,
                                    "virtual_size_bytes": VIRTUAL_SIZE,
                                },
                                vm_id=vm_id,
                                generation=generation,
                            )
                        )
                    )
            finally:
                self._stop(process)

            registry_path = configuration.storage.state_root / "registry.sqlite3"
            with SQLiteRegistry.open(
                registry_path,
                writer=False,
            ) as registry:
                self.assertIs(registry.status.mode, RegistryMode.READ_ONLY)
                bases = registry.list_base_images(active_only=True)
                disks = registry.list_disks(materialized_only=True)
                self.assertEqual(len(bases), 1)
                self.assertEqual(len(disks), 2)
                self.assertEqual(
                    {disk.base_image_id for disk in disks},
                    {bases[0].image_id},
                )
                self.assertEqual(
                    len({(disk.device_id, disk.inode) for disk in disks}),
                    2,
                )
                for disk in disks:
                    disk_path = Path(disk.path)
                    details = disk_path.stat(follow_symlinks=False)
                    self.assertTrue(stat.S_ISREG(details.st_mode))
                    self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
                    self.assertLess(
                        details.st_blocks * 512,
                        VIRTUAL_SIZE // 64,
                    )

            restarted, _ = self._start(config_path)
            self._stop(restarted)

            with SQLiteRegistry.open(
                registry_path,
                writer=False,
            ) as registry:
                victim = Path(
                    registry.list_disks(materialized_only=True)[0].path
                )
            original = victim.with_suffix(".registered-inode")
            victim.rename(original)
            shutil.copyfile(original, victim)
            os.chmod(victim, 0o600)
            self.assertNotEqual(
                victim.stat(follow_symlinks=False).st_ino,
                original.stat(follow_symlinks=False).st_ino,
            )

            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            rejected = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "somnus_vm.daemon_runtime",
                    "--config",
                    str(config_path),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = rejected.communicate(timeout=_TIMEOUT_SECONDS)
            self.assertNotEqual(rejected.returncode, 0, (stdout, stderr))
            self.assertFalse(configuration.daemon.control_socket.exists())
            self.assertIn("RECOVERY_REQUIRED", stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
