"""
MORPHEUS CHAT - AI Virtual Machine Instance State and Metadata Schemas
Production-grade Pydantic models for persistent AI VM lifecycle management
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, validator, root_validator


class VMState(str, Enum):
    """Virtual machine lifecycle states (Mirroring vm_manager.py)"""
    CREATING = "creating"
    RUNNING = "running"
    SUSPENDED = "suspended"
    SAVING = "saving"
    RESTORING = "restoring"
    BACKING_UP = "backing_up"
    ERROR = "error"
    TERMINATED = "terminated" # Added for clarity in the lifecycle


class VMSpecs(BaseModel):
    """VM hardware specifications (Mirroring vm_manager.py)"""
    vcpus: int = Field(default=4, ge=1)
    memory_gb: int = Field(default=8, ge=1)
    storage_gb: int = Field(default=100, ge=10)
    gpu_enabled: bool = False
    network_enabled: bool = True


class VMResourceUsage(BaseModel):
    """Real-time resource consumption tracking for a VM"""
    # Provide defaults so the model can be instantiated without arguments
    cpu_percent: float = Field(default=0.0, ge=0, le=100,
                               description="CPU usage percentage")
    memory_bytes: int = Field(default=0, ge=0,
                              description="Memory usage in bytes")
    disk_bytes: int = Field(default=0, ge=0,
                            description="Disk usage in bytes")
    network_bytes_in: int = Field(default=0, ge=0,
                                 description="Network bytes received")
    network_bytes_out: int = Field(default=0, ge=0,
                                  description="Network bytes sent")
    execution_time: float = Field(default=0.0, ge=0,
                                 description="Command execution time in seconds")
    
    @property
    def memory_mb(self) -> float:
        """Memory usage in megabytes"""
        return self.memory_bytes / (1024 * 1024)
    
    @property
    def disk_mb(self) -> float:
        """Disk usage in megabytes"""
        return self.disk_bytes / (1024 * 1024)


class VMSecurityMetrics(BaseModel):
    """Security violation and threat detection metrics for a VM"""
    content_filter_violations: int = Field(default=0, ge=0)
    prompt_injection_attempts: int = Field(default=0, ge=0)
    capability_violations: int = Field(default=0, ge=0) # E.g., VM trying to access restricted resources
    rate_limit_violations: int = Field(default=0, ge=0)
    suspicious_activities: List[str] = Field(default_factory=list)
    last_violation: Optional[datetime] = None
    threat_score: float = Field(default=0.0, ge=0, le=1.0, description="Normalized threat score")


class PromptEventType(str, Enum):
    """Types of prompt events that should be stored as first-class memory objects"""
    PROMPT_GENERATED = "prompt_generated"
    PROMPT_MODIFIED = "prompt_modified"
    MEMORY_SYNTHESIS = "memory_synthesis"
    SEMANTIC_GRAFT_CREATED = "semantic_graft_created"
    PERFORMANCE_UPDATE = "performance_update"
    LAYER_UPDATE = "layer_update"
    CONTEXT_INJECTION = "context_injection"
    AUTONOMOUS_ADAPTATION = "autonomous_adaptation"


class PromptMemoryTags(BaseModel):
    """Schema-level tagging system for prompt events as memory objects"""
    event_type: PromptEventType = Field(description="Type of prompt event")
    user_id: str = Field(description="User ID associated with prompt")
    session_id: Optional[str] = Field(None, description="Session ID if applicable")
    vm_id: Optional[UUID] = Field(None, description="VM ID if VM-scoped")
    
    # Semantic tags for indexing and retrieval
    semantic_tags: List[str] = Field(default_factory=list, description="Semantic tags for memory indexing")
    technical_context: List[str] = Field(default_factory=list, description="Technical context tags")
    collaboration_context: List[str] = Field(default_factory=list, description="Collaboration context tags")
    
    # Performance and quality tags
    quality_score: Optional[float] = Field(None, ge=0, le=1.0, description="Quality score of prompt/response")
    relevance_score: Optional[float] = Field(None, ge=0, le=1.0, description="Relevance score")
    adaptation_success: Optional[bool] = Field(None, description="Whether adaptation was successful")
    
    # Memory system integration
    memory_importance: str = Field(default="medium", description="Memory importance level")
    memory_scope: str = Field(default="private", description="Memory scope")
    retention_policy: str = Field(default="standard", description="Retention policy override")
    
    # Temporal and usage metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: Optional[datetime] = Field(None)
    access_count: int = Field(default=0, ge=0)
    
    # Cross-referencing
    related_prompt_events: List[str] = Field(default_factory=list, description="IDs of related prompt events")
    parent_prompt_event: Optional[str] = Field(None, description="Parent prompt event ID")
    child_prompt_events: List[str] = Field(default_factory=list, description="Child prompt event IDs")


class PromptEventMemoryObject(BaseModel):
    """First-class memory object for prompt events with full semantic integration"""
    event_id: UUID = Field(default_factory=uuid4, description="Unique prompt event identifier")
    user_id: str = Field(description="User associated with prompt event")
    session_id: Optional[str] = Field(None, description="Session context")
    vm_id: Optional[UUID] = Field(None, description="VM context")
    
    # Event content and metadata
    event_type: PromptEventType = Field(description="Type of prompt event")
    prompt_content: str = Field(description="Generated prompt content")
    user_input: Optional[str] = Field(None, description="Original user input that triggered prompt")
    response_content: Optional[str] = Field(None, description="AI response generated from prompt")
    
    # Semantic embedding data
    prompt_embedding_vector: Optional[List[float]] = Field(None, description="Semantic embedding of prompt")
    content_hash: str = Field(description="SHA-256 hash for deduplication")
    
    # Memory system integration
    memory_tags: PromptMemoryTags = Field(description="Schema-level tags for memory indexing")
    
    # Performance metrics
    generation_time_ms: Optional[float] = Field(None, description="Time to generate prompt in milliseconds")
    prompt_length: int = Field(description="Length of generated prompt")
    semantic_similarity_scores: Dict[str, float] = Field(default_factory=dict, description="Similarity to related prompts")
    
    # Adaptation tracking
    adaptations_applied: List[str] = Field(default_factory=list, description="List of adaptations applied")
    performance_impact: Optional[float] = Field(None, description="Performance impact of this prompt")
    user_feedback_score: Optional[float] = Field(None, description="User feedback on prompt effectiveness")
    
    # Memory consolidation
    memory_consolidation_status: str = Field(default="pending", description="Status of memory consolidation")
    consolidated_into_memory_id: Optional[str] = Field(None, description="Memory ID if consolidated")
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    indexed_at: Optional[datetime] = Field(None, description="When semantic indexing completed")
    
    def update_access_tracking(self):
        """Update access tracking for this prompt event"""
        self.memory_tags.last_accessed = datetime.now(timezone.utc)
        self.memory_tags.access_count += 1
        self.updated_at = datetime.now(timezone.utc)
    
    def add_semantic_similarity(self, other_event_id: str, similarity_score: float):
        """Add semantic similarity score to another prompt event"""
        self.semantic_similarity_scores[other_event_id] = similarity_score
    
    def mark_consolidated(self, memory_id: str):
        """Mark this prompt event as consolidated into persistent memory"""
        self.memory_consolidation_status = "consolidated"
        self.consolidated_into_memory_id = memory_id
        self.updated_at = datetime.now(timezone.utc)
    
    @property
    def is_high_quality(self) -> bool:
        """Determine if this is a high-quality prompt event worth preserving"""
        if self.user_feedback_score and self.user_feedback_score > 0.8:
            return True
        if self.memory_tags.quality_score and self.memory_tags.quality_score > 0.75:
            return True
        if self.memory_tags.access_count > 5:
            return True
        return False
    
    @property
    def memory_priority(self) -> str:
        """Determine memory priority based on quality and usage"""
        if self.is_high_quality:
            return "high"
        elif self.memory_tags.access_count > 2 or (self.memory_tags.quality_score and self.memory_tags.quality_score > 0.6):
            return "medium"
        else:
            return "low"


class AIModelConfiguration(BaseModel):
    """Active AI model configuration for the VM"""
    model_id: str = Field(description="Model identifier from local registry")
    model_type: str = Field(description="Model type (local/remote)") # Primarily local for your use case
    context_length: int = Field(description="Maximum context length in tokens")
    capabilities: List[str] = Field(description="Model capabilities (e.g., text generation, code interpretation)")
    
    # Generation parameters
    temperature: float = Field(default=0.7, ge=0, le=2.0)
    top_p: float = Field(default=0.9, ge=0, le=1.0)
    top_k: int = Field(default=50, ge=1)
    max_new_tokens: int = Field(default=2048, ge=1)
    repetition_penalty: float = Field(default=1.1, ge=0.5, le=2.0)
    
    # Quantization settings (important for local models)
    quantization_enabled: bool = Field(default=False)
    quantization_bits: Optional[int] = Field(None, ge=2, le=16)
    
    # Device management for local models
    preferred_device: Optional[str] = Field(None, description="Preferred execution device (e.g., 'cuda', 'cpu')")


class AIContextWindow(BaseModel):
    """AI's context window management with token tracking"""
    max_tokens: int = Field(description="Maximum context window size")
    current_tokens: int = Field(default=0, ge=0, description="Current token usage")
    system_tokens: int = Field(default=0, ge=0, description="System prompt tokens")
    user_tokens: int = Field(default=0, ge=0, description="User message tokens")
    assistant_tokens: int = Field(default=0, ge=0, description="Assistant response tokens")
    
    # Truncation and memory management
    truncation_enabled: bool = Field(default=True)
    importance_weights: Dict[str, float] = Field(default_factory=dict) # For importance-weighted truncation
    anchor_tokens: List[int] = Field(default_factory=list, description="Never-truncate token ranges")
    
    @validator('current_tokens')
    def validate_token_limit(cls, v, values):
        """Ensure current tokens don't exceed maximum"""
        max_tokens = values.get('max_tokens', 0)
        if v > max_tokens:
            raise ValueError(f"Current tokens ({v}) exceed maximum ({max_tokens})")
        return v
    
    @property
    def utilization_percent(self) -> float:
        """Context window utilization percentage"""
        if self.max_tokens == 0:
            return 0.0
        return (self.current_tokens / self.max_tokens) * 100
    
    @property
    def available_tokens(self) -> int:
        """Available tokens for new content"""
        return max(0, self.max_tokens - self.current_tokens)


