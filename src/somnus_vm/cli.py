"""Canonical command-line entrypoint for the Somnus VM lab.

Source: vm_lab.zip orchestration lineage
Integrated: 2026-08-04
Purpose: Exposes only the host behaviors proven without virtualization metal
    and keeps lifecycle, guest runtime, and operator surfaces outside boot.
IO: Read-only doctor, plan, and topology commands only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
import shlex

from .config import ConfigurationError, default_configuration, load_configuration
from .doctor import run_doctor
from .host.planner import VMPlanner
from .host.qemu import QemuPlanError
from .topology import COMPONENTS


CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = CHECKOUT_ROOT if (CHECKOUT_ROOT / "SCOPE.md").is_file() else None


def _parser() -> argparse.ArgumentParser:
    """Build the canonical CLI argument parser."""

    parser = argparse.ArgumentParser(prog="vm-lab", description="Contract-first Somnus VM lab control plane")
    parser.add_argument("--config", help="Explicit VM lab TOML; defaults to install-safe per-user paths")
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor", help="Run read-only host and topology checks")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    topology = subcommands.add_parser("topology", help="Show live, cold, lineage, and quarantine placement")
    topology.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    plan = subcommands.add_parser("plan", help="Build QEMU argv without writing state")
    plan.add_argument("--name", required=True)
    plan.add_argument("--disk", required=True)
    plan.add_argument("--memory-mib", type=int, default=4096)
    plan.add_argument("--vcpus", type=int, default=2)
    plan.add_argument("--tcg", action="store_true", help="Disable KVM for this plan")
    plan.add_argument("--json", action="store_true")

    return parser


def _run_topology(as_json: bool) -> int:
    """Emit the machine-readable component boundary registry."""

    payload = [
        {
            "name": spec.name,
            "boundary": spec.boundary.value,
            "disposition": spec.disposition.value,
            "path": spec.relative_path,
            "boot_import": spec.boot_import,
            "reason": spec.reason,
        }
        for spec in COMPONENTS
    ]
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in payload:
            print(f"{item['disposition']:<10} {item['boundary']:<8} {item['name']:<24} {item['path']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one canonical host-control command."""

    args = _parser().parse_args(argv)
    try:
        if args.command == "topology":
            return _run_topology(args.json)
        config = load_configuration(args.config) if args.config else default_configuration()
        if args.command == "doctor":
            report = run_doctor(config, SOURCE_ROOT)
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
            else:
                for check in report.checks:
                    print(f"{check.status.value.upper():<5} {check.name:<20} {check.detail}")
            return 0 if report.healthy else 1
        if args.command == "plan":
            record, plan = VMPlanner(config).preview(
                name=args.name,
                disk_path=args.disk,
                memory_mib=args.memory_mib,
                vcpus=args.vcpus,
                enable_kvm=False if args.tcg else None,
            )
            # The canonical protocol record is secret-free by construction.
            # Planning never generates, stores, or redacts an agent credential.
            record_payload = record.to_dict()
            payload = {"record": record_payload, "argv": list(plan.argv), "ports_reserved": False}
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(shlex.join(plan.argv))
            return 0
        raise QemuPlanError(f"Unsupported command: {args.command}")
    except (ConfigurationError, QemuPlanError, ValueError, OSError) as exc:
        print(f"vm-lab: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
