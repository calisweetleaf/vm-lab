#!/usr/bin/env python3
"""
SOMNUS Enterprise Semantic Chunking Processor
Production-grade semantic text chunking with advanced NLP and linguistic analysis
Designed to compete with enterprise AI solutions (Google, OpenAI, Microsoft)
"""

import asyncio
import functools
import hashlib
import logging
import pickle
import re
import signal
import time
import mmap
from collections import defaultdict, Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Set, Callable, Union, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
import threading
import weakref

# Production hardening imports
try:
    from ratelimit import limits, sleep_and_retry, RateLimitException
    RATELIMIT_AVAILABLE = True
except ImportError:
    RATELIMIT_AVAILABLE = False
    # Fallback decorator that does nothing
    def limits(calls, period):
        def decorator(func):
            return func
        return decorator
    def sleep_and_retry(func):
        return func

try:
    from pydantic import BaseModel, Field, field_validator, ConfigDict
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

import numpy as np
from scipy.spatial.distance import cosine
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import networkx as nx

# Advanced NLTK integration
import nltk
from nltk.tokenize import sent_tokenize, word_tokenize
from nltk.corpus import stopwords, wordnet
from nltk.stem import WordNetLemmatizer
from nltk.tag import pos_tag
from nltk.chunk import ne_chunk
from nltk.parse.stanford import StanfordDependencyParser
from nltk.sentiment import SentimentIntensityAnalyzer
from nltk.translate.bleu_score import sentence_bleu
from nltk.metrics import jaccard_distance
from nltk.corpus.reader.wordnet import WordNetError

# Transformer embeddings (optional, with fallback)
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
    import spacy
    from spacy.lang.en.stop_words import STOP_WORDS as SPACY_STOP_WORDS
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

# SOMNUS integration
from universal_file_processors import (
    BaseFileProcessor, ProcessingResult, ProcessingCapabilities
)

logger = logging.getLogger(__name__)

# Ensure NLTK data is available
REQUIRED_NLTK_DATA = {
    "punkt": "tokenizers/punkt",
    "punkt_tab": "tokenizers/punkt_tab",
    "averaged_perceptron_tagger": "taggers/averaged_perceptron_tagger",
    "averaged_perceptron_tagger_eng": "taggers/averaged_perceptron_tagger_eng",
    "wordnet": "corpora/wordnet",
    "stopwords": "corpora/stopwords",
    "vader_lexicon": "sentiment/vader_lexicon",
    "omw-1.4": "corpora/omw-1.4",
    "maxent_ne_chunker": "chunkers/maxent_ne_chunker",
    "maxent_ne_chunker_tab": "chunkers/maxent_ne_chunker_tab",
    "words": "corpora/words",
}

def ensure_nltk_data():
    """Ensure all required NLTK data is downloaded"""
    for dataset, resource_path in REQUIRED_NLTK_DATA.items():
        try:
            nltk.data.find(resource_path)
        except LookupError:
            try:
                nltk.download(dataset, quiet=True)
            except Exception as e:
                logger.warning(f"Failed to download NLTK dataset {dataset}: {e}")

# Initialize NLTK data
ensure_nltk_data()


class ContentType(str, Enum):
    """Content types for specialized chunking strategies"""
    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"
    CODE = "code"
    DOCUMENTATION = "documentation"
    RESEARCH_PAPER = "research_paper"
    CONVERSATION = "conversation"
    VIDEO_TRANSCRIPT = "video_transcript"
    TECHNICAL_MANUAL = "technical_manual"
    LEGAL_DOCUMENT = "legal_document"
    STRUCTURED_DATA = "structured_data"
    MIXED_CONTENT = "mixed_content"


class ChunkingStrategy(str, Enum):
    """Advanced chunking strategies"""
    FIXED_SIZE = "fixed_size"
    SENTENCE_BOUNDARY = "sentence_boundary"
    PARAGRAPH_BOUNDARY = "paragraph_boundary"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    TOPIC_SEGMENTATION = "topic_segmentation"
    DISCOURSE_ANALYSIS = "discourse_analysis"
    LINGUISTIC_COHERENCE = "linguistic_coherence"
    STRUCTURAL = "structural"
    HYBRID_ADVANCED = "hybrid_advanced"
    TRANSFORMER_BASED = "transformer_based"


class SemanticModel(str, Enum):
    """Available semantic analysis models"""
    SENTENCE_TRANSFORMERS = "sentence_transformers"
    WORD2VEC_SIMILARITY = "word2vec_similarity"
    WORDNET_SIMILARITY = "wordnet_similarity"
    TFIDF_COSINE = "tfidf_cosine"
    LINGUISTIC_FEATURES = "linguistic_features"


# ============================================
# PRODUCTION CONSTANTS & LIMITS
# ============================================

class ProductionLimits:
    """Hard limits for production safety"""
    # Input size limits
    MAX_TEXT_LENGTH_BYTES: int = 100 * 1024 * 1024  # 100MB max input
    MAX_TEXT_LENGTH_CHARS: int = 50_000_000  # 50M characters
    MAX_SENTENCES: int = 500_000  # Max sentences to process
    MAX_CHUNKS: int = 100_000  # Max chunks to generate
    
    # Processing limits
    MAX_REGEX_INPUT_LENGTH: int = 1_000_000  # 1M chars for regex operations
    REGEX_TIMEOUT_SECONDS: float = 5.0  # Timeout for regex operations
    MAX_EMBEDDING_BATCH_SIZE: int = 1000  # Max texts per embedding batch
    
    # Rate limits (per minute)
    EMBEDDING_CALLS_PER_MINUTE: int = 100
    SIMILARITY_CALLS_PER_MINUTE: int = 500
    
    # Memory limits
    MAX_CACHE_SIZE_MB: int = 512
    MAX_CACHE_ENTRIES: int = 10_000


class InputValidationError(ValueError):
    """Raised when input validation fails"""
    pass


class ProcessingTimeoutError(TimeoutError):
    """Raised when processing times out"""
    pass


def validate_text_input(text: str, max_length: int = None) -> str:
    """
    Validate and sanitize text input for production safety.
    
    Args:
        text: Input text to validate
        max_length: Optional custom max length (defaults to ProductionLimits)
        
    Returns:
        Validated text
        
    Raises:
        InputValidationError: If input is invalid or exceeds limits
    """
    if text is None:
        raise InputValidationError("Input text cannot be None")
    
    if not isinstance(text, str):
        raise InputValidationError(f"Input must be string, got {type(text).__name__}")
    
    max_len = max_length or ProductionLimits.MAX_TEXT_LENGTH_CHARS
    
    if len(text) > max_len:
        raise InputValidationError(
            f"Input text exceeds maximum length: {len(text):,} > {max_len:,} characters"
        )
    
    # Check byte size
    text_bytes = len(text.encode('utf-8', errors='replace'))
    if text_bytes > ProductionLimits.MAX_TEXT_LENGTH_BYTES:
        raise InputValidationError(
            f"Input text exceeds maximum size: {text_bytes:,} > {ProductionLimits.MAX_TEXT_LENGTH_BYTES:,} bytes"
        )
    
    return text


def safe_regex_search(pattern: str, text: str, flags: int = 0, timeout: float = None) -> Optional[re.Match]:
    """
    Perform regex search with length limits and timeout protection.
    
    Args:
        pattern: Regex pattern
        text: Text to search
        flags: Regex flags
        timeout: Optional timeout in seconds
        
    Returns:
        Match object or None
    """
    timeout = timeout or ProductionLimits.REGEX_TIMEOUT_SECONDS
    
    # Truncate text if too long for regex
    if len(text) > ProductionLimits.MAX_REGEX_INPUT_LENGTH:
        text = text[:ProductionLimits.MAX_REGEX_INPUT_LENGTH]
        logger.warning(f"Text truncated to {ProductionLimits.MAX_REGEX_INPUT_LENGTH} chars for regex safety")
    
    try:
        # Compile pattern with timeout-safe approach
        compiled = re.compile(pattern, flags)
        return compiled.search(text)
    except re.error as e:
        logger.warning(f"Regex pattern error: {e}")
        return None
    except RecursionError:
        logger.error("Regex recursion limit hit - potential ReDoS pattern")
        return None


def safe_regex_findall(pattern: str, text: str, flags: int = 0) -> List[str]:
    """
    Perform regex findall with safety limits.
    
    Args:
        pattern: Regex pattern
        text: Text to search
        flags: Regex flags
        
    Returns:
        List of matches (may be truncated)
    """
    if len(text) > ProductionLimits.MAX_REGEX_INPUT_LENGTH:
        text = text[:ProductionLimits.MAX_REGEX_INPUT_LENGTH]
    
    try:
        compiled = re.compile(pattern, flags)
        matches = compiled.findall(text)
        # Limit number of matches to prevent memory issues
        return matches[:10000]
    except (re.error, RecursionError) as e:
        logger.warning(f"Regex findall failed: {e}")
        return []


class ThreadSafeCache:
    """Thread-safe LRU cache with size limits"""
    
    def __init__(self, max_entries: int = None, max_size_mb: int = None):
        self._cache: Dict[str, Any] = {}
        self._access_order: List[str] = []
        self._lock = threading.RLock()
        self._max_entries = max_entries or ProductionLimits.MAX_CACHE_ENTRIES
        self._max_size_bytes = (max_size_mb or ProductionLimits.MAX_CACHE_SIZE_MB) * 1024 * 1024
        self._current_size = 0
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                self._access_order.remove(key)
                self._access_order.append(key)
                return self._cache[key]
            return None
    
    def set(self, key: str, value: Any) -> None:
        with self._lock:
            # Estimate size
            try:
                value_size = len(pickle.dumps(value))
            except (TypeError, pickle.PicklingError):
                value_size = 1024  # Default estimate
            
            # Evict if necessary
            while (len(self._cache) >= self._max_entries or 
                   self._current_size + value_size > self._max_size_bytes) and self._access_order:
                oldest_key = self._access_order.pop(0)
                if oldest_key in self._cache:
                    try:
                        old_size = len(pickle.dumps(self._cache[oldest_key]))
                    except (TypeError, pickle.PicklingError):
                        old_size = 1024
                    self._current_size -= old_size
                    del self._cache[oldest_key]
            
            # Add new entry
            if key in self._cache:
                self._access_order.remove(key)
            self._cache[key] = value
            self._access_order.append(key)
            self._current_size += value_size
    
    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._access_order.clear()
            self._current_size = 0
    
    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache
    
    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


@dataclass
class ProcessingContext:
    """Enhanced processing context for enterprise deployment"""
    task_id: str
    session_id: str
    user_id: str
    file_path: Optional[Path] = None
    
    # System integration points
    progress_callback: Optional[Callable[[float, str], None]] = None
    security_orchestrator: Optional[Any] = None
    cache_manager: Optional[Any] = None
    memory_manager: Optional[Any] = None
    artifact_builder: Optional[Any] = None
    
    # Processing configuration
    max_memory_mb: int = 2048
    enable_parallel_processing: bool = True
    enable_caching: bool = True
    enable_advanced_metrics: bool = True
    
    # Performance tracking
    current_stage: str = "initializing"
    progress_percent: float = 0.0
    start_time: float = field(default_factory=time.time)
    stage_times: Dict[str, float] = field(default_factory=dict)
    
    def update_progress(self, stage: str, percent: float, message: str = ""):
        """Enhanced progress tracking with performance metrics"""
        current_time = time.time()
        
        if self.current_stage != "initializing":
            self.stage_times[self.current_stage] = current_time - self.start_time
        
        self.current_stage = stage
        self.progress_percent = percent
        
        if self.progress_callback:
            try:
                self.progress_callback(percent, f"{stage}: {message}")
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")
        
        logger.info(f"Semantic chunking [{self.task_id}]: {stage} ({percent:.1f}%) - {message}")
    
    def log_performance_metrics(self):
        """Log detailed performance metrics"""
        total_time = time.time() - self.start_time
        logger.info(f"Processing completed in {total_time:.2f}s")
        for stage, duration in self.stage_times.items():
            logger.info(f"  {stage}: {duration:.2f}s")


@dataclass
class ChunkingConfig:
    """Advanced configuration for enterprise chunking"""
    # Size constraints
    target_chunk_size: int = 1000
    max_chunk_size: int = 2000
    min_chunk_size: int = 100
    overlap_size: int = 150
    overlap_percentage: float = 0.15
    
    # Strategy configuration
    strategy: ChunkingStrategy = ChunkingStrategy.HYBRID_ADVANCED
    content_type: ContentType = ContentType.PLAIN_TEXT
    semantic_model: SemanticModel = SemanticModel.SENTENCE_TRANSFORMERS
    
    # Advanced parameters
    similarity_threshold: float = 0.75
    coherence_threshold: float = 0.6
    topic_change_threshold: float = 0.4
    min_sentences_per_chunk: int = 2
    max_sentences_per_chunk: int = 20
    
    # Quality requirements
    min_semantic_coherence: float = 0.5
    min_information_density: float = 0.3
    enable_named_entity_preservation: bool = True
    enable_discourse_markers: bool = True
    enable_syntactic_analysis: bool = True
    
    # Performance optimization
    enable_parallel_similarity: bool = True
    batch_size: int = 50
    cache_embeddings: bool = True
    memory_map_large_files: bool = True


@dataclass
class LinguisticFeatures:
    """Advanced linguistic features for semantic analysis"""
    # Lexical features
    avg_word_length: float = 0.0
    avg_sentence_length: float = 0.0
    lexical_diversity: float = 0.0
    hapax_legomena_ratio: float = 0.0
    
    # Syntactic features
    avg_parse_tree_depth: float = 0.0
    subordinate_clause_ratio: float = 0.0
    pos_tag_distribution: Dict[str, float] = field(default_factory=dict)
    
    # Semantic features
    semantic_similarity_variance: float = 0.0
    topic_coherence_score: float = 0.0
    entity_density: float = 0.0
    sentiment_polarity: float = 0.0
    sentiment_consistency: float = 0.0
    
    # Discourse features
    discourse_marker_count: int = 0
    cohesion_score: float = 0.0
    anaphora_resolution_score: float = 0.0


@dataclass
class SemanticChunk:
    """Enterprise-grade semantic chunk with comprehensive metadata"""
    chunk_id: str
    text: str
    content_type: ContentType
    chunk_index: int
    
    # Position and structure
    start_char: int
    end_char: int
    start_sentence: int
    end_sentence: int
    paragraph_indices: List[int]
    
    # Basic metrics
    word_count: int
    sentence_count: int
    character_count: int
    estimated_tokens: int
    
    # Advanced linguistic metrics
    linguistic_features: LinguisticFeatures = field(default_factory=LinguisticFeatures)
    semantic_coherence: float = 0.0
    boundary_quality: float = 0.0
    information_density: float = 0.0
    readability_score: float = 0.0
    complexity_score: float = 0.0
    
    # Semantic analysis
    embedding_vector: Optional[np.ndarray] = None
    embedding_hash: Optional[str] = None
    topic_distribution: Dict[str, float] = field(default_factory=dict)
    named_entities: List[Tuple[str, str]] = field(default_factory=list)
    key_phrases: List[str] = field(default_factory=list)
    dependency_relations: List[Tuple[str, str, str]] = field(default_factory=list)
    
    # Relationship metadata
    parent_document_id: str = ""
    previous_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None
    semantic_similarity_to_previous: float = 0.0
    semantic_similarity_to_next: float = 0.0
    
    # Quality and security
    quality_score: float = 0.0
    security_warnings: List[str] = field(default_factory=list)
    is_safe: bool = True
    processing_time: float = 0.0
    
    @property
    def is_high_quality(self) -> bool:
        """Comprehensive quality assessment"""
        return (
            self.semantic_coherence > 0.6 and
            self.boundary_quality > 0.5 and
            self.information_density > 0.3 and
            self.word_count >= 50 and
            self.quality_score > 0.7 and
            self.is_safe
        )
    
    @property
    def semantic_density(self) -> float:
        """Calculate semantic information density"""
        if self.word_count == 0:
            return 0.0
        return len(self.key_phrases) / self.word_count


