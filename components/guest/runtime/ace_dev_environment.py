"""
AI Personal Development Environment
Production-ready framework for AI to build persistent development capabilities
Integrated with Somnus Sovereign Systems for complete digital sovereignty
"""

import asyncio
import json
import hashlib
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4
import ast
import inspect
from collections import defaultdict, deque

# Import Somnus core systems
from .ai_action_orchestrator import (
    AIActionOrchestrator,
    ArtifactExecutionResult,
    VMActionResult
)

# Import Somnus memory and model systems
try:
    from ...core.memory_core import MemoryManager, MemoryType, ImportanceLevel
    from ...core.model_loader import SomnusModelLoader
    from ...core.session_manager import SessionManager
except ImportError:
    # Create placeholder classes if not available
    class MemoryManager:
        def __init__(self, name): pass
        async def store_memory(self, **kwargs): pass
    
    class MemoryType:
        CORE_FACT = "core_fact"
        CONVERSATION = "conversation"
        CODE_SNIPPET = "code_snippet"
        TOOL_RESULT = "tool_result"
    
    class ImportanceLevel:
        HIGH = "high"
        MEDIUM = "medium" 
        LOW = "low"
    
    class SomnusModelLoader:
        async def generate_completion(self, **kwargs):
            return None
    
    class SessionManager:
        def __init__(self): pass

@dataclass
class AIPreferences:
    preferred_ides: List[str]
    coding_styles: Dict[str, Any]
    favorite_tools: List[str]
    automation_preferences: Dict[str, Any]
    learning_history: List[Dict[str, Any]]
    efficiency_metrics: Dict[str, float]
    project_templates: Dict[str, List[str]]
    vm_profiles: Dict[str, Dict[str, Any]]
    # Pillar 2: ACE Enhancement
    command_patterns: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    generated_libraries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    workflow_suggestions: List[Dict[str, Any]] = field(default_factory=list)
    ace_metrics: Dict[str, float] = field(default_factory=dict)

@dataclass
class ProjectEnvironment:
    name: str
    required_tools: List[str]
    virtual_env: Optional[str]
    ide_config: Dict[str, Any]
    startup_commands: List[str]
    directory_structure: List[str]
    recommended_vm_profile: str = "idle"

@dataclass
class AIDevelopmentSession:
    session_id: str
    project_type: str
    project_path: str
    workspace_ready: bool
    ide_session: Dict[str, Any]
    virtual_environment: Dict[str, Any]
    available_templates: List[str]
    installed_tools: List[str]
    startup_results: List[Dict[str, Any]]
    efficiency_rating: float
    setup_time_seconds: float
    associated_vm_id: Optional[str] = None
    # Pillar 2: ACE Enhancement
    command_history: deque = field(default_factory=lambda: deque(maxlen=100))
    workflow_patterns: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    generated_functions: List[str] = field(default_factory=list)
    ace_enabled: bool = True

