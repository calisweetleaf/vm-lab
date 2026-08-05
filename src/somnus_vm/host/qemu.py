"""Deterministic, non-mutating QEMU launch planning.

Source: vm_supervisor.py CustomVMManager lineage
Integrated: 2026-08-04
Purpose: Produces reviewable argv without shell interpolation, daemonization,
    fixed ports, fabricated guest addresses, or ambiguous disk-path parsing.
IO: Pure planning; no directory, image, process, socket, or registry mutation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..config import HostConfiguration
from ..contracts.vm import VMRecord


class QemuPlanError(ValueError):
    """Raised when a safe launch plan cannot be represented."""


@dataclass(frozen=True, slots=True)
class QemuLaunchPlan:
    """Fully resolved process plan with no shell interpolation."""

    argv: tuple[str, ...]
    qmp_socket: Path
    pid_file: Path
    log_path: Path


class QemuCommandBuilder:
    """Build non-mutating QEMU argv from one validated VM record."""

    def __init__(self, config: HostConfiguration) -> None:
        """Retain validated host configuration."""

        self._config = config

    def build(self, record: VMRecord) -> QemuLaunchPlan:
        """Create a fully explicit launch plan for a future process owner."""

        state_dir = self._config.state_dir / "vms" / str(record.vm_id)
        qmp_socket = self._config.runtime_dir / f"{record.vm_id}.qmp"
        pid_file = state_dir / "qemu.pid"
        log_path = state_dir / "qemu.log"
        if len(os.fsencode(qmp_socket)) > 107:
            raise QemuPlanError(f"QMP socket path exceeds the Linux AF_UNIX limit: {qmp_socket}")
        vnc_display = record.ports.vnc - 5900
        if vnc_display < 0:
            raise QemuPlanError("VNC ports must be 5900 or greater")

        blockdev = json.dumps(
            {
                "driver": "qcow2",
                "node-name": "somnus-disk",
                "file": {
                    "driver": "file",
                    "filename": record.definition.disk_path,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        argv: list[str] = [
            self._config.qemu_binary,
            "-name",
            f"guest={record.definition.name}",
            "-uuid",
            str(record.vm_id),
            "-m",
            str(record.definition.memory_mib),
            "-smp",
            f"cpus={record.definition.vcpus}",
        ]
        if record.definition.enable_kvm and self._config.enable_kvm:
            argv.extend(["-enable-kvm", "-cpu", "host"])
        else:
            argv.extend(["-machine", "accel=tcg", "-cpu", "max"])
        argv.extend(
            [
                "-blockdev",
                blockdev,
                "-device",
                "virtio-blk-pci,drive=somnus-disk",
                "-netdev",
                (
                    "user,id=net0,"
                    f"hostfwd=tcp:127.0.0.1:{record.ports.ssh}-:22,"
                    f"hostfwd=tcp:127.0.0.1:{record.ports.agent}-:9901"
                ),
                "-device",
                "virtio-net-pci,netdev=net0",
                "-vnc",
                f"127.0.0.1:{vnc_display}",
                "-qmp",
                f"unix:{qmp_socket},server=on,wait=off",
                "-pidfile",
                str(pid_file),
                "-serial",
                f"file:{log_path}",
                "-monitor",
                "none",
                "-no-reboot",
            ]
        )
        return QemuLaunchPlan(tuple(argv), qmp_socket, pid_file, log_path)
