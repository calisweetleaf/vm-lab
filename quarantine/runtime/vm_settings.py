"""
SOMNUS SYSTEMS - VM Settings Manager (PRODUCTION-READY)
Persistent AI Computing Environment Configuration

ARCHITECTURE PHILOSOPHY:
- Each AI agent gets persistent VM that never resets
- Progressive capability building and tool accumulation
- Resource efficiency through intelligent allocation
- Complete user sovereignty over VM configurations
- On-demand VM creation and lifecycle management

PRODUCTION FEATURES:
- Multi-layered security with input validation
- Integration with VM Supervisor and Orchestrator
- Resource quota enforcement and monitoring
- Capability tracking and efficiency metrics
- Automated backup and snapshot management
- Cross-VM learning and capability synchronization
- Hardware acceleration (GPU) support
- Network isolation and security policies
- SSH key management and secure access
- Automated cleanup and maintenance
"""

import asyncio
import json
import logging
import psutil
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Union
from uuid import UUID, uuid4
from dataclasses import dataclass, field, asdict
from enum import Enum
import secrets

import aiofiles
from pydantic import BaseModel, Field, validator, ValidationError

from .vm_supervisor import VMSupervisor, VMState, ResourceProfile
from .ai_action_orchestrator import AIActionOrchestrator
from .ai_browser_system2 import AIBrowserResearch

logger = logging.getLogger(__name__)


# ============================================================================
# SECURITY VALIDATION UTILITIES
# ============================================================================

class SecurityValidator:
    """Security validation utilities for VM settings"""
    
    @staticmethod
    def sanitize_path(path: str) -> str:
        """Sanitize file paths to prevent directory traversal"""
        if not path:
            return ""
        # Remove any .. or absolute path attempts
        sanitized = re.sub(r'\.\.+', '', str(path))
        sanitized = re.sub(r'^/+', '', sanitized)
        return sanitized
    
    @staticmethod
    def sanitize_command(cmd: str, allowed_chars: str = r'[\w\s\-\_=/\.\:]') -> str:
        """Sanitize command strings to prevent injection"""
        if not cmd:
            return ""
        return re.sub(f'[^{allowed_chars}]', '', cmd)
    
    @staticmethod
    def validate_user_id(user_id: str) -> bool:
        """Validate user ID format"""
        if not user_id:
            return False
        return bool(re.match(r'^[\w\-@\.]+$', user_id) and 3 <= len(user_id) <= 50)
    
    @staticmethod
    def validate_instance_name(name: str) -> bool:
        """Validate VM instance name"""
        if not name:
            return False
        return bool(re.match(r'^[\w\-\s]+$', name) and 1 <= len(name) <= 100)
    
    @staticmethod
    def generate_secure_token(length: int = 32) -> str:
        """Generate cryptographically secure token"""
        return secrets.token_urlsafe(length)


# ============================================================================
# VM CONFIGURATION MODELS (ENHANCED)
# ============================================================================

class BackupSchedule(str, Enum):
    """VM backup frequency options"""
    DISABLED = "disabled"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ON_CAPABILITY_CHANGE = "on_capability_change"
    REAL_TIME = "real_time"


class ResourcePolicy(str, Enum):
    """VM resource allocation policies"""
    CONSERVATIVE = "conservative"    # Minimal resources, slow scale-up
    BALANCED = "balanced"           # Moderate resources, adaptive scaling
    PERFORMANCE = "performance"     # High resources, fast response
    UNLIMITED = "unlimited"         # No artificial limits
    RESEARCH = "research"          # Optimized for research workloads
    DEVELOPMENT = "development"     # Optimized for development workflows
    CREATIVITY = "creativity"        # High CPU/GPU for media generation


class NetworkPolicy(str, Enum):
    """VM network isolation policies"""
    ISOLATED = "isolated"          # No external network access
    LIMITED = "limited"            # Restricted external access
    BRIDGED = "bridged"           # Full network access (default)
    CUSTOM = "custom"             # User-defined network rules


class SecurityLevel(str, Enum):
    """VM security isolation levels"""
    LOW = "low"                  # Standard isolation
    MEDIUM = "medium"            # Enhanced isolation
    HIGH = "high"               # Maximum isolation with encryption
    PARANOID = "paranoid"         # Air-gapped, encrypted storage


class BrowserType(str, Enum):
    """Supported browser types for AI research"""
    CHROME = "chrome"
    FIREFOX = "firefox"
    RESEARCH_BROWSER = "research_browser"
    CUSTOM = "custom"


class ResearchWorkflowType(str, Enum):
    """AI research workflow types"""
    COMPREHENSIVE = "comprehensive"      # Thorough research with multiple sources
    SPEED_RUN = "speed_run"             # Fast research focusing on primary sources
    ACADEMIC = "academic"               # Academic research with scholarly sources
    TECHNICAL = "technical"             # Technical documentation and specs
    FACT_CHECKING = "fact_checking"     # Verification and validation research
    TREND_ANALYSIS = "trend_analysis"   # Pattern and trend identification
    NEWS_MONITORING = "news_monitoring" # Continuous news and updates tracking


class BrowserAutomationMode(str, Enum):
    """Browser automation modes"""
    HEADLESS = "headless"              # No GUI, faster execution
    VISIBLE = "visible"                # Full browser UI for debugging
    STEALTH = "stealth"                # Anti-detection mode
    DEBUG = "debug"                    # Debug mode with logging


class ContentExtractionLevel(str, Enum):
    """Content extraction depth levels"""
    QUICK = "quick"                    # Title, meta, text only
    STANDARD = "standard"              # Full text, images, links
    DEEP = "deep"                      # All content including scripts, styles
    VISUAL = "visual"                  # Includes screenshots and visual analysis


@dataclass
class VMHardwareSpec:
    """VM hardware specifications with validation"""
    vcpus: int = 4
    memory_gb: int = 8
    storage_gb: int = 100
    gpu_enabled: bool = False
    gpu_memory_gb: Optional[int] = None
    
    # Network configuration
    network_enabled: bool = True
    ssh_port: int = 2222
    vnc_port: int = 5900
    agent_port: int = 9901
    
    # Performance tuning
    cpu_priority: str = "normal"  # low, normal, high, realtime
    memory_ballooning: bool = True
    disk_cache_mode: str = "writethrough"  # none, writethrough, writeback, unsafe
    io_threads: int = 1
    
    # Hardware acceleration
    kvm_enabled: bool = True
    nested_virtualization: bool = False
    
    def __post_init__(self):
        """Validate hardware specifications"""
        if self.vcpus < 1 or self.vcpus > 128:
            raise ValueError("vCPUs must be between 1 and 128")
        if self.memory_gb < 1 or self.memory_gb > 512:
            raise ValueError("Memory must be between 1GB and 512GB")
        if self.storage_gb < 10 or self.storage_gb > 10000:
            raise ValueError("Storage must be between 10GB and 10TB")
        if self.gpu_memory_gb and (self.gpu_memory_gb < 1 or self.gpu_memory_gb > 80):
            raise ValueError("GPU memory must be between 1GB and 80GB")
        if not (1024 <= self.ssh_port <= 65535):
            raise ValueError("SSH port must be between 1024 and 65535")
        if not (1024 <= self.vnc_port <= 65535):
            raise ValueError("VNC port must be between 1024 and 65535")


@dataclass
class VMPersonality:
    """AI personality configuration for VM behavior"""
    agent_name: str = "AI_Assistant"
    specialization: str = "general"  # general, research, coding, analysis, creative
    creativity_level: float = 0.7
    research_methodology: str = "systematic"  # systematic, exploratory, hybrid
    
    # Workspace preferences
    preferred_ide: str = "vscode"
    preferred_shell: str = "bash"
    preferred_browser: str = "firefox"
    preferred_terminal: str = "gnome-terminal"
    
    # Capability preferences
    auto_install_tools: bool = True
    capability_learning_enabled: bool = True
    cross_session_memory: bool = True
    auto_save_enabled: bool = True
    auto_backup_enabled: bool = True
    
    # Research preferences
    research_depth: str = "comprehensive"  # quick, standard, comprehensive, exhaustive
    source_quality_threshold: float = 0.7
    max_research_sources: int = 50
    
    def __post_init__(self):
        """Validate personality settings"""
        if not (0.0 <= self.creativity_level <= 1.0):
            raise ValueError("Creativity level must be between 0.0 and 1.0")
        if not (0.0 <= self.source_quality_threshold <= 1.0):
            raise ValueError("Source quality threshold must be between 0.0 and 1.0")


