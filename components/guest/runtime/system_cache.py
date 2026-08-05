#!/usr/bin/env python3
"""
SOMNUS Unified Performance Cache Engine v3.0
============================================

Production-ready runtime caching system integrated with SOMNUS memory core.
Provides hot performance caching complementary to persistent memory storage.

Integration Strategy:
- Runtime performance cache (hot data, fast access)
- Session-aware namespacing with automatic cleanup
- Model/artifact result caching with dependency tracking
- VM state and metrics caching
- Background persistence and cleanup loops
- LRU eviction with intelligent priority scoring
"""

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import pickle
import sqlite3
import threading
import time
import weakref
import zlib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union, Callable, Protocol
from uuid import UUID, uuid4
from enum import Enum
from contextlib import asynccontextmanager
import shelve

from core.memory_core import MemoryManager, MemoryType, MemoryImportance

logger = logging.getLogger(__name__)


class _AsyncIOLoopManager:
    """Manages a dedicated asyncio event loop in a separate thread to safely run async code from sync contexts."""
    _instance: Optional["_AsyncIOLoopManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'loop'):
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="SomnusCache-AsyncIO")
            self.thread.start()
            logger.info("Started dedicated asyncio event loop for SomnusCache.")

    def run_coroutine(self, coro, timeout=10):
        """Runs a coroutine in the managed event loop and waits for the result with a timeout."""
        if not self.thread.is_alive() or not self.loop.is_running():
            raise RuntimeError("AsyncIO event loop is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Coroutine execution timed out after {timeout} seconds.")
            future.cancel()
            return None
        except Exception as e:
            logger.error(f"Exception in coroutine execution: {e}", exc_info=True)
            return None

    def submit_coroutine(self, coro):
        """Submits a coroutine to the event loop without waiting for the result."""
        if not self.thread.is_alive() or not self.loop.is_running():
            logger.error("AsyncIO event loop is not running. Cannot submit coroutine.")
            return
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        future.add_done_callback(self._log_future_exception)

    def _log_future_exception(self, future):
        try:
            if future.cancelled():
                return
            if future.exception():
                logger.error(f"Async task failed in background: {future.exception()}", exc_info=future.exception())
        except (asyncio.CancelledError, concurrent.futures.CancelledError):
            pass

    def shutdown(self):
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                logger.warning("AsyncIO thread did not shut down gracefully.")
        logger.info("Shut down dedicated asyncio event loop.")


class CacheNamespace(str, Enum):
    GLOBAL = "global"
    SESSION = "session"
    USER = "user"
    VM = "vm"
    ARTIFACT = "artifact"
    MODEL = "model"
    RESEARCH = "research"
    SYSTEM = "system"


class CachePriority(str, Enum):
    CRITICAL = "critical"    # Never evict
    HIGH = "high"           # Evict last
    MEDIUM = "medium"       # Normal eviction
    LOW = "low"            # Evict early
    TEMPORARY = "temporary" # Evict first