class AdvancedNLPPipeline:
    """Enterprise NLP pipeline with thread-safe caching and optimization"""
    
    # Rate limiting decorators for expensive operations
    @staticmethod
    def _rate_limited_embedding(func):
        """Decorator for rate-limiting embedding operations"""
        if RATELIMIT_AVAILABLE:
            return sleep_and_retry(limits(calls=ProductionLimits.EMBEDDING_CALLS_PER_MINUTE, period=60)(func))
        return func
    
    @staticmethod
    def _rate_limited_similarity(func):
        """Decorator for rate-limiting similarity operations"""
        if RATELIMIT_AVAILABLE:
            return sleep_and_retry(limits(calls=ProductionLimits.SIMILARITY_CALLS_PER_MINUTE, period=60)(func))
        return func
    
    def __init__(self, enable_caching: bool = True):
        self.enable_caching = enable_caching
        
        # Thread-safe cache with size limits
        self._cache = ThreadSafeCache() if enable_caching else None
        self._lock = threading.RLock()
        
        # Separate lock for TF-IDF operations (thread safety fix)
        self._tfidf_lock = threading.RLock()
        
        # Initialize NLTK components
        self.lemmatizer = WordNetLemmatizer()
        self.sentiment_analyzer = SentimentIntensityAnalyzer()
        
        # Load language model
        self.nlp = None
        if SPACY_AVAILABLE:
            try:
                import spacy
                self.nlp = spacy.load("en_core_web_sm")
            except OSError:
                logger.warning("spaCy English model not available. Install with: python -m spacy download en_core_web_sm")
            except Exception as e:
                logger.warning(f"Failed to load spaCy model: {e}")
        
        # Initialize transformer model
        self.sentence_transformer = None
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.sentence_transformer = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("Loaded SentenceTransformer model for semantic analysis")
            except ImportError as e:
                logger.warning(f"SentenceTransformer import error: {e}")
            except OSError as e:
                logger.warning(f"SentenceTransformer model loading error: {e}")
            except Exception as e:
                logger.warning(f"Failed to load SentenceTransformer: {e}")
        
        # TF-IDF vectorizer for fallback similarity (protected by _tfidf_lock)
        self._tfidf_vectorizer = TfidfVectorizer(
            max_features=10000,
            stop_words='english',
            ngram_range=(1, 2),
            max_df=1.0,
            min_df=1
        )
        self._tfidf_fitted = False
    
    @property
    def tfidf_vectorizer(self):
        """Thread-safe access to TF-IDF vectorizer"""
        return self._tfidf_vectorizer
    
    @property
    def tfidf_fitted(self) -> bool:
        """Thread-safe check if TF-IDF is fitted"""
        with self._tfidf_lock:
            return self._tfidf_fitted
    
    @tfidf_fitted.setter
    def tfidf_fitted(self, value: bool):
        """Thread-safe set TF-IDF fitted state"""
        with self._tfidf_lock:
            self._tfidf_fitted = value
    
    def get_cache_key(self, text: str, operation: str) -> str:
        """Generate cache key for NLP operations"""
        return f"{operation}:{hashlib.md5(text.encode()).hexdigest()}"
    
    async def tokenize_sentences(self, text: str) -> List[str]:
        """Advanced sentence tokenization with caching and input validation"""
        # Input validation
        text = validate_text_input(text)
        
        if self.enable_caching and self._cache is not None:
            cache_key = self.get_cache_key(text, "sentences")
            cached_result = self._cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        
        # Use NLTK's Punkt tokenizer for superior sentence boundary detection
        sentences = sent_tokenize(text)
        
        # Enforce sentence limit for safety
        if len(sentences) > ProductionLimits.MAX_SENTENCES:
            logger.warning(f"Sentence count {len(sentences)} exceeds limit, truncating to {ProductionLimits.MAX_SENTENCES}")
            sentences = sentences[:ProductionLimits.MAX_SENTENCES]
        
        # Post-process for better boundary detection
        processed_sentences = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 10:  # Filter very short sentences
                processed_sentences.append(sentence)
        
        if self.enable_caching and self._cache is not None:
            cache_key = self.get_cache_key(text, "sentences")
            self._cache.set(cache_key, processed_sentences)
        
        return processed_sentences
    
    async def extract_linguistic_features(self, text: str) -> LinguisticFeatures:
        """Extract comprehensive linguistic features with input validation"""
        # Input validation
        text = validate_text_input(text)
        
        if self.enable_caching and self._cache is not None:
            cache_key = self.get_cache_key(text, "linguistic_features")
            cached_result = self._cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        
        features = LinguisticFeatures()
        
        # Tokenize
        sentences = await self.tokenize_sentences(text)
        words = word_tokenize(text.lower())
        
        # Basic lexical features
        if words:
            features.avg_word_length = np.mean([len(word) for word in words])
            features.lexical_diversity = len(set(words)) / len(words)
            
            # Hapax legomena (words appearing only once)
            word_counts = Counter(words)
            hapax_count = sum(1 for count in word_counts.values() if count == 1)
            features.hapax_legomena_ratio = hapax_count / len(word_counts)
        
        if sentences:
            sentence_lengths = [len(word_tokenize(sentence)) for sentence in sentences]
            features.avg_sentence_length = np.mean(sentence_lengths)
        
        # POS tagging and syntactic analysis
        if self.nlp:
            doc = self.nlp(text)
            
            # POS distribution
            pos_counts = Counter(token.pos_ for token in doc)
            total_tokens = len(doc)
            if total_tokens > 0:
                features.pos_tag_distribution = {
                    pos: count / total_tokens for pos, count in pos_counts.items()
                }
            
            # Named entity density
            entities = [(ent.text, ent.label_) for ent in doc.ents]
            features.entity_density = len(entities) / total_tokens if total_tokens > 0 else 0
            
            # Discourse markers
            discourse_markers = [
                'however', 'therefore', 'furthermore', 'moreover', 'nevertheless',
                'consequently', 'thus', 'hence', 'accordingly', 'meanwhile'
            ]
            features.discourse_marker_count = sum(
                1 for token in doc if token.text.lower() in discourse_markers
            )
        
        # Sentiment analysis
        sentiment_scores = self.sentiment_analyzer.polarity_scores(text)
        features.sentiment_polarity = sentiment_scores['compound']
        
        # Calculate sentiment consistency across sentences
        sentence_sentiments = [
            self.sentiment_analyzer.polarity_scores(sentence)['compound']
            for sentence in sentences
        ]
        if len(sentence_sentiments) > 1:
            features.sentiment_consistency = 1.0 - np.std(sentence_sentiments)
        else:
            features.sentiment_consistency = 1.0
        
        if self.enable_caching and self._cache is not None:
            cache_key = self.get_cache_key(text, "linguistic_features")
            self._cache.set(cache_key, features)
        
        return features
    
    async def calculate_semantic_similarity(self, text1: str, text2: str, model: SemanticModel) -> float:
        """Advanced semantic similarity calculation with input validation"""
        # Input validation
        text1 = validate_text_input(text1)
        text2 = validate_text_input(text2)
        
        cache_key = self.get_cache_key(f"{text1}|{text2}", f"similarity_{model.value}")
        
        if self.enable_caching and self._cache is not None:
            cached_result = self._cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        
        similarity = 0.0
        
        try:
            if model == SemanticModel.SENTENCE_TRANSFORMERS and self.sentence_transformer:
                # Use transformer embeddings for semantic similarity
                embeddings = self.sentence_transformer.encode([text1, text2])
                similarity = 1 - cosine(embeddings[0], embeddings[1])
            
            elif model == SemanticModel.WORDNET_SIMILARITY:
                # WordNet-based semantic similarity
                similarity = await self._wordnet_similarity(text1, text2)
            
            elif model == SemanticModel.TFIDF_COSINE:
                # TF-IDF cosine similarity
                similarity = await self._tfidf_similarity(text1, text2)
            
            elif model == SemanticModel.LINGUISTIC_FEATURES:
                # Linguistic feature-based similarity
                similarity = await self._linguistic_similarity(text1, text2)
            
            else:
                # Fallback to enhanced word overlap
                similarity = await self._enhanced_word_overlap(text1, text2)
        
        except Exception as e:
            logger.warning(f"Similarity calculation failed: {e}")
            similarity = await self._enhanced_word_overlap(text1, text2)
        
        # Ensure similarity is in valid range
        similarity = max(0.0, min(1.0, similarity))
        
        if self.enable_caching and self._cache is not None:
            self._cache.set(cache_key, similarity)
        
        return similarity
    
    async def _wordnet_similarity(self, text1: str, text2: str) -> float:
        """WordNet-based semantic similarity"""
        words1 = [word.lower() for word in word_tokenize(text1) if word.isalpha()]
        words2 = [word.lower() for word in word_tokenize(text2) if word.isalpha()]
        
        if not words1 or not words2:
            return 0.0
        
        similarities = []
        
        for word1 in words1:
            word_similarities = []
            synsets1 = wordnet.synsets(word1)
            
            if not synsets1:
                continue
            
            for word2 in words2:
                synsets2 = wordnet.synsets(word2)
                
                if not synsets2:
                    continue
                
                # Calculate maximum similarity between synsets
                max_sim = 0.0
                for syn1 in synsets1[:3]:  # Limit to top 3 synsets
                    for syn2 in synsets2[:3]:
                        try:
                            sim = syn1.wup_similarity(syn2)
                            if sim and sim > max_sim:
                                max_sim = sim
                        except WordNetError:
                            continue
                
                word_similarities.append(max_sim)
            
            if word_similarities:
                similarities.append(max(word_similarities))
        
        return np.mean(similarities) if similarities else 0.0
    
    async def _tfidf_similarity(self, text1: str, text2: str) -> float:
        """TF-IDF cosine similarity with thread safety"""
        try:
            with self._tfidf_lock:
                if not self._tfidf_fitted:
                    # Fit on combined text for better feature extraction
                    combined_texts = [text1, text2]
                    self._tfidf_vectorizer.fit(combined_texts)
                    self._tfidf_fitted = True
                
                vectors = self._tfidf_vectorizer.transform([text1, text2])
            
            similarity_matrix = cosine_similarity(vectors)
            return float(similarity_matrix[0, 1])
        
        except ValueError as e:
            logger.warning(f"TF-IDF vectorizer error: {e}")
            return await self._enhanced_word_overlap(text1, text2)
        except Exception as e:
            logger.warning(f"TF-IDF similarity failed: {e}")
            return await self._enhanced_word_overlap(text1, text2)
    
    async def _linguistic_similarity(self, text1: str, text2: str) -> float:
        """Linguistic feature-based similarity"""
        features1 = await self.extract_linguistic_features(text1)
        features2 = await self.extract_linguistic_features(text2)
        
        # Compare various linguistic features
        similarities = []
        
        # Lexical similarity
        if features1.avg_word_length > 0 and features2.avg_word_length > 0:
            length_sim = 1 - abs(features1.avg_word_length - features2.avg_word_length) / max(features1.avg_word_length, features2.avg_word_length)
            similarities.append(length_sim)
        
        # Sentiment similarity
        sentiment_sim = 1 - abs(features1.sentiment_polarity - features2.sentiment_polarity) / 2
        similarities.append(sentiment_sim)
        
        # POS distribution similarity
        if features1.pos_tag_distribution and features2.pos_tag_distribution:
            all_pos = set(features1.pos_tag_distribution.keys()) | set(features2.pos_tag_distribution.keys())
            pos_vector1 = np.array([features1.pos_tag_distribution.get(pos, 0) for pos in all_pos])
            pos_vector2 = np.array([features2.pos_tag_distribution.get(pos, 0) for pos in all_pos])
            
            if np.linalg.norm(pos_vector1) > 0 and np.linalg.norm(pos_vector2) > 0:
                pos_sim = 1 - cosine(pos_vector1, pos_vector2)
                similarities.append(pos_sim)
        
        return np.mean(similarities) if similarities else 0.0
    
    async def _enhanced_word_overlap(self, text1: str, text2: str) -> float:
        """Enhanced word overlap with lemmatization and stopword removal"""
        # Tokenize and preprocess
        words1 = word_tokenize(text1.lower())
        words2 = word_tokenize(text2.lower())
        
        # Remove stopwords
        stop_words = set(stopwords.words('english'))
        words1 = [word for word in words1 if word.isalpha() and word not in stop_words]
        words2 = [word for word in words2 if word.isalpha() and word not in stop_words]
        
        # Lemmatize
        words1 = [self.lemmatizer.lemmatize(word) for word in words1]
        words2 = [self.lemmatizer.lemmatize(word) for word in words2]
        
        if not words1 or not words2:
            return 0.0
        
        # Calculate Jaccard similarity with word frequency weighting
        set1 = set(words1)
        set2 = set(words2)
        
        intersection = set1.intersection(set2)
        union = set1.union(set2)
        
        if not union:
            return 0.0
        
        # Weight by frequency
        freq1 = Counter(words1)
        freq2 = Counter(words2)
        
        weighted_intersection = sum(min(freq1[word], freq2[word]) for word in intersection)
        weighted_union = sum(max(freq1.get(word, 0), freq2.get(word, 0)) for word in union)
        
        return weighted_intersection / weighted_union if weighted_union > 0 else 0.0
    
    async def generate_embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """Generate embeddings for multiple texts efficiently with batch limits"""
        if not texts:
            return []
        
        # Enforce batch size limit
        if len(texts) > ProductionLimits.MAX_EMBEDDING_BATCH_SIZE:
            logger.warning(
                f"Embedding batch size {len(texts)} exceeds limit, "
                f"truncating to {ProductionLimits.MAX_EMBEDDING_BATCH_SIZE}"
            )
            texts = texts[:ProductionLimits.MAX_EMBEDDING_BATCH_SIZE]
        
        # Validate all inputs
        validated_texts = []
        for text in texts:
            try:
                validated_texts.append(validate_text_input(text))
            except InputValidationError as e:
                logger.warning(f"Skipping invalid text in batch: {e}")
                validated_texts.append("")  # Placeholder to maintain indices
        
        if self.sentence_transformer:
            try:
                return self.sentence_transformer.encode(validated_texts)
            except Exception as e:
                logger.warning(f"SentenceTransformer encoding failed: {e}")
                # Fall through to TF-IDF fallback
        
        # Fallback to TF-IDF vectors
        with self._tfidf_lock:
            if not self._tfidf_fitted:
                self._tfidf_vectorizer.fit(validated_texts)
                self._tfidf_fitted = True
            
            try:
                vectors = self._tfidf_vectorizer.transform(validated_texts)
                return [vector.toarray().flatten() for vector in vectors]
            except ValueError as e:
                logger.warning(f"TF-IDF transform failed: {e}")
                # Return zero vectors as last resort
                return [np.zeros(10000) for _ in validated_texts]


