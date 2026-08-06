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
import re
from dataclasses import dataclass
from pathlib import Path
import unicodedata
from typing import Final

from ..config import HostConfiguration
from ..contracts.vm import VMRecord
from .qemu_process import QemuProcessError, command_sha256 as _process_command_sha256


_QMP_PATH_MAX_BYTES: Final[int] = 103
_REGULAR_PATH_MAX_BYTES: Final[int] = 4_096
_ARGV_ITEM_MAX_BYTES: Final[int] = 16_384
_ARGV_TOTAL_MAX_BYTES: Final[int] = 65_536
_ARGV_MAX_ITEMS: Final[int] = 128
_SANDBOX_POLICY: Final[str] = (
    "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"
)
_BLOCK_NODE_NAME: Final[str] = "somnus-disk"
_BLOCK_DEVICE_ID: Final[str] = "somnus-root-disk"
QEMU_OVERLAY_FDSET_ID: Final[int] = 1
QEMU_BASE_FDSET_ID: Final[int] = 2
QEMU_OVERLAY_FDSET_PATH: Final[str] = (
    f"/dev/fdset/{QEMU_OVERLAY_FDSET_ID}"
)
QEMU_BASE_FDSET_PATH: Final[str] = f"/dev/fdset/{QEMU_BASE_FDSET_ID}"
QEMU_OVERLAY_FDSET_OPAQUE: Final[str] = "somnus-overlay-rw"
QEMU_BASE_FDSET_OPAQUE: Final[str] = "somnus-base-ro"
QEMU_OVERLAY_FILE_NODE: Final[str] = "somnus-overlay-file"
QEMU_BASE_FILE_NODE: Final[str] = "somnus-base-file"
QEMU_BASE_FORMAT_NODE: Final[str] = "somnus-base-qcow2"
_FORBIDDEN_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "-add-fd",
        "-daemonize",
        "-D",
        "-fw_cfg",
        "-net",
        "-netdev",
        "-object",
        "-readconfig",
        "-spice",
        "-vnc",
        "-writeconfig",
    }
)


class QemuPlanError(ValueError):
    """Raised when a safe launch plan cannot be represented."""


def _contains_control_or_surrogate(value: str) -> bool:
    """Return whether text is unsafe at an argv or local-path boundary."""

    return any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in value
    )


def _validate_path_text(
    value: str,
    label: str,
    *,
    maximum_bytes: int,
    reject_comma: bool,
) -> None:
    """Validate one absolute, lexically canonical Linux path without I/O."""

    if not isinstance(value, str) or not value:
        raise QemuPlanError(f"{label} must be a non-empty string")
    if _contains_control_or_surrogate(value):
        raise QemuPlanError(f"{label} contains a control or surrogate character")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise QemuPlanError(f"{label} must be valid UTF-8") from exc
    if len(encoded) > maximum_bytes:
        raise QemuPlanError(
            f"{label} exceeds its {maximum_bytes}-byte encoded path limit"
        )
    if not value.startswith("/") or value == "/" or value.startswith("//"):
        raise QemuPlanError(f"{label} must be an absolute non-root Linux path")
    if os.path.normpath(value) != value:
        raise QemuPlanError(f"{label} must be lexically canonical")
    if reject_comma and "," in value:
        raise QemuPlanError(
            f"{label} contains ',' which is ambiguous at the QEMU boundary"
        )


def _validate_plan_path(
    value: Path,
    label: str,
    *,
    maximum_bytes: int = _REGULAR_PATH_MAX_BYTES,
) -> None:
    """Validate one generated QEMU control or log pathname."""

    if not isinstance(value, Path):
        raise QemuPlanError(f"{label} must be a Path")
    _validate_path_text(
        str(value),
        label,
        maximum_bytes=maximum_bytes,
        reject_comma=True,
    )


def _command_sha256(argv: tuple[str, ...]) -> str:
    """Use the process owner's exact command-identity contract."""

    try:
        return _process_command_sha256(argv)
    except QemuProcessError as exc:
        raise QemuPlanError("argv violates the process identity contract") from exc


