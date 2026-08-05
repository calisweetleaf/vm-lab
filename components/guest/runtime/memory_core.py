"""
Somnus Systems - Persistent Memory Management System for Virtual Machines
Advanced cross-session memory with semantic indexing, encryption, and privacy controls.

Memory Architecture:
- Semantic vector storage with embedding-based retrieval
- Multi-modal memory support (text, code, files, images)
- User-scoped encryption with granular privacy controls
- Importance-based retention and forgetting mechanisms
- Cross-session context reconstruction and continuity
"""

import asyncio
import logging
import pickle
import sqlite3
import hashlib
import json
import time
import yaml
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set, Union, TypeAlias
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4
from contextlib import asynccontextmanager

import numpy as np
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import os
from sklearn.cluster import KMeans
import httpx

from pydantic import BaseModel, Field, validator
import aiosqlite

UserID: TypeAlias = str
SessionID: TypeAlias = str

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Memory classification for retrieval and retention strategies"""
    CORE_FACT = "core_fact"           # Persistent user facts (name, preferences)
    CONVERSATION = "conversation"      # Chat exchanges and context
    DOCUMENT = "document"             # Uploaded files and analysis
    CODE_SNIPPET = "code_snippet"    # Generated/executed code
    TOOL_RESULT = "tool_result"       # Plugin/tool outputs
    USER_DIRECTIVE = "user_directive"  # User-defined behaviors
    SYSTEM_EVENT = "system_event"     # Technical events and errors
    CUSTOM_INSTRUCTION = "custom_instruction"  # Custom system/user instructions
    INTERACTION = "interaction"       # General interaction events
    DEV_SESSION = "dev_session"       # Development session objects


class MemoryImportance(str, Enum):
    """Importance levels for retention and retrieval prioritization"""
    CRITICAL = "critical"    # Never forget (user identity, core preferences)
    HIGH = "high"           # Long-term retention (important facts, insights)
    MEDIUM = "medium"       # Medium-term retention (useful context)
    LOW = "low"            # Short-term retention (ephemeral interactions)
    TEMPORARY = "temporary" # Session-only (debugging, system messages)


class MemoryScope(str, Enum):
    """Memory access scope and sharing permissions"""
    PRIVATE = "private"     # User-only access
    SHARED = "shared"       # Shareable with other users (if permitted)
    SYSTEM = "system"       # System-wide (anonymized analytics)
    ARCHIVED = "archived"   # Compressed long-term storage


@dataclass
class MemoryVector:
    """Semantic vector representation with metadata"""
    embedding: np.ndarray
    content: str
    memory_type: MemoryType
    importance: MemoryImportance
    scope: MemoryScope
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    source_session: Optional[SessionID] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SimpleLocalCollection:
    """A very small in-memory collection mimic for Chroma-like API used as fallback.

    NOTE: This is intentionally minimal and only implements the subset of methods
    used by MemoryManager: add, query, get, delete. It's not optimized for large
    datasets but allows the system to operate without Chroma during development.
    """

    def __init__(self, name: str, persist_path: Optional[Path] = None):
        self.name = name
        self._items: Dict[str, Dict[str, Any]] = {}
        self._persist_path = persist_path

    def add(self, embeddings, documents, metadatas, ids):
        for emb, doc, meta, _id in zip(embeddings, documents, metadatas, ids):
            self._items[_id] = {
                'embedding': np.array(emb, dtype=float),
                'document': doc,
                'metadata': meta
            }

    def query(self, query_embeddings, n_results=5, where=None, include=None):
        # Single query vector supported
        q = np.array(query_embeddings[0], dtype=float)
        ids = []
        documents = []
        metadatas = []
        distances = []

        for _id, item in self._items.items():
            emb = item['embedding']
            # cosine distance
            if np.linalg.norm(emb) == 0 or np.linalg.norm(q) == 0:
                dist = 1.0
            else:
                cos = np.dot(emb, q) / (np.linalg.norm(emb) * np.linalg.norm(q))
                dist = 1.0 - float(np.clip(cos, -1.0, 1.0))

            ids.append(_id)
            documents.append(item['document'])
            metadatas.append(item['metadata'])
            distances.append(dist)

        # sort by distance (lower is more similar)
        order = np.argsort(distances)[:n_results]
        return {
            'ids': [[ids[i] for i in order]],
            'documents': [[documents[i] for i in order]],
            'metadatas': [[metadatas[i] for i in order]],
            'distances': [[distances[i] for i in order]]
        }

    def get(self, where=None, include=None):
        ids = list(self._items.keys())
        documents = [v['document'] for v in self._items.values()]
        metadatas = [v['metadata'] for v in self._items.values()]
        return {'ids': ids, 'documents': documents, 'metadatas': metadatas}

    def delete(self, ids):
        for _id in ids:
            if _id in self._items:
                del self._items[_id]


class SimpleLocalVectorDB:
    """Minimal vector DB client exposing get_or_create_collection and get_collection
    to mimic the methods used by MemoryManager. Collections live in memory and can
    optionally persist as a JSON file for modest durability.
    """

    def __init__(self, persist_directory: Optional[str] = None):
        self._collections: Dict[str, SimpleLocalCollection] = {}
        self._persist_directory = Path(persist_directory) if persist_directory else None
        if self._persist_directory:
            self._persist_directory.mkdir(parents=True, exist_ok=True)
            # Attempt to load persisted collections to make the in-process fallback
            # useful across separate runs (seed -> retrieve in different processes).
            try:
                for p in self._persist_directory.glob('*.json'):
                    try:
                        with open(p, 'r', encoding='utf-8') as fh:
                            data = json.load(fh)
                        name = p.stem
                        coll = SimpleLocalCollection(name, p)
                        items = data.get('items', {})
                        for _id, v in items.items():
                            try:
                                emb = np.array(v.get('embedding', []), dtype=float)
                            except Exception:
                                emb = np.array([], dtype=float)
                            coll._items[_id] = {
                                'embedding': emb,
                                'document': v.get('document', ''),
                                'metadata': v.get('metadata', {})
                            }
                        self._collections[name] = coll
                    except Exception:
                        logger.debug("Failed loading persisted collection %s", p, exc_info=True)
            except Exception:
                logger.debug("Error scanning persist directory for SimpleLocalVectorDB", exc_info=True)

    def get_or_create_collection(self, name: str, metadata: Optional[Dict[str, Any]] = None):
        if name not in self._collections:
            p = (self._persist_directory / f"{name}.json") if self._persist_directory else None
            self._collections[name] = SimpleLocalCollection(name, p)
        return self._collections[name]

    def get_collection(self, name: str):
        if name in self._collections:
            return self._collections[name]
        raise KeyError(f"Collection {name} not found in SimpleLocalVectorDB")

    def list_collections(self):
        return list(self._collections.keys())

    def persist(self):
        # Optional simple persistence: write each collection to a JSON file with minimal data
        if not self._persist_directory:
            return
        for name, coll in self._collections.items():
            out = {
                'items': {
                    _id: {
                        'document': v['document'],
                        'metadata': v['metadata'],
                        'embedding': v['embedding'].tolist()
                    } for _id, v in coll._items.items()
                }
            }
            path = self._persist_directory / f"{name}.json"
            try:
                with open(path, 'w', encoding='utf-8') as fh:
                    json.dump(out, fh)
            except Exception:
                logger.debug("Failed to persist SimpleLocalVectorDB collection %s", name, exc_info=True)


class MemoryEntry(BaseModel):
    """Structured memory entry with full metadata"""
    memory_id: UUID = Field(default_factory=uuid4)
    user_id: UserID = Field(description="Owner of this memory")
    content: str = Field(description="Memory content (may be encrypted)")
    content_hash: str = Field(description="SHA-256 hash for deduplication")
    memory_type: MemoryType = Field(description="Memory classification")
    importance: MemoryImportance = Field(description="Retention priority")
    scope: MemoryScope = Field(description="Access permissions")
    
    # Temporal metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(None, description="Optional expiration")
    
    # Usage tracking
    access_count: int = Field(default=0, ge=0)
    relevance_score: float = Field(default=1.0, ge=0, le=1.0)
    
    # Source tracking
    source_session: Optional[SessionID] = Field(None)
    source_type: str = Field(default="user_input")
    
    # Relationships
    related_memories: List[UUID] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    
    # Privacy and encryption
    is_encrypted: bool = Field(default=True)
    encryption_key_id: Optional[str] = Field(None)
    
    # Vector storage reference
    vector_id: Optional[str] = Field(None, description="ChromaDB vector ID")
    
    def update_access(self):
        """Update access tracking"""
        self.last_accessed = datetime.now(timezone.utc)
        self.access_count += 1
    
    def calculate_retention_score(self) -> float:
        """Calculate dynamic retention score based on usage and importance"""
        age_days = (datetime.now(timezone.utc) - self.created_at).days
        recency_factor = max(0.1, 1.0 - (age_days / 365))  # Decay over a year
        
        importance_weights = {
            MemoryImportance.CRITICAL: 1.0,
            MemoryImportance.HIGH: 0.8,
            MemoryImportance.MEDIUM: 0.6,
            MemoryImportance.LOW: 0.4,
            MemoryImportance.TEMPORARY: 0.1
        }
        
        access_factor = min(1.0, self.access_count / 10)  # Normalize access frequency
        importance_factor = importance_weights[self.importance]
        
        return (recency_factor * 0.3 + access_factor * 0.3 + importance_factor * 0.4)
    
    @property
    def should_retain(self) -> bool:
        """Determine if memory should be retained based on score and policies"""
        if self.importance == MemoryImportance.CRITICAL:
            return True
        if self.importance == MemoryImportance.TEMPORARY:
            return False
        
        return self.calculate_retention_score() > 0.3


class MemoryConfiguration(BaseModel):
    """Comprehensive memory system configuration"""
    # Storage configuration
    vector_db_path: str = Field(default="data/memory/vectors")
    metadata_db_path: str = Field(default="data/memory/metadata.db")
    max_memories_per_user: int = Field(default=50000, ge=1000)
    max_storage_per_user_gb: float = Field(default=1.0, ge=0.1)
    
    # Embedding configuration
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    embedding_dimension: int = Field(default=384, ge=128)
    batch_size: int = Field(default=32, ge=1, le=256)
    
    # Retention policies
    retention_policies: Dict[MemoryImportance, int] = Field(
        default_factory=lambda: {
            MemoryImportance.CRITICAL: -1,      # Never expire
            MemoryImportance.HIGH: 365,         # 1 year
            MemoryImportance.MEDIUM: 90,        # 3 months
            MemoryImportance.LOW: 30,           # 1 month
            MemoryImportance.TEMPORARY: 1       # 1 day
        }
    )
    
    # Privacy and encryption
    encryption_enabled: bool = Field(default=True)
    user_data_isolation: bool = Field(default=True)
    kms_enabled: bool = Field(default=False, description="Enable Key Management Service integration")
    kms_type: Optional[str] = Field(default=None, description="Type of KMS (e.g., 'vault', 'aws_kms')")
    kms_address: Optional[str] = Field(default=None, description="KMS server address")
    kms_token: Optional[str] = Field(default=None, description="KMS authentication token (for Vault)")
    kms_secret_path: Optional[str] = Field(default=None, description="Path to secret in KMS")
    
    # Performance tuning
    similarity_threshold: float = Field(default=0.7, ge=0.1, le=1.0)
    max_retrieval_results: int = Field(default=20, ge=1, le=100)
    background_cleanup_interval: int = Field(default=3600, ge=300)
    
    # Advanced features
    semantic_clustering: bool = Field(default=True)
    cross_modal_indexing: bool = Field(default=True)
    importance_learning: bool = Field(default=True)


class EncryptionError(RuntimeError):
    """Domain‑specific error for encryption/decryption failures."""
    pass


class MemoryEncryption:
    """
    Production‑grade encryption for per‑user memory.

    Guarantees:
    - Deterministic key derivation per user (no hard‑coded secrets).
    - Thread‑safe access to internal key cache.
    - Full input validation and explicit error handling.
    - Secure logging (no secret material is ever logged).
    - Optional master‑key rotation with automatic re‑derivation of user keys.
    """

    def __init__(self, master_key: Optional[bytes] = None) -> None:
        """
        Initialise the encryption helper.

        Args:
            master_key: 32‑byte base key. If ``None``, a fresh key is generated.
                        The key must be stored securely by the caller (e.g. env var,
                        secret manager).  No secrets are persisted to disk by this class.
        """
        # Determine master key in the following order of precedence:
        # 1) explicit master_key parameter (must be 32 bytes)
        # 2) SOMNUS_MASTER_KEY environment variable (hex or urlsafe-base64 representing 32 bytes)
        # 3) generate a fresh random key
        if master_key is not None:
            if not isinstance(master_key, (bytes, bytearray)):
                raise TypeError("master_key must be bytes")
            if len(master_key) != 32:
                raise ValueError("master_key must be exactly 32 bytes for internal derivation")
            self._master_key = master_key
        else:
            env_key = os.environ.get('SOMNUS_MASTER_KEY')
            if env_key:
                candidate = None
                # Try hex
                try:
                    candidate = bytes.fromhex(env_key)
                except Exception:
                    candidate = None

                # Try urlsafe-base64
                if not candidate:
                    try:
                        candidate = base64.urlsafe_b64decode(env_key)
                    except Exception:
                        candidate = None

                if candidate and len(candidate) == 32:
                    self._master_key = candidate
                else:
                    logging.getLogger(__name__).warning(
                        "SOMNUS_MASTER_KEY provided but invalid; falling back to generated master key"
                    )
                    self._master_key = Fernet.generate_key()
            else:
                self._master_key = Fernet.generate_key()

        self._user_keys: Dict[UserID, Fernet] = {}
        self._lock = asyncio.Lock()   # async‑compatible lock for concurrent access
        self._logger = logging.getLogger(__name__)

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    async def _derive_user_key(self, user_id: UserID, password: Optional[str] = None) -> Fernet:
        """
        Derive (or retrieve cached) a user‑specific Fernet instance.

        The derivation uses PBKDF2HMAC with a per‑user salt derived from the
        ``user_id``.  ``password`` is currently unused but kept for future
        extensibility (e.g. user‑provided passphrase).

        Args:
            user_id: Identifier of the user – must be a non‑empty string.
            password: Optional additional secret material (currently ignored).

        Returns:
            Fernet instance ready for encrypt/decrypt operations.
        """
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non‑empty string")

        async with self._lock:
            if user_id in self._user_keys:
                return self._user_keys[user_id]

            # Derive a deterministic per‑user key
            salt = hashlib.sha256(user_id.encode()).digest()[:16]

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100_000,
            )

            # Combine master key with user identifier for key material
            key_material = self._master_key + user_id.encode()
            derived_key = kdf.derive(key_material)
            fernet_key = base64.urlsafe_b64encode(derived_key)

            fernet = Fernet(fernet_key)
            self._user_keys[user_id] = fernet
            return fernet

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    async def encrypt_memory(self, content: str, user_id: UserID) -> Tuple[bytes, str]:
        """
        Encrypt ``content`` for ``user_id``.

        Returns a tuple ``(encrypted_bytes, key_id)`` where ``key_id`` is a
        deterministic identifier derived from the ``user_id`` (first 16 hex
        characters of its SHA‑256 hash).  ``key_id`` can be stored for lookup
        without exposing the actual key.

        Raises:
            EncryptionError: on any cryptographic failure.
            ValueError: if ``content`` is empty.
        """
        if not isinstance(content, str) or not content:
            raise ValueError("content must be a non‑empty string")

        try:
            fernet = await self._derive_user_key(user_id)
            encrypted = fernet.encrypt(content.encode())
            key_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
            return encrypted, key_id
        except Exception as exc:
            self._logger.exception(
                "Encryption failed for user_id=%s", user_id
            )
            raise EncryptionError("Failed to encrypt memory") from exc

    async def decrypt_memory(self, encrypted_content: bytes, user_id: UserID) -> str:
        """
        Decrypt ``encrypted_content`` for ``user_id``.

        Returns the original plaintext string.

        Raises:
            EncryptionError: on any cryptographic failure.
            ValueError: if ``encrypted_content`` is empty.
        """
        if not isinstance(encrypted_content, (bytes, bytearray)) or not encrypted_content:
            raise ValueError("encrypted_content must be non‑empty bytes")

        try:
            fernet = await self._derive_user_key(user_id)
            decrypted = fernet.decrypt(encrypted_content)
            return decrypted.decode()
        except Exception as exc:
            self._logger.exception(
                "Decryption failed for user_id=%s", user_id
            )
            raise EncryptionError("Failed to decrypt memory") from exc

    async def rotate_master_key(self, new_master_key: bytes) -> None:
        """
        Rotate the master key used for all user‑key derivations.

        All cached user keys are cleared; subsequent operations will lazily
        re‑derive keys using the new master key.  The caller must ensure the new
        key is stored securely before invoking this method.

        Args:
            new_master_key: 32‑byte key.

        Raises:
            ValueError: if the key length is invalid.
        """
        if not isinstance(new_master_key, (bytes, bytearray)):
            raise TypeError("new_master_key must be bytes")
        normalized_key = bytes(new_master_key)
        if len(normalized_key) != 32:
            raise ValueError("new_master_key must be exactly 32 bytes")

        async with self._lock:
            self._master_key = normalized_key
            self._user_keys.clear()
            self._logger.info("Master key rotated; user key cache cleared")


class MemoryManager:
    """
    Production-grade persistent memory management system.
    
    Implements advanced semantic indexing, encryption, and retention policies
    for cross-session user memory with privacy controls and performance optimization.
    """
    
    def __init__(self, config: Optional[MemoryConfiguration] = None):
        self.config = config or MemoryConfiguration()
        self.encryption = MemoryEncryption()
        self.embedding_model = None
        
        # Storage components
        self.vector_db = None
        self.metadata_db_path = Path(self.config.metadata_db_path)
        self.metadata_db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Performance tracking
        self.metrics = {
            'memories_stored': 0,
            'memories_retrieved': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'retention_cleanups': 0
        }
        
        # Background tasks
        self.cleanup_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        logger.info("Memory manager initialized")
    
    async def initialize(self):
        """Initialize memory system components"""
        try:
            # Initialize embedding model
            if self.embedding_model is None:
                logger.info(f"Loading embedding model: {self.config.embedding_model}")
                # Lazy import to avoid heavy deps at module import
                try:
                    from sentence_transformers import SentenceTransformer  # type: ignore
                except Exception as e:
                    logger.error(f"Failed to import sentence_transformers: {e}")
                    raise
                self.embedding_model = SentenceTransformer(self.config.embedding_model)
            
            # Initialize vector database
            await self._initialize_vector_db()
            
            # Initialize metadata database
            await self._initialize_metadata_db()
            
            # Start background tasks
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            
            logger.info("Memory system initialized successfully")
            
        except Exception as e:
            logger.error(f"Memory system initialization failed: {e}")
            raise
    
    async def _initialize_vector_db(self):
        """Initialize ChromaDB for vector storage"""
        # Lazy import to avoid heavy dependency import during module import
        try:
            import chromadb  # type: ignore
            from chromadb.config import Settings  # type: ignore
        except Exception as e:
            logger.error(f"Failed to import chromadb: {e}")
            # Fall back to in-process vector DB
            logger.warning("ChromaDB not available; using in-process SimpleLocalVectorDB fallback.")
            self.vector_db = SimpleLocalVectorDB(persist_directory=str(Path(self.config.vector_db_path)))
            return
        vector_path = Path(self.config.vector_db_path)
        vector_path.mkdir(parents=True, exist_ok=True)
        
        # Configure ChromaDB with persistence
        settings = Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=str(vector_path),
            anonymized_telemetry=False
        )
        
        try:
            self.vector_db = chromadb.Client(settings)
            logger.info(f"Vector database initialized at {vector_path}")
        except ValueError as ve:
            # Chromadb new/old client migration error; fall back to simple local store
            logger.warning("ChromaDB raised ValueError during Client creation; falling back to SimpleLocalVectorDB. Error: %s", ve)
            self.vector_db = SimpleLocalVectorDB(persist_directory=str(vector_path))
        except Exception as e:
            logger.error(f"Unexpected error initializing Chromadb client: {e}")
            # Last resort: use in-process fallback to allow the app to function
            self.vector_db = SimpleLocalVectorDB(persist_directory=str(vector_path))
    
    async def _initialize_metadata_db(self):
        """Initialize SQLite database for memory metadata"""
        async with self._get_db_connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    last_accessed TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    relevance_score REAL DEFAULT 1.0,
                    source_session TEXT,
                    source_type TEXT,
                    related_memories TEXT,  -- JSON array
                    tags TEXT,             -- JSON array
                    is_encrypted BOOLEAN DEFAULT TRUE,
                    encryption_key_id TEXT,
                    vector_id TEXT,
                    metadata TEXT          -- JSON blob for additional data
                )
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS encrypted_content (
                    memory_id TEXT PRIMARY KEY,
                    encrypted_data BLOB NOT NULL,
                    FOREIGN KEY (memory_id) REFERENCES memories (memory_id)
                )
            """)
            
            # Create indexes for performance
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON memories (user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_type ON memories (memory_type)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_importance ON memories (importance)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON memories (created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON memories (content_hash)")
            
            await conn.commit()
        
        logger.info("Metadata database initialized")
    
    @asynccontextmanager
    async def _get_db_connection(self):
        """Get async database connection with proper error handling"""
        conn = await aiosqlite.connect(self.metadata_db_path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()
    
    async def store_memory(
        self, 
        user_id: UserID, 
        content: str, 
        memory_type: MemoryType = MemoryType.CONVERSATION,
        importance: MemoryImportance = MemoryImportance.MEDIUM,
        scope: MemoryScope = MemoryScope.PRIVATE,
        source_session: Optional[SessionID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> UUID:
        """
        Store new memory with semantic indexing and encryption.
        
        Returns the memory_id for future reference.
        """
        try:
            # Create memory entry
            memory_entry = MemoryEntry(
                user_id=user_id,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                memory_type=memory_type,
                importance=importance,
                scope=scope,
                source_session=source_session,
                tags=tags or [],
            )
            
            # Check for duplicates
            existing_memory = await self._find_duplicate_memory(user_id, memory_entry.content_hash)
            if existing_memory:
                # Update existing memory instead of creating duplicate
                await self._update_memory_access(existing_memory['memory_id'])
                return UUID(existing_memory['memory_id'])
            
            # Encrypt content if enabled
            if self.config.encryption_enabled:
                encrypted_content, key_id = await self.encryption.encrypt_memory(content, user_id)
                memory_entry.is_encrypted = True
                memory_entry.encryption_key_id = key_id
            else:
                encrypted_content = content.encode()
                memory_entry.is_encrypted = False
            
            # Generate embedding
            embedding = await self._generate_embedding(content)
            
            # Store in vector database
            collection_name = f"user_{hashlib.sha256(user_id.encode()).hexdigest()[:16]}"
            collection = await self._get_or_create_collection(collection_name)
            
            vector_id = str(memory_entry.memory_id)
            try:
                collection.add(
                    embeddings=[embedding.tolist()],
                    documents=[content[:1000]],  # Truncate for ChromaDB
                    metadatas=[{
                        'memory_type': memory_type.value,
                        'importance': importance.value,
                        'created_at': memory_entry.created_at.isoformat(),
                        'tags': json.dumps(tags or [])
                    }],
                    ids=[vector_id]
                )
            except Exception as e:
                logger.error(f"Failed to add vector to ChromaDB for memory {memory_entry.memory_id}: {e}", exc_info=True)
                # If vector storage fails, we should not proceed to store metadata.
                raise
            
            memory_entry.vector_id = vector_id
            
            # Store in metadata database
            async with self._get_db_connection() as conn:
                await conn.execute("""
                    INSERT INTO memories (
                        memory_id, user_id, content_hash, memory_type, importance, scope,
                        created_at, last_accessed, access_count, source_session,
                        source_type, related_memories, tags, is_encrypted,
                        encryption_key_id, vector_id, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(memory_entry.memory_id), user_id, memory_entry.content_hash,
                    memory_type.value, importance.value, scope.value,
                    memory_entry.created_at.isoformat(), memory_entry.last_accessed.isoformat(),
                    memory_entry.access_count, str(source_session) if source_session else None,
                    memory_entry.source_type, json.dumps([str(uuid) for uuid in memory_entry.related_memories]),
                    json.dumps(memory_entry.tags), memory_entry.is_encrypted,
                    memory_entry.encryption_key_id, vector_id,
                    json.dumps(metadata or {})
                ))
                
                # Store encrypted content separately
                await conn.execute("""
                    INSERT INTO encrypted_content (memory_id, encrypted_data)
                    VALUES (?, ?)
                """, (str(memory_entry.memory_id), encrypted_content))
                
                await conn.commit()
            
            self.metrics['memories_stored'] += 1
            logger.info(f"Stored memory {memory_entry.memory_id} for user {user_id}")
            
            return memory_entry.memory_id
            
        except Exception as e:
            logger.error(f"Failed to store memory for user {user_id}: {e}", exc_info=True)
            raise
    
    async def retrieve_memories(
        self,
        user_id: UserID,
        query: Optional[str] = None,
        memory_types: Optional[List[MemoryType]] = None,
        importance_threshold: MemoryImportance = MemoryImportance.LOW,
        limit: int = 20,
        include_content: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories using semantic search and filtering.
        
        Returns list of memory dictionaries with content and metadata.
        """
        try:
            # Get user's vector collection
            collection_name = f"user_{hashlib.sha256(user_id.encode()).hexdigest()[:16]}"
            collection = await self._get_or_create_collection(collection_name)
            
            # Build metadata filters
            where_conditions = []
            if memory_types:
                where_conditions.append({
                    'memory_type': {'$in': [mt.value for mt in memory_types]}
                })
            
            importance_order = [
                MemoryImportance.CRITICAL, MemoryImportance.HIGH, 
                MemoryImportance.MEDIUM, MemoryImportance.LOW, MemoryImportance.TEMPORARY
            ]
            valid_importance = importance_order[:importance_order.index(importance_threshold) + 1]
            where_conditions.append({
                'importance': {'$in': [imp.value for imp in valid_importance]}
            })
            
            where_filter = {'$and': where_conditions} if len(where_conditions) > 1 else where_conditions[0] if where_conditions else None

            # Perform semantic search if query provided
            if query:
                query_embedding = await self._generate_embedding(query)
                try:
                    results = collection.query(
                        query_embeddings=[query_embedding.tolist()],
                        n_results=min(limit, self.config.max_retrieval_results),
                        where=where_filter,
                        include=['documents', 'metadatas', 'distances']
                    )
                except Exception as e:
                    logger.error(f"ChromaDB query failed for user {user_id}: {e}", exc_info=True)
                    return []
                
                memory_ids = results['ids'][0] if results and results['ids'] else []
                similarities = [1 - d for d in results['distances'][0]] if results and results['distances'] else []
            else:
                try:
                    # Get all memories for user with filters
                    all_results = collection.get(
                        where=where_filter,
                        include=['documents', 'metadatas']
                    )
                except Exception as e:
                    logger.error(f"ChromaDB get failed for user {user_id}: {e}", exc_info=True)
                    return []
                memory_ids = all_results['ids'][:limit] if all_results and all_results['ids'] else []
                similarities = [1.0] * len(memory_ids)  # No semantic ranking
            
            # Retrieve full memory data from metadata database
            if not memory_ids:
                # Fallback: if vector DB had no results (e.g., in-memory collections
                # not shared between processes), query the metadata DB directly to
                # return recent memories matching the filters.
                try:
                    async with self._get_db_connection() as conn:
                        where_clauses = ["user_id = ?"]
                        params = [user_id]

                        if memory_types:
                            placeholders = ",".join(["?" for _ in memory_types])
                            where_clauses.append(f"memory_type IN ({placeholders})")
                            params.extend([mt.value if isinstance(mt, MemoryType) else mt for mt in memory_types])

                        if where_filter is not None and isinstance(where_filter, dict):
                            # If importance filter exists in where_filter, handle it simply
                            # by adding an importance IN (...) clause when present.
                            imp_vals = None
                            for cond in where_conditions:
                                if 'importance' in cond:
                                    imp_vals = cond['importance'].get('$in')
                                    break
                            if imp_vals:
                                placeholders = ",".join(["?" for _ in imp_vals])
                                where_clauses.append(f"importance IN ({placeholders})")
                                params.extend(imp_vals)

                        q = "SELECT memory_id FROM memories WHERE " + " AND ".join(where_clauses) + " ORDER BY created_at DESC LIMIT ?"
                        params.append(limit)
                        cursor = await conn.execute(q, tuple(params))
                        rows = await cursor.fetchall()
                        memory_ids = [r['memory_id'] for r in rows]
                        # If we obtained ids from metadata fallback, set default similarities
                        similarities = [1.0] * len(memory_ids)
                except Exception as e:
                    logger.error(f"Fallback metadata DB query failed: {e}")
                    return []

            memories = []
            for memory_id, similarity in zip(memory_ids, similarities):
                memory_data = await self._get_memory_by_id(memory_id, user_id, include_content)
                if memory_data:
                    memory_data['similarity_score'] = similarity
                    memories.append(memory_data)
                    await self._update_memory_access(memory_id)
            
            # Sort by importance and similarity
            memories.sort(key=lambda m: (
                m.get('importance_rank', 5),  # CRITICAL=0, TEMPORARY=4
                -m.get('similarity_score', 0)
            ))
            
            self.metrics['memories_retrieved'] += len(memories)
            logger.info(f"Retrieved {len(memories)} memories for user {user_id}")
            
            return memories
            
        except Exception as e:
            logger.error(f"Failed to retrieve memories for user {user_id}: {e}", exc_info=True)
            return []
    
    async def _generate_embedding(self, text: str) -> np.ndarray:
        """Generate semantic embedding for text"""
        if not self.embedding_model:
            raise RuntimeError("Embedding model not initialized")
        
        # Run embedding generation in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None, self.embedding_model.encode, text
        )
        return embedding
    
    async def _get_or_create_collection(self, collection_name: str):
        """Get or create ChromaDB collection for user"""
        try:
            # Use get_or_create for atomicity
            collection = self.vector_db.get_or_create_collection(
                name=collection_name,
                metadata={"description": f"Memory vectors for user collection {collection_name}"}
            )
            return collection
        except Exception as e:
            logger.error(f"Failed to get or create ChromaDB collection {collection_name}: {e}", exc_info=True)
            raise
    
    async def _find_duplicate_memory(self, user_id: UserID, content_hash: str) -> Optional[Dict[str, Any]]:
        """Find existing memory with same content hash"""
        async with self._get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT memory_id, created_at FROM memories
                WHERE user_id = ? AND content_hash = ?
                ORDER BY created_at DESC LIMIT 1
            """, (user_id, content_hash))
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def _get_memory_by_id(
        self, 
        memory_id: str, 
        user_id: UserID, 
        include_content: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Retrieve memory by ID with optional content decryption"""
        async with self._get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT m.*, ec.encrypted_data
                FROM memories m
                LEFT JOIN encrypted_content ec ON m.memory_id = ec.memory_id
                WHERE m.memory_id = ? AND m.user_id = ?
            """, (memory_id, user_id))
            row = await cursor.fetchone()
            
            if not row:
                return None
            
            memory_data = dict(row)
            
            # Decrypt content if needed and requested
            if include_content and memory_data['encrypted_data']:
                if memory_data['is_encrypted']:
                    try:
                        decrypted_content = await self.encryption.decrypt_memory(
                            memory_data['encrypted_data'], user_id
                        )
                        memory_data['content'] = decrypted_content
                    except Exception as e:
                        logger.error(f"Failed to decrypt memory {memory_id}: {e}")
                        memory_data['content'] = "[DECRYPTION_FAILED]"
                else:
                    memory_data['content'] = memory_data['encrypted_data'].decode()
            
            # Parse JSON fields
            memory_data['related_memories'] = [UUID(uuid_str) for uuid_str in json.loads(memory_data.get('related_memories', '[]'))]
            memory_data['tags'] = json.loads(memory_data.get('tags', '[]'))
            memory_data['metadata'] = json.loads(memory_data.get('metadata', '{}'))
            
            # Add importance ranking for sorting
            importance_ranks = {
                MemoryImportance.CRITICAL.value: 0,
                MemoryImportance.HIGH.value: 1,
                MemoryImportance.MEDIUM.value: 2,
                MemoryImportance.LOW.value: 3,
                MemoryImportance.TEMPORARY.value: 4
            }
            memory_data['importance_rank'] = importance_ranks.get(memory_data['importance'], 5)
            
            return memory_data

    async def get_memory_by_id(self, memory_id: str, user_id: UserID, include_content: bool = True) -> Optional[Dict[str, Any]]:
        """
        Public method to retrieve memory by ID with optional content decryption.
        
        Args:
            memory_id: The ID of the memory to retrieve
            user_id: The user ID associated with the memory
            include_content: Whether to include the decrypted content
            
        Returns:
            Dictionary containing memory data or None if not found
        """
        return await self._get_memory_by_id(memory_id, user_id, include_content)
    
    async def _update_memory_access(self, memory_id: str):
        """Update memory access tracking"""
        async with self._get_db_connection() as conn:
            await conn.execute("""
                UPDATE memories
                SET last_accessed = ?, access_count = access_count + 1
                WHERE memory_id = ?
            """, (datetime.now(timezone.utc).isoformat(), memory_id))
            await conn.commit()
    
    async def _cleanup_loop(self):
        """Background cleanup task for expired and low-importance memories"""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.config.background_cleanup_interval)
                
                logger.info("Starting memory cleanup cycle.")
                # Find memories to cleanup based on retention policies
                await self._cleanup_expired_memories()
                await self._cleanup_low_relevance_memories()
                
                self.metrics['retention_cleanups'] += 1
                logger.info("Memory cleanup cycle completed.")
                
            except asyncio.CancelledError:
                logger.info("Memory cleanup loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in memory cleanup loop: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait before retrying on unexpected error
    
    async def _cleanup_expired_memories(self):
        """Remove expired memories based on retention policies"""
        current_time = datetime.now(timezone.utc)
        
        async with self._get_db_connection() as conn:
            # Find expired memories based on expires_at or retention policies for TEMPORARY
            # This query is simplified to be more robust.
            cursor = await conn.execute("""
                SELECT memory_id, user_id, vector_id FROM memories
                WHERE (expires_at IS NOT NULL AND expires_at < ?)
                   OR (importance = ? AND created_at < ?)
            """, (
                current_time.isoformat(),
                MemoryImportance.TEMPORARY.value,
                (current_time - timedelta(days=self.config.retention_policies.get(MemoryImportance.TEMPORARY, 1))).isoformat()
            ))
            
            expired_memories = await cursor.fetchall()
            if not expired_memories:
                return

            logger.info(f"Found {len(expired_memories)} expired memories to remove.")
            for memory in expired_memories:
                await self._delete_memory(memory['memory_id'], memory['user_id'], memory['vector_id'])
    
    async def _cleanup_low_relevance_memories(self):
        """Remove low-relevance memories when approaching storage limits."""
        user_ids = set()
        async with self._get_db_connection() as conn:
            cursor = await conn.execute("SELECT DISTINCT user_id FROM memories")
            rows = await cursor.fetchall()
            for row in rows:
                user_ids.add(row['user_id'])

        for user_id in user_ids:
            try:
                current_usage_bytes = await self._calculate_user_storage_usage(user_id)
                max_usage_bytes = self.config.max_storage_per_user_gb * (1024**3)

                if current_usage_bytes > max_usage_bytes:
                    logger.info(f"User {user_id} is over storage limit. Current: {current_usage_bytes / (1024**3):.2f} GB, Max: {self.config.max_storage_per_user_gb:.2f} GB. Initiating cleanup.")
                    
                    memories_to_consider = []
                    async with self._get_db_connection() as conn:
                        # Fetch all non-critical memories for the user to evaluate for cleanup
                        cursor = await conn.execute("""
                            SELECT m.memory_id, m.user_id, m.importance, m.memory_type, m.created_at, 
                                   m.last_accessed, m.access_count, m.vector_id,
                                   LENGTH(ec.encrypted_data) as content_size
                            FROM memories m
                            LEFT JOIN encrypted_content ec ON m.memory_id = ec.memory_id
                            WHERE m.user_id = ? AND m.importance != ?
                        """, (user_id, MemoryImportance.CRITICAL.value))
                        
                        rows = await cursor.fetchall()
                        for row in rows:
                            try:
                                temp_entry = MemoryEntry(
                                    memory_id=UUID(row['memory_id']),
                                    user_id=row['user_id'],
                                    content="",
                                    content_hash="",
                                    memory_type=MemoryType(row['memory_type']),
                                    importance=MemoryImportance(row['importance']),
                                    scope=MemoryScope.PRIVATE,
                                    created_at=datetime.fromisoformat(row['created_at']),
                                    last_accessed=datetime.fromisoformat(row['last_accessed']) if row['last_accessed'] else datetime.now(timezone.utc),
                                    access_count=row['access_count'] or 0
                                )
                                score = temp_entry.calculate_retention_score()
                                estimated_size = (row['content_size'] or 0) + 1024  # 1KB for metadata overhead
                                memories_to_consider.append((score, row['memory_id'], row['user_id'], row['vector_id'], estimated_size))
                            except (ValueError, TypeError) as e:
                                logger.warning(f"Skipping memory {row['memory_id']} during cleanup due to data issue: {e}")
                                continue
                    
                    memories_to_consider.sort(key=lambda x: x[0])

                    deleted_count = 0
                    target_usage_bytes = max_usage_bytes * 0.95;
                    
                    for score, mem_id, u_id, vec_id, est_size in memories_to_consider:
                        if current_usage_bytes <= target_usage_bytes:
                            break
                        
                        await self._delete_memory(mem_id, u_id, vec_id)
                        current_usage_bytes -= est_size
                        deleted_count += 1
                    
                    if deleted_count > 0:
                        final_usage_gb = (await self._calculate_user_storage_usage(user_id)) / (1024**3)
                        logger.info(f"Cleaned up {deleted_count} low-relevance memories for user {user_id}. New usage: {final_usage_gb:.2f} GB.")
            except Exception as e:
                logger.error(f"Failed to perform low-relevance cleanup for user {user_id}: {e}", exc_info=True)

    async def _delete_memory(self, memory_id: str, user_id: UserID, vector_id: Optional[str]):
        """Delete memory from all storage systems"""
        try:
            # Remove from vector database
            if vector_id:
                try:
                    collection_name = f"user_{hashlib.sha256(user_id.encode()).hexdigest()[:16]}"
                    # Use get_collection to avoid creating it if it doesn't exist on deletion
                    collection = self.vector_db.get_collection(name=collection_name)
                    collection.delete(ids=[vector_id])
                except Exception as e:
                    # Log as a warning because metadata deletion should still proceed.
                    logger.warning(f"Failed to delete vector {vector_id} from ChromaDB for memory {memory_id}. Error: {e}")
            
            # Remove from metadata database
            async with self._get_db_connection() as conn:
                await conn.execute("DELETE FROM encrypted_content WHERE memory_id = ?", (memory_id,))
                await conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
                await conn.commit()
            
            logger.debug(f"Deleted memory {memory_id}")
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}", exc_info=True)
    
    async def get_user_memory_stats(self, user_id: UserID) -> Dict[str, Any]:
        """Get comprehensive memory statistics for user"""
        async with self._get_db_connection() as conn:
            # Total memories by type
            cursor = await conn.execute("""
                SELECT memory_type, importance, COUNT(*) as count
                FROM memories
                WHERE user_id = ?
                GROUP BY memory_type, importance
            """, (user_id,))
            type_stats = await cursor.fetchall()
            
            # Recent activity
            cursor = await conn.execute("""
                SELECT COUNT(*) as total, 
                       MAX(last_accessed) as last_access,
                       SUM(access_count) as total_accesses
                FROM memories
                WHERE user_id = ?
            """, (user_id,))
            activity_stats = await cursor.fetchone()
            
            return {
                'total_memories': activity_stats['total'] if activity_stats else 0,
                'last_access': activity_stats['last_access'] if activity_stats else None,
                'total_accesses': activity_stats['total_accesses'] if activity_stats else 0,
                'by_type': [dict(row) for row in type_stats],
                'storage_usage_bytes': await self._calculate_user_storage_usage(user_id)
            }
    
    async def _calculate_user_storage_usage(self, user_id: UserID) -> int:
        """Calculate the total storage usage for a given user in bytes."""
        total_bytes = 0

        # 1. Calculate SQLite database usage for the user
        async with self._get_db_connection() as conn:
            # Estimate size of user's entries in 'memories' table
            cursor = await conn.execute("""
                SELECT LENGTH(memory_id) + LENGTH(user_id) + LENGTH(content_hash) +
                       LENGTH(memory_type) + LENGTH(importance) + LENGTH(scope) +
                       LENGTH(created_at) + LENGTH(last_accessed) + LENGTH(expires_at) +
                       LENGTH(access_count) + LENGTH(relevance_score) + LENGTH(source_session) +
                       LENGTH(source_type) + LENGTH(related_memories) + LENGTH(tags) +
                       LENGTH(is_encrypted) + LENGTH(encryption_key_id) + LENGTH(vector_id) +
                       LENGTH(metadata) AS row_size
                FROM memories
                WHERE user_id = ?
            """, (user_id,))
            for row in await cursor.fetchall():
                total_bytes += (row['row_size'] or 0)

            # Estimate size of user's encrypted content in 'encrypted_content' table
            cursor = await conn.execute("""
                SELECT LENGTH(encrypted_data) AS data_size
                FROM encrypted_content ec
                JOIN memories m ON ec.memory_id = m.memory_id
                WHERE m.user_id = ?
            """, (user_id,))
            for row in await cursor.fetchall():
                total_bytes += (row['data_size'] or 0)

        # 2. Calculate ChromaDB storage usage for the user
        # ChromaDB stores data in a directory structure. We need to find the user's collection directory.
        # The collection name is derived from the user_id hash.
        user_collection_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        chroma_user_path = Path(self.config.vector_db_path) / f"chroma_{user_collection_hash}"
        
        if chroma_user_path.exists() and chroma_user_path.is_dir():
            for item in chroma_user_path.rglob('*'):
                if item.is_file():
                    total_bytes += item.stat().st_size
        
        return total_bytes
    
    async def export_user_memories(self, user_id: UserID) -> Dict[str, Any]:
        """Export all user memories for backup/migration"""
        memories = await self.retrieve_memories(
            user_id=user_id,
            limit=self.config.max_memories_per_user,
            include_content=True
        )
        
        return {
            'memory_count': len(memories),
            'memories': memories
        }

    async def synthesize_topic(self, user_id: UserID, topic_query: str, num_clusters: int = 3) -> Dict[str, Any]:
        """
        Synthesizes a topic by retrieving relevant memories, clustering them,
        and providing a summary for each identified sub-topic.
        """
        if not self.config.semantic_clustering:
            logger.warning("Semantic clustering is disabled in configuration.")
            return {"error": "Semantic clustering is disabled."}

        logger.info(f"Synthesizing topic '{topic_query}' for user {user_id} with {num_clusters} clusters.")

        # 1. Retrieve a large number of relevant memories
        # Increase limit to get enough data for clustering
        raw_memories = await self.retrieve_memories(
            user_id=user_id,
            query=topic_query,
            limit=self.config.max_memories_per_user, # Retrieve as many as possible
            include_content=True
        )

        if not raw_memories:
            return {"message": "No relevant memories found for the given topic."}

        # Filter out memories without content or that failed decryption
        processable_memories = [m for m in raw_memories if m.get('content') and "[DECRYPTION_FAILED]" not in m['content']]
        if not processable_memories:
            return {"message": "No processable memories found for the given topic."}

        # 2. Generate embeddings for these memories
        contents = [m['content'] for m in processable_memories]
        embeddings = await self._generate_embedding_batch(contents)

        if len(embeddings) < num_clusters:
            logger.warning(f"Not enough memories ({len(embeddings)}) to form {num_clusters} clusters. Reducing cluster count.")
            num_clusters = max(1, len(embeddings)) # Ensure at least 1 cluster if memories exist

        # 3. Use KMeans to cluster the embeddings
        try:
            kmeans = KMeans(n_clusters=num_clusters, random_state=0, n_init=10)
            kmeans.fit(embeddings)
            labels = kmeans.labels_
        except Exception as e:
            logger.error(f"KMeans clustering failed: {e}")
            return {"error": f"Failed to cluster memories: {e}"}

        clustered_topics = []
        for i in range(num_clusters):
            cluster_indices = np.where(labels == i)[0]
            cluster_memories = [processable_memories[j] for j in cluster_indices]

            if not cluster_memories:
                continue

            # Sort memories within cluster by relevance/importance to pick a good representative
            cluster_memories.sort(key=lambda m: m.get('relevance_score', 0) * m.get('importance_rank', 0), reverse=True)

            # For simplicity, take the most relevant memory as a representative
            # In a more advanced system, you might use an LLM to summarize the cluster
            representative_memory = cluster_memories[0]
            
            # Basic summary: combine content of top N memories or use representative
            summary_content = " ".join([m['content'] for m in cluster_memories[:min(3, len(cluster_memories))]]) # Top 3 memories

            clustered_topics.append({
                "cluster_id": i,
                "representative_memory_id": str(representative_memory['memory_id']),
                "representative_content_snippet": representative_memory['content'][:200] + "..." if len(representative_memory['content']) > 200 else representative_memory['content'],
                "memory_count": len(cluster_memories),
                "summary": summary_content, # This could be improved with LLM summarization
                "example_tags": list(set(tag for m in cluster_memories for tag in m.get('tags', [])))
            })
        
        return {
            "topic_query": topic_query,
            "total_memories_considered": len(processable_memories),
            "num_clusters_formed": len(clustered_topics),
            "clusters": clustered_topics
        }

    async def _generate_embedding_batch(self, texts: List[str]) -> np.ndarray:
        """Generate semantic embeddings for a batch of texts"""
        if not self.embedding_model:
            raise RuntimeError("Embedding model not initialized")
        
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None, self.embedding_model.encode, texts, {'show_progress_bar': False}
        )
        return embeddings
    
    async def shutdown(self):
        """Graceful shutdown of memory system"""
        logger.info("Shutting down memory system...")
        
        self._shutdown_event.set()
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Persist vector database
        if self.vector_db:
            try:
                self.vector_db.persist()
            except Exception as e:
                logger.error(f"Error persisting vector database: {e}")
        
        logger.info("Memory system shutdown complete")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get memory system performance metrics"""
        return {
            **self.metrics,
            'vector_db_collections': len(self.vector_db.list_collections()) if self.vector_db else 0,
            'embedding_model': self.config.embedding_model,
            'encryption_enabled': self.config.encryption_enabled
        }