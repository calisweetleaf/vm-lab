"""Read-only host and package preflight checks.

Source: vm_lab.zip audit requirements
Integrated: 2026-08-04
Purpose: Detects missing binaries, images, permissions, topology, and accidental
    activation of cold components before any VM mutation occurs.
IO: Filesystem metadata, executable lookup, and bounded read-only subprocesses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import LabConfiguration
from .topology import validate_topology


class CheckStatus(str, Enum):
    """Doctor check severity and outcome."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One machine-readable preflight observation."""

    name: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible check."""

        return {"name": self.name, "status": self.status.value, "detail": self.detail}


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
