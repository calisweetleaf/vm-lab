"""Direct P1 settings, profile, and compatibility checks.

Source: PLAN.md P1-013 through P1-019
Integrated: 2026-08-05
Purpose: Proves typed settings composition, exact TOML schemas, strict numeric
    handling, path-owner separation, safe sockets, generated profiles, and
    secret-reference-only configuration without VM mutation.
IO: Uses disposable TOML files only; performs no host or guest mutation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_vm.config import (
    AgentSettings,
    ComponentConfiguration,
    ConfigurationError,
    DaemonSettings,
    HostConfiguration,
    LabConfiguration,
    NetworkSettings,
    PolicySettings,
    PortRange,
    SecuritySettings,
    StorageSettings,
    default_configuration,
    load_configuration,
    named_profile,
)


def _expect_rejected(function: object) -> None:
    """Require one callable to fail through the settings-domain exception."""

    if not callable(function):
        raise AssertionError("Expected a callable")
    try:
        function()
    except ConfigurationError:
        return
    raise AssertionError("Expected ConfigurationError")


def _toml_string(value: str | Path) -> str:
    """Encode one simple path as a TOML-compatible basic string."""

    return json.dumps(str(value))


def _split_configuration(
    root: Path,
    *,
    state_root: Path | None = None,
    runtime_root: Path | None = None,
    image_root: Path | None = None,
    base_image: Path | None = None,
    backup_root: Path | None = None,
    log_root: Path | None = None,
    secret_root: Path | None = None,
    token_file: Path | None = None,
    control_socket: Path | None = None,
    agent_timeout: str = "2.0",
    daemon_timeout: str = "3.0",
    max_active_vms: str = "1",
    ssh_ports: str = "[32000, 32031]",
    agent_ports: str = "[32100, 32131]",
    vnc_ports: str = "[6200, 6231]",
    credential_provider: str = "file",
    extra: str = "",
) -> str:
    """Build one complete split-table configuration with injectable hazards."""

    state = state_root or root / "state"
    runtime = runtime_root or root / "runtime"
    images = image_root or root / "images"
    base = base_image or images / "base.qcow2"
    backups = backup_root or root / "backups"
    logs = log_root or root / "logs"
    secrets = secret_root or root / "secrets"
    token = token_file or secrets / "guest-agent.token"
    socket = control_socket or runtime / "control.sock"
    return "\n".join(
        [
            "[vm_lab]",
            'profile = "lab-tcg"',
            "",
            "[host]",
            'qemu_binary = "qemu-system-x86_64"',
            'qemu_img_binary = "qemu-img"',
            "enable_kvm = false",
            "",
            "[daemon]",
            f"runtime_root = {_toml_string(runtime)}",
            f"control_socket = {_toml_string(socket)}",
            f"request_timeout_seconds = {daemon_timeout}",
            "",
            "[storage]",
            f"state_root = {_toml_string(state)}",
            f"image_root = {_toml_string(images)}",
            f"base_image = {_toml_string(base)}",
            f"backup_root = {_toml_string(backups)}",
            f"log_root = {_toml_string(logs)}",
            "",
            "[network]",
            'bind_host = "127.0.0.1"',
            f"ssh_ports = {ssh_ports}",
            f"agent_ports = {agent_ports}",
            f"vnc_ports = {vnc_ports}",
            "",
            "[agent]",
            f"timeout_seconds = {agent_timeout}",
            "",
            "[security]",
            f'credential_provider = "{credential_provider}"',
            f"secret_root = {_toml_string(secrets)}",
            f"guest_agent_token_file = {_toml_string(token)}",
            "",
            "[policy]",
            f"max_active_vms = {max_active_vms}",
            "",
            "[components]",
            "guest_runtime_candidates = false",
            "operator_shell = false",
            "native_tools = false",
            "file_processing = false",
            "digital_twin_lineage = false",
            "quarantined_runtime = false",
            "",
            extra,
        ]
    )


def _load_text(root: Path, content: str) -> LabConfiguration:
    """Write and load one disposable settings document."""

    path = root / f"settings-{uuid4().hex}.toml"
    path.write_text(content, encoding="utf-8")
    return load_configuration(path)


def check_legacy_compatibility() -> None:
    """Keep current valid config files and v0.1 host attributes consumable."""

    lab = load_configuration(PROJECT_ROOT / "configs" / "lab.toml")
    host = load_configuration(PROJECT_ROOT / "configs" / "host.toml")
    assert lab.profile == "lab"
    assert not lab.host.enable_kvm
    assert host.profile == "host-kvm"
    assert host.host.enable_kvm
    assert lab.host.state_dir == lab.storage.state_root
    assert lab.host.runtime_dir == lab.daemon.runtime_root
    assert lab.host.base_image == lab.storage.base_image
    assert lab.storage.image_root == lab.storage.base_image.parent
    assert lab.storage.base_image.is_relative_to(lab.storage.image_root)
    assert lab.storage.base_image != lab.storage.image_root
    assert lab.host.ssh_ports is lab.network.ssh_ports
    assert lab.host.agent_ports is lab.network.agent_ports
    assert lab.host.vnc_ports is lab.network.vnc_ports
    assert lab.host.agent_timeout_seconds == lab.agent.timeout_seconds
    assert lab.host.max_active_vms == lab.policy.max_active_vms == 1
    assert isinstance(lab.host, HostConfiguration)
    assert isinstance(lab.daemon, DaemonSettings)
    assert isinstance(lab.storage, StorageSettings)
    assert isinstance(lab.network, NetworkSettings)
    assert isinstance(lab.agent, AgentSettings)
    assert isinstance(lab.security, SecuritySettings)
    assert isinstance(lab.policy, PolicySettings)
    assert isinstance(lab.components, ComponentConfiguration)


def check_split_settings() -> None:
    """Load every P1 settings owner without legacy duplicate authority."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        config = _load_text(root, _split_configuration(root))
        assert config.profile == "lab-tcg"
        assert config.daemon.control_socket == root / "runtime" / "control.sock"
        assert config.storage.image_root == root / "images"
        assert config.storage.base_image == root / "images" / "base.qcow2"
        assert config.storage.backup_root == root / "backups"
        assert config.storage.log_root == root / "logs"
        assert config.network.bind_host == "127.0.0.1"
        assert config.network.ssh_ports == PortRange(32000, 32031)
        assert config.agent.timeout_seconds == 2.0
        assert config.daemon.request_timeout_seconds == 3.0
        assert config.security.credential_provider == "file"
        assert config.security.guest_agent_token_file == (
            root / "secrets" / "guest-agent.token"
        )
        assert config.policy.max_active_vms == 1


