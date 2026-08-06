"""Physical P2 proof for the authenticated local daemon transport shell.

Source: PLAN.md P2 daemon/ownership/transport lane
Integrated: 2026-08-05
Purpose: Exercises real Unix sockets, kernel peer credentials, daemon locking,
    strict one-frame request/response semantics, concurrent clients, deadlines,
    stale endpoints, and closed error mapping without supplying a mutation
    service or promoting public lifecycle commands.
IO: Temporary private directories, AF_UNIX sockets, threads, and one real
    isolated Python client process.  No mocks, network fallback, or fake-success
    transport is used.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
    ERROR_CODE_REGISTRY,
    ErrorCode,
    ErrorEnvelope,
)
from somnus_vm.client import UnixControlClient
from somnus_vm.config import DaemonSettings
from somnus_vm.daemon import UnixControlDaemon
from somnus_vm.transport import (
    DEFAULT_MAX_FRAME_BYTES,
    DaemonOwnershipError,
    FrameProtocolError,
    FrameTooLarge,
    PeerAuthenticationError,
    TransportTimeout,
)


_FRAME_HEADER = struct.Struct("!I")
_VM_ID = UUID("11111111-1111-4111-8111-111111111111")
_BOOT_ID = UUID("22222222-2222-4222-8222-222222222222")
_SECRET_MARKER = "never-reflect-this-payload-or-exception"


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
    raise AssertionError("expected a fail-closed exception")


def _request(
    operation: str = "transport.echo",
    *,
    payload: dict[str, object] | None = None,
) -> ControlRequest:
    return ControlRequest(
        schema_version=CURRENT_SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=uuid4(),
        correlation_id=uuid4(),
        vm_id=_VM_ID,
        boot_id=_BOOT_ID,
        generation=7,
        timestamp=datetime.now(timezone.utc),
        operation=operation,
        payload=payload or {},
    )


def _success(
    request: ControlRequest,
    result: dict[str, object],
    *,
    request_id: UUID | None = None,
) -> ControlResponse:
    return ControlResponse(
        schema_version=request.schema_version,
        protocol_version=request.protocol_version,
        request_id=request_id or request.request_id,
        correlation_id=request.correlation_id,
        operation_id=uuid4(),
        vm_id=request.vm_id,
        boot_id=request.boot_id,
        generation=request.generation,
        timestamp=datetime.now(timezone.utc),
        result=result,
    )


def _closed_error(
    request: ControlRequest,
    code: ErrorCode,
) -> ErrorEnvelope:
    specification = ERROR_CODE_REGISTRY[code]
    return ErrorEnvelope(
        schema_version=request.schema_version,
        protocol_version=request.protocol_version,
        request_id=request.request_id,
        correlation_id=request.correlation_id,
        operation_id=uuid4(),
        vm_id=request.vm_id,
        boot_id=request.boot_id,
        generation=request.generation,
        timestamp=datetime.now(timezone.utc),
        code=code,
        category=specification.category,
        exit_code=specification.exit_code,
    )


class _ExerciseHandler:
    """Thread-safe injected handler with no daemon or registry authority."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(
        self,
        request: ControlRequest,
        peer: object,
    ) -> ControlResponse | ErrorEnvelope:
        with self._lock:
            self.calls += 1
        pid = getattr(peer, "pid")
        uid = getattr(peer, "uid")
        gid = getattr(peer, "gid")
        if request.operation == "transport.raise":
            raise RuntimeError(_SECRET_MARKER)
        if request.operation == "transport.mismatch":
            return _success(
                request,
                {"untrusted": _SECRET_MARKER},
                request_id=uuid4(),
            )
        if request.operation == "transport.slow":
            time.sleep(0.55)
            return _success(request, {"late": True})
        if request.operation == "transport.policy":
            return _closed_error(request, ErrorCode.POLICY_REJECTED)
        return _success(
            request,
            {
                "peer_pid": pid,
                "peer_uid": uid,
                "peer_gid": gid,
                "index": request.payload.get("index"),
            },
        )


def _settings(
    root: Path,
    *,
    name: str = "control.sock",
    timeout: float = 0.4,
) -> DaemonSettings:
    runtime = root / "runtime"
    return DaemonSettings(
        runtime_root=runtime,
        control_socket=runtime / name,
        request_timeout_seconds=timeout,
    )


