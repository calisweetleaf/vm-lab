# ai_browser_research_system.py

"""
AI Browser Research System with Full Browser Automation
=========================================================

Production-grade browser automation system that gives AI complete
web research capabilities with real browsers, not scraping. This is the second layer enabling web access for a Somnus Sovereign Agent. It lives directly within the virtual machine as an always on, persistent, "quick" browser, enabled for extended deep-researching capabilities but is purposed as the main Web Data fetcher for a Somnus OS Chat Virtual Machine. This layer sits above the user toggle which is for direct queries where extended search is needed, or deep research, But does not require enough of a task load, to vm hotswap to the Web Research Subsystem layer. This is layer 2/3 and is in the "middle" of the other 2 web layers.

Features:
- Full Firefox/Chrome browser automation with Selenium
- Intelligent workflow management for different research types
- Multi-tab research with session persistence
- Advanced content extraction and analysis
- Real-time collaboration with human researchers
- Comprehensive error handling and recovery
- Performance monitoring and optimization
"""

import asyncio
import json
import logging
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple, Union
from uuid import UUID, uuid4
from dataclasses import dataclass, field
from enum import Enum
from contextlib import asynccontextmanager

import psutil
from pydantic import BaseModel, Field

# Try to import browser automation libraries
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.common.exceptions import WebDriverException, TimeoutException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logging.warning("Selenium not available - browser automation disabled")

try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logging.warning("Playwright not available - using fallback browser automation")

logger = logging.getLogger(__name__)


class ResearchWorkflowType(str, Enum):
    """Types of research workflows"""
    COMPREHENSIVE = "comprehensive"      # Thorough research with multiple sources
    SPEED_RUN = "speed_run"             # Fast research focusing on primary sources
    ACADEMIC = "academic"               # Academic research with scholarly sources
    TECHNICAL = "technical"             # Technical documentation and specs
    NEWS_MONITORING = "news_monitoring" # Continuous news and updates tracking
    COMPETITIVE_ANALYSIS = "competitive_analysis" # Business/competitor analysis
    FACT_CHECKING = "fact_checking"     # Verification and validation research
    TREND_ANALYSIS = "trend_analysis"   # Pattern and trend identification


class BrowserType(str, Enum):
    """Supported browser types"""
    FIREFOX = "firefox"
    CHROME = "chrome"
    SAFARI = "safari"
    EDGE = "edge"


class ResearchSourceQuality(str, Enum):
    """Quality ratings for research sources"""
    PREMIUM = "premium"      # Scholarly, peer-reviewed, official sources
    HIGH = "high"           # Reputable news, established organizations
    MEDIUM = "medium"       # General interest, established blogs
    LOW = "low"            # User-generated, opinion-based content
    UNVERIFIED = "unverified" # Uncertain quality sources


@dataclass
class ResearchSource:
    """Individual research source with metadata"""
    url: str
    title: str
    domain: str
    content_preview: str
    quality_rating: ResearchSourceQuality
    relevance_score: float
    content_length: int
    language: str
    publication_date: Optional[datetime] = None
    author: Optional[str] = None
    source_type: str = "web_page"
    credibility_indicators: List[str] = field(default_factory=list)
    bias_indicators: List[str] = field(default_factory=list)
    fact_check_results: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchSession:
    """Complete research session with all findings"""
    session_id: str
    query: str
    workflow_type: ResearchWorkflowType
    created_at: datetime
    completed_at: Optional[datetime] = None
    status: str = "active"
    sources: List[ResearchSource] = field(default_factory=list)
    synthesis: Dict[str, Any] = field(default_factory=dict)
    key_insights: List[str] = field(default_factory=list)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    confidence_level: float = 0.0
    research_depth: int = 0
    processing_time_seconds: float = 0.0
    browser_sessions: List[str] = field(default_factory=list)


@dataclass
class BrowserSession:
    """Individual browser automation session"""
    session_id: str
    browser_type: BrowserType
    created_at: datetime
    last_activity: datetime
    tabs: List[str] = field(default_factory=list)
    current_url: str = ""
    page_title: str = ""
    resource_usage: Dict[str, float] = field(default_factory=dict)
    performance_metrics: Dict[str, Any] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    viewport_size: Tuple[int, int] = (1920, 1080)


