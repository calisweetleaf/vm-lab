#!/usr/bin/env python3
"""
Compatibility facade for the canonical semantic chunking engine.

`semantic_chunking_rewrite.py` is the real implementation.
This module preserves the stable import path that the rest of the file processor
stack should use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor

from universal_file_processors import (
    BaseFileProcessor,
    ProcessingCapabilities,
    ProcessingResult,
)
import semantic_chunking_rewrite as advanced


ContentType = advanced.ContentType
SemanticModel = advanced.SemanticModel
InputValidationError = advanced.InputValidationError
ProcessingTimeoutError = advanced.ProcessingTimeoutError
ProductionLimits = advanced.ProductionLimits
validate_text_input = advanced.validate_text_input
safe_regex_search = advanced.safe_regex_search
safe_regex_findall = advanced.safe_regex_findall
create_chunking_config_for_content_type = advanced.create_chunking_config_for_content_type


class ChunkingStrategy(str, Enum):
    """Stable strategy names kept for older callers."""

    FIXED_SIZE = "fixed_size"
    SENTENCE_BOUNDARY = "sentence_boundary"
    PARAGRAPH_BOUNDARY = "paragraph_boundary"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    STRUCTURAL = "structural"
    HYBRID = "hybrid"
    CONTENT_AWARE = "content_aware"

    def to_advanced(self) -> advanced.ChunkingStrategy:
        mapping = {
            ChunkingStrategy.FIXED_SIZE: advanced.ChunkingStrategy.FIXED_SIZE,
            ChunkingStrategy.SENTENCE_BOUNDARY: advanced.ChunkingStrategy.SENTENCE_BOUNDARY,
            ChunkingStrategy.PARAGRAPH_BOUNDARY: advanced.ChunkingStrategy.PARAGRAPH_BOUNDARY,
            ChunkingStrategy.SEMANTIC_SIMILARITY: advanced.ChunkingStrategy.SEMANTIC_SIMILARITY,
            ChunkingStrategy.STRUCTURAL: advanced.ChunkingStrategy.STRUCTURAL,
            ChunkingStrategy.HYBRID: advanced.ChunkingStrategy.HYBRID_ADVANCED,
            ChunkingStrategy.CONTENT_AWARE: advanced.ChunkingStrategy.HYBRID_ADVANCED,
        }
        return mapping[self]


@dataclass
class ProcessingContext:
    """Backwards-compatible processing context."""

    task_id: str
    session_id: str
    user_id: str
    file_path: Optional[Path] = None
    progress_callback: Optional[Callable[[float, str], None]] = None
    security_orchestrator: Optional[Any] = None
    cache_manager: Optional[Any] = None
    memory_manager: Optional[Any] = None
    artifact_builder: Optional[Any] = None
    current_stage: str = "initializing"
    progress_percent: float = 0.0
    start_time: float = field(default_factory=time.time)

    def update_progress(self, stage: str, percent: float, message: str = "") -> None:
        self.current_stage = stage
        self.progress_percent = percent
        if self.progress_callback:
            self.progress_callback(percent, f"{stage}: {message}")

    def log_error(self, message: str) -> None:
        raise RuntimeError(message)

    def to_advanced(self) -> advanced.ProcessingContext:
        return advanced.ProcessingContext(
            task_id=self.task_id,
            session_id=self.session_id,
            user_id=self.user_id,
            file_path=self.file_path,
            progress_callback=self.progress_callback,
            security_orchestrator=self.security_orchestrator,
            cache_manager=self.cache_manager,
            memory_manager=self.memory_manager,
            artifact_builder=self.artifact_builder,
            current_stage=self.current_stage,
            progress_percent=self.progress_percent,
            start_time=self.start_time,
        )


@dataclass
class ChunkingConfig:
    """Legacy config shape that maps cleanly onto the advanced config."""

    target_chunk_size: int = 1000
    max_chunk_size: int = 1500
    min_chunk_size: int = 200
    overlap_size: int = 200
    overlap_percentage: float = 0.15
    strategy: ChunkingStrategy = ChunkingStrategy.CONTENT_AWARE
    content_type: ContentType = ContentType.PLAIN_TEXT
    similarity_threshold: float = 0.7
    preserve_code_blocks: bool = True
    preserve_tables: bool = True
    preserve_lists: bool = True
    min_sentences_per_chunk: int = 2
    semantic_model: SemanticModel = SemanticModel.TFIDF_COSINE

    def to_advanced(self) -> advanced.ChunkingConfig:
        return advanced.ChunkingConfig(
            target_chunk_size=self.target_chunk_size,
            max_chunk_size=max(self.max_chunk_size, self.target_chunk_size),
            min_chunk_size=self.min_chunk_size,
            overlap_size=self.overlap_size,
            overlap_percentage=self.overlap_percentage,
            strategy=self.strategy.to_advanced(),
            content_type=self.content_type,
            semantic_model=self.semantic_model,
            similarity_threshold=self.similarity_threshold,
            min_sentences_per_chunk=self.min_sentences_per_chunk,
        )


SemanticChunk = advanced.SemanticChunk
SemanticChunk.char_count = property(lambda self: self.character_count)
SemanticChunk.topics = property(
    lambda self: [
        topic
        for topic, _score in sorted(
            self.topic_distribution.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
)


class SemanticChunkingProcessor(BaseFileProcessor):
    """
    Stable processor surface backed by the advanced implementation.

    The old and rewrite chunkers are now one path: callers import this module,
    which delegates to `semantic_chunking_rewrite.py`.
    """

    def __init__(self, capabilities: ProcessingCapabilities, executor: ThreadPoolExecutor):
        super().__init__(capabilities, executor)
        self.processor_name = "SemanticChunkingProcessor"
        self._engine = advanced.SemanticChunkingProcessor(capabilities, executor)

    def get_supported_extensions(self) -> Set[str]:
        return self._engine.get_supported_extensions()

    def can_process(self, file_path: Path, mime_type: str) -> bool:
        return self._engine.can_process(file_path, mime_type)

    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        return await self._engine.process_file(file_path, metadata)

    async def chunk_text(self, raw_text: str, context: ProcessingContext) -> ProcessingResult:
        advanced_context = context if isinstance(context, advanced.ProcessingContext) else context.to_advanced()
        return await self._engine.chunk_text(raw_text, advanced_context)


def get_semantic_chunking_processor(
    capabilities: ProcessingCapabilities = None,
    executor: ThreadPoolExecutor = None,
) -> SemanticChunkingProcessor:
    if capabilities is None:
        capabilities = ProcessingCapabilities()

    if executor is None:
        executor = ThreadPoolExecutor(max_workers=4)

    return SemanticChunkingProcessor(capabilities, executor)

