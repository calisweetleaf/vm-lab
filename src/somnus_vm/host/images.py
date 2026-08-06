"""Verified qcow2 manifests and the shell-free ``qemu-img`` boundary.

This module owns image *observation* and primitive creation only.  It does not
own VM records, operation journals, publication policy, QEMU processes, guest
installation, or deletion.  :mod:`somnus_vm.host.storage` composes these
primitives under the single daemon/registry mutation authority.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final
from uuid import UUID


IMAGE_MANIFEST_SCHEMA_VERSION: Final[int] = 1
DEFAULT_IMAGE_COMMAND_TIMEOUT_SECONDS: Final[float] = 120.0
DEFAULT_IMAGE_OUTPUT_LIMIT_BYTES: Final[int] = 1_048_576
DEFAULT_AIPC_VIRTUAL_SIZE_BYTES: Final[int] = 100 * 1024**3
P3_SPARSE_ALLOCATION_DIVISOR: Final[int] = 64
P3_ALLOCATION_OBSERVER_TOLERANCE_BYTES: Final[int] = 16 * 1024**2
_MAX_PATH_BYTES: Final[int] = 4_096
_MAX_TEXT_LENGTH: Final[int] = 4_096
_MAX_TOOL_ENTRIES: Final[int] = 32
_MAX_PAYLOAD_ENTRIES: Final[int] = 1_024
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
)
_ARCHITECTURES: Final[frozenset[str]] = frozenset(
    {"x86_64", "aarch64", "riscv64"}
)
_IMAGE_FORMATS: Final[frozenset[str]] = frozenset({"qcow2"})
_MANIFEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "image_id",
        "origin",
        "sha256",
        "size_bytes",
        "format",
        "virtual_size_bytes",
        "backing_chain",
        "architecture",
        "os_family",
        "os_release",
        "os_variant",
        "created_at",
        "tool_versions",
        "guest_payload_manifest",
    }
)


class ImageError(RuntimeError):
    """Base class for closed image/storage primitive failures."""


class ImageManifestError(ImageError):
    """Raised when a manifest is ambiguous, unsupported, or noncanonical."""


class ImageIntegrityError(ImageError):
    """Raised when measured image bytes or structure disagree with authority."""


class ImageOwnershipError(ImageError):
    """Raised when a path or inode cannot have one unambiguous owner."""


class QemuImgError(ImageError):
    """Raised when the observed ``qemu-img`` boundary fails."""


def _strict_object(
    value: object,
    keys: frozenset[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise ImageManifestError(f"{label} must contain exactly {sorted(keys)}")
    if any(not isinstance(key, str) for key in value):
        raise ImageManifestError(f"{label} keys must be strings")
    return value


def _strict_string(
    value: object,
    label: str,
    *,
    maximum: int = _MAX_TEXT_LENGTH,
    token: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ImageManifestError(f"{label} is invalid")
    if token and not _TOKEN_PATTERN.fullmatch(value):
        raise ImageManifestError(f"{label} must be a lowercase machine token")
    return value


def _strict_int(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise ImageManifestError(f"{label} is outside the supported bound")
    return value


def _strict_sha256(value: object, label: str) -> str:
    selected = _strict_string(value, label, maximum=64)
    if not _SHA256_PATTERN.fullmatch(selected):
        raise ImageManifestError(f"{label} must be a lowercase SHA256 digest")
    return selected


def _strict_uuid(value: object, label: str) -> UUID:
    try:
        selected = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ImageManifestError(f"{label} must be a UUID") from exc
    if selected.int == 0:
        raise ImageManifestError(f"{label} must be nonnil")
    return selected


def _strict_time(value: object, label: str) -> str:
    text = _strict_string(value, label, maximum=64)
    if not text.endswith("Z"):
        raise ImageManifestError(f"{label} must be canonical UTC with Z")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as exc:
        raise ImageManifestError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ImageManifestError(f"{label} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    if text != canonical:
        raise ImageManifestError(f"{label} must use canonical microsecond UTC")
    return text


def canonical_json_bytes(value: object) -> bytes:
    """Encode one deterministic, bounded image authority payload."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ImageManifestError("image metadata is not canonical JSON") from exc
    if not encoded or len(encoded) > DEFAULT_IMAGE_OUTPUT_LIMIT_BYTES:
        raise ImageManifestError("image metadata exceeds the supported bound")
    return encoded


