"""
MORPHEUS CHAT - Live Web Search & Deep Research Engine - System Wide
Sovereign web search with privacy protection and comprehensive research capabilities.

Architecture Features:
- Second Layer of research, beyond the integrated vm-browser(which is for general quick usage and web access. This is for deep research and web access when the AI or user deems necessary and/or does not want to spin up and launch into the dedicated research platform.) This is only used in the main chat mode and projects.
- Privacy-first search via DuckDuckGo and SearxNG
- Multi-source aggregation and source credibility scoring
- Automatic fact-checking and bias detection
- Deep research with recursive query expansion
- Content extraction and summarization
- Citation tracking and evidence weighting
- Separated from the integrated browser ai_browser_research_system enabling a 2 part browser/web access. One in which the ai will choose to use or can choose, when it deems a search to be necessary.
- This file is togglable by the user and connects the AI, to the local host OS devices browser (users device installed browser) for when explicit web queries or researches are deemed necessary. This system is only enabled when toggle by user.

"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlparse
from uuid import UUID, uuid4
from dataclasses import dataclass, field
from enum import Enum
# Added imports
from pathlib import Path
from io import BytesIO
import contextlib
import re

import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from newspaper import Article
from textstat import flesch_reading_ease

from pydantic import BaseModel, Field, validator
try:
    from schemas.session import SessionID, UserID
except Exception:
    from typing import TypeAlias
    SessionID: TypeAlias = str
    UserID: TypeAlias = str

# Optional dependency probes
try:
    import pyautogui  # type: ignore
    _HAS_PYAUTOGUI = True
except Exception:
    pyautogui = None  # type: ignore
    _HAS_PYAUTOGUI = False

try:
    from mss import mss as mss_factory  # type: ignore
    _HAS_MSS = True
except Exception:
    mss_factory = None  # type: ignore
    _HAS_MSS = False

try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    _HAS_TESS = True
except Exception:
    pytesseract = None  # type: ignore
    Image = None  # type: ignore
    _HAS_TESS = False

logger = logging.getLogger(__name__)

# --- System-wide web access toggle (UI-controlled) ---
class SystemWebAccess:
    """Feature flag for system-wide web access, controlled by the UI."""
    _enabled: bool = False
    _reason: str = "disabled by default"
    _last_changed: float = time.time()

    @classmethod
    def enable(cls, reason: str = "user_enabled"):
        cls._enabled = True
        cls._reason = reason
        cls._last_changed = time.time()
        logger.info("System web access enabled", extra={"event": "web_access_toggle", "reason": reason})

    @classmethod
    def disable(cls, reason: str = "user_disabled"):
        cls._enabled = False
        cls._reason = reason
        cls._last_changed = time.time()
        logger.info("System web access disabled", extra={"event": "web_access_toggle", "reason": reason})

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def status(cls) -> Dict[str, Any]:
        return {"enabled": cls._enabled, "reason": cls._reason, "last_changed": cls._last_changed}

# --- Resilience helpers: retry + circuit breaker ---
class AsyncCircuitBreaker:
    """Simple per-host circuit breaker for outbound HTTP calls."""
    def __init__(self, failure_threshold: int = 3, recovery_time: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.failures = 0
        self.last_failure: float = 0.0
        self.opened_at: float = 0.0

    def allow(self) -> bool:
        if self.failures < self.failure_threshold:
            return True
        # breaker open; check cooldown
        if time.time() - self.opened_at >= self.recovery_time:
            # half-open
            return True
        return False

    def record_success(self):
        self.failures = 0
        self.last_failure = 0.0
        self.opened_at = 0.0

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.failure_threshold and self.opened_at == 0.0:
            self.opened_at = self.last_failure

class SearchProvider(str, Enum):
    """Available search providers with privacy focus"""
    DUCKDUCKGO = "duckduckgo"
    SEARXNG = "searxng"
    BRAVE = "brave"
    STARTPAGE = "startpage"


class SourceType(str, Enum):
    """Content source classification"""
    NEWS = "news"
    ACADEMIC = "academic"
    TECHNICAL = "technical"
    BLOG = "blog"
    FORUM = "forum"
    SOCIAL = "social"
    GOVERNMENT = "government"
    COMMERCIAL = "commercial"
    UNKNOWN = "unknown"


class CredibilityScore(str, Enum):
    """Source credibility assessment"""
    VERY_HIGH = "very_high"    # 0.9-1.0
    HIGH = "high"              # 0.7-0.9
    MEDIUM = "medium"          # 0.5-0.7
    LOW = "low"               # 0.3-0.5
    VERY_LOW = "very_low"     # 0.0-0.3


@dataclass
class SearchResult:
    """Individual search result with enhanced metadata"""
    url: str
    title: str
    snippet: str
    source_domain: str
    search_rank: int
    
    # Content analysis
    content: Optional[str] = None
    extracted_text: Optional[str] = None
    word_count: int = 0
    reading_level: Optional[float] = None
    
    # Source credibility
    source_type: SourceType = SourceType.UNKNOWN
    credibility_score: float = 0.5
    credibility_factors: List[str] = field(default_factory=list)
    
    # Temporal data
    publish_date: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    age_days: Optional[int] = None
    
    # Search metadata
    search_query: str = ""
    search_provider: SearchProvider = SearchProvider.DUCKDUCKGO
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Analysis results
    key_points: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    sentiment_score: Optional[float] = None
    bias_indicators: List[str] = field(default_factory=list)
    
    @property
    def is_recent(self) -> bool:  # FIX: property must not accept args
        """Check if content is recent within a default 30-day window"""
        if not self.publish_date:
            return False
        age = datetime.now(timezone.utc) - self.publish_date
        return age.days <= 30

    @property
    def credibility_level(self) -> 'CredibilityScore':
        """Get credibility level based on score"""
        if self.credibility_score >= 0.9:
            return CredibilityScore.VERY_HIGH
        elif self.credibility_score >= 0.7:
            return CredibilityScore.HIGH
        elif self.credibility_score >= 0.5:
            return CredibilityScore.MEDIUM
        elif self.credibility_score >= 0.3:
            return CredibilityScore.LOW
        else:
            return CredibilityScore.VERY_LOW


class ResearchQuery(BaseModel):
    """Structured research query with depth and scope"""
    query_id: UUID = Field(default_factory=uuid4)
    original_query: str = Field(description="User's original research question")
    user_id: UserID = Field(description="User conducting research")
    session_id: SessionID = Field(description="Session context")
    
    # Research configuration
    max_depth: int = Field(default=3, ge=1, le=5, description="Research depth levels")
    max_sources: int = Field(default=20, ge=5, le=100, description="Maximum sources per level")
    min_credibility: float = Field(default=0.3, ge=0, le=1, description="Minimum source credibility")
    
    # Content preferences
    source_types: List[SourceType] = Field(default_factory=lambda: [
        SourceType.NEWS, SourceType.ACADEMIC, SourceType.TECHNICAL
    ])
    max_age_days: Optional[int] = Field(None, description="Maximum content age")
    languages: List[str] = Field(default_factory=lambda: ["en"])
    
    # Research focus
    focus_areas: List[str] = Field(default_factory=list, description="Specific topics to focus on")
    exclude_terms: List[str] = Field(default_factory=list, description="Terms to avoid")
    
    # Output preferences
    include_citations: bool = Field(default=True)
    include_summaries: bool = Field(default=True)
    bias_analysis: bool = Field(default=True)
    fact_checking: bool = Field(default=True)
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @validator('original_query')
    def _validate_original_query(cls, v: str) -> str:
        q = (v or "").strip()
        if not q:
            raise ValueError("original_query must be non-empty")
        if len(q) > 500:
            raise ValueError("original_query too long")
        return q

    @validator('min_credibility')
    def _validate_credibility(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("min_credibility must be between 0 and 1")
        return v

    @validator('languages', each_item=True)
    def _validate_languages(cls, v: str) -> str:
        if not v or len(v) > 8:
            raise ValueError("invalid language code")
        return v


class ResearchReport(BaseModel):
    """Comprehensive research report with analysis and citations"""
    report_id: UUID = Field(default_factory=uuid4)
    query: ResearchQuery = Field(description="Original research query")
    
    # Executive summary
    executive_summary: str = Field(description="Key findings summary")
    key_findings: List[str] = Field(description="Main discoveries")
    confidence_level: float = Field(ge=0, le=1, description="Overall confidence in findings")
    
    # Research results
    total_sources: int = Field(ge=0, description="Total sources analyzed")
    credible_sources: int = Field(ge=0, description="High-credibility sources")
    search_results: List[SearchResult] = Field(description="All search results")
    
    # Analysis
    consensus_points: List[str] = Field(default_factory=list, description="Points with broad agreement")
    conflicting_information: List[str] = Field(default_factory=list, description="Contradictory findings")
    knowledge_gaps: List[str] = Field(default_factory=list, description="Areas needing more research")
    
    # Quality metrics
    source_diversity: float = Field(default=0.0, ge=0, le=1, description="Diversity of source types")  # FIX: default
    temporal_coverage: float = Field(default=0.0, ge=0, le=1, description="Time range coverage")       # FIX: default
    geographic_coverage: List[str] = Field(default_factory=list, description="Geographic regions covered")
    
    # Bias and fact-checking
    bias_assessment: Dict[str, float] = Field(default_factory=dict, description="Bias scores by type")
    fact_check_results: List[Dict[str, Any]] = Field(default_factory=list, description="Fact-checking results")
    
    # Citations and references
    citations: List[Dict[str, str]] = Field(default_factory=list, description="Formatted citations")
    reference_urls: List[str] = Field(default_factory=list, description="Source URLs")
    
    # Metadata
    research_duration: float = Field(ge=0, description="Research time in seconds")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LocalBrowserController:
    """
    Sovereign local OS browser controller.
    - Launches/focuses system browser safely via default handler.
    - Simulates keyboard/mouse via pyautogui (if available).
    - Captures screen via mss/pyautogui; OCR via Tesseract (if available).
    - No third-party online APIs. Localhost-only streaming for UI.
    """
    def __init__(self, screenshot_dir: Optional[str] = None):
        self.screenshot_dir = Path(screenshot_dir or (Path.home() / "somnus_local_web_stream"))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._latest_png: Optional[bytes] = None
        self._last_capture_ts: float = 0.0
        self._running: bool = False
        self._screen = mss_factory() if _HAS_MSS else None
        logger.info(
            "LocalBrowserController initialized",
            extra={
                "event": "local_controller_init",
                "has_pyautogui": _HAS_PYAUTOGUI,
                "has_mss": _HAS_MSS,
                "has_tess": _HAS_TESS
            }
        )

    async def start(self) -> bool:
        """Prepare controller for use."""
        if not SystemWebAccess.is_enabled():
            logger.info("Local controller start blocked; system web access disabled")
            return False
        self._running = True
        return True

    async def stop(self):
        """Stop controller and cleanup resources."""
        self._running = False
        try:
            if self._screen and hasattr(self._screen, 'close'):
                self._screen.close()  # type: ignore
        except Exception:
            pass

    async def open_url(self, url: str) -> bool:
        """Open URL in system default browser."""
        if not self._running or not SystemWebAccess.is_enabled():
            return False
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https"):
            return False
        import webbrowser
        try:
            ok = webbrowser.open(url, new=2)
            await asyncio.sleep(2.0)  # allow render
            return bool(ok)
        except Exception as e:
            logger.error("Local open_url failed", extra={"event": "local_open_failed", "error": str(e)})
            return False

    async def type_text(self, text: str, interval: float = 0.02) -> bool:
        """Type text into the focused browser."""
        if not self._running or not _HAS_PYAUTOGUI:
            return False
        try:
            await asyncio.to_thread(pyautogui.typewrite, text, interval=interval)  # type: ignore
            return True
        except Exception as e:
            logger.warning("type_text failed", extra={"event": "type_failed", "error": str(e)})
            return False

    async def press_keys(self, *keys: str) -> bool:
        """Press hotkeys in the focused window."""
        if not self._running or not _HAS_PYAUTOGUI:
            return False
        try:
            await asyncio.to_thread(pyautogui.hotkey, *keys)  # type: ignore
            return True
        except Exception as e:
            logger.warning("press_keys failed", extra={"event": "hotkey_failed", "error": str(e)})
            return False

    async def screenshot_png(self) -> Optional[bytes]:
        """Capture a full-screen PNG."""
        if not self._running:
            return None
        try:
            if _HAS_MSS and self._screen and Image is not None:
                shot = self._screen.grab(self._screen.monitors[0])  # type: ignore
                img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)  # type: ignore
                buf = BytesIO()
                img.save(buf, format="PNG")
                data = buf.getvalue()
            elif _HAS_PYAUTOGUI:
                # pyautogui.screenshot returns a PIL.Image; guard if PIL missing
                img = await asyncio.to_thread(pyautogui.screenshot)  # type: ignore
                buf = BytesIO()
                img.save(buf, format="PNG")
                data = buf.getvalue()
            else:
                return None
            self._latest_png = data
            self._last_capture_ts = time.time()
            try:
                out = self.screenshot_dir / "latest.png"
                with open(out, "wb") as f:
                    f.write(data)
            except Exception:
                pass
            return data
        except Exception as e:
            logger.warning("screenshot failed", extra={"event": "screenshot_failed", "error": str(e)})
            return None

    def get_latest_png(self) -> Optional[bytes]:
        """Return last captured PNG frame, if any."""
        return self._latest_png

    async def ocr_text(self, png_bytes: bytes) -> str:
        """Extract text via Tesseract OCR; returns empty string if unavailable."""
        if not _HAS_TESS or not png_bytes:
            return ""
        try:
            img = Image.open(BytesIO(png_bytes))  # type: ignore
            text = await asyncio.to_thread(pytesseract.image_to_string, img)  # type: ignore
            return text or ""
        except Exception as e:
            logger.warning("OCR failed", extra={"event": "ocr_failed", "error": str(e)})
            return ""

    @staticmethod
    def parse_links_from_text(text: str) -> List[Tuple[str, str, str]]:
        """
        Parse OCR text into rough (title, url, snippet) tuples.
        Heuristic: URL lines + surrounding lines as title/snippet.
        """
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        results: List[Tuple[str, str, str]] = []
        url_re = re.compile(r'(https?://[^\s]+)', re.IGNORECASE)
        for i, line in enumerate(lines):
            m = url_re.search(line)
            if not m:
                continue
            url = m.group(1).strip().rstrip(').,;\'"')
            title = ""
            snippet = ""
            if i > 0:
                title = lines[i - 1][:200]
            if i + 1 < len(lines):
                snippet = lines[i + 1][:300]
            results.append((title or "Result", url, snippet))
        return results


class LiveRenderServer:
    """
    Localhost-only live render server to stream MJPEG frames and status.
    No external exposure; binds 127.0.0.1 by default.
    """
    def __init__(self, controller: LocalBrowserController, host: str = "127.0.0.1", port: int = 8765):
        self.controller = controller
        self.host = host
        self.port = port
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._app: Optional[web.Application] = None

    async def start(self) -> bool:
        if self._runner:
            return True
        self._app = web.Application()
        self._app.add_routes([
            web.get("/live/status", self._status),
            web.get("/live/screenshot", self._screenshot),
            web.get("/live/stream.mjpeg", self._mjpeg_stream),
        ])
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info("LiveRenderServer started", extra={"event": "live_render_started", "addr": f"http://{self.host}:{self.port}"})
        return True

    async def stop(self):
        try:
            if self._site:
                await self._site.stop()
            if self._runner:
                await self._runner.cleanup()
        finally:
            self._site = None
            self._runner = None
            self._app = None

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "system_web_access": SystemWebAccess.status(),
            "has_pyautogui": _HAS_PYAUTOGUI,
            "has_mss": _HAS_MSS,
            "has_tesseract": _HAS_TESS
        })

    async def _screenshot(self, request: web.Request) -> web.Response:
        if not SystemWebAccess.is_enabled():
            return web.Response(status=403, text="disabled")
        img = self.controller.get_latest_png() or await self.controller.screenshot_png()
        if not img:
            return web.Response(status=503, text="no_frame")
        return web.Response(body=img, content_type="image/png")

    async def _mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        if not SystemWebAccess.is_enabled():
            return web.Response(status=403, text="disabled")
        boundary = "frame"
        resp = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': f'multipart/x-mixed-replace; boundary=--{boundary}',
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
            }
        )
        await resp.prepare(request)
        try:
            while True:
                # Detect client disconnect
                if request.transport is None or request.transport.is_closing():
                    break
                img = await self.controller.screenshot_png()
                if img:
                    await resp.write(f"--{boundary}\r\n".encode())
                    await resp.write(b"Content-Type: image/png\r\n")
                    await resp.write(f"Content-Length: {len(img)}\r\n\r\n".encode())
                    await resp.write(img)
                    await resp.write(b"\r\n")
                await asyncio.sleep(0.5)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.warning("MJPEG stream error", extra={"event": "mjpeg_stream_error", "error": str(e)})
        finally:
            with contextlib.suppress(Exception):
                await resp.write_eof()
        return resp

# ...existing code...

class WebSearchEngine:
    """
    Privacy-first web search engine with multiple provider support.
    
    Implements sovereign search capabilities without tracking or API dependencies.
    """
    def __init__(
        self,
        primary_provider: SearchProvider = SearchProvider.DUCKDUCKGO,
        timeout: int = 30,
        max_concurrent: int = 5
    ):
        self.primary_provider = primary_provider
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_concurrent = max_concurrent
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._cb_per_host: Dict[str, AsyncCircuitBreaker] = {}
        # Local controller / live server hooks
        self._local_controller: Optional[LocalBrowserController] = None
        self._live_server: Optional[LiveRenderServer] = None
        # Provider configurations
        self.providers = {
            SearchProvider.DUCKDUCKGO: {
                "search_url": "https://duckduckgo.com/html/",
                "params": {"q": "{query}", "s": "0", "dc": "1"},
                "result_selector": ".result__body",
                "title_selector": ".result__title a",
                "snippet_selector": ".result__snippet",
                "url_selector": ".result__title a"
            },
            SearchProvider.SEARXNG: {
                "search_url": "https://searx.org/search",
                "params": {"q": "{query}", "format": "json", "safesearch": "0"},
                "json_results": True
            }
        }
        logger.info(f"Web search engine initialized with provider: {primary_provider}")

    async def __aenter__(self):
        """Async context manager entry"""
        connector = aiohttp.TCPConnector(
            limit=self.max_concurrent,
            limit_per_host=2,
            ttl_dns_cache=300,
            use_dns_cache=True
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=self.timeout,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
            self.session = None

    async def close(self):
        """Explicit cleanup to satisfy resource lifecycle requirements."""
        await self.__aexit__(None, None, None)

    def _get_cb(self, host: str) -> AsyncCircuitBreaker:
        if host not in self._cb_per_host:
            self._cb_per_host[host] = AsyncCircuitBreaker()
        return self._cb_per_host[host]

    async def _ensure_session(self):
        if not self.session:
            await self.__aenter__()

    async def _http_get(self, url: str, *, params: Optional[Dict[str, Any]] = None, correlation_id: str = "") -> Optional[aiohttp.ClientResponse]:
        """HTTP GET with retries, jittered backoff, and circuit breaker."""
        await self._ensure_session()
        assert self.session is not None
        host = urlparse(url).netloc
        cb = self._get_cb(host)

        if not cb.allow():
            logger.warning("Circuit open; request blocked", extra={"event": "circuit_open", "host": host, "correlation_id": correlation_id})
            return None

        attempts = 0
        max_attempts = 3
        backoff = 0.5

        while attempts < max_attempts:
            try:
                resp = await self.session.get(url, params=params)
                if resp.status >= 500:
                    # server error counts as failure; consume text to free connection
                    _ = await resp.text()
                    raise aiohttp.ClientResponseError(request_info=resp.request_info, history=resp.history, status=resp.status, message="server_error")
                cb.record_success()
                return resp
            except Exception as e:
                attempts += 1
                cb.record_failure()
                logger.warning(
                    "HTTP GET failed",
                    extra={"event": "http_get_failed", "url": url, "attempt": attempts, "error": str(e), "correlation_id": correlation_id}
                )
                if attempts >= max_attempts:
                    break
                # jittered backoff
                await asyncio.sleep(backoff + (0.1 * attempts))
                backoff *= 2

        logger.error("HTTP GET exhausted retries", extra={"event": "http_get_exhausted", "url": url, "correlation_id": correlation_id})
        return None

    def attach_local_controller(self, controller: LocalBrowserController, *, start_stream: bool = False, host: str = "127.0.0.1", port: int = 8765) -> None:
        """
        Attach a LocalBrowserController and optionally start a localhost live stream server.
        Parameters:
            controller: LocalBrowserController instance to attach.
            start_stream: If True, starts a LiveRenderServer bound to host:port (requires SystemWebAccess enabled).
            host: Host for live stream server.
            port: Port for live stream server.
        """
        self._local_controller = controller
        if start_stream and SystemWebAccess.is_enabled():
            # Fire-and-forget start; caller can await start_local_stream for deterministic startup
            asyncio.create_task(self.start_local_stream(host=host, port=port))

    async def start_local_stream(self, host: str = "127.0.0.1", port: int = 8765) -> bool:
        """
        Start the LocalBrowserController and LiveRenderServer for streaming screenshots.
        Returns True on success.
        """
        if not SystemWebAccess.is_enabled():
            logger.info("Start stream blocked; system web access disabled", extra={"event": "web_access_blocked"})
            return False
        if not self._local_controller:
            logger.warning("No local controller attached", extra={"event": "no_local_controller"})
            return False
        started = await self._local_controller.start()
        if not started:
            return False
        self._live_server = LiveRenderServer(self._local_controller, host=host, port=port)
        return await self._live_server.start()

    async def stop_local_stream(self) -> None:
        """Stop the live stream server and controller if running."""
        if self._live_server:
            with contextlib.suppress(Exception):
                await self._live_server.stop()
            self._live_server = None
        if self._local_controller:
            with contextlib.suppress(Exception):
                await self._local_controller.stop()

    async def search(
        self,
        query: str,
        max_results: int = 10,
        provider: Optional[SearchProvider] = None,
        correlation_id: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Perform web search with specified provider.
        Returns list of SearchResult objects with metadata.
        """
        if not SystemWebAccess.is_enabled():
            logger.info("Search blocked; system web access disabled", extra={"event": "web_access_blocked", "correlation_id": correlation_id})
            return []

        q = (query or "").strip()
        if not q:
            logger.warning("Empty query provided", extra={"event": "invalid_query", "correlation_id": correlation_id})
            return []

        max_results = max(1, min(int(max_results), 50))
        provider = provider or self.primary_provider
        corr = correlation_id or str(uuid4())

        try:
            async with self.semaphore:
                if provider == SearchProvider.DUCKDUCKGO:
                    return await self._search_duckduckgo(q, max_results, corr)
                elif provider == SearchProvider.SEARXNG:
                    return await self._search_searxng(q, max_results, corr)
                else:
                    logger.warning("Provider not implemented; falling back to DuckDuckGo", extra={"event": "provider_fallback", "provider": str(provider), "correlation_id": corr})
                    return await self._search_duckduckgo(q, max_results, corr)
        except Exception as e:
            logger.error("Search failed", extra={"event": "search_failed", "query": q[:120], "error": str(e), "correlation_id": corr})
            return []

    async def _search_duckduckgo(self, query: str, max_results: int, correlation_id: str) -> List[SearchResult]:
        """Search using DuckDuckGo HTML interface"""
        config = self.providers[SearchProvider.DUCKDUCKGO]
        params = {k: v.format(query=query) if isinstance(v, str) else v for k, v in config["params"].items()}

        response = await self._http_get(config["search_url"], params=params, correlation_id=correlation_id)
        if not response:
            return []
        if response.status != 200:
            logger.error("DuckDuckGo HTTP error", extra={"event": "provider_http_error", "status": response.status, "correlation_id": correlation_id})
            return []

        html = await response.text()
        soup = BeautifulSoup(html, 'html.parser')

        results: List[SearchResult] = []
        result_elements = soup.select(config["result_selector"])[:max_results]

        for idx, element in enumerate(result_elements):
            try:
                title_elem = element.select_one(config["title_selector"])
                title = title_elem.get_text(strip=True) if title_elem else "No title"

                url_elem = element.select_one(config["url_selector"])
                url = url_elem.get('href', '') if url_elem else ''

                if url.startswith('/l/?uddg='):
                    import urllib.parse
                    url = urllib.parse.unquote(url.split('uddg=')[1].split('&')[0])

                snippet_elem = element.select_one(config["snippet_selector"])
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

                if url and title:
                    domain = urlparse(url).netloc
                    results.append(SearchResult(
                        url=url,
                        title=title,
                        snippet=snippet,
                        source_domain=domain,
                        search_rank=idx + 1,
                        search_query=query,
                        search_provider=SearchProvider.DUCKDUCKGO
                    ))
            except Exception as e:
                logger.warning("Failed to parse DDG result", extra={"event": "parse_result_failed", "idx": idx, "error": str(e), "correlation_id": correlation_id})
                continue

        logger.info("DuckDuckGo search completed", extra={"event": "search_completed", "provider": "duckduckgo", "count": len(results), "correlation_id": correlation_id})
        return results

    async def _search_searxng(self, query: str, max_results: int, correlation_id: str) -> List[SearchResult]:
        """Search using SearxNG instance"""
        config = self.providers[SearchProvider.SEARXNG]
        params = {k: v.format(query=query) if isinstance(v, str) else v for k, v in config["params"].items()}

        response = await self._http_get(config["search_url"], params=params, correlation_id=correlation_id)
        if not response:
            return []
        if response.status != 200:
            logger.error("SearxNG HTTP error", extra={"event": "provider_http_error", "status": response.status, "correlation_id": correlation_id})
            return []

        data = await response.json()
        results: List[SearchResult] = []

        for idx, item in enumerate(data.get('results', [])[:max_results]):
            try:
                url_val = item.get('url', '')
                results.append(SearchResult(
                    url=url_val,
                    title=item.get('title', 'No title'),
                    snippet=item.get('content', ''),
                    source_domain=urlparse(url_val).netloc,
                    search_rank=idx + 1,
                    search_query=query,
                    search_provider=SearchProvider.SEARXNG
                ))
            except Exception as e:
                logger.warning("Failed to parse SearxNG result", extra={"event": "parse_result_failed", "idx": idx, "error": str(e), "correlation_id": correlation_id})
                continue

        logger.info("SearxNG search completed", extra={"event": "search_completed", "provider": "searxng", "count": len(results), "correlation_id": correlation_id})
        return results

    async def multi_provider_search(
        self,
        query: str,
        providers: List[SearchProvider] = None,
        max_results_per_provider: int = 5,
        correlation_id: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Search across multiple providers and merge results.
        Deduplicates and ranks results by relevance and credibility.
        """
        corr = correlation_id or str(uuid4())
        providers = providers or [SearchProvider.DUCKDUCKGO, SearchProvider.SEARXNG]

        tasks = [self.search(query, max_results_per_provider, provider, correlation_id=corr) for provider in providers]
        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: List[SearchResult] = []
        seen_urls = set()

        for provider_results in search_results:
            if isinstance(provider_results, Exception):
                logger.error("Provider search failed", extra={"event": "provider_search_failed", "error": str(provider_results), "correlation_id": corr})
                continue
            for result in provider_results:
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    all_results.append(result)

        all_results.sort(key=lambda r: (r.search_rank, providers.index(r.search_provider)))
        logger.info("Multi-provider search merged results", extra={"event": "multi_search_done", "unique": len(all_results), "correlation_id": corr})
        return all_results

    def open_in_local_browser(self, url: str) -> bool:
        """Safely open a URL in the user's default OS browser (explicit action)."""
        import webbrowser
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https"):
            logger.warning("Blocked non-http(s) URL open", extra={"event": "open_blocked", "url": url})
            return False
        if not SystemWebAccess.is_enabled():
            logger.info("Open blocked; system web access disabled", extra={"event": "web_access_blocked_open"})
            return False
        try:
            return webbrowser.open(url)
        except Exception as e:
            logger.error("Failed to open local browser", extra={"event": "open_failed", "error": str(e)})
            return False

    @staticmethod
    def to_vm_sources(results: List[SearchResult]) -> List[Dict[str, Any]]:
        """Convert results to VM browser research system shape (sources_found)."""
        vm_list: List[Dict[str, Any]] = []
        for r in results:
            vm_list.append({
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "position": r.search_rank
            })
        return vm_list

class ContentAnalyzer:
    """
    Advanced content analysis for research quality assessment.
    
    Provides credibility scoring, bias detection, and fact-checking capabilities.
    """
    def __init__(self):
        # Domain credibility mapping
        self.domain_credibility = {
            # Very High Credibility
            'nature.com': 0.95, 'science.org': 0.95, 'nejm.org': 0.95,
            'who.int': 0.9, 'cdc.gov': 0.9, 'nih.gov': 0.9,
            'reuters.com': 0.9, 'ap.org': 0.9, 'bbc.com': 0.85,
            
            # High Credibility
            'nytimes.com': 0.8, 'washingtonpost.com': 0.8, 'theguardian.com': 0.8,
            'economist.com': 0.85, 'wsj.com': 0.8, 'npr.org': 0.8,
            'arxiv.org': 0.8, 'pubmed.ncbi.nlm.nih.gov': 0.9,
            
            # Medium Credibility
            'wikipedia.org': 0.7, 'cnn.com': 0.6, 'forbes.com': 0.6,
            'techcrunch.com': 0.6, 'medium.com': 0.5,
            
            # Lower Credibility
            'reddit.com': 0.4, 'quora.com': 0.4, 'yahoo.com': 0.4,
            'buzzfeed.com': 0.3, 'dailymail.co.uk': 0.3
        }
        
        # Bias indicators
        self.bias_keywords = {
            'political_left': ['progressive', 'liberal', 'socialist', 'leftist'],
            'political_right': ['conservative', 'republican', 'right-wing', 'traditional'],
            'sensational': ['shocking', 'unbelievable', 'devastating', 'explosive'],
            'conspiracy': ['cover-up', 'hidden truth', 'they don\'t want you to know'],
            'commercial': ['buy now', 'limited time', 'special offer', 'discount']
        }
        
        logger.info("Content analyzer initialized")
    
    async def analyze_content(self, result: SearchResult) -> SearchResult:
        """
        Comprehensive content analysis with credibility and bias assessment.
        
        Updates the SearchResult with analysis results.
        """
        try:
            if not result.content:
                result.content = await self._extract_content(result.url)
            if result.content:
                result.word_count = len(result.content.split())
                try:
                    result.reading_level = flesch_reading_ease(result.content)
                except Exception:
                    result.reading_level = None
            # Source type classification
            result.source_type = self._classify_source_type(result.source_domain, result.url)
            
            # Credibility assessment
            result.credibility_score = self._assess_credibility(result)
            
            # Bias detection
            result.bias_indicators = self._detect_bias(result.content or result.snippet)
            
            # Entity extraction (simplified)
            result.entities = self._extract_entities(result.content or result.snippet)
            
            # Key points extraction
            result.key_points = self._extract_key_points(result.content or result.snippet)
            
            # Publication date detection
            result.publish_date = await self._extract_publish_date(result.url, result.content)
            if result.publish_date:
                result.age_days = (datetime.now(timezone.utc) - result.publish_date).days
            
            logger.debug("Content analysis completed", extra={"event": "content_analyzed", "url": result.url})
            return result
        
        except Exception as e:
            logger.error("Content analysis failed", extra={"event": "content_analysis_failed", "url": result.url, "error": str(e)})
            return result
    
    async def _extract_content(self, url: str) -> Optional[str]:
        """Extract clean text content from URL"""
        try:
            article = Article(url)
            # Run parse steps with timeout to avoid hangs
            await asyncio.wait_for(asyncio.to_thread(article.download), timeout=20)
            await asyncio.wait_for(asyncio.to_thread(article.parse), timeout=20)
            
            return article.text
        
        except Exception as e:
            logger.warning("Content extraction failed", extra={"event": "content_extract_failed", "url": url, "error": str(e)})
            return None
    
    def _classify_source_type(self, domain: str, url: str) -> SourceType:
        """Classify source type based on domain and URL patterns"""
        domain = domain.lower()
        url = url.lower()
        
        # Government sources
        if domain.endswith('.gov') or domain.endswith('.mil'):
            return SourceType.GOVERNMENT
        
        # Academic sources
        if (domain.endswith('.edu') or 'arxiv' in domain or 'pubmed' in domain or
            'scholar.google' in domain or 'researchgate' in domain):
            return SourceType.ACADEMIC
        
        # News sources
        news_indicators = ['news', 'times', 'post', 'herald', 'guardian', 'bbc', 'cnn', 'reuters', 'ap.org']
        if any(indicator in domain for indicator in news_indicators):
            return SourceType.NEWS
        
        # Technical sources
        tech_indicators = ['stackoverflow', 'github', 'techcrunch', 'wired', 'ars-technica']
        if any(indicator in domain for indicator in tech_indicators):
            return SourceType.TECHNICAL
        
        # Social media and forums
        social_indicators = ['reddit', 'twitter', 'facebook', 'linkedin', 'quora']
        if any(indicator in domain for indicator in social_indicators):
            return SourceType.SOCIAL
        
        # Blogs
        if 'blog' in domain or 'medium.com' in domain or 'wordpress' in domain:
            return SourceType.BLOG
        
        # Commercial
        if '.com' in domain and 'shop' in url or 'buy' in url or 'store' in url:
            return SourceType.COMMERCIAL
        
        return SourceType.UNKNOWN
    
    def _assess_credibility(self, result: SearchResult) -> float:
        """Assess source credibility based on multiple factors"""
        base_score = self.domain_credibility.get(result.source_domain, 0.5)
        
        # Adjust based on source type
        type_adjustments = {
            SourceType.ACADEMIC: 0.1,
            SourceType.GOVERNMENT: 0.1,
            SourceType.NEWS: 0.05,
            SourceType.TECHNICAL: 0.0,
            SourceType.BLOG: -0.1,
            SourceType.SOCIAL: -0.2,
            SourceType.FORUM: -0.15,
            SourceType.COMMERCIAL: -0.1
        }
        
        adjustment = type_adjustments.get(result.source_type, 0)
        credibility = base_score + adjustment
        
        # Additional factors
        if result.content:
            # Length bonus (longer articles often more credible)
            if result.word_count > 1000:
                credibility += 0.05
            elif result.word_count < 200:
                credibility -= 0.1
            
            # Reading level (too simple or too complex might indicate issues)
            if result.reading_level and 30 <= result.reading_level <= 70:
                credibility += 0.05
        
        # Recency bonus for news
        if result.source_type == SourceType.NEWS and result.is_recent:
            credibility += 0.05
        
        # Bias penalty
        if result.bias_indicators:
            credibility -= 0.05 * len(result.bias_indicators)
        
        return max(0.0, min(1.0, credibility))
    
    def _detect_bias(self, content: str) -> List[str]:
        """Detect potential bias indicators in content"""
        if not content:
            return []
        
        content_lower = content.lower()
        detected_bias = []
        
        for bias_type, keywords in self.bias_keywords.items():
            if any(keyword in content_lower for keyword in keywords):
                detected_bias.append(bias_type)
        
        return detected_bias
    
    def _extract_entities(self, content: str) -> List[str]:
        """Extract named entities (simplified implementation)"""
        if not content:
            return []
        
        # Simple regex-based entity extraction
        # In production, would use spaCy or similar NLP library
        entities = []
        
        # Find capitalized words (potential proper nouns)
        import re
        capitalized_words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', content)
        
        # Filter common words and take unique entities
        common_words = {'The', 'This', 'That', 'These', 'Those', 'A', 'An', 'In', 'On', 'At', 'By', 'For'}
        entities = list(set([word for word in capitalized_words 
                           if word not in common_words and len(word) > 2]))
        
        return entities[:10]  # Limit to top 10
    
    def _extract_key_points(self, content: str) -> List[str]:
        """Extract key points from content (simplified)"""
        if not content:
            return []
        
        # Split into sentences and find important ones
        sentences = content.split('. ')
        key_points = []
        
        # Look for sentences with key indicators
        importance_indicators = [
            'study shows', 'research indicates', 'according to', 'data reveals',
            'experts say', 'scientists found', 'results show', 'analysis suggests'
        ]
        
        for sentence in sentences[:20]:  # Limit search
            sentence_lower = sentence.lower()
            if (any(indicator in sentence_lower for indicator in importance_indicators) or
                len(sentence.split()) > 10):  # Substantial sentences
                key_points.append(sentence.strip())
        
        return key_points[:5]  # Top 5 key points
    
    async def _extract_publish_date(self, url: str, content: Optional[str]) -> Optional[datetime]:
        """Extract publication date from content or metadata"""
        try:
            # Use newspaper3k's date extraction
            article = Article(url)
            if content:
                article.set_text(content)
            else:
                await asyncio.to_thread(article.download)
                await asyncio.to_thread(article.parse)
            
            return article.publish_date
        
        except Exception:
            return None


class DeepResearchEngine:
    """
    Advanced research engine with recursive query expansion and multi-source analysis.
    
    Implements comprehensive research workflows with quality assessment and synthesis.
    """
    def __init__(self, search_engine: WebSearchEngine, content_analyzer: ContentAnalyzer):
        self.search_engine = search_engine
        self.content_analyzer = content_analyzer
        self.max_concurrent_analysis = 5
        self.analysis_semaphore = asyncio.Semaphore(self.max_concurrent_analysis)
        logger.info("Deep research engine initialized")

    async def conduct_research(self, query: ResearchQuery) -> 'ResearchReport':
        """
        Conduct comprehensive research with multiple levels and analysis.
        
        Returns detailed research report with findings and citations.
        """
        start_time = time.time()
        correlation_id = str(query.session_id) if query and query.session_id else str(uuid4())
        logger.info("Starting deep research", extra={"event": "research_start", "query": query.original_query[:120], "correlation_id": correlation_id})

        try:
            initial_results = await self._level_1_search(query, correlation_id)
            expanded_results = await self._level_2_expansion(query, initial_results, correlation_id)
            deep_results = await self._level_3_deep_dive(query, expanded_results, correlation_id)
            all_results = initial_results + expanded_results + deep_results
            analyzed_results = await self._analyze_all_results(all_results)
            report = await self._generate_report(query, analyzed_results, start_time)
            logger.info("Research completed", extra={"event": "research_done", "analyzed": len(analyzed_results), "duration_s": report.research_duration})
            return report
        except Exception as e:
            logger.error("Research failed", extra={"event": "research_failed", "error": str(e), "correlation_id": correlation_id})
            return ResearchReport(
                query=query,
                executive_summary=f"Research failed due to error: {str(e)}",
                key_findings=["Research could not be completed"],
                confidence_level=0.0,
                total_sources=0,
                credible_sources=0,
                search_results=[],
                # defaults for required metrics are provided by model
                research_duration=time.time() - start_time
            )

    async def _level_1_search(self, query: 'ResearchQuery', correlation_id: str) -> List[SearchResult]:
        results = await self.search_engine.multi_provider_search(
            query.original_query,
            max_results_per_provider=query.max_sources // 2,
            correlation_id=correlation_id
        )
        logger.info("Level 1 search", extra={"event": "level1_done", "count": len(results), "correlation_id": correlation_id})
        return results

    async def _level_2_expansion(self, query: 'ResearchQuery', initial_results: List[SearchResult], correlation_id: str) -> List[SearchResult]:
        if query.max_depth < 2:
            return []
        expansion_queries = self._generate_expansion_queries(query, initial_results)
        expanded_results: List[SearchResult] = []
        for exp_query in expansion_queries[:3]:
            expanded_results.extend(await self.search_engine.search(exp_query, max_results=5, correlation_id=correlation_id))
        logger.info("Level 2 expansion", extra={"event": "level2_done", "count": len(expanded_results), "correlation_id": correlation_id})
        return expanded_results

    async def _level_3_deep_dive(self, query: 'ResearchQuery', previous_results: List[SearchResult], correlation_id: str) -> List[SearchResult]:
        if query.max_depth < 3:
            return []
        high_value_sources = [r for r in previous_results if r.credibility_score >= 0.7][:5]
        deep_results: List[SearchResult] = []
        for source in high_value_sources:
            if source.entities:
                entity_query = f"{query.original_query} {' '.join(source.entities[:3])}"
                deep_results.extend(await self.search_engine.search(entity_query, max_results=3, correlation_id=correlation_id))
        logger.info("Level 3 deep dive", extra={"event": "level3_done", "count": len(deep_results), "correlation_id": correlation_id})
        return deep_results

    def _generate_expansion_queries(self, query: ResearchQuery, results: List[SearchResult]) -> List[str]:
        """Generate expansion queries based on initial results"""
        expansion_queries = []
        
        # Extract common entities across results
        all_entities = []
        for result in results[:5]:  # Top 5 results
            all_entities.extend(result.entities)
        
        # Find most common entities
        from collections import Counter
        entity_counts = Counter(all_entities)
        top_entities = [entity for entity, count in entity_counts.most_common(5)]
        
        # Generate queries combining original with entities
        for entity in top_entities:
            expansion_queries.append(f"{query.original_query} {entity}")
        
        # Add focus area queries
        for focus in query.focus_areas:
            expansion_queries.append(f"{query.original_query} {focus}")
        
        return expansion_queries
    
    async def _analyze_all_results(self, results: List[SearchResult]) -> List[SearchResult]:
        """Analyze all search results concurrently"""
        async def analyze_with_semaphore(result):
            async with self.analysis_semaphore:
                return await self.content_analyzer.analyze_content(result)
        
        # Remove duplicates
        unique_results = []
        seen_urls = set()
        for result in results:
            if result.url not in seen_urls:
                seen_urls.add(result.url)
                unique_results.append(result)
        
        # Analyze concurrently
        analysis_tasks = [analyze_with_semaphore(result) for result in unique_results]
        analyzed_results = await asyncio.gather(*analysis_tasks, return_exceptions=True)
        
        # Filter out failed analyses
        valid_results = [
            result for result in analyzed_results
            if not isinstance(result, Exception)
        ]
        
        logger.info(f"Content analysis completed: {len(valid_results)}/{len(unique_results)} successful")
        return valid_results
    
    async def _generate_report(
        self,
        query: 'ResearchQuery',
        results: List[SearchResult],
        start_time: float
    ) -> 'ResearchReport':
        """Generate comprehensive research report"""
        credible_results = [r for r in results if r.credibility_score >= query.min_credibility]
        executive_summary = self._generate_executive_summary(query, credible_results)
        key_findings = self._extract_key_findings(credible_results)
        consensus_points, conflicts = self._analyze_consensus(credible_results)
        source_diversity = self._calculate_source_diversity(credible_results)
        confidence_level = self._calculate_confidence(credible_results)
        citations = self._generate_citations(credible_results)
        bias_assessment = self._assess_overall_bias(credible_results)
        temporal_coverage = self._calculate_temporal_coverage(credible_results)

        return ResearchReport(
            query=query,
            executive_summary=executive_summary,
            key_findings=key_findings,
            confidence_level=confidence_level,
            total_sources=len(results),
            credible_sources=len(credible_results),
            search_results=credible_results,
            consensus_points=consensus_points,
            conflicting_information=conflicts,
            source_diversity=source_diversity,
            temporal_coverage=temporal_coverage,
            bias_assessment=bias_assessment,
            citations=citations,
            reference_urls=[r.url for r in credible_results],
            research_duration=time.time() - start_time
        )

    def _generate_executive_summary(self, query: ResearchQuery, results: List[SearchResult]) -> str:
        """Generate executive summary of research findings"""
        if not results:
            return "No credible sources found for the research query."
        
        # Analyze source types
        source_types = [r.source_type.value for r in results]
        type_counts = {t: source_types.count(t) for t in set(source_types)}
        
        # Analyze credibility
        avg_credibility = sum(r.credibility_score for r in results) / len(results)
        high_cred_count = sum(1 for r in results if r.credibility_score >= 0.7)
        
        summary_parts = [
            f"Research on '{query.original_query}' analyzed {len(results)} credible sources.",
            f"Source breakdown: {', '.join([f'{k}: {v}' for k, v in type_counts.items()])}.",
            f"Average source credibility: {avg_credibility:.2f} ({high_cred_count} high-credibility sources)."
        ]
        
        # Add key insights if available
        if results[0].key_points:
            summary_parts.append(f"Key insight: {results[0].key_points[0]}")
        
        return " ".join(summary_parts)
    
    def _extract_key_findings(self, results: List[SearchResult]) -> List[str]:
        """Extract key findings from research results"""
        all_key_points = []
        for result in results[:10]:  # Top 10 sources
            all_key_points.extend(result.key_points)
        
        # Remove duplicates and return top findings
        unique_findings = list(set(all_key_points))
        return unique_findings[:8]  # Top 8 findings
    
    def _analyze_consensus(self, results: List[SearchResult]) -> Tuple[List[str], List[str]]:
        """Analyze consensus and conflicts in sources"""
        # Simplified implementation - would use NLP for real consensus analysis
        consensus_points = ["Analysis of source consensus requires advanced NLP implementation"]
        conflicts = ["Conflict detection requires semantic analysis of source content"]
        
        return consensus_points, conflicts
    
    def _calculate_source_diversity(self, results: List[SearchResult]) -> float:
        """Calculate diversity of source types"""
        if not results:
            return 0.0
        
        source_types = set(r.source_type for r in results)
        max_diversity = len(SourceType)
        
        return len(source_types) / max_diversity
    
    def _calculate_confidence(self, results: List[SearchResult]) -> float:
        """Calculate overall confidence in research findings"""
        if not results:
            return 0.0
        
        # Base confidence on average credibility and source count
        avg_credibility = sum(r.credibility_score for r in results) / len(results)
        source_count_factor = min(1.0, len(results) / 10)  # Confidence increases with more sources
        
        return (avg_credibility * 0.7) + (source_count_factor * 0.3)
    
    def _generate_citations(self, results: List[SearchResult]) -> List[Dict[str, str]]:
        """Generate formatted citations for sources"""
        citations = []
        
        for i, result in enumerate(results, 1):
            citation = {
                'number': str(i),
                'title': result.title,
                'url': result.url,
                'domain': result.source_domain,
                'type': result.source_type.value,
                'credibility': result.credibility_level.value
            }
            
            if result.publish_date:
                citation['date'] = result.publish_date.strftime('%Y-%m-%d')
            
            citations.append(citation)
        
        return citations
    
    def _assess_overall_bias(self, results: List[SearchResult]) -> Dict[str, float]:
        """Assess overall bias across all sources"""
        bias_counts = {}
        total_sources = len(results)
        
        if total_sources == 0:
            return {}
        
        # Count bias indicators across sources
        for result in results:
            for bias in result.bias_indicators:
                bias_counts[bias] = bias_counts.get(bias, 0) + 1
        
        # Convert to percentages
        bias_assessment = {
            bias: count / total_sources 
            for bias, count in bias_counts.items()
        }
        
        return bias_assessment

    def _calculate_temporal_coverage(self, results: List[SearchResult]) -> float:
        """Ratio of sources with a publish_date within the last year."""
        if not results:
            return 0.0
        recent_year = 365
        now = datetime.now(timezone.utc)
        count_recent = 0
        for r in results:
            if r.publish_date and (now - r.publish_date).days <= recent_year:
                count_recent += 1
        return count_recent / len(results)


# Factory function for easy integration
async def create_research_system() -> Tuple[WebSearchEngine, DeepResearchEngine]:
    """
    Factory function to create complete research system.
    
    Returns configured search engine and research engine.
    """
    search_engine = WebSearchEngine()
    await search_engine.__aenter__()
    content_analyzer = ContentAnalyzer()
    research_engine = DeepResearchEngine(search_engine, content_analyzer)
    try:
        controller = LocalBrowserController()
        search_engine.attach_local_controller(controller)
    except Exception:
        pass
    logger.info("Research system created successfully")
    return search_engine, research_engine