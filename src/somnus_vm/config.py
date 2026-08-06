"""Strict host settings for the Somnus VM control plane.

Source: promoted v0.1 config plus PLAN.md Phase P1 settings requirements
Integrated: 2026-08-05
Purpose: Separates daemon, storage, network, guest-agent, security, and policy
    settings while preserving the v0.1 ``LabConfiguration.host`` attributes.
IO: Reads one explicitly selected TOML file; generated profiles perform no
    writes and require no checkout-local resources.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import tomllib
import unicodedata
from typing import Final


__all__ = [
    "AgentSettings",
    "ComponentConfiguration",
    "ConfigurationError",
    "DaemonSettings",
    "HostConfiguration",
    "LabConfiguration",
    "NetworkSettings",
    "PolicySettings",
    "PortRange",
    "SecuritySettings",
    "StorageSettings",
    "default_configuration",
    "load_configuration",
    "named_profile",
]


_PROFILE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_BINARY_MAX_CHARACTERS: Final[int] = 4_096
_SOCKET_PATH_MAX_BYTES: Final[int] = 107
_QMP_PROBE_NAME: Final[str] = "00000000-0000-0000-0000-000000000000.qmp"
_MISSING: Final[object] = object()
_NAMED_PROFILES: Final[frozenset[str]] = frozenset(
    {"lab-tcg", "host-kvm", "constrained-host"}
)

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "vm_lab",
        "host",
        "components",
        "daemon",
        "storage",
        "network",
        "agent",
        "security",
        "policy",
    }
)
_LAB_KEYS: Final[frozenset[str]] = frozenset({"profile"})
_HOST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "state_dir",
        "runtime_dir",
        "base_image",
        "qemu_binary",
        "qemu_img_binary",
        "enable_kvm",
        "ssh_ports",
        "agent_ports",
        "vnc_ports",
        "agent_timeout_seconds",
    }
)
_COMPONENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "guest_runtime_candidates",
        "operator_shell",
        "native_tools",
        "file_processing",
        "digital_twin_lineage",
        "quarantined_runtime",
    }
)
_DAEMON_KEYS: Final[frozenset[str]] = frozenset(
    {"runtime_root", "control_socket", "request_timeout_seconds"}
)
_STORAGE_KEYS: Final[frozenset[str]] = frozenset(
    {"state_root", "image_root", "base_image", "backup_root", "log_root"}
)
_NETWORK_KEYS: Final[frozenset[str]] = frozenset(
    {"bind_host", "ssh_ports", "agent_ports", "vnc_ports"}
)
_AGENT_KEYS: Final[frozenset[str]] = frozenset({"timeout_seconds"})
_SECURITY_KEYS: Final[frozenset[str]] = frozenset(
    {"credential_provider", "secret_root", "guest_agent_token_file"}
)
_POLICY_KEYS: Final[frozenset[str]] = frozenset({"max_active_vms"})


class ConfigurationError(ValueError):
    """Raised when host settings are missing, ambiguous, or unsafe."""


def _strict_bool(value: object, label: str) -> bool:
    """Require a TOML boolean without truthy coercion."""

    if not isinstance(value, bool):
        raise ConfigurationError(f"{label} must be a boolean")
    return value


def _strict_int(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Require a strict integer and reject booleans."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigurationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{label} must be at most {maximum}")
    return value


def _finite_number(
    value: object,
    label: str,
    *,
    minimum_exclusive: float = 0.0,
    maximum: float = 86_400.0,
) -> float:
    """Require one finite, positive TOML integer or float."""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigurationError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigurationError(f"{label} must be finite")
    if result <= minimum_exclusive or result > maximum:
        raise ConfigurationError(
            f"{label} must be greater than {minimum_exclusive:g} and at most {maximum:g}"
        )
    return result


def _bounded_string(
    value: object,
    label: str,
    *,
    default: str | object = _MISSING,
    maximum: int = _BINARY_MAX_CHARACTERS,
) -> str:
    """Require one non-empty, bounded string without control characters."""

    candidate = default if value is _MISSING else value
    if not isinstance(candidate, str) or not candidate.strip():
        raise ConfigurationError(f"{label} must be a non-empty string")
    if len(candidate) > maximum:
        raise ConfigurationError(f"{label} must not exceed {maximum} characters")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in candidate
    ):
        raise ConfigurationError(f"{label} contains a control or surrogate character")
    return candidate.strip()


def _check_keys(table: dict[str, object], allowed: frozenset[str], label: str) -> None:
    """Reject unknown settings before any value is coerced or ignored."""

    unknown = set(table) - allowed
    if unknown:
        raise ConfigurationError(
            f"{label} contains unknown keys: {', '.join(sorted(unknown))}"
        )


def _table(
    payload: dict[str, object],
    key: str,
    allowed: frozenset[str],
    *,
    required: bool,
) -> dict[str, object]:
    """Return one exact TOML table or an empty optional table."""

    value = payload.get(key, _MISSING)
    if value is _MISSING:
        if required:
            raise ConfigurationError(f"Missing [{key}] table")
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"[{key}] must be a table")
    result = dict(value)
    _check_keys(result, allowed, f"[{key}]")
    return result


def _select_setting(
    legacy: dict[str, object],
    legacy_key: str,
    current: dict[str, object],
    current_key: str,
    label: str,
    *,
    default: object = _MISSING,
) -> object:
    """Select one legacy or split-table setting and reject duplicate authority."""

    legacy_present = legacy_key in legacy
    current_present = current_key in current
    if legacy_present and current_present:
        raise ConfigurationError(
            f"{label} is declared in both [host] and its owning P1 settings table"
        )
    if legacy_present:
        return legacy[legacy_key]
    if current_present:
        return current[current_key]
    if default is not _MISSING:
        return default
    raise ConfigurationError(f"Missing required setting: {label}")


def _resolve_path(base_dir: Path, value: object, label: str) -> Path:
    """Resolve one required path and reject unsafe text before normalization."""

    text = _bounded_string(value, label)
    path = Path(text).expanduser()
    resolved = path.resolve() if path.is_absolute() else (base_dir / path).resolve()
    _validate_absolute_path(resolved, label)
    return resolved


def _validate_absolute_path(
    path: object,
    label: str,
    *,
    reject_comma: bool = False,
) -> Path:
    """Validate one normalized absolute local path."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise ConfigurationError(f"{label} must be an absolute Path")
    if path != path.resolve():
        raise ConfigurationError(f"{label} must be a canonical resolved path")
    text = str(path)
    if not text or text == path.anchor:
        raise ConfigurationError(f"{label} cannot be a filesystem root")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in text):
        raise ConfigurationError(f"{label} contains a control or surrogate character")
    if reject_comma and "," in text:
        raise ConfigurationError(
            f"{label} contains ',' which is ambiguous at the QEMU boundary"
        )
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigurationError(f"{label} must be valid UTF-8") from exc
    return path


