"""Physical P2 proof for the single-owner registry mutation service.

The proof in this module stays deliberately below the future VM lifecycle:

* requests are strict canonical ``ControlRequest`` values;
* SQLite is a real on-disk registry;
* checkpoint interruption is a real child-process exit, fired only after the
  service's post-commit checkpoint observer runs;
* recovery reconstructs exact intent and result bytes from the registry;
* no QEMU process, qcow2 byte, QMP socket, PID file, or lifecycle command is
  accepted as part of registry ownership.

No mock or substitute establishes success.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
TEST_ROOT = Path(__file__).resolve().parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol import (  # noqa: E402
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
    ErrorCode,
    ErrorEnvelope,
)
from somnus_vm.config import (  # noqa: E402
    AgentSettings,
    DaemonSettings,
    HostConfiguration,
    NetworkSettings,
    PolicySettings,
    PortRange,
    SecuritySettings,
    StorageSettings,
)
from somnus_vm.host.registry import (  # noqa: E402
    RegistryMode,
    SQLiteRegistry,
)
from somnus_vm.host.service import RegistryMutationService  # noqa: E402
from somnus_vm.transport import PeerCredentials  # noqa: E402


_CRASH_EXIT = 73
_CHECKPOINTS = (
    "intent_persisted",
    "resource_state_committed",
    "completion_observed",
)
_MUTATIONS = (
    "registry.declare",
    "registry.lease",
    "registry.update",
    "registry.cancel",
)


def _configuration(root: Path) -> HostConfiguration:
    """Build one isolated local-only P2 host composition.

    The configured QEMU names deliberately do not resolve.  Registry service
    proof must complete without consulting either executable.
    """

    root = root.resolve()
    return HostConfiguration(
        qemu_binary="p2-qemu-must-not-run",
        qemu_img_binary="p2-qemu-img-must-not-run",
        enable_kvm=False,
        daemon=DaemonSettings(
            runtime_root=root / "r",
            control_socket=root / "r" / "control.sock",
            request_timeout_seconds=5.0,
        ),
        storage=StorageSettings(
            state_root=root / "s",
            image_root=root / "i",
            base_image=root / "i" / "base.qcow2",
            backup_root=root / "b",
            log_root=root / "l",
        ),
        network=NetworkSettings(
            bind_host="127.0.0.1",
            ssh_ports=PortRange(22_000, 22_031),
            agent_ports=PortRange(23_000, 23_031),
            vnc_ports=PortRange(5_900, 5_931),
        ),
        agent=AgentSettings(timeout_seconds=5.0),
        security=SecuritySettings(
            credential_provider="file",
            secret_root=root / "x",
            guest_agent_token_file=root / "x" / "agent.token",
        ),
        policy=PolicySettings(max_active_vms=1),
    )


def _peer() -> PeerCredentials:
    return PeerCredentials(
        pid=os.getpid(),
        uid=os.geteuid(),
        gid=os.getegid(),
    )


def _request(
    operation: str,
    payload: dict[str, object],
    *,
    vm_id: UUID | None = None,
    generation: int | None = None,
) -> ControlRequest:
    return ControlRequest(
        schema_version=CURRENT_SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=uuid4(),
        correlation_id=uuid4(),
        vm_id=vm_id,
        boot_id=None,
        generation=generation,
        timestamp=datetime.now(timezone.utc),
        operation=operation,
        payload=payload,
    )


def _assert_success(
    response: ControlResponse | ErrorEnvelope,
) -> ControlResponse:
    if not isinstance(response, ControlResponse):
        raise AssertionError(
            f"expected ControlResponse, received {response.to_dict()}"
        )
    return response


def _assert_error(
    response: ControlResponse | ErrorEnvelope,
    code: ErrorCode,
) -> ErrorEnvelope:
    if not isinstance(response, ErrorEnvelope):
        raise AssertionError(
            f"expected {code.value}, received {response.to_dict()}"
        )
    if response.code is not code:
        raise AssertionError(
            f"expected {code.value}, received {response.code.value}"
        )
    return response


def _json_result(response: ControlResponse) -> dict[str, object]:
    decoded = json.loads(response.to_json())
    result = decoded["result"]
    if not isinstance(result, dict):
        raise AssertionError("control result is not an object")
    return result


def _declare(
    service: RegistryMutationService,
    *,
    name: str,
) -> tuple[ControlRequest, ControlResponse, UUID, int, int]:
    request = _request(
        "registry.declare",
        {"memory_mib": 4_096, "name": name, "vcpus": 2},
    )
    response = _assert_success(service(request, _peer()))
    result = _json_result(response)
    return (
        request,
        response,
        UUID(str(result["vm_id"])),
        int(result["generation"]),
        int(result["revision"]),
    )


def _bound_request(
    operation: str,
    vm_id: UUID,
    generation: int,
    revision: int,
) -> ControlRequest:
    return _request(
        operation,
        {"expected_revision": revision},
        vm_id=vm_id,
        generation=generation,
    )


def _row_count(
    database: Path,
    table: str,
    *,
    where: str = "",
    values: tuple[object, ...] = (),
) -> int:
    if table not in {
        "disks",
        "idempotency",
        "lifecycle_events",
        "operations",
        "port_leases",
        "process_identities",
        "snapshots",
        "vms",
    }:
        raise AssertionError("test attempted an unapproved SQL identifier")
    connection = sqlite3.connect(database)
    try:
        query = f"SELECT COUNT(*) FROM {table}"
        if where:
            query += f" WHERE {where}"
        return int(connection.execute(query, values).fetchone()[0])
    finally:
        connection.close()


def _assert_no_machine_artifacts(root: Path) -> None:
    """Require registry intent to remain metadata-only at the filesystem."""

    forbidden_suffixes = {".log", ".pid", ".qmp", ".qcow2", ".raw"}
    found = tuple(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in forbidden_suffixes
    )
    if found:
        raise AssertionError(f"P2 materialized machine artifacts: {found}")
    runtime = root / "r"
    if runtime.exists():
        sockets = tuple(
            path
            for path in runtime.iterdir()
            if path.name.endswith((".qmp", ".pid"))
        )
        if sockets:
            raise AssertionError(f"P2 materialized runtime artifacts: {sockets}")


def _children() -> tuple[int, ...]:
    """Return Linux child identities without launching a probe process."""

    path = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    if not path.is_file():
        return ()
    text = path.read_text(encoding="ascii").strip()
    return tuple(int(value) for value in text.split()) if text else ()


def _prepare_target(
    root: Path,
    operation: str,
) -> tuple[Path, ControlRequest, UUID]:
    """Persist only the prerequisites for one interrupted target operation."""

    database = root / "d" / "registry.sqlite3"
    with SQLiteRegistry.open(database) as registry:
        service = RegistryMutationService(registry, _configuration(root))
        if operation == "registry.declare":
            target = _request(
                operation,
                {
                    "memory_mib": 4_096,
                    "name": "checkpoint-vm",
                    "vcpus": 2,
                },
            )
            # Declaration identity is deterministic from request_id, but the
            # service is the only authority allowed to derive and persist it.
            return database, target, UUID(int=0)

        _, _, vm_id, generation, revision = _declare(
            service,
            name="checkpoint-vm",
        )
        if operation == "registry.cancel":
            lease = _bound_request(
                "registry.lease",
                vm_id,
                generation,
                revision,
            )
            _assert_success(service(lease, _peer()))
        target = _bound_request(
            operation,
            vm_id,
            generation,
            revision,
        )
        return database, target, vm_id


def _crash_after_committed_checkpoint(
    root: Path,
    database: Path,
    request: ControlRequest,
    checkpoint: str,
) -> subprocess.CompletedProcess[str]:
    """Terminate a real writer process from the post-commit observer."""

    request_path = root / "request.json"
    request_path.write_text(request.to_json(), encoding="utf-8")
    os.chmod(request_path, 0o600)
    child = r"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])

from test_registry_service_p2 import _configuration
from somnus_protocol import ControlRequest
from somnus_vm.host.registry import SQLiteRegistry
from somnus_vm.host.service import RegistryMutationService
from somnus_vm.transport import PeerCredentials

root = Path(sys.argv[3])
database = Path(sys.argv[4])
request = ControlRequest.from_dict(
    json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
)
checkpoint = sys.argv[6]

def terminate_after_commit(operation_id, observed):
    if observed == checkpoint:
        os._exit(73)

with SQLiteRegistry.open(database) as registry:
    service = RegistryMutationService(
        registry,
        _configuration(root),
        checkpoint_observer=terminate_after_commit,
    )
    service(
        request,
        PeerCredentials(os.getpid(), os.geteuid(), os.getegid()),
    )
raise SystemExit(91)
"""
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-c",
            child,
            str(SOURCE_ROOT),
            str(TEST_ROOT),
            str(root),
            str(database),
            str(request_path),
            checkpoint,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