def _option_value(argv: tuple[str, ...], option: str) -> str:
    """Return one required option value and reject duplicates or truncation."""

    indexes = [
        index
        for index, item in enumerate(argv[1:], start=1)
        if item == option
    ]
    if len(indexes) != 1:
        raise QemuPlanError(f"QEMU plan must contain exactly one {option}")
    index = indexes[0]
    if index + 1 >= len(argv):
        raise QemuPlanError(f"QEMU plan option {option} is missing its value")
    return argv[index + 1]


def _strict_inherited_fd(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 3
        or value > 2**31 - 1
    ):
        raise QemuPlanError(f"{label} must be an inherited descriptor")
    return value


def _fdset_option(fd: int, *, base: bool) -> str:
    selected = _strict_inherited_fd(
        fd,
        "base FD" if base else "overlay FD",
    )
    return (
        f"fd={selected},"
        f"set={QEMU_BASE_FDSET_ID if base else QEMU_OVERLAY_FDSET_ID},"
        f"opaque={QEMU_BASE_FDSET_OPAQUE if base else QEMU_OVERLAY_FDSET_OPAQUE}"
    )


def _encode_blockdev(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
    )


def _fd_bound_blockdevs() -> tuple[str, ...]:
    """Return the explicit overlay-to-base node graph in dependency order."""

    return (
        _encode_blockdev(
            {
                "auto-read-only": False,
                "driver": "file",
                "filename": QEMU_OVERLAY_FDSET_PATH,
                "locking": "on",
                "node-name": QEMU_OVERLAY_FILE_NODE,
                "read-only": False,
            }
        ),
        _encode_blockdev(
            {
                "auto-read-only": False,
                "driver": "file",
                "filename": QEMU_BASE_FDSET_PATH,
                "locking": "on",
                "node-name": QEMU_BASE_FILE_NODE,
                "read-only": True,
            }
        ),
        _encode_blockdev(
            {
                "auto-read-only": False,
                "backing": None,
                "driver": "qcow2",
                "file": QEMU_BASE_FILE_NODE,
                "node-name": QEMU_BASE_FORMAT_NODE,
                "read-only": True,
            }
        ),
        _encode_blockdev(
            {
                "auto-read-only": False,
                "backing": QEMU_BASE_FORMAT_NODE,
                "driver": "qcow2",
                "file": QEMU_OVERLAY_FILE_NODE,
                "node-name": _BLOCK_NODE_NAME,
                "read-only": False,
            }
        ),
    )


def _blockdev_argv(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for value in values for item in ("-blockdev", value))


def bind_block_fds(
    plan: QemuLaunchPlan,
    *,
    overlay_fd: int,
    base_fd: int,
) -> tuple[str, ...]:
    """Derive the only executable argv from a path-bearing public plan.

    The public plan remains a non-mutating declaration projection.  The
    internal process owner replaces its single pathname block graph with two
    inherited fdsets and an explicit qcow2 overlay-to-base graph before QEMU
    can open either image.
    """

    if not isinstance(plan, QemuLaunchPlan):
        raise TypeError("plan must be a QemuLaunchPlan")
    overlay = _strict_inherited_fd(overlay_fd, "overlay FD")
    base = _strict_inherited_fd(base_fd, "base FD")
    if overlay == base:
        raise QemuPlanError("overlay and base FDs must be distinct")
    indexes = [
        index
        for index, value in enumerate(plan.argv)
        if value == "-blockdev"
    ]
    if len(indexes) != 1 or indexes[0] + 1 >= len(plan.argv):
        raise QemuPlanError("planned blockdev boundary is not unique")
    block_index = indexes[0]
    bound = (
        *plan.argv[:block_index],
        "-add-fd",
        _fdset_option(overlay, base=False),
        "-add-fd",
        _fdset_option(base, base=True),
        *_blockdev_argv(_fd_bound_blockdevs()),
        *plan.argv[block_index + 2 :],
    )
    _validate_argv(tuple(bound), allow_storage_fdsets=True)
    return tuple(bound)


