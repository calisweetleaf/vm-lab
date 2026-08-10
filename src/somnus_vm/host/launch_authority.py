"""Fail-closed disposable-fixture authority for the physical P4 launch gate.

Source: docs/DISPOSABLE_FIXTURE_POLICY.md and TASK-P4-019 safety boundary.
Integrated: 2026-08-07
Purpose: Mints a one-shot permit only for an explicitly marked, private,
    non-production fixture topology before a QEMU runtime may create launch
    paths or release a child process.
IO: Reads filesystem metadata and one bounded fixture marker; never creates,
    deletes, mounts, launches, or signals a process.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import stat
import threading
from typing import Final, Iterable
from uuid import UUID, uuid4

from ..config import HostConfiguration


FIXTURE_MARKER_NAME: Final[str] = ".somnus-disposable-fixture.json"
FIXTURE_MARKER_SCHEMA_VERSION: Final[int] = 1
_MAX_MARKER_BYTES: Final[int] = 65_536
_REQUIRED_MARKER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "base_image_sha256",
        "created_at",
        "owner",
        "permitted_cleanup_root",
        "run_id",
        "schema_version",
        "vm_id",
    }
)


class LaunchAuthorityError(ValueError):
    """Raised when a P4 launch target lacks disposable-fixture authority."""


LAUNCH_AUTHORITY_RECEIPT_SCHEMA: Final[str] = (
    "somnus.disposable-launch-authority.receipt.v1"
)


def _strict_uuid(value: UUID | str, label: str) -> UUID:
    """Parse one non-nil UUID without accepting ambiguous values."""

    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise LaunchAuthorityError(f"{label} must be a UUID") from exc
    else:
        raise LaunchAuthorityError(f"{label} must be a UUID")
    if parsed.int == 0:
        raise LaunchAuthorityError(f"{label} must not be nil")
    return parsed


def _strict_sha256(value: str, label: str) -> str:
    """Validate one lowercase SHA-256 digest."""

    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LaunchAuthorityError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_generation(value: int) -> int:
    """Validate one nonnegative immutable VM generation."""

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LaunchAuthorityError("generation must be a nonnegative integer")
    return value


def _canonical_existing_path(value: str | Path, label: str) -> Path:
    """Return one existing lexical-canonical path without accepting symlink text."""

    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise LaunchAuthorityError(f"{label} must be a filesystem path") from exc
    if (
        not isinstance(text, str)
        or not text.startswith("/")
        or text == "/"
        or text.startswith("//")
        or "\x00" in text
        or os.path.normpath(text) != text
    ):
        raise LaunchAuthorityError(f"{label} must be a canonical absolute non-root path")
    path = Path(text)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LaunchAuthorityError(f"{label} cannot be resolved") from exc
    if resolved != path:
        raise LaunchAuthorityError(f"{label} must not traverse a symlink")
    return path


def _canonical_path_text(value: str | Path, label: str) -> str:
    """Validate canonical absolute path text without requiring it to exist."""

    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise LaunchAuthorityError(f"{label} must be a filesystem path") from exc
    if (
        not isinstance(text, str)
        or not text.startswith("/")
        or text == "/"
        or text.startswith("//")
        or "\x00" in text
        or os.path.normpath(text) != text
    ):
        raise LaunchAuthorityError(
            f"{label} must be a canonical absolute non-root path"
        )
    return text


def _path_is_within(path: Path, root: Path) -> bool:
    """Return whether a canonical path is equal to or beneath a canonical root."""

    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _mount_points() -> frozenset[Path]:
    """Read Linux mount points so a fixture cannot contain a mount escape."""

    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LaunchAuthorityError("Linux mount topology cannot be inspected") from exc
    points: set[Path] = set()
    for line in lines:
        fields = line.split(" ")
        if len(fields) < 6:
            raise LaunchAuthorityError("Linux mount topology is malformed")
        escaped = fields[4]
        decoded = (
            escaped.replace("\\040", " ")
            .replace("\\011", "\t")
            .replace("\\012", "\n")
            .replace("\\134", "\\")
        )
        if not decoded.startswith("/"):
            raise LaunchAuthorityError("Linux mount topology has a relative mount")
        points.add(Path(decoded))
    return frozenset(points)


@dataclass(frozen=True, slots=True)
class PathIdentity:
    """Exact private filesystem identity retained by a disposable permit."""

    role: str
    path: Path
    device_id: int
    inode: int
    owner_uid: int
    mode: int
    link_count: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class ProductionExclusion:
    """Operator-supplied production path and identity that a fixture may not touch."""

    label: str
    path: Path
    device_id: int
    inode: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.label, str)
            or not self.label
            or len(self.label.encode("utf-8")) > 128
        ):
            raise LaunchAuthorityError("production exclusion label is invalid")
        try:
            text = os.fspath(self.path)
        except TypeError as exc:
            raise LaunchAuthorityError("production exclusion path is invalid") from exc
        if (
            not isinstance(text, str)
            or not text.startswith("/")
            or text == "/"
            or text.startswith("//")
            or os.path.normpath(text) != text
        ):
            raise LaunchAuthorityError(
                "production exclusion path must be canonical and absolute"
            )
        if (
            not isinstance(self.device_id, int)
            or isinstance(self.device_id, bool)
            or self.device_id < 0
            or not isinstance(self.inode, int)
            or isinstance(self.inode, bool)
            or self.inode < 1
        ):
            raise LaunchAuthorityError("production exclusion identity is invalid")

    @classmethod
    def observe(cls, label: str, path: str | Path) -> "ProductionExclusion":
        """Capture one explicit existing production exclusion without mutation."""

        selected = _canonical_existing_path(path, "production exclusion path")
        try:
            details = selected.lstat()
        except OSError as exc:
            raise LaunchAuthorityError(
                "production exclusion path cannot be inspected"
            ) from exc
        return cls(label, selected, details.st_dev, details.st_ino)


@dataclass(frozen=True, slots=True)
class DisposableLaunchReceipt:
    """Canonical durable receipt emitted only after one permit is consumed."""

    permit_id: UUID
    run_id: UUID
    vm_id: UUID
    generation: int
    disk_id: UUID
    disk_path: str
    base_sha256: str
    fixture_root: str
    fixture_device_id: int
    fixture_inode: int
    marker_sha256: str
    production_exclusions: tuple[ProductionExclusion, ...]

    def __post_init__(self) -> None:
        for field_name in ("permit_id", "run_id", "vm_id", "disk_id"):
            object.__setattr__(
                self,
                field_name,
                _strict_uuid(getattr(self, field_name), field_name),
            )
        if len({self.permit_id, self.run_id, self.vm_id, self.disk_id}) != 4:
            raise LaunchAuthorityError(
                "disposable launch receipt identifiers cannot be reused"
            )
        _strict_generation(self.generation)
        object.__setattr__(
            self,
            "disk_path",
            _canonical_path_text(self.disk_path, "disk path"),
        )
        object.__setattr__(
            self,
            "fixture_root",
            _canonical_path_text(self.fixture_root, "fixture root"),
        )
        _strict_sha256(self.base_sha256, "base_sha256")
        _strict_sha256(self.marker_sha256, "marker_sha256")
        for label, value, minimum in (
            ("fixture device_id", self.fixture_device_id, 0),
            ("fixture inode", self.fixture_inode, 1),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
            ):
                raise LaunchAuthorityError(f"{label} is invalid")
        if (
            not isinstance(self.production_exclusions, tuple)
            or not self.production_exclusions
            or any(
                not isinstance(item, ProductionExclusion)
                for item in self.production_exclusions
            )
        ):
            raise LaunchAuthorityError(
                "receipt requires explicit production exclusions"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical persisted launch-authority evidence."""

        return {
            "schema": LAUNCH_AUTHORITY_RECEIPT_SCHEMA,
            "permit_id": str(self.permit_id),
            "run_id": str(self.run_id),
            "vm_id": str(self.vm_id),
            "generation": self.generation,
            "disk_id": str(self.disk_id),
            "disk_path": self.disk_path,
            "base_sha256": self.base_sha256,
            "fixture_root": {
                "path": self.fixture_root,
                "device_id": self.fixture_device_id,
                "inode": self.fixture_inode,
            },
            "marker_sha256": self.marker_sha256,
            "production_exclusions": [
                {
                    "label": item.label,
                    "path": os.fspath(item.path),
                    "device_id": item.device_id,
                    "inode": item.inode,
                }
                for item in self.production_exclusions
            ],
        }

    @property
    def evidence_sha256(self) -> str:
        """Hash the exact canonical receipt stored by the launch journal."""

        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> "DisposableLaunchReceipt":
        """Decode one exact canonical journal receipt without filesystem IO."""

        if not isinstance(value, dict) or set(value) != {
            "schema",
            "permit_id",
            "run_id",
            "vm_id",
            "generation",
            "disk_id",
            "disk_path",
            "base_sha256",
            "fixture_root",
            "marker_sha256",
            "production_exclusions",
        }:
            raise LaunchAuthorityError("launch-authority receipt schema is invalid")
        if value["schema"] != LAUNCH_AUTHORITY_RECEIPT_SCHEMA:
            raise LaunchAuthorityError(
                "launch-authority receipt version is unsupported"
            )
        fixture = value["fixture_root"]
        exclusions = value["production_exclusions"]
        if (
            not isinstance(fixture, dict)
            or set(fixture) != {"path", "device_id", "inode"}
            or not isinstance(exclusions, list)
            or not exclusions
        ):
            raise LaunchAuthorityError("launch-authority receipt body is invalid")
        decoded_exclusions: list[ProductionExclusion] = []
        for item in exclusions:
            if not isinstance(item, dict) or set(item) != {
                "label",
                "path",
                "device_id",
                "inode",
            }:
                raise LaunchAuthorityError(
                    "launch-authority exclusion receipt is invalid"
                )
            decoded_exclusions.append(
                ProductionExclusion(
                    label=item["label"],  # type: ignore[arg-type]
                    path=Path(item["path"]),  # type: ignore[arg-type]
                    device_id=item["device_id"],  # type: ignore[arg-type]
                    inode=item["inode"],  # type: ignore[arg-type]
                )
            )
        candidate = cls(
            permit_id=_strict_uuid(value["permit_id"], "permit_id"),  # type: ignore[arg-type]
            run_id=_strict_uuid(value["run_id"], "run_id"),  # type: ignore[arg-type]
            vm_id=_strict_uuid(value["vm_id"], "vm_id"),  # type: ignore[arg-type]
            generation=_strict_generation(value["generation"]),  # type: ignore[arg-type]
            disk_id=_strict_uuid(value["disk_id"], "disk_id"),  # type: ignore[arg-type]
            disk_path=value["disk_path"],  # type: ignore[arg-type]
            base_sha256=_strict_sha256(
                value["base_sha256"],  # type: ignore[arg-type]
                "base_sha256",
            ),
            fixture_root=fixture["path"],  # type: ignore[arg-type]
            fixture_device_id=fixture["device_id"],  # type: ignore[arg-type]
            fixture_inode=fixture["inode"],  # type: ignore[arg-type]
            marker_sha256=_strict_sha256(
                value["marker_sha256"],  # type: ignore[arg-type]
                "marker_sha256",
            ),
            production_exclusions=tuple(decoded_exclusions),
        )
        if candidate.to_dict() != value:
            raise LaunchAuthorityError(
                "launch-authority receipt is not canonical"
            )
        return candidate


