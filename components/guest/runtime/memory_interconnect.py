#!/usr/bin/env python3
"""
Memory Interconnection Layer - SOTA BM25 Retrieval Engine

Provides Okapi BM25 semantic search, intelligent relationship mapping,
memory clustering, and knowledge gap analysis. Production-grade with
full thread safety and proper IDF computation.

BM25 parameters: k1=1.5, b=0.75 (standard Okapi BM25 defaults)
IDF formula: log((N - df + 0.5) / (df + 0.5) + 1) — Robertson-Spärck Jones
"""

import difflib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Sovereign Data Directory — single source of truth for all persistent state.
# Set SOVEREIGN_DATA_DIR env var to override; falls back to canonical MCP path.
# ---------------------------------------------------------------------------
_SOVEREIGN_DATA_DIR: Path = Path(
    os.environ.get("SOVEREIGN_DATA_DIR", r"/home/daeron/Somnus-MCP/data")
)

# ---------------------------------------------------------------------------
# Stopword list — 160+ words covering English function words + code keywords
# ---------------------------------------------------------------------------
STOPWORDS: Set[str] = {
    # Articles / determiners
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "each",
    "every",
    "either",
    "neither",
    "any",
    "all",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "than",
    "too",
    "very",
    # Prepositions
    "about",
    "above",
    "across",
    "after",
    "against",
    "along",
    "among",
    "around",
    "at",
    "before",
    "behind",
    "below",
    "beneath",
    "beside",
    "between",
    "beyond",
    "by",
    "down",
    "during",
    "except",
    "for",
    "from",
    "in",
    "inside",
    "into",
    "like",
    "near",
    "of",
    "off",
    "on",
    "onto",
    "out",
    "outside",
    "over",
    "past",
    "per",
    "since",
    "through",
    "throughout",
    "till",
    "to",
    "toward",
    "under",
    "until",
    "up",
    "upon",
    "with",
    "within",
    "without",
    # Conjunctions
    "and",
    "as",
    "because",
    "but",
    "either",
    "even",
    "if",
    "or",
    "since",
    "so",
    "still",
    "than",
    "though",
    "unless",
    "until",
    "when",
    "where",
    "whether",
    "which",
    "while",
    "who",
    "whom",
    "whose",
    "yet",
    # Pronouns
    "he",
    "her",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "i",
    "it",
    "its",
    "itself",
    "me",
    "mine",
    "my",
    "myself",
    "our",
    "ours",
    "ourselves",
    "she",
    "their",
    "theirs",
    "them",
    "themselves",
    "they",
    "us",
    "we",
    "what",
    "you",
    "your",
    "yours",
    "yourself",
    # Auxiliary verbs
    "are",
    "be",
    "been",
    "being",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "had",
    "has",
    "have",
    "having",
    "is",
    "may",
    "might",
    "must",
    "need",
    "ought",
    "shall",
    "should",
    "was",
    "were",
    "will",
    "would",
    # Common verbs (low semantic value in memory context)
    "get",
    "got",
    "give",
    "given",
    "go",
    "going",
    "gone",
    "make",
    "made",
    "put",
    "run",
    "say",
    "said",
    "see",
    "set",
    "take",
    "try",
    "use",
    "used",
    "using",
    "want",
    "work",
    "know",
    "think",
    "come",
    "look",
    "also",
    # Code-common low-value terms
    "return",
    "print",
    "import",
    "class",
    "self",
    "true",
    "false",
    "none",
    "null",
    "type",
    "value",
    "string",
    "list",
    "dict",
    "object",
    "item",
    "data",
    "result",
    "output",
    "input",
    "call",
    "arg",
    "args",
    "param",
    "params",
    "var",
    "func",
    "function",
    "method",
    "test",
    "pass",
    # Filler / meta
    "just",
    "now",
    "then",
    "here",
    "there",
    "often",
    "always",
    "never",
    "already",
    "still",
    "again",
    "back",
    "way",
    "well",
    "much",
    "many",
    "really",
    "actually",
    "basically",
    "simply",
    "maybe",
    "perhaps",
}

# Porter-lite stemmer: ordered suffix rules (longest first)
_SUFFIX_RULES: List[Tuple[str, str]] = [
    ("ations", ""),
    ("nesses", ""),
    ("ments", ""),
    ("tions", ""),
    ("ation", ""),
    ("ness", ""),
    ("ment", ""),
    ("tion", ""),
    ("ings", ""),
    ("edly", ""),
    ("ably", ""),
    ("ible", ""),
    ("able", ""),
    ("ful", ""),
    ("ous", ""),
    ("ive", ""),
    ("ing", ""),
    ("ess", ""),
    ("est", ""),
    ("ies", "y"),
    ("ied", "y"),
    ("ed", ""),
    ("er", ""),
    ("ly", ""),
    ("es", ""),
    ("s", ""),
]

_MIN_STEM_LEN = 4  # minimum root length after suffix removal


def _stem(word: str) -> str:
    """Porter-lite stemmer using suffix removal rules."""
    for suffix, replacement in _SUFFIX_RULES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM_LEN:
            return word[: -len(suffix)] + replacement
    return word


def _tokenize(text: str) -> List[str]:
    """
    Tokenize text for BM25 indexing.
    - Lowercase
    - Split on non-alphanumeric (preserving underscores in tech terms)
    - Remove stopwords
    - Apply Porter-lite stemming
    - Discard tokens shorter than 3 chars
    """
    if not text:
        return []
    text_lower = str(text).lower()
    # Split on whitespace and punctuation but keep underscores (snake_case)
    raw_tokens = re.findall(r"[a-z][a-z0-9_']*", text_lower)
    result = []
    for tok in raw_tokens:
        # Split on underscores for snake_case (keep both parts)
        parts = tok.split("_") if "_" in tok else [tok]
        for part in parts:
            part = part.strip("'")
            if len(part) < 3:
                continue
            if part in STOPWORDS:
                continue
            stemmed = _stem(part)
            if len(stemmed) >= 3 and stemmed not in STOPWORDS:
                result.append(stemmed)
    return result