def validate_fd_bound_execution(
    planned_argv: tuple[str, ...],
    executed_argv: tuple[str, ...],
) -> tuple[int, int]:
    """Validate an exact planned-to-executed fdset transformation."""

    _validate_argv(planned_argv)
    _validate_argv(executed_argv, allow_storage_fdsets=True)
    add_indexes = [
        index
        for index, value in enumerate(executed_argv)
        if value == "-add-fd"
    ]
    if len(add_indexes) != 2:
        raise QemuPlanError("execution must bind exactly two storage fdsets")
    values: dict[int, int] = {}
    patterns = (
        (
            QEMU_OVERLAY_FDSET_ID,
            QEMU_OVERLAY_FDSET_OPAQUE,
        ),
        (
            QEMU_BASE_FDSET_ID,
            QEMU_BASE_FDSET_OPAQUE,
        ),
    )
    for index, (set_id, opaque) in zip(add_indexes, patterns, strict=True):
        if index + 1 >= len(executed_argv):
            raise QemuPlanError("execution fdset option is truncated")
        match = re.fullmatch(
            rf"fd=([1-9][0-9]*),set={set_id},opaque={re.escape(opaque)}",
            executed_argv[index + 1],
        )
        if match is None:
            raise QemuPlanError("execution fdset option is not canonical")
        values[set_id] = _strict_inherited_fd(
            int(match.group(1)),
            "execution storage FD",
        )
    overlay = values[QEMU_OVERLAY_FDSET_ID]
    base = values[QEMU_BASE_FDSET_ID]
    if overlay == base:
        raise QemuPlanError("execution storage FDs are aliased")
    # Log destinations live beside, not inside, the command vector.  Validate
    # the exact storage-only rewrite directly rather than constructing a
    # synthetic launch plan with invented log paths.
    block_index = planned_argv.index("-blockdev")
    expected = (
        *planned_argv[:block_index],
        "-add-fd",
        _fdset_option(overlay, base=False),
        "-add-fd",
        _fdset_option(base, base=True),
        *_blockdev_argv(_fd_bound_blockdevs()),
        *planned_argv[block_index + 2 :],
    )
    if executed_argv[1:] != tuple(expected)[1:]:
        raise QemuPlanError(
            "execution rewrites more than the reviewed fd-bound block graph"
        )
    return overlay, base


def _require_flag_once(argv: tuple[str, ...], option: str) -> None:
    """Require exactly one valueless hardening option."""

    if sum(item == option for item in argv[1:]) != 1:
        raise QemuPlanError(f"QEMU plan must contain exactly one {option}")


def _validate_smp_topology(value: str) -> None:
    """Require one explicit, deterministic single-socket CPU topology."""

    parts = value.split(",")
    if len(parts) != 5:
        raise QemuPlanError("QEMU SMP topology must contain five explicit fields")
    expected_keys = ("cpus", "sockets", "dies", "cores", "threads")
    values: dict[str, int] = {}
    for part, expected_key in zip(parts, expected_keys, strict=True):
        key, separator, raw_value = part.partition("=")
        if separator != "=" or key != expected_key:
            raise QemuPlanError("QEMU SMP topology fields are not canonical")
        if (
            not raw_value
            or not raw_value.isascii()
            or not raw_value.isdecimal()
            or raw_value.startswith("0")
        ):
            raise QemuPlanError("QEMU SMP topology values must be canonical integers")
        parsed = int(raw_value)
        if parsed < 1 or parsed > 4_096:
            raise QemuPlanError("QEMU SMP topology value is outside policy")
        values[key] = parsed
    if (
        values["sockets"] != 1
        or values["dies"] != 1
        or values["threads"] != 1
        or values["cores"] != values["cpus"]
    ):
        raise QemuPlanError(
            "QEMU SMP topology must map one vCPU to each single-threaded core"
        )


