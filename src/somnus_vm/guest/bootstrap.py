"""Bounded, hash-verified, traversal-safe guest payload placement.

Source: vm_bootstrap.py lineage
Integrated: 2026-08-04
Purpose: Places immutable local payload bytes without pathname re-read races,
    shell/service mutation, archive bombs, collisions, or no-op success.
IO: Reads TOML and local payloads, copies each once into a private verified
    area, stages bounded content, atomically places one target, and writes state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Final
import stat
import tarfile
import tomllib


_SAFE_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RECOVERY_FILE: Final[str] = ".somnus-bootstrap-state.json"
_TARGET_MODE: Final[int] = 0o755
_MAX_PAYLOAD_BYTES: Final[int] = 128 * 1024**3
_MAX_ARCHIVE_MEMBERS: Final[int] = 100_000


class BootstrapError(RuntimeError):
    """Raised when guest bootstrap cannot finish without weakening integrity."""


@dataclass(frozen=True, slots=True)
class PayloadSource:
    """One immutable local payload and its trusted byte contract."""

    name: str
    sha256: str
    archive: str
    size_bytes: int
    unpacked_size_bytes: int
    local_paths: tuple[Path, ...]
    raw_mode: int = 0o644

    def __post_init__(self) -> None:
        """Validate direct construction as strictly as TOML loading."""

        if not _SAFE_NAME.fullmatch(self.name) or self.name == _RECOVERY_FILE:
            raise BootstrapError(f"Unsafe payload name: {self.name!r}")
        if len(self.sha256) != 64 or any(character not in "0123456789abcdef" for character in self.sha256):
            raise BootstrapError(f"Invalid SHA256 for payload {self.name}")
        if self.archive not in {"raw", "zip", "tar.gz"}:
            raise BootstrapError(f"Unsupported archive type: {self.archive}")
        for key, value in (("size_bytes", self.size_bytes), ("unpacked_size_bytes", self.unpacked_size_bytes)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > _MAX_PAYLOAD_BYTES:
                raise BootstrapError(f"{self.name}.{key} must be between 0 and {_MAX_PAYLOAD_BYTES}")
        if self.archive == "raw" and self.size_bytes != self.unpacked_size_bytes:
            raise BootstrapError(f"Raw payload {self.name} must have equal packed and unpacked sizes")
        if not self.local_paths or any(not path.is_absolute() for path in self.local_paths):
            raise BootstrapError(f"Payload {self.name} requires absolute local_paths")
        if self.raw_mode < 0o600 or self.raw_mode > 0o755:
            raise BootstrapError(f"Payload {self.name} raw_mode must be between 0600 and 0755")


@dataclass(frozen=True, slots=True)
class BootstrapConfiguration:
    """Resolved local, payload-only guest bootstrap configuration."""

    target_dir: Path
    marker_file: Path
    payloads: tuple[PayloadSource, ...]

    def __post_init__(self) -> None:
        """Reject no-op, relative, overlapping, and ambiguous payload intent."""

        if not self.payloads:
            raise BootstrapError("At least one verified payload is required")
        if not self.target_dir.is_absolute() or not self.marker_file.is_absolute():
            raise BootstrapError("target_dir and marker_file must be absolute")
        if self.marker_file.resolve().is_relative_to(self.target_dir.resolve()):
            raise BootstrapError("marker_file must live outside target_dir")
        names = [source.name for source in self.payloads]
        if len(names) != len(set(names)):
            raise BootstrapError("Payload names must be unique")


def _required_string(table: dict[str, object], key: str) -> str:
    """Read one non-empty configuration string."""

    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BootstrapError(f"Missing non-empty configuration value: {key}")
    return value.strip()


def _required_int(table: dict[str, object], key: str) -> int:
    """Read one strict TOML integer."""

    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise BootstrapError(f"{key} must be an integer")
    return value


def _safe_name(table: dict[str, object], key: str) -> str:
    """Read one path-safe payload name."""

    value = _required_string(table, key)
    if not _SAFE_NAME.fullmatch(value) or value == _RECOVERY_FILE:
        raise BootstrapError(f"{key} must contain only safe filename characters")
    return value


def _absolute_path(value: object, key: str) -> Path:
    """Require an absolute guest path without control characters."""

    if not isinstance(value, str) or not value.strip():
        raise BootstrapError(f"{key} must be a non-empty path string")
    if any(character in value for character in ("\n", "\r", "\0")):
        raise BootstrapError(f"{key} contains an unsafe character")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise BootstrapError(f"{key} must be absolute")
    return path.resolve()


def _raw_mode(value: object, key: str) -> int:
    """Parse an optional four-digit octal raw-file mode."""

    if value is None:
        return 0o644
    if not isinstance(value, str) or not re.fullmatch(r"0[0-7]{3}", value):
        raise BootstrapError(f"{key} must be an octal string from 0600 through 0755")
    mode = int(value, 8)
    if mode < 0o600 or mode > 0o755:
        raise BootstrapError(f"{key} must be an octal string from 0600 through 0755")
    return mode


def load_bootstrap_configuration(path: str | Path) -> BootstrapConfiguration:
    """Load and validate a bounded local-payload TOML configuration."""

    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise BootstrapError(f"Bootstrap configuration not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BootstrapError(f"Invalid bootstrap TOML: {exc}") from exc

    table = payload.get("bootstrap")
    if not isinstance(table, dict):
        raise BootstrapError("Missing [bootstrap] table")
    payload_rows = payload.get("payloads", [])
    if not isinstance(payload_rows, list):
        raise BootstrapError("payloads must be an array")
    forbidden = {
        "allow_network": table.get("allow_network"),
        "cache_dir": table.get("cache_dir"),
        "post_install": table.get("post_install"),
        "services": payload.get("services"),
    }
    enabled_forbidden = [key for key, value in forbidden.items() if value not in (None, False, [])]
    if enabled_forbidden:
        raise BootstrapError(f"Unpromoted bootstrap fields: {', '.join(sorted(enabled_forbidden))}")

    sources: list[PayloadSource] = []
    for index, row in enumerate(payload_rows):
        if not isinstance(row, dict):
            raise BootstrapError(f"payloads[{index}] must be a table")
        local_values = row.get("local_paths", [])
        if not isinstance(local_values, list) or not all(isinstance(item, str) for item in local_values):
            raise BootstrapError(f"payloads[{index}].local_paths must be strings")
        if row.get("urls") not in (None, []):
            raise BootstrapError(f"payloads[{index}].urls is not promoted in local-only bootstrap v0.1")
        sources.append(
            PayloadSource(
                name=_safe_name(row, "name"),
                sha256=_required_string(row, "sha256").lower(),
                archive=str(row.get("archive", "raw")),
                size_bytes=_required_int(row, "size_bytes"),
                unpacked_size_bytes=_required_int(row, "unpacked_size_bytes"),
                local_paths=tuple(
                    _absolute_path(item, f"payloads[{index}].local_paths")
                    for item in local_values
                ),
                raw_mode=_raw_mode(row.get("raw_mode"), f"payloads[{index}].raw_mode"),
            )
        )

    return BootstrapConfiguration(
        target_dir=_absolute_path(table.get("target_dir"), "bootstrap.target_dir"),
        marker_file=_absolute_path(table.get("marker_file"), "bootstrap.marker_file"),
        payloads=tuple(sources),
    )


def _safe_destination(root: Path, member_name: str) -> Path:
    """Resolve one archive member strictly beneath its extraction root."""

    if not member_name or Path(member_name).is_absolute():
        raise BootstrapError(f"Unsafe archive member: {member_name!r}")
    destination = (root / member_name).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise BootstrapError(f"Archive traversal rejected: {member_name}")
    if destination == (root / _RECOVERY_FILE).resolve():
        raise BootstrapError(f"Reserved bootstrap path rejected: {member_name}")
    return destination


def _copy_exact(source: BinaryIO, output: BinaryIO, expected_bytes: int, label: str) -> None:
    """Copy exactly the declared byte count or fail before exceeding it."""

    total = 0
    while True:
        chunk = source.read(min(1024 * 1024, expected_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > expected_bytes:
            raise BootstrapError(f"{label} exceeds declared size {expected_bytes}")
        output.write(chunk)
    if total != expected_bytes:
        raise BootstrapError(f"{label} contains {total} bytes; expected {expected_bytes}")


def _existing_collision(destination: Path, directory: bool, member_name: str) -> None:
    """Reject cross-payload file collisions while allowing shared directories."""

    if not destination.exists():
        return
    if directory and destination.is_dir():
        return
    raise BootstrapError(f"Payload destination collision rejected: {member_name}")


def extract_zip(path: Path, target: Path, expected_bytes: int) -> None:
    """Extract a bounded ZIP after validating all entries and destinations."""

    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise BootstrapError(f"ZIP has {len(members)} members; limit is {_MAX_ARCHIVE_MEMBERS}")
            unpacked = sum(member.file_size for member in members if not member.is_dir())
            if unpacked != expected_bytes:
                raise BootstrapError(f"ZIP expands to {unpacked} bytes; expected {expected_bytes}")
            destinations: set[Path] = set()
            for member in members:
                destination = _safe_destination(target, member.filename)
                if destination in destinations:
                    raise BootstrapError(f"Duplicate ZIP destination rejected: {member.filename}")
                destinations.add(destination)
                mode = (member.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(mode):
                    raise BootstrapError(f"ZIP symbolic link rejected: {member.filename}")
                if mode and not stat.S_ISREG(mode) and not stat.S_ISDIR(mode):
                    raise BootstrapError(f"Non-regular ZIP member rejected: {member.filename}")
                _existing_collision(destination, member.is_dir(), member.filename)
            target.mkdir(parents=True, exist_ok=True)
            for member in members:
                destination = _safe_destination(target, member.filename)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("xb") as output:
                    _copy_exact(source, output, member.file_size, f"ZIP member {member.filename}")
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise BootstrapError(f"Cannot extract ZIP payload {path}: {exc}") from exc


def extract_tar(path: Path, target: Path, expected_bytes: int) -> None:
    """Extract a bounded gzip TAR after validating entries and destinations."""

    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise BootstrapError(f"TAR has {len(members)} members; limit is {_MAX_ARCHIVE_MEMBERS}")
            unpacked = sum(member.size for member in members if member.isfile())
            if unpacked != expected_bytes:
                raise BootstrapError(f"TAR expands to {unpacked} bytes; expected {expected_bytes}")
            destinations: set[Path] = set()
            for member in members:
                destination = _safe_destination(target, member.name)
                if destination in destinations:
                    raise BootstrapError(f"Duplicate TAR destination rejected: {member.name}")
                destinations.add(destination)
                if not member.isdir() and not member.isfile():
                    raise BootstrapError(f"Non-regular TAR member rejected: {member.name}")
                _existing_collision(destination, member.isdir(), member.name)
            target.mkdir(parents=True, exist_ok=True)
            for member in members:
                destination = _safe_destination(target, member.name)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise BootstrapError(f"Cannot read TAR member: {member.name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source, destination.open("xb") as output:
                    _copy_exact(source, output, member.size, f"TAR member {member.name}")
                os.chmod(destination, member.mode & 0o755)
    except (tarfile.TarError, OSError) as exc:
        raise BootstrapError(f"Cannot extract TAR payload {path}: {exc}") from exc


def extract_payload(source: PayloadSource, path: Path, target: Path) -> None:
    """Place one privately verified payload without overwriting peers."""

    if source.archive == "zip":
        extract_zip(path, target, source.unpacked_size_bytes)
    elif source.archive == "tar.gz":
        extract_tar(path, target, source.unpacked_size_bytes)
    else:
        target.mkdir(parents=True, exist_ok=True)
        destination = target / source.name
        _existing_collision(destination, False, source.name)
        with path.open("rb") as verified, destination.open("xb") as output:
            _copy_exact(verified, output, source.size_bytes, f"Raw payload {source.name}")
        os.chmod(destination, source.raw_mode)


def _materialize_verified(source: PayloadSource, verified_dir: Path, index: int) -> Path:
    """Copy a local source once while hashing into a private immutable path."""

    failures: list[str] = []
    destination = verified_dir / f"payload-{index}.bin"
    for candidate in source.local_paths:
        if not candidate.is_file():
            failures.append(f"{candidate}: not a file")
            continue
        digest = hashlib.sha256()
        total = 0
        try:
            with candidate.open("rb") as input_file, destination.open("xb") as output:
                while True:
                    chunk = input_file.read(min(1024 * 1024, source.size_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > source.size_bytes:
                        raise BootstrapError(f"{candidate} exceeds declared size {source.size_bytes}")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            actual_hash = digest.hexdigest()
            if total != source.size_bytes or actual_hash != source.sha256:
                destination.unlink()
                failures.append(
                    f"{candidate}: size/hash mismatch ({total} bytes, sha256 {actual_hash})"
                )
                continue
            os.chmod(destination, 0o600)
            return destination
        except (OSError, BootstrapError) as exc:
            if destination.exists():
                destination.unlink()
            failures.append(f"{candidate}: {exc}")
    raise BootstrapError(f"No verified local payload for {source.name}: {'; '.join(failures)}")


def _configuration_digest(config: BootstrapConfiguration) -> str:
    """Hash the complete idempotency-relevant bootstrap intent."""

    payload = {
        "target_dir": str(config.target_dir),
        "target_mode": _TARGET_MODE,
        "payloads": [
            {
                "name": source.name,
                "sha256": source.sha256,
                "archive": source.archive,
                "size_bytes": source.size_bytes,
                "unpacked_size_bytes": source.unpacked_size_bytes,
                "local_paths": [str(path) for path in source.local_paths],
                "raw_mode": source.raw_mode,
            }
            for source in config.payloads
        ],
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_payload(config: BootstrapConfiguration) -> dict[str, object]:
    """Return recovery and completion evidence for one configuration."""

    return {
        "schema_version": 1,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "configuration_sha256": _configuration_digest(config),
        "payloads": [{"name": source.name, "sha256": source.sha256} for source in config.payloads],
    }


def _validate_state(path: Path, config: BootstrapConfiguration, label: str) -> None:
    """Validate one recovery or completion state file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise BootstrapError(f"{label} has an unsupported schema")
    if payload.get("configuration_sha256") != _configuration_digest(config):
        raise BootstrapError(f"{label} does not match the current configuration")


