"""Explicit non-public coordinator contract for physical Phase P4 execution.

Source: PLAN.md GATE-P4 and docs/DISPOSABLE_FIXTURE_POLICY.md
Integrated: 2026-08-07
Purpose: Prepare and validate one exact disposable fixture, then provide the
    Python-only daemon activation that drives P3 ownership into the existing
    QEMU runtime owner.  This module is not imported by the public CLI or the
    AF_UNIX control vocabulary.
IO: Creates only a caller-selected fresh disposable root; hashes exact source
    and executable bytes; writes bounded canonical gate evidence; launches
    QEMU only when the separate script supplies the exact run-bound execution
    phrase.  The phrase is an accidental-invocation fence, not authentication
    or a substitute for explicit operator authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Iterable
from uuid import UUID, uuid4

from somnus_protocol import (
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    ControlRequest,
    ControlResponse,
    ErrorEnvelope,
    TransitionEvidence,
    VMRecord,
    VMState,
)

from ..config import LabConfiguration, load_configuration
from ..daemon_runtime import DaemonRuntimeContext
from ..transport import PeerCredentials
from .images import ImageManifest, hash_file
from .launch_authority import (
    FIXTURE_MARKER_NAME,
    FIXTURE_MARKER_SCHEMA_VERSION,
    ProductionExclusion,
    mint_disposable_launch_permit,
)
from .qemu_runtime import RuntimeLaunchResult, RuntimeRecoveryResult
from .qemu_runtime_journal import CHECKPOINT_ORDER


P4_GATE_SPEC_SCHEMA: Final[str] = "somnus.p4-physical-gate.spec.v1"
P4_GATE_PREFLIGHT_SCHEMA: Final[str] = "somnus.p4-physical-gate.preflight.v1"
P4_GATE_CHECKPOINT_SCHEMA: Final[str] = "somnus.p4-physical-gate.checkpoint.v1"
P4_GATE_LAUNCH_SCHEMA: Final[str] = "somnus.p4-physical-gate.launch.v1"
P4_GATE_RECOVERY_SCHEMA: Final[str] = "somnus.p4-physical-gate.recovery.v1"
P4_GATE_RESULT_SCHEMA: Final[str] = "somnus.p4-physical-gate.result.v1"
P4_GATE_MATRIX_SPEC_SCHEMA: Final[str] = (
    "somnus.p4-physical-gate.matrix-spec.v1"
)
P4_GATE_MATRIX_PREFLIGHT_SCHEMA: Final[str] = (
    "somnus.p4-physical-gate.matrix-preflight.v1"
)
P4_GATE_MATRIX_RESULT_SCHEMA: Final[str] = (
    "somnus.p4-physical-gate.matrix-result.v1"
)
P4_GATE_AUTHORIZATION_PREFIX: Final[str] = (
    "I_AUTHORIZE_ONE_DISPOSABLE_VM_LAB_GATE_P4:"
)
P4_GATE_MATRIX_AUTHORIZATION_PREFIX: Final[str] = (
    "I_AUTHORIZE_DISPOSABLE_VM_LAB_GATE_P4_FULL_MATRIX:"
)
P4_GATE_CONFIG_NAME: Final[str] = "gate-p4.toml"
P4_GATE_SPEC_NAME: Final[str] = "gate-p4-spec.json"
P4_GATE_MATRIX_SPEC_NAME: Final[str] = "gate-p4-matrix.json"
P4_GATE_MATRIX_RESULT_NAME: Final[str] = "matrix-result.json"
P4_GATE_MATRIX_PROGRESS_NAME: Final[str] = "matrix-progress.json"
P4_GATE_MATRIX_FAILURE_NAME: Final[str] = "matrix-failure.json"
P4_GATE_EVIDENCE_DIRECTORY: Final[str] = "gate-evidence"
P4_GATE_VIRTUAL_SIZE_BYTES: Final[int] = 100 * 1024**3
_MAX_SPEC_BYTES: Final[int] = 262_144
_MAX_VERSION_BYTES: Final[int] = 65_536
_ROOT_NAMES: Final[tuple[str, ...]] = (
    "state",
    "runtime",
    "logs",
    "images",
    "backups",
    "secrets",
)


class P4GateError(RuntimeError):
    """Raised when a physical-gate request is not exact and disposable."""


def _canonical_bytes(value: object) -> bytes:
    """Encode a JSON value with one stable representation."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise P4GateError("gate evidence is not canonical JSON") from exc


def _read_canonical_object(path: Path, *, maximum: int) -> dict[str, object]:
    """Read one private regular file and require exact canonical JSON bytes."""

    try:
        details = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise P4GateError(f"gate file is unavailable: {path}") from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
        or not 1 <= len(raw) <= maximum
    ):
        raise P4GateError(f"gate file is not private and bounded: {path}")
    try:
        decoded = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P4GateError(f"gate file is not canonical JSON: {path}") from exc
    if not isinstance(decoded, dict) or _canonical_bytes(decoded) != raw:
        raise P4GateError(f"gate file is not canonical JSON: {path}")
    return decoded


def _write_exclusive(path: Path, payload: bytes, mode: int = 0o600) -> None:
    """Publish one new evidence file without replacement or symlink traversal."""

    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            mode,
        )
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise P4GateError("gate evidence write made no progress")
            written += count
        os.fsync(descriptor)
    except OSError as exc:
        raise P4GateError(f"gate evidence cannot be published: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _replace_canonical(path: Path, value: object) -> None:
    """Atomically replace one gate-owned canonical evidence record."""

    payload = _canonical_bytes(value)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    _write_exclusive(temporary, payload)
    try:
        os.replace(temporary, path)
        directory = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise P4GateError(f"gate evidence cannot be committed: {path}") from exc


def _canonical_existing(value: str | Path, label: str) -> Path:
    """Require one existing canonical absolute path with no symlink text."""

    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise P4GateError(f"{label} must be a filesystem path") from exc
    if (
        not isinstance(text, str)
        or not text.startswith("/")
        or text in {"/", ""}
        or text.startswith("//")
        or "\x00" in text
        or os.path.normpath(text) != text
    ):
        raise P4GateError(f"{label} must be canonical and absolute")
    path = Path(text)
    try:
        if path.resolve(strict=True) != path:
            raise P4GateError(f"{label} must not traverse a symlink")
    except OSError as exc:
        raise P4GateError(f"{label} cannot be resolved") from exc
    return path


def _strict_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, str):
        raise P4GateError(f"{label} must be a UUID")
    try:
        selected = UUID(value)
    except ValueError as exc:
        raise P4GateError(f"{label} must be a UUID") from exc
    if selected.int == 0:
        raise P4GateError(f"{label} must not be nil")
    return selected


def _sha256_file(path: Path, *, unique_link: bool = True) -> tuple[str, int]:
    """Hash one regular file through the P3 bounded file owner."""

    try:
        return hash_file(path, unique_link=unique_link)
    except Exception as exc:
        raise P4GateError(f"gate input cannot be hashed: {path}") from exc


