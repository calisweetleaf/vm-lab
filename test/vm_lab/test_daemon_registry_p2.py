"""Consumed-boundary P2 proof for the daemon, client, registry, and recovery.

This module deliberately crosses process boundaries.  It uses a real Unix
socket, Linux peer credentials, independent client processes, one daemon
process, one on-disk SQLite registry, real advisory locks, and real SIGKILL.
The checkpoint observer is only a post-commit synchronization probe: the
parent kills the daemon process after the named durable checkpoint is visible.
No mock, sleep-based success, or synthetic registry substitutes establish the
gate.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from datetime import datetime, timezone
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from somnus_protocol import (
    CURRENT_PROTOCOL_VERSION,
    SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
)
from somnus_vm.client import UnixControlClient
from somnus_vm.config import (
    AgentSettings,
    DaemonSettings,
    HostConfiguration,
    NetworkSettings,
    PolicySettings,
    PortRange,
    SecuritySettings,
    StorageSettings,
    load_configuration,
)
from somnus_vm.daemon import UnixControlDaemon
from somnus_vm.host.registry import RegistryMode, SQLiteRegistry
from somnus_vm.host.service import RegistryMutationService
from somnus_vm.transport import TransportError

_CLIENT_COUNT = 24
_PROCESS_TIMEOUT_SECONDS = 30.0
_CHECKPOINTS = (
    "intent_persisted",
    "resource_state_committed",
    "completion_observed",
)
_OPERATIONS = (
    "registry.declare",
    "registry.lease",
    "registry.update",
    "registry.cancel",
)


def _host_configuration(root: Path) -> HostConfiguration:
    marker = root / "qemu-invoked"
    for executable_name in ("qemu-must-not-run", "qemu-img-must-not-run"):
        executable = root / executable_name
        executable.write_text(
            (
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text("
                f"{executable_name!r}, encoding='utf-8')\n"
                "raise SystemExit(97)\n"
            ),
            encoding="utf-8",
        )
        executable.chmod(0o700)
    state_root = root / "state"
    runtime_root = root / "runtime"
    image_root = root / "images"
    backup_root = root / "backups"
    log_root = root / "logs"
    secret_root = root / "secrets"
    return HostConfiguration(
        qemu_binary=str(root / "qemu-must-not-run"),
        qemu_img_binary=str(root / "qemu-img-must-not-run"),
        enable_kvm=False,
        daemon=DaemonSettings(
            runtime_root=runtime_root,
            control_socket=runtime_root / "control.sock",
            request_timeout_seconds=10.0,
        ),
        storage=StorageSettings(
            state_root=state_root,
            image_root=image_root,
            base_image=image_root / "base.qcow2",
            backup_root=backup_root,
            log_root=log_root,
        ),
        network=NetworkSettings(
            bind_host="127.0.0.1",
            ssh_ports=PortRange(21_000, 21_127),
            agent_ports=PortRange(22_000, 22_127),
            vnc_ports=PortRange(5_900, 6_027),
        ),
        agent=AgentSettings(timeout_seconds=10.0),
        security=SecuritySettings(
            credential_provider="file",
            secret_root=secret_root,
            guest_agent_token_file=secret_root / "guest-agent.token",
        ),
        policy=PolicySettings(max_active_vms=1),
    )


def _request(
    operation: str,
    payload: dict[str, object],
    *,
    vm_id: UUID | None = None,
    generation: int | None = None,
    request_id: UUID | None = None,
) -> ControlRequest:
    return ControlRequest(
        schema_version=SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=request_id or uuid4(),
        correlation_id=uuid4(),
        vm_id=vm_id,
        boot_id=None,
        generation=generation,
        timestamp=datetime.now(timezone.utc),
        operation=operation,
        payload=payload,
    )


def _runtime_configuration_file(root: Path) -> Path:
    """Write the exact strict configuration consumed by the real entry point."""

    configuration = _host_configuration(root)
    path = root / "daemon.toml"
    path.write_text(
        "\n".join(
            [
                "[vm_lab]",
                'profile = "p2-gate"',
                "",
                "[host]",
                f'qemu_binary = "{configuration.qemu_binary}"',
                f'qemu_img_binary = "{configuration.qemu_img_binary}"',
                "enable_kvm = false",
                "",
                "[daemon]",
                f'runtime_root = "{configuration.daemon.runtime_root}"',
                f'control_socket = "{configuration.daemon.control_socket}"',
                "request_timeout_seconds = 10.0",
                "",
                "[storage]",
                f'state_root = "{configuration.storage.state_root}"',
                f'image_root = "{configuration.storage.image_root}"',
                f'base_image = "{configuration.storage.base_image}"',
                f'backup_root = "{configuration.storage.backup_root}"',
                f'log_root = "{configuration.storage.log_root}"',
                "",
                "[network]",
                'bind_host = "127.0.0.1"',
                "ssh_ports = [21000, 21127]",
                "agent_ports = [22000, 22127]",
                "vnc_ports = [5900, 6027]",
                "",
                "[agent]",
                "timeout_seconds = 10.0",
                "",
                "[security]",
                'credential_provider = "file"',
                f'secret_root = "{configuration.security.secret_root}"',
                "guest_agent_token_file = "
                f'"{configuration.security.guest_agent_token_file}"',
                "",
                "[policy]",
                "max_active_vms = 1",
                "",
                "[components]",
                "guest_runtime_candidates = false",
                "operator_shell = false",
                "native_tools = false",
                "file_processing = false",
                "digital_twin_lineage = false",
                "quarantined_runtime = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _daemon_process(
    configuration: HostConfiguration,
    ready: Connection,
    stop_event: Any,
    checkpoint_sender: Connection | None = None,
    checkpoint_target: str | None = None,
) -> None:
    """Own one registry/daemon process until clean stop or external SIGKILL."""

    daemon: UnixControlDaemon | None = None
    try:
        registry_path = configuration.storage.state_root / "registry.sqlite3"
        with SQLiteRegistry.open(registry_path) as registry:
            fired = False

            def observe(operation_id: UUID, checkpoint: str) -> None:
                nonlocal fired
                if (
                    not fired
                    and checkpoint_sender is not None
                    and checkpoint == checkpoint_target
                ):
                    fired = True
                    checkpoint_sender.send(
                        {
                            "checkpoint": checkpoint,
                            "operation_id": str(operation_id),
                        }
                    )
                    # This is a deterministic kill window after the registry
                    # transaction committed.  Only SIGKILL releases it.
                    while True:
                        time.sleep(3_600)

            service = RegistryMutationService(
                registry,
                configuration,
                checkpoint_observer=observe if checkpoint_target else None,
            )
            if registry.status.mode is RegistryMode.READ_WRITE:
                service.recover_incomplete_operations()
            daemon = UnixControlDaemon(
                configuration.daemon,
                service,
                allowed_uids={os.geteuid()},
                max_workers=32,
                backlog=128,
            )
            daemon.start()
            ready.send(
                {
                    "ok": True,
                    "pid": os.getpid(),
                    "recovery_mode": registry.status.mode.value,
                }
            )
            stop_event.wait()
            daemon.shutdown(
                timeout_seconds=configuration.daemon.request_timeout_seconds
            )
    except BaseException:
        try:
            ready.send({"ok": False, "traceback": traceback.format_exc()})
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    finally:
        if daemon is not None:
            try:
                daemon.shutdown(
                    timeout_seconds=configuration.daemon.request_timeout_seconds
                )
            except BaseException:
                pass
        ready.close()
        if checkpoint_sender is not None:
            checkpoint_sender.close()


def _client_process(
    socket_path: Path,
    request_payload: dict[str, object],
    start_event: Any,
    results: Any,
    index: int,
) -> None:
    """Issue exactly one request from one independent client process."""

    try:
        if not start_event.wait(_PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("client start barrier expired")
        request = ControlRequest.from_dict(request_payload)
        response = UnixControlClient(
            socket_path,
            timeout_seconds=10.0,
            allowed_server_uids={os.geteuid()},
        ).request(request)
        results.put(
            {
                "index": index,
                "ok": True,
                "response": response.to_dict(),
            }
        )
    except BaseException:
        results.put(
            {
                "index": index,
                "ok": False,
                "traceback": traceback.format_exc(),
            }
        )


class P2DaemonRegistryGate(unittest.TestCase):
    """Prove the single mutation owner at its real local consumption boundary."""

    def setUp(self) -> None:
        self.assertNotEqual(
            os.geteuid(),
            0,
            "GATE-P2 requires an unprivileged daemon process",
        )
        self.context = multiprocessing.get_context("fork")

    def _start_daemon(
        self,
        configuration: HostConfiguration,
        *,
        checkpoint_target: str | None = None,
    ) -> tuple[
        multiprocessing.Process,
        Any,
        Connection | None,
    ]:
        ready_receiver, ready_sender = self.context.Pipe(duplex=False)
        checkpoint_receiver: Connection | None = None
        checkpoint_sender: Connection | None = None
        if checkpoint_target is not None:
            checkpoint_receiver, checkpoint_sender = self.context.Pipe(
                duplex=False
            )
        stop_event = self.context.Event()
        process = self.context.Process(
            target=_daemon_process,
            args=(
                configuration,
                ready_sender,
                stop_event,
                checkpoint_sender,
                checkpoint_target,
            ),
            name="somnus-vm-daemon-gate",
        )
        process.start()
        ready_sender.close()
        if checkpoint_sender is not None:
            checkpoint_sender.close()
        try:
            self.assertTrue(
                ready_receiver.poll(_PROCESS_TIMEOUT_SECONDS),
                "daemon did not report readiness",
            )
            ready = ready_receiver.recv()
        finally:
            ready_receiver.close()
        self.assertTrue(ready["ok"], ready.get("traceback"))
        self.assertEqual(ready["pid"], process.pid)
        self.assertTrue(configuration.daemon.control_socket.is_socket())
        return process, stop_event, checkpoint_receiver

    def _stop_daemon(
        self,
        configuration: HostConfiguration,
        process: multiprocessing.Process,
        stop_event: Any,
    ) -> None:
        stop_event.set()
        process.join(_PROCESS_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(5.0)
            self.fail("daemon did not shut down before the physical deadline")
        self.assertEqual(process.exitcode, 0)
        self.assertFalse(configuration.daemon.control_socket.exists())

    def _one_request(
        self,
        configuration: HostConfiguration,
        request: ControlRequest,
    ) -> ControlResponse:
        response = UnixControlClient(
            configuration.daemon.control_socket,
            timeout_seconds=10.0,
            allowed_server_uids={os.geteuid()},
        ).request(request)
        self.assertIsInstance(response, ControlResponse)
        assert isinstance(response, ControlResponse)
        return response

    def _wave(
        self,
        configuration: HostConfiguration,
        requests: list[ControlRequest],
    ) -> list[dict[str, object]]:
        start_event = self.context.Event()
        results = self.context.Queue()
        processes = [
            self.context.Process(
                target=_client_process,
                args=(
                    configuration.daemon.control_socket,
                    request.to_dict(),
                    start_event,
                    results,
                    index,
                ),
                name=f"somnus-client-{index:02d}",
            )
            for index, request in enumerate(requests)
        ]
        for process in processes:
            process.start()
        start_event.set()

        rows: list[dict[str, object]] = []
        try:
            for _ in processes:
                rows.append(
                    results.get(timeout=_PROCESS_TIMEOUT_SECONDS)
                )
        except queue.Empty:
            for process in processes:
                if process.is_alive():
                    process.kill()
            self.fail("independent client wave did not return every result")
        finally:
            for process in processes:
                process.join(_PROCESS_TIMEOUT_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(5.0)
            results.close()
            results.join_thread()

        failures = [row for row in rows if not row["ok"]]
        self.assertFalse(
            failures,
            "\n\n".join(str(row.get("traceback")) for row in failures),
        )
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        ordered = sorted(rows, key=lambda row: int(row["index"]))
        return [dict(row["response"]) for row in ordered]

    def _seed_for_operation(
        self,
        configuration: HostConfiguration,
        operation: str,
    ) -> ControlRequest:
        process, stop_event, checkpoint = self._start_daemon(configuration)
        self.assertIsNone(checkpoint)
        try:
            declare = _request(
                "registry.declare",
                {
                    "memory_mib": 4_096,
                    "name": f"kill-{operation.rsplit('.', 1)[-1]}",
                    "vcpus": 2,
                },
            )
            declared = self._one_request(configuration, declare)
            if operation == "registry.declare":
                # Use a distinct request against an otherwise empty registry.
                cancel = _request(
                    "registry.cancel",
                    {"expected_revision": 0},
                    vm_id=UUID(str(declared.result["vm_id"])),
                    generation=0,
                )
                self._one_request(configuration, cancel)
                return _request(
                    "registry.declare",
                    {
                        "memory_mib": 8_192,
                        "name": "kill-declare-target",
                        "vcpus": 4,
                    },
                )

            vm_id = UUID(str(declared.result["vm_id"]))
            if operation == "registry.cancel":
                lease = _request(
                    "registry.lease",
                    {"expected_revision": 0},
                    vm_id=vm_id,
                    generation=0,
                )
                self._one_request(configuration, lease)
            return _request(
                operation,
                {"expected_revision": 0},
                vm_id=vm_id,
                generation=0,
            )
        finally:
            self._stop_daemon(configuration, process, stop_event)

    def test_independent_process_race_has_one_consistent_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vm-lab-p2-race-") as raw:
            root = Path(raw)
            configuration = _host_configuration(root)
            process, stop_event, checkpoint = self._start_daemon(configuration)
            self.assertIsNone(checkpoint)
            request_waves: list[list[ControlRequest]] = []
            response_waves: list[list[dict[str, object]]] = []
            try:
                declarations = [
                    _request(
                        "registry.declare",
                        {
                            "memory_mib": 4_096 + index,
                            "name": f"race-{index:02d}",
                            "vcpus": 2,
                        },
                    )
                    for index in range(_CLIENT_COUNT)
                ]
                declared = self._wave(configuration, declarations)
                request_waves.append(declarations)
                response_waves.append(declared)

                identities = [
                    UUID(str(response["result"]["vm_id"]))
                    for response in declared
                ]
                self.assertEqual(len(set(identities)), _CLIENT_COUNT)

                leases = [
                    _request(
                        "registry.lease",
                        {"expected_revision": 0},
                        vm_id=identity,
                        generation=0,
                    )
                    for identity in identities
                ]
                leased = self._wave(configuration, leases)
                request_waves.append(leases)
                response_waves.append(leased)

                updates = [
                    _request(
                        "registry.update",
                        {"expected_revision": 0},
                        vm_id=identity,
                        generation=0,
                    )
                    for identity in identities
                ]
                updated = self._wave(configuration, updates)
                request_waves.append(updates)
                response_waves.append(updated)

                cancellations = [
                    _request(
                        "registry.cancel",
                        {"expected_revision": 1},
                        vm_id=identity,
                        generation=0,
                    )
                    for identity in identities
                ]
                canceled = self._wave(configuration, cancellations)
                request_waves.append(cancellations)
                response_waves.append(canceled)

                for requests, original in zip(
                    request_waves,
                    response_waves,
                    strict=True,
                ):
                    for request, first in zip(requests, original, strict=True):
                        replay = self._one_request(configuration, request)
                        self.assertEqual(
                            replay.to_dict()["result"],
                            first["result"],
                        )
                        self.assertEqual(
                            str(replay.operation_id),
                            first["operation_id"],
                        )
            finally:
                self._stop_daemon(configuration, process, stop_event)

            database = configuration.storage.state_root / "registry.sqlite3"
            connection = sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
            )
            try:
                connection.row_factory = sqlite3.Row
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
                vm_rows = connection.execute(
                    "SELECT * FROM vms ORDER BY normalized_name"
                ).fetchall()
                self.assertEqual(len(vm_rows), _CLIENT_COUNT)
                self.assertTrue(all(row["active"] == 0 for row in vm_rows))
                self.assertTrue(all(row["revision"] == 2 for row in vm_rows))
                self.assertEqual(
                    len({row["normalized_name"] for row in vm_rows}),
                    _CLIENT_COUNT,
                )
                self.assertEqual(
                    len({row["qmp_socket"] for row in vm_rows}),
                    _CLIENT_COUNT,
                )
                endpoints = {
                    int(row[column])
                    for row in vm_rows
                    for column in (
                        "ssh_port",
                        "agent_port",
                        "display_port",
                    )
                }
                self.assertEqual(len(endpoints), _CLIENT_COUNT * 3)

                lease_rows = connection.execute(
                    "SELECT * FROM port_leases"
                ).fetchall()
                self.assertEqual(len(lease_rows), _CLIENT_COUNT * 3)
                self.assertTrue(all(row["active"] == 0 for row in lease_rows))
                self.assertTrue(
                    all(
                        row["operation_id"] is not None
                        and row["released_by_operation_id"] is not None
                        and row["operation_id"]
                        != row["released_by_operation_id"]
                        for row in lease_rows
                    )
                )
                self.assertEqual(
                    len({(row["host"], row["port"]) for row in lease_rows}),
                    _CLIENT_COUNT * 3,
                )

                disk_rows = connection.execute("SELECT * FROM disks").fetchall()
                self.assertEqual(len(disk_rows), _CLIENT_COUNT)
                self.assertTrue(all(row["active"] == 0 for row in disk_rows))
                self.assertTrue(
                    all(
                        row["operation_id"] is not None
                        and row["released_by_operation_id"] is not None
                        and row["operation_id"]
                        != row["released_by_operation_id"]
                        for row in disk_rows
                    )
                )
                self.assertEqual(
                    len({row["path"] for row in disk_rows}),
                    _CLIENT_COUNT,
                )

                operations = connection.execute(
                    "SELECT kind,status,COUNT(*) AS total "
                    "FROM operations GROUP BY kind,status"
                ).fetchall()
                self.assertEqual(
                    {
                        (row["kind"], row["status"]): row["total"]
                        for row in operations
                    },
                    {
                        (operation, "completed"): _CLIENT_COUNT
                        for operation in _OPERATIONS
                    },
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM idempotency"
                    ).fetchone()[0],
                    _CLIENT_COUNT * len(_OPERATIONS),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM operation_checkpoints"
                    ).fetchone()[0],
                    _CLIENT_COUNT * len(_OPERATIONS) * len(_CHECKPOINTS),
                )
                checkpoint_counts = connection.execute(
                    "SELECT name,COUNT(*) AS total "
                    "FROM operation_checkpoints GROUP BY name"
                ).fetchall()
                self.assertEqual(
                    {
                        row["name"]: row["total"]
                        for row in checkpoint_counts
                    },
                    {
                        name: _CLIENT_COUNT * len(_OPERATIONS)
                        for name in _CHECKPOINTS
                    },
                )
                contradictory = connection.execute(
                    "SELECT operation_id,COUNT(*) AS total,"
                    "COUNT(DISTINCT ordinal) AS ordinals,"
                    "MIN(ordinal) AS first,MAX(ordinal) AS last "
                    "FROM operation_checkpoints GROUP BY operation_id "
                    "HAVING total != 3 OR ordinals != 3 "
                    "OR first != 0 OR last != 2"
                ).fetchall()
                self.assertEqual(contradictory, [])
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM lifecycle_events"
                    ).fetchone()[0],
                    0,
                    "registry-only P2 operations must not invent lifecycle events",
                )
            finally:
                connection.close()

            self.assertFalse(configuration.daemon.control_socket.exists())
            self.assertFalse(
                any(
                    configuration.storage.state_root.glob(
                        "vms/**/aipc.qcow2"
                    )
                ),
                "P2 declaration must not construct disk bytes",
            )
            self.assertFalse((root / "qemu-invoked").exists())

            # A clean second owner can acquire both physical locks and recover
            # the stale pathname state left by neither a live daemon nor writer.
            second, second_stop, second_checkpoint = self._start_daemon(
                configuration
            )
            self.assertIsNone(second_checkpoint)
            self._stop_daemon(configuration, second, second_stop)

    def test_real_daemon_entrypoint_composes_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vm-lab-p2-runtime-") as raw:
            root = Path(raw)
            config_path = _runtime_configuration_file(root)
            configuration = load_configuration(config_path)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(
                Path(__file__).resolve().parents[2] / "src"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "somnus_vm.daemon_runtime",
                    "--config",
                    str(config_path),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            request = _request("registry.status", {})
            response: ControlResponse | None = None
            deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
            while response is None and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    response = self._one_request(
                        configuration.host,
                        request,
                    )
                except TransportError:
                    time.sleep(0.02)
            if response is None:
                process.kill()
                stdout, stderr = process.communicate(timeout=5.0)
                self.fail(
                    "real daemon entry point never served an authenticated "
                    f"request\nstdout:\n{stdout}\nstderr:\n{stderr}"
                )
            self.assertEqual(response.result["mode"], "read_write")

            process.send_signal(signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(
                    timeout=_PROCESS_TIMEOUT_SECONDS
                )
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5.0)
                self.fail(
                    "real daemon entry point ignored SIGTERM"
                    f"\nstdout:\n{stdout}\nstderr:\n{stderr}"
                )
            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertFalse(configuration.daemon.control_socket.exists())
            self.assertFalse((root / "qemu-invoked").exists())
            with SQLiteRegistry.open(
                configuration.storage.state_root / "registry.sqlite3",
                writer=False,
            ) as registry:
                self.assertIs(registry.status.mode, RegistryMode.READ_ONLY)
                self.assertEqual(registry.list_operations(), ())

    def test_real_sigkill_at_every_checkpoint_recovers_exactly(self) -> None:
        for operation in _OPERATIONS:
            for checkpoint in _CHECKPOINTS:
                with self.subTest(operation=operation, checkpoint=checkpoint):
                    with tempfile.TemporaryDirectory(
                        prefix="vm-lab-p2-kill-"
                    ) as raw:
                        configuration = _host_configuration(Path(raw))
                        request = self._seed_for_operation(
                            configuration,
                            operation,
                        )
                        daemon, _stop, notification = self._start_daemon(
                            configuration,
                            checkpoint_target=checkpoint,
                        )
                        assert notification is not None
                        start_event = self.context.Event()
                        results = self.context.Queue()
                        client = self.context.Process(
                            target=_client_process,
                            args=(
                                configuration.daemon.control_socket,
                                request.to_dict(),
                                start_event,
                                results,
                                0,
                            ),
                            name="somnus-kill-window-client",
                        )
                        client.start()
                        start_event.set()
                        try:
                            self.assertTrue(
                                notification.poll(
                                    _PROCESS_TIMEOUT_SECONDS
                                ),
                                "durable checkpoint was not reached",
                            )
                            observed = notification.recv()
                            self.assertEqual(
                                observed["checkpoint"],
                                checkpoint,
                            )
                            os.kill(daemon.pid, signal.SIGKILL)
                            daemon.join(_PROCESS_TIMEOUT_SECONDS)
                            self.assertEqual(
                                daemon.exitcode,
                                -signal.SIGKILL,
                            )
                        finally:
                            notification.close()
                            client.join(_PROCESS_TIMEOUT_SECONDS)
                            if client.is_alive():
                                client.kill()
                                client.join(5.0)
                            try:
                                interrupted = results.get(timeout=5.0)
                            except queue.Empty:
                                self.fail(
                                    "killed request client did not observe "
                                    "transport termination"
                                )
                            self.assertFalse(
                                interrupted["ok"],
                                "daemon returned success before the selected "
                                "post-commit kill window",
                            )
                            results.close()
                            results.join_thread()

                        restarted, restarted_stop, restarted_checkpoint = (
                            self._start_daemon(configuration)
                        )
                        self.assertIsNone(restarted_checkpoint)
                        try:
                            first = self._one_request(
                                configuration,
                                request,
                            )
                            second = self._one_request(
                                configuration,
                                request,
                            )
                            self.assertEqual(first.result, second.result)
                            self.assertEqual(
                                first.operation_id,
                                second.operation_id,
                            )
                        finally:
                            self._stop_daemon(
                                configuration,
                                restarted,
                                restarted_stop,
                            )

                        with SQLiteRegistry.open(
                            configuration.storage.state_root
                            / "registry.sqlite3"
                        ) as registry:
                            operation_row = registry.find_idempotency(
                                str(request.request_id)
                            )
                            self.assertIsNotNone(operation_row)
                            assert operation_row is not None
                            self.assertEqual(
                                operation_row.status,
                                "completed",
                            )
                            checkpoints = registry.list_checkpoints(
                                operation_row.operation_id
                            )
                            self.assertEqual(
                                tuple(item.name for item in checkpoints),
                                _CHECKPOINTS,
                            )
                        self.assertFalse(
                            configuration.daemon.control_socket.exists()
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