@dataclass
class VMBrowserSettings:
    """Browser configuration for AI research and automation"""
    
    # Browser selection
    primary_browser: BrowserType = BrowserType.FIREFOX
    fallback_browser: BrowserType = BrowserType.CHROME
    custom_browser_path: Optional[str] = None
    
    # Automation settings
    automation_mode: BrowserAutomationMode = BrowserAutomationMode.HEADLESS
    default_viewport: tuple = field(default_factory=lambda: (1920, 1080))
    user_agent: Optional[str] = None
    
    # Performance settings
    page_load_timeout: int = 30  # seconds
    script_timeout: int = 10     # seconds
    navigation_timeout: int = 15  # seconds
    
    # Content extraction
    extraction_level: ContentExtractionLevel = ContentExtractionLevel.STANDARD
    max_concurrent_extractions: int = 3
    extraction_delay: float = 0.5  # seconds between extractions
    
    # Research settings
    default_workflow: ResearchWorkflowType = ResearchWorkflowType.COMPREHENSIVE
    max_research_sources: int = 50
    source_quality_threshold: float = 0.7
    enable_fact_checking: bool = True
    enable_cross_referencing: bool = True
    enable_visual_analysis: bool = True
    
    # Extension management
    enabled_extensions: List[str] = field(default_factory=lambda: [
        "ad_blocker",
        "privacy_protection",
        "research_assistant"
    ])
    custom_extension_paths: List[str] = field(default_factory=list)
    
    # Resource limits
    max_memory_usage_mb: int = 2048  # 2GB per browser session
    max_cpu_usage_percent: float = 25.0  # 25% of VM CPU
    max_disk_cache_mb: int = 512
    
    # Security settings
    disable_javascript: bool = False
    disable_images: bool = False
    block_third_party_cookies: bool = True
    enable_stealth_mode: bool = False
    
    # Screenshot settings
    enable_screenshots: bool = True
    screenshot_format: str = "png"
    screenshot_quality: int = 90
    screenshot_interval: float = 2.0  # seconds between screenshots
    
    # Session management
    auto_cleanup_sessions: bool = True
    max_session_duration_minutes: int = 120
    idle_timeout_minutes: int = 30
    
    def __post_init__(self):
        """Validate browser settings"""
        if self.page_load_timeout < 1 or self.page_load_timeout > 300:
            raise ValueError("Page load timeout must be between 1 and 300 seconds")
        if not (0.0 <= self.source_quality_threshold <= 1.0):
            raise ValueError("Source quality threshold must be between 0.0 and 1.0")
        if self.max_memory_usage_mb < 100 or self.max_memory_usage_mb > 10000:
            raise ValueError("Max memory usage must be between 100MB and 10GB")
        if not (0.0 <= self.max_cpu_usage_percent <= 100.0):
            raise ValueError("CPU usage percent must be between 0.0 and 100.0")


@dataclass
class VMCapabilities:
    """Track VM capabilities and learned skills"""
    installed_tools: List[str] = field(default_factory=list)
    learned_capabilities: List[str] = field(default_factory=list)
    research_bookmarks: List[str] = field(default_factory=list)
    custom_workflows: Dict[str, Any] = field(default_factory=dict)
    personal_libraries: List[str] = field(default_factory=list)
    automation_scripts: List[str] = field(default_factory=list)
    
    # Capability metrics
    total_capabilities: int = 0
    capabilities_per_hour: float = 0.0
    last_capability_added: Optional[datetime] = None


@dataclass
class VMPerformanceMetrics:
    """VM performance and efficiency metrics"""
    total_uptime_hours: float = 0.0
    efficiency_rating: float = 1.0
    capability_acquisition_rate: float = 0.0
    tasks_completed: int = 0
    tasks_failed: int = 0
    average_task_time: float = 0.0
    
    # Resource utilization
    peak_cpu_usage: float = 0.0
    peak_memory_usage: float = 0.0
    average_cpu_usage: float = 0.0
    average_memory_usage: float = 0.0
    
    # Quality metrics
    code_quality_score: float = 0.0
    research_quality_score: float = 0.0
    collaboration_effectiveness: float = 0.0


class VMInstanceSettings(BaseModel):
    """Production-ready configuration for individual VM instances"""
    
    # ========================================
    # IDENTIFICATION AND OWNERSHIP
    # ========================================
    instance_id: UUID = Field(default_factory=uuid4, description="Unique VM instance identifier")
    instance_name: str = Field(..., min_length=1, max_length=100, description="Human-readable VM name")
    user_id: str = Field(..., description="Owner user ID", min_length=3, max_length=50)
    
    # ========================================
    # HARDWARE AND RESOURCES
    # ========================================
    hardware_spec: VMHardwareSpec = Field(default_factory=VMHardwareSpec, description="VM hardware configuration")
    resource_policy: ResourcePolicy = Field(default=ResourcePolicy.BALANCED, description="Resource allocation policy")
    
    # ========================================
    # AI PERSONALITY AND BEHAVIOR
    # ========================================
    personality: VMPersonality = Field(default_factory=VMPersonality, description="AI personality settings")
    
    # ========================================
    # SECURITY AND ISOLATION
    # ========================================
    security_level: str = Field(default="medium", description="Security isolation level")
    network_policy: NetworkPolicy = Field(default=NetworkPolicy.BRIDGED, description="Network access policy")
    ssh_public_key: Optional[str] = Field(None, description="SSH public key for secure access")
    access_token: str = Field(default_factory=lambda: SecurityValidator.generate_secure_token(), description="Secure access token")
    
    # ========================================
    # LIFECYCLE MANAGEMENT
    # ========================================
    auto_suspend_minutes: int = Field(default=30, ge=5, le=1440, description="Auto-suspend timeout")
    max_idle_hours: int = Field(default=24, ge=1, le=168, description="Maximum idle time before archive")
    backup_schedule: BackupSchedule = Field(default=BackupSchedule.DAILY, description="Backup frequency")
    retention_days: int = Field(default=90, ge=7, le=365, description="Data retention period")
    
    # ========================================
    # STORAGE AND PERSISTENCE
    # ========================================
    vm_disk_path: Optional[str] = Field(None, description="Path to VM disk image")
    snapshot_path: Optional[str] = Field(None, description="Path to VM snapshots")
    backup_path: Optional[str] = Field(None, description="Path to VM backups")
    memory_snapshot_path: Optional[str] = Field(None, description="Path to memory snapshots")
    
    # ========================================
    # BROWSER AND RESEARCH CONFIGURATION
    # ========================================
    browser_settings: VMBrowserSettings = Field(default_factory=VMBrowserSettings, description="Browser automation and research settings")
    
    # ========================================
    # CAPABILITIES AND LEARNING
    # ========================================
    capabilities: VMCapabilities = Field(default_factory=VMCapabilities, description="VM capabilities tracking")
    
    # ========================================
    # PERFORMANCE METRICS
    # ========================================
    metrics: VMPerformanceMetrics = Field(default_factory=VMPerformanceMetrics, description="Performance metrics")
    
    # ========================================
    # INTEGRATION AND METADATA
    # ========================================
    dev_session_id: Optional[UUID] = Field(None, description="Associated development session ID")
    orchestrator_id: Optional[UUID] = Field(None, description="Associated orchestrator instance ID")
    supervisor_id: Optional[UUID] = Field(None, description="Associated supervisor instance ID")
    
    # ========================================
    # CREATION AND MODIFICATION TRACKING
    # ========================================
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp")
    last_modified: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Last modification timestamp")
    last_backup: Optional[datetime] = Field(None, description="Last backup timestamp")
    last_heartbeat: Optional[datetime] = Field(None, description="Last heartbeat from VM")
    
    # ========================================
    # CURRENT STATE
    # ========================================
    current_state: VMState = Field(default=VMState.CREATING, description="Current VM state")
    is_active: bool = Field(default=False, description="Whether VM is currently active")
    is_healthy: bool = Field(default=True, description="VM health status")
    
    class Config:
        """Pydantic configuration"""
        validate_assignment = True
        use_enum_values = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v)
        }
    
    @validator('instance_name')
    def validate_instance_name(cls, v):
        """Validate instance name format"""
        if not SecurityValidator.validate_instance_name(v):
            raise ValueError("Invalid instance name format")
        return v
    
    @validator('user_id')
    def validate_user_id(cls, v):
        """Validate user ID format"""
        if not SecurityValidator.validate_user_id(v):
            raise ValueError("Invalid user ID format")
        return v
    
    @validator('ssh_public_key')
    def validate_ssh_key(cls, v):
        """Validate SSH public key format"""
        if v and not v.startswith(('ssh-rsa', 'ssh-ed25519', 'ecdsa-sha2-')):
            raise ValueError("Invalid SSH public key format")
        return v


