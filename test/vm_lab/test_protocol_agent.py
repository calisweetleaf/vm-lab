"""Direct adversarial checks for the pure Somnus guest-agent protocol family.

Source: PLAN.md Phase P1 / GATE-P1
Integrated: 2026-08-05
Purpose: Proves exact mapping round-trips, strict types, bounded results, safe
    guest paths, and recursively immutable health details without network IO.
IO: None beyond process-local assertions and terminal output.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol.agent import (
    AgentContractError,
    AgentEndpoint,
    AgentHealth,
    CommandResult,
    FileWriteResult,
)


def _expect_rejected(function: object) -> None:
    """Require one callable to fail through the agent-domain exception."""

    if not callable(function):
        raise AssertionError("Expected a callable")
    try:
        function()
    except AgentContractError:
        return
    raise AssertionError("Expected AgentContractError")


def check_endpoint_authority() -> None:
    """Keep every promoted v0.1 endpoint exact."""

    assert {endpoint.name: endpoint.value for endpoint in AgentEndpoint} == {
        "HEALTH_LIVE": "/health/live",
        "HEALTH_READY": "/health/ready",
        "STATUS": "/status",
        "STATS": "/stats",
        "EXECUTE": "/execute",
        "FILE_WRITE": "/files/write",
        "SOFT_REBOOT": "/soft-reboot",
    }


def check_health_roundtrip_and_immutability() -> None:
    """Round-trip nested details without retaining mutable caller state."""

    source = {
        "boot_id": "boot-one",
        "services": {"agent": "ready"},
        "checks": [True, 3, None],
    }
    health = AgentHealth(healthy=True, state="ready", details=source)
    source["boot_id"] = "mutated"
    source["services"]["agent"] = "mutated"  # type: ignore[index]
    source["checks"].append(False)  # type: ignore[union-attr]

    assert health.to_dict() == {
        "healthy": True,
        "state": "ready",
        "details": {
            "boot_id": "boot-one",
            "services": {"agent": "ready"},
            "checks": [True, 3, None],
        },
    }
    assert AgentHealth.from_dict(health.to_dict()).to_dict() == health.to_dict()
    assert AgentHealth.from_json(health.to_json()) == health
    assert health.to_json() == health.to_json()
    assert health.to_json() == (
        '{"details":{"boot_id":"boot-one","checks":[true,3,null],'
        '"services":{"agent":"ready"}},"healthy":true,"state":"ready"}'
    )

    wire = health.to_dict()
    wire_details = wire["details"]
    assert isinstance(wire_details, dict)
    wire_details["boot_id"] = "wire-mutation"
    assert health.to_dict()["details"] != wire_details

    try:
        health.details["boot_id"] = "forbidden"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("Top-level health details remained mutable")

    services = health.details["services"]
    assert hasattr(services, "__getitem__")
    try:
        services["agent"] = "forbidden"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("Nested health details remained mutable")

    assert isinstance(health.details["checks"], tuple)


def check_health_rejections() -> None:
    """Reject unknown keys, coercion, unsafe states, and unbounded details."""

    _expect_rejected(
        lambda: AgentHealth.from_dict(
            {"healthy": True, "state": "ready", "details": {}, "future": True}
        )
    )
    _expect_rejected(lambda: AgentHealth.from_dict({"healthy": True}))
    _expect_rejected(lambda: AgentHealth.from_dict({"healthy": 1, "state": "ready"}))
    _expect_rejected(lambda: AgentHealth(healthy=True, state="Ready", details={}))
    _expect_rejected(lambda: AgentHealth(healthy=True, state="ready state", details={}))
    _expect_rejected(
        lambda: AgentHealth(healthy=True, state="ready", details={"bad": object()})
    )
    _expect_rejected(
        lambda: AgentHealth(
            healthy=True,
            state="ready",
            details={"oversized": "x" * 4_097},
        )
    )
    _expect_rejected(
        lambda: AgentHealth(
            healthy=True,
            state="ready",
            details={"a": {"b": {"c": {"d": {"e": "too deep"}}}}},
        )
    )
    _expect_rejected(
        lambda: AgentHealth.from_json(
            '{"healthy":true,"healthy":false,"state":"ready"}'
        )
    )
    _expect_rejected(
        lambda: AgentHealth.from_json(
            '{"healthy":true,"state":"ready","schema_version":2}'
        )
    )
    _expect_rejected(
        lambda: AgentHealth.from_json(
            '{"healthy":true,"state":"ready","details":{"oversized":"'
            + ("x" * 70_000)
            + '"}}'
        )
    )


def check_command_roundtrip_and_defaults() -> None:
    """Round-trip command results and preserve legacy optional output defaults."""

    success = CommandResult(success=True, exit_code=0, stdout="done\n", stderr="")
    assert CommandResult.from_dict(success.to_dict()).to_dict() == success.to_dict()
    assert CommandResult.from_json(success.to_json()) == success
    assert success.to_json() == (
        '{"exit_code":0,"stderr":"","stdout":"done\\n","success":true}'
    )
    escaped = CommandResult(success=True, exit_code=0, stdout="\x00", stderr="")
    assert CommandResult.from_json(escaped.to_json()) == escaped
    maximal_escaped = CommandResult(
        success=True,
        exit_code=0,
        stdout="\x00" * 1_048_576,
        stderr="",
    )
    assert CommandResult.from_json(maximal_escaped.to_json()) == maximal_escaped
    assert CommandResult.from_dict({"success": False, "exit_code": -1}).to_dict() == {
        "success": False,
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
    }


def check_command_rejections() -> None:
    """Reject unknown fields, coercion, contradictions, and oversized output."""

    _expect_rejected(
        lambda: CommandResult.from_dict(
            {
                "success": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "command": "not part of the result schema",
            }
        )
    )
    _expect_rejected(lambda: CommandResult.from_dict({"success": "true", "exit_code": 0}))
    _expect_rejected(lambda: CommandResult.from_dict({"success": True, "exit_code": False}))
    _expect_rejected(lambda: CommandResult(True, 1, "", ""))
    _expect_rejected(lambda: CommandResult(False, 0, "", ""))
    _expect_rejected(lambda: CommandResult(False, 256, "", ""))
    _expect_rejected(lambda: CommandResult(False, -256, "", ""))
    _expect_rejected(
        lambda: CommandResult(
            False,
            1,
            "x" * 600_000,
            "y" * 600_000,
        )
    )
    _expect_rejected(
        lambda: CommandResult(True, 0, "\x00" * 1_048_577, "")
    )
    _expect_rejected(lambda: CommandResult(False, 1, object(), ""))  # type: ignore[arg-type]
    _expect_rejected(
        lambda: CommandResult.from_json(
            '{"success":true,"exit_code":0,"success":false}'
        )
    )
    _expect_rejected(
        lambda: CommandResult.from_json(
            '{"success":true,"exit_code":0,"future_result_schema":2}'
        )
    )


def check_file_write_roundtrip() -> None:
    """Round-trip successful and failed atomic write observations."""

    written = FileWriteResult(success=True, path="/opt/somnus/state/result.json", bytes_written=42)
    assert FileWriteResult.from_dict(written.to_dict()).to_dict() == written.to_dict()
    assert FileWriteResult.from_json(written.to_json()) == written
    assert written.to_json() == (
        '{"bytes_written":42,"path":"/opt/somnus/state/result.json","success":true}'
    )
    escaped_path = "/" + ('"' * 4_095)
    escaped_path_result = FileWriteResult(True, escaped_path, 1)
    assert FileWriteResult.from_json(escaped_path_result.to_json()) == escaped_path_result
    failed = FileWriteResult(success=False, path="/opt/somnus/state/result.json", bytes_written=0)
    assert FileWriteResult.from_dict(failed.to_dict()).to_dict() == failed.to_dict()


def check_file_write_rejections() -> None:
    """Reject unsafe paths, non-integers, partial failures, and unknown fields."""

    _expect_rejected(
        lambda: FileWriteResult.from_dict(
            {
                "success": True,
                "path": "/opt/somnus/result",
                "bytes_written": 1,
                "sha256": "not-yet-part-of-this-schema",
            }
        )
    )
    _expect_rejected(lambda: FileWriteResult(True, "relative/path", 1))
    _expect_rejected(lambda: FileWriteResult(True, "//host/share/result", 1))
    _expect_rejected(lambda: FileWriteResult(True, "/opt/somnus/../escape", 1))
    _expect_rejected(lambda: FileWriteResult(True, "/opt//somnus/result", 1))
    _expect_rejected(lambda: FileWriteResult(True, "/opt/somnus/result\nnext", 1))
    _expect_rejected(lambda: FileWriteResult(True, "/", 1))
    _expect_rejected(lambda: FileWriteResult(True, "/opt/somnus/result", True))
    _expect_rejected(lambda: FileWriteResult(True, "/opt/somnus/result", -1))
    _expect_rejected(lambda: FileWriteResult(False, "/opt/somnus/result", 1))
    _expect_rejected(
        lambda: FileWriteResult(True, "/opt/somnus/result", 1_073_741_825)
    )
    _expect_rejected(
        lambda: FileWriteResult.from_json(
            '{"success":true,"path":"/opt/somnus/result","bytes_written":1,'
            '"bytes_written":2}'
        )
    )
    _expect_rejected(
        lambda: FileWriteResult.from_json(
            '{"success":true,"path":"/opt/somnus/result","bytes_written":1,'
            '"future_result_schema":2}'
        )
    )
    _expect_rejected(
        lambda: FileWriteResult.from_json(
            '{"success":true,"path":"/' + ("a" * 5_000) + '","bytes_written":1}'
        )
    )


def check_public_root_and_cold_import_purity() -> None:
    """Prove the installed-like package root exposes agent schemas without IO."""

    import somnus_protocol

    assert somnus_protocol.AgentHealth is AgentHealth
    assert somnus_protocol.CommandResult is CommandResult
    assert somnus_protocol.FileWriteResult is FileWriteResult

    code = """
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
before = {path.name for path in Path.cwd().iterdir()}
sys.path.insert(0, str(source))
import somnus_protocol
assert somnus_protocol.AgentHealth.__module__ == 'somnus_protocol.agent'
assert somnus_protocol.CommandResult.__module__ == 'somnus_protocol.agent'
assert somnus_protocol.FileWriteResult.__module__ == 'somnus_protocol.agent'
assert not any(
    name == 'somnus_vm' or name.startswith('somnus_vm.')
    for name in sys.modules
)
after = {path.name for path in Path.cwd().iterdir()}
assert after == before, (before, after)
"""
    with tempfile.TemporaryDirectory(prefix="somnus-protocol-cold-import-") as directory:
        completed = subprocess.run(
            [sys.executable, "-c", code, str(SOURCE_ROOT)],
            cwd=directory,
            text=True,
            capture_output=True,
            check=False,
        )
    if completed.returncode != 0:
        raise AssertionError(
            "cold installed-like somnus_protocol import was not pure:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def run_agent_protocol_checks() -> str:
    """Run every agent protocol check and return consumed-harness detail."""

    checks = (
        check_endpoint_authority,
        check_health_roundtrip_and_immutability,
        check_health_rejections,
        check_command_roundtrip_and_defaults,
        check_command_rejections,
        check_file_write_roundtrip,
        check_file_write_rejections,
        check_public_root_and_cold_import_purity,
    )
    for check in checks:
        check()
    return f"round-tripped and adversarially validated {len(checks)} agent protocol surfaces"


def main() -> int:
    """Run every agent protocol check without a test-framework dependency."""

    checks = (
        check_endpoint_authority,
        check_health_roundtrip_and_immutability,
        check_health_rejections,
        check_command_roundtrip_and_defaults,
        check_command_rejections,
        check_file_write_roundtrip,
        check_file_write_rejections,
        check_public_root_and_cold_import_purity,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"PASS agent protocol: {len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