def _ensure_descendant(path: Path, root: Path, label: str) -> None:
    """Require one path to be a strict descendant of its owning root."""

    if path == root or not path.is_relative_to(root):
        raise ConfigurationError(f"{label} must be a strict descendant of {root}")


def _validate_socket_path(path: Path, runtime_root: Path, label: str) -> None:
    """Require a safe AF_UNIX socket pathname owned by the runtime root."""

    _validate_absolute_path(path, label, reject_comma=True)
    _ensure_descendant(path, runtime_root, label)
    if len(os.fsencode(path)) > _SOCKET_PATH_MAX_BYTES:
        raise ConfigurationError(
            f"{label} exceeds the Linux AF_UNIX limit of {_SOCKET_PATH_MAX_BYTES} bytes"
        )


def _validate_distinct_roots(roots: dict[str, Path]) -> None:
    """Reject aliases and either-direction descendants among ownership roots."""

    items = tuple(roots.items())
    for index, (left_name, left_path) in enumerate(items):
        for right_name, right_path in items[index + 1 :]:
            if (
                left_path == right_path
                or left_path.is_relative_to(right_path)
                or right_path.is_relative_to(left_path)
            ):
                raise ConfigurationError(
                    f"{left_name} and {right_name} must be distinct non-descendant roots: "
                    f"{left_path} vs {right_path}"
                )


def _sibling_root(state_root: Path, suffix: str) -> Path:
    """Derive an isolated sibling root from the state root."""

    return state_root.with_name(f"{state_root.name}-{suffix}")


def _safe_binary(value: object, label: str, default: str) -> str:
    """Validate one argv-safe executable name or absolute path."""

    binary = _bounded_string(
        value if value is not _MISSING else _MISSING,
        label,
        default=default,
    )
    if "\x00" in binary:
        raise ConfigurationError(f"{label} contains a null byte")
    return binary