def check_unknown_and_duplicate_authority_rejections() -> None:
    """Reject unknown settings and two owners for one semantic value."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        base = _split_configuration(root)
        hostile = (
            base + "\n[unexpected]\nvalue = true\n",
            base.replace(
                'qemu_binary = "qemu-system-x86_64"',
                'qemu_binary = "qemu-system-x86_64"\nunknown_host_key = true',
            ),
            base.replace(
                'profile = "lab-tcg"',
                'profile = "lab-tcg"\nunknown_profile_key = true',
            ),
            base.replace(
                'bind_host = "127.0.0.1"',
                'bind_host = "127.0.0.1"\nunknown_network_key = 1',
            ),
            base.replace(
                "request_timeout_seconds = 3.0",
                "request_timeout_seconds = 3.0\nunknown_daemon_key = true",
            ),
            base.replace(
                f"log_root = {_toml_string(root / 'logs')}",
                (
                    f"log_root = {_toml_string(root / 'logs')}\n"
                    "unknown_storage_key = true"
                ),
            ),
            base.replace(
                "timeout_seconds = 2.0",
                "timeout_seconds = 2.0\nunknown_agent_key = true",
            ),
            base.replace(
                "max_active_vms = 1",
                "max_active_vms = 1\nunknown_policy_key = true",
            ),
            base.replace(
                "operator_shell = false",
                "operator_shell = false\nunknown_component_key = true",
            ),
            base.replace(
                'qemu_img_binary = "qemu-img"',
                f'qemu_img_binary = "qemu-img"\nstate_dir = {_toml_string(root / "second-state")}',
            ),
            base.replace(
                f'credential_provider = "file"',
                'credential_provider = "file"\nagent_token = "raw-secret"',
            ),
        )
        for content in hostile:
            _expect_rejected(lambda content=content: _load_text(root, content))


def check_strict_numbers_and_booleans() -> None:
    """Reject bool-as-int, numeric strings, NaN, infinity, and bad ranges."""

    _expect_rejected(lambda: PortRange(True, 2222))
    _expect_rejected(lambda: PortRange(2222, False))
    _expect_rejected(lambda: PolicySettings(max_active_vms=True))
    _expect_rejected(lambda: AgentSettings(timeout_seconds=float("nan")))
    _expect_rejected(lambda: AgentSettings(timeout_seconds=float("inf")))
    _expect_rejected(lambda: ComponentConfiguration(operator_shell=1))  # type: ignore[arg-type]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        hostile = (
            _split_configuration(root, agent_timeout='"2.0"'),
            _split_configuration(root, agent_timeout="true"),
            _split_configuration(root, agent_timeout="nan"),
            _split_configuration(root, daemon_timeout="inf"),
            _split_configuration(root, max_active_vms="true"),
            _split_configuration(root, ssh_ports="[true, 32031]"),
            _split_configuration(root, ssh_ports="[32031, 32000]"),
            _split_configuration(
                root,
                ssh_ports="[32000, 32105]",
                agent_ports="[32100, 32131]",
            ),
        )
        for content in hostile:
            _expect_rejected(lambda content=content: _load_text(root, content))


def check_qemu_and_socket_path_rejections() -> None:
    """Reject delimiter ambiguity, escaping sockets, and AF_UNIX overflow."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        hostile = (
            _split_configuration(root, state_root=root / "state,bad"),
            _split_configuration(root, runtime_root=root / "runtime,bad"),
            _split_configuration(root, log_root=root / "logs,bad"),
            _split_configuration(
                root,
                control_socket=root / "outside-runtime" / "control.sock",
            ),
            _split_configuration(
                root,
                runtime_root=root / ("r" * 100),
                control_socket=root / ("r" * 100) / "control.sock",
            ),
        )
        for content in hostile:
            _expect_rejected(lambda content=content: _load_text(root, content))

        comma_image = _load_text(
            root,
            _split_configuration(
                root,
                base_image=root / "images" / "base,encoded-by-blockdev.qcow2",
            ),
        )
        assert "," in str(comma_image.storage.base_image)


