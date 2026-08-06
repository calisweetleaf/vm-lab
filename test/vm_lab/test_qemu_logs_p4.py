"""Physical P4 proof for private, crash-surviving QEMU log containment.

The suite launches only Python helpers.  It uses real Unix sockets, inherited
pipe descriptors, Linux parent-death signaling, concurrent subprocess writers,
and actual private files.  It establishes the logging boundary only: it does
not launch QEMU or promote a public lifecycle action.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_vm.host.qemu_log_guard import (  # noqa: E402
    READY,
    RELEASE,
    RELEASED,
)
from somnus_vm.host.qemu_logs import (  # noqa: E402
    MAX_LINE_BYTES,
    MIN_LOG_BYTES,
    QemuLogError,
    RedactedRotatingLog,
)


GUARD = (
    SOURCE_ROOT / "somnus_vm" / "host" / "qemu_log_guard.py"
).resolve(strict=True)
PYTHON = Path(sys.executable).resolve(strict=True)
OVERSIZED = b"[somnus redacted one oversized unterminated log line]\n"


def _receive_exact(
    channel: socket.socket,
    expected: bytes,
    timeout_seconds: float = 5.0,
) -> None:
    channel.settimeout(timeout_seconds)
    payload = bytearray()
    while len(payload) < len(expected):
        block = channel.recv(len(expected) - len(payload))
        if not block:
            break
        payload.extend(block)
    if bytes(payload) != expected:
        raise AssertionError(
            f"log guard control payload disagreed: {bytes(payload)!r}"
        )


def _close_fd(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _cleanup_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5.0)
            except (ChildProcessError, subprocess.TimeoutExpired):
                pass
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


class QemuLogsP4Tests(unittest.TestCase):
    def _spawn_guard(
        self,
        root: Path,
        *,
        max_bytes: int = MIN_LOG_BYTES,
        backup_count: int = 2,
    ) -> tuple[
        subprocess.Popen[bytes],
        socket.socket,
        int,
        int,
        Path,
        Path,
    ]:
        log_root = root / "logs"
        serial_path = log_root / "serial.log"
        qemu_path = log_root / "qemu.log"
        parent_channel, child_channel = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        serial_read, serial_write = os.pipe()
        qemu_read, qemu_write = os.pipe()
        argv = (
            os.fspath(PYTHON),
            os.fspath(GUARD),
            str(os.getpid()),
            str(child_channel.fileno()),
            str(serial_read),
            str(qemu_read),
            os.fspath(log_root),
            os.fspath(serial_path),
            os.fspath(qemu_path),
            str(max_bytes),
            str(backup_count),
        )
        try:
            process = subprocess.Popen(
                argv,
                executable=os.fspath(PYTHON),
                cwd=PROJECT_ROOT,
                env={
                    "PYTHONPATH": os.fspath(SOURCE_ROOT),
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                pass_fds=(
                    child_channel.fileno(),
                    serial_read,
                    qemu_read,
                ),
            )
        finally:
            child_channel.close()
            _close_fd(serial_read)
            _close_fd(qemu_read)
        self.addCleanup(_cleanup_process, process)
        self.addCleanup(parent_channel.close)
        self.addCleanup(_close_fd, serial_write)
        self.addCleanup(_close_fd, qemu_write)
        _receive_exact(parent_channel, READY)
        return (
            process,
            parent_channel,
            serial_write,
            qemu_write,
            serial_path,
            qemu_path,
        )

    @staticmethod
    def _release(channel: socket.socket) -> None:
        channel.sendall(RELEASE)
        _receive_exact(channel, RELEASED)
        channel.shutdown(socket.SHUT_WR)

    def test_split_chunk_and_oversized_lines_are_redacted_before_disk(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-redaction-"
        ) as raw:
            root = Path(raw).resolve()
            root.chmod(0o700)
            path = root / "serial.log"
            explicit = b"split-explicit-secret"
            with RedactedRotatingLog(
                path,
                max_bytes=MIN_LOG_BYTES,
                backup_count=2,
                explicit_secrets=(explicit, b"abcd"),
            ) as log:
                log.feed(b'{"password":"value with ')
                log.feed(b'spaces","access_token":"chunked-')
                self.assertEqual(path.read_bytes(), b"")
                log.feed(
                    b'secret"} Authorization: Bearer bearer-secret '
                    b"https://operator:uri-secret@example.invalid/ "
                    + b"x://"
                    + (b"u" * 300)
                    + b":long-uri-secret@example.invalid/ "
                    b"split-explicit-"
                )
                log.feed(b"secret\n")
                first = path.read_bytes()
                for secret in (
                    b"value with spaces",
                    b"chunked-secret",
                    b"bearer-secret",
                    b"uri-secret",
                    b"long-uri-secret",
                    explicit,
                ):
                    self.assertNotIn(secret, first)
                self.assertGreaterEqual(first.count(b"<redacted>"), 6)

                log.feed(b"A" * (MAX_LINE_BYTES // 2))
                self.assertEqual(path.read_bytes(), first)
                log.feed(
                    b"B" * (MAX_LINE_BYTES // 2)
                    + b"token=oversized-secret"
                )
                log.feed(b"discarded-tail\nnormal token=final-secret,\n")
                # Redaction expansion must degrade to the same safe marker,
                # never fail the pump or persist part of the raw record.
                log.feed((b"abcd" * 16_000) + b"\n")

            payload = path.read_bytes()
            self.assertNotIn(b"oversized-secret", payload)
            self.assertNotIn(b"final-secret", payload)
            self.assertNotIn(b"A" * 128, payload)
            self.assertNotIn(b"abcd", payload)
            self.assertEqual(payload.count(OVERSIZED), 2)
            self.assertIn(b"normal token=<redacted>,", payload)

    def test_private_root_and_retention_entries_refuse_links_and_bad_modes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-ownership-"
        ) as raw:
            root = Path(raw).resolve()
            root.chmod(0o700)

            bad_mode = root / "bad-mode"
            bad_mode.mkdir(mode=0o700)
            bad_mode.chmod(0o755)
            with self.assertRaises(QemuLogError):
                RedactedRotatingLog(bad_mode / "serial.log")

            target_root = root / "target-root"
            target_root.mkdir(mode=0o700)
            linked_root = root / "linked-root"
            linked_root.symlink_to(target_root, target_is_directory=True)
            with self.assertRaises(QemuLogError):
                RedactedRotatingLog(linked_root / "serial.log")

            symlink_root = root / "symlink-entry"
            symlink_root.mkdir(mode=0o700)
            external = root / "external.log"
            external.write_bytes(b"do-not-touch")
            external.chmod(0o600)
            (symlink_root / "serial.log").symlink_to(external)
            with self.assertRaises(QemuLogError):
                RedactedRotatingLog(symlink_root / "serial.log")

            hardlink_root = root / "hardlink-entry"
            hardlink_root.mkdir(mode=0o700)
            os.link(external, hardlink_root / "serial.log")
            with self.assertRaises(QemuLogError):
                RedactedRotatingLog(hardlink_root / "serial.log")

            loose_root = root / "loose-entry"
            loose_root.mkdir(mode=0o700)
            loose = loose_root / "serial.log"
            loose.write_bytes(b"unsafe")
            loose.chmod(0o644)
            with self.assertRaises(QemuLogError):
                RedactedRotatingLog(loose)

            valid_root = root / "valid"
            path = valid_root / "serial.log"
            with RedactedRotatingLog(path) as log:
                log.feed(b"safe\n")
                self.assertEqual(
                    stat.S_IMODE(valid_root.stat().st_mode),
                    0o700,
                )
                details = path.stat(follow_symlinks=False)
                self.assertTrue(stat.S_ISREG(details.st_mode))
                self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
                self.assertEqual(details.st_uid, os.geteuid())
                self.assertEqual(details.st_nlink, 1)
                with self.assertRaises(QemuLogError):
                    RedactedRotatingLog(path)

    def test_rotation_and_retention_remain_strictly_bounded(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-rotation-"
        ) as raw:
            root = Path(raw).resolve()
            root.chmod(0o700)
            path = root / "qemu.log"
            secret = b"rotation-secret"
            with RedactedRotatingLog(
                path,
                max_bytes=MIN_LOG_BYTES,
                backup_count=4,
                explicit_secrets=(secret,),
            ) as log:
                for index in range(48):
                    log.feed(
                        f"record-{index:04d} ".encode("ascii")
                        + secret
                        + b" "
                        + (bytes((65 + index % 26,)) * 8_000)
                        + b"\n"
                    )
            # A later, lower retention policy must retire generations that
            # were valid under the former bound rather than leave hidden
            # unbounded history behind.
            with RedactedRotatingLog(
                path,
                max_bytes=MIN_LOG_BYTES,
                backup_count=2,
                explicit_secrets=(secret,),
            ) as log:
                log.feed(b"record-0048 rotation-secret newest\n")

            entries = tuple(
                sorted(
                    (
                        item
                        for item in root.iterdir()
                        if item.name == path.name
                        or item.name.startswith(f"{path.name}.")
                    ),
                    key=lambda item: item.name,
                )
            )
            self.assertEqual(
                tuple(item.name for item in entries),
                ("qemu.log", "qemu.log.1", "qemu.log.2"),
            )
            self.assertLessEqual(
                sum(item.stat().st_size for item in entries),
                MIN_LOG_BYTES * 3,
            )
            retained = b"".join(item.read_bytes() for item in entries)
            self.assertNotIn(secret, retained)
            self.assertNotIn(b"record-0000", retained)
            self.assertIn(b"record-0048", path.read_bytes())
            for item in entries:
                details = item.stat(follow_symlinks=False)
                self.assertLessEqual(details.st_size, MIN_LOG_BYTES)
                self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
                self.assertEqual(details.st_nlink, 1)

    def test_guard_acknowledges_durable_files_then_drains_both_pipes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-drain-"
        ) as raw:
            root = Path(raw).resolve()
            root.chmod(0o700)
            (
                guard,
                channel,
                serial_write,
                qemu_write,
                serial_path,
                qemu_path,
            ) = self._spawn_guard(root)

            # READY is sent only after both empty owner-private files exist.
            for path in (serial_path, qemu_path):
                details = path.stat(follow_symlinks=False)
                self.assertEqual(details.st_size, 0)
                self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
                self.assertEqual(details.st_nlink, 1)
            locks = tuple(serial_path.parent.glob(".somnus-log-*.lock"))
            self.assertEqual(len(locks), 2)
            for lock in locks:
                details = lock.stat(follow_symlinks=False)
                self.assertTrue(stat.S_ISREG(details.st_mode))
                self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
                self.assertEqual(details.st_nlink, 1)
            self._release(channel)

            serial_secret = "serial-secret-value"
            qemu_secret = "qemu-secret-value"
            serial_script = (
                "import os\n"
                f"secret={serial_secret!r}.encode()\n"
                "pad=b's'*1000\n"
                "for i in range(1800):\n"
                " os.write(1,b'password='+secret+b','+pad+b'\\n')\n"
            )
            qemu_script = (
                "import os\n"
                f"secret={qemu_secret!r}.encode()\n"
                "pad=b'q'*1000\n"
                "for i in range(1800):\n"
                " os.write(1,b'Authorization: Bearer '+secret+b' '+pad+b'\\n')\n"
            )
            serial_writer = subprocess.Popen(
                (os.fspath(PYTHON), "-c", serial_script),
                executable=os.fspath(PYTHON),
                stdin=subprocess.DEVNULL,
                stdout=serial_write,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            qemu_writer = subprocess.Popen(
                (os.fspath(PYTHON), "-c", qemu_script),
                executable=os.fspath(PYTHON),
                stdin=subprocess.DEVNULL,
                stdout=qemu_write,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self.addCleanup(_cleanup_process, serial_writer)
            self.addCleanup(_cleanup_process, qemu_writer)
            _close_fd(serial_write)
            _close_fd(qemu_write)
            self.assertEqual(serial_writer.wait(timeout=15.0), 0)
            self.assertEqual(qemu_writer.wait(timeout=15.0), 0)
            returncode = guard.wait(timeout=15.0)
            stderr = (
                guard.stderr.read().decode("utf-8", errors="replace")
                if guard.stderr is not None
                else ""
            )
            self.assertEqual(returncode, 0, stderr)
            for path, secret in (
                (serial_path, serial_secret.encode()),
                (qemu_path, qemu_secret.encode()),
            ):
                entries = tuple(
                    item
                    for item in path.parent.iterdir()
                    if item.name == path.name
                    or item.name.startswith(f"{path.name}.")
                )
                self.assertLessEqual(len(entries), 3)
                self.assertLessEqual(
                    sum(item.stat().st_size for item in entries),
                    MIN_LOG_BYTES * 3,
                )
                retained = b"".join(item.read_bytes() for item in entries)
                self.assertNotIn(secret, retained)
                self.assertIn(b"<redacted>", retained)

    def test_guard_waits_for_both_eofs_and_rejects_ambiguous_release(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-eof-"
        ) as raw:
            root = Path(raw).resolve()
            root.chmod(0o700)
            (
                guard,
                channel,
                serial_write,
                qemu_write,
                serial_path,
                qemu_path,
            ) = self._spawn_guard(root)
            self._release(channel)
            os.write(serial_write, b"serial-eof\n")
            _close_fd(serial_write)
            time.sleep(0.2)
            self.assertIsNone(
                guard.poll(),
                "guardian exited while the QEMU stderr writer remained open",
            )
            os.write(qemu_write, b"qemu-eof\n")
            _close_fd(qemu_write)
            self.assertEqual(guard.wait(timeout=5.0), 0)
            self.assertIn(b"serial-eof", serial_path.read_bytes())
            self.assertIn(b"qemu-eof", qemu_path.read_bytes())

        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-release-"
        ) as raw:
            root = Path(raw).resolve()
            root.chmod(0o700)
            (
                guard,
                channel,
                serial_write,
                qemu_write,
                serial_path,
                qemu_path,
            ) = self._spawn_guard(root)
            os.write(serial_write, b"password=must-never-persist,\n")
            channel.sendall(b"SOMNUS_QEMU_LOG_GUARD_RELEASE_V1-extra\n")
            channel.shutdown(socket.SHUT_WR)
            _close_fd(serial_write)
            _close_fd(qemu_write)
            returncode = guard.wait(timeout=5.0)
            stderr = (
                guard.stderr.read().decode("utf-8", errors="replace")
                if guard.stderr is not None
                else ""
            )
            self.assertEqual(returncode, 124, stderr)
            self.assertEqual(serial_path.read_bytes(), b"")
            self.assertEqual(qemu_path.read_bytes(), b"")

    def test_parent_death_before_release_kills_guard(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-parent-before-"
        ) as raw:
            root = Path(raw).resolve()
            state_path = root / "state.json"
            coordinator = self._launch_coordinator(
                mode="before",
                state_path=state_path,
            )
            stdout, stderr = coordinator.communicate(timeout=10.0)
            self.assertEqual(coordinator.returncode, 0, (stdout, stderr))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            guard_pid = int(state["guard_pid"])
            self._wait_process_absent(guard_pid)
            self.assertEqual(
                Path(state["serial_path"]).read_bytes(),
                b"",
            )
            self.assertEqual(
                Path(state["qemu_path"]).read_bytes(),
                b"",
            )

    def test_parent_death_after_release_keeps_guardian_draining(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-log-parent-after-"
        ) as raw:
            root = Path(raw).resolve()
            state_path = root / "state.json"
            coordinator = self._launch_coordinator(
                mode="after",
                state_path=state_path,
            )
            stdout, stderr = coordinator.communicate(timeout=10.0)
            self.assertEqual(coordinator.returncode, 0, (stdout, stderr))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self._wait_process_absent(int(state["writer_pid"]), timeout=10.0)
            self._wait_process_absent(int(state["guard_pid"]), timeout=10.0)
            serial = Path(state["serial_path"]).read_bytes()
            qemu = Path(state["qemu_path"]).read_bytes()
            self.assertIn(b"after-release-serial-final", serial)
            self.assertIn(b"after-release-qemu-final", qemu)
            self.assertNotIn(b"post-parent-secret", serial + qemu)
            self.assertIn(b"<redacted>", serial + qemu)

    @staticmethod
    def _wait_process_absent(pid: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if Path(f"/proc/{pid}").exists():
            raise AssertionError(f"process {pid} remained after its proof deadline")

    def _launch_coordinator(
        self,
        *,
        mode: str,
        state_path: Path,
    ) -> subprocess.Popen[bytes]:
        helper_path = state_path.with_name("log_coordinator.py")
        helper_path.write_text(
            "\n".join(
                (
                    "import json, os, socket, subprocess, sys, time",
                    "from pathlib import Path",
                    "mode, state_text, python_text, guard_text = sys.argv[1:]",
                    "state = Path(state_text)",
                    "log_root = state.parent / 'logs'",
                    "serial_path = log_root / 'serial.log'",
                    "qemu_path = log_root / 'qemu.log'",
                    "parent, child = socket.socketpair("
                    "socket.AF_UNIX, socket.SOCK_STREAM)",
                    "serial_read, serial_write = os.pipe()",
                    "qemu_read, qemu_write = os.pipe()",
                    "argv = (python_text, guard_text, str(os.getpid()), "
                    "str(child.fileno()), str(serial_read), str(qemu_read), "
                    "str(log_root), str(serial_path), str(qemu_path), "
                    f"{str(MIN_LOG_BYTES)!r}, '2')",
                    "guard = subprocess.Popen("
                    "argv, executable=python_text, stdin=subprocess.DEVNULL, "
                    "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
                    "start_new_session=True, close_fds=True, "
                    "pass_fds=(child.fileno(), serial_read, qemu_read))",
                    "child.close(); os.close(serial_read); os.close(qemu_read)",
                    "parent.settimeout(5.0)",
                    "ready = bytearray()",
                    f"while len(ready) < {len(READY)}:",
                    f"    block = parent.recv({len(READY)} - len(ready))",
                    "    if not block: raise RuntimeError('guard closed early')",
                    "    ready.extend(block)",
                    f"assert bytes(ready) == {READY!r}",
                    "writer_pid = 0",
                    "if mode == 'after':",
                    f"    parent.sendall({RELEASE!r})",
                    "    released = bytearray()",
                    f"    while len(released) < {len(RELEASED)}:",
                    f"        block = parent.recv({len(RELEASED)} - len(released))",
                    "        if not block: raise RuntimeError('release ack missing')",
                    "        released.extend(block)",
                    f"    assert bytes(released) == {RELEASED!r}",
                    "    writer_code = \"import os,time\\n\" "
                    "\"time.sleep(0.5)\\n\" "
                    "\"secret=b'post-parent-secret'\\n\" "
                    "\"for i in range(200):\\n\" "
                    "\" os.write(1,b'password='+secret+b', serial\\\\n')\\n\" "
                    "\" os.write(2,b'Authorization: Bearer '+secret"
                    "+b' qemu\\\\n')\\n\" "
                    "\"os.write(1,b'after-release-serial-final\\\\n')\\n\" "
                    "\"os.write(2,b'after-release-qemu-final\\\\n')\\n\"",
                    "    writer = subprocess.Popen("
                    "(python_text, '-c', writer_code), executable=python_text, "
                    "stdin=subprocess.DEVNULL, stdout=serial_write, "
                    "stderr=qemu_write, start_new_session=True, close_fds=True)",
                    "    writer_pid = writer.pid",
                    "payload = {'guard_pid': guard.pid, "
                    "'writer_pid': writer_pid, "
                    "'serial_path': str(serial_path), "
                    "'qemu_path': str(qemu_path)}",
                    "temporary = state.with_suffix('.tmp')",
                    "temporary.write_text(json.dumps(payload), encoding='utf-8')",
                    "temporary.chmod(0o600)",
                    "temporary.replace(state)",
                    "os._exit(0)",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        environment = {
            "PYTHONPATH": os.fspath(SOURCE_ROOT),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        return subprocess.Popen(
            (
                os.fspath(PYTHON),
                os.fspath(helper_path),
                mode,
                os.fspath(state_path),
                os.fspath(PYTHON),
                os.fspath(GUARD),
            ),
            executable=os.fspath(PYTHON),
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
