#!/usr/bin/env python3
"""Prepare, preflight, and explicitly execute the physical VM Lab P4 gate.

This is a repository operator surface, not an installed lifecycle command.
Its default modes never launch QEMU. ``execute`` and ``execute-matrix`` require
exact run-bound execution phrases and delegate all P3/QEMU mutation to the
ordinary daemon composition through its Python-only hook. The deterministic
phrases prevent accidental invocation; they do not authenticate Daeron or
replace his explicit authority to perform the physical run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Final
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if os.fspath(SOURCE) not in sys.path:
    sys.path.insert(0, os.fspath(SOURCE))

from somnus_vm.daemon_runtime import (  # noqa: E402
    DaemonRuntimeHooks,
    run as run_daemon_runtime,
)
from somnus_vm.host.p4_gate import (  # noqa: E402
    GateProductionPath,
    P4_GATE_MATRIX_RESULT_SCHEMA,
    P4_GATE_RESULT_SCHEMA,
    P4GateActivation,
    P4GateError,
    P4GateMatrixSpec,
    P4GateRecoveryActivation,
    P4GateSpec,
    preflight_gate,
    preflight_gate_matrix,
    prepare_gate_fixture,
    prepare_gate_matrix,
    validate_sealed_gate_matrix_result,
    validate_sealed_gate_result,
)
from somnus_vm.host.qemu_process import (  # noqa: E402
    ObservedProcessIdentity,
    ProcessIdentityMismatchError,
    ProcessUnavailableError,
    observe_process,
    revalidate_process,
    terminate_owned_process,
)
from somnus_vm.host.qemu_runtime_journal import (  # noqa: E402
    CHECKPOINT_ORDER,
    RuntimeLaunchJournal,
)
from somnus_vm.host.qmp import QMPClient  # noqa: E402
from somnus_vm.host.registry import SQLiteRegistry  # noqa: E402


_WAIT_SECONDS: Final[float] = 180.0
_POLL_SECONDS: Final[float] = 0.05
_CLEANUP_SCHEMA: Final[str] = "somnus.p4-physical-gate.cleanup.v1"
_MAX_SEALED_LOG_BYTES: Final[int] = 64 * 1024 * 1024


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _atomic_write(path: Path, value: object) -> None:
    payload = _canonical_bytes(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise P4GateError("gate result write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _parse_exclusion(value: str) -> GateProductionPath:
    label, separator, raw_path = value.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError(
            "production exclusion must use LABEL=/canonical/path"
        )
    try:
        return GateProductionPath(label, raw_path)
    except P4GateError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _wait_for_file_or_exit(
    path: Path,
    process: subprocess.Popen[bytes],
    *,
    timeout: float = _WAIT_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        if process.poll() is not None:
            return path.is_file()
        time.sleep(_POLL_SECONDS)
    return False


def _stop_worker(process: subprocess.Popen[bytes]) -> tuple[int, bytes, bytes]:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=30.0)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10.0)
        raise P4GateError("gate daemon did not complete ordered shutdown")
    return int(process.returncode), stdout, stderr


def _worker_command(
    spec_path: Path,
    authorization: str,
    mode: str,
    evidence_name: str,
    *,
    parent_pid: int,
    control_fd: int,
) -> list[str]:
    return [
        sys.executable,
        os.fspath(Path(__file__).resolve()),
        "_worker",
        mode,
        "--spec",
        os.fspath(spec_path),
        "--authorization",
        authorization,
        "--recovery-evidence-name",
        evidence_name,
        "--parent-pid",
        str(parent_pid),
        "--control-fd",
        str(control_fd),
    ]


def _start_worker(
    spec_path: Path,
    authorization: str,
    mode: str,
    evidence_name: str,
) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.fspath(SOURCE)
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    payload = _canonical_bytes(
        {
            "run_id": str(P4GateSpec.from_path(spec_path).run_id),
            "mode": mode,
            "parent_pid": os.getpid(),
        }
    )
    try:
        if os.write(write_fd, payload) != len(payload):
            raise P4GateError("gate worker control write was incomplete")
        os.close(write_fd)
        write_fd = -1
        process = subprocess.Popen(
            _worker_command(
                spec_path,
                authorization,
                mode,
                evidence_name,
                parent_pid=os.getpid(),
                control_fd=read_fd,
            ),
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(read_fd,),
            start_new_session=True,
        )
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)
    return process


def _journal_for_launch(spec: P4GateSpec, launch: dict[str, object]) -> RuntimeLaunchJournal:
    launch_body = launch.get("launch")
    if not isinstance(launch_body, dict):
        raise P4GateError("launch evidence body is invalid")
    operation_id = UUID(str(launch_body.get("operation_id")))
    configuration = preflight_gate(
        Path(spec.config_path).parent / "gate-p4-spec.json",
        require_fresh_runtime=False,
    )[1]
    registry_path = configuration.storage.state_root / "registry.sqlite3"
    with SQLiteRegistry.open(registry_path, writer=False) as registry:
        operation = registry.get_operation(operation_id)
        if operation is None:
            raise P4GateError("launch operation disappeared from the registry")
        return RuntimeLaunchJournal.replay(
            operation,
            registry.list_checkpoints(operation_id),
        )


def _wait_exact_process_absence(
    expected: ObservedProcessIdentity,
    *,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            revalidate_process(expected)
        except (ProcessUnavailableError, ProcessIdentityMismatchError):
            return
        time.sleep(_POLL_SECONDS)
    raise P4GateError("QEMU did not exit after the authenticated QMP quit")


def _quit_journal_runtime(
    spec: P4GateSpec,
    journal: RuntimeLaunchJournal,
    expected: ObservedProcessIdentity,
) -> dict[str, object]:
    client = QMPClient(
        journal.intent.qmp_socket,
        allowed_server_uids={os.geteuid()},
    )
    try:
        greeting = client.connect()
        if client.peer_credentials.pid != expected.pid:
            raise P4GateError("cleanup QMP peer is not the launched QEMU process")
        status = client.query_status()
        identity = client.query_uuid()
        block = client.query_block()
        fdsets = client.execute_block_graph("query-fdsets")
        nodes = client.execute_block_graph("query-named-block-nodes")
        graph = client.execute_block_graph("query-blockstats")
        quit_response = client.quit()
        responses = [
            response.to_dict() for response in client.response_history
        ]
        events = [event.to_dict() for event in client.drain_events()]
    finally:
        client.close()
    _wait_exact_process_absence(expected)
    return {
        "schema": _CLEANUP_SCHEMA,
        "run_id": str(spec.run_id),
        "process": expected.to_dict(),
        "greeting": greeting.to_dict(),
        "status": status.to_dict(),
        "uuid": identity.to_dict(),
        "block": block.to_dict(),
        "fdsets": fdsets.to_dict(),
        "named_block_nodes": nodes.to_dict(),
        "blockstats": graph.to_dict(),
        "quit": quit_response.to_dict(),
        "response_history": responses,
        "events": events,
        "exit_observed": True,
    }


def _quit_completed_runtime(
    spec: P4GateSpec,
    launch_path: Path,
) -> dict[str, object]:
    launch = json.loads(launch_path.read_text(encoding="ascii"))
    if not isinstance(launch, dict):
        raise P4GateError("launch evidence is invalid")
    journal = _journal_for_launch(spec, launch)
    launch_body = launch.get("launch")
    if not isinstance(launch_body, dict):
        raise P4GateError("launch evidence body is invalid")
    expected = ObservedProcessIdentity.from_dict(launch_body["process"])
    return _quit_journal_runtime(spec, journal, expected)


def _seal_success_runtime_logs(
    spec: P4GateSpec,
    launch_path: Path,
) -> None:
    """Retain the actual serial and QEMU output bytes inside gate evidence."""

    launch = json.loads(launch_path.read_text(encoding="ascii"))
    body = launch.get("launch") if isinstance(launch, dict) else None
    if not isinstance(body, dict):
        raise P4GateError("launch evidence body is invalid")
    pairs = (
        ("serial_log_path", "serial-console.log"),
        ("qemu_log_path", "qemu-stderr.log"),
    )
    log_root = Path(spec.fixture_root) / "logs"
    for source_key, evidence_name in pairs:
        raw_path = body.get(source_key)
        if not isinstance(raw_path, str):
            raise P4GateError("launch result omitted a runtime log path")
        source = Path(raw_path)
        try:
            if source.resolve(strict=True) != source:
                raise P4GateError("runtime log traverses a symlink")
            details = source.lstat()
        except OSError as exc:
            raise P4GateError("runtime log is unavailable") from exc
        if (
            not source.is_relative_to(log_root)
            or not source.is_file()
            or source.is_symlink()
            or details.st_uid != os.geteuid()
            or details.st_nlink != 1
            or details.st_size > _MAX_SEALED_LOG_BYTES
        ):
            raise P4GateError("runtime log is outside its exact bounded owner")
        raw = source.read_bytes()
        if len(raw) != details.st_size:
            raise P4GateError("runtime log changed while it was sealed")
        destination = spec.evidence_root / evidence_name
        if destination.exists() or destination.is_symlink():
            if destination.read_bytes() != raw:
                raise P4GateError("sealed runtime log already changed")
        else:
            descriptor = os.open(
                destination,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | os.O_NOFOLLOW,
                0o600,
            )
            try:
                view = memoryview(raw)
                written = 0
                while written < len(view):
                    count = os.write(descriptor, view[written:])
                    if count <= 0:
                        raise P4GateError(
                            "runtime log archival made no progress"
                        )
                    written += count
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def _checkpoint_journal(
    spec: P4GateSpec,
    checkpoint_path: Path,
) -> tuple[RuntimeLaunchJournal, ObservedProcessIdentity]:
    checkpoint = json.loads(checkpoint_path.read_text(encoding="ascii"))
    if not isinstance(checkpoint, dict):
        raise P4GateError("checkpoint evidence is invalid")
    operation_id = UUID(str(checkpoint.get("operation_id")))
    configuration = preflight_gate(
        Path(spec.config_path).parent / "gate-p4-spec.json",
        require_fresh_runtime=False,
    )[1]
    registry_path = configuration.storage.state_root / "registry.sqlite3"
    with SQLiteRegistry.open(registry_path, writer=False) as registry:
        operation = registry.get_operation(operation_id)
        if operation is None:
            raise P4GateError("checkpoint operation disappeared")
        journal = RuntimeLaunchJournal.replay(
            operation,
            registry.list_checkpoints(operation_id),
        )
    return journal, journal.observed_process_at("process_observed")


def _seal_result(
    spec: P4GateSpec,
    *,
    status: str,
    worker_returncode: int,
    worker_stdout: bytes,
    worker_stderr: bytes,
    recovery_returncode: int,
    recovery_stdout: bytes,
    recovery_stderr: bytes,
) -> Path:
    evidence = spec.evidence_root
    _atomic_write(
        evidence / "result.log",
        {
            "schema": P4_GATE_RESULT_SCHEMA,
            "run_id": str(spec.run_id),
            "worker_stdout": worker_stdout.decode("utf-8", errors="replace"),
            "worker_stderr": worker_stderr.decode("utf-8", errors="replace"),
            "recovery_stdout": recovery_stdout.decode(
                "utf-8", errors="replace"
            ),
            "recovery_stderr": recovery_stderr.decode(
                "utf-8", errors="replace"
            ),
        },
    )
    markdown = (
        f"# VM Lab physical GATE-P4 scenario\n\n"
        f"- run: `{spec.run_id}`\n"
        f"- status: `{status}`\n"
        f"- kill checkpoint: `{spec.kill_at_checkpoint or 'none'}`\n"
    )
    markdown_path = evidence / "result.md"
    descriptor = os.open(
        markdown_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(descriptor, markdown.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    records: list[dict[str, object]] = []
    for path in sorted(evidence.iterdir(), key=lambda item: item.name):
        if path.name == "result.json":
            continue
        if not path.is_file() or path.is_symlink():
            raise P4GateError("gate evidence contains a foreign entry")
        raw = path.read_bytes()
        records.append(
            {
                "name": path.name,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    result = {
        "schema": P4_GATE_RESULT_SCHEMA,
        "run_id": str(spec.run_id),
        "status": status,
        "cleanup_scope": "runtime-process-only;fixture-retained",
        "kill_at_checkpoint": spec.kill_at_checkpoint,
        "worker": {
            "returncode": worker_returncode,
            "stdout_sha256": hashlib.sha256(worker_stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(worker_stderr).hexdigest(),
        },
        "recovery_worker": {
            "returncode": recovery_returncode,
            "stdout_sha256": hashlib.sha256(recovery_stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(recovery_stderr).hexdigest(),
        },
        "records": records,
    }
    result_path = evidence / "result.json"
    _atomic_write(result_path, result)
    validate_sealed_gate_result(
        Path(spec.config_path).parent / "gate-p4-spec.json",
        result_path,
    )
    return result_path


def _run_recovery_worker(
    spec_path: Path,
    spec: P4GateSpec,
    authorization: str,
    evidence_name: str,
) -> tuple[int, bytes, bytes]:
    path = spec.evidence_root / evidence_name
    worker = _start_worker(
        spec_path,
        authorization,
        "recover",
        evidence_name,
    )
    if not _wait_for_file_or_exit(path, worker):
        code, stdout, stderr = _stop_worker(worker)
        raise P4GateError(
            f"recovery daemon produced no evidence (exit {code}): "
            f"{stderr.decode('utf-8', errors='replace')}"
        )
    return _stop_worker(worker)


def _execute_scenario(
    spec_path: Path,
    authorization: str,
    foreign_sentinel: ObservedProcessIdentity,
) -> Path:
    spec, _, _, preflight = preflight_gate(
        spec_path,
        authorization=authorization,
        require_execution_authority=True,
    )
    _atomic_write(
        spec.evidence_root / "preflight.json",
        preflight.to_dict(spec),
    )
    _atomic_write(
        spec.evidence_root / "foreign-process-before.json",
        {
            "schema": "somnus.p4-physical-gate.foreign-process.v1",
            "run_id": str(spec.run_id),
            "phase": "before",
            "process": foreign_sentinel.to_dict(),
        },
    )
    launch_path = spec.evidence_root / "launch.json"
    worker = _start_worker(spec_path, authorization, "launch", "recovery.json")
    checkpoint_path = (
        None
        if spec.kill_at_checkpoint is None
        else spec.evidence_root
        / (
            f"checkpoint-"
            f"{CHECKPOINT_ORDER.index(spec.kill_at_checkpoint):02d}-"
            f"{spec.kill_at_checkpoint}.json"
        )
    )
    awaited = launch_path if checkpoint_path is None else checkpoint_path
    if not _wait_for_file_or_exit(awaited, worker):
        code, stdout, stderr = _stop_worker(worker)
        raise P4GateError(
            f"launch worker produced no gate evidence (exit {code}): "
            f"{stderr.decode('utf-8', errors='replace')}"
        )
    if checkpoint_path is not None:
        try:
            worker_returncode = worker.wait(timeout=30.0)
            worker_stdout, worker_stderr = worker.communicate(timeout=1.0)
        except subprocess.TimeoutExpired as exc:
            worker.kill()
            worker_stdout, worker_stderr = worker.communicate(timeout=10.0)
            raise P4GateError("checkpoint worker did not die by SIGKILL") from exc
        if worker_returncode != -signal.SIGKILL:
            raise P4GateError(
                f"checkpoint worker exited {worker_returncode}, not SIGKILL"
            )
    else:
        worker_returncode, worker_stdout, worker_stderr = _stop_worker(worker)
        if worker_returncode != 0:
            raise P4GateError("successful launch worker did not stop cleanly")
        cleanup = _quit_completed_runtime(spec, launch_path)
        _atomic_write(spec.evidence_root / "cleanup.json", cleanup)
        _seal_success_runtime_logs(spec, launch_path)

    (
        recovery_returncode,
        recovery_stdout,
        recovery_stderr,
    ) = _run_recovery_worker(
        spec_path,
        spec,
        authorization,
        "recovery.json",
    )
    if recovery_returncode != 0:
        raise P4GateError("recovery daemon did not stop cleanly")

    recovery = json.loads(
        (spec.evidence_root / "recovery.json").read_text(encoding="ascii")
    )
    live = recovery.get("live_vm_ids") if isinstance(recovery, dict) else None
    if live:
        if launch_path.is_file():
            cleanup = _quit_completed_runtime(spec, launch_path)
        else:
            if checkpoint_path is None:
                raise P4GateError("adopted runtime has no launch checkpoint")
            journal, expected = _checkpoint_journal(spec, checkpoint_path)
            cleanup = _quit_journal_runtime(spec, journal, expected)
        _atomic_write(spec.evidence_root / "cleanup.json", cleanup)
        (
            second_code,
            second_stdout,
            second_stderr,
        ) = _run_recovery_worker(
            spec_path,
            spec,
            authorization,
            "post-cleanup-recovery.json",
        )
        if second_code != 0:
            raise P4GateError("post-cleanup recovery did not stop cleanly")
        recovery_stdout += second_stdout
        recovery_stderr += second_stderr
        status = "checkpoint_adopted_and_cleaned"
    else:
        status = (
            "completed_and_cleaned"
            if spec.kill_at_checkpoint is None
            else "checkpoint_cleaned"
        )
    foreign_after = revalidate_process(foreign_sentinel)
    _atomic_write(
        spec.evidence_root / "foreign-process-after.json",
        {
            "schema": "somnus.p4-physical-gate.foreign-process.v1",
            "run_id": str(spec.run_id),
            "phase": "after",
            "process": foreign_after.to_dict(),
            "untouched": True,
        },
    )
    return _seal_result(
        spec,
        status=status,
        worker_returncode=worker_returncode,
        worker_stdout=worker_stdout,
        worker_stderr=worker_stderr,
        recovery_returncode=recovery_returncode,
        recovery_stdout=recovery_stdout,
        recovery_stderr=recovery_stderr,
    )


def execute_scenario(spec_path: Path, authorization: str) -> Path:
    """Run one authorized scenario while proving a real foreign task survives."""

    # Authority denial precedes even the real foreign-sentinel child.
    preflight_gate(
        spec_path,
        authorization=authorization,
        require_execution_authority=True,
    )
    sentinel_process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            "import time; time.sleep(3600)",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    sentinel: ObservedProcessIdentity | None = None
    try:
        sentinel = observe_process(sentinel_process.pid)
        return _execute_scenario(spec_path, authorization, sentinel)
    finally:
        if sentinel is not None:
            try:
                terminate_owned_process(
                    sentinel,
                    graceful_timeout_seconds=5.0,
                    kill_timeout_seconds=5.0,
                )
            except (ProcessUnavailableError, ProcessIdentityMismatchError):
                pass
        elif sentinel_process.poll() is None:
            sentinel_process.kill()
            sentinel_process.wait(timeout=5.0)


def _matrix_scenario_record(
    scenario: object,
    result_path: Path,
) -> dict[str, object]:
    """Return one validated, hash-bound aggregate matrix entry."""

    ordinal = int(getattr(scenario, "ordinal"))
    name = str(getattr(scenario, "name"))
    run_id = getattr(scenario, "run_id")
    checkpoint = getattr(scenario, "kill_at_checkpoint")
    spec_path = Path(str(getattr(scenario, "spec_path")))
    result = validate_sealed_gate_result(spec_path, result_path)
    return {
        "ordinal": ordinal,
        "name": name,
        "run_id": str(run_id),
        "kill_at_checkpoint": checkpoint,
        "status": result["status"],
        "result_path": os.fspath(result_path),
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
    }


def _matrix_progress(
    matrix: P4GateMatrixSpec,
    completed: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema": "somnus.p4-physical-gate.matrix-progress.v1",
        "matrix_id": str(matrix.matrix_id),
        "matrix_spec_sha256": hashlib.sha256(
            matrix.canonical_bytes
        ).hexdigest(),
        "completed_count": len(completed),
        "next_ordinal": len(completed),
        "scenarios": completed,
    }


def execute_matrix(matrix_spec_path: Path, execution_phrase: str) -> Path:
    """Execute and seal success plus all sixteen P4 checkpoint scenarios.

    The phrase is an explicit accidental-execution fence bound to this frozen
    matrix.  Human authority remains the caller's external responsibility; the
    deterministic phrase is deliberately not represented as authentication.
    """

    matrix, manifest, preflight = preflight_gate_matrix(
        matrix_spec_path,
        authorization=execution_phrase,
        require_execution_authority=True,
        allow_execution_state=True,
    )
    if matrix.failure_path.exists() or matrix.failure_path.is_symlink():
        raise P4GateError(
            "matrix has a preserved failed attempt and cannot reuse fixtures"
        )
    if matrix.result_path.exists() or matrix.result_path.is_symlink():
        validate_sealed_gate_matrix_result(
            matrix_spec_path,
            matrix.result_path,
        )
        return matrix.result_path

    completed: list[dict[str, object]] = []
    for scenario in matrix.scenarios:
        result_path = (
            Path(scenario.spec_path).parent
            / "gate-evidence"
            / "result.json"
        )
        if result_path.is_file() and not result_path.is_symlink():
            completed.append(
                _matrix_scenario_record(scenario, result_path)
            )
            continue
        if any(
            Path(later.spec_path).parent
            .joinpath("gate-evidence", "result.json")
            .exists()
            for later in matrix.scenarios[scenario.ordinal + 1 :]
        ):
            raise P4GateError(
                "matrix contains a sealed later scenario before this ordinal"
            )
        try:
            result_path = execute_scenario(
                Path(scenario.spec_path),
                P4GateSpec.from_path(
                    scenario.spec_path
                ).execution_phrase,
            )
            completed.append(
                _matrix_scenario_record(scenario, result_path)
            )
            _atomic_write(
                matrix.progress_path,
                _matrix_progress(matrix, completed),
            )
        except BaseException as exc:
            failure = {
                "schema": "somnus.p4-physical-gate.matrix-failure.v1",
                "matrix_id": str(matrix.matrix_id),
                "matrix_spec_sha256": hashlib.sha256(
                    matrix.canonical_bytes
                ).hexdigest(),
                "failed_ordinal": scenario.ordinal,
                "failed_scenario": scenario.name,
                "completed_count": len(completed),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            if not matrix.failure_path.exists():
                _atomic_write(matrix.failure_path, failure)
            raise

    if len(completed) != len(matrix.scenarios):
        raise P4GateError("matrix did not seal every required scenario")
    matrix_result = {
        "schema": P4_GATE_MATRIX_RESULT_SCHEMA,
        "matrix_id": str(matrix.matrix_id),
        "matrix_spec_sha256": hashlib.sha256(
            matrix.canonical_bytes
        ).hexdigest(),
        "matrix_preflight_sha256": hashlib.sha256(
            _canonical_bytes(preflight.to_dict(matrix))
        ).hexdigest(),
        "manifest_sha256": manifest.manifest_sha256,
        "source_sha256": preflight.source_sha256,
        "status": "passed",
        "scenario_count": len(completed),
        "scenarios": completed,
    }
    _atomic_write(matrix.result_path, matrix_result)
    validate_sealed_gate_matrix_result(
        matrix_spec_path,
        matrix.result_path,
    )
    return matrix.result_path


def _worker(
    mode: str,
    spec_path: Path,
    authorization: str,
    evidence_name: str,
    parent_pid: int,
    control_fd: int,
) -> int:
    spec, _, manifest, preflight = preflight_gate(
        spec_path,
        authorization=authorization,
        require_execution_authority=True,
        require_fresh_runtime=mode == "launch",
    )
    if (
        not isinstance(parent_pid, int)
        or isinstance(parent_pid, bool)
        or parent_pid < 2
        or os.getppid() != parent_pid
        or not isinstance(control_fd, int)
        or isinstance(control_fd, bool)
        or control_fd < 3
    ):
        raise P4GateError("gate worker lacks its exact coordinator parent")
    expected_control = _canonical_bytes(
        {
            "run_id": str(spec.run_id),
            "mode": mode,
            "parent_pid": parent_pid,
        }
    )
    try:
        received = os.read(control_fd, len(expected_control) + 1)
        trailing = os.read(control_fd, 1)
    except OSError as exc:
        raise P4GateError("gate worker control authority is unavailable") from exc
    finally:
        try:
            os.close(control_fd)
        except OSError:
            pass
    if received != expected_control or trailing:
        raise P4GateError("gate worker control authority is invalid")
    if mode == "launch":
        activation = P4GateActivation(spec, manifest, preflight)
        hooks = DaemonRuntimeHooks(
            checkpoint_observer=activation.checkpoint_observer,
            activation=activation,
        )
    else:
        hooks = DaemonRuntimeHooks(
            activation=P4GateRecoveryActivation(
                spec,
                evidence_name=evidence_name,
            )
        )
    return run_daemon_runtime(Path(spec.config_path), hooks=hooks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_gate_p4.py",
        description=(
            "Prepare or execute the explicit disposable physical GATE-P4. "
            "No public vm-lab lifecycle command is created."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--fixture-root", type=Path, required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument(
        "--production-exclusion",
        type=_parse_exclusion,
        action="append",
        required=True,
    )
    prepare.add_argument(
        "--qemu-binary",
        default="qemu-system-x86_64",
    )
    prepare.add_argument("--qemu-img-binary", default="qemu-img")
    prepare.add_argument("--kill-at-checkpoint")

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--spec", type=Path, required=True)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--spec", type=Path, required=True)
    execute.add_argument("--authorization", required=True)

    prepare_matrix_parser = subparsers.add_parser("prepare-matrix")
    prepare_matrix_parser.add_argument(
        "--matrix-root",
        type=Path,
        required=True,
    )
    prepare_matrix_parser.add_argument("--manifest", type=Path, required=True)
    prepare_matrix_parser.add_argument("--source", type=Path, required=True)
    prepare_matrix_parser.add_argument(
        "--production-exclusion",
        type=_parse_exclusion,
        action="append",
        required=True,
    )
    prepare_matrix_parser.add_argument(
        "--qemu-binary",
        default="qemu-system-x86_64",
    )
    prepare_matrix_parser.add_argument(
        "--qemu-img-binary",
        default="qemu-img",
    )

    preflight_matrix_parser = subparsers.add_parser("preflight-matrix")
    preflight_matrix_parser.add_argument(
        "--matrix",
        type=Path,
        required=True,
    )

    execute_matrix_parser = subparsers.add_parser("execute-matrix")
    execute_matrix_parser.add_argument(
        "--matrix",
        type=Path,
        required=True,
    )
    execute_matrix_parser.add_argument(
        "--execution-phrase",
        required=True,
    )

    worker = subparsers.add_parser("_worker")
    worker.add_argument("mode", choices=("launch", "recover"))
    worker.add_argument("--spec", type=Path, required=True)
    worker.add_argument("--authorization", required=True)
    worker.add_argument(
        "--recovery-evidence-name",
        default="recovery.json",
    )
    worker.add_argument("--parent-pid", type=int, default=0)
    worker.add_argument("--control-fd", type=int, default=-1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        spec_path = prepare_gate_fixture(
            fixture_root=args.fixture_root,
            manifest_path=args.manifest,
            source_path=args.source,
            production_exclusions=args.production_exclusion,
            qemu_binary=args.qemu_binary,
            qemu_img_binary=args.qemu_img_binary,
            kill_at_checkpoint=args.kill_at_checkpoint,
        )
        spec = P4GateSpec.from_path(spec_path)
        print(
            json.dumps(
                {
                    "spec": os.fspath(spec_path),
                    "run_id": str(spec.run_id),
                    "execution_phrase": spec.execution_phrase,
                    "execution_phrase_is_authentication": False,
                    "qemu_launched": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "preflight":
        spec, _, _, preflight = preflight_gate(args.spec)
        print(
            json.dumps(
                {
                    **preflight.to_dict(spec),
                    "qemu_launched": False,
                    "execution_phrase_required": spec.execution_phrase,
                    "execution_phrase_is_authentication": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "execute":
        result = execute_scenario(args.spec, args.authorization)
        print(os.fspath(result))
        return 0
    if args.command == "prepare-matrix":
        matrix_path = prepare_gate_matrix(
            matrix_root=args.matrix_root,
            manifest_path=args.manifest,
            source_path=args.source,
            production_exclusions=args.production_exclusion,
            qemu_binary=args.qemu_binary,
            qemu_img_binary=args.qemu_img_binary,
        )
        matrix = P4GateMatrixSpec.from_path(matrix_path)
        print(
            json.dumps(
                {
                    "matrix": os.fspath(matrix_path),
                    "matrix_id": str(matrix.matrix_id),
                    "matrix_spec_sha256": hashlib.sha256(
                        matrix.canonical_bytes
                    ).hexdigest(),
                    "execution_phrase": matrix.execution_phrase,
                    "execution_phrase_is_authentication": False,
                    "scenario_count": len(matrix.scenarios),
                    "qemu_launched": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "preflight-matrix":
        matrix, _, matrix_preflight = preflight_gate_matrix(args.matrix)
        print(
            json.dumps(
                {
                    **matrix_preflight.to_dict(matrix),
                    "execution_phrase_required":
                    matrix.execution_phrase,
                    "execution_phrase_is_authentication": False,
                    "qemu_launched": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "execute-matrix":
        result = execute_matrix(args.matrix, args.execution_phrase)
        print(os.fspath(result))
        return 0
    return _worker(
        args.mode,
        args.spec,
        args.authorization,
        args.recovery_evidence_name,
        args.parent_pid,
        args.control_fd,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except P4GateError as exc:
        print(f"GATE-P4 denied: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