@dataclass(frozen=True, slots=True)
class PortRange:
    """Inclusive host-port allocation interval."""

    start: int
    end: int

    def __post_init__(self) -> None:
        """Validate strict, unprivileged, non-empty endpoints."""

        start = _strict_int(self.start, "PortRange.start", minimum=1024, maximum=65_535)
        end = _strict_int(self.end, "PortRange.end", minimum=1024, maximum=65_535)
        if start > end:
            raise ConfigurationError(f"Invalid port range: {start}-{end}")

    def values(self) -> range:
        """Return the inclusive range as an iterable."""

        return range(self.start, self.end + 1)


def _load_range(value: object, label: str) -> PortRange:
    """Load a strict two-integer inclusive port range."""

    if not isinstance(value, list) or len(value) != 2:
        raise ConfigurationError(f"{label} must contain [start, end]")
    return PortRange(
        start=_strict_int(value[0], f"{label}[0]"),
        end=_strict_int(value[1], f"{label}[1]"),
    )


@dataclass(frozen=True, slots=True)
class DaemonSettings:
    """Runtime-root and local control-socket settings for the future daemon."""

    runtime_root: Path
    control_socket: Path
    request_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        """Validate socket ownership, QMP path capacity, and time bounds."""

        _validate_absolute_path(
            self.runtime_root, "daemon.runtime_root", reject_comma=True
        )
        _validate_socket_path(
            self.control_socket, self.runtime_root, "daemon.control_socket"
        )
        qmp_probe = self.runtime_root / _QMP_PROBE_NAME
        if len(os.fsencode(qmp_probe)) > _SOCKET_PATH_MAX_BYTES:
            raise ConfigurationError(
                "daemon.runtime_root is too long for a VM UUID QMP socket"
            )
        object.__setattr__(
            self,
            "request_timeout_seconds",
            _finite_number(
                self.request_timeout_seconds, "daemon.request_timeout_seconds"
            ),
        )


@dataclass(frozen=True, slots=True)
class StorageSettings:
    """Resolved ownership roots and base-image reference."""

    state_root: Path
    image_root: Path
    base_image: Path
    backup_root: Path
    log_root: Path

    def __post_init__(self) -> None:
        """Validate roots and keep the base image inside its image owner."""

        _validate_absolute_path(
            self.state_root, "storage.state_root", reject_comma=True
        )
        _validate_absolute_path(self.image_root, "storage.image_root")
        _validate_absolute_path(self.base_image, "storage.base_image")
        _validate_absolute_path(self.backup_root, "storage.backup_root")
        _validate_absolute_path(
            self.log_root, "storage.log_root", reject_comma=True
        )
        _ensure_descendant(
            self.base_image, self.image_root, "storage.base_image"
        )


