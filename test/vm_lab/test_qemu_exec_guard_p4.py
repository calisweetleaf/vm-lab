"""Physical P4 proof for the journal-before-exec durability handoff.

The target is the real system ``sleep`` executable so the generic handoff can
be proven without launching or pretending to launch QEMU.  Tests use real
processes, inherited Unix sockets, ``PR_SET_PDEATHSIG``, fd-based ``execve``,
``/proc`` identity, parent death, and pidfd termination.  They establish only
the launch-gap primitive—not QEMU, QMP, VM, or guest truth.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
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

from somnus_vm.host.qemu_process import (  # noqa: E402
    ExecutableIdentity,
    ObservedProcessIdentity,
    QemuProcessError,
    open_pinned_executable,
    observe_process,
    require_executable_match,
    resolve_executable,
    terminate_owned_process,
)


GUARD = (
    SOURCE_ROOT / "somnus_vm" / "host" / "qemu_exec_guard.py"
).resolve(strict=True)
PYTHON = resolve_executable(
    os.fspath(Path(sys.executable).resolve(strict=True))
)
SLEEP = resolve_executable("sleep")
READY = b"SOMNUS_QEMU_EXEC_GUARD_READY_V1\n"
RELEASE = b"SOMNUS_QEMU_EXEC_GUARD_RELEASE_V1\n"


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
            f"guard control payload disagreed: {bytes(payload)!r}"
        )


def _cleanup_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
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


def _cleanup_observed(process: ObservedProcessIdentity) -> None:
    try:
        terminate_owned_process(process)
    except QemuProcessError:
        pass


class QemuExecGuardP4Tests(unittest.TestCase):
    def _spawn_guard(
        self,
        *,
        target_argv: tuple[str, ...],
    ) -> tuple[
        subprocess.Popen[bytes],
        socket.socket,
        ObservedProcessIdentity,
        ExecutableIdentity,
    ]:
        parent_channel, child_channel = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        runtime_home = Path(
            tempfile.mkdtemp(prefix="vm-lab-p4-guard-home-")
        ).resolve()
        runtime_home.chmod(0o700)
        self.addCleanup(shutil.rmtree, runtime_home, True)
        target_identity, target_fd = open_pinned_executable("sleep")
        guard_argv = (
            os.fspath(PYTHON),
            os.fspath(GUARD),
            str(os.getpid()),
            str(child_channel.fileno()),
            os.fspath(runtime_home),
            str(target_fd),
            *target_argv,
        )
        try:
            process = subprocess.Popen(
                guard_argv,
                executable=os.fspath(PYTHON),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                pass_fds=(child_channel.fileno(), target_fd),
            )
        finally:
            child_channel.close()
            os.close(target_fd)
        self.addCleanup(_cleanup_process, process)
        self.addCleanup(parent_channel.close)
        _receive_exact(parent_channel, READY)
        guard_identity = observe_process(
            process.pid,
            expected_executable=PYTHON,
            expected_argv=guard_argv,
        )
        return process, parent_channel, guard_identity, target_identity

    def _wait_for_exec(
        self,
        process: subprocess.Popen[bytes],
        target_argv: tuple[str, ...],
        target_identity: ExecutableIdentity,
    ) -> ObservedProcessIdentity:
        deadline = time.monotonic() + 5.0
        latest: BaseException | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = (
                    process.stderr.read().decode("utf-8", errors="replace")
                    if process.stderr is not None
                    else ""
                )
                self.fail(
                    "guard exited before target exec"
                    f" (returncode={process.returncode}, stderr={stderr!r})"
                )
            try:
                observed = observe_process(
                    process.pid,
                    expected_executable=SLEEP,
                    expected_argv=target_argv,
                )
                require_executable_match(target_identity, observed)
                return observed
            except QemuProcessError as exc:
                latest = exc
                time.sleep(0.01)
        self.fail(f"guard did not exec the pinned target: {latest}")

    def test_release_preserves_pid_start_time_argv_and_pinned_inode(self) -> None:
        target_argv = ("qemu-system-p4-proof-token", "30")
        process, channel, guard_identity, target_identity = self._spawn_guard(
            target_argv=target_argv
        )
        channel.sendall(RELEASE)
        channel.shutdown(socket.SHUT_WR)
        target = self._wait_for_exec(
            process,
            target_argv,
            target_identity,
        )
        self.assertEqual(target.pid, guard_identity.pid)
        self.assertEqual(
            target.start_time_ticks,
            guard_identity.start_time_ticks,
        )
        self.assertEqual(target.process_group_id, process.pid)
        self.assertEqual(target.session_id, process.pid)
        inherited_targets: list[str] = []
        for descriptor_path in Path(f"/proc/{process.pid}/fd").iterdir():
            try:
                inherited_targets.append(os.readlink(descriptor_path))
            except FileNotFoundError:
                continue
        self.assertNotIn(os.fspath(SLEEP), inherited_targets)
        evidence = terminate_owned_process(target)
        self.assertTrue(evidence.pidfd_used)
        self.assertTrue(evidence.exited)

    def test_release_replaces_the_inherited_environment_exactly(self) -> None:
        runtime_home = Path(
            tempfile.mkdtemp(prefix="vm-lab-p4-guard-environment-")
        ).resolve()
        runtime_home.chmod(0o700)
        self.addCleanup(shutil.rmtree, runtime_home, True)
        target_identity, target_fd = open_pinned_executable(os.fspath(PYTHON))
        self.assertEqual(target_identity.path, PYTHON)
        parent_channel, child_channel = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        self.addCleanup(parent_channel.close)
        target_argv = (
            "qemu-environment-proof",
            "-I",
            "-c",
            (
                "import json,os;"
                "prior=os.umask(0o077);"
                "print(json.dumps("
                "{'environment':dict(os.environ),'umask':prior},"
                "sort_keys=True,separators=(',',':')))"
            ),
        )
        guard_argv = (
            os.fspath(PYTHON),
            os.fspath(GUARD),
            str(os.getpid()),
            str(child_channel.fileno()),
            os.fspath(runtime_home),
            str(target_fd),
            *target_argv,
        )
        inherited = dict(os.environ)
        inherited.update(
            {
                "LD_PRELOAD": "",
                "PYTHONPATH": (
                    f"{SOURCE_ROOT}{os.pathsep}/tmp/hostile-python-path"
                ),
                "QEMU_AUDIO_DRV": "hostile-audio-backend",
                "SOMNUS_SECRET_PROBE": "never-propagate",
            }
        )
        try:
            process = subprocess.Popen(
                guard_argv,
                executable=os.fspath(PYTHON),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=inherited,
                start_new_session=True,
                close_fds=True,
                pass_fds=(child_channel.fileno(), target_fd),
            )
        finally:
            child_channel.close()
            os.close(target_fd)
        self.addCleanup(_cleanup_process, process)
        _receive_exact(parent_channel, READY)
        parent_channel.sendall(RELEASE)
        parent_channel.shutdown(socket.SHUT_WR)
        stdout, stderr = process.communicate(timeout=5.0)
        self.assertEqual(process.returncode, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["umask"], 0o077)
        self.assertEqual(
            payload["environment"],
            {
                "HOME": os.fspath(runtime_home),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "TMPDIR": os.fspath(runtime_home),
                "TZ": "UTC",
                "XDG_RUNTIME_DIR": os.fspath(runtime_home),
            },
        )

    def test_malformed_release_exits_without_executing_target(self) -> None:
        process, channel, guard_identity, _ = self._spawn_guard(
            target_argv=("qemu-system-p4-invalid-release", "30")
        )
        channel.sendall(b"INVALID\n")
        channel.shutdown(socket.SHUT_WR)
        returncode = process.wait(timeout=5.0)
        stderr = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.stderr is not None
            else ""
        )
        self.assertEqual(returncode, 125)
        self.assertIn("RuntimeError", stderr)
        self.assertFalse(Path(f"/proc/{guard_identity.pid}").exists())

    def test_parent_death_before_release_kills_unjournaled_guard(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-guard-parent-"
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
            child_pid = int(state["pid"])
            deadline = time.monotonic() + 5.0
            while (
                Path(f"/proc/{child_pid}").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertFalse(
                Path(f"/proc/{child_pid}").exists(),
                "guard survived owner death before durable release",
            )

    def test_parent_death_after_release_leaves_exact_adoptable_target(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-guard-adopt-"
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
            target_argv = tuple(state["target_argv"])
            observed = observe_process(
                int(state["pid"]),
                expected_executable=SLEEP,
                expected_argv=target_argv,
            )
            self.addCleanup(_cleanup_observed, observed)
            self.assertEqual(
                observed.start_time_ticks,
                int(state["start_time_ticks"]),
            )
            evidence = terminate_owned_process(observed)
            self.assertTrue(evidence.pidfd_used)
            self.assertTrue(evidence.exited)

    def _launch_coordinator(
        self,
        *,
        mode: str,
        state_path: Path,
    ) -> subprocess.Popen[bytes]:
        helper_path = state_path.with_name("coordinator.py")
        helper_path.write_text(
            "\n".join(
                (
                    "import json, os, socket, subprocess, sys, time",
                    "from pathlib import Path",
                    "from somnus_vm.host.qemu_process import "
                    "open_pinned_executable, observe_process",
                    "mode, state_text, python_text, guard_text = sys.argv[1:]",
                    "state = Path(state_text)",
                    "target, target_fd = open_pinned_executable('sleep')",
                    "parent, child = socket.socketpair("
                    "socket.AF_UNIX, socket.SOCK_STREAM)",
                    "target_argv = ('qemu-system-p4-adoption-token', '30')",
                    "guard_argv = (python_text, guard_text, str(os.getpid()), "
                    "str(child.fileno()), str(state.parent), "
                    "str(target_fd), *target_argv)",
                    "process = subprocess.Popen("
                    "guard_argv, executable=python_text, "
                    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                    "stderr=subprocess.DEVNULL, start_new_session=True, "
                    "close_fds=True, pass_fds=(child.fileno(), target_fd))",
                    "child.close(); os.close(target_fd)",
                    "parent.settimeout(5.0)",
                    "ready = bytearray()",
                    f"while len(ready) < {len(READY)}:",
                    f"    block = parent.recv({len(READY)} - len(ready))",
                    "    if not block: raise RuntimeError('guard closed early')",
                    "    ready.extend(block)",
                    f"assert bytes(ready) == {READY!r}",
                    "guard = observe_process(process.pid)",
                    "if mode == 'after':",
                    f"    parent.sendall({RELEASE!r})",
                    "    parent.shutdown(socket.SHUT_WR)",
                    "    deadline = time.monotonic() + 5.0",
                    "    while True:",
                    "        try:",
                    "            target_process = observe_process("
                    "process.pid, expected_executable=target.path, "
                    "expected_argv=target_argv)",
                    "            break",
                    "        except Exception:",
                    "            if time.monotonic() >= deadline: raise",
                    "            time.sleep(0.01)",
                    "    start_ticks = target_process.start_time_ticks",
                    "else:",
                    "    start_ticks = guard.start_time_ticks",
                    "payload = {'pid': process.pid, "
                    "'start_time_ticks': start_ticks, "
                    "'target_argv': list(target_argv)}",
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