@dataclass
class CacheEntry:
    """Enhanced cache entry with comprehensive metadata and lifecycle management"""
    key: str
    value: Any
    namespace: CacheNamespace
    priority: CachePriority
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    size_bytes: int = 0
    ttl_seconds: Optional[int] = None
    expires_at: Optional[datetime] = None
    tags: Set[str] = field(default_factory=set)
    compressed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    on_expire_callback: Optional[Callable] = None
    
    # Performance tracking
    access_times: List[float] = field(default_factory=list)
    hit_count: int = 0
    
    def __post_init__(self):
        if self.ttl_seconds and not self.expires_at:
            self.expires_at = self.created_at + timedelta(seconds=self.ttl_seconds)
    
    def update_access(self):
        """Update access tracking with temporal patterns"""
        now = datetime.now(timezone.utc)
        current_time = time.time()
        
        self.last_accessed = now
        self.access_count += 1
        self.hit_count += 1
        self.access_times.append(current_time)
        
        # Keep only recent access times (last hour)
        cutoff = current_time - 3600
        self.access_times = [t for t in self.access_times if t > cutoff]
    
    def is_expired(self) -> bool:
        """Check if entry has expired"""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at
    
    def calculate_priority_score(self) -> float:
        """Calculate dynamic priority score for eviction decisions"""
        now = time.time()
        
        # Base priority score
        priority_weights = {
            CachePriority.CRITICAL: 1000.0,
            CachePriority.HIGH: 100.0,
            CachePriority.MEDIUM: 10.0,
            CachePriority.LOW: 1.0,
            CachePriority.TEMPORARY: 0.1
        }
        base_score = priority_weights[self.priority]
        
        # Recency factor (last 5 minutes = max boost)
        age_seconds = now - self.last_accessed.timestamp()
        recency_factor = max(0.1, 1.0 - (age_seconds / 300))
        
        # Frequency factor (recent accesses)
        recent_accesses = len([t for t in self.access_times if now - t < 300])
        frequency_factor = min(2.0, 1.0 + (recent_accesses / 10))
        
        # Size penalty (prefer keeping smaller items)
        size_factor = max(0.5, 1.0 - (self.size_bytes / (10 * 1024 * 1024)))  # 10MB baseline
        
        return base_score * recency_factor * frequency_factor * size_factor
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize entry for persistence"""
        return {
            'key': self.key,
            'namespace': self.namespace.value,
            'priority': self.priority.value,
            'created_at': self.created_at.isoformat(),
            'last_accessed': self.last_accessed.isoformat(),
            'access_count': self.access_count,
            'size_bytes': self.size_bytes,
            'ttl_seconds': self.ttl_seconds,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'tags': list(self.tags),
            'compressed': self.compressed,
            'metadata': self.metadata,
            'hit_count': self.hit_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], value: Any) -> 'CacheEntry':
        """Deserialize entry from persistence"""
        entry = cls(
            key=data['key'],
            value=value,
            namespace=CacheNamespace(data['namespace']),
            priority=CachePriority(data['priority']),
            created_at=datetime.fromisoformat(data['created_at']),
            last_accessed=datetime.fromisoformat(data['last_accessed']),
            access_count=data['access_count'],
            size_bytes=data['size_bytes'],
            ttl_seconds=data.get('ttl_seconds'),
            expires_at=datetime.fromisoformat(data['expires_at']) if data.get('expires_at') else None,
            tags=set(data.get('tags', [])),
            compressed=data.get('compressed', False),
            metadata=data.get('metadata', {}),
            hit_count=data.get('hit_count', 0)
        )
        return entry


class CacheMetrics:
    """Comprehensive cache performance metrics"""
    
    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.deletes = 0
        self.evictions = 0
        self.expired_cleanups = 0
        self.memory_usage_bytes = 0
        self.disk_usage_bytes = 0
        self.hit_ratio = 0.0
        self.avg_access_time_ms = 0.0
        self.namespace_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.start_time = time.time()
    
    def record_hit(self, namespace: CacheNamespace):
        self.hits += 1
        self.namespace_stats[namespace.value]['hits'] += 1
        self._update_hit_ratio()
    
    def record_miss(self, namespace: CacheNamespace):
        self.misses += 1
        self.namespace_stats[namespace.value]['misses'] += 1
        self._update_hit_ratio()
    
    def record_set(self, namespace: CacheNamespace):
        self.sets += 1
        self.namespace_stats[namespace.value]['sets'] += 1
    
    def record_delete(self, namespace: CacheNamespace):
        self.deletes += 1
        self.namespace_stats[namespace.value]['deletes'] += 1
    
    def record_eviction(self, namespace: CacheNamespace):
        self.evictions += 1
        self.namespace_stats[namespace.value]['evictions'] += 1
    
    def _update_hit_ratio(self):
        total = self.hits + self.misses
        self.hit_ratio = self.hits / total if total > 0 else 0.0
    
    def get_summary(self) -> Dict[str, Any]:
        uptime = time.time() - self.start_time
        total_operations = self.hits + self.misses + self.sets + self.deletes
        ops_per_second = total_operations / uptime if uptime > 0 else 0.0
        
        return {
            'uptime_seconds': uptime,
            'total_operations': total_operations,
            'operations_per_second': ops_per_second,
            'hit_ratio': self.hit_ratio,
            'hits': self.hits,
            'misses': self.misses,
            'sets': self.sets,
            'deletes': self.deletes,
            'evictions': self.evictions,
            'expired_cleanups': self.expired_cleanups,
            'memory_usage_mb': self.memory_usage_bytes / (1024 * 1024),
            'disk_usage_mb': self.disk_usage_bytes / (1024 * 1024),
            'avg_access_time_ms': self.avg_access_time_ms,
            'namespace_stats': dict(self.namespace_stats)
        }


class PersistenceManager:
    """Handles cache persistence to disk with compression and integrity checking"""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_db = self.cache_dir / "cache_metadata.db"
        self.data_shelf_path = str(self.cache_dir / "cache_data")
        self.lock = threading.Lock()
        
        self._init_metadata_db()
    
    def _init_metadata_db(self):
        """Initialize SQLite database for cache metadata"""
        conn = sqlite3.connect(self.metadata_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                key TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                entry_data TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                last_accessed TIMESTAMP NOT NULL,
                size_bytes INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_namespace ON cache_entries(namespace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_last_accessed ON cache_entries(last_accessed)")
        conn.commit()
        conn.close()
    
    def save_entry(self, entry: CacheEntry) -> bool:
        """Save cache entry to persistent storage"""
        try:
            with self.lock:
                # Save metadata to SQLite
                conn = sqlite3.connect(self.metadata_db)
                conn.execute("""
                    INSERT OR REPLACE INTO cache_entries 
                    (key, namespace, entry_data, created_at, last_accessed, size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    entry.key,
                    entry.namespace.value,
                    json.dumps(entry.to_dict()),
                    entry.created_at.isoformat(),
                    entry.last_accessed.isoformat(),
                    entry.size_bytes
                ))
                conn.commit()
                conn.close()
                
                # Save value data to shelf
                with shelve.open(self.data_shelf_path, writeback=False) as shelf:
                    # Compress large values
                    value_data = entry.value
                    if entry.size_bytes > 4096:  # 4KB threshold
                        try:
                            pickled = pickle.dumps(value_data)
                            compressed = zlib.compress(pickled, level=6)
                            if len(compressed) < len(pickled) * 0.8:
                                value_data = compressed
                                entry.compressed = True
                        except Exception:
                            pass  # Use uncompressed if compression fails
                    
                    shelf[entry.key] = value_data
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save cache entry {entry.key}: {e}")
            return False
    
    def load_entry(self, key: str) -> Optional[CacheEntry]:
        """Load cache entry from persistent storage"""
        try:
            with self.lock:
                # Load metadata from SQLite
                conn = sqlite3.connect(self.metadata_db)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM cache_entries WHERE key = ?", (key,))
                row = cursor.fetchone()
                conn.close()
                
                if not row:
                    return None
                
                # Load value data from shelf
                with shelve.open(self.data_shelf_path, flag='r') as shelf:
                    if key not in shelf:
                        return None
                    
                    value_data = shelf[key]
                
                # Deserialize entry
                entry_dict = json.loads(row['entry_data'])
                
                # Decompress value if needed
                if entry_dict.get('compressed', False):
                    try:
                        value_data = pickle.loads(zlib.decompress(value_data))
                    except Exception:
                        pass  # Use as-is if decompression fails
                
                entry = CacheEntry.from_dict(entry_dict, value_data)
                return entry
            
        except Exception as e:
            logger.error(f"Failed to load cache entry {key}: {e}")
            return None
    
    def delete_entry(self, key: str) -> bool:
        """Delete cache entry from persistent storage"""
        try:
            with self.lock:
                # Delete from SQLite
                conn = sqlite3.connect(self.metadata_db)
                conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                conn.commit()
                conn.close()
                
                # Delete from shelf
                with shelve.open(self.data_shelf_path, writeback=False) as shelf:
                    if key in shelf:
                        del shelf[key]
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete cache entry {key}: {e}")
            return False
    
    def load_all_metadata(self) -> List[Dict[str, Any]]:
        """Load all cache entry metadata for initialization"""
        try:
            with self.lock:
                conn = sqlite3.connect(self.metadata_db)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM cache_entries ORDER BY last_accessed DESC")
                rows = cursor.fetchall()
                conn.close()
                
                return [dict(row) for row in rows]
            
        except Exception as e:
            logger.error(f"Failed to load cache metadata: {e}")
            return []
    
    def cleanup_expired(self, expired_keys: List[str]) -> int:
        """Cleanup expired entries from persistent storage"""
        if not expired_keys:
            return 0
        
        try:
            with self.lock:
                # Delete from SQLite
                conn = sqlite3.connect(self.metadata_db)
                placeholders = ','.join('?' * len(expired_keys))
                conn.execute(f"DELETE FROM cache_entries WHERE key IN ({placeholders})", expired_keys)
                deleted_count = conn.total_changes
                conn.commit()
                conn.close()
                
                # Delete from shelf
                with shelve.open(self.data_shelf_path, writeback=False) as shelf:
                    for key in expired_keys:
                        if key in shelf:
                            del shelf[key]
                
                return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired entries: {e}")
            return 0
    
    def get_disk_usage(self) -> int:
        """Get total disk usage in bytes"""
        try:
            total_size = 0
            for file_path in self.cache_dir.glob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
            return total_size
        except Exception:
            return 0