def check_root_ownership_rejections() -> None:
    """Reject aliases, unsafe descendants, and escaped owned references."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        hostile = (
            _split_configuration(root, backup_root=root / "state"),
            _split_configuration(root, log_root=root / "state" / "logs"),
            _split_configuration(root, runtime_root=root, control_socket=root / "control.sock"),
            _split_configuration(
                root,
                image_root=root / "state",
                base_image=root / "state" / "base.qcow2",
            ),
            _split_configuration(
                root,
                image_root=root / "images",
                base_image=root / "outside" / "base.qcow2",
            ),
            _split_configuration(
                root,
                image_root=root / "images" / "base.qcow2",
                base_image=root / "images" / "base.qcow2",
            ),
            _split_configuration(root, secret_root=root / "state"),
            _split_configuration(
                root,
                secret_root=root / "secrets",
                token_file=root / "outside" / "token",
            ),
        )
        for content in hostile:
            _expect_rejected(lambda content=content: _load_text(root, content))


def check_generated_profiles() -> None:
    """Generate all named profiles from injected roots without checkout paths."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        state_home = root / "state-home"
        data_home = root / "data-home"
        profiles = {
            name: named_profile(
                name,
                state_home=state_home,
                data_home=data_home,
                runtime_root=root / "runtime" / name,
            )
            for name in ("lab-tcg", "host-kvm", "constrained-host")
        }
        assert not profiles["lab-tcg"].host.enable_kvm
        assert profiles["host-kvm"].host.enable_kvm
        assert not profiles["constrained-host"].host.enable_kvm
        for name, config in profiles.items():
            assert config.profile == name
            assert config.source_path is None
            assert config.policy.max_active_vms == 1
            assert config.storage.state_root.is_relative_to(state_home)
            assert config.storage.image_root.is_relative_to(data_home)
            assert config.daemon.runtime_root == root / "runtime" / name
            assert str(PROJECT_ROOT) not in str(config.storage.state_root)
            assert len(tuple(config.network.ssh_ports.values())) > 1
        assert len(tuple(profiles["constrained-host"].network.ssh_ports.values())) == 8
        _expect_rejected(
            lambda: named_profile(
                "unknown",
                state_home=state_home,
                data_home=data_home,
                runtime_root=root / "runtime" / "unknown",
            )
        )


