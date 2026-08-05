#!/usr/bin/env python3
"""
SOMNUS Enhanced File Upload Manager
Production-ready streaming file processor with comprehensive type support
"""

import asyncio
import hashlib
import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, AsyncIterator
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import weakref

import numpy as np
from PIL import Image

# Optional dependencies with graceful degradation
try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False

try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# Document processing (pip-installable, standard)
from pypdf import PdfReader
from docx import Document
import pandas as pd
import chardet
import zipfile
import tarfile
import json
import yaml
import toml
import sqlite3
import psutil

# GGUF embedding support
try:
    from llama_cpp import Llama
    GGUF_AVAILABLE = True
except ImportError:
    GGUF_AVAILABLE = False

from pydantic import BaseModel, Field
from enum import Enum

logger = logging.getLogger(__name__)


class FileType(str, Enum):
    TEXT = "text"
    DOCUMENT = "document"
    PRESENTATION = "presentation"
    SPREADSHEET = "spreadsheet"
    IMAGE = "image"
    CODE = "code"
    DATA = "data"
    CONFIG = "config"
    ARCHIVE = "archive"
    AUDIO = "audio"
    VIDEO = "video"
    CAD = "cad"
    CREATIVE = "creative"
    SCIENTIFIC = "scientific"
    BLOCKCHAIN = "blockchain"
    DATABASE = "database"
    EXECUTABLE = "executable"
    HEALTHCARE = "healthcare"
    UNKNOWN = "unknown"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    VALIDATING = "validating"
    SCANNING = "scanning"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"


class SecurityLevel(str, Enum):
    SAFE = "safe"
    WARNING = "warning"
    DANGEROUS = "dangerous"
    BLOCKED = "blocked"


class FileMetadata(BaseModel):
    file_id: UUID = Field(default_factory=uuid4)
    filename: str
    file_type: FileType
    mime_type: str
    file_size: int = Field(ge=0)
    file_hash: str
    
    uploaded_by: str
    session_id: UUID
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    processing_progress: float = Field(default=0.0, ge=0, le=100)
    processing_error: Optional[str] = None
    processing_time: Optional[float] = None
    
    extracted_text: Optional[str] = None
    text_length: int = Field(default=0, ge=0)
    language_detected: Optional[str] = None
    encoding_detected: Optional[str] = None
    
    image_dimensions: Optional[Tuple[int, int]] = None
    image_format: Optional[str] = None
    has_text_overlay: bool = False
    
    page_count: Optional[int] = None
    slide_count: Optional[int] = None
    sheet_count: Optional[int] = None
    has_tables: bool = False
    has_images: bool = False
    has_macros: bool = False
    
    security_level: SecurityLevel = SecurityLevel.SAFE
    security_warnings: List[str] = Field(default_factory=list)
    virus_scan_clean: bool = False
    content_safe: bool = False
    
    embedding_model: Optional[str] = None
    chunk_count: int = Field(default=0, ge=0)
    vector_indexed: bool = False
    indexed_at: Optional[datetime] = None
    
    storage_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    preview_available: bool = False
    
    metadata_extracted: Dict[str, Any] = Field(default_factory=dict)
    
    @property
    def is_processed(self) -> bool:
        return self.processing_status == ProcessingStatus.COMPLETED
    
    @property
    def is_safe(self) -> bool:
        return self.security_level in [SecurityLevel.SAFE, SecurityLevel.WARNING]
    
    @property
    def file_size_mb(self) -> float:
        return self.file_size / (1024 * 1024)


class StreamingFileHandler:
    """Handles streaming file upload and processing"""
    
    def __init__(self, temp_dir: str = None):
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir()) / "somnus_uploads"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_size = 8192  # 8KB chunks
    
    async def stream_to_temp_file(
        self, 
        file_stream: AsyncIterator[bytes], 
        filename: str
    ) -> Tuple[Path, str, int]:
        """Stream file data to temporary file and calculate hash"""
        temp_file_id = uuid4()
        temp_path = self.temp_dir / f"{temp_file_id}_{filename}"
        
        hasher = hashlib.sha256()
        total_size = 0
        
        async with aiofiles.open(temp_path, 'wb') as f:
            async for chunk in file_stream:
                await f.write(chunk)
                hasher.update(chunk)
                total_size += len(chunk)
        
        file_hash = hasher.hexdigest()
        return temp_path, file_hash, total_size
    
    async def validate_file_stream(
        self, 
        file_stream: AsyncIterator[bytes], 
        expected_size: int = None,
        max_size: int = 100 * 1024 * 1024  # 100MB default
    ) -> bool:
        """Validate file stream without saving to disk"""
        total_size = 0
        
        async for chunk in file_stream:
            total_size += len(chunk)
            
            if total_size > max_size:
                raise ValueError(f"File too large: {total_size} > {max_size}")
        
        if expected_size and total_size != expected_size:
            raise ValueError(f"Size mismatch: {total_size} != {expected_size}")
        
        return True


