"""
SOMNUS SYSTEMS - Advanced AI Shell Module
Multi-Modal AI Interaction: VM Management + Container Orchestration + Multi-Agent Collaboration

ARCHITECTURE:
- AI operates from persistent VM environment (never resets)
- Orchestrates disposable container overlays for artifact execution
- Coordinates with other AI VMs for multi-agent collaboration
- Maintains security through architectural separation and local-only APIs

SECURITY MODEL:
- All communication is localhost-only (no external exposure)
- Docker API calls are local-only (127.0.0.1)
- Inter-VM communication uses secure local protocols
- Container isolation provides security boundaries
- No cloud dependencies or external APIs
"""

import asyncio
import difflib
import importlib
import importlib.util
import inspect
import json
from abc import ABC, abstractmethod
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4

import aiofiles

# Docker is optional — kernel operates without it for agent-only deployments.
DOCKER_AVAILABLE = False
try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    docker = None  # type: ignore[assignment]
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

artifact_system = None


class ArtifactManager:  # type: ignore[override]
    """Fallback ArtifactManager for standalone shell mode."""

    async def run_artifact(self, *args, **kwargs):
        raise RuntimeError("ArtifactManager is unavailable in standalone mode")


class RepoConfiguration:  # type: ignore[override]
    """Fallback repository configuration for standalone shell mode."""

    def __init__(self, *args, **kwargs):
        pass


class GitHubIntegrationManager:  # type: ignore[override]
    """Fallback GitHub integration manager when project core modules are unavailable."""

    def __init__(self, *args, **kwargs):
        self.available = False

    async def clone_repository(self, *args, **kwargs):
        raise RuntimeError("Git integration is unavailable in standalone mode")


class _FallbackProjectStatus(Enum):
    ACTIVE = "active"


ProjectStatus = _FallbackProjectStatus  # type: ignore

_load_external_integrations = os.getenv("AI_SHELL_LOAD_EXTERNAL_INTEGRATIONS", "0").strip().lower()
if _load_external_integrations in {"1", "true", "yes", "on"}:
    try:
        from core import artifact_system as _artifact_system  # type: ignore
        from core.artifact_system import ArtifactManager as _ArtifactManager  # type: ignore
        from core.git_integration_module import GitHubIntegrationManager as _GitHubIntegrationManager, RepoConfiguration as _RepoConfiguration  # type: ignore
        from projects.project_core import ProjectStatus as _ProjectStatus  # DO NOT IMPORT ANYMORE

        artifact_system = _artifact_system
        ArtifactManager = _ArtifactManager  # type: ignore
        GitHubIntegrationManager = _GitHubIntegrationManager  # type: ignore
        RepoConfiguration = _RepoConfiguration  # type: ignore
        ProjectStatus = _ProjectStatus  # type: ignore
    except Exception as integration_exc:
        logging.getLogger(__name__).warning(
            "External integrations unavailable; continuing in standalone mode: %s",
            integration_exc,
        )

# ---------------------------------------------------------------------------
# Graceful imports: Memory, Cache, Prompt System, File Processing
# These are optional subsystems — the shell works without them.
# ---------------------------------------------------------------------------
MEMORY_AVAILABLE = False
try:
    from src.memory_core import MemoryManager, MemoryConfiguration, MemoryType, MemoryImportance
    MEMORY_AVAILABLE = True
except ImportError:
    pass

CACHE_AVAILABLE = False
try:
    from src.system_cache import SomnusCache, CacheNamespace, CachePriority
    CACHE_AVAILABLE = True
except ImportError:
    pass

PROMPT_SYSTEM_AVAILABLE = False
try:
    from src.modifying_prompts import AutonomousPromptSystem
    PROMPT_SYSTEM_AVAILABLE = True
except ImportError:
    pass

FILE_PROCESSING_AVAILABLE = False
try:
    from src.artifacts.file_upload_system import FileUploadManager, ContentExtractor
    from src.artifacts.accelerated_file_processing import IntelligentFileProcessor, ProcessingPriority
    FILE_PROCESSING_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION AND MODELS
# ============================================================================

class ExecutionContext(str, Enum):
    """Execution context types.

    HOST_NATIVE is the canonical value for direct host-OS execution.
    VM_NATIVE is retained as a backward-compatible alias.
    """
    HOST_NATIVE = "host_native"              # Direct execution on the host OS
    VM_NATIVE = "host_native"                # Backward-compatible alias
    CONTAINER_OVERLAY = "container_overlay"  # Artifact container execution
    MULTI_AGENT = "multi_agent"             # Multi-agent collaboration
    HYBRID = "hybrid"                        # VM orchestrating container
    PROJECT = "project"                     # Project management orchestration


class CommandType(str, Enum):
    """Command classification for routing"""
    SYSTEM = "system"                       # Basic system commands
    ARTIFACT = "artifact"                   # Artifact-related operations
    COLLABORATION = "collaboration"         # Multi-agent commands
    RESEARCH = "research"                   # Research and analysis
    DEVELOPMENT = "development"             # Development workflows
    PROJECT = "project"                     # Project lifecycle commands


class InputType(str, Enum):
    """Input classification for the Master Router"""
    NATURAL_LANGUAGE = "natural"   # plain intent, no prefix
    DIRECT_COMMAND   = "command"   # starts with bb7_ or known tool name
    WORKFLOW         = "workflow"  # starts with "workflow:" or "run workflow:"


class ShellType(str, Enum):
    """Supported shell types for persistent terminal sessions"""
    BASH = "bash"                      # Standard bash shell
    POWERSHELL_CORE = "pwsh"          # PowerShell 7+ (cross-platform)
    POWERSHELL_DESKTOP = "powershell" # Windows PowerShell 5.1
    AZURE_CLOUD_SHELL = "azure"       # Azure Cloud Shell
    DEVELOPER_POWERSHELL = "devpwsh"  # VS Developer PowerShell
    ZSH = "zsh"                       # Zsh with oh-my-zsh support
    FISH = "fish"                     # Friendly Interactive Shell


@dataclass
class ExecutionResult:
    """Comprehensive execution result"""
    command: str
    stdout: str
    stderr: str
    return_code: int
    execution_time: float
    context: ExecutionContext
    command_type: CommandType
    was_corrected: bool = False
    correction_prompt: Optional[str] = None
    container_id: Optional[str] = None
    collaborator_responses: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShellProjectRecord:
    """Lightweight project record stored by the AI shell for orchestration."""
    project_id: str
    name: str
    description: str
    vm_id: str
    status: str
    created_at: str
    specs: Dict[str, Any] = field(default_factory=dict)
    repositories: List[Dict[str, Any]] = field(default_factory=list)


class ContextManager:
    """Preserve state across multi-step executions.

    Maintains a global key-value store and a stack of per-step context frames.
    get_current_context() returns a merged view: global values are the base,
    with each successive stack frame overriding them.
    """

    def __init__(self) -> None:
        self._global: Dict[str, Any] = {}
        self._stack: List[Dict[str, Any]] = []

    def update(self, key: str, value: Any) -> None:
        """Set a value in the global context store."""
        if not isinstance(key, str) or not key:
            raise ValueError("context key must be a non-empty string")
        self._global[key] = value

    def push_context(self, ctx: Dict[str, Any]) -> None:
        """Push a new context frame onto the stack."""
        if not isinstance(ctx, dict):
            raise TypeError("push_context requires a dict")
        self._stack.append(dict(ctx))

    def pop_context(self) -> Dict[str, Any]:
        """Pop and return the most-recent context frame.

        Raises IndexError if the stack is empty.
        """
        if not self._stack:
            raise IndexError("context stack is empty")
        return self._stack.pop()

    def get_current_context(self) -> Dict[str, Any]:
        """Return a merged view of global context overridden by stack frames."""
        merged: Dict[str, Any] = dict(self._global)
        for frame in self._stack:
            merged.update(frame)
        return merged

    def clear(self) -> None:
        """Reset both the global store and the stack."""
        self._global.clear()
        self._stack.clear()