class RegistryServiceP2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_strict_payloads_and_unpromoted_lifecycle_fail_closed(self) -> None:
        root = self.root / "a"
        database = root / "d" / "registry.sqlite3"
        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(registry, _configuration(root))
            peer = _peer()
            invalid = (
                (
                    _request(
                        "registry.declare",
                        {
                            "memory_mib": 4_096,
                            "name": "strict",
                            "unexpected": "rejected",
                            "vcpus": 2,
                        },
                    ),
                    ErrorCode.INVALID_REQUEST,
                ),
                (
                    _request(
                        "registry.declare",
                        {"memory_mib": 4_096, "name": "strict"},
                    ),
                    ErrorCode.INVALID_REQUEST,
                ),
                (
                    _request(
                        "registry.declare",
                        {
                            "memory_mib": True,
                            "name": "strict",
                            "vcpus": 2,
                        },
                    ),
                    ErrorCode.VALIDATION_ERROR,
                ),
                (
                    _request("registry.status", {"ignored": True}),
                    ErrorCode.INVALID_REQUEST,
                ),
                (
                    _request(
                        "registry.update",
                        {"expected_revision": 0},
                    ),
                    ErrorCode.INVALID_REQUEST,
                ),
                (
                    _request("vm.start", {}),
                    ErrorCode.OWNERSHIP_REJECTED,
                ),
            )
            for request, code in invalid:
                with self.subTest(operation=request.operation, code=code.value):
                    _assert_error(service(request, peer), code)

            self.assertEqual(registry.list_vms(), ())
            self.assertEqual(registry.list_operations(), ())
            self.assertEqual(registry.status.mode, RegistryMode.READ_WRITE)

        self.assertEqual(_row_count(database, "vms"), 0)
        self.assertEqual(_row_count(database, "operations"), 0)
        self.assertEqual(_row_count(database, "process_identities"), 0)
        self.assertEqual(_row_count(database, "lifecycle_events"), 0)
        self.assertEqual(_row_count(database, "snapshots"), 0)
        _assert_no_machine_artifacts(root)

    def test_declare_lease_update_cancel_replay_and_conflicts(self) -> None:
        root = self.root / "b"
        database = root / "d" / "registry.sqlite3"
        children_before = _children()
        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(registry, _configuration(root))
            peer = _peer()

            declare, declared, vm_id, generation, revision = _declare(
                service,
                name="primary",
            )
            declared_replay = _assert_success(service(declare, peer))
            self.assertEqual(declared.operation_id, declared_replay.operation_id)
            self.assertEqual(
                _json_result(declared),
                _json_result(declared_replay),
            )

            collision = _request(
                "registry.declare",
                {
                    "memory_mib": 4_096,
                    "name": "primary",
                    "vcpus": 2,
                },
            )
            collided = _assert_error(
                service(collision, peer),
                ErrorCode.CONFLICT,
            )
            collided_replay = _assert_error(
                service(collision, peer),
                ErrorCode.CONFLICT,
            )
            self.assertEqual(
                collided.operation_id,
                collided_replay.operation_id,
            )

            lease = _bound_request(
                "registry.lease",
                vm_id,
                generation,
                revision,
            )
            leased = _assert_success(service(lease, peer))
            leased_replay = _assert_success(service(lease, peer))
            self.assertEqual(leased.operation_id, leased_replay.operation_id)
            self.assertEqual(_json_result(leased), _json_result(leased_replay))
            self.assertEqual(len(_json_result(leased)["leases"]), 3)

            update = _bound_request(
                "registry.update",
                vm_id,
                generation,
                revision,
            )
            updated = _assert_success(service(update, peer))
            updated_replay = _assert_success(service(update, peer))
            self.assertEqual(updated.operation_id, updated_replay.operation_id)
            self.assertEqual(
                _json_result(updated),
                _json_result(updated_replay),
            )
            revision = int(_json_result(updated)["revision"])
            self.assertEqual(revision, 1)

            stale = _bound_request(
                "registry.cancel",
                vm_id,
                generation,
                0,
            )
            _assert_error(service(stale, peer), ErrorCode.CONFLICT)
            current = registry.get_vm(vm_id)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertTrue(current.active)
            self.assertEqual(current.revision, revision)

            cancel = _bound_request(
                "registry.cancel",
                vm_id,
                generation,
                revision,
            )
            canceled = _assert_success(service(cancel, peer))
            canceled_replay = _assert_success(service(cancel, peer))
            self.assertEqual(canceled.operation_id, canceled_replay.operation_id)
            self.assertEqual(
                _json_result(canceled),
                _json_result(canceled_replay),
            )
            self.assertFalse(bool(_json_result(canceled)["active"]))
            self.assertEqual(int(_json_result(canceled)["revision"]), 2)

            # Every successful replay is served from exact durable result
            # bytes and cannot append a checkpoint or repeat a resource write.
            for request in (declare, lease, update, cancel):
                idempotency = registry.find_idempotency(
                    str(request.request_id)
                )
                self.assertIsNotNone(idempotency)
                assert idempotency is not None
                operation = registry.get_operation(idempotency.operation_id)
                self.assertIsNotNone(operation)
                assert operation is not None
                self.assertEqual(operation.status, "completed")
                self.assertEqual(
                    hashlib.sha256(operation.intent).hexdigest(),
                    operation.intent_sha256,
                )
                self.assertEqual(
                    hashlib.sha256(operation.result).hexdigest(),
                    operation.result_sha256,
                )
                self.assertEqual(operation.result, idempotency.result)
                self.assertEqual(
                    tuple(
                        item.name
                        for item in registry.list_checkpoints(
                            operation.operation_id
                        )
                    ),
                    _CHECKPOINTS,
                )

            self.assertEqual(len(registry.list_vms()), 1)
            self.assertEqual(len(registry.list_operations()), 5)

        self.assertEqual(_children(), children_before)
        self.assertEqual(
            _row_count(
                database,
                "port_leases",
                where="active=1",
            ),
            0,
        )
        self.assertEqual(
            _row_count(database, "disks", where="active=1"),
            0,
        )
        self.assertEqual(_row_count(database, "vms"), 1)
        self.assertEqual(_row_count(database, "process_identities"), 0)
        self.assertEqual(_row_count(database, "lifecycle_events"), 0)
        self.assertEqual(_row_count(database, "snapshots"), 0)
        _assert_no_machine_artifacts(root)

    def test_checkpoint_process_death_recovers_all_four_operations(self) -> None:
        case = 0
        for operation in _MUTATIONS:
            for checkpoint_index, checkpoint in enumerate(_CHECKPOINTS):
                case += 1
                root = self.root / f"c{case}"
                with self.subTest(
                    operation=operation,
                    checkpoint=checkpoint,
                ):
                    database, request, expected_vm_id = _prepare_target(
                        root,
                        operation,
                    )
                    process = _crash_after_committed_checkpoint(
                        root,
                        database,
                        request,
                        checkpoint,
                    )
                    self.assertEqual(
                        process.returncode,
                        _CRASH_EXIT,
                        process.stdout + process.stderr,
                    )

                    with SQLiteRegistry.open(database) as registry:
                        self.assertEqual(
                            registry.status.mode,
                            RegistryMode.READ_WRITE,
                        )
                        idempotency = registry.find_idempotency(
                            str(request.request_id)
                        )
                        self.assertIsNotNone(idempotency)
                        assert idempotency is not None
                        operation_before = registry.get_operation(
                            idempotency.operation_id
                        )
                        self.assertIsNotNone(operation_before)
                        assert operation_before is not None
                        persisted_before = registry.list_checkpoints(
                            operation_before.operation_id
                        )
                        self.assertEqual(
                            tuple(item.name for item in persisted_before),
                            _CHECKPOINTS[: checkpoint_index + 1],
                        )
                        self.assertEqual(
                            operation_before.latest_checkpoint,
                            checkpoint_index,
                        )
                        expected_terminal = checkpoint_index == 2
                        self.assertEqual(
                            operation_before.status == "completed",
                            expected_terminal,
                        )

                        service = RegistryMutationService(
                            registry,
                            _configuration(root),
                        )
                        recovered = service.recover_incomplete_operations()
                        self.assertEqual(
                            recovered,
                            0 if expected_terminal else 1,
                        )
                        operation_after = registry.get_operation(
                            operation_before.operation_id
                        )
                        self.assertIsNotNone(operation_after)
                        assert operation_after is not None
                        self.assertEqual(operation_after.status, "completed")
                        self.assertEqual(
                            tuple(
                                item.name
                                for item in registry.list_checkpoints(
                                    operation_after.operation_id
                                )
                            ),
                            _CHECKPOINTS,
                        )
                        replay = _assert_success(service(request, _peer()))
                        self.assertEqual(
                            replay.operation_id,
                            operation_after.operation_id,
                        )
                        self.assertEqual(
                            _json_result(replay),
                            json.loads(operation_after.result),
                        )
                        self.assertEqual(
                            hashlib.sha256(operation_after.intent).hexdigest(),
                            operation_after.intent_sha256,
                        )
                        self.assertEqual(
                            hashlib.sha256(
                                operation_after.owned_resources
                            ).hexdigest(),
                            operation_after.owned_resources_sha256,
                        )
                        self.assertEqual(
                            hashlib.sha256(operation_after.result).hexdigest(),
                            operation_after.result_sha256,
                        )

                        stored = registry.list_vms()
                        self.assertEqual(len(stored), 1)
                        current = stored[0]
                        if expected_vm_id.int != 0:
                            self.assertEqual(
                                current.record.vm_id,
                                expected_vm_id,
                            )
                        expected_revision = {
                            "registry.declare": 0,
                            "registry.lease": 0,
                            "registry.update": 1,
                            "registry.cancel": 1,
                        }[operation]
                        self.assertEqual(
                            current.revision,
                            expected_revision,
                        )
                        self.assertEqual(
                            current.active,
                            operation != "registry.cancel",
                        )

                    active_leases = _row_count(
                        database,
                        "port_leases",
                        where="active=1",
                    )
                    self.assertEqual(
                        active_leases,
                        3
                        if operation in {
                            "registry.lease",
                            "registry.cancel",
                        }
                        and operation != "registry.cancel"
                        else 0,
                    )
                    self.assertEqual(
                        _row_count(database, "disks", where="active=1"),
                        0 if operation == "registry.cancel" else 1,
                    )
                    self.assertEqual(
                        _row_count(database, "process_identities"),
                        0,
                    )
                    self.assertEqual(
                        _row_count(database, "lifecycle_events"),
                        0,
                    )
                    self.assertEqual(
                        _row_count(database, "snapshots"),
                        0,
                    )
                    _assert_no_machine_artifacts(root)

    def test_public_cli_and_service_do_not_expose_vm_lifecycle(self) -> None:
        root = self.root / "d"
        database = root / "d" / "registry.sqlite3"
        with SQLiteRegistry.open(database) as registry:
            service = RegistryMutationService(registry, _configuration(root))
            before = len(registry.list_operations())
            for operation in (
                "vm.start",
                "vm.stop",
                "vm.destroy",
                "vm.snapshot",
                "vm.rollback",
            ):
                response = service(_request(operation, {}), _peer())
                _assert_error(response, ErrorCode.OWNERSHIP_REJECTED)
            self.assertEqual(len(registry.list_operations()), before)

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        help_result = subprocess.run(
            [sys.executable, "-m", "somnus_vm", "--help"],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            help_result.returncode,
            0,
            help_result.stdout + help_result.stderr,
        )
        help_text = help_result.stdout.casefold()
        for command in (
            "start",
            "stop",
            "destroy",
            "snapshot",
            "rollback",
        ):
            self.assertNotIn(f" {command}", help_text)

        self.assertEqual(_row_count(database, "operations"), 0)
        self.assertEqual(_row_count(database, "process_identities"), 0)
        self.assertEqual(_row_count(database, "lifecycle_events"), 0)
        self.assertEqual(_row_count(database, "snapshots"), 0)
        _assert_no_machine_artifacts(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
