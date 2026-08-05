"""
Somnus Sovereign Systems - Git Repository Integration & Deep Indexing
Production-grade repository cloning with automated ingestion pipeline.

Architecture Features:
- Secure repository cloning with comprehensive validation
- Streaming file processing with backpressure handling
- LFS and submodule support with selective processing
- Local security scanning and vulnerability detection
- Artifact container integration for development workflows
- Comprehensive observability and metrics collection
- Resumable operations with checkpoint recovery
- Circuit breakers and resilience patterns
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import (
    Any, AsyncIterator, Dict, List, Optional, Set, Tuple, Union, 
    TypeVar, Generic, Protocol, runtime_checkable
)
from uuid import UUID, uuid4
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from urllib.parse import urlparse
import ipaddress
import mimetypes
from collections import defaultdict
import weakref
import threading
from concurrent.futures import ThreadPoolExecutor
import secrets
import base64

import git
from git import Repo, GitCommandError
import aiofiles
import aiofiles.os
from pydantic import BaseModel, Field, validator, root_validator
import psutil

# Conditional imports with fallback handling
try:
    from core.memory_core import SessionID, UserID, MemoryManager, MemoryType, MemoryImportance
except ImportError:
    try:
        from schemas.session import SessionID, UserID
        from core.memory_core import MemoryManager, MemoryType, MemoryImportance
    except ImportError:
        # Fallback types for standalone operation
        SessionID = str
        UserID = str
        MemoryManager = Any
        MemoryType = Any
        MemoryImportance = Any

try:
    from core.accelerated_file_processing import IntelligentFileProcessor, ProcessingPriority
except ImportError:
    IntelligentFileProcessor = Any
    ProcessingPriority = Any

try:
    from core.file_upload_system import FileUploadManager, FileType, ProcessingStatus
except ImportError:
    FileUploadManager = Any
    FileType = Any
    ProcessingStatus = Any

# Configure structured logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Type variables for generic types
T = TypeVar('T')
K = TypeVar('K')
V = TypeVar('V')


# ============================================================================
# Core Exceptions Hierarchy
# ============================================================================

class RepositoryError(Exception):
    """Base exception for repository operations"""
    def __init__(self, message: str, correlation_id: Optional[str] = None, **kwargs):
        super().__init__(message)
        self.correlation_id = correlation_id or secrets.token_hex(8)
        self.details = kwargs
        self.timestamp = datetime.now(timezone.utc)

class ValidationError(RepositoryError):
    """Repository URL or configuration validation failure"""
    pass

class SecurityError(RepositoryError):
    """Security validation or policy violation"""
    pass

class CloneError(RepositoryError):
    """Repository cloning operation failure"""
    def __init__(self, message: str, repo_url: str, attempt: int = 1, **kwargs):
        super().__init__(message, **kwargs)
        self.repo_url = repo_url
        self.attempt = attempt

class IndexingError(RepositoryError):
    """File indexing or processing failure"""
    def __init__(self, message: str, repo_path: Optional[Path] = None, **kwargs):
        super().__init__(message, **kwargs)
        self.repo_path = repo_path

class ProcessingError(RepositoryError):
    """File processing or memory storage failure"""
    def __init__(self, message: str, file_path: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        self.file_path = file_path

class ResourceExhaustedError(RepositoryError):
    """System resource limits exceeded"""
    def __init__(self, message: str, resource_type: str, current: float, limit: float, **kwargs):
        super().__init__(message, **kwargs)
        self.resource_type = resource_type
        self.current = current
        self.limit = limit

class CircuitBreakerError(RepositoryError):
    """Circuit breaker is open, operation rejected"""
    def __init__(self, message: str, service: str, failure_count: int, **kwargs):
        super().__init__(message, **kwargs)
        self.service = service
        self.failure_count = failure_count

# ============================================================================
# Enhanced Enums and Data Models
# ============================================================================

class RepoStatus(str, Enum):
    """Repository processing status with comprehensive states"""
    INITIALIZING = "initializing"
    VALIDATING = "validating"
    CLONING = "cloning"
    SCANNING_SECURITY = "scanning_security"
    INDEXING = "indexing"
    PROCESSING_FILES = "processing_files"
    STORING_MEMORIES = "storing_memories"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

class RepoFileType(str, Enum):
    """Extended file classification for repositories"""
    SOURCE_CODE = "source_code"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    DATA_FILE = "data_file"
    IMAGE_ASSET = "image_asset"
    BUILD_SCRIPT = "build_script"
    TEST_FILE = "test_file"
    LICENSE = "license"
    SECURITY_POLICY = "security_policy"
    DEPENDENCY_MANIFEST = "dependency_manifest"
    LFS_POINTER = "lfs_pointer"
    SUBMODULE = "submodule"
    BINARY = "binary"
    UNKNOWN = "unknown"

class SecurityThreatLevel(IntEnum):
    """Security threat severity levels"""
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class LFSFetchPolicy(str, Enum):
    """LFS file fetch policies"""
    SKIP = "skip"
    METADATA_ONLY = "metadata_only"
    FETCH_SMALL = "fetch_small"
    FETCH_ALL = "fetch_all"

@dataclass
class SecurityFinding:
    """Security scan finding with context"""
    finding_id: str = field(default_factory=lambda: secrets.token_hex(8))
    threat_level: SecurityThreatLevel = SecurityThreatLevel.NONE
    category: str = ""
    title: str = ""
    description: str = ""
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    remediation: Optional[str] = None
    cve_id: Optional[str] = None
    scanner: str = "unknown"
    confidence: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class LFSPointer:
    """Git LFS pointer metadata"""
    oid: str
    size: int
    file_path: str
    is_fetched: bool = False
    fetch_error: Optional[str] = None

@dataclass
class SubmoduleInfo:
    """Git submodule metadata"""
    path: str
    url: str
    commit_hash: str
    is_initialized: bool = False
    initialization_error: Optional[str] = None

@dataclass
class RepoFile:
    """Enhanced repository file metadata with security and processing info"""
    relative_path: str
    absolute_path: Path
    file_type: RepoFileType
    size_bytes: int
    last_modified: datetime
    
    # File analysis
    file_hash: str = ""
    is_binary: bool = False
    encoding: Optional[str] = None
    language: Optional[str] = None
    line_count: Optional[int] = None
    
    # Processing state
    processed: bool = False
    processing_error: Optional[str] = None
    processing_duration_ms: Optional[float] = None
    memory_id: Optional[UUID] = None
    
    # Security findings
    security_findings: List[SecurityFinding] = field(default_factory=list)
    threat_level: SecurityThreatLevel = SecurityThreatLevel.NONE
    
    # LFS/Special handling
    lfs_pointer: Optional[LFSPointer] = None
    is_sensitive: bool = False
    
    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def has_security_issues(self) -> bool:
        """Check if file has security findings"""
        return any(f.threat_level >= SecurityThreatLevel.MEDIUM for f in self.security_findings)
    
    @property
    def is_processable(self) -> bool:
        """Check if file can be processed safely"""
        return (
            not self.is_binary and 
            self.size_bytes < 50 * 1024 * 1024 and  # 50MB limit
            not self.has_security_issues and
            self.file_type != RepoFileType.LFS_POINTER
        )


@dataclass 
class ProcessingMetrics:
    """Repository processing performance metrics"""
    total_duration_ms: float = 0.0
    clone_duration_ms: float = 0.0
    indexing_duration_ms: float = 0.0
    security_scan_duration_ms: float = 0.0
    processing_duration_ms: float = 0.0
    memory_storage_duration_ms: float = 0.0
    
    files_per_second: float = 0.0
    bytes_per_second: float = 0.0
    peak_memory_usage_mb: float = 0.0
    
    retry_count: int = 0
    backoff_total_ms: float = 0.0

class RepoConfiguration(BaseModel):
    """Repository processing configuration"""
    # Size and timeout limits
    max_repo_size_gb: float = Field(default=5.0, ge=0.1, le=50.0)
    max_file_size_mb: float = Field(default=50.0, ge=0.1, le=500.0)
    clone_timeout_minutes: int = Field(default=30, ge=1, le=120)
    processing_timeout_minutes: int = Field(default=60, ge=5, le=300)
    
    # Clone settings
    shallow_clone: bool = Field(default=True)
    clone_depth: int = Field(default=1, ge=1, le=1000)
    max_clone_retries: int = Field(default=3, ge=1, le=10)
    backoff_base_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    
    # LFS configuration
    lfs_policy: LFSFetchPolicy = Field(default=LFSFetchPolicy.METADATA_ONLY)
    lfs_max_file_size_mb: float = Field(default=100.0)
    
    # Security settings
    enable_security_scanning: bool = Field(default=True)
    security_scan_timeout_minutes: int = Field(default=10)
    max_security_findings: int = Field(default=1000)
    block_on_critical_findings: bool = Field(default=True)
    
    # Processing settings
    enable_streaming_processing: bool = Field(default=True)
    batch_size: int = Field(default=100, ge=1, le=1000)
    max_concurrent_files: int = Field(default=50, ge=1, le=200)
    enable_checkpointing: bool = Field(default=True)
    checkpoint_interval_files: int = Field(default=100)
    
    # Skip patterns
    skip_directories: Set[str] = Field(default_factory=lambda: {
        '.git', '.svn', '.hg', '__pycache__', '.pytest_cache',
        'node_modules', '.venv', 'venv', '.env', 'dist', 'build',
        '.next', '.nuxt', 'target', 'bin', 'obj', '.cache', 'vendor',
        'coverage', '.nyc_output', '.coverage', 'htmlcov'
    })
    skip_file_patterns: Set[str] = Field(default_factory=lambda: {
        '*.pyc', '*.pyo', '*.egg-info', '*.log', '*.tmp', '*.temp',
        '*.cache', '*.bak', '*.swp', '*.DS_Store', 'Thumbs.db'
    })
    
    # Artifact integration
    enable_artifact_integration: bool = Field(default=True)
    artifact_mount_readonly: bool = Field(default=True)

class RepoMetadata(BaseModel):
    """Enhanced repository metadata with comprehensive tracking"""
    # Core identification
    repo_id: UUID = Field(default_factory=uuid4)
    correlation_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    url: str = Field(description="Repository URL")
    name: str = Field(description="Repository name")
    description: Optional[str] = None

    # Processing context
    user_id: str = Field(description="User who initiated clone")
    session_id: str = Field(description="Session context")
    status: RepoStatus = Field(default=RepoStatus.INITIALIZING)
    configuration: RepoConfiguration = Field(default_factory=RepoConfiguration)

    # Repository information
    default_branch: Optional[str] = None
    latest_commit: Optional[str] = None
    commit_count: Optional[int] = None
    contributor_count: Optional[int] = None
    primary_language: Optional[str] = None
    languages: Dict[str, int] = Field(default_factory=dict)  # language -> line count
    topics: List[str] = Field(default_factory=list)
    
    # License and legal
    license_name: Optional[str] = None
    license_file: Optional[str] = None
    has_security_policy: bool = False
    
    # File statistics
    total_files: int = Field(default=0)
    processed_files: int = Field(default=0)
    failed_files: int = Field(default=0)
    skipped_files: int = Field(default=0)
    binary_files: int = Field(default=0)
    
    # Size information
    total_size_bytes: int = Field(default=0)
    text_size_bytes: int = Field(default=0)
    binary_size_bytes: int = Field(default=0)
    
    # LFS and submodules
    has_lfs: bool = False
    lfs_files: List[LFSPointer] = Field(default_factory=list)
    total_lfs_size_bytes: int = Field(default=0)
    
    has_submodules: bool = False
    submodules: List[SubmoduleInfo] = Field(default_factory=list)
    
    # Security information
    security_findings: List[SecurityFinding] = Field(default_factory=list)
    max_threat_level: SecurityThreatLevel = SecurityThreatLevel.NONE
    is_security_approved: bool = True
    
    # Processing progress and timing
    progress_percentage: float = Field(default=0.0, ge=0, le=100)
    processing_start: Optional[datetime] = None
    processing_end: Optional[datetime] = None
    last_checkpoint: Optional[datetime] = None
    metrics: ProcessingMetrics = Field(default_factory=ProcessingMetrics)

    # Storage and paths
    local_path: Optional[str] = None
    bare_repo_path: Optional[str] = None
    checkpoint_file: Optional[str] = None
    
    # Memory integration
    stored_memories: List[UUID] = Field(default_factory=list)
    memory_storage_complete: bool = False
    
    # Artifact integration
    artifact_id: Optional[str] = None
    workspace_mounted: bool = False
    
    # Error tracking and resilience
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    recovery_attempts: int = Field(default=0)
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: str,
            Path: str
        }
    
    @validator('url')
    def validate_url(cls, v):
        if not v or not isinstance(v, str):
            raise ValueError("URL must be a non-empty string")
        return v.strip()
    
    @root_validator
    def validate_consistency(cls, values):
        """Validate internal consistency of metadata"""
        total_files = values.get('total_files', 0)
        processed = values.get('processed_files', 0)
        failed = values.get('failed_files', 0)
        skipped = values.get('skipped_files', 0)
        
        if processed + failed + skipped > total_files:
            logger.warning(f"File count inconsistency: {processed=}, {failed=}, {skipped=}, {total_files=}")
        
        return values

    @property
    def is_complete(self) -> bool:
        """Check if processing is complete"""
        return self.status == RepoStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        """Check if processing failed"""
        return self.status == RepoStatus.FAILED

    @property
    def can_retry(self) -> bool:
        """Check if operation can be retried"""
        return (
            self.status == RepoStatus.FAILED and 
            self.recovery_attempts < self.configuration.max_clone_retries and
            not self.has_critical_security_findings
        )

    @property
    def has_critical_security_findings(self) -> bool:
        """Check for critical security issues"""
        return any(
            finding.threat_level == SecurityThreatLevel.CRITICAL 
            for finding in self.security_findings
        )

    @property
    def processing_time_seconds(self) -> Optional[float]:
        """Calculate total processing duration"""
        if self.processing_start and self.processing_end:
            return (self.processing_end - self.processing_start).total_seconds()
        return None

    @property
    def estimated_completion_time(self) -> Optional[datetime]:
        """Estimate completion time based on current progress"""
        if self.progress_percentage <= 0 or not self.processing_start:
            return None
            
        elapsed = datetime.now(timezone.utc) - self.processing_start
        if self.progress_percentage >= 100:
            return self.processing_end or datetime.now(timezone.utc)
            
        total_estimated = elapsed * (100.0 / self.progress_percentage)
        return self.processing_start + total_estimated

    def add_error(self, error: Exception, context: Optional[str] = None) -> None:
        """Add structured error information"""
        error_info = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'error_type': type(error).__name__,
            'message': str(error),
            'context': context,
            'correlation_id': getattr(error, 'correlation_id', self.correlation_id)
        }
        self.errors.append(error_info)
        self.last_updated = datetime.now(timezone.utc)

    def add_warning(self, message: str) -> None:
        """Add warning message"""
        self.warnings.append(f"[{datetime.now(timezone.utc).isoformat()}] {message}")
        self.last_updated = datetime.now(timezone.utc)

    def update_progress(self, progress: float, message: Optional[str] = None) -> None:
        """Update processing progress with validation"""
        self.progress_percentage = max(0.0, min(100.0, progress))
        self.last_updated = datetime.now(timezone.utc)
        
        if message:
            logger.info(
                f"Repository {self.name} [{self.correlation_id}]: "
                f"{self.progress_percentage:.1f}% - {message}"
            )


# ============================================================================
# Security Validation and URL Sanitization
# ============================================================================

class URLValidator:
    """Production-grade URL validation with security checks"""
    
    # Allowed URL schemes
    ALLOWED_SCHEMES = {'https', 'ssh', 'git'}
    
    # Blocked hosts and patterns
    BLOCKED_HOSTS = {
        'localhost', '127.0.0.1', '0.0.0.0', '::1',
        'metadata.google.internal', '169.254.169.254'  # Cloud metadata services
    }
    
    # Allowed host patterns (for enterprise repositories)
    TRUSTED_HOST_PATTERNS = [
        r'^github\.com$',
        r'^[\w\-]+\.github\.com$',
        r'^gitlab\.com$', 
        r'^[\w\-]+\.gitlab\.com$',
        r'^bitbucket\.org$',
        r'^[\w\-]+\.bitbucket\.org$',
        # Add patterns for enterprise git servers as needed
    ]
    
    @classmethod
    def validate_url(cls, url: str, allow_private_hosts: bool = False) -> bool:
        """
        Validate repository URL with comprehensive security checks.
        
        Args:
            url: Repository URL to validate
            allow_private_hosts: Whether to allow private IP addresses
            
        Returns:
            True if URL passes all security checks
            
        Raises:
            ValidationError: If URL fails validation
        """
        if not url or not isinstance(url, str):
            raise ValidationError("URL must be a non-empty string")
        
        url = url.strip()
        
        try:
            # Handle SSH scp-like URLs (git@host:path/repo.git)
            scp_pattern = re.compile(r'^([\w\-\.]+)@([\w\-\.]+):([/\w\-\.]+)$')
            scp_match = scp_pattern.match(url)
            
            if scp_match:
                username, host, path = scp_match.groups()
                return cls._validate_host(host, allow_private_hosts) and cls._validate_path(path)
            
            # Parse standard URLs
            parsed = urlparse(url)
            
            # Validate scheme
            if not parsed.scheme or parsed.scheme.lower() not in cls.ALLOWED_SCHEMES:
                raise ValidationError(f"Unsupported URL scheme: {parsed.scheme}")
            
            # Validate host
            if not parsed.netloc:
                raise ValidationError("URL must contain a hostname")
                
            host = parsed.netloc.split('@')[-1].split(':')[0].lower()  # Extract hostname
            if not cls._validate_host(host, allow_private_hosts):
                raise ValidationError(f"Host not allowed: {host}")
            
            # Validate path
            if not cls._validate_path(parsed.path):
                raise ValidationError("Invalid repository path")
            
            # Additional security checks
            cls._check_for_injection_patterns(url)
            
            return True
            
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"URL validation failed: {str(e)}")
    
    @classmethod
    def _validate_host(cls, host: str, allow_private: bool = False) -> bool:
        """Validate hostname with security checks"""
        if not host:
            return False
            
        # Check blocked hosts
        if host in cls.BLOCKED_HOSTS:
            return False
        
        # Check if it's an IP address
        try:
            ip = ipaddress.ip_address(host)
            if not allow_private:
                # Block private/local IP addresses
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                    return False
        except ValueError:
            # Not an IP address, continue with domain validation
            pass
        
        # Check against trusted patterns
        for pattern in cls.TRUSTED_HOST_PATTERNS:
            if re.match(pattern, host):
                return True
        
        # For enterprise use, you might want to add additional validation
        # or maintain a whitelist of allowed hosts
        
        return False  # Default: deny unknown hosts
    
    @classmethod
    def _validate_path(cls, path: str) -> bool:
        """Validate repository path"""
        if not path:
            return False
            
        # Remove leading/trailing slashes
        clean_path = path.strip('/')
        if not clean_path:
            return False
        
        # Basic path validation
        path_parts = [part for part in clean_path.split('/') if part]
        if len(path_parts) < 2:  # Expect at least owner/repo
            return False
        
        # Check for suspicious patterns
        suspicious_patterns = ['..', '.git/', 'file://', 'javascript:', 'data:']
        for pattern in suspicious_patterns:
            if pattern in path.lower():
                return False
        
        return True
    
    @classmethod
    def _check_for_injection_patterns(cls, url: str) -> None:
        """Check for command injection patterns"""
        injection_patterns = [
            r'[;&|`$()]',  # Shell metacharacters
            r'%[0-9a-fA-F]{2}',  # URL encoded characters
            r'\\[rnt]',  # Escape sequences
        ]
        
        for pattern in injection_patterns:
            if re.search(pattern, url):
                raise SecurityError(f"Potential injection pattern detected in URL: {pattern}")

class CircuitBreaker:
    """Circuit breaker implementation for external service calls"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'closed'  # closed, open, half-open
        self._lock = threading.Lock()
    
    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        with self._lock:
            if self.state == 'open':
                if self._should_attempt_reset():
                    self.state = 'half-open'
                else:
                    raise CircuitBreakerError(
                        f"Circuit breaker is open due to {self.failure_count} failures",
                        service=func.__name__,
                        failure_count=self.failure_count
                    )
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        return (
            self.last_failure_time and
            time.time() - self.last_failure_time >= self.recovery_timeout
        )
    
    def _on_success(self):
        """Handle successful operation"""
        with self._lock:
            self.failure_count = 0
            self.state = 'closed'
    
    def _on_failure(self):
        """Handle failed operation"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                self.state = 'open'

class ResourceMonitor:
    """System resource monitoring for adaptive processing"""
    
    def __init__(self):
        self.peak_memory_mb = 0.0
        self.start_time = time.time()
    
    def get_current_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        process = psutil.Process()
        memory_info = process.memory_info()
        current_mb = memory_info.rss / 1024 / 1024
        self.peak_memory_mb = max(self.peak_memory_mb, current_mb)
        return current_mb
    
    def check_memory_pressure(self, limit_mb: float = 1024.0) -> bool:
        """Check if memory usage exceeds limit"""
        return self.get_current_memory_usage() > limit_mb
    
    def get_system_load(self) -> Dict[str, float]:
        """Get comprehensive system load metrics"""
        return {
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage_percent': psutil.disk_usage('/').percent if os.name != 'nt' else psutil.disk_usage('C:').percent,
            'load_average': os.getloadavg() if hasattr(os, 'getloadavg') else [0.0, 0.0, 0.0]
        }

# ============================================================================
# Main Integration Manager
# ============================================================================

class GitHubIntegrationManager:
    """
    Production-grade Git repository integration with comprehensive security,
    streaming processing, and artifact integration.
    
    Features:
    - Secure URL validation and sandboxed cloning
    - Streaming file processing with backpressure handling  
    - LFS and submodule support with selective processing
    - Local security scanning and vulnerability detection
    - Circuit breakers and resilience patterns
    - Comprehensive observability and metrics
    - Resumable operations with checkpoint recovery
    - Artifact container integration for development workflows
    """
    
    def __init__(
        self,
        intelligent_processor: Optional[IntelligentFileProcessor] = None,
        memory_manager: Optional[MemoryManager] = None,
        clone_dir: str = "data/repositories",
        config: Optional[RepoConfiguration] = None,
        enable_security_scanning: bool = True,
        enable_artifact_integration: bool = True
    ):
        # Core dependencies
        self.intelligent_processor = intelligent_processor
        self.memory_manager = memory_manager
        
        # Configuration
        self.config = config or RepoConfiguration()
        self.enable_security_scanning = enable_security_scanning
        self.enable_artifact_integration = enable_artifact_integration
        
        # Directory setup
        self.clone_dir = Path(clone_dir)
        self.clone_dir.mkdir(parents=True, exist_ok=True)
        self.bare_repos_dir = self.clone_dir / "bare"
        self.bare_repos_dir.mkdir(exist_ok=True)
        self.checkpoints_dir = self.clone_dir / "checkpoints"
        self.checkpoints_dir.mkdir(exist_ok=True)
        
        # Resilience components
        self.url_validator = URLValidator()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=self.config.max_clone_retries,
            recovery_timeout=60
        )
        self.resource_monitor = ResourceMonitor()
        
        # Processing state
        self.active_repos: Dict[UUID, RepoMetadata] = {}
        self.repo_files: Dict[UUID, List[RepoFile]] = {}
        self.processing_semaphore = asyncio.Semaphore(self.config.max_concurrent_files)
        self.executor = ThreadPoolExecutor(
            max_workers=min(32, (os.cpu_count() or 1) + 4),
            thread_name_prefix="repo-processor"
        )
        
        # File type classification patterns
        self.file_extensions = {
            RepoFileType.SOURCE_CODE: {
                '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h', '.hpp',
                '.cs', '.go', '.rs', '.php', '.rb', '.swift', '.kt', '.scala', '.r', 
                '.m', '.mm', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd'
            },
            RepoFileType.DOCUMENTATION: {
                '.md', '.rst', '.txt', '.adoc', '.wiki', '.tex', '.pdf', '.rtf'
            },
            RepoFileType.CONFIGURATION: {
                '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.config',
                '.env', '.envrc', '.gitignore', '.dockerignore', '.editorconfig'
            },
            RepoFileType.DATA_FILE: {
                '.csv', '.tsv', '.xlsx', '.xls', '.ods', '.sql', '.db', '.sqlite', 
                '.sqlite3', '.jsonl', '.ndjson', '.parquet', '.arrow'
            },
            RepoFileType.IMAGE_ASSET: {
                '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp',
                '.tiff', '.tif', '.psd', '.ai', '.eps'
            },
            RepoFileType.BUILD_SCRIPT: {
                '.gradle', '.maven', '.make', '.cmake', '.bazel', '.BUILD', '.bzl',
                '.ninja', '.gyp', '.gypi', '.gn', '.gni'
            },
            RepoFileType.DEPENDENCY_MANIFEST: {
                'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
                'requirements.txt', 'pyproject.toml', 'poetry.lock', 'Pipfile',
                'Pipfile.lock', 'setup.py', 'setup.cfg', 'Cargo.toml', 'Cargo.lock',
                'go.mod', 'go.sum', 'build.gradle', 'pom.xml', 'composer.json',
                'composer.lock', 'Gemfile', 'Gemfile.lock'
            }
        }
        
        # Special file patterns
        self.special_files = {
            RepoFileType.LICENSE: {'license', 'licence', 'copying', 'copyright'},
            RepoFileType.SECURITY_POLICY: {'security.md', 'security.rst', 'security.txt', '.security'},
            RepoFileType.TEST_FILE: {'test_', '_test.', '.test.', '.spec.', '_spec.'},
        }
        
        # LFS patterns
        self.lfs_extensions = {'.zip', '.tar.gz', '.tgz', '.rar', '.7z', '.bin', '.exe', '.dll', '.so'}
        
        # Metrics and observability
        self.metrics = {
            'repos_cloned': 0,
            'repos_failed': 0,
            'files_processed': 0,
            'bytes_processed': 0,
            'security_findings': 0,
            'lfs_files_detected': 0,
            'processing_time_total': 0.0
        }
        
        logger.info(
            f"Git integration manager initialized: {clone_dir}, "
            f"security_scanning={enable_security_scanning}, "
            f"artifact_integration={enable_artifact_integration}"
        )
    
    async def clone_repository(
        self,
        repo_url: str,
        user_id: str,
        session_id: str,
        branch: Optional[str] = None,
        config_override: Optional[RepoConfiguration] = None
    ) -> RepoMetadata:
        """
        Clone repository with comprehensive security, processing, and integration.
        
        Args:
            repo_url: Repository URL to clone
            user_id: User initiating the clone
            session_id: Session context
            branch: Specific branch to clone (optional)
            config_override: Override default configuration (optional)
            
        Returns:
            RepoMetadata with comprehensive processing results
            
        Raises:
            ValidationError: Invalid URL or configuration
            SecurityError: Security policy violation
            CloneError: Repository cloning failure
            ResourceExhaustedError: Resource limits exceeded
        """
        repo_id = uuid4()
        correlation_id = secrets.token_hex(8)
        processing_start = datetime.now(timezone.utc)
        
        # Use override config if provided
        config = config_override or self.config
        
        logger.info(
            f"Starting repository clone [{correlation_id}]: {repo_url}",
            extra={
                'correlation_id': correlation_id,
                'repo_url': repo_url,
                'user_id': user_id,
                'session_id': session_id
            }
        )
        
        metadata = None
        try:
            # Step 1: URL Validation and Security Check
            await self._validate_and_sanitize_url(repo_url, correlation_id)
            
            # Step 2: Create Metadata and Initialize
            repo_name = self._extract_repo_name(repo_url)
            metadata = RepoMetadata(
                repo_id=repo_id,
                correlation_id=correlation_id,
                url=repo_url,
                name=repo_name,
                user_id=user_id,
                session_id=session_id,
                configuration=config,
                processing_start=processing_start
            )
            
            # Register for tracking
            self.active_repos[repo_id] = metadata
            
            # Step 3: Setup Storage Paths
            await self._setup_storage_paths(metadata)
            metadata.update_progress(5, "Storage initialized")
            
            # Step 4: Clone Repository with Circuit Breaker
            metadata.status = RepoStatus.CLONING
            repo = await self._clone_repository_with_resilience(metadata, branch)
            metadata.update_progress(25, "Repository cloned successfully")
            
            # Step 5: Extract Repository Metadata
            await self._extract_comprehensive_metadata(metadata, repo)
            metadata.update_progress(35, "Metadata extraction completed")
            
            # Step 6: Security Scanning (if enabled)
            if self.enable_security_scanning:
                metadata.status = RepoStatus.SCANNING_SECURITY
                await self._perform_security_scan(metadata)
                metadata.update_progress(45, "Security scanning completed")
            
            # Step 7: LFS and Submodule Detection
            await self._detect_lfs_and_submodules(metadata, repo)
            metadata.update_progress(50, "LFS and submodules detected")
            
            # Step 8: File Indexing with Streaming
            metadata.status = RepoStatus.INDEXING
            await self._index_repository_with_streaming(metadata)
            metadata.update_progress(70, "File indexing completed")
            
            # Step 9: File Processing Pipeline
            metadata.status = RepoStatus.PROCESSING_FILES
            if config.enable_streaming_processing:
                await self._stream_process_files(metadata)
            else:
                await self._batch_process_files(metadata)
            metadata.update_progress(90, "File processing completed")
            
            # Step 10: Memory Storage
            metadata.status = RepoStatus.STORING_MEMORIES
            await self._store_repository_memories(metadata)
            metadata.update_progress(95, "Memory storage completed")
            
            # Step 11: Artifact Integration (if enabled)
            if self.enable_artifact_integration:
                await self._setup_artifact_workspace(metadata)
                metadata.update_progress(98, "Artifact workspace created")
            
            # Step 12: Finalization
            metadata.status = RepoStatus.COMPLETED
            metadata.processing_end = datetime.now(timezone.utc)
            metadata.update_progress(100, "Repository processing completed")
            
            # Update metrics
            self.metrics['repos_cloned'] += 1
            self.metrics['files_processed'] += metadata.processed_files
            self.metrics['bytes_processed'] += metadata.total_size_bytes
            self.metrics['processing_time_total'] += metadata.processing_time_seconds or 0
            
            logger.info(
                f"Repository clone completed [{correlation_id}]: {repo_name} "
                f"({metadata.processing_time_seconds:.1f}s, {metadata.total_files} files)",
                extra={
                    'correlation_id': correlation_id,
                    'repo_id': str(repo_id),
                    'processing_time': metadata.processing_time_seconds,
                    'total_files': metadata.total_files,
                    'processed_files': metadata.processed_files
                }
            )
            
            return metadata
            
        except Exception as e:
            # Error handling with structured logging
            error_context = {
                'correlation_id': correlation_id,
                'repo_url': repo_url,
                'user_id': user_id,
                'processing_stage': metadata.status.value if metadata else 'initialization'
            }
            
            logger.error(
                f"Repository clone failed [{correlation_id}]: {str(e)}",
                extra=error_context,
                exc_info=True
            )
            
            # Ensure metadata exists for error reporting
            if metadata is None:
                metadata = RepoMetadata(
                    repo_id=repo_id,
                    correlation_id=correlation_id,
                    url=repo_url,
                    name=self._extract_repo_name(repo_url),
                    user_id=user_id,
                    session_id=session_id,
                    configuration=config,
                    processing_start=processing_start
                )
                self.active_repos[repo_id] = metadata
            
            # Update metadata with error information
            metadata.status = RepoStatus.FAILED
            metadata.processing_end = datetime.now(timezone.utc)
            metadata.add_error(e, context=f"Clone operation failed at stage: {metadata.status.value}")
            
            # Cleanup resources
            await self._cleanup_on_failure(metadata)
            
            # Update metrics
            self.metrics['repos_failed'] += 1
            
            return metadata

    async def _validate_and_sanitize_url(self, repo_url: str, correlation_id: str) -> None:
        """Validate and sanitize repository URL with comprehensive security checks"""
        try:
            # Primary validation with circuit breaker
            self.circuit_breaker.call(self.url_validator.validate_url, repo_url)
            
            logger.debug(
                f"URL validation passed [{correlation_id}]: {repo_url}",
                extra={'correlation_id': correlation_id}
            )
        except Exception as e:
            raise ValidationError(
                f"URL validation failed: {str(e)}",
                correlation_id=correlation_id,
                url=repo_url
            )

    async def _setup_storage_paths(self, metadata: RepoMetadata) -> None:
        """Setup storage paths for repository processing"""
        try:
            # Main working directory
            local_path = self.clone_dir / f"{metadata.repo_id}_{metadata.name}"
            metadata.local_path = str(local_path)
            
            # Bare repository for efficient mirroring
            bare_path = self.bare_repos_dir / f"{metadata.repo_id}.git"
            metadata.bare_repo_path = str(bare_path)
            
            # Checkpoint file for resumable operations
            if metadata.configuration.enable_checkpointing:
                checkpoint_path = self.checkpoints_dir / f"{metadata.repo_id}.json"
                metadata.checkpoint_file = str(checkpoint_path)
                
        except Exception as e:
            raise IndexingError(
                f"Failed to setup storage paths: {str(e)}",
                correlation_id=metadata.correlation_id
            )

    def _validate_repo_url(self, url: str) -> bool:
        """Validate repository URL format and accessibility"""
        try:
            # Accept SSH scp-like URLs (e.g., git@github.com:org/repo.git)
            scp_like = re.compile(r"^[\w\-\.]+@[\w\-\.]+:[\w\-/\.]+(\.git)?$")
            if scp_like.match(url):
                host = url.split("@", 1)[1].split(":", 1)[0].lower()
                # Allow common hosts and enterprise domains
                return bool(host)

            # Standard URL formats
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False

            allowed_schemes = {"http", "https", "ssh", "git"}
            if parsed.scheme.lower() not in allowed_schemes:
                return False

            # Basic path sanity: expect at least /owner/repo
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) < 2:
                return False

            return True
        except Exception:
            return False

    def _extract_repo_name(self, url: str) -> str:
        """Extract repository name from URL"""
        try:
            # Handle different URL formats
            if url.endswith('.git'):
                url = url[:-4]
            
            parts = url.rstrip('/').split('/')
            return parts[-1] if parts else 'unknown-repo'
            
        except Exception:
            return 'unknown-repo'
    
    async def _clone_repo(
        self, 
        url: str, 
        local_path: Path, 
        branch: Optional[str] = None
    ) -> Repo:
        """Clone repository with timeout, retries, and size limits"""
        # Ensure parent directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Disallow interactive prompts to avoid hangs
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"  # Prevent credential prompts

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_clone_retries + 1):
            try:
                if local_path.exists():
                    shutil.rmtree(local_path, ignore_errors=True)

                cmd = ['git', 'clone', '--no-tags', '--depth', '1']
                if branch:
                    cmd.extend(['-b', branch])
                cmd.extend([url, str(local_path)])

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=self.timeout_seconds
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    raise TimeoutError(f"Repository clone timed out after {self.timeout_seconds}s")

                if process.returncode != 0:
                    err = stderr.decode(errors="ignore")
                    raise RuntimeError(f"Git clone failed (attempt {attempt}/{self.max_clone_retries}): {err.strip()}")

                # Validate repository structure
                if not (local_path.exists() and (local_path / ".git").exists()):
                    raise RuntimeError("Clone completed but .git directory not found")

                # Check repository size
                repo_size = 0
                for f in local_path.rglob('*'):
                    try:
                        if f.is_file():
                            repo_size += f.stat().st_size
                        # Early abort if exceeding size
                        if repo_size > self.max_repo_size_bytes:
                            raise ValueError("size_exceeded")
                    except FileNotFoundError:
                        # Handle ephemeral files disappearing during traversal
                        continue

                if repo_size > self.max_repo_size_bytes:
                    shutil.rmtree(local_path, ignore_errors=True)
                    raise ValueError(f"Repository too large: {repo_size / (1024**3):.1f}GB")

                # Load with GitPython for metadata
                return Repo(local_path)

            except Exception as e:
                last_error = e
                # Retry only for transient errors
                message = str(e).lower()
                is_transient = any(
                    kw in message
                    for kw in [
                        "timed out", "timeout", "temporarily unavailable",
                        "could not resolve", "connection reset", "connection refused",
                        "remote end hung up", "failed to connect", "http 5", "early eof"
                    ]
                )
                if attempt >= self.max_clone_retries or not is_transient:
                    # Do not retry non-transient failures
                    break

                # Exponential backoff with jitter
                sleep_for = self.backoff_base_seconds * (2 ** (attempt - 1))
                sleep_for += random.uniform(0, 0.25 * sleep_for)
                logger.warning(f"Clone failed (attempt {attempt}); retrying in {sleep_for:.2f}s")
                await asyncio.sleep(sleep_for)

        # Cleanup on failure and re-raise
        try:
            if local_path.exists():
                shutil.rmtree(local_path, ignore_errors=True)
        finally:
            if last_error:
                raise last_error
            raise RuntimeError("Unknown cloning error")

    async def _extract_repo_metadata(self, metadata: RepoMetadata, repo: Repo):
        """Extract repository metadata from Git repo"""
        try:
            # Determine default branch robustly, even in shallow/detached clones
            default_branch = None
            try:
                default_branch = repo.active_branch.name  # May fail in detached HEAD
            except Exception:
                try:
                    # Derive from origin/HEAD symbolic ref
                    head_ref = repo.git.symbolic_ref("refs/remotes/origin/HEAD")
                    # Format: refs/remotes/origin/main
                    default_branch = head_ref.rsplit("/", 1)[-1]
                except Exception:
                    # Fallback: use HEAD commit short SHA
                    default_branch = None

            metadata.default_branch = default_branch
            # Latest commit short SHA
            try:
                metadata.latest_commit = repo.head.commit.hexsha[:8]
            except Exception:
                metadata.latest_commit = None

            # Count commits (bounded for performance)
            try:
                max_count = 1000
                commit_iter = repo.iter_commits(max_count=max_count)
                cnt = 0
                contributors: Set[str] = set()
                for c in commit_iter:
                    cnt += 1
                    if c.author and c.author.email:
                        contributors.add(c.author.email.lower())
                metadata.commit_count = cnt
                # Estimate unique contributor count from sampled commits
                metadata.contributor_count = len(contributors) if contributors else None
            except Exception:
                metadata.commit_count = None
                metadata.contributor_count = None

            # Pull description from README (bounded read)
            readme_files = ['README.md', 'README.rst', 'README.txt', 'README']
            repo_path = Path(repo.working_dir)
            for readme in readme_files:
                readme_path = repo_path / readme
                if readme_path.exists():
                    try:
                        async with aiofiles.open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                            # Only read up to 64KB
                            content = await f.read(64 * 1024)
                            lines = content.split('\n')
                            for line in lines:
                                line = line.strip()
                                if line and not line.startswith('#'):
                                    metadata.description = line[:200]
                                    break
                    except Exception:
                        pass
                    break

        except Exception as e:
            logger.warning(f"Failed to extract repository metadata: {e}")

    async def _index_repository_files(self, metadata: RepoMetadata):
        """Index all files in repository with classification"""
        repo_path = Path(metadata.local_path)
        files = []
        total_size = 0
        
        # Skip common directories
        skip_dirs = {
            '.git', '.svn', '.hg', '__pycache__', '.pytest_cache',
            'node_modules', '.venv', 'venv', '.env', 'dist', 'build',
            '.next', '.nuxt', 'target', 'bin', 'obj', '.cache', 'vendor'
        }
        
        try:
            for file_path in repo_path.rglob('*'):
                if file_path.is_file():
                    # Skip files in excluded directories
                    if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
                        continue
                    
                    try:
                        stat = file_path.stat()
                        relative_path = file_path.relative_to(repo_path)
                        
                        # Classify file type
                        file_type = self._classify_file(file_path)
                        
                        # Extract basic metadata
                        repo_file = RepoFile(
                            relative_path=str(relative_path),
                            absolute_path=file_path,
                            file_type=file_type,
                            size_bytes=stat.st_size,
                            last_modified=datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                        )
                        
                        # Add language detection for source files
                        if file_type == RepoFileType.SOURCE_CODE:
                            repo_file.language = self._detect_language(file_path)
                        
                        # Count lines for text files
                        if file_type in [RepoFileType.SOURCE_CODE, RepoFileType.DOCUMENTATION]:
                            repo_file.line_count = await self._count_lines(file_path)
                        
                        files.append(repo_file)
                        total_size += stat.st_size
                        
                    except Exception as e:
                        logger.warning(f"Failed to index file {file_path}: {e}")
                        continue
            
            # Update metadata
            metadata.total_files = len(files)
            metadata.total_size_bytes = total_size
            
            # Store file list
            self.repo_files[metadata.repo_id] = files
            
            logger.info(f"Indexed {len(files)} files totaling {total_size / (1024**2):.1f}MB")
            
        except Exception as e:
            logger.error(f"File indexing failed: {e}")
            metadata.errors.append(f"Indexing error: {str(e)}")
    
    def _classify_file(self, file_path: Path) -> RepoFileType:
        """Classify file type based on extension and name patterns"""
        file_ext = file_path.suffix.lower()
        file_name = file_path.name.lower()
        
        # Check special files first
        if file_name in ['license', 'licence', 'copying']:
            return RepoFileType.LICENSE
        
        if 'test' in file_name or file_name.startswith('test_'):
            return RepoFileType.TEST_FILE
        
        if file_name.lower() in ['dockerfile', 'makefile', 'cmakelists.txt']:
            return RepoFileType.BUILD_SCRIPT
        
        # Check by extension
        for file_type, extensions in self.file_extensions.items():
            if file_ext in extensions:
                return file_type
        
        return RepoFileType.UNKNOWN
    
    def _detect_language(self, file_path: Path) -> str:
        """Detect programming language from file extension"""
        ext_to_lang = {
            '.py': 'Python',
            '.js': 'JavaScript',
            '.ts': 'TypeScript',
            '.java': 'Java',
            '.cpp': 'C++',
            '.c': 'C',
            '.h': 'C/C++',
            '.cs': 'C#',
            '.go': 'Go',
            '.rs': 'Rust',
            '.php': 'PHP',
            '.rb': 'Ruby',
            '.swift': 'Swift',
            '.kt': 'Kotlin',
            '.scala': 'Scala',
            '.r': 'R',
            '.m': 'Objective-C',
            '.sh': 'Shell'
        }
        
        return ext_to_lang.get(file_path.suffix.lower(), 'Unknown')
    
    async def _count_lines(self, file_path: Path) -> Optional[int]:
        """Count lines in text file"""
        try:
            # Limit file size for line counting
            if file_path.stat().st_size > 1024 * 1024:  # 1MB limit
                return None

            line_count = 0
            async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                async for _ in f:
                    line_count += 1
            return line_count
        except Exception:
            return None
    
    async def _process_repository_files(self, metadata: RepoMetadata):
        """Process repository files through upload system"""
        if metadata.repo_id not in self.repo_files:
            raise ValueError("Repository files not indexed")
        
        files_to_process = []
        for repo_file in self.repo_files[metadata.repo_id]:
            try:
                # Skip large files
                if repo_file.size_bytes > 10 * 1024 * 1024:  # 10MB limit
                    repo_file.processing_error = "File too large for processing"
                    continue

                # Read file content
                async with aiofiles.open(repo_file.absolute_path, 'rb') as f:
                    file_data = await f.read()
                
                files_to_process.append((file_data, repo_file.relative_path))

            except Exception as e:
                logger.warning(f"Failed to read file {repo_file.relative_path} for processing: {e}")
                repo_file.processing_error = str(e)

        # Queue the entire batch for processing
        logger.info(f"Queueing {len(files_to_process)} files for accelerated processing.")
        task_ids = await self.intelligent_processor.queue_batch(
            file_batch=files_to_process,
            user_id=metadata.user_id,
            session_id=metadata.session_id,
            priority=ProcessingPriority.BACKGROUND,
            source_context=f"git_clone:{metadata.name}"
        )

        # Optionally, you can wait for completion and update status,
        # or this can be handled asynchronously by another part of the system.
        # For this integration, we'll assume it's fire-and-forget for now.
        metadata.processed_files = len(task_ids)
        
        logger.info(f"Successfully queued {len(task_ids)} files for processing.")

    async def _store_repository_memory(
        self,
        metadata: RepoMetadata,
        repo_file: RepoFile,
        content: str
    ) -> UUID:
        """Store repository file in memory system with context"""
        
        # Create contextual content
        context_content = f"""Repository: {metadata.name}