class AIVMInstanceMetadata(BaseModel):
    """Comprehensive AI Virtual Machine instance metadata and state tracking"""
    vm_id: UUID = Field(default_factory=uuid4, description="Unique VM identifier (AI's personal computer ID)")
    user_id: Optional[str] = Field(None, description="Associated user identifier (if a user owns this AI VM)")
    instance_name: str = Field(description="Human-readable instance name for the AI")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_vm_state_change: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # When VM state last changed
    expires_at: Optional[datetime] = Field(None, description="VM expiration time (if temporary)")
    
    # VM State
    vm_state: VMState = Field(default=VMState.CREATING)
    vm_state_history: List[Dict[str, Any]] = Field(default_factory=list) # Track state transitions
    
    # VM Hardware and connection information
    specs: VMSpecs = Field(default_factory=VMSpecs)
    vm_disk_path: str = Field(description="Path to VM disk image on host")
    internal_ip: Optional[str] = None
    ssh_port: int = Field(default=2222, ge=1024, le=65535) # Port for SSH access to VM
    vnc_port: int = Field(default=5900, ge=1024, le=65535) # Port for VNC access to VM
    
    # AI-specific configurations within the VM
    personality_config: Dict[str, Any] = Field(default_factory=dict) # AI's unique personality settings
    installed_tools: List[str] = Field(default_factory=list) # Tools AI has permanently installed in its VM
    capabilities_learned: List[str] = Field(default_factory=list) # Capabilities AI has developed
    research_bookmarks: List[str] = Field(default_factory=list)
    custom_workflows: Dict[str, Any] = Field(default_factory=dict)
    
    # AI Model configuration active within this VM instance
    ai_model_config: Optional[AIModelConfiguration] = Field(None)
    
    # AI's context and long-term memory
    context_window: AIContextWindow = Field(default_factory=lambda: AIContextWindow(max_tokens=8192))
    custom_instructions: Optional[str] = Field(None, description="User-defined instructions for this AI")
    persistent_memory_snapshot: Dict[str, Any] = Field(default_factory=dict) # Snapshot of AI's memory within VM
    
    # Performance and resource tracking
    resource_usage: VMResourceUsage = Field(default_factory=VMResourceUsage)
    security_metrics: VMSecurityMetrics = Field(default_factory=VMSecurityMetrics)
    
    # Interaction metrics
    total_interactions: int = Field(default=0, ge=0) # Total commands executed, messages processed
    total_tokens_processed: int = Field(default=0, ge=0)
    tools_invoked: List[str] = Field(default_factory=list)
    files_managed: List[str] = Field(default_factory=list) # Files created/modified by AI in VM
    
    # Prompt event tracking (first-class memory objects)
    total_prompt_events: int = Field(default=0, ge=0, description="Total prompt events generated")
    active_prompt_events: List[str] = Field(default_factory=list, description="Active prompt event IDs")
    consolidated_prompt_events: int = Field(default=0, ge=0, description="Prompt events consolidated to memory")
    prompt_quality_avg: float = Field(default=0.5, ge=0, le=1.0, description="Average prompt quality score")
    last_prompt_event_id: Optional[str] = Field(None, description="Last prompt event ID generated")
    
    # Error tracking
    error_count: int = Field(default=0, ge=0)
    last_error: Optional[str] = Field(None)
    error_history: List[Dict[str, Any]] = Field(default_factory=list)
    
    def update_activity(self):
        """Update last activity timestamp for the VM"""
        self.last_activity = datetime.now(timezone.utc)
    
    def add_vm_state_transition(self, new_state: VMState, reason: str = ""):
        """Record VM state transition with timestamp and reason"""
        transition = {
            "from_state": self.vm_state.value,
            "to_state": new_state.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason
        }
        self.vm_state_history.append(transition)
        self.vm_state = new_state
        self.last_vm_state_change = datetime.now(timezone.utc)
        self.update_activity() # Activity updated on state change
    
    def add_error(self, error_message: str, error_type: str = "unknown"):
        """Record error with details"""
        error_record = {
            "message": error_message,
            "type": error_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vm_state": self.vm_state.value
        }
        self.error_history.append(error_record)
        self.last_error = error_message
        self.error_count += 1
    
    def add_prompt_event(self, prompt_event_id: str, quality_score: Optional[float] = None):
        """Record a new prompt event"""
        self.total_prompt_events += 1
        self.last_prompt_event_id = prompt_event_id
        
        # Track active prompt events (keep last 50)
        self.active_prompt_events.append(prompt_event_id)
        if len(self.active_prompt_events) > 50:
            self.active_prompt_events.pop(0)
        
        # Update quality average if provided
        if quality_score is not None:
            # Exponential moving average
            self.prompt_quality_avg = (self.prompt_quality_avg * 0.9) + (quality_score * 0.1)
        
        self.update_activity()
    
    def consolidate_prompt_event(self, prompt_event_id: str):
        """Mark a prompt event as consolidated into persistent memory"""
        self.consolidated_prompt_events += 1
        
        # Remove from active events if present
        if prompt_event_id in self.active_prompt_events:
            self.active_prompt_events.remove(prompt_event_id)
        
        self.update_activity()
    
    def get_prompt_metrics(self) -> Dict[str, Any]:
        """Get comprehensive prompt event metrics"""
        return {
            "total_prompt_events": self.total_prompt_events,
            "active_prompt_events_count": len(self.active_prompt_events),
            "consolidated_prompt_events": self.consolidated_prompt_events,
            "prompt_quality_average": self.prompt_quality_avg,
            "consolidation_rate": (
                self.consolidated_prompt_events / self.total_prompt_events 
                if self.total_prompt_events > 0 else 0.0
            ),
            "last_prompt_event": self.last_prompt_event_id
        }
    
    def is_expired(self) -> bool:
        """Check if VM has expired (if it's a temporary instance)"""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at
    
    def is_idle(self, idle_threshold_minutes: int = 60) -> bool:
        """Check if VM has been idle for too long, indicating it can be suspended"""
        if not self.last_activity:
            return False
        
        idle_duration = datetime.now(timezone.utc) - self.last_activity
        return idle_duration.total_seconds() > (idle_threshold_minutes * 60)
    
    @property
    def uptime_seconds(self) -> float:
        """VM uptime in seconds since creation"""
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()
    
    @property
    def is_healthy(self) -> bool:
        """Basic health check based on VM state and error rate"""
        if self.vm_state in [VMState.ERROR, VMState.TERMINATED]:
            return False
        
        # Check error rate (e.g., max 10% error rate for healthy VM operations)
        if self.total_interactions > 10:
            error_rate = self.error_count / self.total_interactions
            if error_rate > 0.1:
                return False
        
        return True


