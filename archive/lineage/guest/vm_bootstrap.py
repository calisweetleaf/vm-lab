#!/usr/bin/env python3
"""
Somnus VM Bootstrap (in-VM)
Production-grade bootstrap that seeds the AI environment inside a persistent VM.
Local-first, offline-capable, with optional browser-assisted download and SHA256 verification.
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("somnus_vm_bootstrap")


@dataclass
class PayloadSource:
    name: str
    url: Optional[str] = None
    urls: List[str] = field(default_factory=list)
    local_paths: List[str] = field(default_factory=list)
    sha256: Optional[str] = None
    archive: str = "tar.gz"  # tar.gz or zip or none


@dataclass
class BootstrapConfig:
    target_dir: str = "/opt/somnus"
    cache_dir: str = "/var/lib/somnus/bootstrap/cache"
    marker_file: str = "/opt/somnus/.bootstrap_complete"
    use_ai_vm_browser: bool = True
    payloads: List[PayloadSource] = field(default_factory=list)
    post_install: List[str] = field(default_factory=list)
    create_systemd_units: bool = True
    shell_entry: str = "/opt/somnus/original-python/advanced_ai_shell.py"
    agent_entry: str = "/opt/somnus/somnus_agent.py"
    user: str = "ai"


DEFAULT_CONFIG_PATHS = [
    "/etc/somnus/bootstrap.json",
    "/home/ai/config/bootstrap.json",
    "/opt/somnus/bootstrap.json",
]


def setup_logging() -> None:
    log_dir = Path("/var/log/somnus")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_dir = Path(".")
    log_path = log_dir / "vm_bootstrap.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )


def load_config() -> BootstrapConfig:
    config_data: Dict[str, object] = {}
    for path in DEFAULT_CONFIG_PATHS:
        p = Path(path)
        if p.exists():
            try:
                config_data = json.loads(p.read_text(encoding="utf-8"))
                logger.info("Loaded bootstrap config from %s", p)
                break
            except Exception as exc:
                logger.warning("Failed to read config %s: %s", p, exc)

    payloads = []
    for item in config_data.get("payloads", []):
        try:
            if "urls" not in item and "url" in item:
                item["urls"] = [item["url"]]
            if "local_paths" not in item:
                item["local_paths"] = []
            payloads.append(PayloadSource(**item))
        except Exception as exc:
            logger.warning("Invalid payload config %s: %s", item, exc)

    cfg = BootstrapConfig(
        target_dir=config_data.get("target_dir", "/opt/somnus"),
        cache_dir=config_data.get("cache_dir", "/var/lib/somnus/bootstrap/cache"),
        marker_file=config_data.get("marker_file", "/opt/somnus/.bootstrap_complete"),
        use_ai_vm_browser=bool(config_data.get("use_ai_vm_browser", True)),
        payloads=payloads,
        post_install=config_data.get("post_install", []),
        create_systemd_units=bool(config_data.get("create_systemd_units", True)),
        shell_entry=config_data.get("shell_entry", "/opt/somnus/original-python/advanced_ai_shell.py"),
        agent_entry=config_data.get("agent_entry", "/opt/somnus/somnus_agent.py"),
        user=config_data.get("user", "ai"),
    )
    return cfg


def marker_exists(marker_path: Path) -> bool:
    try:
        return marker_path.exists()
    except Exception:
        return False


def write_marker(marker_path: Path) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("bootstrap_complete\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def download_with_browser(url: str, dest: Path) -> None:
    try:
        try:
            from .ai_vm_browser import download_file  # type: ignore
        except Exception:
            from ai_vm_browser import download_file  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"ai_vm_browser download unavailable: {exc}") from exc

    await download_file(url, str(dest))


def download_with_urllib(url: str, dest: Path) -> None:
    from urllib.request import urlretrieve
    dest.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(url, dest)


async def fetch_payload(cfg: BootstrapConfig, payload: PayloadSource, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = payload_filename(payload)
    dest = cache_dir / filename

    local_path = find_local_payload(payload, filename)
    if local_path is not None:
        logger.info("Using local payload: %s", local_path)
        return local_path

    if dest.exists():
        logger.info("Using cached payload: %s", dest)
        return dest

    urls = candidate_urls(payload)
    if not urls:
        raise RuntimeError(f"No payload URLs provided for {payload.name}")

    last_error: Optional[Exception] = None
    for url in urls:
        logger.info("Downloading payload %s from %s", payload.name, url)
        try:
            if cfg.use_ai_vm_browser:
                await download_with_browser(url, dest)
            else:
                download_with_urllib(url, dest)
            return dest
        except Exception as exc:
            last_error = exc
            logger.warning("Download failed for %s: %s", url, exc)
            if dest.exists():
                try:
                    dest.unlink()
                except Exception:
                    pass

    raise RuntimeError(f"All payload URLs failed for {payload.name}: {last_error}")


def verify_payload(payload: PayloadSource, path: Path) -> None:
    if not payload.sha256:
        logger.warning("No sha256 provided for %s; skipping verification", payload.name)
        return
    actual = sha256_file(path)
    if actual.lower() != payload.sha256.lower():
        raise RuntimeError(f"SHA256 mismatch for {payload.name}: expected {payload.sha256}, got {actual}")
    logger.info("Verified SHA256 for %s", payload.name)


def safe_extract_tar(tar: tarfile.TarFile, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for member in tar.getmembers():
        member_path = target / member.name
        if not str(member_path.resolve()).startswith(str(target.resolve())):
            raise RuntimeError(f"Unsafe path in tar: {member.name}")
    tar.extractall(target)


def extract_payload(payload: PayloadSource, path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    if payload.archive == "tar.gz":
        with tarfile.open(path, "r:gz") as tar:
            safe_extract_tar(tar, target_dir)
        return
    if payload.archive == "zip":
        with zipfile.ZipFile(path) as zipf:
            for member in zipf.namelist():
                member_path = target_dir / member
                if not str(member_path.resolve()).startswith(str(target_dir.resolve())):
                    raise RuntimeError(f"Unsafe path in zip: {member}")
            zipf.extractall(target_dir)
        return
    # raw file
    dest = target_dir / payload_filename(payload)
    shutil.copy2(path, dest)


def run_post_install(commands: List[str], cwd: Path) -> None:
    for cmd in commands:
        logger.info("Post-install: %s", cmd)
        result = subprocess.run(cmd, shell=True, cwd=str(cwd))
        if result.returncode != 0:
            raise RuntimeError(f"Post-install command failed: {cmd}")


def write_systemd_unit(name: str, exec_cmd: str, user: str) -> None:
    unit_content = f"""[Unit]