class ShellSession:
    """Persistent shell session with process lifecycle management"""
    
    def __init__(self, shell_type: ShellType, session_id: str = None):
        self.shell_type = shell_type
        self.session_id = session_id or f"shell-{str(uuid4())[:8]}"
        self.process = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.working_directory = Path.cwd()
        self.environment = os.environ.copy()
        self.history: List[str] = []
        self.established = False
        self._lock = asyncio.Lock()
        
    async def __aenter__(self):
        """Context manager entry - establish shell connection"""
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - clean up resources"""
        await self.cleanup()
        
    async def initialize(self):
        """Initialize persistent shell process"""
        try:
            # Determine shell command based on type
            shell_commands = {
                ShellType.BASH: ["/bin/bash", "--login"],
                ShellType.ZSH: ["/bin/zsh", "--login"],
                ShellType.FISH: ["/bin/fish"],
                ShellType.POWERSHELL_CORE: ["pwsh", "-NoExit", "-Command", "& { $host.UI.RawUI.WindowTitle = 'AI Shell'; while($true) { Sleep 1 } }"],
                ShellType.POWERSHELL_DESKTOP: ["powershell", "-NoExit"],
                ShellType.DEVELOPER_POWERSHELL: ["devpwsh", "-NoExit"],
                ShellType.AZURE_CLOUD_SHELL: ["az", "cloud-shell", "connect", "--shell-type", "pwsh"]
            }
            
            cmd = shell_commands.get(self.shell_type)
            if not cmd:
                raise ValueError(f"Unsupported shell type: {self.shell_type}")
                
            # Create subprocess with PIPE communication
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_directory,
                env=self.environment,
                shell=False
            )
            
            self.stdin = self.process.stdin
            self.stdout = self.process.stdout
            self.stderr = self.process.stderr
            
            # Verify shell is ready (send echo and wait for response)
            await self._verify_shell_ready()
            self.established = True
            
        except Exception as e:
            logger.error(f"Failed to initialize shell session {self.session_id}: {e}")
            raise
            
    async def _verify_shell_ready(self):
        """Verify shell is ready to accept commands"""
        test_cmd = "echo 'shell_ready'\n"
        self.stdin.write(test_cmd.encode())
        await self.stdin.drain()
        
        # Read response with timeout
        ready = asyncio.create_task(self._read_response(timeout=5.0))
        try:
            await asyncio.wait_for(ready, timeout=5.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Shell initialization failed - no response from shell process")
            
    async def _read_response(self, timeout: float = 30.0) -> str:
        """Read response from shell until prompt or timeout"""
        output = []
        start_time = asyncio.get_event_loop().time()
        
        while True:
            if asyncio.get_event_loop().time() - start_time > timeout:
                break
                
            try:
                line = await asyncio.wait_for(
                    self.stdout.readline(), 
                    timeout=0.1
                )
                
                if line:
                    decoded = line.decode().rstrip()
                    output.append(decoded)
                    
                    # Check for shell prompt indicators
                    if self._is_prompt_line(decoded):
                        break
            except asyncio.TimeoutError:
                continue
                
        return "\n".join(output).strip()
        
    def _is_prompt_line(self, line: str) -> bool:
        """Detect if line is a shell prompt"""
        prompt_patterns = [
            r".*\$\s*$",          # Bash/Zsh prompts
            r".*>\s*$",           # PowerShell prompts
            r".*#\s*$",           # Root prompts
            r"PS.*>\s*$",         # PowerShell PS> prompt
            r"azure:/.*>\s*$"     # Azure Cloud Shell prompt
        ]
        
        return bool(any(re.search(pattern, line) for pattern in prompt_patterns))
        
    async def execute_command(self, command: str, timeout: float = 300.0) -> ExecutionResult:
        """Execute command in persistent shell session"""
        async with self._lock:
            if not self.established:
                raise RuntimeError("Shell session not initialized")
                
            start_time = asyncio.get_event_loop().time()
            
            try:
                # Write command to stdin
                command_with_newline = f"{command}\n"
                self.stdin.write(command_with_newline.encode())
                await self.stdin.drain()
                
                # Record in history
                self.history.append(command)
                
                # Read response
                stdout = await asyncio.wait_for(
                    self._read_response(timeout=timeout),
                    timeout=timeout
                )
                
                execution_time = asyncio.get_event_loop().time() - start_time
                
                return ExecutionResult(
                    command=command,
                    stdout=stdout,
                    stderr="",  # stderr reading can be added
                    return_code=0,  # Return code tracking can be added
                    execution_time=execution_time,
                    context=ExecutionContext.VM_NATIVE,
                    command_type=self._classify_command(command),
                    metadata={"shell_type": self.shell_type, "session_id": self.session_id}
                )
                
            except asyncio.TimeoutError:
                execution_time = asyncio.get_event_loop().time() - start_time
                return ExecutionResult(
                    command=command,
                    stdout="",
                    stderr=f"Command timeout after {timeout}s",
                    return_code=1,
                    execution_time=execution_time,
                    context=ExecutionContext.VM_NATIVE,
                    command_type=self._classify_command(command),
                    metadata={"shell_type": self.shell_type, "session_id": self.session_id}
                )
                
    def _classify_command(self, command: str) -> CommandType: #Needs advanced logic . Docker is only called for aetifacts. There is no set artifact config and they are called/configured per call. They are disposable super computers. Artifacts have many seperate configs the ai can call. The full artifact system all used together is only when utilized by the human user serving as a seperate system. Containers are not persistent they are disposed and onlu one can be active at a time sort of like a temporary process. This serves to "sandbox" yet increase tenfold the models capabilties. This terminal shell should not expect an operating system, a user, or output. This is tailored towars ai use but humans aswell routing through different inputs serving as the "end-all" be all replacement to terminals and kernels. This file is over engineered on purpose in one file we have done it this far we will not seperate. To keep with the advanced complexity i personally have coded, I simply ask you to not be over whelmed but keep the advanced SOTA++ level code. This means the most advanced code possible to be produced. You are contuing my systems. Never focus too hard on one function or capability but always consider the entire codebase. The vm creation logic i made for me as i will have my own copy of this kernel and turnroute to my own terminal. This is an agnostic codebase ie this codebase when done we will just fork/copy to different codebases. bb7 tools r only considered native as they expect and output json. The shell still does allow any MCP protocol. The native and src/ tools are always running. The "router" must ALWAYS direct to modifying_prompts that is the models "context window" tied with the memory. Think of the fast path tools as more like ability to specialize say if some use case involved a project or other tpe. The memory tool from bb7 should be for per project, the src system is the always on core system. The advanced web tool allows absolute access to the web, tied to git allowing full sovereignty. The bb7 web tool likely will rarely be used
        """Classify command for routing.

        Structured pipeline:
          1. Native tool commands → SYSTEM (JSON I/O, routed through NativeToolBridge)
          2. Project lifecycle → PROJECT
          3. Docker/container/kubectl → ARTIFACT (disposable sandbox — per-call config)
          4. Collaboration verbs → COLLABORATION
          5. Research/analysis intents → RESEARCH
          6. Development tool commands → DEVELOPMENT
          7. Default → SYSTEM (OS-level shell)
        """
        lower = command.lower().strip()

        # 1. Development tool commands (exact prefix match)
        if any(lower.startswith(p) for p in (
            'git ', 'npm ', 'pip ', 'cargo ', 'make ', 'cmake ',
            'python ', 'python3 ', 'node ', 'go ', 'rustc ',
            'gcc ', 'g++ ', 'javac ',
        )):
            return CommandType.DEVELOPMENT

        # 2. Container/artifact commands (disposable sandbox semantics)
        if any(lower.startswith(p) for p in ('docker ', 'kubectl ', 'podman ')):
            return CommandType.ARTIFACT

        # 3. Cloud CLI → RESEARCH (infrastructure interrogation)
        if any(lower.startswith(p) for p in ('az ', 'aws ', 'gcloud ')):
            return CommandType.RESEARCH

        # 4. Default: SYSTEM (OS-level shell command)
        return CommandType.SYSTEM
            
    def update_environment(self, key: str, value: str):
        """Update environment variable in shell session"""
        self.environment[key] = value
        if self.established:
            # Send environment update command to shell
            if self.shell_type in [ShellType.POWERSHELL_CORE, ShellType.POWERSHELL_DESKTOP, ShellType.DEVELOPER_POWERSHELL]:
                asyncio.create_task(self.execute_command(f"$env:{key}='{value}'"))
            else:
                asyncio.create_task(self.execute_command(f"export {key}={value}"))
                
    def set_working_directory(self, path: Union[str, Path]):
        """Change working directory in shell session"""
        self.working_directory = Path(path)
        if self.established:
            if self.shell_type in [ShellType.POWERSHELL_CORE, ShellType.POWERSHELL_DESKTOP, ShellType.DEVELOPER_POWERSHELL]:
                asyncio.create_task(self.execute_command(f"Set-Location '{path}'"))
            else:
                asyncio.create_task(self.execute_command(f"cd '{path}'"))
                
    async def cleanup(self):
        """Clean up shell process and resources"""
        if self.process and self.process.returncode is None:
            try:
                # Send exit command based on shell type
                if self.shell_type in [ShellType.POWERSHELL_CORE, ShellType.POWERSHELL_DESKTOP, ShellType.DEVELOPER_POWERSHELL]:
                    self.stdin.write("exit\n".encode())
                else:
                    self.stdin.write("exit\n".encode())
                    
                await self.stdin.drain()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                
            except asyncio.TimeoutError:
                # Force terminate if graceful exit fails
                self.process.terminate()
                await self.process.wait()
                
            finally:
                self.established = False


@dataclass
class ContainerSpec:
    """Container specification for artifact execution"""
    image: str = "somnus-artifact:unlimited"
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None
    gpu_access: bool = True
    network_access: bool = True
    environment: Dict[str, str] = field(default_factory=dict)
    volumes: Dict[str, str] = field(default_factory=dict)
    working_dir: str = "/workspace"


@dataclass
class CollaborationSession:
    """Multi-agent collaboration session"""
    session_id: UUID
    primary_agent_id: UUID
    collaborator_ids: List[UUID]
    task_description: str
    status: str = "initializing"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    responses: Dict[UUID, str] = field(default_factory=dict)
    final_synthesis: Optional[str] = None


class AIShellSettings(BaseSettings):
    """Advanced AI Shell configuration"""
    # VM Connection settings
    vm_host: str = "127.0.0.1"
    vm_port: int = 22
    vm_user: str = "morpheus"
    vm_password: Optional[str] = None
    vm_ssh_key_path: Optional[str] = f"{os.path.expanduser('~')}/.ssh/id_rsa"
    command_timeout: int = 300  # 5 minutes default
    
    # Container orchestration
    docker_host: str = "unix:///var/run/docker.sock"
    container_network: str = "somnus_network"
    artifact_registry: str = "localhost:5000"
    
    # Multi-agent collaboration
    collaboration_port_base: int = 8100
    max_concurrent_agents: int = 10
    agent_communication_timeout: int = 30
    
    # Memory and logging
    memory_log_path: str = "./data/ai_shell_memory.jsonl"
    execution_log_path: str = "./data/ai_shell_execution.log"
    
    # Security settings
    allow_privileged_containers: bool = False
    restrict_network_access: bool = False
    enable_command_validation: bool = True

    # Native tool bridge (direct loading from tools/ without MCP transport)
    enable_native_tools: bool = True
    native_tools_dir: Optional[str] = None
    native_tool_prefix: str = "bb7_"

    # Memory subsystem
    enable_memory: bool = True
    memory_db_path: str = "data/memory"

    # Cache subsystem
    enable_cache: bool = True
    cache_dir: str = "data/runtime_cache"
    cache_max_entries: int = 10000
    cache_max_memory_mb: int = 512

    # Autonomous Prompt System
    enable_prompt_system: bool = True

    # File processing (direct OS)
    enable_file_processing: bool = True
    file_upload_dir: str = "data/file_uploads"
    
    class Config:
        env_prefix = "AI_SHELL_"


# ============================================================================
# CORE VM MANAGER
# ============================================================================

class AdvancedVMManager:
    """Enhanced VM manager with container orchestration capabilities"""
    
    def __init__(self, settings: AIShellSettings, agent_id: Optional[UUID] = None):
        self.settings = settings
        self.agent_id = agent_id or uuid4()
        # self.ssh_client = None  # removed
        self.docker_client = None
        self.active_containers: Dict[str, Any] = {}
        self.collaboration_socket: Optional[socket.socket] = None
        
    async def initialize(self) -> bool:
        """Initialize VM and container connections"""
        try:
            # Establish Docker client for container orchestration
            await self._initialize_docker_client()
            
            # Setup collaboration networking if needed
            await self._setup_collaboration_network()
            
            logger.info(f"AI Shell initialized for agent {self.agent_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize AI Shell: {e}")
            return False
    
    async def _initialize_docker_client(self):
        """Initialize Docker client for container orchestration"""
        try:
            self.docker_client = docker.from_env()
            
            # Test Docker connection with timeout
            try:
                self.docker_client.ping()
            except docker.errors.APIError as e:
                raise ConnectionError(f"Docker API error: {e}")
            
            # Ensure Somnus network exists with proper configuration
            try:
                network = self.docker_client.networks.get(self.settings.container_network)
                # Verify network configuration
                network.reload()
                if not network.attrs.get('Driver') == 'bridge':
                    logger.warning(f"Network {self.settings.container_network} has unexpected driver")
            except docker.errors.NotFound:
                # Create network with security-focused options
                ipam_pool = docker.types.IPAMPool(
                    subnet='172.20.0.0/16',
                    gateway='172.20.0.1'
                )
                ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool])
                
                self.docker_client.networks.create(
                    self.settings.container_network,
                    driver="bridge",
                    ipam=ipam_config,
                    options={
                        "com.docker.network.bridge.enable_icc": "true",
                        "com.docker.network.bridge.enable_ip_masquerade": "true",
                        "com.docker.network.bridge.host_binding_ipv4": "127.0.0.1"
                    },
                    labels={
                        "somnus.system": "ai_shell",
                        "somnus.agent_id": str(self.agent_id)
                    }
                )
            
            # Verify Docker daemon capabilities
            info = self.docker_client.info()
            if not info.get('ServerVersion'):
                raise RuntimeError("Unable to determine Docker server version")
                
            logger.info(f"Docker client initialized (server: {info['ServerVersion']})")
            
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            raise
    
    async def _setup_collaboration_network(self):
        """Setup networking for multi-agent collaboration"""
        try:
            # Calculate unique port for this agent
            agent_hash = hash(str(self.agent_id)) & 0x7FFFFFFF  # Ensure positive
            collaboration_port = self.settings.collaboration_port_base + (agent_hash % 1000)
            
            # Ensure port is within valid range
            if collaboration_port > 65535:
                collaboration_port = 65535
            
            # Create listening socket for agent communication
            self.collaboration_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.collaboration_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.collaboration_socket.setblocking(False)
            
            # Bind to localhost only for security
            self.collaboration_socket.bind(("127.0.0.1", collaboration_port))
            self.collaboration_socket.listen(5)
            
            logger.info(f"Collaboration network setup on port {collaboration_port}")
            
        except OSError as e:
            if e.errno == 98:  # Address already in use
                # Try next available port
                for port in range(collaboration_port + 1, collaboration_port + 100):
                    try:
                        self.collaboration_socket.bind(("127.0.0.1", port))
                        self.collaboration_socket.listen(5)
                        logger.info(f"Collaboration network setup on fallback port {port}")
                        break
                    except OSError:
                        continue
                else:
                    raise RuntimeError("No available ports for collaboration network")
            else:
                logger.error(f"Failed to setup collaboration network: {e}")
                raise
    
    async def execute_in_vm(self, command: str, **kwargs) -> Tuple[str, str, int]:
        """Execute command on the host OS.

        Prefers ``artifact_system.run_command()`` when available (integrated
        mode).  Falls back to ``asyncio.create_subprocess_shell()`` for
        standalone / host-native operation.
        """
        try:
            if not command or not isinstance(command, str):
                raise ValueError("Invalid command provided")

            command = command.strip()
            if not command:
                raise ValueError("Empty command provided")

            logger.debug(f"Executing on host: {command}")

            # Path 1: Integrated artifact system (when available)
            if artifact_system is not None and hasattr(artifact_system, 'run_command'):
                stdout_data, stderr_data, exit_status = artifact_system.run_command(
                    command,
                    timeout=self.settings.command_timeout
                )
                logger.info(f"Host command (artifact_system): exit_code={exit_status}, cmd_length={len(command)}")
                return stdout_data, stderr_data, exit_status

            # Path 2: Host-native subprocess fallback (standalone mode)
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.cwd()),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(self.settings.command_timeout),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error(f"Command timed out after {self.settings.command_timeout}s: {command[:80]}")
                return "", f"Command timed out after {self.settings.command_timeout}s", 1

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode if proc.returncode is not None else 0
            logger.info(f"Host command (subprocess): exit_code={exit_code}, cmd_length={len(command)}")
            return stdout_str, stderr_str, exit_code

        except Exception as e:
            logger.error(f"Host execution error: {e}")
            return "", str(e), -1


# ============================================================================
# CONTAINER ORCHESTRATION
# ============================================================================

class ContainerOrchestrator:
    """Orchestrates container overlays for artifact execution"""
    
    def __init__(self, vm_manager: AdvancedVMManager):
        self.vm_manager = vm_manager
        self.active_containers: Dict[str, docker.models.containers.Container] = {}
        self._container_lock = asyncio.Lock()
    
    async def create_artifact_container(
        self, 
        artifact_id: str, 
        spec: ContainerSpec,
        files: Dict[str, str] = None
    ) -> str:
        """Create specialized container for artifact execution with full validation"""
        if not artifact_id or not isinstance(artifact_id, str):
            raise ValueError("Invalid artifact_id provided")
        
        if not spec or not isinstance(spec, ContainerSpec):
            raise ValueError("Invalid ContainerSpec provided")

        if not DOCKER_AVAILABLE:
            raise RuntimeError(
                "Docker is not available. Install the 'docker' Python package "
                "and ensure the Docker daemon is running."
            )
        
        async with self._container_lock:
            try:
                # ── Single-active-container enforcement ──────────────────
                # Per architectural doctrine: containers are disposable,
                # only one can be active at a time (temporary sandboxed process).
                if self.active_containers:
                    existing = list(self.active_containers.keys())
                    logger.info(
                        f"Enforcing single-active-container policy: "
                        f"cleaning up {len(existing)} existing container(s)"
                    )
                    for name in existing:
                        try:
                            await self.cleanup_container(name, force=True)
                        except Exception as cleanup_exc:
                            logger.warning(f"Pre-create cleanup of '{name}' failed: {cleanup_exc}")

                container_name = f"artifact_{artifact_id}_{int(time.time())}"
                
                # Validate container image exists
                try:
                    self.vm_manager.docker_client.images.get(spec.image)
                except docker.errors.ImageNotFound:
                    logger.info(f"Pulling container image: {spec.image}")
                    self.vm_manager.docker_client.images.pull(spec.image)
                
                # Prepare container configuration with security hardening
                container_config = {
                    "image": spec.image,
                    "name": container_name,
                    "detach": True,
                    "network": self.vm_manager.settings.container_network,
                    "working_dir": spec.working_dir,
                    "environment": {
                        **spec.environment,
                        "AGENT_ID": str(self.vm_manager.agent_id),
                        "ARTIFACT_ID": artifact_id
                    },
                    "volumes": {
                        **spec.volumes,
                        "/tmp": {"bind": "/tmp", "mode": "rw"}
                    },
                    "auto_remove": False,
                    "stdin_open": True,
                    "tty": True,
                    "remove": False,
                    "labels": {
                        "somnus.system": "ai_shell",
                        "somnus.agent_id": str(self.vm_manager.agent_id),
                        "somnus.artifact_id": artifact_id,
                        "somnus.created_at": datetime.now(timezone.utc).isoformat()
                    }
                }
                
                # Add resource limits with validation
                if spec.cpu_limit:
                    try:
                        cpu_value = float(spec.cpu_limit)
                        if cpu_value <= 0:
                            raise ValueError("CPU limit must be positive")
                        container_config["cpu_period"] = 100000
                        container_config["cpu_quota"] = int(cpu_value * 100000)
                    except ValueError as e:
                        logger.warning(f"Invalid CPU limit '{spec.cpu_limit}': {e}")
                
                if spec.memory_limit:
                    try:
                        # Parse memory limit (supports formats like "512m", "1g")
                        memory_bytes = self._parse_memory_limit(spec.memory_limit)
                        container_config["mem_limit"] = memory_bytes
                    except ValueError as e:
                        logger.warning(f"Invalid memory limit '{spec.memory_limit}': {e}")
                
                # Security restrictions
                if not self.vm_manager.settings.allow_privileged_containers:
                    container_config["privileged"] = False
                    container_config["cap_drop"] = ["ALL"]
                    container_config["security_opt"] = ["no-new-privileges"]
                
                # Network restrictions
                if self.vm_manager.settings.restrict_network_access:
                    container_config["network_mode"] = "none"
                
                # Enable GPU access if requested and available
                if spec.gpu_access:
                    try:
                        info = self.vm_manager.docker_client.info()
                        if 'Runtimes' in info and 'nvidia' in info['Runtimes']:
                            container_config["device_requests"] = [
                                docker.types.DeviceRequest(
                                    count=-1, 
                                    capabilities=[["gpu"]],
                                    options={'gpu': 'all'}
                                )
                            ]
                    except Exception as e:
                        logger.warning(f"GPU access requested but not available: {e}")
                
                # Create and start container
                container = self.vm_manager.docker_client.containers.run(**container_config)
                
                # Wait for container to be ready
                max_wait = 30
                waited = 0
                while waited < max_wait:
                    container.reload()
                    if container.status == 'running':
                        break
                    await asyncio.sleep(1)
                    waited += 1
                
                if container.status != 'running':
                    logs = container.logs().decode('utf-8', errors='replace')
                    raise RuntimeError(f"Container failed to start: {logs}")
                
                # Copy files to container if provided
                if files:
                    await self._copy_files_to_container(container, files)
                
                self.active_containers[container_name] = container
                
                logger.info(f"Created artifact container: {container_name} (status: {container.status})")
                return container_name
                
            except docker.errors.APIError as e:
                logger.error(f"Docker API error creating container: {e}")
                raise
            except Exception as e:
                logger.error(f"Failed to create artifact container: {e}")
                raise
    
    def _parse_memory_limit(self, limit: str) -> int:
        """Parse memory limit string to bytes"""
        limit = limit.lower().strip()
        
        multipliers = {
            'k': 1024,
            'm': 1024 * 1024,
            'g': 1024 * 1024 * 1024,
            'kb': 1024,
            'mb': 1024 * 1024,
            'gb': 1024 * 1024 * 1024
        }
        
        for suffix, multiplier in multipliers.items():
            if limit.endswith(suffix):
                try:
                    value = float(limit[:-len(suffix)])
                    return int(value * multiplier)
                except ValueError:
                    pass
        
        # Try direct bytes
        try:
            return int(limit)
        except ValueError:
            raise ValueError(f"Invalid memory limit format: {limit}")
    
    async def execute_in_container(
        self, 
        container_name: str, 
        command: str,
        stream_output: bool = False
    ) -> Tuple[str, str, int]:
        """Execute command in specific container with comprehensive monitoring"""
        if not container_name or not isinstance(container_name, str):
            raise ValueError("Invalid container_name provided")
        
        if not command or not isinstance(command, str):
            raise ValueError("Invalid command provided")
        
        try:
            container = self.active_containers.get(container_name)
            if not container:
                # Try to get container from Docker if not in active list
                try:
                    container = self.vm_manager.docker_client.containers.get(container_name)
                except docker.errors.NotFound:
                    raise ValueError(f"Container {container_name} not found")
            
            # Verify container is running
            container.reload()
            if container.status != 'running':
                raise RuntimeError(f"Container {container_name} is not running (status: {container.status})")
            
            logger.debug(f"Executing in container {container_name}: {command}")
            
            # Execute command with proper timeout
            timeout = self.vm_manager.settings.command_timeout
            exec_result = container.exec_run(
                cmd=["/bin/bash", "-c", command],
                stdout=True,
                stderr=True,
                stream=stream_output,
                demux=True,
                tty=False,
                privileged=False,
                user="root"
            )
            
            if stream_output:
                # Handle streaming output with timeout
                stdout_lines = []
                stderr_lines = []
                
                try:
                    for output in exec_result.output:
                        if output[0]:  # stdout
                            stdout_lines.append(output[0].decode('utf-8', errors='replace'))
                        if output[1]:  # stderr
                            stderr_lines.append(output[1].decode('utf-8', errors='replace'))
                    
                    stdout_data = ''.join(stdout_lines)
                    stderr_data = ''.join(stderr_lines)
                except Exception as e:
                    stdout_data = ""
                    stderr_data = f"Error reading stream output: {e}"
            else:
                stdout_data = exec_result.output.decode('utf-8', errors='replace') if exec_result.output else ""
                stderr_data = ""
            
            # Log execution
            logger.info(f"Container command executed: container={container_name}, exit_code={exec_result.exit_code}")
            
            return stdout_data, stderr_data, exec_result.exit_code
            
        except docker.errors.APIError as e:
            logger.error(f"Docker API error during container execution: {e}")
            return "", str(e), -1
        except Exception as e:
            logger.error(f"Container execution error: {e}")
            return "", str(e), -1
    
    async def _copy_files_to_container(self, container, files: Dict[str, str]):
        """Copy files to container workspace with validation"""
        if not files or not isinstance(files, dict):
            raise ValueError("Invalid files dictionary provided")
        
        try:
            import tarfile
            import io
            
            # Validate file paths and content
            for filename, content in files.items():
                if not filename or not isinstance(filename, str):
                    raise ValueError("Invalid filename in files dictionary")
                if not isinstance(content, str):
                    raise ValueError(f"Content for {filename} must be string")
                
                # Sanitize filename
                if '..' in filename or filename.startswith('/'):
                    raise ValueError(f"Invalid filename: {filename}")
            
            # Create tar archive with files
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
                for filename, content in files.items():
                    file_data = content.encode('utf-8')
                    tarinfo = tarfile.TarInfo(name=filename)
                    tarinfo.size = len(file_data)
                    tarinfo.mode = 0o644
                    tarinfo.mtime = time.time()
                    tar.addfile(tarinfo, io.BytesIO(file_data))
            
            tar_buffer.seek(0)
            
            # Extract to container
            success = container.put_archive("/workspace", tar_buffer.getvalue())
            if not success:
                raise RuntimeError("Failed to copy files to container")
            
            logger.debug(f"Successfully copied {len(files)} files to container")
            
        except Exception as e:
            logger.error(f"Failed to copy files to container: {e}")
            raise
    
    async def cleanup_container(self, container_name: str, force: bool = False):
        """Cleanup artifact container with proper resource management"""
        if not container_name:
            return
        
        try:
            container = self.active_containers.get(container_name)
            if not container:
                # Try to get from Docker
                try:
                    container = self.vm_manager.docker_client.containers.get(container_name)
                except docker.errors.NotFound:
                    logger.warning(f"Container {container_name} not found during cleanup")
                    return
            
            # Get logs before cleanup if container had issues
            container.reload()
            if container.status == 'exited' and container.attrs.get('State', {}).get('ExitCode', 0) != 0:
                logs = container.logs(tail=100).decode('utf-8', errors='replace')
                logger.warning(f"Container {container_name} exited with errors: {logs}")
            
            # Stop container gracefully
            try:
                container.stop(timeout=10 if not force else 1)
            except docker.errors.APIError as e:
                if "is not running" not in str(e):
                    logger.warning(f"Error stopping container {container_name}: {e}")
            
            # Remove container
            try:
                container.remove(force=force, v=True)  # Remove volumes as well
            except docker.errors.APIError as e:
                logger.warning(f"Error removing container {container_name}: {e}")
            
            # Remove from active containers
            if container_name in self.active_containers:
                del self.active_containers[container_name]
            
            logger.info(f"Cleaned up container: {container_name}")
            
        except Exception as e:
            logger.error(f"Failed to cleanup container {container_name}: {e}")
    
    async def cleanup_all_containers(self):
        """Cleanup all active containers"""
        containers_to_cleanup = list(self.active_containers.keys())
        for container_name in containers_to_cleanup:
            await self.cleanup_container(container_name)


# ============================================================================
# MULTI-AGENT COLLABORATION
# ============================================================================

class CollaborationManager:
    """Manages multi-agent collaboration sessions with security and reliability"""
    
    def __init__(self, vm_manager: AdvancedVMManager):
        self.vm_manager = vm_manager
        self.active_sessions: Dict[UUID, CollaborationSession] = {}
        self.agent_connections: Dict[UUID, Dict[str, Any]] = {}
        self._session_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
    
    async def initiate_collaboration(
        self, 
        task_description: str,
        collaborator_agents: List[UUID],
        coordination_strategy: str = "parallel"
    ) -> UUID:
        """Initiate multi-agent collaboration session with full validation"""
        if not task_description or not isinstance(task_description, str):
            raise ValueError("Invalid task_description provided")
        
        if not collaborator_agents or not isinstance(collaborator_agents, list):
            raise ValueError("Invalid collaborator_agents list provided")
        
        if len(collaborator_agents) > self.vm_manager.settings.max_concurrent_agents:
            raise ValueError(f"Too many collaborators: {len(collaborator_agents)} > {self.vm_manager.settings.max_concurrent_agents}")
        
        async with self._session_lock:
            try:
                session_id = uuid4()
                
                # Validate all agent IDs
                for agent_id in collaborator_agents:
                    if not isinstance(agent_id, UUID):
                        raise ValueError(f"Invalid agent ID: {agent_id}")
                
                session = CollaborationSession(
                    session_id=session_id,
                    primary_agent_id=self.vm_manager.agent_id,
                    collaborator_ids=collaborator_agents,
                    task_description=task_description,
                    status="initializing"
                )
                
                self.active_sessions[session_id] = session
                
                # Establish connections to collaborator agents with timeout
                connection_tasks = []
                for agent_id in collaborator_agents:
                    connection_tasks.append(self._connect_to_agent(agent_id))
                
                # Wait for all connections with timeout
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*connection_tasks, return_exceptions=True),
                        timeout=30
                    )
                except asyncio.TimeoutError:
                    raise RuntimeError("Timeout establishing agent connections")
                
                # Send task delegation messages
                delegation_tasks = []
                for agent_id in collaborator_agents:
                    if agent_id in self.agent_connections:
                        delegation_tasks.append(
                            self._send_task_delegation(session_id, agent_id, task_description)
                        )
                
                if delegation_tasks:
                    await asyncio.wait_for(
                        asyncio.gather(*delegation_tasks, return_exceptions=True),
                        timeout=30
                    )
                
                session.status = "executing"
                
                logger.info(
                    f"Initiated collaboration session {session_id} "
                    f"with {len(collaborator_agents)} agents: {collaborator_agents}"
                )
                
                return session_id
                
            except Exception as e:
                logger.error(f"Failed to initiate collaboration: {e}")
                # Cleanup on failure
                if session_id in self.active_sessions:
                    del self.active_sessions[session_id]
                raise
    
    async def _connect_to_agent(self, agent_id: UUID):
        """Establish secure connection to collaborator agent"""
        if not isinstance(agent_id, UUID):
            raise ValueError("Invalid agent ID")
        
        async with self._connection_lock:
            try:
                if agent_id in self.agent_connections:
                    # Check if existing connection is still valid
                    connection = self.agent_connections[agent_id]
                    sock = connection.get("socket")
                    if sock and not sock._closed:
                        return
                    else:
                        # Close and remove stale connection
                        if sock:
                            sock.close()
                        del self.agent_connections[agent_id]
                
                # Calculate agent's collaboration port
                agent_hash = hash(str(agent_id)) & 0x7FFFFFFF
                agent_port = self.vm_manager.settings.collaboration_port_base + (agent_hash % 1000)
                
                # Ensure port is valid
                if agent_port > 65535:
                    agent_port = 65535
                
                # Create connection with timeout
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.vm_manager.settings.agent_communication_timeout)
                
                # Connect with retry
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        sock.connect(("127.0.0.1", agent_port))
                        break
                    except (ConnectionRefusedError, socket.timeout) as e:
                        if attempt == max_retries - 1:
                            raise ConnectionError(f"Failed to connect to agent {agent_id} on port {agent_port}: {e}")
                        await asyncio.sleep(1)
                
                # Set socket to non-blocking for async operations
                sock.setblocking(False)
                
                self.agent_connections[agent_id] = {
                    "socket": sock,
                    "connected_at": datetime.now(timezone.utc),
                    "last_activity": datetime.now(timezone.utc)
                }
                
                logger.info(f"Connected to collaborator agent {agent_id} on port {agent_port}")
                
            except Exception as e:
                logger.error(f"Failed to connect to agent {agent_id}: {e}")
                raise
    
    async def _send_task_delegation(self, session_id: UUID, agent_id: UUID, task: str):
        """Send secure task delegation to collaborator agent"""
        if not all([session_id, agent_id, task]):
            raise ValueError("Invalid parameters for task delegation")
        
        try:
            connection = self.agent_connections.get(agent_id)
            if not connection:
                raise ConnectionError(f"No connection to agent {agent_id}")
            
            message = {
                "type": "task_delegation",
                "session_id": str(session_id),
                "sender_id": str(self.vm_manager.agent_id),
                "task_description": task,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "protocol_version": "1.0"
            }
            
            sock = connection["socket"]
            message_data = json.dumps(message).encode('utf-8')
            message_length = len(message_data)
            
            # Send with timeout
            sock.settimeout(self.vm_manager.settings.agent_communication_timeout)
            
            # Send length first (4 bytes, big-endian)
            sock.send(message_length.to_bytes(4, 'big'))
            
            # Send message data
            total_sent = 0
            while total_sent < message_length:
                sent = sock.send(message_data[total_sent:])
                if sent == 0:
                    raise ConnectionError("Socket connection broken")
                total_sent += sent
            
            # Update last activity
            connection["last_activity"] = datetime.now(timezone.utc)
            
            logger.debug(f"Sent task delegation to agent {agent_id} for session {session_id}")
            
        except Exception as e:
            logger.error(f"Failed to send task delegation to agent {agent_id}: {e}")
            # Remove broken connection
            if agent_id in self.agent_connections:
                self.agent_connections[agent_id]["socket"].close()
                del self.agent_connections[agent_id]
            raise
    
    async def collect_collaboration_responses(self, session_id: UUID, timeout: int = 300) -> Dict[str, Any]:
        """Collect responses from all collaborating agents with timeout management"""
        if not isinstance(session_id, UUID):
            raise ValueError("Invalid session_id")
        
        session = self.active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status != "executing":
            raise ValueError(f"Session {session_id} is not in executing state")
        
        try:
            responses = {}
            start_time = time.time()
            
            # Collect responses from each collaborator
            response_tasks = []
            for agent_id in session.collaborator_ids:
                if agent_id in self.agent_connections:
                    response_tasks.append(
                        self._receive_agent_response(agent_id, timeout)
                    )
                else:
                    responses[str(agent_id)] = {"error": "No active connection"}
            
            # Wait for responses with timeout
            if response_tasks:
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*response_tasks, return_exceptions=True),
                        timeout=timeout
                    )
                    
                    # Map results to agent IDs
                    for agent_id, result in zip(session.collaborator_ids, results):
                        if isinstance(result, Exception):
                            responses[str(agent_id)] = {"error": str(result)}
                        else:
                            responses[str(agent_id)] = result
                except asyncio.TimeoutError:
                    # Handle timeout gracefully
                    for agent_id in session.collaborator_ids:
                        if str(agent_id) not in responses:
                            responses[str(agent_id)] = {"error": "Response timeout"}
            
            session.responses = responses
            session.status = "synthesis"
            
            # Synthesize final response
            final_response = await self._synthesize_responses(session)
            session.final_synthesis = final_response
            session.status = "completed"
            
            total_time = time.time() - start_time
            
            return {
                "session_id": str(session_id),
                "total_time": total_time,
                "individual_responses": responses,
                "synthesized_response": final_response,
                "participating_agents": [str(aid) for aid in session.collaborator_ids],
                "completion_time": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to collect collaboration responses: {e}")
            session.status = "failed"
            raise
    
    async def _receive_agent_response(self, agent_id: UUID, timeout: int) -> Dict[str, Any]:
        """Receive response from specific agent with timeout handling"""
        if not isinstance(agent_id, UUID):
            raise ValueError("Invalid agent_id")
        
        connection = self.agent_connections.get(agent_id)
        if not connection:
            raise ConnectionError(f"No connection to agent {agent_id}")
        
        try:
            sock = connection["socket"]
            sock.settimeout(timeout)
            
            # Receive message length
            length_data = b""
            while len(length_data) < 4:
                chunk = sock.recv(4 - len(length_data))
                if not chunk:
                    raise ConnectionError("Connection closed by peer")
                length_data += chunk
            
            message_length = int.from_bytes(length_data, 'big')
            
            if message_length <= 0 or message_length > 10 * 1024 * 1024:  # 10MB limit
                raise ValueError(f"Invalid message length: {message_length}")
            
            # Receive message data
            message_data = b""
            while len(message_data) < message_length:
                chunk = sock.recv(min(message_length - len(message_data), 8192))
                if not chunk:
                    raise ConnectionError("Connection closed during message reception")
                message_data += chunk
            
            response = json.loads(message_data.decode('utf-8'))
            
            # Validate response structure
            if not isinstance(response, dict):
                raise ValueError("Invalid response format")
            
            # Update last activity
            connection["last_activity"] = datetime.now(timezone.utc)
            
            return response
            
        except socket.timeout:
            raise TimeoutError(f"Timeout receiving response from agent {agent_id}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in agent response: {e}")
        except Exception as e:
            logger.error(f"Failed to receive response from agent {agent_id}: {e}")
            # Remove broken connection
            sock.close()
            if agent_id in self.agent_connections:
                del self.agent_connections[agent_id]
            raise
    
    async def _synthesize_responses(self, session: CollaborationSession) -> str:
        """Synthesize individual agent responses into unified result with intelligence"""
        try:
            if not session.responses:
                return "No responses received from collaborating agents"
            
            # Analyze response quality and completeness
            successful_responses = []
            error_responses = []
            
            for agent_id, response in session.responses.items():
                if isinstance(response, dict) and "error" not in response:
                    successful_responses.append((agent_id, response))
                else:
                    error_responses.append((agent_id, response))
            
            # Build comprehensive synthesis
            synthesis_parts = [
                f"# Multi-Agent Collaboration Result",
                f"**Session ID:** {session.session_id}",
                f"**Task:** {session.task_description}",
                f"**Completed:** {datetime.now(timezone.utc).isoformat()}",
                f"**Participants:** {len(session.collaborator_ids)} agents",
                "",
                "## Summary",
                f"- **Successful Responses:** {len(successful_responses)}",
                f"- **Failed Responses:** {len(error_responses)}",
                "",
                "## Individual Contributions"
            ]
            
            # Add successful responses
            for agent_id, response in successful_responses:
                content = response.get('content', str(response))
                synthesis_parts.append(f"\n### Agent {agent_id}")
                synthesis_parts.append(f"```\n{content}\n```")
            
            # Add error responses
            if error_responses:
                synthesis_parts.append("\n## Errors and Issues")
                for agent_id, response in error_responses:
                    error_message = response.get('error', 'Unknown error')
                    synthesis_parts.append(f"- **Agent {agent_id}:** {error_message}")
            
            # Final remarks
            synthesis_parts.append("\n---")
            synthesis_parts.append("End of collaborative synthesis.")
            
            return "\n".join(synthesis_parts)
            
        except Exception as e:
            logger.error(f"Failed to synthesize responses: {e}")
            return f"Synthesis failed: {str(e)}"


class ShellManager:
    """Manages persistent shell sessions for different shell types"""
    
    def __init__(self, logger: logging.Logger):
        self.sessions: Dict[ShellType, ShellSession] = {}
        self.logger = logger
        self.default_shell: ShellType = ShellType.BASH
        self.current_shell: ShellType = ShellType.BASH
        
    async def get_or_create_session(self, shell_type: ShellType = None) -> ShellSession:
        """Get existing session or create new one if needed"""
        shell_type = shell_type or self.current_shell
        
        if shell_type not in self.sessions or not self.sessions[shell_type].established:
            self.logger.info(f"Creating new {shell_type} session")
            session = ShellSession(shell_type)
            await session.initialize()
            self.sessions[shell_type] = session
        elif not self.sessions[shell_type].established:
            # Reinitialize if session died
            self.logger.warning(f"Reinitializing {shell_type} session")
            await self.sessions[shell_type].initialize()
            
        return self.sessions[shell_type]
        
    def set_current_shell(self, shell_type: ShellType):
        """Set current active shell type"""
        self.current_shell = shell_type
        self.logger.info(f"Switched to {shell_type} shell")
        
    def list_sessions(self) -> Dict[ShellType, dict]:
        """List all shell sessions with their status"""
        result = {}
        for shell_type, session in self.sessions.items():
            result[shell_type] = {
                "session_id": session.session_id,
                "established": session.established,
                "history_count": len(session.history),
                "working_directory": str(session.working_directory)
            }
        return result
        
    async def cleanup_session(self, shell_type: ShellType):
        """Clean up specific shell session"""
        if shell_type in self.sessions:
            await self.sessions[shell_type].cleanup()
            del self.sessions[shell_type]
            
    async def cleanup_all(self):
        """Clean up all shell sessions"""
        for shell_type in list(self.sessions.keys()):
            await self.cleanup_session(shell_type)
            
    async def switch_shell(self, shell_type: ShellType, preserve_state: bool = True) -> bool:
        """Switch to different shell type"""
        try:
            # Get new session (will create if needed)
            session = await self.get_or_create_session(shell_type)
            
            if preserve_state and self.current_shell in self.sessions:
                # Preserve working directory
                current_session = self.sessions[self.current_shell]
                if current_session.working_directory:
                    session.set_working_directory(current_session.working_directory)
                    
            self.current_shell = shell_type
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to switch to {shell_type}: {e}")
            return False


class NativeToolBridge:
    """Directly load and invoke tools from a local tools directory."""

    def __init__(self, tools_dir: Optional[Union[str, Path]] = None, tool_prefix: str = "bb7_"):
        self._requested_tools_dir = str(tools_dir).strip() if tools_dir else None
        self._tool_prefix = tool_prefix or "bb7_"
        self._tools_dir: Optional[Path] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._tools: Dict[str, Callable[..., Any]] = {}
        self._tool_metadata: Dict[str, Dict[str, Any]] = {}
        self._tool_instances: List[Any] = []
        self._tool_providers: Dict[str, Any] = {}
        self._load_errors: List[Dict[str, str]] = []
        self._execution_stats: Dict[str, Dict[str, Any]] = {}

    @property
    def tools_dir(self) -> Optional[Path]:
        return self._tools_dir

    async def initialize(self) -> Dict[str, Any]:
        """Load tools from disk once and return bridge health metadata."""
        async with self._init_lock:
            if not self._initialized:
                self._tools_dir = self._resolve_tools_dir()
                await asyncio.to_thread(self._load_tools_from_directory)
                self._initialized = True
        return await self.health()

    async def close(self) -> None:
        """Close loaded tool instances that expose a close() method."""
        instances = list(self._tool_instances)
        self._tool_instances.clear()

        for instance in instances:
            close_method = getattr(instance, "close", None)
            if not callable(close_method):
                continue
            try:
                maybe_result = close_method()
                if inspect.isawaitable(maybe_result):
                    await maybe_result
            except Exception as exc:
                logger.warning(f"NativeToolBridge close() failed for {instance.__class__.__name__}: {exc}")

        with self._state_lock:
            self._tools.clear()
            self._tool_metadata.clear()
            self._tool_providers.clear()
            self._load_errors.clear()
            self._initialized = False

    async def health(self) -> Dict[str, Any]:
        """Return bridge health and indexing metadata."""
        with self._state_lock:
            return {
                "initialized": self._initialized,
                "tools_dir": str(self._tools_dir) if self._tools_dir else None,
                "tool_count": len(self._tools),
                "error_count": len(self._load_errors),
                "load_errors": list(self._load_errors),
            }

    async def list_tools(self, prefix: Optional[str] = None) -> Dict[str, Any]:
        """List indexed tools with optional prefix filtering."""
        await self.initialize()
        filter_prefix = (prefix or "").strip()

        with self._state_lock:
            names = sorted(self._tools.keys())
            if filter_prefix:
                names = [name for name in names if name.startswith(filter_prefix)]
            tools = [
                {
                    "name": name,
                    "module": self._tool_metadata.get(name, {}).get("module"),
                    "source": self._tool_metadata.get(name, {}).get("source"),
                    "is_async": self._tool_metadata.get(name, {}).get("is_async", False),
                }
                for name in names
            ]
            load_errors = list(self._load_errors)

        return {
            "tools_dir": str(self._tools_dir) if self._tools_dir else None,
            "count": len(tools),
            "prefix": filter_prefix or None,
            "tools": tools,
            "load_errors": load_errors,
        }

    async def get_tool_provider(self, tool_name: str) -> Optional[Any]:
        """Return the instantiated provider object that exported *tool_name*."""
        await self.initialize()
        with self._state_lock:
            return self._tool_providers.get(tool_name)

    async def invoke_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Invoke a native tool callable with resilient argument-shape fallback."""
        await self.initialize()

        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a non-empty string")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object (dict)")

        with self._state_lock:
            tool_callable = self._tools.get(tool_name)
            metadata = dict(self._tool_metadata.get(tool_name, {}))

        if not callable(tool_callable):
            raise KeyError(f"Native tool not found: {tool_name}")

        if inspect.iscoroutinefunction(tool_callable):
            return await self._invoke_async_callable(tool_callable, arguments)

        result = await asyncio.to_thread(self._invoke_sync_callable, tool_callable, arguments)
        if inspect.isawaitable(result):
            return await result

        # Enrich dict results with invocation metadata when possible.
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("_native_tool_metadata", metadata)

        return result

    async def invoke_with_tracking(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Wrap invoke_tool() with per-tool timing and success tracking."""
        start = time.monotonic()
        success = False
        try:
            result = await self.invoke_tool(tool_name, arguments)
            success = True
            return result
        except Exception as exc:
            raise
        finally:
            elapsed = time.monotonic() - start
            stats = self._execution_stats.setdefault(tool_name, {
                "count": 0, "success": 0, "total_time": 0.0, "last_used": None,
            })
            stats["count"] += 1
            if success:
                stats["success"] += 1
            stats["total_time"] += elapsed
            stats["last_used"] = time.time()

    def get_tool_health(self) -> Dict[str, Any]:
        """Return reliability metrics for all tools that have been invoked."""
        health: Dict[str, Any] = {}
        for name, stats in self._execution_stats.items():
            if stats["count"] > 0:
                health[name] = {
                    "reliability": stats["success"] / stats["count"],
                    "avg_time": stats["total_time"] / stats["count"],
                    "total_calls": stats["count"],
                    "last_used": stats["last_used"],
                }
        return health

    def get_tool_stats(self, tool_name: str) -> Dict[str, Any]:
        """Return execution stats for a single tool by name."""
        return dict(self._execution_stats.get(tool_name, {
            "count": 0, "success": 0, "total_time": 0.0, "last_used": None,
        }))

    def _resolve_tools_dir(self) -> Path:
        """Resolve the best tools directory candidate."""
        candidates: List[Path] = []
        env_dir = os.getenv("AI_SHELL_NATIVE_TOOLS_DIR", "").strip()
        if self._requested_tools_dir:
            candidates.append(Path(self._requested_tools_dir))
        if env_dir:
            candidates.append(Path(env_dir))

        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir / "tools")
        candidates.append(Path.cwd() / "tools")

        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:
                continue
            if resolved.exists() and resolved.is_dir():
                return resolved

        raise FileNotFoundError(
            "Could not resolve tools directory. Set AI_SHELL_NATIVE_TOOLS_DIR or AIShellSettings.native_tools_dir."
        )

    def _load_tools_from_directory(self) -> None:
        """Load tools from classes and module-level bb7_* / native functions. BB7 tools allow natural language shell input these are not however tools for the actual agent to use.

        Scans the top-level tools directory AND all immediate child
        subdirectories that are Python packages (contain __init__.py).
        This enables automatic discovery of both tools/bb7/ and tools/native/.
        """
        tools: Dict[str, Callable[..., Any]] = {}
        metadata: Dict[str, Dict[str, Any]] = {}
        instances: List[Any] = []
        providers: Dict[str, Any] = {}
        load_errors: List[Dict[str, str]] = []

        # Collect scan targets: the root dir + any package subdirectories
        scan_targets: List[Tuple[Path, Optional[str]]] = []

        # Ensure the tools root's *parent* is on sys.path so imports resolve
        root_parent = str(self._tools_dir.parent.resolve())
        if root_parent not in sys.path:
            sys.path.insert(0, root_parent)

        # Also ensure the tools root itself is on sys.path for sub-package imports
        root_str = str(self._tools_dir.resolve())
        if root_str not in sys.path:
            sys.path.insert(0, root_str)

        # 1. Top-level .py files in tools/
        is_root_package = (self._tools_dir / "__init__.py").exists()
        root_package_name = self._tools_dir.name if is_root_package else None
        top_level_modules = sorted(
            p for p in self._tools_dir.glob("*.py") if p.name != "__init__.py"
        )
        if top_level_modules:
            scan_targets.append((self._tools_dir, root_package_name))

        # 2. Subdirectory packages (e.g. tools/bb7/, tools/native/)
        for subdir in sorted(self._tools_dir.iterdir()):
            if not subdir.is_dir():
                continue
            if subdir.name.startswith((".", "__")):
                continue
            # Only load subdirectories that are proper Python packages
            if not (subdir / "__init__.py").exists():
                logger.debug(f"Skipping non-package subdirectory: {subdir.name}")
                continue
            scan_targets.append((subdir, subdir.name))

        if not scan_targets:
            raise RuntimeError(f"No tool modules found in {self._tools_dir} or its subdirectories")

        # Scan each target directory
        for target_dir, package_name in scan_targets:
            modules = sorted(
                p for p in target_dir.glob("*.py") if p.name != "__init__.py"
            )

            # Ensure each subdir's parent is on sys.path for package imports
            subdir_parent = str(target_dir.parent.resolve())
            if subdir_parent not in sys.path:
                sys.path.insert(0, subdir_parent)

            for module_path in modules:
                qualified_name = f"{package_name}.{module_path.stem}" if package_name else module_path.stem
                try:
                    module = self._import_tool_module(module_path, package_name)
                except Exception as exc:
                    load_errors.append({"module": qualified_name, "error": str(exc)})
                    continue

                try:
                    class_instances = self._collect_class_tools(module, qualified_name, tools, metadata, providers)
                    instances.extend(class_instances)
                    self._collect_module_functions(module, qualified_name, tools, metadata)
                except Exception as exc:
                    load_errors.append({"module": qualified_name, "error": str(exc)})

        logger.info(
            f"NativeToolBridge loaded {len(tools)} tools from "
            f"{len(scan_targets)} directories, {len(load_errors)} errors"
        )

        with self._state_lock:
            self._tools = tools
            self._tool_metadata = metadata
            self._tool_instances = instances
            self._tool_providers = providers
            self._load_errors = load_errors

    def _import_tool_module(self, module_path: Path, package_name: Optional[str]):
        """Import a tool module by package path when possible, else by file path."""
        if package_name:
            import_name = f"{package_name}.{module_path.stem}"
            module = importlib.import_module(import_name)
            return importlib.reload(module)

        dynamic_name = f"_native_tools_{module_path.stem}_{abs(hash(str(module_path)))}"
        spec = importlib.util.spec_from_file_location(dynamic_name, str(module_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create import spec for {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[dynamic_name] = module
        spec.loader.exec_module(module)
        return module

    def _collect_class_tools(
        self,
        module: Any,
        module_name: str,
        tools: Dict[str, Callable[..., Any]],
        metadata: Dict[str, Dict[str, Any]],
        providers: Dict[str, Any],
    ) -> List[Any]:
        """Collect tools exposed via class_instance.get_tools()."""
        instances: List[Any] = []

        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            get_tools = getattr(cls, "get_tools", None)
            if not callable(get_tools):
                continue

            try:
                instance = cls()
            except Exception as exc:
                logger.debug(f"Skipping {module_name}.{cls.__name__}: init failed ({exc})")
                continue

            try:
                exported = instance.get_tools()
            except Exception as exc:
                logger.debug(f"Skipping {module_name}.{cls.__name__}: get_tools failed ({exc})")
                continue

            if not isinstance(exported, dict):
                continue

            instances.append(instance)
            for tool_name, tool_entry in exported.items():
                tool_callable = tool_entry
                if isinstance(tool_entry, dict):
                    tool_callable = tool_entry.get("function")
                if not callable(tool_callable):
                    continue
                if tool_name in tools:
                    logger.warning(
                        f"Duplicate native tool name '{tool_name}' ignored from {module_name}.{cls.__name__}"
                    )
                    continue
                tools[tool_name] = tool_callable
                providers[tool_name] = instance
                entry_metadata = {
                    "module": module_name,
                    "source": f"class:{cls.__name__}",
                    "provider_class": cls.__name__,
                    "is_async": inspect.iscoroutinefunction(tool_callable),
                }
                if isinstance(tool_entry, dict):
                    if "description" in tool_entry:
                        entry_metadata["description"] = str(tool_entry.get("description"))
                    if "parameters" in tool_entry and isinstance(tool_entry.get("parameters"), list):
                        entry_metadata["parameters"] = tool_entry.get("parameters")
                    if "inputSchema" in tool_entry and isinstance(tool_entry.get("inputSchema"), dict):
                        entry_metadata["inputSchema"] = tool_entry.get("inputSchema")
                metadata[tool_name] = entry_metadata

        return instances

    def _collect_module_functions(
        self,
        module: Any,
        module_name: str,
        tools: Dict[str, Callable[..., Any]],
        metadata: Dict[str, Dict[str, Any]],
    ) -> None:
        """Collect module-level bb7_* functions that are not already registered."""
        for func_name, func in inspect.getmembers(module, inspect.isfunction):
            if func.__module__ != module.__name__:
                continue
            if not func_name.startswith(self._tool_prefix):
                continue
            if func_name in tools:
                continue
            tools[func_name] = func
            metadata[func_name] = {
                "module": module_name,
                "source": "module_function",
                "is_async": inspect.iscoroutinefunction(func),
            }

    async def _invoke_async_callable(self, fn: Callable[..., Any], arguments: Dict[str, Any]) -> Any:
        """Invoke async callables with kwargs/dict/no-arg fallbacks."""
        errors: List[Tuple[str, str]] = []

        try:
            return await fn(**arguments)
        except TypeError as exc:
            errors.append(("kwargs", str(exc)))

        try:
            return await fn(arguments)
        except TypeError as exc:
            errors.append(("dict", str(exc)))

        try:
            return await fn()
        except TypeError as exc:
            errors.append(("no_args", str(exc)))

        raise TypeError(
            f"Unable to invoke async tool {getattr(fn, '__name__', '<unknown>')} with supported call shapes: {errors}"
        )

    def _invoke_sync_callable(self, fn: Callable[..., Any], arguments: Dict[str, Any]) -> Any:
        """Invoke sync callables with kwargs/dict/no-arg fallbacks."""
        errors: List[Tuple[str, str]] = []

        try:
            return fn(**arguments)
        except TypeError as exc:
            errors.append(("kwargs", str(exc)))

        try:
            return fn(arguments)
        except TypeError as exc:
            errors.append(("dict", str(exc)))

        try:
            return fn()
        except TypeError as exc:
            errors.append(("no_args", str(exc)))

        raise TypeError(
            f"Unable to invoke tool {getattr(fn, '__name__', '<unknown>')} with supported call shapes: {errors}"
        )


# ============================================================================
# UNIFIED AI SHELL INTERFACE
# ============================================================================

class AdvancedAIShell:
    """
    Unified AI Shell supporting VM operations, container orchestration, and multi-agent collaboration.
    Provides intelligent command routing and execution context management.
    """
    
    def __init__(self, settings: AIShellSettings = None, agent_id: UUID = None, artifact_manager: ArtifactManager = None):
        self.settings = settings or AIShellSettings()
        self.agent_id = agent_id or uuid4()
        
        # Core components
        self.vm_manager = AdvancedVMManager(self.settings, self.agent_id)
        self.container_orchestrator = ContainerOrchestrator(self.vm_manager)
        self.collaboration_manager = CollaborationManager(self.vm_manager)
        self.logger = logger
        self.shell_manager = ShellManager(logger)  # NEW: Persistent shell session manager
        self.native_tool_bridge = NativeToolBridge(
            tools_dir=self.settings.native_tools_dir,
            tool_prefix=self.settings.native_tool_prefix,
        )
        
        # Tie to external unlimited execution environment
        self.artifact_manager = artifact_manager  # may be None if not provided
        
        # ── Integrated Subsystems (initialized lazily in initialize()) ──
        self.memory_manager: Optional[Any] = None
        self.cache: Optional[Any] = None
        self.prompt_system: Optional[Any] = None
        self.file_manager: Optional[Any] = None
        self.file_processor: Optional[Any] = None

        # Host-native is the default execution context.  VM and container
        # paths are only used when explicitly requested.  This ensures the shell
        # operates on the host OS by default without requiring a running VM.
        self.default_execution_context: ExecutionContext = ExecutionContext.VM_NATIVE

        # State tracking
        self.command_history: List[ExecutionResult] = []
        self.active_contexts: Dict[str, ExecutionContext] = {}
        self.initialized = False
        self.native_tools_ready = False
        self.current_shell_type: ShellType = ShellType.BASH  # Track current shell type
        self.projects_dir = Path("data/project_shell")
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.project_catalog_file = self.projects_dir / "project_catalog.json"
        self.project_catalog: Dict[str, Dict[str, Any]] = {}
        self.active_project_id: Optional[str] = None
        self.git_manager = GitHubIntegrationManager(
            clone_dir=str(self.projects_dir / "repos"),
            config=RepoConfiguration(),
            enable_security_scanning=True,
            enable_artifact_integration=True
        )
    
    async def initialize(self) -> bool:
        """Initialize the advanced AI shell and all integrated subsystems."""
        try:
            success = await self.vm_manager.initialize()
            if success:
                self.project_catalog = await self._load_project_catalog()
                if self.settings.enable_native_tools:
                    try:
                        await self.initialize_native_tools()
                    except Exception as native_exc:
                        logger.warning(f"Native tool bridge initialization skipped: {native_exc}")

                # ── Phase 2: Memory Subsystem ──
                await self._initialize_memory_subsystem()

                # ── Phase 2b: Cache Subsystem ──
                await self._initialize_cache_subsystem()

                # ── Phase 3: Autonomous Prompt System ──
                await self._initialize_prompt_system()

                # ── Phase 4: Direct OS File Processing ──
                await self._initialize_file_processing()

                self.initialized = True
                logger.info(f"Advanced AI Shell initialized for agent {self.agent_id}")
            return success
        except Exception as e:
            logger.error(f"Failed to initialize Advanced AI Shell: {e}")
            return False

    async def initialize_native_tools(self) -> Dict[str, Any]:
        """Initialize standalone native tools without requiring MCP server transport."""
        if not self.settings.enable_native_tools:
            self.native_tools_ready = False
            return {
                "enabled": False,
                "reason": "Native tools are disabled by configuration",
            }

        health = await self.native_tool_bridge.initialize()
        self.native_tools_ready = bool(health.get("initialized"))
        return health

    # ────────────────────────────────────────────────────────────────────────
    # SUBSYSTEM INITIALIZERS
    # ────────────────────────────────────────────────────────────────────────

    async def _initialize_memory_subsystem(self) -> None:
        """Initialize the persistent memory manager (graceful)."""
        if not (MEMORY_AVAILABLE and self.settings.enable_memory):
            logger.info("Memory subsystem disabled or unavailable")
            return
        try:
            config = MemoryConfiguration(
                vector_db_path=self.settings.memory_db_path + "/vectors",
                metadata_db_path=self.settings.memory_db_path + "/metadata.db",
            )
            self.memory_manager = MemoryManager(config)
            await self.memory_manager.initialize()
            logger.info("Memory subsystem initialized")
        except Exception as exc:
            logger.warning(f"Memory subsystem initialization failed (non-fatal): {exc}")
            self.memory_manager = None

    async def _initialize_cache_subsystem(self) -> None:
        """Initialize the SomnusCache engine (graceful)."""
        if not (CACHE_AVAILABLE and self.settings.enable_cache):
            logger.info("Cache subsystem disabled or unavailable")
            return
        try:
            cache_config = {
                'max_entries': self.settings.cache_max_entries,
                'max_memory_mb': self.settings.cache_max_memory_mb,
                'cache_dir': self.settings.cache_dir,
                'persistence_enabled': True,
            }
            self.cache = SomnusCache(
                config=cache_config,
                memory_manager=self.memory_manager,  # may be None — SomnusCache handles it
            )
            logger.info("Cache subsystem initialized")
        except Exception as exc:
            logger.warning(f"Cache subsystem initialization failed (non-fatal): {exc}")
            self.cache = None

    async def _initialize_prompt_system(self) -> None:
        """Initialize the Autonomous Prompt System (graceful)."""
        if not (PROMPT_SYSTEM_AVAILABLE and self.settings.enable_prompt_system):
            logger.info("Prompt system disabled or unavailable")
            return
        if not self.cache:
            logger.info("Prompt system requires cache — skipped")
            return
        try:
            self.prompt_system = AutonomousPromptSystem(
                cache=self.cache,
                memory_manager=self.memory_manager,
                user_id=str(self.agent_id),
            )
            await self.prompt_system.initialize_prompt_layers()
            logger.info("Autonomous Prompt System initialized")
        except Exception as exc:
            logger.warning(f"Prompt system initialization failed (non-fatal): {exc}")
            self.prompt_system = None

    async def _initialize_file_processing(self) -> None:
        """Initialize direct OS file processing (graceful)."""
        if not (FILE_PROCESSING_AVAILABLE and self.settings.enable_file_processing):
            logger.info("File processing disabled or unavailable")
            return
        try:
            self.file_manager = FileUploadManager(
                upload_dir=self.settings.file_upload_dir,
            )
            self.file_processor = IntelligentFileProcessor(
                base_file_manager=self.file_manager,
                cache=self.cache,  # may be None — IntelligentFileProcessor handles it
            )
            await self.file_processor.start()
            logger.info("File processing subsystem initialized")
        except Exception as exc:
            logger.warning(f"File processing initialization failed (non-fatal): {exc}")
            self.file_manager = None
            self.file_processor = None

    # ────────────────────────────────────────────────────────────────────────
    # PUBLIC SUBSYSTEM APIs
    # ────────────────────────────────────────────────────────────────────────

    async def generate_prompt(
        self,
        user_input: str,
        session_id: str,
        task_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Generate a context-enriched prompt via the Autonomous Prompt System.

        Returns the full assembled prompt string, or None if the prompt
        system is unavailable.
        """
        if not self.prompt_system:
            return None
        try:
            return await self.prompt_system.generate_current_prompt(
                user_input=user_input,
                session_id=session_id,
                task_context=task_context,
            )
        except Exception as exc:
            logger.error(f"Prompt generation failed: {exc}")
            return None

    async def process_file(
        self,
        file_path: str,
        user_id: str = "system",
        session_id: str = "shell",
        priority: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """Process a file directly from the OS filesystem.

        Reads the file from *file_path*, extracts content, generates
        embeddings (if available), and returns metadata + extracted text.
        Bypasses containers entirely — direct OS access.
        """
        if not self.file_processor:
            logger.warning("File processing unavailable")
            return None
        try:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                return {"error": f"File not found: {file_path}"}

            file_data = file_path_obj.read_bytes()
            task_priority = priority or (ProcessingPriority.MEDIUM if FILE_PROCESSING_AVAILABLE else None)
            task_id = await self.file_processor.queue_file(
                file_data=file_data,
                filename=file_path_obj.name,
                user_id=user_id,
                session_id=session_id,
                priority=task_priority,
                source_context="shell_direct",
            )
            return {"task_id": str(task_id), "filename": file_path_obj.name, "status": "queued"}
        except Exception as exc:
            logger.error(f"File processing failed for {file_path}: {exc}")
            return {"error": str(exc)}

    async def process_file_batch(
        self,
        file_paths: List[str],
        user_id: str = "system",
        session_id: str = "shell",
    ) -> List[Dict[str, Any]]:
        """Process multiple files from the OS filesystem."""
        results = []
        for fp in file_paths:
            result = await self.process_file(fp, user_id=user_id, session_id=session_id)
            results.append(result or {"error": f"None returned for {fp}"})
        return results

    async def store_memory(
        self,
        content: str,
        memory_type: str = "core_fact",
        user_id: str = "system",
        importance: str = "medium",
        tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Store a memory entry in the persistent memory system."""
        if not self.memory_manager:
            return None
        try:
            mem_type = MemoryType(memory_type) if MEMORY_AVAILABLE else memory_type
            mem_importance = MemoryImportance(importance) if MEMORY_AVAILABLE else importance
            memory_id = await self.memory_manager.store_memory(
                content=content,
                user_id=user_id,
                memory_type=mem_type,
                importance=mem_importance,
                tags=tags or [],
            )
            return str(memory_id)
        except Exception as exc:
            logger.error(f"Memory storage failed: {exc}")
            return None

    async def recall_memories(
        self,
        query: str,
        user_id: str = "system",
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant memories by semantic similarity."""
        if not self.memory_manager:
            return []
        try:
            memories = await self.memory_manager.retrieve_memories(
                query=query,
                user_id=user_id,
                max_results=max_results,
            )
            return memories
        except Exception as exc:
            logger.error(f"Memory recall failed: {exc}")
            return []

    def get_subsystem_status(self) -> Dict[str, Any]:
        """Return health/availability of all integrated subsystems."""
        return {
            "memory": {
                "available": MEMORY_AVAILABLE,
                "initialized": self.memory_manager is not None,
            },
            "cache": {
                "available": CACHE_AVAILABLE,
                "initialized": self.cache is not None,
            },
            "prompt_system": {
                "available": PROMPT_SYSTEM_AVAILABLE,
                "initialized": self.prompt_system is not None,
            },
            "file_processing": {
                "available": FILE_PROCESSING_AVAILABLE,
                "initialized": self.file_processor is not None,
            },
            "native_tools": {
                "available": True,
                "initialized": self.native_tools_ready,
            },
        }

    async def list_native_tools(self, prefix: Optional[str] = None) -> Dict[str, Any]:
        """List directly loaded native tools."""
        if not self.settings.enable_native_tools:
            raise RuntimeError("Native tools are disabled")
        return await self.native_tool_bridge.list_tools(prefix=prefix)

    async def call_native_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Invoke a direct native tool callable."""
        if not self.settings.enable_native_tools:
            raise RuntimeError("Native tools are disabled")
        return await self.native_tool_bridge.invoke_tool(tool_name, arguments or {})

    async def get_native_tool_provider(self, tool_name: str) -> Optional[Any]:
        """Return the provider instance that exported *tool_name*."""
        if not self.settings.enable_native_tools:
            raise RuntimeError("Native tools are disabled")
        return await self.native_tool_bridge.get_tool_provider(tool_name)
    
    async def execute_command(
        self,
        command: str,
        context: Optional[ExecutionContext] = None,
        container_spec: Optional[ContainerSpec] = None,
        artifact_files: Optional[Dict[str, str]] = None,
        collaboration_agents: Optional[List[UUID]] = None,
        use_persistent_shell: bool = True,
        **kwargs
    ) -> ExecutionResult:
        """Unified command execution with intelligent context routing.

        When *context* is None the shell's ``default_execution_context``
        (``VM_NATIVE`` — host-native) is used.

        Parameters
        ----------
        use_persistent_shell : bool
            When True (default), host-native execution uses the persistent
            shell session for state preservation.  Set to False for isolated
            operations like package installation that should not pollute the
            interactive shell state.
        """
        if context is None:
            context = self.default_execution_context

        native_tool_command = self._is_native_tool_command(command)

        if not self.initialized and not native_tool_command:
            raise RuntimeError("AI Shell not initialized")
        
        start_time = time.time()
        command_type = self._classify_command(command)

        if context != ExecutionContext.PROJECT and command_type == CommandType.PROJECT:
            context = ExecutionContext.PROJECT

        try:
            # Route standalone native tool commands before VM/container execution.
            tool_result = await self._try_execute_native_tool_command(command)
            if tool_result is not None:
                result = tool_result
                context = ExecutionContext.VM_NATIVE
                command_type = CommandType.SYSTEM
            # Route command based on context and type
            elif context == ExecutionContext.VM_NATIVE:
                result = await self._execute_vm_native(
                    command, command_type,
                    use_persistent_shell=use_persistent_shell,
                )
                
            elif context == ExecutionContext.CONTAINER_OVERLAY:
                result = await self._execute_container_overlay(
                    command, command_type, container_spec, artifact_files
                )
                
            elif context == ExecutionContext.MULTI_AGENT:
                result = await self._execute_multi_agent(
                    command, command_type, collaboration_agents
                )
                
            elif context == ExecutionContext.HYBRID:
                result = await self._execute_hybrid(
                    command, command_type, container_spec, artifact_files
                )
                
            elif context == ExecutionContext.PROJECT:
                result = await self._execute_project_command(command)
                
            else:
                raise ValueError(f"Unknown execution context: {context}")
            
            execution_time = time.time() - start_time
            
            # Create comprehensive execution result
            execution_result = ExecutionResult(
                command=command,
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                return_code=result.get("return_code", 0),
                execution_time=execution_time,
                context=context,
                command_type=command_type,
                container_id=result.get("container_id"),
                collaborator_responses=result.get("collaborator_responses", []),
                metadata=result.get("metadata", {})
            )
            
            # Store in history
            self.command_history.append(execution_result)
            
            # Log execution
            await self._log_execution(execution_result)
            
            return execution_result
            
        except Exception as e:
            execution_time = time.time() - start_time
            error_result = ExecutionResult(
                command=command,
                stdout="",
                stderr=str(e),
                return_code=-1,
                execution_time=execution_time,
                context=context,
                command_type=command_type
            )
            
            self.command_history.append(error_result)
            logger.error(f"Command execution failed: {e}")
            return error_result
    
    def _classify_command(self, command: str) -> CommandType:
        """Classify command type for intelligent routing.

        Structured pipeline (evaluated top-down, first match wins):
          1. Native tool commands (bb7_*, tool list/call) → SYSTEM
          2. Project lifecycle (``project create/list/...``) → PROJECT
          3. Container / Docker / kubectl → ARTIFACT (disposable sandbox)
          4. Collaboration verbs → COLLABORATION
          5. Research / analysis / investigation intents → RESEARCH
          6. Development tool commands (git, npm, pip, cargo, ...) → DEVELOPMENT
          7. Default → SYSTEM (OS-level shell command)

        Design rationale (from Daeron's architectural notes):
          - Docker = disposable artifact sandbox, per-call config, single-active
          - bb7 tools = native JSON I/O, routed through NativeToolBridge
          - src/ tools + modifying_prompts are always-on cognition layer
          - The router must ALWAYS direct through modifying_prompts (context window)
          - bb7 memory = per-project scoped, src memory = always-on core
        """
        command_lower = command.lower().strip()

        # 1. Native tool invocations (JSON I/O through NativeToolBridge)
        if self._is_native_tool_command(command):
            return CommandType.SYSTEM

        # 2. Project lifecycle commands
        if command_lower.startswith('project '):
            return CommandType.PROJECT

        # 3. Container / artifact commands (disposable sandbox semantics)
        #    Per Daeron's notes: containers are not persistent, disposed after use,
        #    only one can be active at a time — a temporary sandboxed process.
        if any(command_lower.startswith(p) for p in ('docker ', 'kubectl ', 'podman ')):
            return CommandType.ARTIFACT
        if any(kw in command_lower for kw in ('container ', 'artifact ')):
            return CommandType.ARTIFACT

        # 4. Multi-agent collaboration verbs
        if any(kw in command_lower for kw in (
            'collaborate', 'delegate', 'synthesize', 'coordinate',
            'multi-agent', 'broadcast',
        )):
            return CommandType.COLLABORATION

        # 5. Research and analysis intents
        if any(kw in command_lower for kw in (
            'research', 'analyze', 'investigate', 'audit',
            'deep research', 'survey', 'benchmark',
        )):
            return CommandType.RESEARCH

        # 6. Development tool commands (exact prefix match)
        if any(command_lower.startswith(p) for p in (
            'git ', 'npm ', 'pip ', 'cargo ', 'make ', 'cmake ',
            'python ', 'python3 ', 'node ', 'go ', 'rustc ',
            'gcc ', 'g++ ', 'javac ', 'dotnet ', 'mvn ', 'gradle ',
            'code ', 'develop ', 'build ', 'compile ',
        )):
            return CommandType.DEVELOPMENT

        # 7. Default: OS-level shell command
        return CommandType.SYSTEM

    def _is_native_tool_command(self, command: str) -> bool:
        """Detect direct-native-tool command patterns."""
        if not isinstance(command, str):
            return False
        stripped = command.strip()
        if not stripped:
            return False
        lower = stripped.lower()
        return (
            lower.startswith("tool list")
            or lower.startswith("tools list")
            or lower.startswith("tool call ")
            or lower.startswith("tools call ")
            or stripped.startswith(self.settings.native_tool_prefix)
        )

    def _parse_native_tool_args(self, args_text: str) -> Dict[str, Any]:
        """Parse optional JSON arguments for a tool invocation."""
        text = (args_text or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Tool arguments must be valid JSON object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be a JSON object")
        return parsed

    @staticmethod
    def _stringify_payload(payload: Any) -> str:
        """Serialize payloads for terminal-friendly stdout."""
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, indent=2, default=str)
        if payload is None:
            return ""
        return str(payload)

    async def _try_execute_native_tool_command(self, command: str) -> Optional[Dict[str, Any]]:
        """Execute direct tool commands and return shell-shaped response payloads."""
        if not self.settings.enable_native_tools:
            return None

        stripped = command.strip()
        if not stripped:
            return None

        lower = stripped.lower()

        if lower.startswith("tool list") or lower.startswith("tools list"):
            parts = stripped.split(None, 2)
            prefix = parts[2].strip() if len(parts) == 3 else None
            listing = await self.list_native_tools(prefix=prefix)
            return {
                "stdout": self._stringify_payload(listing),
                "stderr": "",
                "return_code": 0,
                "metadata": {
                    "execution_location": "native_tool_bridge",
                    "operation": "list",
                    "prefix": prefix,
                },
            }

        if lower.startswith("tool call ") or lower.startswith("tools call "):
            parts = stripped.split(None, 3)
            if len(parts) < 3:
                raise ValueError("Usage: tool call <tool_name> [json_args]")
            tool_name = parts[2].strip()
            args_text = parts[3] if len(parts) >= 4 else ""
            arguments = self._parse_native_tool_args(args_text)
            output = await self.call_native_tool(tool_name, arguments)
            return {
                "stdout": self._stringify_payload({"tool": tool_name, "result": output}),
                "stderr": "",
                "return_code": 0,
                "metadata": {
                    "execution_location": "native_tool_bridge",
                    "operation": "call",
                    "tool_name": tool_name,
                },
            }

        if stripped.startswith(self.settings.native_tool_prefix):
            tool_name, _, args_text = stripped.partition(" ")
            arguments = self._parse_native_tool_args(args_text)
            output = await self.call_native_tool(tool_name, arguments)
            return {
                "stdout": self._stringify_payload({"tool": tool_name, "result": output}),
                "stderr": "",
                "return_code": 0,
                "metadata": {
                    "execution_location": "native_tool_bridge",
                    "operation": "direct_tool_name",
                    "tool_name": tool_name,
                },
            }

        return None
    
    async def _execute_vm_native(self, command: str, command_type: CommandType, use_persistent_shell: bool = True) -> Dict[str, Any]:
        """Execute command directly in AI's VM using persistent shell or legacy method"""
        
        if use_persistent_shell:
            try:
                # Use persistent shell session for better state management
                session = await self.shell_manager.get_or_create_session(self.current_shell_type)
                result = await session.execute_command(command)
                
                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "return_code": result.return_code,
                    "metadata": {
                        "execution_location": "ai_vm_persistent_shell",
                        "shell_session_id": result.metadata.get("session_id"),
                        "shell_type": result.metadata.get("shell_type"),
                        "execution_time": result.execution_time
                    }
                }
            except Exception as e:
                self.logger.warning(f"Persistent shell execution failed, falling back to VM manager: {e}")
                # Fall through to legacy method
        
        # Legacy method as fallback
        stdout, stderr, return_code = await self.vm_manager.execute_in_vm(command)
        
        return {
            "stdout": stdout,
            "stderr": stderr,
            "return_code": return_code,
            "metadata": {"execution_location": "ai_vm"}
        }
    
    async def _execute_container_overlay(
        self, 
        command: str, 
        command_type: CommandType,
        container_spec: Optional[ContainerSpec] = None,
        artifact_files: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Execute command in artifact container overlay
        
        If an ArtifactManager is available, delegate container creation and execution to it
        to ensure a single source of truth for unlimited execution environments.
        """
        # Prefer using the external ArtifactManager for consistency across subsystems.
        if self.artifact_manager is not None:
            try:
                exec_result = await self.artifact_manager.run_artifact(
                    command=command,
                    files=artifact_files or {},
                    spec=container_spec or ContainerSpec()
                )

                # Validate the response shape — fail loud if the API
                # returns an unexpected type.
                if not isinstance(exec_result, dict):
                    raise TypeError(
                        f"ArtifactManager.run_artifact() returned {type(exec_result).__name__}, "
                        f"expected dict with stdout/stderr/return_code keys."
                    )

                return {
                    "stdout": str(exec_result.get("stdout", "")),
                    "stderr": str(exec_result.get("stderr", "")),
                    "return_code": int(exec_result.get("return_code", 0)),
                    "container_id": exec_result.get("container_id"),
                    "metadata": {
                        "execution_location": "artifact_manager",
                        "artifact_id": exec_result.get("artifact_id"),
                    },
                }
            except Exception as e:
                logger.error(f"ArtifactManager execution failed, falling back to Docker: {e}")
                # Fallback to internal orchestrator

        # Guard: Docker must be available for internal orchestration.
        if not DOCKER_AVAILABLE:
            raise RuntimeError(
                "Container overlay execution requires Docker. "
                "Install the 'docker' Python package and ensure the Docker daemon is running, "
                "or provide an ArtifactManager instance."
            )
        
        # Fallback to internal Docker orchestration (existing logic)
        if not container_spec:
            container_spec = ContainerSpec()
        
        artifact_id = str(uuid4())
        container_name = await self.container_orchestrator.create_artifact_container(
            artifact_id, container_spec, artifact_files
        )
        
        try:
            stdout, stderr, return_code = await self.container_orchestrator.execute_in_container(
                container_name, command
            )
            
            return {
                "stdout": stdout,
                "stderr": stderr,
                "return_code": return_code,
                "container_id": container_name,
                "metadata": {
                    "execution_location": "artifact_container",
                    "artifact_id": artifact_id
                }
            }
        finally:
            await self.container_orchestrator.cleanup_container(container_name)

    async def _execute_multi_agent(
        self, 
        command: str, 
        command_type: CommandType,
        collaboration_agents: Optional[List[UUID]] = None
    ) -> Dict[str, Any]:
        """Execute command through multi-agent collaboration"""
        
        if not collaboration_agents:
            raise ValueError("Collaboration agents required for multi-agent execution")
        
        # Initiate collaboration session
        session_id = await self.collaboration_manager.initiate_collaboration(
            command, collaboration_agents
        )
        
        # Collect responses from all agents
        collaboration_result = await self.collaboration_manager.collect_collaboration_responses(
            session_id
        )
        
        return {
            "stdout": collaboration_result["synthesized_response"],
            "stderr": "",
            "return_code": 0,
            "collaborator_responses": collaboration_result["individual_responses"],
            "metadata": {
                "execution_location": "multi_agent_collaboration",
                "session_id": str(session_id),
                "collaboration_time": collaboration_result["total_time"]
            }
        }
    
    async def _execute_hybrid(
        self, 
        command: str, 
        command_type: CommandType,
        container_spec: Optional[ContainerSpec] = None,
        artifact_files: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Execute command with AI VM orchestrating container operations"""
        
        # First, execute preparation steps in VM
        prep_command = f"echo 'Preparing container orchestration for: {command}'"
        vm_stdout, vm_stderr, vm_return_code = await self.vm_manager.execute_in_vm(prep_command)
        
        # Then execute main command using the (potentially) unified artifact path
        container_result = await self._execute_container_overlay(
            command, command_type, container_spec, artifact_files
        )
        
        # Combine results
        combined_stdout = f"VM Preparation: {vm_stdout}\n\nContainer Execution:\n{container_result['stdout']}"
        
        return {
            "stdout": combined_stdout,
            "stderr": container_result["stderr"],
            "return_code": container_result["return_code"],
            "container_id": container_result.get("container_id"),
            "metadata": {
                "execution_location": "hybrid_vm_container",
                "vm_preparation": vm_stdout,
                "container_result": container_result["metadata"]
            }
        }
    
    async def _log_execution(self, result: ExecutionResult):
        """Log execution result for monitoring and debugging"""
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_id": str(self.agent_id),
                "command": result.command,
                "context": result.context.value,
                "command_type": result.command_type.value,
                "return_code": result.return_code,
                "execution_time": result.execution_time,
                "success": result.return_code == 0
            }
            
            async with aiofiles.open(self.settings.execution_log_path, 'a') as f:
                await f.write(json.dumps(log_entry) + '\n')
                
        except Exception as e:
            logger.error(f"Failed to log execution: {e}")

    async def _load_project_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Load persisted project catalog for the shell"""
        if not self.project_catalog_file.exists():
            return {}

        try:
            raw = await asyncio.to_thread(self.project_catalog_file.read_text, encoding="utf-8")
            entries = json.loads(raw)
            catalog = {}
            if isinstance(entries, list):
                for entry in entries:
                    pid = entry.get("project_id")
                    if pid:
                        catalog[pid] = entry
            return catalog
        except Exception as e:
            logger.warning(f"Failed to load project catalog: {e}")
            return {}

    async def _persist_project_catalog(self) -> None:
        """Persist the current project catalog to disk"""
        try:
            data = list(self.project_catalog.values())
            await asyncio.to_thread(
                self.project_catalog_file.write_text,
                json.dumps(data, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Failed to persist project catalog: {e}")

    def _get_active_project_record(self) -> Optional[Dict[str, Any]]:
        if not self.active_project_id:
            return None
        return self.project_catalog.get(self.active_project_id)

    def _describe_project(self, record: Dict[str, Any]) -> str:
        repo_count = len(record.get("repositories", []))
        return (
            f"{record['name']} ({record['project_id']}) - "
            f"{record['status']} - VM {record['vm_id']} - "
            f"{repo_count} repo(s)"
        )

    async def _execute_project_command(self, command: str) -> Dict[str, Any]:
        """Route and execute simple project lifecycle operations"""
        tokens = shlex.split(command)
        if len(tokens) < 2:
            return {
                "stdout": "Project command requires a subcommand (create|list|select|status|repo ingest|help)",
                "stderr": "",
                "return_code": 1,
                "metadata": {}
            }

        action = tokens[1].lower()

        try:
            if action == "create":
                if len(tokens) < 3:
                    raise ValueError("Project create requires a name")

                project_name = tokens[2]
                description = " ".join(tokens[3:]).strip()
                project_id = uuid4()
                vm_id = uuid4()
                record = ShellProjectRecord(
                    project_id=str(project_id),
                    name=project_name,
                    description=description,
                    vm_id=str(vm_id),
                    status=ProjectStatus.ACTIVE.value,
                    created_at=datetime.now(timezone.utc).isoformat()
                )
                self.project_catalog[record.project_id] = record.__dict__
                self.active_project_id = record.project_id
                await self._persist_project_catalog()

                return {
                    "stdout": f"Project '{project_name}' created and selected (id={record.project_id})",
                    "stderr": "",
                    "return_code": 0,
                    "metadata": {"project_id": record.project_id}
                }

            elif action == "list":
                if not self.project_catalog:
                    return {
                        "stdout": "No projects registered yet",
                        "stderr": "",
                        "return_code": 0,
                        "metadata": {}
                    }

                lines = [
                    self._describe_project(record)
                    for record in self.project_catalog.values()
                ]
                return {
                    "stdout": "\n".join(lines),
                    "stderr": "",
                    "return_code": 0,
                    "metadata": {"total_projects": len(lines)}
                }

            elif action == "select":
                if len(tokens) < 3:
                    raise ValueError("Select requires a project_id")

                candidate = tokens[2]
                if candidate not in self.project_catalog:
                    raise KeyError(f"Unknown project_id: {candidate}")

                self.active_project_id = candidate
                return {
                    "stdout": f"Active project set to {self.project_catalog[candidate]['name']}",
                    "stderr": "",
                    "return_code": 0,
                    "metadata": {"project_id": candidate}
                }

            elif action == "status":
                record = self._get_active_project_record()
                if not record:
                    return {
                        "stdout": "No active project selected",
                        "stderr": "",
                        "return_code": 0,
                        "metadata": {}
                    }

                return {
                    "stdout": self._describe_project(record),
                    "stderr": "",
                    "return_code": 0,
                    "metadata": {"project_id": record["project_id"]}
                }

            elif action == "repo" and len(tokens) >= 3 and tokens[2].lower() == "ingest":
                record = self._get_active_project_record()
                if not record:
                    raise RuntimeError("Select a project before ingesting repositories")

                if len(tokens) < 4:
                    raise ValueError("Repository URL is required")

                repo_url = tokens[3]
                branch = None
                if "--branch" in tokens:
                    idx = tokens.index("--branch")
                    if idx + 1 < len(tokens):
                        branch = tokens[idx + 1]

                metadata = await self.git_manager.clone_repository(
                    repo_url=repo_url,
                    user_id=str(self.agent_id),
                    session_id="advanced_shell",
                    branch=branch
                )

                repo_entry = {
                    "repo_id": str(metadata.repo_id),
                    "url": metadata.url,
                    "name": metadata.name,
                    "status": metadata.status,
                    "progress": metadata.progress_percentage,
                    "cloned_at": metadata.processing_start.isoformat() if metadata.processing_start else datetime.now(timezone.utc).isoformat()
                }
                record.setdefault("repositories", []).append(repo_entry)
                await self._persist_project_catalog()

                return {
                    "stdout": f"Repository '{metadata.name}' ingested into project {record['name']}",
                    "stderr": "",
                    "return_code": 0,
                    "metadata": {"project_id": record["project_id"], "repo_id": repo_entry["repo_id"]}
                }

            elif action == "help":
                help_text = (
                    "Project commands: create <name> [description], list, select <project_id>, "
                    "status, repo ingest <url> [--branch name]"
                )
                return {"stdout": help_text, "stderr": "", "return_code": 0, "metadata": {}}

            else:
                raise ValueError(f"Unknown project subcommand: {action}")

        except Exception as exc:
            logger.error(f"Project command error: {exc}")
            return {
                "stdout": "",
                "stderr": str(exc),
                "return_code": 1,
                "metadata": {}
            }
    
    async def switch_shell(self, shell_type: ShellType, preserve_state: bool = True) -> bool:
        """Switch to a different shell type for persistent terminal sessions
        
        Args:
            shell_type: The target shell type (bash, pwsh, azure, etc.)
            preserve_state: Whether to preserve working directory and environment
            
        Returns:
            bool: True if switch successful, False otherwise
        """
        try:
            success = await self.shell_manager.switch_shell(shell_type, preserve_state)
            if success:
                self.current_shell_type = shell_type
                logger.info(f"Advanced AI Shell switched to {shell_type}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to switch shell to {shell_type}: {e}")
            return False
    
    async def get_active_shell_session(self) -> ShellSession:
        """Get the currently active persistent shell session"""
        return await self.shell_manager.get_or_create_session(self.current_shell_type)
    
    def list_shell_sessions(self) -> Dict[ShellType, dict]:
        """List all active shell sessions with their status"""
        return self.shell_manager.list_sessions()
    
    async def close_shell_session(self, shell_type: ShellType = None):
        """Close a specific shell session or the current one if shell_type is None"""
        target_type = shell_type or self.current_shell_type
        await self.shell_manager.cleanup_session(target_type)
    
    async def install_shell(self, shell_type: ShellType) -> bool:
        """Install a shell type if not already available, using system package managers
        
        Args:
            shell_type: The shell type to install
            
        Returns:
            bool: True if installation succeeded or shell already exists
        """
        # Check if shell is already available
        if await self._is_shell_available(shell_type):
            logger.info(f"{shell_type.value} shell already available")
            return True
            
        # Installation commands based on OS
        install_commands = await self._get_shell_install_commands(shell_type)
        if not install_commands:
            logger.error(f"No installation commands available for {shell_type}")
            return False
            
        # Execute installation
        for cmd in install_commands:
            try:
                result = await self.execute_command(
                    cmd, 
                    context=ExecutionContext.VM_NATIVE,
                    use_persistent_shell=False  # Use fresh process for installation
                )
                if result.return_code != 0:
                    logger.error(f"Shell installation command failed: {cmd}")
                    logger.error(f"Error: {result.stderr}")
                    return False
            except Exception as e:
                logger.error(f"Shell installation error: {e}")
                return False
                
        logger.info(f"Successfully installed {shell_type.value} shell")
        return True
    
    async def _is_shell_available(self, shell_type: ShellType) -> bool:
        """Check if a shell type is available on the system"""
        check_commands = {
            ShellType.BASH: "which bash",
            ShellType.ZSH: "which zsh", 
            ShellType.FISH: "which fish",
            ShellType.POWERSHELL_CORE: "which pwsh",
            ShellType.POWERSHELL_DESKTOP: "which powershell",
            ShellType.DEVELOPER_POWERSHELL: "which devpwsh",
            ShellType.AZURE_CLOUD_SHELL: "az --version"  # Check Azure CLI exists
        }
        
        cmd = check_commands.get(shell_type)
        if not cmd:
            return False
            
        try:
            result = await self.execute_command(
                cmd, 
                context=ExecutionContext.VM_NATIVE,
                use_persistent_shell=False
            )
            return result.return_code == 0
        except Exception:
            return False
    
    async def _get_shell_install_commands(self, shell_type: ShellType) -> List[str]:
        """Get installation commands for a shell type based on detected OS"""
        # Detect OS type
        uname_result = await self.execute_command("uname -s", context=ExecutionContext.VM_NATIVE, use_persistent_shell=False)
        is_windows = uname_result.return_code != 0 or "Linux" not in uname_result.stdout
        
        # Get package manager
        is_apt = await self._is_command_available("apt-get")
        is_brew = await self._is_command_available("brew")
        is_yum = await self._is_command_available("yum")
        is_dnf = await self._is_command_available("dnf")
        
        package_manager = None
        if is_apt:
            package_manager = "apt"
        elif is_brew:
            package_manager = "brew"
        elif is_yum:
            package_manager = "yum"
        elif is_dnf:
            package_manager = "dnf"
            
        # Build installation commands
        commands = []
        
        if shell_type == ShellType.POWERSHELL_CORE:
            if package_manager == "apt":
                commands = [
                    "wget -q https://packages.microsoft.com/config/ubuntu/20.04/packages-microsoft-prod.deb",
                    "sudo dpkg -i packages-microsoft-prod.deb",
                    "sudo apt-get update",
                    "sudo apt-get install -y powershell"
                ]
            elif package_manager == "brew":
                commands = ["brew install --cask powershell"]
            elif is_windows:
                commands = ["# Manual installation required for PowerShell Core on Windows"]
                
        elif shell_type == ShellType.ZSH:
            if package_manager == "apt":
                commands = ["sudo apt-get update", "sudo apt-get install -y zsh"]
            elif package_manager == "brew":
                commands = ["brew install zsh"]
            elif package_manager in ["yum", "dnf"]:
                commands = ["sudo yum install -y zsh"] if package_manager == "yum" else ["sudo dnf install -y zsh"]
                
        elif shell_type == ShellType.FISH:
            if package_manager == "apt":
                commands = ["sudo apt-add-repository ppa:fish-shell/release-3", "sudo apt-get update", "sudo apt-get install -y fish"]
            elif package_manager == "brew":
                commands = ["brew install fish"]
                
        elif shell_type == ShellType.AZURE_CLOUD_SHELL:
            # Azure Cloud Shell requires Azure CLI
            if package_manager == "apt":
                commands = [
                    "curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"
                ]
            elif package_manager == "brew":
                commands = ["brew install azure-cli"]
                
        return commands
    
    async def _is_command_available(self, command: str) -> bool:
        """Check if a command is available on the system"""
        try:
            result = await self.execute_command(
                f"which {command}", 
                context=ExecutionContext.VM_NATIVE,
                use_persistent_shell=False
            )
            return result.return_code == 0
        except Exception:
            return False
    
    async def _install_shell_from_download(
        self,
        shell_type: ShellType,
        download_url: str,
        install_script: Optional[str] = None,
    ) -> bool:
        """Download and install a shell binary from a URL.

        Steps:
          1. Download the file to a temporary location via ``curl``/``wget``.
          2. Validate the download (non-empty, success exit code).
          3. Execute the install script (or the downloaded file itself).
          4. Verify the installed shell is available.

        Returns True on success, False on failure (never raises).
        """
        import tempfile

        logger.info(f"Installing {shell_type.value} from {download_url}")
        tmp_dir = tempfile.mkdtemp(prefix="ai_shell_install_")
        download_path = Path(tmp_dir) / "installer"

        try:
            # ── Step 1: Download ──────────────────────────────────────
            # Prefer curl (more common on Linux/macOS), fall back to wget.
            curl_available = await self._is_command_available("curl")
            if curl_available:
                dl_cmd = f"curl -fsSL -o '{download_path}' '{download_url}'"
            else:
                dl_cmd = f"wget -q -O '{download_path}' '{download_url}'"

            dl_result = await self.execute_command(
                dl_cmd,
                context=ExecutionContext.VM_NATIVE,
                use_persistent_shell=False,
            )
            if dl_result.return_code != 0:
                logger.error(
                    f"Download failed (rc={dl_result.return_code}): {dl_result.stderr}"
                )
                return False

            # ── Step 2: Validate ──────────────────────────────────────
            if not download_path.exists() or download_path.stat().st_size == 0:
                logger.error(f"Download validation failed: file missing or empty at {download_path}")
                return False

            # ── Step 3: Execute install ───────────────────────────────
            if install_script:
                exec_cmd = install_script.replace("{{DOWNLOAD_PATH}}", str(download_path))
            else:
                # Default: mark as executable and run
                await self.execute_command(
                    f"chmod +x '{download_path}'",
                    context=ExecutionContext.VM_NATIVE,
                    use_persistent_shell=False,
                )
                exec_cmd = str(download_path)

            install_result = await self.execute_command(
                exec_cmd,
                context=ExecutionContext.VM_NATIVE,
                use_persistent_shell=False,
            )
            if install_result.return_code != 0:
                logger.error(
                    f"Install script failed (rc={install_result.return_code}): "
                    f"{install_result.stderr}"
                )
                return False

            # ── Step 4: Verify ────────────────────────────────────────
            if await self._is_shell_available(shell_type):
                logger.info(f"Successfully installed {shell_type.value} from download")
                return True

            logger.error(
                f"{shell_type.value} not found on PATH after installation"
            )
            return False

        except Exception as exc:
            logger.error(f"Shell installation from download failed: {exc}")
            return False
        finally:
            # Cleanup temporary download directory
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass  # best-effort cleanup
    
    async def shutdown(self):
        """Graceful shutdown of AI Shell and all integrated subsystems."""
        try:
            if self.settings.enable_native_tools:
                await self.native_tool_bridge.close()
                self.native_tools_ready = False

            # Cleanup shell sessions
            await self.shell_manager.cleanup_all()
            
            # Cleanup active containers
            for container_name in list(self.container_orchestrator.active_containers.keys()):
                await self.container_orchestrator.cleanup_container(container_name)
            
            # Close collaboration connections
            for connection in self.collaboration_manager.agent_connections.values():
                if "socket" in connection:
                    connection["socket"].close()

            # ── Shutdown integrated subsystems ──
            if self.file_processor and hasattr(self.file_processor, 'stop'):
                try:
                    await self.file_processor.stop()
                    logger.info("File processor stopped")
                except Exception as exc:
                    logger.warning(f"File processor shutdown error: {exc}")

            if self.prompt_system and hasattr(self.prompt_system, '_shutdown_synthesis'):
                self.prompt_system._shutdown_synthesis = True
                logger.info("Prompt system synthesis loop signaled to stop")

            if self.cache and hasattr(self.cache, 'shutdown'):
                try:
                    self.cache.shutdown()
                    logger.info("Cache subsystem shutdown")
                except Exception as exc:
                    logger.warning(f"Cache shutdown error: {exc}")

            self.initialized = False
            logger.info("AI Shell shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


# ============================================================================
# MASTER CONTROL PANEL — PHASE 1 COMPONENTS
# ============================================================================


# ============================================================================
# MEMORY PROVIDER ABSTRACTION — Phase 1.5
# ============================================================================
# Three independent memory systems (memory_core, bb7 tools, session context)
# were running in parallel with no unified interface.  This ABC provides a
# single seam so the REPL and future subsystems access memory through one
# provider, with automatic fallback ordering.
# ============================================================================


class MemoryProvider(ABC):
    """Abstract interface for unified memory access."""

    @abstractmethod
    async def store(
        self,
        content: str,
        memory_type: str = "conversation",
        importance: str = "low",
        tags: Optional[List[str]] = None,
    ) -> str:
        """Store content in memory. Returns a memory identifier string."""
        ...

    @abstractmethod
    async def retrieve(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Retrieve memories matching *query*. Returns list of memory dicts."""
        ...

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """Return memory usage statistics."""
        ...

    @abstractmethod
    async def initialize(self) -> None:
        """Perform any async setup required before first use."""
        ...

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Return health status: ``{"healthy": bool, "provider": str, ...}``."""
        ...


class NativeMemoryProvider(MemoryProvider):
    """Wraps ``src.memory_core.MemoryManager`` for direct in-process access.

    Reuses the shell's already-initialized ``memory_manager`` when available.
    Falls back gracefully if ``MEMORY_AVAILABLE`` is False.
    """

    _TYPE_MAP: Dict[str, Any] = {}  # populated lazily once MemoryType is available
    _IMPORTANCE_MAP: Dict[str, Any] = {}

    def __init__(self, user_id: str = "system") -> None:
        self._manager: Optional[Any] = None
        self._user_id = user_id
        self._initialized = False

    async def initialize(self) -> None:
        if not MEMORY_AVAILABLE:
            return
        try:
            mgr = MemoryManager()
            await mgr.initialize()
            self._manager = mgr
            # Build enum lookup tables
            NativeMemoryProvider._TYPE_MAP = {
                t.name.lower(): t for t in MemoryType
            }
            NativeMemoryProvider._IMPORTANCE_MAP = {
                i.name.lower(): i for i in MemoryImportance
            }
            self._initialized = True
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "NativeMemoryProvider init failed: %s", exc
            )

    async def store(
        self,
        content: str,
        memory_type: str = "conversation",
        importance: str = "low",
        tags: Optional[List[str]] = None,
    ) -> str:
        if self._manager is None:
            return ""
        mt = self._TYPE_MAP.get(memory_type.lower(), MemoryType.CONVERSATION)
        mi = self._IMPORTANCE_MAP.get(importance.lower(), MemoryImportance.LOW)
        mid = await self._manager.store_memory(
            user_id=self._user_id,
            content=content,
            memory_type=mt,
            importance=mi,
            tags=tags or [],
        )
        return str(mid)

    async def retrieve(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        if self._manager is None:
            return []
        return await self._manager.retrieve_memories(
            user_id=self._user_id, query=query, limit=limit
        )

    async def get_stats(self) -> Dict[str, Any]:
        if self._manager is None:
            return {"available": False, "provider": "native"}
        return await self._manager.get_user_memory_stats(user_id=self._user_id)

    async def health_check(self) -> Dict[str, Any]:
        """Check if the native memory backend is operational."""
        healthy = self._initialized and self._manager is not None
        info: Dict[str, Any] = {"healthy": healthy, "provider": "native"}
        if healthy:
            try:
                stats = await self.get_stats()
                info["stats_available"] = True
                info["memory_count"] = stats.get("total_memories", stats.get("count", "unknown"))
            except Exception as exc:
                info["healthy"] = False
                info["error"] = str(exc)
        return info


class BB7MemoryProvider(MemoryProvider):
    """Wraps bb7 tool calls as a compatibility fallback.

    Uses ``shell.call_native_tool()`` so all calls go through the
    NativeToolBridge — no direct imports of bb7 modules required.
    """

    def __init__(self, shell: "AdvancedAIShell") -> None:
        self._shell = shell

    async def initialize(self) -> None:
        # bb7 tools are discovered during shell.initialize(); nothing extra.
        pass

    async def store(
        self,
        content: str,
        memory_type: str = "conversation",
        importance: str = "low",
        tags: Optional[List[str]] = None,
    ) -> str:
        try:
            result = await self._shell.call_native_tool(
                "bb7_memory_store",
                {
                    "content": content,
                    "memory_type": memory_type,
                    "importance": importance,
                    "tags": ",".join(tags) if tags else "",
                },
            )
            if isinstance(result, dict):
                return result.get("memory_id", result.get("id", ""))
            return str(result) if result else ""
        except Exception:
            return ""

    async def retrieve(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        # Prefer bb7_memory_search (semantic, accepts query + max_results).
        # Fall back to bb7_memory_retrieve (exact key lookup, accepts key + include_related).
        try:
            result = await self._shell.call_native_tool(
                "bb7_memory_search",
                {"query": query, "max_results": limit},
            )
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("memories", result.get("results", []))
            # If bb7_memory_search returned a string (formatted output),
            # wrap it so callers always get a list.
            if isinstance(result, str) and result:
                return [{"content": result, "source": "bb7_memory_search"}]
        except Exception as search_exc:
            logger.debug("bb7_memory_search failed (%s), trying exact key lookup", search_exc)

        # Fallback: exact key retrieval via bb7_memory_retrieve
        try:
            result = await self._shell.call_native_tool(
                "bb7_memory_retrieve",
                {"key": query, "include_related": True},
            )
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
            if isinstance(result, str) and result:
                return [{"content": result, "source": "bb7_memory_retrieve"}]
            return []
        except Exception:
            return []

    async def get_stats(self) -> Dict[str, Any]:
        try:
            result = await self._shell.call_native_tool("bb7_memory_stats", None)
            if isinstance(result, dict):
                return result
            return {"raw": str(result), "provider": "bb7"}
        except Exception:
            return {"available": False, "provider": "bb7"}

    async def health_check(self) -> Dict[str, Any]:
        """Check if bb7 memory tools are reachable."""
        try:
            stats = await self.get_stats()
            return {"healthy": stats.get("available", True), "provider": "bb7", "stats": stats}
        except Exception as exc:
            return {"healthy": False, "provider": "bb7", "error": str(exc)}


class WorkflowEngine:
    """Record, replay, and automate command sequences.

    Workflows are persisted as JSON files under data/workflows/.  Each
    workflow file stores the list of recorded commands together with their
    execution results.  Workflows can later be replayed via execute_workflow().
    """

    def __init__(
        self,
        data_dir: Union[str, Path] = "data",
        router: Any = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.workflows_dir = self.data_dir / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.router = router  # set later via set_router() to avoid circular init
        self._active_recording: Optional[Dict[str, Any]] = None
        self._logger = logging.getLogger(__name__ + ".WorkflowEngine")

    def set_router(self, router: Any) -> None:
        """Inject the router dependency after construction."""
        self.router = router

    def start_recording(self, name: str, description: str = "") -> str:
        """Start recording commands as a named workflow.

        Returns the auto-generated workflow_id (UUID string).
        Raises RuntimeError if a recording is already in progress.
        """
        if self._active_recording is not None:
            raise RuntimeError(
                f"A recording is already in progress: '{self._active_recording['name']}'. "
                "Call stop_recording() before starting a new one."
            )
        if not name or not isinstance(name, str):
            raise ValueError("Workflow name must be a non-empty string")
        wf_id = str(uuid4())
        self._active_recording = {
            "id": wf_id,
            "name": name,
            "description": description,
            "commands": [],
            "created_at": time.time(),
            "stop_on_failure": True,
        }
        self._logger.info("Started recording workflow '%s' (id=%s)", name, wf_id)
        return wf_id

    def record_command(self, command: str, result: "ExecutionResult") -> None:
        """Append a command and its result to the active recording.

        Raises RuntimeError if no recording is active.
        """
        if self._active_recording is None:
            raise RuntimeError("No active recording. Call start_recording() first.")
        self._active_recording["commands"].append({
            "command": command,
            "context": result.context.value,
            "success": result.return_code == 0,
            "execution_time": result.execution_time,
        })

    def stop_recording(self) -> Dict[str, Any]:
        """Stop the active recording, persist the workflow to disk, and return it.

        Raises RuntimeError if no recording is active.
        """
        if self._active_recording is None:
            raise RuntimeError("No active recording to stop.")
        wf = self._active_recording
        wf["completed_at"] = time.time()
        wf_file = self.workflows_dir / f"{wf['name']}.json"
        wf_file.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        self._logger.info(
            "Stopped recording workflow '%s' — %d commands written to %s",
            wf["name"], len(wf["commands"]), wf_file,
        )
        self._active_recording = None
        return wf

    async def execute_workflow(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List["ExecutionResult"]:
        """Execute a saved workflow by name.

        Steps are executed sequentially.  If a step fails and stop_on_failure
        is True (the default), execution halts and the partial results are
        returned.

        Parameters are substituted into command strings using {{key}} syntax.
        """
        wf_file = self.workflows_dir / f"{name}.json"
        if not wf_file.exists():
            raise FileNotFoundError(f"Workflow not found: {name}")
        try:
            wf = json.loads(wf_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Workflow file is malformed: {wf_file}: {exc}") from exc

        if self.router is None:
            raise RuntimeError("WorkflowEngine.router not set — call set_router() first.")

        results: List[ExecutionResult] = []
        stop_on_failure: bool = wf.get("stop_on_failure", True)
        for step in wf.get("commands", []):
            cmd = self._apply_params(step.get("command", ""), params or {})
            try:
                result = await self.router.route(cmd)
            except Exception as exc:
                self._logger.error("Workflow step '%s' raised: %s", cmd, exc)
                start_dummy = time.monotonic()
                result = ExecutionResult(
                    command=cmd,
                    stdout="",
                    stderr=str(exc),
                    return_code=1,
                    execution_time=0.0,
                    context=ExecutionContext.VM_NATIVE,
                    command_type=CommandType.SYSTEM,
                    metadata={"error": str(exc)},
                )
            results.append(result)
            if result.return_code != 0 and stop_on_failure:
                self._logger.warning(
                    "Workflow '%s' stopped after failed step: %s", name, cmd
                )
                break
        return results

    def list_workflows(self) -> List[Dict[str, Any]]:
        """Return a summary list of all persisted workflows."""
        workflows: List[Dict[str, Any]] = []
        for wf_file in sorted(self.workflows_dir.glob("*.json")):
            try:
                wf = json.loads(wf_file.read_text(encoding="utf-8"))
                workflows.append({
                    "name": wf.get("name", wf_file.stem),
                    "description": wf.get("description", ""),
                    "commands_count": len(wf.get("commands", [])),
                    "created_at": wf.get("created_at"),
                })
            except Exception as exc:
                self._logger.warning("Could not read workflow file %s: %s", wf_file, exc)
        return workflows

    def _apply_params(self, command: str, params: Dict[str, Any]) -> str:
        """Replace {{key}} placeholders in a command string with param values."""
        for k, v in params.items():
            command = command.replace(f"{{{{{k}}}}}", str(v))
        return command


# ============================================================================
# PHASE 2 COMPONENTS — UX Surface Layer
# ============================================================================


class CommandPalette:
    """Fuzzy-searchable index of all native tools.

    Uses ``difflib.SequenceMatcher`` for relevance scoring against tool
    names and descriptions.  Categories are inferred from the tool name
    prefix (e.g. ``bb7_memory_*`` -> ``memory``).
    """

    _CATEGORY_PREFIXES = {
        "memory": "memory",
        "web": "web",
        "search": "web",
        "fetch": "web",
        "download": "web",
        "run_command": "execution",
        "terminal": "execution",
        "analyze": "analysis",
        "project": "project",
        "workflow": "workflow",
        "performance": "optimization",
        "cognitive": "optimization",
        "adaptive": "optimization",
        "workspace": "context",
        "capabilities": "context",
    }

    def __init__(self, shell: "AdvancedAIShell") -> None:
        self.shell = shell

    async def search(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Return tools matching *query*, ranked by fuzzy relevance score."""
        tool_list = await self.shell.list_native_tools()
        tools: List[Dict[str, Any]] = tool_list.get("tools", [])
        scored: List[Dict[str, Any]] = []
        for t in tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            score = self._fuzzy_score(query, name, desc)
            if score > 0.1:
                scored.append({
                    "name": name,
                    "description": desc,
                    "category": self._categorize_tool(name),
                    "score": round(score, 3),
                    "is_async": t.get("is_async", False),
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def _fuzzy_score(self, query: str, name: str, description: str) -> float:
        """Compute a relevance score in [0, 1] for *query* against a tool."""
        q = query.lower()
        # Exact substring match in name is a strong signal
        if q in name.lower():
            return 0.95
        name_ratio = difflib.SequenceMatcher(None, q, name.lower()).ratio()
        desc_ratio = difflib.SequenceMatcher(None, q, description.lower()).ratio()
        return max(name_ratio * 0.7 + desc_ratio * 0.3, desc_ratio * 0.6 + name_ratio * 0.4)

    def _categorize_tool(self, tool_name: str) -> str:
        """Infer a human-readable category from the tool name."""
        lower = tool_name.lower().replace("bb7_", "")
        for prefix, category in self._CATEGORY_PREFIXES.items():
            if lower.startswith(prefix):
                return category
        return "general"

    async def get_categories(self) -> Dict[str, List[str]]:
        """Return all tools grouped by inferred category."""
        tool_list = await self.shell.list_native_tools()
        tools: List[Dict[str, Any]] = tool_list.get("tools", [])
        cats: Dict[str, List[str]] = {}
        for t in tools:
            name = t.get("name", "")
            cat = self._categorize_tool(name)
            cats.setdefault(cat, []).append(name)
        return cats


class RuntimeVisibility:
    """In-memory metrics collector for tool executions.

    Records per-tool call counts, success rates, and timing stats.
    Designed to be wired into MasterRouter for automatic recording.
    """

    def __init__(self) -> None:
        self._executions: List[Dict[str, Any]] = []
        self._tool_stats: Dict[str, Dict[str, Any]] = {}
        self._start_time: float = time.monotonic()

    def record_execution(self, tool_name: str, elapsed: float, success: bool) -> None:
        """Record a single tool execution event."""
        self._executions.append({
            "tool": tool_name,
            "elapsed": elapsed,
            "success": success,
            "timestamp": time.time(),
        })
        stats = self._tool_stats.setdefault(tool_name, {
            "calls": 0, "successes": 0, "failures": 0,
            "total_time": 0.0, "min_time": float("inf"), "max_time": 0.0,
        })
        stats["calls"] += 1
        if success:
            stats["successes"] += 1
        else:
            stats["failures"] += 1
        stats["total_time"] += elapsed
        stats["min_time"] = min(stats["min_time"], elapsed)
        stats["max_time"] = max(stats["max_time"], elapsed)

    def get_status(self) -> Dict[str, Any]:
        """Return aggregate system status metrics."""
        total = len(self._executions)
        successes = sum(1 for e in self._executions if e["success"])
        uptime = time.monotonic() - self._start_time
        return {
            "uptime_seconds": round(uptime, 1),
            "total_executions": total,
            "success_rate": round(successes / total, 3) if total else 1.0,
            "unique_tools_used": len(self._tool_stats),
            "recent_commands": self._executions[-5:],
        }

    def get_tool_stats(self) -> Dict[str, Dict[str, Any]]:
        """Return per-tool statistics."""
        out: Dict[str, Dict[str, Any]] = {}
        for name, s in self._tool_stats.items():
            avg = s["total_time"] / s["calls"] if s["calls"] else 0.0
            out[name] = {
                "calls": s["calls"],
                "success_rate": round(s["successes"] / s["calls"], 3) if s["calls"] else 1.0,
                "avg_time": round(avg, 4),
                "min_time": round(s["min_time"], 4) if s["min_time"] != float("inf") else 0.0,
                "max_time": round(s["max_time"], 4),
            }
        return out


class LiveDashboard:
    """Plain-text dashboard renderer using box-drawing characters.

    Displays system status, top tools by usage, recent commands, and
    active workflow status.
    """

    def __init__(
        self,
        visibility: RuntimeVisibility,
        interface: "UnifiedCommandInterface",
    ) -> None:
        self.visibility = visibility
        self.interface = interface

    def render(self) -> str:
        """Render the full dashboard as a plain-text string."""
        status = self.visibility.get_status()
        tool_stats = self.visibility.get_tool_stats()

        sections: List[str] = []

        # System status section
        sections.append(self._render_section("System Status", [
            ("Uptime", f"{status['uptime_seconds']}s"),
            ("Executions", str(status["total_executions"])),
            ("Success Rate", f"{status['success_rate'] * 100:.1f}%"),
            ("Unique Tools", str(status["unique_tools_used"])),
        ]))

        # Top tools by call count
        if tool_stats:
            sorted_tools = sorted(tool_stats.items(), key=lambda x: x[1]["calls"], reverse=True)[:5]
            tool_rows = [
                (name, f"{s['calls']} calls, avg {s['avg_time']:.3f}s")
                for name, s in sorted_tools
            ]
            sections.append(self._render_section("Top Tools", tool_rows))

        # Recent commands
        recent = self.interface.history.recent(5)
        if recent:
            cmd_rows = [
                (e.get("command", "?")[:40], f"rc={e.get('return_code', '?')}")
                for e in recent
            ]
            sections.append(self._render_section("Recent Commands", cmd_rows))

        # Active workflows
        if hasattr(self.interface, "workflow_engine"):
            wf = self.interface.workflow_engine
            if wf._active_recording is not None:
                sections.append(self._render_section("Active Workflow", [
                    ("Recording", wf._active_recording.get("name", "unnamed")),
                ]))

        return "\n".join(sections)

    def _render_section(self, title: str, rows: List[Tuple[str, str]]) -> str:
        """Render a single dashboard section with box-drawing borders."""
        width = 60
        lines: List[str] = []
        lines.append(f"\u250c{'─' * (width - 2)}\u2510")
        lines.append(f"\u2502 {title:<{width - 4}} \u2502")
        lines.append(f"\u251c{'─' * (width - 2)}\u2524")
        for label, value in rows:
            content = f"  {label:<25} {value}"
            lines.append(f"\u2502 {content:<{width - 4}} \u2502")
        lines.append(f"\u2514{'─' * (width - 2)}\u2518")
        return "\n".join(lines)


class MessagingChannel:
    """Lightweight file-based messaging channel.

    Messages are stored as JSONL files under ``data/messages/<channel>.jsonl``.
    Each message has: id, sender, content, metadata, timestamp.
    """

    def __init__(self, data_dir: Union[str, Path] = "data") -> None:
        self._msg_dir = Path(data_dir) / "messages"
        self._msg_dir.mkdir(parents=True, exist_ok=True)

    async def send(
        self,
        channel: str,
        content: str,
        sender: str = "ai",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Send a message to *channel*. Returns the message id."""
        msg_id = uuid.uuid4().hex[:12]
        entry = {
            "id": msg_id,
            "sender": sender,
            "content": content,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        channel_file = self._msg_dir / f"{channel}.jsonl"
        async with aiofiles.open(str(channel_file), mode="a", encoding="utf-8") as fh:
            await fh.write(json.dumps(entry, default=str) + "\n")
        return msg_id

    async def receive(
        self,
        channel: str,
        since: Optional[float] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Read messages from *channel*, optionally filtered by timestamp."""
        channel_file = self._msg_dir / f"{channel}.jsonl"
        if not channel_file.exists():
            return []
        messages: List[Dict[str, Any]] = []
        async with aiofiles.open(str(channel_file), mode="r", encoding="utf-8") as fh:
            async for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None and msg.get("timestamp", 0) < since:
                    continue
                messages.append(msg)
        return messages[-limit:]

    async def list_channels(self) -> List[Dict[str, str]]:
        """Return a list of available channels with their file paths."""
        channels: List[Dict[str, str]] = []
        for f in sorted(self._msg_dir.glob("*.jsonl")):
            channels.append({"name": f.stem, "path": str(f)})
        return channels


# ============================================================================
# EXO CONTRACT NORMALIZERS — Phase 1.5
# ============================================================================
# The ExoskeletonTool returns {"name", "score"} in route candidates and
# {"chain": [str, ...]} in plans.  The MasterRouter expects {"tool",
# "confidence"} and {"tool_chain": [{...}, ...]}.  These adapters bridge
# the gap so both old and future schemas work without touching Exo internals.
# ============================================================================


def _normalize_route_candidate(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a single route candidate to canonical keys.

    Exo returns ``{"name": ..., "score": ...}``.
    Router expects ``{"tool": ..., "confidence": ...}``.
    This function is idempotent — already-canonical dicts pass through unchanged.
    """
    out = dict(raw)
    if "tool" not in out and "name" in out:
        out["tool"] = out["name"]
    if "confidence" not in out and "score" in out:
        out["confidence"] = out["score"]
    return out


def _normalize_plan_step(raw_step: Any, plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a single plan step to a canonical step dict.

    Exo chain entries are plain strings (``"bb7_memory_stats"``).
    Router expects dicts with ``{"tool": ..., "arguments": ...}``.
    """
    if isinstance(raw_step, str):
        return {
            "tool": raw_step,
            "arguments": {},
            "source": "exo_plan",
        }
    if isinstance(raw_step, dict):
        out = dict(raw_step)
        if "tool" not in out and "name" in out:
            out["tool"] = out["name"]
        out.setdefault("arguments", {})
        out.setdefault("source", "exo_plan")
        return out
    return {"tool": str(raw_step), "arguments": {}, "source": "exo_plan"}


def _normalize_plan(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a full candidate plan to canonical structure.

    Maps ``"chain"`` -> ``"tool_chain"`` as a list of normalized step dicts.
    Preserves plan_id, confidence, fallback, and all other keys.
    """
    out = dict(raw)
    chain = out.pop("chain", None) or out.get("tool_chain", [])
    out["tool_chain"] = [_normalize_plan_step(s, out) for s in chain]
    if "confidence" not in out and "score" in out:
        out["confidence"] = out["score"]
    return out


def _generate_synthetic_plan_id() -> str:
    """Create a synthetic plan_id for single-tool reflection tracking."""
    return f"synthetic_{int(time.time())}_{uuid.uuid4().hex[:8]}"


class MasterRouter:
    """Central intelligence routing layer.

    Routes user input through one of three paths:
      DIRECT_COMMAND  → NativeToolBridge via AdvancedAIShell.call_native_tool()
      WORKFLOW        → WorkflowEngine.execute_workflow()
      NATURAL_LANGUAGE → ExoskeletonTool plan/route/reflect cycle (if available)
    """

    def __init__(
        self,
        shell: "AdvancedAIShell",
        exo: Any,
        workflow_engine: WorkflowEngine,
    ) -> None:
        if shell is None:
            raise ValueError("MasterRouter requires a non-None AdvancedAIShell instance")
        self.shell = shell
        self.exo = exo
        self.workflow_engine = workflow_engine
        self.workflow_engine.set_router(self)
        self.context_manager = ContextManager()
        self._logger = logging.getLogger(__name__ + ".MasterRouter")

    async def initialize(self) -> None:
        """Ensure the underlying shell is fully initialized before routing."""
        if not self.shell.initialized:
            await self.shell.initialize()

    async def route(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> "ExecutionResult":
        """Classify and dispatch a user command.

        Parameters
        ----------
        user_input : str
            Raw command/intent string from the user.
        context : dict, optional
            Additional context values that override the ContextManager's current
            state for this single invocation.
        """
        if not isinstance(user_input, str):
            raise TypeError("user_input must be a string")
        input_type = self._classify_input(user_input)
        ctx = context if context is not None else self.context_manager.get_current_context()

        if input_type == InputType.DIRECT_COMMAND:
            return await self._execute_direct(user_input, ctx)
        elif input_type == InputType.WORKFLOW:
            return await self._execute_workflow_input(user_input, ctx)
        else:
            return await self._route_intent(user_input, ctx)

    def _classify_input(self, text: str) -> InputType:
        """Classify a raw input string into DIRECT_COMMAND, WORKFLOW, or NATURAL_LANGUAGE."""
        stripped = text.strip()
        lower = stripped.lower()
        # Direct tool calls: starts with a known prefix
        if (
            stripped.startswith("bb7_")
            or lower.startswith("tool ")
            or lower.startswith("shell ")
        ):
            return InputType.DIRECT_COMMAND
        # Workflow execution
        if lower.startswith("workflow:") or lower.startswith("run workflow:"):
            return InputType.WORKFLOW
        return InputType.NATURAL_LANGUAGE

    async def _execute_direct(
        self, command: str, context: Dict[str, Any]
    ) -> "ExecutionResult":
        """Execute a direct tool command via the NativeToolBridge."""
        start = time.monotonic()
        try:
            parts = command.strip().split(None, 1)
            tool_name = parts[0]
            args: Dict[str, Any] = {}
            if len(parts) > 1:
                try:
                    args = json.loads(parts[1])
                    if not isinstance(args, dict):
                        args = {"input": parts[1]}
                except (json.JSONDecodeError, ValueError):
                    args = {"input": parts[1]}

            raw = await self.shell.call_native_tool(tool_name, args if args else None)
            if isinstance(raw, (dict, list)):
                stdout = json.dumps(raw, indent=2, default=str)
            else:
                stdout = str(raw) if raw is not None else ""

            elapsed = time.monotonic() - start
            return ExecutionResult(
                command=command,
                stdout=stdout,
                stderr="",
                return_code=0,
                execution_time=elapsed,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"tool": tool_name, "args": args},
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            self._logger.error("Direct execution failed for '%s': %s", command, exc)
            return ExecutionResult(
                command=command,
                stdout="",
                stderr=str(exc),
                return_code=1,
                execution_time=elapsed,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"error": str(exc)},
            )

    async def _execute_workflow_input(
        self, command: str, context: Dict[str, Any]
    ) -> "ExecutionResult":
        """Parse a 'workflow: name' or 'run workflow: name' command and execute it."""
        start = time.monotonic()
        lower = command.lower()
        if lower.startswith("run workflow:"):
            name = command[len("run workflow:"):].strip()
        else:
            name = command[len("workflow:"):].strip()

        if not name:
            return ExecutionResult(
                command=command,
                stdout="",
                stderr="Workflow name must not be empty. Usage: 'workflow: <name>'",
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={},
            )

        try:
            results = await self.workflow_engine.execute_workflow(name)
            stdout = f"Workflow '{name}' completed: {len(results)} steps\n"
            stdout += "\n".join(
                f"  [{'OK' if r.return_code == 0 else 'ERR'}] {r.command}"
                for r in results
            )
            return ExecutionResult(
                command=command,
                stdout=stdout,
                stderr="",
                return_code=0,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"workflow": name, "steps": len(results)},
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                command=command,
                stdout="",
                stderr=str(exc),
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"error": str(exc)},
            )
        except Exception as exc:
            self._logger.error("Workflow execution error for '%s': %s", name, exc)
            return ExecutionResult(
                command=command,
                stdout="",
                stderr=f"Workflow execution error: {exc}",
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"error": str(exc)},
            )

    async def _route_intent(
        self, intent: str, context: Dict[str, Any]
    ) -> "ExecutionResult":
        """Route a natural language intent through the exoskeleton planner.

        Deterministic 6-step pipeline:
          classify -> normalize -> route/plan -> execute -> context update -> reflect(ALWAYS)

        If the exoskeleton is unavailable, returns an error result directing the
        user to use direct bb7_ commands instead.
        """
        start = time.monotonic()

        if self.exo is None:
            return ExecutionResult(
                command=intent,
                stdout="",
                stderr=(
                    "Natural language routing is unavailable (ExoskeletonTool not loaded). "
                    "Use direct bb7_ commands or prefix your command with 'bb7_'."
                ),
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={},
            )

        plan_id: Optional[str] = None
        tools_used: List[str] = []
        success = False
        error_msg = ""

        # ── ASPS pre-request hook (non-blocking) ─────────────────────
        context = await self._asps_pre_request(intent, context)

        try:
            # ── Step 1: Classify — get route candidates from Exo ─────────
            routing = self.exo.bb7_exo_route(
                intent=intent, max_candidates=5, include_neighbors=True
            )
            raw_top: List[Dict[str, Any]] = routing.get("top_tools", [])

            # ── Step 2: Normalize — adapt Exo schema to canonical keys ───
            top_tools = [_normalize_route_candidate(c) for c in raw_top]

            # ── Step 3+4: Route/Plan + Execute ───────────────────────────
            if self._needs_workflow(top_tools):
                # Multi-tool orchestration path
                raw_plan = self.exo.bb7_exo_plan(
                    intent=intent, beam_width=3, max_chain_length=5
                )
                raw_best: Dict[str, Any] = (raw_plan.get("candidate_plans") or [{}])[0]
                best = _normalize_plan(raw_best)
                plan_id = best.get("plan_id")
                results_data: List[Any] = []
                for step in best.get("tool_chain", []):
                    tool_name: Optional[str] = step.get("tool")
                    if not tool_name:
                        continue
                    tools_used.append(tool_name)
                    args = self._build_args(step, context)
                    raw = await self.shell.call_native_tool(tool_name, args if args else None)
                    results_data.append(raw)
                    # ── Step 5: Context update ───────────────────────
                    self.context_manager.update(tool_name, raw)
                stdout = self._format_results(results_data)
            else:
                # Single-tool path
                tool_name = top_tools[0].get("tool") if top_tools else None
                if not tool_name:
                    return ExecutionResult(
                        command=intent,
                        stdout="",
                        stderr="No matching tool found for the given intent.",
                        return_code=1,
                        execution_time=time.monotonic() - start,
                        context=ExecutionContext.VM_NATIVE,
                        command_type=CommandType.SYSTEM,
                        metadata={},
                    )
                # Assign synthetic plan_id so reflection fires
                plan_id = _generate_synthetic_plan_id()
                tools_used.append(tool_name)
                raw = await self.shell.call_native_tool(tool_name, None)
                if isinstance(raw, (dict, list)):
                    stdout = json.dumps(raw, indent=2, default=str)
                else:
                    stdout = str(raw) if raw is not None else ""
                # ── Step 5: Context update ───────────────────────────
                self.context_manager.update(tool_name, raw)

            success = True
            return ExecutionResult(
                command=intent,
                stdout=stdout,
                stderr="",
                return_code=0,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.RESEARCH,
                metadata={"tools_used": tools_used, "plan_id": plan_id},
            )

        except Exception as exc:
            error_msg = str(exc)
            self._logger.error("Intent routing failed for '%s': %s", intent, exc)
            return ExecutionResult(
                command=intent,
                stdout="",
                stderr=error_msg,
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.RESEARCH,
                metadata={"tools_used": tools_used, "error": error_msg},
            )
        finally:
            # ── Step 6: Reflect — ALWAYS fires when exo exists + tools ran ──
            if self.exo is not None and tools_used:
                reflect_plan_id = plan_id or _generate_synthetic_plan_id()
                try:
                    self.exo.bb7_exo_reflect(
                        plan_id=reflect_plan_id,
                        tools_used=tools_used,
                        success=success,
                        error=error_msg if not success else None,
                        intent=intent,
                    )
                except Exception as reflect_exc:
                    self._logger.warning(
                        "Exoskeleton reflection failed for plan %s: %s",
                        reflect_plan_id, reflect_exc,
                    )
            # ── ASPS post-response hook (non-blocking) ───────────────
            if tools_used:
                _asps_result = ExecutionResult(
                    command=intent, stdout="", stderr=error_msg,
                    return_code=0 if success else 1,
                    execution_time=time.monotonic() - start,
                    context=ExecutionContext.VM_NATIVE,
                    command_type=CommandType.RESEARCH,
                    metadata={"tools_used": tools_used},
                )
                try:
                    await self._asps_post_response(intent, _asps_result, tools_used)
                except Exception:
                    pass  # non-blocking

    def _needs_workflow(self, top_tools: List[Dict[str, Any]]) -> bool:
        """Return True when multi-tool orchestration is warranted.

        Criteria: at least two candidate tools exist, the second candidate has
        a confidence score above 0.4, and the gap between the top two scores is
        less than 0.3 (i.e. they are close enough to both be relevant).

        Accepts both ``"confidence"`` (canonical) and ``"score"`` (exo raw) keys.
        """
        if len(top_tools) >= 2:
            top_conf: float = top_tools[0].get("confidence", top_tools[0].get("score", 0.0))
            sec_conf: float = top_tools[1].get("confidence", top_tools[1].get("score", 0.0))
            return sec_conf > 0.4 and (top_conf - sec_conf) < 0.3
        return False

    def _build_args(
        self, step: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build tool arguments for a plan step, resolving {{key}} placeholders."""
        args: Dict[str, Any] = dict(step.get("arguments", {}))
        for k, v in list(args.items()):
            if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
                ctx_key = v[2:-2].strip()
                if ctx_key in context:
                    args[k] = context[ctx_key]
        return args

    def _format_results(self, results: List[Any]) -> str:
        """Format a list of tool results into human-readable multi-step output."""
        parts: List[str] = []
        for i, r in enumerate(results, 1):
            if isinstance(r, (dict, list)):
                parts.append(f"[Step {i}]\n{json.dumps(r, indent=2, default=str)}")
            else:
                parts.append(f"[Step {i}]\n{r}")
        return "\n\n".join(parts)

    # ── Gate 5A: ASPS Hooks ──────────────────────────────────────────
    async def _asps_pre_request(
        self, intent: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Non-blocking ASPS pre-request hook.

        Called before classification in ``_route_intent()``. The router
        uses ASPS as a prompt-routing substrate first, then continues into
        Exoskeleton planning and native tool execution. If ASPS is not
        available, the original context is returned unchanged.
        """
        asps = getattr(self, "_asps", None)
        if asps is None:
            return context
        try:
            enriched = dict(context)
            enriched["asps_active"] = True

            task_context = dict(context) if isinstance(context, dict) else {}
            task_context.setdefault("task_type", "intent_routing")
            task_context.setdefault("technical_domain", "advanced_ai_shell")
            task_context.setdefault("routing_mode", "control_panel_native")
            session_id = str(enriched.get("session_id") or "control_panel_router")

            prompt_generator = getattr(asps, "generate_current_prompt", None)
            if callable(prompt_generator):
                synthesized_prompt = await prompt_generator(
                    user_input=intent,
                    session_id=session_id,
                    task_context=task_context,
                )
                if synthesized_prompt:
                    synthesized_prompt = str(synthesized_prompt)
                    enriched["routing_via_asps"] = True
                    enriched["asps_prompt_preview"] = synthesized_prompt[:4000]
                    enriched["asps_prompt_length"] = len(synthesized_prompt)

            if hasattr(asps, "performance_metrics"):
                asps.performance_metrics.task_completion_rate += 0.01  # track attempt
            return enriched
        except Exception as exc:
            self._logger.debug("ASPS pre-request hook error (non-blocking): %s", exc)
            return context

    async def _asps_post_response(
        self, intent: str, result: "ExecutionResult", tools_used: List[str]
    ) -> None:
        """Non-blocking ASPS post-response hook.

        Called after execution in ``_route_intent()``.  Feeds outcome
        data to ASPS for performance tracking and prompt evolution.
        """
        asps = getattr(self, "_asps", None)
        if asps is None:
            return
        try:
            if hasattr(asps, "performance_metrics"):
                if result.return_code == 0:
                    asps.performance_metrics.response_quality_score = min(
                        1.0, asps.performance_metrics.response_quality_score + 0.05
                    )
                else:
                    asps.performance_metrics.response_quality_score = max(
                        0.0, asps.performance_metrics.response_quality_score - 0.1
                    )
        except Exception as exc:
            self._logger.debug("ASPS post-response hook error (non-blocking): %s", exc)


class CommandHistory:
    """Persistent command history backed by a JSONL file.

    Each entry records the command text, return code, execution time, and a
    Unix timestamp.  Entries are appended atomically to avoid truncating an
    existing history file on restart.
    """

    def __init__(
        self,
        history_file: Union[str, Path] = "data/command_history.jsonl",
    ) -> None:
        self._file = Path(history_file)
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._entries: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        """Load all existing history entries from disk."""
        if not self._file.exists():
            return []
        entries: List[Dict[str, Any]] = []
        for line in self._file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # skip malformed lines rather than aborting
        return entries

    def add(self, command: str, result: "ExecutionResult") -> None:
        """Append a command + result record to both the in-memory list and disk."""
        entry: Dict[str, Any] = {
            "command": command,
            "return_code": result.return_code,
            "execution_time": result.execution_time,
            "timestamp": time.time(),
        }
        self._entries.append(entry)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def recent(self, n: int = 20) -> List[Dict[str, Any]]:
        """Return the last n history entries (most-recent last)."""
        if n <= 0:
            return []
        return list(self._entries[-n:])

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Return all entries whose command contains query (case-insensitive)."""
        if not query:
            return list(self._entries)
        q = query.lower()
        return [e for e in self._entries if q in e.get("command", "").lower()]

    def clear(self) -> None:
        """Wipe the in-memory list and truncate the history file."""
        self._entries.clear()
        self._file.write_text("", encoding="utf-8")


class UnifiedCommandInterface:
    """Single entry point for all user input.

    Prefix dispatch table
    ---------------------
    ``//``   Comment — silently ignored; returns None.
    ``?``    Help — list available tools, optionally filtered by the rest of
             the string.
    ``!``    Workflow — execute the named workflow.
    ``@``    Messaging — native JSONL-backed channel send/read.
    (other)  Passed to MasterRouter.route().
    """

    PHASE3_MSG = "Messaging is available natively through JSONL channels."

    _SHORTCUTS: Dict[str, str] = {
        "/s": "bb7_memory_stats",
        "/m": "bb7_memory_retrieve",
        "/t": "tool list",
        "/w": "bb7_analyze_workflow_patterns",
        "/p": "bb7_analyze_project_structure",
    }

    def __init__(self, router: MasterRouter) -> None:
        if not isinstance(router, MasterRouter):
            raise TypeError("UnifiedCommandInterface requires a MasterRouter instance")
        self.router = router
        self.workflow_engine = router.workflow_engine
        self.history = CommandHistory()
        self.palette = CommandPalette(router.shell)
        self.visibility = RuntimeVisibility()
        self.dashboard = LiveDashboard(self.visibility, self)
        self.messaging = MessagingChannel()

    async def process_input(self, raw_input: str) -> Optional["ExecutionResult"]:
        """Classify and dispatch raw input.

        Returns None for comment lines (``//`` prefix) so callers can skip
        printing.  All other inputs return an ExecutionResult.

        Prefix dispatch:
            ``//``       Comment (returns None)
            ``?query``   Palette search (fuzzy tool search)
            ``!name``    Workflow execution
            ``@``        Messaging (Phase 3 stub)
            ``/s``, etc  Shortcut aliases
            ``status``   System status summary
            ``dashboard`` Full live dashboard
            ``palette:`` Palette category listing
            (other)      Routed via MasterRouter
        """
        text = raw_input.strip() if isinstance(raw_input, str) else ""

        if not text:
            return None

        # Comment — silently ignore
        if text.startswith("//"):
            return None

        # Shortcut expansion (e.g. /s -> bb7_memory_stats)
        if text in self._SHORTCUTS:
            text = self._SHORTCUTS[text]

        # Help / Palette search
        if text.startswith("?"):
            query = text[1:].strip()
            result = await self._palette_search(query)
            self.history.add(raw_input, result)
            return result

        # Workflow shorthand
        if text.startswith("!"):
            name = text[1:].strip()
            result = await self._run_workflow(name)
            self.history.add(raw_input, result)
            return result

        # Messaging — @channel message or @channel (read)
        if text.startswith("@"):
            result = await self._handle_messaging(text)
            self.history.add(raw_input, result)
            return result

        # Built-in REPL commands
        lower = text.lower()

        if lower == "status":
            status = self.visibility.get_status()
            result = ExecutionResult(
                command=text, stdout=json.dumps(status, indent=2, default=str),
                stderr="", return_code=0, execution_time=0.0,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.SYSTEM,
                metadata={"source": "runtime_visibility"},
            )
            self.history.add(raw_input, result)
            return result

        if lower == "dashboard":
            rendered = self.dashboard.render()
            result = ExecutionResult(
                command=text, stdout=rendered,
                stderr="", return_code=0, execution_time=0.0,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.SYSTEM,
                metadata={"source": "live_dashboard"},
            )
            self.history.add(raw_input, result)
            return result

        if lower.startswith("palette:"):
            cats = await self.palette.get_categories()
            result = ExecutionResult(
                command=text, stdout=json.dumps(cats, indent=2),
                stderr="", return_code=0, execution_time=0.0,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.SYSTEM,
                metadata={"source": "command_palette"},
            )
            self.history.add(raw_input, result)
            return result

        # Normal routing
        start = time.monotonic()
        result = await self.router.route(text)
        elapsed = time.monotonic() - start
        # Record execution in visibility layer
        tool_name = result.metadata.get("tool", result.metadata.get("tools_used", [text])[0] if result.metadata.get("tools_used") else text)
        self.visibility.record_execution(str(tool_name), elapsed, result.return_code == 0)
        self.history.add(raw_input, result)
        return result

    async def _palette_search(self, query: str) -> "ExecutionResult":
        """Search the command palette for tools matching *query*."""
        start = time.monotonic()
        if not query:
            # No query — fall back to full tool listing
            return await self.show_help("")
        try:
            results = await self.palette.search(query)
            lines = [f"  {r['name']} ({r['category']}) — score {r['score']}" for r in results]
            stdout = f"Palette search for '{query}' ({len(results)} results):\n" + "\n".join(lines) if lines else f"No tools matching '{query}'"
            return ExecutionResult(
                command=f"?{query}", stdout=stdout, stderr="",
                return_code=0, execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.SYSTEM,
                metadata={"palette_results": len(results), "query": query},
            )
        except Exception as exc:
            return ExecutionResult(
                command=f"?{query}", stdout="", stderr=str(exc),
                return_code=1, execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.SYSTEM,
                metadata={},
            )

    async def _handle_messaging(self, text: str) -> "ExecutionResult":
        """Handle @channel or @channel message dispatch."""
        start = time.monotonic()
        rest = text[1:].strip()
        if not rest:
            # List available channels
            try:
                channels = await self.messaging.list_channels()
                stdout = "Channels:\n" + "\n".join(f"  #{c['name']}" for c in channels) if channels else "No channels yet. Send with: @channel your message"
                return ExecutionResult(
                    command=text, stdout=stdout, stderr="",
                    return_code=0, execution_time=time.monotonic() - start,
                    context=ExecutionContext.VM_NATIVE, command_type=CommandType.COLLABORATION,
                    metadata={"channels": len(channels)},
                )
            except Exception as exc:
                return ExecutionResult(
                    command=text, stdout="", stderr=str(exc),
                    return_code=1, execution_time=time.monotonic() - start,
                    context=ExecutionContext.VM_NATIVE, command_type=CommandType.COLLABORATION,
                    metadata={},
                )
        parts = rest.split(None, 1)
        channel = parts[0]
        if len(parts) == 1:
            # Read from channel
            try:
                messages = await self.messaging.receive(channel, limit=20)
                if not messages:
                    stdout = f"No messages in #{channel}"
                else:
                    lines = [f"  [{m.get('sender', '?')}] {m.get('content', '')}" for m in messages]
                    stdout = f"#{channel} ({len(messages)} messages):\n" + "\n".join(lines)
                return ExecutionResult(
                    command=text, stdout=stdout, stderr="",
                    return_code=0, execution_time=time.monotonic() - start,
                    context=ExecutionContext.VM_NATIVE, command_type=CommandType.COLLABORATION,
                    metadata={"channel": channel, "count": len(messages)},
                )
            except Exception as exc:
                return ExecutionResult(
                    command=text, stdout="", stderr=str(exc),
                    return_code=1, execution_time=time.monotonic() - start,
                    context=ExecutionContext.VM_NATIVE, command_type=CommandType.COLLABORATION,
                    metadata={},
                )
        # Send message to channel
        content = parts[1]
        try:
            msg_id = await self.messaging.send(channel, content)
            return ExecutionResult(
                command=text, stdout=f"Sent to #{channel} (id: {msg_id})",
                stderr="", return_code=0, execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.COLLABORATION,
                metadata={"channel": channel, "msg_id": msg_id},
            )
        except Exception as exc:
            return ExecutionResult(
                command=text, stdout="", stderr=str(exc),
                return_code=1, execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE, command_type=CommandType.COLLABORATION,
                metadata={},
            )

    async def show_help(self, query: str = "") -> "ExecutionResult":
        """List available tools, optionally filtered by a query substring."""
        start = time.monotonic()
        try:
            tool_list = await self.router.shell.native_tool_bridge.list_tools()
            tools: List[Dict[str, Any]] = tool_list.get("tools", [])
            if query:
                q = query.lower()
                tools = [t for t in tools if q in t["name"].lower()]
            lines = [
                f"  {t['name']}" + (" [async]" if t.get("is_async") else "")
                for t in tools
            ]
            header = f"Available tools ({len(tools)}):"
            if query:
                header += f" (filter: '{query}')"
            stdout = header + "\n" + "\n".join(lines) if lines else header + "\n  (none found)"
            return ExecutionResult(
                command=f"?{query}",
                stdout=stdout,
                stderr="",
                return_code=0,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"tool_count": len(tools), "query": query},
            )
        except Exception as exc:
            return ExecutionResult(
                command=f"?{query}",
                stdout="",
                stderr=str(exc),
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={},
            )

    async def _run_workflow(self, name: str) -> "ExecutionResult":
        """Execute a workflow by name and format the results."""
        start = time.monotonic()
        if not name:
            return ExecutionResult(
                command="!",
                stdout="",
                stderr="Workflow name must not be empty. Usage: '!<workflow_name>'",
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={},
            )
        try:
            results = await self.workflow_engine.execute_workflow(name)
            stdout = f"Workflow '{name}': {len(results)} steps completed\n"
            stdout += "\n".join(
                f"  [{'OK' if r.return_code == 0 else 'ERR'}] {r.command}"
                for r in results
            )
            return ExecutionResult(
                command=f"!{name}",
                stdout=stdout,
                stderr="",
                return_code=0,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"workflow": name, "steps": len(results)},
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                command=f"!{name}",
                stdout="",
                stderr=str(exc),
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"error": str(exc)},
            )
        except Exception as exc:
            return ExecutionResult(
                command=f"!{name}",
                stdout="",
                stderr=f"Workflow execution error: {exc}",
                return_code=1,
                execution_time=time.monotonic() - start,
                context=ExecutionContext.VM_NATIVE,
                command_type=CommandType.SYSTEM,
                metadata={"error": str(exc)},
            )


# ============================================================================
# CONVENIENCE FUNCTIONS AND EXAMPLES
# ============================================================================

async def create_ai_shell(agent_id: Optional[UUID] = None, artifact_manager: ArtifactManager = None) -> AdvancedAIShell:
    """Create and initialize an AI Shell instance"""
    settings = AIShellSettings()
    shell = AdvancedAIShell(settings, agent_id, artifact_manager)
    
    success = await shell.initialize()
    if not success:
        raise RuntimeError("Failed to initialize AI Shell")
    
    return shell


async def demo_ai_shell_capabilities():
    """Demonstrate the full capabilities of the Advanced AI Shell"""
    print("🚀 Advanced AI Shell Capabilities Demo")
    print("=" * 50)
    
    # Create AI shell instance
    shell = await create_ai_shell()
    
    try:
        # 1. VM Native execution
        print("\n1. VM Native Execution:")
        result = await shell.execute_command(
            "uname -a && python3 --version",
            context=ExecutionContext.VM_NATIVE
        )
        print(f"Output: {result.stdout}")
        
        # 2. Container overlay execution
        print("\n2. Container Overlay Execution:")
        container_spec = ContainerSpec(
            image="python:3.11-slim",
            environment={"PYTHONPATH": "/workspace"}
        )
        
        result = await shell.execute_command(
            "python3 -c 'import sys; print(f\"Python {sys.version} in container\")'",
            context=ExecutionContext.CONTAINER_OVERLAY,
            container_spec=container_spec
        )
        print(f"Container Output: {result.stdout}")
        print(f"Container ID: {result.container_id}")
        
        # 3. Hybrid execution
        print("\n3. Hybrid Execution (VM orchestrating container):")
        artifact_files = {
            "demo.py": "print('Hello from artifact container!')\nprint('Unlimited execution capabilities!')"
        }
        
        result = await shell.execute_command(
            "python3 demo.py",
            context=ExecutionContext.HYBRID,
            container_spec=container_spec,
            artifact_files=artifact_files
        )
        print(f"Hybrid Output: {result.stdout}")
        
        # 4. Multi-Agent collaboration execution
        print("\n4. Multi-Agent Collaboration Execution:")
        # For demo, we'll collaborate with two instances of ourselves
        agent_id_1 = uuid4()
        agent_id_2 = uuid4()
        
        result = await shell.execute_command(
            "Collaborate on a task with error handling",
            context=ExecutionContext.MULTI_AGENT,
            collaboration_agents=[agent_id_1, agent_id_2]
        )
        print(f"Collaboration Output: {result.stdout}")
        print(f"Agent Responses: {result.collaborator_responses}")
        
        print("\n✅ Demo completed successfully!")
        print(f"Total commands executed: {len(shell.command_history)}")
        
    except Exception as e:
        print(f"❌ Demo failed: {e}")
        
    finally:
        await shell.shutdown()


# ============================================================================
# MASTER CONTROL PANEL — REPL ENTRY POINT
# ============================================================================

async def _control_panel_initialize() -> UnifiedCommandInterface:
    """Wire up the master control panel and return the unified command interface."""
    shell = AdvancedAIShell()
    await shell.initialize()

    # Resolve Exoskeleton through the native bridge instead of direct package imports.
    exo: Any = None
    try:
        exo = await shell.get_native_tool_provider("bb7_exo_route")
        if exo is not None:
            logger.info("Exoskeleton provider resolved through NativeToolBridge — natural language routing enabled.")
        else:
            logger.warning("Exoskeleton provider unavailable from NativeToolBridge — natural language routing disabled.")
    except Exception as exo_exc:
        logger.warning("Exoskeleton provider resolution failed: %s", exo_exc)

    workflow_engine = WorkflowEngine(data_dir=Path("data"))
    router = MasterRouter(shell=shell, exo=exo, workflow_engine=workflow_engine)
    await router.initialize()
    interface = UnifiedCommandInterface(router=router)

    # ── Memory provider selection (Native first, BB7 fallback) ───────
    # Compatibility note: bb7_ names remain operator-facing, but execution stays native.
    memory_provider: Optional[MemoryProvider] = None
    provider_info: Dict[str, Any] = {
        "provider": None,
        "reason": None,
        "fallback_tried": False,
    }
    try:
        native_mp = NativeMemoryProvider()
        await native_mp.initialize()
        if native_mp._manager is not None:
            memory_provider = native_mp
            provider_info["provider"] = "NativeMemoryProvider"
            provider_info["reason"] = "memory_core available and initialized"
            logger.info("Memory provider: NativeMemoryProvider (memory_core)")
        else:
            provider_info["reason"] = "NativeMemoryProvider init returned None manager"
            logger.info("NativeMemoryProvider: manager is None, trying fallback")
    except Exception as mp_exc:
        provider_info["reason"] = f"NativeMemoryProvider exception: {mp_exc}"
        logger.warning("NativeMemoryProvider selection failed: %s", mp_exc)

    if memory_provider is None:
        provider_info["fallback_tried"] = True
        try:
            bb7_mp = BB7MemoryProvider(shell=shell)
            await bb7_mp.initialize()
            memory_provider = bb7_mp
            provider_info["provider"] = "BB7MemoryProvider"
            provider_info["reason"] = (provider_info.get("reason", "") +
                                       " -> BB7 fallback succeeded")
            logger.info("Memory provider: BB7MemoryProvider (tool bridge)")
        except Exception as mp_exc:
            provider_info["reason"] = (provider_info.get("reason", "") +
                                       f" -> BB7 also failed: {mp_exc}")
            logger.warning("BB7MemoryProvider selection failed: %s", mp_exc)

    interface._memory_provider = memory_provider  # type: ignore[attr-defined]
    interface._memory_provider_info = provider_info  # type: ignore[attr-defined]

    # Gate 4A: Wire SessionMemoryContext if native memory is active
    if isinstance(memory_provider, NativeMemoryProvider) and memory_provider._manager is not None:
        try:
            from src.memory_integration import SessionMemoryContext  # type: ignore
            session_ctx = SessionMemoryContext(
                session_id=f"control_panel_{uuid.uuid4().hex[:8]}",
                user_id="system",
                memory_manager=memory_provider._manager,
            )
            interface._session_ctx = session_ctx  # type: ignore[attr-defined]
            logger.info("SessionMemoryContext attached to interface")
        except ImportError:
            logger.info("SessionMemoryContext unavailable (src.memory_integration not found)")
        except Exception as smc_exc:
            logger.warning("SessionMemoryContext init failed: %s", smc_exc)

    # Gate 5A: ASPS initialization (non-blocking)
    #
    # CRITICAL: ASPS must be attached to the MasterRouter (not just the
    # interface) so that MasterRouter._asps_pre_request() and
    # _asps_post_response() actually fire during intent routing.
    asps_instance: Any = None
    if shell.prompt_system is not None:
        asps_instance = shell.prompt_system
        logger.info("ASPS resolved from shell.prompt_system")
    elif PROMPT_SYSTEM_AVAILABLE and CACHE_AVAILABLE:
        try:
            cache_inst = SomnusCache()
            asps = AutonomousPromptSystem(
                cache=cache_inst,
                memory_manager=memory_provider._manager if isinstance(memory_provider, NativeMemoryProvider) else None,
                user_id="system",
            )
            init_layers = getattr(asps, "initialize_prompt_layers", None)
            if callable(init_layers):
                await init_layers()
            asps_instance = asps
            logger.info("ASPS resolved via fallback instance")
        except Exception as asps_exc:
            logger.warning("ASPS initialization skipped: %s", asps_exc)

    if asps_instance is not None:
        # Attach to BOTH router (for pre/post hooks) AND interface (for consumers)
        router._asps = asps_instance  # type: ignore[attr-defined]
        interface._asps = asps_instance  # type: ignore[attr-defined]
        logger.info("ASPS attached to MasterRouter and UnifiedCommandInterface")
    else:
        logger.info("ASPS unavailable — natural language routing will skip prompt enrichment")

    return interface


# Backward-compatible alias retained for existing automation and notes.
_mcp_initialize = _control_panel_initialize


async def _control_panel_repl() -> None:
    """Master Control Panel — interactive async REPL loop."""
    print("[SHELL] Initializing Master Control Panel...")
    try:
        interface = await _control_panel_initialize()
    except Exception as exc:
        print(f"[SHELL] FATAL: initialization failed: {exc}")
        return

    print(
        "[SHELL] Ready. Commands: 'exit'=quit | '?query'=search | "
        "'!name'=workflow | '//...'=comment | '@msg'=messaging\n"
        "        Built-in: 'status' | 'dashboard' | 'palette:' | shortcuts: /s /m /t /w /p\n"
        "        Compatibility: legacy _mcp_* aliases remain for existing automation."
    )
    print("-" * 70)

    while True:
        try:
            raw = input("[SHELL] > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\\n[SHELL] Exiting.")
            break

        if not raw:
            continue

        if raw.lower() in ("exit", "quit"):
            print("[SHELL] Goodbye.")
            break

        try:
            result = await interface.process_input(raw)
        except Exception as exc:
            print(f"[ERR] Unexpected error: {exc}")
            continue

        if result is None:
            continue  # comment — nothing to print

        status = "[OK] " if result.return_code == 0 else "[ERR]"
        timing = f"({result.execution_time:.3f}s)"

        if result.stdout:
            print(f"{status} {timing}")
            print(result.stdout)
        if result.stderr:
            print(f"[ERR] {result.stderr}")
        print()

        # ── Best-effort memory store — never blocks the REPL ─────────
        mp: Optional[MemoryProvider] = getattr(interface, "_memory_provider", None)
        if mp is not None and result.return_code == 0 and result.stdout:
            try:
                await mp.store(
                    content=f"Command: {raw}\nResult: {result.stdout[:500]}",
                    memory_type="conversation",
                    importance="low",
                    tags=["mcp_repl"],
                )
            except Exception:
                pass  # best-effort — never block the REPL loop


# Backward-compatible alias retained for existing automation and notes.
_mcp_repl = _control_panel_repl


if __name__ == "__main__":
    asyncio.run(_control_panel_repl())