class AICreationRequest(BaseModel):
    """Request payload for creating a new AI Virtual Machine instance"""
    user_id: Optional[str] = Field(None, description="User identifier if associated with a user")
    instance_name: str = Field(description="Desired human-readable name for the AI")
    personality_config: Dict[str, Any] = Field(default_factory=dict, description="Initial personality settings for the AI")
    
    # VM Resource overrides (optional, defaults come from base_config)
    vcpus: Optional[int] = Field(None, ge=1, description="Override number of virtual CPUs")
    memory_gb: Optional[int] = Field(None, ge=1, description="Override amount of RAM in GB")
    storage_gb: Optional[int] = Field(None, ge=10, description="Override disk storage in GB")
    gpu_enabled: Optional[bool] = Field(None, description="Enable or disable GPU for this VM")
    network_enabled: Optional[bool] = Field(None, description="Enable or disable network access for this VM")
    
    # Initial AI model to load (if specific model is required for the AI personality)
    initial_model_id: Optional[str] = Field(None, description="ID of the initial AI model to load into the VM")
    
    # Capabilities to pre-install/configure in the VM
    initial_capabilities: List[str] = Field(default_factory=list, description="List of capabilities to pre-configure (e.g., 'web_research', 'code_development')")
    
    # VM lifecycle settings
    auto_suspend_enabled: bool = Field(default=True, description="Enable automatic suspension of VM when idle")
    auto_backup_enabled: bool = Field(default=False, description="Enable automatic daily backups of the VM disk")