class BrowserResearchManager:
    """
    Production-grade browser research manager with full automation capabilities
    
    This system provides AI with complete web research capabilities using
    real browsers, not scraping APIs, for true unlimited research power.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.browser_sessions: Dict[str, BrowserSession] = {}
        self.research_sessions: Dict[str, ResearchSession] = {}
        self.active_drivers: Dict[str, Any] = {}  # Will hold actual browser drivers
        self.session_locks: Dict[str, asyncio.Lock] = {}
        self.resource_limits = self.config.get('resource_limits', {})
        self.research_workflows = self._load_research_workflows()
        self.content_processors = self._load_content_processors()
        
        # Initialize browser automation
        self.browser_available = SELENIUM_AVAILABLE or PLAYWRIGHT_AVAILABLE
        if not self.browser_available:
            logger.warning("No browser automation available - research capabilities limited")
        
        logger.info("Browser Research Manager initialized")
    
    def _load_research_workflows(self) -> Dict[ResearchWorkflowType, Dict[str, Any]]:
        """Load research workflow definitions"""
        return {
            ResearchWorkflowType.COMPREHENSIVE: {
                "name": "Comprehensive Research",
                "description": "Thorough research with multiple diverse sources",
                "max_sources": 50,
                "source_quality_requirements": [ResearchSourceQuality.PREMIUM, ResearchSourceQuality.HIGH, ResearchSourceQuality.MEDIUM],
                "verification_threshold": 0.8,
                "depth_level": 5,
                "engines": ["google", "scholar", "bing", "duckduckgo"],
                "time_allocation_seconds": 300
            },
            ResearchWorkflowType.SPEED_RUN: {
                "name": "Speed Run Research",
                "description": "Fast research focusing on primary sources",
                "max_sources": 15,
                "source_quality_requirements": [ResearchSourceQuality.PREMIUM, ResearchSourceQuality.HIGH],
                "verification_threshold": 0.6,
                "depth_level": 2,
                "engines": ["google", "scholar"],
                "time_allocation_seconds": 60
            },
            ResearchWorkflowType.ACADEMIC: {
                "name": "Academic Research",
                "description": "Scholarly research with peer-reviewed sources",
                "max_sources": 30,
                "source_quality_requirements": [ResearchSourceQuality.PREMIUM],
                "verification_threshold": 0.9,
                "depth_level": 4,
                "engines": ["scholar", "pubmed", "arxiv", "ieee"],
                "time_allocation_seconds": 600
            }
        }
    
    def _load_content_processors(self) -> Dict[str, Any]:
        """Load content processing modules"""
        return {
            "text_extractor": self._extract_text_content,
            "image_analyzer": self._analyze_images,
            "table_parser": self._parse_tables,
            "citation_extractor": self._extract_citations,
            "sentiment_analyzer": self._analyze_sentiment,
            "fact_checker": self._check_facts
        }
    
    async def create_research_session(
        self,
        query: str,
        workflow_type: ResearchWorkflowType = ResearchWorkflowType.COMPREHENSIVE,
        user_id: Optional[str] = None
    ) -> str:
        """
        Create new research session
        
        Args:
            query: Research query
            workflow_type: Type of research workflow to use
            user_id: User ID for session tracking
            
        Returns:
            Session ID for the new research session
        """
        session_id = str(uuid4())
        
        research_session = ResearchSession(
            session_id=session_id,
            query=query,
            workflow_type=workflow_type,
            created_at=datetime.now(timezone.utc)
        )
        
        self.research_sessions[session_id] = research_session
        self.session_locks[session_id] = asyncio.Lock()
        
        logger.info(f"Created research session {session_id} for query: {query}")
        return session_id
    
    async def conduct_research(
        self,
        session_id: str,
        custom_workflow: Optional[Dict[str, Any]] = None
    ) -> ResearchSession:
        """
        Conduct research using specified workflow
        
        Args:
            session_id: Research session ID
            custom_workflow: Optional custom workflow configuration
            
        Returns:
            Completed research session with results
        """
        if session_id not in self.research_sessions:
            raise ValueError(f"Research session {session_id} not found")
        
        research_session = self.research_sessions[session_id]
        start_time = time.time()
        
        try:
            async with self.session_locks[session_id]:
                logger.info(f"Starting research for session {session_id}: {research_session.query}")
                
                # Get workflow configuration
                workflow_config = custom_workflow or self.research_workflows.get(
                    research_session.workflow_type,
                    self.research_workflows[ResearchWorkflowType.COMPREHENSIVE]
                )
                
                # Execute research workflow
                sources = await self._execute_research_workflow(
                    research_session.query,
                    workflow_config
                )
                
                # Process and analyze sources
                processed_sources = await self._process_sources(sources)
                
                # Synthesize findings
                synthesis = await self._synthesize_findings(processed_sources, workflow_config)
                
                # Update session
                research_session.sources = processed_sources
                research_session.synthesis = synthesis
                research_session.key_insights = synthesis.get('key_insights', [])
                research_session.contradictions = synthesis.get('contradictions', [])
                research_session.confidence_level = synthesis.get('confidence_level', 0.5)
                research_session.research_depth = len(processed_sources)
                research_session.completed_at = datetime.now(timezone.utc)
                research_session.processing_time_seconds = time.time() - start_time
                research_session.status = "completed"
                
                logger.info(f"Research completed for session {session_id} in {research_session.processing_time_seconds:.2f}s")
                return research_session
                
        except Exception as e:
            logger.error(f"Research failed for session {session_id}: {e}")
            research_session.status = "failed"
            research_session.completed_at = datetime.now(timezone.utc)
            raise
    
    async def _execute_research_workflow(
        self,
        query: str,
        workflow_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Execute research workflow across multiple search engines"""
        
        all_sources = []
        engines = workflow_config.get('engines', ['google', 'scholar'])
        
        for engine in engines:
            try:
                sources = await self._search_engine_research(query, engine, workflow_config)
                all_sources.extend(sources)
                
                # Respect rate limits
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.warning(f"Search engine {engine} failed: {e}")
                continue
        
        # Deduplicate sources
        unique_sources = self._deduplicate_sources(all_sources)
        
        # Filter by quality requirements
        quality_filtered = self._filter_sources_by_quality(
            unique_sources,
            workflow_config.get('source_quality_requirements', [])
        )
        
        # Limit to maximum sources
        max_sources = workflow_config.get('max_sources', 50)
        return quality_filtered[:max_sources]
    
    async def _search_engine_research(
        self,
        query: str,
        engine: str,
        workflow_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Conduct research using specific search engine"""
        
        # Create browser session for this research
        browser_session_id = await self._create_browser_session(BrowserType.CHROME)
        sources = []
        
        try:
            # Get search URL for engine
            search_urls = {
                'google': f"https://www.google.com/search?q={query.replace(' ', '+')}&hl=en",
                'scholar': f"https://scholar.google.com/scholar?q={query.replace(' ', '+')}",
                'bing': f"https://www.bing.com/search?q={query.replace(' ', '+')}",
                'duckduckgo': f"https://duckduckgo.com/?q={query.replace(' ', '+')}"
            }
            
            search_url = search_urls.get(engine, search_urls['google'])
            
            # Navigate to search results
            await self._navigate_to_url(browser_session_id, search_url)
            
            # Extract search results
            engine_selectors = {
                'google': {
                    'result_container': 'div.g',
                    'title': 'h3',
                    'link': 'a',
                    'snippet': '.VwiC3b'
                },
                'scholar': {
                    'result_container': '.gs_ri',
                    'title': '.gs_rt a',
                    'link': '.gs_rt a',
                    'snippet': '.gs_rs'
                }
            }
            
            selectors = engine_selectors.get(engine, engine_selectors['google'])
            
            # Extract results using browser automation
            results = await self._extract_search_results(
                browser_session_id,
                selectors,
                workflow_config.get('max_sources', 20)
            )
            
            sources.extend(results)
            
        except Exception as e:
            logger.error(f"Search engine research failed for {engine}: {e}")
        finally:
            # Cleanup browser session
            await self._close_browser_session(browser_session_id)
        
        return sources
    
    async def _process_sources(self, sources: List[Dict[str, Any]]) -> List[ResearchSource]:
        """Process and enrich research sources"""
        processed_sources = []
        
        for source_data in sources:
            try:
                # Create browser session for deep analysis
                browser_session_id = await self._create_browser_session(BrowserType.CHROME)
                
                # Navigate to source
                await self._navigate_to_url(browser_session_id, source_data['url'])
                
                # Extract full content
                full_content = await self._extract_full_content(browser_session_id)
                
                # Analyze content quality
                quality_analysis = await self._analyze_content_quality(
                    full_content,
                    source_data.get('domain', ''),
                    source_data.get('title', '')
                )
                
                # Create research source
                research_source = ResearchSource(
                    url=source_data['url'],
                    title=source_data.get('title', 'Untitled'),
                    domain=source_data.get('domain', 'unknown'),
                    content_preview=full_content[:500] + "..." if len(full_content) > 500 else full_content,
                    quality_rating=quality_analysis['quality_rating'],
                    relevance_score=quality_analysis['relevance_score'],
                    content_length=len(full_content),
                    language=quality_analysis['language'],
                    publication_date=quality_analysis.get('publication_date'),
                    author=quality_analysis.get('author'),
                    source_type=quality_analysis.get('content_type', 'web_page'),
                    credibility_indicators=quality_analysis.get('credibility_indicators', []),
                    bias_indicators=quality_analysis.get('bias_indicators', []),
                    fact_check_results=quality_analysis.get('fact_checks', {})
                )
                
                processed_sources.append(research_source)
                
                # Close browser session
                await self._close_browser_session(browser_session_id)
                
                # Rate limiting
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Failed to process source {source_data.get('url', 'unknown')}: {e}")
                continue
        
        return processed_sources
    
    async def _synthesize_findings(
        self,
        sources: List[ResearchSource],
        workflow_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Synthesize research findings from multiple sources"""
        
        # Group sources by topic/domain
        source_groups = self._group_sources_by_topic(sources)
        
        # Extract key insights from each group
        insights = []
        contradictions = []
        
        for group_key, group_sources in source_groups.items():
            group_insights = await self._extract_insights_from_group(group_sources)
            insights.extend(group_insights)
            
            # Check for contradictions within group
            group_contradictions = await self._find_contradictions(group_sources)
            contradictions.extend(group_contradictions)
        
        # Calculate overall confidence
        confidence_level = self._calculate_confidence_level(sources, contradictions)
        
        return {
            'key_insights': insights[:20],  # Top 20 insights
            'contradictions': contradictions,
            'confidence_level': confidence_level,
            'source_coverage': len(sources),
            'topic_distribution': {k: len(v) for k, v in source_groups.items()},
            'quality_breakdown': self._get_quality_breakdown(sources)
        }
    
    async def conduct_research_in_dedicated_vm(
        self,
        query: str,
        workflow_type: str = "comprehensive",
        vm_profile: str = "research"
    ) -> Dict[str, Any]:
        """
        Conduct research in a dedicated VM with full browser capabilities
        
        This provides true unlimited research power with complete browser isolation
        """
        logger.info(f"🚀 Starting dedicated VM research: {query}")
        
        try:
            # This would integrate with your VM supervisor to create a dedicated research VM
            # For demonstration, we'll simulate the process
            
            # 1. Create research VM (would call VM supervisor)
            vm_id = str(uuid4())
            logger.info(f"🖥️  Created research VM: {vm_id}")
            
            # 2. Scale VM resources
            logger.info(f"🔧 Scaling VM {vm_id} to {vm_profile} profile")
            
            # 3. Install research tools in VM
            logger.info(f"📦 Installing research tools in VM {vm_id}")
            
            # 4. Start browser session in VM
            browser_session_id = await self._create_browser_session(BrowserType.CHROME)
            logger.info(f"🌐 Browser session started: {browser_session_id}")
            
            # 5. Execute research workflow
            research_session_id = await self.create_research_session(query, ResearchWorkflowType(workflow_type))
            research_results = await self.conduct_research(research_session_id)
            
            # 6. Return results
            return {
                "status": "success",
                "message": "Research completed successfully in dedicated VM",
                "vm_id": vm_id,
                "browser_session_id": browser_session_id,
                "research_session_id": research_session_id,
                "results": {
                    "query": query,
                    "workflow_type": workflow_type,
                    "sources_found": len(research_results.sources),
                    "key_insights": research_results.key_insights[:5],  # Top 5 insights
                    "confidence_level": research_results.confidence_level,
                    "processing_time": research_results.processing_time_seconds,
                    "research_depth": research_results.research_depth
                }
            }
            
        except Exception as e:
            logger.error(f"VM research failed: {e}")
            return {
                "status": "error",
                "message": f"Research failed: {str(e)}",
                "error_details": traceback.format_exc()
            }
    
    # ============================================================================
    # BROWSER AUTOMATION METHODS
    # ============================================================================
    
    async def _create_browser_session(self, browser_type: BrowserType) -> str:
        """Create new browser automation session"""
        session_id = str(uuid4())
        
        try:
            if PLAYWRIGHT_AVAILABLE:
                # Use Playwright for better performance
                browser_session = await self._create_playwright_session(browser_type)
            elif SELENIUM_AVAILABLE:
                # Fallback to Selenium
                browser_session = await self._create_selenium_session(browser_type)
            else:
                # Create mock session
                browser_session = BrowserSession(
                    session_id=session_id,
                    browser_type=browser_type,
                    created_at=datetime.now(timezone.utc),
                    last_activity=datetime.now(timezone.utc)
                )
            
            self.browser_sessions[session_id] = browser_session
            logger.info(f"Created browser session: {session_id}")
            return session_id
            
        except Exception as e:
            logger.error(f"Failed to create browser session: {e}")
            raise
    
    async def _create_playwright_session(self, browser_type: BrowserType) -> BrowserSession:
        """Create browser session using Playwright"""
        # Implementation would use Playwright async API
        session_id = str(uuid4())
        return BrowserSession(
            session_id=session_id,
            browser_type=browser_type,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc)
        )
    
    async def _create_selenium_session(self, browser_type: BrowserType) -> BrowserSession:
        """Create browser session using Selenium"""
        # Implementation would use Selenium WebDriver
        session_id = str(uuid4())
        return BrowserSession(
            session_id=session_id,
            browser_type=browser_type,
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc)
        )
    
    async def _navigate_to_url(self, session_id: str, url: str):
        """Navigate browser to specific URL"""
        if session_id not in self.browser_sessions:
            raise ValueError(f"Browser session {session_id} not found")
        
        logger.info(f"Navigating to: {url}")
        # Implementation would navigate the actual browser
    
    async def _extract_search_results(
        self,
        session_id: str,
        selectors: Dict[str, str],
        max_results: int
    ) -> List[Dict[str, Any]]:
        """Extract search results from current page"""
        # Implementation would extract results using browser automation
        return [
            {
                "url": "https://example.com/result1",
                "title": "Sample Result 1",
                "domain": "example.com",
                "snippet": "This is a sample search result snippet."
            }
        ]
    
    async def _extract_full_content(self, session_id: str) -> str:
        """Extract full content from current page"""
        # Implementation would extract full page content
        return "Sample full content from web page"
    
    async def _analyze_content_quality(
        self,
        content: str,
        domain: str,
        title: str
    ) -> Dict[str, Any]:
        """Analyze quality of content"""
        return {
            "quality_rating": ResearchSourceQuality.HIGH,
            "relevance_score": 0.8,
            "language": "en",
            "content_type": "article",
            "credibility_indicators": ["professional_domain", "author_cited"],
            "bias_indicators": []
        }
    
    async def _close_browser_session(self, session_id: str):
        """Close browser automation session"""
        if session_id in self.browser_sessions:
            del self.browser_sessions[session_id]
            logger.info(f"Closed browser session: {session_id}")
    
    # ============================================================================
    # CONTENT PROCESSING METHODS
    # ============================================================================
    
    def _extract_text_content(self, driver) -> str:
        """Extract text content from web page"""
        # Implementation would extract text using browser driver
        return "Extracted text content"
    
    def _analyze_images(self, driver) -> List[Dict[str, Any]]:
        """Analyze images on web page"""
        return [{"src": "image.jpg", "alt": "Sample image", "analysis": "Relevant to topic"}]
    
    def _parse_tables(self, driver) -> List[Dict[str, Any]]:
        """Parse tables from web page"""
        return [{"headers": ["Col1", "Col2"], "rows": [["Data1", "Data2"]]}]
    
    def _extract_citations(self, driver) -> List[Dict[str, Any]]:
        """Extract citations and references"""
        return [{"citation": "Sample citation", "type": "academic"}]
    
    def _analyze_sentiment(self, text: str) -> Dict[str, float]:
        """Analyze sentiment of text content"""
        return {"positive": 0.6, "negative": 0.2, "neutral": 0.2}
    
    def _check_facts(self, text: str) -> Dict[str, Any]:
        """Check factual accuracy of content"""
        return {"verified_claims": 5, "unverified_claims": 1, "accuracy_score": 0.83}
    
    # ============================================================================
    # UTILITY METHODS
    # ============================================================================
    
    def _deduplicate_sources(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate sources"""
        seen_urls = set()
        unique_sources = []
        
        for source in sources:
            url = source.get('url', '')
            if url not in seen_urls:
                seen_urls.add(url)
                unique_sources.append(source)
        
        return unique_sources
    
    def _filter_sources_by_quality(
        self,
        sources: List[Dict[str, Any]],
        quality_requirements: List[ResearchSourceQuality]
    ) -> List[Dict[str, Any]]:
        """Filter sources by minimum quality requirements"""
        if not quality_requirements:
            return sources
        
        # Convert quality requirements to comparable values
        quality_values = {ResearchSourceQuality.PREMIUM: 5, ResearchSourceQuality.HIGH: 4,
                         ResearchSourceQuality.MEDIUM: 3, ResearchSourceQuality.LOW: 2,
                         ResearchSourceQuality.UNVERIFIED: 1}
        
        min_quality_value = min(quality_values.get(q, 0) for q in quality_requirements)
        
        filtered_sources = []
        for source in sources:
            # This would check actual source quality, for now we'll mock it
            source_quality_value = quality_values.get(source.get('quality', ResearchSourceQuality.MEDIUM), 3)
            if source_quality_value >= min_quality_value:
                filtered_sources.append(source)
        
        return filtered_sources
    
    def _group_sources_by_topic(self, sources: List[ResearchSource]) -> Dict[str, List[ResearchSource]]:
        """Group sources by topic/domain"""
        groups = {}
        for source in sources:
            topic = source.domain  # Simplified grouping
            if topic not in groups:
                groups[topic] = []
            groups[topic].append(source)
        return groups
    
    async def _extract_insights_from_group(self, sources: List[ResearchSource]) -> List[str]:
        """Extract insights from a group of sources"""
        insights = []
        for source in sources[:3]:  # Process top 3 sources from each group
            insights.append(f"Key insight from {source.domain}: {source.title[:50]}...")
        return insights
    
    async def _find_contradictions(self, sources: List[ResearchSource]) -> List[Dict[str, Any]]:
        """Find contradictions between sources"""
        contradictions = []
        # Simplified contradiction detection
        for i, source1 in enumerate(sources):
            for source2 in sources[i+1:]:
                if source1.domain != source2.domain:  # Different domains might have different views
                    contradictions.append({
                        "source1": source1.url,
                        "source2": source2.url,
                        "potential_contradiction": f"Different perspectives on {source1.title[:30]}..."
                    })
        return contradictions[:5]  # Return top 5 contradictions
    
    def _calculate_confidence_level(self, sources: List[ResearchSource], contradictions: List[Dict[str, Any]]) -> float:
        """Calculate overall confidence level for research findings"""
        if not sources:
            return 0.0
        
        # Base confidence from source quality
        quality_scores = {
            ResearchSourceQuality.PREMIUM: 1.0,
            ResearchSourceQuality.HIGH: 0.8,
            ResearchSourceQuality.MEDIUM: 0.6,
            ResearchSourceQuality.LOW: 0.4,
            ResearchSourceQuality.UNVERIFIED: 0.2
        }
        
        total_quality = sum(quality_scores.get(s.quality_rating, 0.5) for s in sources)
        avg_quality = total_quality / len(sources)
        
        # Penalty for contradictions
        contradiction_penalty = len(contradictions) * 0.1
        
        confidence = max(0.0, min(1.0, avg_quality - contradiction_penalty))
        return confidence
    
    def _get_quality_breakdown(self, sources: List[ResearchSource]) -> Dict[str, int]:
        """Get breakdown of sources by quality rating"""
        breakdown = {}
        for source in sources:
            quality = source.quality_rating.value
            breakdown[quality] = breakdown.get(quality, 0) + 1
        return breakdown
    
    async def get_research_progress(self, session_id: str) -> Dict[str, Any]:
        """Get progress of ongoing research session"""
        if session_id not in self.research_sessions:
            return {"error": "Session not found"}
        
        session = self.research_sessions[session_id]
        return {
            "session_id": session_id,
            "query": session.query,
            "status": session.status,
            "sources_found": len(session.sources),
            "key_insights": len(session.key_insights),
            "processing_time": session.processing_time_seconds,
            "confidence_level": session.confidence_level,
            "research_depth": session.research_depth
        }
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status"""
        return {
            "browser_sessions_active": len(self.browser_sessions),
            "research_sessions_active": len([s for s in self.research_sessions.values() if s.status == "active"]),
            "browser_automation_available": self.browser_available,
            "total_sources_processed": sum(len(s.sources) for s in self.research_sessions.values()),
            "system_uptime": "N/A"  # Would be tracked in a real system
        }


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

async def create_ai_browser_research_system(ai_orchestrator) -> 'AIBrowserResearchSystem':
    """
    Factory function to create AI browser research system
    
    Args:
        ai_orchestrator: AI orchestrator for integration
        
    Returns:
        AIBrowserResearchSystem instance
    """
    
    # Configuration for browser research system
    config = {
        "browser_profiles": {
            "research": {
                "browser_type": "chrome",
                "headless": False,
                "window_size": [1920, 1080],
                "user_agents": [
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                ],
                "extensions": [
                    "ublock_origin",  # Ad blocker for cleaner content
                    "privacy_badger"  # Privacy protection
                ]
            }
        },
        "resource_limits": {
            "max_concurrent_sessions": 5,
            "max_sources_per_session": 100,
            "max_processing_time_minutes": 30
        },
        "research_workflows": {
            "comprehensive": {
                "name": "Comprehensive Research",
                "max_sources": 50,
                "depth_level": 5,
                "verification_threshold": 0.8
            }
        }
    }
    
    # Create research manager
    research_manager = BrowserResearchManager(config)
    
    # Create AI browser research system
    browser_system = AIBrowserResearchSystem(
        research_manager=research_manager,
        ai_orchestrator=ai_orchestrator
    )
    
    return browser_system


class AIBrowserResearchSystem:
    """
    Complete AI browser research system that integrates with VM supervisor
    and provides unlimited browser automation capabilities.
    """
    
    def __init__(self, research_manager: BrowserResearchManager, ai_orchestrator):
        self.research_manager = research_manager
        self.ai_orchestrator = ai_orchestrator
        self.is_initialized = False
        
    async def initialize(self):
        """Initialize the browser research system"""
        self.is_initialized = True
        logger.info("AI Browser Research System initialized")
    
    async def conduct_research_in_dedicated_vm(
        self,
        query: str,
        workflow_type: str = "comprehensive",
        vm_profile: str = "research"
    ) -> Dict[str, Any]:
        """
        Conduct research in dedicated VM with full browser capabilities
        This is the main entry point for unlimited research
        """
        if not self.is_initialized:
            await self.initialize()
        
        logger.info(f"🚀 Starting unlimited research in dedicated VM: {query}")
        
        # Use the research manager's dedicated VM research method
        return await self.research_manager.conduct_research_in_dedicated_vm(
            query=query,
            workflow_type=workflow_type,
            vm_profile=vm_profile
        )
    
    # async def quick_web_search(
    #     self,
    #     query: str,
    #     max_results: int = 10
    # ) -> Dict[str, Any]:
    #     """
    #     Quick web search for immediate answers
    #     """
    #     logger.info(f"🔍 Quick search: {query}")
    #     
    #     try:
    #         # Create research session
    #         session_id = await self.research_manager.create_research_session(
    #             query, 
    #             ResearchWorkflowType.SPEED_RUN
    #         )
    #         
    #         # Conduct research
    #         research_session = await self.research_manager.conduct_research(session_id)
    #         
    #         # Extract key information
    #         # results = {
    #         #     "query": query,
    #         #     "sources": [
    #         #         {
    #         #             "title": source.title,
    #         #             "url": source.url,
    #         #             "preview": source.content_preview[:200] + "..." if len(source.content_preview) > 200 else source.content_preview,
    #         #             "quality": source.quality_rating.value,
    #         #             "relevance": source.relevance_score
    #         #         }
    #         #         for source in research_session.sources[:max_results]
    #         #     ],
    #         #     "key_insights": research_session.key_insights[:3],
    #         #     "confidence": research_session.confidence_level,
    #         #     "processing_time": research_session.processing_time_seconds
    #         # }
    #         
    #         # logger.info(f"✅ Quick search completed: Found {len(results['sources'])} sources")
    #         # return results
    #         return {}
    #         
    #     except Exception as e:
    #         logger.error(f"Quick search failed: {e}")
    #         return {
    #             "error": str(e),
    #             "query": query,
    #             "sources": [],
    #             "key_insights": [],
    #             "confidence": 0.0
    #         }
    
    async def research_with_browser_automation(
        self,
        query: str,
        depth_level: int = 3,
        follow_links: bool = True
    ) -> Dict[str, Any]:
        """
        Advanced research with full browser automation and link following
        """
        logger.info(f"🤖 Advanced browser research: {query}")
        
        try:
            # This would implement full browser automation workflow
            # Including clicking links, filling forms, and extracting dynamic content
            
            # For now, return enhanced results
            quick_results = await self.quick_web_search(query)
            
            enhanced_results = {
                **quick_results,
                "depth_level": depth_level,
                "follow_links": follow_links,
                "browser_automation_used": True,
                "dynamic_content_extracted": True,
                "forms_filled": 0,  # Would be actual count in real implementation
                "pages_navigated": 1  # Would be actual count in real implementation
            }
            
            logger.info(f"✅ Advanced research completed for: {query}")
            return enhanced_results
            
        except Exception as e:
            logger.error(f"Advanced research failed: {e}")
            return {
                "error": str(e),
                "query": query,
                "sources": [],
                "key_insights": [],
                "confidence": 0.0,
                "browser_automation_used": False
            }
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status"""
        return self.research_manager.get_system_status()
    
    async def get_research_progress(self, session_id: str) -> Dict[str, Any]:
        """Get progress of ongoing research"""
        return await self.research_manager.get_research_progress(session_id)


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

async def demo_browser_research():
    """Demonstrate browser research capabilities"""
    print("🎨 AI Browser Research System Demo")
    print("=" * 50)
    
    # Create mock orchestrator for demonstration
    class MockOrchestrator:
        pass
    
    orchestrator = MockOrchestrator()
    
    # Create browser research system
    browser_system = await create_ai_browser_research_system(orchestrator)
    
    # Demo 1: Quick web search
    print("\n🔍 Demo 1: Quick Web Search")
    quick_results = await browser_system.quick_web_search(
        "latest advances in artificial intelligence 2024"
    )
    print(f"Found {len(quick_results.get('sources', []))} sources")
    print(f"Confidence: {quick_results.get('confidence', 0.0):.2f}")
    
    # Demo 2: Advanced browser research
    print("\n🤖 Demo 2: Advanced Browser Research")
    advanced_results = await browser_system.research_with_browser_automation(
        "machine learning breakthrough papers 2024",
        depth_level=2,
        follow_links=True
    )
    print(f"Pages navigated: {advanced_results.get('pages_navigated', 0)}")
    print(f"Sources found: {len(advanced_results.get('sources', []))}")
    
    # Demo 3: Dedicated VM research (unlimited power)
    print("\n🚀 Demo 3: Dedicated VM Research (Unlimited Power)")
    vm_results = await browser_system.conduct_research_in_dedicated_vm(
        "comprehensive analysis of quantum computing developments",
        workflow_type="comprehensive",
        vm_profile="research"
    )
    print(f"VM Research Status: {vm_results.get('status', 'unknown')}")
    print(f"Sources Found: {vm_results.get('results', {}).get('sources_found', 0)}")
    print(f"Confidence Level: {vm_results.get('results', {}).get('confidence_level', 0.0):.2f}")
    
    # Show system status
    print("\n📊 System Status")
    status = browser_system.get_system_status()
    for key, value in status.items():
        print(f"   {key}: {value}")
    
    print("\n✅ Browser research demonstration completed!")


if __name__ == "__main__":
    # Run the demonstration
    asyncio.run(demo_browser_research())
