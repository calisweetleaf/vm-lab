"""Executable composition root for the single internal mutation owner.

P4 composes durable QEMU recovery and reconciliation behind the daemon, but
the public control membrane remains the P2/P3 registry and storage service.
No public lifecycle operation is exposed here.
"""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path
from types import FrameType
from typing import Final

from .config import ConfigurationError, load_configuration
from .daemon import UnixControlDaemon
from .host.registry import (
    RegistryError,
    RegistryMode,
    SQLiteRegistry,
)
from .host.qemu_runtime import QemuRuntimeError, QemuRuntimeOwner
from .host.service import RegistryMutationService, RegistryServiceError
from .transport import DaemonOwnershipError

_REGISTRY_NAME: Final[str] = "registry.sqlite3"


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


def run(config_path: Path) -> int:
    """Compose and serve one unprivileged registry authority."""

    if os.geteuid() == 0:
        raise DaemonOwnershipError(
            "somnus-vm-daemon must run as an unprivileged service account"
        )
    configuration = load_configuration(config_path)
    registry_path = configuration.storage.state_root / _REGISTRY_NAME
    with SQLiteRegistry.open(registry_path, writer=True) as registry:
        service = RegistryMutationService(registry, configuration.host)
        runtime = QemuRuntimeOwner(registry, configuration.host)
        daemon: UnixControlDaemon | None = None
        previous_term: signal.Handlers | None = None
        previous_int: signal.Handlers | None = None
        try:
            if registry.status.mode is RegistryMode.READ_WRITE:
                runtime.recover_incomplete_launches()
                runtime.reconcile_completed_launches()
                service.recover_incomplete_operations()
                service.reconcile_storage_authority(
                    exclude_live_vm_ids=runtime.live_vm_ids
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


__all__ = ["build_parser", "main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