def _capture_identity(
    path: Path,
    role: str,
    *,
    directory: bool,
    fixture_root: Path,
    fixture_device_id: int,
    mounts: frozenset[Path],
) -> PathIdentity:
    """Capture one exact private fixture path while rejecting traversal aliases."""

    if not _path_is_within(path, fixture_root):
        raise LaunchAuthorityError(f"{role} must be beneath the fixture root")
    relative = path.relative_to(fixture_root)
    current = fixture_root
    for component in relative.parts:
        current = current / component
        try:
            details = current.lstat()
        except OSError as exc:
            raise LaunchAuthorityError(f"{role} path cannot be inspected") from exc
        if stat.S_ISLNK(details.st_mode):
            raise LaunchAuthorityError(f"{role} path traverses a symlink")
        if details.st_dev != fixture_device_id:
            raise LaunchAuthorityError(f"{role} path crosses a filesystem boundary")
        if current in mounts:
            raise LaunchAuthorityError(f"{role} path crosses a mount boundary")
    try:
        selected = path.lstat()
    except OSError as exc:
        raise LaunchAuthorityError(f"{role} cannot be inspected") from exc
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(selected.st_mode):
        kind = "directory" if directory else "regular file"
        raise LaunchAuthorityError(f"{role} must be a {kind}")
    expected_mode = 0o700 if directory else 0o600
    if (
        selected.st_uid != os.geteuid()
        or stat.S_IMODE(selected.st_mode) != expected_mode
    ):
        raise LaunchAuthorityError(f"{role} is not privately owned")
    if not directory and selected.st_nlink != 1:
        raise LaunchAuthorityError(f"{role} has a hard-link alias")
    return PathIdentity(
        role=role,
        path=path,
        device_id=selected.st_dev,
        inode=selected.st_ino,
        owner_uid=selected.st_uid,
        mode=stat.S_IMODE(selected.st_mode),
        link_count=selected.st_nlink,
        size_bytes=selected.st_size,
        mtime_ns=selected.st_mtime_ns,
        ctime_ns=selected.st_ctime_ns,
    )


