"""Ephemeral VM record and QEMU-plan composition.

Source: vm_supervisor.py planning lineage
Integrated: 2026-08-04
Purpose: Keeps plan-time allocation separate from the unpromoted persistent
    lifecycle owner while preserving one canonical VM contract.
IO: Brief localhost bind probes only; no persistent reservation or writes.
"""

from __future__ import annotations

from pathlib import Path

from ..config import LabConfiguration
from ..contracts.vm import VMDefinition, VMRecord
from .ports import PortAllocator, PortExhaustedError
from .qemu import QemuCommandBuilder, QemuLaunchPlan, QemuPlanError


class VMPlanner:
    """Compose one ephemeral VM record and fully explicit launch plan."""

    def __init__(self, config: LabConfiguration) -> None:
        """Bind the planner to one validated lab configuration."""

        self._config = config
        self._ports = PortAllocator(config.host)
        self._builder = QemuCommandBuilder(config.host)

    def preview(
        self,
        name: str,
        disk_path: str | Path,
        memory_mib: int = 4096,
        vcpus: int = 2,
        enable_kvm: bool | None = None,
    ) -> tuple[VMRecord, QemuLaunchPlan]:
        """Return a bind-checked but deliberately non-reserved QEMU plan."""

        try:
            ports = self._ports.allocate()
        except PortExhaustedError as exc:
            raise QemuPlanError(str(exc)) from exc
        try:
            definition = VMDefinition(
                name=name,
                disk_path=str(Path(disk_path).expanduser().resolve()),
                memory_mib=memory_mib,
                vcpus=vcpus,
                enable_kvm=self._config.host.enable_kvm if enable_kvm is None else enable_kvm,
            )
            record = VMRecord(
                definition=definition,
                ports=ports,
            )
            plan = self._builder.build(record)
            # VMRecord is an immutable protocol value.  The QEMU builder owns
            # no record mutation: it returns pure plan locations which become
            # a new declared record for the caller to serialize or inspect.
            record = record.with_planned_runtime(
                qmp_socket=str(plan.qmp_socket),
                pid_file=str(plan.pid_file),
                log_path=str(plan.log_path),
            )
            return record, plan
        finally:
            self._ports.release(ports)
