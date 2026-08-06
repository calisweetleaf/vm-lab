"""Direct P1 proof that the public host planner consumes canonical VM records.

This is intentionally a consumer proof, not a schema-only test: it exercises
the real ``VMPlanner`` and an isolated ``python -m somnus_vm plan`` subprocess.
Neither path is allowed to create a credential, reserve a port beyond the
probe, create host state, or expose a lifecycle command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol.vm import VMDefinition, VMPorts, VMRecord
from somnus_vm import VMRecord as RootVMRecord
from somnus_vm.config import load_configuration
from somnus_vm.contracts.vm import VMRecord as CompatibilityVMRecord
from somnus_vm.host.planner import VMPlanner


def _write_fixture_config(root: Path) -> Path:
    """Create an isolated config with broad high-port probe ranges."""

    config = root / "host-consumption.toml"
    config.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "host-consumption"',
                "",
                "[host]",
                f'state_dir = "{root / "state"}"',
                f'runtime_dir = "{root / "runtime"}"',
                f'base_image = "{root / "images/base.qcow2"}"',
                'qemu_binary = "qemu-system-x86_64"',
                'qemu_img_binary = "qemu-img"',
                "enable_kvm = false",
                "ssh_ports = [39000, 39127]",
                "agent_ports = [39200, 39327]",
                "vnc_ports = [5930, 6057]",
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
    return config


def _run_module(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Execute the packaged public command in an isolated child process."""

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from somnus_vm.cli import main; raise SystemExit(main(sys.argv[2:]))", str(SOURCE_ROOT), *arguments],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_host_protocol_consumption_checks() -> str:
    """Prove planner/CLI use canonical secret-free VM records end to end."""

    assert CompatibilityVMRecord is VMRecord
    assert RootVMRecord is VMRecord

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config_path = _write_fixture_config(root)
        config = load_configuration(config_path)
        planner = VMPlanner(config)
        record, plan = planner.preview(
            name="persistent-aipc",
            disk_path=root / "persistent-aipc.qcow2",
            memory_mib=4096,
            vcpus=2,
            enable_kvm=False,
        )
        assert type(record) is VMRecord
        assert record.definition == VMDefinition(
            name="persistent-aipc",
            disk_path=str((root / "persistent-aipc.qcow2").resolve()),
            memory_mib=4096,
            vcpus=2,
            enable_kvm=False,
        )
        assert type(record.ports) is VMPorts
        direct_payload = record.to_dict()
        assert direct_payload["schema_version"] == 1
        assert "agent_token" not in direct_payload
        assert VMRecord.from_dict(direct_payload).to_dict() == direct_payload
        assert str(record.vm_id) == plan.argv[plan.argv.index("-uuid") + 1]
        assert not config.host.state_dir.exists()
        assert not config.host.runtime_dir.exists()

        command = _run_module(
            [
                "--config",
                str(config_path),
                "plan",
                "--name",
                "public-persistent-aipc",
                "--disk",
                str(root / "public-persistent-aipc.qcow2"),
                "--tcg",
                "--json",
            ],
            root,
        )
        assert command.returncode == 0, command.stderr
        payload = json.loads(command.stdout)
        public_record = payload["record"]
        assert payload["ports_reserved"] is False
        assert public_record["schema_version"] == 1
        assert public_record["state"] == "declared"
        assert public_record["transitions"] == []
        assert public_record["migration_notes"] == []
        assert "agent_token" not in public_record
        assert "agent_token" not in command.stdout
        argv = payload["argv"]
        assert argv[argv.index("-uuid") + 1] == public_record["vm_id"]
        assert "-daemonize" not in argv
        assert not config.host.state_dir.exists()
        assert not config.host.runtime_dir.exists()

    return (
        "proved canonical VMRecord identity through compatibility imports and "
        "the real non-mutating planner/CLI; public plan output is versioned, "
        "secret-free, and leaves no host state or port reservation"
    )


if __name__ == "__main__":
    print(run_host_protocol_consumption_checks())