def _validate_argv(
    argv: tuple[str, ...],
    *,
    allow_storage_fdsets: bool = False,
) -> None:
    """Enforce the immutable, headless, no-network P4 command membrane."""

    if not isinstance(argv, tuple) or not argv or len(argv) > _ARGV_MAX_ITEMS:
        raise QemuPlanError(
            f"argv must be a non-empty tuple of at most {_ARGV_MAX_ITEMS} items"
        )
    total_bytes = 0
    for index, item in enumerate(argv):
        if not isinstance(item, str) or not item:
            raise QemuPlanError(f"argv[{index}] must be a non-empty string")
        if _contains_control_or_surrogate(item):
            raise QemuPlanError(
                f"argv[{index}] contains a control or surrogate character"
            )
        try:
            item_bytes = item.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise QemuPlanError(f"argv[{index}] must be valid UTF-8") from exc
        if len(item_bytes) > _ARGV_ITEM_MAX_BYTES:
            raise QemuPlanError(
                f"argv[{index}] exceeds {_ARGV_ITEM_MAX_BYTES} encoded bytes"
            )
        total_bytes += len(item_bytes) + 1
    if total_bytes > _ARGV_TOTAL_MAX_BYTES:
        raise QemuPlanError(
            f"argv exceeds the {_ARGV_TOTAL_MAX_BYTES}-byte plan limit"
        )

    option_tokens = frozenset(argv[1:])
    forbidden_options = _FORBIDDEN_OPTIONS
    if allow_storage_fdsets:
        forbidden_options = forbidden_options - {"-add-fd"}
    forbidden = sorted(option_tokens & forbidden_options)
    if forbidden:
        raise QemuPlanError(
            "QEMU plan contains forbidden pre-P5 or secret-bearing options: "
            + ", ".join(forbidden)
        )
    for flag in ("-no-user-config", "-nodefaults", "-no-reboot"):
        _require_flag_once(argv, flag)
    if _option_value(argv, "-sandbox") != _SANDBOX_POLICY:
        raise QemuPlanError("QEMU plan sandbox policy is not the closed P4 policy")
    if _option_value(argv, "-display") != "none":
        raise QemuPlanError("QEMU display must remain disabled before P5")
    if _option_value(argv, "-nic") != "none":
        raise QemuPlanError("QEMU networking must remain disabled before P5")
    if _option_value(argv, "-monitor") != "none":
        raise QemuPlanError("QEMU HMP monitor must remain disabled")
    _validate_smp_topology(_option_value(argv, "-smp"))
    if _option_value(argv, "-device") != (
        f"virtio-blk-pci,id={_BLOCK_DEVICE_ID},drive={_BLOCK_NODE_NAME}"
    ):
        raise QemuPlanError("QEMU root block frontend identity is not canonical")


@dataclass(frozen=True, slots=True)
class QemuLaunchPlan:
    """Fully resolved, validated process intent with no machine-side claim."""

    argv: tuple[str, ...]
    qmp_socket: Path
    pid_file: Path
    serial_log_path: Path
    qemu_log_path: Path
    command_sha256: str

    def __post_init__(self) -> None:
        """Reject a plan whose paths, hardening options, or hash disagree."""

        _validate_argv(self.argv)
        _validate_plan_path(
            self.qmp_socket,
            "QMP socket",
            maximum_bytes=_QMP_PATH_MAX_BYTES,
        )
        _validate_plan_path(self.pid_file, "QEMU pidfile")
        _validate_plan_path(self.serial_log_path, "serial log")
        _validate_plan_path(self.qemu_log_path, "QEMU log")
        if len(
            {
                self.qmp_socket,
                self.pid_file,
                self.serial_log_path,
                self.qemu_log_path,
            }
        ) != 4:
            raise QemuPlanError("QMP, pidfile, serial log, and QEMU log must be distinct")

        if _option_value(self.argv, "-qmp") != (
            f"unix:{self.qmp_socket},server=on,wait=off"
        ):
            raise QemuPlanError("QMP argv does not match the canonical socket")
        if _option_value(self.argv, "-pidfile") != str(self.pid_file):
            raise QemuPlanError("pidfile argv does not match the canonical path")
        if _option_value(self.argv, "-serial") != "stdio":
            raise QemuPlanError(
                "serial output must enter the supervised redaction pipe"
            )
        if self.command_sha256 != _command_sha256(self.argv):
            raise QemuPlanError("command_sha256 does not match the exact argv vector")

    @property
    def log_path(self) -> Path:
        """Compatibility projection for the VM record's primary serial log."""

        return self.serial_log_path