def _write_json_atomic(path: Path, payload: dict[str, object], mode: int) -> None:
    """Atomically write one durable JSON state file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="bootstrap-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        raise BootstrapError(f"Cannot persist bootstrap state {path}: {exc}") from exc


def _completed(config: BootstrapConfiguration) -> bool:
    """Validate both state layers before treating bootstrap as complete."""

    if not config.marker_file.exists():
        return False
    _validate_state(config.marker_file, config, "Bootstrap marker")
    recovery = config.target_dir / _RECOVERY_FILE
    if not config.target_dir.is_dir() or not recovery.is_file():
        raise BootstrapError("Bootstrap marker exists but placed target state is missing")
    _validate_state(recovery, config, "Placed target state")
    return True


def bootstrap(config: BootstrapConfiguration) -> bool:
    """Install or recover one verified local payload set and report work."""

    if _completed(config):
        return False
    recovery = config.target_dir / _RECOVERY_FILE
    if config.target_dir.exists():
        if not config.target_dir.is_dir():
            raise BootstrapError("target_dir exists but is not a directory")
        if any(config.target_dir.iterdir()):
            if not recovery.is_file():
                raise BootstrapError("target_dir is nonempty and has no recovery state")
            _validate_state(recovery, config, "Placed target state")
            _write_json_atomic(config.marker_file, _state_payload(config), 0o600)
            return True

    config.target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="somnus-work-", dir=config.target_dir.parent) as temporary:
        work = Path(temporary)
        verified_dir = work / "verified"
        content_dir = work / "content"
        verified_dir.mkdir(mode=0o700)
        content_dir.mkdir(mode=_TARGET_MODE)
        for index, source in enumerate(config.payloads):
            verified = _materialize_verified(source, verified_dir, index)
            extract_payload(source, verified, content_dir)
        _write_json_atomic(content_dir / _RECOVERY_FILE, _state_payload(config), 0o600)
        if config.target_dir.exists():
            config.target_dir.rmdir()
        content_dir.replace(config.target_dir)
    _write_json_atomic(config.marker_file, _state_payload(config), 0o600)
    return True


def main() -> int:
    """Run local-payload guest bootstrap from an explicit TOML file."""

    parser = argparse.ArgumentParser(description="Somnus VM bounded local-payload bootstrap")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        ran = bootstrap(load_bootstrap_configuration(args.config))
    except (BootstrapError, OSError) as exc:
        print(f"somnus-guest-bootstrap: {exc}", file=sys.stderr)
        return 2
    print("bootstrap complete" if ran else "bootstrap already complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
