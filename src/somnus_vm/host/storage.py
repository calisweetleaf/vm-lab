"""Daemon-owned publication and recovery for persistent AIPC storage.

The store deliberately supports only two P3 mutations:

* import one exact, standalone qcow2 base into the configured immutable slot;
* create one sparse writable overlay at the disk path already declared for a
  VM generation.

Network acquisition, ISO installation, QEMU launch, arbitrary disk import,
clone/export/detach, snapshots, and deletion are separate operations and are
not implemented here.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final, Iterator
from uuid import UUID

from ..config import HostConfiguration
from .images import (
    DEFAULT_AIPC_VIRTUAL_SIZE_BYTES,
    ImageInfo,
    ImageIntegrityError,
    ImageManifest,
    ImageOwnershipError,
    QemuImgBackend,
    canonical_json_bytes,
    canonical_path,
    hash_file,
    require_regular_file,
)


STORAGE_MARKER_SCHEMA_VERSION: Final[int] = 1
_MAX_OPERATION_LOCK_WAIT_SECONDS: Final[float] = 130.0
_BASE_STAGE_FILES: Final[frozenset[str]] = frozenset(
    {"operation.lock", "base.qcow2", "manifest.json"}
)
_OVERLAY_STAGE_FILES: Final[frozenset[str]] = frozenset(
    {"operation.lock", "aipc.qcow2", "owner.json"}
)

StorageCheckpoint = Callable[[str, Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class StorageResumeState:
    """Durable checkpoint prefix governing what recovery may reconstruct."""

    checkpoint_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.checkpoint_names, tuple)
            or len(set(self.checkpoint_names)) != len(self.checkpoint_names)
            or any(
                not isinstance(name, str)
                or not name
                or len(name) > 128
                for name in self.checkpoint_names
            )
        ):
            raise ImageIntegrityError("storage resume checkpoint state is invalid")

    def reached(self, name: str) -> bool:
        return name in self.checkpoint_names


@dataclass(frozen=True, slots=True)
class BaseImageEvidence:
    """Measured immutable base identity returned to the registry owner."""

    manifest: ImageManifest
    path: Path
    manifest_path: Path
    device_id: int
    inode: int
    file_size_bytes: int
    allocated_size_bytes: int
    mode: int
    qemu_img_version: str
    chain_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allocated_size_bytes": self.allocated_size_bytes,
            "chain_sha256": self.chain_sha256,
            "device_id": self.device_id,
            "file_size_bytes": self.file_size_bytes,
            "image_id": str(self.manifest.image_id),
            "inode": self.inode,
            "manifest": self.manifest.to_dict(),
            "manifest_path": os.fspath(self.manifest_path),
            "manifest_sha256": self.manifest.manifest_sha256,
            "mode": self.mode,
            "path": os.fspath(self.path),
            "qemu_img_version": self.qemu_img_version,
        }


@dataclass(frozen=True, slots=True)
class OverlayCreateIntent:
    """All owner-derived facts fixed before an overlay mutation begins."""

    operation_id: UUID
    vm_id: UUID
    generation: int
    disk_id: UUID
    ownership_token: UUID
    final_path: Path
    base_image_id: UUID
    base_image_sha256: str
    virtual_size_bytes: int
    created_at: str

    def __post_init__(self) -> None:
        for label in (
            "operation_id",
            "vm_id",
            "disk_id",
            "ownership_token",
            "base_image_id",
        ):
            value = getattr(self, label)
            if not isinstance(value, UUID) or value.int == 0:
                raise ImageOwnershipError(f"{label} must be a nonnil UUID")
        if (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or not 0 <= self.generation <= 2**63 - 1
        ):
            raise ImageOwnershipError("generation is outside the supported bound")
        if (
            not isinstance(self.virtual_size_bytes, int)
            or isinstance(self.virtual_size_bytes, bool)
            or not 1024**3 <= self.virtual_size_bytes <= 16 * 1024**4
        ):
            raise ImageOwnershipError(
                "virtual_size_bytes is outside the supported bound"
            )
        if (
            not isinstance(self.base_image_sha256, str)
            or len(self.base_image_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.base_image_sha256)
        ):
            raise ImageOwnershipError("base_image_sha256 is invalid")
        if (
            not isinstance(self.created_at, str)
            or not self.created_at.endswith("Z")
            or len(self.created_at) > 64
        ):
            raise ImageOwnershipError("created_at is invalid")
        object.__setattr__(
            self,
            "final_path",
            canonical_path(
                self.final_path,
                "overlay final path",
                must_exist=False,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "base_image_id": str(self.base_image_id),
            "base_image_sha256": self.base_image_sha256,
            "created_at": self.created_at,
            "disk_id": str(self.disk_id),
            "final_path": os.fspath(self.final_path),
            "generation": self.generation,
            "operation_id": str(self.operation_id),
            "ownership_token": str(self.ownership_token),
            "virtual_size_bytes": self.virtual_size_bytes,
            "vm_id": str(self.vm_id),
        }


@dataclass(frozen=True, slots=True)
class OverlayEvidence:
    """Measured published overlay identity returned to the registry owner."""

    intent: OverlayCreateIntent
    marker_path: Path
    device_id: int
    inode: int
    file_size_bytes: int
    allocated_size_bytes: int
    qemu_actual_size_bytes: int
    mode: int
    qemu_img_version: str
    chain_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allocated_size_bytes": self.allocated_size_bytes,
            "base_image_id": str(self.intent.base_image_id),
            "base_image_sha256": self.intent.base_image_sha256,
            "chain_sha256": self.chain_sha256,
            "device_id": self.device_id,
            "disk_id": str(self.intent.disk_id),
            "file_size_bytes": self.file_size_bytes,
            "format": "qcow2",
            "generation": self.intent.generation,
            "inode": self.inode,
            "marker_path": os.fspath(self.marker_path),
            "mode": self.mode,
            "operation_id": str(self.intent.operation_id),
            "ownership_token": str(self.intent.ownership_token),
            "path": os.fspath(self.intent.final_path),
            "qemu_actual_size_bytes": self.qemu_actual_size_bytes,
            "qemu_img_version": self.qemu_img_version,
            "virtual_size_bytes": self.intent.virtual_size_bytes,
            "vm_id": str(self.intent.vm_id),
        }

    @property
    def marker_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "schema_version": STORAGE_MARKER_SCHEMA_VERSION,
                **self.to_dict(),
                "created_at": self.intent.created_at,
            }
        )

    @property
    def marker_sha256(self) -> str:
        return sha256(self.marker_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class RegisteredOverlay:
    """Registry projection used for startup physical reconciliation."""

    vm_id: UUID
    generation: int
    disk_id: UUID
    ownership_token: UUID
    operation_id: UUID
    path: Path
    base_image_id: UUID
    base_image_sha256: str
    virtual_size_bytes: int
    device_id: int
    inode: int
    file_size_bytes: int
    allocated_size_bytes: int
    mode: int
    qemu_img_version: str
    chain_sha256: str
    marker_sha256: str


@dataclass(frozen=True, slots=True)
class RegisteredBase:
    """Registry projection for an immutable published base image."""

    manifest: ImageManifest
    path: Path
    device_id: int
    inode: int
    file_size_bytes: int
    allocated_size_bytes: int
    mode: int
    qemu_img_version: str
    chain_sha256: str


def _allocated_size(details: os.stat_result) -> int:
    blocks = getattr(details, "st_blocks", None)
    if not isinstance(blocks, int) or blocks < 0:
        raise ImageIntegrityError("filesystem allocation cannot be observed")
    return blocks * 512


def _mode(details: os.stat_result) -> int:
    return stat.S_IMODE(details.st_mode)


def _path_present(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ImageOwnershipError("owned path cannot be observed") from exc
    return True


def _chain_payload(chain: tuple[ImageInfo, ...]) -> list[dict[str, object]]:
    return [
        {
            "actual_size_bytes": entry.actual_size_bytes,
            "backing_filename": (
                os.fspath(entry.backing_filename)
                if entry.backing_filename is not None
                else None
            ),
            "corrupt": entry.corrupt,
            "dirty": entry.dirty,
            "format": entry.format,
            "layer": index,
            "virtual_size_bytes": entry.virtual_size_bytes,
        }
        for index, entry in enumerate(chain)
    ]


def _chain_sha256(chain: tuple[ImageInfo, ...]) -> str:
    return sha256(canonical_json_bytes(_chain_payload(chain))).hexdigest()


def _fsync_file(path: Path) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ImageIntegrityError("owned file could not be fsynced") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ImageIntegrityError("owned directory could not be fsynced") from exc


def _write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, mode)
    except OSError as exc:
        raise ImageOwnershipError("owned metadata path already exists") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short metadata write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise ImageIntegrityError("owned metadata could not be written") from exc
    finally:
        os.close(descriptor)


def _publish_no_replace(candidate: Path, destination: Path) -> None:
    """Publish one same-filesystem file without overwriting an existing name.

    ``link`` is an atomic no-replace namespace operation.  A crash between the
    link and candidate unlink leaves two names for the same inode; recovery
    recognizes that exact inode pair and removes only the recorded candidate.
    """

    try:
        os.link(candidate, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise ImageOwnershipError("publication destination already exists") from exc
    except OSError as exc:
        raise ImageIntegrityError("owned file could not be published") from exc
    _fsync_directory(destination.parent)
    try:
        candidate.unlink()
    except OSError as exc:
        raise ImageIntegrityError(
            "published candidate could not be unlinked"
        ) from exc
    _fsync_directory(candidate.parent)


def _unlink_regular(path: Path, *, allow_link_to: Path | None = None) -> None:
    try:
        details = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ImageOwnershipError("staged path cannot be observed") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ImageOwnershipError("staged path is not a regular file")
    if allow_link_to is not None:
        try:
            target = allow_link_to.stat(follow_symlinks=False)
        except OSError as exc:
            raise ImageOwnershipError(
                "published hard-link target cannot be observed"
            ) from exc
        if (
            details.st_nlink != 2
            or target.st_nlink != 2
            or not stat.S_ISREG(target.st_mode)
            or details.st_dev != target.st_dev
            or details.st_ino != target.st_ino
        ):
            raise ImageOwnershipError("staged hard-link identity is ambiguous")
    elif details.st_nlink != 1:
        raise ImageOwnershipError("staged path has an ambiguous hard link")
    try:
        path.unlink()
    except OSError as exc:
        raise ImageIntegrityError("staged file could not be removed") from exc
    _fsync_directory(path.parent)


class ImageStore:
    """Physical P3 store composed under the daemon's single mutation lock."""

    def __init__(
        self,
        configuration: HostConfiguration,
        *,
        backend: QemuImgBackend | None = None,
    ) -> None:
        if not isinstance(configuration, HostConfiguration):
            raise TypeError("configuration must be HostConfiguration")
        self.configuration = configuration
        self.backend = (
            backend
            if backend is not None
            else QemuImgBackend(configuration.qemu_img_binary)
        )
        if not isinstance(self.backend, QemuImgBackend):
            raise TypeError("backend must be QemuImgBackend")
        self.state_root = configuration.storage.state_root
        self.image_root = configuration.storage.image_root
        self.base_path = configuration.storage.base_image
        self.base_manifest_path = Path(f"{self.base_path}.manifest.json")
        self._prepare_owner_directory(self.state_root)
        self._prepare_owner_directory(self.image_root)
        self._root_identities = {
            self.state_root: self._directory_identity(self.state_root),
            self.image_root: self._directory_identity(self.image_root),
        }
        if len(set(self._root_identities.values())) != len(
            self._root_identities
        ):
            raise ImageOwnershipError(
                "storage owner roots physically alias one another"
            )

    @staticmethod
    def _prepare_owner_directory(path: Path) -> None:
        selected = canonical_path(path, "storage owner root", must_exist=False)
        try:
            selected.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise ImageOwnershipError(
                "storage owner root could not be created"
            ) from exc
        selected = canonical_path(selected, "storage owner root", must_exist=True)
        try:
            details = selected.stat(follow_symlinks=False)
        except OSError as exc:
            raise ImageOwnershipError(
                "storage owner root cannot be observed"
            ) from exc
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or _mode(details) & 0o022
        ):
            raise ImageOwnershipError(
                "storage owner root permissions or owner are unsafe"
            )

    @staticmethod
    def _directory_identity(path: Path) -> tuple[int, int]:
        details = path.stat(follow_symlinks=False)
        if not stat.S_ISDIR(details.st_mode):
            raise ImageOwnershipError("storage owner root is not a directory")
        return details.st_dev, details.st_ino

    def _revalidate_roots(self) -> None:
        observed: set[tuple[int, int]] = set()
        for path, expected in self._root_identities.items():
            selected = canonical_path(
                path,
                "storage owner root",
                must_exist=True,
            )
            details = selected.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_uid != os.geteuid()
                or _mode(details) & 0o022
                or (details.st_dev, details.st_ino) != expected
            ):
                raise ImageOwnershipError(
                    "storage owner root identity changed"
                )
            observed.add((details.st_dev, details.st_ino))
        if len(observed) != len(self._root_identities):
            raise ImageOwnershipError(
                "storage owner roots physically alias one another"
            )

    @staticmethod
    def _require_descendant(child: Path, parent: Path, label: str) -> None:
        try:
            child.relative_to(parent)
        except ValueError as exc:
            raise ImageOwnershipError(f"{label} escapes its owner root") from exc
        if child == parent:
            raise ImageOwnershipError(f"{label} cannot equal its owner root")

    def _base_stage(self, operation_id: UUID) -> Path:
        return self.image_root / ".staging" / f"base-{operation_id}"

    @staticmethod
    def _overlay_stage(final_path: Path, operation_id: UUID) -> Path:
        return final_path.parent / ".staging" / str(operation_id)

    def _prepare_stage(self, stage: Path) -> None:
        self._require_descendant(stage, self.state_root if stage.is_relative_to(self.state_root) else self.image_root, "stage")
        self._prepare_owner_directory(stage.parent)
        try:
            stage.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ImageOwnershipError("operation stage could not be created") from exc
        canonical_path(stage, "operation stage", must_exist=True)
        details = stage.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or _mode(details) != 0o700
        ):
            raise ImageOwnershipError("operation stage permissions are unsafe")
        _fsync_directory(stage.parent)

    @contextmanager
    def _operation_lock(self, stage: Path) -> Iterator[int]:
        self._prepare_stage(stage)
        lock_path = stage / "operation.lock"
        try:
            descriptor = os.open(
                lock_path,
                os.O_RDWR
                | os.O_CREAT
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            raise ImageOwnershipError(
                "operation lock could not be opened"
            ) from exc
        deadline = time.monotonic() + _MAX_OPERATION_LOCK_WAIT_SECONDS
        try:
            lock_details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_details.st_mode)
                or lock_details.st_uid != os.geteuid()
                or lock_details.st_nlink != 1
                or _mode(lock_details) != 0o600
            ):
                raise ImageOwnershipError(
                    "operation lock ownership is unsafe"
                )
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ImageOwnershipError(
                            "a prior image subprocess still owns the operation stage"
                        )
                    time.sleep(0.05)
            yield descriptor
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _checkpoint(
        callback: StorageCheckpoint,
        name: str,
        payload: Mapping[str, object],
    ) -> None:
        if not callable(callback):
            raise TypeError("checkpoint callback must be callable")
        callback(name, payload)

    @staticmethod
    def _copy_source_once(
        source: Path,
        destination: Path,
        manifest: ImageManifest,
    ) -> None:
        source_path = canonical_path(
            source,
            "base-image import source",
            must_exist=True,
        )
        source_flags = (
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        )
        destination_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            source_fd = os.open(source_path, source_flags)
        except OSError as exc:
            raise ImageIntegrityError(
                "base-image import source could not be opened"
            ) from exc
        destination_fd: int | None = None
        digest = sha256()
        copied = 0
        try:
            before = os.fstat(source_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != manifest.size_bytes
            ):
                raise ImageOwnershipError(
                    "base-image import source identity is unsafe"
                )
            try:
                destination_fd = os.open(
                    destination,
                    destination_flags,
                    0o600,
                )
            except OSError as exc:
                raise ImageOwnershipError(
                    "base-image staging destination already exists"
                ) from exc
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                copied += len(block)
                view = memoryview(block)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise OSError("short base-image write")
                    view = view[written:]
            os.fsync(destination_fd)
            after = os.fstat(source_fd)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or copied != manifest.size_bytes
                or digest.hexdigest() != manifest.sha256
            ):
                raise ImageIntegrityError(
                    "base-image import bytes disagree with the manifest"
                )
        except OSError as exc:
            raise ImageIntegrityError(
                "base-image import could not be completed"
            ) from exc
        finally:
            if destination_fd is not None:
                os.close(destination_fd)
            os.close(source_fd)

    @staticmethod
    def _settle_link(candidate: Path, final: Path) -> bool:
        if not _path_present(candidate) or not _path_present(final):
            return False
        _unlink_regular(candidate, allow_link_to=final)
        return True

    @staticmethod
    def _read_owned_bytes(path: Path, *, maximum: int) -> bytes:
        selected = canonical_path(
            path,
            "owned metadata",
            must_exist=True,
        )
        try:
            descriptor = os.open(
                selected,
                os.O_RDONLY
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise ImageIntegrityError("owned metadata cannot be opened") from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size < 1
                or before.st_size > maximum
            ):
                raise ImageIntegrityError(
                    "owned metadata identity or size disagrees"
                )
            actual = bytearray()
            while len(actual) <= maximum:
                block = os.read(
                    descriptor,
                    min(65_536, maximum + 1 - len(actual)),
                )
                if not block:
                    break
                actual.extend(block)
            after = os.fstat(descriptor)
            if (
                len(actual) > maximum
                or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise ImageIntegrityError("owned metadata bytes disagree")
        except OSError as exc:
            raise ImageIntegrityError("owned metadata cannot be read") from exc
        finally:
            os.close(descriptor)
        return bytes(actual)

    @classmethod
    def _read_exact(cls, path: Path, expected: bytes) -> None:
        actual = cls._read_owned_bytes(path, maximum=len(expected))
        if actual != expected:
            raise ImageIntegrityError("owned metadata bytes disagree")

    def _base_evidence(self, manifest: ImageManifest) -> BaseImageEvidence:
        chain = self.backend.verify_base(self.base_path, manifest)
        _, details = require_regular_file(
            self.base_path,
            "published base image",
            unique_link=True,
        )
        expected_mode = _mode(details)
        if expected_mode != 0o444 or details.st_uid != os.geteuid():
            raise ImageOwnershipError(
                "published base image is not immutable and daemon-owned"
            )
        self._read_exact(self.base_manifest_path, manifest.canonical_bytes)
        _, manifest_details = require_regular_file(
            self.base_manifest_path,
            "published base manifest",
            unique_link=True,
        )
        if (
            manifest_details.st_uid != os.geteuid()
            or _mode(manifest_details) != 0o444
        ):
            raise ImageOwnershipError(
                "published base manifest is not immutable and daemon-owned"
            )
        if self.base_path.is_relative_to(self.state_root / "vms"):
            raise ImageOwnershipError(
                "base image cannot be inside an instance-owned directory"
            )
        return BaseImageEvidence(
            manifest=manifest,
            path=self.base_path,
            manifest_path=self.base_manifest_path,
            device_id=details.st_dev,
            inode=details.st_ino,
            file_size_bytes=details.st_size,
            allocated_size_bytes=_allocated_size(details),
            mode=expected_mode,
            qemu_img_version=self.backend.version,
            chain_sha256=_chain_sha256(chain),
        )

    def import_base(
        self,
        operation_id: UUID,
        source_path: str | Path,
        manifest: ImageManifest,
        checkpoint: StorageCheckpoint,
        *,
        resume_state: StorageResumeState | None = None,
    ) -> BaseImageEvidence:
        """Verify and atomically publish one immutable standalone base image."""

        if not isinstance(operation_id, UUID) or operation_id.int == 0:
            raise ImageOwnershipError("operation_id must be a nonnil UUID")
        if not isinstance(manifest, ImageManifest):
            raise TypeError("manifest must be ImageManifest")
        resume = (
            StorageResumeState()
            if resume_state is None
            else resume_state
        )
        if not isinstance(resume, StorageResumeState):
            raise TypeError("resume_state must be StorageResumeState")
        self._revalidate_roots()
        self._require_descendant(self.base_path, self.image_root, "base image")
        if self.base_path.is_relative_to(self.state_root / "vms"):
            raise ImageOwnershipError(
                "base image cannot be inside an instance-owned directory"
            )
        stage = self._base_stage(operation_id)
        candidate = stage / "base.qcow2"
        manifest_candidate = stage / "manifest.json"
        with self._operation_lock(stage):
            self._checkpoint(
                checkpoint,
                "base_stage_prepared",
                {
                    "operation_id": str(operation_id),
                    "stage_path": os.fspath(stage),
                },
            )
            self._settle_link(candidate, self.base_path)
            self._settle_link(manifest_candidate, self.base_manifest_path)
            base_present = _path_present(self.base_path)
            manifest_present = _path_present(self.base_manifest_path)
            if resume.reached("base_published") and not (
                base_present and manifest_present
            ):
                raise ImageIntegrityError(
                    "published base-image authority disappeared"
                )
            if base_present:
                if not manifest_present:
                    if _path_present(manifest_candidate):
                        self._read_exact(
                            manifest_candidate,
                            manifest.canonical_bytes,
                        )
                        _publish_no_replace(
                            manifest_candidate,
                            self.base_manifest_path,
                        )
                    else:
                        raise ImageOwnershipError(
                            "published base image has no authoritative manifest"
                        )
                    _fsync_file(self.base_manifest_path)
                    _fsync_directory(self.base_manifest_path.parent)
                evidence = self._base_evidence(manifest)
                self._checkpoint(
                    checkpoint,
                    "base_bytes_staged",
                    {
                        "device_id": evidence.device_id,
                        "file_size_bytes": evidence.file_size_bytes,
                        "inode": evidence.inode,
                        "operation_id": str(operation_id),
                        "sha256": manifest.sha256,
                        "size_bytes": manifest.size_bytes,
                        "stage_path": os.fspath(candidate),
                    },
                )
                self._checkpoint(
                    checkpoint,
                    "base_verified",
                    {
                        "chain_sha256": evidence.chain_sha256,
                        "manifest_sha256": manifest.manifest_sha256,
                        "operation_id": str(operation_id),
                        "qemu_img_version": evidence.qemu_img_version,
                    },
                )
                self._checkpoint(
                    checkpoint,
                    "base_published",
                    evidence.to_dict(),
                )
                self._cleanup_stage(stage, _BASE_STAGE_FILES)
                return evidence
            if manifest_present:
                raise ImageOwnershipError(
                    "base manifest exists without its image"
                )
            resume_requires_candidate = (
                resume.reached("base_bytes_staged")
                or resume.reached("base_verified")
            )
            if resume_requires_candidate:
                if not _path_present(candidate):
                    raise ImageIntegrityError(
                        "checkpointed staged base image disappeared"
                    )
            else:
                _unlink_regular(candidate)
                _unlink_regular(manifest_candidate)
                self._copy_source_once(Path(source_path), candidate, manifest)
                _fsync_directory(stage)
            digest, byte_size = hash_file(candidate, unique_link=True)
            if digest != manifest.sha256 or byte_size != manifest.size_bytes:
                raise ImageIntegrityError(
                    "staged base bytes disagree with the manifest"
                )
            staged_base_details = candidate.stat(follow_symlinks=False)
            self._checkpoint(
                checkpoint,
                "base_bytes_staged",
                {
                    "device_id": staged_base_details.st_dev,
                    "file_size_bytes": staged_base_details.st_size,
                    "inode": staged_base_details.st_ino,
                    "operation_id": str(operation_id),
                    "sha256": digest,
                    "size_bytes": byte_size,
                    "stage_path": os.fspath(candidate),
                },
            )
            chain = self.backend.verify_base(candidate, manifest)
            verified_base_details = candidate.stat(follow_symlinks=False)
            if (
                verified_base_details.st_dev != staged_base_details.st_dev
                or verified_base_details.st_ino != staged_base_details.st_ino
                or verified_base_details.st_size != staged_base_details.st_size
            ):
                raise ImageIntegrityError(
                    "staged base identity changed between checkpoints"
                )
            self._checkpoint(
                checkpoint,
                "base_verified",
                {
                    "chain_sha256": _chain_sha256(chain),
                    "manifest_sha256": manifest.manifest_sha256,
                    "operation_id": str(operation_id),
                    "qemu_img_version": self.backend.version,
                },
            )
            if _path_present(manifest_candidate):
                self._read_exact(
                    manifest_candidate,
                    manifest.canonical_bytes,
                )
            else:
                _write_exclusive(
                    manifest_candidate,
                    manifest.canonical_bytes,
                    0o400,
                )
            os.chmod(candidate, 0o444)
            os.chmod(manifest_candidate, 0o444)
            _fsync_file(candidate)
            _fsync_file(manifest_candidate)
            _fsync_directory(stage)
            _publish_no_replace(candidate, self.base_path)
            _publish_no_replace(
                manifest_candidate,
                self.base_manifest_path,
            )
            _fsync_file(self.base_path)
            _fsync_file(self.base_manifest_path)
            _fsync_directory(self.image_root)
            evidence = self._base_evidence(manifest)
            self._checkpoint(
                checkpoint,
                "base_published",
                evidence.to_dict(),
            )
        self._cleanup_stage(stage, _BASE_STAGE_FILES)
        return evidence

    def verify_published_base(
        self,
        manifest: ImageManifest,
    ) -> BaseImageEvidence:
        self._revalidate_roots()
        return self._base_evidence(manifest)

    def verify_registered_base(
        self,
        authority: RegisteredBase,
    ) -> BaseImageEvidence:
        """Require live base bytes to match every durable physical fact."""

        if not isinstance(authority, RegisteredBase):
            raise TypeError("authority must be RegisteredBase")
        if authority.path != self.base_path:
            raise ImageIntegrityError(
                "registered base path disagrees with configuration"
            )
        evidence = self.verify_published_base(authority.manifest)
        if (
            evidence.path != authority.path
            or evidence.device_id != authority.device_id
            or evidence.inode != authority.inode
            or evidence.file_size_bytes != authority.file_size_bytes
            or evidence.allocated_size_bytes != authority.allocated_size_bytes
            or evidence.mode != authority.mode
            or evidence.qemu_img_version != authority.qemu_img_version
            or evidence.chain_sha256 != authority.chain_sha256
        ):
            raise ImageIntegrityError(
                "base-image filesystem truth disagrees with the registry"
            )
        return evidence

    def _validate_overlay_target(self, intent: OverlayCreateIntent) -> None:
        expected = (
            self.state_root
            / "vms"
            / str(intent.vm_id)
            / "storage"
            / "aipc.qcow2"
        )
        if intent.final_path != expected:
            raise ImageOwnershipError(
                "overlay target does not match its VM owner path"
            )
        self._require_descendant(intent.final_path, self.state_root, "overlay")
        if self.base_path.is_relative_to(intent.final_path.parent):
            raise ImageOwnershipError(
                "base image cannot be instance-owned"
            )

    def _overlay_evidence(
        self,
        intent: OverlayCreateIntent,
        base: BaseImageEvidence,
        *,
        require_initial_sparse: bool = True,
    ) -> OverlayEvidence:
        chain = self.backend.verify_overlay(
            intent.final_path,
            base.path,
            base.manifest,
            virtual_size_bytes=intent.virtual_size_bytes,
            require_initial_sparse=require_initial_sparse,
        )
        _, details = require_regular_file(
            intent.final_path,
            "published overlay",
            unique_link=True,
        )
        if (
            details.st_uid != os.geteuid()
            or _mode(details) != 0o600
            or (details.st_dev, details.st_ino)
            == (base.device_id, base.inode)
        ):
            raise ImageOwnershipError(
                "published overlay identity or permissions are unsafe"
            )
        return OverlayEvidence(
            intent=intent,
            marker_path=Path(f"{intent.final_path}.owner.json"),
            device_id=details.st_dev,
            inode=details.st_ino,
            file_size_bytes=details.st_size,
            allocated_size_bytes=_allocated_size(details),
            qemu_actual_size_bytes=chain[0].actual_size_bytes,
            mode=_mode(details),
            qemu_img_version=self.backend.version,
            chain_sha256=_chain_sha256(chain),
        )

    def create_overlay(
        self,
        intent: OverlayCreateIntent,
        base_authority: RegisteredBase,
        checkpoint: StorageCheckpoint,
        *,
        resume_state: StorageResumeState | None = None,
    ) -> OverlayEvidence:
        """Create or deterministically adopt one journal-owned overlay."""

        if not isinstance(intent, OverlayCreateIntent):
            raise TypeError("intent must be OverlayCreateIntent")
        if not isinstance(base_authority, RegisteredBase):
            raise TypeError("base_authority must be RegisteredBase")
        resume = (
            StorageResumeState()
            if resume_state is None
            else resume_state
        )
        if not isinstance(resume, StorageResumeState):
            raise TypeError("resume_state must be StorageResumeState")
        self._revalidate_roots()
        self._validate_overlay_target(intent)
        base_manifest = base_authority.manifest
        if (
            intent.base_image_id != base_manifest.image_id
            or intent.base_image_sha256 != base_manifest.sha256
        ):
            raise ImageIntegrityError(
                "overlay intent disagrees with the base manifest"
            )
        base = self.verify_registered_base(base_authority)
        self._prepare_owner_directory(intent.final_path.parent)
        stage = self._overlay_stage(intent.final_path, intent.operation_id)
        candidate = stage / "aipc.qcow2"
        marker_candidate = stage / "owner.json"
        marker_path = Path(f"{intent.final_path}.owner.json")
        with self._operation_lock(stage) as lock_fd:
            self._checkpoint(
                checkpoint,
                "overlay_stage_prepared",
                {
                    "operation_id": str(intent.operation_id),
                    "stage_path": os.fspath(stage),
                },
            )
            self._settle_link(candidate, intent.final_path)
            self._settle_link(marker_candidate, marker_path)
            overlay_present = _path_present(intent.final_path)
            marker_present = _path_present(marker_path)
            if resume.reached("overlay_published") and not (
                overlay_present and marker_present
            ):
                raise ImageIntegrityError(
                    "published overlay authority disappeared"
                )
            if overlay_present:
                evidence = self._overlay_evidence(intent, base)
                self.verify_registered_base(base_authority)
                if marker_present:
                    self._read_exact(marker_path, evidence.marker_bytes)
                elif _path_present(marker_candidate):
                    self._read_exact(marker_candidate, evidence.marker_bytes)
                    _publish_no_replace(marker_candidate, marker_path)
                    _fsync_file(marker_path)
                    _fsync_directory(marker_path.parent)
                else:
                    raise ImageOwnershipError(
                        "published overlay has no journal-owned marker"
                    )
                self._checkpoint(
                    checkpoint,
                    "overlay_created",
                    {
                        "allocated_size_bytes": evidence.allocated_size_bytes,
                        "chain_sha256": evidence.chain_sha256,
                        "device_id": evidence.device_id,
                        "file_size_bytes": evidence.file_size_bytes,
                        "inode": evidence.inode,
                        "mode": evidence.mode,
                        "operation_id": str(intent.operation_id),
                        "qemu_actual_size_bytes": (
                            evidence.qemu_actual_size_bytes
                        ),
                        "stage_path": os.fspath(candidate),
                        "virtual_size_bytes": intent.virtual_size_bytes,
                    },
                )
                self._checkpoint(
                    checkpoint,
                    "overlay_verified",
                    {
                        "chain_sha256": evidence.chain_sha256,
                        "operation_id": str(intent.operation_id),
                        "qemu_img_version": evidence.qemu_img_version,
                    },
                )
                self._checkpoint(
                    checkpoint,
                    "overlay_published",
                    evidence.to_dict(),
                )
                self._cleanup_stage(stage, _OVERLAY_STAGE_FILES)
                return evidence
            if marker_present:
                raise ImageOwnershipError(
                    "overlay marker exists without its owned image"
                )
            resume_requires_candidate = (
                resume.reached("overlay_created")
                or resume.reached("overlay_verified")
            )
            if resume_requires_candidate:
                if not _path_present(candidate):
                    raise ImageIntegrityError(
                        "checkpointed staged overlay disappeared"
                    )
            else:
                _unlink_regular(candidate)
                _unlink_regular(marker_candidate)
                self.backend.create_overlay(
                    candidate,
                    base.path,
                    virtual_size_bytes=intent.virtual_size_bytes,
                    operation_lock_fd=lock_fd,
                )
                os.chmod(candidate, 0o600)
                _fsync_file(candidate)
                _fsync_directory(stage)
            created_chain = self.backend.inspect(candidate)
            created_details = candidate.stat(follow_symlinks=False)
            self._checkpoint(
                checkpoint,
                "overlay_created",
                {
                    "allocated_size_bytes": _allocated_size(created_details),
                    "chain_sha256": _chain_sha256(created_chain),
                    "device_id": created_details.st_dev,
                    "file_size_bytes": created_details.st_size,
                    "inode": created_details.st_ino,
                    "mode": _mode(created_details),
                    "operation_id": str(intent.operation_id),
                    "qemu_actual_size_bytes": (
                        created_chain[0].actual_size_bytes
                    ),
                    "stage_path": os.fspath(candidate),
                    "virtual_size_bytes": intent.virtual_size_bytes,
                },
            )
            verified_chain = self.backend.verify_overlay(
                candidate,
                base.path,
                base.manifest,
                virtual_size_bytes=intent.virtual_size_bytes,
            )
            self.verify_registered_base(base_authority)
            self._checkpoint(
                checkpoint,
                "overlay_verified",
                {
                    "chain_sha256": _chain_sha256(verified_chain),
                    "operation_id": str(intent.operation_id),
                    "qemu_img_version": self.backend.version,
                },
            )
            staged_details = candidate.stat(follow_symlinks=False)
            if (
                staged_details.st_dev != created_details.st_dev
                or staged_details.st_ino != created_details.st_ino
                or staged_details.st_size != created_details.st_size
                or _mode(staged_details) != _mode(created_details)
            ):
                raise ImageIntegrityError(
                    "staged overlay identity changed between checkpoints"
                )
            staged_evidence = OverlayEvidence(
                intent=intent,
                marker_path=marker_path,
                device_id=staged_details.st_dev,
                inode=staged_details.st_ino,
                file_size_bytes=staged_details.st_size,
                allocated_size_bytes=_allocated_size(staged_details),
                qemu_actual_size_bytes=(
                    verified_chain[0].actual_size_bytes
                ),
                mode=_mode(staged_details),
                qemu_img_version=self.backend.version,
                chain_sha256=_chain_sha256(verified_chain),
            )
            if _path_present(marker_candidate):
                self._read_exact(
                    marker_candidate,
                    staged_evidence.marker_bytes,
                )
            else:
                _write_exclusive(
                    marker_candidate,
                    staged_evidence.marker_bytes,
                    0o600,
                )
            _fsync_file(candidate)
            _fsync_file(marker_candidate)
            _fsync_directory(stage)
            _publish_no_replace(candidate, intent.final_path)
            _publish_no_replace(marker_candidate, marker_path)
            _fsync_file(intent.final_path)
            _fsync_file(marker_path)
            _fsync_directory(intent.final_path.parent)
            evidence = self._overlay_evidence(intent, base)
            self._read_exact(marker_path, evidence.marker_bytes)
            self._checkpoint(
                checkpoint,
                "overlay_published",
                evidence.to_dict(),
            )
        self._cleanup_stage(stage, _OVERLAY_STAGE_FILES)
        return evidence

    def verify_registered_overlay(
        self,
        authority: RegisteredOverlay,
        base_authority: RegisteredBase,
        *,
        mutable_runtime: bool = False,
    ) -> OverlayEvidence:
        """Re-observe one durable disk row before the daemon accepts work.

        P3 materialization records the initial qcow2 allocation as immutable
        creation evidence.  Once a P4 runtime observation exists, legitimate
        guest writes may change file length, filesystem allocation, QEMU's
        reported actual size, and therefore the historical full-chain digest.
        ``mutable_runtime`` relaxes only those observational fields; inode,
        owner marker, permissions, base identity, format, backing order,
        virtual capacity, dirty/corrupt state, and tool identity remain
        independently re-measured by ``_overlay_evidence``.
        """

        if not isinstance(authority, RegisteredOverlay):
            raise TypeError("authority must be RegisteredOverlay")
        if not isinstance(base_authority, RegisteredBase):
            raise TypeError("base_authority must be RegisteredBase")
        if not isinstance(mutable_runtime, bool):
            raise TypeError("mutable_runtime must be boolean")
        marker_path = Path(f"{authority.path}.owner.json")
        marker_bytes = self._read_owned_bytes(
            marker_path,
            maximum=65_536,
        )
        if sha256(marker_bytes).hexdigest() != authority.marker_sha256:
            raise ImageIntegrityError(
                "overlay marker hash disagrees with the registry"
            )

        def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            marker = json.loads(
                marker_bytes.decode("utf-8"),
                object_pairs_hook=pairs_hook,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("non-finite value")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ImageIntegrityError(
                "overlay marker is not strict JSON"
            ) from exc
        if not isinstance(marker, dict) or canonical_json_bytes(marker) != marker_bytes:
            raise ImageIntegrityError(
                "overlay marker is not canonical JSON"
            )
        created_at = marker.get("created_at")
        if not isinstance(created_at, str):
            raise ImageIntegrityError("overlay marker creation time is invalid")
        intent = OverlayCreateIntent(
            operation_id=authority.operation_id,
            vm_id=authority.vm_id,
            generation=authority.generation,
            disk_id=authority.disk_id,
            ownership_token=authority.ownership_token,
            final_path=authority.path,
            base_image_id=authority.base_image_id,
            base_image_sha256=authority.base_image_sha256,
            virtual_size_bytes=authority.virtual_size_bytes,
            created_at=created_at,
        )
        base = self.verify_registered_base(base_authority)
        evidence = self._overlay_evidence(
            intent,
            base,
            require_initial_sparse=False,
        )
        self.verify_registered_base(base_authority)
        if not mutable_runtime and evidence.marker_bytes != marker_bytes:
            raise ImageIntegrityError(
                "overlay marker disagrees with measured image authority"
            )
        if (
            evidence.device_id != authority.device_id
            or evidence.inode != authority.inode
            or evidence.mode != authority.mode
            or evidence.qemu_img_version != authority.qemu_img_version
            or (
                not mutable_runtime
                and (
                    evidence.file_size_bytes != authority.file_size_bytes
                    or evidence.allocated_size_bytes
                    != authority.allocated_size_bytes
                    or evidence.chain_sha256 != authority.chain_sha256
                )
            )
        ):
            raise ImageIntegrityError(
                "overlay filesystem truth disagrees with the registry"
            )
        _, marker_details = require_regular_file(
            marker_path,
            "overlay ownership marker",
            unique_link=True,
        )
        if (
            marker_details.st_uid != os.geteuid()
            or _mode(marker_details) != 0o600
        ):
            raise ImageOwnershipError(
                "overlay ownership marker permissions are unsafe"
            )
        return evidence

    @staticmethod
    def _cleanup_stage(stage: Path, allowed_names: frozenset[str]) -> None:
        try:
            details = stage.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ImageOwnershipError("operation stage cannot be observed") from exc
        if not stat.S_ISDIR(details.st_mode):
            raise ImageOwnershipError("operation stage is not a directory")
        try:
            entries = tuple(os.scandir(stage))
        except OSError as exc:
            raise ImageOwnershipError("operation stage cannot be scanned") from exc
        for entry in entries:
            if entry.name not in allowed_names or entry.is_symlink():
                raise ImageOwnershipError(
                    "operation stage contains an unowned entry"
                )
            try:
                entry_details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ImageOwnershipError(
                    "operation stage entry cannot be observed"
                ) from exc
            if not stat.S_ISREG(entry_details.st_mode) or entry_details.st_nlink != 1:
                raise ImageOwnershipError(
                    "operation stage entry has ambiguous ownership"
                )
            try:
                os.unlink(entry.path)
            except OSError as exc:
                raise ImageIntegrityError(
                    "operation stage entry could not be removed"
                ) from exc
        try:
            stage.rmdir()
        except OSError as exc:
            raise ImageIntegrityError("operation stage could not be removed") from exc
        _fsync_directory(stage.parent)
        try:
            stage.parent.rmdir()
        except OSError as exc:
            # The shared .staging parent may own other operation directories.
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise ImageIntegrityError(
                    "shared staging directory could not be settled"
                ) from exc
        else:
            _fsync_directory(stage.parent.parent)


__all__ = [
    "BaseImageEvidence",
    "ImageStore",
    "OverlayCreateIntent",
    "OverlayEvidence",
    "RegisteredBase",
    "RegisteredOverlay",
    "STORAGE_MARKER_SCHEMA_VERSION",
    "StorageCheckpoint",
    "StorageResumeState",
]
