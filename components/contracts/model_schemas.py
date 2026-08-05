"""
MORPHEUS CHAT - Model Configuration and Registry Schemas
Advanced Pydantic models for multi-modal, quantized, and agentic model management
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Union, Literal
from pathlib import Path

from pydantic import BaseModel, Field, validator, root_validator
from pydantic.types import SecretStr


class ModelType(str, Enum):
    """Model deployment types with architectural implications"""
    LOCAL_TRANSFORMER = "local_transformer"
    LOCAL_QUANTIZED = "local_quantized"
    API_PROXY = "api_proxy"
    DISTRIBUTED = "distributed"
    EDGE_OPTIMIZED = "edge_optimized"
    AGENTIC = "agentic"


class ModelFormat(str, Enum):
    """Model serialization and deployment formats"""
    HUGGINGFACE = "huggingface"
    PYTORCH = "pytorch"
    ONNX = "onnx"
    TENSORRT = "tensorrt"
    GGUF = "gguf"
    GGML = "ggml"
    SAFETENSORS = "safetensors"
    OPENVINO = "openvino"


class QuantizationMethod(str, Enum):
    """
    Quantization algorithms with precision-performance tradeoffs, aligned with ModelLoader.
    """
    BITSANDBYTES = "bitsandbytes"
    GPTQ = "gptq"
    AWQ = "awq"
    GGUF = "gguf"
    GGML = "ggml"
    EXLLAMAV2 = "exllamav2"
    PYTORCH_DYNAMIC = "pytorch_dynamic"
    PYTORCH_STATIC = "pytorch_static"
    TORCH_AO = "torch_ao"
    ONNX = "onnx_quantization"
    FP16 = "fp16"
    CUSTOM = "custom"


class AttentionImplementation(str, Enum):
    """Attention mechanism optimizations"""
    FLASH_ATTENTION_2 = "flash_attention_2"
    FLASH_ATTENTION = "flash_attention"
    MEMORY_EFFICIENT = "memory_efficient"
    MATH_ATTENTION = "math_attention"
    SLIDING_WINDOW = "sliding_window"
    LONGFORMER = "longformer"
    LINFORMER = "linformer"


class ModelCapability(str, Enum):
    """Fine-grained capability taxonomy"""
    TEXT_GENERATION = "text_generation"
    CHAT_COMPLETION = "chat_completion"
    CODE_GENERATION = "code_generation"
    MATHEMATICAL_REASONING = "mathematical_reasoning"
    LOGICAL_REASONING = "logical_reasoning"
    VISION_UNDERSTANDING = "vision_understanding"
    AUDIO_PROCESSING = "audio_processing"
    MULTIMODAL_FUSION = "multimodal_fusion"
    TOOL_CALLING = "tool_calling"
    FUNCTION_CALLING = "function_calling"
    AGENT_ORCHESTRATION = "agent_orchestration"
    MEMORY_RETRIEVAL = "memory_retrieval"
    CONTEXT_EXTENSION = "context_extension"
    FINE_TUNING = "fine_tuning"
    EMBEDDING_GENERATION = "embedding_generation"


class HardwareAcceleration(BaseModel):
    """Hardware acceleration configuration matrix"""
    cuda_enabled: bool = Field(default=False, description="NVIDIA CUDA support")
    cuda_version: Optional[str] = Field(None, description="CUDA version requirement")
    cuda_compute_capability: Optional[str] = Field(None, description="Minimum compute capability")
    
    # Alternative accelerations
    rocm_enabled: bool = Field(default=False, description="AMD ROCm support")
    metal_enabled: bool = Field(default=False, description="Apple Metal Performance Shaders")
    openvino_enabled: bool = Field(default=False, description="Intel OpenVINO support")
    tensorrt_enabled: bool = Field(default=False, description="NVIDIA TensorRT optimization")
    
    # CPU optimizations
    mkl_enabled: bool = Field(default=False, description="Intel MKL optimization")
    openmp_enabled: bool = Field(default=True, description="OpenMP parallelization")
    avx2_required: bool = Field(default=False, description="AVX2 instruction set requirement")
    avx512_support: bool = Field(default=False, description="AVX-512 optimization")
    
    # Memory optimizations
    unified_memory: bool = Field(default=False, description="Unified CPU-GPU memory")
    memory_mapping: bool = Field(default=True, description="Memory-mapped model loading")
    gradient_checkpointing: bool = Field(default=False, description="Gradient checkpointing for memory")


class QuantizationConfig(BaseModel):
    """Advanced quantization configuration with calibration datasets"""
    enabled: bool = Field(default=False, description="Enable quantization")
    method: QuantizationMethod = Field(default=QuantizationMethod.BITSANDBYTES)
    bits: int = Field(default=4, ge=1, le=16, description="Quantization bit width")
    
    # Advanced quantization parameters
    group_size: int = Field(default=128, description="Quantization group size")
    activation_order: bool = Field(default=False, description="Use activation order for GPTQ")
    desc_act: bool = Field(default=False, description="Use descending activation order")
    static_groups: bool = Field(default=False, description="Use static quantization groups")
    
    # Calibration configuration
    calibration_dataset: Optional[str] = Field(None, description="Calibration dataset path")
    calibration_samples: int = Field(default=128, description="Number of calibration samples")
    calibration_sequence_length: int = Field(default=2048, description="Calibration sequence length")
    
    # Quality preservation
    preserve_accuracy: bool = Field(default=True, description="Prioritize accuracy preservation")
    accuracy_threshold: float = Field(default=0.95, ge=0.0, le=1.0, description="Minimum accuracy retention")
    perplexity_threshold: Optional[float] = Field(None, description="Maximum perplexity degradation")
    
    # Hardware-specific optimizations
    optimize_for_inference: bool = Field(default=True, description="Optimize for inference speed")
    kernel_fusion: bool = Field(default=True, description="Enable kernel fusion")
    custom_kernels: bool = Field(default=False, description="Use custom CUDA kernels")


class ModelResourceRequirements(BaseModel):
    """Comprehensive resource requirement specification"""
    # Memory requirements
    gpu_memory_gb: float = Field(description="GPU memory requirement in GB")
    system_memory_gb: float = Field(description="System RAM requirement in GB")
    disk_space_gb: float = Field(description="Disk space requirement in GB")
    
    # Compute requirements
    min_gpu_compute_capability: Optional[str] = Field(None, description="Minimum GPU compute capability")
    recommended_gpu_memory_gb: Optional[float] = Field(None, description="Recommended GPU memory")
    cpu_cores: int = Field(default=4, ge=1, description="Minimum CPU cores")
    
    # Performance characteristics
    tokens_per_second_estimate: Optional[float] = Field(None, description="Estimated generation speed")
    latency_ms_estimate: Optional[float] = Field(None, description="Estimated first token latency")
    throughput_tokens_per_second: Optional[float] = Field(None, description="Batch throughput estimate")
    
    # Scaling characteristics
    memory_scaling_factor: float = Field(default=1.0, description="Memory scaling with batch size")
    compute_scaling_factor: float = Field(default=1.0, description="Compute scaling with sequence length")
    
    @validator('gpu_memory_gb')
    def validate_gpu_memory(cls, v):
        """Ensure reasonable GPU memory requirements"""
        if v < 0.5 or v > 80:  # Reasonable bounds for current hardware
            raise ValueError("GPU memory requirement must be between 0.5GB and 80GB")
        return v


class ModelOptimizations(BaseModel):
    """Model optimization and acceleration configuration"""
    # PyTorch optimizations
    torch_compile: bool = Field(default=False, description="Enable PyTorch 2.0 compilation")
    torch_compile_mode: str = Field(default="default", description="Compilation mode")
    torch_compile_backend: str = Field(default="inductor", description="Compilation backend")
    
    # Attention optimizations
    attention_implementation: AttentionImplementation = Field(
        default=AttentionImplementation.FLASH_ATTENTION_2,
        description="Attention mechanism implementation"
    )
    attention_dropout: float = Field(default=0.0, ge=0.0, le=1.0)
    
    # Memory optimizations
    use_cache: bool = Field(default=True, description="Enable KV cache")
    kv_cache_quantization: bool = Field(default=False, description="Quantize KV cache")
    dynamic_cache: bool = Field(default=True, description="Dynamic cache allocation")
    cache_implementation: str = Field(default="transformers", description="Cache implementation")
    
    # Parallelization
    tensor_parallel_size: int = Field(default=1, ge=1, description="Tensor parallelism degree")
    pipeline_parallel_size: int = Field(default=1, ge=1, description="Pipeline parallelism degree")
    data_parallel_size: int = Field(default=1, ge=1, description="Data parallelism degree")
    
    # Advanced features
    speculative_decoding: bool = Field(default=False, description="Enable speculative decoding")
    speculative_draft_model: Optional[str] = Field(None, description="Draft model for speculation")
    continuous_batching: bool = Field(default=True, description="Enable continuous batching")
    
    # Model surgery
    layer_pruning: bool = Field(default=False, description="Enable layer pruning")
    pruned_layers: List[int] = Field(default_factory=list, description="Layers to prune")
    knowledge_distillation: bool = Field(default=False, description="Apply knowledge distillation")


class APIConfiguration(BaseModel):
    """API-based model configuration with fallback strategies"""
    provider: str = Field(description="API provider (openai, anthropic, etc.)")
    base_url: str = Field(description="API base URL")
    model_id: str = Field(description="Provider-specific model identifier")
    
    # Authentication
    api_key: Optional[SecretStr] = Field(None, description="API key")
    organization_id: Optional[str] = Field(None, description="Organization identifier")
    
    # Rate limiting and quotas
    requests_per_minute: int = Field(default=60, ge=1, description="Request rate limit")
    tokens_per_minute: int = Field(default=60000, ge=1, description="Token rate limit")
    concurrent_requests: int = Field(default=5, ge=1, description="Max concurrent requests")
    
    # Retry and fallback
    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts")
    retry_delay: float = Field(default=1.0, ge=0, description="Base retry delay in seconds")
    exponential_backoff: bool = Field(default=True, description="Use exponential backoff")
    
    # Timeout configuration
    connect_timeout: float = Field(default=10.0, ge=0, description="Connection timeout")
    read_timeout: float = Field(default=60.0, ge=0, description="Read timeout")
    total_timeout: float = Field(default=120.0, ge=0, description="Total request timeout")
    
    # Fallback configuration
    fallback_models: List[str] = Field(default_factory=list, description="Fallback model chain")
    health_check_interval: int = Field(default=60, ge=1, description="Health check interval in seconds")


class ModelConfiguration(BaseModel):
    """Comprehensive model configuration with multi-modal support"""
    # Basic identification
    model_id: str = Field(description="Unique model identifier")
    name: str = Field(description="Human-readable model name")
    version: str = Field(default="1.0.0", description="Model version")
    description: Optional[str] = Field(None, description="Model description")
    
    # Model architecture
    model_type: ModelType = Field(description="Model deployment type")
    model_format: ModelFormat = Field(description="Model serialization format")
    architecture: str = Field(description="Model architecture (e.g., llama, mistral)")
    parameter_count: Optional[int] = Field(None, description="Number of parameters")
    
    # Model location and loading
    model_path: Optional[Path] = Field(None, description="Local model path")
    model_url: Optional[str] = Field(None, description="Remote model URL")
    tokenizer_path: Optional[Path] = Field(None, description="Tokenizer path")
    config_path: Optional[Path] = Field(None, description="Model config path")
    
    # Context and capabilities
    context_length: int = Field(description="Maximum context length in tokens")
    vocabulary_size: Optional[int] = Field(None, description="Vocabulary size")
    capabilities: List[ModelCapability] = Field(description="Model capabilities")
    supported_modalities: List[str] = Field(default_factory=lambda: ["text"], description="Supported input modalities")
    
    # Resource requirements
    resource_requirements: ModelResourceRequirements = Field(description="Hardware requirements")
    
    # Optimization configuration
    hardware_acceleration: HardwareAcceleration = Field(default_factory=HardwareAcceleration)
    quantization: QuantizationConfig = Field(default_factory=QuantizationConfig)
    optimizations: ModelOptimizations = Field(default_factory=ModelOptimizations)
    
    # API configuration (for API-based models)
    api_config: Optional[APIConfiguration] = Field(None, description="API configuration")
    
    # Generation defaults
    default_generation_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "max_new_tokens": 2048,
            "repetition_penalty": 1.1,
            "do_sample": True,
            "pad_token_id": None,
            "eos_token_id": None,
        },
        description="Default generation parameters"
    )
    
    # Security and safety
    safety_config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "enable_content_filter": True,
            "toxicity_threshold": 0.8,
            "prompt_injection_detection": True,
            "max_generation_time": 30,
        },
        description="Safety and content filtering configuration"
    )
    
    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: List[str] = Field(default_factory=list, description="Model tags for categorization")
    system_model_id: Optional[str] = Field(None, description="Unique system-generated ID for the model, used for de-fingerprinting")
    ghost_security_config: Optional[GhostSecurityConfig] = Field(None, description="Configuration for Ghost Security Manager")
    
    @root_validator(skip_on_failure=True)
    def validate_model_configuration(cls, values):
        """Validate model configuration consistency"""
        model_type = values.get('model_type')
        model_path = values.get('model_path')
        api_config = values.get('api_config')
        
        # Local models must have a path
        if model_type in [ModelType.LOCAL_TRANSFORMER, ModelType.LOCAL_QUANTIZED] and not model_path:
            raise ValueError("Local models must specify a model_path")
        
        # API models must have API configuration
        if model_type == ModelType.API_PROXY and not api_config:
            raise ValueError("API proxy models must specify api_config")
        
        # Quantized models must have quantization config
        quantization = values.get('quantization', {})
        if model_type == ModelType.LOCAL_QUANTIZED and not quantization.enabled:
            raise ValueError("Quantized models must have quantization enabled")
        
        return values
    
    def update_timestamp(self):
        """Update the last modified timestamp"""
        self.updated_at = datetime.now(timezone.utc)
    
    def supports_capability(self, capability: ModelCapability) -> bool:
        """Check if model supports a specific capability"""
        return capability in self.capabilities
    
    def estimate_memory_usage(self, batch_size: int = 1, sequence_length: int = 2048) -> float:
        """Estimate memory usage for given parameters"""
        base_memory = self.resource_requirements.gpu_memory_gb
        
        # Scale based on batch size and sequence length
        memory_scaling = self.resource_requirements.memory_scaling_factor
        scaling_factor = (batch_size * sequence_length / 2048) * memory_scaling
        
        return base_memory * scaling_factor
    
    @property
    def is_multimodal(self) -> bool:
        """Check if model supports multiple modalities"""
        return len(self.supported_modalities) > 1
    
    @property
    def is_agentic(self) -> bool:
        """Check if model supports agentic capabilities"""
        agentic_capabilities = {
            ModelCapability.TOOL_CALLING,
            ModelCapability.FUNCTION_CALLING,
            ModelCapability.AGENT_ORCHESTRATION
        }
        return any(cap in self.capabilities for cap in agentic_capabilities)


class ModelRegistry(BaseModel):
    """Model registry with capability-based routing"""
    models: Dict[str, ModelConfiguration] = Field(default_factory=dict, description="Registered models")
    capability_routing: Dict[ModelCapability, List[str]] = Field(
        default_factory=dict,
        description="Capability to model mapping"
    )
    fallback_chain: List[str] = Field(default_factory=list, description="Default fallback chain")
    
    def register_model(self, model_config: ModelConfiguration):
        """Register a new model in the registry"""
        self.models[model_config.model_id] = model_config
        
        # Update capability routing
        for capability in model_config.capabilities:
            if capability not in self.capability_routing:
                self.capability_routing[capability] = []
            
            if model_config.model_id not in self.capability_routing[capability]:
                self.capability_routing[capability].append(model_config.model_id)
    
    def get_models_by_capability(self, capability: ModelCapability) -> List[ModelConfiguration]:
        """Get all models that support a specific capability"""
        model_ids = self.capability_routing.get(capability, [])
        return [self.models[model_id] for model_id in model_ids if model_id in self.models]
    
    def get_best_model_for_task(
        self, 
        capabilities: List[ModelCapability],
        resource_constraints: Optional[Dict[str, Any]] = None
    ) -> Optional[ModelConfiguration]:
        """Select the best model for a given task and resource constraints"""
        # Find models that support all required capabilities
        candidate_models = []
        
        for model_id, model_config in self.models.items():
            if all(cap in model_config.capabilities for cap in capabilities):
                candidate_models.append(model_config)
        
        if not candidate_models:
            return None
        
        # Apply resource constraints if specified
        if resource_constraints:
            gpu_memory_limit = resource_constraints.get('gpu_memory_gb')
            if gpu_memory_limit:
                candidate_models = [
                    model for model in candidate_models
                    if model.resource_requirements.gpu_memory_gb <= gpu_memory_limit
                ]
        
        if not candidate_models:
            return None
        
        # Rank by parameter count (assuming larger is better, within constraints)
        candidate_models.sort(
            key=lambda m: m.parameter_count or 0,
            reverse=True
        )
        
        return candidate_models[0]


class ModelLoadRequest(BaseModel):
    """Request to load a specific model"""
    model_id: str = Field(description="Model identifier to load")
    device: Optional[str] = Field(None, description="Target device (cuda:0, cpu, etc.)")
    quantization_override: Optional[QuantizationConfig] = Field(None, description="Override quantization settings")
    optimization_override: Optional[ModelOptimizations] = Field(None, description="Override optimization settings")
    
    # Generation parameter overrides
    generation_config_override: Optional[Dict[str, Any]] = Field(None, description="Override generation config")
    
    # Resource allocation
    max_memory_allocation: Optional[str] = Field(None, description="Maximum memory allocation")
    reserved_memory: Optional[str] = Field(None, description="Reserved memory for other processes")



class AntiFingerprintingConfig(BaseModel):
    mode: str = Field(default="stealth")
    target_vectors: List[str] = Field(default_factory=list)
    obfuscation_strength: float = Field(default=0.5)
    mimicry_target: Optional[str] = None
    temporal_variance: bool = True
    signature_masking: bool = True
    false_positives: bool = False
    response_delay_variance: Tuple[float, float] = Field(default=(0.1, 0.5))
    pattern_corruption_rate: float = Field(default=0.15)
    decoy_responses_enabled: bool = False
    behavioral_drift_rate: float = Field(default=0.05)

class ModelSteganographyConfig(BaseModel):
    watermark_enabled: bool = False
    covert_channels: List[str] = Field(default_factory=list)
    hidden_payloads: Dict[str, str] = Field(default_factory=dict)
    extraction_keys: Dict[str, str] = Field(default_factory=dict)

class GhostSecurityConfig(BaseModel):
    enabled: bool = Field(default=False, description="Enable Ghost Security Manager for this model")
    anti_fingerprinting: AntiFingerprintingConfig = Field(default_factory=AntiFingerprintingConfig)
    steganography: ModelSteganographyConfig = Field(default_factory=ModelSteganographyConfig)
    
class ModelLoadResponse(BaseModel):
    """Response from model loading operation"""
    model_id: str = Field(description="Loaded model identifier")
    success: bool = Field(description="Load operation success status")
    message: str = Field(description="Status message")
    
    # Performance metrics
    load_time_seconds: float = Field(description="Model loading time")
    memory_usage_gb: float = Field(description="Actual memory usage")
    estimated_tokens_per_second: Optional[float] = Field(None, description="Performance estimate")
    
    # Model information
    model_info: Optional[Dict[str, Any]] = Field(None, description="Loaded model metadata")
    device_allocation: Optional[Dict[str, str]] = Field(None, description="Device allocation details")
    
    # Error information (if applicable)
    error_type: Optional[str] = Field(None, description="Error type if load failed")
    error_details: Optional[str] = Field(None, description="Detailed error information")


# Type aliases for convenience
ModelID = str
CapabilitySet = List[ModelCapability]
ResourceConstraints = Dict[str, Any]


class ModelUnloadRequest(BaseModel):
    """Request to unload a specific model"""
    model_id: str = Field(description="Model identifier to unload")

class ModelUnloadResponse(BaseModel):
    """Response from model unloading operation"""
    model_id: str = Field(description="Unloaded model identifier")
    success: bool = Field(description="Unload operation success status")
    message: str = Field(description="Status message")

class ModelGenerationRequest(BaseModel):
    """Request for model generation"""
    model_id: str = Field(description="Model identifier to use for generation")
    prompt: str = Field(description="The input prompt for the model")
    generation_config: Optional[Dict[str, Any]] = Field(None, description="Override generation parameters")

class ModelGenerationResponse(BaseModel):
    """Response from a model generation"""
    model_id: str = Field(description="Model identifier used for generation")
    generated_text: str = Field(description="The generated text")
    success: bool = Field(description="Generation success status")
    error_message: Optional[str] = Field(None, description="Error message if generation failed")

class ModelStatus(BaseModel):
    """Represents the status of a loaded model"""
    model_id: str = Field(description="Model identifier")
    status: Literal["loading", "loaded", "unloaded", "error"] = Field(description="Current status of the model")
    memory_usage_gb: float = Field(description="Memory usage in GB")
    device: str = Field(description="Device the model is loaded on")
    error_message: Optional[str] = Field(None, description="Error message if status is 'error'")
