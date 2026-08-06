"""Focused P4 proof for the pure, non-mutating QEMU command builder.

The checks consume the real planner and public CLI, but never execute the QEMU
binary.  They prove exact argv/path/hash intent only; they make no process,
QMP, guest, or lifecycle claim.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol.vm import VMDefinition, VMPorts, VMRecord
from somnus_vm.config import HostConfiguration, LabConfiguration, load_configuration
from somnus_vm.host.planner import VMPlanner
from somnus_vm.host.qemu import QemuCommandBuilder, QemuLaunchPlan, QemuPlanError
from somnus_vm.host.qemu_process import command_sha256


VM_ID = UUID("8e0be5ca-4c32-46fb-b657-4fe4abfd31e6")
SANDBOX_POLICY = (
    "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"
)


def _write_configuration(root: Path, *, enable_kvm: bool) -> Path:
    """Write one isolated legacy-compatible host profile."""

    path = root / ("host-kvm.toml" if enable_kvm else "host-tcg.toml")
    path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "p4-qemu-planning"',
                "",
                "[host]",
                f'state_dir = "{root / "state"}"',
                f'runtime_dir = "{root / "runtime"}"',
                f'base_image = "{root / "images/base.qcow2"}"',
                'qemu_binary = "qemu-system-x86_64"',
                'qemu_img_binary = "qemu-img"',
                f"enable_kvm = {'true' if enable_kvm else 'false'}",
                "ssh_ports = [43000, 43127]",
                "agent_ports = [43200, 43327]",
                "vnc_ports = [6000, 6127]",
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


def _record(
    disk_path: Path,
    *,
    enable_kvm: bool = False,
    vm_id: UUID = VM_ID,
) -> VMRecord:
    """Create one strict canonical VM record without runtime observations."""

    return VMRecord(
        definition=VMDefinition(
            name="persistent-aipc",
            disk_path=str(disk_path),
            memory_mib=4096,
            vcpus=2,
            enable_kvm=enable_kvm,
        ),
        ports=VMPorts(ssh=43001, agent=43201, vnc=6001),
        vm_id=vm_id,
    )


def _hash_argv(argv: tuple[str, ...] | list[str]) -> str:
    """Independently reproduce the plan's unambiguous command digest."""

    payload = json.dumps(
        list(argv),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _option_value(argv: tuple[str, ...] | list[str], option: str) -> str:
    """Return a required option value for direct assertions."""

    assert argv.count(option) == 1, (option, argv)
    index = argv.index(option)
    assert index + 1 < len(argv), (option, argv)
    return argv[index + 1]


def _expect_rejected(
    action: Callable[[], object],
    expected: type[BaseException],
) -> None:
    """Require one hostile construction to fail loudly."""

    try:
        action()
    except expected:
        return
    raise AssertionError(f"expected {expected.__name__}")


def _run_cli(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Consume the real public CLI in an isolated Python subprocess."""

    script = (
        "import sys; "
        "sys.path.insert(0, sys.argv[1]); "
        "from somnus_vm.cli import main; "
        "raise SystemExit(main(sys.argv[2:]))"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", script, str(SOURCE_ROOT), *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _assert_closed_p4_argv(
    plan: QemuLaunchPlan,
    configuration: HostConfiguration,
    disk_path: Path,
) -> None:
    """Assert the exact pre-P5 command membrane and canonical targets."""

    argv = plan.argv
    assert argv[0] == configuration.qemu_binary
    for flag in ("-no-user-config", "-nodefaults", "-no-reboot"):
        assert argv.count(flag) == 1
    assert "-daemonize" not in argv
    assert _option_value(argv, "-sandbox") == SANDBOX_POLICY
    assert _option_value(argv, "-display") == "none"
    assert _option_value(argv, "-nic") == "none"
    assert _option_value(argv, "-monitor") == "none"
    assert _option_value(argv, "-smp") == (
        "cpus=2,sockets=1,dies=1,cores=2,threads=1"
    )
    assert _option_value(argv, "-device") == (
        "virtio-blk-pci,id=somnus-root-disk,drive=somnus-disk"
    )
    for forbidden in (
        "-add-fd",
        "-fw_cfg",
        "-net",
        "-netdev",
        "-object",
        "-readconfig",
        "-spice",
        "-vnc",
        "-writeconfig",
    ):
        assert forbidden not in argv
    assert not any("hostfwd" in item for item in argv)
    assert not any("virtio-net" in item for item in argv)

    blockdev_text = _option_value(argv, "-blockdev")
    blockdev = json.loads(blockdev_text)
    assert blockdev == {
        "driver": "qcow2",
        "file": {"driver": "file", "filename": str(disk_path)},
        "node-name": "somnus-disk",
    }
    assert str(disk_path) not in argv
    assert _option_value(argv, "-qmp") == (
        f"unix:{plan.qmp_socket},server=on,wait=off"
    )
    assert _option_value(argv, "-pidfile") == str(plan.pid_file)
    assert _option_value(argv, "-serial") == "stdio"
    assert "-D" not in argv
    assert str(plan.serial_log_path) not in argv
    assert str(plan.qemu_log_path) not in argv
    assert plan.log_path == plan.serial_log_path
    assert plan.command_sha256 == _hash_argv(argv)
    assert plan.command_sha256 == command_sha256(argv)
    assert len(plan.command_sha256) == 64

    assert plan.qmp_socket == (
        configuration.daemon.runtime_root / f"{VM_ID}.qmp"
    )
    assert plan.pid_file == configuration.daemon.runtime_root / f"{VM_ID}.pid"
    assert plan.serial_log_path == (
        configuration.storage.log_root / f"{VM_ID}.log"
    )
    assert plan.qemu_log_path == (
        configuration.storage.log_root / f"{VM_ID}.qemu.log"
    )
    assert len(
        {
            plan.qmp_socket,
            plan.pid_file,
            plan.serial_log_path,
            plan.qemu_log_path,
        }
    ) == 4
    assert len(os.fsencode(plan.qmp_socket)) <= 103
    for path in (plan.pid_file, plan.serial_log_path, plan.qemu_log_path):
        assert len(os.fsencode(path)) <= 4096
    assert not any(
        any(ord(character) < 32 or ord(character) == 127 for character in item)
        for item in argv
    )


def check_hardened_deterministic_plan() -> str:
    """Prove exact headless, no-network argv and deterministic identity."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        lab = load_configuration(_write_configuration(root, enable_kvm=False))
        disk = root / "disk,λ with spaces;$(touch planner-pwned).qcow2"
        record = _record(disk)
        builder = QemuCommandBuilder(lab.host)
        first = builder.build(record)
        second = builder.build(record)

        assert first == second
        _assert_closed_p4_argv(first, lab.host, disk)
        argv_text = json.dumps(first.argv)
        assert str(lab.host.security.guest_agent_token_file) not in argv_text
        assert str(lab.host.security.secret_root) not in argv_text
        assert not (root / "planner-pwned").exists()
        assert not lab.host.state_dir.exists()
        assert not lab.host.runtime_dir.exists()
        assert not lab.host.storage.log_root.exists()

    return (
        "built deterministic headless no-network argv with JSON blockdev, "
        "closed sandbox policy, separate log targets, and an exact command hash"
    )


def check_runtime_binding_and_policy_rejection() -> str:
    """Prove canonical path binding, KVM policy, and hostile-plan rejection."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        tcg_lab = load_configuration(_write_configuration(root, enable_kvm=False))
        disk = root / "canonical-disk.qcow2"
        record = _record(disk)
        builder = QemuCommandBuilder(tcg_lab.host)
        plan = builder.build(record)

        bound = record.with_planned_runtime(
            qmp_socket=str(plan.qmp_socket),
            pid_file=str(plan.pid_file),
            log_path=str(plan.serial_log_path),
        )
        assert builder.build(bound) == plan

        mismatched = record.with_planned_runtime(
            qmp_socket=str(tcg_lab.host.runtime_dir / "other.qmp"),
            pid_file=str(plan.pid_file),
            log_path=str(plan.serial_log_path),
        )
        _expect_rejected(lambda: builder.build(mismatched), QemuPlanError)
        _expect_rejected(
            lambda: builder.build(_record(disk, enable_kvm=True)),
            QemuPlanError,
        )
        _expect_rejected(
            lambda: builder.build(
                _record(tcg_lab.host.security.guest_agent_token_file)
            ),
            QemuPlanError,
        )
        _expect_rejected(
            lambda: builder.build(_record(root / "nested" / ".." / "disk.qcow2")),
            QemuPlanError,
        )

        kvm_lab = load_configuration(_write_configuration(root, enable_kvm=True))
        kvm_plan = QemuCommandBuilder(kvm_lab.host).build(
            _record(disk, enable_kvm=True)
        )
        assert "-enable-kvm" in kvm_plan.argv
        assert _option_value(kvm_plan.argv, "-cpu") == "host"
        assert "accel=tcg" not in kvm_plan.argv
        tcg_under_kvm_profile = QemuCommandBuilder(kvm_lab.host).build(record)
        assert "-enable-kvm" not in tcg_under_kvm_profile.argv
        assert _option_value(tcg_under_kvm_profile.argv, "-machine") == "accel=tcg"

        _expect_rejected(
            lambda: replace(plan, command_sha256="0" * 64),
            QemuPlanError,
        )
        for hostile_socket in (
            root / "unsafe,socket.qmp",
            Path("/tmp/unsafe\nsocket.qmp"),
            Path("/") / ("é" * 52),
        ):
            _expect_rejected(
                lambda hostile_socket=hostile_socket: replace(
                    plan,
                    qmp_socket=hostile_socket,
                ),
                QemuPlanError,
            )
        hostile_argv = (*plan.argv, "-vnc", "127.0.0.1:0")
        _expect_rejected(
            lambda: replace(
                plan,
                argv=hostile_argv,
                command_sha256=_hash_argv(hostile_argv),
            ),
            QemuPlanError,
        )
        secret_argv = (*plan.argv, "-object", "secret,id=credential,data=value")
        _expect_rejected(
            lambda: replace(
                plan,
                argv=secret_argv,
                command_sha256=_hash_argv(secret_argv),
            ),
            QemuPlanError,
        )
        malformed_smp = tuple(
            "cpus=2"
            if item == "cpus=2,sockets=1,dies=1,cores=2,threads=1"
            else item
            for item in plan.argv
        )
        _expect_rejected(
            lambda: replace(
                plan,
                argv=malformed_smp,
                command_sha256=_hash_argv(malformed_smp),
            ),
            QemuPlanError,
        )
        anonymous_device = tuple(
            "virtio-blk-pci,drive=somnus-disk"
            if item
            == "virtio-blk-pci,id=somnus-root-disk,drive=somnus-disk"
            else item
            for item in plan.argv
        )
        _expect_rejected(
            lambda: replace(
                plan,
                argv=anonymous_device,
                command_sha256=_hash_argv(anonymous_device),
            ),
            QemuPlanError,
        )

    return (
        "bound runtime paths to host ownership, rejected KVM downgrade, secret "
        "roots, noncanonical paths, hash tampering, display, and secret options"
    )


def check_real_planner_and_cli_consumption() -> str:
    """Prove the real planner and CLI consume the hardened builder without QEMU."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        configuration_path = _write_configuration(root, enable_kvm=False)
        lab: LabConfiguration = load_configuration(configuration_path)
        planner_disk = root / "planner,comma.qcow2"
        record, plan = VMPlanner(lab).preview(
            name="planner-consumer",
            disk_path=planner_disk,
            enable_kvm=False,
        )
        assert record.qmp_socket == str(plan.qmp_socket)
        assert record.pid_file == str(plan.pid_file)
        assert record.log_path == str(plan.serial_log_path)
        assert record.ports.to_dict()
        assert not any("hostfwd" in item for item in plan.argv)

        cli_disk = root / "cli,$(touch cli-pwned).qcow2"
        command = _run_cli(
            [
                "--config",
                str(configuration_path),
                "plan",
                "--name",
                "cli-consumer",
                "--disk",
                str(cli_disk),
                "--tcg",
                "--json",
            ],
            root,
        )
        assert command.returncode == 0, command.stderr
        payload = json.loads(command.stdout)
        assert set(payload) == {"argv", "ports_reserved", "record"}
        assert payload["ports_reserved"] is False
        argv = payload["argv"]
        assert "-daemonize" not in argv
        assert _option_value(argv, "-display") == "none"
        assert _option_value(argv, "-nic") == "none"
        assert not any("hostfwd" in item for item in argv)
        assert json.loads(_option_value(argv, "-blockdev"))["file"]["filename"] == str(
            cli_disk
        )
        assert len(_hash_argv(argv)) == 64
        assert not (root / "cli-pwned").exists()
        assert not lab.host.state_dir.exists()
        assert not lab.host.runtime_dir.exists()
        assert not lab.host.storage.log_root.exists()

    return (
        "consumed the hardened plan through VMPlanner and the real public CLI "
        "without QEMU execution, state writes, reservations, or shell expansion"
    )


def run_qemu_planning_p4_checks() -> list[str]:
    """Run every focused P4 pure-planning check."""

    return [
        check_hardened_deterministic_plan(),
        check_runtime_binding_and_policy_rejection(),
        check_real_planner_and_cli_consumption(),
    ]


if __name__ == "__main__":
    for result in run_qemu_planning_p4_checks():
        print(result)