def _frame(payload: bytes) -> bytes:
    return _FRAME_HEADER.pack(len(payload)) + payload


def _raw_exchange(
    path: Path,
    outgoing: bytes,
    *,
    half_close: bool = True,
    timeout: float = 2.0,
) -> bytes:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(os.fspath(path))
        connection.sendall(outgoing)
        if half_close:
            connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = connection.recv(65_536)
            except ConnectionResetError:
                # Linux may emit RST rather than orderly EOF when the server
                # rejects a duplicate/trailing frame while unread bytes remain.
                return b"".join(chunks)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        connection.close()


def _check_modes(settings: DaemonSettings) -> Path:
    socket_mode = stat.S_IMODE(settings.control_socket.lstat().st_mode)
    runtime_mode = stat.S_IMODE(settings.runtime_root.lstat().st_mode)
    lock = settings.control_socket.with_name(
        f".{settings.control_socket.name}.lock"
    )
    lock_mode = stat.S_IMODE(lock.lstat().st_mode)
    assert socket_mode == 0o600
    assert runtime_mode == 0o700
    assert lock_mode == 0o600
    assert settings.control_socket.lstat().st_uid == os.geteuid()
    assert settings.runtime_root.lstat().st_uid == os.geteuid()
    assert lock.lstat().st_uid == os.geteuid()
    return lock