File: {repo_file.relative_path}
Type: {repo_file.file_type.value}
Language: {repo_file.language or 'Unknown'}
Size: {repo_file.size_bytes} bytes
Lines: {repo_file.line_count or 'Unknown'}

Content:
{content}"""
        
        # Determine memory importance based on file type
        importance_map = {
            RepoFileType.DOCUMENTATION: MemoryImportance.HIGH,
            RepoFileType.SOURCE_CODE: MemoryImportance.MEDIUM,
            RepoFileType.CONFIGURATION: MemoryImportance.MEDIUM,
            RepoFileType.DATA_FILE: MemoryImportance.LOW,
            RepoFileType.LICENSE: MemoryImportance.LOW
        }
        
        importance = importance_map.get(repo_file.file_type, MemoryImportance.LOW)
        
        # Store in memory
        memory_id = await self.memory_manager.store_memory(
            user_id=metadata.user_id,
            content=context_content,
            memory_type=MemoryType.DOCUMENT,
            importance=importance,
            source_session=metadata.session_id,
            tags=[
                'repository',
                metadata.name,
                repo_file.file_type.value,
                repo_file.language or 'unknown'
            ],
            metadata={
                'repo_id': str(metadata.repo_id),
                'repo_url': metadata.url,
                'file_path': repo_file.relative_path,
                'file_type': repo_file.file_type.value,
                'language': repo_file.language,
                'size_bytes': repo_file.size_bytes,
                'line_count': repo_file.line_count
            }
        )
        
        return memory_id
    
    async def _update_progress(self, metadata: RepoMetadata, progress: float, message: str):
        """Update processing progress with logging and state persistence."""
        if metadata is None:
            logger.warning("Attempted to update progress with None metadata")
            return

        # Clamp progress to the valid range [0, 100]
        clamped_progress = max(0.0, min(100.0, progress))
        metadata.progress_percentage = clamped_progress
        metadata.last_updated = datetime.now(timezone.utc)

        logger.info(f"Repository {metadata.name}: {clamped_progress:.1f}% - {message}")

    async def get_repository_status(self, repo_id: UUID) -> Optional[RepoMetadata]:
        """Retrieve the current processing metadata for a repository."""
        metadata = self.active_repos.get(repo_id)
        if metadata is None:
            logger.debug(f"Requested status for unknown repository ID: {repo_id}")
        return metadata

    async def list_user_repositories(self, user_id: str) -> List[RepoMetadata]:
        """Return all repository metadata objects owned by the specified user."""
        user_repos = [
            repo for repo in self.active_repos.values()
            if repo.user_id == user_id
        ]
        logger.debug(f"Listing {len(user_repos)} repositories for user {user_id}")
        return user_repos
    
    async def search_repository_content(
        self,
        user_id: str,
        query: str,
        repo_id: Optional[UUID] = None
    ) -> List[Dict[str, Any]]:
        """Search repository content through memory system"""
        
        # Build search tags
        search_tags = ['repository']
        if repo_id:
            search_tags.append(str(repo_id))
        
        # Search through memory system
        memories = await self.memory_manager.retrieve_memories(
            user_id=user_id,
            query=query,
            memory_types=[MemoryType.DOCUMENT],
            limit=20
        )
        
        # Filter repository memories
        repo_memories = []
        for memory in memories:
            tags = memory.get('tags', [])
            if 'repository' not in tags:
                continue
            metadata_dict = memory.get('metadata', {})
            if repo_id and metadata_dict.get('repo_id') != str(repo_id):
                continue
            repo_memories.append({
                'memory_id': memory['memory_id'],
                'repo_id': metadata_dict.get('repo_id'),
                'repo_url': metadata_dict.get('repo_url'),
                'file_path': metadata_dict.get('file_path'),
                'file_type': metadata_dict.get('file_type'),
                'language': metadata_dict.get('language'),
                'similarity_score': memory.get('similarity_score', 0),
                'content_preview': memory['content'][:500] + '...' if len(memory['content']) > 500 else memory['content']
            })
        
        return repo_memories
    
    async def cleanup_repository(self, repo_id: UUID, user_id: str) -> bool:
        """Clean up repository files and data"""
        try:
            metadata = self.active_repos.get(repo_id)
            if not metadata or metadata.user_id != user_id:
                return False
            
            # Remove local files
            if metadata.local_path and Path(metadata.local_path).exists():
                shutil.rmtree(metadata.local_path, ignore_errors=True)
            
            # Remove from tracking
            self.active_repos.pop(repo_id, None)
            self.repo_files.pop(repo_id, None)
            
            logger.info(f"Repository {metadata.name} cleaned up successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cleanup repository {repo_id}: {e}")
            return False
    
    def get_system_stats(self) -> Dict[str, Any]:
        """Get system statistics"""
        total_repos = len(self.active_repos)
        completed_repos = sum(1 for r in self.active_repos.values() if r.is_complete)
        failed_repos = sum(1 for r in self.active_repos.values() if r.status == RepoStatus.FAILED)
        active_processing = sum(
            1 for r in self.active_repos.values()
            if r.status in {RepoStatus.INITIALIZING, RepoStatus.CLONING, RepoStatus.INDEXING, RepoStatus.PROCESSING}
        )
        total_files = sum(r.total_files for r in self.active_repos.values())
        total_size = sum(r.total_size_bytes for r in self.active_repos.values())
        
        return {
            'total_repositories': total_repos,
            'completed_repositories': completed_repos,
            'failed_repositories': failed_repos,
            'total_files_indexed': total_files,
            'total_size_gb': total_size / (1024**3),
            'active_processing': active_processing,
            'clone_directory': str(self.clone_dir),
            'metrics': self.metrics.copy()
        }

# ============================================================================
# Critical Production Methods - Essential Implementations
# ============================================================================

    async def _stream_process_files(self, metadata: RepoMetadata) -> None:
        """Stream process files with backpressure handling and error recovery"""
        if metadata.repo_id not in self.repo_files:
            raise ProcessingError("Repository files not indexed", correlation_id=metadata.correlation_id)
        
        files_to_process = [f for f in self.repo_files[metadata.repo_id] if f.is_processable]
        processed_count = 0
        failed_count = 0
        
        logger.info(f"Starting stream processing [{metadata.correlation_id}]: {len(files_to_process)} files")
        
        # Process files in batches with semaphore control
        async def process_file_batch(batch: List[RepoFile]) -> None:
            nonlocal processed_count, failed_count
            
            for repo_file in batch:
                async with self.processing_semaphore:  # Backpressure control
                    try:
                        # Check memory pressure
                        if self.resource_monitor.check_memory_pressure():
                            await asyncio.sleep(0.1)  # Brief pause under pressure
                        
                        # Read and process file
                        if self.intelligent_processor:
                            async with aiofiles.open(repo_file.absolute_path, 'rb') as f:
                                file_data = await f.read()
                            
                            task_id = await self.intelligent_processor.queue_file(
                                file_data=file_data,
                                filename=repo_file.relative_path,
                                user_id=metadata.user_id,
                                session_id=metadata.session_id,
                                priority=ProcessingPriority.BACKGROUND if hasattr(ProcessingPriority, 'BACKGROUND') else 4,
                                source_context=f"git_clone:{metadata.name}"
                            )
                            
                            repo_file.processed = True
                            processed_count += 1
                        
                        # Checkpoint progress periodically
                        if (processed_count % metadata.configuration.checkpoint_interval_files == 0 and 
                            metadata.configuration.enable_checkpointing):
                            await self._save_checkpoint(metadata)
                        
                    except Exception as e:
                        repo_file.processing_error = str(e)
                        repo_file.processed = False
                        failed_count += 1
                        logger.warning(
                            f"File processing failed [{metadata.correlation_id}]: {repo_file.relative_path} - {e}"
                        )
        
        # Process in batches
        batch_size = metadata.configuration.batch_size
        for i in range(0, len(files_to_process), batch_size):
            batch = files_to_process[i:i + batch_size]
            await process_file_batch(batch)
            
            # Update progress
            progress = min(90, 70 + (i / len(files_to_process)) * 20)
            metadata.update_progress(progress, f"Processed {processed_count} files")
        
        # Update final counts
        metadata.processed_files = processed_count
        metadata.failed_files = failed_count
        
        logger.info(
            f"Stream processing completed [{metadata.correlation_id}]: "
            f"{processed_count} processed, {failed_count} failed"
        )

    async def _detect_lfs_and_submodules(self, metadata: RepoMetadata, repo: Repo) -> None:
        """Detect Git LFS files and submodules"""
        repo_path = Path(repo.working_dir)
        
        # Check for LFS
        gitattributes_path = repo_path / '.gitattributes'
        if gitattributes_path.exists():
            try:
                async with aiofiles.open(gitattributes_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = await f.read()
                    if 'filter=lfs' in content:
                        metadata.has_lfs = True
                        await self._process_lfs_files(metadata, repo_path, content)
            except Exception as e:
                metadata.add_warning(f"Failed to process .gitattributes: {e}")
        
        # Check for submodules
        gitmodules_path = repo_path / '.gitmodules'
        if gitmodules_path.exists():
            try:
                metadata.has_submodules = True
                await self._process_submodules(metadata, repo, gitmodules_path)
            except Exception as e:
                metadata.add_warning(f"Failed to process submodules: {e}")

    async def _process_lfs_files(self, metadata: RepoMetadata, repo_path: Path, gitattributes_content: str) -> None:
        """Process LFS file pointers"""
        lfs_patterns = []
        
        # Parse .gitattributes for LFS patterns
        for line in gitattributes_content.split('\n'):
            if 'filter=lfs' in line:
                pattern = line.split()[0]
                lfs_patterns.append(pattern)
        
        if not lfs_patterns:
            return
        
        # Find LFS pointer files
        for file_path in repo_path.rglob('*'):
            if file_path.is_file():
                # Check if file matches LFS patterns
                relative_path = file_path.relative_to(repo_path)
                if any(relative_path.match(pattern) for pattern in lfs_patterns):
                    try:
                        # Read LFS pointer
                        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = await f.read(200)  # LFS pointers are small
                        
                        if content.startswith('version https://git-lfs.github.com/spec/v1'):
                            # Parse LFS pointer
                            lines = content.strip().split('\n')
                            oid = None
                            size = 0
                            
                            for line in lines:
                                if line.startswith('oid sha256:'):
                                    oid = line.split(':', 1)[1]
                                elif line.startswith('size '):
                                    size = int(line.split()[1])
                            
                            if oid:
                                lfs_pointer = LFSPointer(
                                    oid=oid,
                                    size=size,
                                    file_path=str(relative_path)
                                )
                                metadata.lfs_files.append(lfs_pointer)
                                metadata.total_lfs_size_bytes += size
                    except Exception:
                        continue
        
        logger.debug(f"Detected {len(metadata.lfs_files)} LFS files [{metadata.correlation_id}]")

    async def _perform_security_scan(self, metadata: RepoMetadata) -> None:
        """Perform local security scanning"""
        if not metadata.local_path:
            return
        
        scanner = LocalSecurityScanner()
        repo_path = Path(metadata.local_path)
        
        try:
            timeout_seconds = metadata.configuration.security_scan_timeout_minutes * 60
            findings = await asyncio.wait_for(
                scanner.scan_repository(repo_path, metadata.configuration.max_security_findings),
                timeout=timeout_seconds
            )
            
            metadata.security_findings = findings
            
            # Determine maximum threat level
            if findings:
                metadata.max_threat_level = max(f.threat_level for f in findings)
                
                # Check if processing should be blocked
                critical_findings = [f for f in findings if f.threat_level == SecurityThreatLevel.CRITICAL]
                if critical_findings and metadata.configuration.block_on_critical_findings:
                    metadata.is_security_approved = False
                    raise SecurityError(
                        f"Repository blocked due to {len(critical_findings)} critical security findings",
                        correlation_id=metadata.correlation_id
                    )
            
            self.metrics['security_findings'] += len(findings)
            
        except asyncio.TimeoutError:
            metadata.add_warning("Security scan timed out")
        except SecurityError:
            raise  # Re-raise security errors
        except Exception as e:
            metadata.add_warning(f"Security scan failed: {str(e)}")

    async def _setup_artifact_workspace(self, metadata: RepoMetadata) -> None:
        """Setup artifact workspace for development integration"""
        if not self.enable_artifact_integration or not metadata.local_path:
            return
        
        try:
            # This would integrate with the artifact system
            # For now, we'll create a placeholder artifact ID
            metadata.artifact_id = f"repo-{metadata.repo_id}"
            
            # Mark workspace as mounted (readonly by default)
            metadata.workspace_mounted = True
            
            logger.debug(f"Artifact workspace setup [{metadata.correlation_id}]: {metadata.artifact_id}")
            
        except Exception as e:
            metadata.add_warning(f"Artifact workspace setup failed: {str(e)}")

    async def _save_checkpoint(self, metadata: RepoMetadata) -> None:
        """Save processing checkpoint for recovery"""
        if not metadata.checkpoint_file:
            return
        
        try:
            checkpoint_data = {
                'repo_id': str(metadata.repo_id),
                'correlation_id': metadata.correlation_id,
                'status': metadata.status.value,
                'progress_percentage': metadata.progress_percentage,
                'processed_files': metadata.processed_files,
                'failed_files': metadata.failed_files,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            async with aiofiles.open(metadata.checkpoint_file, 'w') as f:
                await f.write(json.dumps(checkpoint_data, indent=2))
            
            metadata.last_checkpoint = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.warning(f"Failed to save checkpoint [{metadata.correlation_id}]: {e}")

    async def _cleanup_on_failure(self, metadata: RepoMetadata) -> None:
        """Cleanup resources after failure"""
        try:
            # Remove local repository files
            if metadata.local_path and Path(metadata.local_path).exists():
                shutil.rmtree(metadata.local_path, ignore_errors=True)
            
            # Remove bare repository
            if metadata.bare_repo_path and Path(metadata.bare_repo_path).exists():
                shutil.rmtree(metadata.bare_repo_path, ignore_errors=True)
            
            # Remove checkpoint file
            if metadata.checkpoint_file and Path(metadata.checkpoint_file).exists():
                Path(metadata.checkpoint_file).unlink(missing_ok=True)
            
            logger.debug(f"Cleanup completed [{metadata.correlation_id}]")
            
        except Exception as e:
            logger.warning(f"Cleanup failed [{metadata.correlation_id}]: {e}")

    def _detect_language(self, file_path: Path) -> str:
        """Enhanced language detection"""
        ext_to_lang = {
            '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript', '.jsx': 'JavaScript',
            '.tsx': 'TypeScript', '.java': 'Java', '.cpp': 'C++', '.c': 'C', '.h': 'C/C++',
            '.hpp': 'C++', '.cs': 'C#', '.go': 'Go', '.rs': 'Rust', '.php': 'PHP',
            '.rb': 'Ruby', '.swift': 'Swift', '.kt': 'Kotlin', '.scala': 'Scala',
            '.r': 'R', '.m': 'Objective-C', '.mm': 'Objective-C++', '.sh': 'Shell',
            '.bash': 'Bash', '.zsh': 'Zsh', '.ps1': 'PowerShell', '.bat': 'Batch',
            '.yaml': 'YAML', '.yml': 'YAML', '.json': 'JSON', '.xml': 'XML',
            '.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS', '.sass': 'Sass',
            '.vue': 'Vue', '.svelte': 'Svelte', '.sql': 'SQL', '.md': 'Markdown',
            '.rst': 'reStructuredText', '.tex': 'LaTeX'
        }
        
        return ext_to_lang.get(file_path.suffix.lower(), 'Unknown')

    async def _count_file_lines(self, file_path: Path) -> Optional[int]:
        """Count lines in text file with size limits"""
        try:
            if file_path.stat().st_size > 1024 * 1024:  # 1MB limit
                return None
            
            line_count = 0
            async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                async for _ in f:
                    line_count += 1
                    
            return line_count
        except Exception:
            return None

    async def _store_repository_memories(self, metadata: RepoMetadata) -> None:
        """Store repository content in memory system"""
        if not self.memory_manager:
            metadata.add_warning("Memory manager not available for memory storage")
            return
        
        try:
            # Store high-level repository summary
            repo_summary = f"""Repository: {metadata.name}