class SomnusCache:
    """Production‑ready unified cache engine for SOMNUS.

    This cache can optionally synchronize its entries with the persistent memory system
    defined in ``vm_memory_system.memory_core``.  When a ``MemoryManager`` instance is
    supplied via the ``memory_manager`` argument, each cache entry is also stored as a
    memory of type ``MemoryType.SYSTEM_EVENT`` (or a custom type) so that the cache
    state survives process restarts and can be queried through the normal memory APIs.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, memory_manager: Optional["MemoryManager"] = None):
        # Configuration with sensible defaults
        self.config = config or {}
        self.max_entries = self.config.get('max_entries', 10000)
        self.max_memory_mb = self.config.get('max_memory_mb', 512)
        self.max_memory_bytes = self.max_memory_mb * 1024 * 1024
        self.cache_dir = Path(self.config.get('cache_dir', 'data/runtime_cache'))
        self.persistence_enabled = self.config.get('persistence_enabled', True)
        self.cleanup_interval = self.config.get('cleanup_interval_seconds', 300)  # 5 minutes
        self.compression_threshold = self.config.get('compression_threshold_bytes', 4096)

        # Core storage
        self.entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self.current_memory_usage = 0
        self.lock = threading.RLock()

        # Namespace tracking
        self.namespace_keys: Dict[CacheNamespace, Set[str]] = defaultdict(set)
        self.session_keys: Dict[str, Set[str]] = defaultdict(set)  # session_id -> keys

        # Persistence and metrics
        self.persistence = PersistenceManager(self.cache_dir) if self.persistence_enabled else None
        self.metrics = CacheMetrics()

        # Optional integration with the global memory system
        self.memory_manager = memory_manager
        self.async_loop_manager: Optional[_AsyncIOLoopManager] = None

        # Background tasks
        self.cleanup_task: Optional[threading.Thread] = None
        self.running = False
        self._shutdown_event = threading.Event()

        # Weak references for callback cleanup
        self._expire_callbacks: Dict[str, weakref.ref] = {}

        # Load persisted entries (from local persistence) and, if a memory manager is provided,
        # also attempt to hydrate from the memory system.
        if self.persistence_enabled:
            self._load_from_persistence()
        if self.memory_manager:
            self.async_loop_manager = _AsyncIOLoopManager()
            logger.info("Hydrating cache from MemoryManager...")
            self.async_loop_manager.run_coroutine(self._load_from_memory_manager())

        logger.info(f"SOMNUS Cache initialized: {len(self.entries)} entries, {self.current_memory_usage / 1024 / 1024:.1f}MB")
    
    def start_background_cleanup(self):
        """Start background cleanup thread"""
        if self.cleanup_task and self.cleanup_task.is_alive():
            return
        
        self.running = True
        self._shutdown_event.clear()
        self.cleanup_task = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="SomnusCache-Cleanup"
        )
        self.cleanup_task.start()
        logger.info("Cache background cleanup started")
    
    def stop_background_cleanup(self):
        """Stop background cleanup thread"""
        self.running = False
        self._shutdown_event.set()
        if self.cleanup_task:
            self.cleanup_task.join(timeout=5)
        logger.info("Cache background cleanup stopped")
    
    def _cleanup_loop(self):
        """Background cleanup loop"""
        while self.running and not self._shutdown_event.wait(self.cleanup_interval):
            try:
                self.cleanup_expired()
                self._enforce_size_limits()
                self._update_memory_metrics()
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")
    
    async def _load_from_memory_manager(self) -> None:
        """Populate the cache with entries that were previously stored via the
        ``MemoryManager``.  Only entries that belong to the ``SYSTEM_EVENT``
        memory type and contain a ``cache_key`` field in their metadata are
        considered cache entries.
        """
        try:
            memories = await self.memory_manager.retrieve_memories(
                user_id="system", # System cache entries are stored under a global user
                memory_types=[MemoryType.SYSTEM_EVENT],
                limit=10000, # Load a reasonable number of recent cache entries
                include_content=True,
            )
            for mem in memories:
                meta = mem.get('metadata', {})
                cache_key = meta.get('cache_key')
                if not cache_key or cache_key in self.entries:
                    continue
                
                try:
                    value = pickle.loads(mem['content'].encode('latin1')) if isinstance(mem['content'], str) else pickle.loads(mem['content'])
                except (pickle.UnpicklingError, TypeError, AttributeError) as e:
                    logger.warning(f"Could not deserialize cache value for key {cache_key} from memory: {e}")
                    continue

                namespace = CacheNamespace(meta.get('namespace', CacheNamespace.GLOBAL.value))
                priority = CachePriority(meta.get('priority', CachePriority.MEDIUM.value))
                ttl = meta.get('ttl_seconds')
                tags = set(meta.get('tags', []))
                
                entry = CacheEntry(
                    key=cache_key,
                    value=value,
                    namespace=namespace,
                    priority=priority,
                    created_at=datetime.fromisoformat(mem.get('created_at')),
                    last_accessed=datetime.fromisoformat(mem.get('last_accessed')),
                    ttl_seconds=ttl,
                    tags=tags,
                )
                self.entries[cache_key] = entry
                self.current_memory_usage += entry.size_bytes
                self.namespace_keys[namespace].add(cache_key)
        except Exception as e:
            logger.error(f"Failed to hydrate cache from MemoryManager: {e}", exc_info=True)

    async def _store_in_memory_manager(self, entry: CacheEntry) -> None:
        """Store a cache entry in the global memory system.  The entry is saved as a
        ``SYSTEM_EVENT`` memory with additional metadata that allows it to be
        re‑hydrated later.
        """
        if not self.memory_manager:
            return
        try:
            # Serialize value to bytes for storage
            serialized_value = pickle.dumps(entry.value)

            await self.memory_manager.store_memory(
                user_id="system",
                content=serialized_value.decode('latin1'), # Store as a string
                memory_type=MemoryType.SYSTEM_EVENT,
                importance=MemoryImportance.MEDIUM,
                tags=list(entry.tags),
                metadata={
                    "cache_key": entry.key,
                    "namespace": entry.namespace.value,
                    "priority": entry.priority.value,
                    "ttl_seconds": entry.ttl_seconds,
                    "source": "SomnusCache"
                },
            )
        except Exception as e:
            logger.error(f"Failed to store cache entry {entry.key} in MemoryManager: {e}", exc_info=True)

    def set(
        self,
        key: str,
        value: Any,
        namespace: CacheNamespace = CacheNamespace.GLOBAL,
        priority: CachePriority = CachePriority.MEDIUM,
        ttl_seconds: Optional[int] = None,
        tags: Optional[Set[str]] = None,
        session_id: Optional[str] = None,
        on_expire: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Set cache entry with comprehensive options.

        If a ``MemoryManager`` is attached, the entry is also persisted to the
        global memory system.
        """
        start_time = time.time()
        try:
            with self.lock:
                full_key = self._create_full_key(key, namespace, session_id)
                try:
                    serialized = pickle.dumps(value)
                    size_bytes = len(serialized)
                except Exception:
                    size_bytes = len(str(value).encode('utf-8'))
                now = datetime.now(timezone.utc)
                entry = CacheEntry(
                    key=full_key,
                    value=value,
                    namespace=namespace,
                    priority=priority,
                    created_at=now,
                    last_accessed=now,
                    size_bytes=size_bytes,
                    ttl_seconds=ttl_seconds,
                    tags=tags or set(),
                    metadata=metadata or {},
                    on_expire_callback=on_expire,
                )
                if not self._make_space_if_needed(entry.size_bytes):
                    logger.warning(f"Failed to make space for cache entry {full_key}")
                    return False
                if full_key in self.entries:
                    old_entry = self.entries.pop(full_key)
                    self.current_memory_usage -= old_entry.size_bytes
                    self._remove_from_namespaces(old_entry)
                self.entries[full_key] = entry
                self.current_memory_usage += entry.size_bytes
                self.namespace_keys[namespace].add(full_key)
                if session_id:
                    self.session_keys[session_id].add(full_key)
                if on_expire:
                    self._expire_callbacks[full_key] = weakref.ref(on_expire)
                if self.persistence:
                    self.persistence.save_entry(entry)
                
                if self.memory_manager and self.async_loop_manager:
                    self.async_loop_manager.submit_coroutine(self._store_in_memory_manager(entry))

                self.metrics.record_set(namespace)
                self.metrics.memory_usage_bytes = self.current_memory_usage
                access_time_ms = (time.time() - start_time) * 1000
                self.metrics.avg_access_time_ms = (
                    self.metrics.avg_access_time_ms * 0.9 + access_time_ms * 0.1
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set cache entry {key}: {e}", exc_info=True)
            return False
    
    def get(self, 
            key: str, 
            namespace: CacheNamespace = CacheNamespace.GLOBAL,
            session_id: Optional[str] = None,
            default: Any = None) -> Any:
        """
        Get cache entry with namespace/session support.
        
        Args:
            key: Cache key
            namespace: Cache namespace
            session_id: Optional session ID
            default: Default value if not found
        """
        start_time = time.time()
        
        try:
            with self.lock:
                full_key = self._create_full_key(key, namespace, session_id)
                
                if full_key not in self.entries:
                    self.metrics.record_miss(namespace)
                    return default
                
                entry = self.entries[full_key]
                
                # Check expiration
                if entry.is_expired():
                    self._expire_entry(full_key, entry)
                    self.metrics.record_miss(namespace)
                    return default
                
                # Update access tracking
                entry.update_access()
                
                # Move to end for LRU
                self.entries.move_to_end(full_key)
                
                # Update metrics
                self.metrics.record_hit(namespace)
                access_time_ms = (time.time() - start_time) * 1000
                self.metrics.avg_access_time_ms = (
                    self.metrics.avg_access_time_ms * 0.9 + access_time_ms * 0.1
                )
                
                return entry.value
                
        except Exception as e:
            logger.error(f"Failed to get cache entry {key}: {e}", exc_info=True)
            self.metrics.record_miss(namespace)
            return default
    
    def delete(self, 
               key: str, 
               namespace: CacheNamespace = CacheNamespace.GLOBAL,
               session_id: Optional[str] = None) -> bool:
        """Delete cache entry"""
        try:
            with self.lock:
                full_key = self._create_full_key(key, namespace, session_id)
                
                if full_key not in self.entries:
                    return False
                
                entry = self.entries.pop(full_key)
                self.current_memory_usage -= entry.size_bytes
                self._remove_from_namespaces(entry)
                
                # Remove from session tracking
                if session_id and session_id in self.session_keys:
                    self.session_keys[session_id].discard(full_key)
                
                # Remove expire callback
                self._expire_callbacks.pop(full_key, None)
                
                # Delete from persistence
                if self.persistence:
                    self.persistence.delete_entry(full_key)
                
                self.metrics.record_delete(namespace)
                self.metrics.memory_usage_bytes = self.current_memory_usage
                
                return True
                
        except Exception as e:
            logger.error(f"Failed to delete cache entry {key}: {e}", exc_info=True)
            return False
    
    def clear_namespace(self, namespace: CacheNamespace) -> int:
        """Clear all entries in a namespace"""
        try:
            with self.lock:
                keys_to_remove = list(self.namespace_keys[namespace])
                removed_count = 0
                
                for full_key in keys_to_remove:
                    if full_key in self.entries:
                        entry = self.entries.pop(full_key)
                        self.current_memory_usage -= entry.size_bytes
                        removed_count += 1
                        
                        # Delete from persistence
                        if self.persistence:
                            self.persistence.delete_entry(full_key)
                        
                        # Remove expire callback
                        self._expire_callbacks.pop(full_key, None)
                
                # Clear namespace tracking
                self.namespace_keys[namespace].clear()
                
                # Remove from session tracking
                for session_keys in self.session_keys.values():
                    session_keys -= set(keys_to_remove)
                
                self.metrics.memory_usage_bytes = self.current_memory_usage
                
                logger.info(f"Cleared {removed_count} entries from namespace {namespace}")
                return removed_count
                
        except Exception as e:
            logger.error(f"Failed to clear namespace {namespace}: {e}", exc_info=True)
            return 0
    
    def clear_session(self, session_id: str) -> int:
        """Clear all entries for a session"""
        try:
            with self.lock:
                if session_id not in self.session_keys:
                    return 0
                
                keys_to_remove = list(self.session_keys[session_id])
                removed_count = 0
                
                for full_key in keys_to_remove:
                    if full_key in self.entries:
                        entry = self.entries.pop(full_key)
                        self.current_memory_usage -= entry.size_bytes
                        self._remove_from_namespaces(entry)
                        removed_count += 1
                        
                        # Delete from persistence
                        if self.persistence:
                            self.persistence.delete_entry(full_key)
                        
                        # Remove expire callback
                        self._expire_callbacks.pop(full_key, None)
                
                # Clear session tracking
                del self.session_keys[session_id]
                
                self.metrics.memory_usage_bytes = self.current_memory_usage
                
                logger.info(f"Cleared {removed_count} entries for session {session_id}")
                return removed_count
                
        except Exception as e:
            logger.error(f"Failed to clear session {session_id}: {e}", exc_info=True)
            return 0
    
    def clear_all(self) -> int:
        """Clear **all** cache entries across every namespace and session.
        
        Returns
        -------
        int
            Number of entries removed.
        """
        try:
            with self.lock:
                removed_count = len(self.entries)
                
                # Delete persistence data
                if self.persistence:
                    # Load all keys first to avoid mutating while iterating
                    all_keys = list(self.entries.keys())
                    for key in all_keys:
                        self.persistence.delete_entry(key)
                
                # Reset internal structures
                self.entries.clear()
                self.namespace_keys.clear()
                self.session_keys.clear()
                self._expire_callbacks.clear()
                self.current_memory_usage = 0
                
                # Update metrics
                self.metrics.memory_usage_bytes = 0
                self.metrics.deletes += removed_count
                logger.info(f"Cleared all {removed_count} cache entries")
                
                return removed_count
        except Exception as e:
            logger.error(f"Failed to clear all cache entries: {e}", exc_info=True)
            return 0

    @property
    def total_memory_usage_mb(self) -> float:
        """Current memory usage of the cache in megabytes (convenient shortcut)."""
        return self.current_memory_usage / (1024 * 1024)
    
    def cleanup_expired(self) -> int:
        """Cleanup expired entries"""
        try:
            with self.lock:
                current_time = datetime.now(timezone.utc)
                expired_keys = []
                
                for full_key, entry in list(self.entries.items()):
                    if entry.is_expired():
                        expired_keys.append(full_key)
                
                # Remove expired entries
                for full_key in expired_keys:
                    if full_key in self.entries:
                        entry = self.entries.pop(full_key)
                        self.current_memory_usage -= entry.size_bytes
                        self._remove_from_namespaces(entry)
                        self._expire_entry(full_key, entry)
                
                # Cleanup persistence
                if self.persistence and expired_keys:
                    self.persistence.cleanup_expired(expired_keys)
                
                if expired_keys:
                    self.metrics.expired_cleanups += len(expired_keys)
                    self.metrics.memory_usage_bytes = self.current_memory_usage
                    logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
                
                return len(expired_keys)
                
        except Exception as e:
            logger.error(f"Cache cleanup failed: {e}", exc_info=True)
            return 0
    
    def _create_full_key(self, key: str, namespace: CacheNamespace, session_id: Optional[str]) -> str:
        """Create full cache key with namespace and session"""
        parts = [namespace.value, key]
        if session_id:
            parts.insert(1, f"session:{session_id}")
        return ":".join(parts)
    
    def _make_space_if_needed(self, required_bytes: int) -> bool:
        """Make space for new entry by evicting if necessary"""
        # Check entry count limit
        if len(self.entries) >= self.max_entries:
            if not self._evict_by_priority(1):
                return False
        
        # Check memory limit
        if self.current_memory_usage + required_bytes > self.max_memory_bytes:
            bytes_to_free = (self.current_memory_usage + required_bytes) - self.max_memory_bytes
            bytes_freed = 0
            
            # Evict by priority score until we have enough space
            candidates = self._get_eviction_candidates()
            
            for full_key, entry in candidates:
                if entry.priority == CachePriority.CRITICAL:
                    continue
                
                self.entries.pop(full_key)
                self.current_memory_usage -= entry.size_bytes
                self._remove_from_namespaces(entry)
                bytes_freed += entry.size_bytes
                
                # Delete from persistence
                if self.persistence:
                    self.persistence.delete_entry(full_key)
                
                # Remove expire callback
                self._expire_callbacks.pop(full_key, None)
                
                self.metrics.record_eviction(entry.namespace)
                
                if bytes_freed >= bytes_to_free:
                    break
            
            return bytes_freed >= bytes_to_free
        
        return True
    
    def _get_eviction_candidates(self) -> List[Tuple[str, CacheEntry]]:
        """Get entries sorted by eviction priority (lowest score first)"""
        candidates = [
            (full_key, entry) for full_key, entry in self.entries.items()
            if entry.priority != CachePriority.CRITICAL
        ]
        
        # Sort by priority score (ascending - lowest score evicted first)
        candidates.sort(key=lambda x: x[1].calculate_priority_score())
        
        return candidates
    
    def _evict_by_priority(self, count: int) -> bool:
        """Evict specified number of entries by priority"""
        candidates = self._get_eviction_candidates()
        
        evicted = 0
        for full_key, entry in candidates[:count]:
            self.entries.pop(full_key)
            self.current_memory_usage -= entry.size_bytes
            self._remove_from_namespaces(entry)
            
            # Delete from persistence
            if self.persistence:
                self.persistence.delete_entry(full_key)
            
            # Remove expire callback
            self._expire_callbacks.pop(full_key, None)
            
            self.metrics.record_eviction(entry.namespace)
            evicted += 1
        
        return evicted == count
    
    def _enforce_size_limits(self):
        """Enforce cache size limits"""
        with self.lock:
            # Enforce entry count limit
            if len(self.entries) > self.max_entries:
                excess = len(self.entries) - self.max_entries
                self._evict_by_priority(excess)
            
            # Enforce memory limit
            if self.current_memory_usage > self.max_memory_bytes:
                bytes_to_free = self.current_memory_usage - self.max_memory_bytes
                candidates = self._get_eviction_candidates()
                
                bytes_freed = 0
                for full_key, entry in candidates:
                    if entry.priority == CachePriority.CRITICAL:
                        continue
                    
                    self.entries.pop(full_key)
                    self.current_memory_usage -= entry.size_bytes
                    self._remove_from_namespaces(entry)
                    bytes_freed += entry.size_bytes
                    
                    if self.persistence:
                        self.persistence.delete_entry(full_key)
                    
                    self._expire_callbacks.pop(full_key, None)
                    self.metrics.record_eviction(entry.namespace)
                    
                    if bytes_freed >= bytes_to_free:
                        break
    
    def _remove_from_namespaces(self, entry: CacheEntry):
        """Remove entry from namespace tracking"""
        self.namespace_keys[entry.namespace].discard(entry.key)
        
        # Remove from session tracking
        for session_keys in self.session_keys.values():
            session_keys.discard(entry.key)
    
    def _expire_entry(self, full_key: str, entry: CacheEntry):
        """Handle entry expiration with callback"""
        try:
            # Call expire callback if it exists
            if full_key in self._expire_callbacks:
                callback_ref = self._expire_callbacks.pop(full_key)
                callback = callback_ref()
                if callback:
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"Expire callback failed for {full_key}: {e}")
        except Exception as e:
            logger.error(f"Error handling entry expiration for {full_key}: {e}")
    
    def _load_from_persistence(self):
        """Load cache entries from persistence on startup"""
        if not self.persistence:
            return
        
        try:
            metadata_entries = self.persistence.load_all_metadata()
            loaded_count = 0
            
            for metadata in metadata_entries:
                try:
                    full_key = metadata['key']
                    entry = self.persistence.load_entry(full_key)
                    
                    if entry and not entry.is_expired():
                        self.entries[full_key] = entry
                        self.current_memory_usage += entry.size_bytes
                        self.namespace_keys[entry.namespace].add(full_key)
                        
                        # Extract session ID from key for session tracking
                        parts = full_key.split(':')
                        if len(parts) > 2 and parts[1].startswith('session:'):
                            session_id = parts[1][8:]  # Remove 'session:' prefix
                            self.session_keys[session_id].add(full_key)
                        
                        loaded_count += 1
                    
                except Exception as e:
                        logger.error(f"Failed to load cache entry {metadata.get('key', 'unknown')}: {e}")
            
            logger.info(f"Loaded {loaded_count} cache entries from persistence")
            
        except Exception as e:
            logger.error(f"Failed to load cache from persistence: {e}")
    
    def _update_memory_metrics(self):
        """Update memory-related metrics"""
        self.metrics.memory_usage_bytes = self.current_memory_usage
        if self.persistence:
            self.metrics.disk_usage_bytes = self.persistence.get_disk_usage()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics"""
        with self.lock:
            namespace_stats = {}
            for namespace, keys in self.namespace_keys.items():
                entries = [self.entries[key] for key in keys if key in self.entries]
                namespace_stats[namespace.value] = {
                    'entry_count': len(entries),
                    'memory_usage_mb': sum(e.size_bytes for e in entries) / (1024 * 1024),
                    'avg_access_count': sum(e.access_count for e in entries) / len(entries) if entries else 0,
                    'hit_count': sum(e.hit_count for e in entries)
                }
            
            return {
                'total_entries': len(self.entries),
                'memory_usage_mb': self.current_memory_usage / (1024 * 1024),
                'memory_limit_mb': self.max_memory_mb,
                'memory_utilization': self.current_memory_usage / self.max_memory_bytes if self.max_memory_bytes > 0 else 0,
                'entry_limit': self.max_entries,
                'entry_utilization': len(self.entries) / self.max_entries if self.max_entries > 0 else 0,
                'active_sessions': len(self.session_keys),
                'namespace_stats': namespace_stats,
                'metrics': self.metrics.get_summary()
            }
    
    def save_to_disk(self) -> bool:
        """Manually save all entries to disk"""
        if not self.persistence:
            logger.warning("Persistence not enabled")
            return False
        
        try:
            with self.lock:
                saved_count = 0
                for entry in self.entries.values():
                    if self.persistence.save_entry(entry):
                        saved_count += 1
                
                logger.info(f"Saved {saved_count} cache entries to disk")
                return True
                
        except Exception as e:
            logger.error(f"Failed to save cache to disk: {e}")
            return False
    
    def shutdown(self):
        """Graceful shutdown with persistence"""
        logger.info("Shutting down SOMNUS Cache...")
        
        # Stop background cleanup
        self.stop_background_cleanup()
        
        # Shutdown async manager if it exists
        if self.async_loop_manager:
            self.async_loop_manager.shutdown()

        # Save to disk if persistence enabled
        if self.persistence_enabled:
            self.save_to_disk()
        
        # Clear in-memory data
        with self.lock:
            self.entries.clear()
            self.namespace_keys.clear()
            self.session_keys.clear()
            self._expire_callbacks.clear()
            self.current_memory_usage = 0
        
        logger.info("SOMNUS Cache shutdown complete")


# High-level API decorators and utilities

def cache_result(cache: SomnusCache, 
                key_func: Optional[Callable] = None,
                namespace: CacheNamespace = CacheNamespace.GLOBAL,
                ttl_seconds: Optional[int] = None,
                priority: CachePriority = CachePriority.MEDIUM):
    """
    Decorator to cache function results.
    
    Args:
        cache: SomnusCache instance
        key_func: Function to generate cache key from args/kwargs
        namespace: Cache namespace
        ttl_seconds: Time to live
        priority: Cache priority
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Default key generation
                key_parts = [func.__name__]
                if args:
                    key_parts.append(hashlib.sha256(str(args).encode()).hexdigest()[:8])
                if kwargs:
                    key_parts.append(hashlib.sha256(str(sorted(kwargs.items())).encode()).hexdigest()[:8])
                cache_key = ":".join(key_parts)
            
            # Try to get from cache
            result = cache.get(cache_key, namespace)
            if result is not None:
                return result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, namespace, priority, ttl_seconds)
            
            return result
        
        return wrapper
    return decorator