def _check_real_child_process(path: Path) -> None:
    script = r"""
import json, os, sys
from datetime import datetime, timezone
from uuid import uuid4, UUID
sys.path.insert(0, sys.argv[1])
from somnus_protocol import (
    CURRENT_PROTOCOL_VERSION, CURRENT_SCHEMA_VERSION, ControlRequest
)
from somnus_vm.client import UnixControlClient
request = ControlRequest(
    schema_version=CURRENT_SCHEMA_VERSION,
    protocol_version=CURRENT_PROTOCOL_VERSION,
    request_id=uuid4(),
    correlation_id=uuid4(),
    vm_id=UUID("11111111-1111-4111-8111-111111111111"),
    boot_id=UUID("22222222-2222-4222-8222-222222222222"),
    generation=7,
    timestamp=datetime.now(timezone.utc),
    operation="transport.echo",
    payload={"index": 9001},
)
response = UnixControlClient(sys.argv[2], timeout_seconds=2.0).request(request)
print(json.dumps({
    "child_pid": os.getpid(),
    "response": response.to_dict(),
}, sort_keys=True))
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(SOURCE_ROOT),
            str(path),
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process_pid = process.pid
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr
    payload = json.loads(stdout)
    assert payload["child_pid"] == process_pid
    assert payload["response"]["result"]["peer_pid"] == process_pid
    assert payload["response"]["result"]["peer_uid"] == os.geteuid()


def _check_hostile_framing(
    daemon: UnixControlDaemon,
    client: UnixControlClient,
) -> None:
    path = daemon.socket_path
    request = _request(payload={"secret": _SECRET_MARKER})
    request_bytes = request.to_json().encode("utf-8")

    started = time.monotonic()
    oversized = _raw_exchange(
        path,
        _FRAME_HEADER.pack(DEFAULT_MAX_FRAME_BYTES + 1),
    )
    assert oversized == b""
    assert time.monotonic() - started < 0.35

    assert _raw_exchange(path, _FRAME_HEADER.pack(20) + b"{}") == b""
    assert _raw_exchange(path, _frame(request_bytes) + _frame(request_bytes)) == b""
    assert _raw_exchange(path, _frame(b"{}")) == b""

    # A complete request without a write-side EOF is not dispatchable.
    started = time.monotonic()
    assert _raw_exchange(
        path,
        _frame(request_bytes),
        half_close=False,
    ) == b""
    elapsed = time.monotonic() - started
    assert 0.30 <= elapsed < 1.5

    # The absolute accepted-at deadline includes handler execution.
    started = time.monotonic()
    _expect(
        FrameProtocolError,
        lambda: client.request(
            _request("transport.slow"),
            timeout_seconds=1.5,
        ),
    )
    elapsed = time.monotonic() - started
    assert 0.30 <= elapsed < 0.8


def _hostile_response_case(
    root: Path,
    outgoing: bytes,
    expected: type[BaseException],
    *,
    response_delay: float = 0.0,
    client_timeout: float = 0.5,
) -> None:
    """Exercise client-side framing against a real one-shot Unix listener."""

    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    path = root / "hostile.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(os.fspath(path))
    os.chmod(path, 0o600)
    listener.listen(1)
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2.0)
                while connection.recv(65_536):
                    pass
                if response_delay:
                    time.sleep(response_delay)
                try:
                    connection.sendall(outgoing)
                    connection.shutdown(socket.SHUT_WR)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=serve, name="hostile-response", daemon=False)
    thread.start()
    try:
        client = UnixControlClient(path, timeout_seconds=client_timeout)
        _expect(expected, lambda: client.request(_request()))
    finally:
        thread.join(timeout=3.0)
        listener.close()
        if path.exists():
            path.unlink()
    assert not thread.is_alive()
    assert not failures, failures


def _check_hostile_responses(root: Path) -> None:
    _hostile_response_case(
        root / "oversized-response",
        _FRAME_HEADER.pack(DEFAULT_MAX_FRAME_BYTES + 1),
        FrameTooLarge,
    )
    _hostile_response_case(
        root / "truncated-response",
        _FRAME_HEADER.pack(20) + b"{}",
        FrameProtocolError,
    )
    valid = _closed_error(_request(), ErrorCode.UNAVAILABLE).to_json().encode(
        "utf-8"
    )
    _hostile_response_case(
        root / "duplicate-response",
        _frame(valid) + _frame(valid),
        FrameProtocolError,
    )
    _hostile_response_case(
        root / "timeout-response",
        _frame(valid),
        TransportTimeout,
        response_delay=0.3,
        client_timeout=0.1,
    )


def _check_client_authenticates_server(root: Path) -> None:
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / "untrusted-server.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(os.fspath(path))
    os.chmod(path, 0o600)
    listener.listen(1)
    received: list[bytes] = []

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            chunks: list[bytes] = []
            while True:
                chunk = connection.recv(65_536)
                if not chunk:
                    break
                chunks.append(chunk)
            received.append(b"".join(chunks))

    thread = threading.Thread(
        target=serve,
        name="unauthorized-server",
        daemon=False,
    )
    thread.start()
    try:
        client = UnixControlClient(
            path,
            timeout_seconds=1.0,
            allowed_server_uids=(),
        )
        _expect(PeerAuthenticationError, lambda: client.request(_request()))
    finally:
        thread.join(timeout=2.0)
        listener.close()
        if path.exists():
            path.unlink()
    assert not thread.is_alive()
    assert received == [b""]


def _check_stale_live_and_unsafe_paths(root: Path) -> None:
    handler = _ExerciseHandler()

    stale_settings = _settings(root / "stale", timeout=1.0)
    stale_settings.runtime_root.mkdir(parents=True, mode=0o700)
    os.chmod(stale_settings.runtime_root, 0o700)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(os.fspath(stale_settings.control_socket))
    stale.close()
    fake_pid_lock = stale_settings.control_socket.with_name(
        f".{stale_settings.control_socket.name}.lock"
    )
    fake_pid_lock.write_text("999999\\n", encoding="utf-8")
    os.chmod(fake_pid_lock, 0o600)
    stale_daemon = UnixControlDaemon(stale_settings, handler)
    stale_daemon.start()
    try:
        response = UnixControlClient(
            stale_settings.control_socket,
            timeout_seconds=2.0,
        ).request(_request())
        assert isinstance(response, ControlResponse)
    finally:
        stale_daemon.shutdown()
    assert not stale_settings.control_socket.exists()
    # The unlocked file and its fake PID were never accepted as authority.
    assert fake_pid_lock.read_text(encoding="utf-8") == "999999\\n"

    live_settings = _settings(root / "live", timeout=1.0)
    live_settings.runtime_root.mkdir(parents=True, mode=0o700)
    os.chmod(live_settings.runtime_root, 0o700)
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    live.bind(os.fspath(live_settings.control_socket))
    os.chmod(live_settings.control_socket, 0o600)
    live.listen(1)
    original = live_settings.control_socket.lstat()
    conflicting = UnixControlDaemon(live_settings, handler)
    _expect(DaemonOwnershipError, conflicting.start)
    preserved = live_settings.control_socket.lstat()
    assert (preserved.st_dev, preserved.st_ino) == (
        original.st_dev,
        original.st_ino,
    )
    live.close()
    live_settings.control_socket.unlink()

    unsafe_settings = _settings(root / "unsafe", timeout=1.0)
    unsafe_settings.runtime_root.mkdir(parents=True, mode=0o700)
    os.chmod(unsafe_settings.runtime_root, 0o700)
    unsafe_settings.control_socket.write_text("not a socket", encoding="utf-8")
    unsafe = UnixControlDaemon(unsafe_settings, handler)
    _expect(DaemonOwnershipError, unsafe.start)
    assert unsafe_settings.control_socket.read_text(encoding="utf-8") == "not a socket"

    permissive_settings = _settings(root / "permissive", timeout=1.0)
    permissive_settings.runtime_root.mkdir(parents=True, mode=0o755)
    os.chmod(permissive_settings.runtime_root, 0o755)
    permissive = UnixControlDaemon(permissive_settings, handler)
    _expect(DaemonOwnershipError, permissive.start)
    assert not permissive_settings.control_socket.exists()


def _check_unauthorized_real_peer(root: Path) -> None:
    handler = _ExerciseHandler()
    settings = _settings(root / "unauthorized", timeout=0.5)
    daemon = UnixControlDaemon(settings, handler, allowed_uids=())
    daemon.start()
    try:
        client = UnixControlClient(settings.control_socket, timeout_seconds=1.0)
        rejection = _expect(FrameProtocolError, lambda: client.request(_request()))
        assert _SECRET_MARKER not in str(rejection)
        assert handler.calls == 0
    finally:
        daemon.shutdown()


def _check_shutdown_ownership_retention(root: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_handler(
        request: ControlRequest,
        peer: object,
    ) -> ControlResponse:
        entered.set()
        if not release.wait(timeout=5.0):
            raise RuntimeError("test handler release was not signaled")
        return _success(request, {"peer_uid": getattr(peer, "uid")})

    settings = _settings(root / "shutdown", timeout=2.0)
    daemon = UnixControlDaemon(settings, blocking_handler, max_workers=1)
    daemon.start()
    client = UnixControlClient(settings.control_socket, timeout_seconds=3.0)
    results: list[ControlResponse | ErrorEnvelope] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            results.append(client.request(_request()))
        except BaseException as exc:
            failures.append(exc)

    caller = threading.Thread(target=invoke, name="blocking-client", daemon=False)
    caller.start()
    assert entered.wait(timeout=1.0)
    _expect(TransportTimeout, lambda: daemon.shutdown(timeout_seconds=0.05))
    assert settings.control_socket.exists()
    second = UnixControlDaemon(settings, _ExerciseHandler())
    _expect(DaemonOwnershipError, second.start)

    release.set()
    caller.join(timeout=2.0)
    assert not caller.is_alive()
    assert not failures, failures
    assert len(results) == 1 and isinstance(results[0], ControlResponse)
    daemon.shutdown(timeout_seconds=2.0)
    assert not settings.control_socket.exists()


def _check_inode_safe_cleanup(root: Path) -> None:
    settings = _settings(root / "inode-cleanup", timeout=1.0)
    daemon = UnixControlDaemon(settings, _ExerciseHandler())
    daemon.start()
    original = settings.control_socket.lstat()
    settings.control_socket.unlink()

    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(os.fspath(settings.control_socket))
    os.chmod(settings.control_socket, 0o600)
    replacement.listen(1)
    replacement_metadata = settings.control_socket.lstat()
    assert (replacement_metadata.st_dev, replacement_metadata.st_ino) != (
        original.st_dev,
        original.st_ino,
    )
    try:
        daemon.shutdown(timeout_seconds=2.0)
        preserved = settings.control_socket.lstat()
        assert (preserved.st_dev, preserved.st_ino) == (
            replacement_metadata.st_dev,
            replacement_metadata.st_ino,
        )
    finally:
        replacement.close()
        if settings.control_socket.exists():
            settings.control_socket.unlink()


def _check_concurrent_shutdown(root: Path) -> None:
    settings = _settings(root / "concurrent-shutdown", timeout=1.0)
    daemon = UnixControlDaemon(settings, _ExerciseHandler())
    daemon.start()
    failures: list[BaseException] = []

    def stop() -> None:
        try:
            daemon.shutdown(timeout_seconds=2.0)
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=stop, name=f"shutdown-{index}", daemon=False)
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)
    assert all(not thread.is_alive() for thread in threads)
    assert not failures, failures
    assert not settings.control_socket.exists()


def _check_cold_import_boundary() -> None:
    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import json,sys;"
                f"sys.path.insert(0,{str(SOURCE_ROOT)!r});"
                "import somnus_vm.daemon,somnus_vm.client;"
                "print(json.dumps(sorted(name for name in sys.modules if "
                "name.startswith(('somnus_vm.registry','somnus_vm.host.qemu',"
                "'somnus_vm.guest','components')))))"
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


def run_daemon_transport_p2_checks() -> str:
    """Prove authenticated, bounded, exclusive, real local transport."""

    assert hasattr(socket, "SO_PEERCRED"), "P2 Linux peer credentials unavailable"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        settings = _settings(root / "main")
        handler = _ExerciseHandler()
        daemon = UnixControlDaemon(
            settings,
            handler,
            max_workers=8,
            backlog=64,
        )
        daemon.start()
        lock_path = _check_modes(settings)
        client = UnixControlClient(
            settings.control_socket,
            timeout_seconds=2.0,
        )
        try:
            request = _request(payload={"index": 1, "secret": _SECRET_MARKER})
            response = client.request(request)
            assert isinstance(response, ControlResponse)
            assert response.request_id == request.request_id
            assert response.correlation_id == request.correlation_id
            assert response.result["peer_pid"] == os.getpid()
            assert response.result["peer_uid"] == os.geteuid()
            assert _SECRET_MARKER not in response.to_json()

            policy = client.request(_request("transport.policy"))
            assert isinstance(policy, ErrorEnvelope)
            assert policy.code is ErrorCode.POLICY_REJECTED

            raised = client.request(
                _request(
                    "transport.raise",
                    payload={"secret": _SECRET_MARKER},
                )
            )
            assert isinstance(raised, ErrorEnvelope)
            assert raised.code is ErrorCode.INTERNAL_ERROR
            assert _SECRET_MARKER not in raised.to_json()

            mismatched = client.request(_request("transport.mismatch"))
            assert isinstance(mismatched, ErrorEnvelope)
            assert mismatched.code is ErrorCode.INTERNAL_ERROR
            assert _SECRET_MARKER not in mismatched.to_json()

            _check_real_child_process(settings.control_socket)

            def invoke(index: int) -> tuple[int, int]:
                current = _request(payload={"index": index})
                result = client.request(current)
                assert isinstance(result, ControlResponse)
                assert result.request_id == current.request_id
                assert result.correlation_id == current.correlation_id
                return int(result.result["index"]), int(result.result["peer_uid"])

            with ThreadPoolExecutor(max_workers=24) as pool:
                concurrent = list(pool.map(invoke, range(48)))
            assert sorted(index for index, _ in concurrent) == list(range(48))
            assert {uid for _, uid in concurrent} == {os.geteuid()}

            second = UnixControlDaemon(settings, _ExerciseHandler())
            _expect(DaemonOwnershipError, second.start)
            assert daemon.running

            _check_hostile_framing(daemon, client)
        finally:
            daemon.shutdown(timeout_seconds=5.0)

        assert daemon.wait(timeout_seconds=0.0)
        assert not daemon.running
        assert not settings.control_socket.exists()
        assert lock_path.exists()
        assert stat.S_IMODE(lock_path.lstat().st_mode) == 0o600

        _check_stale_live_and_unsafe_paths(root)
        _check_unauthorized_real_peer(root)
        _check_hostile_responses(root)
        _check_client_authenticates_server(root)
        _check_shutdown_ownership_retention(root)
        _check_inode_safe_cleanup(root)
        _check_concurrent_shutdown(root)

    _check_cold_import_boundary()
    return (
        "proved real SO_PEERCRED-authenticated Unix request/response transport, "
        "strict pre-JSON byte framing, absolute deadlines, concurrent clients, "
        "exclusive lock ownership, stale/live socket discrimination, private "
        "permissions, closed redacted errors, and deterministic cleanup"
    )


if __name__ == "__main__":
    print(run_daemon_transport_p2_checks())