def _decode_json(payload: str | bytes, label: str) -> object:
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raise ImageManifestError(f"{label} must be JSON text")
    if not raw or len(raw) > DEFAULT_IMAGE_OUTPUT_LIMIT_BYTES:
        raise ImageManifestError(f"{label} exceeds the supported bound")

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("non-finite JSON value")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ImageManifestError(f"{label} is not strict JSON") from exc


@dataclass(frozen=True, slots=True)
class ToolVersion:
    """One tool identity bound into an image manifest."""

    name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _strict_string(self.name, "tool_versions.name", maximum=64, token=True),
        )
        object.__setattr__(
            self,
            "version",
            _strict_string(self.version, "tool_versions.version", maximum=512),
        )

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_dict(cls, value: object) -> "ToolVersion":
        row = _strict_object(
            value,
            frozenset({"name", "version"}),
            "tool version",
        )
        return cls(name=row["name"], version=row["version"])


@dataclass(frozen=True, slots=True)
class GuestPayloadManifest:
    """One guest payload expected to be resident in an image."""

    name: str
    version: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _strict_string(
                self.name,
                "guest_payload_manifest.name",
                maximum=128,
                token=True,
            ),
        )
        object.__setattr__(
            self,
            "version",
            _strict_string(
                self.version,
                "guest_payload_manifest.version",
                maximum=128,
            ),
        )
        object.__setattr__(
            self,
            "sha256",
            _strict_sha256(
                self.sha256,
                "guest_payload_manifest.sha256",
            ),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _strict_int(
                self.size_bytes,
                "guest_payload_manifest.size_bytes",
                minimum=1,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, value: object) -> "GuestPayloadManifest":
        row = _strict_object(
            value,
            frozenset({"name", "version", "sha256", "size_bytes"}),
            "guest payload manifest entry",
        )
        return cls(
            name=row["name"],
            version=row["version"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
        )


@dataclass(frozen=True, slots=True)
class BackingImageManifest:
    """Portable identity for one expected backing ancestor."""

    sha256: str
    format: str
    virtual_size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sha256",
            _strict_sha256(self.sha256, "backing_chain.sha256"),
        )
        selected_format = _strict_string(
            self.format,
            "backing_chain.format",
            maximum=32,
            token=True,
        )
        if selected_format not in _IMAGE_FORMATS:
            raise ImageManifestError("backing_chain.format is unsupported")
        object.__setattr__(self, "format", selected_format)
        object.__setattr__(
            self,
            "virtual_size_bytes",
            _strict_int(
                self.virtual_size_bytes,
                "backing_chain.virtual_size_bytes",
                minimum=1,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "format": self.format,
            "virtual_size_bytes": self.virtual_size_bytes,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BackingImageManifest":
        row = _strict_object(
            value,
            frozenset({"sha256", "format", "virtual_size_bytes"}),
            "backing-chain entry",
        )
        return cls(
            sha256=row["sha256"],
            format=row["format"],
            virtual_size_bytes=row["virtual_size_bytes"],
        )


@dataclass(frozen=True, slots=True)
class ImageManifest:
    """Versioned authority for exact immutable base-image bytes."""

    schema_version: int
    image_id: UUID
    origin: str
    sha256: str
    size_bytes: int
    format: str
    virtual_size_bytes: int
    backing_chain: tuple[BackingImageManifest, ...]
    architecture: str
    os_family: str
    os_release: str
    os_variant: str
    created_at: str
    tool_versions: tuple[ToolVersion, ...]
    guest_payload_manifest: tuple[GuestPayloadManifest, ...]

    def __post_init__(self) -> None:
        if self.schema_version != IMAGE_MANIFEST_SCHEMA_VERSION:
            raise ImageManifestError("image manifest schema version is unsupported")
        object.__setattr__(
            self,
            "image_id",
            _strict_uuid(self.image_id, "image_id"),
        )
        object.__setattr__(
            self,
            "origin",
            _strict_string(self.origin, "origin"),
        )
        object.__setattr__(
            self,
            "sha256",
            _strict_sha256(self.sha256, "sha256"),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _strict_int(self.size_bytes, "size_bytes", minimum=1),
        )
        selected_format = _strict_string(
            self.format,
            "format",
            maximum=32,
            token=True,
        )
        if selected_format not in _IMAGE_FORMATS:
            raise ImageManifestError("image format is unsupported")
        object.__setattr__(self, "format", selected_format)
        object.__setattr__(
            self,
            "virtual_size_bytes",
            _strict_int(
                self.virtual_size_bytes,
                "virtual_size_bytes",
                minimum=1,
            ),
        )
        backing_chain = tuple(self.backing_chain)
        if len(backing_chain) > 64 or any(
            not isinstance(item, BackingImageManifest) for item in backing_chain
        ):
            raise ImageManifestError("backing_chain is invalid")
        object.__setattr__(self, "backing_chain", backing_chain)
        architecture = _strict_string(
            self.architecture,
            "architecture",
            maximum=32,
            token=True,
        )
        if architecture not in _ARCHITECTURES:
            raise ImageManifestError("architecture is unsupported")
        object.__setattr__(self, "architecture", architecture)
        for field_name in ("os_family", "os_release", "os_variant"):
            object.__setattr__(
                self,
                field_name,
                _strict_string(
                    getattr(self, field_name),
                    field_name,
                    maximum=128,
                    token=field_name in {"os_family", "os_variant"},
                ),
            )
        object.__setattr__(
            self,
            "created_at",
            _strict_time(self.created_at, "created_at"),
        )
        tools = tuple(self.tool_versions)
        if (
            not tools
            or len(tools) > _MAX_TOOL_ENTRIES
            or any(not isinstance(item, ToolVersion) for item in tools)
            or len({item.name for item in tools}) != len(tools)
            or tuple(sorted(tools, key=lambda item: item.name)) != tools
        ):
            raise ImageManifestError(
                "tool_versions must be nonempty, unique, and sorted by name"
            )
        object.__setattr__(self, "tool_versions", tools)
        payloads = tuple(self.guest_payload_manifest)
        if (
            len(payloads) > _MAX_PAYLOAD_ENTRIES
            or any(not isinstance(item, GuestPayloadManifest) for item in payloads)
            or len({item.name for item in payloads}) != len(payloads)
            or tuple(sorted(payloads, key=lambda item: item.name)) != payloads
        ):
            raise ImageManifestError(
                "guest_payload_manifest must be unique and sorted by name"
            )
        object.__setattr__(self, "guest_payload_manifest", payloads)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "image_id": str(self.image_id),
            "origin": self.origin,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "virtual_size_bytes": self.virtual_size_bytes,
            "backing_chain": [item.to_dict() for item in self.backing_chain],
            "architecture": self.architecture,
            "os_family": self.os_family,
            "os_release": self.os_release,
            "os_variant": self.os_variant,
            "created_at": self.created_at,
            "tool_versions": [item.to_dict() for item in self.tool_versions],
            "guest_payload_manifest": [
                item.to_dict() for item in self.guest_payload_manifest
            ],
        }

    def to_json(self) -> str:
        return canonical_json_bytes(self.to_dict()).decode("utf-8")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def manifest_sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> "ImageManifest":
        row = _strict_object(value, _MANIFEST_KEYS, "image manifest")
        backing = row["backing_chain"]
        tools = row["tool_versions"]
        payloads = row["guest_payload_manifest"]
        if not isinstance(backing, list):
            raise ImageManifestError("backing_chain must be an array")
        if not isinstance(tools, list):
            raise ImageManifestError("tool_versions must be an array")
        if not isinstance(payloads, list):
            raise ImageManifestError("guest_payload_manifest must be an array")
        return cls(
            schema_version=row["schema_version"],
            image_id=row["image_id"],
            origin=row["origin"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            format=row["format"],
            virtual_size_bytes=row["virtual_size_bytes"],
            backing_chain=tuple(BackingImageManifest.from_dict(item) for item in backing),
            architecture=row["architecture"],
            os_family=row["os_family"],
            os_release=row["os_release"],
            os_variant=row["os_variant"],
            created_at=row["created_at"],
            tool_versions=tuple(ToolVersion.from_dict(item) for item in tools),
            guest_payload_manifest=tuple(
                GuestPayloadManifest.from_dict(item) for item in payloads
            ),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "ImageManifest":
        return cls.from_dict(_decode_json(payload, "image manifest"))


def canonical_path(
    value: str | Path,
    label: str,
    *,
    must_exist: bool,
) -> Path:
    """Return one absolute symlink-free path or fail closed."""

    try:
        path = Path(value)
        text = os.fspath(path)
    except (TypeError, ValueError) as exc:
        raise ImageOwnershipError(f"{label} is invalid") from exc
    if (
        not path.is_absolute()
        or path == Path("/")
        or "\x00" in text
        or len(os.fsencode(path)) > _MAX_PATH_BYTES
        or PurePosixPath(text) != path
    ):
        raise ImageOwnershipError(
            f"{label} must be a canonical absolute non-root path"
        )
    try:
        resolved = path.resolve(strict=must_exist)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise ImageOwnershipError(f"{label} cannot be resolved") from exc
    if resolved != path:
        raise ImageOwnershipError(f"{label} must not contain a symlink")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ImageOwnershipError(f"{label} contains a control character")
    return path


def require_regular_file(
    path: str | Path,
    label: str,
    *,
    unique_link: bool,
) -> tuple[Path, os.stat_result]:
    selected = canonical_path(path, label, must_exist=True)
    try:
        details = selected.stat(follow_symlinks=False)
    except OSError as exc:
        raise ImageOwnershipError(f"{label} cannot be observed") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ImageOwnershipError(f"{label} must be a regular file")
    if unique_link and details.st_nlink != 1:
        raise ImageOwnershipError(f"{label} has a hard-link alias")
    return selected, details


def hash_file(path: str | Path, *, unique_link: bool = False) -> tuple[str, int]:
    """Hash one regular file through one no-follow descriptor."""

    selected = canonical_path(path, "image path", must_exist=True)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(selected, flags)
    except OSError as exc:
        raise ImageIntegrityError("image bytes cannot be opened") from exc
    digest = sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ImageOwnershipError("image path must be a regular file")
        if unique_link and before.st_nlink != 1:
            raise ImageOwnershipError("image path has a hard-link alias")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or size != after.st_size
        ):
            raise ImageIntegrityError("image bytes changed during hashing")
    except OSError as exc:
        raise ImageIntegrityError("image bytes could not be hashed") from exc
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _stable_identity(
    before: os.stat_result,
    after: os.stat_result,
    *,
    include_mode: bool,
) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
        and before.st_nlink == after.st_nlink
        and getattr(before, "st_blocks", None)
        == getattr(after, "st_blocks", None)
        and (
            not include_mode
            or stat.S_IMODE(before.st_mode) == stat.S_IMODE(after.st_mode)
        )
    )


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Strict projection of one ``qemu-img info`` chain member."""

    filename: Path
    format: str
    virtual_size_bytes: int
    actual_size_bytes: int
    backing_filename: Path | None
    dirty: bool
    corrupt: bool


@dataclass(frozen=True, slots=True)
class QemuImgCommand:
    """Bounded observed result from one argv-only qemu-img process."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


class QemuImgBackend:
    """One bounded, shell-free interface to an observed ``qemu-img`` binary."""

    def __init__(
        self,
        binary: str = "qemu-img",
        *,
        timeout_seconds: float = DEFAULT_IMAGE_COMMAND_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_IMAGE_OUTPUT_LIMIT_BYTES,
    ) -> None:
        if (
            not isinstance(binary, str)
            or not binary
            or "\x00" in binary
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in binary
            )
        ):
            raise QemuImgError("qemu-img binary is invalid")
        if "/" in binary or "\\" in binary:
            candidate = Path(binary)
            try:
                resolved_candidate = candidate.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise QemuImgError(
                    "qemu-img binary path cannot be resolved"
                ) from exc
            if (
                "\\" in binary
                or not candidate.is_absolute()
                or candidate == Path("/")
                or resolved_candidate != candidate
            ):
                raise QemuImgError(
                    "qemu-img binary path must be canonical and absolute"
                )
            located = os.fspath(candidate)
        else:
            located = shutil.which(binary)
            if located is None:
                raise QemuImgError("qemu-img executable is unavailable")
        selected, details = require_regular_file(
            Path(located).resolve(),
            "qemu-img binary",
            unique_link=False,
        )
        if not os.access(selected, os.X_OK) or details.st_mode & 0o022:
            raise QemuImgError("qemu-img executable permissions are unsafe")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0.1 <= float(timeout_seconds) <= 3_600.0
        ):
            raise QemuImgError("qemu-img timeout is outside the supported bound")
        if (
            not isinstance(output_limit_bytes, int)
            or isinstance(output_limit_bytes, bool)
            or not 4_096 <= output_limit_bytes <= 16_777_216
        ):
            raise QemuImgError(
                "qemu-img output limit is outside the supported bound"
            )
        self.binary = selected
        self.timeout_seconds = float(timeout_seconds)
        self.output_limit_bytes = output_limit_bytes
        self.version = self._probe_version()

    def _probe_version(self) -> str:
        result = self._run(("--version",))
        try:
            first_line = result.stdout.decode("utf-8").splitlines()[0]
        except (UnicodeDecodeError, IndexError) as exc:
            raise QemuImgError("qemu-img version output is invalid") from exc
        if not first_line.startswith("qemu-img version ") or len(first_line) > 512:
            raise QemuImgError("qemu-img version output is unsupported")
        return first_line

    def _run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> QemuImgCommand:
        argv = (os.fspath(self.binary), *arguments)
        if any(
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            for argument in argv
        ):
            raise QemuImgError("qemu-img argv is invalid")
        timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        try:
            guarded_argv = (
                sys.executable,
                os.fspath(Path(__file__).with_name("exec_guard.py")),
                str(os.getpid()),
                *argv,
            )
            process = subprocess.Popen(
                guarded_argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                pass_fds=pass_fds,
                start_new_session=True,
            )
        except OSError as exc:
            raise QemuImgError("qemu-img could not be started") from exc
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
        output = {
            stdout_fd: bytearray(),
            stderr_fd: bytearray(),
        }
        selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        failure: QemuImgError | None = None
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = QemuImgError(
                        "qemu-img exceeded its absolute timeout"
                    )
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    descriptor = key.fileobj.fileno()
                    try:
                        block = os.read(descriptor, 65_536)
                    except BlockingIOError:
                        continue
                    if not block:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    output[descriptor].extend(block)
                    if len(output[descriptor]) > self.output_limit_bytes:
                        failure = QemuImgError(
                            "qemu-img output exceeded the supported bound"
                        )
                        break
                if failure is not None:
                    break
            if failure is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired as exc:
                        raise QemuImgError(
                            "qemu-img could not be reaped after SIGKILL"
                        ) from exc
                raise failure
            returncode = process.wait(
                timeout=max(0.1, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired as kill_exc:
                raise QemuImgError(
                    "qemu-img could not be reaped after SIGKILL"
                ) from kill_exc
            raise QemuImgError("qemu-img exceeded its absolute timeout") from exc
        finally:
            selector.close()
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
        result = QemuImgCommand(
            argv=argv,
            returncode=returncode,
            stdout=bytes(output[stdout_fd]),
            stderr=bytes(output[stderr_fd]),
        )
        if result.returncode != 0:
            raise QemuImgError(
                f"qemu-img command failed with exit status {result.returncode}"
            )
        return result

    @staticmethod
    def _json_output(result: QemuImgCommand, label: str) -> object:
        try:
            raw = result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImageIntegrityError(f"{label} output is not UTF-8") from exc
        try:
            return _decode_json(raw, label)
        except ImageManifestError as exc:
            raise ImageIntegrityError(f"{label} output is invalid") from exc

    def inspect(self, path: str | Path) -> tuple[ImageInfo, ...]:
        selected, _ = require_regular_file(
            path,
            "image path",
            unique_link=False,
        )
        result = self._run(
            (
                "info",
                "--output=json",
                "--backing-chain",
                "--force-share",
                os.fspath(selected),
            )
        )
        decoded = self._json_output(result, "qemu-img info")
        if not isinstance(decoded, list) or not decoded or len(decoded) > 64:
            raise ImageIntegrityError("qemu-img backing chain is invalid")
        entries: list[ImageInfo] = []
        for index, value in enumerate(decoded):
            if not isinstance(value, dict):
                raise ImageIntegrityError("qemu-img info entry is invalid")
            filename = value.get("filename")
            image_format = value.get("format")
            virtual_size = value.get("virtual-size")
            actual_size = value.get("actual-size")
            dirty = value.get("dirty-flag", False)
            if (
                not isinstance(filename, str)
                or not isinstance(image_format, str)
                or not isinstance(virtual_size, int)
                or isinstance(virtual_size, bool)
                or virtual_size < 1
                or not isinstance(actual_size, int)
                or isinstance(actual_size, bool)
                or actual_size < 0
                or not isinstance(dirty, bool)
            ):
                raise ImageIntegrityError("qemu-img info fields are invalid")
            selected_filename = canonical_path(
                filename,
                f"qemu-img chain[{index}].filename",
                must_exist=True,
            )
            backing_value = value.get(
                "full-backing-filename",
                value.get("backing-filename"),
            )
            backing_path = None
            if backing_value is not None:
                if not isinstance(backing_value, str):
                    raise ImageIntegrityError(
                        "qemu-img backing filename is invalid"
                    )
                candidate = Path(backing_value)
                if not candidate.is_absolute():
                    candidate = selected_filename.parent / candidate
                backing_path = canonical_path(
                    candidate.resolve(strict=True),
                    f"qemu-img chain[{index}].backing_filename",
                    must_exist=True,
                )
            format_specific = value.get("format-specific")
            corrupt = False
            if isinstance(format_specific, dict):
                data = format_specific.get("data")
                if isinstance(data, dict):
                    raw_corrupt = data.get("corrupt", False)
                    if not isinstance(raw_corrupt, bool):
                        raise ImageIntegrityError(
                            "qemu-img corruption flag is invalid"
                        )
                    corrupt = raw_corrupt
            entries.append(
                ImageInfo(
                    filename=selected_filename,
                    format=image_format,
                    virtual_size_bytes=virtual_size,
                    actual_size_bytes=actual_size,
                    backing_filename=backing_path,
                    dirty=dirty,
                    corrupt=corrupt,
                )
            )
        if entries[0].filename != selected:
            raise ImageIntegrityError(
                "qemu-img reported a different primary image path"
            )
        for index, entry in enumerate(entries[:-1]):
            if entry.backing_filename != entries[index + 1].filename:
                raise ImageIntegrityError(
                    "qemu-img backing chain is noncontiguous"
                )
        if entries[-1].backing_filename is not None:
            raise ImageIntegrityError("qemu-img backing chain is incomplete")
        return tuple(entries)

    def check(self, path: str | Path) -> dict[str, object]:
        selected, _ = require_regular_file(
            path,
            "image path",
            unique_link=False,
        )
        result = self._run(
            (
                "check",
                "--output=json",
                os.fspath(selected),
            )
        )
        decoded = self._json_output(result, "qemu-img check")
        if not isinstance(decoded, dict):
            raise ImageIntegrityError("qemu-img check result is invalid")
        errors = decoded.get("check-errors", 0)
        corruptions = decoded.get("corruptions", 0)
        leaks = decoded.get("leaks", 0)
        for label, value in (
            ("check-errors", errors),
            ("corruptions", corruptions),
            ("leaks", leaks),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ImageIntegrityError(f"qemu-img {label} is invalid")
        if errors or corruptions or leaks:
            raise ImageIntegrityError("qemu-img check reported image damage")
        return decoded

    def verify_base(
        self,
        path: str | Path,
        manifest: ImageManifest,
    ) -> tuple[ImageInfo, ...]:
        if not isinstance(manifest, ImageManifest):
            raise TypeError("manifest must be ImageManifest")
        selected, details = require_regular_file(
            path,
            "base image",
            unique_link=True,
        )
        digest, byte_size = hash_file(selected, unique_link=True)
        if digest != manifest.sha256 or byte_size != manifest.size_bytes:
            raise ImageIntegrityError(
                "base image bytes disagree with the manifest"
            )
        chain = self.inspect(selected)
        if (
            len(chain) != 1
            or manifest.backing_chain
            or chain[0].format != manifest.format
            or chain[0].virtual_size_bytes != manifest.virtual_size_bytes
            or chain[0].backing_filename is not None
            or chain[0].dirty
            or chain[0].corrupt
        ):
            raise ImageIntegrityError(
                "base image structure disagrees with the manifest"
            )
        if details.st_size != manifest.size_bytes:
            raise ImageIntegrityError("base image byte size changed")
        self.check(selected)
        try:
            after = selected.stat(follow_symlinks=False)
        except OSError as exc:
            raise ImageIntegrityError(
                "base image identity cannot be re-observed"
            ) from exc
        if not _stable_identity(details, after, include_mode=True):
            raise ImageIntegrityError(
                "base image identity changed during verification"
            )
        return chain

    def create_overlay(
        self,
        destination: str | Path,
        base_image: str | Path,
        *,
        virtual_size_bytes: int = DEFAULT_AIPC_VIRTUAL_SIZE_BYTES,
        operation_lock_fd: int | None = None,
    ) -> None:
        destination_path = canonical_path(
            destination,
            "overlay destination",
            must_exist=False,
        )
        base_path, _ = require_regular_file(
            base_image,
            "base image",
            unique_link=True,
        )
        size = _strict_int(
            virtual_size_bytes,
            "virtual_size_bytes",
            minimum=1024**3,
            maximum=16 * 1024**4,
        )
        if (
            operation_lock_fd is not None
            and (
                not isinstance(operation_lock_fd, int)
                or isinstance(operation_lock_fd, bool)
                or operation_lock_fd < 0
            )
        ):
            raise QemuImgError("operation lock descriptor is invalid")
        if destination_path.exists():
            raise ImageOwnershipError("overlay destination already exists")
        pass_fds = (
            ()
            if operation_lock_fd is None
            else (operation_lock_fd,)
        )
        self._run(
            (
                "create",
                "-q",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                os.fspath(base_path),
                "-o",
                "lazy_refcounts=on",
                os.fspath(destination_path),
                str(size),
            ),
            pass_fds=pass_fds,
        )

    def verify_overlay(
        self,
        path: str | Path,
        base_image: str | Path,
        base_manifest: ImageManifest,
        *,
        virtual_size_bytes: int,
        require_initial_sparse: bool = True,
    ) -> tuple[ImageInfo, ...]:
        if not isinstance(require_initial_sparse, bool):
            raise TypeError("require_initial_sparse must be boolean")
        selected, details = require_regular_file(
            path,
            "overlay image",
            unique_link=True,
        )
        base_path, _ = require_regular_file(
            base_image,
            "base image",
            unique_link=True,
        )
        self.verify_base(base_path, base_manifest)
        chain = self.inspect(selected)
        if (
            len(chain) != 2
            or chain[0].format != "qcow2"
            or chain[0].virtual_size_bytes != virtual_size_bytes
            or chain[0].backing_filename != base_path
            or chain[0].dirty
            or chain[0].corrupt
            or chain[1].filename != base_path
            or chain[1].format != base_manifest.format
            or chain[1].virtual_size_bytes != base_manifest.virtual_size_bytes
            or chain[1].backing_filename is not None
            or chain[1].dirty
            or chain[1].corrupt
        ):
            raise ImageIntegrityError(
                "overlay backing chain disagrees with its authority"
            )
        if require_initial_sparse:
            blocks = getattr(details, "st_blocks", None)
            if not isinstance(blocks, int) or blocks < 0:
                raise ImageIntegrityError(
                    "overlay filesystem allocation cannot be observed"
                )
            filesystem_allocation = blocks * 512
            qemu_allocation = chain[0].actual_size_bytes
            sparse_limit = virtual_size_bytes // P3_SPARSE_ALLOCATION_DIVISOR
            if (
                details.st_size >= sparse_limit
                or filesystem_allocation >= sparse_limit
                or qemu_allocation >= sparse_limit
            ):
                raise ImageIntegrityError(
                    "overlay is unexpectedly allocated rather than sparse"
                )
            if (
                abs(filesystem_allocation - qemu_allocation)
                > P3_ALLOCATION_OBSERVER_TOLERANCE_BYTES
            ):
                raise ImageIntegrityError(
                    "overlay allocation observers materially disagree"
                )
        self.check(selected)
        try:
            after = selected.stat(follow_symlinks=False)
        except OSError as exc:
            raise ImageIntegrityError(
                "overlay image identity cannot be re-observed"
            ) from exc
        if not _stable_identity(details, after, include_mode=True):
            raise ImageIntegrityError(
                "overlay image identity changed during verification"
            )
        return chain


__all__ = [
    "BackingImageManifest",
    "DEFAULT_AIPC_VIRTUAL_SIZE_BYTES",
    "DEFAULT_IMAGE_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_IMAGE_OUTPUT_LIMIT_BYTES",
    "GuestPayloadManifest",
    "IMAGE_MANIFEST_SCHEMA_VERSION",
    "ImageError",
    "ImageInfo",
    "ImageIntegrityError",
    "ImageManifest",
    "ImageManifestError",
    "ImageOwnershipError",
    "P3_ALLOCATION_OBSERVER_TOLERANCE_BYTES",
    "P3_SPARSE_ALLOCATION_DIVISOR",
    "QemuImgBackend",
    "QemuImgCommand",
    "QemuImgError",
    "ToolVersion",
    "canonical_json_bytes",
    "canonical_path",
    "hash_file",
    "require_regular_file",
]