# Factory function for easy setup
def create_somnus_cache(config: Optional[Dict[str, Any]] = None, memory_manager: Optional[MemoryManager] = None) -> SomnusCache:
    """
    Create SOMNUS cache with production defaults.
    
    Args:
        config: Optional configuration dictionary
        memory_manager: Optional MemoryManager instance for integration
    
    Returns:
        Configured SomnusCache instance
    """
    default_config = {
        'max_entries': 10000,
        'max_memory_mb': 512,
        'cache_dir': 'data/runtime_cache',
        'persistence_enabled': True,
        'cleanup_interval_seconds': 300,
        'compression_threshold_bytes': 4096
    }
    
    if config:
        default_config.update(config)
    
    cache = SomnusCache(default_config, memory_manager=memory_manager)
    cache.start_background_cleanup()
    
    return cache


# Session-aware cache context manager
class SessionCacheContext:
    """Context manager for session-scoped caching"""
    
    def __init__(self, cache: SomnusCache, session_id: str):
        self.cache = cache
        self.session_id = session_id
        self.namespace = CacheNamespace.SESSION
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Optionally clear session cache on exit
        pass
    
    def set(self, key: str, value: Any, **kwargs):
        """Set value in session cache"""
        return self.cache.set(key, value, namespace=self.namespace, session_id=self.session_id, **kwargs)
    
    def get(self, key: str, default: Any = None):
        """Get value from session cache"""
        return self.cache.get(key, namespace=self.namespace, session_id=self.session_id, default=default)
    
    def delete(self, key: str):
        """Delete value from session cache"""
        return self.cache.delete(key, namespace=self.namespace, session_id=self.session_id)
    
    def clear(self):
        """Clear all session cache entries"""
        return self.cache.clear_session(self.session_id)


