"""Focused P4 proof for read-only QEMU sandbox compatibility preflight.

The checks execute only QEMU's ``-help`` argument-consumption path. They never
create a VM, disk, QMP socket, runtime directory, or lifecycle record.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_vm.config import load_configuration
from somnus_vm.doctor import CheckStatus, run_doctor


def _write_configuration(
    root: Path,
    qemu_binary: str,
    *,
    enable_kvm: bool = False,
) -> Path:
    """Write one isolated profile with an explicit executable under test."""

    path = root / "doctor-p4.toml"
    path.write_text(
        "\n".join(
            (
                "[vm_lab]",
                'profile = "doctor-p4"',
                "",
                "[host]",
                f"state_dir = {json.dumps(str(root / 'state'))}",
                f"runtime_dir = {json.dumps(str(root / 'runtime'))}",
                f"base_image = {json.dumps(str(root / 'images/base.qcow2'))}",
                f"qemu_binary = {json.dumps(qemu_binary)}",
                'qemu_img_binary = "qemu-img"',
                f"enable_kvm = {'true' if enable_kvm else 'false'}",
                "ssh_ports = [44000, 44127]",
                "agent_ports = [44200, 44327]",
                "vnc_ports = [6200, 6327]",
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


def _write_executable(root: Path, name: str, body: str) -> Path:
    """Create one real local hostile executable for the doctor boundary."""

    path = root / name
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def _sandbox_check(config_path: Path) -> object:
    """Consume the public doctor report and return its sandbox observation."""

    report = run_doctor(load_configuration(config_path), project_root=None)
    return next(check for check in report.checks if check.name == "qemu_sandbox")


def check_real_qemu_consumes_closed_probe_when_available() -> str:
    """Use the installed QEMU binary when present without launching a machine."""

    qemu = shutil.which("qemu-system-x86_64")
    if qemu is None:
        return "no local qemu-system-x86_64 is installed; unavailable-binary failure remains covered"
    with tempfile.TemporaryDirectory() as temporary:
        check = _sandbox_check(_write_configuration(Path(temporary), qemu))
    assert check.status is CheckStatus.PASS, check.to_dict()
    payload = check.to_dict()
    evidence = payload["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["binary"] == qemu
    assert evidence["returncode"] == "0"
    assert int(evidence["stdout_bytes"]) > 0
    assert len(evidence["argv_sha256"]) == 64
    return "interrogated the local QEMU -help parser with the exact closed P4 option set without launching a VM"


def check_hostile_executable_and_protocol_fail_loud() -> str:
    """Reject unavailable, rejecting, silent-success, and output-flood executables."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        unavailable = _sandbox_check(
            _write_configuration(root, str(root / "missing-qemu"))
        )
        assert unavailable.status is CheckStatus.FAIL
        assert unavailable.remediation is not None
        assert unavailable.to_dict()["evidence"] == {
            "configured_binary": str(root / "missing-qemu")
        }

        expected_arguments = [
            "-no-user-config",
            "-nodefaults",
            "-machine",
            "accel=tcg",
            "-cpu",
            "max",
            "-sandbox",
            "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
            "-display",
            "none",
            "-nic",
            "none",
            "-monitor",
            "none",
            "-no-reboot",
            "-help",
        ]
        argument_auditor = _write_executable(
            root,
            "argument-auditor",
            "import sys\n"
            f"expected = {expected_arguments!r}\n"
            "if sys.argv[1:] != expected:\n"
            "    sys.stderr.write(repr(sys.argv[1:]))\n"
            "    raise SystemExit(74)\n"
            "sys.stdout.write('QEMU help protocol accepted')\n",
        )
        audited = _sandbox_check(_write_configuration(root, str(argument_auditor)))
        assert audited.status is CheckStatus.PASS, audited.to_dict()

        kvm_expected_arguments = [
            "-no-user-config",
            "-nodefaults",
            "-enable-kvm",
            "-cpu",
            "host",
            *expected_arguments[6:],
        ]
        kvm_argument_auditor = _write_executable(
            root,
            "kvm-argument-auditor",
            "import sys\n"
            f"expected = {kvm_expected_arguments!r}\n"
            "if sys.argv[1:] != expected:\n"
            "    sys.stderr.write(repr(sys.argv[1:]))\n"
            "    raise SystemExit(75)\n"
            "sys.stdout.write('QEMU help protocol accepted')\n",
        )
        kvm_audited = _sandbox_check(
            _write_configuration(
                root,
                str(kvm_argument_auditor),
                enable_kvm=True,
            )
        )
        assert kvm_audited.status is CheckStatus.PASS, kvm_audited.to_dict()

        rejecting = _write_executable(
            root,
            "rejecting-qemu",
            "import sys\nsys.stderr.write('unsupported P4 option')\nraise SystemExit(73)\n",
        )
        rejected = _sandbox_check(_write_configuration(root, str(rejecting)))
        assert rejected.status is CheckStatus.FAIL
        assert rejected.remediation is not None
        rejected_evidence = rejected.to_dict()["evidence"]
        assert isinstance(rejected_evidence, dict)
        assert rejected_evidence["returncode"] == "73"
        assert "unsupported P4 option" not in rejected.detail

        silent = _write_executable(root, "silent-success", "raise SystemExit(0)\n")
        silent_check = _sandbox_check(_write_configuration(root, str(silent)))
        assert silent_check.status is CheckStatus.FAIL
        assert "QEMU help protocol" in silent_check.detail

        flood = _write_executable(
            root,
            "flood-qemu",
            "import sys\nsys.stdout.buffer.write(b'x' * 200000)\nsys.stdout.flush()\n",
        )
        flood_check = _sandbox_check(_write_configuration(root, str(flood)))
        assert flood_check.status is CheckStatus.FAIL
        assert "output exceeded" in flood_check.detail
        flood_evidence = flood_check.to_dict()["evidence"]
        assert isinstance(flood_evidence, dict)
        assert int(flood_evidence["stdout_bytes"]) > 131_072
        assert flood_check.remediation is not None
    return "proved the exact probe vector, then rejected unavailable, option-rejecting, protocol-silent, and unbounded-output executable behavior with structured remediation"


def check_public_cli_emits_structured_sandbox_failure() -> str:
    """Consume the machine-readable public doctor boundary for incompatibility."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        rejecting = _write_executable(
            root,
            "cli-rejecting-qemu",
            "import sys\nsys.stderr.write('hostile error')\nraise SystemExit(91)\n",
        )
        config = _write_configuration(root, str(rejecting))
        environment = {"PYTHONPATH": str(SOURCE_ROOT)}
        completed = subprocess.run(
            [sys.executable, "-m", "somnus_vm", "--config", str(config), "doctor", "--json"],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert completed.returncode == 1, completed.stderr
    payload = json.loads(completed.stdout)
    sandbox = next(item for item in payload["checks"] if item["name"] == "qemu_sandbox")
    assert sandbox["status"] == "fail"
    assert sandbox["evidence"]["returncode"] == "91"
    assert sandbox["remediation"].startswith("Select a local QEMU build")
    assert "hostile error" not in sandbox["detail"]
    return "proved the public doctor JSON reports incompatible sandbox support as a structured failing host fact"


def run_doctor_p4_checks() -> list[str]:
    """Run every P4 doctor compatibility proof without a VM launch."""

    return [
        check_real_qemu_consumes_closed_probe_when_available(),
        check_hostile_executable_and_protocol_fail_loud(),
        check_public_cli_emits_structured_sandbox_failure(),
    ]


if __name__ == "__main__":
    for result in run_doctor_p4_checks():
        print(result)