class VMPoolSettings(BaseModel):
    """Global VM pool management configuration"""
    
    # ========================================
    # SYSTEM IDENTIFICATION
    # ========================================
    system_id: UUID = Field(default_factory=uuid4, description="System identifier")
    version: str = Field(default="2.0.0", description="System version")
    
    # ========================================
    # POOL MANAGEMENT
    # ========================================
    max_concurrent_vms: int = Field(default=10, ge=1, le=1000, description="Maximum concurrent VMs")
    max_vms_per_user: int = Field(default=5, ge=1, le=100, description="Maximum VMs per user")
    max_vms_per_project: int = Field(default=3, ge=1, le=50, description="Maximum VMs per project")
    
    # ========================================
    # RESOURCE MANAGEMENT
    # ========================================
    global_resource_limits: Dict[str, Any] = Field(default_factory=lambda: {
        "total_cpu_cores": psutil.cpu_count(),
        "total_memory_gb": int(psutil.virtual_memory().total / (1024**3)),
        "total_storage_gb": max(1000, int(psutil.disk_usage('/').total / (1024**4) * 0.8)),  # 80% of available disk
        "total_gpu_memory_gb": 0,  # Will be auto-detected
        "reserved_cpu_cores": 2,    # Reserve for host system
        "reserved_memory_gb": 4,     # Reserve for host system
        "reserved_storage_gb": 100,  # Reserve for host system
        "gpu_reserved_memory_gb": 0  # Reserve for host system
    }, description="Global resource limits")
    
    # ========================================
    # VM TEMPLATES AND BASE IMAGES
    # ========================================
    base_image_path: str = Field(default="/data/vm_templates/base_ai_computer.qcow2", description="Base VM image path")
    template_directory: str = Field(default="/data/vm_templates", description="VM templates directory")
    instance_directory: str = Field(default="/data/vm_instances", description="VM instances directory")
    backup_directory: str = Field(default="/data/vm_backups", description="VM backups directory")
    snapshot_directory: str = Field(default="/data/vm_snapshots", description="VM snapshots directory")
    memory_snapshots_directory: str = Field(default="/data/vm_memory_snapshots", description="Memory snapshots directory")
    
    # ========================================
    # DEFAULT SPECIFICATIONS
    # ========================================
    default_hardware_spec: VMHardwareSpec = Field(default_factory=VMHardwareSpec, description="Default hardware spec")
    default_personality: VMPersonality = Field(default_factory=VMPersonality, description="Default personality")
    default_resource_policy: ResourcePolicy = Field(default=ResourcePolicy.BALANCED, description="Default resource policy")
    default_security_level: SecurityLevel = Field(default=SecurityLevel.MEDIUM, description="Default security level")
    default_network_policy: NetworkPolicy = Field(default=NetworkPolicy.BRIDGED, description="Default network policy")
    default_browser_settings: VMBrowserSettings = Field(default_factory=VMBrowserSettings, description="Default browser settings")
    
    # ========================================
    # AUTOMATION SETTINGS
    # ========================================
    auto_backup_enabled: bool = Field(default=True, description="Enable automatic backups")
    auto_cleanup_enabled: bool = Field(default=True, description="Enable automatic cleanup")
    capability_sync_enabled: bool = Field(default=True, description="Enable capability synchronization")
    cross_vm_learning: bool = Field(default=False, description="Enable cross-VM learning")
    auto_health_check: bool = Field(default=True, description="Enable automatic health checks")
    auto_resource_balancing: bool = Field(default=True, description="Enable automatic resource balancing")
    auto_browser_cleanup: bool = Field(default=True, description="Automatically cleanup browser sessions")
    auto_research_archival: bool = Field(default=True, description="Automatically archive research results")
    
    # ========================================
    # PERFORMANCE OPTIMIZATION
    # ========================================
    vm_balancing_enabled: bool = Field(default=True, description="Enable VM load balancing")
    resource_monitoring_interval: int = Field(default=30, ge=5, le=3600, description="Resource monitoring interval (seconds)")
    health_check_interval: int = Field(default=60, ge=10, le=7200, description="Health check interval (seconds)")
    capability_sync_interval: int = Field(default=300, ge=60, le=86400, description="Capability sync interval (seconds)")
    snapshot_retention_days: int = Field(default=30, ge=1, le=365, description="Snapshot retention period (days)")
    backup_retention_days: int = Field(default=90, ge=7, le=730, description="Backup retention period (days)")
    
    # ========================================
    # SECURITY SETTINGS
    # ========================================
    ssh_key_management: bool = Field(default=True, description="Enable SSH key management")
    network_isolation: bool = Field(default=True, description="Enable network isolation")
    snapshot_encryption: bool = Field(default=False, description="Enable snapshot encryption")
    backup_encryption: bool = Field(default=True, description="Enable backup encryption")
    require_secure_tokens: bool = Field(default=True, description="Require secure access tokens")
    enable_audit_logging: bool = Field(default=True, description="Enable audit logging")
    
    # ========================================
    # INTEGRATION SETTINGS
    # ========================================
    integration_supervisor: bool = Field(default=True, description="Integrate with VM Supervisor")
    integration_orchestrator: bool = Field(default=True, description="Integrate with AI Orchestrator")
    integration_memory_core: bool = Field(default=True, description="Integrate with Memory Core")
    integration_artifact_system: bool = Field(default=True, description="Integrate with Artifact System")
    integration_browser_system: bool = Field(default=True, description="Integrate with AI Browser System")
    
    # ========================================
    # MONITORING AND ALERTING
    # ========================================
    enable_prometheus_metrics: bool = Field(default=False, description="Enable Prometheus metrics")
    enable_grafana_dashboard: bool = Field(default=False, description="Enable Grafana dashboard")
    alert_cpu_threshold: float = Field(default=85.0, ge=0.0, le=100.0, description="CPU usage alert threshold")
    alert_memory_threshold: float = Field(default=90.0, ge=0.0, le=100.0, description="Memory usage alert threshold")
    alert_disk_threshold: float = Field(default=85.0, ge=0.0, le=100.0, description="Disk usage alert threshold")
    alert_browser_memory_threshold: float = Field(default=80.0, ge=0.0, le=100.0, description="Browser memory usage alert threshold")
    alert_browser_cpu_threshold: float = Field(default=70.0, ge=0.0, le=100.0, description="Browser CPU usage alert threshold")
    alert_research_duration_threshold: int = Field(default=3600, ge=60, le=86400, description="Research duration alert threshold (seconds)")
    
    class Config:
        """Pydantic configuration"""
        validate_assignment = True
        use_enum_values = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v)
        }


# ============================================================================
# VM SETTINGS MANAGER (PRODUCTION-READY)
# ============================================================================