def _binary_identity(binary: str, label: str) -> dict[str, object]:
    """Resolve, inspect, hash, and query one exact executable without launch."""

    candidate = Path(binary) if "/" in binary else Path(shutil.which(binary) or "")
    if not candidate.is_absolute():
        raise P4GateError(f"{label} executable is unavailable")
    selected = _canonical_existing(candidate.resolve(), f"{label} executable")
    details = selected.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_mode & 0o022
        or not details.st_mode & 0o111
    ):
        raise P4GateError(f"{label} executable ownership is unsafe")
    digest, size = _sha256_file(selected, unique_link=False)
    try:
        completed = subprocess.run(
            [os.fspath(selected), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=False,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise P4GateError(f"{label} version probe failed") from exc
    version = completed.stdout + completed.stderr
    if completed.returncode != 0 or not 1 <= len(version) <= _MAX_VERSION_BYTES:
        raise P4GateError(f"{label} version probe was rejected or unbounded")
    return {
        "path": os.fspath(selected),
        "device_id": details.st_dev,
        "inode": details.st_ino,
        "owner_uid": details.st_uid,
        "mode": stat.S_IMODE(details.st_mode),
        "size_bytes": size,
        "sha256": digest,
        "version_sha256": hashlib.sha256(version).hexdigest(),
        "version_text": version.decode("utf-8", errors="strict").strip(),
    }


@dataclass(frozen=True, slots=True)
class GateProductionPath:
    """One explicit production exclusion retained in the gate specification."""

    label: str
    path: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.label, str)
            or not self.label
            or len(self.label.encode("utf-8")) > 128
        ):
            raise P4GateError("production exclusion label is invalid")
        selected = _canonical_existing(self.path, "production exclusion")
        object.__setattr__(self, "path", os.fspath(selected))

    def to_dict(self) -> dict[str, object]:
        return {"label": self.label, "path": self.path}


@dataclass(frozen=True, slots=True)
class P4GateSpec:
    """Canonical prepared input for one disposable launch or kill scenario."""

    run_id: UUID
    fixture_root: str
    config_path: str
    manifest_path: str
    source_path: str
    declaration_request_id: UUID
    kill_at_checkpoint: str | None
    production_exclusions: tuple[GateProductionPath, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID) or self.run_id.int == 0:
            raise P4GateError("run_id must be a nonnil UUID")
        if (
            not isinstance(self.declaration_request_id, UUID)
            or self.declaration_request_id.int == 0
            or self.declaration_request_id == self.run_id
        ):
            raise P4GateError("declaration_request_id must be a distinct UUID")
        for field_name in (
            "fixture_root",
            "config_path",
            "manifest_path",
            "source_path",
        ):
            selected = _canonical_existing(
                getattr(self, field_name),
                field_name,
            )
            object.__setattr__(self, field_name, os.fspath(selected))
        fixture = Path(self.fixture_root)
        if not Path(self.config_path).is_relative_to(fixture / "runtime"):
            raise P4GateError("gate config must be inside the fixture runtime root")
        if self.kill_at_checkpoint is not None and (
            not isinstance(self.kill_at_checkpoint, str)
            or self.kill_at_checkpoint not in CHECKPOINT_ORDER
        ):
            raise P4GateError("kill_at_checkpoint is not a P4 launch checkpoint")
        if (
            not isinstance(self.production_exclusions, tuple)
            or not self.production_exclusions
            or any(
                not isinstance(item, GateProductionPath)
                for item in self.production_exclusions
            )
        ):
            raise P4GateError("at least one production exclusion is required")
        labels = [item.label for item in self.production_exclusions]
        if len(set(labels)) != len(labels):
            raise P4GateError("production exclusion labels must be unique")

    @property
    def evidence_root(self) -> Path:
        return Path(self.fixture_root) / "runtime" / P4_GATE_EVIDENCE_DIRECTORY

    @property
    def execution_phrase(self) -> str:
        return f"{P4_GATE_AUTHORIZATION_PREFIX}{self.run_id}"

    @property
    def authorization_token(self) -> str:
        """Compatibility name; this deterministic phrase is not authentication."""

        return self.execution_phrase

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": P4_GATE_SPEC_SCHEMA,
            "run_id": str(self.run_id),
            "fixture_root": self.fixture_root,
            "config_path": self.config_path,
            "manifest_path": self.manifest_path,
            "source_path": self.source_path,
            "declaration_request_id": str(self.declaration_request_id),
            "kill_at_checkpoint": self.kill_at_checkpoint,
            "production_exclusions": [
                item.to_dict() for item in self.production_exclusions
            ],
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_path(cls, path: str | Path) -> "P4GateSpec":
        selected = _canonical_existing(path, "gate specification")
        value = _read_canonical_object(selected, maximum=_MAX_SPEC_BYTES)
        if set(value) != {
            "schema",
            "run_id",
            "fixture_root",
            "config_path",
            "manifest_path",
            "source_path",
            "declaration_request_id",
            "kill_at_checkpoint",
            "production_exclusions",
        } or value["schema"] != P4_GATE_SPEC_SCHEMA:
            raise P4GateError("gate specification schema is invalid")
        exclusions = value["production_exclusions"]
        if not isinstance(exclusions, list):
            raise P4GateError("gate production exclusions are invalid")
        decoded: list[GateProductionPath] = []
        for item in exclusions:
            if (
                not isinstance(item, dict)
                or set(item) != {"label", "path"}
                or not isinstance(item["label"], str)
                or not isinstance(item["path"], str)
            ):
                raise P4GateError("gate production exclusion is invalid")
            decoded.append(GateProductionPath(item["label"], item["path"]))
        candidate = cls(
            run_id=_strict_uuid(value["run_id"], "run_id"),
            fixture_root=value["fixture_root"],  # type: ignore[arg-type]
            config_path=value["config_path"],  # type: ignore[arg-type]
            manifest_path=value["manifest_path"],  # type: ignore[arg-type]
            source_path=value["source_path"],  # type: ignore[arg-type]
            declaration_request_id=_strict_uuid(
                value["declaration_request_id"],
                "declaration_request_id",
            ),
            kill_at_checkpoint=value["kill_at_checkpoint"],  # type: ignore[arg-type]
            production_exclusions=tuple(decoded),
        )
        if candidate.to_dict() != value:
            raise P4GateError("gate specification is not canonical")
        return candidate


@dataclass(frozen=True, slots=True)
class P4GatePreflight:
    """Exact non-launch facts required before a daemon worker may start."""

    spec_sha256: str
    config_sha256: str
    config_size_bytes: int
    manifest_sha256: str
    source_sha256: str
    source_size_bytes: int
    qemu: dict[str, object]
    qemu_img: dict[str, object]
    exclusions: tuple[ProductionExclusion, ...]

    def to_dict(self, spec: P4GateSpec) -> dict[str, object]:
        return {
            "schema": P4_GATE_PREFLIGHT_SCHEMA,
            "run_id": str(spec.run_id),
            "spec_sha256": self.spec_sha256,
            "configuration": {
                "path": spec.config_path,
                "sha256": self.config_sha256,
                "size_bytes": self.config_size_bytes,
            },
            "manifest_sha256": self.manifest_sha256,
            "source": {
                "path": spec.source_path,
                "sha256": self.source_sha256,
                "size_bytes": self.source_size_bytes,
            },
            "qemu": self.qemu,
            "qemu_img": self.qemu_img,
            "production_exclusions": [
                {
                    "label": item.label,
                    "path": os.fspath(item.path),
                    "device_id": item.device_id,
                    "inode": item.inode,
                }
                for item in self.exclusions
            ],
        }


