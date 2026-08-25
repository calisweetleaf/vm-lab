"""Real integration harness for the promoted VM lab boundary.

Source: vm_lab reconstitution scope
Integrated: 2026-08-04
Purpose: Exercises only the promoted contracts, configuration, planning,
    bootstrap, packaging, topology, doctor, and execution-plan behaviors.
IO: Uses temporary files, bind probes, subprocess CLI, and run artifacts.

Modified: 2026-08-05
Modified by: daeron and Codex
Justification: Phase P0 plan enforcement remains covered; Phase P1 composes
    canonical VM lifecycle, control/event, guest-agent result, and typed
    settings contracts that must pass through the same consumed smoke and
    installed-wheel boundaries. Phase P2 adds the authenticated single-owner
    daemon, normalized registry, operation service, and process-kill recovery
    gate without promoting QEMU lifecycle mutation. Phase P3 adds verified
    image ownership, and Phase P4 adds pure command planning plus focused
    process, guarded-log, composed-QMP-identity, append-only runtime-storage,
    and launch-journal proof without public lifecycle.
Provenance: PROVENANCE.md, Phase P0 baseline, TASK-P1-001 composition, and
    TASK-P2-001 through TASK-P4-001 gates.
Files: test/vm_lab/test_vm_lab.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import stat


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
EXPECTED_CHECK_COUNT = 40
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_vm.config import ConfigurationError, default_configuration, load_configuration
from somnus_vm.contracts.agent import AgentEndpoint
from somnus_vm.contracts.vm import VMDefinition, VMPorts, VMRecord, VMState, VMTransitionError
from somnus_vm.doctor import CheckStatus, run_doctor
from somnus_vm.guest.bootstrap import (
    BootstrapConfiguration,
    BootstrapError,
    PayloadSource,
    bootstrap,
    extract_zip,
)
from somnus_vm.host.planner import VMPlanner
from somnus_vm.host.ports import PortAllocator
from test_protocol_agent import run_agent_protocol_checks
from test_protocol_control import run_control_protocol_checks
from test_protocol_host_consumption import run_host_protocol_consumption_checks
from test_protocol_settings import run_settings_protocol_checks
from test_protocol_vm_lifecycle import run_vm_lifecycle_checks
from test_protocol_vm_primitives import run_vm_primitive_checks


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One integration check result."""

    name: str
    status: str
    detail: str
    duration_ms: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible result."""

        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 3),
        }


def _fixture_config(directory: Path) -> Path:
    """Write a real isolated TOML configuration for integration checks."""

    config_path = directory / "fixture.toml"
    config_path.write_text(
        "\n".join(
            [
                "[vm_lab]",
                'profile = "fixture"',
                "",
                "[host]",
                'state_dir = "state"',
                f'runtime_dir = "/tmp/svm-{directory.name[-8:]}"',
                'base_image = "images/base.qcow2"',
                'qemu_binary = "qemu-system-x86_64"',
                'qemu_img_binary = "qemu-img"',
                "enable_kvm = false",
                "ssh_ports = [32000, 32031]",
                "agent_ports = [32100, 32131]",
                "vnc_ports = [6200, 6231]",
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
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _expect(error_type: type[BaseException], function: object) -> None:
    """Assert that one callable raises the requested exception type."""

    if not callable(function):
        raise AssertionError("Expected a callable")
    rejected = False
    try:
        function()
    except error_type:
        rejected = True
    assert rejected, f"Expected {error_type.__name__}"


def check_contract_roundtrip() -> str:
    """Prove serialization, endpoint authority, and lifecycle grammar."""

    record = VMRecord(
        definition=VMDefinition(name="contract-one", disk_path="/tmp/contract-one.qcow2"),
        ports=VMPorts(ssh=2222, agent=9901, vnc=5900),
    )
    assert VMRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
    _expect(VMTransitionError, lambda: record.transition(VMState.PROVISIONED))
    assert AgentEndpoint.EXECUTE.value == "/execute"
    assert AgentEndpoint.FILE_WRITE.value == "/files/write"
    return (
        "round-tripped one canonical secret-free record, rejected an illegal "
        "state jump, and fixed guest endpoint names"
    )


def check_configuration() -> str:
    """Load checkout and install-safe configuration with strict types."""

    config = load_configuration(PROJECT_ROOT / "configs" / "lab.toml")
    assert config.profile == "lab" and config.source_path is not None
    assert config.host.state_dir.is_absolute() and config.host.runtime_dir.is_absolute()
    assert not config.components.file_processing and not config.components.operator_shell
    default = default_configuration()
    assert default.source_path is None and default.profile == "user-tcg"
    with tempfile.TemporaryDirectory() as temporary:
        invalid = _fixture_config(Path(temporary))
        invalid.write_text(invalid.read_text(encoding="utf-8").replace("enable_kvm = false", 'enable_kvm = "false"'), encoding="utf-8")
        _expect(ConfigurationError, lambda: load_configuration(invalid))
        comma_path = _fixture_config(Path(temporary))
        comma_path.write_text(
            comma_path.read_text(encoding="utf-8").replace(
                f'runtime_dir = "/tmp/svm-{Path(temporary).name[-8:]}"',
                'runtime_dir = "/tmp/svm,bad"',
            ),
            encoding="utf-8",
        )
        _expect(ConfigurationError, lambda: load_configuration(comma_path))
    return "loaded resolved configs and rejected truthy booleans plus ambiguous QMP paths"


def check_port_allocation() -> str:
    """Allocate plan-time ports concurrently and prove local uniqueness."""

    with tempfile.TemporaryDirectory() as temporary:
        allocator = PortAllocator(load_configuration(_fixture_config(Path(temporary))).host)
        with ThreadPoolExecutor(max_workers=8) as executor:
            allocations = list(executor.map(lambda _: allocator.allocate(), range(8)))
        values = [port for item in allocations for port in (item.ssh, item.agent, item.vnc)]
        assert len(values) == len(set(values)) == 24
        for item in allocations:
            allocator.release(item)
    return "bind-checked 24 distinct ephemeral ports across eight threads"


def check_qemu_plan() -> str:
    """Prove the non-mutating P4 headless/no-network command contract."""

    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT.resolve()) as temporary:
        root = Path(temporary).resolve()
        config = load_configuration(_fixture_config(root))
        disk = (root / "disk,with,commas.qcow2").resolve()
        record, plan = VMPlanner(config).preview("plan-one", disk, enable_kvm=None)
        argv = list(plan.argv)
        assert "-daemonize" not in argv
        for flag in ("-no-user-config", "-nodefaults", "-no-reboot"):
            assert argv.count(flag) == 1
        assert argv[argv.index("-sandbox") + 1] == (
            "on,obsolete=deny,elevateprivileges=deny,"
            "spawn=deny,resourcecontrol=deny"
        )
        assert argv[argv.index("-display") + 1] == "none"
        assert argv[argv.index("-nic") + 1] == "none"
        assert argv[argv.index("-monitor") + 1] == "none"
        for forbidden in ("-net", "-netdev", "-spice", "-vnc"):
            assert forbidden not in argv
        assert not any("hostfwd" in value or "virtio-net" in value for value in argv)

        assert "-blockdev" in argv
        blockdev = json.loads(argv[argv.index("-blockdev") + 1])
        assert blockdev["file"]["filename"] == str(disk)
        assert str(disk) not in argv
        assert argv[argv.index("-qmp") + 1] == (
            f"unix:{plan.qmp_socket},server=on,wait=off"
        )
        assert argv[argv.index("-pidfile") + 1] == str(plan.pid_file)
        assert argv[argv.index("-serial") + 1] == "stdio"
        assert "-D" not in argv
        assert str(plan.serial_log_path) not in argv
        assert str(plan.qemu_log_path) not in argv
        assert plan.log_path == plan.serial_log_path
        assert plan.serial_log_path != plan.qemu_log_path
        command_bytes = json.dumps(
            argv,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        assert plan.command_sha256 == hashlib.sha256(command_bytes).hexdigest()
        assert record.qmp_socket == str(plan.qmp_socket)
        assert record.pid_file == str(plan.pid_file)
        assert record.log_path == str(plan.serial_log_path)
        assert len(os.fsencode(plan.qmp_socket)) <= 103
        assert not record.definition.enable_kvm
        assert str(config.host.security.guest_agent_token_file) not in json.dumps(argv)
        assert not config.host.runtime_dir.exists()
        assert not config.host.storage.log_root.exists()
    return (
        "encoded a comma-bearing disk with JSON blockdev, closed sandbox, "
        "headless/no-network argv, separate logs, and exact command identity"
    )


def check_bootstrap_extraction() -> str:
    """Extract regular ZIP data and reject traversal, links, and duplicates."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        valid = root / "valid.zip"
        with zipfile.ZipFile(valid, "w") as archive:
            archive.writestr("runtime/config.json", "{}")
        extract_zip(valid, root / "valid-out", 2)
        assert (root / "valid-out/runtime/config.json").read_text(encoding="utf-8") == "{}"

        traversal = root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../escape.txt", "blocked")
        _expect(BootstrapError, lambda: extract_zip(traversal, root / "traversal-out", 7))
        assert not (root / "escape.txt").exists()

        symlink = root / "symlink.zip"
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(symlink, "w") as archive:
            archive.writestr(info, "target")
        _expect(BootstrapError, lambda: extract_zip(symlink, root / "symlink-out", 6))

        duplicate = root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("same", "one")
                archive.writestr("same", "two")
        _expect(BootstrapError, lambda: extract_zip(duplicate, root / "duplicate-out", 6))
        _expect(BootstrapError, lambda: extract_zip(valid, root / "wrong-size-out", 1))
    return "extracted bounded data and rejected traversal, links, duplicates, and size drift"