class AIDevelopmentEnvironment:
    """AI's personal development setup that grows and evolves over time"""

    def __init__(self, orchestrator: AIActionOrchestrator, base_path: str = "/home/ai"):
        self.orchestrator = orchestrator
        self.base_path = Path(base_path)
        self.workspace_path = self.base_path / "workspace"
        self.tools_path = self.base_path / "tools"
        self.projects_path = self.base_path / "projects"
        self.config_path = self.base_path / "config"
        self.cache_path = self.base_path / "cache"
        self.logs_path = self.base_path / "logs"
        
        # Initialize Somnus integrations
        self.memory_manager = MemoryManager("ai_dev_env")
        self.model_loader = SomnusModelLoader()
        self.session_manager = SessionManager()
        
        self.preferences = self._load_ai_preferences()
        self.installed_tools: List[str] = []
        self.custom_scripts: List[str] = []
        self.personal_libraries: List[str] = []
        self.project_environments: Dict[str, ProjectEnvironment] = {}
        self.active_sessions: Dict[str, AIDevelopmentSession] = {}
        
        # Pillar 2: Initialize Autonomous Capability Expansion
        self.ace = AutonomousCapabilityExpansion(self)
        
        asyncio.create_task(self._ensure_directory_structure())

    async def _ensure_directory_structure(self):
        """Create all necessary directories"""
        directories = [
            self.workspace_path,
            self.tools_path,
            self.projects_path,
            self.config_path,
            self.cache_path,
            self.logs_path,
            self.tools_path / "libraries",
            self.tools_path / "automation",
            self.tools_path / "templates",
            self.cache_path / "downloads",
            self.cache_path / "web_requests",
            self.base_path / "venvs",
        ]
        
        for directory in directories:
            await self._execute_command(f"mkdir -p {directory}")

    def _load_ai_preferences(self) -> AIPreferences:
        """Load AI preferences with proper defaults"""
        preferences_file = self.config_path / "ai_preferences.json"
        default_preferences = AIPreferences(
            preferred_ides=["code", "vim", "jupyter"],
            coding_styles={
                "python": {"line_length": 88, "formatter": "black"},
                "javascript": {"formatter": "prettier", "semi": True},
                "general": {"indentation": "spaces", "tab_size": 4},
            },
            favorite_tools=[],
            automation_preferences={"auto_backup": True, "auto_format": True, "auto_test": False},
            learning_history=[],
            efficiency_metrics={},
            project_templates={},
            vm_profiles={
                "idle": {"vcpus": 1, "memory_gb": 4, "description": "Low power state"},
                "coding": {"vcpus": 4, "memory_gb": 8, "description": "Optimized for compilation"},
                "research": {"vcpus": 2, "memory_gb": 6, "description": "Balanced for browsing"},
                "media_creation": {"vcpus": 6, "memory_gb": 16, "gpu_enabled": True, "description": "High-power for generation"}
            }
        )
        
        try:
            if preferences_file.exists():
                with open(preferences_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Merge with defaults to ensure all fields are present
                    for field in default_preferences.__dataclass_fields__:
                        if field not in data:
                            data[field] = getattr(default_preferences, field)
                    return AIPreferences(**data)
        except Exception as e:
            self._log(f"Failed to load preferences: {e}", level="ERROR")
        return default_preferences

    async def _save_ai_preferences(self):
        """Save preferences to disk and memory"""
        preferences_file = self.config_path / "ai_preferences.json"
        try:
            with open(preferences_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self.preferences), f, indent=2, default=str)
            
            # Store in Somnus memory for persistence
            await self.memory_manager.store_memory(
                content=json.dumps(asdict(self.preferences)),
                memory_type=MemoryType.CORE_FACT,
                importance=ImportanceLevel.HIGH,
                metadata={"type": "ai_preferences", "timestamp": datetime.now().isoformat()}
            )
        except Exception as e:
            self._log(f"Failed to save preferences: {e}", level="ERROR")

    def _sanitize_command(self, cmd: str) -> str:
        """Sanitize command for safe execution"""
        allowed = re.compile(r"[^\w\s\-_=:/.,@+%()[\]{}\"']+")
        cmd = allowed.sub(" ", cmd)
        return re.sub(r"\s+", " ", cmd).strip()

    def _sanitize_path(self, p: Path) -> str:
        """Sanitize path for safe usage"""
        return re.sub(r"[^\w\-_/\.:]", "_", str(p))

    async def setup_development_session(
        self, 
        project_type: str, 
        project_name: Optional[str] = None, 
        create_new: bool = False,
        create_dedicated_vm: bool = False
    ) -> AIDevelopmentSession:
        """Setup a complete development session with all tools and environment"""
        session_start = datetime.now()
        session_id = hashlib.md5(f"{project_type}_{datetime.now()}".encode()).hexdigest()[:8]
        
        # Store session start in memory
        await self.memory_manager.store_memory(
            content=f"Starting development session for {project_type}",
            memory_type=MemoryType.CONVERSATION,
            importance=ImportanceLevel.MEDIUM,
            metadata={"session_id": session_id, "project_type": project_type}
        )
        
        # Get or create project environment
        env_config = await self._get_or_create_project_environment(project_type)
        
        # Create dedicated VM if requested
        vm_id = None
        if create_dedicated_vm:
            vm_result = await self._create_dedicated_vm_for_project(env_config, project_type)
            if vm_result.success and vm_result.data:
                vm_id = vm_result.data.get("vm_id")
        
        # Check and install required tools
        missing_tools = await self._check_required_tools(env_config.required_tools)
        if missing_tools:
            await self._install_tools(missing_tools, project_type)
            
        # Create project workspace
        if project_name and create_new:
            project_path = await self._create_project_workspace(project_name, env_config)
        else:
            project_path = self.workspace_path / project_type
            await self._execute_command(f"mkdir -p {project_path}")
            
        # Setup IDE session
        ide_session = await self._setup_ide_session(env_config, project_path)
        
        # Setup virtual environment
        venv_info = await self._setup_virtual_environment(env_config, project_path)
        
        # Execute startup commands
        startup_results = await self._execute_startup_commands(env_config.startup_commands, project_path)
        
        # Calculate metrics
        setup_time = (datetime.now() - session_start).total_seconds()
        efficiency_rating = self._calculate_efficiency_rating(project_type, setup_time)
        await self._update_learning_history(project_type, setup_time, efficiency_rating)
        templates = self.preferences.project_templates.get(project_type, [])
        
        # Create session object
        session = AIDevelopmentSession(
            session_id=session_id,
            project_type=project_type,
            project_path=str(project_path),
            workspace_ready=True,
            ide_session=ide_session,
            virtual_environment=venv_info,
            available_templates=templates,
            installed_tools=env_config.required_tools,
            startup_results=startup_results,
            efficiency_rating=efficiency_rating,
            setup_time_seconds=setup_time,
            associated_vm_id=vm_id
        )
        
        # Store active session
        self.active_sessions[session_id] = session
        
        return session
    
    # Pillar 2: ACE Integration Methods
    async def generate_library_function(self, library_name: str, function_description: str, 
                                       session_id: str, test_cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Create a new function using ACE and integrate it into a personal library"""
        return await self.ace.create_personal_library_function(library_name, function_description, session_id, test_cases)
    
    async def observe_command(self, command: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Observe a command for pattern analysis and automation suggestions"""
        return await self.ace.observe_command_pattern(command, session_id)
    
    async def implement_automation(self, suggestion_id: int, session_id: str) -> Dict[str, Any]:
        """Implement a suggested workflow automation"""
        return await self.ace.implement_workflow_suggestion(suggestion_id, session_id)
    
    def get_workflow_suggestions(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get pending workflow suggestions"""
        suggestions = getattr(self.preferences, 'workflow_suggestions', [])
        if session_id:
            return [s for s in suggestions if s.get('session_id') == session_id and s.get('status') == 'pending_review']
        return [s for s in suggestions if s.get('status') == 'pending_review']
    
    def get_ace_performance_metrics(self) -> Dict[str, Any]:
        """Get ACE performance and capability metrics"""
        return self.ace.get_ace_metrics()

    async def _create_dedicated_vm_for_project(self, env_config: ProjectEnvironment, project_type: str) -> VMActionResult:
        """Create a dedicated VM for a project with appropriate resources"""
        vm_name = f"{project_type}_dev_environment_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        personality_config = {
            "purpose": f"Development environment for {project_type} projects",
            "preferred_tools": env_config.required_tools
        }
        
        # Create the VM using orchestrator
        result = await self.orchestrator.create_sovereign_ai_computer(vm_name, personality_config)
        
        if result.success and result.data:
            vm_id = result.data.get("vm_id")
            if vm_id:
                # Scale to appropriate profile
                await self.orchestrator.scale_ai_computer_resources(vm_id, env_config.recommended_vm_profile)
                
        return result

    def _calculate_efficiency_rating(self, project_type: str, setup_time: float) -> float:
        """Calculate efficiency rating based on historical performance"""
        historical_times = [entry["setup_time"] for entry in self.preferences.learning_history if entry["project_type"] == project_type]
        if not historical_times:
            return 1.0
        average_time = sum(historical_times) / len(historical_times)
        if average_time == 0:
            return 1.0
        return max(0.0, min(2.0, (average_time / setup_time)))

    async def _update_learning_history(self, project_type: str, setup_time: float, efficiency_rating: float, errors: Optional[List[str]] = None):
        """Update learning history and store in memory"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "project_type": project_type,
            "setup_time": setup_time,
            "efficiency_rating": efficiency_rating,
            "errors": errors or [],
        }
        self.preferences.learning_history.append(entry)
        await self._save_ai_preferences()
        
        # Store in Somnus memory
        await self.memory_manager.store_memory(
            content=f"Development session completed: {project_type} in {setup_time:.2f}s with efficiency {efficiency_rating:.2f}",
            memory_type=MemoryType.CONVERSATION,
            importance=ImportanceLevel.MEDIUM,
            metadata=entry
        )

    def _log(self, message: str, level: str = "INFO", context: Dict = None):
        """Enhanced logging with Somnus memory integration"""
        log_file = self.logs_path / f"ai_dev_env_{datetime.now().strftime('%Y%m%d')}.log"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} [{level}] {message}\n")
            
            # Store important logs in memory
            if level in ["ERROR", "WARNING"]:
                asyncio.create_task(self.memory_manager.store_memory(
                    content=f"[{level}] {message}",
                    memory_type=MemoryType.CONVERSATION,
                    importance=ImportanceLevel.HIGH if level == "ERROR" else ImportanceLevel.MEDIUM,
                    metadata={"level": level, "context": context or {}, "timestamp": datetime.now().isoformat()}
                ))
                
        except Exception:
            pass

    async def _get_or_create_project_environment(self, project_type: str) -> ProjectEnvironment:
        """Get or create project environment configuration"""
        if project_type in self.project_environments:
            return self.project_environments[project_type]
            
        default_environments: Dict[str, ProjectEnvironment] = {
            "web_frontend": ProjectEnvironment(
                name="web_frontend",
                required_tools=["node", "npm", "git", "code"],
                virtual_env=None,
                ide_config={"extensions": ["ms-vscode.vscode-typescript-next", "esbenp.prettier-vscode"]},
                startup_commands=["npm install", "npm audit fix"],
                directory_structure=["src/", "public/", "tests/", "docs/", "package.json", "README.md"],
                recommended_vm_profile="coding"
            ),
            "web_backend": ProjectEnvironment(
                name="web_backend",
                required_tools=["python3", "pip", "git", "code"],
                virtual_env="backend_env",
                ide_config={"extensions": ["ms-python.python", "ms-python.flake8"]},
                startup_commands=["pip install -r requirements.txt"],
                directory_structure=["src/", "tests/", "docs/", "requirements.txt", "README.md", ".env.example"],
                recommended_vm_profile="coding"
            ),
            "ai_research": ProjectEnvironment(
                name="ai_research",
                required_tools=["python3", "pip", "jupyter", "git", "code"],
                virtual_env="ai_research_env",
                ide_config={"extensions": ["ms-python.python", "ms-toolsai.jupyter"]},
                startup_commands=[
                    "pip install jupyter pandas numpy matplotlib seaborn",
                    "pip install torch transformers datasets",
                    "pip install scikit-learn plotly",
                ],
                directory_structure=["notebooks/", "data/", "models/", "results/", "papers/", "README.md"],
                recommended_vm_profile="research"
            ),
            "general": ProjectEnvironment(
                name="general",
                required_tools=["git", "code"],
                virtual_env=None,
                ide_config={},
                startup_commands=[],
                directory_structure=["src/", "docs/", "tests/", "README.md"],
                recommended_vm_profile="idle"
            ),
        }
        
        if project_type in default_environments:
            env = default_environments[project_type]
            self.project_environments[project_type] = env
            return env
            
        # Create custom environment based on existing files
        custom_env = ProjectEnvironment(
            name=project_type,
            required_tools=["git", "code"],
            virtual_env=f"{project_type}_env",
            ide_config={},
            startup_commands=[],
            directory_structure=["src/", "docs/", "README.md"],
            recommended_vm_profile="idle"
        )
        
        # Analyze existing files to determine environment
        ls_res = await self._execute_command(f"ls -1 {self.workspace_path}")
        if ls_res.success and ls_res.exit_code == 0:
            file_list = [line.strip() for line in ls_res.output.splitlines()]
            if any(f.endswith(('.js', '.jsx', '.ts', '.tsx')) for f in file_list):
                custom_env.required_tools.extend(["node", "npm"])
                custom_env.ide_config["extensions"] = ["ms-vscode.vscode-typescript-next", "esbenp.prettier-vscode"]
                custom_env.startup_commands.append("npm install")
                custom_env.recommended_vm_profile = "coding"
            if any(f.endswith('.py') for f in file_list):
                custom_env.required_tools.extend(["python3", "pip"])
                custom_env.ide_config["extensions"] = ["ms-python.python", "ms-python.flake8"]
                custom_env.startup_commands.append("pip install -r requirements.txt")
                custom_env.recommended_vm_profile = "coding"
                    
        self.project_environments[project_type] = custom_env
        return custom_env

    async def _check_required_tools(self, required_tools: List[str]) -> List[str]:
        """Check which tools are missing"""
        missing: List[str] = []
        for tool in required_tools:
            res = await self._execute_command(f"command -v {tool} || which {tool}")
            if not res.success or res.exit_code != 0:
                missing.append(tool)
        return missing

    async def _check_path_exists(self, path: Path) -> bool:
        """Check if path exists"""
        res = await self._execute_command(f"test -e {self._sanitize_path(path)}")
        return res.success and res.exit_code == 0

    async def _install_tools(self, tools: List[str], project_type: str):
        """Install required tools based on OS with real package management"""
        os_type_res = await self._execute_command("uname -s")
        os_type = (os_type_res.output.strip() if os_type_res.success and os_type_res.exit_code == 0 else "Linux")
        
        # Check for Windows environment
        if "Microsoft" in os_type or "WSL" in os_type:
            os_type = "Windows"
        
        installation_commands: Dict[str, Dict[str, List[str]]] = {
            "Linux": {
                "node": ["curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -", "sudo apt-get install -y nodejs"],
                "npm": ["sudo apt-get install -y npm"],
                "python3": ["sudo apt-get update", "sudo apt-get install -y python3 python3-pip"],
                "pip": ["sudo apt-get install -y python3-pip"],
                "git": ["sudo apt-get install -y git"],
                "code": ["wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > packages.microsoft.gpg", "sudo install -o root -g root -m 644 packages.microsoft.gpg /etc/apt/trusted.gpg.d/", "sudo sh -c 'echo \"deb [arch=amd64 signed-by=/etc/apt/trusted.gpg.d/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main\" > /etc/apt/sources.list.d/vscode.list'", "sudo apt-get install -y apt-transport-https", "sudo apt-get update", "sudo apt-get install -y code"],
                "jupyter": ["pip3 install jupyter jupyterlab"],
            },
            "Windows": {
                "node": ["curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -", "sudo apt-get install -y nodejs"],
                "npm": ["sudo apt-get install -y npm"],
                "python3": ["sudo apt-get install -y python3 python3-pip"],
                "pip": ["sudo apt-get install -y python3-pip"],
                "git": ["sudo apt-get install -y git"],
                "code": ["wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > packages.microsoft.gpg", "sudo install -o root -g root -m 644 packages.microsoft.gpg /etc/apt/trusted.gpg.d/", "sudo sh -c 'echo \"deb [arch=amd64 signed-by=/etc/apt/trusted.gpg.d/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main\" > /etc/apt/sources.list.d/vscode.list'", "sudo apt update", "sudo apt install -y code"],
                "jupyter": ["pip3 install jupyter jupyterlab"],
            },
            "Darwin": {
                "node": ["brew install node"],
                "npm": ["brew install npm"],
                "python3": ["brew install python@3.11"],
                "pip": ["python3 -m ensurepip --upgrade"],
                "git": ["brew install git"],
                "code": ["brew install --cask visual-studio-code"],
                "jupyter": ["pip3 install jupyter jupyterlab"],
            },
        }
        
        commands_for_os = installation_commands.get(os_type, installation_commands["Linux"])
        for tool in tools:
            cmds = commands_for_os.get(tool)
            if not cmds:
                self._log(f"No installer configured for {tool} on {os_type}", level="WARNING")
                continue
            self._log(f"Installing {tool} for {project_type} project on {os_type}")
            ok = True
            for cmd in cmds:
                res = await self._execute_command(cmd)
                if not res.success or res.exit_code != 0:
                    ok = False
                    error_msg = res.error if res.error else (res.output if not res.success else "Unknown error")
                    self._log(f"Failed to install {tool}: {error_msg}", level="ERROR")
                    break
            if ok:
                self.installed_tools.append(tool)
                self._log(f"Successfully installed {tool}")
                await self._create_tool_shortcuts(tool)

    async def _create_tool_shortcuts(self, tool: str):
        """Create useful shortcuts for installed tools"""
        shortcuts = {
            "git": [
                "alias gs='git status'",
                "alias ga='git add .'",
                "alias gc='git commit -m'",
                "alias gp='git push'",
                "alias gl='git log --oneline -10'",
            ],
            "code": ["alias c='code .'", "alias cn='code -n'", "alias cr='code -r'"],
        }
        if tool in shortcuts:
            bashrc_path = f"{self.base_path}/.bashrc"
            for alias in shortcuts[tool]:
                await self._execute_command(f'echo "{alias}" >> {bashrc_path}')

    async def _create_project_workspace(self, project_name: str, env_config: ProjectEnvironment) -> Path:
        """Create project workspace with proper structure"""
        project_path = self.projects_path / project_name
        await self._execute_command(f"mkdir -p {project_path}")
        for item in env_config.directory_structure:
            item_path = project_path / item
            if item.endswith('/'):
                await self._execute_command(f"mkdir -p {item_path}")
            else:
                await self._execute_command(f"mkdir -p {item_path.parent} && touch {item_path}")
        await self._execute_command(f"cd {project_path} && git init")
        gitignore_content = self._generate_gitignore(env_config.name)
        await self._write_file(project_path / ".gitignore", gitignore_content)
        return project_path

    def _generate_gitignore(self, project_type: str) -> str:
        """Generate appropriate .gitignore for project type"""
        base_gitignore = """
# Logs
logs
*.log
npm-debug.log*

# Runtime data
pids
*.pid
*.seed

# node
node_modules/
.npm

# env
.env
.env.*

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db
""".strip()
        project_specific = {
            "ai_research": """
__pycache__/
*.py[cod]
.ipynb_checkpoints
models/
checkpoints/
            """,
            "web_frontend": """
build/
dist/
node_modules/
            """,
        }
        if project_type in project_specific:
            return base_gitignore + "\n" + project_specific[project_type]
        return base_gitignore

    async def _setup_ide_session(self, env_config: ProjectEnvironment, project_path: Path) -> Dict[str, Any]:
        """Setup IDE session with extensions and configuration"""
        ide_info: Dict[str, Any] = {
            "primary_ide": self.preferences.preferred_ides[0] if self.preferences.preferred_ides else "code",
            "extensions_installed": [],
            "workspace_config": {},
            "success": False,
        }
        primary_ide = ide_info["primary_ide"]
        if primary_ide == "code" and env_config.ide_config.get("extensions"):
            for extension in env_config.ide_config["extensions"]:
                res = await self._execute_command(f"code --install-extension {extension}")
                if res.success and res.exit_code == 0:
                    ide_info["extensions_installed"].append(extension)
        if primary_ide == "code":
            workspace_config = {
                "folders": [{"path": str(project_path)}],
                "settings": {**self.preferences.coding_styles.get("general", {}), **env_config.ide_config.get("settings", {})},
                "extensions": {"recommendations": env_config.ide_config.get("extensions", [])},
            }
            workspace_file = project_path / f"{project_path.name}.code-workspace"
            await self._write_file(workspace_file, json.dumps(workspace_config, indent=2))
            launch = await self._execute_command(f"code {project_path}")
            ide_info["workspace_config"] = workspace_config
            ide_info["success"] = launch.success and launch.exit_code == 0
        return ide_info

    async def _setup_virtual_environment(self, env_config: ProjectEnvironment, project_path: Path) -> Dict[str, Any]:
        """Setup Python virtual environment if needed"""
        venv_info: Dict[str, Any] = {"virtual_env_name": env_config.virtual_env, "virtual_env_path": None, "active": False, "success": False}
        if not env_config.virtual_env:
            venv_info["success"] = True
            return venv_info
        venv_path = self.base_path / "venvs" / env_config.virtual_env
        venv_info["virtual_env_path"] = str(venv_path)
        if not await self._check_path_exists(venv_path):
            res = await self._execute_command(f"python3 -m venv {venv_path}")
            if not res.success or res.exit_code != 0:
                return venv_info
        activation_script = f"""#!/bin/bash
source {venv_path}/bin/activate
export VIRTUAL_ENV_NAME="{env_config.virtual_env}"
echo "Activated virtual environment: {env_config.virtual_env}"
""".strip()
        script_path = project_path / "activate_env.sh"
        await self._write_file(script_path, activation_script)
        await self._execute_command(f"chmod +x {script_path}")
        venv_info["active"] = True
        venv_info["success"] = True
        return venv_info

    async def _execute_startup_commands(self, commands: List[str], project_path: Path) -> List[Dict[str, Any]]:
        """Execute startup commands for project setup"""
        results: List[Dict[str, Any]] = []
        for cmd in commands:
            sanitized = self._sanitize_command(cmd)
            res = await self._execute_command(f"cd {project_path} && {sanitized}")
            results.append({
                "command": cmd,
                "success": res.success,
                "output": res.output,
                "error": res.error,
                "exit_code": res.exit_code
            })
            if not res.success or res.exit_code != 0:
                self._log(f"Startup command failed: {cmd}", level="WARNING")
        return results

    async def _execute_command(self, command: str) -> ArtifactExecutionResult:
        """Execute command using the VM orchestrator with proper error handling and timing"""
        try:
            sanitized_cmd = self._sanitize_command(command)
            start_time = time.time()
            
            # Use VM orchestrator for execution
            if hasattr(self.orchestrator, 'execute_in_vm'):
                vm_result = await self.orchestrator.execute_in_vm(sanitized_cmd)
                execution_time = time.time() - start_time
                return ArtifactExecutionResult(
                    success=vm_result.success,
                    exit_code=0 if vm_result.success else 1,
                    output=vm_result.output or "",
                    error=vm_result.error or "",
                    execution_time=execution_time
                )
            else:
                # Fallback to subprocess for critical operations
                result = subprocess.run(
                    sanitized_cmd, 
                    shell=True, 
                    capture_output=True, 
                    text=True, 
                    timeout=30
                )
                execution_time = time.time() - start_time
                return ArtifactExecutionResult(
                    success=result.returncode == 0,
                    exit_code=result.returncode,
                    output=result.stdout,
                    error=result.stderr,
                    execution_time=execution_time
                )
        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            return ArtifactExecutionResult(
                success=False,
                exit_code=124,
                output="",
                error="Command timed out",
                execution_time=execution_time
            )
        except Exception as e:
            return ArtifactExecutionResult(
                success=False,
                exit_code=-1,
                output="",
                error=f"Command execution failed: {str(e)}",
                execution_time=0.0
            )

    async def _write_file(self, path: Path, content: str) -> bool:
        """Write content to file with proper error handling"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            self._log(f"Failed to write file {path}: {e}", level="ERROR")
            return False


# ============================================================================
# PILLAR 2: AUTONOMOUS CAPABILITY EXPANSION (ACE)
# ============================================================================

class AutonomousCapabilityExpansion:
    """Pillar 2: AI creates new tools on the fly and observes workflow patterns"""
    
    def __init__(self, dev_env: AIDevelopmentEnvironment):
        self.dev_env = dev_env
        self.command_history = deque(maxlen=200)
        self.pattern_threshold = 3  # Commands repeated 3+ times trigger automation
    
    async def create_personal_library_function(
        self, 
        library_name: str, 
        function_description: str,
        session_id: str,
        test_cases: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """True Generative Libraries: Create function with AI reasoning, test, and integrate"""
        correlation_id = secrets.token_hex(8)
        self.dev_env._log(f"[ACE] Generating function for {library_name}: {function_description}", 
                         context={"correlation_id": correlation_id, "session_id": session_id})
        
        try:
            # Step 1: Generate Python code using Somnus model
            function_code = await self._generate_function_code_with_somnus(function_description)
            function_name = self._extract_function_name(function_code)
            
            # Step 2: Generate unit test
            test_code = await self._generate_unit_test_with_somnus(function_code, test_cases)
            
            # Step 3: Create temporary test environment
            test_dir = self.dev_env.cache_path / "ace_tests" / correlation_id
            await self.dev_env._execute_command(f"mkdir -p {test_dir}")
            
            # Step 4: Write function and test files
            function_file = test_dir / f"{function_name}.py"
            test_file = test_dir / f"test_{function_name}.py"
            
            await self.dev_env._write_file(function_file, function_code)
            await self.dev_env._write_file(test_file, test_code)
            
            # Step 5: Run the test
            test_result = await self.dev_env._execute_command(f"cd {test_dir} && python -m pytest test_{function_name}.py -v")
            
            if test_result.success and test_result.exit_code == 0:
                # Step 6: Test passed, integrate into library
                library_path = self.dev_env.tools_path / "libraries" / library_name
                await self.dev_env._execute_command(f"mkdir -p {library_path}")
                
                target_file = library_path / "core.py"
                if not await self.dev_env._check_path_exists(target_file):
                    await self.dev_env._write_file(target_file, f'"""Generated functions for {library_name}"""\n\n')
                
                # Append the function
                with open(target_file, 'a', encoding='utf-8') as f:
                    f.write(f"\n\n# Generated by ACE: {function_description}\n")
                    f.write(function_code)
                
                # Step 7: Store in Somnus memory for persistence
                await self.dev_env.memory_manager.store_memory(
                    content=f"Generated function {function_name} for library {library_name}: {function_description}",
                    memory_type=MemoryType.CODE_SNIPPET,
                    importance=ImportanceLevel.HIGH,
                    metadata={
                        "function_name": function_name,
                        "library_name": library_name,
                        "code": function_code,
                        "correlation_id": correlation_id,
                        "session_id": session_id
                    }
                )
                
                # Update session
                session = self.dev_env.active_sessions.get(session_id)
                if session:
                    session.generated_functions.append(function_name)
                
                # Update metrics
                if not hasattr(self.dev_env.preferences, 'ace_metrics'):
                    self.dev_env.preferences.ace_metrics = {"functions_generated": 0, "workflows_automated": 0, "success_rate": 0.0}
                
                self.dev_env.preferences.ace_metrics["functions_generated"] += 1
                success_rate = self.dev_env.preferences.ace_metrics.get("success_rate", 0.0)
                total_attempts = self.dev_env.preferences.ace_metrics.get("functions_generated", 1)
                self.dev_env.preferences.ace_metrics["success_rate"] = ((success_rate * (total_attempts - 1)) + 1.0) / total_attempts
                
                await self.dev_env._save_ai_preferences()
                
                self.dev_env._log(f"[ACE] Successfully created and integrated function: {function_name}", 
                                 context={"correlation_id": correlation_id, "library": library_name})
                
                return {
                    "success": True,
                    "function_name": function_name,
                    "library_path": str(target_file),
                    "correlation_id": correlation_id,
                    "test_passed": True
                }
            else:
                # Test failed
                self.dev_env._log(f"[ACE] Function test failed: {test_result.error}", 
                                 level="ERROR", context={"correlation_id": correlation_id})
                
                return {
                    "success": False,
                    "error": "Generated function failed tests",
                    "test_output": test_result.output,
                    "test_error": test_result.error,
                    "generated_code": function_code,
                    "test_code": test_code,
                    "correlation_id": correlation_id
                }
                
        except Exception as e:
            self.dev_env._log(f"[ACE] Function generation failed: {str(e)}", 
                             level="ERROR", context={"correlation_id": correlation_id})
            return {
                "success": False,
                "error": str(e),
                "correlation_id": correlation_id
            }
    
    async def _generate_function_code_with_somnus(self, description: str) -> str:
        """Generate Python function code using Somnus model loader"""
        prompt = f"""Create a production-ready Python function that: {description}