@dataclass(frozen=True, slots=True)
class P4GateMatrixScenario:
    """One immutable success or checkpoint case in the complete P4 matrix."""

    ordinal: int
    name: str
    kill_at_checkpoint: str | None
    spec_path: str
    run_id: UUID
    spec_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or not 0 <= self.ordinal <= len(CHECKPOINT_ORDER)
        ):
            raise P4GateError("matrix scenario ordinal is invalid")
        expected_checkpoint = (
            None if self.ordinal == 0 else CHECKPOINT_ORDER[self.ordinal - 1]
        )
        if self.kill_at_checkpoint != expected_checkpoint:
            raise P4GateError("matrix scenario checkpoint order is invalid")
        expected_name = (
            "00-success"
            if self.ordinal == 0
            else f"{self.ordinal:02d}-{expected_checkpoint}"
        )
        if self.name != expected_name:
            raise P4GateError("matrix scenario name is invalid")
        selected_path = _canonical_existing(
            self.spec_path,
            "matrix scenario specification",
        )
        object.__setattr__(self, "spec_path", os.fspath(selected_path))
        if not isinstance(self.run_id, UUID) or self.run_id.int == 0:
            raise P4GateError("matrix scenario run_id must be a nonnil UUID")
        if (
            not isinstance(self.spec_sha256, str)
            or len(self.spec_sha256) != 64
            or any(item not in "0123456789abcdef" for item in self.spec_sha256)
        ):
            raise P4GateError("matrix scenario spec_sha256 is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "name": self.name,
            "kill_at_checkpoint": self.kill_at_checkpoint,
            "spec_path": self.spec_path,
            "run_id": str(self.run_id),
            "spec_sha256": self.spec_sha256,
        }


@dataclass(frozen=True, slots=True)
class P4GateMatrixSpec:
    """Canonical authority-independent description of the full P4 matrix."""

    matrix_id: UUID
    matrix_root: str
    manifest_path: str
    source_path: str
    production_exclusions: tuple[GateProductionPath, ...]
    scenarios: tuple[P4GateMatrixScenario, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.matrix_id, UUID) or self.matrix_id.int == 0:
            raise P4GateError("matrix_id must be a nonnil UUID")
        for field_name in ("matrix_root", "manifest_path", "source_path"):
            selected = _canonical_existing(
                getattr(self, field_name),
                field_name,
            )
            object.__setattr__(self, field_name, os.fspath(selected))
        if (
            not isinstance(self.production_exclusions, tuple)
            or not self.production_exclusions
            or any(
                not isinstance(item, GateProductionPath)
                for item in self.production_exclusions
            )
        ):
            raise P4GateError(
                "matrix requires explicit production exclusions"
            )
        if (
            not isinstance(self.scenarios, tuple)
            or len(self.scenarios) != len(CHECKPOINT_ORDER) + 1
            or any(
                not isinstance(item, P4GateMatrixScenario)
                for item in self.scenarios
            )
        ):
            raise P4GateError(
                "matrix requires one success and every checkpoint scenario"
            )
        if tuple(item.ordinal for item in self.scenarios) != tuple(
            range(len(CHECKPOINT_ORDER) + 1)
        ):
            raise P4GateError("matrix scenario ordinals are not contiguous")
        run_ids = [item.run_id for item in self.scenarios]
        if self.matrix_id in run_ids or len(set(run_ids)) != len(run_ids):
            raise P4GateError("matrix and scenario identifiers must be unique")
        scenario_root = Path(self.matrix_root) / "scenarios"
        for item in self.scenarios:
            expected = (
                scenario_root
                / f"{item.ordinal:02d}"
                / "runtime"
                / P4_GATE_SPEC_NAME
            )
            if Path(item.spec_path) != expected:
                raise P4GateError(
                    "matrix scenario spec is outside its exact owner root"
                )

    @property
    def execution_phrase(self) -> str:
        return f"{P4_GATE_MATRIX_AUTHORIZATION_PREFIX}{self.matrix_id}"

    @property
    def authorization_token(self) -> str:
        """Compatibility name; this deterministic phrase is not authentication."""

        return self.execution_phrase

    @property
    def result_path(self) -> Path:
        return Path(self.matrix_root) / P4_GATE_MATRIX_RESULT_NAME

    @property
    def progress_path(self) -> Path:
        return Path(self.matrix_root) / P4_GATE_MATRIX_PROGRESS_NAME

    @property
    def failure_path(self) -> Path:
        return Path(self.matrix_root) / P4_GATE_MATRIX_FAILURE_NAME

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": P4_GATE_MATRIX_SPEC_SCHEMA,
            "matrix_id": str(self.matrix_id),
            "matrix_root": self.matrix_root,
            "manifest_path": self.manifest_path,
            "source_path": self.source_path,
            "production_exclusions": [
                item.to_dict() for item in self.production_exclusions
            ],
            "scenarios": [item.to_dict() for item in self.scenarios],
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_path(cls, path: str | Path) -> "P4GateMatrixSpec":
        selected = _canonical_existing(path, "gate matrix specification")
        value = _read_canonical_object(selected, maximum=_MAX_SPEC_BYTES)
        if set(value) != {
            "schema",
            "matrix_id",
            "matrix_root",
            "manifest_path",
            "source_path",
            "production_exclusions",
            "scenarios",
        } or value["schema"] != P4_GATE_MATRIX_SPEC_SCHEMA:
            raise P4GateError("gate matrix specification schema is invalid")
        exclusions = value["production_exclusions"]
        scenarios = value["scenarios"]
        if not isinstance(exclusions, list) or not isinstance(scenarios, list):
            raise P4GateError("gate matrix specification body is invalid")
        decoded_exclusions: list[GateProductionPath] = []
        for item in exclusions:
            if (
                not isinstance(item, dict)
                or set(item) != {"label", "path"}
                or not isinstance(item["label"], str)
                or not isinstance(item["path"], str)
            ):
                raise P4GateError("gate matrix exclusion is invalid")
            decoded_exclusions.append(
                GateProductionPath(item["label"], item["path"])
            )
        decoded_scenarios: list[P4GateMatrixScenario] = []
        for item in scenarios:
            if not isinstance(item, dict) or set(item) != {
                "ordinal",
                "name",
                "kill_at_checkpoint",
                "spec_path",
                "run_id",
                "spec_sha256",
            }:
                raise P4GateError("gate matrix scenario is invalid")
            decoded_scenarios.append(
                P4GateMatrixScenario(
                    ordinal=item["ordinal"],  # type: ignore[arg-type]
                    name=item["name"],  # type: ignore[arg-type]
                    kill_at_checkpoint=item["kill_at_checkpoint"],  # type: ignore[arg-type]
                    spec_path=item["spec_path"],  # type: ignore[arg-type]
                    run_id=_strict_uuid(item["run_id"], "scenario run_id"),
                    spec_sha256=item["spec_sha256"],  # type: ignore[arg-type]
                )
            )
        candidate = cls(
            matrix_id=_strict_uuid(value["matrix_id"], "matrix_id"),
            matrix_root=value["matrix_root"],  # type: ignore[arg-type]
            manifest_path=value["manifest_path"],  # type: ignore[arg-type]
            source_path=value["source_path"],  # type: ignore[arg-type]
            production_exclusions=tuple(decoded_exclusions),
            scenarios=tuple(decoded_scenarios),
        )
        if candidate.to_dict() != value:
            raise P4GateError("gate matrix specification is not canonical")
        return candidate