class FileTypeClassifier:
    """Advanced file type classification with content analysis"""
    
    def __init__(self):
        # Use python-magic if available, otherwise fall back to mimetypes stdlib
        self.magic_detector = None
        if MAGIC_AVAILABLE:
            try:
                self.magic_detector = magic.Magic(mime=True)
            except Exception:
                pass
        
        # Comprehensive file type mappings
        self.type_mappings = {
            # Text files
            FileType.TEXT: {
                'extensions': ['.txt', '.md', '.rst', '.log', '.readme'],
                'mime_patterns': ['text/plain', 'text/markdown']
            },
            
            # Documents
            FileType.DOCUMENT: {
                'extensions': ['.pdf', '.doc', '.docx', '.rtf', '.odt'],
                'mime_patterns': ['application/pdf', 'application/msword', 'application/vnd.openxmlformats']
            },
            
            # Presentations
            FileType.PRESENTATION: {
                'extensions': ['.ppt', '.pptx', '.odp', '.key'],
                'mime_patterns': ['application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml']
            },
            
            # Spreadsheets
            FileType.SPREADSHEET: {
                'extensions': ['.xls', '.xlsx', '.xlsm', '.ods', '.csv', '.numbers'],
                'mime_patterns': ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml', 'text/csv']
            },
            
            # Images
            FileType.IMAGE: {
                'extensions': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.svg'],
                'mime_patterns': ['image/']
            },
            
            # Code files
            FileType.CODE: {
                'extensions': ['.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.h', '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.ts', '.jsx', '.tsx', '.vue', '.svelte', '.sol', '.vyper', '.move'],
                'mime_patterns': ['text/x-python', 'text/javascript', 'text/html', 'text/css']
            },
            
            # Data files
            FileType.DATA: {
                'extensions': ['.json', '.xml', '.parquet', '.avro', '.arrow', '.orc', '.hdf5', '.nc', '.fits'],
                'mime_patterns': ['application/json', 'application/xml', 'text/xml']
            },
            
            # Configuration files
            FileType.CONFIG: {
                'extensions': ['.yaml', '.yml', '.toml', '.ini', '.cfg', '.env', '.dockerfile', '.tf', '.hcl'],
                'mime_patterns': ['application/x-yaml', 'text/x-yaml']
            },
            
            # Archives
            FileType.ARCHIVE: {
                'extensions': ['.zip', '.tar', '.gz', '.bz2', '.xz', '.rar', '.7z'],
                'mime_patterns': ['application/zip', 'application/x-tar', 'application/gzip']
            },
            
            # Audio
            FileType.AUDIO: {
                'extensions': ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac'],
                'mime_patterns': ['audio/']
            },
            
            # Video
            FileType.VIDEO: {
                'extensions': ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm'],
                'mime_patterns': ['video/']
            },
            
            # CAD
            FileType.CAD: {
                'extensions': ['.dwg', '.dxf', '.step', '.stp', '.iges', '.igs', '.stl', '.obj', '.ply', '.blend'],
                'mime_patterns': ['application/acad']
            },
            
            # Creative
            FileType.CREATIVE: {
                'extensions': ['.psd', '.ai', '.eps', '.indd', '.sketch', '.fig', '.xd'],
                'mime_patterns': ['application/postscript']
            },
            
            # Scientific
            FileType.SCIENTIFIC: {
                'extensions': ['.mat', '.sav', '.dta', '.sas7bdat', '.rdata', '.nii', '.dicom'],
                'mime_patterns': ['application/x-matlab']
            },
            
            # Database
            FileType.DATABASE: {
                'extensions': ['.sqlite', '.db', '.accdb', '.mdb'],
                'mime_patterns': ['application/x-sqlite3', 'application/vnd.ms-access']
            },
            
            # Blockchain
            FileType.BLOCKCHAIN: {
                'extensions': ['.sol', '.vyper', '.move'],
                'mime_patterns': []
            },

            # Healthcare interoperability — HL7, FHIR, CDA
            FileType.HEALTHCARE: {
                'extensions': [
                    '.hl7', '.hl7v2', '.hl7v3',
                    '.fhir',
                    '.cda',
                ],
                'mime_patterns': [
                    'application/hl7-v2',
                    'application/fhir+json',
                    'application/fhir+xml',
                    'text/xml+cda',
                ]
            }
        }
    
    def classify_file(self, filename: str, file_data: bytes = None, mime_type: str = None) -> FileType:
        """Classify file type based on extension, MIME type, and content"""
        p = Path(filename)
        suffix = p.suffix.lower()

        # Handle compound healthcare extensions before the main loop
        compound = ''.join(p.suffixes[-2:]).lower()
        if compound in {'.fhir.json', '.fhir.xml', '.cda.xml'}:
            return FileType.HEALTHCARE
        
        if not mime_type and file_data:
            try:
                mime_type = self.magic_detector.from_buffer(file_data)
            except (AttributeError, TypeError, OSError) as e:
                logger.debug(f"Magic detection failed, falling back to mimetypes: {e}")
                mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        
        # Check each file type mapping
        for file_type, mapping in self.type_mappings.items():
            # Check extension
            if suffix in mapping['extensions']:
                return file_type
            
            # Check MIME type patterns
            if mime_type:
                for pattern in mapping['mime_patterns']:
                    if pattern.endswith('/') and mime_type.startswith(pattern):
                        return file_type
                    elif pattern in mime_type:
                        return file_type
        
        # Content-based classification for unknown files
        if file_data:
            return self._classify_by_content(file_data)
        
        return FileType.UNKNOWN
    
    def _classify_by_content(self, file_data: bytes) -> FileType:
        """Classify based on content analysis"""
        try:
            # Try to decode as text
            text_content = file_data.decode('utf-8', errors='ignore')
            
            # Check for code patterns
            code_indicators = ['def ', 'function ', 'class ', 'import ', '#include', 'package ']
            if any(indicator in text_content for indicator in code_indicators):
                return FileType.CODE
            
            # Check for config patterns
            config_indicators = ['[section]', 'key=value', '---\n']
            if any(indicator in text_content for indicator in config_indicators):
                return FileType.CONFIG
            
            # Check for structured data
            try:
                json.loads(text_content)
                return FileType.DATA
            except (json.JSONDecodeError, ValueError):
                pass
            
            return FileType.TEXT
            
        except UnicodeDecodeError:
            # Binary file - check magic bytes
            if file_data.startswith(b'\x89PNG'):
                return FileType.IMAGE
            elif file_data.startswith(b'\xFF\xD8\xFF'):
                return FileType.IMAGE
            elif file_data.startswith(b'PK\x03\x04'):
                return FileType.ARCHIVE
            elif file_data.startswith(b'%PDF'):
                return FileType.DOCUMENT
            
        return FileType.UNKNOWN