def _assert_identity(identity: PathIdentity, *, directory: bool) -> None:
    """Revalidate one captured path identity immediately before permit use."""

    try:
        details = identity.path.lstat()
    except OSError as exc:
        raise LaunchAuthorityError(f"{identity.role} disappeared") from exc
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected_type(details.st_mode)
        or details.st_dev != identity.device_id
        or details.st_ino != identity.inode
        or details.st_uid != identity.owner_uid
        or stat.S_IMODE(details.st_mode) != identity.mode
        or details.st_nlink != identity.link_count
        or details.st_size != identity.size_bytes
        or details.st_mtime_ns != identity.mtime_ns
        or details.st_ctime_ns != identity.ctime_ns
    ):
        raise LaunchAuthorityError(f"{identity.role} identity changed")


def _read_marker(
    marker: PathIdentity,
    *,
    fixture_root: Path,
    run_id: UUID,
    vm_id: UUID,
    base_sha256: str,
) -> str:
    """Verify the canonical fixture marker and return its exact digest."""

    _assert_identity(marker, directory=False)
    try:
        raw = marker.path.read_bytes()
    except OSError as exc:
        raise LaunchAuthorityError("fixture marker cannot be read") from exc
    if not 1 <= len(raw) <= _MAX_MARKER_BYTES:
        raise LaunchAuthorityError("fixture marker size is invalid")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LaunchAuthorityError("fixture marker is not JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != _REQUIRED_MARKER_KEYS:
        raise LaunchAuthorityError("fixture marker schema is invalid")
    canonical = json.dumps(
        decoded,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if canonical != raw:
        raise LaunchAuthorityError("fixture marker is not canonical JSON")
    if (
        decoded.get("schema_version") != FIXTURE_MARKER_SCHEMA_VERSION
        or decoded.get("run_id") != str(run_id)
        or decoded.get("vm_id") != str(vm_id)
        or decoded.get("base_image_sha256") != base_sha256
        or decoded.get("permitted_cleanup_root") != os.fspath(fixture_root)
        or decoded.get("owner") != f"uid:{os.geteuid()}"
    ):
        raise LaunchAuthorityError("fixture marker does not authorize this launch")
    created_at = decoded.get("created_at")
    if (
        not isinstance(created_at, str)
        or not created_at
        or len(created_at.encode("utf-8")) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in created_at)
    ):
        raise LaunchAuthorityError("fixture marker creation time is invalid")
    return hashlib.sha256(raw).hexdigest()


def _configuration_roots(configuration: HostConfiguration) -> tuple[tuple[str, Path], ...]:
    """Return every private root that a P4 launch can consume or mutate."""

    return (
        ("state root", configuration.storage.state_root),
        ("runtime root", configuration.daemon.runtime_root),
        ("log root", configuration.storage.log_root),
        ("image root", configuration.storage.image_root),
        ("backup root", configuration.storage.backup_root),
        ("secret root", configuration.security.secret_root),
    )


def _assert_fresh_fixture_root(
    fixture_root: Path,
    marker_path: Path,
    roots: tuple[tuple[str, Path], ...],
) -> None:
    """Reject unexpected top-level fixture contents before a permit is minted."""

    expected_children = {FIXTURE_MARKER_NAME}
    for _, root in roots:
        try:
            relative = root.relative_to(fixture_root)
        except ValueError as exc:
            raise LaunchAuthorityError(
                "configuration root must be beneath the fixture root"
            ) from exc
        if not relative.parts:
            raise LaunchAuthorityError("configuration root must not equal fixture root")
        expected_children.add(relative.parts[0])
    try:
        actual_children = {entry.name for entry in fixture_root.iterdir()}
    except OSError as exc:
        raise LaunchAuthorityError("fixture root cannot be listed") from exc
    if marker_path.name != FIXTURE_MARKER_NAME or actual_children != expected_children:
        raise LaunchAuthorityError("fixture root is not fresh and exact")


def _assert_not_production(
    subjects: Iterable[PathIdentity],
    exclusions: tuple[ProductionExclusion, ...],
) -> None:
    """Reject production path overlap, ancestry, and exact inode aliases."""

    for exclusion in exclusions:
        for subject in subjects:
            if (
                _path_is_within(subject.path, exclusion.path)
                or _path_is_within(exclusion.path, subject.path)
                or (
                    subject.device_id == exclusion.device_id
                    and subject.inode == exclusion.inode
                )
            ):
                raise LaunchAuthorityError(
                    f"{subject.role} overlaps production exclusion {exclusion.label}"
                )


@dataclass(frozen=True, slots=True)
class DisposableLaunchPermit:
    """One in-memory, one-shot authority for a verified disposable P4 launch."""

    permit_id: UUID
    run_id: UUID
    vm_id: UUID
    generation: int
    disk_id: UUID
    base_sha256: str
    fixture_root: PathIdentity
    marker: PathIdentity
    marker_sha256: str
    roots: tuple[PathIdentity, ...]
    disk: PathIdentity
    exclusions: tuple[ProductionExclusion, ...]
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )
    _consumed: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def consumed(self) -> bool:
        """Return whether this permit has already been presented for launch."""

        with self._lock:
            return self._consumed

    def revalidate_and_consume(
        self,
        configuration: HostConfiguration,
        *,
        vm_id: UUID | str,
        generation: int,
        disk_id: UUID | str,
        disk_path: str | Path,
        base_sha256: str,
    ) -> DisposableLaunchReceipt:
        """Burn this permit and prove the supplied launch authority is unchanged.

        A failed validation deliberately consumes the permit as well: a caller
        cannot retry after observing a fixture replacement or production overlap.
        """

        if not isinstance(configuration, HostConfiguration):
            raise LaunchAuthorityError("configuration must be HostConfiguration")
        with self._lock:
            if self._consumed:
                raise LaunchAuthorityError("disposable launch permit was already consumed")
            object.__setattr__(self, "_consumed", True)
            selected_vm = _strict_uuid(vm_id, "vm_id")
            selected_disk = _strict_uuid(disk_id, "disk_id")
            selected_generation = _strict_generation(generation)
            selected_base = _strict_sha256(base_sha256, "base_sha256")
            selected_path = _canonical_existing_path(disk_path, "disk path")
            if (
                selected_vm != self.vm_id
                or selected_generation != self.generation
                or selected_disk != self.disk_id
                or selected_base != self.base_sha256
                or selected_path != self.disk.path
            ):
                raise LaunchAuthorityError("launch facts do not match disposable permit")
            _assert_identity(self.fixture_root, directory=True)
            mounts = _mount_points()
            if self.fixture_root.path in mounts:
                raise LaunchAuthorityError("fixture root must not be a mount point")
            roots = _configuration_roots(configuration)
            if len(roots) != len(self.roots):
                raise LaunchAuthorityError("configuration root set changed")
            for identity, (role, path) in zip(self.roots, roots, strict=True):
                selected = _canonical_existing_path(path, role)
                if identity.role != role or selected != identity.path:
                    raise LaunchAuthorityError("configuration root path changed")
                _capture_identity(
                    selected,
                    role,
                    directory=True,
                    fixture_root=self.fixture_root.path,
                    fixture_device_id=self.fixture_root.device_id,
                    mounts=mounts,
                )
                _assert_identity(identity, directory=True)
            _capture_identity(
                self.marker.path,
                "fixture marker",
                directory=False,
                fixture_root=self.fixture_root.path,
                fixture_device_id=self.fixture_root.device_id,
                mounts=mounts,
            )
            marker_digest = _read_marker(
                self.marker,
                fixture_root=self.fixture_root.path,
                run_id=self.run_id,
                vm_id=self.vm_id,
                base_sha256=self.base_sha256,
            )
            if marker_digest != self.marker_sha256:
                raise LaunchAuthorityError("fixture marker digest changed")
            _capture_identity(
                self.disk.path,
                "P3 overlay",
                directory=False,
                fixture_root=self.fixture_root.path,
                fixture_device_id=self.fixture_root.device_id,
                mounts=mounts,
            )
            _assert_identity(self.disk, directory=False)
            _assert_not_production(
                (self.fixture_root, self.marker, *self.roots, self.disk),
                self.exclusions,
            )
            return DisposableLaunchReceipt(
                permit_id=self.permit_id,
                run_id=self.run_id,
                vm_id=self.vm_id,
                generation=self.generation,
                disk_id=self.disk_id,
                disk_path=os.fspath(self.disk.path),
                base_sha256=self.base_sha256,
                fixture_root=os.fspath(self.fixture_root.path),
                fixture_device_id=self.fixture_root.device_id,
                fixture_inode=self.fixture_root.inode,
                marker_sha256=self.marker_sha256,
                production_exclusions=self.exclusions,
            )