@dataclass(frozen=True, slots=True)
class P4GateMatrixPreflight:
    """Validated non-launch projection of all seventeen prepared scenarios."""

    matrix_spec_sha256: str
    manifest_sha256: str
    source_sha256: str
    source_size_bytes: int
    scenario_preflight_sha256: tuple[str, ...]

    def to_dict(self, spec: P4GateMatrixSpec) -> dict[str, object]:
        return {
            "schema": P4_GATE_MATRIX_PREFLIGHT_SCHEMA,
            "matrix_id": str(spec.matrix_id),
            "matrix_spec_sha256": self.matrix_spec_sha256,
            "manifest_sha256": self.manifest_sha256,
            "source": {
                "path": spec.source_path,
                "sha256": self.source_sha256,
                "size_bytes": self.source_size_bytes,
            },
            "scenarios": [
                {
                    "ordinal": scenario.ordinal,
                    "name": scenario.name,
                    "run_id": str(scenario.run_id),
                    "kill_at_checkpoint": scenario.kill_at_checkpoint,
                    "spec_sha256": scenario.spec_sha256,
                    "preflight_sha256": digest,
                }
                for scenario, digest in zip(
                    spec.scenarios,
                    self.scenario_preflight_sha256,
                    strict=True,
                )
            ],
        }


def _ensure_private_directory(path: Path) -> None:
    details = path.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise P4GateError(f"gate directory is not private: {path}")


def _config_text(
    root: Path,
    *,
    qemu_binary: Path,
    qemu_img_binary: Path,
) -> str:
    """Render the exact current host configuration for one disposable gate."""

    return "\n".join(
        (
            "[vm_lab]",
            'profile = "p4-disposable-physical-gate"',
            "",
            "[host]",
            f'qemu_binary = "{qemu_binary}"',
            f'qemu_img_binary = "{qemu_img_binary}"',
            "enable_kvm = false",
            "",
            "[daemon]",
            f'runtime_root = "{root / "runtime"}"',
            f'control_socket = "{root / "runtime" / "control.sock"}"',
            "request_timeout_seconds = 30.0",
            "",
            "[storage]",
            f'state_root = "{root / "state"}"',
            f'image_root = "{root / "images"}"',
            f'base_image = "{root / "images" / "base.qcow2"}"',
            f'backup_root = "{root / "backups"}"',
            f'log_root = "{root / "logs"}"',
            "",
            "[network]",
            'bind_host = "127.0.0.1"',
            "ssh_ports = [47000, 47063]",
            "agent_ports = [47100, 47163]",
            "vnc_ports = [6400, 6463]",
            "",
            "[agent]",
            "timeout_seconds = 30.0",
            "",
            "[security]",
            'credential_provider = "file"',
            f'secret_root = "{root / "secrets"}"',
            f'guest_agent_token_file = "{root / "secrets" / "agent.token"}"',
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
        )
    )


def prepare_gate_fixture(
    *,
    fixture_root: str | Path,
    manifest_path: str | Path,
    source_path: str | Path,
    production_exclusions: Iterable[GateProductionPath],
    qemu_binary: str = "qemu-system-x86_64",
    qemu_img_binary: str = "qemu-img",
    kill_at_checkpoint: str | None = None,
    run_id: UUID | None = None,
) -> Path:
    """Create one fresh private fixture and return its canonical spec path.

    This preparation never invokes qemu-system or qemu-img.  It validates the
    caller's immutable input paths, resolves executable locations, and creates
    only the six roots already owned by the live configuration model.
    """

    root = Path(os.fspath(fixture_root))
    if not root.is_absolute() or root == Path("/") or root.exists():
        raise P4GateError("fixture root must be a new absolute non-root path")
    selected_manifest = _canonical_existing(manifest_path, "manifest")
    selected_source = _canonical_existing(source_path, "source image")
    manifest = ImageManifest.from_json(selected_manifest.read_bytes())
    digest, size = _sha256_file(selected_source)
    if digest != manifest.sha256 or size != manifest.size_bytes:
        raise P4GateError("source image bytes disagree with the manifest")
    qemu_identity = _binary_identity(qemu_binary, "qemu-system")
    qemu_img_identity = _binary_identity(qemu_img_binary, "qemu-img")
    selected_run = uuid4() if run_id is None else run_id
    if not isinstance(selected_run, UUID) or selected_run.int == 0:
        raise P4GateError("run_id must be a nonnil UUID")
    exclusions = tuple(production_exclusions)
    if not exclusions:
        raise P4GateError("at least one production exclusion is required")

    try:
        root.mkdir(mode=0o700)
        for name in _ROOT_NAMES:
            (root / name).mkdir(mode=0o700)
        evidence = root / "runtime" / P4_GATE_EVIDENCE_DIRECTORY
        evidence.mkdir(mode=0o700)
        config_path = root / "runtime" / P4_GATE_CONFIG_NAME
        _write_exclusive(
            config_path,
            _config_text(
                root,
                qemu_binary=Path(str(qemu_identity["path"])),
                qemu_img_binary=Path(str(qemu_img_identity["path"])),
            ).encode("utf-8"),
        )
        spec = P4GateSpec(
            run_id=selected_run,
            fixture_root=os.fspath(root),
            config_path=os.fspath(config_path),
            manifest_path=os.fspath(selected_manifest),
            source_path=os.fspath(selected_source),
            declaration_request_id=uuid4(),
            kill_at_checkpoint=kill_at_checkpoint,
            production_exclusions=exclusions,
        )
        spec_path = root / "runtime" / P4_GATE_SPEC_NAME
        _write_exclusive(spec_path, spec.canonical_bytes)
        return spec_path
    except BaseException:
        # Preparation has not crossed a process or P3 ownership boundary.
        # Remove only the exact root created by this call.
        if root.exists():
            shutil.rmtree(root)
        raise


