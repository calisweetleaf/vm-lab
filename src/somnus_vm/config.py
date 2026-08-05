"""TOML configuration for the Somnus VM lab host control plane.

Source: vm_settings.py and vm_supervisor.py lineage
Integrated: 2026-08-04
Purpose: Reduces the live host configuration to values the control plane
    actually consumes and resolves every path before a process boundary.
IO: Reads one local TOML file; performs no writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigurationError(ValueError):
    """Raised when the host TOML file is missing or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class PortRange:
    """Inclusive host-port allocation interval."""

    start: int
    end: int

    def __post_init__(self) -> None:
        """Validate an unprivileged, non-empty port interval."""

        if self.start < 1024 or self.end > 65535 or self.start > self.end:
            raise ConfigurationError(f"Invalid port range: {self.start}-{self.end}")

    def values(self) -> range:
        """Return the inclusive range as an iterable."""

        return range(self.start, self.end + 1)


@dataclass(frozen=True, slots=True)
class HostConfiguration:
    """Resolved settings consumed by the host supervisor."""

    state_dir: Path
    runtime_dir: Path
    base_image: Path
    qemu_binary: str
    qemu_img_binary: str
    enable_kvm: bool
    ssh_ports: PortRange
    agent_ports: PortRange
    vnc_ports: PortRange
    agent_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ComponentConfiguration:
    """Boot policy for optional capability planes."""

    guest_runtime_candidates: bool = False
    operator_shell: bool = False
    native_tools: bool = False
    file_processing: bool = False
    digital_twin_lineage: bool = False
    quarantined_runtime: bool = False


@dataclass(frozen=True, slots=True)
class LabConfiguration:
    """Complete configuration loaded by the canonical CLI."""

    profile: str
    host: HostConfiguration
    components: ComponentConfiguration
    source_path: Path | None


def _require_table(payload: dict[str, object], key: str) -> dict[str, object]:
    """Return one TOML table or raise a configuration error."""

    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"Missing [{key}] table")
    return value


def _resolve_path(base_dir: Path, value: object, key: str) -> Path:
    """Resolve one required path relative to the configuration file."""

    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty path string")
    if any(character in value for character in ("\n", "\r", "\0")):
        raise ConfigurationError(f"{key} contains an unsafe control character")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _load_range(table: dict[str, object], key: str) -> PortRange:
    """Load a two-integer inclusive port range."""

    value = table.get(key)
    if not isinstance(value, list) or len(value) != 2:
        raise ConfigurationError(f"host.{key} must contain [start, end]")
    try:
        return PortRange(start=int(value[0]), end=int(value[1]))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"host.{key} must contain integers") from exc


def _optional_bool(table: dict[str, object], key: str, default: bool = False) -> bool:
    """Load a TOML boolean without accepting truthy strings or integers."""

    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} must be a boolean")
    return value


def _optional_string(table: dict[str, object], key: str, default: str) -> str:
    """Load a non-empty optional string."""

    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value.strip()


def load_configuration(path: str | Path) -> LabConfiguration:
    """Read and validate one VM lab TOML configuration.

    Args:
        path: TOML file path.

    Returns:
        Fully resolved LabConfiguration.

    Raises:
        ConfigurationError: If the file cannot be read or fails validation.
    """

    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {config_path}: {exc}") from exc

    base_dir = config_path.parent
    lab_table = _require_table(payload, "vm_lab")
    host_table = _require_table(payload, "host")
    component_table = _require_table(payload, "components")

    profile = lab_table.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise ConfigurationError("vm_lab.profile must be a non-empty string")

    try:
        timeout = float(host_table.get("agent_timeout_seconds", 30.0))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("host.agent_timeout_seconds must be numeric") from exc
    if timeout <= 0:
        raise ConfigurationError("host.agent_timeout_seconds must be positive")

    vnc_ports = _load_range(host_table, "vnc_ports")
    if vnc_ports.start < 5900:
        raise ConfigurationError("host.vnc_ports must start at 5900 or greater")
    state_dir = _resolve_path(base_dir, host_table.get("state_dir"), "host.state_dir")
    runtime_dir = _resolve_path(base_dir, host_table.get("runtime_dir"), "host.runtime_dir")
    if "," in str(state_dir):
        raise ConfigurationError("host.state_dir contains a character QEMU serial paths cannot represent safely")
    if any(character in str(runtime_dir) for character in (",", "\n", "\r", "\0")):
        raise ConfigurationError("host.runtime_dir contains a character QEMU cannot represent safely")
    host = HostConfiguration(
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        base_image=_resolve_path(base_dir, host_table.get("base_image"), "host.base_image"),
        qemu_binary=_optional_string(host_table, "qemu_binary", "qemu-system-x86_64"),
        qemu_img_binary=_optional_string(host_table, "qemu_img_binary", "qemu-img"),
        enable_kvm=_optional_bool(host_table, "enable_kvm", True),
        ssh_ports=_load_range(host_table, "ssh_ports"),
        agent_ports=_load_range(host_table, "agent_ports"),
        vnc_ports=vnc_ports,
        agent_timeout_seconds=timeout,
    )
    components = ComponentConfiguration(
        guest_runtime_candidates=_optional_bool(component_table, "guest_runtime_candidates"),
        operator_shell=_optional_bool(component_table, "operator_shell"),
        native_tools=_optional_bool(component_table, "native_tools"),
        file_processing=_optional_bool(component_table, "file_processing"),
        digital_twin_lineage=_optional_bool(component_table, "digital_twin_lineage"),
        quarantined_runtime=_optional_bool(component_table, "quarantined_runtime"),
    )
    if components.file_processing and not components.guest_runtime_candidates:
        raise ConfigurationError("file_processing requires guest_runtime_candidates")
    if components.native_tools and not components.operator_shell:
        raise ConfigurationError("native_tools requires operator_shell")
    if components.quarantined_runtime:
        raise ConfigurationError("quarantined_runtime cannot be enabled by configuration")

    return LabConfiguration(
        profile=profile.strip(),
        host=host,
        components=components,
        source_path=config_path,
    )


def default_configuration() -> LabConfiguration:
    """Build an install-safe per-user configuration without repo-relative files."""

    state_base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    data_base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    runtime_value = os.environ.get("XDG_RUNTIME_DIR")
    runtime_base = Path(runtime_value) if runtime_value else Path("/tmp") / f"somnus-vm-{os.getuid()}"
    return LabConfiguration(
        profile="user-tcg",
        host=HostConfiguration(
            state_dir=(state_base / "somnus-vm").expanduser().resolve(),
            runtime_dir=runtime_base.expanduser().resolve(),
            base_image=(data_base / "somnus-vm" / "base_ai_os.qcow2").expanduser().resolve(),
            qemu_binary="qemu-system-x86_64",
            qemu_img_binary="qemu-img",
            enable_kvm=False,
            ssh_ports=PortRange(2222, 2299),
            agent_ports=PortRange(9901, 9978),
            vnc_ports=PortRange(5900, 5977),
            agent_timeout_seconds=30.0,
        ),
        components=ComponentConfiguration(),
        source_path=None,
    )