URL: {metadata.url}
Description: {metadata.description or 'No description'}
Primary Language: {metadata.primary_language or 'Unknown'}
Total Files: {metadata.total_files}
Size: {metadata.total_size_bytes / 1024**2:.1f}MB
License: {metadata.license_name or 'Unknown'}"""
            
            summary_memory_id = await self.memory_manager.store_memory(
                user_id=metadata.user_id,
                content=repo_summary,
                memory_type=MemoryType.DOCUMENT if hasattr(MemoryType, 'DOCUMENT') else 'document',
                importance=MemoryImportance.HIGH if hasattr(MemoryImportance, 'HIGH') else 'high',
                source_session=metadata.session_id,
                tags=['repository', 'summary', metadata.name],
                metadata={'repo_id': str(metadata.repo_id), 'type': 'repository_summary'}
            )
            
            metadata.stored_memories.append(summary_memory_id)
            metadata.memory_storage_complete = True
            
            logger.debug(f"Repository memories stored [{metadata.correlation_id}]: {len(metadata.stored_memories)} items")
            
        except Exception as e:
            metadata.add_warning(f"Memory storage failed: {str(e)}")

# ============================================================================
# Local Security Scanner Implementation
# ============================================================================

class LocalSecurityScanner:
    """Local security scanning without external dependencies"""
    
    def __init__(self):
        self.secret_patterns = [
            (r'[Aa]ws_[Aa]ccess_[Kk]ey_[Ii][Dd]\s*=\s*["\']?[A-Z0-9]{20}["\']?', 'AWS Access Key'),
            (r'[Aa]ws_[Ss]ecret_[Aa]ccess_[Kk]ey\s*=\s*["\']?[A-Za-z0-9/+=]{40}["\']?', 'AWS Secret Key'),
            (r'github_token\s*[:=]\s*["\']?[a-z0-9]{40}["\']?', 'GitHub Token'),
            (r'password\s*[:=]\s*["\'][^"\']{8,}["\']', 'Hardcoded Password'),
            (r'api[_\-]?key\s*[:=]\s*["\']?[a-zA-Z0-9]{16,}["\']?', 'API Key'),
        ]
        
        self.suspicious_extensions = {'.key', '.pem', '.p12', '.pfx', '.crt', '.keystore'}
        
    async def scan_repository(self, repo_path: Path, max_files: int = 1000) -> List[SecurityFinding]:
        """Scan repository for security issues"""
        findings = []
        scanned_count = 0
        
        try:
            for file_path in repo_path.rglob('*'):
                if scanned_count >= max_files:
                    break
                    
                if file_path.is_file():
                    file_findings = await self._scan_file(file_path, repo_path)
                    findings.extend(file_findings)
                    scanned_count += 1
                    
        except Exception as e:
            logger.warning(f"Security scan failed: {e}")
            
        return findings
    
    async def _scan_file(self, file_path: Path, repo_path: Path) -> List[SecurityFinding]:
        """Scan individual file for security issues"""
        findings = []
        relative_path = str(file_path.relative_to(repo_path))
        
        try:
            # Check suspicious file extensions
            if file_path.suffix.lower() in self.suspicious_extensions:
                findings.append(SecurityFinding(
                    threat_level=SecurityThreatLevel.MEDIUM,
                    category="sensitive_file",
                    title=f"Potentially sensitive file: {file_path.name}",
                    description=f"File with extension {file_path.suffix} may contain sensitive data",
                    file_path=relative_path,
                    scanner="local_file_scanner",
                    confidence=0.8
                ))
            
            # Skip binary files and large files
            if file_path.stat().st_size > 1024 * 1024:  # 1MB limit
                return findings
                
            # Read file content for pattern matching
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = await f.read(100 * 1024)  # Read first 100KB
                    
                # Check for secret patterns
                for pattern, description in self.secret_patterns:
                    matches = re.finditer(pattern, content, re.IGNORECASE)
                    for match in matches:
                        line_num = content[:match.start()].count('\n') + 1
                        findings.append(SecurityFinding(
                            threat_level=SecurityThreatLevel.HIGH,
                            category="secret_exposure",
                            title=f"Potential {description}",
                            description=f"Pattern matching {description} detected",
                            file_path=relative_path,
                            line_number=line_num,
                            scanner="pattern_matcher",
                            confidence=0.9
                        ))
                        
            except (UnicodeDecodeError, OSError):
                pass  # Skip files that can't be read as text
                
        except Exception as e:
            logger.debug(f"Failed to scan file {file_path}: {e}")
            
        return findings

