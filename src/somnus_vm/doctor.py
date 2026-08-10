"""Read-only host and package preflight checks.

Source: vm_lab.zip audit requirements
Integrated: 2026-08-04
Purpose: Detects missing binaries, images, permissions, topology, and accidental
    activation of cold components before any VM mutation occurs.
IO: Filesystem metadata, executable lookup, and bounded read-only subprocesses.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .config import LabConfiguration
from .host.qemu import _SANDBOX_POLICY
from .topology import validate_topology


_QEMU_PROBE_TIMEOUT_SECONDS: Final[float] = 10.0
_QEMU_PROBE_OUTPUT_LIMIT_BYTES: Final[int] = 131_072
_QEMU_P4_HARDENING_ARGUMENTS: Final[tuple[str, ...]] = (
    "-no-user-config",
    "-nodefaults",
    "-sandbox",
    _SANDBOX_POLICY,
    "-display",
    "none",
    "-nic",
    "none",
    "-monitor",
    "none",
    "-no-reboot",
    "-help",
)


class CheckStatus(str, Enum):
    """Doctor check severity and outcome."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class _QemuProbeFailure(RuntimeError):
    """Retain bounded subprocess evidence for one failed QEMU probe."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None,
        stdout: bytes,
        stderr: bytes,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One machine-readable preflight observation."""

    name: str
    status: CheckStatus
    detail: str
    evidence: dict[str, str] | None = None
    remediation: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible check."""

        payload: dict[str, object] = {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
        }
        if self.evidence is not None:
            payload["evidence"] = self.evidence
        if self.remediation is not None:
            payload["remediation"] = self.remediation
        return payload


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Aggregate read-only preflight report."""

    profile: str
    checks: tuple[DoctorCheck, ...]

    @property
    def healthy(self) -> bool:
        """Return true when no check failed."""

        return all(check.status is not CheckStatus.FAIL for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible report."""

        return {
            "profile": self.profile,
            "healthy": self.healthy,
            "summary": {
                status.value: sum(check.status is status for check in self.checks)
                for status in CheckStatus
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def _nearest_existing(path: Path) -> Path:
    """Return the nearest existing ancestor of a path."""

    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _binary_check(name: str, configured: str) -> DoctorCheck:
    """Resolve one required executable."""

    resolved = shutil.which(configured)
    if not resolved:
        return DoctorCheck(name, CheckStatus.FAIL, f"Not found: {configured}")
    return DoctorCheck(name, CheckStatus.PASS, resolved)


def _probe_evidence(
    binary: str,
    argv: tuple[str, ...],
    *,
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
) -> dict[str, str]:
    """Encode bounded, non-prose evidence for one executable probe."""

    argv_json = json.dumps([binary, *argv], ensure_ascii=True, separators=(",", ":"))
    return {
        "binary": binary,
        "argv_sha256": hashlib.sha256(argv_json.encode("ascii")).hexdigest(),
        "returncode": "unavailable" if returncode is None else str(returncode),
        "stdout_bytes": str(len(stdout)),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_bytes": str(len(stderr)),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }


def _qemu_probe_arguments(enable_kvm: bool) -> tuple[str, ...]:
    """Project the selected P4 hardening and accelerator flags onto ``-help``."""

    accelerator = (
        ("-enable-kvm", "-cpu", "host")
        if enable_kvm
        else ("-machine", "accel=tcg", "-cpu", "max")
    )
    return (
        "-no-user-config",
        "-nodefaults",
        *accelerator,
        *_QEMU_P4_HARDENING_ARGUMENTS[2:],
    )


def _terminate_probe(process: subprocess.Popen[bytes]) -> None:
    """Terminate one bounded, non-daemonized probe process group."""

    if process.poll() is not None:
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.wait()
            return
        process.wait(timeout=1.0)


def _run_bounded_probe(binary: str, argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
    """Run a read-only argument-consumption probe with bounded output and time."""

    try:
        process = subprocess.Popen(
            [binary, *argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise _QemuProbeFailure(
            "probe process could not be started",
            returncode=None,
            stdout=b"",
            stderr=b"",
        ) from exc
    assert process.stdout is not None
    assert process.stderr is not None
    streams = (process.stdout, process.stderr)
    selector = selectors.DefaultSelector()
    output = {stream.fileno(): bytearray() for stream in streams}
    deadline = time.monotonic() + _QEMU_PROBE_TIMEOUT_SECONDS
    failure: str | None = None
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "probe exceeded its absolute timeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                try:
                    block = os.read(stream.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(stream)
                    continue
                captured = output[stream.fileno()]
                captured.extend(block)
                if len(captured) > _QEMU_PROBE_OUTPUT_LIMIT_BYTES:
                    failure = "probe output exceeded the supported bound"
                    break
            if failure is not None:
                break
        if failure is not None:
            _terminate_probe(process)
            raise _QemuProbeFailure(
                failure,
                returncode=process.returncode,
                stdout=bytes(output[process.stdout.fileno()]),
                stderr=bytes(output[process.stderr.fileno()]),
            )
        try:
            returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            _terminate_probe(process)
            raise _QemuProbeFailure(
                "probe exceeded its absolute timeout",
                returncode=process.returncode,
                stdout=bytes(output[process.stdout.fileno()]),
                stderr=bytes(output[process.stderr.fileno()]),
            ) from exc
        return (
            returncode,
            bytes(output[process.stdout.fileno()]),
            bytes(output[process.stderr.fileno()]),
        )
    finally:
        selector.close()
        for stream in streams:
            stream.close()


def _qemu_sandbox_check(config: LabConfiguration) -> DoctorCheck:
    """Prove the selected QEMU parses the exact closed P4 feature membrane."""

    configured = config.host.qemu_binary
    probe_arguments = _qemu_probe_arguments(config.host.enable_kvm)
    resolved = shutil.which(configured)
    remediation = (
        "Select a local QEMU build that accepts the exact P4 sandbox, "
        "headless, no-network, TCG, and no-reboot option set."
    )
    if not resolved:
        return DoctorCheck(
            "qemu_sandbox",
            CheckStatus.FAIL,
            "Selected QEMU executable is unavailable for the P4 sandbox probe",
            evidence={"configured_binary": configured},
            remediation=remediation,
        )
    try:
        returncode, stdout, stderr = _run_bounded_probe(
            resolved,
            probe_arguments,
        )
    except _QemuProbeFailure as exc:
        return DoctorCheck(
            "qemu_sandbox",
            CheckStatus.FAIL,
            f"Selected QEMU could not complete the P4 sandbox probe: {exc}",
            evidence=_probe_evidence(
                resolved,
                probe_arguments,
                returncode=exc.returncode,
                stdout=exc.stdout,
                stderr=exc.stderr,
            ),
            remediation=remediation,
        )
    evidence = _probe_evidence(
        resolved,
        probe_arguments,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if returncode != 0:
        return DoctorCheck(
            "qemu_sandbox",
            CheckStatus.FAIL,
            "Selected QEMU rejected the exact P4 sandbox/feature probe",
            evidence=evidence,
            remediation=remediation,
        )
    if b"QEMU" not in stdout:
        return DoctorCheck(
            "qemu_sandbox",
            CheckStatus.FAIL,
            "Selected executable returned success without a QEMU help protocol response",
            evidence=evidence,
            remediation=remediation,
        )
    return DoctorCheck(
        "qemu_sandbox",
        CheckStatus.PASS,
        "Selected QEMU accepted the exact closed P4 sandbox/feature probe",
        evidence=evidence,
    )


def _image_check(config: LabConfiguration) -> DoctorCheck:
    """Inspect configured base-image format when qemu-img is available."""

    image = config.host.base_image
    if not image.is_file():
        return DoctorCheck("base_image", CheckStatus.FAIL, f"Not found: {image}")
    qemu_img = shutil.which(config.host.qemu_img_binary)
    if not qemu_img:
        return DoctorCheck("base_image", CheckStatus.WARN, f"Exists but qemu-img cannot inspect it: {image}")
    try:
        result = subprocess.run(
            [qemu_img, "info", "--output=json", str(image)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DoctorCheck("base_image", CheckStatus.FAIL, f"Inspection failed: {exc}")
    if result.returncode != 0:
        return DoctorCheck("base_image", CheckStatus.FAIL, result.stderr.strip() or "qemu-img info failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return DoctorCheck("base_image", CheckStatus.FAIL, f"qemu-img returned invalid JSON: {exc}")
    image_format = payload.get("format") if isinstance(payload, dict) else None
    if image_format != "qcow2":
        return DoctorCheck("base_image", CheckStatus.FAIL, f"Expected qcow2, found {image_format!r}")
    return DoctorCheck("base_image", CheckStatus.PASS, f"qcow2: {image}")


def run_doctor(config: LabConfiguration, project_root: str | Path | None) -> DoctorReport:
    """Run every read-only preflight check.

    Args:
        config: Validated lab configuration.
        project_root: Reorganized source root, or None for an installed package.

    Returns:
        DoctorReport with pass, warning, and failure evidence.
    """

    checks: list[DoctorCheck] = []
    version_ok = sys.version_info >= (3, 12)
    checks.append(
        DoctorCheck(
            "python",
            CheckStatus.PASS if version_ok else CheckStatus.FAIL,
            sys.version.split()[0],
        )
    )
    if project_root is None:
        checks.append(
            DoctorCheck(
                "topology",
                CheckStatus.WARN,
                "Installed runtime detected; source-preservation paths are not part of the wheel",
            )
        )
    else:
        missing = validate_topology(project_root)
        checks.append(
            DoctorCheck(
                "topology",
                CheckStatus.FAIL if missing else CheckStatus.PASS,
                f"Missing: {', '.join(missing)}" if missing else "All declared component paths exist",
            )
        )
    ancestor = _nearest_existing(config.host.state_dir)
    writable = ancestor.exists() and os.access(ancestor, os.W_OK)
    checks.append(
        DoctorCheck(
            "state_directory",
            CheckStatus.PASS if writable else CheckStatus.FAIL,
            f"Nearest existing ancestor: {ancestor}",
        )
    )
    runtime_ancestor = _nearest_existing(config.host.runtime_dir)
    runtime_writable = runtime_ancestor.exists() and os.access(runtime_ancestor, os.W_OK)
    qmp_probe = config.host.runtime_dir / "00000000-0000-0000-0000-000000000000.qmp"
    qmp_fits = len(os.fsencode(qmp_probe)) <= 107
    checks.append(
        DoctorCheck(
            "runtime_directory",
            CheckStatus.PASS if runtime_writable and qmp_fits else CheckStatus.FAIL,
            (
                f"Nearest existing ancestor: {runtime_ancestor}; QMP path budget is valid"
                if qmp_fits
                else f"QMP socket path would exceed 107 bytes: {qmp_probe}"
            ),
        )
    )
    checks.append(_binary_check("qemu", config.host.qemu_binary))
    checks.append(_qemu_sandbox_check(config))
    checks.append(_binary_check("qemu_img", config.host.qemu_img_binary))
    checks.append(_image_check(config))

    if config.host.enable_kvm:
        kvm = Path("/dev/kvm")
        allowed = kvm.exists() and os.access(kvm, os.R_OK | os.W_OK)
        checks.append(
            DoctorCheck(
                "kvm",
                CheckStatus.PASS if allowed else CheckStatus.FAIL,
                "/dev/kvm is usable" if allowed else "/dev/kvm is missing or not readable/writable",
            )
        )
    else:
        checks.append(DoctorCheck("kvm", CheckStatus.WARN, "KVM disabled; QEMU will use TCG"))

    cold_flags = {
        "guest_runtime_candidates": config.components.guest_runtime_candidates,
        "operator_shell": config.components.operator_shell,
        "native_tools": config.components.native_tools,
        "file_processing": config.components.file_processing,
        "digital_twin_lineage": config.components.digital_twin_lineage,
    }
    enabled_cold = sorted(name for name, enabled in cold_flags.items() if enabled)
    checks.append(
        DoctorCheck(
            "promotion_gate",
            CheckStatus.FAIL if enabled_cold else CheckStatus.PASS,
            f"Unpromoted components enabled: {', '.join(enabled_cold)}"
            if enabled_cold
            else "Only live components are enabled",
        )
    )
    return DoctorReport(profile=config.profile, checks=tuple(checks))
