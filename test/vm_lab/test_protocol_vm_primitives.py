"""Direct P1 proof for canonical VM declaration primitives."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol.vm import VMContractError, VMDefinition, VMPorts


def _reject(function: object) -> None:
    assert callable(function)
    try:
        function()
    except VMContractError:
        return
    raise AssertionError("expected VMContractError")


def run_vm_primitive_checks() -> str:
    """Prove exact round trips, strict rejection, aliases, and cold import."""

    definition = VMDefinition(
        name="persistent-aipc",
        disk_path="/var/lib/somnus/aipc.qcow2",
        memory_mib=4096,
        vcpus=2,
        enable_kvm=False,
    )
    ports = VMPorts(ssh=2222, agent=9901, vnc=5900)
    assert VMDefinition.from_dict(definition.to_dict()) == definition
    assert VMPorts.from_dict(ports.to_dict()) == ports

    invalid_definitions = [
        {**definition.to_dict(), "unknown": "hidden-intent"},
        {**definition.to_dict(), "memory_mib": "4096"},
        {**definition.to_dict(), "vcpus": True},
        {**definition.to_dict(), "enable_kvm": 0},
        {**definition.to_dict(), "disk_path": "relative.qcow2"},
        {**definition.to_dict(), "name": "bad name"},
    ]
    for payload in invalid_definitions:
        _reject(lambda payload=payload: VMDefinition.from_dict(payload))
    invalid_ports = [
        {**ports.to_dict(), "unknown": 1},
        {**ports.to_dict(), "ssh": "2222"},
        {**ports.to_dict(), "agent": True},
        {"ssh": 2222, "agent": 2222, "vnc": 5900},
    ]
    for payload in invalid_ports:
        _reject(lambda payload=payload: VMPorts.from_dict(payload))

    from somnus_vm import VMDefinition as RootDefinition
    from somnus_vm.contracts.vm import VMDefinition as CompatibilityDefinition
    from somnus_vm.contracts.vm import VMPorts as CompatibilityPorts

    assert RootDefinition is VMDefinition
    assert CompatibilityDefinition is VMDefinition
    assert CompatibilityPorts is VMPorts

    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import json,sys;"
                f"sys.path.insert(0,{str(SOURCE_ROOT)!r});"
                "import somnus_protocol.vm;"
                "print(json.dumps(sorted(name for name in sys.modules "
                "if name.startswith(('somnus_vm','somnus_guest','components')))))"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == []
    return (
        "round-tripped strict VM definition/ports, rejected coercion and "
        "unknown fields, preserved aliases, and proved cold import isolation"
    )


if __name__ == "__main__":
    print(run_vm_primitive_checks())
