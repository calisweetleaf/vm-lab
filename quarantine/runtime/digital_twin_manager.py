# digital_twin_manager.py
"""
SOMNUS SYSTEMS - The Digital Twin Manager
An AI that builds a comprehensive, multi-layered model of itself and its environment.

This module creates the AI's "cognition" through a tiered memory system:
- Live Memory: Real-time, volatile system state for sense-of-self. This works in tandem with ASPS
- Hot Memory: A high-speed vector database of the AI's capabilities and architecture.
- Cold Memory: The deep, persistent archive of logs and raw configuration files.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

# Core system imports (assuming they are accessible)
from vm_settings import VMSettingsManager
from artifact_settings import OnDemandCapabilityManager
from research_settings_system import ResearchSettingsManager
from agent_collaboration_core import AgentCollaborationHub
from core.memory_core import MemoryManager

# For the "Hot Memory" tier (as seen in project_knowledge.py)
try:
    import chromadb
    from sentence_transformers import SentenceTransformer
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

logger = logging.getLogger(__name__)

@dataclass
class LiveStateData:
    """Holds real-time, volatile state data for the Digital Twin."""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    active_vms: int = 0
    active_capabilities: list = field(default_factory=list)
    active_agents: int = 0
    timestamp: float = field(default_factory=time.time)

class DigitalTwinManager:
    """Manages the AI's self-model across live, hot, and cold memory tiers."""

    def __init__(
        self,
        vm_settings_manager: VMSettingsManager,
        artifact_settings_manager: OnDemandCapabilityManager,
        research_settings_manager: ResearchSettingsManager,
        agent_hub: AgentCollaborationHub,
        memory_manager: MemoryManager
    ):
        self.vm_settings = vm_settings_manager
        self.artifact_settings = artifact_settings_manager
        self.research_settings = research_settings_manager
        self.agent_hub = agent_hub
        self.memory_manager = memory_manager # For "Cold Memory" access

        # Tier 1: Live Memory
        self.live_state = LiveStateData()

        # Tier 2: Hot Memory (Vector DB)
        if CHROMA_AVAILABLE:
            self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            self.chroma_client = chromadb.Client()
            self.hot_memory = self.chroma_client.get_or_create_collection("digital_twin_hot_memory")
        else:
            self.hot_memory = None
            logger.warning("ChromaDB or SentenceTransformers not found. Hot memory will be disabled.")

        logger.info("Digital Twin Manager initialized.")

    async def initialize_twin(self):
        """Builds the initial hot memory index and starts live monitoring."""
        logger.info("Initializing the Digital Twin...")
        # Start the live memory monitor
        asyncio.create_task(self._monitor_live_state())
        # Build the hot memory index from all configuration files
        await self._build_hot_memory_index()
        logger.info("Digital Twin is online and self-aware.")

    async def _monitor_live_state(self):
        """Periodically updates the 'Live Memory' with real-time system status."""
        while True:
            try:
                import psutil
                self.live_state.cpu_percent = psutil.cpu_percent()
                self.live_state.memory_percent = psutil.virtual_memory().percent
                self.live_state.active_vms = len(self.vm_settings.active_vms)
                self.live_state.active_capabilities = list(self.artifact_settings.active_capabilities.keys())
                self.live_state.active_agents = len(self.agent_hub.agents)
                self.live_state.timestamp = time.time()
            except Exception as e:
                logger.error(f"Error updating live state: {e}")
            await asyncio.sleep(5) # Update every 5 seconds

    async def _build_hot_memory_index(self):
        """Reads all settings, extracts key info, and builds the vector index."""
        if not self.hot_memory:
            return

        logger.info("Building hot memory index for Digital Twin...")
        documents = []
        metadatas = []
        ids = []
        doc_id_counter = 0

        # Process VM Settings
        vm_pool_settings = self.vm_settings.pool_settings.dict()
        documents.append(f"VM Pool Configuration: Manages up to {vm_pool_settings['max_concurrent_vms']} VMs per user. Base image is located at {vm_pool_settings['base_image_path']}.")
        metadatas.append({"source": "vm_settings.py", "type": "configuration"})
        ids.append(f"doc_{doc_id_counter}"); doc_id_counter += 1

        # Process Artifact Settings
        artifact_settings = self.artifact_settings.settings.dict()
        for cap_id, cap_def in artifact_settings.get('available_capabilities', {}).items():
            documents.append(f"Capability '{cap_def['name']}': {cap_def['description']}. It runs in a '{cap_def['container_image']}' container and is triggered by {', '.join(cap_def['activation_triggers'])}.")
            metadatas.append({"source": "artifact_settings.py", "type": "capability", "capability_id": cap_id})
            ids.append(f"doc_{doc_id_counter}"); doc_id_counter += 1
            
        # Process Research Settings
        research_defaults = self.research_settings.global_defaults
        documents.append(f"Research Subsystem Default Mode: {research_defaults.research_mode.value}. Default Trust Level: {research_defaults.source_trust_level.value}.")
        metadatas.append({"source": "research_settings_system.py", "type": "configuration"})
        ids.append(f"doc_{doc_id_counter}"); doc_id_counter += 1

        # Process Agent Profiles
        for agent in self.agent_hub.agents.values():
            profile = agent.profile
            documents.append(f"Agent Profile '{profile.name}': Specializes in {', '.join([c.value for c in profile.capabilities])}. Operates with a collaboration preference of {profile.collaboration_preference}.")
            metadatas.append({"source": "agent_collaboration_core.py", "type": "agent_profile", "agent_id": str(profile.agent_id)})
            ids.append(f"doc_{doc_id_counter}"); doc_id_counter += 1

        # Add documents to ChromaDB
        if documents:
            embeddings = self.embedding_model.encode(documents).tolist()
            self.hot_memory.add(embeddings=embeddings, documents=documents, metadatas=metadatas, ids=ids)
        logger.info(f"Hot memory index built with {len(documents)} documents.")

    async def query_digital_twin(self, query: str) -> str:
        """The main interface for the AI to ask questions about itself."""
        logger.info(f"Digital Twin received query: '{query}'")
        
        # 1. Check Live Memory for real-time data
        if "current cpu" in query.lower() or "right now" in query.lower():
            return f"My current CPU usage is {self.live_state.cpu_percent:.1f}% and memory is at {self.live_state.memory_percent:.1f}%. I have {self.live_state.active_vms} VMs and {len(self.live_state.active_capabilities)} capabilities active."

        # 2. Query Hot Memory for architectural/capability questions
        if self.hot_memory:
            query_embedding = self.embedding_model.encode([query]).tolist()
            results = self.hot_memory.query(query_embeddings=query_embedding, n_results=3)
            
            context = "Based on my self-model:\n"
            for doc in results['documents'][0]:
                context += f"- {doc}\n"
            
            # Use an AI model to synthesize the final answer
            # This would call one of your existing agent models
            final_answer = f"Synthesized Answer based on Hot Memory:\nQuery: {query}\nContext:\n{context}"
            logger.info("Answer synthesized from Hot Memory.")
            return final_answer

        # 3. Fallback to Cold Memory for historical/deep queries (Simplified)
        if "history" in query.lower() or "last week" in query.lower():
            logger.info("Query requires deep introspection. Accessing Cold Memory...")
            # This is a simplified example. A real implementation would parse the query
            # and perform a structured search against the MemoryManager.
            memories = await self.memory_manager.retrieve_memories(user_id="system", query="system_event", limit=5)
            return f"From my deep memory archives, here are some recent system events: {json.dumps(memories, indent=2, default=str)}"

        return "I am unable to answer that question about myself at this time."