def check_generated_runtime_scoping() -> None:
    """Give each generated profile a distinct application-owned runtime root."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        original = os.environ.get("XDG_RUNTIME_DIR")
        try:
            os.environ["XDG_RUNTIME_DIR"] = str(root / "xdg-runtime")
            generated = (
                default_configuration(),
                named_profile(
                    "lab-tcg",
                    state_home=root / "lab-state",
                    data_home=root / "lab-data",
                ),
                named_profile(
                    "host-kvm",
                    state_home=root / "host-state",
                    data_home=root / "host-data",
                ),
                named_profile(
                    "constrained-host",
                    state_home=root / "small-state",
                    data_home=root / "small-data",
                ),
            )
        finally:
            if original is None:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                os.environ["XDG_RUNTIME_DIR"] = original

        runtime_roots = {config.daemon.runtime_root for config in generated}
        control_sockets = {config.daemon.control_socket for config in generated}
        assert len(runtime_roots) == len(generated) == 4
        assert len(control_sockets) == len(generated) == 4
        for config in generated:
            expected = root / "xdg-runtime" / "somnus-vm" / config.profile
            assert config.daemon.runtime_root == expected
            assert config.daemon.control_socket == expected / "control.sock"


def check_install_safe_default_and_secret_references() -> None:
    """Keep user-tcg defaults generated and expose secret locations, not values."""

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        original = {
            key: os.environ.get(key)
            for key in ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR")
        }
        try:
            os.environ["XDG_STATE_HOME"] = str(root / "state-home")
            os.environ["XDG_DATA_HOME"] = str(root / "data-home")
            os.environ["XDG_RUNTIME_DIR"] = str(root / "runtime")
            config = default_configuration()
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        assert config.profile == "user-tcg"
        assert config.source_path is None
        assert config.storage.state_root == root / "state-home" / "somnus-vm"
        assert config.storage.image_root == root / "data-home" / "somnus-vm"
        assert config.daemon.runtime_root == root / "runtime" / "somnus-vm" / "user-tcg"
        assert config.policy.max_active_vms == 1
        assert config.security.credential_provider == "file"
        assert config.security.guest_agent_token_file.is_relative_to(
            config.security.secret_root
        )
        assert not hasattr(config.security, "token")
        assert not hasattr(config.security, "password")
        assert not hasattr(config.agent, "token")


def _settings_checks() -> tuple[object, ...]:
    """Return the exact P1 settings proof sequence."""

    return (
        check_legacy_compatibility,
        check_split_settings,
        check_unknown_and_duplicate_authority_rejections,
        check_strict_numbers_and_booleans,
        check_qemu_and_socket_path_rejections,
        check_root_ownership_rejections,
        check_generated_profiles,
        check_generated_runtime_scoping,
        check_install_safe_default_and_secret_references,
    )


def run_settings_protocol_checks() -> str:
    """Run every P1 settings proof for the consumed smoke boundary."""

    checks = _settings_checks()
    for check in checks:
        check()
    return (
        f"validated {len(checks)} strict typed-settings cases across legacy, "
        "split, generated-profile, root-ownership, and secret-reference paths"
    )


def main() -> int:
    """Run every P1 settings check without a third-party test framework."""

    checks = _settings_checks()
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"PASS P1 settings: {len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