class AIVMResponse(BaseModel):
    """Response payload for AI VM operations"""
    vm_id: UUID = Field(description="Unique VM identifier")
    instance_name: str = Field(description="Name of the AI VM")
    vm_state: VMState = Field(description="Current VM state")
    message: str = Field(description="Response message from the VM manager")
    
    # Connection details
    ssh_connection_string: Optional[str] = Field(None, description="SSH command to connect to the VM")
    vnc_connection_string: Optional[str] = Field(None, description="VNC URI to connect to the VM's desktop")
    web_interface_url: Optional[str] = Field(None, description="URL for the AI's web-based interface/dashboard")
    internal_ip: Optional[str] = Field(None, description="Internal IP address of the VM")
    
    # Resource and capability information
    current_specs: VMSpecs = Field(description="Current hardware specifications of the VM")
    installed_capabilities: List[str] = Field(default_factory=list, description="Capabilities installed in the AI VM")
    
    # Overall system capacity (from the manager's perspective)
    available_host_resources: Dict[str, Any] = Field(default_factory=dict, description="Available resources on the host machine")
    estimated_host_capacity: Dict[str, int] = Field(default_factory=dict, description="Estimated capacity for new VMs on the host")


class AIVMListResponse(BaseModel):
    """Response for listing AI VM instances"""
    ai_vms: List[AIVMInstanceMetadata] = Field(description="List of AI VM metadata")
    total_vms: int = Field(description="Total number of AI VMs")
    running_vms: int = Field(description="Number of currently running AI VMs")
    suspended_vms: int = Field(description="Number of currently suspended AI VMs")
    overall_host_resource_usage: Dict[str, float] = Field(description="Overall resource usage on the host system")


# Type aliases for convenience
AIVMID = UUID
UserID = str
SessionID = UUID  # Added alias for session identifiers


class SessionState(str, Enum):
    """Lifecycle states for a user's interactive session."""
    ACTIVE = "active"
    CLOSED = "closed"
    TIMED_OUT = "timed_out"
    ERROR = "error"


class SessionMetadata(BaseModel):
    """Metadata for a user's interactive session."""
    session_id: SessionID = Field(default_factory=uuid4)
    user_id: UserID
    state: SessionState = Field(default=SessionState.ACTIVE)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    custom_instructions: Optional[str] = Field(None)
    total_interactions: int = 0
    
    def update_activity(self):
        self.last_activity_at = datetime.now(timezone.utc)
        self.total_interactions += 1


class SessionCreationRequest(BaseModel):
    """Request to create a new user session."""
    user_id: UserID
    custom_instructions: Optional[str] = None


