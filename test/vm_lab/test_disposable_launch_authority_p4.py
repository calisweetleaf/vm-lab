"""Fail-closed physical proofs for disposable P4 launch authority.

These checks use only real private files and directories.  They deliberately do
not launch QEMU: a permit is the pre-launch safety boundary, not GATE-P4 proof.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_vm.config import HostConfiguration, load_configuration  # noqa: E402
from somnus_vm.host.launch_authority import (  # noqa: E402
    DisposableLaunchReceipt,
    FIXTURE_MARKER_NAME,
    FIXTURE_MARKER_SCHEMA_VERSION,
    LaunchAuthorityError,
    ProductionExclusion,
    mint_disposable_launch_permit,
)


_ROOT_NAMES = ("state", "runtime", "logs", "images", "backups", "secrets")


def _write_configuration(
    config_path: Path,
    fixture_root: Path,
    *,
    state_root: Path | None = None,
) -> HostConfiguration:
    """Write and load one real split-root fixture configuration."""

    selected_state_root = fixture_root / "state" if state_root is None else state_root
    config_path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "p4-disposable-launch-authority"',
                "",
                "[host]",
                'qemu_binary = "qemu-system-x86_64"',
                'qemu_img_binary = "qemu-img"',
                "enable_kvm = false",
                "ssh_ports = [46000, 46127]",
                "agent_ports = [46200, 46327]",
                "vnc_ports = [6400, 6527]",
                "agent_timeout_seconds = 2.0",
                "",
                "[daemon]",
                f'runtime_root = "{fixture_root / "runtime"}"',
                f'control_socket = "{fixture_root / "runtime" / "control.sock"}"',
                "",
                "[storage]",
                f'state_root = "{selected_state_root}"',
                f'image_root = "{fixture_root / "images"}"',
                f'base_image = "{fixture_root / "images" / "base.qcow2"}"',
                f'backup_root = "{fixture_root / "backups"}"',
                f'log_root = "{fixture_root / "logs"}"',
                "",
                "[security]",
                'credential_provider = "file"',
                f'secret_root = "{fixture_root / "secrets"}"',
                f'guest_agent_token_file = "{fixture_root / "secrets" / "guest-agent.token"}"',
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
    return load_configuration(config_path).host


class DisposableLaunchAuthorityP4Tests(unittest.TestCase):
    """Use a newly-created canonical fixture topology for every proof."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-disposable-authority-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()
        self.fixture_root = self.root / "fixture"
        self.fixture_root.mkdir(mode=0o700)
        os.chmod(self.fixture_root, 0o700)
        self.configuration = _write_configuration(
            self.root / "host.toml",
            self.fixture_root,
        )
        self._create_topology(self.configuration)
        self.run_id = uuid4()
        self.vm_id = uuid4()
        self.disk_id = uuid4()
        self.generation = 1
        self.base_sha256 = hashlib.sha256(b"immutable-fixture-base").hexdigest()
        self.disk_path = (
            self.configuration.storage.state_root
            / "vms"
            / str(self.vm_id)
            / "storage"
            / "aipc.qcow2"
        )
        self.disk_path.parent.mkdir(mode=0o700, parents=True)
        self._chmod_directories(self.configuration.storage.state_root)
        self.disk_path.write_bytes(b"private fixture overlay")
        os.chmod(self.disk_path, 0o600)
        self._write_marker()
        self.production_path = self.root / "production-aipc.qcow2"
        self.production_path.write_bytes(b"not a fixture")
        os.chmod(self.production_path, 0o600)
        self.production_exclusions = (
            ProductionExclusion.observe("operator-production-disk", self.production_path),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_topology(self, configuration: HostConfiguration) -> None:
        for root in (
            configuration.storage.state_root,
            configuration.daemon.runtime_root,
            configuration.storage.log_root,
            configuration.storage.image_root,
            configuration.storage.backup_root,
            configuration.security.secret_root,
        ):
            root.mkdir(mode=0o700, parents=True)
            os.chmod(root, 0o700)
        configuration.storage.base_image.write_bytes(b"immutable-fixture-base")
        os.chmod(configuration.storage.base_image, 0o600)

    @staticmethod
    def _chmod_directories(root: Path) -> None:
        for directory in (root, *root.rglob("*")):
            if directory.is_dir():
                os.chmod(directory, 0o700)

    def _write_marker(self, *, created_at: str = "2026-08-07T00:00:00Z") -> None:
        marker = self.fixture_root / FIXTURE_MARKER_NAME
        payload = {
            "base_image_sha256": self.base_sha256,
            "created_at": created_at,
            "owner": f"uid:{os.geteuid()}",
            "permitted_cleanup_root": os.fspath(self.fixture_root),
            "run_id": str(self.run_id),
            "schema_version": FIXTURE_MARKER_SCHEMA_VERSION,
            "vm_id": str(self.vm_id),
        }
        marker.write_bytes(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        os.chmod(marker, 0o600)

    def _mint(self, **overrides: object):
        values: dict[str, object] = {
            "configuration": self.configuration,
            "fixture_root": self.fixture_root,
            "run_id": self.run_id,
            "vm_id": self.vm_id,
            "generation": self.generation,
            "disk_id": self.disk_id,
            "disk_path": self.disk_path,
            "base_sha256": self.base_sha256,
            "production_exclusions": self.production_exclusions,
        }
        values.update(overrides)
        return mint_disposable_launch_permit(**values)  # type: ignore[arg-type]

    def _consume(self, permit: object, **overrides: object) -> DisposableLaunchReceipt:
        values: dict[str, object] = {
            "configuration": self.configuration,
            "vm_id": self.vm_id,
            "generation": self.generation,
            "disk_id": self.disk_id,
            "disk_path": self.disk_path,
            "base_sha256": self.base_sha256,
        }
        values.update(overrides)
        return permit.revalidate_and_consume(**values)  # type: ignore[union-attr,no-any-return]

    def test_mints_exact_one_shot_permit_and_binds_all_launch_facts(self) -> None:
        permit = self._mint()

        self.assertEqual(permit.run_id, self.run_id)
        self.assertEqual(permit.vm_id, self.vm_id)
        self.assertEqual(permit.generation, self.generation)
        self.assertEqual(permit.disk_id, self.disk_id)
        self.assertEqual(permit.disk.path, self.disk_path)
        self.assertEqual(permit.base_sha256, self.base_sha256)
        self.assertEqual(
            {identity.role for identity in permit.roots},
            {
                "state root",
                "runtime root",
                "log root",
                "image root",
                "backup root",
                "secret root",
            },
        )
        self.assertFalse(permit.consumed)

        receipt = self._consume(permit)

        self.assertTrue(permit.consumed)
        self.assertEqual(receipt.permit_id, permit.permit_id)
        self.assertEqual(receipt.vm_id, self.vm_id)
        self.assertEqual(receipt.disk_id, self.disk_id)
        self.assertEqual(receipt.marker_sha256, permit.marker_sha256)
        self.assertEqual(
            DisposableLaunchReceipt.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertEqual(len(receipt.evidence_sha256), 64)
        with self.assertRaises(LaunchAuthorityError):
            self._consume(permit)

    def test_rejects_symlink_hardlink_and_configured_root_escape(self) -> None:
        fixture_alias = self.root / "fixture-alias"
        fixture_alias.symlink_to(self.fixture_root, target_is_directory=True)
        with self.assertRaises(LaunchAuthorityError):
            self._mint(fixture_root=fixture_alias)

        disk_alias = self.root / "disk-hardlink.qcow2"
        os.link(self.disk_path, disk_alias)
        with self.assertRaises(LaunchAuthorityError):
            self._mint()
        disk_alias.unlink()

        outside_state = self.root / "outside-state"
        outside_state.mkdir(mode=0o700)
        os.chmod(outside_state, 0o700)
        escaped_configuration = _write_configuration(
            self.root / "escaped.toml",
            self.fixture_root,
            state_root=outside_state,
        )
        with self.assertRaises(LaunchAuthorityError):
            self._mint(configuration=escaped_configuration)

    def test_rejects_explicit_production_path_equality_and_ancestor_overlap(self) -> None:
        disk_exclusion = ProductionExclusion.observe(
            "operator-production-disk-alias",
            self.disk_path,
        )
        with self.assertRaises(LaunchAuthorityError):
            self._mint(production_exclusions=(disk_exclusion,))

        fixture_ancestor = ProductionExclusion.observe(
            "operator-production-root",
            self.root,
        )
        with self.assertRaises(LaunchAuthorityError):
            self._mint(production_exclusions=(fixture_ancestor,))

    def test_revalidation_burns_permit_on_hostile_marker_or_disk_replacement(self) -> None:
        marker_permit = self._mint()
        self._write_marker(created_at="2026-08-07T00:00:01Z")
        with self.assertRaises(LaunchAuthorityError):
            self._consume(marker_permit)
        self.assertTrue(marker_permit.consumed)
        with self.assertRaises(LaunchAuthorityError):
            self._consume(marker_permit)

        self._write_marker()
        disk_permit = self._mint()
        replacement = self.disk_path.with_name("replacement.qcow2")
        replacement.write_bytes(b"replacement overlay")
        os.chmod(replacement, 0o600)
        replacement.replace(self.disk_path)
        with self.assertRaises(LaunchAuthorityError):
            self._consume(disk_permit)
        self.assertTrue(disk_permit.consumed)

    def test_revalidation_rejects_changed_configuration_root_identity(self) -> None:
        permit = self._mint()
        replacement_state_root = self.root / "replacement-state"
        replacement_state_root.mkdir(mode=0o700)
        os.chmod(replacement_state_root, 0o700)
        changed_configuration = replace(
            self.configuration,
            storage=replace(
                self.configuration.storage,
                state_root=replacement_state_root,
            ),
        )

        with self.assertRaises(LaunchAuthorityError):
            self._consume(permit, configuration=changed_configuration)

        self.assertTrue(permit.consumed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