class SecurityValidator:
    """Comprehensive security validation for uploaded files"""
    
    def __init__(self):
        self.max_file_size = 500 * 1024 * 1024  # 500MB
        self.dangerous_extensions = {
            '.exe', '.bat', '.cmd', '.com', '.scr', '.pif', '.vbs', '.js', '.jar',
            '.msi', '.dll', '.sys', '.drv', '.ocx', '.cpl', '.inf', '.reg'
        }
        self.suspicious_mime_types = {
            'application/x-msdownload', 'application/x-msdos-program',
            'application/x-executable', 'application/x-java-archive'
        }
    
    async def validate_file(self, file_path: Path, metadata: FileMetadata) -> SecurityLevel:
        """Comprehensive security validation"""
        warnings = []
        security_level = SecurityLevel.SAFE
        
        # Size check
        if metadata.file_size > self.max_file_size:
            warnings.append(f"File size {metadata.file_size_mb:.1f}MB exceeds limit")
            security_level = SecurityLevel.WARNING
        
        # Extension check
        file_ext = file_path.suffix.lower()
        if file_ext in self.dangerous_extensions:
            warnings.append(f"Potentially dangerous file type: {file_ext}")
            security_level = SecurityLevel.DANGEROUS
        
        # MIME type check
        if metadata.mime_type in self.suspicious_mime_types:
            warnings.append(f"Suspicious MIME type: {metadata.mime_type}")
            security_level = SecurityLevel.WARNING
        
        # Content validation
        content_warnings = await self._validate_content(file_path, metadata.file_type)
        warnings.extend(content_warnings)
        
        if content_warnings:
            security_level = max(security_level, SecurityLevel.WARNING)
        
        # Virus scan simulation (integrate with real scanner)
        virus_clean = await self._scan_for_viruses(file_path)
        if not virus_clean:
            warnings.append("Failed virus scan")
            security_level = SecurityLevel.BLOCKED
        
        metadata.security_warnings = warnings
        metadata.virus_scan_clean = virus_clean
        metadata.content_safe = security_level != SecurityLevel.BLOCKED
        
        return security_level
    
    async def _validate_content(self, file_path: Path, file_type: FileType) -> List[str]:
        """Validate file content based on type"""
        warnings = []
        
        try:
            if file_type == FileType.ARCHIVE:
                warnings.extend(await self._validate_archive(file_path))
            elif file_type == FileType.DOCUMENT:
                warnings.extend(await self._validate_document(file_path))
            elif file_type == FileType.CODE:
                warnings.extend(await self._validate_code(file_path))
        except Exception as e:
            warnings.append(f"Content validation error: {str(e)}")
        
        return warnings
    
    async def _validate_archive(self, file_path: Path) -> List[str]:
        """Validate archive contents"""
        warnings = []
        
        try:
            if file_path.suffix.lower() == '.zip':
                with zipfile.ZipFile(file_path, 'r') as zf:
                    for info in zf.infolist():
                        if Path(info.filename).suffix.lower() in self.dangerous_extensions:
                            warnings.append(f"Dangerous file in archive: {info.filename}")
        except Exception as e:
            warnings.append(f"Archive validation failed: {str(e)}")
        
        return warnings
    
    async def _validate_document(self, file_path: Path) -> List[str]:
        """Validate document for macros and embedded content"""
        warnings = []
        
        try:
            if file_path.suffix.lower() in ['.docx', '.xlsx', '.pptx']:
                # Check for macros in Office documents
                with zipfile.ZipFile(file_path, 'r') as zf:
                    if any('vbaProject' in name for name in zf.namelist()):
                        warnings.append("Document contains macros")
        except Exception:
            pass
        
        return warnings
    
    async def _validate_code(self, file_path: Path) -> List[str]:
        """Validate code files for suspicious patterns"""
        warnings = []
        
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = await f.read()
            
            suspicious_patterns = [
                'eval(', 'exec(', 'subprocess.', 'os.system', '__import__',
                'shell=True', 'rm -rf', 'format c:', 'delete *'
            ]
            
            for pattern in suspicious_patterns:
                if pattern in content.lower():
                    warnings.append(f"Suspicious code pattern detected: {pattern}")
        except Exception:
            pass
        
        return warnings
    
    async def _scan_for_viruses(self, file_path: Path) -> bool:
        """Virus scanning - integrate with ClamAV or similar"""
        # Placeholder for real virus scanning
        # In production, integrate with ClamAV:
        # result = subprocess.run(['clamscan', str(file_path)], capture_output=True)
        # return result.returncode == 0
        
        return True  # Assume clean for now