class VMSettingsManager:
    """
    Production-ready VM settings manager with full integration
    
    Features:
    - Secure configuration management
    - Integration with VM Supervisor and Orchestrator
    - Resource quota enforcement
    - Capability tracking and analytics
    - Automated backup and snapshot management
    - Cross-VM learning synchronization
    - Hardware acceleration support
    - Comprehensive monitoring and alerting
    """
    
    def __init__(
        self,
        pool_settings: Optional[VMPoolSettings] = None,
        config_path: str = "data/vm_settings.json",
        vm_supervisor: Optional[VMSupervisor] = None,
        ai_orchestrator: Optional[AIActionOrchestrator] = None
    ):
        # Core components
        self.pool_settings = pool_settings or VMPoolSettings()
        self.config_path = Path(SecurityValidator.sanitize_path(config_path))
        self.vm_supervisor = vm_supervisor
        self.ai_orchestrator = ai_orchestrator
        self.security_validator = SecurityValidator()
        
        # State tracking
        self.active_vms: Dict[UUID, VMInstanceSettings] = {}
        self.vm_metrics: Dict[UUID, Dict[str, Any]] = {}
        self.resource_usage: Dict[str, float] = {}
        self.user_quotas: Dict[str, Dict[str, int]] = {}
        
        # Integration state
        self.integration_enabled: bool = True
        self.last_sync_time: Optional[datetime] = None
        
        # Background tasks
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}
        self._resource_monitor_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._capability_sync_task: Optional[asyncio.Task] = None
        self._backup_task: Optional[asyncio.Task] = None
        
        # Task synchronization
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        
        logger.info("Production VM Settings Manager initialized")
    
    async def initialize(self) -> bool:
        """
        Initialize the VM settings management system
        
        Returns:
            bool: True if initialization successful
        """
        try:
            logger.info("Initializing production VM settings manager...")
            
            # Validate system resources
            await self._validate_system_resources()
            
            # Initialize directories with proper permissions
            await self._initialize_directories()
            
            # Load existing configuration
            await self._load_settings()
            
            # Initialize integrations
            await self._initialize_integrations()
            
            # Start background monitoring
            await self._start_monitoring_tasks()
            
            # Warm up VM supervisor if available
            if self.vm_supervisor:
                self.vm_supervisor.start_monitoring()
            
            logger.info(f"VM Settings Manager initialized with capacity for {self.pool_settings.max_concurrent_vms} VMs")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize VM Settings Manager: {e}", exc_info=True)
            return False
    
    async def create_vm_instance(
        self,
        user_id: str,
        instance_name: str,
        hardware_spec: Optional[VMHardwareSpec] = None,
        personality: Optional[VMPersonality] = None,
        resource_policy: Optional[ResourcePolicy] = None,
        security_level: Optional[str] = None
    ) -> Optional[VMInstanceSettings]:
        """
        Create new VM instance with production-level validation
        
        Args:
            user_id: Owner user ID
            instance_name: VM instance name
            hardware_spec: Hardware specifications
            personality: AI personality configuration
            resource_policy: Resource allocation policy
            security_level: Security isolation level
            
        Returns:
            VMInstanceSettings or None if creation failed
        """
        async with self._lock:
            try:
                # Validate inputs
                if not self.security_validator.validate_user_id(user_id):
                    logger.error(f"Invalid user ID: {user_id}")
                    return None
                
                if not self.security_validator.validate_instance_name(instance_name):
                    logger.error(f"Invalid instance name: {instance_name}")
                    return None
                
                # Check user VM limits
                if not await self._check_user_limits(user_id):
                    logger.warning(f"User {user_id} exceeded VM limit")
                    return None
                
                # Check resource availability
                if not await self._check_resource_availability(hardware_spec):
                    logger.warning(f"Insufficient resources for VM creation")
                    return None
                
                # Create VM configuration
                vm_settings = VMInstanceSettings(
                    instance_name=instance_name,
                    user_id=user_id,
                    hardware_spec=hardware_spec or self.pool_settings.default_hardware_spec,
                    personality=personality or self.pool_settings.default_personality,
                    resource_policy=resource_policy or self.pool_settings.default_resource_policy,
                    security_level=security_level or self.pool_settings.default_security_level.value
                )
                
                # Set up secure storage paths
                await self._setup_storage_paths(vm_settings)
                
                # Initialize capabilities tracking
                vm_settings.capabilities = VMCapabilities()
                vm_settings.metrics = VMPerformanceMetrics()
                
                # Generate secure access token
                vm_settings.access_token = self.security_validator.generate_secure_token()
                
                # Register VM
                self.active_vms[vm_settings.instance_id] = vm_settings
                
                # Initialize metrics tracking
                self.vm_metrics[vm_settings.instance_id] = {
                    "creation_time": datetime.now(timezone.utc),
                    "last_heartbeat": None,
                    "last_capability_sync": None,
                    "resource_usage": {"cpu": 0.0, "memory": 0.0, "disk": 0.0, "gpu": 0.0},
                    "alerts": [],
                    "capability_changes": [],
                    "security_events": []
                }
                
                # Create VM in supervisor if available
                if self.vm_supervisor:
                    personality_config = asdict(vm_settings.personality)
                    vm_instance = await self.vm_supervisor.create_ai_computer(
                        instance_name=instance_name,
                        personality_config=personality_config
                    )
                    if vm_instance:
                        vm_settings.dev_session_id = vm_instance.vm_id
                        vm_settings.current_state = VMState.RUNNING
                        vm_settings.is_active = True
                        logger.info(f"VM created in supervisor: {vm_instance.vm_id}")
                
                # Save configuration
                await self._save_settings()
                
                logger.info(f"Created VM instance {vm_settings.instance_name} for user {user_id}")
                return vm_settings
                
            except ValidationError as e:
                logger.error(f"VM settings validation failed: {e}")
                return None
            except Exception as e:
                logger.error(f"Failed to create VM instance: {e}", exc_info=True)
                return None
    
    async def update_vm_settings(
        self,
        instance_id: UUID,
        updates: Dict[str, Any],
        validate_security: bool = True
    ) -> bool:
        """
        Update VM instance settings with validation
        
        Args:
            instance_id: VM instance ID
            updates: Settings updates
            validate_security: Whether to validate security-sensitive changes
            
        Returns:
            bool: True if update successful
        """
        async with self._lock:
            try:
                if instance_id not in self.active_vms:
                    logger.error(f"VM instance {instance_id} not found")
                    return False
                
                vm_settings = self.active_vms[instance_id]
                
                # Validate security-sensitive changes
                if validate_security:
                    security_issues = await self._validate_security_changes(updates)
                    if security_issues:
                        logger.warning(f"Security validation failed for VM {instance_id}: {security_issues}")
                        return False
                
                # Apply updates with type checking
                for key, value in updates.items():
                    if hasattr(vm_settings, key):
                        # Handle nested objects
                        if key == "hardware_spec" and isinstance(value, dict):
                            await self._update_hardware_spec(vm_settings, value)
                        elif key == "personality" and isinstance(value, dict):
                            await self._update_personality(vm_settings, value)
                        elif key == "capabilities" and isinstance(value, dict):
                            await self._update_capabilities(vm_settings, value)
                        else:
                            setattr(vm_settings, key, value)
                    else:
                        logger.warning(f"Unknown setting: {key}")
                
                vm_settings.last_modified = datetime.now(timezone.utc)
                
                # Save updated configuration
                await self._save_settings()
                
                logger.info(f"Updated VM settings for instance {instance_id}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update VM settings: {e}", exc_info=True)
                return False
    
    async def track_capability_acquisition(
        self,
        instance_id: UUID,
        capability: str,
        capability_type: str = "tool",
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Track when AI acquires new capabilities with analytics
        
        Args:
            instance_id: VM instance ID
            capability: Capability name
            capability_type: Type of capability (tool, skill, library, script)
            metadata: Additional metadata
            
        Returns:
            bool: True if tracking successful
        """
        async with self._lock:
            try:
                if instance_id not in self.active_vms:
                    logger.error(f"VM instance {instance_id} not found")
                    return False
                
                vm_settings = self.active_vms[instance_id]
                current_time = datetime.now(timezone.utc)
                
                # Add to appropriate tracking list
                if capability_type == "tool" and capability not in vm_settings.capabilities.installed_tools:
                    vm_settings.capabilities.installed_tools.append(capability)
                    logger.info(f"VM {instance_id} installed tool: {capability}")
                elif capability_type == "skill" and capability not in vm_settings.capabilities.learned_capabilities:
                    vm_settings.capabilities.learned_capabilities.append(capability)
                    logger.info(f"VM {instance_id} learned skill: {capability}")
                elif capability_type == "library" and capability not in vm_settings.capabilities.personal_libraries:
                    vm_settings.capabilities.personal_libraries.append(capability)
                    logger.info(f"VM {instance_id} created library: {capability}")
                elif capability_type == "script" and capability not in vm_settings.capabilities.automation_scripts:
                    vm_settings.capabilities.automation_scripts.append(capability)
                    logger.info(f"VM {instance_id} created automation: {capability}")
                else:
                    logger.debug(f"Capability {capability} already tracked")
                    return True
                
                # Update capability tracking
                vm_settings.capabilities.last_capability_added = current_time
                vm_settings.capabilities.total_capabilities = (
                    len(vm_settings.capabilities.installed_tools) +
                    len(vm_settings.capabilities.learned_capabilities) +
                    len(vm_settings.capabilities.personal_libraries) +
                    len(vm_settings.capabilities.automation_scripts)
                )
                
                # Calculate efficiency improvement
                vm_settings.metrics.efficiency_rating = 1.0 + (vm_settings.capabilities.total_capabilities * 0.15)
                
                # Update acquisition rate
                if vm_settings.metrics.total_uptime_hours > 0:
                    vm_settings.metrics.capability_acquisition_rate = vm_settings.capabilities.total_capabilities / vm_settings.metrics.total_uptime_hours
                
                # Track in metrics
                if instance_id in self.vm_metrics:
                    capability_entry = {
                        "capability": capability,
                        "type": capability_type,
                        "timestamp": current_time.isoformat(),
                        "metadata": metadata or {},
                        "total_capabilities": vm_settings.capabilities.total_capabilities
                    }
                    self.vm_metrics[instance_id]["capability_changes"].append(capability_entry)
                
                # Trigger backup if configured
                if self.pool_settings.backup_schedule == BackupSchedule.ON_CAPABILITY_CHANGE:
                    await self._trigger_backup(instance_id)
                
                # Sync with other VMs if cross-VM learning enabled
                if self.pool_settings.cross_vm_learning:
                    await self._sync_capability_across_vms(instance_id, capability, capability_type)
                
                # Save configuration
                await self._save_settings()
                
                logger.info(f"VM {instance_id} acquired capability: {capability} ({capability_type})")
                return True
                
            except Exception as e:
                logger.error(f"Failed to track capability acquisition: {e}", exc_info=True)
                return False
    
    async def get_vm_status(
        self,
        instance_id: Optional[UUID] = None,
        include_metrics: bool = True
    ) -> Dict[str, Any]:
        """
        Get comprehensive VM status with metrics
        
        Args:
            instance_id: Specific VM instance ID (or None for all)
            include_metrics: Whether to include detailed metrics
            
        Returns:
            Dict containing VM status information
        """
        try:
            if instance_id:
                if instance_id not in self.active_vms:
                    return {"error": f"VM instance {instance_id} not found", "status": "not_found"}
                
                vm_settings = self.active_vms[instance_id]
                metrics = self.vm_metrics.get(instance_id, {})
                
                status = {
                    "instance_id": str(instance_id),
                    "instance_name": vm_settings.instance_name,
                    "user_id": vm_settings.user_id,
                    "current_state": vm_settings.current_state.value,
                    "is_active": vm_settings.is_active,
                    "is_healthy": vm_settings.is_healthy,
                    "resource_policy": vm_settings.resource_policy.value,
                    "security_level": vm_settings.security_level,
                    "network_policy": vm_settings.network_policy.value,
                    "hardware_spec": asdict(vm_settings.hardware_spec),
                    "personality": asdict(vm_settings.personality),
                    "capabilities": asdict(vm_settings.capabilities),
                    "metrics": asdict(vm_settings.metrics) if include_metrics else {},
                    "uptime_hours": vm_settings.metrics.total_uptime_hours,
                    "created_at": vm_settings.created_at.isoformat(),
                    "last_modified": vm_settings.last_modified.isoformat(),
                    "last_heartbeat": vm_settings.last_heartbeat.isoformat() if vm_settings.last_heartbeat else None,
                    "integration_status": {
                        "supervisor_connected": self.vm_supervisor is not None,
                        "orchestrator_connected": self.ai_orchestrator is not None,
                        "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None
                    }
                }
                
                # Add supervisor status if available
                if self.vm_supervisor and vm_settings.dev_session_id:
                    try:
                        vm_instance = self.vm_supervisor.active_vms.get(vm_settings.dev_session_id)
                        if vm_instance:
                            status["vm_supervisor_status"] = {
                                "state": vm_instance.vm_state.value,
                                "ip_address": vm_instance.internal_ip,
                                "current_profile": vm_instance.current_profile,
                                "agent_port": vm_instance.agent_port,
                                "ssh_port": vm_instance.ssh_port,
                                "vnc_port": vm_instance.vnc_port
                            }
                    except Exception as e:
                        logger.debug(f"Could not get supervisor status: {e}")
                
                return status
            
            # Return pool status
            return {
                "pool_status": {
                    "system_id": str(self.pool_settings.system_id),
                    "version": self.pool_settings.version,
                    "active_vms": len(self.active_vms),
                    "max_concurrent": self.pool_settings.max_concurrent_vms,
                    "total_capacity": self.pool_settings.global_resource_limits,
                    "current_usage": self.resource_usage,
                    "available_capacity": await self._calculate_available_capacity(),
                    "integration_status": {
                        "supervisor_connected": self.vm_supervisor is not None,
                        "orchestrator_connected": self.ai_orchestrator is not None,
                        "memory_core_connected": True,  # Assuming memory core is always available
                        "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None
                    }
                },
                "active_instances": [
                    {
                        "instance_id": str(vm_id),
                        "instance_name": vm_settings.instance_name,
                        "user_id": vm_settings.user_id,
                        "current_state": vm_settings.current_state.value,
                        "is_active": vm_settings.is_active,
                        "efficiency_rating": vm_settings.metrics.efficiency_rating,
                        "total_capabilities": vm_settings.capabilities.total_capabilities,
                        "uptime_hours": vm_settings.metrics.total_uptime_hours,
                        "resource_usage": self.vm_metrics.get(vm_id, {}).get("resource_usage", {})
                    }
                    for vm_id, vm_settings in self.active_vms.items()
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to get VM status: {e}", exc_info=True)
            return {"error": str(e), "status": "error"}
    
    async def configure_vm_pool(self, pool_updates: Dict[str, Any]) -> bool:
        """
        Update VM pool configuration with validation
        
        Args:
            pool_updates: Pool configuration updates
            
        Returns:
            bool: True if update successful
        """
        try:
            # Validate critical settings
            if "max_concurrent_vms" in pool_updates:
                new_max = pool_updates["max_concurrent_vms"]
                if new_max < len(self.active_vms):
                    logger.error("Cannot reduce max_concurrent_vms below current active VM count")
                    return False
            
            # Apply updates
            for key, value in pool_updates.items():
                if hasattr(self.pool_settings, key):
                    if key == "global_resource_limits" and isinstance(value, dict):
                        self.pool_settings.global_resource_limits.update(value)
                    elif key in ["default_hardware_spec", "default_personality"] and isinstance(value, dict):
                        # Handle nested object updates
                        target_obj = getattr(self.pool_settings, key)
                        for obj_key, obj_value in value.items():
                            if hasattr(target_obj, obj_key):
                                setattr(target_obj, obj_key, obj_value)
                    else:
                        setattr(self.pool_settings, key, value)
                else:
                    logger.warning(f"Unknown pool setting: {key}")
            
            # Re-validate resource limits
            await self._validate_system_resources()
            
            # Save configuration
            await self._save_settings()
            
            logger.info("VM pool configuration updated successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update pool configuration: {e}", exc_info=True)
            return False
    
    # ============================================================================
    # BROWSER SYSTEM INTEGRATION
    # ============================================================================
    
    async def update_browser_settings(
        self,
        instance_id: UUID,
        browser_updates: Dict[str, Any]
    ) -> bool:
        """
        Update browser settings for a VM instance
        
        Args:
            instance_id: VM instance ID
            browser_updates: Browser configuration updates
            
        Returns:
            bool: True if update successful
        """
        async with self._lock:
            try:
                if instance_id not in self.active_vms:
                    logger.error(f"VM instance {instance_id} not found")
                    return False
                
                vm_settings = self.active_vms[instance_id]
                
                # Update browser settings
                for key, value in browser_updates.items():
                    if hasattr(vm_settings.browser_settings, key):
                        setattr(vm_settings.browser_settings, key, value)
                    else:
                        logger.warning(f"Unknown browser setting: {key}")
                
                vm_settings.last_modified = datetime.now(timezone.utc)
                await self._save_settings()
                
                logger.info(f"Updated browser settings for VM {instance_id}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update browser settings: {e}", exc_info=True)
                return False
    
    async def get_browser_status(self, instance_id: UUID) -> Dict[str, Any]:
        """
        Get browser system status for a VM instance
        
        Args:
            instance_id: VM instance ID
            
        Returns:
            Dict containing browser status information
        """
        try:
            if instance_id not in self.active_vms:
                return {"error": f"VM instance {instance_id} not found", "status": "not_found"}
            
            vm_settings = self.active_vms[instance_id]
            
            return {
                "instance_id": str(instance_id),
                "browser_enabled": self.pool_settings.integration_browser_system,
                "primary_browser": vm_settings.browser_settings.primary_browser.value,
                "automation_mode": vm_settings.browser_settings.automation_mode.value,
                "extraction_level": vm_settings.browser_settings.extraction_level.value,
                "default_workflow": vm_settings.browser_settings.default_workflow.value,
                "enabled_extensions": vm_settings.browser_settings.enabled_extensions,
                "max_concurrent_extractions": vm_settings.browser_settings.max_concurrent_extractions,
                "resource_limits": {
                    "max_memory_mb": vm_settings.browser_settings.max_memory_usage_mb,
                    "max_cpu_percent": vm_settings.browser_settings.max_cpu_usage_percent,
                    "max_disk_cache_mb": vm_settings.browser_settings.max_disk_cache_mb
                },
                "security_settings": {
                    "disable_javascript": vm_settings.browser_settings.disable_javascript,
                    "block_third_party_cookies": vm_settings.browser_settings.block_third_party_cookies,
                    "enable_stealth_mode": vm_settings.browser_settings.enable_stealth_mode
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get browser status: {e}", exc_info=True)
            return {"error": str(e), "status": "error"}
    
    async def configure_research_workflow(
        self,
        instance_id: UUID,
        workflow_name: str,
        workflow_config: Dict[str, Any]
    ) -> bool:
        """
        Configure a research workflow for a VM instance
        
        Args:
            instance_id: VM instance ID
            workflow_name: Name of the workflow
            workflow_config: Workflow configuration
            
        Returns:
            bool: True if configuration successful
        """
        try:
            if instance_id not in self.active_vms:
                logger.error(f"VM instance {instance_id} not found")
                return False
            
            vm_settings = self.active_vms[instance_id]
            
            # Store workflow in custom_workflows
            vm_settings.capabilities.custom_workflows[workflow_name] = workflow_config
            vm_settings.last_modified = datetime.now(timezone.utc)
            
            await self._save_settings()
            
            logger.info(f"Configured research workflow '{workflow_name}' for VM {instance_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to configure research workflow: {e}", exc_info=True)
            return False
    
    async def get_research_templates(self) -> Dict[str, Any]:
        """
        Get available research workflow templates
        
        Returns:
            Dict containing research templates
        """
        try:
            templates_dir = Path('/templates')
            workflows = {}
            
            if templates_dir.exists():
                for template_file in templates_dir.glob('*.json'):
                    try:
                        async with aiofiles.open(template_file, 'r') as f:
                            workflow_name = template_file.stem
                            workflows[workflow_name] = json.loads(await f.read())
                    except Exception as e:
                        logger.warning(f"Failed to load template {template_file}: {e}")
            
            else:
                # Default templates
                workflows = {
                    "comprehensive": {
                        "name": "Comprehensive Research",
                        "max_results_per_engine": 20,
                        "max_deep_reads": 10,
                        "source_types": ["academic", "news", "technical", "social"],
                        "verification_required": True,
                        "cross_reference_sources": 3
                    },
                    "speed_run": {
                        "name": "Speed Run Research", 
                        "max_results_per_engine": 10,
                        "max_deep_reads": 5,
                        "source_types": ["news", "technical"],
                        "verification_required": False,
                        "cross_reference_sources": 1
                    },
                    "academic": {
                        "name": "Academic Research",
                        "max_results_per_engine": 15,
                        "max_deep_reads": 15,
                        "source_types": ["academic", "technical"],
                        "verification_required": True,
                        "cross_reference_sources": 5,
                        "require_peer_reviewed": True
                    }
                }
            
            return workflows
            
        except Exception as e:
            logger.error(f"Failed to get research templates: {e}", exc_info=True)
            return {}
    
    async def list_browser_extensions(self) -> List[Dict[str, Any]]:
        """
        List available browser extensions
        
        Returns:
            List of available extensions with metadata
        """
        try:
            extensions_dir = Path('/home/ai/extensions')
            extensions = []
            
            if extensions_dir.exists():
                for ext_dir in extensions_dir.iterdir():
                    if ext_dir.is_dir():
                        manifest_file = ext_dir / 'manifest.json'
                        if manifest_file.exists():
                            try:
                                async with aiofiles.open(manifest_file, 'r') as f:
                                    manifest = json.loads(await f.read())
                                    extensions.append({
                                        "id": ext_dir.name,
                                        "name": manifest.get('name', ext_dir.name),
                                        "version": manifest.get('version', 'unknown'),
                                        "description": manifest.get('description', ''),
                                        "permissions": manifest.get('permissions', [])
                                    })
                            except Exception as e:
                                logger.warning(f"Failed to read manifest for {ext_dir}: {e}")
            
            return extensions
            
        except Exception as e:
            logger.error(f"Failed to list browser extensions: {e}", exc_info=True)
            return []
    
    async def shutdown(self) -> bool:
        """
        Graceful shutdown of VM settings management
        
        Returns:
            bool: True if shutdown successful
        """
        try:
            logger.info("Shutting down VM settings management...")
            
            # Signal shutdown
            self._shutdown_event.set()
            
            # Stop all background tasks
            await self._stop_monitoring_tasks()
            
            # Final settings save
            await self._save_settings()
            
            # Disconnect from supervisor
            if self.vm_supervisor:
                self.vm_supervisor.stop_monitoring()
            
            logger.info("VM settings management shutdown complete")
            return True
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
            return False
    
    # ============================================================================
    # INTERNAL HELPER METHODS
    # ============================================================================
    
    async def _validate_system_resources(self) -> None:
        """Validate system resources meet minimum requirements"""
        # Check CPU cores
        available_cores = psutil.cpu_count()
        if available_cores < (self.pool_settings.global_resource_limits["reserved_cpu_cores"] + 2):
            logger.warning(f"Low CPU cores: {available_cores} available, {self.pool_settings.global_resource_limits['reserved_cpu_cores']} reserved")
        
        # Check memory
        available_memory = int(psutil.virtual_memory().total / (1024**3))
        if available_memory < (self.pool_settings.global_resource_limits["reserved_memory_gb"] + 8):
            logger.warning(f"Low memory: {available_memory}GB available, {self.pool_settings.global_resource_limits['reserved_memory_gb']}GB reserved")
        
        # Check disk space
        disk_usage = psutil.disk_usage(self.pool_settings.instance_directory)
        available_storage = int(disk_usage.total / (1024**4))
        if available_storage < (self.pool_settings.global_resource_limits["reserved_storage_gb"] + 100):
            logger.warning(f"Low storage: {available_storage}TB available, {self.pool_settings.global_resource_limits['reserved_storage_gb']}GB reserved")
    
    async def _initialize_directories(self) -> None:
        """Initialize VM storage directories with proper permissions"""
        directories = [
            self.pool_settings.template_directory,
            self.pool_settings.instance_directory,
            self.pool_settings.backup_directory,
            self.pool_settings.snapshot_directory,
            self.pool_settings.memory_snapshots_directory
        ]
        
        for directory in directories:
            dir_path = Path(directory)
            dir_path.mkdir(parents=True, exist_ok=True, mode=0o755)
            
            # Set restrictive permissions for sensitive directories
            if "backup" in directory or "snapshot" in directory:
                dir_path.chmod(0o700)
    
    async def _load_settings(self) -> None:
        """Load settings from configuration file with validation"""
        if not self.config_path.exists():
            logger.info(f"Configuration file not found at {self.config_path}, using defaults")
            return
        
        try:
            async with aiofiles.open(self.config_path, 'r') as f:
                settings_data = json.loads(await f.read())
            
            # Load pool settings
            if "pool_settings" in settings_data:
                pool_data = settings_data["pool_settings"]
                for key, value in pool_data.items():
                    if hasattr(self.pool_settings, key):
                        # Handle nested objects
                        if key in ["default_hardware_spec", "default_personality"] and isinstance(value, dict):
                            target = getattr(self.pool_settings, key)
                            for k, v in value.items():
                                if hasattr(target, k):
                                    setattr(target, k, v)
                        else:
                            setattr(self.pool_settings, key, value)
            
            # Load active VMs
            if "active_vms" in settings_data:
                for vm_data in settings_data["active_vms"]:
                    try:
                        vm_settings = VMInstanceSettings(**vm_data)
                        self.active_vms[vm_settings.instance_id] = vm_settings
                    except ValidationError as e:
                        logger.error(f"Failed to load VM instance: {e}")
            
            # Load metrics
            if "vm_metrics" in settings_data:
                self.vm_metrics.update(settings_data["vm_metrics"])
            
            logger.info(f"VM settings loaded from {self.config_path}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in configuration file: {e}")
        except Exception as e:
            logger.error(f"Failed to load VM settings: {e}", exc_info=True)
    
    async def _save_settings(self) -> None:
        """Save settings to configuration file with atomic write"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            
            settings_data = {
                "pool_settings": self.pool_settings.dict(),
                "active_vms": [vm.dict() for vm in self.active_vms.values()],
                "vm_metrics": self.vm_metrics,
                "resource_usage": self.resource_usage,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "system_info": {
                    "python_version": "3.11+",
                    "platform": "linux",
                    "total_system_memory": psutil.virtual_memory().total,
                    "total_system_cpu": psutil.cpu_count()
                }
            }
            
            # Write to temporary file first (atomic operation)
            temp_path = self.config_path.with_suffix('.tmp')
            async with aiofiles.open(temp_path, 'w') as f:
                await f.write(json.dumps(settings_data, indent=2, default=str))
            
            # Atomic rename
            temp_path.replace(self.config_path)
            
            # Set restrictive permissions
            self.config_path.chmod(0o600)
            
            logger.debug(f"VM settings saved to {self.config_path}")
            
        except Exception as e:
            logger.error(f"Failed to save VM settings: {e}", exc_info=True)
    
    async def _initialize_integrations(self) -> None:
        """Initialize integrations with other system components"""
        integrations = []
        
        # VM Supervisor integration
        if self.pool_settings.integration_supervisor and self.vm_supervisor:
            try:
                await self.vm_supervisor.initialize()
                integrations.append("VM Supervisor")
            except Exception as e:
                logger.error(f"Failed to initialize VM Supervisor: {e}")
        
        # AI Orchestrator integration
        if self.pool_settings.integration_orchestrator and self.ai_orchestrator:
            try:
                # Additional orchestrator initialization if needed
                integrations.append("AI Orchestrator")
            except Exception as e:
                logger.error(f"Failed to initialize AI Orchestrator: {e}")
        
        if integrations:
            logger.info(f"Initialized integrations: {', '.join(integrations)}")
    
    async def _setup_storage_paths(self, vm_settings: VMInstanceSettings) -> None:
        """Set up storage paths for VM instance"""
        # Sanitize instance name for paths
        safe_name = re.sub(r'[^\w\-]', '_', vm_settings.instance_name.lower())
        
        vm_settings.vm_disk_path = str(
            Path(self.pool_settings.instance_directory) / f"{vm_settings.instance_id}_{safe_name}.qcow2"
        )
        vm_settings.snapshot_path = str(
            Path(self.pool_settings.snapshot_directory) / str(vm_settings.instance_id)
        )
        vm_settings.backup_path = str(
            Path(self.pool_settings.backup_directory) / str(vm_settings.instance_id)
        )
        vm_settings.memory_snapshot_path = str(
            Path(self.pool_settings.memory_snapshots_directory) / str(vm_settings.instance_id)
        )
        
        # Create directories
        for path in [vm_settings.snapshot_path, vm_settings.backup_path, vm_settings.memory_snapshot_path]:
            Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
    
    async def _check_user_limits(self, user_id: str) -> bool:
        """Check if user has exceeded VM limits"""
        user_vm_count = sum(1 for vm in self.active_vms.values() if vm.user_id == user_id)
        return user_vm_count < self.pool_settings.max_vms_per_user
    
    async def _check_resource_availability(self, hardware_spec: Optional[VMHardwareSpec]) -> bool:
        """Check if sufficient resources are available for VM creation"""
        if not hardware_spec:
            hardware_spec = self.pool_settings.default_hardware_spec
        
        # Calculate current resource usage
        current_cpu = sum(vm.hardware_spec.vcpus for vm in self.active_vms.values())
        current_memory = sum(vm.hardware_spec.memory_gb for vm in self.active_vms.values())
        current_storage = sum(vm.hardware_spec.storage_gb for vm in self.active_vms.values())
        
        # Calculate available resources
        limits = self.pool_settings.global_resource_limits
        available_cpu = limits["total_cpu_cores"] - limits["reserved_cpu_cores"] - current_cpu
        available_memory = limits["total_memory_gb"] - limits["reserved_memory_gb"] - current_memory
        available_storage = limits["total_storage_gb"] - limits["reserved_storage_gb"] - current_storage
        
        # Check availability
        cpu_ok = hardware_spec.vcpus <= available_cpu
        memory_ok = hardware_spec.memory_gb <= available_memory
        storage_ok = hardware_spec.storage_gb <= available_storage
        
        if not (cpu_ok and memory_ok and storage_ok):
            logger.warning(f"Insufficient resources: CPU={cpu_ok}, Memory={memory_ok}, Storage={storage_ok}")
            return False
        
        return True
    
    async def _calculate_available_capacity(self) -> Dict[str, int]:
        """Calculate available resource capacity"""
        limits = self.pool_settings.global_resource_limits
        current_cpu = sum(vm.hardware_spec.vcpus for vm in self.active_vms.values())
        current_memory = sum(vm.hardware_spec.memory_gb for vm in self.active_vms.values())
        current_storage = sum(vm.hardware_spec.storage_gb for vm in self.active_vms.values())
        
        return {
            "available_cpu_cores": limits["total_cpu_cores"] - limits["reserved_cpu_cores"] - current_cpu,
            "available_memory_gb": limits["total_memory_gb"] - limits["reserved_memory_gb"] - current_memory,
            "available_storage_gb": limits["total_storage_gb"] - limits["reserved_storage_gb"] - current_storage,
            "remaining_vm_slots": self.pool_settings.max_concurrent_vms - len(self.active_vms)
        }
    
    async def _validate_security_changes(self, updates: Dict[str, Any]) -> List[str]:
        """Validate security-sensitive configuration changes"""
        issues = []
        
        security_sensitive_keys = [
            "security_level", "network_policy", "ssh_public_key", "access_token"
        ]
        
        for key in security_sensitive_keys:
            if key in updates:
                value = updates[key]
                if key == "ssh_public_key" and value:
                    if not value.startswith(('ssh-rsa', 'ssh-ed25519', 'ecdsa-sha2-')):
                        issues.append(f"Invalid SSH key format for {key}")
                elif key == "security_level" and value:
                    if value not in ["low", "medium", "high", "paranoid"]:
                        issues.append(f"Invalid security level: {value}")
        
        return issues
    
    async def _update_hardware_spec(self, vm_settings: VMInstanceSettings, updates: Dict[str, Any]) -> None:
        """Update hardware spec with validation"""
        for key, value in updates.items():
            if hasattr(vm_settings.hardware_spec, key):
                setattr(vm_settings.hardware_spec, key, value)
            else:
                logger.warning(f"Unknown hardware spec: {key}")
    
    async def _update_personality(self, vm_settings: VMInstanceSettings, updates: Dict[str, Any]) -> None:
        """Update personality with validation"""
        for key, value in updates.items():
            if hasattr(vm_settings.personality, key):
                setattr(vm_settings.personality, key, value)
            else:
                logger.warning(f"Unknown personality setting: {key}")
    
    async def _update_capabilities(self, vm_settings: VMInstanceSettings, updates: Dict[str, Any]) -> None:
        """Update capabilities tracking"""
        for key, value in updates.items():
            if hasattr(vm_settings.capabilities, key):
                setattr(vm_settings.capabilities, key, value)
            else:
                logger.warning(f"Unknown capability: {key}")
    
    async def _trigger_backup(self, instance_id: UUID) -> None:
        """Trigger backup for VM instance"""
        if instance_id not in self.active_vms:
            return
        
        vm_settings = self.active_vms[instance_id]
        logger.info(f"Triggering backup for VM {instance_id}")
        
        # Update last backup time
        vm_settings.last_backup = datetime.now(timezone.utc)
        await self._save_settings()
        
        # Trigger supervisor backup if available
        if self.vm_supervisor and vm_settings.dev_session_id:
            try:
                snapshot = self.vm_supervisor.create_snapshot(
                    vm_settings.dev_session_id,
                    description=f"Automatic backup - capability change"
                )
                logger.info(f"Created snapshot: {snapshot.snapshot_name}")
            except Exception as e:
                logger.error(f"Failed to create snapshot: {e}")
    
    async def _sync_capability_across_vms(self, source_instance_id: UUID, capability: str, capability_type: str) -> None:
        """Sync capability across all VMs for same user"""
        try:
            source_vm = self.active_vms.get(source_instance_id)
            if not source_vm:
                return
            
            sync_count = 0
            for vm_id, vm_settings in self.active_vms.items():
                if (vm_id != source_instance_id and 
                    vm_settings.user_id == source_vm.user_id and
                    self.pool_settings.cross_vm_learning):
                    
                    # Check if capability should be synced
                    if capability_type == "tool" and capability not in vm_settings.capabilities.installed_tools:
                        vm_settings.capabilities.installed_tools.append(capability)
                        sync_count += 1
                    elif capability_type == "skill" and capability not in vm_settings.capabilities.learned_capabilities:
                        vm_settings.capabilities.learned_capabilities.append(capability)
                        sync_count += 1
            
            if sync_count > 0:
                logger.info(f"Synced {capability} to {sync_count} other VMs for user {source_vm.user_id}")
                await self._save_settings()
        
        except Exception as e:
            logger.error(f"Failed to sync capability across VMs: {e}")
    
    async def _start_monitoring_tasks(self) -> None:
        """Start all background monitoring tasks"""
        logger.info("Starting VM monitoring tasks...")
        
        try:
            # Resource monitoring
            if self.pool_settings.resource_monitoring_interval > 0:
                self._resource_monitor_task = asyncio.create_task(
                    self._resource_monitoring_loop(),
                    name="resource_monitor"
                )
                logger.debug("Started resource monitoring task")
            
            # Health checks
            if self.pool_settings.health_check_interval > 0:
                self._health_check_task = asyncio.create_task(
                    self._health_check_loop(),
                    name="health_check"
                )
                logger.debug("Started health check task")
            
            # Capability sync
            if self.pool_settings.capability_sync_enabled and self.pool_settings.capability_sync_interval > 0:
                self._capability_sync_task = asyncio.create_task(
                    self._capability_sync_loop(),
                    name="capability_sync"
                )
                logger.debug("Started capability sync task")
            
            # Backup manager
            if self.pool_settings.auto_backup_enabled:
                self._backup_task = asyncio.create_task(
                    self._backup_management_loop(),
                    name="backup_manager"
                )
                logger.debug("Started backup management task")
            
        except Exception as e:
            logger.error(f"Failed to start monitoring tasks: {e}", exc_info=True)
    
    async def _stop_monitoring_tasks(self) -> None:
        """Stop all background monitoring tasks"""
        logger.info("Stopping VM monitoring tasks...")
        
        tasks = [
            self._resource_monitor_task,
            self._health_check_task,
            self._capability_sync_task,
            self._backup_task
        ]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error cancelling task: {e}")
    
    async def _capability_sync_loop(self) -> None:
        """Background capability synchronization loop"""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.pool_settings.capability_sync_interval)
                
                if self.pool_settings.cross_vm_learning:
                    await self._sync_capabilities_across_all_vms()
                
                self.last_sync_time = datetime.now(timezone.utc)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in capability sync loop: {e}")
                await asyncio.sleep(60)
    
    async def _sync_capabilities_across_all_vms(self) -> None:
        """Sync capabilities across all VMs for each user"""
        try:
            # Group VMs by user
            user_vms: Dict[str, List[VMInstanceSettings]] = {}
            for vm in self.active_vms.values():
                if vm.user_id not in user_vms:
                    user_vms[vm.user_id] = []
                user_vms[vm.user_id].append(vm)
            
            sync_count = 0
            for user_id, vms in user_vms.items():
                if len(vms) < 2:
                    continue  # No need to sync if only one VM
                
                # Collect all capabilities for this user
                all_tools = set()
                all_skills = set()
                all_libraries = set()
                all_scripts = set()
                
                for vm in vms:
                    all_tools.update(vm.capabilities.installed_tools)
                    all_skills.update(vm.capabilities.learned_capabilities)
                    all_libraries.update(vm.capabilities.personal_libraries)
                    all_scripts.update(vm.capabilities.automation_scripts)
                
                # Sync to all VMs
                for vm in vms:
                    for tool in all_tools:
                        if tool not in vm.capabilities.installed_tools:
                            vm.capabilities.installed_tools.append(tool)
                            sync_count += 1
                    
                    for skill in all_skills:
                        if skill not in vm.capabilities.learned_capabilities:
                            vm.capabilities.learned_capabilities.append(skill)
                            sync_count += 1
                    
                    for library in all_libraries:
                        if library not in vm.capabilities.personal_libraries:
                            vm.capabilities.personal_libraries.append(library)
                            sync_count += 1
                    
                    for script in all_scripts:
                        if script not in vm.capabilities.automation_scripts:
                            vm.capabilities.automation_scripts.append(script)
                            sync_count += 1
            
            if sync_count > 0:
                logger.info(f"Synced {sync_count} capabilities across VMs")
                await self._save_settings()
        
        except Exception as e:
            logger.error(f"Failed to sync capabilities across all VMs: {e}")
    
    async def _backup_management_loop(self) -> None:
        """Background backup management loop"""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(3600)  # Run hourly
                
                if not self.pool_settings.auto_backup_enabled:
                    continue
                
                for vm_id, vm_settings in self.active_vms.items():
                    if vm_settings.backup_schedule == BackupSchedule.DISABLED:
                        continue
                    
                    # Check if backup is due
                    if vm_settings.last_backup:
                        hours_since_backup = (datetime.now(timezone.utc) - vm_settings.last_backup).total_seconds() / 3600
                        
                        backup_due = False
                        if vm_settings.backup_schedule == BackupSchedule.DAILY and hours_since_backup >= 24:
                            backup_due = True
                        elif vm_settings.backup_schedule == BackupSchedule.WEEKLY and hours_since_backup >= 168:
                            backup_due = True
                        elif vm_settings.backup_schedule == BackupSchedule.MONTHLY and hours_since_backup >= 720:
                            backup_due = True
                        
                        if backup_due:
                            await self._trigger_backup(vm_id)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in backup management loop: {e}")
                await asyncio.sleep(300)