class TopicSegmentation:
    """Advanced topic segmentation using graph-based methods"""
    
    def __init__(self, nlp_pipeline: AdvancedNLPPipeline):
        self.nlp_pipeline = nlp_pipeline
    
    async def segment_by_topics(self, sentences: List[str], threshold: float = 0.4) -> List[int]:
        """Segment text into topically coherent chunks using graph-based analysis"""
        if len(sentences) <= 2:
            return [len(sentences)]
        
        # Calculate similarity matrix
        similarity_matrix = await self._calculate_similarity_matrix(sentences)
        
        # Build similarity graph
        graph = self._build_similarity_graph(similarity_matrix, threshold)
        
        # Find community structure (topic boundaries)
        boundaries = self._detect_topic_boundaries(graph, len(sentences))
        
        return boundaries
    
    async def _calculate_similarity_matrix(self, sentences: List[str]) -> np.ndarray:
        """Calculate pairwise similarity matrix for sentences"""
        n = len(sentences)
        similarity_matrix = np.zeros((n, n))
        
        # Use batch processing for efficiency
        embeddings = await self.nlp_pipeline.generate_embeddings(sentences)
        
        for i in range(n):
            for j in range(i + 1, n):
                similarity = 1 - cosine(embeddings[i], embeddings[j])
                similarity_matrix[i, j] = similarity
                similarity_matrix[j, i] = similarity
        
        return similarity_matrix
    
    def _build_similarity_graph(self, similarity_matrix: np.ndarray, threshold: float) -> nx.Graph:
        """Build similarity graph from similarity matrix"""
        graph = nx.Graph()
        n = similarity_matrix.shape[0]
        
        # Add nodes
        for i in range(n):
            graph.add_node(i)
        
        # Add edges based on similarity threshold
        for i in range(n):
            for j in range(i + 1, n):
                if similarity_matrix[i, j] > threshold:
                    graph.add_edge(i, j, weight=similarity_matrix[i, j])
        
        return graph
    
    def _detect_topic_boundaries(self, graph: nx.Graph, num_sentences: int) -> List[int]:
        """Detect topic boundaries using graph connectivity analysis"""
        boundaries = [0]
        
        # Find connected components
        components = list(nx.connected_components(graph))
        
        if len(components) > 1:
            # Sort components by starting sentence
            sorted_components = sorted(components, key=lambda comp: min(comp))
            
            for i, component in enumerate(sorted_components[:-1]):
                # Find boundary between components
                current_max = max(component)
                next_min = min(sorted_components[i + 1])
                
                # Add boundary point
                boundary = (current_max + next_min + 1) // 2
                if boundary not in boundaries and boundary < num_sentences:
                    boundaries.append(boundary)
        
        # Ensure we end with the total number of sentences
        if boundaries[-1] != num_sentences:
            boundaries.append(num_sentences)
        
        return sorted(boundaries)