def preflight_gate(
    spec_path: str | Path,
    *,
    authorization: str | None = None,
    require_execution_authority: bool = False,
    require_fresh_runtime: bool = True,
) -> tuple[P4GateSpec, LabConfiguration, ImageManifest, P4GatePreflight]:
    """Validate the complete static gate boundary without launching a VM."""

    selected_spec_path = _canonical_existing(spec_path, "gate specification")
    spec = P4GateSpec.from_path(selected_spec_path)
    if not isinstance(require_fresh_runtime, bool):
        raise TypeError("require_fresh_runtime must be bool")
    root = Path(spec.fixture_root)
    _ensure_private_directory(root)
    for name in _ROOT_NAMES:
        _ensure_private_directory(root / name)
    _ensure_private_directory(spec.evidence_root)
    expected_top = set(_ROOT_NAMES)
    marker_path = root / FIXTURE_MARKER_NAME
    if not require_fresh_runtime and marker_path.is_file():
        expected_top.add(FIXTURE_MARKER_NAME)
    if {entry.name for entry in root.iterdir()} != expected_top:
        raise P4GateError("fixture top-level contents are not exact")
    if require_execution_authority and authorization != spec.execution_phrase:
        raise P4GateError("explicit disposable GATE-P4 authority is absent")
    configuration = load_configuration(Path(spec.config_path))
    config_digest, config_size = _sha256_file(Path(spec.config_path))
    expected_roots = {
        root / "state",
        root / "runtime",
        root / "logs",
        root / "images",
        root / "backups",
        root / "secrets",
    }
    actual_roots = {
        configuration.storage.state_root,
        configuration.daemon.runtime_root,
        configuration.storage.log_root,
        configuration.storage.image_root,
        configuration.storage.backup_root,
        configuration.security.secret_root,
    }
    if actual_roots != expected_roots:
        raise P4GateError("configuration roots do not equal the prepared fixture")
    if require_fresh_runtime:
        for forbidden in (
            configuration.storage.state_root / "registry.sqlite3",
            configuration.daemon.control_socket,
        ):
            if forbidden.exists() or forbidden.is_symlink():
                raise P4GateError("gate fixture already contains runtime state")
    manifest = ImageManifest.from_json(Path(spec.manifest_path).read_bytes())
    source_digest, source_size = _sha256_file(Path(spec.source_path))
    if (
        source_digest != manifest.sha256
        or source_size != manifest.size_bytes
    ):
        raise P4GateError("source image bytes disagree with the manifest")
    exclusions = tuple(
        ProductionExclusion.observe(item.label, item.path)
        for item in spec.production_exclusions
    )
    fixture_details = root.lstat()
    for exclusion in exclusions:
        if (
            root.is_relative_to(exclusion.path)
            or exclusion.path.is_relative_to(root)
            or (
                exclusion.device_id == fixture_details.st_dev
                and exclusion.inode == fixture_details.st_ino
            )
        ):
            raise P4GateError(
                f"fixture overlaps production exclusion {exclusion.label}"
            )
    qemu = _binary_identity(configuration.host.qemu_binary, "qemu-system")
    qemu_img = _binary_identity(
        configuration.host.qemu_img_binary,
        "qemu-img",
    )
    preflight = P4GatePreflight(
        spec_sha256=hashlib.sha256(spec.canonical_bytes).hexdigest(),
        config_sha256=config_digest,
        config_size_bytes=config_size,
        manifest_sha256=manifest.manifest_sha256,
        source_sha256=source_digest,
        source_size_bytes=source_size,
        qemu=qemu,
        qemu_img=qemu_img,
        exclusions=exclusions,
    )
    return spec, configuration, manifest, preflight


def prepare_gate_matrix(
    *,
    matrix_root: str | Path,
    manifest_path: str | Path,
    source_path: str | Path,
    production_exclusions: Iterable[GateProductionPath],
    qemu_binary: str = "qemu-system-x86_64",
    qemu_img_binary: str = "qemu-img",
    matrix_id: UUID | None = None,
) -> Path:
    """Prepare one success fixture plus all sixteen checkpoint fixtures.

    Preparation performs only exact source/executable observation and private
    directory/spec creation.  It never presents an execution token, opens the
    daemon registry, invokes qemu-img mutation, or launches qemu-system.
    """

    root = Path(os.fspath(matrix_root))
    if not root.is_absolute() or root == Path("/") or root.exists():
        raise P4GateError(
            "matrix root must be a new absolute non-root path"
        )
    selected_matrix_id = uuid4() if matrix_id is None else matrix_id
    if (
        not isinstance(selected_matrix_id, UUID)
        or selected_matrix_id.int == 0
    ):
        raise P4GateError("matrix_id must be a nonnil UUID")
    exclusions = tuple(production_exclusions)
    if (
        not exclusions
        or any(not isinstance(item, GateProductionPath) for item in exclusions)
    ):
        raise P4GateError(
            "matrix requires explicit production exclusions"
        )
    selected_manifest = _canonical_existing(manifest_path, "manifest")
    selected_source = _canonical_existing(source_path, "source image")
    try:
        root.mkdir(mode=0o700)
        scenario_parent = root / "scenarios"
        scenario_parent.mkdir(mode=0o700)
        scenarios: list[P4GateMatrixScenario] = []
        for ordinal in range(len(CHECKPOINT_ORDER) + 1):
            checkpoint = (
                None if ordinal == 0 else CHECKPOINT_ORDER[ordinal - 1]
            )
            name = (
                "00-success"
                if checkpoint is None
                else f"{ordinal:02d}-{checkpoint}"
            )
            spec_path = prepare_gate_fixture(
                fixture_root=scenario_parent / f"{ordinal:02d}",
                manifest_path=selected_manifest,
                source_path=selected_source,
                production_exclusions=exclusions,
                qemu_binary=qemu_binary,
                qemu_img_binary=qemu_img_binary,
                kill_at_checkpoint=checkpoint,
            )
            scenario_spec = P4GateSpec.from_path(spec_path)
            scenarios.append(
                P4GateMatrixScenario(
                    ordinal=ordinal,
                    name=name,
                    kill_at_checkpoint=checkpoint,
                    spec_path=os.fspath(spec_path),
                    run_id=scenario_spec.run_id,
                    spec_sha256=hashlib.sha256(
                        scenario_spec.canonical_bytes
                    ).hexdigest(),
                )
            )
        matrix = P4GateMatrixSpec(
            matrix_id=selected_matrix_id,
            matrix_root=os.fspath(root),
            manifest_path=os.fspath(selected_manifest),
            source_path=os.fspath(selected_source),
            production_exclusions=exclusions,
            scenarios=tuple(scenarios),
        )
        path = root / P4_GATE_MATRIX_SPEC_NAME
        _write_exclusive(path, matrix.canonical_bytes)
        return path
    except BaseException:
        if root.exists():
            shutil.rmtree(root)
        raise


