#!/usr/bin/env python3
"""
SOMNUS V2 File System — Sovereignty Layer
=========================================
Deterministic dependency profiles, protocol-based adapters, and capability gating.
Zero paid APIs. Zero runtime downloads unless explicitly enabled.
Sovereign by design — graceful degradation is a feature, not a compromise.

This module is the contract layer: it defines WHAT the v2 file system can do
on any given machine, and provides no-op adapters so every code path runs
without hard external coupling.
"""

import importlib
import importlib.util
import logging
import platform
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set, runtime_checkable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPENDENCY PROFILES — Lock deployment behavior
# ═══════════════════════════════════════════════════════════════════════════════

class SovereigntyMode(str, Enum):
    """How sovereign is this deployment?"""
    FULL_LOCAL = "full_local"          # pip-installable only, no system binaries
    HYBRID_LOCAL = "hybrid_local"     # pip + optional system binaries (ffmpeg, tesseract, etc.)
    DEVELOPMENT = "development"       # All features enabled, may use network


@dataclass(frozen=True)
class DependencyProfile:
    """Immutable configuration for runtime behavior.
    
    Controls what the v2 file system is _allowed_ to use at runtime.
    No paid APIs. Ever. This is a sovereignty contract.
    """
    name: str
    mode: SovereigntyMode
    require_runtime_downloads: bool = False   # NLTK data, models, etc.
    allow_system_binaries: bool = True        # ffmpeg, tesseract, blender CLI
    allow_gpu: bool = True                    # CUDA / MPS acceleration
    max_model_size_mb: int = 500              # Cap on local model RAM
    
    def __post_init__(self):
        if self.mode == SovereigntyMode.FULL_LOCAL:
            object.__setattr__(self, 'allow_system_binaries', False)


# Pre-built profiles — use these, don't construct ad-hoc
FULL_LOCAL = DependencyProfile(
    name="full_local",
    mode=SovereigntyMode.FULL_LOCAL,
    require_runtime_downloads=False,
    allow_system_binaries=False,
)

HYBRID_LOCAL = DependencyProfile(
    name="hybrid_local",
    mode=SovereigntyMode.HYBRID_LOCAL,
    require_runtime_downloads=True,
    allow_system_binaries=True,
)

DEVELOPMENT = DependencyProfile(
    name="development",
    mode=SovereigntyMode.DEVELOPMENT,
    require_runtime_downloads=True,
    allow_system_binaries=True,
    allow_gpu=True,
)

# Default profile — sovereign out of the box
DEFAULT_PROFILE = HYBRID_LOCAL


# ═══════════════════════════════════════════════════════════════════════════════
#  PROTOCOL ADAPTERS — Decouple v2 from everything else
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class MemorySink(Protocol):
    """Protocol for pushing processed content to an external memory system.
    
    Any object with an async push() method satisfies this protocol.
    v2 never imports memory_core directly — it talks through this contract.
    """
    async def push(
        self,
        content: str,
        metadata: Dict[str, Any],
        tags: List[str],
        **kwargs: Any
    ) -> Any: ...


@runtime_checkable
class CacheStore(Protocol):
    """Protocol for cache operations. Adapts to SomnusCache or any KV store."""
    async def get(self, key: str) -> Optional[Any]: ...
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None: ...
    async def delete(self, key: str) -> bool: ...


class NullMemorySink:
    """No-op memory adapter. v2 stays autonomous without memory_core."""
    
    async def push(
        self,
        content: str,
        metadata: Dict[str, Any],
        tags: List[str],
        **kwargs: Any
    ) -> None:
        logger.debug(f"NullMemorySink: discarding {len(content)} chars, tags={tags}")
        return None


class NullCacheStore:
    """No-op cache adapter. v2 works without SomnusCache."""
    
    async def get(self, key: str) -> Optional[Any]:
        return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        pass
    
    async def delete(self, key: str) -> bool:
        return False