@dataclass(frozen=True, slots=True)
class NetworkSettings:
    """Loopback-only host forwarding and allocation ranges."""

    bind_host: str
    ssh_ports: PortRange
    agent_ports: PortRange
    vnc_ports: PortRange

    def __post_init__(self) -> None:
        """Validate local binding, port classes, and cross-role separation."""

        bind_host = _bounded_string(self.bind_host, "network.bind_host", maximum=64)
        if bind_host != "127.0.0.1":
            raise ConfigurationError(
                "network.bind_host must remain 127.0.0.1 until a non-local gate is proven"
            )
        for label, interval in (
            ("network.ssh_ports", self.ssh_ports),
            ("network.agent_ports", self.agent_ports),
            ("network.vnc_ports", self.vnc_ports),
        ):
            if not isinstance(interval, PortRange):
                raise ConfigurationError(f"{label} must be a PortRange")
        if self.vnc_ports.start < 5_900:
            raise ConfigurationError("network.vnc_ports must start at 5900 or greater")
        ranges = (
            ("network.ssh_ports", self.ssh_ports),
            ("network.agent_ports", self.agent_ports),
            ("network.vnc_ports", self.vnc_ports),
        )
        for index, (left_name, left) in enumerate(ranges):
            for right_name, right in ranges[index + 1 :]:
                if max(left.start, right.start) <= min(left.end, right.end):
                    raise ConfigurationError(
                        f"{left_name} and {right_name} must not overlap"
                    )
        object.__setattr__(self, "bind_host", bind_host)


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Guest-agent control timing without authentication material."""

    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        """Validate a finite bounded request deadline."""

        object.__setattr__(
            self,
            "timeout_seconds",
            _finite_number(self.timeout_seconds, "agent.timeout_seconds"),
        )


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    """References to host-owned secret provisioning locations, never values."""

    credential_provider: str
    secret_root: Path
    guest_agent_token_file: Path

    def __post_init__(self) -> None:
        """Allow only a local file reference beneath a dedicated secret root."""

        provider = _bounded_string(
            self.credential_provider, "security.credential_provider", maximum=32
        )
        if provider != "file":
            raise ConfigurationError(
                "security.credential_provider must be 'file' for the local-first profile"
            )
        _validate_absolute_path(self.secret_root, "security.secret_root")
        _validate_absolute_path(
            self.guest_agent_token_file, "security.guest_agent_token_file"
        )
        _ensure_descendant(
            self.guest_agent_token_file,
            self.secret_root,
            "security.guest_agent_token_file",
        )
        object.__setattr__(self, "credential_provider", provider)


@dataclass(frozen=True, slots=True)
class PolicySettings:
    """Operator activation policy independent of multi-VM correctness."""

    max_active_vms: int = 1

    def __post_init__(self) -> None:
        """Validate a bounded policy without changing allocation correctness."""

        _strict_int(
            self.max_active_vms,
            "policy.max_active_vms",
            minimum=1,
            maximum=256,
        )


@dataclass(frozen=True, slots=True)
class HostConfiguration:
    """Composed host settings with v0.1 compatibility properties."""

    qemu_binary: str
    qemu_img_binary: str
    enable_kvm: bool
    daemon: DaemonSettings
    storage: StorageSettings
    network: NetworkSettings
    agent: AgentSettings
    security: SecuritySettings
    policy: PolicySettings

    def __post_init__(self) -> None:
        """Validate direct composition and cross-owner root isolation."""

        object.__setattr__(
            self,
            "qemu_binary",
            _safe_binary(self.qemu_binary, "host.qemu_binary", "qemu-system-x86_64"),
        )
        object.__setattr__(
            self,
            "qemu_img_binary",
            _safe_binary(self.qemu_img_binary, "host.qemu_img_binary", "qemu-img"),
        )
        object.__setattr__(
            self,
            "enable_kvm",
            _strict_bool(self.enable_kvm, "host.enable_kvm"),
        )
        expected_types = (
            ("host.daemon", self.daemon, DaemonSettings),
            ("host.storage", self.storage, StorageSettings),
            ("host.network", self.network, NetworkSettings),
            ("host.agent", self.agent, AgentSettings),
            ("host.security", self.security, SecuritySettings),
            ("host.policy", self.policy, PolicySettings),
        )
        for label, value, expected in expected_types:
            if not isinstance(value, expected):
                raise ConfigurationError(f"{label} must be {expected.__name__}")
        _validate_distinct_roots(
            {
                "state root": self.storage.state_root,
                "runtime root": self.daemon.runtime_root,
                "image root": self.storage.image_root,
                "backup root": self.storage.backup_root,
                "log root": self.storage.log_root,
                "secret root": self.security.secret_root,
            }
        )

    @property
    def state_dir(self) -> Path:
        """Compatibility alias for the v0.1 host state directory."""

        return self.storage.state_root

    @property
    def runtime_dir(self) -> Path:
        """Compatibility alias for the v0.1 host runtime directory."""

        return self.daemon.runtime_root

    @property
    def base_image(self) -> Path:
        """Compatibility alias for the v0.1 base image."""

        return self.storage.base_image

    @property
    def ssh_ports(self) -> PortRange:
        """Compatibility alias for SSH forwarding ports."""

        return self.network.ssh_ports

    @property
    def agent_ports(self) -> PortRange:
        """Compatibility alias for guest-agent forwarding ports."""

        return self.network.agent_ports

    @property
    def vnc_ports(self) -> PortRange:
        """Compatibility alias for VNC display ports."""

        return self.network.vnc_ports

    @property
    def agent_timeout_seconds(self) -> float:
        """Compatibility alias for the guest-agent deadline."""

        return self.agent.timeout_seconds

    @property
    def max_active_vms(self) -> int:
        """Expose normal activation policy without constraining allocators."""

        return self.policy.max_active_vms


@dataclass(frozen=True, slots=True)
class ComponentConfiguration:
    """Boot policy for optional capability planes."""

    guest_runtime_candidates: bool = False
    operator_shell: bool = False
    native_tools: bool = False
    file_processing: bool = False
    digital_twin_lineage: bool = False
    quarantined_runtime: bool = False

    def __post_init__(self) -> None:
        """Validate strict flags and the current promotion dependencies."""

        for name in (
            "guest_runtime_candidates",
            "operator_shell",
            "native_tools",
            "file_processing",
            "digital_twin_lineage",
            "quarantined_runtime",
        ):
            _strict_bool(getattr(self, name), f"components.{name}")
        if self.file_processing and not self.guest_runtime_candidates:
            raise ConfigurationError(
                "components.file_processing requires guest_runtime_candidates"
            )
        if self.native_tools and not self.operator_shell:
            raise ConfigurationError(
                "components.native_tools requires operator_shell"
            )
        if self.quarantined_runtime:
            raise ConfigurationError(
                "components.quarantined_runtime cannot be enabled"
            )


@dataclass(frozen=True, slots=True)
class LabConfiguration:
    """Complete configuration loaded by the canonical CLI."""

    profile: str
    host: HostConfiguration
    components: ComponentConfiguration
    source_path: Path | None

    def __post_init__(self) -> None:
        """Validate direct construction and preserve one composed authority."""

        profile = _bounded_string(self.profile, "vm_lab.profile", maximum=64)
        if not _PROFILE_PATTERN.fullmatch(profile):
            raise ConfigurationError(
                "vm_lab.profile must be a lowercase machine token"
            )
        if not isinstance(self.host, HostConfiguration):
            raise ConfigurationError("host must be HostConfiguration")
        if not isinstance(self.components, ComponentConfiguration):
            raise ConfigurationError("components must be ComponentConfiguration")
        if self.source_path is not None:
            _validate_absolute_path(self.source_path, "source_path")
        object.__setattr__(self, "profile", profile)

    @property
    def daemon(self) -> DaemonSettings:
        """Return daemon settings from the host composition."""

        return self.host.daemon

    @property
    def storage(self) -> StorageSettings:
        """Return storage settings from the host composition."""

        return self.host.storage

    @property
    def network(self) -> NetworkSettings:
        """Return network settings from the host composition."""

        return self.host.network

    @property
    def agent(self) -> AgentSettings:
        """Return guest-agent settings from the host composition."""

        return self.host.agent

    @property
    def security(self) -> SecuritySettings:
        """Return security settings from the host composition."""

        return self.host.security

    @property
    def policy(self) -> PolicySettings:
        """Return activation policy from the host composition."""

        return self.host.policy


def _component_settings(table: dict[str, object]) -> ComponentConfiguration:
    """Load current component gates through strict booleans."""

    return ComponentConfiguration(
        guest_runtime_candidates=_strict_bool(
            table.get("guest_runtime_candidates", False),
            "components.guest_runtime_candidates",
        ),
        operator_shell=_strict_bool(
            table.get("operator_shell", False), "components.operator_shell"
        ),
        native_tools=_strict_bool(
            table.get("native_tools", False), "components.native_tools"
        ),
        file_processing=_strict_bool(
            table.get("file_processing", False), "components.file_processing"
        ),
        digital_twin_lineage=_strict_bool(
            table.get("digital_twin_lineage", False),
            "components.digital_twin_lineage",
        ),
        quarantined_runtime=_strict_bool(
            table.get("quarantined_runtime", False),
            "components.quarantined_runtime",
        ),
    )


def load_configuration(path: str | Path) -> LabConfiguration:
    """Read one exact legacy-compatible or split-table TOML configuration."""

    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"Configuration file not found: {config_path}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigurationError("Configuration root must be a TOML document")
    _check_keys(payload, _TOP_LEVEL_KEYS, "configuration")

    lab_table = _table(payload, "vm_lab", _LAB_KEYS, required=True)
    host_table = _table(payload, "host", _HOST_KEYS, required=True)
    component_table = _table(
        payload, "components", _COMPONENT_KEYS, required=True
    )
    daemon_table = _table(payload, "daemon", _DAEMON_KEYS, required=False)
    storage_table = _table(payload, "storage", _STORAGE_KEYS, required=False)
    network_table = _table(payload, "network", _NETWORK_KEYS, required=False)
    agent_table = _table(payload, "agent", _AGENT_KEYS, required=False)
    security_table = _table(payload, "security", _SECURITY_KEYS, required=False)
    policy_table = _table(payload, "policy", _POLICY_KEYS, required=False)

    profile = _bounded_string(
        lab_table.get("profile", _MISSING), "vm_lab.profile", maximum=64
    )
    base_dir = config_path.parent
    state_root = _resolve_path(
        base_dir,
        _select_setting(
            host_table,
            "state_dir",
            storage_table,
            "state_root",
            "storage.state_root",
        ),
        "storage.state_root",
    )
    runtime_root = _resolve_path(
        base_dir,
        _select_setting(
            host_table,
            "runtime_dir",
            daemon_table,
            "runtime_root",
            "daemon.runtime_root",
        ),
        "daemon.runtime_root",
    )
    base_image = _resolve_path(
        base_dir,
        _select_setting(
            host_table,
            "base_image",
            storage_table,
            "base_image",
            "storage.base_image",
        ),
        "storage.base_image",
    )
    image_root = _resolve_path(
        base_dir,
        storage_table.get("image_root", str(base_image.parent)),
        "storage.image_root",
    )
    backup_root = _resolve_path(
        base_dir,
        storage_table.get("backup_root", str(_sibling_root(state_root, "backups"))),
        "storage.backup_root",
    )
    log_root = _resolve_path(
        base_dir,
        storage_table.get("log_root", str(_sibling_root(state_root, "logs"))),
        "storage.log_root",
    )
    control_socket = _resolve_path(
        base_dir,
        daemon_table.get("control_socket", str(runtime_root / "control.sock")),
        "daemon.control_socket",
    )
    secret_root = _resolve_path(
        base_dir,
        security_table.get(
            "secret_root", str(_sibling_root(state_root, "secrets"))
        ),
        "security.secret_root",
    )
    token_file = _resolve_path(
        base_dir,
        security_table.get(
            "guest_agent_token_file", str(secret_root / "guest-agent.token")
        ),
        "security.guest_agent_token_file",
    )

    ssh_ports = _load_range(
        _select_setting(
            host_table,
            "ssh_ports",
            network_table,
            "ssh_ports",
            "network.ssh_ports",
        ),
        "network.ssh_ports",
    )
    agent_ports = _load_range(
        _select_setting(
            host_table,
            "agent_ports",
            network_table,
            "agent_ports",
            "network.agent_ports",
        ),
        "network.agent_ports",
    )
    vnc_ports = _load_range(
        _select_setting(
            host_table,
            "vnc_ports",
            network_table,
            "vnc_ports",
            "network.vnc_ports",
        ),
        "network.vnc_ports",
    )
    timeout = _finite_number(
        _select_setting(
            host_table,
            "agent_timeout_seconds",
            agent_table,
            "timeout_seconds",
            "agent.timeout_seconds",
            default=30.0,
        ),
        "agent.timeout_seconds",
    )

    host = HostConfiguration(
        qemu_binary=_safe_binary(
            host_table.get("qemu_binary", _MISSING),
            "host.qemu_binary",
            "qemu-system-x86_64",
        ),
        qemu_img_binary=_safe_binary(
            host_table.get("qemu_img_binary", _MISSING),
            "host.qemu_img_binary",
            "qemu-img",
        ),
        enable_kvm=_strict_bool(
            host_table.get("enable_kvm", True), "host.enable_kvm"
        ),
        daemon=DaemonSettings(
            runtime_root=runtime_root,
            control_socket=control_socket,
            request_timeout_seconds=_finite_number(
                daemon_table.get("request_timeout_seconds", 30.0),
                "daemon.request_timeout_seconds",
            ),
        ),
        storage=StorageSettings(
            state_root=state_root,
            image_root=image_root,
            base_image=base_image,
            backup_root=backup_root,
            log_root=log_root,
        ),
        network=NetworkSettings(
            bind_host=_bounded_string(
                network_table.get("bind_host", "127.0.0.1"),
                "network.bind_host",
                maximum=64,
            ),
            ssh_ports=ssh_ports,
            agent_ports=agent_ports,
            vnc_ports=vnc_ports,
        ),
        agent=AgentSettings(timeout_seconds=timeout),
        security=SecuritySettings(
            credential_provider=_bounded_string(
                security_table.get("credential_provider", "file"),
                "security.credential_provider",
                maximum=32,
            ),
            secret_root=secret_root,
            guest_agent_token_file=token_file,
        ),
        policy=PolicySettings(
            max_active_vms=_strict_int(
                policy_table.get("max_active_vms", 1),
                "policy.max_active_vms",
                minimum=1,
                maximum=256,
            )
        ),
    )
    return LabConfiguration(
        profile=profile,
        host=host,
        components=_component_settings(component_table),
        source_path=config_path,
    )


def _generated_base(
    value: str | Path | None,
    environment_key: str,
    fallback: Path,
    label: str,
) -> Path:
    """Resolve one injectable or XDG base without checkout-relative fallback."""

    selected: str | Path
    if value is not None:
        selected = value
    else:
        selected = os.environ.get(environment_key, str(fallback))
    path = Path(selected).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{label} must be absolute")
    return path.resolve()


def _generated_configuration(
    profile: str,
    *,
    enable_kvm: bool,
    constrained: bool,
    state_home: str | Path | None,
    data_home: str | Path | None,
    runtime_root: str | Path | None,
) -> LabConfiguration:
    """Build one install-safe profile entirely from user/runtime roots."""

    state_base = _generated_base(
        state_home,
        "XDG_STATE_HOME",
        Path.home() / ".local" / "state",
        "state_home",
    )
    data_base = _generated_base(
        data_home,
        "XDG_DATA_HOME",
        Path.home() / ".local" / "share",
        "data_home",
    )
    if runtime_root is None:
        runtime_value = os.environ.get("XDG_RUNTIME_DIR")
        runtime_base = (
            Path(runtime_value) / "somnus-vm"
            if runtime_value
            else Path("/tmp") / f"somnus-vm-{os.getuid()}"
        )
        selected_runtime = runtime_base / profile
    else:
        selected_runtime = Path(runtime_root)
    if not selected_runtime.expanduser().is_absolute():
        raise ConfigurationError("runtime_root must be absolute")
    runtime = selected_runtime.expanduser().resolve()

    state = (state_base / "somnus-vm").resolve()
    images = (data_base / "somnus-vm").resolve()
    width = 7 if constrained else 77
    host = HostConfiguration(
        qemu_binary="qemu-system-x86_64",
        qemu_img_binary="qemu-img",
        enable_kvm=enable_kvm,
        daemon=DaemonSettings(
            runtime_root=runtime,
            control_socket=runtime / "control.sock",
            request_timeout_seconds=30.0,
        ),
        storage=StorageSettings(
            state_root=state,
            image_root=images,
            base_image=images / "base_ai_os.qcow2",
            backup_root=(state_base / "somnus-vm-backups").resolve(),
            log_root=(state_base / "somnus-vm-logs").resolve(),
        ),
        network=NetworkSettings(
            bind_host="127.0.0.1",
            ssh_ports=PortRange(2_222, 2_222 + width),
            agent_ports=PortRange(9_901, 9_901 + width),
            vnc_ports=PortRange(5_900, 5_900 + width),
        ),
        agent=AgentSettings(timeout_seconds=30.0),
        security=SecuritySettings(
            credential_provider="file",
            secret_root=(state_base / "somnus-vm-secrets").resolve(),
            guest_agent_token_file=(
                state_base / "somnus-vm-secrets" / "guest-agent.token"
            ).resolve(),
        ),
        policy=PolicySettings(max_active_vms=1),
    )
    return LabConfiguration(
        profile=profile,
        host=host,
        components=ComponentConfiguration(),
        source_path=None,
    )


def named_profile(
    profile: str,
    *,
    state_home: str | Path | None = None,
    data_home: str | Path | None = None,
    runtime_root: str | Path | None = None,
) -> LabConfiguration:
    """Build one named P1 profile without machine-specific embedded paths."""

    name = _bounded_string(profile, "profile", maximum=64)
    if name not in _NAMED_PROFILES:
        raise ConfigurationError(
            f"Unknown generated profile {name!r}; expected one of "
            f"{', '.join(sorted(_NAMED_PROFILES))}"
        )
    return _generated_configuration(
        name,
        enable_kvm=name == "host-kvm",
        constrained=name == "constrained-host",
        state_home=state_home,
        data_home=data_home,
        runtime_root=runtime_root,
    )


def default_configuration() -> LabConfiguration:
    """Build the v0.1-compatible install-safe per-user TCG configuration."""

    return _generated_configuration(
        "user-tcg",
        enable_kvm=False,
        constrained=False,
        state_home=None,
        data_home=None,
        runtime_root=None,
    )