if __name__ == "__main__":
    # This is an example of how you would integrate and use the DigitalTwinManager
    # You would need to mock or provide real instances of your manager classes.
    
    async def demo():
        # Mocking the managers for demonstration purposes
        class MockManager:
            def __init__(self, data={}):
                self.__dict__.update(data)

        # Create mock managers with sample data that mirrors your system
        mock_vm_settings = MockManager({
            "pool_settings": MockManager({"dict": lambda: {"max_concurrent_vms": 10, "base_image_path": "/path/to/image.qcow2"}}),
            "active_vms": {"vm1": True, "vm2": True}
        })
        mock_artifact_settings = MockManager({
            "settings": MockManager({"dict": lambda: {"available_capabilities": {
                "unlimited_execution": {"name": "Unlimited Execution", "description": "Execute any code.", "container_image": "img1", "activation_triggers": ["user_intent"]}
            }}}),
            "active_capabilities": {"unlimited_execution": True}
        })
        mock_research_settings = MockManager({
            "global_defaults": MockManager({"research_mode": MockManager({"value": "balanced"}), "source_trust_level": MockManager({"value": "curated"})})
        })
        mock_agent_hub = MockManager({
            "agents": {
                "agent1": MockManager({"profile": MockManager({"name": "Researcher", "capabilities": [MockManager({"value": "research"})], "collaboration_preference": 0.8, "agent_id": "agent1"})})
            }
        })
        mock_memory_manager = MockManager() # Add mock methods if needed

        # Initialize the Digital Twin
        twin = DigitalTwinManager(
            mock_vm_settings,
            mock_artifact_settings,
            mock_research_settings,
            mock_agent_hub,
            mock_memory_manager
        )
        await twin.initialize_twin()
        
        print("-" * 50)
        
        # Example Query 1: Live State
        response1 = await twin.query_digital_twin("What is your current CPU and memory usage right now?")
        print(f"Query 1 -> Live Memory:\n{response1}\n")
        
        print("-" * 50)

        # Example Query 2: Hot Memory (Capabilities)
        response2 = await twin.query_digital_twin("What are my capabilities for code execution?")
        print(f"Query 2 -> Hot Memory:\n{response2}\n")

        print("-" * 50)

    # Run the demo
    logging.basicConfig(level=logging.INFO)
    if CHROMA_AVAILABLE:
        asyncio.run(demo())
    else:
        print("Please install chromadb and sentence-transformers (`pip install chromadb sentence-transformers`) to run the demo.")