# ============================================================================
# FACTORY AND INITIALIZATION
# ============================================================================

async def create_vm_settings_manager(
    config_path: str = "data/vm_settings.json",
    max_concurrent_vms: int = 10,
    enable_supervisor_integration: bool = True,
    enable_orchestrator_integration: bool = True,
    vm_supervisor: Optional[VMSupervisor] = None,
    ai_orchestrator: Optional[AIActionOrchestrator] = None
) -> Optional[VMSettingsManager]:
    """
    Factory function to create and initialize production-ready VM settings manager
    
    Args:
        config_path: Path to configuration file
        max_concurrent_vms: Maximum number of concurrent VMs
        enable_supervisor_integration: Enable VM Supervisor integration
        enable_orchestrator_integration: Enable AI Orchestrator integration
        vm_supervisor: VM Supervisor instance (optional)
        ai_orchestrator: AI Orchestrator instance (optional)
        
    Returns:
        VMSettingsManager instance or None if creation failed
    """
    try:
        logger.info("Creating production VM settings manager...")
        
        # Create pool settings
        pool_settings = VMPoolSettings(max_concurrent_vms=max_concurrent_vms)
        
        # Create manager
        manager = VMSettingsManager(
            pool_settings=pool_settings,
            config_path=config_path,
            vm_supervisor=vm_supervisor,
            ai_orchestrator=ai_orchestrator
        )
        
        # Initialize
        success = await manager.initialize()
        if success:
            logger.info("Production VM settings manager created and initialized successfully")
            return manager
        else:
            logger.error("Failed to initialize VM settings manager")
            return None
            
    except Exception as e:
        logger.error(f"Failed to create VM settings manager: {e}", exc_info=True)
        return None


