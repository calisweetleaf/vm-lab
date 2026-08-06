"""Physical protocol proof for the bounded P4 QMP client.

The peer is a real isolated Python process using a real AF_UNIX stream and
kernel peer credentials.  It deliberately behaves like the QMP wire protocol
but is not QEMU, so these tests prove framing, negotiation, correlation,
deadlines, event separation, and hostile-input bounds only.  They never launch
QEMU, mount an image, or claim VM identity/readiness/lifecycle truth.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol._validation import thaw_json  # noqa: E402
from somnus_vm.host.qmp import (  # noqa: E402
    ALLOWED_QMP_COMMANDS,
    BLOCK_GRAPH_QMP_COMMANDS,
    CORE_QMP_COMMANDS,
    QMPClient,
    QMPCommandError,
    QMPError,
    QMPHandshakeError,
    QMPProtocolError,
    QMPSocketIdentity,
    QMPStateError,
    QMPTimeout,
    QMPUnavailable,
    QMPValidationError,
)


_FIXTURE_SERVER = r"""
import json
import os
import socket
import sys
import time
from pathlib import Path

socket_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
transcript_path = Path(sys.argv[3])
scenario = sys.argv[4]
expected_commands = int(sys.argv[5])

GREETING = {
    "QMP": {
        "version": {
            "qemu": {"major": 8, "minor": 2, "micro": 2},
            "package": "fixture",
        },
        "capabilities": ["oob"],
    }
}


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\r\n"


def send_raw(connection, payload, *, fragmented=False, delay=0.0):
    if not fragmented:
        connection.sendall(payload)
        return
    cursor = 0
    widths = (1, 2, 3, 5, 8, 13)
    index = 0
    while cursor < len(payload):
        width = widths[index % len(widths)]
        connection.sendall(payload[cursor:cursor + width])
        cursor += width
        index += 1
        if delay:
            time.sleep(delay)


def read_line(connection):
    payload = bytearray()
    while True:
        chunk = connection.recv(1)
        if not chunk:
            raise EOFError("fixture peer closed before command newline")
        payload.extend(chunk)
        if chunk == b"\n":
            break
        if len(payload) > 2_000_000:
            raise AssertionError("fixture received an unbounded command")
    line = bytes(payload).rstrip(b"\r\n")
    return json.loads(line.decode("utf-8"))


def record(row):
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def event(name, sequence):
    return {
        "event": name,
        "data": {"sequence": sequence},
        "timestamp": {
            "seconds": 1_700_000_000 + sequence,
            "microseconds": sequence,
        },
    }


def wait_for_eof(connection):
    connection.settimeout(5.0)
    try:
        while connection.recv(65_536):
            pass
    except (TimeoutError, ConnectionResetError, BrokenPipeError):
        pass


socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(socket_path.parent, 0o700)
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    listener.bind(os.fspath(socket_path))
    os.chmod(socket_path, 0o660 if scenario == "permissive_socket" else 0o600)
    listener.listen(1)
    ready_path.write_text(str(os.getpid()) + "\n", encoding="ascii")
    connection, _ = listener.accept()
    with connection:
        try:
            if scenario == "greeting_duplicate":
                raw = (
                    b'{"QMP":{"version":{"qemu":{"major":8,"minor":2,'
                    b'"micro":2},"package":"fixture"},"capabilities":[]},'
                    b'"QMP":{}}\r\n'
                )
                send_raw(connection, raw)
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "greeting_unknown":
                row = dict(GREETING)
                row["unexpected"] = True
                send_raw(connection, encoded(row))
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "greeting_bad_version":
                row = {
                    "QMP": {
                        "version": {
                            "qemu": {
                                "major": True,
                                "minor": 2,
                                "micro": 2,
                            },
                            "package": "fixture",
                        },
                        "capabilities": [],
                    }
                }
                send_raw(connection, encoded(row))
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "greeting_missing_caps":
                row = {
                    "QMP": {
                        "version": GREETING["QMP"]["version"],
                    }
                }
                send_raw(connection, encoded(row))
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "greeting_oversize":
                send_raw(connection, b"{" + (b" " * 2_048))
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "slow_greeting":
                send_raw(
                    connection,
                    encoded(GREETING),
                    fragmented=True,
                    delay=0.025,
                )
                wait_for_eof(connection)
                raise SystemExit(0)

            send_raw(
                connection,
                encoded(GREETING),
                fragmented=scenario in {"happy", "out_of_order"},
            )
            capability = read_line(connection)
            record(capability)
            assert capability == {
                "execute": "qmp_capabilities",
                "id": 1,
            }, capability

            if scenario == "capability_error":
                send_raw(
                    connection,
                    encoded(
                        {
                            "error": {
                                "class": "CommandNotFound",
                                "desc": "capabilities rejected",
                            },
                            "id": 1,
                        }
                    ),
                )
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "capability_mismatch":
                send_raw(connection, encoded({"return": {}, "id": 99}))
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "capability_nonempty":
                send_raw(
                    connection,
                    encoded({"return": {"unexpected": True}, "id": 1}),
                )
                wait_for_eof(connection)
                raise SystemExit(0)

            # Prove that an asynchronous event cannot be consumed as the
            # capability response.
            send_raw(connection, encoded(event("CAPABILITY_READY", 0)))
            send_raw(
                connection,
                encoded({"return": {}, "id": 1}),
                fragmented=scenario in {"happy", "out_of_order"},
            )

            if scenario == "event_overflow":
                send_raw(connection, encoded(event("OVERFLOW_ONE", 1)))
                send_raw(connection, encoded(event("OVERFLOW_TWO", 2)))
                wait_for_eof(connection)
                raise SystemExit(0)
            if scenario == "idle":
                wait_for_eof(connection)
                raise SystemExit(0)

            if scenario == "out_of_order":
                first = read_line(connection)
                second = read_line(connection)
                record(first)
                record(second)
                assert first["id"] < second["id"], (first, second)
                send_raw(
                    connection,
                    encoded(
                        {
                            "return": {
                                "command": second["execute"],
                                "server_pid": os.getpid(),
                            },
                            "id": second["id"],
                        }
                    ),
                )
                send_raw(connection, encoded(event("INTERLEAVED", 1)))
                send_raw(
                    connection,
                    encoded(
                        {
                            "return": {
                                "command": first["execute"],
                                "server_pid": os.getpid(),
                            },
                            "id": first["id"],
                        }
                    ),
                )
                wait_for_eof(connection)
                raise SystemExit(0)

            if scenario == "happy":
                send_raw(connection, encoded(event("IDLE_EVENT", 1)))
                for sequence in range(expected_commands):
                    command = read_line(connection)
                    record(command)
                    assert command["id"] == sequence + 2, command
                    send_raw(
                        connection,
                        encoded(event("COMMAND_EVENT", sequence + 2)),
                        fragmented=sequence == 0,
                    )
                    send_raw(
                        connection,
                        encoded(
                            {
                                "return": {
                                    "arguments": command.get("arguments", {}),
                                    "command": command["execute"],
                                    "server_pid": os.getpid(),
                                },
                                "id": command["id"],
                            }
                        ),
                        fragmented=sequence == 0,
                    )
                wait_for_eof(connection)
                raise SystemExit(0)

            command = read_line(connection)
            record(command)
            command_id = command["id"]

            if scenario == "command_error":
                send_raw(
                    connection,
                    encoded(
                        {
                            "error": {
                                "class": "GenericError",
                                "desc": "fixture-secret-description",
                                "data": {"retryable": False},
                            },
                            "id": command_id,
                        }
                    ),
                )
            elif scenario == "slow_response":
                send_raw(
                    connection,
                    encoded(
                        {
                            "return": {"status": "running"},
                            "id": command_id,
                        }
                    ),
                    fragmented=True,
                    delay=0.025,
                )
            elif scenario == "response_duplicate":
                send_raw(
                    connection,
                    (
                        b'{"return":{},"return":{"duplicate":true},"id":'
                        + str(command_id).encode("ascii")
                        + b"}\r\n"
                    ),
                )
            elif scenario == "response_malformed":
                send_raw(connection, b'{"return":,}\r\n')
            elif scenario == "response_oversize":
                send_raw(connection, b'{"return":"' + (b"x" * 2_048))
            elif scenario == "response_deep":
                value = {}
                for _ in range(12):
                    value = {"next": value}
                send_raw(
                    connection,
                    encoded({"return": value, "id": command_id}),
                )
            elif scenario == "response_items":
                send_raw(
                    connection,
                    encoded(
                        {
                            "return": list(range(64)),
                            "id": command_id,
                        }
                    ),
                )
            elif scenario == "response_string":
                send_raw(
                    connection,
                    encoded(
                        {
                            "return": "x" * 80,
                            "id": command_id,
                        }
                    ),
                )
            elif scenario == "response_mismatch":
                send_raw(
                    connection,
                    encoded({"return": {}, "id": command_id + 999}),
                )
            elif scenario == "response_unexpected":
                send_raw(connection, encoded(GREETING))
            elif scenario == "response_both":
                send_raw(
                    connection,
                    encoded(
                        {
                            "return": {},
                            "error": {
                                "class": "GenericError",
                                "desc": "ambiguous",
                            },
                            "id": command_id,
                        }
                    ),
                )
            elif scenario == "response_no_id":
                send_raw(connection, encoded({"return": {}}))
            elif scenario == "event_bad":
                send_raw(
                    connection,
                    encoded({"event": "BROKEN", "data": {}}),
                )
            elif scenario == "response_truncated":
                send_raw(connection, b'{"return":{"partial":true')
                connection.shutdown(socket.SHUT_WR)
            else:
                raise AssertionError("unknown fixture scenario: " + scenario)
            wait_for_eof(connection)
        except (BrokenPipeError, ConnectionResetError):
            pass