def preflight_gate_matrix(
    matrix_spec_path: str | Path,
    *,
    authorization: str | None = None,
    require_execution_authority: bool = False,
    allow_execution_state: bool = False,
) -> tuple[P4GateMatrixSpec, ImageManifest, P4GateMatrixPreflight]:
    """Validate the full prepared matrix without creating a process."""

    if not isinstance(allow_execution_state, bool):
        raise TypeError("allow_execution_state must be bool")
    selected_path = _canonical_existing(
        matrix_spec_path,
        "gate matrix specification",
    )
    matrix = P4GateMatrixSpec.from_path(selected_path)
    root = Path(matrix.matrix_root)
    if selected_path != root / P4_GATE_MATRIX_SPEC_NAME:
        raise P4GateError("gate matrix specification has the wrong owner")
    _ensure_private_directory(root)
    _ensure_private_directory(root / "scenarios")
    allowed_children = {"scenarios", P4_GATE_MATRIX_SPEC_NAME}
    if allow_execution_state:
        allowed_children.update(
            {
                P4_GATE_MATRIX_PROGRESS_NAME,
                P4_GATE_MATRIX_FAILURE_NAME,
                P4_GATE_MATRIX_RESULT_NAME,
            }
        )
    actual_children = {entry.name for entry in root.iterdir()}
    if (
        actual_children - allowed_children
        or not {"scenarios", P4_GATE_MATRIX_SPEC_NAME}.issubset(
            actual_children
        )
    ):
        raise P4GateError("gate matrix root contents are not exact")
    if (
        require_execution_authority
        and authorization != matrix.execution_phrase
    ):
        raise P4GateError("explicit full GATE-P4 matrix authority is absent")
    exclusions = tuple(
        ProductionExclusion.observe(item.label, item.path)
        for item in matrix.production_exclusions
    )
    root_details = root.lstat()
    for exclusion in exclusions:
        if (
            root.is_relative_to(exclusion.path)
            or exclusion.path.is_relative_to(root)
            or (
                exclusion.device_id == root_details.st_dev
                and exclusion.inode == root_details.st_ino
            )
        ):
            raise P4GateError(
                f"matrix overlaps production exclusion {exclusion.label}"
            )
    manifest = ImageManifest.from_json(
        Path(matrix.manifest_path).read_bytes()
    )
    source_digest, source_size = _sha256_file(Path(matrix.source_path))
    if (
        source_digest != manifest.sha256
        or source_size != manifest.size_bytes
    ):
        raise P4GateError("matrix source bytes disagree with the manifest")
    actual_scenario_names = {
        entry.name for entry in (root / "scenarios").iterdir()
    }
    expected_scenario_names = {
        f"{item.ordinal:02d}" for item in matrix.scenarios
    }
    if actual_scenario_names != expected_scenario_names:
        raise P4GateError("matrix scenario directory set is not exact")
    preflight_digests: list[str] = []
    first_qemu: dict[str, object] | None = None
    first_qemu_img: dict[str, object] | None = None
    for scenario in matrix.scenarios:
        scenario_spec = P4GateSpec.from_path(scenario.spec_path)
        if (
            hashlib.sha256(scenario_spec.canonical_bytes).hexdigest()
            != scenario.spec_sha256
            or scenario_spec.run_id != scenario.run_id
            or scenario_spec.kill_at_checkpoint
            != scenario.kill_at_checkpoint
            or scenario_spec.manifest_path != matrix.manifest_path
            or scenario_spec.source_path != matrix.source_path
            or scenario_spec.production_exclusions
            != matrix.production_exclusions
        ):
            raise P4GateError(
                "matrix scenario specification changed after preparation"
            )
        _, _, scenario_manifest, scenario_preflight = preflight_gate(
            scenario.spec_path,
            require_fresh_runtime=not allow_execution_state,
        )
        if scenario_manifest != manifest:
            raise P4GateError("matrix scenario manifest identity changed")
        if first_qemu is None:
            first_qemu = scenario_preflight.qemu
            first_qemu_img = scenario_preflight.qemu_img
        elif (
            scenario_preflight.qemu != first_qemu
            or scenario_preflight.qemu_img != first_qemu_img
        ):
            raise P4GateError(
                "matrix scenarios do not share executable authority"
            )
        preflight_digests.append(
            hashlib.sha256(
                _canonical_bytes(scenario_preflight.to_dict(scenario_spec))
            ).hexdigest()
        )
    preflight = P4GateMatrixPreflight(
        matrix_spec_sha256=hashlib.sha256(
            matrix.canonical_bytes
        ).hexdigest(),
        manifest_sha256=manifest.manifest_sha256,
        source_sha256=source_digest,
        source_size_bytes=source_size,
        scenario_preflight_sha256=tuple(preflight_digests),
    )
    return matrix, manifest, preflight


def _request(
    operation: str,
    payload: dict[str, object],
    *,
    request_id: UUID | None = None,
    vm_id: UUID | None = None,
    generation: int | None = None,
) -> ControlRequest:
    candidate = ControlRequest(
        schema_version=CURRENT_SCHEMA_VERSION,
        protocol_version=CURRENT_PROTOCOL_VERSION,
        request_id=uuid4() if request_id is None else request_id,
        correlation_id=uuid4(),
        vm_id=vm_id,
        boot_id=None,
        generation=generation,
        timestamp=datetime.now(timezone.utc),
        operation=operation,
        payload=payload,
    )
    return ControlRequest.from_json(candidate.to_json())


def _success(value: ControlResponse | ErrorEnvelope, operation: str) -> ControlResponse:
    if not isinstance(value, ControlResponse):
        raise P4GateError(
            f"{operation} failed at the daemon-owned service boundary: "
            f"{value.to_dict()}"
        )
    return value


def _transition_evidence(
    record: VMRecord,
    facts: dict[str, str],
) -> TransitionEvidence:
    return TransitionEvidence(
        evidence_id=uuid4(),
        observed_at=record.updated_at + timedelta(seconds=1),
        facts={"intent_digest": record.intent_digest, **facts},
        vm_id=record.vm_id,
        generation=record.generation,
        boot_id=None,
        intent_digest=record.intent_digest,
    )


def _provisioned(record: VMRecord, image_sha256: str, disk_id: UUID) -> VMRecord:
    provisioning = record.transition(
        VMState.PROVISIONING,
        _transition_evidence(
            record,
            {"operation_id": str(uuid4())},
        ),
    )
    return provisioning.transition(
        VMState.PROVISIONED,
        _transition_evidence(
            provisioning,
            {
                "image_sha256": image_sha256,
                "operation_id": str(uuid4()),
                "storage_id": str(disk_id),
            },
        ),
    )


def _write_fixture_marker(
    spec: P4GateSpec,
    *,
    vm_id: UUID,
    base_sha256: str,
) -> Path:
    marker = Path(spec.fixture_root) / FIXTURE_MARKER_NAME
    _write_exclusive(
        marker,
        _canonical_bytes(
            {
                "schema_version": FIXTURE_MARKER_SCHEMA_VERSION,
                "run_id": str(spec.run_id),
                "vm_id": str(vm_id),
                "created_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "owner": f"uid:{os.geteuid()}",
                "base_image_sha256": base_sha256,
                "permitted_cleanup_root": spec.fixture_root,
            }
        ),
    )
    return marker


def _evidence_path(spec: P4GateSpec, name: str) -> Path:
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or name in {".", ".."}
    ):
        raise P4GateError("gate evidence filename is invalid")
    return spec.evidence_root / name


