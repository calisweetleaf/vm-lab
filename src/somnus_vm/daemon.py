"""Authenticated local control daemon shell.

Source: PLAN.md P2 daemon/ownership boundary
Integrated: 2026-08-05
Purpose: Owns one private Unix-domain endpoint and dispatches canonical
    ``ControlRequest`` values to an injected operation owner.
IO: Private runtime directories, an advisory ownership lock, one AF_UNIX
    listener, real kernel peer credentials, and bounded worker threads.

This module intentionally imports no registry, QEMU, image, guest, lifecycle,
or operator implementation.  A future mutation service supplies the handler;
the daemon only authenticates, frames, validates, dispatches, and returns one
closed canonical envelope.
"""

from __future__ import annotations

import errno
import fcntl
import os
import socket
import stat
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TypeAlias

from somnus_protocol import (
    ControlRequest,
    ControlResponse,
    ERROR_CODE_REGISTRY,
    ErrorCode,
    ErrorEnvelope,
)

from .config import DaemonSettings
from .transport import (
    DEFAULT_MAX_FRAME_BYTES,
    DaemonOwnershipError,
    FrameProtocolError,
    PeerAuthenticationError,
    PeerCredentials,
    TransportError,
    TransportTimeout,
    deadline_after,
    get_peer_credentials,
    normalize_socket_path,
    normalize_uid_allowlist,
    receive_single_frame,
    send_single_frame,
    validate_frame_limit,
)


ResponseEnvelope: TypeAlias = ControlResponse | ErrorEnvelope


class RequestHandler(Protocol):
    """Injected owner of operation semantics and mutation authority."""

    def __call__(
        self,
        request: ControlRequest,
        peer: PeerCredentials,
    ) -> ResponseEnvelope:
        """Return exactly one canonical response for an authenticated request."""


def _validate_positive_int(
    value: object,
    label: str,
    *,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > maximum
    ):
        raise TransportError(f"{label} must be between 1 and {maximum}")
    return value


def _validate_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DaemonOwnershipError(
            "private runtime directory metadata is unavailable"
        ) from exc
    if resolved != path:
        raise DaemonOwnershipError(
            "private runtime directory cannot traverse a symbolic link"
        )
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DaemonOwnershipError(
            "private runtime path is not a real directory"
        )
    if metadata.st_uid != os.geteuid():
        raise DaemonOwnershipError(
            "private runtime directory has a different owner"
        )
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise DaemonOwnershipError(
            "private runtime directory permissions must be 0700"
        )


def _create_private_directory(path: Path) -> None:
    """Create one directory chain and validate every daemon-owned component."""

    try:
        if path.resolve(strict=False) != path:
            raise DaemonOwnershipError(
                "private runtime directory cannot traverse a symbolic link"
            )
    except OSError as exc:
        raise DaemonOwnershipError(
            "private runtime directory cannot be resolved safely"
        ) from exc

    missing: list[Path] = []
    cursor = path
    while True:
        try:
            cursor.lstat()
            break
        except FileNotFoundError:
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise DaemonOwnershipError(
                    "private runtime directory has no existing ancestor"
                )
            cursor = parent
        except OSError as exc:
            raise DaemonOwnershipError(
                "private runtime directory metadata is unavailable"
            ) from exc

    for component in reversed(missing):
        try:
            component.mkdir(mode=0o700)
        except FileExistsError:
            # A racing creator is accepted only if the same strict validation
            # below proves it is our private directory.
            pass
        except OSError as exc:
            raise DaemonOwnershipError(
                "private runtime directory could not be created"
            ) from exc
        _validate_private_directory(component)
    _validate_private_directory(path)


