"""Linux parent-death guard for daemon-owned native subprocesses.

The mutation daemon must never leave ``qemu-img`` writing after its single
owner dies.  This tiny standard-library launcher arms Linux
``PR_SET_PDEATHSIG`` and then replaces itself with the already validated native
executable.  It is an internal process boundary, not a public command.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from pathlib import Path
from typing import Final, Sequence


_PR_SET_PDEATHSIG: Final[int] = 1
_FAILURE_EXIT: Final[int] = 126


def _set_parent_death_signal(selected_signal: int) -> None:
    if sys.platform != "linux":
        raise RuntimeError("native subprocess parent-death guard requires Linux")
    if (
        not isinstance(selected_signal, int)
        or isinstance(selected_signal, bool)
        or selected_signal < 0
        or selected_signal >= signal.NSIG
    ):
        raise RuntimeError("parent-death signal is invalid")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_PDEATHSIG, selected_signal, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _arm_parent_death(expected_parent_pid: int) -> None:
    _set_parent_death_signal(signal.SIGKILL)
    if os.getppid() != expected_parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)


def _clear_parent_death(expected_parent_pid: int) -> None:
    """Disarm only while the expected parent still owns the release boundary."""

    if os.getppid() != expected_parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)
    _set_parent_death_signal(0)


def main(arguments: Sequence[str] | None = None) -> int:
    argv = tuple(sys.argv[1:] if arguments is None else arguments)
    if len(argv) < 2:
        raise RuntimeError("parent PID and executable are required")
    try:
        expected_parent_pid = int(argv[0], 10)
    except ValueError as exc:
        raise RuntimeError("parent PID is invalid") from exc
    if expected_parent_pid < 1:
        raise RuntimeError("parent PID is invalid")
    executable = Path(argv[1])
    if (
        not executable.is_absolute()
        or executable == Path("/")
        or executable.resolve(strict=True) != executable
    ):
        raise RuntimeError("guarded executable must be canonical and absolute")
    _arm_parent_death(expected_parent_pid)
    os.execv(
        executable,
        (os.fspath(executable), *argv[2:]),
    )
    raise AssertionError("os.execv unexpectedly returned")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"somnus native exec guard failed: {type(exc).__name__}\n"
        os.write(2, message.encode("ascii", errors="replace")[:512])
        raise SystemExit(_FAILURE_EXIT)
