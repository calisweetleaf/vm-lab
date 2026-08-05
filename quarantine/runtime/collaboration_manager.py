# core/collaboration_manager.py
"""
Collaboration Session Manager
----------------------------
Central orchestrator for multi‑agent collaboration sessions.
It provisions VMs, loads models, creates shared memory/workspace, and
coordinates the :class:`AgentCollaborationHub`.

The implementation follows the plan outlined by the user and integrates
with existing Somnus components without introducing external dependencies.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
import logging

# --- Somnus imports -------------------------------------------------------
# Virtual machine supervision
from .virtual_machine.vm_supervisor import VMSupervisor, AIVMInstance

# Agent collaboration core (message bus, hub, etc.)
from multi_agent.multi_agent_collab.agent_collaboration_core import AgentCollaborationHub

# Memory management (shared context for the session)
from core.memory_core import MemoryManager
# Session‑scoped memory context
from core.memory_integration import SessionMemoryContext
# Unified cache integrated with memory core
from .system_cache import create_somnus_cache, SomnusCache, SessionCacheContext

# Model loading for each agent VM
from core.model_loader import ModelLoader

# Prompt management – stable system prompts for role identity
from core.prompt_manager import SystemPromptManager

# File processing & shared workspace handling
from core.accelerated_file_processing import IntelligentFileProcessor

# -------------------------------------------------------------------------

# NOTE: ``SessionMemoryContext`` is not a concrete class in the current codebase.
# For now we treat the ``MemoryManager`` instance itself as the shared context.
# If a dedicated context class is added later, replace the type hint accordingly.


class CollaborationSession:
    """State holder for an active multi‑agent collaboration session.

    Attributes
    ----------
    session_id: UUID
        Unique identifier for the session.
    user_id: str
        Identifier of the user who created the session.
    agents: Dict[str, AIVMInstance]
        Mapping from role name (e.g. "researcher") to the VM instance that hosts
        the agent.
    shared_workspace: Path
        Directory on the host that is mounted into each agent VM.
    memory_manager: Optional[MemoryManager]
        Shared memory context used by agents to exchange information.
    memory_context: Optional[SessionMemoryContext]
        Session-scoped helper that manages session-specific memories and
        context-window enhancement.
    cache: Optional[SomnusCache]
        Runtime cache (hot data) for this session; namespaced to the
        session and cleaned up on shutdown.
    hub: Optional[AgentCollaborationHub]
        Central hub that routes messages between agents.
    status: str
        Human‑readable status flag (initializing, ready, shutting_down, …).
    """

    def __init__(self, session_id: UUID, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
        self.agents: Dict[str, AIVMInstance] = {}

        # Use a cross-platform temporary base directory for session workspaces
        base_tmp = Path(tempfile.gettempdir()) / "collab_sessions"
        self.shared_workspace = base_tmp / str(session_id)

        # Shared services / helpers (wired during session creation)
        self.memory_manager: Optional[MemoryManager] = None
        self.memory_context: Optional[SessionMemoryContext] = None
        self.cache: Optional[SomnusCache] = None
        self.cache_context: Optional[SessionCacheContext] = None
        self.hub: Optional[AgentCollaborationHub] = None
        self.status = "initializing"

        # Ensure the workspace exists on the host.
        self.shared_workspace.mkdir(parents=True, exist_ok=True)

    # Helper to clean up the workspace on shutdown.
    def _cleanup_workspace(self) -> None:
        if self.shared_workspace.exists():
            shutil.rmtree(self.shared_workspace, ignore_errors=True)


class CollaborationSessionManager:
    """Central orchestrator for multi‑agent collaboration sessions.

    The manager is instantiated once (e.g. as a FastAPI singleton) and holds
    references to the core services required to spin up a session.
    """

    def __init__(
        self,
        vm_supervisor: VMSupervisor,
        model_loader: ModelLoader,
        memory_manager: MemoryManager,
        prompt_manager: SystemPromptManager,
        file_processor: IntelligentFileProcessor,
    ) -> None:
        self.vm_supervisor = vm_supervisor
        self.model_loader = model_loader
        self.memory_manager = memory_manager
        self.prompt_manager = prompt_manager
        self.file_processor = file_processor
        self.active_sessions: Dict[UUID, CollaborationSession] = {}

    async def create_session(
        self,
        user_id: str,
        agent_profiles: List[Dict[str, Any]],
        enable_web_access: bool = False,
    ) -> CollaborationSession:
        """Create and provision a full multi‑agent collaboration session.

        Parameters
        ----------
        user_id: str
            Identifier of the user requesting the session.
        agent_profiles: List[Dict[str, Any]]
            Each dict must contain at least ``role`` (str) and ``model_id`` (str).
            Additional keys are passed as ``personality_config`` to the VM.
        enable_web_access: bool, optional
            If ``True`` the agents' system prompts will include web‑access
            permissions.
        """
        session_id = uuid4()
        session = CollaborationSession(session_id, user_id)

        # -----------------------------------------------------------------
        # 1️⃣ Provision VMs for each requested agent profile.
        # -----------------------------------------------------------------
        provisioning_tasks = []
        for profile in agent_profiles:
            # ``create_ai_computer`` is assumed to be an async method on the
            # supervisor that returns an ``AIVMInstance``.
            task = self.vm_supervisor.create_ai_computer(
                instance_name=f"collab-{session_id}-{profile['role']}",
                personality_config=profile,
            )
            provisioning_tasks.append(task)

        vm_instances: List[AIVMInstance] = await asyncio.gather(*provisioning_tasks)

        for profile, vm in zip(agent_profiles, vm_instances):
            session.agents[profile["role"]] = vm

        # -----------------------------------------------------------------
        # 2️⃣ Load models into each VM.
        # -----------------------------------------------------------------
        model_load_tasks = []
        for profile in agent_profiles:
            model_id = profile.get("model_id")
            if model_id:
                # ``load_model_into_vm`` is a placeholder for the actual
                # implementation – it should copy the model files into the VM
                # and register them with the VM's ``ModelLoader``.
                task = self.model_loader.load_model_into_vm(
                    vm_instance=session.agents[profile["role"]],
                    model_id=model_id,
                )
                model_load_tasks.append(task)
        if model_load_tasks:
            await asyncio.gather(*model_load_tasks)

        # -----------------------------------------------------------------
        # 3️⃣ Create shared memory context.
        # -----------------------------------------------------------------
        # For now we reuse the global ``MemoryManager``; a per‑session slice can
        # be created later if needed.
        session.memory_manager = self.memory_manager

        # Instantiate a session-scoped memory context helper. Use string ids
        # for interoperability with the memory subsystem types in the repo.
        try:
            session.memory_context = SessionMemoryContext(
                session_id=str(session_id),
                user_id=user_id,
                memory_manager=self.memory_manager,
            )
            # initialize_context may enrich system prompts or preload relevant memories
            await session.memory_context.initialize_context()
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to initialize SessionMemoryContext for %s: %s", session_id, e
            )

        # Create a per-session runtime cache (hot store). The cache factory
        # will start background maintenance tasks; pass the memory manager so
        # entries may optionally be persisted into the long-term memory store.
        try:
            session.cache = create_somnus_cache(
                {"cache_dir": f"data/runtime_cache/{session_id}"},
                memory_manager=self.memory_manager,
            )
            # Provide a thin session-scoped interface for convenience
            try:
                session.cache_context = SessionCacheContext(session.cache, str(session_id))
            except Exception:
                session.cache_context = None
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to create SomnusCache for %s: %s", session_id, e
            )

        # -----------------------------------------------------------------
        # 4️⃣ Initialise the AgentCollaborationHub.
        # -----------------------------------------------------------------
        session.hub = AgentCollaborationHub(
            agents=session.agents,
            shared_memory=session.memory_manager,
            shared_workspace=session.shared_workspace,
            prompt_manager=self.prompt_manager,
            file_processor=self.file_processor,
            enable_web=enable_web_access,
        )

        # Attach session-scoped helpers to the hub so agents can access them
        # without requiring changes to the hub constructor signature.
        if session.hub:
            try:
                setattr(session.hub, "memory_context", session.memory_context)
                setattr(session.hub, "cache", session.cache)
                # Attach the session-scoped cache context if available
                try:
                    setattr(session.hub, "cache_context", getattr(session, "cache_context", None))
                except Exception:
                    pass
            except Exception:
                # non-fatal; hub will continue to operate even if helpers aren't attached
                logging.getLogger(__name__).debug(
                    "Could not attach memory_context/cache to AgentCollaborationHub for %s",
                    session_id,
                )

        session.status = "ready"
        self.active_sessions[session_id] = session
        return session

    async def dispatch_task_to_session(self, session_id: UUID, task: str) -> Any:
        """Send a high‑level user task to the session's collaboration hub.

        The hub is responsible for delegating the task to the appropriate
        agents, collecting intermediate results and synthesising a final answer.
        """
        session = self.active_sessions.get(session_id)
        if not session or not session.hub:
            raise ValueError("Session not found or not ready.")
        return await session.hub.process_user_input(task, context={})

    async def shutdown_session(self, session_id: UUID) -> None:
        """Shut down all VMs and clean up resources for a session."""
        session = self.active_sessions.pop(session_id, None)
        if not session:
            return

        # Attempt to persist or summarise any in-memory session state before
        # tearing down VMs. These steps are best-effort: failures should not
        # prevent resource cleanup.
        if session.memory_context:
            try:
                # If implemented, get a short summary or flush buffer to persistent store
                summary = await session.memory_context.get_session_summary()
                logging.getLogger(__name__).info(
                    "Session %s memory summary on shutdown: %s", session_id, summary
                )
            except Exception:
                logging.getLogger(__name__).debug(
                    "SessionMemoryContext flush failed for %s", session_id
                )

        if session.cache:
            try:
                # Clear session-scoped cache entries and stop background workers
                try:
                    session.cache.clear_session(str(session_id))
                except Exception:
                    pass
                try:
                    # Prefer a full shutdown of the per-session cache instance
                    session.cache.shutdown()
                except Exception:
                    # Some cache implementations expose stop_background_cleanup()
                    try:
                        session.cache.stop_background_cleanup()
                    except Exception:
                        pass
                # Clear any lightweight cache context wrapper if present
                try:
                    if getattr(session, "cache_context", None):
                        session.cache_context.clear()
                except Exception:
                    pass
            except Exception:
                logging.getLogger(__name__).debug(
                    "Failed to cleanly shutdown SomnusCache for %s", session_id
                )

        # -----------------------------------------------------------------
        # 1️⃣ Shut down each VM.
        # -----------------------------------------------------------------
        shutdown_tasks = [
            self.vm_supervisor.shutdown_vm(vm.vm_id) for vm in session.agents.values()
        ]
        await asyncio.gather(*shutdown_tasks)

        # -----------------------------------------------------------------
        # 2️⃣ Clean up the shared workspace.
        # -----------------------------------------------------------------
        session._cleanup_workspace()

        session.status = "shutdown"

# Export symbols for ``from ... import *`` convenience.
__all__ = ["CollaborationSession", "CollaborationSessionManager"]