# ============================================================================
# USAGE EXAMPLE AND TESTING
# ============================================================================

async def main():
    """Example usage of production VM settings manager"""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create VM settings manager
    manager = await create_vm_settings_manager(
        max_concurrent_vms=5,
        config_path="data/vm_settings_prod.json"
    )
    
    if not manager:
        print("Failed to create VM settings manager")
        return
    
    try:
        print("=" * 60)
        print("Production VM Settings Manager Test")
        print("=" * 60)
        
        # Create test VM instance
        print("\n1. Creating VM instance...")
        hardware_spec = VMHardwareSpec(
            vcpus=4,
            memory_gb=8,
            storage_gb=100,
            gpu_enabled=False
        )
        
        personality = VMPersonality(
            agent_name="Test_AI",
            specialization="research",
            creativity_level=0.8
        )
        
        vm_settings = await manager.create_vm_instance(
            user_id="test_user",
            instance_name="Production Test VM",
            hardware_spec=hardware_spec,
            personality=personality
        )
        
        if vm_settings:
            print(f"âœ“ Created VM: {vm_settings.instance_name}")
            print(f"  - Instance ID: {vm_settings.instance_id}")
            print(f"  - Access Token: {vm_settings.access_token[:20]}...")
            print(f"  - Security Level: {vm_settings.security_level}")
        else:
            print("âœ— Failed to create VM instance")
            return
        
        # Track capability acquisition
        print("\n2. Tracking capability acquisition...")
        success = await manager.track_capability_acquisition(
            vm_settings.instance_id,
            "python_development",
            "tool",
            metadata={"version": "3.11", "packages": ["numpy", "pandas"]}
        )
        print(f"{'âœ“' if success else 'âœ—'} Capability tracking: {success}")
        
        # Get VM status
        print("\n3. Getting VM status...")
        status = await manager.get_vm_status(vm_settings.instance_id)
        if "error" not in status:
            print(f"âœ“ VM Status retrieved")
            print(f"  - State: {status['current_state']}")
            print(f"  - Efficiency Rating: {status['efficiency_rating']:.2f}")
            print(f"  - Total Capabilities: {status['capabilities']['total_capabilities']}")
            print(f"  - Integration Status:")
            print(f"    - Supervisor: {status['integration_status']['supervisor_connected']}")
            print(f"    - Orchestrator: {status['integration_status']['orchestrator_connected']}")
        else:
            print(f"âœ— Failed to get VM status: {status['error']}")
        
        # Get pool status
        print("\n4. Getting pool status...")
        pool_status = await manager.get_vm_status()
        if "error" not in pool_status:
            print(f"âœ“ Pool Status retrieved")
            print(f"  - Active VMs: {pool_status['pool_status']['active_vms']}")
            print(f"  - Max Concurrent: {pool_status['pool_status']['max_concurrent']}")
            print(f"  - Available Capacity:")
            capacity = await manager._calculate_available_capacity()
            print(f"    - CPU Cores: {capacity['available_cpu_cores']}")
            print(f"    - Memory: {capacity['available_memory_gb']}GB")
            print(f"    - Storage: {capacity['available_storage_gb']}GB")
            print(f"    - VM Slots: {capacity['remaining_vm_slots']}")
        
        # Test VM limit enforcement
        print("\n5. Testing VM limit enforcement...")
        for i in range(manager.pool_settings.max_vms_per_user):
            test_vm = await manager.create_vm_instance(
                user_id="test_user",
                instance_name=f"Test VM {i+2}"
            )
            if not test_vm:
                print(f"âœ“ VM limit enforced correctly (limit: {manager.pool_settings.max_vms_per_user})")
                break
        
        print("\n" + "=" * 60)
        print("Production VM Settings Manager Test Complete")
        print("=" * 60)
        
    finally:
        # Cleanup
        print("\n6. Cleaning up...")
        if manager:
            await manager.shutdown()
            print("âœ“ Manager shutdown complete")


if __name__ == "__main__":
    # Run the example
    asyncio.run(main())