class MemoryInterconnectionEngine:
    """
    SOTA BM25-powered memory relationship engine.

    Replaces Jaccard similarity with Okapi BM25 (k1=1.5, b=0.75) for all
    retrieval operations. Provides full thread safety, proper IDF computation,
    semantic clustering, and knowledge gap detection.
    """

    BM25_K1 = 1.5
    BM25_B = 0.75

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = str(_SOVEREIGN_DATA_DIR)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        self._lock = threading.Lock()

        # File paths for persistent indices
        self.relationships_file = self.data_dir / "memory_relationships.json"
        self.concept_index_file = self.data_dir / "concept_index.json"
        self.importance_scores_file = self.data_dir / "importance_scores.json"

        self._load_indices()
        self._last_rebuild_ts: float = 0.0
        self.logger.info(
            f"MemoryInterconnectionEngine initialised with BM25 "
            f"(k1={self.BM25_K1}, b={self.BM25_B}). "
            f"Docs: {self.concept_index.get('total_docs', 0)}"
        )

    # ------------------------------------------------------------------
    # Index persistence
    # ------------------------------------------------------------------

    def _load_indices(self) -> None:
        """Load persisted indices from disk; initialise empty if missing/corrupt."""
        try:
            if self.relationships_file.exists():
                with open(self.relationships_file, "r", encoding="utf-8") as f:
                    self.relationships = json.load(f)
            else:
                self.relationships = {
                    "memory_links": {},
                    "concept_map": {},
                    "session_connections": {},
                }

            if self.concept_index_file.exists():
                with open(self.concept_index_file, "r", encoding="utf-8") as f:
                    self.concept_index = json.load(f)
            else:
                self.concept_index = self._empty_concept_index()

            # Ensure new BM25 fields exist on old indices
            ci = self.concept_index
            ci.setdefault("doc_term_freq", {})
            ci.setdefault("doc_lengths", {})
            ci.setdefault("doc_frequency", {})
            ci.setdefault("avg_doc_length", 0.0)
            ci.setdefault("total_docs", 0)
            ci.setdefault("idf_dirty", True)
            ci.setdefault("concepts", {})

            if self.importance_scores_file.exists():
                with open(self.importance_scores_file, "r", encoding="utf-8") as f:
                    self.importance_scores = json.load(f)
            else:
                self.importance_scores = {}

        except Exception as e:
            self.logger.error(f"Error loading memory indices: {e}. Resetting.")
            self._initialize_empty_indices()

    @staticmethod
    def _empty_concept_index() -> Dict[str, Any]:
        return {
            "concepts": {},
            "doc_term_freq": {},  # {memory_id: {term: count}}
            "doc_lengths": {},  # {memory_id: total_token_count}
            "doc_frequency": {},  # {term: num_docs_containing_term}
            "avg_doc_length": 0.0,
            "total_docs": 0,
            "idf_dirty": True,
        }

    def _initialize_empty_indices(self) -> None:
        self.relationships = {
            "memory_links": {},
            "concept_map": {},
            "session_connections": {},
        }
        self.concept_index = self._empty_concept_index()
        self.importance_scores = {}

    @staticmethod
    def _atomic_write(path: Path, obj: Any) -> None:
        """Atomically write *obj* as JSON to *path* using a temp-file swap.

        Retries up to 8 times on Windows file-locking errors (winerror 5/32).
        Raises the last exception if all attempts fail.
        """
        serialized = json.dumps(obj, indent=2, ensure_ascii=False)
        last_error: Optional[Exception] = None

        for attempt in range(8):
            tmp = path.with_name(
                f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(serialized)
                os.replace(tmp, path)
                return
            except Exception as exc:
                last_error = exc
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass

                winerror = getattr(exc, "winerror", None)
                if isinstance(exc, PermissionError) or winerror in {5, 32}:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise

        if last_error is not None:
            raise last_error

    def _save_indices(self) -> None:
        """Atomically persist all indices to disk. Caller must hold self._lock."""
        try:
            self._atomic_write(self.relationships_file, self.relationships)
            self._atomic_write(self.concept_index_file, self.concept_index)
            self._atomic_write(self.importance_scores_file, self.importance_scores)
        except Exception as e:
            self.logger.error(f"Error saving memory indices: {e}")

    # ------------------------------------------------------------------
    # BM25 core machinery
    # ------------------------------------------------------------------

    def _update_doc_stats(self, memory_id: str, tokens: List[str]) -> None:
        """
        Update per-document TF stats and global doc-frequency counts.
        Must be called under self._lock.
        """
        ci = self.concept_index
        old_tokens = ci["doc_term_freq"].get(memory_id, {})

        # Decrement old doc_frequency counts if this memory_id already indexed
        if old_tokens:
            for term in old_tokens:
                ci["doc_frequency"][term] = max(ci["doc_frequency"].get(term, 1) - 1, 0)
                if ci["doc_frequency"][term] == 0:
                    del ci["doc_frequency"][term]
            # Remove from total_docs count (we'll re-add below)
            ci["total_docs"] = max(ci.get("total_docs", 1) - 1, 0)

        # Compute new TF
        tf: Dict[str, int] = {}
        for tok in tokens:
            tf[tok] = tf.get(tok, 0) + 1

        ci["doc_term_freq"][memory_id] = tf
        ci["doc_lengths"][memory_id] = len(tokens)
        ci["total_docs"] = ci.get("total_docs", 0) + 1

        # Update doc_frequency
        for term in tf:
            ci["doc_frequency"][term] = ci["doc_frequency"].get(term, 0) + 1

        # Recompute avg_doc_length
        if ci["doc_lengths"]:
            ci["avg_doc_length"] = sum(ci["doc_lengths"].values()) / len(
                ci["doc_lengths"]
            )

        ci["idf_dirty"] = True

    def _compute_idf(self) -> Dict[str, float]:
        """
        Compute IDF for all terms using Robertson-Spärck Jones formula.
        Result is cached until idf_dirty is set.
        """
        ci = self.concept_index
        n = max(ci.get("total_docs", 0), 1)
        idf: Dict[str, float] = {}
        for term, df in ci.get("doc_frequency", {}).items():
            idf[term] = math.log((n - df + 0.5) / (df + 0.5) + 1)
        ci["idf_dirty"] = False
        return idf

    def _get_idf(self) -> Dict[str, float]:
        """Return IDF dict, recomputing if dirty. Caller must hold self._lock."""
        if not hasattr(self, "_idf_cache") or self.concept_index.get("idf_dirty", True):
            self._idf_cache = self._compute_idf()
            self._last_rebuild_ts = time.time()
        return self._idf_cache

    def _bm25_score(
        self,
        query_tokens: List[str],
        memory_id: str,
        idf: Dict[str, float],
    ) -> float:
        """
        Compute Okapi BM25 score for a single document against query tokens.
        score = Σ_term [ IDF(t) * tf*(k1+1) / (tf + k1*(1-b+b*|D|/avgdl)) ]
        """
        ci = self.concept_index
        doc_tf = ci["doc_term_freq"].get(memory_id, {})
        if not doc_tf:
            return 0.0

        doc_len = ci["doc_lengths"].get(memory_id, 0)
        avgdl = max(ci.get("avg_doc_length", 1.0), 1.0)
        k1 = self.BM25_K1
        b = self.BM25_B
        score = 0.0

        for term in query_tokens:
            if term not in idf:
                continue
            tf = doc_tf.get(term, 0)
            if tf == 0:
                continue
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1.0 - b + b * doc_len / avgdl)
            score += idf[term] * (numerator / denominator)

        return score

    # ------------------------------------------------------------------
    # Public API — concept extraction (kept for backward compat)
    # ------------------------------------------------------------------

    def extract_concepts(self, text: str) -> List[str]:
        """
        Extract key concepts using the BM25 tokenizer.
        Returns de-duplicated list capped at 30 terms.
        """
        if not text:
            return []
        tokens = _tokenize(str(text))

        # Also add camelCase / UPPER_CASE technical terms verbatim (pre-stemming)
        tech_terms = re.findall(
            r"\b[a-zA-Z]+[A-Z][a-zA-Z]*\b|\b[A-Z_]{2,}\b", str(text)
        )
        for t in tech_terms:
            stemmed = _stem(t.lower())
            if len(stemmed) >= 3 and stemmed not in STOPWORDS:
                tokens.append(stemmed)

        return list(dict.fromkeys(tokens))[:30]  # preserve insertion order, dedupe

    def calculate_semantic_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate semantic similarity between two texts using BM25.
        Treats text1 as query, text2 as document; then averages with reverse.
        Normalised to [0, 1].
        """
        if not text1 or not text2:
            return 0.0

        q1 = _tokenize(str(text1))
        q2 = _tokenize(str(text2))

        if not q1 or not q2:
            return difflib.SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

        # Build temporary in-memory index for just these two texts
        n = 2
        df: Dict[str, int] = {}
        tf1: Dict[str, int] = Counter(q1)
        tf2: Dict[str, int] = Counter(q2)
        all_terms = set(q1) | set(q2)
        for term in all_terms:
            df[term] = (1 if term in tf1 else 0) + (1 if term in tf2 else 0)

        avgdl = (len(q1) + len(q2)) / 2.0
        k1, b = self.BM25_K1, self.BM25_B

        def _score(
            query_tf: Dict[str, int], doc_tf: Dict[str, int], doc_len: int
        ) -> float:
            s = 0.0
            for term, qtf in query_tf.items():
                if term not in doc_tf:
                    continue
                idf_val = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1)
                tf = doc_tf[term]
                num = tf * (k1 + 1)
                den = tf + k1 * (1 - b + b * doc_len / avgdl)
                s += idf_val * (num / den)
            return s

        s12 = _score(tf1, tf2, len(q2))
        s21 = _score(tf2, tf1, len(q1))

        # Normalise: max possible score is when query==document
        max12 = _score(tf1, tf1, len(q1)) or 1.0
        max21 = _score(tf2, tf2, len(q2)) or 1.0

        norm1 = min(s12 / max12, 1.0) if max12 > 0 else 0.0
        norm2 = min(s21 / max21, 1.0) if max21 > 0 else 0.0

        return (norm1 + norm2) / 2.0

    # ------------------------------------------------------------------
    # Core indexing
    # ------------------------------------------------------------------

    def analyze_memory_entry(
        self, key: str, value: str, source: str = "memory"
    ) -> Dict[str, Any]:
        """
        Index a memory entry: extract tokens, update BM25 stats,
        build concept index, find related memories.
        """
        tokens = _tokenize(str(value) + " " + str(key))
        concepts = list(dict.fromkeys(tokens))[:30]

        importance = self._calculate_importance(str(value), concepts)
        memory_id = f"{source}:{key}"

        with self._lock:
            # BM25 doc stats
            self._update_doc_stats(memory_id, tokens)

            # Importance score record
            self.importance_scores[memory_id] = {
                "score": importance,
                "timestamp": time.time(),
                "concepts": concepts,
            }

            # Concept index (concept → [memory_ids])
            for concept in concepts:
                refs = self.concept_index["concepts"].setdefault(concept, [])
                if memory_id not in refs:
                    refs.append(memory_id)

            # Find related memories (BM25-ranked)
            idf = self._get_idf()
            related = self._find_related_memories_locked(key, tokens, source, idf)

            # Relationship graph
            self.relationships["memory_links"][memory_id] = {
                "concepts": concepts,
                "related": related,
                "importance": importance,
                "last_updated": time.time(),
            }

            self._save_indices()

        return {
            "concepts": concepts,
            "importance": importance,
            "related_memories": related,
        }

    def _calculate_importance(self, content: str, concepts: List[str]) -> float:
        """Heuristic importance score combining length, richness, and keyword signals."""
        importance = 0.0
        importance += min(len(content) / 1500.0, 0.25)  # length factor
        importance += min(len(concepts) * 0.04, 0.30)  # concept richness

        content_lower = content.lower()
        tech_kws = [
            "error",
            "debug",
            "fix",
            "solution",
            "bug",
            "issue",
            "problem",
            "resolv",
            "crash",
            "fail",
            "except",
        ]
        importance += min(sum(0.08 for kw in tech_kws if kw in content_lower), 0.25)

        insight_kws = [
            "decid",
            "learn",
            "discov",
            "realiz",
            "important",
            "key",
            "critical",
            "crucial",
            "must",
            "alway",
        ]
        importance += min(sum(0.08 for kw in insight_kws if kw in content_lower), 0.25)

        return round(min(importance, 1.0), 4)

    def _find_related_memories_locked(
        self,
        key: str,
        query_tokens: List[str],
        source: str,
        idf: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        BM25-rank all indexed memories against query_tokens.
        Caller must hold self._lock.
        Returns top-10 related (excluding self).
        """
        current_id = f"{source}:{key}"
        related: List[Dict[str, Any]] = []

        for memory_id in self.relationships["memory_links"]:
            if memory_id == current_id:
                continue
            score = self._bm25_score(query_tokens, memory_id, idf)
            if score > 0.05:
                related.append(
                    {
                        "memory_id": memory_id,
                        "similarity": round(score, 4),
                        "importance": self.relationships["memory_links"][memory_id].get(
                            "importance", 0.0
                        ),
                    }
                )

        related.sort(
            key=lambda x: x["similarity"] * 0.7 + x["importance"] * 0.3,
            reverse=True,
        )
        return related[:10]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def intelligent_search(
        self, query: str, max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """BM25-ranked search across all indexed memories."""
        query_tokens = _tokenize(str(query))
        if not query_tokens:
            return []

        with self._lock:
            idf = self._get_idf()
            results: List[Dict[str, Any]] = []

            for memory_id in self.relationships["memory_links"]:
                score = self._bm25_score(query_tokens, memory_id, idf)
                if score > 0.0:
                    results.append(
                        {
                            "memory_id": memory_id,
                            "relevance_score": round(score, 4),
                            "matched_concepts": [
                                t
                                for t in query_tokens
                                if t
                                in self.concept_index["doc_term_freq"].get(
                                    memory_id, {}
                                )
                            ],
                            "importance": self.relationships["memory_links"][
                                memory_id
                            ].get("importance", 0.0),
                        }
                    )

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results[:max_results]

    # ------------------------------------------------------------------
    # Index health & forced rebuild  (F1 + F4 SOTA++ fix)
    # ------------------------------------------------------------------

    def index_stats(self) -> Dict[str, Any]:
        """
        Return BM25 index health metrics.

        Compares indexed document count (in doc_term_freq) against the
        relationship graph (memory_links) and reports staleness.
        Use this in bb7_memory_stats to give truthful index metrics.
        """
        with self._lock:
            ci = self.concept_index
            indexed_docs = len(ci.get("doc_term_freq", {}))
            link_docs = len(self.relationships.get("memory_links", {}))
            is_dirty = ci.get("idf_dirty", True)
            total_docs = ci.get("total_docs", 0)
            avg_dl = ci.get("avg_doc_length", 0.0)
            vocab_size = len(ci.get("doc_frequency", {}))
            last_rebuild = getattr(self, "_last_rebuild_ts", 0.0)

        return {
            "indexed_docs": indexed_docs,
            "relationship_docs": link_docs,
            "total_docs_counter": total_docs,
            "is_stale": is_dirty or (indexed_docs != link_docs),
            "idf_dirty": is_dirty,
            "avg_doc_length": round(avg_dl, 2),
            "vocab_size": vocab_size,
            "last_rebuild_ts": last_rebuild,
        }

    def force_rebuild(self) -> Dict[str, Any]:
        """
        Force a full IDF rebuild and consistency check.

        Ensures doc_term_freq, doc_frequency, concept_index["concepts"],
        importance_scores, and memory_links are all aligned with each other.
        Call after bulk operations to guarantee search correctness.
        Returns stats about what was repaired and pruned.
        """
        repaired = 0
        with self._lock:
            ci = self.concept_index
            valid_ids: Set[str] = set(self.relationships.get("memory_links", {}).keys())

            # Collect all stale IDs across every index structure, then prune once.
            stale_ids: Set[str] = set()

            # ── Pass 0a: prune doc_term_freq / doc_lengths for IDs absent from memory_links
            for mid in list(ci.get("doc_term_freq", {}).keys()):
                if mid not in valid_ids:
                    ci["doc_term_freq"].pop(mid, None)
                    ci["doc_lengths"].pop(mid, None)
                    stale_ids.add(mid)

            # ── Pass 0b: prune importance_scores for IDs absent from memory_links
            for mid in list(self.importance_scores.keys()):
                if mid not in valid_ids:
                    self.importance_scores.pop(mid, None)
                    stale_ids.add(mid)

            # ── Pass 0c: prune concept_index["concepts"] entries pointing to absent IDs
            for concept, refs in list(ci.get("concepts", {}).items()):
                cleaned = [r for r in refs if r in valid_ids]
                if cleaned:
                    ci["concepts"][concept] = cleaned
                else:
                    del ci["concepts"][concept]

            # ── Pass 1: re-index any memory_link entry missing from doc_term_freq
            for memory_id in list(valid_ids):
                if memory_id not in ci.get("doc_term_freq", {}):
                    link_info = self.relationships["memory_links"][memory_id]
                    concepts = link_info.get("concepts", [])
                    if concepts:
                        self._update_doc_stats(memory_id, concepts)
                        repaired += 1

            # ── Pass 2: reconcile total_docs counter with actual doc_term_freq
            actual_docs = len(ci.get("doc_term_freq", {}))
            if ci.get("total_docs", 0) != actual_docs:
                ci["total_docs"] = actual_docs

            # ── Pass 3: recompute avg_doc_length from scratch
            doc_lengths = ci.get("doc_lengths", {})
            if doc_lengths:
                ci["avg_doc_length"] = sum(doc_lengths.values()) / len(doc_lengths)
            else:
                ci["avg_doc_length"] = 0.0

            # ── Pass 4: rebuild doc_frequency from scratch for full consistency
            doc_freq: Dict[str, int] = {}
            for tf_dict in ci.get("doc_term_freq", {}).values():
                for term in tf_dict:
                    doc_freq[term] = doc_freq.get(term, 0) + 1
            ci["doc_frequency"] = doc_freq

            # ── Pass 5: full IDF recompute
            self._idf_cache = self._compute_idf()
            ci["idf_dirty"] = False
            self._last_rebuild_ts = time.time()

            self._save_indices()

        return {
            "status": "rebuilt",
            "repaired_entries": repaired,
            "pruned_entries": len(stale_ids),
            "stats": self.index_stats(),
        }

    # ------------------------------------------------------------------
    # Concept network
    # ------------------------------------------------------------------

    def get_concept_network(self, concept: str) -> Dict[str, Any]:
        """Get memories and co-occurring concepts for a given concept."""
        stemmed_concept = _stem(concept.lower())
        with self._lock:
            # Try exact match first, then stemmed
            refs = (
                self.concept_index["concepts"].get(concept)
                or self.concept_index["concepts"].get(stemmed_concept)
                or []
            )

            related_concepts: Counter = Counter()
            for ref in refs:
                if ref in self.relationships["memory_links"]:
                    for c in self.relationships["memory_links"][ref].get(
                        "concepts", []
                    ):
                        if c not in (concept, stemmed_concept):
                            related_concepts[c] += 1

        return {
            "concept": concept,
            "memories": refs,
            "related_concepts": dict(related_concepts.most_common(10)),
            "total_references": len(refs),
        }

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def cluster_memories(self, n_clusters: int = 5) -> str:
        """
        Greedy BM25 clustering of all indexed memories.

        Algorithm:
          1. Pick unassigned memory with highest importance as centroid.
          2. Assign all unassigned memories with BM25 similarity > 0.25 to cluster.
          3. Repeat until n_clusters reached or no unassigned remain.

        Returns a formatted string describing each cluster.
        """
        with self._lock:
            idf = self._get_idf()
            all_ids = list(self.relationships["memory_links"].keys())

        if not all_ids:
            return "No memories indexed yet — nothing to cluster."

        # Sort by importance (descending) to pick best centroids first
        sorted_ids = sorted(
            all_ids,
            key=lambda mid: self.relationships["memory_links"][mid].get(
                "importance", 0
            ),
            reverse=True,
        )

        clusters: Dict[str, List[str]] = {}
        assigned: Set[str] = set()

        for centroid in sorted_ids:
            if centroid in assigned:
                continue
            if len(clusters) >= n_clusters:
                break

            cluster_label = centroid
            members = [centroid]
            assigned.add(centroid)

            # Score every unassigned memory against centroid's tokens
            centroid_tokens = list(
                self.concept_index["doc_term_freq"].get(centroid, {}).keys()
            )

            with self._lock:
                for candidate in sorted_ids:
                    if candidate in assigned:
                        continue
                    score = self._bm25_score(centroid_tokens, candidate, idf)
                    # Normalise against self-score
                    self_score = self._bm25_score(centroid_tokens, centroid, idf) or 1.0
                    if score / self_score > 0.25:
                        members.append(candidate)
                        assigned.add(candidate)

            clusters[cluster_label] = members

        # Format output
        lines = [
            f"Memory Clusters ({len(clusters)} clusters, {len(assigned)}/{len(all_ids)} memories assigned)\n"
        ]
        for i, (centroid_id, members) in enumerate(clusters.items(), 1):
            short_id = (
                centroid_id.split(":", 1)[1] if ":" in centroid_id else centroid_id
            )
            concepts = (
                self.relationships["memory_links"]
                .get(centroid_id, {})
                .get("concepts", [])[:4]
            )
            lines.append(f"Cluster {i}: '{short_id}' ({len(members)} members)")
            if concepts:
                lines.append(f"  Key concepts: {', '.join(concepts)}")
            for m in members[:5]:
                short_m = m.split(":", 1)[1] if ":" in m else m
                lines.append(f"  - {short_m}")
            if len(members) > 5:
                lines.append(f"  ... and {len(members) - 5} more")
            lines.append("")

        # Unassigned memories
        unassigned = [mid for mid in all_ids if mid not in assigned]
        if unassigned:
            lines.append(f"Unclustered: {len(unassigned)} memories")
            for mid in unassigned[:3]:
                short = mid.split(":", 1)[1] if ":" in mid else mid
                lines.append(f"  - {short}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Knowledge gap detection
    # ------------------------------------------------------------------

    def find_knowledge_gaps(self, min_frequency: int = 3) -> str:
        """
        Identify concepts referenced in 3+ memories that lack a dedicated
        memory entry. These are 'gaps' — topics that matter but aren't
        explicitly documented.

        Returns formatted list of gap concepts with frequency and examples.
        """
        with self._lock:
            concept_refs = self.concept_index.get("concepts", {})
            indexed_keys = set()
            for mid in self.relationships["memory_links"]:
                key = mid.split(":", 1)[1] if ":" in mid else mid
                indexed_keys.add(_stem(key.lower()))

        gaps: List[Dict[str, Any]] = []
        for concept, refs in concept_refs.items():
            if len(refs) < min_frequency:
                continue
            # Check if any memory key matches this concept
            if concept in indexed_keys:
                continue
            # Also check stemmed version
            if _stem(concept) in indexed_keys:
                continue

            example_keys = [r.split(":", 1)[1] if ":" in r else r for r in refs[:3]]
            gaps.append(
                {
                    "concept": concept,
                    "frequency": len(refs),
                    "example_memories": example_keys,
                }
            )

        gaps.sort(key=lambda x: x["frequency"], reverse=True)

        if not gaps:
            return f"No knowledge gaps found (min_frequency={min_frequency}). Your memory is well-documented!"

        lines = [
            f"Knowledge Gaps — {len(gaps)} concepts mentioned {min_frequency}+ times without dedicated entries:\n"
        ]
        for i, gap in enumerate(gaps[:20], 1):
            lines.append(
                f"{i:2d}. '{gap['concept']}' — referenced in {gap['frequency']} memories"
            )
            lines.append(f"      Examples: {', '.join(gap['example_memories'])}")

        if len(gaps) > 20:
            lines.append(f"\n... and {len(gaps) - 20} more gaps")

        lines.append(
            "\nTip: Use bb7_memory_store to create dedicated entries for these concepts."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Memory graph export (DOT format)
    # ------------------------------------------------------------------

    def get_memory_graph(self, min_similarity: float = 0.3) -> str:
        """
        Export the memory relationship graph in Graphviz DOT format.
        Only includes edges where similarity >= min_similarity.
        """
        with self._lock:
            links = dict(self.relationships["memory_links"])

        lines = ["digraph memory_graph {", '  rankdir="LR";', "  node [shape=box];", ""]
        edge_count = 0

        for memory_id, data in links.items():
            short_from = memory_id.split(":", 1)[1] if ":" in memory_id else memory_id
            short_from = short_from.replace('"', '\\"')
            importance = data.get("importance", 0.0)
            lines.append(
                f'  "{short_from}" [style=filled, '
                f'fillcolor="{self._importance_color(importance)}"];'
            )
            for rel in data.get("related", []):
                sim = rel.get("similarity", 0.0)
                if sim < min_similarity:
                    continue
                short_to = (
                    rel["memory_id"].split(":", 1)[1]
                    if ":" in rel["memory_id"]
                    else rel["memory_id"]
                )
                short_to = short_to.replace('"', '\\"')
                lines.append(f'  "{short_from}" -> "{short_to}" [label="{sim:.2f}"];')
                edge_count += 1

        lines.append("}")
        return (
            "\n".join(lines)
            + f"\n// {len(links)} nodes, {edge_count} edges (min_sim={min_similarity})"
        )

    @staticmethod
    def _importance_color(importance: float) -> str:
        """Map importance [0,1] to a light colour for DOT graph fill."""
        if importance >= 0.7:
            return "#ffdddd"  # reddish — high importance
        if importance >= 0.4:
            return "#ffffdd"  # yellow — medium
        return "#ddffdd"  # greenish — low

    # ------------------------------------------------------------------
    # Insights (returns formatted string for MCP protocol)
    # ------------------------------------------------------------------

    def get_memory_insights(self) -> str:
        """Generate formatted insights report about the memory network."""
        with self._lock:
            total_memories = len(self.relationships["memory_links"])
            total_concepts = len(self.concept_index.get("concepts", {}))
            links_data = dict(self.relationships["memory_links"])
            concept_data = dict(self.concept_index.get("concepts", {}))

        if total_memories == 0:
            return "Memory network is empty — no insights available yet."

        # Top memories by importance
        top_mems = sorted(
            [(mid, d.get("importance", 0.0)) for mid, d in links_data.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:5]

        # Most connected concepts
        top_concepts = sorted(
            ((c, len(refs)) for c, refs in concept_data.items()),
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        # Network density
        total_connections = sum(len(d.get("related", [])) for d in links_data.values())
        avg_connections = (
            total_connections / total_memories if total_memories > 0 else 0
        )
        density = (
            total_connections / (total_memories * (total_memories - 1))
            if total_memories > 1
            else 0
        )

        lines = [
            "Memory Network Insights",
            "=" * 45,
            f"Total indexed memories : {total_memories}",
            f"Total unique concepts  : {total_concepts}",
            f"Total relationships    : {total_connections}",
            f"Avg connections/memory : {avg_connections:.1f}",
            f"Network density        : {density:.4f}",
            f"BM25 index docs        : {self.concept_index.get('total_docs', 0)}",
            f"Avg document length    : {self.concept_index.get('avg_doc_length', 0):.1f} tokens",
            "",
            "Top 5 Memories by Importance:",
        ]
        for mid, imp in top_mems:
            short = mid.split(":", 1)[1] if ":" in mid else mid
            lines.append(f"  {imp:.3f}  {short}")

        lines.append("")
        lines.append("Top 10 Concepts by Memory Coverage:")
        for concept, count in top_concepts:
            lines.append(f"  {count:3d} memories  {concept}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Consolidation (bug-fixed: now cleans concept_index too)
    # ------------------------------------------------------------------

    def consolidate_memories(
        self, age_threshold_days: int = 30, explicit_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Archive low-importance, old memories and clean all indices.
        Fixes original bug where concept_index was not pruned on archive.

        If explicit_ids is provided, those specific IDs are archived regardless of age/importance.
        """
        current_time = time.time()
        threshold_time = current_time - (age_threshold_days * 86400)

        with self._lock:
            to_archive_set: Set[str] = set()
            if explicit_ids:
                # Only archive IDs that actually exist in our index
                for eid in explicit_ids:
                    if eid in self.relationships["memory_links"]:
                        to_archive_set.add(eid)
            else:
                for memory_id, data in self.relationships["memory_links"].items():
                    if (
                        data.get("last_updated", current_time) < threshold_time
                        and data.get("importance", 0.0) < 0.3
                    ):
                        to_archive_set.add(memory_id)

            if not to_archive_set:
                return {
                    "consolidated": 0,
                    "archived": 0,
                    "archive_file": None,
                    "message": f"No memories older than {age_threshold_days} days with importance < 0.3",
                }

            # Write archive atomically so it is never left half-written
            archive_file = self.data_dir / f"memory_archive_{int(current_time)}.json"
            archive_data = {
                "archived_at": current_time,
                "age_threshold_days": age_threshold_days,
                "memories": {
                    mid: self.relationships["memory_links"][mid] for mid in to_archive_set
                },
            }
            self._atomic_write(archive_file, archive_data)

            to_archive_set = set(to_archive)

            # Remove from relationship graph
            for mid in to_archive_set:
                del self.relationships["memory_links"][mid]

            # Prune cross-references in remaining memories
            for data in self.relationships["memory_links"].values():
                if "related" in data:
                    data["related"] = [r for r in data["related"] if r.get("memory_id") not in to_archive_set]

            # Prune concept_index["concepts"]
            # --- FIX: prune cross-references in remaining memory_links ---
            for mid, link_info in self.relationships["memory_links"].items():
                if "related" in link_info:
                    link_info["related"] = [
                        r for r in link_info["related"]
                        if r.get("memory_id") not in to_archive_set
                    ]

            # --- FIX: prune concept_index["concepts"] ---
            for concept, refs in list(self.concept_index["concepts"].items()):
                cleaned = [r for r in refs if r not in to_archive_set]
                if cleaned:
                    self.concept_index["concepts"][concept] = cleaned
                else:
                    del self.concept_index["concepts"][concept]

            # Prune BM25 doc stats
            ci = self.concept_index
            for mid in to_archive_set:
                old_tf = ci["doc_term_freq"].pop(mid, {})
                ci["doc_lengths"].pop(mid, None)
                ci["total_docs"] = max(ci.get("total_docs", 0) - 1, 0)
                for term in old_tf:
                    ci["doc_frequency"][term] = max(
                        ci["doc_frequency"].get(term, 1) - 1, 0
                    )
                    if ci["doc_frequency"][term] == 0:
                        del ci["doc_frequency"][term]

                self.importance_scores.pop(mid, None)

            # Recompute avg_doc_length
            if ci["doc_lengths"]:
                ci["avg_doc_length"] = sum(ci["doc_lengths"].values()) / len(
                    ci["doc_lengths"]
                )
            else:
                ci["avg_doc_length"] = 0.0
            ci["idf_dirty"] = True

            self._save_indices()

        return {
            "consolidated": len(to_archive_set),
            "archived": len(to_archive_set),
            "archive_file": str(archive_file),
            "pruned_concept_refs": True,
            "pruned_related_links": True,
        }


    @staticmethod
    def _merge_tool_arguments(
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
        param_names: List[str],
    ) -> Dict[str, Any]:
        """Merge positional, kwargs, and single-dict invocation styles."""
        merged: Dict[str, Any] = {}
        if args:
            if len(args) == 1 and isinstance(args[0], dict):
                merged.update(args[0])
            else:
                for name, value in zip(param_names, args):
                    merged[name] = value
        merged.update(kwargs)
        return merged

    def _normalize_tool_registry(self, tools: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Wrap tools so they tolerate kwargs, dict-style, and positional calls."""

        def _wrap(tool_fn: Callable[..., Any], param_names: List[str]) -> Callable[..., Any]:
            def _wrapped(*args: Any, **kwargs: Any) -> Any:
                merged = self._merge_tool_arguments(args, kwargs, param_names)

                if not merged:
                    try:
                        return tool_fn()
                    except TypeError:
                        pass

                try:
                    return tool_fn(**merged)
                except TypeError as first_error:
                    if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
                        try:
                            return tool_fn(args[0])
                        except TypeError:
                            pass
                    if not param_names:
                        # Legacy dispatchers may pass an arguments dict even for no-arg tools.
                        return tool_fn()
                    raise first_error

            return _wrapped

        normalized: Dict[str, Dict[str, Any]] = {}
        for tool_name, tool_def in tools.items():
            if not isinstance(tool_def, dict):
                normalized[tool_name] = tool_def
                continue

            wrapped = dict(tool_def)
            raw_fn = wrapped.get("function")
            param_names = [
                p.get("name")
                for p in wrapped.get("parameters", [])
                if isinstance(p, dict) and p.get("name")
            ]
            if callable(raw_fn):
                wrapped["function"] = _wrap(raw_fn, param_names)
            normalized[tool_name] = wrapped

        return normalized

    # ------------------------------------------------------------------
    # MCP tool registration
    # ------------------------------------------------------------------

    def get_tools(self) -> Dict[str, Any]:
        """Return all MCP tool definitions with bb7_ prefix."""
        tools: Dict[str, Dict[str, Any]] = {
            "bb7_memory_analyze_entry": {
                "function": lambda key, value, source="memory": str(
                    self.analyze_memory_entry(key, value, source)
                ),
                "description": (
                    "Index a memory entry using BM25, extract concepts, "
                    "and map relationships to existing memories."
                ),
                "parameters": [
                    {
                        "name": "key",
                        "type": "string",
                        "required": True,
                        "description": "Memory key identifier.",
                    },
                    {
                        "name": "value",
                        "type": "string",
                        "required": True,
                        "description": "Memory content to index.",
                    },
                    {
                        "name": "source",
                        "type": "string",
                        "required": False,
                        "description": "Source namespace (default: 'memory').",
                    },
                ],
            },
            "bb7_memory_intelligent_search": {
                "function": lambda query, max_results=10: self._format_search_results(
                    self.intelligent_search(query, int(max_results)), query
                ),
                "description": (
                    "BM25-ranked semantic search across all indexed memories. "
                    "Significantly more accurate than keyword search."
                ),
                "parameters": [
                    {
                        "name": "query",
                        "type": "string",
                        "required": True,
                        "description": "Search query.",
                    },
                    {
                        "name": "max_results",
                        "type": "number",
                        "required": False,
                        "description": "Maximum results to return (default: 10).",
                    },
                ],
            },
            "bb7_memory_get_insights": {
                "function": self.get_memory_insights,
                "description": "Formatted insights report about the memory network: density, top concepts, important memories.",
                "parameters": [],
            },
            "bb7_memory_cluster": {
                "function": lambda n_clusters=5: self.cluster_memories(int(n_clusters)),
                "description": "Group indexed memories into BM25 semantic clusters.",
                "parameters": [
                    {
                        "name": "n_clusters",
                        "type": "number",
                        "required": False,
                        "description": "Target number of clusters (default: 5).",
                    },
                ],
            },
            "bb7_memory_find_gaps": {
                "function": lambda min_frequency=3: self.find_knowledge_gaps(
                    int(min_frequency)
                ),
                "description": "Find concepts referenced in multiple memories but lacking a dedicated entry.",
                "parameters": [
                    {
                        "name": "min_frequency",
                        "type": "number",
                        "required": False,
                        "description": "Minimum reference count to flag as a gap (default: 3).",
                    },
                ],
            },
            "bb7_memory_graph": {
                "function": lambda min_similarity=0.3: self.get_memory_graph(
                    float(min_similarity)
                ),
                "description": "Export memory relationship graph in Graphviz DOT format for visualisation.",
                "parameters": [
                    {
                        "name": "min_similarity",
                        "type": "number",
                        "required": False,
                        "description": "Minimum BM25 similarity to draw an edge (default: 0.3).",
                    },
                ],
            },
            "bb7_memory_consolidate_index": {
                "function": lambda age_threshold_days=30, explicit_ids=None: str(
                    self.consolidate_memories(int(age_threshold_days), explicit_ids)
                ),
                "description": (
                    "Archive low-importance old memories and prune all indices. "
                    "Fixes orphaned concept refs from the original implementation."
                ),
                "parameters": [
                    {
                        "name": "age_threshold_days",
                        "type": "number",
                        "required": False,
                        "description": "Age threshold in days for archival (default: 30).",
                    },
                    {
                        "name": "explicit_ids",
                        "type": "array",
                        "items": {"type": "string"},
                        "required": False,
                        "description": "Optional list of specific memory IDs to archive.",
                    },
                ],
            },
            "bb7_memory_concept_network": {
                "function": lambda concept: str(self.get_concept_network(concept)),
                "description": "Get the network of memories and related concepts for a given concept.",
                "parameters": [
                    {
                        "name": "concept",
                        "type": "string",
                        "required": True,
                        "description": "Concept to explore.",
                    },
                ],
            },
            "bb7_memory_extract_concepts": {
                "function": lambda text: (
                    ", ".join(self.extract_concepts(text)) or "No concepts extracted."
                ),
                "description": "Extract key BM25 tokens/concepts from text.",
                "parameters": [
                    {
                        "name": "text",
                        "type": "string",
                        "required": True,
                        "description": "Text to analyse.",
                    },
                ],
            },
        }
        return self._normalize_tool_registry(tools)

    def _format_search_results(self, results: List[Dict[str, Any]], query: str) -> str:
        """Format BM25 search results as human-readable string."""
        if not results:
            return f"No memories found matching '{query}'"
        lines = [f"BM25 search results for '{query}' ({len(results)} found):\n"]
        for i, r in enumerate(results, 1):
            mid = r["memory_id"]
            key = mid.split(":", 1)[1] if ":" in mid else mid
            score = r["relevance_score"]
            matched = r.get("matched_concepts", [])[:4]
            lines.append(f"{i:2d}. {key}  (score: {score:.3f})")
            if matched:
                lines.append(f"      Matched: {', '.join(matched)}")
        return "\n".join(lines)