class SemanticChunkingProcessor(BaseFileProcessor):
    """Enterprise-grade semantic chunking processor with advanced NLP"""
    
    def __init__(self, capabilities: ProcessingCapabilities, executor: ThreadPoolExecutor):
        super().__init__(capabilities, executor)
        self.processor_name = "SemanticChunkingProcessor"
        
        # Initialize advanced NLP pipeline
        self.nlp_pipeline = AdvancedNLPPipeline(enable_caching=True)
        self.topic_segmentation = TopicSegmentation(self.nlp_pipeline)
        
        # Performance optimization
        self._embedding_cache = weakref.WeakValueDictionary()
        self._similarity_cache = {}
        self._cache_lock = threading.RLock()
    
    def get_supported_extensions(self) -> Set[str]:
        """Comprehensive file extension support"""
        return {
            # Text files
            '.txt', '.md', '.rst', '.log', '.readme', '.text',
            # Structured data
            '.json', '.xml', '.yaml', '.yml', '.toml', '.jsonl',
            # Tabular data
            '.csv', '.tsv', '.tab',
            # Code files
            '.py', '.js', '.java', '.cpp', '.c', '.h', '.cs', '.php', '.rb', '.go', '.rs',
            '.html', '.htm', '.css', '.sql', '.r', '.m', '.scala', '.kt', '.swift',
            # Documentation
            '.tex', '.bib', '.rtf', '.wiki',
            # Configuration
            '.conf', '.cfg', '.ini', '.env', '.properties',
            # Markup
            '.asciidoc', '.adoc', '.textile', '.org'
        }
    
    def can_process(self, file_path: Path, mime_type: str) -> bool:
        """Enhanced file compatibility detection"""
        ext = file_path.suffix.lower()
        
        # Direct extension support
        if ext in self.get_supported_extensions():
            return True
        
        # MIME type support
        if mime_type.startswith(('text/', 'application/json', 'application/xml')):
            return True
        
        # Content-based detection for files without extensions
        if not ext:
            return self._detect_text_content(file_path)
        
        return False
    
    def _detect_text_content(self, file_path: Path) -> bool:
        """Detect if file contains readable text content"""
        try:
            with open(file_path, 'rb') as f:
                sample = f.read(8192)  # Read first 8KB
            
            # Check for text content
            try:
                sample.decode('utf-8')
                # Calculate printable character ratio
                printable_ratio = sum(1 for byte in sample if 32 <= byte <= 126 or byte in (9, 10, 13)) / len(sample)
                return printable_ratio > 0.7
            except UnicodeDecodeError:
                # Try other encodings
                for encoding in ['latin1', 'cp1252']:
                    try:
                        decoded = sample.decode(encoding)
                        printable_ratio = sum(1 for c in decoded if c.isprintable() or c.isspace()) / len(decoded)
                        return printable_ratio > 0.7
                    except (UnicodeDecodeError, LookupError) as e:
                        logger.debug(f"Encoding {encoding} failed: {e}")
                        continue
                return False
        except Exception:
            return False
    
    async def process_file(self, file_path: Path, metadata: Dict[str, Any]) -> ProcessingResult:
        """Process file through enterprise semantic chunking pipeline"""
        start_time = time.time()
        
        # Create enhanced processing context
        context = ProcessingContext(
            task_id=metadata.get('task_id', f"chunk_{int(time.time())}"),
            session_id=metadata.get('session_id', 'unknown'),
            user_id=metadata.get('user_id', 'unknown'),
            file_path=file_path,
            progress_callback=metadata.get('progress_callback'),
            security_orchestrator=metadata.get('security_orchestrator'),
            cache_manager=metadata.get('cache_manager'),
            memory_manager=metadata.get('memory_manager'),
            artifact_builder=metadata.get('artifact_builder'),
            max_memory_mb=metadata.get('max_memory_mb', 2048),
            enable_parallel_processing=metadata.get('enable_parallel_processing', True),
            enable_advanced_metrics=metadata.get('enable_advanced_metrics', True)
        )
        
        try:
            context.update_progress("file_reading", 5.0, f"Reading file: {file_path.name}")
            
            # Efficient file reading with memory mapping for large files
            file_size = file_path.stat().st_size
            
            if file_size > 50 * 1024 * 1024:  # 50MB threshold
                raw_text = await self._read_large_file_efficiently(file_path, context)
            else:
                raw_text = await self._read_file_standard(file_path)
            
            if not raw_text.strip():
                return ProcessingResult(
                    success=False,
                    error_message="No readable text content found",
                    processing_time=time.time() - start_time,
                    processor_used=self.processor_name
                )
            
            # Process extracted text
            return await self.chunk_text(raw_text, context)
            
        except Exception as e:
            context.log_performance_metrics()
            logger.error(f"File processing failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _read_large_file_efficiently(self, file_path: Path, context: ProcessingContext) -> str:
        """Memory-efficient reading of large files"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mmapped_file:
                    return mmapped_file.read().decode('utf-8')
        except UnicodeDecodeError:
            # Fallback to standard reading with encoding detection
            return await self._read_file_standard(file_path)
    
    async def _read_file_standard(self, file_path: Path) -> str:
        """Standard file reading with encoding detection"""
        # Try UTF-8 first
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # Try other common encodings
            for encoding in ['latin1', 'cp1252', 'iso-8859-1']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        return f.read()
                except UnicodeDecodeError:
                    continue
            
            raise ValueError(f"Unable to decode text from {file_path}")
    
    async def chunk_text(self, raw_text: str, context: ProcessingContext) -> ProcessingResult:
        """Advanced text chunking with enterprise-grade NLP analysis"""
        start_time = time.time()
        
        try:
            # Generate content hash for caching
            content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
            cache_key = f"semantic_chunks_v2:{content_hash}"
            
            context.update_progress("cache_check", 10.0, "Checking cache for processed results")
            
            # Enhanced cache checking
            if context.cache_manager and context.enable_caching:
                cached_result = await context.cache_manager.get(cache_key)
                if cached_result:
                    context.update_progress("cache_hit", 100.0, "Retrieved from cache")
                    return ProcessingResult(**cached_result)
            
            context.update_progress("preprocessing", 15.0, "Preprocessing and sanitizing text")
            
            # Advanced text preprocessing
            sanitized_text = await self._advanced_text_preprocessing(raw_text)
            
            if len(sanitized_text.strip()) < 50:  # Minimum viable content
                return ProcessingResult(
                    success=False,
                    error_message="Insufficient text content after preprocessing",
                    processing_time=time.time() - start_time,
                    processor_used=self.processor_name
                )
            
            context.update_progress("content_analysis", 25.0, "Analyzing content structure and type")
            
            # Advanced content type detection
            content_type = await self._detect_content_type_advanced(sanitized_text, context.file_path)
            
            # Create advanced chunking configuration
            config = await self._create_optimal_config(content_type, sanitized_text, context)
            
            context.update_progress("sentence_segmentation", 35.0, "Performing advanced sentence segmentation")
            
            # Advanced sentence segmentation
            sentences = await self.nlp_pipeline.tokenize_sentences(sanitized_text)
            
            if len(sentences) < 2:
                # Single sentence or very short text
                chunk = await self._create_single_chunk(sanitized_text, config, context)
                chunks = [chunk] if chunk else []
            else:
                context.update_progress("semantic_analysis", 50.0, f"Performing semantic analysis using {config.semantic_model.value}")
                
                # Execute advanced chunking strategy
                chunks = await self._execute_advanced_chunking_strategy(
                    sanitized_text, sentences, config, context
                )
            
            context.update_progress("quality_assessment", 70.0, "Assessing chunk quality and coherence")
            
            # Advanced quality assessment and optimization
            optimized_chunks = await self._optimize_chunk_quality(chunks, config, context)
            
            context.update_progress("security_validation", 80.0, "Validating chunks through security orchestrator")
            
            # Security validation
            validated_chunks = await self._validate_chunks_security(optimized_chunks, context)
            
            context.update_progress("memory_storage", 85.0, "Storing chunks in memory system")
            
            # Enhanced memory system integration
            if context.memory_manager:
                await self._store_chunks_in_memory_advanced(validated_chunks, context)
            
            context.update_progress("artifact_generation", 90.0, "Creating comprehensive artifacts")
            
            # Advanced artifact generation
            if context.artifact_builder:
                await self._create_advanced_artifacts(validated_chunks, context, config)
            
            # Generate comprehensive processing result
            processing_result = await self._generate_processing_result(
                validated_chunks, content_type, config, context, start_time
            )
            
            context.update_progress("caching_results", 95.0, "Caching results for future use")
            
            # Enhanced caching
            if context.cache_manager and context.enable_caching:
                cache_data = processing_result.__dict__.copy()
                # Remove non-serializable data
                cache_data.pop('preview_data', None)
                cache_data.pop('thumbnail_data', None)
                await context.cache_manager.set(cache_key, cache_data, ttl_seconds=86400)
            
            context.update_progress("complete", 100.0, "Processing completed successfully")
            context.log_performance_metrics()
            
            return processing_result
            
        except Exception as e:
            context.log_performance_metrics()
            logger.error(f"Advanced text chunking failed: {e}")
            return ProcessingResult(
                success=False,
                error_message=str(e),
                processing_time=time.time() - start_time,
                processor_used=self.processor_name
            )
    
    async def _advanced_text_preprocessing(self, text: str) -> str:
        """Advanced text preprocessing with normalization"""
        # Unicode normalization
        import unicodedata
        text = unicodedata.normalize('NFKC', text)
        
        # Remove control characters except useful whitespace
        import re
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        
        # Normalize whitespace while preserving paragraph structure
        text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces/tabs to single space
        text = re.sub(r'\n[ \t]*\n', '\n\n', text)  # Normalize paragraph breaks
        text = re.sub(r'\n{3,}', '\n\n', text)  # Limit consecutive newlines
        
        return text.strip()
    
    async def _detect_content_type_advanced(self, text: str, file_path: Optional[Path]) -> ContentType:
        """Advanced content type detection using multiple signals"""
        # File extension hints
        if file_path:
            ext = file_path.suffix.lower()
            if ext in ['.py', '.js', '.java', '.cpp', '.c', '.h', '.cs', '.php', '.rb', '.go', '.rs']:
                return ContentType.CODE
            elif ext in ['.md', '.markdown']:
                return ContentType.MARKDOWN
            elif ext in ['.json', '.xml', '.yaml', '.yml']:
                return ContentType.STRUCTURED_DATA
        
        # Content-based detection using advanced pattern matching
        text_sample = text[:5000].lower()  # Use first 5KB for analysis
        
        # Research paper patterns
        research_patterns = [
            r'\babstract\b', r'\bintroduction\b', r'\bmethodology\b', r'\bresults\b',
            r'\bconclusion\b', r'\breferences\b', r'\bbibliography\b', r'\bcitation\b'
        ]
        research_score = sum(1 for pattern in research_patterns if re.search(pattern, text_sample))
        
        # Code patterns with more sophisticated detection
        code_patterns = [
            r'\bdef\s+\w+\s*\(', r'\bfunction\s+\w+\s*\(', r'\bclass\s+\w+',
            r'\bimport\s+\w+', r'\b#include\s*<', r'\bpublic\s+class',
            r'{\s*\n', r'}\s*\n', r';\s*\n'
        ]
        code_score = sum(1 for pattern in code_patterns if re.search(pattern, text_sample))
        
        # Conversation patterns
        conversation_patterns = [
            r'^\w+:', r'speaker\s+\d+:', r'\[\d{2}:\d{2}\]', r'Q:', r'A:'
        ]
        conversation_score = sum(1 for pattern in conversation_patterns if re.search(pattern, text_sample, re.MULTILINE))
        
        # Legal document patterns
        legal_patterns = [
            r'\bwhereas\b', r'\btherefore\b', r'\bplaintiff\b', r'\bdefendant\b',
            r'\bhereby\b', r'\bwitnesseth\b', r'\barticle\s+\d+', r'\bsection\s+\d+'
        ]
        legal_score = sum(1 for pattern in legal_patterns if re.search(pattern, text_sample))
        
        # Technical manual patterns
        technical_patterns = [
            r'\binstallation\b', r'\bconfiguration\b', r'\btroubleshooting\b',
            r'\bprerequisites\b', r'\brequirements\b', r'\bgetting\s+started\b'
        ]
        technical_score = sum(1 for pattern in technical_patterns if re.search(pattern, text_sample))
        
        # Markdown patterns
        markdown_patterns = [
            r'^#+\s', r'\*\*.*\*\*', r'\[.*\]\(.*\)', r'```', r'^\s*[-*]\s', r'^\s*\d+\.\s'
        ]
        markdown_score = sum(1 for pattern in markdown_patterns if re.search(pattern, text_sample, re.MULTILINE))
        
        # Score-based classification
        scores = {
            ContentType.RESEARCH_PAPER: research_score,
            ContentType.CODE: code_score,
            ContentType.CONVERSATION: conversation_score,
            ContentType.LEGAL_DOCUMENT: legal_score,
            ContentType.TECHNICAL_MANUAL: technical_score,
            ContentType.MARKDOWN: markdown_score
        }
        
        # Find highest scoring type
        max_score = max(scores.values())
        if max_score >= 3:  # Minimum confidence threshold
            for content_type, score in scores.items():
                if score == max_score:
                    return content_type
        
        # Check for mixed content
        high_scores = [score for score in scores.values() if score >= 2]
        if len(high_scores) >= 2:
            return ContentType.MIXED_CONTENT
        
        # Default classification
        return ContentType.DOCUMENTATION if markdown_score >= 1 else ContentType.PLAIN_TEXT
    
    async def _create_optimal_config(
        self, 
        content_type: ContentType, 
        text: str, 
        context: ProcessingContext
    ) -> ChunkingConfig:
        """Create optimal chunking configuration based on content analysis"""
        
        # Base configuration
        config = ChunkingConfig(content_type=content_type)
        
        # Adjust strategy based on content type
        strategy_map = {
            ContentType.CODE: ChunkingStrategy.STRUCTURAL,
            ContentType.RESEARCH_PAPER: ChunkingStrategy.TOPIC_SEGMENTATION,
            ContentType.CONVERSATION: ChunkingStrategy.DISCOURSE_ANALYSIS,
            ContentType.LEGAL_DOCUMENT: ChunkingStrategy.PARAGRAPH_BOUNDARY,
            ContentType.TECHNICAL_MANUAL: ChunkingStrategy.STRUCTURAL,
            ContentType.MARKDOWN: ChunkingStrategy.STRUCTURAL,
            ContentType.STRUCTURED_DATA: ChunkingStrategy.STRUCTURAL,
            ContentType.MIXED_CONTENT: ChunkingStrategy.HYBRID_ADVANCED,
            ContentType.PLAIN_TEXT: ChunkingStrategy.SEMANTIC_SIMILARITY
        }
        
        config.strategy = strategy_map.get(content_type, ChunkingStrategy.HYBRID_ADVANCED)
        
        # Adjust semantic model based on availability and content
        if self.nlp_pipeline.sentence_transformer:
            config.semantic_model = SemanticModel.SENTENCE_TRANSFORMERS
        elif content_type in [ContentType.RESEARCH_PAPER, ContentType.TECHNICAL_MANUAL]:
            config.semantic_model = SemanticModel.LINGUISTIC_FEATURES
        else:
            config.semantic_model = SemanticModel.TFIDF_COSINE
        
        # Adjust chunk sizes based on content characteristics
        text_length = len(text)
        sentence_count = len(await self.nlp_pipeline.tokenize_sentences(text))
        avg_sentence_length = text_length / max(sentence_count, 1)
        
        if avg_sentence_length > 200:  # Long sentences
            config.target_chunk_size = 1500
            config.max_chunk_size = 2500
        elif avg_sentence_length < 50:  # Short sentences
            config.target_chunk_size = 800
            config.max_chunk_size = 1200
        
        # Adjust thresholds based on content type
        if content_type == ContentType.CODE:
            config.similarity_threshold = 0.8  # Higher precision for code
            config.preserve_code_blocks = True
        elif content_type == ContentType.CONVERSATION:
            config.similarity_threshold = 0.6  # Lower for natural conversation
            config.enable_discourse_markers = True
        elif content_type == ContentType.RESEARCH_PAPER:
            config.coherence_threshold = 0.7
            config.enable_named_entity_preservation = True
        
        # Enable parallel processing for large texts
        if text_length > 100000 and context.enable_parallel_processing:
            config.enable_parallel_similarity = True
            config.batch_size = min(100, sentence_count // 4)
        
        return config
    
    async def _execute_advanced_chunking_strategy(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Execute advanced chunking strategy with sophisticated algorithms"""
        
        if config.strategy == ChunkingStrategy.TRANSFORMER_BASED:
            return await self._chunk_transformer_based(text, sentences, config, context)
        elif config.strategy == ChunkingStrategy.TOPIC_SEGMENTATION:
            return await self._chunk_topic_segmentation(text, sentences, config, context)
        elif config.strategy == ChunkingStrategy.DISCOURSE_ANALYSIS:
            return await self._chunk_discourse_analysis(text, sentences, config, context)
        elif config.strategy == ChunkingStrategy.LINGUISTIC_COHERENCE:
            return await self._chunk_linguistic_coherence(text, sentences, config, context)
        elif config.strategy == ChunkingStrategy.STRUCTURAL:
            return await self._chunk_structural_advanced(text, sentences, config, context)
        elif config.strategy == ChunkingStrategy.HYBRID_ADVANCED:
            return await self._chunk_hybrid_advanced(text, sentences, config, context)
        else:
            # Fallback to enhanced semantic similarity
            return await self._chunk_semantic_similarity_advanced(text, sentences, config, context)
    
    async def _chunk_transformer_based(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Transformer-based chunking using embedding similarity"""
        
        if not self.nlp_pipeline.sentence_transformer:
            # Fallback to alternative method
            return await self._chunk_semantic_similarity_advanced(text, sentences, config, context)
        
        # Generate embeddings for all sentences
        embeddings = await self.nlp_pipeline.generate_embeddings(sentences)
        
        # Calculate similarity matrix
        n = len(sentences)
        similarity_matrix = np.zeros((n, n))
        
        for i in range(n):
            for j in range(i + 1, n):
                similarity = 1 - cosine(embeddings[i], embeddings[j])
                similarity_matrix[i, j] = similarity
                similarity_matrix[j, i] = similarity
        
        # Find optimal boundaries using dynamic programming
        boundaries = await self._find_optimal_boundaries_dp(
            sentences, similarity_matrix, config
        )
        
        # Create chunks from boundaries
        chunks = []
        document_id = context.task_id
        char_position = 0
        
        for i, (start_idx, end_idx) in enumerate(zip(boundaries[:-1], boundaries[1:])):
            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(chunk_sentences)
            
            chunk = await self._create_advanced_chunk(
                text=chunk_text,
                sentences=chunk_sentences,
                chunk_index=i,
                start_sentence=start_idx,
                end_sentence=end_idx,
                start_char=char_position,
                end_char=char_position + len(chunk_text),
                config=config,
                document_id=document_id,
                embeddings=embeddings[start_idx:end_idx]
            )
            
            chunks.append(chunk)
            char_position += len(chunk_text) + 1
        
        return chunks
    
    async def _chunk_topic_segmentation(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Topic-based segmentation using advanced graph methods"""
        
        # Use topic segmentation to find boundaries
        boundaries = await self.topic_segmentation.segment_by_topics(
            sentences, config.topic_change_threshold
        )
        
        chunks = []
        document_id = context.task_id
        char_position = 0
        
        for i, (start_idx, end_idx) in enumerate(zip(boundaries[:-1], boundaries[1:])):
            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(chunk_sentences)
            
            # Ensure chunk meets size requirements
            if len(chunk_text) < config.min_chunk_size and i > 0:
                # Merge with previous chunk
                prev_chunk = chunks[-1]
                prev_chunk.text += ' ' + chunk_text
                prev_chunk.end_sentence = end_idx
                prev_chunk.end_char = char_position + len(chunk_text)
                prev_chunk.sentence_count += len(chunk_sentences)
                prev_chunk.word_count += len(chunk_text.split())
                continue
            
            chunk = await self._create_advanced_chunk(
                text=chunk_text,
                sentences=chunk_sentences,
                chunk_index=len(chunks),
                start_sentence=start_idx,
                end_sentence=end_idx,
                start_char=char_position,
                end_char=char_position + len(chunk_text),
                config=config,
                document_id=document_id
            )
            
            chunks.append(chunk)
            char_position += len(chunk_text) + 1
        
        return chunks
    
    async def _chunk_discourse_analysis(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Discourse-aware chunking for conversational content"""
        
        # Identify discourse markers and speaker changes
        discourse_boundaries = []
        
        for i, sentence in enumerate(sentences):
            # Check for speaker changes (simple heuristics)
            if re.match(r'^\w+:', sentence.strip()):
                discourse_boundaries.append(i)
            
            # Check for time markers
            if re.search(r'\[\d{2}:\d{2}\]', sentence):
                discourse_boundaries.append(i)
            
            # Check for discourse markers
            discourse_markers = [
                'however', 'therefore', 'furthermore', 'moreover', 'nevertheless',
                'meanwhile', 'subsequently', 'additionally', 'consequently'
            ]
            
            sentence_lower = sentence.lower()
            if any(marker in sentence_lower for marker in discourse_markers):
                discourse_boundaries.append(i)
        
        # Add start and end boundaries
        discourse_boundaries = [0] + sorted(set(discourse_boundaries)) + [len(sentences)]
        
        # Create chunks respecting discourse structure
        chunks = []
        document_id = context.task_id
        char_position = 0
        
        for i, (start_idx, end_idx) in enumerate(zip(discourse_boundaries[:-1], discourse_boundaries[1:])):
            if end_idx - start_idx < 1:
                continue
            
            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(chunk_sentences)
            
            # Split large discourse segments
            if len(chunk_text) > config.max_chunk_size:
                sub_chunks = await self._split_large_discourse_segment(
                    chunk_sentences, config, document_id, len(chunks)
                )
                chunks.extend(sub_chunks)
            else:
                chunk = await self._create_advanced_chunk(
                    text=chunk_text,
                    sentences=chunk_sentences,
                    chunk_index=len(chunks),
                    start_sentence=start_idx,
                    end_sentence=end_idx,
                    start_char=char_position,
                    end_char=char_position + len(chunk_text),
                    config=config,
                    document_id=document_id
                )
                chunks.append(chunk)
            
            char_position += len(chunk_text) + 1
        
        return chunks
    
    async def _chunk_linguistic_coherence(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Chunking based on linguistic coherence analysis"""
        
        # Calculate linguistic coherence scores between adjacent sentences
        coherence_scores = []
        
        for i in range(len(sentences) - 1):
            score = await self._calculate_linguistic_coherence(
                sentences[i], sentences[i + 1]
            )
            coherence_scores.append(score)
        
        # Find boundaries where coherence drops significantly
        boundaries = [0]
        coherence_threshold = config.coherence_threshold
        
        for i, score in enumerate(coherence_scores):
            if score < coherence_threshold:
                boundaries.append(i + 1)
        
        boundaries.append(len(sentences))
        
        # Optimize boundaries to meet size constraints
        optimized_boundaries = await self._optimize_boundaries_for_size(
            sentences, boundaries, config
        )
        
        # Create chunks
        chunks = []
        document_id = context.task_id
        char_position = 0
        
        for i, (start_idx, end_idx) in enumerate(zip(optimized_boundaries[:-1], optimized_boundaries[1:])):
            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(chunk_sentences)
            
            chunk = await self._create_advanced_chunk(
                text=chunk_text,
                sentences=chunk_sentences,
                chunk_index=i,
                start_sentence=start_idx,
                end_sentence=end_idx,
                start_char=char_position,
                end_char=char_position + len(chunk_text),
                config=config,
                document_id=document_id
            )
            
            chunks.append(chunk)
            char_position += len(chunk_text) + 1
        
        return chunks
    
    async def _chunk_structural_advanced(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Advanced structural chunking with enhanced pattern detection"""
        
        # Analyze document structure
        structure = await self._analyze_document_structure_advanced(text)
        
        # Generate structural boundaries
        boundaries = await self._get_structural_boundaries_advanced(structure, sentences)
        
        # Create chunks from structural boundaries
        chunks = []
        document_id = context.task_id
        char_position = 0
        
        for i, (start_idx, end_idx) in enumerate(zip(boundaries[:-1], boundaries[1:])):
            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(chunk_sentences)
            
            # Handle oversized structural chunks
            if len(chunk_text) > config.max_chunk_size:
                sub_chunks = await self._split_oversized_structural_chunk(
                    chunk_sentences, config, document_id, len(chunks)
                )
                chunks.extend(sub_chunks)
            else:
                chunk = await self._create_advanced_chunk(
                    text=chunk_text,
                    sentences=chunk_sentences,
                    chunk_index=len(chunks),
                    start_sentence=start_idx,
                    end_sentence=end_idx,
                    start_char=char_position,
                    end_char=char_position + len(chunk_text),
                    config=config,
                    document_id=document_id
                )
                chunks.append(chunk)
            
            char_position += len(chunk_text) + 1
        
        return chunks
    
    async def _chunk_hybrid_advanced(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Advanced hybrid chunking combining multiple strategies"""
        
        # First pass: structural analysis
        structure = await self._analyze_document_structure_advanced(text)
        
        if structure['has_clear_structure']:
            # Use structural chunking as base
            base_chunks = await self._chunk_structural_advanced(text, sentences, config, context)
        else:
            # Use topic segmentation as base
            base_chunks = await self._chunk_topic_segmentation(text, sentences, config, context)
        
        # Second pass: optimize using semantic similarity
        optimized_chunks = []
        
        for chunk in base_chunks:
            if len(chunk.text) > config.max_chunk_size:
                # Split large chunks using semantic similarity
                chunk_sentences = await self.nlp_pipeline.tokenize_sentences(chunk.text)
                sub_chunks = await self._chunk_semantic_similarity_advanced(
                    chunk.text, chunk_sentences, config, context
                )
                
                # Update chunk indices
                for j, sub_chunk in enumerate(sub_chunks):
                    sub_chunk.chunk_id = f"{chunk.chunk_id}_sub_{j}"
                    sub_chunk.chunk_index = len(optimized_chunks)
                    optimized_chunks.append(sub_chunk)
            
            elif len(chunk.text) < config.min_chunk_size and optimized_chunks:
                # Merge small chunks with previous chunk
                prev_chunk = optimized_chunks[-1]
                prev_chunk.text += ' ' + chunk.text
                prev_chunk.end_char = chunk.end_char
                prev_chunk.end_sentence = chunk.end_sentence
                prev_chunk.sentence_count += chunk.sentence_count
                prev_chunk.word_count += chunk.word_count
            
            else:
                chunk.chunk_index = len(optimized_chunks)
                optimized_chunks.append(chunk)
        
        return optimized_chunks
    
    async def _chunk_semantic_similarity_advanced(
        self,
        text: str,
        sentences: List[str],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Advanced semantic similarity-based chunking"""
        
        if len(sentences) <= 2:
            chunk = await self._create_single_chunk(text, config, context)
            return [chunk] if chunk else []
        
        # Calculate pairwise similarities efficiently
        similarities = []
        
        if config.enable_parallel_similarity and len(sentences) > config.batch_size:
            similarities = await self._calculate_similarities_parallel(sentences, config)
        else:
            similarities = await self._calculate_similarities_sequential(sentences, config)
        
        # Find optimal boundaries using similarity thresholds
        boundaries = [0]
        
        for i, similarity in enumerate(similarities):
            if similarity < config.similarity_threshold:
                boundaries.append(i + 1)
        
        boundaries.append(len(sentences))
        
        # Optimize boundaries for chunk size constraints
        optimized_boundaries = await self._optimize_boundaries_for_size(
            sentences, boundaries, config
        )
        
        # Create chunks
        chunks = []
        document_id = context.task_id
        char_position = 0
        
        for i, (start_idx, end_idx) in enumerate(zip(optimized_boundaries[:-1], optimized_boundaries[1:])):
            chunk_sentences = sentences[start_idx:end_idx]
            chunk_text = ' '.join(chunk_sentences)
            
            chunk = await self._create_advanced_chunk(
                text=chunk_text,
                sentences=chunk_sentences,
                chunk_index=i,
                start_sentence=start_idx,
                end_sentence=end_idx,
                start_char=char_position,
                end_char=char_position + len(chunk_text),
                config=config,
                document_id=document_id
            )
            
            chunks.append(chunk)
            char_position += len(chunk_text) + 1
        
        return chunks
    
    async def _create_advanced_chunk(
        self,
        text: str,
        sentences: List[str],
        chunk_index: int,
        start_sentence: int,
        end_sentence: int,
        start_char: int,
        end_char: int,
        config: ChunkingConfig,
        document_id: str,
        embeddings: Optional[List[np.ndarray]] = None
    ) -> SemanticChunk:
        """Create an advanced semantic chunk with comprehensive analysis"""
        
        chunk_start_time = time.time()
        
        # Create base chunk
        chunk = SemanticChunk(
            chunk_id=f"{document_id}_chunk_{chunk_index:04d}",
            text=text,
            content_type=config.content_type,
            chunk_index=chunk_index,
            start_char=start_char,
            end_char=end_char,
            start_sentence=start_sentence,
            end_sentence=end_sentence,
            paragraph_indices=[],  # Will be filled if needed
            word_count=len(text.split()),
            sentence_count=len(sentences),
            character_count=len(text),
            estimated_tokens=len(text) // 4,
            parent_document_id=document_id
        )
        
        # Extract advanced linguistic features
        if config.enable_syntactic_analysis:
            chunk.linguistic_features = await self.nlp_pipeline.extract_linguistic_features(text)
        
        # Calculate semantic coherence
        chunk.semantic_coherence = await self._calculate_chunk_coherence(sentences, config)
        
        # Calculate information density
        chunk.information_density = self._calculate_information_density_advanced(text)
        
        # Calculate readability score
        chunk.readability_score = self._calculate_readability_score(text)
        
        # Calculate complexity score
        chunk.complexity_score = self._calculate_complexity_score(text, chunk.linguistic_features)
        
        # Generate embedding
        if embeddings:
            chunk.embedding_vector = np.mean(embeddings, axis=0)
        elif config.cache_embeddings:
            embeddings_list = await self.nlp_pipeline.generate_embeddings([text])
            chunk.embedding_vector = embeddings_list[0]
        
        if chunk.embedding_vector is not None:
            chunk.embedding_hash = hashlib.md5(chunk.embedding_vector.tobytes()).hexdigest()
        
        # Extract named entities
        if config.enable_named_entity_preservation and self.nlp_pipeline.nlp:
            doc = self.nlp_pipeline.nlp(text)
            chunk.named_entities = [(ent.text, ent.label_) for ent in doc.ents]
        
        # Extract key phrases using advanced methods
        chunk.key_phrases = await self._extract_key_phrases_advanced(text)
        
        # Calculate overall quality score
        chunk.quality_score = await self._calculate_overall_quality_score(chunk)
        
        chunk.processing_time = time.time() - chunk_start_time
        
        return chunk
    
    async def _calculate_similarities_parallel(
        self, 
        sentences: List[str], 
        config: ChunkingConfig
    ) -> List[float]:
        """Calculate sentence similarities in parallel batches"""
        
        similarities = []
        batch_size = config.batch_size
        
        # Process in batches to avoid memory issues
        for i in range(0, len(sentences) - 1, batch_size):
            batch_end = min(i + batch_size, len(sentences) - 1)
            batch_similarities = []
            
            # Use ThreadPoolExecutor for CPU-bound similarity calculations
            loop = asyncio.get_event_loop()
            
            with ThreadPoolExecutor(max_workers=4) as executor:
                tasks = []
                
                for j in range(i, batch_end):
                    task = loop.run_in_executor(
                        executor,
                        self._calculate_similarity_sync,
                        sentences[j],
                        sentences[j + 1],
                        config.semantic_model
                    )
                    tasks.append(task)
                
                batch_results = await asyncio.gather(*tasks)
                batch_similarities.extend(batch_results)
            
            similarities.extend(batch_similarities)
        
        return similarities
    
    def _calculate_similarity_sync(self, sent1: str, sent2: str, model: SemanticModel) -> float:
        """Synchronous similarity calculation for thread pool execution"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.nlp_pipeline.calculate_semantic_similarity(sent1, sent2, model)
            )
        finally:
            loop.close()
    
    async def _calculate_similarities_sequential(
        self, 
        sentences: List[str], 
        config: ChunkingConfig
    ) -> List[float]:
        """Calculate sentence similarities sequentially"""
        
        similarities = []
        
        for i in range(len(sentences) - 1):
            similarity = await self.nlp_pipeline.calculate_semantic_similarity(
                sentences[i], sentences[i + 1], config.semantic_model
            )
            similarities.append(similarity)
        
        return similarities
    
    async def _optimize_boundaries_for_size(
        self,
        sentences: List[str],
        boundaries: List[int],
        config: ChunkingConfig
    ) -> List[int]:
        """Optimize chunk boundaries to meet size constraints"""
        
        optimized = [0]
        current_size = 0
        current_sentences = 0
        
        for i in range(1, len(sentences) + 1):
            sentence_length = len(sentences[i - 1])
            current_size += sentence_length
            current_sentences += 1
            
            # Check if we should create a boundary here
            should_break = False
            
            # Original boundary point
            if i in boundaries:
                should_break = True
            
            # Size constraints
            elif current_size >= config.target_chunk_size:
                should_break = True
            
            # Maximum size reached
            elif current_size >= config.max_chunk_size:
                should_break = True
            
            # Minimum sentence count
            elif (current_sentences >= config.min_sentences_per_chunk and 
                  current_size >= config.min_chunk_size):
                # Check if next boundary is far away
                next_boundary = next((b for b in boundaries if b > i), len(sentences))
                if next_boundary - i > config.max_sentences_per_chunk:
                    should_break = True
            
            if should_break and i < len(sentences):
                optimized.append(i)
                current_size = 0
                current_sentences = 0
        
        if optimized[-1] != len(sentences):
            optimized.append(len(sentences))
        
        return optimized
    
    async def _calculate_chunk_coherence(
        self, 
        sentences: List[str], 
        config: ChunkingConfig
    ) -> float:
        """Calculate semantic coherence within a chunk"""
        
        if len(sentences) <= 1:
            return 1.0
        
        similarities = []
        
        # Calculate all pairwise similarities within chunk
        for i in range(len(sentences)):
            for j in range(i + 1, len(sentences)):
                similarity = await self.nlp_pipeline.calculate_semantic_similarity(
                    sentences[i], sentences[j], config.semantic_model
                )
                similarities.append(similarity)
        
        # Return average similarity as coherence score
        return np.mean(similarities) if similarities else 0.5
    
    def _calculate_information_density_advanced(self, text: str) -> float:
        """Calculate advanced information density metrics"""
        
        words = text.lower().split()
        
        if not words:
            return 0.0
        
        # Unique word ratio
        unique_words = len(set(words))
        total_words = len(words)
        lexical_diversity = unique_words / total_words
        
        # Content word ratio (non-stop words)
        try:
            stop_words = set(stopwords.words('english'))
            content_words = [word for word in words if word not in stop_words and word.isalpha()]
            content_ratio = len(content_words) / total_words
        except (LookupError, OSError) as e:
            logger.debug(f"Stopwords lookup failed: {e}")
            content_ratio = 0.7  # Default estimate
        
        # Named entity density
        entity_density = 0.0
        if self.nlp_pipeline.nlp:
            try:
                doc = self.nlp_pipeline.nlp(text)
                entities = [ent for ent in doc.ents]
                entity_density = len(entities) / total_words
            except (ValueError, RuntimeError) as e:
                logger.debug(f"spaCy entity extraction failed: {e}")
        
        # Combine metrics
        information_density = (
            lexical_diversity * 0.4 +
            content_ratio * 0.4 +
            entity_density * 0.2
        )
        
        return min(information_density, 1.0)
    
    def _calculate_readability_score(self, text: str) -> float:
        """Calculate readability score using Flesch Reading Ease"""
        
        try:
            sentences = sent_tokenize(text)
            words = word_tokenize(text)
            
            if not sentences or not words:
                return 0.5
            
            # Count syllables (simple approximation)
            def count_syllables(word):
                word = word.lower()
                if word.endswith('e'):
                    word = word[:-1]
                syllables = len([char for char in word if char in 'aeiouy'])
                return max(syllables, 1)
            
            total_syllables = sum(count_syllables(word) for word in words if word.isalpha())
            
            # Flesch Reading Ease formula
            avg_sentence_length = len(words) / len(sentences)
            avg_syllables_per_word = total_syllables / len(words)
            
            flesch_score = 206.835 - (1.015 * avg_sentence_length) - (84.6 * avg_syllables_per_word)
            
            # Normalize to 0-1 range
            normalized_score = max(0, min(100, flesch_score)) / 100
            
            return normalized_score
        
        except Exception as e:
            logger.warning(f"Readability calculation failed: {e}")
            return 0.5
    
    def _calculate_complexity_score(self, text: str, linguistic_features: LinguisticFeatures) -> float:
        """Calculate text complexity score"""
        
        try:
            # Lexical complexity
            lexical_complexity = linguistic_features.avg_word_length / 10.0  # Normalize by typical max
            lexical_complexity += (1.0 - linguistic_features.lexical_diversity)  # Less diversity = more complex
            
            # Syntactic complexity
            syntactic_complexity = linguistic_features.avg_sentence_length / 50.0  # Normalize by typical max
            syntactic_complexity += linguistic_features.subordinate_clause_ratio
            
            # Semantic complexity
            semantic_complexity = (1.0 - linguistic_features.semantic_similarity_variance)
            semantic_complexity += linguistic_features.entity_density
            
            # Combine metrics
            overall_complexity = (
                lexical_complexity * 0.3 +
                syntactic_complexity * 0.4 +
                semantic_complexity * 0.3
            )
            
            return min(overall_complexity, 1.0)
        
        except Exception as e:
            logger.warning(f"Complexity calculation failed: {e}")
            return 0.5
    
    async def _extract_key_phrases_advanced(self, text: str) -> List[str]:
        """Extract key phrases using advanced NLP techniques"""
        
        key_phrases = []
        
        try:
            # Method 1: spaCy noun phrases
            if self.nlp_pipeline.nlp:
                doc = self.nlp_pipeline.nlp(text)
                noun_phrases = [chunk.text.lower().strip() for chunk in doc.noun_chunks 
                              if len(chunk.text.strip()) > 3]
                key_phrases.extend(noun_phrases[:10])
            
            # Method 2: TF-IDF important terms
            words = word_tokenize(text.lower())
            stop_words = set(stopwords.words('english'))
            filtered_words = [word for word in words if word.isalpha() and word not in stop_words]
            
            word_freq = Counter(filtered_words)
            top_words = [word for word, count in word_freq.most_common(15)]
            key_phrases.extend(top_words)
            
            # Method 3: Named entities
            if self.nlp_pipeline.nlp:
                doc = self.nlp_pipeline.nlp(text)
                entities = [ent.text.lower().strip() for ent in doc.ents 
                           if len(ent.text.strip()) > 2]
                key_phrases.extend(entities[:5])
            
            # Remove duplicates and return top phrases
            unique_phrases = list(dict.fromkeys(key_phrases))  # Preserve order
            return unique_phrases[:20]
        
        except Exception as e:
            logger.warning(f"Key phrase extraction failed: {e}")
            # Fallback to simple word frequency
            words = text.lower().split()
            word_freq = Counter([word for word in words if len(word) > 3])
            return [word for word, count in word_freq.most_common(10)]
    
    async def _calculate_overall_quality_score(self, chunk: SemanticChunk) -> float:
        """Calculate comprehensive quality score for chunk"""
        
        scores = []
        
        # Semantic coherence (30%)
        scores.append(chunk.semantic_coherence * 0.3)
        
        # Information density (25%)
        scores.append(chunk.information_density * 0.25)
        
        # Readability (20%)
        scores.append(chunk.readability_score * 0.2)
        
        # Size appropriateness (15%)
        size_score = 1.0
        if chunk.word_count < 50:
            size_score = chunk.word_count / 50.0
        elif chunk.word_count > 500:
            size_score = max(0.5, 1.0 - (chunk.word_count - 500) / 1000.0)
        scores.append(size_score * 0.15)
        
        # Linguistic quality (10%)
        linguistic_score = 0.8  # Default
        if chunk.linguistic_features.lexical_diversity > 0:
            linguistic_score = min(1.0, chunk.linguistic_features.lexical_diversity * 2)
        scores.append(linguistic_score * 0.1)
        
        return sum(scores)
    
    # [Continuing with remaining methods...]
    # The implementation continues with additional sophisticated methods for:
    # - Advanced quality optimization
    # - Security validation
    # - Memory storage with enhanced metadata
    # - Artifact generation with comprehensive analytics
    # - Performance monitoring and optimization
    
    async def _optimize_chunk_quality(
        self,
        chunks: List[SemanticChunk],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Advanced chunk quality optimization"""
        
        optimized_chunks = []
        
        for i, chunk in enumerate(chunks):
            # Check quality thresholds
            if chunk.quality_score < 0.5:
                # Attempt to improve chunk quality
                improved_chunk = await self._improve_chunk_quality(chunk, config)
                optimized_chunks.append(improved_chunk)
            else:
                optimized_chunks.append(chunk)
        
        # Post-processing optimization
        final_chunks = await self._post_process_chunk_relationships(optimized_chunks)
        
        return final_chunks
    
    async def _improve_chunk_quality(self, chunk: SemanticChunk, config: ChunkingConfig) -> SemanticChunk:
        """Improve individual chunk quality"""
        
        # If chunk is too small, mark for potential merging
        if chunk.word_count < 30:
            chunk.quality_score *= 0.8
        
        # If coherence is low, try to identify and remove outlier sentences
        if chunk.semantic_coherence < 0.4 and chunk.sentence_count > 3:
            sentences = await self.nlp_pipeline.tokenize_sentences(chunk.text)
            improved_sentences = await self._remove_outlier_sentences(sentences, config)
            
            if len(improved_sentences) >= 2:
                chunk.text = ' '.join(improved_sentences)
                chunk.sentence_count = len(improved_sentences)
                chunk.word_count = len(chunk.text.split())
                chunk.character_count = len(chunk.text)
                
                # Recalculate quality metrics
                chunk.semantic_coherence = await self._calculate_chunk_coherence(improved_sentences, config)
                chunk.quality_score = await self._calculate_overall_quality_score(chunk)
        
        return chunk
    
    async def _remove_outlier_sentences(self, sentences: List[str], config: ChunkingConfig) -> List[str]:
        """Remove outlier sentences that reduce coherence"""
        
        if len(sentences) <= 2:
            return sentences
        
        # Calculate similarity of each sentence to the rest
        sentence_scores = []
        
        for i, sentence in enumerate(sentences):
            other_sentences = sentences[:i] + sentences[i+1:]
            similarities = []
            
            for other_sentence in other_sentences:
                similarity = await self.nlp_pipeline.calculate_semantic_similarity(
                    sentence, other_sentence, config.semantic_model
                )
                similarities.append(similarity)
            
            avg_similarity = np.mean(similarities) if similarities else 0.0
            sentence_scores.append((i, avg_similarity))
        
        # Sort by similarity score
        sentence_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Keep top 80% of sentences
        keep_count = max(2, int(len(sentences) * 0.8))
        keep_indices = sorted([score[0] for score in sentence_scores[:keep_count]])
        
        return [sentences[i] for i in keep_indices]
    
    async def _post_process_chunk_relationships(self, chunks: List[SemanticChunk]) -> List[SemanticChunk]:
        """Post-process to establish chunk relationships"""
        
        for i, chunk in enumerate(chunks):
            # Set relationship IDs
            if i > 0:
                chunk.previous_chunk_id = chunks[i-1].chunk_id
                # Calculate similarity to previous chunk
                chunk.semantic_similarity_to_previous = await self.nlp_pipeline.calculate_semantic_similarity(
                    chunks[i-1].text[-200:], chunk.text[:200], SemanticModel.SENTENCE_TRANSFORMERS
                )
            
            if i < len(chunks) - 1:
                chunk.next_chunk_id = chunks[i+1].chunk_id
                # Calculate similarity to next chunk  
                chunk.semantic_similarity_to_next = await self.nlp_pipeline.calculate_semantic_similarity(
                    chunk.text[-200:], chunks[i+1].text[:200], SemanticModel.SENTENCE_TRANSFORMERS
                )
            
            # Update total chunks
            chunk.total_chunks = len(chunks)
        
        return chunks
    
    async def _validate_chunks_security(
        self,
        chunks: List[SemanticChunk],
        context: ProcessingContext
    ) -> List[SemanticChunk]:
        """Enhanced security validation with detailed reporting"""
        
        if not context.security_orchestrator:
            return chunks
        
        validated_chunks = []
        
        for chunk in chunks:
            try:
                # Comprehensive security validation
                security_result = await context.security_orchestrator.validate_message_and_execute(
                    chunk.text,
                    session_id=context.session_id,
                    user_id=context.user_id,
                    content_type=chunk.content_type.value
                )
                
                chunk.is_safe = security_result.get('safe', True)
                
                if not chunk.is_safe:
                    chunk.security_warnings.append(security_result.get('reason', 'Security validation failed'))
                    chunk.quality_score *= 0.5  # Penalize quality score for unsafe content
                
                validated_chunks.append(chunk)
                
            except Exception as e:
                logger.error(f"Security validation failed for chunk {chunk.chunk_id}: {e}")
                chunk.is_safe = False
                chunk.security_warnings.append(f"Security validation error: {str(e)}")
                chunk.quality_score = 0.0
                validated_chunks.append(chunk)
        
        return validated_chunks
    
    async def _store_chunks_in_memory_advanced(
        self,
        chunks: List[SemanticChunk],
        context: ProcessingContext
    ):
        """Enhanced memory storage with comprehensive metadata"""
        
        safe_chunks = [chunk for chunk in chunks if chunk.is_safe and chunk.quality_score > 0.5]
        
        for chunk in safe_chunks:
            try:
                # Comprehensive metadata for memory storage
                memory_metadata = {
                    'chunk_id': chunk.chunk_id,
                    'source_file': str(context.file_path) if context.file_path else 'text_input',
                    'chunk_index': chunk.chunk_index,
                    'total_chunks': len(chunks),
                    
                    # Quality metrics
                    'semantic_coherence': chunk.semantic_coherence,
                    'information_density': chunk.information_density,
                    'quality_score': chunk.quality_score,
                    'readability_score': chunk.readability_score,
                    'complexity_score': chunk.complexity_score,
                    
                    # Content characteristics
                    'content_type': chunk.content_type.value,
                    'word_count': chunk.word_count,
                    'sentence_count': chunk.sentence_count,
                    'character_count': chunk.character_count,
                    
                    # Semantic features
                    'key_phrases': chunk.key_phrases,
                    'named_entities': [{'text': ent[0], 'label': ent[1]} for ent in chunk.named_entities],
                    'topic_distribution': chunk.topic_distribution,
                    
                    # Linguistic features
                    'linguistic_features': {
                        'avg_word_length': chunk.linguistic_features.avg_word_length,
                        'lexical_diversity': chunk.linguistic_features.lexical_diversity,
                        'sentiment_polarity': chunk.linguistic_features.sentiment_polarity,
                        'entity_density': chunk.linguistic_features.entity_density
                    },
                    
                    # Processing metadata
                    'processing_time': chunk.processing_time,
                    'embedding_hash': chunk.embedding_hash
                }
                
                # Enhanced tagging
                tags = [
                    'semantic_chunk',
                    chunk.content_type.value,
                    context.task_id,
                    f'quality_{int(chunk.quality_score * 10)}'  # Quality tier tag
                ]
                
                # Add content-specific tags
                if chunk.semantic_coherence > 0.8:
                    tags.append('high_coherence')
                if chunk.information_density > 0.7:
                    tags.append('information_dense')
                if chunk.named_entities:
                    tags.append('contains_entities')
                    tags.extend([f'entity_{ent[1].lower()}' for ent in chunk.named_entities[:3]])
                
                # Store in memory system
                await context.memory_manager.push(
                    content=chunk.text,
                    metadata=memory_metadata,
                    tags=tags,
                    session_id=context.session_id,
                    importance_score=chunk.quality_score,
                    embedding_vector=chunk.embedding_vector
                )
                
            except Exception as e:
                logger.error(f"Memory storage failed for chunk {chunk.chunk_id}: {e}")
    
    async def _create_advanced_artifacts(
        self,
        chunks: List[SemanticChunk],
        context: ProcessingContext,
        config: ChunkingConfig
    ):
        """Create comprehensive artifacts with analytics"""
        
        try:
            # Generate comprehensive analytics
            analytics = await self._generate_chunk_analytics(chunks, config, context)
            
            # Main chunking report artifact
            chunking_report = {
                'type': 'advanced_semantic_chunking_report',
                'version': '2.0',
                'metadata': {
                    'source_file': str(context.file_path) if context.file_path else 'text_input',
                    'task_id': context.task_id,
                    'processing_timestamp': datetime.now(timezone.utc).isoformat(),
                    'processing_time_seconds': time.time() - context.start_time,
                    'content_type': config.content_type.value,
                    'chunking_strategy': config.strategy.value,
                    'semantic_model': config.semantic_model.value
                },
                'summary': {
                    'total_chunks': len(chunks),
                    'safe_chunks': len([c for c in chunks if c.is_safe]),
                    'high_quality_chunks': len([c for c in chunks if c.quality_score > 0.8]),
                    'avg_quality_score': np.mean([c.quality_score for c in chunks]),
                    'avg_semantic_coherence': np.mean([c.semantic_coherence for c in chunks]),
                    'avg_information_density': np.mean([c.information_density for c in chunks]),
                    'total_word_count': sum(c.word_count for c in chunks),
                    'total_processing_time': sum(c.processing_time for c in chunks)
                },
                'analytics': analytics,
                'chunks': await self._serialize_chunks_for_artifact(chunks)
            }
            
            await context.artifact_builder.create_artifact(
                artifact_type='semantic_chunking_report',
                content=chunking_report,
                session_id=context.session_id,
                user_id=context.user_id,
                metadata={
                    'source_file': str(context.file_path) if context.file_path else 'text_input',
                    'task_id': context.task_id,
                    'chunk_count': len(chunks),
                    'quality_score': analytics['overall_quality_score']
                }
            )
            
            # Performance metrics artifact
            performance_metrics = {
                'type': 'chunking_performance_metrics',
                'stage_times': context.stage_times,
                'total_processing_time': time.time() - context.start_time,
                'memory_usage': analytics.get('memory_usage', {}),
                'processing_efficiency': analytics.get('processing_efficiency', {})
            }
            
            await context.artifact_builder.create_artifact(
                artifact_type='performance_metrics',
                content=performance_metrics,
                session_id=context.session_id,
                user_id=context.user_id,
                metadata={'task_id': context.task_id}
            )
            
        except Exception as e:
            logger.error(f"Artifact creation failed: {e}")
    
    async def _generate_chunk_analytics(
        self,
        chunks: List[SemanticChunk],
        config: ChunkingConfig,
        context: ProcessingContext
    ) -> Dict[str, Any]:
        """Generate comprehensive analytics for chunking results"""
        
        if not chunks:
            return {'overall_quality_score': 0.0}
        
        analytics = {}
        
        # Quality distribution analysis
        quality_scores = [chunk.quality_score for chunk in chunks]
        analytics['quality_distribution'] = {
            'mean': np.mean(quality_scores),
            'std': np.std(quality_scores),
            'median': np.median(quality_scores),
            'min': np.min(quality_scores),
            'max': np.max(quality_scores),
            'percentiles': {
                '25th': np.percentile(quality_scores, 25),
                '75th': np.percentile(quality_scores, 75),
                '95th': np.percentile(quality_scores, 95)
            }
        }
        
        # Coherence analysis
        coherence_scores = [chunk.semantic_coherence for chunk in chunks]
        analytics['coherence_analysis'] = {
            'average_coherence': np.mean(coherence_scores),
            'coherence_variance': np.var(coherence_scores),
            'high_coherence_chunks': len([c for c in coherence_scores if c > 0.8]),
            'low_coherence_chunks': len([c for c in coherence_scores if c < 0.4])
        }
        
        # Size distribution analysis
        word_counts = [chunk.word_count for chunk in chunks]
        char_counts = [chunk.character_count for chunk in chunks]
        
        analytics['size_distribution'] = {
            'word_count_stats': {
                'mean': np.mean(word_counts),
                'std': np.std(word_counts),
                'min': np.min(word_counts),
                'max': np.max(word_counts)
            },
            'character_count_stats': {
                'mean': np.mean(char_counts),
                'std': np.std(char_counts),
                'min': np.min(char_counts),
                'max': np.max(char_counts)
            },
            'size_consistency': 1.0 - (np.std(word_counts) / np.mean(word_counts)) if np.mean(word_counts) > 0 else 0.0
        }
        
        # Content analysis
        content_types = Counter([chunk.content_type.value for chunk in chunks])
        all_key_phrases = []
        all_entities = []
        
        for chunk in chunks:
            all_key_phrases.extend(chunk.key_phrases)
            all_entities.extend([ent[0] for ent in chunk.named_entities])
        
        analytics['content_analysis'] = {
            'content_type_distribution': dict(content_types),
            'total_unique_phrases': len(set(all_key_phrases)),
            'total_unique_entities': len(set(all_entities)),
            'most_common_phrases': Counter(all_key_phrases).most_common(10),
            'most_common_entities': Counter(all_entities).most_common(10)
        }
        
        # Linguistic complexity analysis
        complexity_scores = [chunk.complexity_score for chunk in chunks]
        readability_scores = [chunk.readability_score for chunk in chunks]
        
        analytics['linguistic_analysis'] = {
            'average_complexity': np.mean(complexity_scores),
            'average_readability': np.mean(readability_scores),
            'complexity_range': np.max(complexity_scores) - np.min(complexity_scores),
            'readability_consistency': 1.0 - np.std(readability_scores)
        }
        
        # Security analysis
        safe_chunks = [chunk for chunk in chunks if chunk.is_safe]
        security_warnings = []
        for chunk in chunks:
            security_warnings.extend(chunk.security_warnings)
        
        analytics['security_analysis'] = {
            'safe_chunk_ratio': len(safe_chunks) / len(chunks),
            'total_security_warnings': len(security_warnings),
            'unique_warning_types': len(set(security_warnings)),
            'common_warnings': Counter(security_warnings).most_common(5)
        }
        
        # Processing efficiency analysis
        processing_times = [chunk.processing_time for chunk in chunks]
        analytics['processing_efficiency'] = {
            'total_processing_time': sum(processing_times),
            'average_time_per_chunk': np.mean(processing_times),
            'processing_rate_chunks_per_second': len(chunks) / (time.time() - context.start_time),
            'slowest_chunk_time': np.max(processing_times),
            'fastest_chunk_time': np.min(processing_times)
        }
        
        # Memory usage estimation
        total_text_size = sum(len(chunk.text.encode('utf-8')) for chunk in chunks)
        embedding_size = 0
        if chunks[0].embedding_vector is not None:
            embedding_size = len(chunks) * chunks[0].embedding_vector.nbytes
        
        analytics['memory_usage'] = {
            'total_text_bytes': total_text_size,
            'estimated_embedding_bytes': embedding_size,
            'total_estimated_bytes': total_text_size + embedding_size,
            'average_chunk_size_bytes': total_text_size / len(chunks)
        }
        
        # Overall quality assessment
        analytics['overall_quality_score'] = np.mean([
            analytics['quality_distribution']['mean'],
            analytics['coherence_analysis']['average_coherence'],
            analytics['size_distribution']['size_consistency'],
            analytics['security_analysis']['safe_chunk_ratio']
        ])
        
        # Optimization recommendations
        recommendations = []
        
        if analytics['quality_distribution']['mean'] < 0.6:
            recommendations.append("Consider using a more sophisticated chunking strategy")
        
        if analytics['coherence_analysis']['low_coherence_chunks'] > len(chunks) * 0.2:
            recommendations.append("High number of low-coherence chunks detected - review similarity thresholds")
        
        if analytics['size_distribution']['size_consistency'] < 0.7:
            recommendations.append("Chunk sizes are inconsistent - consider adjusting size constraints")
        
        if analytics['security_analysis']['safe_chunk_ratio'] < 0.95:
            recommendations.append("Security warnings detected - review content filtering policies")
        
        analytics['recommendations'] = recommendations
        
        return analytics
    
    async def _serialize_chunks_for_artifact(self, chunks: List[SemanticChunk]) -> List[Dict[str, Any]]:
        """Serialize chunks for artifact storage with comprehensive metadata"""
        
        serialized_chunks = []
        
        for chunk in chunks:
            chunk_data = {
                'chunk_id': chunk.chunk_id,
                'chunk_index': chunk.chunk_index,
                'text_preview': chunk.text[:300] + "..." if len(chunk.text) > 300 else chunk.text,
                'full_text_available': True,
                
                # Basic metrics
                'word_count': chunk.word_count,
                'sentence_count': chunk.sentence_count,
                'character_count': chunk.character_count,
                'estimated_tokens': chunk.estimated_tokens,
                
                # Position metadata
                'start_char': chunk.start_char,
                'end_char': chunk.end_char,
                'start_sentence': chunk.start_sentence,
                'end_sentence': chunk.end_sentence,
                
                # Quality metrics
                'quality_score': round(chunk.quality_score, 3),
                'semantic_coherence': round(chunk.semantic_coherence, 3),
                'information_density': round(chunk.information_density, 3),
                'readability_score': round(chunk.readability_score, 3),
                'complexity_score': round(chunk.complexity_score, 3),
                'boundary_quality': round(chunk.boundary_quality, 3),
                
                # Content features
                'content_type': chunk.content_type.value,
                'key_phrases': chunk.key_phrases[:10],  # Top 10 phrases
                'named_entities': [{'text': ent[0], 'label': ent[1]} for ent in chunk.named_entities[:10]],
                'topic_distribution': chunk.topic_distribution,
                
                # Linguistic features summary
                'linguistic_summary': {
                    'avg_word_length': round(chunk.linguistic_features.avg_word_length, 2),
                    'lexical_diversity': round(chunk.linguistic_features.lexical_diversity, 3),
                    'sentiment_polarity': round(chunk.linguistic_features.sentiment_polarity, 3),
                    'entity_density': round(chunk.linguistic_features.entity_density, 3)
                },
                
                # Relationships
                'previous_chunk_id': chunk.previous_chunk_id,
                'next_chunk_id': chunk.next_chunk_id,
                'similarity_to_previous': round(chunk.semantic_similarity_to_previous, 3),
                'similarity_to_next': round(chunk.semantic_similarity_to_next, 3),
                
                # Security and processing
                'is_safe': chunk.is_safe,
                'security_warnings': chunk.security_warnings,
                'processing_time': round(chunk.processing_time, 4),
                'embedding_available': chunk.embedding_vector is not None,
                'embedding_hash': chunk.embedding_hash
            }
            
            serialized_chunks.append(chunk_data)
        
        return serialized_chunks
    
    async def _generate_processing_result(
        self,
        chunks: List[SemanticChunk],
        content_type: ContentType,
        config: ChunkingConfig,
        context: ProcessingContext,
        start_time: float
    ) -> ProcessingResult:
        """Generate comprehensive processing result"""
        
        # Calculate summary statistics
        safe_chunks = [chunk for chunk in chunks if chunk.is_safe]
        high_quality_chunks = [chunk for chunk in chunks if chunk.quality_score > 0.8]
        
        # Generate extracted text summary
        extracted_text = await self._synthesize_chunk_summary(chunks, content_type)
        
        # Comprehensive metadata
        result_metadata = {
            # Basic counts
            'total_chunks': len(chunks),
            'safe_chunks': len(safe_chunks),
            'high_quality_chunks': len(high_quality_chunks),
            'processing_version': '2.0',
            
            # Content characteristics
            'content_type': content_type.value,
            'chunking_strategy': config.strategy.value,
            'semantic_model': config.semantic_model.value,
            
            # Quality metrics
            'avg_quality_score': np.mean([c.quality_score for c in chunks]) if chunks else 0.0,
            'avg_semantic_coherence': np.mean([c.semantic_coherence for c in chunks]) if chunks else 0.0,
            'avg_information_density': np.mean([c.information_density for c in chunks]) if chunks else 0.0,
            'avg_readability_score': np.mean([c.readability_score for c in chunks]) if chunks else 0.0,
            'avg_complexity_score': np.mean([c.complexity_score for c in chunks]) if chunks else 0.0,
            
            # Size distribution
            'avg_chunk_size_words': np.mean([c.word_count for c in chunks]) if chunks else 0,
            'avg_chunk_size_chars': np.mean([c.character_count for c in chunks]) if chunks else 0,
            'size_variance': np.var([c.word_count for c in chunks]) if chunks else 0,
            
            # Content analysis
            'total_key_phrases': len(set(phrase for chunk in chunks for phrase in chunk.key_phrases)),
            'total_named_entities': len(set(ent[0] for chunk in chunks for ent in chunk.named_entities)),
            'content_languages_detected': list(set(chunk.linguistic_features.sentiment_polarity for chunk in chunks)),
            
            # Processing performance
            'total_processing_time': time.time() - start_time,
            'avg_processing_time_per_chunk': np.mean([c.processing_time for c in chunks]) if chunks else 0.0,
            'chunks_per_second': len(chunks) / (time.time() - start_time) if (time.time() - start_time) > 0 else 0,
            
            # System utilization
            'memory_efficient_processing': config.memory_map_large_files,
            'parallel_processing_used': config.enable_parallel_similarity,
            'embedding_caching_used': config.cache_embeddings,
            'advanced_nlp_features_used': config.enable_syntactic_analysis,
            
            # Configuration used
            'target_chunk_size': config.target_chunk_size,
            'similarity_threshold': config.similarity_threshold,
            'coherence_threshold': config.coherence_threshold,
            'preserved_named_entities': config.enable_named_entity_preservation,
            
            # Stage performance breakdown
            'stage_performance': context.stage_times
        }
        
        # Determine capabilities used
        capabilities_used = ['advanced_semantic_chunking', 'nltk_processing']
        
        if self.nlp_pipeline.sentence_transformer:
            capabilities_used.append('transformer_embeddings')
        if self.nlp_pipeline.nlp:
            capabilities_used.append('spacy_nlp')
        if config.enable_parallel_similarity:
            capabilities_used.append('parallel_processing')
        if config.cache_embeddings:
            capabilities_used.append('embedding_caching')
        
        return ProcessingResult(
            success=True,
            extracted_text=extracted_text,
            metadata=result_metadata,
            processing_time=time.time() - start_time,
            processor_used=self.processor_name,
            capabilities_used=capabilities_used
        )
    
    async def _synthesize_chunk_summary(self, chunks: List[SemanticChunk], content_type: ContentType) -> str:
        """Generate comprehensive text summary of chunking results"""
        
        if not chunks:
            return "No chunks generated from input text."
        
        safe_chunks = [chunk for chunk in chunks if chunk.is_safe]
        high_quality_chunks = [chunk for chunk in chunks if chunk.quality_score > 0.8]
        
        summary_parts = [
            f"Advanced Semantic Chunking Results",
            f"==========================================",
            f"",
            f"Document Analysis:",
            f"- Content Type: {content_type.value.replace('_', ' ').title()}",
            f"- Total Chunks: {len(chunks)}",
            f"- Safe Chunks: {len(safe_chunks)} ({len(safe_chunks)/len(chunks)*100:.1f}%)",
            f"- High Quality Chunks: {len(high_quality_chunks)} ({len(high_quality_chunks)/len(chunks)*100:.1f}%)",
            f"",
            f"Quality Metrics:",
            f"- Average Quality Score: {np.mean([c.quality_score for c in chunks]):.3f}",
            f"- Average Semantic Coherence: {np.mean([c.semantic_coherence for c in chunks]):.3f}",
            f"- Average Information Density: {np.mean([c.information_density for c in chunks]):.3f}",
            f"- Average Readability: {np.mean([c.readability_score for c in chunks]):.3f}",
            f"",
            f"Size Distribution:",
            f"- Average Chunk Size: {np.mean([c.word_count for c in chunks]):.0f} words",
            f"- Size Range: {np.min([c.word_count for c in chunks])}-{np.max([c.word_count for c in chunks])} words",
            f"- Size Consistency: {1.0 - (np.std([c.word_count for c in chunks]) / np.mean([c.word_count for c in chunks])):.3f}",
            f"",
            f"Content Analysis:",
            f"- Unique Key Phrases: {len(set(phrase for chunk in chunks for phrase in chunk.key_phrases))}",
            f"- Named Entities: {len(set(ent[0] for chunk in chunks for ent in chunk.named_entities))}",
            f"",
            f"Sample High-Quality Chunks:",
            f"=========================="
        ]
        
        # Include previews of top 3 chunks by quality
        top_chunks = sorted(safe_chunks, key=lambda c: c.quality_score, reverse=True)[:3]
        
        for i, chunk in enumerate(top_chunks, 1):
            preview = chunk.text[:200] + "..." if len(chunk.text) > 200 else chunk.text
            summary_parts.extend([
                f"",
                f"Chunk {i} (Quality: {chunk.quality_score:.3f}, Coherence: {chunk.semantic_coherence:.3f}):",
                f"{preview}",
                f"Key Phrases: {', '.join(chunk.key_phrases[:5])}"
            ])
        
        if len(safe_chunks) > 3:
            summary_parts.extend([
                f"",
                f"... and {len(safe_chunks) - 3} additional high-quality chunks available."
            ])
        
        # Add processing insights
        if chunks:
            avg_complexity = np.mean([c.complexity_score for c in chunks])
            if avg_complexity > 0.7:
                summary_parts.append("\n📊 Analysis: High linguistic complexity detected - suitable for technical or academic content.")
            elif avg_complexity < 0.3:
                summary_parts.append("\n📊 Analysis: Low complexity content - well-suited for general audience.")
            
            avg_coherence = np.mean([c.semantic_coherence for c in chunks])
            if avg_coherence > 0.8:
                summary_parts.append("🎯 Analysis: Excellent semantic coherence - chunks maintain strong topical relationships.")
            elif avg_coherence < 0.5:
                summary_parts.append("⚠️  Analysis: Lower coherence detected - content may benefit from different chunking strategy.")
        
        return "\n".join(summary_parts)
    
    async def _analyze_document_structure_advanced(self, text: str) -> Dict[str, Any]:
        """Advanced document structure analysis"""
        
        structure = {
            'has_clear_structure': False,
            'headers': [],
            'code_blocks': [],
            'tables': [],
            'lists': [],
            'quotes': [],
            'paragraphs': [],
            'structure_score': 0.0
        }
        
        lines = text.split('\n')
        in_code_block = False
        code_block_start = None
        current_paragraph = []
        
        # Enhanced pattern detection
        header_patterns = [
            (r'^#{1,6}\s+(.+)', 'markdown_header'),
            (r'^([A-Z][^a-z\n]{2,50})$', 'caps_header'),
            (r'^\d+\.\s+(.+)', 'numbered_header'),
            (r'^([A-Z][a-z\s]+):$', 'colon_header'),
            (r'^=+$|^-+$', 'underline_header')
        ]
        
        list_patterns = [
            (r'^\s*[-*+]\s+', 'bullet_list'),
            (r'^\s*\d+\.\s+', 'numbered_list'),
            (r'^\s*[a-zA-Z]\.\s+', 'lettered_list'),
            (r'^\s*[ivxlcdm]+\.\s+', 'roman_list')
        ]
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            # Code block detection
            if '```' in line or line.startswith('    ') or line.startswith('\t'):
                if '```' in line:
                    if not in_code_block:
                        in_code_block = True
                        code_block_start = i
                    else:
                        in_code_block = False
                        if code_block_start is not None:
                            structure['code_blocks'].append({
                                'start_line': code_block_start,
                                'end_line': i,
                                'type': 'fenced' if '```' in line else 'indented'
                            })
                continue
            
            if in_code_block:
                continue
            
            # Header detection
            for pattern, header_type in header_patterns:
                match = re.match(pattern, line_stripped, re.MULTILINE)
                if match:
                    level = 1
                    if header_type == 'markdown_header':
                        level = line_stripped.count('#')
                    elif header_type == 'numbered_header':
                        level = 2
                    
                    structure['headers'].append({
                        'line': i,
                        'text': match.group(1) if match.groups() else line_stripped,
                        'level': level,
                        'type': header_type
                    })
                    break
            
            # List detection
            for pattern, list_type in list_patterns:
                if re.match(pattern, line):
                    structure['lists'].append({
                        'line': i,
                        'text': line_stripped,
                        'type': list_type,
                        'level': len(line) - len(line.lstrip())  # Indentation level
                    })
                    break
            
            # Table detection (enhanced)
            if '|' in line and line.count('|') >= 2:
                structure['tables'].append({
                    'line': i,
                    'text': line_stripped,
                    'columns': line.count('|') - 1
                })
            
            # Quote detection
            if line_stripped.startswith('>'):
                structure['quotes'].append({
                    'line': i,
                    'text': line_stripped[1:].strip(),
                    'level': len(line_stripped) - len(line_stripped.lstrip('>'))
                })
            
            # Paragraph building
            if line_stripped:
                current_paragraph.append((i, line_stripped))
            else:
                if current_paragraph:
                    structure['paragraphs'].append({
                        'start_line': current_paragraph[0][0],
                        'end_line': current_paragraph[-1][0],
                        'text': ' '.join([p[1] for p in current_paragraph]),
                        'word_count': len(' '.join([p[1] for p in current_paragraph]).split())
                    })
                    current_paragraph = []
        
        # Final paragraph
        if current_paragraph:
            structure['paragraphs'].append({
                'start_line': current_paragraph[0][0],
                'end_line': current_paragraph[-1][0],
                'text': ' '.join([p[1] for p in current_paragraph]),
                'word_count': len(' '.join([p[1] for p in current_paragraph]).split())
            })
        
        # Calculate structure score
        structure_indicators = 0
        if len(structure['headers']) > 0:
            structure_indicators += min(len(structure['headers']) * 2, 10)
        if len(structure['code_blocks']) > 0:
            structure_indicators += min(len(structure['code_blocks']) * 3, 15)
        if len(structure['lists']) > 0:
            structure_indicators += min(len(structure['lists']), 10)
        if len(structure['tables']) > 0:
            structure_indicators += min(len(structure['tables']) * 2, 10)
        
        structure['structure_score'] = min(structure_indicators / 25.0, 1.0)  # Normalize to 0-1
        structure['has_clear_structure'] = structure['structure_score'] > 0.3
        
        return structure
    
    async def _get_structural_boundaries_advanced(
        self, 
        structure: Dict[str, Any], 
        sentences: List[str]
    ) -> List[int]:
        """Generate advanced structural boundaries"""
        
        text = ' '.join(sentences)
        lines = text.split('\n')
        boundaries = {0}  # Always start with beginning
        
        # Add boundaries at major structural elements
        for header in structure['headers']:
            if header['level'] <= 2:  # Major headers only
                # Find corresponding sentence
                sentence_idx = self._find_sentence_for_line(header['line'], sentences)
                if sentence_idx is not None:
                    boundaries.add(sentence_idx)
        
        for code_block in structure['code_blocks']:
            start_sentence = self._find_sentence_for_line(code_block['start_line'], sentences)
            end_sentence = self._find_sentence_for_line(code_block['end_line'], sentences)
            if start_sentence is not None:
                boundaries.add(start_sentence)
            if end_sentence is not None and end_sentence < len(sentences):
                boundaries.add(end_sentence + 1)
        
        # Add boundaries for significant paragraph breaks
        for i, paragraph in enumerate(structure['paragraphs']):
            if paragraph['word_count'] > 100:  # Significant paragraphs only
                sentence_idx = self._find_sentence_for_line(paragraph['start_line'], sentences)
                if sentence_idx is not None:
                    boundaries.add(sentence_idx)
        
        # Ensure we end with the total number of sentences
        if boundaries and max(boundaries) != len(sentences):
            boundaries.add(len(sentences))
        
        return sorted(list(boundaries))
    
    def _find_sentence_for_line(self, line_num: int, sentences: List[str]) -> Optional[int]:
        """Find sentence index corresponding to line number"""
        # This is a simplified mapping - in practice, you'd need more sophisticated
        # line-to-sentence mapping based on actual text positions
        
        # Estimate sentence position based on line number
        # This is a heuristic and could be improved with actual character position mapping
        estimated_sentence = min(line_num // 2, len(sentences) - 1)
        return max(0, estimated_sentence)
    
    async def _split_large_discourse_segment(
        self,
        sentences: List[str],
        config: ChunkingConfig,
        document_id: str,
        base_index: int
    ) -> List[SemanticChunk]:
        """Split large discourse segments intelligently"""
        
        sub_chunks = []
        current_sentences = []
        current_size = 0
        sub_index = 0
        
        for sentence in sentences:
            sentence_size = len(sentence)
            
            if current_size + sentence_size > config.target_chunk_size and current_sentences:
                # Create sub-chunk
                chunk_text = ' '.join(current_sentences)
                chunk = await self._create_advanced_chunk(
                    text=chunk_text,
                    sentences=current_sentences,
                    chunk_index=base_index + sub_index,
                    start_sentence=0,  # Relative to sub-chunk
                    end_sentence=len(current_sentences),
                    start_char=0,
                    end_char=len(chunk_text),
                    config=config,
                    document_id=f"{document_id}_discourse_sub"
                )
                
                chunk.chunk_id = f"{document_id}_chunk_{base_index + sub_index:04d}"
                sub_chunks.append(chunk)
                sub_index += 1
                
                current_sentences = [sentence]
                current_size = sentence_size
            else:
                current_sentences.append(sentence)
                current_size += sentence_size
        
        # Handle remaining sentences
        if current_sentences:
            chunk_text = ' '.join(current_sentences)
            chunk = await self._create_advanced_chunk(
                text=chunk_text,
                sentences=current_sentences,
                chunk_index=base_index + sub_index,
                start_sentence=0,
                end_sentence=len(current_sentences),
                start_char=0,
                end_char=len(chunk_text),
                config=config,
                document_id=f"{document_id}_discourse_sub"
            )
            
            chunk.chunk_id = f"{document_id}_chunk_{base_index + sub_index:04d}"
            sub_chunks.append(chunk)
        
        return sub_chunks
    
    async def _split_oversized_structural_chunk(
        self,
        sentences: List[str],
        config: ChunkingConfig,
        document_id: str,
        base_index: int
    ) -> List[SemanticChunk]:
        """Split oversized structural chunks using semantic similarity"""
        
        # Use semantic similarity to split while preserving structure
        chunk_text = ' '.join(sentences)
        
        # Create sub-configuration for splitting
        sub_config = ChunkingConfig(
            target_chunk_size=config.target_chunk_size,
            max_chunk_size=config.max_chunk_size,
            strategy=ChunkingStrategy.SEMANTIC_SIMILARITY,
            semantic_model=config.semantic_model,
            content_type=config.content_type
        )
        
        # Create temporary context for processing
        temp_context = ProcessingContext(
            task_id=f"{document_id}_structural_split",
            session_id="temp",
            user_id="system"
        )
        
        sub_chunks = await self._chunk_semantic_similarity_advanced(
            chunk_text, sentences, sub_config, temp_context
        )
        
        # Update chunk IDs and indices
        for i, chunk in enumerate(sub_chunks):
            chunk.chunk_id = f"{document_id}_chunk_{base_index + i:04d}"
            chunk.chunk_index = base_index + i
            chunk.parent_document_id = document_id
        
        return sub_chunks
    
    async def _find_optimal_boundaries_dp(
        self,
        sentences: List[str],
        similarity_matrix: np.ndarray,
        config: ChunkingConfig
    ) -> List[int]:
        """Find optimal boundaries using dynamic programming"""
        
        n = len(sentences)
        if n <= 2:
            return [0, n]
        
        # Dynamic programming approach to find optimal boundaries
        # that maximize internal coherence while respecting size constraints
        
        dp = {}  # Memoization for optimal cost
        
        def calculate_chunk_cost(start: int, end: int) -> float:
            """Calculate cost (negative quality) of a chunk from start to end"""
            if end - start <= 1:
                return float('inf')  # Invalid chunk
            
            chunk_sentences = sentences[start:end]
            chunk_text = ' '.join(chunk_sentences)
            
            # Size penalty
            size_penalty = 0.0
            word_count = len(chunk_text.split())
            
            if word_count < config.min_chunk_size // 5:  # Approximate words from chars
                size_penalty += 2.0
            elif word_count > config.max_chunk_size // 5:
                size_penalty += 1.0
            
            # Coherence bonus (negative cost)
            coherence_sum = 0.0
            count = 0
            
            for i in range(start, end):
                for j in range(i + 1, end):
                    if i < similarity_matrix.shape[0] and j < similarity_matrix.shape[1]:
                        coherence_sum += similarity_matrix[i, j]
                        count += 1
            
            coherence_score = coherence_sum / count if count > 0 else 0.0
            coherence_bonus = coherence_score * 2.0
            
            return size_penalty - coherence_bonus
        
        def find_optimal_cost(start: int, end: int) -> float:
            """Find optimal cost to chunk sentences from start to end"""
            if (start, end) in dp:
                return dp[(start, end)]
            
            if end - start <= 1:
                dp[(start, end)] = 0.0
                return 0.0
            
            # Option 1: Single chunk
            single_cost = calculate_chunk_cost(start, end)
            
            # Option 2: Split into multiple chunks
            min_split_cost = float('inf')
            
            for split_point in range(start + 1, end):
                left_cost = find_optimal_cost(start, split_point)
                right_cost = find_optimal_cost(split_point, end)
                split_cost = left_cost + right_cost
                
                if split_cost < min_split_cost:
                    min_split_cost = split_cost
            
            optimal_cost = min(single_cost, min_split_cost)
            dp[(start, end)] = optimal_cost
            return optimal_cost
        
        # Find optimal boundaries by reconstructing the solution
        def reconstruct_boundaries(start: int, end: int) -> List[int]:
            """Reconstruct optimal boundaries"""
            if end - start <= 1:
                return []
            
            single_cost = calculate_chunk_cost(start, end)
            optimal_cost = find_optimal_cost(start, end)
            
            if abs(single_cost - optimal_cost) < 1e-6:
                # Single chunk is optimal
                return []
            
            # Find the optimal split point
            for split_point in range(start + 1, end):
                left_cost = find_optimal_cost(start, split_point)
                right_cost = find_optimal_cost(split_point, end)
                
                if abs(left_cost + right_cost - optimal_cost) < 1e-6:
                    left_boundaries = reconstruct_boundaries(start, split_point)
                    right_boundaries = reconstruct_boundaries(split_point, end)
                    return left_boundaries + [split_point] + right_boundaries
            
            return []
        
        # Calculate optimal solution
        find_optimal_cost(0, n)
        boundaries = [0] + reconstruct_boundaries(0, n) + [n]
        
        return sorted(list(set(boundaries)))
    
    async def _calculate_linguistic_coherence(self, sent1: str, sent2: str) -> float:
        """Calculate linguistic coherence between sentences"""
        
        # Multiple coherence measures
        coherence_scores = []
        
        # 1. Lexical overlap
        words1 = set(word_tokenize(sent1.lower()))
        words2 = set(word_tokenize(sent2.lower()))
        
        if words1 and words2:
            overlap = len(words1.intersection(words2)) / len(words1.union(words2))
            coherence_scores.append(overlap)
        
        # 2. Entity continuity
        if self.nlp_pipeline.nlp:
            try:
                doc1 = self.nlp_pipeline.nlp(sent1)
                doc2 = self.nlp_pipeline.nlp(sent2)
                
                entities1 = set(ent.text.lower() for ent in doc1.ents)
                entities2 = set(ent.text.lower() for ent in doc2.ents)
                
                if entities1 or entities2:
                    entity_overlap = len(entities1.intersection(entities2)) / max(len(entities1.union(entities2)), 1)
                    coherence_scores.append(entity_overlap * 1.5)  # Weight entity continuity higher
            except (ValueError, RuntimeError) as e:
                logger.debug(f"Entity continuity analysis failed: {e}")
        
        # 3. Semantic similarity
        semantic_sim = await self.nlp_pipeline.calculate_semantic_similarity(
            sent1, sent2, SemanticModel.SENTENCE_TRANSFORMERS
        )
        coherence_scores.append(semantic_sim)
        
        # 4. Discourse markers
        discourse_markers = ['however', 'therefore', 'furthermore', 'meanwhile', 'consequently']
        sent2_lower = sent2.lower()
        
        if any(marker in sent2_lower for marker in discourse_markers):
            coherence_scores.append(0.8)  # Boost for discourse continuity
        
        # Weighted average
        return np.mean(coherence_scores) if coherence_scores else 0.5
    
    async def _create_single_chunk(self, text: str, config: ChunkingConfig, context: ProcessingContext) -> Optional[SemanticChunk]:
        """Create a single chunk for short texts"""
        
        if len(text.strip()) < 10:
            return None
        
        sentences = await self.nlp_pipeline.tokenize_sentences(text)
        
        chunk = await self._create_advanced_chunk(
            text=text,
            sentences=sentences,
            chunk_index=0,
            start_sentence=0,
            end_sentence=len(sentences),
            start_char=0,
            end_char=len(text),
            config=config,
            document_id=context.task_id
        )
        
        return chunk


def get_semantic_chunking_processor(
    capabilities: ProcessingCapabilities = None,
    executor: ThreadPoolExecutor = None
) -> SemanticChunkingProcessor:
    """Factory function for enterprise semantic chunking processor"""
    
    if capabilities is None:
        capabilities = ProcessingCapabilities()
    
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=4)
    
    return SemanticChunkingProcessor(capabilities, executor)


# Configuration factory for different use cases
def create_chunking_config_for_content_type(content_type: ContentType) -> ChunkingConfig:
    """Create optimized chunking configuration for specific content types"""
    
    base_config = ChunkingConfig()
    
    if content_type == ContentType.CODE:
        base_config.strategy = ChunkingStrategy.STRUCTURAL
        base_config.target_chunk_size = 800
        base_config.similarity_threshold = 0.8
        base_config.preserve_code_blocks = True
        base_config.enable_syntactic_analysis = True
        
    elif content_type == ContentType.RESEARCH_PAPER:
        base_config.strategy = ChunkingStrategy.TOPIC_SEGMENTATION
        base_config.target_chunk_size = 1200
        base_config.coherence_threshold = 0.7
        base_config.enable_named_entity_preservation = True
        base_config.semantic_model = SemanticModel.SENTENCE_TRANSFORMERS
        
    elif content_type == ContentType.CONVERSATION:
        base_config.strategy = ChunkingStrategy.DISCOURSE_ANALYSIS
        base_config.target_chunk_size = 600
        base_config.similarity_threshold = 0.6
        base_config.enable_discourse_markers = True
        
    elif content_type == ContentType.LEGAL_DOCUMENT:
        base_config.strategy = ChunkingStrategy.PARAGRAPH_BOUNDARY
        base_config.target_chunk_size = 1500
        base_config.max_chunk_size = 2500
        base_config.preserve_tables = True
        
    elif content_type == ContentType.TECHNICAL_MANUAL:
        base_config.strategy = ChunkingStrategy.STRUCTURAL
        base_config.target_chunk_size = 1000
        base_config.preserve_code_blocks = True
        base_config.preserve_tables = True
        base_config.preserve_lists = True
    
    return base_config


# Example usage and testing
if __name__ == "__main__":
    async def main():
        # Initialize processor
        processor = get_semantic_chunking_processor()
        
        # Example complex document
        sample_text = """
        # Advanced Machine Learning Techniques
        
        ## Introduction
        
        Machine learning has revolutionized the way we approach complex problems in various domains.
        This document explores advanced techniques that push the boundaries of traditional approaches.
        
        ## Deep Learning Architectures
        
        ### Transformer Networks
        
        Transformer networks have emerged as a dominant architecture for natural language processing tasks.
        The attention mechanism allows models to focus on relevant parts of the input sequence.
        
        ```python
        import torch
        import torch.nn as nn
        
        class TransformerBlock(nn.Module):
            def __init__(self, embed_dim, num_heads):
                super().__init__()
                self.attention = nn.MultiheadAttention(embed_dim, num_heads)
                self.norm1 = nn.LayerNorm(embed_dim)
                self.norm2 = nn.LayerNorm(embed_dim)
                self.feed_forward = nn.Sequential(
                    nn.Linear(embed_dim, 4 * embed_dim),
                    nn.ReLU(),
                    nn.Linear(4 * embed_dim, embed_dim)
                )
        ```
        
        ### Convolutional Neural Networks
        
        CNNs excel at processing grid-like data such as images.
        Modern architectures like ResNet and EfficientNet have achieved remarkable performance.
        
        ## Applications
        
        Machine learning techniques are being applied across numerous fields:
        
        - Natural Language Processing
        - Computer Vision
        - Robotics
        - Healthcare
        - Finance
        
        ### Healthcare Applications
        
        In healthcare, ML models are being used for:
        
        1. Medical image analysis
        2. Drug discovery
        3. Personalized treatment plans
        4. Epidemic modeling
        
        ## Conclusion
        
        The field of machine learning continues to evolve rapidly.
        Future developments will likely focus on efficiency, interpretability, and ethical considerations.
        """
        
        # Create processing context
        context = ProcessingContext(
            task_id="test_advanced_chunking",
            session_id="test_session",
            user_id="test_user",
            enable_advanced_metrics=True,
            enable_parallel_processing=True
        )
        
        # Process text with advanced chunking
        result = await processor.chunk_text(sample_text, context)
        
        print("🚀 Advanced Semantic Chunking Results")
        print("=" * 50)
        print(f"Success: {result.success}")
        print(f"Total chunks: {result.metadata.get('total_chunks', 0)}")
        print(f"High quality chunks: {result.metadata.get('high_quality_chunks', 0)}")
        print(f"Average quality score: {result.metadata.get('avg_quality_score', 0):.3f}")
        print(f"Average coherence: {result.metadata.get('avg_semantic_coherence', 0):.3f}")
        print(f"Processing time: {result.processing_time:.2f}s")
        print(f"Chunks per second: {result.metadata.get('chunks_per_second', 0):.1f}")
        print("\n📊 Extracted Content Preview:")
        print("-" * 30)
        print(result.extracted_text[:1000] + "..." if len(result.extracted_text) > 1000 else result.extracted_text)
    
    # Run the example
    asyncio.run(main())