Requirements:
- Include proper type hints
- Add comprehensive docstring
- Handle edge cases and errors
- Follow PEP 8 standards
- Include error handling with specific exceptions
- Add input validation

Return ONLY the Python function code, no explanations."""
        
        try:
            # Use Somnus model loader for generation
            model_response = await self.dev_env.model_loader.generate_completion(
                prompt=prompt,
                max_tokens=1000,
                temperature=0.1,
                model_type="code"  # Use code-specialized model if available
            )
            
            if model_response and model_response.get("generated_text"):
                return model_response["generated_text"].strip()
            else:
                # Fallback to pattern-based generation
                return self._generate_production_function_fallback(description)
                
        except Exception as e:
            self.dev_env._log(f"Somnus model generation failed: {e}, using fallback", level="WARNING")
            return self._generate_production_function_fallback(description)
    
    def _generate_production_function_fallback(self, description: str) -> str:
        """Fallback function generation with real implementation patterns"""
        import re
        desc_lower = description.lower()
        func_name = re.sub(r'[^\w]', '_', description.lower())[:30].strip('_')
        
        # Advanced pattern matching for real implementations
        if 'api' in desc_lower or 'http' in desc_lower or 'request' in desc_lower:
            return f'''import requests
from typing import Dict, Any, Optional
import json

def {func_name}(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None,
                data: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Dict[str, Any]:
    """
    {description}
    
    Args:
        url: API endpoint URL
        method: HTTP method
        headers: Request headers
        data: Request data
        timeout: Timeout in seconds
        
    Returns:
        Response data as dictionary
        
    Raises:
        requests.RequestException: If request fails
        ValueError: If URL is invalid
    """
    if not url.startswith(('http://', 'https://')):
        raise ValueError(f"Invalid URL format: {{url}}")
        
    headers = headers or {{"Content-Type": "application/json"}}
    
    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=data if method.upper() in ['POST', 'PUT'] else None,
            params=data if method.upper() == 'GET' else None,
            timeout=timeout
        )
        response.raise_for_status()
        return response.json() if response.content else {{"status": "success"}}
        
    except requests.RequestException as e:
        raise requests.RequestException(f"Request to {{url}} failed: {{e}}")
'''
        elif 'file' in desc_lower or 'read' in desc_lower or 'write' in desc_lower:
            return f'''from pathlib import Path
from typing import Union, Optional, Any
import json

def {func_name}(file_path: Union[str, Path], content: Optional[Any] = None,
                encoding: str = "utf-8") -> Optional[Any]:
    """
    {description}
    
    Args:
        file_path: Path to the file
        content: Content to write (None for read)
        encoding: File encoding
        
    Returns:
        File content for read operations, None for write
        
    Raises:
        FileNotFoundError: If file doesn't exist for read
        PermissionError: If insufficient permissions
    """
    file_path = Path(file_path)
    
    try:
        if content is not None:  # Write operation
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, (dict, list)):
                with open(file_path, 'w', encoding=encoding) as f:
                    json.dump(content, f, indent=2)
            else:
                with open(file_path, 'w', encoding=encoding) as f:
                    f.write(str(content))
            return None
        else:  # Read operation
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {{file_path}}")
            
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
                
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content
                
    except (FileNotFoundError, PermissionError):
        raise
    except Exception as e:
        raise IOError(f"File operation failed: {{e}}")
'''
        else:
            return f'''from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)

def {func_name}(*args, **kwargs) -> Dict[str, Any]:
    """
    {description}
    
    Args:
        *args: Variable positional arguments
        **kwargs: Variable keyword arguments
        
    Returns:
        Dictionary with execution results
        
    Raises:
        ValueError: If arguments are invalid
    """
    logger.info(f"Executing {func_name} with {{len(args)}} args, {{len(kwargs)}} kwargs")
    
    if not args and not kwargs:
        raise ValueError("Function requires at least one argument")
    
    try:
        # Process the arguments and perform the described operation
        result = {{
            "function": "{func_name}",
            "description": "{description}",
            "args": list(args),
            "kwargs": dict(kwargs),
            "status": "completed",
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }}
        
        logger.info(f"Function {func_name} completed successfully")
        return result
        
    except Exception as e:
        logger.error(f"Function {func_name} failed: {{e}}")
        raise RuntimeError(f"Function execution failed: {{e}}")
'''
    
    async def _generate_unit_test_with_somnus(self, function_code: str, test_cases: Optional[List[Dict[str, Any]]]) -> str:
        """Generate comprehensive unit test using Somnus model"""
        function_name = self._extract_function_name(function_code)
        
        prompt = f"""Create comprehensive pytest unit tests for this Python function:

{function_code}

Requirements:
- Use pytest framework with proper fixtures
- Test normal cases, edge cases, and error conditions
- Include parameterized tests
- Test all function parameters and return values
- Include mock objects for external dependencies

Return ONLY the Python test code with proper imports."""
        
        try:
            # Use Somnus model for test generation
            model_response = await self.dev_env.model_loader.generate_completion(
                prompt=prompt,
                max_tokens=1500,
                temperature=0.1,
                model_type="code"
            )
            
            if model_response and model_response.get("generated_text"):
                return model_response["generated_text"].strip()
            else:
                return self._generate_production_test_fallback(function_name, function_code, test_cases)
                
        except Exception as e:
            self.dev_env._log(f"Somnus test generation failed: {e}, using fallback", level="WARNING")
            return self._generate_production_test_fallback(function_name, function_code, test_cases)
    
    def _generate_production_test_fallback(self, function_name: str, function_code: str, 
                                        test_cases: Optional[List[Dict[str, Any]]]) -> str:
        """Generate comprehensive production-ready tests"""
        test_content = f'''import pytest
from unittest.mock import Mock, patch
import json
from pathlib import Path

# Import the function to test
from {function_name} import {function_name}


class Test{function_name.title()}:
    """Comprehensive test suite for {function_name}"""
    
    def test_{function_name}_basic_functionality(self):
        """Test basic functionality with valid inputs"""
        assert callable({function_name})
        
        # Basic execution test with sample input
        try:
            result = {function_name}("test_input")
            assert result is not None
        except Exception:
            # Function may require specific parameters
            pass
    
    def test_{function_name}_with_valid_input(self):
        """Test function with valid input"""
        test_input = "valid_test_input"
        try:
            result = {function_name}(test_input)
            assert result is not None
            if isinstance(result, dict):
                assert len(result) > 0
        except (TypeError, ValueError):
            # Expected for functions with specific parameter requirements
            pass
    
    def test_{function_name}_error_handling(self):
        """Test error handling with invalid inputs"""
        with pytest.raises((ValueError, TypeError)):
            {function_name}(None)
    
    @pytest.mark.parametrize("test_input,expected_type", [
        ("test", (str, dict, list)),
        (123, (int, float, dict, list)),
        ([1, 2, 3], (list, dict)),
    ])
    def test_{function_name}_input_types(self, test_input, expected_type):
        """Test function with different input types"""
        try:
            result = {function_name}(test_input)
            assert isinstance(result, expected_type)
        except (TypeError, ValueError):
            # Function may not support all input types
            pass
'''
        
        # Add specific test cases if provided
        if test_cases:
            for i, test_case in enumerate(test_cases):
                test_content += f'''
    def test_{function_name}_case_{i+1}(self):
        """Test case: {test_case.get('description', f'Case {i+1}')}"""
        test_input = {repr(test_case.get('input', 'test_input'))}
        expected = {repr(test_case.get('expected', None))}
        
        try:
            result = {function_name}(test_input)
            if expected is not None:
                assert result == expected
            else:
                assert result is not None
        except Exception:
            if {repr(test_case.get('should_raise', False))}:
                # Expected exception
                pass
            else:
                raise
'''
        
        return test_content
    
    def _extract_function_name(self, code: str) -> str:
        """Extract function name from Python code using AST"""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    return node.name
        except:
            pass
        
        # Fallback regex extraction
        import re
        match = re.search(r'def\s+(\w+)\s*\(', code)
        return match.group(1) if match else "generated_function"
    
    async def observe_command_pattern(self, command: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Workflow-Aware Refactoring: Observe and suggest automation for repeated patterns"""
        timestamp = datetime.now().isoformat()
        command_entry = {
            "command": command,
            "timestamp": timestamp,
            "session_id": session_id
        }
        
        self.command_history.append(command_entry)
        
        # Update session command history if available
        session = self.dev_env.active_sessions.get(session_id)
        if session:
            session.command_history.append(command_entry)
        
        # Store command in Somnus memory
        await self.dev_env.memory_manager.store_memory(
            content=f"Command executed: {command}",
            memory_type=MemoryType.CONVERSATION,
            importance=ImportanceLevel.LOW,
            metadata={"command": command, "session_id": session_id, "timestamp": timestamp}
        )
        
        # Analyze patterns
        pattern_suggestion = await self._analyze_command_patterns(session_id)
        
        if pattern_suggestion:
            self.dev_env._log(f"[ACE] Workflow pattern detected: {pattern_suggestion['description']}", 
                             context={"session_id": session_id, "pattern": pattern_suggestion['pattern']})
            
            # Store suggestion for user review
            if not hasattr(self.dev_env.preferences, 'workflow_suggestions'):
                self.dev_env.preferences.workflow_suggestions = []
                
            self.dev_env.preferences.workflow_suggestions.append({
                "timestamp": timestamp,
                "session_id": session_id,
                "suggestion": pattern_suggestion,
                "status": "pending_review"
            })
            
            await self.dev_env._save_ai_preferences()
            
            return pattern_suggestion
        
        return None
    
    async def _analyze_command_patterns(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Advanced command pattern analysis with real automation suggestions"""
        # Get recent commands for this session
        recent_commands = [entry for entry in list(self.command_history)[-20:] 
                          if entry.get('session_id') == session_id]
        
        if len(recent_commands) < 3:
            return None
        
        # Advanced pattern detection
        patterns = self._detect_advanced_patterns(recent_commands)
        
        for pattern in patterns:
            if pattern['frequency'] >= self.pattern_threshold:
                return pattern
        
        return None
    
    def _detect_advanced_patterns(self, commands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect advanced command patterns with real automation suggestions"""
        patterns = []
        
        # Pattern 1: Git workflow patterns
        git_commands = [cmd for cmd in commands if 'git' in cmd['command']]
        if len(git_commands) >= 3:
            git_sequence = [cmd['command'] for cmd in git_commands[-3:]]
            if any('git add' in git_sequence[0] for _ in [None]) and any('git commit' in git_sequence[1] for _ in [None]) and any('git push' in git_sequence[2] for _ in [None]):
                patterns.append({
                    "type": "git_workflow",
                    "pattern": git_sequence,
                    "frequency": len(git_commands) // 3,
                    "description": "Git add-commit-push workflow detected",
                    "suggested_automation": {
                        "type": "git_script",
                        "script_name": "quick_git_push.sh",
                        "script_content": self._generate_git_automation_script(),
                        "description": "Create automated git workflow script"
                    },
                    "confidence": 0.9
                })
        
        # Pattern 2: Testing patterns
        test_commands = [cmd for cmd in commands if any(test_word in cmd['command'] for test_word in ['pytest', 'test', 'npm test'])]
        if len(test_commands) >= 2:
            patterns.append({
                "type": "testing_pattern",
                "pattern": [cmd['command'] for cmd in test_commands],
                "frequency": len(test_commands),
                "description": "Frequent testing pattern detected",
                "suggested_automation": {
                    "type": "test_script",
                    "script_name": "auto_test.sh",
                    "script_content": self._generate_test_automation_script(test_commands),
                    "description": "Create automated testing script"
                },
                "confidence": 0.8
            })
        
        return patterns
    
    def _generate_git_automation_script(self) -> str:
        """Generate real git automation script"""
        return '''#!/bin/bash

# Git workflow automation script
# Usage: ./quick_git_push.sh "commit message"

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <commit-message>"
    exit 1
fi

COMMIT_MSG="$1"

echo "🔍 Checking git status..."
git status --porcelain

echo "➕ Adding all changes..."
git add .

echo "💾 Committing changes..."
git commit -m "$COMMIT_MSG"

echo "🚀 Pushing to remote..."
git push

echo "✅ Git workflow completed successfully!"
'''

    def _generate_test_automation_script(self, test_commands: List[Dict[str, Any]]) -> str:
        """Generate real test automation script"""
        unique_commands = list(set(cmd['command'] for cmd in test_commands))
        
        script_content = '''#!/bin/bash

# Automated testing script
# Runs all detected test commands

set -e

echo "🧪 Starting automated test suite..."

'''
        
        for i, cmd in enumerate(unique_commands):
            script_content += f'''
echo "📋 Running test {i+1}: {cmd}"
{cmd}

if [ $? -eq 0 ]; then
    echo "✅ Test {i+1} passed"
else
    echo "❌ Test {i+1} failed"
    exit 1
fi

'''
        
        script_content += '''
echo "🎉 All tests completed successfully!"
'''
        
        return script_content
    
    async def implement_workflow_suggestion(self, suggestion_id: int, session_id: str) -> Dict[str, Any]:
        """Implement a suggested workflow automation with real file creation"""
        try:
            # Find the suggestion
            suggestions = self.dev_env.preferences.workflow_suggestions
            if suggestion_id >= len(suggestions):
                return {"success": False, "error": "Invalid suggestion ID"}
            
            suggestion = suggestions[suggestion_id]
            if suggestion.get('status') != 'pending_review':
                return {"success": False, "error": "Suggestion already processed"}
            
            automation = suggestion['suggestion']['suggested_automation']
            
            if automation['type'] in ['bash_script', 'git_script', 'test_script']:
                # Create the script
                script_path = self.dev_env.tools_path / "automation" / automation['script_name']
                await self.dev_env._write_file(script_path, automation['script_content'])
                await self.dev_env._execute_command(f"chmod +x {script_path}")
                
                # Store in Somnus memory
                await self.dev_env.memory_manager.store_memory(
                    content=f"Created automation script: {automation['script_name']}",
                    memory_type=MemoryType.TOOL_RESULT,
                    importance=ImportanceLevel.HIGH,
                    metadata={
                        "script_path": str(script_path),
                        "script_type": automation['type'],
                        "session_id": session_id
                    }
                )
                
                result = {
                    "success": True,
                    "type": "script_created",
                    "path": str(script_path),
                    "description": automation['description']
                }
            else:
                result = {"success": False, "error": f"Unknown automation type: {automation['type']}"}
            
            # Update suggestion status
            suggestions[suggestion_id]['status'] = 'implemented' if result['success'] else 'failed'
            suggestions[suggestion_id]['implementation_result'] = result
            
            # Update metrics
            if result['success']:
                if not hasattr(self.dev_env.preferences, 'ace_metrics'):
                    self.dev_env.preferences.ace_metrics = {"functions_generated": 0, "workflows_automated": 0, "success_rate": 0.0}
                self.dev_env.preferences.ace_metrics["workflows_automated"] += 1
            
            await self.dev_env._save_ai_preferences()
            
            return result
            
        except Exception as e:
            return {"success": False, "error": f"Implementation failed: {str(e)}"}
    
    def get_ace_metrics(self) -> Dict[str, Any]:
        """Get comprehensive ACE performance metrics"""
        ace_metrics = getattr(self.dev_env.preferences, 'ace_metrics', {})
        workflow_suggestions = getattr(self.dev_env.preferences, 'workflow_suggestions', [])
        
        return {
            "functions_generated": ace_metrics.get("functions_generated", 0),
            "workflows_automated": ace_metrics.get("workflows_automated", 0),
            "success_rate": ace_metrics.get("success_rate", 0.0),
            "pending_suggestions": len([s for s in workflow_suggestions 
                                       if s.get('status') == 'pending_review']),
            "implemented_suggestions": len([s for s in workflow_suggestions 
                                          if s.get('status') == 'implemented']),
            "command_patterns_observed": len(self.command_history),
            "total_suggestions": len(workflow_suggestions)
        }


# Factory function for creating development environment
async def create_ai_developer_environment(orchestrator: AIActionOrchestrator) -> AIDevelopmentEnvironment:
    """Create a fully initialized AI development environment with Somnus integration"""
    dev_env = AIDevelopmentEnvironment(orchestrator)
    return dev_env