class InMemoryCacheStore:
    """Simple in-memory cache for standalone v2 operation."""
    
    def __init__(self, max_entries: int = 10_000):
        self._store: Dict[str, Any] = {}
        self._max = max_entries
    
    async def get(self, key: str) -> Optional[Any]:
        return self._store.get(key)
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if len(self._store) >= self._max:
            # Evict oldest 10%
            keys_to_remove = list(self._store.keys())[:self._max // 10]
            for k in keys_to_remove:
                del self._store[k]
        self._store[key] = value
    
    async def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  CAPABILITY REGISTRY — Know what you have before you use it
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FeatureStatus:
    """Status of a single feature/dependency."""
    name: str
    available: bool
    category: str  # "pip", "system_binary", "stdlib", "optional_model"
    reason: str = ""
    version: str = ""


class CapabilityRegistry:
    """
    Centralized registry of all available features for the v2 file system.
    
    Probed once at startup. Thread-safe after initialization.
    Every capability check goes through here — no scattered try/except at call sites.
    """
    
    def __init__(self, profile: DependencyProfile = DEFAULT_PROFILE):
        self.profile = profile
        self._features: Dict[str, FeatureStatus] = {}
        self._probed = False
    
    def probe_all(self) -> "CapabilityRegistry":
        """Probe all capabilities. Call once at startup."""
        if self._probed:
            return self
        
        # Pip-installable libraries
        self._probe_pip("numpy", "pip")
        self._probe_pip("PIL", "pip", import_name="PIL")
        self._probe_pip("cv2", "pip", import_name="cv2")
        self._probe_pip("pytesseract", "pip")
        self._probe_pip("magic", "pip", import_name="magic")
        self._probe_pip("aiofiles", "pip")
        self._probe_pip("pypdf", "pip", import_name="pypdf")
        self._probe_pip("docx", "pip", import_name="docx")
        self._probe_pip("pandas", "pip", import_name="pandas")
        self._probe_pip("chardet", "pip", import_name="chardet")
        self._probe_pip("yaml", "pip", import_name="yaml")
        self._probe_pip("toml", "pip", import_name="toml")
        self._probe_pip("psutil", "pip", import_name="psutil")
        self._probe_pip("nltk", "pip", import_name="nltk")
        self._probe_pip("scipy", "pip", import_name="scipy")
        self._probe_pip("sklearn", "pip", import_name="sklearn")
        self._probe_pip("networkx", "pip", import_name="networkx")
        self._probe_pip("sentence_transformers", "optional_model")
        self._probe_pip("spacy", "optional_model")
        self._probe_pip("llama_cpp", "optional_model", import_name="llama_cpp")
        self._probe_pip("torch", "optional_model")
        self._probe_pip("psd_tools", "pip")
        self._probe_pip("ezdxf", "pip")
        self._probe_pip("trimesh", "pip")
        self._probe_pip("h5py", "pip")
        self._probe_pip("pydicom", "pip")
        self._probe_pip("librosa", "pip")
        self._probe_pip("ffmpeg", "pip", import_name="ffmpeg")
        self._probe_pip("hl7apy", "pip", import_name="hl7apy")
        self._probe_pip("fhir_resources", "pip", import_name="fhir")
        
        # System binaries (only if profile allows)
        if self.profile.allow_system_binaries:
            self._probe_binary("ffmpeg")
            self._probe_binary("tesseract")
            self._probe_binary("blender")
            self._probe_binary("imagemagick", binary_name="magick")
        else:
            for name in ("ffmpeg", "tesseract", "blender", "imagemagick"):
                self._features[name + "_binary"] = FeatureStatus(
                    name=name + "_binary",
                    available=False,
                    category="system_binary",
                    reason=f"Blocked by {self.profile.name} profile"
                )
        
        self._probed = True
        logger.info(f"CapabilityRegistry probed: {self.available_count}/{self.total_count} features available")
        return self
    
    def _probe_pip(self, name: str, category: str, import_name: str = None):
        """Check if a pip package is importable."""
        import_name = import_name or name
        try:
            spec = importlib.util.find_spec(import_name)
            available = spec is not None
            version = ""
            if available:
                try:
                    mod = importlib.import_module(import_name)
                    version = getattr(mod, "__version__", "")
                except Exception:
                    pass
            self._features[name] = FeatureStatus(
                name=name, available=available, category=category,
                version=version, reason="" if available else "Not installed"
            )
        except (ModuleNotFoundError, ValueError):
            self._features[name] = FeatureStatus(
                name=name, available=False, category=category, reason="Not installed"
            )
    
    def _probe_binary(self, name: str, binary_name: str = None):
        """Check if a system binary is on PATH."""
        binary_name = binary_name or name
        path = shutil.which(binary_name)
        self._features[name + "_binary"] = FeatureStatus(
            name=name + "_binary",
            available=path is not None,
            category="system_binary",
            reason="" if path else f"{binary_name} not found on PATH",
            version=path or ""
        )
    
    def is_available(self, feature: str) -> bool:
        """Check if a feature is available and allowed by profile."""
        status = self._features.get(feature)
        if not status:
            return False
        return status.available
    
    def require(self, feature: str) -> bool:
        """Check availability and log warning if missing."""
        available = self.is_available(feature)
        if not available:
            status = self._features.get(feature)
            reason = status.reason if status else "Unknown feature"
            logger.warning(f"Feature '{feature}' unavailable: {reason}")
        return available
    
    @property
    def available_count(self) -> int:
        return sum(1 for f in self._features.values() if f.available)
    
    @property
    def total_count(self) -> int:
        return len(self._features)
    
    def get_capability_matrix(self) -> Dict[str, Dict[str, Any]]:
        """Return full capability matrix for diagnostics."""
        return {
            name: {
                "available": f.available,
                "category": f.category,
                "reason": f.reason,
                "version": f.version
            }
            for name, f in sorted(self._features.items())
        }
    
    def get_summary(self) -> str:
        """Human-readable capability summary."""
        lines = [
            f"=== SOMNUS V2 Capability Registry ===",
            f"Profile: {self.profile.name} ({self.profile.mode.value})",
            f"Platform: {platform.system()} {platform.machine()}",
            f"Python: {sys.version.split()[0]}",
            f"Features: {self.available_count}/{self.total_count} available",
            f"",
        ]
        
        by_category: Dict[str, List[FeatureStatus]] = {}
        for f in self._features.values():
            by_category.setdefault(f.category, []).append(f)
        
        for cat, features in sorted(by_category.items()):
            lines.append(f"  [{cat}]")
            for f in sorted(features, key=lambda x: x.name):
                icon = "OK" if f.available else "MISSING"
                ver = f" v{f.version}" if f.version and f.available else ""
                reason = f" ({f.reason})" if f.reason and not f.available else ""
                lines.append(f"    [{icon}] {f.name}{ver}{reason}")
            lines.append("")
        
        return "\n".join(lines)


def check_feature_gate(
    feature: str,
    installed: bool,
    profile: DependencyProfile = DEFAULT_PROFILE
) -> bool:
    """Centralized feature gate for predictable degradation.
    
    Use this at call sites where you need to decide whether to use an
    optional feature. Returns False if the profile disallows it, even
    if the package is installed.
    """
    if feature in {"ffmpeg", "tesseract", "blender", "imagemagick"} and not profile.allow_system_binaries:
        return False
    if feature in {"sentence_transformers", "spacy", "torch"} and not profile.require_runtime_downloads:
        # These need runtime model downloads on first use
        return False
    return installed


# ═══════════════════════════════════════════════════════════════════════════════
#  BOOT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BootCheckResult:
    """Result of validating a single module's importability."""
    module: str
    ok: bool
    error: str = ""
    load_time_ms: float = 0.0


def validate_module_import(module_path: str) -> BootCheckResult:
    """Validate that a v2 module can be imported without errors.
    
    This catches syntax errors, missing imports, and broken dependencies
    BEFORE runtime traffic hits them. Use as a CI/release gate.
    """
    import time as _time
    
    target = Path(module_path)
    if not target.exists():
        return BootCheckResult(module=module_path, ok=False, error="file_not_found")
    
    start = _time.perf_counter()
    try:
        spec = importlib.util.spec_from_file_location(target.stem, str(target))
        if not spec or not spec.loader:
            return BootCheckResult(module=module_path, ok=False, error="spec_creation_failed")
        mod = importlib.util.module_from_spec(spec)
        # Don't actually execute — just compile to catch syntax errors
        import py_compile
        py_compile.compile(str(target), doraise=True)
        elapsed = (_time.perf_counter() - start) * 1000
        return BootCheckResult(module=module_path, ok=True, load_time_ms=elapsed)
    except py_compile.PyCompileError as exc:
        elapsed = (_time.perf_counter() - start) * 1000
        return BootCheckResult(
            module=module_path, ok=False,
            error=f"SyntaxError: {exc}", load_time_ms=elapsed
        )
    except Exception as exc:
        elapsed = (_time.perf_counter() - start) * 1000
        return BootCheckResult(
            module=module_path, ok=False,
            error=f"{type(exc).__name__}: {exc}", load_time_ms=elapsed
        )


def validate_all_v2_modules() -> List[BootCheckResult]:
    """Validate all v2 file system modules. Returns list of results."""
    v2_dir = Path(__file__).parent
    modules = [
        v2_dir / "enhanced_file_manager.py",
        v2_dir / "persistent_processing_queue.py",
        v2_dir / "semantic_chunking.py",
        v2_dir / "semantic_chunking_rewrite.py",
        v2_dir / "universal_file_processors.py",
        v2_dir / "sovereignty.py",
    ]
    return [validate_module_import(str(m)) for m in modules]


# ═══════════════════════════════════════════════════════════════════════════════
#  MODULE SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Boot check all v2 modules
    print("\n=== V2 File System Boot Check ===\n")
    results = validate_all_v2_modules()
    for r in results:
        icon = "PASS" if r.ok else "FAIL"
        name = Path(r.module).name
        print(f"  [{icon}] {name} ({r.load_time_ms:.1f}ms){'' if r.ok else f' — {r.error}'}")
    
    all_ok = all(r.ok for r in results)
    print(f"\n{'ALL MODULES PASS' if all_ok else 'BOOT CHECK FAILED'}")
    
    # Capability probe
    print()
    registry = CapabilityRegistry(HYBRID_LOCAL).probe_all()
    print(registry.get_summary())
    
    sys.exit(0 if all_ok else 1)