finally:
    listener.close()
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
"""


def _expect(
    expected: type[BaseException] | tuple[type[BaseException], ...],
    function: object,
) -> BaseException:
    if not callable(function):
        raise AssertionError("expected a callable")
    try:
        function()
    except expected as exc:
        return exc
    raise AssertionError(f"expected {expected!r}")


class _Fixture:
    def __init__(
        self,
        root: Path,
        scenario: str,
        *,
        expected_commands: int = 0,
    ) -> None:
        self.root = root
        self.scenario = scenario
        self.socket_path = root / "run" / "qmp.sock"
        self.ready_path = root / "ready"
        self.transcript_path = root / "transcript.jsonl"
        self.expected_commands = expected_commands
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> "_Fixture":
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                _FIXTURE_SERVER,
                str(self.socket_path),
                str(self.ready_path),
                str(self.transcript_path),
                self.scenario,
                str(self.expected_commands),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.ready_path.is_file():
                return self
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate()
                raise AssertionError(
                    f"fixture failed before readiness: {stdout}\n{stderr}"
                )
            time.sleep(0.005)
        self.stop(check=False)
        raise AssertionError("fixture did not publish readiness")

    @property
    def pid(self) -> int:
        if self.process is None:
            raise AssertionError("fixture is not started")
        return self.process.pid

    def transcript(self) -> list[dict[str, object]]:
        if not self.transcript_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.transcript_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line
        ]

    def stop(self, *, check: bool = True) -> tuple[str, str]:
        process = self.process
        if process is None:
            return "", ""
        self.process = None
        if not check and process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=6.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=2.0)
        if check and process.returncode != 0:
            raise AssertionError(
                f"fixture {self.scenario!r} failed with "
                f"{process.returncode}: {stdout}\n{stderr}"
            )
        return stdout, stderr


def _client(
    fixture: _Fixture,
    *,
    timeout_seconds: float = 1.5,
    max_message_bytes: int = 512,
    max_depth: int = 6,
    max_items: int = 16,
    max_string: int = 16,
    max_events: int = 256,
    allowed_server_uids: tuple[int, ...] | None = None,
) -> QMPClient:
    return QMPClient(
        fixture.socket_path,
        timeout_seconds=timeout_seconds,
        max_message_bytes=max_message_bytes,
        max_depth=max_depth,
        max_items=max_items,
        max_string=max_string,
        max_events=max_events,
        allowed_server_uids=allowed_server_uids,
    )


class QMPP4Tests(unittest.TestCase):
    def test_socket_identity_is_captured_connection_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = _Fixture(
                root,
                "happy",
                expected_commands=1,
            ).start()
            client = _client(
                fixture,
                max_message_bytes=4_096,
                max_depth=16,
                max_items=256,
                max_string=256,
            )
            replacement_listener: socket.socket | None = None
            displaced = fixture.socket_path.with_name("qmp.connected.sock")
            try:
                with self.assertRaisesRegex(
                    QMPStateError,
                    "socket identity is unavailable",
                ):
                    _ = client.socket_identity

                path_at_connect = fixture.socket_path.lstat()
                client.connect()
                captured = client.socket_identity
                expected = QMPSocketIdentity(
                    device_id=path_at_connect.st_dev,
                    inode=path_at_connect.st_ino,
                    uid=path_at_connect.st_uid,
                    mode=stat.S_IMODE(path_at_connect.st_mode),
                )
                self.assertEqual(captured, expected)
                self.assertEqual(captured.uid, os.geteuid())
                self.assertEqual(captured.mode, 0o600)

                fixture.socket_path.rename(displaced)
                replacement_listener = socket.socket(
                    socket.AF_UNIX,
                    socket.SOCK_STREAM,
                )
                replacement_listener.bind(os.fspath(fixture.socket_path))
                os.chmod(fixture.socket_path, 0o600)
                replacement_listener.listen(1)
                current_path = fixture.socket_path.lstat()
                self.assertNotEqual(current_path.st_ino, captured.inode)

                # ``socket_identity`` is immutable evidence of the inode
                # authenticated during connect.  It intentionally does not
                # assert that the pathname still resolves to that inode.
                self.assertIs(client.socket_identity, captured)
                response = client.query_status()
                self.assertEqual(response.value["command"], "query-status")
                self.assertEqual(response.value["server_pid"], fixture.pid)
            finally:
                client.close()
            fixture.stop()
            if replacement_listener is not None:
                replacement_listener.close()
            fixture.socket_path.unlink(missing_ok=True)
            displaced.unlink(missing_ok=True)

    def test_real_process_negotiation_allowlist_fragmentation_and_events(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = (
                "query-status",
                "query-name",
                "query-uuid",
                "query-block",
                "query-cpus-fast",
                "system_powerdown",
                *sorted(BLOCK_GRAPH_QMP_COMMANDS),
                "quit",
            )
            self.assertEqual(set(commands), set(ALLOWED_QMP_COMMANDS))
            fixture = _Fixture(
                root,
                "happy",
                expected_commands=len(commands),
            ).start()
            client = _client(
                fixture,
                max_message_bytes=4_096,
                max_depth=16,
                max_items=256,
                max_string=256,
            )
            try:
                greeting = client.connect()
                self.assertEqual(
                    (
                        greeting.version.major,
                        greeting.version.minor,
                        greeting.version.micro,
                    ),
                    (8, 2, 2),
                )
                self.assertEqual(greeting.version.package, "fixture")
                self.assertEqual(greeting.capabilities, ("oob",))
                self.assertEqual(client.peer_credentials.pid, fixture.pid)
                self.assertEqual(client.peer_credentials.uid, os.geteuid())
                self.assertTrue(client.connected)
                self.assertEqual(
                    stat.S_IMODE(
                        fixture.socket_path.lstat().st_mode
                    ),
                    0o600,
                )
                self.assertEqual(
                    stat.S_IMODE(
                        fixture.socket_path.parent.lstat().st_mode
                    ),
                    0o700,
                )

                responses = []
                core_methods = {
                    "query-status": client.query_status,
                    "query-name": client.query_name,
                    "query-uuid": client.query_uuid,
                    "query-block": client.query_block,
                    "query-cpus-fast": client.query_cpus_fast,
                    "system_powerdown": client.system_powerdown,
                    "quit": client.quit,
                }
                for command in commands:
                    if command in BLOCK_GRAPH_QMP_COMMANDS:
                        if command in {
                            "query-named-block-nodes",
                            "query-block-jobs",
                        }:
                            response = client.execute_block_graph(command)
                        else:
                            response = client.execute_block_graph(
                                command,
                                {
                                    "node-name": f"node-{len(responses)}",
                                    "sequence": len(responses),
                                },
                            )
                    else:
                        response = core_methods[command]()
                    responses.append(response)
                    self.assertEqual(response.command, command)
                    self.assertEqual(response.value["command"], command)
                    self.assertEqual(
                        response.value["server_pid"],
                        fixture.pid,
                    )
                self.assertEqual(
                    [response.command_id for response in responses],
                    list(range(2, len(commands) + 2)),
                )

                # One event arrived before the capability response, one while
                # idle, and one was interleaved before every command response.
                events = [
                    client.next_event(timeout_seconds=2.0)
                    for _ in range(len(commands) + 2)
                ]
                self.assertEqual(events[0].name, "CAPABILITY_READY")
                self.assertEqual(events[1].name, "IDLE_EVENT")
                self.assertEqual(
                    {event.name for event in events[2:]},
                    {"COMMAND_EVENT"},
                )
                self.assertEqual(client.pending_event_count, 0)
            finally:
                client.close()
            fixture.stop()

            transcript = fixture.transcript()
            self.assertEqual(
                transcript[0],
                {"execute": "qmp_capabilities", "id": 1},
            )
            self.assertEqual(
                [row["id"] for row in transcript],
                list(range(1, len(commands) + 2)),
            )
            self.assertEqual(
                [row["execute"] for row in transcript[1:]],
                list(commands),
            )

    def test_concurrent_out_of_order_responses_remain_id_correlated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(
                Path(temporary),
                "out_of_order",
                expected_commands=2,
            ).start()
            client = _client(fixture)
            client.connect()
            responses: dict[str, object] = {}
            failures: list[BaseException] = []
            barrier = threading.Barrier(3)

            def invoke(command: str) -> None:
                try:
                    barrier.wait(timeout=2.0)
                    responses[command] = client.execute(command)
                except BaseException as exc:
                    failures.append(exc)

            threads = [
                threading.Thread(
                    target=invoke,
                    args=(command,),
                    name=f"qmp-{command}",
                    daemon=False,
                )
                for command in ("query-status", "query-uuid")
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2.0)
            for thread in threads:
                thread.join(timeout=3.0)
            try:
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertFalse(failures, failures)
                self.assertEqual(set(responses), {"query-status", "query-uuid"})
                for command, response in responses.items():
                    self.assertEqual(response.command, command)
                    self.assertEqual(response.value["command"], command)
                    self.assertEqual(
                        response.value["server_pid"],
                        fixture.pid,
                    )
                self.assertEqual(
                    client.next_event(timeout_seconds=1.0).name,
                    "CAPABILITY_READY",
                )
                self.assertEqual(
                    client.next_event(timeout_seconds=1.0).name,
                    "INTERLEAVED",
                )
            finally:
                client.close()
            fixture.stop()
            transcript = fixture.transcript()
            self.assertEqual(
                [row["id"] for row in transcript],
                [1, 2, 3],
            )
            self.assertEqual(
                len({row["id"] for row in transcript}),
                3,
            )

    def test_local_allowlist_validation_and_correlated_command_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), "command_error").start()
            client = _client(fixture, max_string=64)
            client.connect()
            try:
                _expect(
                    QMPValidationError,
                    lambda: client.execute("human-monitor-command"),
                )
                _expect(
                    QMPValidationError,
                    lambda: client.execute(
                        "query-status",
                        {"unexpected": True},
                    ),
                )
                _expect(
                    QMPValidationError,
                    lambda: client.execute_block_graph("blockdev-add"),
                )
                _expect(
                    QMPValidationError,
                    lambda: client.execute_block_graph(
                        "query-status",
                        {},
                    ),
                )
                _expect(
                    QMPValidationError,
                    lambda: client.execute(
                        "blockdev-add",
                        {"value": 1.25},
                    ),
                )

                failure = _expect(
                    QMPCommandError,
                    client.query_status,
                )
                self.assertEqual(failure.command, "query-status")
                self.assertEqual(failure.command_id, 2)
                self.assertEqual(failure.error_class, "GenericError")
                self.assertEqual(
                    failure.description,
                    "fixture-secret-description",
                )
                self.assertNotIn(
                    "fixture-secret-description",
                    str(failure),
                )
                self.assertEqual(
                    thaw_json(failure.data),
                    {"retryable": False},
                )
                self.assertTrue(client.connected)
            finally:
                client.close()
            fixture.stop()
            self.assertEqual(
                fixture.transcript(),
                [
                    {"execute": "qmp_capabilities", "id": 1},
                    {"execute": "query-status", "id": 2},
                ],
            )

    def test_one_absolute_deadline_defeats_response_trickle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = _Fixture(Path(temporary), "slow_response").start()
            client = _client(fixture, timeout_seconds=1.0)
            client.connect()
            # Consume the capability event so an event cannot mask the command
            # deadline result.
            self.assertEqual(
                client.next_event(timeout_seconds=1.0).name,
                "CAPABILITY_READY",
            )
            started = time.monotonic()
            try:
                _expect(
                    QMPTimeout,
                    lambda: client.query_status(timeout_seconds=0.15),
                )
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, 0.12)
                self.assertLess(elapsed, 0.6)
                self.assertFalse(client.connected)
                _expect(QMPTimeout, client.query_status)
            finally:
                client.close()
            fixture.stop()

    def test_greeting_and_capability_negotiation_fail_closed(self) -> None:
        cases: tuple[
            tuple[str, type[BaseException], float],
            ...,
        ] = (
            ("greeting_duplicate", QMPHandshakeError, 1.0),
            ("greeting_unknown", QMPHandshakeError, 1.0),
            ("greeting_bad_version", QMPHandshakeError, 1.0),
            ("greeting_missing_caps", QMPHandshakeError, 1.0),
            ("greeting_oversize", QMPProtocolError, 1.0),
            ("slow_greeting", QMPTimeout, 0.15),
            ("capability_error", QMPHandshakeError, 1.0),
            ("capability_mismatch", QMPHandshakeError, 1.0),
            ("capability_nonempty", QMPHandshakeError, 1.0),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (scenario, expected, timeout) in enumerate(cases):
                with self.subTest(scenario=scenario):
                    fixture = _Fixture(
                        root / f"{index}-{scenario}",
                        scenario,
                    ).start()
                    client = _client(fixture, max_string=64)
                    try:
                        _expect(
                            expected,
                            lambda: client.connect(
                                timeout_seconds=timeout
                            ),
                        )
                        self.assertFalse(client.connected)
                        _expect(QMPStateError, lambda: client.connect())
                    finally:
                        client.close()
                    fixture.stop()

    def test_response_bytes_depth_items_strings_and_shapes_are_bounded(
        self,
    ) -> None:
        scenarios = (
            "response_duplicate",
            "response_malformed",
            "response_oversize",
            "response_deep",
            "response_items",
            "response_string",
            "response_mismatch",
            "response_unexpected",
            "response_both",
            "response_no_id",
            "event_bad",
            "response_truncated",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, scenario in enumerate(scenarios):
                with self.subTest(scenario=scenario):
                    fixture = _Fixture(
                        root / f"{index}-{scenario}",
                        scenario,
                    ).start()
                    client = _client(fixture)
                    client.connect()
                    try:
                        # The capability event proves that valid interleaving
                        # still works before each adversarial response.
                        self.assertEqual(
                            client.next_event(timeout_seconds=1.0).name,
                            "CAPABILITY_READY",
                        )
                        _expect(QMPProtocolError, client.query_status)
                        self.assertFalse(client.connected)
                        _expect(QMPProtocolError, client.query_uuid)
                    finally:
                        client.close()
                    fixture.stop()

    def test_event_queue_is_bounded_and_idle_wait_has_a_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overflow = _Fixture(root / "overflow", "event_overflow").start()
            client = _client(overflow, max_events=1)
            client.connect()
            try:
                deadline = time.monotonic() + 2.0
                while client.connected and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertFalse(client.connected)
                _expect(QMPProtocolError, client.query_status)
            finally:
                client.close()
            overflow.stop()

            idle = _Fixture(root / "idle", "idle").start()
            client = _client(idle)
            client.connect()
            try:
                self.assertEqual(
                    client.next_event(timeout_seconds=1.0).name,
                    "CAPABILITY_READY",
                )
                started = time.monotonic()
                _expect(
                    QMPTimeout,
                    lambda: client.next_event(timeout_seconds=0.1),
                )
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, 0.08)
                self.assertLess(elapsed, 0.5)
                self.assertTrue(client.connected)
            finally:
                client.close()
            idle.stop()

    def test_socket_path_privacy_peer_authorization_and_cold_import(self) -> None:
        _expect(
            QMPValidationError,
            lambda: QMPClient("relative/qmp.sock"),
        )
        _expect(
            QMPValidationError,
            lambda: QMPClient("/tmp/../tmp/qmp.sock"),
        )
        _expect(
            QMPValidationError,
            lambda: QMPClient("/" + ("x" * 200)),
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unauthorized = _Fixture(
                root / "unauthorized",
                "idle",
            ).start()
            client = _client(
                unauthorized,
                allowed_server_uids=(),
            )
            try:
                _expect(QMPUnavailable, client.connect)
            finally:
                client.close()
            # No client could connect, so terminating this waiting fixture is
            # cleanup rather than evidence.
            unauthorized.stop(check=False)

            permissive = _Fixture(
                root / "permissive",
                "permissive_socket",
            ).start()
            client = _client(permissive)
            try:
                _expect(QMPUnavailable, client.connect)
            finally:
                client.close()
            permissive.stop(check=False)

        child = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import json,sys;"
                    f"sys.path.insert(0,{str(SOURCE_ROOT)!r});"
                    "import somnus_vm.host.qmp;"
                    "print(json.dumps(sorted(name for name in sys.modules if "
                    "name.startswith(('somnus_vm.daemon',"
                    "'somnus_vm.host.registry','somnus_vm.host.storage',"
                    "'somnus_vm.guest','components')))))"
                ),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
