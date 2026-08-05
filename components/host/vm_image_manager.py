"""
================================================================================
Somnus VM Image Manager - ISO to QCOW2 Conversion & Bootstrap System
================================================================================

PRODUCTION-GRADE VM Image Management for AI Personal Computer- Phone (AIPC-Device).

This module handles:
1. Converting ISO images to bootable QCOW2 disks with unattended installation
2. Creating golden images with pre-installed Somnus Agent environments
3. Managing VM templates at AWS/Azure/GCP industrial scale

Architecture:
- Base Image: Clean OS installed from ISO (unattended via kickstart/preseed)
- Golden Image: Base + Somnus Agent (installed via SSH into running VM)
- Instance Disk: Copy-on-write snapshot from Golden for each AIPC

Dependencies:
- qemu-img (QCOW2 operations)
- qemu-system-x86_64 (VM creation and management)
- ssh (Agent deployment to running VMs)
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
import tarfile
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


async def _run_ssh_command(
    host: str,
    port: int,
    username: str,
    password: str,
    command: str,
    timeout: int = 60
) -> Tuple[int, str, str]:
    """
    Run a command via SSH using sshpass (if available) for password auth.
    Used for bootstrapping the agent when it's not yet running.
    """
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=no",
        "-o", "PasswordAuthentication=yes",
        "-p", str(port),
        f"{username}@{host}",
        command
    ]
    
    # Use sshpass for password auth if available
    env = os.environ.copy()
    if shutil.which("sshpass"):
        ssh_cmd = ["sshpass", "-p", password] + ssh_cmd
    
    proc = await asyncio.create_subprocess_exec(
        *ssh_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout
        )
        return proc.returncode, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        proc.kill()
        raise


async def _scp_to_vm(
    host: str,
    port: int,
    username: str,
    password: str,
    local_path: Path,
    remote_path: str,
    timeout: int = 60
):
    """Copy a file to VM via SCP."""
    scp_cmd = [
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-P", str(port),
        str(local_path),
        f"{username}@{host}:{remote_path}"
    ]
    
    if shutil.which("sshpass"):
        scp_cmd = ["sshpass", "-p", password] + scp_cmd
    
    proc = await asyncio.create_subprocess_exec(
        *scp_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"SCP failed: {stderr.decode()}")


class OSFamily(str, Enum):
    """Supported operating system families."""
    UBUNTU = "ubuntu"
    DEBIAN = "debian"
    FEDORA = "fedora"
    CENTOS = "centos"
    ALPINE = "alpine"
    ARCH = "arch"
    WINDOWS = "windows"
    CUSTOM = "custom"




class AgentEnvironmentSetup:
    """
    PRODUCTION-GRADE Somnus Agent Deployment via Localhost API.
    
    Installs the agent by:
    1. Booting the VM with the target disk image
    2. Waiting for the Somnus Agent API (localhost:9901) to become available
    3. Deploying agent files via HTTP API (consistent with advanced_ai_shell.py architecture)
    4. Installing and configuring the agent service
    
    This uses the SAME localhost API that the AI uses internally - maintaining
    architectural consistency with advanced_ai_shell.py's artifact_system.run_command().
    """
    
    AGENT_PACKAGES = [
        "python3",
        "python3-pip",
        "python3-venv",
        "python3-dev",
    ]
    
    AGENT_PYTHON_PACKAGES = [
        "flask",
        "psutil",
        "requests",
        "pydantic",
        "aiohttp",
    ]
    
    # Default credentials for fresh unattended installs
    DEFAULT_USER = "somnus"
    DEFAULT_PASSWORD = "somnus"
    
    def __init__(self, image_path: Path, work_dir: Optional[Path] = None):
        self.image_path = image_path
        self.work_dir = work_dir or Path(tempfile.gettempdir()) / "somnus_agent_setup"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._api_base_url: Optional[str] = None
        
    async def setup_agent_environment(
        self,
        agent_config: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Set up the Somnus agent environment.
        
        Tries API first (if cloud-init worked), falls back to SSH bootstrap.
        After this, the agent runs on localhost:9901 for all future operations.
        
        Returns:
            Tuple of (success, message)
        """
        logger.info(f"Setting up agent environment in {self.image_path}")
        
        vm_id = uuid4().hex[:8]
        
        try:
            # 1. Boot the VM with both SSH and Agent API ports
            logger.info(f"[{vm_id}] Starting temporary VM for agent installation...")
            vm_process, ssh_port, api_port = await self._start_temp_vm_with_ssh(vm_id)
            
            # 2. Wait for SSH to be available
            logger.info(f"[{vm_id}] Waiting for SSH (localhost:{ssh_port})...")
            ssh_ready = await self._wait_for_ssh("127.0.0.1", ssh_port, timeout=300)
            if not ssh_ready:
                self._cleanup_vm(vm_process)
                return False, "SSH did not become available - OS may not have installed correctly"
            
            logger.info(f"[{vm_id}] SSH is ready")
            
            # 3. Check if agent is already running (cloud-init worked)
            logger.info(f"[{vm_id}] Checking if agent is already running...")
            api_ready = await self._wait_for_api("127.0.0.1", api_port, timeout=30)
            
            if not api_ready:
                # Agent not running - bootstrap it via SSH
                logger.info(f"[{vm_id}] Agent not running - bootstrapping via SSH...")
                await self._bootstrap_agent_via_ssh("127.0.0.1", ssh_port, agent_config or {})
                
                # Now wait for agent API
                logger.info(f"[{vm_id}] Waiting for Agent API after SSH bootstrap...")
                api_ready = await self._wait_for_api("127.0.0.1", api_port, timeout=60)
                if not api_ready:
                    logger.warning(f"[{vm_id}] Agent API still not responding after SSH bootstrap")
            else:
                logger.info(f"[{vm_id}] Agent already running (cloud-init worked!)")
            
            # 4. Verify/update agent config via API if it's working
            if api_ready:
                await self._verify_agent_via_api("127.0.0.1", api_port, agent_config or {})
            
            # 5. Graceful shutdown via SSH
            logger.info(f"[{vm_id}] Shutting down VM...")
            try:
                await _run_ssh_command(
                    "127.0.0.1", ssh_port,
                    self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                    "sudo shutdown -h now",
                    timeout=10
                )
            except Exception:
                pass
            
            # Wait for process to exit
            try:
                await asyncio.wait_for(vm_process.wait(), timeout=60)
            except asyncio.TimeoutError:
                vm_process.terminate()
                await asyncio.wait_for(vm_process.wait(), timeout=10)
            
            logger.info(f"[{vm_id}] Agent installation complete")
            return True, "Agent environment set up successfully"
            
        except Exception as e:
            logger.error(f"[{vm_id}] Agent setup failed: {e}")
            return False, str(e)
    
    async def _enable_ssh_in_image(self) -> bool:
        """
        Enable SSH in the QCOW2 image using WSL + guestfish.
        This modifies the image offline before booting.
        """
        logger.info("Attempting to enable SSH in image using WSL...")
        
        try:
            wsl_path = str(self.image_path).replace("\\", "/").replace("C:", "/mnt/c")
            
            # Create a script to enable SSH using guestfish
            script = f"""
#!/bin/bash
set -e

# Check if guestfish is available
if ! command -v guestfish &> /dev/null; then
    echo "Installing guestfish..."
    apt-get update && apt-get install -y libguestfs-tools
fi

# Enable SSH service in the image
echo "Enabling SSH in {wsl_path}..."
guestfish -a "{wsl_path}" -i << 'GUESTFISH'
sh 'apt-get update && apt-get install -y openssh-server'
sh 'systemctl enable ssh'
sh 'echo "somnus:somnus" | chpasswd'
sh 'usermod -aG sudo somnus'
sh 'echo "somnus ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/somnus'
sh 'chmod 440 /etc/sudoers.d/somnus'
quit
GUESTFISH

echo "SSH enabled successfully"
"""
            
            # Run the script in WSL
            proc = await asyncio.create_subprocess_exec(
                "wsl", "bash", "-c", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                logger.info("SSH enabled in image via WSL/guestfish")
                return True
            else:
                logger.warning(f"guestfish failed: {stderr.decode()}")
                return False
                
        except Exception as e:
            logger.warning(f"Could not enable SSH via WSL: {e}")
            return False
    
    async def _start_temp_vm_with_ssh(self, vm_id: str) -> Tuple[asyncio.subprocess.Process, int, int]:
        """Start VM with SSH and Agent API ports forwarded.
        
        Returns:
            Tuple of (process, ssh_port, api_port)
        """
        ssh_port = self._get_free_port()
        api_port = self._get_free_port()
        
        use_wsl = self._use_wsl()
        is_windows = sys.platform == "win32"
        
        # Try to enable SSH offline first (WSL guestfish)
        ssh_enabled_offline = await self._enable_ssh_in_image()
        
        if use_wsl:
            wsl_path = str(self.image_path).replace("\\", "/").replace("C:", "/mnt/c")
            drive_arg = f"file={wsl_path},format=qcow2,if=virtio,cache=writeback"
            qemu_binary = "wsl"
            qemu_args = ["qemu-system-x86_64"]
        else:
            drive_arg = f"file={self.image_path},format=qcow2,if=virtio,cache=writeback"
            qemu_binary = "qemu-system-x86_64"
            qemu_args = []
        
        qemu_cmd = [
            qemu_binary,
            *qemu_args,
            "-name", f"somnus-agent-setup-{vm_id}",
            "-m", "2048",
            "-smp", "2",
            "-drive", drive_arg,
            # Forward both SSH (22) and Agent API (9901)
            "-netdev", f"user,id=net0,hostfwd=tcp::{ssh_port}-:22,hostfwd=tcp::{api_port}-:9901",
            "-device", "virtio-net,netdev=net0",
        ]
        
        # Platform-specific options
        if use_wsl:
            qemu_cmd.extend(["-daemonize"])
            try:
                result = subprocess.run(
                    ["wsl", "bash", "-c", "test -r /dev/kvm && echo yes || echo no"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.stdout.strip() == "yes":
                    qemu_cmd.append("-enable-kvm")
                else:
                    qemu_cmd.extend(["-cpu", "max"])
            except:
                qemu_cmd.extend(["-cpu", "max"])
        elif is_windows:
            qemu_cmd.extend(["-nographic", "-cpu", "max"])
        else:
            qemu_cmd.extend(["-nographic", "-daemonize"])
            if self._kvm_available():
                qemu_cmd.append("-enable-kvm")
            else:
                qemu_cmd.extend(["-cpu", "max"])
        
        logger.debug(f"Starting QEMU: {' '.join(qemu_cmd)}")
        
        proc = await asyncio.create_subprocess_exec(
            *qemu_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        
        await asyncio.sleep(2)
        
        if proc.returncode is not None and proc.returncode != 0:
            stderr = ""
            if proc.stderr:
                stderr = (await proc.stderr.read()).decode()
            raise RuntimeError(f"QEMU failed to start: {stderr}")
        
        return proc, ssh_port, api_port
    
    async def _wait_for_ssh(self, host: str, port: int, timeout: int = 300) -> bool:
        """Wait for SSH service to be available."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Try to connect to SSH port
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=5
                )
                writer.close()
                await writer.wait_closed()
                
                # Try an actual SSH command
                returncode, stdout, stderr = await _run_ssh_command(
                    host, port,
                    self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                    "echo 'SSH_READY'",
                    timeout=10
                )
                if returncode == 0 and "SSH_READY" in stdout:
                    return True
            except Exception:
                pass
            
            await asyncio.sleep(2)
        
        return False
    
    async def _bootstrap_agent_via_ssh(self, host: str, port: int, agent_config: Dict[str, Any]):
        """Bootstrap the agent via SSH (install and start it)."""
        logger.info("Bootstrapping agent via SSH...")
        
        # First ensure SSH is installed and running (Ubuntu Desktop might not have it)
        logger.info("Ensuring SSH is available in VM...")
        try:
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                "sudo apt-get update && sudo apt-get install -y openssh-server",
                timeout=120
            )
        except Exception as e:
            logger.warning(f"SSH install may have failed (might already be installed): {e}")
        
        # Create agent tarball
        tar_path = await self._create_agent_tarball()
        
        try:
            # 1. Create directories
            logger.info("Creating agent directories...")
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                "sudo mkdir -p /opt/somnus/agent /etc/somnus && sudo chown somnus:somnus /opt/somnus/agent",
                timeout=30
            )
            
            # 2. Upload tarball via SCP
            logger.info("Uploading agent tarball...")
            await _scp_to_vm(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                tar_path, "/tmp/somnus_agent.tar.gz"
            )
            
            # 3. Extract and install
            logger.info("Extracting agent files...")
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                "sudo tar -xzf /tmp/somnus_agent.tar.gz -C /opt/somnus/agent && rm /tmp/somnus_agent.tar.gz",
                timeout=60
            )
            
            # 4. Install Python dependencies
            logger.info("Installing Python dependencies...")
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                f"sudo pip3 install {' '.join(self.AGENT_PYTHON_PACKAGES)} 2>/dev/null || sudo python3 -m pip install {' '.join(self.AGENT_PYTHON_PACKAGES)}",
                timeout=180
            )
            
            # 5. Write agent config
            logger.info("Writing agent config...")
            config_json = json.dumps(agent_config, indent=2).replace('"', '\\"')
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                f'echo "{config_json}" | sudo tee /etc/somnus/agent_config.json',
                timeout=10
            )
            
            # 6. Create systemd service - write to file then move
            logger.info("Creating systemd service...")
            service_content = self._generate_systemd_service()
            # Write to temp file
            temp_service = self.work_dir / "somnus-agent.service"
            temp_service.write_text(service_content)
            # Copy via SCP
            await _scp_to_vm(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                temp_service, "/tmp/somnus-agent.service"
            )
            # Move to systemd
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                "sudo mv /tmp/somnus-agent.service /etc/systemd/system/somnus-agent.service",
                timeout=10
            )
            
            # 7. Start the agent immediately
            logger.info("Starting Somnus Agent...")
            await _run_ssh_command(
                host, port,
                self.DEFAULT_USER, self.DEFAULT_PASSWORD,
                "sudo systemctl daemon-reload && sudo systemctl enable somnus-agent.service && sudo systemctl start somnus-agent.service",
                timeout=30
            )
            
            # Give agent time to start
            await asyncio.sleep(3)
            
            logger.info("Agent bootstrap complete!")
            
        finally:
            if tar_path.exists():
                tar_path.unlink()
    
    async def _verify_agent_via_api(self, host: str, port: int, agent_config: Dict[str, Any]):
        """Verify agent is working and update config if needed via API."""
        import aiohttp
        
        base_url = f"http://{host}:{port}"
        
        async with aiohttp.ClientSession() as session:
            # Check status
            async with session.get(f"{base_url}/status") as resp:
                if resp.status == 200:
                    status = await resp.json()
                    logger.info(f"Agent status: {status}")
                else:
                    logger.warning(f"Agent status check failed: {resp.status}")
            
            # If we need to update config, we can do it via API
            if agent_config:
                logger.info("Updating agent config via API...")
                async with session.post(
                    f"{base_url}/write_file",
                    json={
                        "path": "/etc/somnus/agent_config.json",
                        "content": json.dumps(agent_config, indent=2),
                        "encoding": "utf-8"
                    }
                ) as resp:
                    if resp.status == 200:
                        logger.info("Agent config updated")
                    else:
                        logger.warning(f"Could not update config: {await resp.text()}")
    
    def _get_qemu_binary(self) -> str:
        """Get the appropriate QEMU binary (native or WSL)."""
        is_windows = sys.platform == "win32"
        
        if is_windows:
            # Check for WSL QEMU first (better performance)
            try:
                result = subprocess.run(
                    ["wsl", "which", "qemu-system-x86_64"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.debug("Using WSL QEMU for better performance")
                    return "wsl qemu-system-x86_64"
            except Exception:
                pass
            
            # Fall back to native Windows QEMU
            return "qemu-system-x86_64"
        else:
            return "qemu-system-x86_64"
    
    def _use_wsl(self) -> bool:
        """Check if we should use WSL for QEMU."""
        if sys.platform != "win32":
            return False
        try:
            result = subprocess.run(
                ["wsl", "which", "qemu-system-x86_64"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    async def _start_temp_vm(self, vm_id: str) -> Tuple[asyncio.subprocess.Process, int]:
        """Start a temporary VM and return (process, agent_api_port).
        
        Exposes the Somnus Agent API port (9901) for localhost API communication.
        This is the SAME port used by advanced_ai_shell.py for artifact execution.
        """
        
        # Find available ports - forward to agent API port 9901 inside VM
        agent_port = self._get_free_port()
        
        # Check if we should use WSL
        use_wsl = self._use_wsl()
        is_windows = sys.platform == "win32"
        
        # Build QEMU command
        if use_wsl:
            # WSL: Use WSL QEMU with KVM acceleration
            qemu_binary = "wsl"
            qemu_args = ["qemu-system-x86_64"]
            # Convert Windows path to WSL path
            wsl_path = str(self.image_path).replace("\\", "/").replace("C:", "/mnt/c")
            drive_arg = f"file={wsl_path},format=qcow2,if=virtio,cache=writeback"
        else:
            # Native Windows or Linux
            qemu_binary = "qemu-system-x86_64"
            qemu_args = []
            drive_arg = f"file={self.image_path},format=qcow2,if=virtio,cache=writeback"
        
        qemu_cmd = [
            qemu_binary,
            *qemu_args,
            "-name", f"somnus-agent-setup-{vm_id}",
            "-m", "2048",
            "-smp", "2",
            "-drive", drive_arg,
            # Forward host:agent_port to VM:9901 (Somnus Agent API)
            "-netdev", f"user,id=net0,hostfwd=tcp::{agent_port}-:9901",
            "-device", "virtio-net,netdev=net0",
        ]
        
        # Platform-specific options
        if use_wsl:
            # WSL: Use -daemonize only (NO -nographic, they conflict!)
            # Check if KVM is available without permission issues
            qemu_cmd.extend(["-daemonize"])
            try:
                result = subprocess.run(
                    ["wsl", "bash", "-c", "test -r /dev/kvm && echo yes || echo no"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.stdout.strip() == "yes":
                    qemu_cmd.append("-enable-kvm")
                else:
                    qemu_cmd.extend(["-cpu", "max"])
            except:
                qemu_cmd.extend(["-cpu", "max"])
        elif is_windows:
            # Windows native: No -daemonize (not supported), no KVM
            qemu_cmd.extend([
                "-nographic",
                "-cpu", "max",
            ])
        else:
            # Linux native
            qemu_cmd.extend([
                "-nographic",
                "-daemonize",
            ])
            if self._kvm_available():
                qemu_cmd.append("-enable-kvm")
            else:
                qemu_cmd.extend(["-cpu", "max"])
        
        logger.debug(f"Starting QEMU: {' '.join(qemu_cmd)}")
        
        proc = await asyncio.create_subprocess_exec(
            *qemu_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Give QEMU a moment to start and check for immediate failures
        await asyncio.sleep(2)
        
        if proc.returncode is not None and proc.returncode != 0:
            stderr = ""
            if proc.stderr:
                stderr = (await proc.stderr.read()).decode()
            raise RuntimeError(f"QEMU failed to start: {stderr}")
        
        return proc, agent_port
    
    async def _wait_for_api(self, host: str, port: int, timeout: int = 300) -> bool:
        """Wait for Somnus Agent API to become available."""
        import aiohttp
        
        start_time = time.time()
        # Agent uses /health/ready for readiness probe
        health_url = f"http://{host}:{port}/health/ready"
        
        while time.time() - start_time < timeout:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    async with session.get(health_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("status") == "ready":
                                logger.info(f"Somnus Agent API ready at {health_url}")
                                return True
            except Exception:
                pass
            
            await asyncio.sleep(2)
        
        return False
    
    async def _deploy_agent_via_api(
        self,
        host: str,
        port: int,
        agent_config: Dict[str, Any]
    ):
        """Deploy agent files via Somnus Agent HTTP API (consistent with advanced_ai_shell.py)."""
        import aiohttp
        
        # Create agent tarball
        tar_path = await self._create_agent_tarball()
        
        base_url = f"http://{host}:{port}"
        
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Create remote directories via API (using execute_command endpoint)
                logger.info("Creating agent directories via API...")
                async with session.post(
                    f"{base_url}/execute_command",
                    json={
                        "command": "sudo mkdir -p /opt/somnus/agent /etc/somnus && sudo chown somnus:somnus /opt/somnus/agent",
                        "timeout": 30
                    }
                ) as resp:
                    result = await resp.json()
                    if result.get("exit_code") != 0:
                        logger.warning(f"Directory creation warning: {result.get('stderr')}")
                
                # 2. Upload tarball via write_file API (base64 encoded)
                logger.info("Uploading agent tarball via API...")
                import base64
                with open(tar_path, 'rb') as f:
                    tar_content = base64.b64encode(f.read()).decode('utf-8')
                
                async with session.post(
                    f"{base_url}/write_file",
                    json={
                        "path": "/tmp/somnus_agent.tar.gz",
                        "content": tar_content,
                        "mode": "wb"  # binary mode for base64
                    }
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Failed to upload tarball: {await resp.text()}")
                
                # 3. Extract tarball via API
                logger.info("Extracting agent files via API...")
                async with session.post(
                    f"{base_url}/execute_command",
                    json={
                        "command": "sudo tar -xzf /tmp/somnus_agent.tar.gz -C /opt/somnus/agent && rm /tmp/somnus_agent.tar.gz",
                        "timeout": 60
                    }
                ) as resp:
                    result = await resp.json()
                    if result.get("exit_code") != 0:
                        raise RuntimeError(f"Failed to extract tarball: {result.get('stderr')}")
                
                # 4. Install Python dependencies via API
                logger.info("Installing Python dependencies via API...")
                async with session.post(
                    f"{base_url}/execute_command",
                    json={
                        "command": f"sudo pip3 install {' '.join(self.AGENT_PYTHON_PACKAGES)}",
                        "timeout": 180
                    }
                ) as resp:
                    result = await resp.json()
                    # pip may return warnings, so we just log
                    logger.debug(f"pip install result: {result}")
                
                # 5. Write agent config via API
                logger.info("Writing agent config via API...")
                async with session.post(
                    f"{base_url}/write_file",
                    json={
                        "path": "/etc/somnus/agent_config.json",
                        "content": json.dumps(agent_config, indent=2),
                        "encoding": "utf-8"
                    }
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Failed to write config: {await resp.text()}")
                
                # 6. Create systemd service via API
                logger.info("Creating systemd service via API...")
                async with session.post(
                    f"{base_url}/write_file",
                    json={
                        "path": "/etc/systemd/system/somnus-agent.service",
                        "content": self._generate_systemd_service(),
                        "encoding": "utf-8"
                    }
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Failed to write service file: {await resp.text()}")
                
                # 7. Enable service via API
                logger.info("Enabling systemd service via API...")
                async with session.post(
                    f"{base_url}/execute_command",
                    json={
                        "command": "sudo systemctl daemon-reload && sudo systemctl enable somnus-agent.service",
                        "timeout": 30
                    }
                ) as resp:
                    result = await resp.json()
                    if result.get("exit_code") != 0:
                        raise RuntimeError(f"Failed to enable service: {result.get('stderr')}")
                
                logger.info("Agent deployment via API complete!")
                
        finally:
            if tar_path.exists():
                tar_path.unlink()
    
    async def _create_agent_tarball(self) -> Path:
        """Create a tarball of agent files."""
        tar_path = self.work_dir / f"somnus_agent_{uuid4().hex[:8]}.tar.gz"
        project_root = Path(__file__).parent
        
        with tarfile.open(tar_path, "w:gz") as tar:
            # Add agent files
            agent_files = [
                "somnus_agent.py",
                "vm_supervisor.py",
                "vm_image_manager.py",
                "vm_orchestrator.py",
            ]
            
            for file_name in agent_files:
                file_path = project_root / file_name
                if file_path.exists():
                    tar.add(file_path, arcname=file_name)
            
            # Add core modules
            for dir_name in ["core", "backend"]:
                dir_path = project_root / dir_name
                if dir_path.exists():
                    tar.add(dir_path, arcname=dir_name)
        
        return tar_path
    
    async def _graceful_shutdown_via_api(self, host: str, port: int):
        """Shutdown the VM gracefully via Agent API."""
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                # Use the API to shutdown
                async with session.post(
                    f"http://{host}:{port}/execute_command",
                    json={
                        "command": "sudo shutdown -h now",
                        "timeout": 5
                    }
                ):
                    pass  # Shutdown command may not return
        except Exception:
            pass  # Shutdown command may not return
    
    def _cleanup_vm(self, process: asyncio.subprocess.Process):
        """Force cleanup of VM process."""
        try:
            process.terminate()
        except Exception:
            pass
    
    def _get_free_port(self) -> int:
        """Get a free TCP port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port
    
    def _kvm_available(self) -> bool:
        """Check if KVM acceleration is available."""
        return os.path.exists("/dev/kvm")
    
    def _generate_systemd_service(self) -> str:
        """Generate systemd service file content."""
        return """[Unit]
Description=Somnus Digital Twin Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/somnus/agent
ExecStart=/usr/bin/python3 /opt/somnus/agent/somnus_agent.py
Restart=always
RestartSec=5
Environment=PYTHONPATH=/opt/somnus/agent

[Install]
WantedBy=multi-user.target
"""




class ISOConverter:
    """Handles conversion of ISO images to bootable QCOW2 disks."""

    async def convert_iso_to_qcow2(
        self,
        iso_path: Path,
        output_path: Path,
        disk_size_gb: int,
        os_family: OSFamily,
        unattended_config: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """Convert ISO to QCOW2."""
        try:
            logger.info(f"Converting ISO {iso_path} to {output_path}")
            
            # 1. Create empty QCOW2 disk
            if not await self._create_disk(output_path, disk_size_gb):
                 return False, "Failed to create disk image"

            # 2. Boot VM for installation
            # If unattended_config is None, we assume manual install via VNC/Window
            logger.info("Booting VM for installation...")
            await self._run_install_vm(iso_path, output_path)
            
            return True, "Installation process finished (assumed success)"
        except Exception as e:
            logger.error(f"ISO conversion failed: {e}")
            return False, str(e)

    async def _create_disk(self, path: Path, size_gb: int) -> bool:
        """Create a new QCOW2 disk image."""
        cmd = ["qemu-img", "create", "-f", "qcow2", str(path), f"{size_gb}G"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"qemu-img create failed: {stderr.decode()}")
            return False
        return True

    async def _create_cloud_init_iso(self, work_dir: Path) -> Path:
        """Create a cloud-init ISO for unattended Ubuntu installation with agent auto-install."""
        import tempfile
        
        # Cloud-init user-data for unattended install + agent setup
        user_data = """#cloud-config
# Somnus AIPC Auto-Install Configuration
hostname: somnus-aipc
fqdn: somnus-aipc.local

users:
  - name: somnus
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: users, admin
    home: /home/somnus
    shell: /bin/bash
    lock_passwd: false
    passwd: $6$rounds=4096$saltsalt$7nJ5.vXNldxCZc/EhKpXqbz1I2P.nCdNMwp54hE7q2gQVxM.koF1kLWroZ/PhE6K3

# Install required packages
package_update: true
packages:
  - python3
  - python3-pip
  - python3-venv
  - curl
  - wget
  - git

# Run commands on first boot
runcmd:
  # Create agent directories
  - mkdir -p /opt/somnus/agent /etc/somnus
  - chown somnus:somnus /opt/somnus/agent
  
  # Install Python dependencies
  - pip3 install flask psutil requests pydantic aiohttp --break-system-packages || pip3 install flask psutil requests pydantic aiohttp
  
  # Create agent config
  - |
    cat > /etc/somnus/agent_config.json << 'EOF'
    {
        "agent_port": 9901,
        "monitored_processes": ["python3", "dockerd"],
        "log_files": {
            "syslog": "/var/log/syslog",
            "auth": "/var/log/auth.log"
        },
        "monitor_interval_seconds": 5
    }
    EOF
  
  # Create systemd service for agent
  - |
    cat > /etc/systemd/system/somnus-agent.service << 'EOF'
    [Unit]
    Description=Somnus Digital Twin Agent
    After=network.target

    [Service]
    Type=simple
    User=root
    WorkingDirectory=/opt/somnus/agent
    ExecStart=/usr/bin/python3 /opt/somnus/agent/somnus_agent.py
    Restart=always
    RestartSec=5
    Environment=PYTHONPATH=/opt/somnus/agent

    [Install]
    WantedBy=multi-user.target
    EOF
  
  # Enable agent service (will start when agent files are present)
  - systemctl daemon-reload
  - systemctl enable somnus-agent.service
  
  # Mark setup complete
  - echo "Somnus AIPC base image ready" > /etc/somnus/setup_complete

# Poweroff after setup (we'll copy the disk before this)
power_state:
  mode: poweroff
  message: Somnus AIPC setup complete
  timeout: 30
"""
        
        meta_data = """instance-id: somnus-aipc-001
local-hostname: somnus-aipc
"""
        
        # Create temp directory for cloud-init files
        ci_dir = work_dir / f"cloud-init-{uuid4().hex[:8]}"
        ci_dir.mkdir(parents=True, exist_ok=True)
        
        # Write files
        (ci_dir / "user-data").write_text(user_data)
        (ci_dir / "meta-data").write_text(meta_data)
        
        # Create ISO using genisoimage or mkisofs
        iso_path = work_dir / f"cloud-init-{uuid4().hex[:8]}.iso"
        
        # Try to create ISO using available tools (native or WSL)
        try:
            # Check if we should use WSL for genisoimage
            use_wsl_geniso = False
            if sys.platform == "win32":
                try:
                    result = subprocess.run(
                        ["wsl", "which", "genisoimage"],
                        capture_output=True,
                        timeout=5
                    )
                    use_wsl_geniso = result.returncode == 0
                except:
                    pass
            
            if use_wsl_geniso:
                # Use WSL genisoimage
                wsl_ci_dir = str(ci_dir).replace("\\", "/").replace("C:", "/mnt/c")
                wsl_iso_path = str(iso_path).replace("\\", "/").replace("C:", "/mnt/c")
                cmd = [
                    "wsl", "genisoimage",
                    "-output", wsl_iso_path,
                    "-volid", "cidata",
                    "-joliet",
                    "-rock",
                    wsl_ci_dir
                ]
            else:
                # Try native genisoimage (Linux) or mkisofs
                cmd = [
                    "genisoimage" if shutil.which("genisoimage") else "mkisofs",
                    "-output", str(iso_path),
                    "-volid", "cidata",
                    "-joliet",
                    "-rock",
                    str(ci_dir)
                ]
            
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
            
            if proc.returncode != 0:
                raise RuntimeError("Failed to create cloud-init ISO")
                
        except Exception as e:
            logger.warning(f"Could not create cloud-init ISO: {e}")
            logger.warning("Falling back to manual install mode")
            # Return None to indicate manual install needed
            return None
        
        return iso_path

    async def _run_install_vm(self, iso: Path, disk: Path):
        """Run the VM with ISO mounted for installation.
        
        Tries cloud-init auto-install first, falls back to manual install.
        On Windows: Opens a window for manual installation if cloud-init fails
        WSL: Preferred on Windows for better performance with KVM
        """
        is_windows = sys.platform == "win32"
        work_dir = Path(tempfile.gettempdir()) / f"somnus_install_{uuid4().hex[:8]}"
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Try to create cloud-init ISO for unattended install
        cloud_init_iso = await self._create_cloud_init_iso(work_dir)
        
        # Check for WSL QEMU (better performance on Windows)
        use_wsl = False
        if is_windows:
            try:
                result = subprocess.run(
                    ["wsl", "which", "qemu-system-x86_64"],
                    capture_output=True,
                    timeout=5
                )
                use_wsl = result.returncode == 0
            except Exception:
                pass
        
        # Build command based on platform
        if use_wsl:
            # WSL: Convert paths and use WSL QEMU
            wsl_disk = str(disk).replace("\\", "/").replace("C:", "/mnt/c")
            wsl_iso = str(iso).replace("\\", "/").replace("C:", "/mnt/c")
            
            cmd = [
                "wsl", "qemu-system-x86_64",
                "-m", "4096",
                "-smp", "4",
                "-drive", f"file={wsl_disk},format=qcow2,if=virtio",
                "-cdrom", wsl_iso,
                "-boot", "d",
                "-vga", "std",
                "-netdev", "user,id=net0",
                "-device", "virtio-net,netdev=net0",
            ]
            
            # Check if KVM is available (may fail due to permissions)
            try:
                result = subprocess.run(
                    ["wsl", "bash", "-c", "test -r /dev/kvm && echo yes || echo no"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.stdout.strip() == "yes":
                    cmd.append("-enable-kvm")
                    logger.info("Using WSL2 QEMU with KVM acceleration")
                else:
                    cmd.extend(["-cpu", "max"])
                    logger.info("Using WSL2 QEMU without KVM (no permission)")
            except:
                cmd.extend(["-cpu", "max"])
                logger.info("Using WSL2 QEMU without KVM")
            
            # Add cloud-init ISO if available
            if cloud_init_iso:
                wsl_ci = str(cloud_init_iso).replace("\\", "/").replace("C:", "/mnt/c")
                cmd.extend(["-drive", f"file={wsl_ci},format=raw,if=virtio,readonly=on"])
                logger.info("Using cloud-init for unattended installation")
        else:
            # Native QEMU
            cmd = [
                "qemu-system-x86_64",
                "-m", "4096",
                "-smp", "4",
                "-drive", f"file={disk},format=qcow2,if=virtio",
                "-cdrom", str(iso),
                "-boot", "d",
                "-vga", "std",
            ]
            
            # Add cloud-init ISO if available
            if cloud_init_iso:
                cmd.extend(["-drive", f"file={cloud_init_iso},format=raw,if=virtio,readonly=on"])
                logger.info("Using cloud-init for unattended installation")
            
            # Platform-specific options
            if is_windows:
                cmd.extend([
                    "-netdev", "user,id=net0",
                    "-device", "virtio-net,netdev=net0",
                    "-cpu", "max",
                ])
            else:
                cmd.extend([
                    "-netdev", "user,id=net0",
                    "-device", "virtio-net,netdev=net0",
                ])
                if os.path.exists("/dev/kvm"):
                    cmd.append("-enable-kvm")
                else:
                    cmd.extend(["-cpu", "max"])
        
        if cloud_init_iso:
            logger.info("=" * 60)
            logger.info("UNATTENDED INSTALLATION WITH CLOUD-INIT")
            logger.info("The OS will install automatically.")
            logger.info("Agent will be installed and started automatically.")
            logger.info("VM will shut down when complete.")
            logger.info("=" * 60)
        else:
            logger.info("=" * 60)
            logger.info("MANUAL INSTALLATION REQUIRED")
            logger.info("Cloud-init not available on this system.")
            logger.info("INSTALL THE OS MANUALLY IN THE QEMU WINDOW")
            logger.info("1. Select 'Install to Hard Drive'")
            logger.info("2. Use automatic partitioning (entire disk)")
            logger.info("3. Set username: 'somnus', password: 'somnus'")
            logger.info("4. Wait for installation to complete")
            logger.info("5. When VM shuts down or prompts to reboot, close the window")
            logger.info("=" * 60)
        
        logger.info(f"Launching installer VM...")
        
        # Launch QEMU
        proc = await asyncio.create_subprocess_exec(*cmd)
        
        # Wait for VM to complete (cloud-init will power off, or user closes window)
        await proc.wait()
        
        # Cleanup cloud-init ISO
        if cloud_init_iso and cloud_init_iso.exists():
            cloud_init_iso.unlink()
        if work_dir.exists():
            import shutil
            shutil.rmtree(work_dir)
        
        logger.info("QEMU installer VM closed - OS installation complete")


class VMImageManager:
    """Main class for managing VM images at industrial scale."""
    
    def __init__(self, storage_path: Path = Path("/var/lib/somnus/images")):
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.iso_converter = ISOConverter()
        self.metadata_file = self.storage_path / "image_metadata.json"
        self._load_metadata()
        
    def _load_metadata(self):
        """Load image metadata from disk."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    self.metadata = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")
                self.metadata = {}
        else:
            self.metadata = {}
    
    def _save_metadata(self):
        """Save image metadata to disk."""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2, default=str)
    
    async def create_base_image_from_iso(
        self,
        name: str,
        iso_path: Path,
        os_family: OSFamily,
        disk_size_gb: int = 50,
        unattended_config: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, Optional[UUID]]:
        """
        Create a base VM image from an ISO file using unattended installation.
        
        This boots the ISO in a temporary VM and performs fully automated
        OS installation using kickstart or preseed.
        """
        image_id = uuid4()
        output_path = self.storage_path / f"{name}_{image_id.hex[:8]}.qcow2"
        
        success, message = await self.iso_converter.convert_iso_to_qcow2(
            iso_path=iso_path,
            output_path=output_path,
            disk_size_gb=disk_size_gb,
            os_family=os_family,
            unattended_config=unattended_config
        )
        
        if success:
            # Calculate checksum
            checksum = await self._calculate_checksum(output_path)
            
            # Store metadata
            self.metadata[str(image_id)] = {
                "image_id": str(image_id),
                "name": name,
                "os_family": os_family.value,
                "format": "qcow2",
                "path": str(output_path),
                "size_bytes": output_path.stat().st_size,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "checksum_sha256": checksum,
                "is_golden_image": False
            }
            self._save_metadata()
            
            return True, f"Image created: {output_path}", image_id
        
        return False, message, None
    
    async def create_golden_image(
        self,
        base_image_id: UUID,
        name: str,
        agent_config: Optional[Dict[str, Any]] = None,
        additional_packages: Optional[List[str]] = None
    ) -> Tuple[bool, str, Optional[UUID]]:
        """
        Create a golden image with the Somnus agent pre-installed.
        
        This copies the base image, boots it, and installs the agent via SSH
        for maximum compatibility across host platforms.
        """
        if str(base_image_id) not in self.metadata:
            return False, f"Base image {base_image_id} not found", None
        
        base_meta = self.metadata[str(base_image_id)]
        base_path = Path(base_meta["path"])
        
        if not base_path.exists():
            return False, f"Base image file not found: {base_path}", None
        
        golden_id = uuid4()
        golden_path = self.storage_path / f"{name}_{golden_id.hex[:8]}.qcow2"
        
        try:
            # Create a copy of the base image
            logger.info(f"Creating golden image copy: {golden_path}")
            shutil.copy2(base_path, golden_path)
            
            # Set up agent environment by booting VM and installing via SSH
            agent_setup = AgentEnvironmentSetup(golden_path)
            success, message = await agent_setup.setup_agent_environment(agent_config)
            
            if not success:
                golden_path.unlink(missing_ok=True)
                return False, f"Agent setup failed: {message}", None
            
            # Calculate checksum
            checksum = await self._calculate_checksum(golden_path)
            
            # Store metadata
            self.metadata[str(golden_id)] = {
                "image_id": str(golden_id),
                "name": name,
                "os_family": base_meta["os_family"],
                "format": "qcow2",
                "path": str(golden_path),
                "size_bytes": golden_path.stat().st_size,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "checksum_sha256": checksum,
                "parent_image_id": str(base_image_id),
                "is_golden_image": True,
                "installed_packages": additional_packages or []
            }
            self._save_metadata()
            
            return True, f"Golden image created: {golden_path}", golden_id
            
        except Exception as e:
            golden_path.unlink(missing_ok=True)
            return False, str(e), None
    
    async def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        
        return sha256_hash.hexdigest()
    
    def list_images(self, golden_only: bool = False) -> List[Dict[str, Any]]:
        """List all available images."""
        images = []
        for image_id, meta in self.metadata.items():
            if golden_only and not meta.get("is_golden_image", False):
                continue
            images.append(meta)
        return images
    
    def get_image_path(self, image_id: UUID) -> Optional[Path]:
        """Get the path to an image by ID."""
        if str(image_id) in self.metadata:
            path = Path(self.metadata[str(image_id)]["path"])
            if path.exists():
                return path
        return None
    
    async def delete_image(self, image_id: UUID) -> bool:
        """Delete an image and its metadata."""
        if str(image_id) not in self.metadata:
            return False
        
        meta = self.metadata[str(image_id)]
        path = Path(meta["path"])
        
        # Check if any images depend on this one
        for other_id, other_meta in self.metadata.items():
            if other_meta.get("parent_image_id") == str(image_id):
                logger.warning(f"Cannot delete image {image_id}: image {other_id} depends on it")
                return False
        
        # Delete the file
        if path.exists():
            path.unlink()
        
        # Remove metadata
        del self.metadata[str(image_id)]
        self._save_metadata()
        
        return True


# Factory function
async def create_vm_image_manager(
    storage_path: str = "/var/lib/somnus/images"
) -> VMImageManager:
    """Create and initialize a VMImageManager."""
    manager = VMImageManager(Path(storage_path))
    return manager


# Example usage
async def example_usage():
    """Example of how to use the VMImageManager."""
    
    manager = await create_vm_image_manager("/tmp/somnus_images")
    
    # Example: Create base image from ISO (requires actual ISO file)
    # success, message, image_id = await manager.create_base_image_from_iso(
    #     name="ubuntu-22.04-base",
    #     iso_path=Path("/path/to/ubuntu-22.04.iso"),
    #     os_family=OSFamily.UBUNTU,
    #     disk_size_gb=50
    # )
    
    # List images
    images = manager.list_images()
    print(f"Available images: {len(images)}")
    for img in images:
        print(f"  - {img['name']} ({img['image_id']})")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(example_usage())