# Example usage and testing
if __name__ == "__main__":
    # Create cache with custom config
    cache_config = {
        'max_entries': 1000,
        'max_memory_mb': 128,
        'cache_dir': 'test_cache',
        'cleanup_interval_seconds': 30
    }
    
    cache = create_somnus_cache(cache_config)
    
    try:
        # Basic usage
        cache.set("test_key", {"data": "test_value"}, namespace=CacheNamespace.GLOBAL, ttl_seconds=60)
        result = cache.get("test_key", namespace=CacheNamespace.GLOBAL)
        print(f"Cache result: {result}")
        
        # Session-scoped usage
        with SessionCacheContext(cache, "session_123") as session_cache:
            session_cache.set("user_data", {"user_id": 123, "name": "Test User"})
            user_data = session_cache.get("user_data")
            print(f"Session data: {user_data}")
        
        # Namespace usage
        cache.set("model_result", {"tokens": 150, "time": 2.5}, 
                 namespace=CacheNamespace.MODEL, priority=CachePriority.HIGH)
        
        # Stats
        stats = cache.get_stats()
        print(f"Cache stats: {json.dumps(stats, indent=2)}")
        
        # Decorator usage
        @cache_result(cache, namespace=CacheNamespace.ARTIFACT, ttl_seconds=300)
        def expensive_computation(x, y):
            time.sleep(1)  # Simulate expensive work
            return x * y + 42
        
        # First call - computed
        result1 = expensive_computation(5, 10)
        print(f"First call result: {result1}")
        
        # Second call - cached
        result2 = expensive_computation(5, 10)
        print(f"Second call result: {result2}")
        
    finally:
        cache.shutdown()
        # Clean up the singleton async manager if it was created
        if _AsyncIOLoopManager._instance:
            _AsyncIOLoopManager._instance.shutdown()