class P4GateActivation:
    """One launch activation executed inside the ordinary daemon owner."""

    def __init__(
        self,
        spec: P4GateSpec,
        manifest: ImageManifest,
        preflight: P4GatePreflight,
    ) -> None:
        self._spec = spec
        self._manifest = manifest
        self._preflight = preflight

    def checkpoint_observer(self, operation_id: UUID, checkpoint: str) -> None:
        """Persist the durable checkpoint observation before optional SIGKILL."""

        if checkpoint not in CHECKPOINT_ORDER:
            raise P4GateError("runtime emitted an unknown launch checkpoint")
        path = _evidence_path(
            self._spec,
            f"checkpoint-{CHECKPOINT_ORDER.index(checkpoint):02d}-{checkpoint}.json",
        )
        _write_exclusive(
            path,
            _canonical_bytes(
                {
                    "schema": P4_GATE_CHECKPOINT_SCHEMA,
                    "run_id": str(self._spec.run_id),
                    "operation_id": str(operation_id),
                    "checkpoint": checkpoint,
                    "ordinal": CHECKPOINT_ORDER.index(checkpoint),
                    "worker_pid": os.getpid(),
                }
            ),
        )
        if checkpoint == self._spec.kill_at_checkpoint:
            os.kill(os.getpid(), 9)
            raise AssertionError("SIGKILL returned")

    def __call__(self, context: DaemonRuntimeContext) -> None:
        """Materialize P3 authority and consume it through the P4 runtime."""

        if context.configuration.source_path is None:
            raise P4GateError("daemon configuration has no source identity")
        config_digest, config_size = _sha256_file(
            context.configuration.source_path
        )
        if (
            config_digest != self._preflight.config_sha256
            or config_size != self._preflight.config_size_bytes
        ):
            raise P4GateError("daemon configuration changed after preflight")
        current_exclusions = tuple(
            ProductionExclusion.observe(item.label, item.path)
            for item in self._spec.production_exclusions
        )
        if current_exclusions != self._preflight.exclusions:
            raise P4GateError("production exclusion identity changed after preflight")
        if (
            context.incomplete_recovery
            or context.completed_reconciliation
            or context.registry.list_vms(active_only=False)
        ):
            raise P4GateError("launch activation requires a fresh registry")
        peer = PeerCredentials(
            pid=os.getpid(),
            uid=os.geteuid(),
            gid=os.getegid(),
        )
        imported = _success(
            context.service(
                _request(
                    "storage.import_base",
                    {
                        "manifest": self._manifest.to_dict(),
                        "source_path": self._spec.source_path,
                    },
                ),
                peer,
            ),
            "storage.import_base",
        )
        declared = _success(
            context.service(
                _request(
                    "registry.declare",
                    {
                        "memory_mib": 4096,
                        "name": f"gate-p4-{str(self._spec.run_id)[:8]}",
                        "vcpus": 2,
                    },
                    request_id=self._spec.declaration_request_id,
                ),
                peer,
            ),
            "registry.declare",
        )
        vm_id = UUID(str(declared.result["vm_id"]))
        generation = int(declared.result["generation"])
        revision = int(declared.result["revision"])
        _success(
            context.service(
                _request(
                    "registry.lease",
                    {"expected_revision": revision},
                    vm_id=vm_id,
                    generation=generation,
                ),
                peer,
            ),
            "registry.lease",
        )
        _success(
            context.service(
                _request(
                    "storage.create_overlay",
                    {
                        "base_image_id": str(
                            imported.result["registry_image_id"]
                        ),
                        "expected_revision": revision,
                        "virtual_size_bytes": P4_GATE_VIRTUAL_SIZE_BYTES,
                    },
                    vm_id=vm_id,
                    generation=generation,
                ),
                peer,
            ),
            "storage.create_overlay",
        )
        stored = context.registry.get_vm(vm_id)
        disks = context.registry.list_disks(
            vm_id=vm_id,
            active_only=True,
            materialized_only=True,
        )
        if stored is None or len(disks) != 1:
            raise P4GateError("P3 owner did not publish one exact VM disk")
        disk = disks[0]
        provisioned = _provisioned(
            stored.record,
            self._manifest.sha256,
            disk.disk_id,
        )
        with context.registry.write_transaction() as transaction:
            transaction.compare_and_swap_vm(
                vm_id,
                stored.revision,
                provisioned,
            )
        marker = _write_fixture_marker(
            self._spec,
            vm_id=vm_id,
            base_sha256=self._manifest.sha256,
        )
        permit = mint_disposable_launch_permit(
            context.configuration.host,
            fixture_root=self._spec.fixture_root,
            run_id=self._spec.run_id,
            vm_id=vm_id,
            generation=generation,
            disk_id=disk.disk_id,
            disk_path=disk.path,
            base_sha256=self._manifest.sha256,
            production_exclusions=self._preflight.exclusions,
        )
        launch = context.runtime.launch(vm_id, permit=permit)
        _write_exclusive(
            _evidence_path(self._spec, "launch.json"),
            _canonical_bytes(
                {
                    "schema": P4_GATE_LAUNCH_SCHEMA,
                    "run_id": str(self._spec.run_id),
                    "fixture_marker": os.fspath(marker),
                    "preflight": self._preflight.to_dict(self._spec),
                    "launch": launch.to_dict(),
                }
            ),
        )


class P4GateRecoveryActivation:
    """Record ordinary daemon recovery/reconciliation after one worker exit."""

    def __init__(
        self,
        spec: P4GateSpec,
        *,
        evidence_name: str = "recovery.json",
    ) -> None:
        self._spec = spec
        self._evidence_name = evidence_name
        _evidence_path(spec, evidence_name)

    def __call__(self, context: DaemonRuntimeContext) -> None:
        _replace_canonical(
            _evidence_path(self._spec, self._evidence_name),
            {
                "schema": P4_GATE_RECOVERY_SCHEMA,
                "run_id": str(self._spec.run_id),
                "incomplete_recovery": [
                    item.to_dict() for item in context.incomplete_recovery
                ],
                "completed_reconciliation": [
                    item.to_dict()
                    for item in context.completed_reconciliation
                ],
                "live_vm_ids": sorted(
                    str(item) for item in context.runtime.live_vm_ids
                ),
            },
        )


def serialize_launch_result(result: RuntimeLaunchResult) -> dict[str, object]:
    """Public helper retained for gate scripts and exact result-schema tests."""

    if not isinstance(result, RuntimeLaunchResult):
        raise TypeError("result must be RuntimeLaunchResult")
    return result.to_dict()


def serialize_recovery_results(
    values: Iterable[RuntimeRecoveryResult],
) -> list[dict[str, object]]:
    """Serialize daemon recovery decisions without reaching into internals."""

    try:
        selected = tuple(values)
    except TypeError as exc:
        raise TypeError("values must be iterable") from exc
    if any(not isinstance(item, RuntimeRecoveryResult) for item in selected):
        raise TypeError("values must contain RuntimeRecoveryResult")
    return [item.to_dict() for item in selected]


