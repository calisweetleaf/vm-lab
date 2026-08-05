"""Thread-safe localhost port allocation for QEMU user networking.

Source: vm_supervisor.py port-forwarding lineage
Integrated: 2026-08-04
Purpose: Prevents the fixed 2222/9901/5900 collisions in the source supervisor.
IO: Briefly binds localhost sockets to prove availability; retains no sockets.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterable

from ..config import HostConfiguration, PortRange
from ..contracts.vm import VMPorts


class PortExhaustedError(RuntimeError):
    """Raised when a configured host-port range has no available port."""


class PortAllocator:
    """Allocate unique SSH, agent, and VNC ports inside one supervisor process."""

    def __init__(self, config: HostConfiguration) -> None:
        """Initialize the allocator from validated host ranges."""

        self._config = config
        self._leased: set[int] = set()
        self._lock = threading.Lock()

    def allocate(self, reserved: Iterable[int] = ()) -> VMPorts:
        """Reserve one available port from each configured role range.

        Args:
            reserved: Ports already persisted by other VM records.

        Returns:
            Three unique host ports held by this allocator.
        """

        with self._lock:
            unavailable = set(reserved) | self._leased
            chosen: list[int] = []
            try:
                ssh = self._find_available(self._config.ssh_ports, unavailable)
                chosen.append(ssh)
                unavailable.add(ssh)
                agent = self._find_available(self._config.agent_ports, unavailable)
                chosen.append(agent)
                unavailable.add(agent)
                vnc = self._find_available(self._config.vnc_ports, unavailable)
                chosen.append(vnc)
            except PortExhaustedError:
                for port in chosen:
                    self._leased.discard(port)
                raise
            self._leased.update(chosen)
            return VMPorts(ssh=ssh, agent=agent, vnc=vnc)

    def release(self, ports: VMPorts) -> None:
        """Release ports previously returned by allocate."""

        with self._lock:
            self._leased.difference_update({ports.ssh, ports.agent, ports.vnc})

    def _find_available(self, interval: PortRange, unavailable: set[int]) -> int:
        """Return and tentatively lease the first bindable port."""

        for port in interval.values():
            if port in unavailable:
                continue
            if self._can_bind(port):
                self._leased.add(port)
                return port
        raise PortExhaustedError(f"No available localhost port in {interval.start}-{interval.end}")

    @staticmethod
    def _can_bind(port: int) -> bool:
        """Probe whether localhost can bind a TCP port."""

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True