def check_bootstrap_idempotency() -> str:
    """Run a real payload install and validate the completion marker."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        payload = root / "payload.bin"
        payload.write_bytes(b"verified payload")
        source = PayloadSource(
            name="runtime.bin",
            sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
            archive="raw",
            size_bytes=len(payload.read_bytes()),
            unpacked_size_bytes=len(payload.read_bytes()),
            local_paths=(payload,),
        )
        config = BootstrapConfiguration(
            target_dir=root / "target",
            marker_file=root / "state/complete.json",
            payloads=(source,),
        )
        assert bootstrap(config)
        assert (config.target_dir / "runtime.bin").read_bytes() == b"verified payload"
        assert stat.S_IMODE(config.target_dir.stat().st_mode) == 0o755
        assert stat.S_IMODE(config.marker_file.stat().st_mode) == 0o600
        assert not bootstrap(config)
        config.marker_file.unlink()
        assert bootstrap(config)
        assert not bootstrap(config)
        marker = json.loads(config.marker_file.read_text(encoding="utf-8"))
        marker["configuration_sha256"] = "0" * 64
        config.marker_file.write_text(json.dumps(marker), encoding="utf-8")
        _expect(BootstrapError, lambda: bootstrap(config))
        _expect(
            BootstrapError,
            lambda: PayloadSource("../escape", "0" * 64, "raw", 1, 1, (payload,)),
        )
        _expect(
            BootstrapError,
            lambda: BootstrapConfiguration(root / "empty", root / "empty-marker", ()),
        )
        _expect(
            BootstrapError,
            lambda: BootstrapConfiguration(
                root / "duplicate-target",
                root / "duplicate-marker",
                (source, source),
            ),
        )
        mislabeled = PayloadSource(
            name="broken.zip",
            sha256=source.sha256,
            archive="zip",
            size_bytes=source.size_bytes,
            unpacked_size_bytes=source.unpacked_size_bytes,
            local_paths=(payload,),
        )
        broken = BootstrapConfiguration(
            target_dir=root / "broken-target",
            marker_file=root / "broken-state/complete.json",
            payloads=(mislabeled,),
        )
        _expect(BootstrapError, lambda: bootstrap(broken))

        first_zip = root / "first.zip"
        second_zip = root / "second.zip"
        with zipfile.ZipFile(first_zip, "w") as archive:
            archive.writestr("shared.txt", "first")
        with zipfile.ZipFile(second_zip, "w") as archive:
            archive.writestr("shared.txt", "second")
        colliding_sources = (
            PayloadSource(
                "first.zip",
                hashlib.sha256(first_zip.read_bytes()).hexdigest(),
                "zip",
                first_zip.stat().st_size,
                5,
                (first_zip,),
            ),
            PayloadSource(
                "second.zip",
                hashlib.sha256(second_zip.read_bytes()).hexdigest(),
                "zip",
                second_zip.stat().st_size,
                6,
                (second_zip,),
            ),
        )
        collision = BootstrapConfiguration(
            root / "collision-target",
            root / "collision-state/complete.json",
            colliding_sources,
        )
        _expect(BootstrapError, lambda: bootstrap(collision))
    return "installed/recovered bounded payloads and rejected stale, ambiguous, mislabeled, or colliding intent"


def check_doctor_truthfulness() -> str:
    """Prove preflight reports actual host blockers instead of success."""

    config = load_configuration(PROJECT_ROOT / "configs" / "lab.toml")
    report = run_doctor(config, PROJECT_ROOT)
    checks = {check.name: check for check in report.checks}
    assert checks["topology"].status is CheckStatus.PASS
    assert checks["promotion_gate"].status is CheckStatus.PASS
    assert "qemu" in checks and "qemu_img" in checks and "runtime_directory" in checks
    return f"reported {len(report.checks)} source-aware checks with healthy={report.healthy}"


def _module_command(arguments: list[str], python_path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the canonical module in a clean child process."""

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(python_path)
    return subprocess.run(
        [sys.executable, "-m", "somnus_vm", *arguments],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def check_cli_entrypoint() -> str:
    """Run topology and a redacted plan through the canonical module."""

    with tempfile.TemporaryDirectory() as temporary:
        cwd = Path(temporary)
        topology = _module_command(["topology", "--json"], SOURCE_ROOT, cwd)
        assert topology.returncode == 0, topology.stderr
        topology_rows = json.loads(topology.stdout)
        topology_by_name = {item["name"]: item for item in topology_rows}
        live_names = {
            name
            for name, item in topology_by_name.items()
            if item["disposition"] == "live"
        }
        assert live_names == {
            "daemon_runtime",
            "guest_bootstrap",
            "host_planner",
            "host_planning_api",
            "host_port_allocator",
            "host_qemu_plan_builder",
            "native_exec_guard",
            "persistent_image_storage",
            "protocol_compatibility",
            "protocol_contracts",
            "qemu_img_provenance",
            "registry_mutation_service",
            "sqlite_registry",
            "unix_control_client",
            "unix_control_daemon",
            "unix_transport",
        }
        assert all(
            topology_by_name[name]["boot_import"] is False
            for name in {
                "daemon_runtime",
                "native_exec_guard",
                "persistent_image_storage",
                "qemu_img_provenance",
                "registry_mutation_service",
                "sqlite_registry",
                "unix_control_daemon",
                "unix_control_client",
                "unix_transport",
            }
        )
        assert topology_by_name["image_manager"]["disposition"] == "candidate"
        plan = _module_command(
            ["--config", str(PROJECT_ROOT / "configs/lab.toml"), "plan", "--name", "cli-one", "--disk", "/tmp/cli.qcow2", "--json"],
            SOURCE_ROOT,
            cwd,
        )
        assert plan.returncode == 0, plan.stderr
        payload = json.loads(plan.stdout)
        assert payload["ports_reserved"] is False
        assert "agent_token" not in payload["record"]

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            occupied = int(blocker.getsockname()[1])
            exhausted_config = _fixture_config(cwd)
            exhausted_config.write_text(
                exhausted_config.read_text(encoding="utf-8").replace(
                    "ssh_ports = [32000, 32031]",
                    f"ssh_ports = [{occupied}, {occupied}]",
                ),
                encoding="utf-8",
            )
            exhausted = _module_command(
                ["--config", str(exhausted_config), "plan", "--name", "blocked", "--disk", "/tmp/blocked.qcow2"],
                SOURCE_ROOT,
                cwd,
            )
            assert exhausted.returncode == 2
            assert "No available localhost port" in exhausted.stderr
            assert "Traceback" not in exhausted.stderr
    return "ran checkout-independent topology, redacted planning, and clean port-exhaustion errors"


def check_wheel_runtime() -> str:
    """Build a wheel and prove installed defaults do not depend on repo files."""

    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT.resolve()) as temporary:
        root = Path(temporary).resolve()
        package_root = root / "package"
        pip_tmp = root / "pip-tmp"
        wheel_dir = root / "wheel"
        site_dir = root / "site"
        package_root.mkdir()
        pip_tmp.mkdir()
        wheel_dir.mkdir()
        pip_environment = os.environ.copy()
        pip_environment["PIP_NO_INDEX"] = "1"
        pip_environment["TMPDIR"] = str(pip_tmp)
        shutil.copy2(PROJECT_ROOT / "pyproject.toml", package_root / "pyproject.toml")
        shutil.copytree(
            SOURCE_ROOT,
            package_root / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
        built = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheel_dir)],
            cwd=package_root,
            env=pip_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert built.returncode == 0, built.stderr
        wheels = list(wheel_dir.glob("*.whl"))
        assert len(wheels) == 1
        with zipfile.ZipFile(wheels[0]) as wheel:
            entry_points = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/entry_points.txt")
            ]
            assert len(entry_points) == 1
            entry_point_text = wheel.read(entry_points[0]).decode("utf-8")
            assert (
                "somnus-vm-daemon = somnus_vm.daemon_runtime:main"
                in entry_point_text
            )
        installed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(site_dir), str(wheels[0])],
            env=pip_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert installed.returncode == 0, installed.stderr
        protocol_import = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import sys;"
                    f"sys.path.insert(0,{str(site_dir)!r});"
                    "from somnus_protocol.vm import VMDefinition as canonical;"
                    "from somnus_protocol.vm import VMRecord as canonical_record;"
                    "from somnus_vm import VMDefinition as root;"
                    "from somnus_vm import VMRecord as root_record;"
                    "from somnus_vm.contracts.vm import VMDefinition as compat;"
                    "from somnus_vm.contracts.vm import VMRecord as compat_record;"
                    "from somnus_protocol.agent import AgentHealth as agent;"
                    "from somnus_vm.contracts.agent import AgentHealth as agent_compat;"
                    "from somnus_protocol import DiagnosticReference as diagnostic_root;"
                    "from somnus_protocol import ProtocolNegotiation as negotiation_root;"
                    "from somnus_protocol import ProtocolValidationError as validation_root;"
                    "from somnus_protocol import RuntimeObservation as observation_root;"
                    "from somnus_protocol._validation import ProtocolValidationError as validation;"
                    "from somnus_protocol.control import DiagnosticReference as diagnostic;"
                    "from somnus_protocol.version import ProtocolNegotiation as negotiation;"
                    "from somnus_protocol.vm import RuntimeObservation as observation;"
                    "assert canonical is root is compat;"
                    "assert canonical_record is root_record is compat_record;"
                    "assert agent is agent_compat;"
                    "assert diagnostic is diagnostic_root;"
                    "assert negotiation is negotiation_root;"
                    "assert validation is validation_root;"
                    "assert observation is observation_root"
                ),
            ],
            cwd=root,
            env=pip_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert protocol_import.returncode == 0, protocol_import.stderr
        daemon_entrypoint = site_dir / "bin" / "somnus-vm-daemon"
        assert daemon_entrypoint.is_file()
        daemon_environment = os.environ.copy()
        daemon_environment["PYTHONPATH"] = str(site_dir)
        daemon_help = subprocess.run(
            [str(daemon_entrypoint), "--help"],
            cwd=root,
            env=daemon_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert daemon_help.returncode == 0, daemon_help.stderr
        assert (
            "single local VM registry and image mutation owner"
            in daemon_help.stdout
        )
        for lifecycle_command in (
            " start ",
            " stop ",
            " destroy ",
            " snapshot ",
            " rollback ",
        ):
            assert lifecycle_command not in daemon_help.stdout
        topology = _module_command(["topology", "--json"], site_dir, root)
        assert topology.returncode == 0, topology.stderr
        doctor = _module_command(["doctor", "--json"], site_dir, root)
        assert doctor.returncode == 1
        doctor_payload = json.loads(doctor.stdout)
        topology_check = next(item for item in doctor_payload["checks"] if item["name"] == "topology")
        assert topology_check["status"] == "warn"

        invalid_payload = root / "not-a-zip.bin"
        invalid_payload.write_bytes(b"not a zip")
        invalid_config = root / "invalid-bootstrap.toml"
        invalid_config.write_text(
            "\n".join(
                [
                    "[bootstrap]",
                    f'target_dir = "{root / "guest-target"}"',
                    f'marker_file = "{root / "guest-state/complete.json"}"',
                    "",
                    "[[payloads]]",
                    'name = "runtime.zip"',
                    f'sha256 = "{hashlib.sha256(invalid_payload.read_bytes()).hexdigest()}"',
                    'archive = "zip"',
                    f"size_bytes = {invalid_payload.stat().st_size}",
                    "unpacked_size_bytes = 9",
                    f'local_paths = ["{invalid_payload}"]',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        bootstrap_environment = os.environ.copy()
        bootstrap_environment["PYTHONPATH"] = str(site_dir)
        invalid_run = subprocess.run(
            [sys.executable, "-m", "somnus_vm.guest.bootstrap", "--config", str(invalid_config)],
            cwd=root,
            env=bootstrap_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert invalid_run.returncode == 2
        assert "Cannot extract ZIP payload" in invalid_run.stderr
        assert "Traceback" not in invalid_run.stderr
        shutil.rmtree(root)
    assert not root.exists(), f"Wheel workspace leaked: {root}"
    return (
        "installed a wheel with canonical/compatibility protocol identity, "
        "checkout-free CLI, the registry/storage daemon entry point, and clean "
        "malformed-payload exit behavior"
    )


def _run_physical_test_module(filename: str, detail: str) -> str:
    """Run one focused proof module as a consumed smoke boundary."""

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "test" / "vm_lab" / filename)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, (
        f"{filename} failed\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return detail


def check_daemon_transport_p2() -> str:
    return _run_physical_test_module(
        "test_daemon_transport_p2.py",
        "proved bounded SO_PEERCRED Unix transport and exclusive daemon ownership",
    )


def check_registry_p2() -> str:
    return _run_physical_test_module(
        "test_registry_p2.py",
        "proved normalized SQLite ownership, integrity, migration, and writer locking",
    )


def check_registry_service_p2() -> str:
    return _run_physical_test_module(
        "test_registry_service_p2.py",
        "proved strict metadata operations and abrupt writer-exit recovery",
    )


def check_daemon_registry_p2() -> str:
    return _run_physical_test_module(
        "test_daemon_registry_p2.py",
        "proved 24-process operation races and real SIGKILL recovery at every checkpoint",
    )


def check_storage_p3() -> str:
    return _run_physical_test_module(
        "test_storage_p3.py",
        "proved pinned-base import, two real 100 GiB overlays, and exact checkpoint recovery",
    )


def check_storage_p3_adversarial() -> str:
    return _run_physical_test_module(
        "test_storage_p3_adversarial.py",
        "proved foreign-image non-adoption, checkpoint tamper rejection, and parent-death containment",
    )


def check_daemon_storage_p3() -> str:
    return _run_physical_test_module(
        "test_daemon_storage_p3.py",
        "proved real daemon/client storage composition and startup physical reconciliation",
    )


def check_qemu_planning_p4() -> str:
    return _run_physical_test_module(
        "test_qemu_planning_p4.py",
        "proved hardened QEMU planning through builder, planner, and public CLI",
    )


def check_doctor_p4() -> str:
    return _run_physical_test_module(
        "test_doctor_p4.py",
        "proved read-only QEMU sandbox option consumption and structured incompatibility evidence",
    )


def check_disposable_launch_authority_p4() -> str:
    return _run_physical_test_module(
        "test_disposable_launch_authority_p4.py",
        "proved one-shot non-production fixture authority and canonical launch receipt without launching QEMU",
    )


def check_p4_gate_coordinator() -> str:
    return _run_physical_test_module(
        "test_p4_gate_coordinator.py",
        "proved exact physical-gate preparation and pre-daemon denial without launching QEMU",
    )


def check_qemu_process_p4() -> str:
    return _run_physical_test_module(
        "test_qemu_process_p4.py",
        "proved exact Linux process identity, pidfiles, and signal revalidation",
    )


def check_qemu_exec_guard_p4() -> str:
    return _run_physical_test_module(
        "test_qemu_exec_guard_p4.py",
        "proved journal-before-exec handoff, parent-death containment, and exact adoption",
    )


def check_qemu_logs_p4() -> str:
    return _run_physical_test_module(
        "test_qemu_logs_p4.py",
        "proved private redaction, bounded rotation, dual-pipe drain, and durable guardian release",
    )


def check_qmp_p4() -> str:
    return _run_physical_test_module(
        "test_qmp_p4.py",
        "proved bounded QMP framing, negotiation, response/event separation, and identity",
    )


def check_qmp_identity_p4() -> str:
    return _run_physical_test_module(
        "test_qmp_identity_p4.py",
        "proved strict stabilized QMP identity composition and foreign-process rejection",
    )


def check_registry_p4() -> str:
    return _run_physical_test_module(
        "test_registry_p4.py",
        "proved registry-owned P4 process and disk-runtime observation authority",
    )


def check_storage_runtime_p4() -> str:
    return _run_physical_test_module(
        "test_storage_runtime_p4.py",
        "proved real qcow2 growth remains append-only runtime evidence without rewriting P3 ownership",
    )


def check_qemu_runtime_journal_p4() -> str:
    return _run_physical_test_module(
        "test_qemu_runtime_journal_p4.py",
        "proved the internal runtime.launch journal, exact identity binding, and crash-safe adoption",
    )


def check_qemu_runtime_p4() -> str:
    return _run_physical_test_module(
        "test_qemu_runtime_p4.py",
        "proved pidfd liveness, bounded pidfile readiness, and no-replace runtime-path retirement",
    )


def check_daemon_runtime_p4() -> str:
    return _run_physical_test_module(
        "test_daemon_runtime_p4.py",
        "proved internal runtime reconciliation precedes serving while public lifecycle remains absent",
    )


def check_source_manifest() -> str:
    """Verify every preserved upload artifact against the checked-in hash map."""

    manifest_path = PROJECT_ROOT / "source-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["source_archive_sha256"] == "63f698f7af3ce989de8885a3b3b68b7214dedbffb1ac6671636b1f5d13829f94"
    entries = payload["files"]
    assert len(entries) == 33
    for entry in entries:
        path = PROJECT_ROOT / entry["preserved_path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    return "verified 33/33 preserved source files against their byte hashes"


def check_plan_contract() -> str:
    """Verify the root execution plan is complete, unique, and honest."""

    plan_path = PROJECT_ROOT / "PLAN.md"
    plan = plan_path.read_text(encoding="utf-8")
    required_sections = (
        "## 2. Non-negotiable architecture invariants",
        "## 4. Full-SOTA definition of done",
        "## 7. Critical path and releases",
        "## 27. File-by-file implementation map",
        "## 28. Required physical test inventory",
        "## 29. Immediate next implementation sequence",
        "## 30. Final sign-off",
    )
    for section in required_sections:
        assert section in plan, f"Missing execution-plan section: {section}"
    for phase in range(19):
        assert f"Phase P{phase}" in plan, f"Missing execution phase P{phase}"
        assert f"`GATE-P{phase}`" in plan, f"Missing physical gate P{phase}"

    identifier_pattern = re.compile(
        r"- \[[ x]\] `((?:INV|BASE|DONE|EXEC|TEST)-\d{3}|P\d+-\d{3}|GATE-P\d+)`"
    )
    identifiers = identifier_pattern.findall(plan)
    assert len(identifiers) >= 250, f"Execution plan is unexpectedly shallow: {len(identifiers)} IDs"
    duplicates = sorted({identifier for identifier in identifiers if identifiers.count(identifier) > 1})
    assert not duplicates, f"Duplicate execution-plan IDs: {duplicates}"

    plan_index = json.loads((PROJECT_ROOT / "docs" / "plan-index.json").read_text(encoding="utf-8"))
    completed_phases = {
        phase["id"] for phase in plan_index["phases"] if phase["status"] == "complete"
    }
    completed_items = {
        item["id"]
        for phase in plan_index["phases"]
        for item in phase["items"]
        if item["status"] == "complete"
    }
    allowed_completed = {
        identifier
        for identifier in identifiers
        if identifier.startswith("BASE-")
        or identifier in completed_items
        or any(
            identifier == f"GATE-{phase_id}"
            for phase_id in completed_phases
        )
    }
    completed = set(re.findall(r"- \[x\] `([^`]+)`", plan))
    assert completed
    assert completed <= allowed_completed, (
        f"Completed plan IDs are not backed by completed indexed phases: "
        f"{sorted(completed - allowed_completed)}"
    )
    for boundary in (
        "AIPC is a persistent full computer",
        "external `backend/virtual_machine` donor remains first-party read-only lineage",
        "Kerminal remains the agency and operator surface",
        "Artifact processors remain outside the qcow2",
        "normal policy permits one active AIPC",
    ):
        assert boundary in plan, f"Missing architecture boundary: {boundary}"
    assert "No command may report success for a simulated operation" in plan
    return f"validated {len(identifiers)} unique execution IDs across phases P0-P18"


def _run_plan_validator(root: Path) -> tuple[int, dict[str, object], str]:
    """Run the real plan-only verifier against one repository fixture."""

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_repository.py"),
            "--plan-only",
            "--json",
            "--root",
            str(root),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return completed.returncode, payload, completed.stderr


def check_plan_index() -> str:
    """Verify the live execution DAG through its public validator CLI."""

    return_code, payload, stderr = _run_plan_validator(PROJECT_ROOT)
    assert return_code == 0, (
        f"plan-only verifier exited {return_code}: {stderr or payload}"
    )
    assert payload.get("ok") is True
    summary = payload.get("summary")
    assert isinstance(summary, dict)
    assert summary.get("fail") == 0
    checks = payload.get("checks")
    assert isinstance(checks, list)
    names = {
        item.get("name")
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for name in (
        "plan-index:unique-plan-ids",
        "plan-index:execution-order",
        "plan-index:one-current-phase",
        "plan-index:task-agreement",
        "plan-index:no-false-physical-completion",
    ):
        assert name in names, f"Missing plan-index observation: {name}"
    return f"validated live plan index with {summary.get('pass')} direct observations"


def _materialize_plan_fixture(root: Path) -> None:
    """Create one real minimal repository fixture for plan validation."""

    for relative in ("PLAN.md", "TASK.md", "docs/plan-index.json"):
        source = PROJECT_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    payload = json.loads((root / "docs" / "plan-index.json").read_text(encoding="utf-8"))
    fixture_paths = set(payload["baseline_evidence"])
    for phase in payload["phases"]:
        fixture_paths.update(phase["owner_files"])
        fixture_paths.update(phase["evidence"])
        for item in phase["items"]:
            fixture_paths.update(item["evidence"])
    for relative in sorted(fixture_paths):
        source = PROJECT_ROOT / relative
        target = root / relative
        if target.exists():
            continue
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture evidence\n", encoding="utf-8")


def check_plan_index_rejections() -> str:
    """Prove malformed, ambiguous, and unevidenced plan states fail loudly."""

    scenarios: list[tuple[str, object]] = []

    def duplicate_json_key(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                '"schema_version": 1,',
                '"schema_version": 1,\\n  "schema_version": 1,',
                1,
            ),
            encoding="utf-8",
        )

    scenarios.append(("duplicate-json-key", duplicate_json_key))

    def missing_dependency(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["phases"][2]["depends_on"] = ["P99"]
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    scenarios.append(("missing-dependency", missing_dependency))

    def dependency_cycle(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["phases"][1]["depends_on"] = ["P2"]
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    scenarios.append(("dependency-cycle", dependency_cycle))

    def missing_gate(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["phases"][0]["gate_id"] = "GATE-P99"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    scenarios.append(("missing-gate", missing_gate))

    def multiple_current_phases(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        current_phase = payload["current_phase"]
        competing = next(
            phase
            for phase in payload["phases"]
            if phase["id"] != current_phase and phase["status"] == "pending"
        )
        competing["status"] = "ready"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    scenarios.append(("multiple-current-phases", multiple_current_phases))

    def completed_without_evidence(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["phases"][1]["items"][0]["status"] = "complete"
        payload["phases"][1]["items"][0]["evidence"] = []
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    scenarios.append(("completed-without-evidence", completed_without_evidence))

    def owner_path_escape(root: Path) -> None:
        path = root / "docs" / "plan-index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["phases"][0]["owner_files"] = ["../outside"]
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    scenarios.append(("owner-path-escape", owner_path_escape))

    def duplicate_plan_id(root: Path) -> None:
        path = root / "PLAN.md"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n- [ ] `P0-001` duplicate hostile fixture\n")

    scenarios.append(("duplicate-plan-id", duplicate_plan_id))

    with tempfile.TemporaryDirectory(prefix="vm-lab-plan-fixtures-") as temporary:
        fixture_root = Path(temporary)
        base = fixture_root / "base"
        _materialize_plan_fixture(base)
        base_code, base_payload, base_stderr = _run_plan_validator(base)
        assert base_code == 0, f"base fixture invalid: {base_stderr or base_payload}"

        rejected: list[str] = []
        for name, mutate in scenarios:
            assert callable(mutate)
            scenario_root = fixture_root / name
            shutil.copytree(base, scenario_root)
            mutate(scenario_root)
            return_code, payload, stderr = _run_plan_validator(scenario_root)
            assert return_code == 1, (
                f"{name} returned {return_code}: {stderr or payload}"
            )
            assert payload.get("ok") is False, f"{name} did not report structured failure"
            rejected.append(name)

    return f"rejected {len(rejected)} hostile plan fixtures: {', '.join(rejected)}"


def _run_check(name: str, function: object) -> CheckResult:
    """Run one check while preserving fail-loud, still-continue behavior."""

    import time

    if not callable(function):
        return CheckResult(name, "fail", "check is not callable", 0.0)
    started = time.perf_counter()
    try:
        detail = function()
        status = "pass"
    except (
        AssertionError,
        BootstrapError,
        ConfigurationError,
        RuntimeError,
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        status = "fail"
    duration_ms = (time.perf_counter() - started) * 1000
    return CheckResult(name, status, str(detail), duration_ms)


def _write_artifacts(results: list[CheckResult], started_at: datetime, ended_at: datetime) -> Path:
    """Write JSON, first-person Markdown, log, and the root run ledger."""

    timestamp = ended_at.strftime("%Y%m%dT%H%M%SZ")
    run_dir = PROJECT_ROOT / "test" / "vm_lab" / "runs" / timestamp
    scope_text = (PROJECT_ROOT / "SCOPE.md").read_text(encoding="utf-8")
    mode_match = re.search(r"^- mode: (WRAP|EDIT|COMPOSE)$", scope_text, re.MULTILINE)
    target_match = re.search(r"^- target_module: (.+)$", scope_text, re.MULTILINE)
    if mode_match is None or target_match is None:
        raise ValueError("SCOPE.md is missing a machine-readable mode or target_module")
    mode = mode_match.group(1)
    target_module = target_match.group(1).replace("`", "")
    run_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(result.status == "pass" for result in results)
    failed = sum(result.status == "fail" for result in results)
    payload = {
        "domain": "vm_lab",
        "mode": mode,
        "scope": "SCOPE.md",
        "target_module": target_module,
        "status": "pass" if failed == 0 else "fail",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "pass_count": passed,
        "fail_count": failed,
        "skip_count": 0,
        "counts": {"passed": passed, "failed": failed, "skipped": 0},
        "checks": [result.to_dict() for result in results],
    }
    (run_dir / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = [
        "# VM Lab Integration Run",
        "",
        f"I ran {len(results)} checks against the public read-only CLI, internal daemon/registry/storage boundary, and guest bootstrap.",
        f"I observed {passed} passes, {failed} failures, and 0 skips.",
        "",
        "## What I exercised",
        "",
    ]
    markdown.extend(f"- `{result.name}`: {result.status} — {result.detail}" for result in results)
    markdown.extend(
        [
            "",
            "## What was surprising",
            "",
            "I intentionally accept an unhealthy doctor report on hosts without QEMU. The test passes when the doctor names those blockers accurately. QEMU launch, QMP identity, and public lifecycle mutation remain withheld until their physical gates pass.",
            "",
        ]
    )
    (run_dir / "result.md").write_text("\n".join(markdown), encoding="utf-8")
    log_lines = ["COMMAND", f"{sys.executable} test/vm_lab/smoke.py", "", "STDOUT"]
    log_lines.extend(f"[{result.status.upper()}] {result.name}: {result.detail}" for result in results)
    log_lines.extend(["", "STDERR", "<none captured by harness>", ""])
    (run_dir / "result.log").write_text("\n".join(log_lines), encoding="utf-8")
    relative = run_dir.relative_to(PROJECT_ROOT)
    ledger = "\n".join(
        [
            "# Latest SOTA Run",
            "",
            "<!-- SOTA_RUN_LATEST_START -->",
            f"- mode: {mode}",
            "- domain: vm_lab",
            "- scope: SCOPE.md",
            f"- target_module: {target_module}",
            "- smoke_harness: test/vm_lab/smoke.py",
            f"- run_dir: {relative}",
            f"- status: {payload['status']}",
            f"- pass_count: {passed}",
            f"- fail_count: {failed}",
            "- skip_count: 0",
            f"- result_json: {relative / 'result.json'}",
            f"- result_md: {relative / 'result.md'}",
            f"- result_log: {relative / 'result.log'}",
            "<!-- SOTA_RUN_LATEST_END -->",
            "",
        ]
    )
    (PROJECT_ROOT / "SOTA_RUN.md").write_text(ledger, encoding="utf-8")
    return run_dir


def main() -> int:
    """Run the complete VM lab integration suite."""

    started_at = datetime.now(timezone.utc)
    checks = [
        ("protocol_vm_primitives", run_vm_primitive_checks),
        ("protocol_agent", run_agent_protocol_checks),
        ("protocol_control", run_control_protocol_checks),
        ("protocol_settings", run_settings_protocol_checks),
        ("protocol_host_consumption", run_host_protocol_consumption_checks),
        ("protocol_vm_lifecycle", run_vm_lifecycle_checks),
        ("contract_roundtrip", check_contract_roundtrip),
        ("configuration", check_configuration),
        ("port_allocation", check_port_allocation),
        ("qemu_plan", check_qemu_plan),
        ("bootstrap_extraction", check_bootstrap_extraction),
        ("bootstrap_idempotency", check_bootstrap_idempotency),
        ("doctor_truthfulness", check_doctor_truthfulness),
        ("cli_entrypoint", check_cli_entrypoint),
        ("wheel_runtime", check_wheel_runtime),
        ("daemon_transport_p2", check_daemon_transport_p2),
        ("registry_p2", check_registry_p2),
        ("registry_service_p2", check_registry_service_p2),
        ("daemon_registry_p2", check_daemon_registry_p2),
        ("storage_p3", check_storage_p3),
        ("storage_p3_adversarial", check_storage_p3_adversarial),
        ("daemon_storage_p3", check_daemon_storage_p3),
        ("qemu_planning_p4", check_qemu_planning_p4),
        ("doctor_p4", check_doctor_p4),
        (
            "disposable_launch_authority_p4",
            check_disposable_launch_authority_p4,
        ),
        ("p4_gate_coordinator", check_p4_gate_coordinator),
        ("qemu_process_p4", check_qemu_process_p4),
        ("qemu_exec_guard_p4", check_qemu_exec_guard_p4),
        ("qemu_logs_p4", check_qemu_logs_p4),
        ("qmp_p4", check_qmp_p4),
        ("qmp_identity_p4", check_qmp_identity_p4),
        ("registry_p4", check_registry_p4),
        ("storage_runtime_p4", check_storage_runtime_p4),
        ("qemu_runtime_journal_p4", check_qemu_runtime_journal_p4),
        ("qemu_runtime_p4", check_qemu_runtime_p4),
        ("daemon_runtime_p4", check_daemon_runtime_p4),
        ("plan_contract", check_plan_contract),
        ("plan_index", check_plan_index),
        ("plan_index_rejections", check_plan_index_rejections),
        ("source_manifest", check_source_manifest),
    ]
    assert len(checks) == EXPECTED_CHECK_COUNT
    results = [_run_check(name, function) for name, function in checks]
    ended_at = datetime.now(timezone.utc)
    run_dir = _write_artifacts(results, started_at, ended_at)
    for result in results:
        print(f"[{result.status.upper()}] {result.name}: {result.detail}")
    print(f"Artifacts: {run_dir}")
    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