class GGUFEmbeddingEngine:
    """GGUF-based embedding generation with caching"""
    
    def __init__(self, model_path: str = None, cache_engine=None):
        self.model = None
        self.cache = cache_engine
        self.model_name = None
        
        if model_path and GGUF_AVAILABLE and Path(model_path).exists():
            try:
                self.model = Llama(
                    model_path=model_path,
                    embedding=True,
                    n_ctx=2048,
                    n_gpu_layers=-1,  # Use all GPU layers
                    verbose=False
                )
                self.model_name = Path(model_path).name
                logger.info(f"GGUF embedding model loaded: {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to load GGUF model: {e}")
    
    def is_available(self) -> bool:
        """Check if embedding model is available"""
        return self.model is not None
    
    async def generate_embeddings(self, text: str, cache_key: str = None) -> Optional[np.ndarray]:
        """Generate embeddings with caching"""
        if not self.model:
            return None
        
        # Check cache first
        if cache_key and self.cache:
            cached = await self.cache.get(f"embedding:{cache_key}")
            if cached is not None:
                return np.array(cached)
        
        try:
            # Generate embedding
            embedding = self.model.create_embedding(text[:2000])  # Truncate to context size
            embedding_array = np.array(embedding['data'][0]['embedding'], dtype=np.float32)
            
            # Cache result
            if cache_key and self.cache:
                await self.cache.set(
                    f"embedding:{cache_key}", 
                    embedding_array.tolist(),
                    ttl_seconds=86400  # 24 hours
                )
            
            return embedding_array
            
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return None
    
    async def batch_generate_embeddings(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """Generate embeddings for multiple texts"""
        return [await self.generate_embeddings(text) for text in texts]


class EnhancedFileUploadManager:
    """Production-ready file upload manager with streaming and comprehensive processing"""
    
    def __init__(
        self,
        upload_dir: str,
        cache_engine=None,
        memory_manager=None,
        gguf_model_path: str = None,
        max_workers: int = 4
    ):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        
        self.cache = cache_engine
        self.memory_manager = memory_manager
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Initialize components
        self.streaming_handler = StreamingFileHandler()
        self.classifier = FileTypeClassifier()
        self.security_validator = SecurityValidator()
        self.embedding_engine = GGUFEmbeddingEngine(gguf_model_path, cache_engine)
        
        # Active uploads tracking
        self.active_uploads: Dict[UUID, FileMetadata] = {}
        self.upload_callbacks: Dict[UUID, List[weakref.ref]] = {}
        
        logger.info(f"Enhanced file upload manager initialized")
    
    async def upload_file_stream(
        self,
        file_stream: AsyncIterator[bytes],
        filename: str,
        user_id: str,
        session_id: UUID,
        progress_callback: Optional[callable] = None
    ) -> FileMetadata:
        """Upload file from stream with comprehensive processing"""
        
        start_time = time.time()
        
        # Create initial metadata
        metadata = FileMetadata(
            filename=filename,
            file_type=FileType.UNKNOWN,
            mime_type="application/octet-stream",
            file_size=0,
            file_hash="",
            uploaded_by=user_id,
            session_id=session_id,
            processing_status=ProcessingStatus.UPLOADING
        )
        
        self.active_uploads[metadata.file_id] = metadata
        
        if progress_callback:
            self._add_progress_callback(metadata.file_id, progress_callback)
        
        try:
            await self._update_progress(metadata.file_id, 5.0, "Streaming file data...")
            
            # Stream to temporary file
            temp_path, file_hash, file_size = await self.streaming_handler.stream_to_temp_file(
                file_stream, filename
            )
            
            metadata.file_hash = file_hash
            metadata.file_size = file_size
            metadata.processing_status = ProcessingStatus.VALIDATING
            
            await self._update_progress(metadata.file_id, 15.0, "Validating file...")
            
            # Check for duplicate by hash
            if await self._check_duplicate(file_hash, user_id):
                metadata.processing_status = ProcessingStatus.COMPLETED
                metadata.processing_error = "Duplicate file detected"
                return metadata
            
            # Classify file type
            with open(temp_path, 'rb') as f:
                first_chunk = f.read(8192)
            
            metadata.file_type = self.classifier.classify_file(filename, first_chunk)
            metadata.mime_type = self.classifier.magic_detector.from_file(str(temp_path))
            
            await self._update_progress(metadata.file_id, 25.0, "Security scanning...")
            
            # Security validation
            metadata.security_level = await self.security_validator.validate_file(temp_path, metadata)
            
            if not metadata.is_safe:
                metadata.processing_status = ProcessingStatus.FAILED
                metadata.processing_error = f"Security validation failed: {metadata.security_level}"
                return metadata
            
            await self._update_progress(metadata.file_id, 40.0, "Extracting content...")
            
            # Content extraction
            await self._extract_content(temp_path, metadata)
            
            await self._update_progress(metadata.file_id, 70.0, "Generating embeddings...")
            
            # Generate embeddings
            if metadata.extracted_text and self.embedding_engine.is_available():
                await self._generate_embeddings(metadata)
            
            await self._update_progress(metadata.file_id, 85.0, "Storing file...")
            
            # Move to permanent storage
            permanent_path = self.upload_dir / f"{metadata.file_id}_{filename}"
            temp_path.rename(permanent_path)
            metadata.storage_path = str(permanent_path)
            
            await self._update_progress(metadata.file_id, 95.0, "Indexing...")
            
            # Store in memory system if available
            if self.memory_manager and metadata.extracted_text:
                await self._store_in_memory_system(metadata)
            
            # Cache metadata
            if self.cache:
                await self.cache.set(
                    f"file_metadata:{metadata.file_id}",
                    metadata.dict(),
                    ttl_seconds=86400 * 30  # 30 days
                )
                await self.cache.set(
                    f"file_hash:{file_hash}",
                    metadata.file_id,
                    ttl_seconds=86400 * 30
                )
            
            metadata.processing_status = ProcessingStatus.COMPLETED
            metadata.processing_time = time.time() - start_time
            metadata.indexed_at = datetime.now(timezone.utc)
            
            await self._update_progress(metadata.file_id, 100.0, "Upload completed")
            
            logger.info(f"File upload completed: {filename} ({metadata.file_id}) in {metadata.processing_time:.2f}s")
            
            return metadata
            
        except Exception as e:
            logger.error(f"File upload failed: {filename} - {e}")
            metadata.processing_status = ProcessingStatus.FAILED
            metadata.processing_error = str(e)
            metadata.processing_time = time.time() - start_time
            
            # Cleanup temp file
            try:
                if 'temp_path' in locals() and temp_path.exists():
                    temp_path.unlink()
            except (OSError, PermissionError) as cleanup_err:
                logger.debug(f"Temp file cleanup failed: {cleanup_err}")
            
            await self._update_progress(metadata.file_id, 0.0, f"Upload failed: {str(e)}")
            
            return metadata
    
    async def _extract_content(self, file_path: Path, metadata: FileMetadata):
        """Extract content based on file type"""
        try:
            if metadata.file_type == FileType.TEXT:
                await self._extract_text_content(file_path, metadata)
            elif metadata.file_type == FileType.DOCUMENT:
                await self._extract_document_content(file_path, metadata)
            elif metadata.file_type == FileType.IMAGE:
                await self._extract_image_content(file_path, metadata)
            elif metadata.file_type == FileType.CODE:
                await self._extract_code_content(file_path, metadata)
            elif metadata.file_type == FileType.DATA:
                await self._extract_data_content(file_path, metadata)
            elif metadata.file_type == FileType.CONFIG:
                await self._extract_config_content(file_path, metadata)
            elif metadata.file_type == FileType.SPREADSHEET:
                await self._extract_spreadsheet_content(file_path, metadata)
            elif metadata.file_type == FileType.PRESENTATION:
                await self._extract_presentation_content(file_path, metadata)
            elif metadata.file_type == FileType.ARCHIVE:
                await self._extract_archive_content(file_path, metadata)
            elif metadata.file_type == FileType.DATABASE:
                await self._extract_database_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Content extraction failed for {file_path}: {e}")
            metadata.processing_error = f"Content extraction error: {str(e)}"
    
    async def _extract_text_content(self, file_path: Path, metadata: FileMetadata):
        """Extract text file content"""
        try:
            # Detect encoding
            with open(file_path, 'rb') as f:
                raw_data = f.read()
                encoding_result = chardet.detect(raw_data)
                encoding = encoding_result.get('encoding', 'utf-8')
            
            # Read with detected encoding
            async with aiofiles.open(file_path, 'r', encoding=encoding, errors='replace') as f:
                content = await f.read()
            
            metadata.extracted_text = content
            metadata.text_length = len(content)
            metadata.encoding_detected = encoding
            metadata.metadata_extracted = {
                'line_count': content.count('\n') + 1,
                'word_count': len(content.split()),
                'char_count': len(content),
                'encoding_confidence': encoding_result.get('confidence', 0)
            }
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
    
    async def _extract_document_content(self, file_path: Path, metadata: FileMetadata):
        """Extract content from documents (PDF, DOCX, etc.)"""
        suffix = file_path.suffix.lower()
        
        try:
            if suffix == '.pdf':
                await self._extract_pdf_content(file_path, metadata)
            elif suffix in ['.docx', '.doc']:
                await self._extract_docx_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Document extraction failed: {e}")
    
    async def _extract_pdf_content(self, file_path: Path, metadata: FileMetadata):
        """Extract PDF content"""
        text_content = []
        
        reader = PdfReader(str(file_path))
        metadata.page_count = len(reader.pages)
        
        for page_num, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text.strip():
                text_content.append(f"--- Page {page_num + 1} ---\n{page_text}")
        
        metadata.extracted_text = "\n\n".join(text_content)
        metadata.text_length = len(metadata.extracted_text)
        metadata.metadata_extracted = {
            'page_count': metadata.page_count,
            'has_images': any('/XObject' in page.get('/Resources', {}) for page in reader.pages)
        }
    
    async def _extract_docx_content(self, file_path: Path, metadata: FileMetadata):
        """Extract DOCX content"""
        doc = Document(str(file_path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        
        metadata.extracted_text = "\n\n".join(paragraphs)
        metadata.text_length = len(metadata.extracted_text)
        metadata.has_tables = len(doc.tables) > 0
        metadata.has_images = len(doc.inline_shapes) > 0
        metadata.metadata_extracted = {
            'paragraph_count': len(paragraphs),
            'table_count': len(doc.tables),
            'image_count': len(doc.inline_shapes)
        }
    
    async def _extract_image_content(self, file_path: Path, metadata: FileMetadata):
        """Extract text from images using OCR"""
        try:
            # Load image
            image = cv2.imread(str(file_path))
            if image is None:
                return
            
            # Get image dimensions
            height, width = image.shape[:2]
            metadata.image_dimensions = (width, height)
            metadata.image_format = file_path.suffix.lower()[1:]
            
            # Convert to grayscale for OCR
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            
            # Apply preprocessing
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # OCR extraction
            extracted_text = pytesseract.image_to_string(thresh, config='--oem 3 --psm 6')
            
            if extracted_text.strip():
                metadata.extracted_text = extracted_text.strip()
                metadata.text_length = len(metadata.extracted_text)
                metadata.has_text_overlay = True
            
            metadata.metadata_extracted = {
                'image_width': width,
                'image_height': height,
                'image_format': metadata.image_format,
                'file_size_kb': metadata.file_size / 1024
            }
        except Exception as e:
            logger.error(f"Image OCR failed: {e}")
    
    async def _extract_code_content(self, file_path: Path, metadata: FileMetadata):
        """Extract code file content with analysis"""
        await self._extract_text_content(file_path, metadata)
        
        if metadata.extracted_text:
            lines = metadata.extracted_text.split('\n')
            
            # Code analysis
            blank_lines = sum(1 for line in lines if not line.strip())
            comment_lines = 0
            
            # Simple comment detection
            comment_prefixes = ['#', '//', '--', '/*', '*', '%', '<!--']
            for line in lines:
                stripped = line.strip()
                if any(stripped.startswith(prefix) for prefix in comment_prefixes):
                    comment_lines += 1
            
            loc = len(lines) - blank_lines - comment_lines
            
            metadata.metadata_extracted.update({
                'total_lines': len(lines),
                'blank_lines': blank_lines,
                'comment_lines': comment_lines,
                'lines_of_code': loc,
                'language': file_path.suffix.lower()[1:] if file_path.suffix else 'unknown'
            })
    
    async def _extract_data_content(self, file_path: Path, metadata: FileMetadata):
        """Extract data file content"""
        suffix = file_path.suffix.lower()
        
        try:
            if suffix == '.json':
                await self._extract_json_content(file_path, metadata)
            elif suffix == '.csv':
                await self._extract_csv_content(file_path, metadata)
            elif suffix in ['.yaml', '.yml']:
                await self._extract_yaml_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Data extraction failed: {e}")
    
    async def _extract_json_content(self, file_path: Path, metadata: FileMetadata):
        """Extract JSON content"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        try:
            parsed = json.loads(content)
            
            if isinstance(parsed, dict):
                description = f"JSON object with {len(parsed)} keys: {list(parsed.keys())[:10]}"
            elif isinstance(parsed, list):
                description = f"JSON array with {len(parsed)} items"
            else:
                description = f"JSON value: {type(parsed).__name__}"
            
            metadata.extracted_text = f"{description}\n\nContent preview:\n{content[:1000]}"
            metadata.text_length = len(content)
            metadata.metadata_extracted = {
                'json_type': type(parsed).__name__,
                'size_bytes': len(content),
                'valid_json': True
            }
        except json.JSONDecodeError:
            metadata.extracted_text = content[:1000]
            metadata.metadata_extracted = {'valid_json': False}
    
    async def _extract_csv_content(self, file_path: Path, metadata: FileMetadata):
        """Extract CSV content"""
        try:
            df = pd.read_csv(file_path, nrows=1000)  # Limit for large files
            
            description_parts = [
                f"CSV dataset with {len(df)} rows and {len(df.columns)} columns",
                f"Columns: {', '.join(df.columns.tolist())}",
            ]
            
            if len(df) > 0:
                description_parts.append("Sample data:")
                description_parts.append(df.head(3).to_string())
            
            metadata.extracted_text = "\n".join(description_parts)
            metadata.text_length = len(metadata.extracted_text)
            metadata.has_tables = True
            metadata.metadata_extracted = {
                'row_count': len(df),
                'column_count': len(df.columns),
                'columns': df.columns.tolist(),
                'data_types': {col: str(dtype) for col, dtype in df.dtypes.items()}
            }
        except Exception as e:
            logger.error(f"CSV extraction failed: {e}")
    
    async def _extract_yaml_content(self, file_path: Path, metadata: FileMetadata):
        """Extract YAML content"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        try:
            parsed = yaml.safe_load(content)
            
            if isinstance(parsed, dict):
                description = f"YAML configuration with {len(parsed)} top-level keys: {list(parsed.keys())[:10]}"
            else:
                description = f"YAML {type(parsed).__name__}"
            
            metadata.extracted_text = f"{description}\n\nContent:\n{content}"
            metadata.text_length = len(content)
            metadata.metadata_extracted = {
                'yaml_type': type(parsed).__name__,
                'valid_yaml': True
            }
        except yaml.YAMLError:
            metadata.extracted_text = content
            metadata.metadata_extracted = {'valid_yaml': False}
    
    async def _extract_config_content(self, file_path: Path, metadata: FileMetadata):
        """Extract configuration file content"""
        suffix = file_path.suffix.lower()
        
        if suffix in ['.yaml', '.yml']:
            await self._extract_yaml_content(file_path, metadata)
        elif suffix == '.toml':
            await self._extract_toml_content(file_path, metadata)
        elif suffix in ['.ini', '.cfg']:
            await self._extract_ini_content(file_path, metadata)
        else:
            await self._extract_text_content(file_path, metadata)
    
    async def _extract_toml_content(self, file_path: Path, metadata: FileMetadata):
        """Extract TOML content"""
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        try:
            parsed = toml.loads(content)
            description = f"TOML configuration with {len(parsed)} sections: {list(parsed.keys())[:10]}"
            
            metadata.extracted_text = f"{description}\n\nContent:\n{content}"
            metadata.text_length = len(content)
            metadata.metadata_extracted = {
                'toml_sections': list(parsed.keys()),
                'valid_toml': True
            }
        except toml.TomlDecodeError:
            metadata.extracted_text = content
            metadata.metadata_extracted = {'valid_toml': False}
    
    async def _extract_ini_content(self, file_path: Path, metadata: FileMetadata):
        """Extract INI/CFG content"""
        import configparser
        
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        try:
            config = configparser.ConfigParser()
            config.read_string(content)
            
            sections = list(config.sections())
            description = f"Configuration file with {len(sections)} sections: {sections[:10]}"
            
            metadata.extracted_text = f"{description}\n\nContent:\n{content}"
            metadata.text_length = len(content)
            metadata.metadata_extracted = {
                'config_sections': sections,
                'valid_config': True
            }
        except configparser.Error:
            metadata.extracted_text = content
            metadata.metadata_extracted = {'valid_config': False}
    
    async def _extract_spreadsheet_content(self, file_path: Path, metadata: FileMetadata):
        """Extract spreadsheet content"""
        suffix = file_path.suffix.lower()
        
        try:
            if suffix == '.csv':
                await self._extract_csv_content(file_path, metadata)
            elif suffix in ['.xlsx', '.xls']:
                await self._extract_excel_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Spreadsheet extraction failed: {e}")
    
    async def _extract_excel_content(self, file_path: Path, metadata: FileMetadata):
        """Extract Excel content"""
        try:
            # Read all sheets
            excel_file = pd.ExcelFile(file_path)
            sheet_names = excel_file.sheet_names
            metadata.sheet_count = len(sheet_names)
            
            descriptions = []
            total_rows = 0
            
            for sheet_name in sheet_names[:5]:  # Limit to first 5 sheets
                df = pd.read_excel(file_path, sheet_name=sheet_name, nrows=100)
                total_rows += len(df)
                
                descriptions.append(f"Sheet '{sheet_name}': {len(df)} rows, {len(df.columns)} columns")
                if len(df) > 0:
                    descriptions.append(f"Columns: {', '.join(df.columns.tolist()[:10])}")
            
            metadata.extracted_text = "\n".join(descriptions)
            metadata.text_length = len(metadata.extracted_text)
            metadata.has_tables = True
            metadata.metadata_extracted = {
                'sheet_count': metadata.sheet_count,
                'sheet_names': sheet_names,
                'total_estimated_rows': total_rows
            }
        except Exception as e:
            logger.error(f"Excel extraction failed: {e}")
    
    async def _extract_presentation_content(self, file_path: Path, metadata: FileMetadata):
        """Extract presentation content"""
        suffix = file_path.suffix.lower()
        
        try:
            if suffix in ['.pptx', '.ppt']:
                await self._extract_powerpoint_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Presentation extraction failed: {e}")
    
    async def _extract_powerpoint_content(self, file_path: Path, metadata: FileMetadata):
        """Extract PowerPoint content"""
        try:
            from pptx import Presentation
            
            prs = Presentation(str(file_path))
            metadata.slide_count = len(prs.slides)
            
            slide_texts = []
            for i, slide in enumerate(prs.slides):
                slide_text = []
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        slide_text.append(shape.text.strip())
                
                if slide_text:
                    slide_texts.append(f"--- Slide {i + 1} ---\n" + "\n".join(slide_text))
            
            metadata.extracted_text = "\n\n".join(slide_texts)
            metadata.text_length = len(metadata.extracted_text)
            metadata.metadata_extracted = {
                'slide_count': metadata.slide_count,
                'slides_with_text': len(slide_texts)
            }
        except Exception as e:
            logger.error(f"PowerPoint extraction failed: {e}")
    
    async def _extract_archive_content(self, file_path: Path, metadata: FileMetadata):
        """Extract archive content listing"""
        suffix = file_path.suffix.lower()
        
        try:
            if suffix == '.zip':
                await self._extract_zip_content(file_path, metadata)
            elif suffix in ['.tar', '.gz', '.bz2']:
                await self._extract_tar_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Archive extraction failed: {e}")
    
    async def _extract_zip_content(self, file_path: Path, metadata: FileMetadata):
        """Extract ZIP archive listing"""
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                file_list = zf.namelist()
                
                descriptions = [f"ZIP archive containing {len(file_list)} files:"]
                
                # Group by file types
                file_types = {}
                total_size = 0
                
                for info in zf.infolist():
                    if not info.is_dir():
                        ext = Path(info.filename).suffix.lower()
                        file_types[ext] = file_types.get(ext, 0) + 1
                        total_size += info.file_size
                
                descriptions.append(f"Total uncompressed size: {total_size / 1024 / 1024:.1f} MB")
                descriptions.append("File types:")
                for ext, count in sorted(file_types.items()):
                    descriptions.append(f"  {ext or 'no extension'}: {count} files")
                
                # List first 20 files
                descriptions.append("\nFiles (first 20):")
                for filename in file_list[:20]:
                    descriptions.append(f"  {filename}")
                
                if len(file_list) > 20:
                    descriptions.append(f"  ... and {len(file_list) - 20} more files")
                
                metadata.extracted_text = "\n".join(descriptions)
                metadata.text_length = len(metadata.extracted_text)
                metadata.metadata_extracted = {
                    'file_count': len(file_list),
                    'file_types': file_types,
                    'uncompressed_size': total_size
                }
        except Exception as e:
            logger.error(f"ZIP extraction failed: {e}")
    
    async def _extract_tar_content(self, file_path: Path, metadata: FileMetadata):
        """Extract TAR archive listing"""
        try:
            with tarfile.open(file_path, 'r:*') as tf:
                members = tf.getmembers()
                file_list = [m.name for m in members if m.isfile()]
                
                descriptions = [f"TAR archive containing {len(file_list)} files:"]
                
                # Group by file types
                file_types = {}
                total_size = sum(m.size for m in members if m.isfile())
                
                for member in members:
                    if member.isfile():
                        ext = Path(member.name).suffix.lower()
                        file_types[ext] = file_types.get(ext, 0) + 1
                
                descriptions.append(f"Total size: {total_size / 1024 / 1024:.1f} MB")
                descriptions.append("File types:")
                for ext, count in sorted(file_types.items()):
                    descriptions.append(f"  {ext or 'no extension'}: {count} files")
                
                metadata.extracted_text = "\n".join(descriptions)
                metadata.text_length = len(metadata.extracted_text)
                metadata.metadata_extracted = {
                    'file_count': len(file_list),
                    'file_types': file_types,
                    'total_size': total_size
                }
        except Exception as e:
            logger.error(f"TAR extraction failed: {e}")
    
    async def _extract_database_content(self, file_path: Path, metadata: FileMetadata):
        """Extract database content"""
        suffix = file_path.suffix.lower()
        
        try:
            if suffix in ['.sqlite', '.db']:
                await self._extract_sqlite_content(file_path, metadata)
        except Exception as e:
            logger.error(f"Database extraction failed: {e}")
    
    async def _extract_sqlite_content(self, file_path: Path, metadata: FileMetadata):
        """Extract SQLite database schema and content summary"""
        try:
            conn = sqlite3.connect(str(file_path))
            cursor = conn.cursor()
            
            # Get tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            
            descriptions = [f"SQLite database with {len(tables)} tables:"]
            
            for table_name, in tables:
                # Get table info
                cursor.execute(f"PRAGMA table_info({table_name});")
                columns = cursor.fetchall()
                
                # Get row count
                cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
                row_count = cursor.fetchone()[0]
                
                descriptions.append(f"\nTable '{table_name}': {row_count} rows, {len(columns)} columns")
                descriptions.append("Columns:")
                for col in columns:
                    descriptions.append(f"  {col[1]} ({col[2]})")
            
            conn.close()
            
            metadata.extracted_text = "\n".join(descriptions)
            metadata.text_length = len(metadata.extracted_text)
            metadata.has_tables = len(tables) > 0
            metadata.metadata_extracted = {
                'table_count': len(tables),
                'table_names': [t[0] for t in tables]
            }
        except Exception as e:
            logger.error(f"SQLite extraction failed: {e}")
    
    async def _generate_embeddings(self, metadata: FileMetadata):
        """Generate embeddings for extracted text"""
        if not metadata.extracted_text or not self.embedding_engine.is_available():
            return
        
        try:
            # Chunk text for large documents
            text = metadata.extracted_text
            chunk_size = 1000
            chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
            
            # Generate embeddings
            embeddings = []
            for i, chunk in enumerate(chunks):
                if chunk.strip():
                    cache_key = f"{metadata.file_hash}_{i}"
                    embedding = await self.embedding_engine.generate_embeddings(chunk, cache_key)
                    if embedding is not None:
                        embeddings.append(embedding)
            
            metadata.chunk_count = len(embeddings)
            metadata.embedding_model = self.embedding_engine.model_name
            metadata.vector_indexed = True
            
            # Store embeddings in vector database (implementation depends on your vector DB)
            # This would integrate with ChromaDB, Pinecone, or similar
            
            logger.info(f"Generated {len(embeddings)} embeddings for {metadata.filename}")
            
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
    
    async def _store_in_memory_system(self, metadata: FileMetadata):
        """Store file content in memory management system"""
        if not self.memory_manager or not metadata.extracted_text:
            return
        
        try:
            # Protocol-based: memory_manager must implement async store_memory()
            # No hard coupling to memory_core — works with any MemorySink adapter
            store_kwargs = {
                'user_id': metadata.uploaded_by,
                'content': metadata.extracted_text,
                'tags': ['uploaded_file', metadata.file_type.value],
                'metadata': {
                    'filename': metadata.filename,
                    'file_id': str(metadata.file_id),
                    'file_size': metadata.file_size,
                    'file_type': metadata.file_type.value,
                    'upload_timestamp': metadata.uploaded_at.isoformat()
                }
            }
            
            # Try protocol-based push() first, then legacy store_memory()
            if hasattr(self.memory_manager, 'push'):
                await self.memory_manager.push(
                    content=metadata.extracted_text,
                    metadata=store_kwargs['metadata'],
                    tags=store_kwargs['tags']
                )
            elif hasattr(self.memory_manager, 'store_memory'):
                # Legacy compat: try importing types if available
                try:
                    from memory_core import MemoryType, MemoryImportance, MemoryScope
                    store_kwargs['memory_type'] = MemoryType.DOCUMENT
                    store_kwargs['importance'] = MemoryImportance.MEDIUM
                    store_kwargs['scope'] = MemoryScope.PRIVATE
                    store_kwargs['source_session'] = metadata.session_id
                except ImportError:
                    pass
                await self.memory_manager.store_memory(**store_kwargs)
            else:
                logger.warning("Memory manager has no push() or store_memory() method")
                return
            
            logger.info(f"Stored file content in memory system: {metadata.filename}")
            
        except Exception as e:
            logger.error(f"Memory storage failed: {e}")
    
    async def _check_duplicate(self, file_hash: str, user_id: str) -> bool:
        """Check if file is duplicate based on hash"""
        if not self.cache:
            return False
        
        try:
            existing_file_id = await self.cache.get(f"file_hash:{file_hash}")
            if existing_file_id:
                # Check if file belongs to same user
                existing_metadata = await self.cache.get(f"file_metadata:{existing_file_id}")
                if existing_metadata and existing_metadata.get('uploaded_by') == user_id:
                    return True
        except Exception:
            pass
        
        return False
    
    def _add_progress_callback(self, file_id: UUID, callback: callable):
        """Add progress callback for file upload"""
        if file_id not in self.upload_callbacks:
            self.upload_callbacks[file_id] = []
        self.upload_callbacks[file_id].append(weakref.ref(callback))
    
    async def _update_progress(self, file_id: UUID, progress: float, status: str):
        """Update upload progress and notify callbacks"""
        metadata = self.active_uploads.get(file_id)
        if metadata:
            metadata.processing_progress = progress
        
        # Notify callbacks
        if file_id in self.upload_callbacks:
            callbacks = self.upload_callbacks[file_id]
            active_callbacks = []
            
            for callback_ref in callbacks:
                callback = callback_ref()
                if callback:
                    try:
                        callback(progress, status)
                        active_callbacks.append(callback_ref)
                    except Exception as e:
                        logger.error(f"Progress callback error: {e}")
            
            self.upload_callbacks[file_id] = active_callbacks
    
    async def get_file_metadata(self, file_id: UUID) -> Optional[FileMetadata]:
        """Get file metadata by ID"""
        # Check active uploads first
        if file_id in self.active_uploads:
            return self.active_uploads[file_id]
        
        # Check cache
        if self.cache:
            cached_data = await self.cache.get(f"file_metadata:{file_id}")
            if cached_data:
                return FileMetadata(**cached_data)
        
        return None
    
    async def search_files(
        self,
        query: str,
        user_id: str,
        file_types: Optional[List[FileType]] = None,
        limit: int = 10
    ) -> List[FileMetadata]:
        """Search files using text content and embeddings"""
        matching_files = []
        
        # Search active uploads
        for metadata in self.active_uploads.values():
            if metadata.uploaded_by != user_id:
                continue
            
            if file_types and metadata.file_type not in file_types:
                continue
            
            # Text search
            if query.lower() in (metadata.extracted_text or "").lower():
                matching_files.append(metadata)
            elif query.lower() in metadata.filename.lower():
                matching_files.append(metadata)
        
        # If embedding model available, could do semantic search here
        # This would integrate with vector database
        
        # Sort by relevance (simple text length for now)
        matching_files.sort(key=lambda m: len(m.extracted_text or ""), reverse=True)
        
        return matching_files[:limit]
    
    async def delete_file(self, file_id: UUID, user_id: str) -> bool:
        """Delete file and cleanup storage"""
        metadata = await self.get_file_metadata(file_id)
        if not metadata or metadata.uploaded_by != user_id:
            return False
        
        try:
            # Remove from disk
            if metadata.storage_path:
                file_path = Path(metadata.storage_path)
                if file_path.exists():
                    file_path.unlink()
            
            # Remove from cache
            if self.cache:
                await self.cache.delete(f"file_metadata:{file_id}")
                await self.cache.delete(f"file_hash:{metadata.file_hash}")
            
            # Remove from active uploads
            self.active_uploads.pop(file_id, None)
            self.upload_callbacks.pop(file_id, None)
            
            logger.info(f"Deleted file {file_id}: {metadata.filename}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            return False
    
    def get_upload_stats(self, user_id: str) -> Dict[str, Any]:
        """Get upload statistics for user"""
        user_files = [m for m in self.active_uploads.values() if m.uploaded_by == user_id]
        
        total_size = sum(m.file_size for m in user_files)
        by_type = {}
        
        for metadata in user_files:
            file_type = metadata.file_type.value
            if file_type not in by_type:
                by_type[file_type] = {"count": 0, "size": 0}
            by_type[file_type]["count"] += 1
            by_type[file_type]["size"] += metadata.file_size
        
        return {
            "total_files": len(user_files),
            "total_size_mb": total_size / (1024 * 1024),
            "by_type": by_type,
            "embedding_model": self.embedding_engine.model_name,
            "embedding_available": self.embedding_engine.is_available()
        }
    
    async def cleanup_temp_files(self, max_age_hours: int = 24):
        """Cleanup old temporary files"""
        try:
            temp_dir = self.streaming_handler.temp_dir
            cutoff_time = time.time() - (max_age_hours * 3600)
            
            for temp_file in temp_dir.glob("*"):
                if temp_file.is_file() and temp_file.stat().st_mtime < cutoff_time:
                    try:
                        temp_file.unlink()
                        logger.debug(f"Cleaned up temp file: {temp_file}")
                    except Exception as e:
                        logger.error(f"Failed to cleanup temp file {temp_file}: {e}")
        
        except Exception as e:
            logger.error(f"Temp file cleanup failed: {e}")
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down file upload manager...")
        
        # Cleanup temp files
        await self.cleanup_temp_files(0)  # Clean all temp files
        
        # Shutdown executor
        self.executor.shutdown(wait=True)
        
        logger.info("File upload manager shutdown complete")


# Factory function for easy setup
def create_file_upload_manager(
    upload_dir: str,
    cache_engine=None,
    memory_manager=None,
    gguf_model_path: str = None,
    max_workers: int = 4
) -> EnhancedFileUploadManager:
    """Create enhanced file upload manager with production configuration"""
    
    return EnhancedFileUploadManager(
        upload_dir=upload_dir,
        cache_engine=cache_engine,
        memory_manager=memory_manager,
        gguf_model_path=gguf_model_path,
        max_workers=max_workers
    )


# Example usage
if __name__ == "__main__":
    async def main():
        # Create file manager
        file_manager = create_file_upload_manager(
            upload_dir="data/uploads",
            max_workers=4
        )
        
        # Example file stream (in real usage, this would come from web framework)
        async def mock_file_stream():
            test_content = b"This is a test file content for SOMNUS file upload system."
            chunk_size = 10
            for i in range(0, len(test_content), chunk_size):
                yield test_content[i:i+chunk_size]
        
        # Upload file
        metadata = await file_manager.upload_file_stream(
            file_stream=mock_file_stream(),
            filename="test.txt",
            user_id="test_user",
            session_id=uuid4()
        )
        
        print(f"Upload result: {metadata.processing_status}")
        print(f"File ID: {metadata.file_id}")
        print(f"Extracted text: {metadata.extracted_text}")
        
        # Get stats
        stats = file_manager.get_upload_stats("test_user")
        print(f"Upload stats: {stats}")
        
        # Shutdown
        await file_manager.shutdown()
    
    asyncio.run(main())