def mint_disposable_launch_permit(
    configuration: HostConfiguration,
    *,
    fixture_root: str | Path,
    run_id: UUID | str,
    vm_id: UUID | str,
    generation: int,
    disk_id: UUID | str,
    disk_path: str | Path,
    base_sha256: str,
    production_exclusions: Iterable[ProductionExclusion],
    permit_id: UUID | str | None = None,
) -> DisposableLaunchPermit:
    """Mint one permit after proving a fresh, marked, non-production topology.

    The caller supplies explicit production identities; no environment variable,
    profile name, current directory, or filename convention is authorization.
    """

    if not isinstance(configuration, HostConfiguration):
        raise LaunchAuthorityError("configuration must be HostConfiguration")
    selected_fixture = _canonical_existing_path(fixture_root, "fixture root")
    selected_run = _strict_uuid(run_id, "run_id")
    selected_vm = _strict_uuid(vm_id, "vm_id")
    selected_generation = _strict_generation(generation)
    selected_disk = _strict_uuid(disk_id, "disk_id")
    selected_base = _strict_sha256(base_sha256, "base_sha256")
    selected_permit = uuid4() if permit_id is None else _strict_uuid(permit_id, "permit_id")
    try:
        exclusions = tuple(production_exclusions)
    except TypeError as exc:
        raise LaunchAuthorityError("production exclusions must be iterable") from exc
    if not exclusions or any(
        not isinstance(exclusion, ProductionExclusion) for exclusion in exclusions
    ):
        raise LaunchAuthorityError("at least one explicit production exclusion is required")
    labels = [exclusion.label for exclusion in exclusions]
    if len(set(labels)) != len(labels):
        raise LaunchAuthorityError("production exclusion labels must be unique")
    try:
        fixture_details = selected_fixture.lstat()
    except OSError as exc:
        raise LaunchAuthorityError("fixture root cannot be inspected") from exc
    if (
        not stat.S_ISDIR(fixture_details.st_mode)
        or fixture_details.st_uid != os.geteuid()
        or stat.S_IMODE(fixture_details.st_mode) != 0o700
    ):
        raise LaunchAuthorityError("fixture root is not a private directory")
    mounts = _mount_points()
    if selected_fixture in mounts:
        raise LaunchAuthorityError("fixture root must not be a mount point")
    roots = _configuration_roots(configuration)
    selected_roots = tuple(
        (role, _canonical_existing_path(path, role)) for role, path in roots
    )
    if len({path for _, path in selected_roots}) != len(selected_roots):
        raise LaunchAuthorityError("configuration roots are not distinct")
    marker_path = selected_fixture / FIXTURE_MARKER_NAME
    selected_marker = _capture_identity(
        marker_path,
        "fixture marker",
        directory=False,
        fixture_root=selected_fixture,
        fixture_device_id=fixture_details.st_dev,
        mounts=mounts,
    )
    _assert_fresh_fixture_root(selected_fixture, marker_path, selected_roots)
    root_identities = tuple(
        _capture_identity(
            path,
            role,
            directory=True,
            fixture_root=selected_fixture,
            fixture_device_id=fixture_details.st_dev,
            mounts=mounts,
        )
        for role, path in selected_roots
    )
    selected_path = _canonical_existing_path(disk_path, "disk path")
    expected_disk = (
        configuration.storage.state_root
        / "vms"
        / str(selected_vm)
        / "storage"
        / "aipc.qcow2"
    )
    if selected_path != expected_disk:
        raise LaunchAuthorityError("disk path is not the exact P3 fixture overlay")
    disk_identity = _capture_identity(
        selected_path,
        "P3 overlay",
        directory=False,
        fixture_root=selected_fixture,
        fixture_device_id=fixture_details.st_dev,
        mounts=mounts,
    )
    fixture_identity = PathIdentity(
        role="fixture root",
        path=selected_fixture,
        device_id=fixture_details.st_dev,
        inode=fixture_details.st_ino,
        owner_uid=fixture_details.st_uid,
        mode=stat.S_IMODE(fixture_details.st_mode),
        link_count=fixture_details.st_nlink,
        size_bytes=fixture_details.st_size,
        mtime_ns=fixture_details.st_mtime_ns,
        ctime_ns=fixture_details.st_ctime_ns,
    )
    marker_digest = _read_marker(
        selected_marker,
        fixture_root=selected_fixture,
        run_id=selected_run,
        vm_id=selected_vm,
        base_sha256=selected_base,
    )
    _assert_not_production(
        (fixture_identity, selected_marker, *root_identities, disk_identity),
        exclusions,
    )
    return DisposableLaunchPermit(
        permit_id=selected_permit,
        run_id=selected_run,
        vm_id=selected_vm,
        generation=selected_generation,
        disk_id=selected_disk,
        base_sha256=selected_base,
        fixture_root=fixture_identity,
        marker=selected_marker,
        marker_sha256=marker_digest,
        roots=root_identities,
        disk=disk_identity,
        exclusions=exclusions,
    )


__all__ = [
    "DisposableLaunchReceipt",
    "DisposableLaunchPermit",
    "FIXTURE_MARKER_NAME",
    "FIXTURE_MARKER_SCHEMA_VERSION",
    "LAUNCH_AUTHORITY_RECEIPT_SCHEMA",
    "LaunchAuthorityError",
    "PathIdentity",
    "ProductionExclusion",
    "mint_disposable_launch_permit",
]