def validate_sealed_gate_result(
    spec_path: str | Path,
    result_path: str | Path,
) -> dict[str, object]:
    """Rehash every sealed scenario record and reject partial/tampered bundles."""

    spec = P4GateSpec.from_path(spec_path)
    selected = _canonical_existing(result_path, "gate result")
    if selected != spec.evidence_root / "result.json":
        raise P4GateError("gate result is outside the prepared evidence root")
    value = _read_canonical_object(selected, maximum=_MAX_SPEC_BYTES)
    if set(value) != {
        "schema",
        "run_id",
        "status",
        "cleanup_scope",
        "kill_at_checkpoint",
        "worker",
        "recovery_worker",
        "records",
    } or value["schema"] != P4_GATE_RESULT_SCHEMA:
        raise P4GateError("gate result schema is invalid")
    if value["run_id"] != str(spec.run_id):
        raise P4GateError("gate result run identity changed")
    allowed_statuses = {
        "completed_and_cleaned",
        "checkpoint_cleaned",
        "checkpoint_adopted_and_cleaned",
    }
    if value["status"] not in allowed_statuses:
        raise P4GateError("gate result status is not a closed physical outcome")
    if value["cleanup_scope"] != "runtime-process-only;fixture-retained":
        raise P4GateError("gate result cleanup scope is not explicit")
    if value["kill_at_checkpoint"] != spec.kill_at_checkpoint:
        raise P4GateError("gate result checkpoint target changed")
    for key in ("worker", "recovery_worker"):
        worker = value[key]
        if (
            not isinstance(worker, dict)
            or set(worker) != {
                "returncode",
                "stdout_sha256",
                "stderr_sha256",
            }
            or not isinstance(worker["returncode"], int)
            or isinstance(worker["returncode"], bool)
        ):
            raise P4GateError("gate worker result is invalid")
        for digest_name in ("stdout_sha256", "stderr_sha256"):
            digest = worker[digest_name]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(item not in "0123456789abcdef" for item in digest)
            ):
                raise P4GateError("gate worker digest is invalid")
    records = value["records"]
    if not isinstance(records, list) or not records:
        raise P4GateError("gate result has no sealed evidence records")
    expected_names: set[str] = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "size_bytes", "sha256"}
            or not isinstance(record["name"], str)
            or not record["name"]
            or "/" in record["name"]
            or record["name"] in {".", "..", "result.json"}
            or not isinstance(record["size_bytes"], int)
            or isinstance(record["size_bytes"], bool)
            or record["size_bytes"] < 0
            or not isinstance(record["sha256"], str)
            or len(record["sha256"]) != 64
            or any(item not in "0123456789abcdef" for item in record["sha256"])
        ):
            raise P4GateError("gate evidence manifest entry is invalid")
        name = record["name"]
        if name in expected_names:
            raise P4GateError("gate evidence manifest repeats a record")
        expected_names.add(name)
        evidence_path = spec.evidence_root / name
        try:
            details = evidence_path.lstat()
            raw = evidence_path.read_bytes()
        except OSError as exc:
            raise P4GateError("sealed gate evidence disappeared") from exc
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or len(raw) != record["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise P4GateError("sealed gate evidence was replaced or modified")
    actual_names = {
        item.name
        for item in spec.evidence_root.iterdir()
        if item.name != "result.json"
    }
    if actual_names != expected_names:
        raise P4GateError("gate evidence directory disagrees with its manifest")
    required_names = {
        "preflight.json",
        "foreign-process-before.json",
        "foreign-process-after.json",
        "recovery.json",
        "result.log",
        "result.md",
    }
    checkpoint_names = {
        (
            f"checkpoint-{ordinal:02d}-{checkpoint}.json"
        )
        for ordinal, checkpoint in enumerate(CHECKPOINT_ORDER)
    }
    if spec.kill_at_checkpoint is None:
        required_names.update(
            {
                "launch.json",
                "cleanup.json",
                "qemu-stderr.log",
                "serial-console.log",
            }
        )
        required_names.update(checkpoint_names)
        if value["status"] != "completed_and_cleaned":
            raise P4GateError("success scenario has the wrong closed status")
    else:
        target = CHECKPOINT_ORDER.index(spec.kill_at_checkpoint)
        required_names.update(
            (
                f"checkpoint-{ordinal:02d}-{checkpoint}.json"
                for ordinal, checkpoint in enumerate(CHECKPOINT_ORDER)
                if ordinal <= target
            )
        )
        forbidden_later = {
            (
                f"checkpoint-{ordinal:02d}-{checkpoint}.json"
            )
            for ordinal, checkpoint in enumerate(CHECKPOINT_ORDER)
            if ordinal > target
        }
        if expected_names & forbidden_later:
            raise P4GateError(
                "checkpoint scenario contains impossible later evidence"
            )
        if value["status"] == "completed_and_cleaned":
            raise P4GateError("checkpoint scenario has a success-only status")
        if value["worker"]["returncode"] != -9:  # type: ignore[index]
            raise P4GateError("checkpoint worker was not killed by SIGKILL")
        if value["status"] == "checkpoint_adopted_and_cleaned":
            required_names.update(
                {
                    "cleanup.json",
                    "post-cleanup-recovery.json",
                }
            )
    if not required_names.issubset(expected_names):
        raise P4GateError("gate result omits required physical evidence")
    return value


def validate_sealed_gate_matrix_result(
    matrix_spec_path: str | Path,
    result_path: str | Path,
) -> dict[str, object]:
    """Validate one passed matrix and every underlying physical scenario."""

    matrix, manifest, preflight = preflight_gate_matrix(
        matrix_spec_path,
        allow_execution_state=True,
    )
    selected = _canonical_existing(result_path, "gate matrix result")
    if selected != matrix.result_path:
        raise P4GateError("gate matrix result is outside its exact owner")
    value = _read_canonical_object(selected, maximum=_MAX_SPEC_BYTES)
    if set(value) != {
        "schema",
        "matrix_id",
        "matrix_spec_sha256",
        "matrix_preflight_sha256",
        "manifest_sha256",
        "source_sha256",
        "status",
        "scenario_count",
        "scenarios",
    } or value["schema"] != P4_GATE_MATRIX_RESULT_SCHEMA:
        raise P4GateError("gate matrix result schema is invalid")
    if (
        value["matrix_id"] != str(matrix.matrix_id)
        or value["matrix_spec_sha256"]
        != hashlib.sha256(matrix.canonical_bytes).hexdigest()
        or value["matrix_preflight_sha256"]
        != hashlib.sha256(
            _canonical_bytes(preflight.to_dict(matrix))
        ).hexdigest()
        or value["manifest_sha256"] != manifest.manifest_sha256
        or value["source_sha256"] != preflight.source_sha256
        or value["status"] != "passed"
        or value["scenario_count"] != len(matrix.scenarios)
    ):
        raise P4GateError("gate matrix result identity or verdict is invalid")
    scenarios = value["scenarios"]
    if (
        not isinstance(scenarios, list)
        or len(scenarios) != len(matrix.scenarios)
    ):
        raise P4GateError("gate matrix result is not complete")
    for expected, observed in zip(matrix.scenarios, scenarios, strict=True):
        if not isinstance(observed, dict) or set(observed) != {
            "ordinal",
            "name",
            "run_id",
            "kill_at_checkpoint",
            "status",
            "result_path",
            "result_sha256",
        }:
            raise P4GateError("gate matrix scenario result is invalid")
        scenario_result_path = (
            Path(expected.spec_path).parent
            / P4_GATE_EVIDENCE_DIRECTORY
            / "result.json"
        )
        if (
            observed["ordinal"] != expected.ordinal
            or observed["name"] != expected.name
            or observed["run_id"] != str(expected.run_id)
            or observed["kill_at_checkpoint"]
            != expected.kill_at_checkpoint
            or observed["result_path"] != os.fspath(scenario_result_path)
        ):
            raise P4GateError("gate matrix scenario identity changed")
        scenario_result = validate_sealed_gate_result(
            expected.spec_path,
            scenario_result_path,
        )
        raw = scenario_result_path.read_bytes()
        if (
            observed["status"] != scenario_result["status"]
            or observed["result_sha256"]
            != hashlib.sha256(raw).hexdigest()
        ):
            raise P4GateError("gate matrix scenario evidence hash changed")
    return value


__all__ = [
    "GateProductionPath",
    "P4_GATE_AUTHORIZATION_PREFIX",
    "P4_GATE_CHECKPOINT_SCHEMA",
    "P4_GATE_CONFIG_NAME",
    "P4_GATE_EVIDENCE_DIRECTORY",
    "P4_GATE_LAUNCH_SCHEMA",
    "P4_GATE_MATRIX_AUTHORIZATION_PREFIX",
    "P4_GATE_MATRIX_FAILURE_NAME",
    "P4_GATE_MATRIX_PREFLIGHT_SCHEMA",
    "P4_GATE_MATRIX_PROGRESS_NAME",
    "P4_GATE_MATRIX_RESULT_NAME",
    "P4_GATE_MATRIX_RESULT_SCHEMA",
    "P4_GATE_MATRIX_SPEC_NAME",
    "P4_GATE_MATRIX_SPEC_SCHEMA",
    "P4_GATE_PREFLIGHT_SCHEMA",
    "P4_GATE_RECOVERY_SCHEMA",
    "P4_GATE_RESULT_SCHEMA",
    "P4_GATE_SPEC_NAME",
    "P4_GATE_SPEC_SCHEMA",
    "P4GateActivation",
    "P4GateError",
    "P4GateMatrixPreflight",
    "P4GateMatrixScenario",
    "P4GateMatrixSpec",
    "P4GatePreflight",
    "P4GateRecoveryActivation",
    "P4GateSpec",
    "preflight_gate",
    "preflight_gate_matrix",
    "prepare_gate_fixture",
    "prepare_gate_matrix",
    "serialize_launch_result",
    "serialize_recovery_results",
    "validate_sealed_gate_result",
    "validate_sealed_gate_matrix_result",
]
