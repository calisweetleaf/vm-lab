"""Executable composition root for the single internal mutation owner.

P4 composes durable QEMU recovery and reconciliation behind the daemon, but
the public control membrane remains the P2/P3 registry and storage service.
No public lifecycle operation is exposed here.
"""

from __future__ import annotations

import argparse
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Callable, Final
from uuid import UUID

from .config import ConfigurationError, LabConfiguration, load_configuration
from .daemon import UnixControlDaemon
from .host.registry import (
    RegistryError,
    RegistryMode,
    SQLiteRegistry,
)
from .host.qemu_runtime import (
    QemuRuntimeError,
    QemuRuntimeOwner,
    RuntimeRecoveryResult,
)
from .host.service import RegistryMutationService, RegistryServiceError
from .transport import DaemonOwnershipError

_REGISTRY_NAME: Final[str] = "registry.sqlite3"

RuntimeCheckpointObserver = Callable[[UUID, str], None]


@dataclass(frozen=True, slots=True)
class DaemonRuntimeContext:
    """The live single-owner composition offered to one internal activator.

    The context exists only while :func:`run` owns the open writer registry.
    It is not serializable, is not a public control operation, and must never
    escape into a second process or a longer-lived lifecycle owner.
    """

    configuration: LabConfiguration
    registry: SQLiteRegistry
    service: RegistryMutationService
    runtime: QemuRuntimeOwner
    incomplete_recovery: tuple[RuntimeRecoveryResult, ...]
    completed_reconciliation: tuple[RuntimeRecoveryResult, ...]


RuntimeActivation = Callable[[DaemonRuntimeContext], None]


@dataclass(frozen=True, slots=True)
class DaemonRuntimeHooks:
    """Explicit Python-only composition ports for physical integration gates.

    Normal ``somnus-vm-daemon`` execution supplies no hooks.  In particular,
    neither the command-line parser nor the AF_UNIX service vocabulary can
    construct an activation.  A trusted embedding owner may inject one
    checkpoint observer and one after-recovery activation while retaining this
    process as the sole registry, QEMU, and recovery authority.
    """

    checkpoint_observer: RuntimeCheckpointObserver | None = None
    activation: RuntimeActivation | None = None

    def __post_init__(self) -> None:
        if self.checkpoint_observer is not None and not callable(
            self.checkpoint_observer
        ):
            raise TypeError("checkpoint_observer must be callable")
        if self.activation is not None and not callable(self.activation):
            raise TypeError("activation must be callable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="somnus-vm-daemon",
        description=(
            "Run the single local VM registry and image mutation owner. "
            "P4 launch recovery and live-runtime reconciliation are internal; "
            "no public VM lifecycle commands are exposed."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to the strict VM Lab TOML configuration.",
    )
    return parser


def run(
    config_path: Path,
    *,
    hooks: DaemonRuntimeHooks | None = None,
) -> int:
    """Compose and serve one unprivileged registry and runtime authority.

    ``hooks`` is a direct Python embedding seam, not a CLI or transport
    capability.  It exists so a physical gate or later operator-owned
    controller can exercise the already-composed runtime without creating a
    second mutation owner.
    """

    if os.geteuid() == 0:
        raise DaemonOwnershipError(
            "somnus-vm-daemon must run as an unprivileged service account"
        )
    if hooks is not None and not isinstance(hooks, DaemonRuntimeHooks):
        raise TypeError("hooks must be DaemonRuntimeHooks")
    configuration = load_configuration(config_path)
    registry_path = configuration.storage.state_root / _REGISTRY_NAME
    with SQLiteRegistry.open(registry_path, writer=True) as registry:
        service = RegistryMutationService(registry, configuration.host)
        runtime = QemuRuntimeOwner(
            registry,
            configuration.host,
            checkpoint_observer=(
                None if hooks is None else hooks.checkpoint_observer
            ),
        )
        daemon: UnixControlDaemon | None = None
        previous_term: signal.Handlers | None = None
        previous_int: signal.Handlers | None = None
        try:
            incomplete_recovery: tuple[RuntimeRecoveryResult, ...] = ()
            completed_reconciliation: tuple[RuntimeRecoveryResult, ...] = ()
            if registry.status.mode is RegistryMode.READ_WRITE:
                incomplete_recovery = runtime.recover_incomplete_launches()
                completed_reconciliation = (
                    runtime.reconcile_completed_launches()
                )
                service.recover_incomplete_operations()
                service.reconcile_storage_authority(
                    exclude_live_vm_ids=runtime.live_vm_ids
                )
            if hooks is not None and hooks.activation is not None:
                hooks.activation(
                    DaemonRuntimeContext(
                        configuration=configuration,
                        registry=registry,
                        service=service,
                        runtime=runtime,
                        incomplete_recovery=incomplete_recovery,
                        completed_reconciliation=completed_reconciliation,
                    )
                )
            daemon = UnixControlDaemon(
                configuration.daemon,
                service,
                allowed_uids={os.geteuid()},
            )

            def stop(
                _signum: int,
                _frame: FrameType | None,
            ) -> None:
                # Do not release QMP sessions or the registry while a request
                # handler can still own a mutation.
                assert daemon is not None
                daemon.shutdown()

            previous_term = signal.signal(signal.SIGTERM, stop)
            previous_int = signal.signal(signal.SIGINT, stop)
            daemon.serve_forever()
        finally:
            if previous_term is not None:
                signal.signal(signal.SIGTERM, previous_term)
            if previous_int is not None:
                signal.signal(signal.SIGINT, previous_int)
            if daemon is not None:
                # Transport ownership and every handler end before daemon-local
                # QMP sessions are released.  Runtime shutdown deliberately
                # does not stop persistent QEMU children.
                daemon.shutdown()
            runtime.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args.config)
    except (
        ConfigurationError,
        DaemonOwnershipError,
        QemuRuntimeError,
        RegistryError,
        RegistryServiceError,
    ) as exc:
        parser.exit(1, f"somnus-vm-daemon: {exc}\n")


__all__ = [
    "DaemonRuntimeContext",
    "DaemonRuntimeHooks",
    "RuntimeActivation",
    "RuntimeCheckpointObserver",
    "build_parser",
    "main",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