def _prepare_private_tree(runtime_root: Path, socket_parent: Path) -> None:
    if not runtime_root.is_absolute():
        raise DaemonOwnershipError("runtime root must be absolute")
    try:
        if socket_parent.resolve(strict=False) != socket_parent:
            raise DaemonOwnershipError(
                "control socket directory cannot traverse a symbolic link"
            )
    except OSError as exc:
        raise DaemonOwnershipError(
            "control socket directory cannot be resolved safely"
        ) from exc
    try:
        socket_parent.relative_to(runtime_root)
    except ValueError as exc:
        raise DaemonOwnershipError(
            "control socket must remain below the runtime root"
        ) from exc

    _create_private_directory(runtime_root)
    current = runtime_root
    relative = socket_parent.relative_to(runtime_root)
    for component in relative.parts:
        current = current / component
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise DaemonOwnershipError(
                "control socket directory could not be created"
            ) from exc
        _validate_private_directory(current)


class _OwnershipLock:
    """Held advisory lock; pathname content is never treated as authority."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise DaemonOwnershipError("daemon ownership lock is already held")
        flags = os.O_RDWR | os.O_CLOEXEC
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            fd = os.open(self.path, flags | nofollow | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            try:
                fd = os.open(self.path, flags | nofollow)
            except OSError as exc:
                raise DaemonOwnershipError(
                    "daemon ownership lock is unsafe or unavailable"
                ) from exc
        except OSError as exc:
            raise DaemonOwnershipError(
                "daemon ownership lock could not be created"
            ) from exc

        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
            ):
                raise DaemonOwnershipError(
                    "daemon ownership lock has an invalid type or owner"
                )
            if created:
                os.fchmod(fd, 0o600)
                metadata = os.fstat(fd)
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise DaemonOwnershipError(
                    "daemon ownership lock permissions must be 0600"
                )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise DaemonOwnershipError(
                        "daemon ownership lock is held"
                    ) from exc
                raise DaemonOwnershipError(
                    "daemon ownership lock could not be acquired"
                ) from exc

            path_metadata = self.path.lstat()
            if (
                path_metadata.st_dev != metadata.st_dev
                or path_metadata.st_ino != metadata.st_ino
            ):
                raise DaemonOwnershipError(
                    "daemon ownership lock changed during acquisition"
                )
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _probe_existing_socket(path: Path, timeout_seconds: float) -> bool:
    """Return true for a live/accepting endpoint and false only for dead."""

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(min(timeout_seconds, 0.25))
        try:
            probe.connect(os.fspath(path))
        except FileNotFoundError:
            return False
        except ConnectionRefusedError:
            return False
        except TimeoutError as exc:
            raise DaemonOwnershipError(
                "existing control socket liveness is ambiguous"
            ) from exc
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ECONNREFUSED}:
                return False
            raise DaemonOwnershipError(
                "existing control socket cannot be safely classified"
            ) from exc
        return True
    finally:
        probe.close()


def _remove_stale_socket(path: Path, timeout_seconds: float) -> None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DaemonOwnershipError(
            "existing control socket metadata is unavailable"
        ) from exc
    if not stat.S_ISSOCK(before.st_mode):
        raise DaemonOwnershipError(
            "control socket path exists and is not a socket"
        )
    if before.st_uid != os.geteuid():
        raise DaemonOwnershipError(
            "existing control socket has a different owner"
        )
    if _probe_existing_socket(path, timeout_seconds):
        raise DaemonOwnershipError("another live control socket owns the path")
    try:
        after = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISSOCK(after.st_mode)
        or after.st_uid != before.st_uid
        or after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
    ):
        raise DaemonOwnershipError(
            "control socket changed during stale-socket discrimination"
        )
    try:
        path.unlink()
    except OSError as exc:
        raise DaemonOwnershipError(
            "stale control socket could not be removed"
        ) from exc


def _internal_error(request: ControlRequest) -> ErrorEnvelope:
    specification = ERROR_CODE_REGISTRY[ErrorCode.INTERNAL_ERROR]
    return ErrorEnvelope(
        schema_version=request.schema_version,
        protocol_version=request.protocol_version,
        request_id=request.request_id,
        correlation_id=request.correlation_id,
        operation_id=None,
        vm_id=request.vm_id,
        boot_id=request.boot_id,
        generation=request.generation,
        timestamp=datetime.now(timezone.utc),
        code=ErrorCode.INTERNAL_ERROR,
        category=specification.category,
        exit_code=specification.exit_code,
    )


def _response_matches(
    request: ControlRequest,
    response: ResponseEnvelope,
) -> bool:
    return (
        response.schema_version == request.schema_version
        and response.protocol_version == request.protocol_version
        and response.request_id == request.request_id
        and response.correlation_id == request.correlation_id
        and response.vm_id == request.vm_id
        and response.boot_id == request.boot_id
        and response.generation == request.generation
    )


class UnixControlDaemon:
    """One authenticated, bounded local control endpoint.

    ``start()`` returns only after the ownership lock is held, the socket has
    been bound/listened with mode ``0600``, and the accept loop has entered its
    running state.  Callers therefore need no pathname/PID readiness heuristic.
    """

    def __init__(
        self,
        settings: DaemonSettings,
        handler: RequestHandler,
        *,
        allowed_uids: Iterable[int] | None = None,
        max_workers: int = 8,
        backlog: int = 64,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if not isinstance(settings, DaemonSettings):
            raise TransportError("settings must be DaemonSettings")
        if not isinstance(handler, Callable):
            raise TransportError("handler must be callable")
        self._settings = settings
        self._handler = handler
        self._allowed_uids = normalize_uid_allowlist(allowed_uids)
        self._max_workers = _validate_positive_int(
            max_workers, "max_workers", maximum=256
        )
        self._backlog = _validate_positive_int(
            backlog, "backlog", maximum=4096
        )
        self._max_frame_bytes = validate_frame_limit(max_frame_bytes)
        self._socket_path = normalize_socket_path(settings.control_socket)
        self._runtime_root = Path(settings.runtime_root)
        self._lock = _OwnershipLock(
            self._socket_path.with_name(f".{self._socket_path.name}.lock")
        )

        self._lifecycle_lock = threading.RLock()
        self._shutdown_lock = threading.Lock()
        self._future_lock = threading.Lock()
        self._worker_local = threading.local()
        self._stop = threading.Event()
        self._accept_ready = threading.Event()
        self._accept_failed = threading.Event()
        self._terminated = threading.Event()
        # Bound both running work and the executor queue.  The kernel listen
        # backlog remains independently bounded, so a connection flood cannot
        # create an unbounded Python queue.
        self._capacity = threading.BoundedSemaphore(
            self._max_workers + self._backlog
        )
        self._listener: socket.socket | None = None
        self._listener_identity: tuple[int, int] | None = None
        self._accept_thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future[None]] = set()
        self._state = "new"

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            return self._state == "running" and not self._accept_failed.is_set()

    def _bind_listener(self) -> socket.socket:
        _prepare_private_tree(self._runtime_root, self._socket_path.parent)
        self._lock.acquire()
        try:
            _remove_stale_socket(
                self._socket_path,
                self._settings.request_timeout_seconds,
            )
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(os.fspath(self._socket_path))
                os.chmod(self._socket_path, 0o600)
                metadata = self._socket_path.lstat()
                if (
                    not stat.S_ISSOCK(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise DaemonOwnershipError(
                        "bound control socket failed ownership validation"
                    )
                self._listener_identity = (metadata.st_dev, metadata.st_ino)
                listener.listen(self._backlog)
                listener.settimeout(0.1)
                return listener
            except BaseException:
                listener.close()
                self._unlink_owned_socket()
                raise
        except BaseException:
            self._lock.release()
            raise

    def _unlink_owned_socket(self) -> None:
        identity = self._listener_identity
        self._listener_identity = None
        if identity is None:
            return
        try:
            metadata = self._socket_path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            return
        if (
            stat.S_ISSOCK(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and (metadata.st_dev, metadata.st_ino) == identity
        ):
            try:
                self._socket_path.unlink()
            except FileNotFoundError:
                pass

    def start(self) -> None:
        """Own, bind, listen, and activate the background accept loop."""

        with self._lifecycle_lock:
            if self._state != "new":
                raise DaemonOwnershipError(
                    "daemon instance cannot be started more than once"
                )
            self._state = "starting"
            try:
                listener = self._bind_listener()
                executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="somnus-control",
                )
                self._listener = listener
                self._executor = executor
                thread = threading.Thread(
                    target=self._accept_loop,
                    name="somnus-control-accept",
                    daemon=False,
                )
                self._accept_thread = thread
                thread.start()
                if not self._accept_ready.wait(timeout=1.0):
                    raise DaemonOwnershipError(
                        "daemon accept loop did not become ready"
                    )
                if self._accept_failed.is_set() or not thread.is_alive():
                    raise DaemonOwnershipError(
                        "daemon accept loop failed during startup"
                    )
                self._state = "running"
            except BaseException:
                self._stop.set()
                if self._listener is not None:
                    self._listener.close()
                    self._listener = None
                if (
                    self._accept_thread is not None
                    and self._accept_thread is not threading.current_thread()
                ):
                    self._accept_thread.join(timeout=1.0)
                if self._executor is not None:
                    self._executor.shutdown(wait=True, cancel_futures=True)
                    self._executor = None
                self._unlink_owned_socket()
                self._lock.release()
                self._state = "stopped"
                self._terminated.set()
                raise

    def serve_forever(self) -> None:
        """Start and block until another thread invokes ``shutdown()``."""

        self.start()
        try:
            while not self._terminated.wait(timeout=0.1):
                if self._accept_failed.is_set():
                    self.shutdown()
                    raise DaemonOwnershipError(
                        "daemon accept loop failed"
                    )
        except KeyboardInterrupt:
            self.shutdown()
            raise

    def _accept_loop(self) -> None:
        self._accept_ready.set()
        while not self._stop.is_set():
            listener = self._listener
            executor = self._executor
            if listener is None or executor is None:
                return
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                self._accept_failed.set()
                self._stop.set()
                return
            accepted_at = time.monotonic()
            if not self._capacity.acquire(blocking=False):
                connection.close()
                continue
            try:
                future = executor.submit(
                    self._serve_connection,
                    connection,
                    accepted_at,
                )
            except BaseException:
                self._capacity.release()
                connection.close()
                continue
            with self._future_lock:
                self._futures.add(future)
            future.add_done_callback(self._connection_done)

    def _connection_done(self, future: Future[None]) -> None:
        with self._future_lock:
            self._futures.discard(future)
        self._capacity.release()
        # Retrieve the result so a BaseException from an injected handler does
        # not become an unobserved worker failure.  Nothing is logged/reflected.
        try:
            future.result()
        except BaseException:
            pass

    def _serve_connection(
        self,
        connection: socket.socket,
        accepted_at: float,
    ) -> None:
        deadline = accepted_at + self._settings.request_timeout_seconds
        with connection:
            try:
                peer = get_peer_credentials(connection)
                if peer.uid not in self._allowed_uids:
                    raise PeerAuthenticationError(
                        "kernel-authenticated peer uid is not authorized"
                    )
                payload = receive_single_frame(
                    connection,
                    deadline=deadline,
                    max_frame_bytes=self._max_frame_bytes,
                    require_eof=True,
                )
                request = ControlRequest.from_json(payload)
            except (
                PeerAuthenticationError,
                FrameProtocolError,
                TransportTimeout,
                ValueError,
                TypeError,
                UnicodeError,
                RecursionError,
            ):
                # No reflection occurs before both peer and request are fully
                # authenticated/validated; in particular, no supplied IDs or
                # prose are copied into an error response.
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return
            expiry = threading.Timer(
                remaining,
                self._expire_connection,
                args=(connection,),
            )
            expiry.daemon = True
            expiry.start()
            try:
                try:
                    self._worker_local.in_handler = True
                    candidate = self._handler(request, peer)
                    if not isinstance(candidate, (ControlResponse, ErrorEnvelope)):
                        raise TypeError("handler returned a non-envelope value")
                    response: ResponseEnvelope = candidate
                    if not _response_matches(request, response):
                        raise ValueError("handler response identity mismatch")
                    encoded = response.to_json().encode("utf-8")
                except Exception:
                    response = _internal_error(request)
                    encoded = response.to_json().encode("utf-8")
                finally:
                    self._worker_local.in_handler = False

                try:
                    send_single_frame(
                        connection,
                        encoded,
                        deadline=deadline,
                        max_frame_bytes=self._max_frame_bytes,
                    )
                    connection.shutdown(socket.SHUT_WR)
                except (TransportError, OSError):
                    return
            finally:
                expiry.cancel()

    @staticmethod
    def _expire_connection(connection: socket.socket) -> None:
        """End client-visible work at the accepted-at absolute deadline."""

        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def wait(self, timeout_seconds: float | None = None) -> bool:
        """Wait for complete shutdown; return false if a finite wait expires."""

        if timeout_seconds is None:
            self._terminated.wait()
            return True
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or float(timeout_seconds) < 0.0
        ):
            raise TransportError("wait timeout must be a non-negative number")
        return self._terminated.wait(float(timeout_seconds))

    def shutdown(self, timeout_seconds: float | None = None) -> None:
        """Stop accepting and release ownership after all handlers finish.

        If a finite timeout expires while an injected handler is still
        running, this method raises and deliberately retains the socket lock.
        Calling ``shutdown`` again after the handler completes finishes cleanup;
        a second daemon can never overlap a still-running mutation owner.
        """

        if getattr(self._worker_local, "in_handler", False):
            raise TransportError(
                "daemon shutdown cannot run inside its request handler"
            )
        deadline = (
            None
            if timeout_seconds is None
            else deadline_after(timeout_seconds)
        )
        if deadline is None:
            self._shutdown_lock.acquire()
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0 or not self._shutdown_lock.acquire(
                timeout=remaining
            ):
                raise TransportTimeout(
                    "daemon shutdown coordination deadline expired"
                )
        try:
            self._shutdown_owned(deadline)
        finally:
            self._shutdown_lock.release()

    def _shutdown_owned(self, deadline: float | None) -> None:
        """Execute one serialized shutdown attempt."""

        with self._lifecycle_lock:
            if self._state == "stopped":
                return
            if self._state == "new":
                self._state = "stopped"
                self._terminated.set()
                return
            self._state = "stopping"
            self._stop.set()
            listener = self._listener
            self._listener = None
            if listener is not None:
                listener.close()

        thread = self._accept_thread
        if thread is not None and thread is not threading.current_thread():
            remaining = (
                None
                if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            thread.join(remaining)
            if thread.is_alive():
                raise TransportTimeout("daemon accept loop did not stop")

        while True:
            with self._future_lock:
                pending = set(self._futures)
            if not pending:
                break
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0.0:
                raise TransportTimeout(
                    "daemon handlers did not stop before the shutdown deadline"
                )
            _, not_done = wait(pending, timeout=remaining)
            if not_done:
                raise TransportTimeout(
                    "daemon handlers did not stop before the shutdown deadline"
                )

        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        self._unlink_owned_socket()
        self._lock.release()
        with self._lifecycle_lock:
            self._state = "stopped"
            self._terminated.set()

    def __enter__(self) -> "UnixControlDaemon":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()


__all__ = [
    "RequestHandler",
    "ResponseEnvelope",
    "UnixControlDaemon",
]
