"""Physical P4 proof for exact Linux process ownership primitives.

These tests use real ``/proc`` observations, real private pidfiles, real
process groups, and real POSIX signals.  They never launch QEMU and therefore
do not claim VM identity, QMP readiness, guest readiness, or lifecycle
promotion.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from somnus_vm.host.qemu_process import (  # noqa: E402
    ObservedProcessIdentity,
    PidfileIdentity,
    ProcessIdentityMismatchError,
    QemuProcessError,
    command_sha256,
    inspect_executable,
    normalize_argv,
    observe_pidfile,
    observe_process,
    read_pidfile,
    require_executable_match,
    resolve_executable,
    revalidate_process,
    terminate_owned_process,
    verify_pidfile,
)


SLEEP = resolve_executable("sleep")
PYTHON = resolve_executable(
    os.fspath(Path(sys.executable).resolve(strict=True))
)


def _cleanup_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process_group = os.getpgid(process.pid)
        if process_group == process.pid:
            os.killpg(process_group, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5.0)
    except (ChildProcessError, subprocess.TimeoutExpired):
        pass


class QemuProcessP4Tests(unittest.TestCase):
    def _spawn_sleep(
        self,
        *,
        isolated: bool = True,
        seconds: str = "30",
    ) -> tuple[subprocess.Popen[bytes], tuple[str, ...]]:
        argv = normalize_argv(SLEEP, (seconds,))
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=isolated,
            close_fds=True,
        )
        self.addCleanup(_cleanup_process, process)
        return process, argv

    def test_executable_selection_and_command_identity_are_canonical(self) -> None:
        self.assertEqual(resolve_executable("sleep"), SLEEP)
        self.assertEqual(resolve_executable(os.fspath(SLEEP)), SLEEP)
        executable_identity = inspect_executable("sleep")
        self.assertEqual(executable_identity.path, SLEEP)
        self.assertEqual(len(executable_identity.sha256), 64)
        self.assertEqual(
            executable_identity.inode,
            SLEEP.stat(follow_symlinks=False).st_ino,
        )
        argv = normalize_argv(SLEEP, ("30",))
        self.assertEqual(argv, (os.fspath(SLEEP), "30"))
        self.assertEqual(command_sha256(argv), command_sha256(tuple(argv)))
        self.assertEqual(len(command_sha256(argv)), 64)
        self.assertNotEqual(
            command_sha256(argv),
            command_sha256((os.fspath(SLEEP), "31")),
        )

        with tempfile.TemporaryDirectory(prefix="vm-lab-p4-exec-") as raw:
            alias = Path(raw).resolve() / "sleep-link"
            alias.symlink_to(SLEEP)
            with self.assertRaisesRegex(QemuProcessError, "symlink"):
                resolve_executable(os.fspath(alias))

        for invalid in (
            "./sleep",
            "../sleep",
            "missing-vm-lab-p4-executable",
            "bad\nname",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(QemuProcessError):
                    resolve_executable(invalid)

        for invalid_argv in (
            (),
            (os.fspath(SLEEP), ""),
            (os.fspath(SLEEP), "line\nbreak"),
            (os.fspath(SLEEP), "delete\x7f"),
        ):
            with self.subTest(invalid_argv=invalid_argv):
                with self.assertRaises(QemuProcessError):
                    command_sha256(invalid_argv)
        with self.assertRaises(QemuProcessError):
            normalize_argv(SLEEP, ("line\nbreak",))

    def test_proc_observation_binds_every_stable_process_fact(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        self.assertEqual(observed.pid, process.pid)
        self.assertEqual(observed.argv, argv)
        self.assertEqual(observed.process_group_id, process.pid)
        self.assertEqual(observed.session_id, process.pid)
        self.assertEqual(
            observed.command_sha256,
            command_sha256(argv),
        )
        self.assertEqual(observed.executable, SLEEP)
        self.assertEqual(
            observed.to_protocol_identity().pid,
            process.pid,
        )
        current = revalidate_process(observed)
        require_executable_match(inspect_executable("sleep"), current)
        self.assertEqual(
            current.start_time_ticks,
            observed.start_time_ticks,
        )
        payload = observed.to_dict()
        self.assertEqual(
            ObservedProcessIdentity.from_dict(payload),
            observed,
        )
        for hostile in (
            {**payload, "unexpected": "field"},
            {**payload, "pid": True},
            {**payload, "argv": tuple(payload["argv"])},
            {
                **payload,
                "observed_at": str(payload["observed_at"]).replace(
                    "+00:00",
                    "Z",
                ),
            },
            {
                **payload,
                "command_sha256": str(payload["command_sha256"]).upper(),
            },
        ):
            with self.subTest(hostile=hostile):
                with self.assertRaises(QemuProcessError):
                    ObservedProcessIdentity.from_dict(hostile)
        with self.assertRaisesRegex(
            ProcessIdentityMismatchError,
            "argv",
        ):
            observe_process(
                process.pid,
                expected_executable=SLEEP,
                expected_argv=(os.fspath(SLEEP), "29"),
            )
        with self.assertRaisesRegex(
            ProcessIdentityMismatchError,
            "executable",
        ):
            observe_process(
                process.pid,
                expected_executable=PYTHON,
            )

    def test_daemon_uid_owned_executable_is_never_a_trust_anchor(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".vm-lab-p4-executable-",
            dir=Path.home(),
        ) as raw:
            root = Path(raw).resolve()
            executable = root / "owned-sleep"
            shutil.copy2(SLEEP, executable)
            executable.chmod(0o555)
            with self.assertRaisesRegex(
                QemuProcessError,
                "ownership",
            ):
                resolve_executable(os.fspath(executable))

    def test_private_pidfile_must_name_the_exact_observed_process(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        with tempfile.TemporaryDirectory(prefix="vm-lab-p4-pidfile-") as raw:
            root = Path(raw).resolve()
            pidfile = root / "qemu.pid"
            pidfile.write_text(f"{process.pid}\n", encoding="ascii")
            pidfile.chmod(0o600)
            self.assertEqual(read_pidfile(pidfile), process.pid)
            self.assertEqual(verify_pidfile(observed, pidfile), process.pid)

            pidfile.write_text(f"{process.pid + 1}\n", encoding="ascii")
            with self.assertRaisesRegex(
                ProcessIdentityMismatchError,
                "does not match",
            ):
                verify_pidfile(observed, pidfile)

            pidfile.write_text(f"{process.pid}\n", encoding="ascii")
            pidfile.chmod(0o640)
            with self.assertRaisesRegex(QemuProcessError, "unsafe"):
                read_pidfile(pidfile)

            pidfile.chmod(0o600)
            alias = root / "qemu.pid.link"
            alias.symlink_to(pidfile)
            with self.assertRaises(QemuProcessError):
                read_pidfile(alias)

            hardlink = root / "qemu.pid.hardlink"
            os.link(pidfile, hardlink)
            with self.assertRaisesRegex(QemuProcessError, "unsafe"):
                read_pidfile(pidfile)

            hardlink.unlink()
            with self.assertRaisesRegex(QemuProcessError, "unsafe"):
                read_pidfile(pidfile, expected_uid=os.geteuid() + 1)

            fifo = root / "qemu.fifo"
            os.mkfifo(fifo, 0o600)
            started = time.monotonic()
            with self.assertRaisesRegex(QemuProcessError, "unsafe"):
                read_pidfile(fifo)
            self.assertLess(time.monotonic() - started, 0.5)

            pidfile.write_text(f"{process.pid}\n", encoding="ascii")
            pidfile.chmod(0o600)
            root.chmod(0o777)
            try:
                with self.assertRaisesRegex(
                    QemuProcessError,
                    "parent ownership",
                ):
                    read_pidfile(pidfile)
            finally:
                root.chmod(0o700)

    def test_observed_pidfile_binds_exact_private_file_metadata(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        with tempfile.TemporaryDirectory(prefix="vm-lab-p4-pidfile-id-") as raw:
            root = Path(raw).resolve()
            pidfile = root / "qemu.pid"
            pidfile.write_text(f"{process.pid}\n", encoding="ascii")
            pidfile.chmod(0o600)

            identity = observe_pidfile(observed, pidfile)
            details = pidfile.lstat()
            expected = PidfileIdentity(
                path=pidfile,
                pid=process.pid,
                device_id=details.st_dev,
                inode=details.st_ino,
                owner_uid=details.st_uid,
                mode=stat.S_IMODE(details.st_mode),
                link_count=details.st_nlink,
                size_bytes=details.st_size,
                mtime_ns=details.st_mtime_ns,
                ctime_ns=details.st_ctime_ns,
            )
            self.assertEqual(identity, expected)
            self.assertEqual(
                identity.to_dict(),
                {
                    "path": os.fspath(pidfile),
                    "pid": process.pid,
                    "device_id": details.st_dev,
                    "inode": details.st_ino,
                    "owner_uid": details.st_uid,
                    "mode": 0o600,
                    "link_count": 1,
                    "size_bytes": len(f"{process.pid}\n"),
                    "mtime_ns": details.st_mtime_ns,
                    "ctime_ns": details.st_ctime_ns,
                },
            )

    def test_pidfile_replacement_between_observations_is_rejected(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        with tempfile.TemporaryDirectory(prefix="vm-lab-p4-pidfile-race-") as raw:
            root = Path(raw).resolve()
            pidfile = root / "qemu.pid"
            replacement = root / "replacement.pid"
            displaced = root / "displaced.pid"
            for path in (pidfile, replacement):
                path.write_text(f"{process.pid}\n", encoding="ascii")
                path.chmod(0o600)
            original_inode = pidfile.lstat().st_ino
            replacement_inode = replacement.lstat().st_ino
            self.assertNotEqual(original_inode, replacement_inode)

            replaced = False

            def replace_after_verified_read(
                frame: object,
                event: str,
                _: object,
            ) -> object:
                nonlocal replaced
                if (
                    not replaced
                    and event == "line"
                    and getattr(frame, "f_code", None) is observe_pidfile.__code__
                    and "pid" in getattr(frame, "f_locals", {})
                    and "after" not in getattr(frame, "f_locals", {})
                ):
                    # This schedules real pathname replacement at the exact
                    # TOCTOU boundary without replacing or mocking any owner
                    # function: the verified inode remains at ``displaced``
                    # while a different valid private inode takes its path.
                    pidfile.rename(displaced)
                    replacement.rename(pidfile)
                    replaced = True
                return replace_after_verified_read

            previous_trace = sys.gettrace()
            sys.settrace(replace_after_verified_read)
            try:
                with self.assertRaisesRegex(
                    QemuProcessError,
                    "identity changed during observation",
                ):
                    observe_pidfile(observed, pidfile)
            finally:
                sys.settrace(previous_trace)

            self.assertTrue(replaced)
            self.assertEqual(displaced.lstat().st_ino, original_inode)
            self.assertEqual(pidfile.lstat().st_ino, replacement_inode)
            self.assertEqual(
                observe_pidfile(observed, pidfile).inode,
                replacement_inode,
            )

    def test_exact_session_leader_terminates_and_exit_is_observed(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        evidence = terminate_owned_process(
            observed,
            graceful_timeout_seconds=2.0,
            kill_timeout_seconds=2.0,
        )
        self.assertEqual(evidence.pid, process.pid)
        self.assertTrue(evidence.pidfd_used)
        self.assertTrue(evidence.sigterm_sent)
        self.assertFalse(evidence.sigkill_sent)
        self.assertTrue(evidence.exited)
        self.assertEqual(process.wait(timeout=5.0), -signal.SIGTERM)
        self.assertFalse(Path(f"/proc/{process.pid}").exists())

    def test_sigterm_resistance_escalates_only_after_revalidation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vm-lab-p4-term-") as raw:
            ready = Path(raw).resolve() / "ready"
            code = (
                "import signal,time,pathlib;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                f"pathlib.Path({os.fspath(ready)!r}).write_text('ready');"
                "time.sleep(30)"
            )
            argv = normalize_argv(PYTHON, ("-c", code))
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            self.addCleanup(_cleanup_process, process)
            deadline = time.monotonic() + 5.0
            while not ready.is_file() and time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail("SIGTERM-resistant helper exited before readiness")
                time.sleep(0.01)
            self.assertTrue(ready.is_file())
            observed = observe_process(
                process.pid,
                expected_executable=PYTHON,
                expected_argv=argv,
            )
            evidence = terminate_owned_process(
                observed,
                graceful_timeout_seconds=0.05,
                kill_timeout_seconds=2.0,
            )
            self.assertTrue(evidence.pidfd_used)
            self.assertTrue(evidence.sigterm_sent)
            self.assertTrue(evidence.sigkill_sent)
            self.assertTrue(evidence.exited)
            self.assertEqual(process.wait(timeout=5.0), -signal.SIGKILL)
            self.assertFalse(Path(f"/proc/{process.pid}").exists())

    def test_zero_grace_timeout_returns_exact_exit_evidence(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        evidence = terminate_owned_process(
            observed,
            graceful_timeout_seconds=0.0,
            kill_timeout_seconds=2.0,
        )
        self.assertTrue(evidence.pidfd_used)
        self.assertTrue(evidence.exited)
        self.assertIn(
            process.wait(timeout=5.0),
            {-signal.SIGTERM, -signal.SIGKILL},
        )

    def test_forged_identity_never_signals_the_live_process(self) -> None:
        process, argv = self._spawn_sleep()
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        forged = replace(
            observed,
            start_time_ticks=observed.start_time_ticks + 1,
        )
        with self.assertRaisesRegex(
            ProcessIdentityMismatchError,
            "recorded identity",
        ):
            terminate_owned_process(
                forged,
                graceful_timeout_seconds=0.01,
                kill_timeout_seconds=0.01,
            )
        self.assertIsNone(process.poll())
        self.assertEqual(revalidate_process(observed).pid, process.pid)

    def test_unisolated_process_is_rejected_without_signal(self) -> None:
        process, argv = self._spawn_sleep(isolated=False)
        observed = observe_process(
            process.pid,
            expected_executable=SLEEP,
            expected_argv=argv,
        )
        self.assertTrue(
            observed.process_group_id != process.pid
            or observed.session_id != process.pid
        )
        with self.assertRaisesRegex(
            ProcessIdentityMismatchError,
            "isolated",
        ):
            terminate_owned_process(observed)
        self.assertIsNone(process.poll())
        self.assertEqual(revalidate_process(observed).pid, process.pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