class QemuCommandBuilder:
    """Build non-mutating QEMU argv from one validated VM record."""

    def __init__(self, config: HostConfiguration) -> None:
        """Retain validated host configuration."""

        self._config = config

    def build(self, record: VMRecord) -> QemuLaunchPlan:
        """Create a fully explicit launch plan for a future process owner."""

        if not isinstance(record, VMRecord):
            raise QemuPlanError("record must be a canonical VMRecord")
        disk_path = record.definition.disk_path
        _validate_path_text(
            disk_path,
            "VM disk path",
            maximum_bytes=_REGULAR_PATH_MAX_BYTES,
            reject_comma=False,
        )
        secret_root = str(self._config.security.secret_root)
        if disk_path == secret_root or disk_path.startswith(f"{secret_root}/"):
            raise QemuPlanError("VM disk path cannot enter the host secret root")

        qmp_socket = self._config.daemon.runtime_root / f"{record.vm_id}.qmp"
        pid_file = self._config.daemon.runtime_root / f"{record.vm_id}.pid"
        serial_log_path = self._config.storage.log_root / f"{record.vm_id}.log"
        qemu_log_path = self._config.storage.log_root / f"{record.vm_id}.qemu.log"
        _validate_plan_path(
            qmp_socket,
            "QMP socket",
            maximum_bytes=_QMP_PATH_MAX_BYTES,
        )
        _validate_plan_path(pid_file, "QEMU pidfile")
        _validate_plan_path(serial_log_path, "serial log")
        _validate_plan_path(qemu_log_path, "QEMU log")

        planned_runtime = (
            record.qmp_socket,
            record.pid_file,
            record.log_path,
        )
        if any(planned_runtime):
            expected_runtime = (
                str(qmp_socket),
                str(pid_file),
                str(serial_log_path),
            )
            if planned_runtime != expected_runtime:
                raise QemuPlanError(
                    "record runtime paths do not match the host-owned canonical paths"
                )
        if record.definition.enable_kvm and not self._config.enable_kvm:
            raise QemuPlanError(
                "VM definition requests KVM but the selected host profile disables it"
            )

        blockdev = json.dumps(
            {
                "driver": "qcow2",
                "node-name": _BLOCK_NODE_NAME,
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
            "-no-user-config",
            "-nodefaults",
            "-name",
            f"guest={record.definition.name}",
            "-uuid",
            str(record.vm_id),
            "-m",
            str(record.definition.memory_mib),
            "-smp",
            (
                f"cpus={record.definition.vcpus},sockets=1,dies=1,"
                f"cores={record.definition.vcpus},threads=1"
            ),
        ]
        if record.definition.enable_kvm:
            argv.extend(["-enable-kvm", "-cpu", "host"])
        else:
            argv.extend(["-machine", "accel=tcg", "-cpu", "max"])
        argv.extend(
            [
                "-blockdev",
                blockdev,
                "-device",
                (
                    f"virtio-blk-pci,id={_BLOCK_DEVICE_ID},"
                    f"drive={_BLOCK_NODE_NAME}"
                ),
                "-sandbox",
                _SANDBOX_POLICY,
                "-display",
                "none",
                "-nic",
                "none",
                "-qmp",
                f"unix:{qmp_socket},server=on,wait=off",
                "-pidfile",
                str(pid_file),
                "-serial",
                "stdio",
                "-monitor",
                "none",
                "-no-reboot",
            ]
        )
        frozen_argv = tuple(argv)
        return QemuLaunchPlan(
            argv=frozen_argv,
            qmp_socket=qmp_socket,
            pid_file=pid_file,
            serial_log_path=serial_log_path,
            qemu_log_path=qemu_log_path,
            command_sha256=_command_sha256(frozen_argv),
        )