Description=Somnus {name}
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory=/opt/somnus
ExecStart=/usr/bin/python3 {exec_cmd}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    unit_path = Path(f"/etc/systemd/system/somnus-{name}.service")
    unit_path.write_text(unit_content, encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "enable", f"somnus-{name}.service"], check=False)


async def bootstrap() -> None:
    setup_logging()
    cfg = load_config()

    target_dir = Path(cfg.target_dir)
    cache_dir = Path(cfg.cache_dir)
    marker = Path(cfg.marker_file)

    if marker_exists(marker):
        logger.info("Bootstrap marker found; skipping.")
        return

    for payload in cfg.payloads:
        payload_path = await fetch_payload(cfg, payload, cache_dir)
        verify_payload(payload, payload_path)
        extract_payload(payload, payload_path, target_dir)

    if cfg.post_install:
        run_post_install(cfg.post_install, target_dir)

    if cfg.create_systemd_units:
        if Path(cfg.shell_entry).exists():
            write_systemd_unit("shell", cfg.shell_entry, cfg.user)
        if Path(cfg.agent_entry).exists():
            write_systemd_unit("agent", cfg.agent_entry, cfg.user)

    write_marker(marker)
    logger.info("Bootstrap complete.")


def payload_filename(payload: PayloadSource) -> str:
    filename = payload.name.replace("/", "_")
    if payload.archive == "tar.gz":
        return filename + ".tar.gz"
    if payload.archive == "zip":
        return filename + ".zip"
    return filename


def candidate_urls(payload: PayloadSource) -> List[str]:
    urls: List[str] = []
    if payload.url:
        urls.append(payload.url)
    urls.extend(payload.urls)
    deduped: List[str] = []
    for url in urls:
        if url and url not in deduped:
            deduped.append(url)
    return deduped


def find_local_payload(payload: PayloadSource, filename: str) -> Optional[Path]:
    candidates = list(payload.local_paths)
    if not candidates:
        candidates = [
            f"/mnt/seed/{filename}",
            f"/media/seed/{filename}",
            f"/media/cdrom/{filename}",
            f"/cdrom/{filename}",
            f"/seed/{filename}",
            f"/run/media/ai/{filename}",
        ]
    for path_str in candidates:
        p = Path(path_str)
        if p.exists():
            return p
    return None


if __name__ == "__main__":
    asyncio.run(bootstrap())
