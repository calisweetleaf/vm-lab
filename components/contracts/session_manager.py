"""
MORPHEUS CHAT - Development Session Manager
Service for managing the lifecycle of lightweight, conversational dev sessions.

This module provides the core logic for creating, retrieving, updating, and
persisting "Session-as-Repo" objects using the central MemoryManager.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Union
from uuid import UUID

from core.memory_core import MemoryManager, MemoryType, MemoryImportance, MemoryScope
from schemas.dev_session_schemas import DevSession, DevSessionEvent, DevSessionEventType, DevSessionStatus

# Type aliases for clarity
DevSessionID = UUID
ChatSessionID = UUID
SessionID = UUID  # Added to support modules that import SessionID from this module
UserID = str

logger = logging.getLogger(__name__)

# Production IO controls
_IO_TIMEOUT_SECONDS: float = 5.0
_SAVE_RETRIES: int = 3
_LOAD_RETRIES: int = 3
_INITIAL_BACKOFF_SECONDS: float = 0.25
_MAX_BACKOFF_SECONDS: float = 2.0

def _normalize_title(title: str) -> str:
    t = (title or "").strip()
    return t if t else "Untitled Dev Session"

def _ensure_uuid(name: str, value: UUID) -> None:
    if not isinstance(value, UUID):
        raise TypeError(f"{name} must be a UUID")

def _ensure_nonempty_str(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")

class DevSessionManager:
    """
    Manages the lifecycle of DevSession objects.

    This class acts as the service layer between the application logic (e.g., WebSocket handlers)
    and the persistence layer (MemoryManager). It handles the creation, retrieval,
    and state transitions of development sessions.
    """

    def __init__(self, memory_manager: MemoryManager):
        """
        Initializes the DevSessionManager.

        Args:
            memory_manager: An instance of the central MemoryManager for persistence.
        """
        if not isinstance(memory_manager, MemoryManager):
            raise TypeError("memory_manager must be an instance of MemoryManager")
            
        self.memory_manager = memory_manager
        # In-memory cache for active development sessions to reduce DB lookups.
        self.active_sessions: Dict[DevSessionID, DevSession] = {}
        self.cache_lock = asyncio.Lock()
        logger.info("Development Session Manager initialized.")

    async def create_session(self, user_id: UserID, chat_session_id: ChatSessionID, title: str = "Untitled Dev Session") -> DevSession:
        """
        Creates a new development session and persists it.

        Args:
            user_id: The ID of the user initiating the session.
            chat_session_id: The ID of the parent chat session.
            title: An optional initial title for the session.

        Returns:
            The newly created DevSession object.
        """
        # Validate inputs
        _ensure_nonempty_str("user_id", user_id)
        _ensure_uuid("chat_session_id", chat_session_id)
        title = _normalize_title(title)

        session = DevSession(
            user_id=user_id,
            chat_session_id=chat_session_id,
            title=title
        )
        
        # Add the creation event to the log
        session.add_event(
            event_type=DevSessionEventType.SESSION_START,
            content=f"Development session '{title}' started by user {user_id}."
        )

        async with self.cache_lock:
            self.active_sessions[session.dev_session_id] = session

        await self._save_session(session)
        logger.info(
            "Dev session created",
            extra={"dev_session_id": str(session.dev_session_id)}
        )
        return session

    async def get_session(self, dev_session_id: DevSessionID) -> Optional[DevSession]:
        """
        Retrieves a development session, from cache if available, otherwise from memory.

        Args:
            dev_session_id: The ID of the development session to retrieve.

        Returns:
            The DevSession object if found, otherwise None.
        """
        _ensure_uuid("dev_session_id", dev_session_id)

        async with self.cache_lock:
            if dev_session_id in self.active_sessions:
                logger.debug(
                    "Dev session cache hit",
                    extra={"dev_session_id": str(dev_session_id)}
                )
                return self.active_sessions[dev_session_id]

        # If not in cache, load from persistence layer
        session = await self._load_session(dev_session_id)
        if session:
            async with self.cache_lock:
                self.active_sessions[dev_session_id] = session
            logger.info(
                "Dev session loaded into cache",
                extra={"dev_session_id": str(dev_session_id)}
            )
        
        return session

    async def log_event(self, dev_session_id: DevSessionID, event: DevSessionEvent) -> bool:
        """
        Logs a new event to a development session and updates its persistence.

        Args:
            dev_session_id: The ID of the session to update.
            event: The DevSessionEvent to add.

        Returns:
            True if the event was logged successfully, False otherwise.
        """
        _ensure_uuid("dev_session_id", dev_session_id)
        if not isinstance(event, DevSessionEvent):
            raise TypeError("event must be an instance of DevSessionEvent")

        session = await self.get_session(dev_session_id)
        if not session:
            logger.warning(
                "Attempted to log event for non-existent dev session",
                extra={"dev_session_id": str(dev_session_id)}
            )
            return False

        # Avoid duplicating session logic; append validated event and persist
        session.event_log.append(event)
        session.last_activity = event.timestamp

        await self._save_session(session)
        logger.debug(
            "Event logged to dev session",
            extra={
                "dev_session_id": str(dev_session_id),
                "event_type": getattr(event.event_type, "name", str(event.event_type))
            }
        )
        return True

    async def update_code_block(self, dev_session_id: DevSessionID, block_id: UUID, new_content: str) -> bool:
        """
        Updates a code block, creates a diff event, and persists the session.

        Args:
            dev_session_id: The ID of the session containing the block.
            block_id: The ID of the code block to update.
            new_content: The new, full content of the code block.

        Returns:
            True if successful, False otherwise.
        """
        _ensure_uuid("dev_session_id", dev_session_id)
        _ensure_uuid("block_id", block_id)
        if not isinstance(new_content, str):
            raise TypeError("new_content must be a string")

        session = await self.get_session(dev_session_id)
        if not session:
            logger.warning(
                "Cannot update code block: dev session not found",
                extra={"dev_session_id": str(dev_session_id)}
            )
            return False

        try:
            diff_patch = session.update_code_block(block_id, new_content)
            
            # Log the modification event with the diff
            await self.log_event(
                dev_session_id,
                DevSessionEvent(
                    event_type=DevSessionEventType.CODE_BLOCK_MODIFIED,
                    content=diff_patch,
                    target_block_id=block_id,
                    metadata={'new_version': session.get_code_block(block_id).version}
                )
            )
            return True
        except KeyError as e:
            logger.error(
                "Error updating code block - block not found",
                extra={
                    "dev_session_id": str(dev_session_id),
                    "block_id": str(block_id),
                    "error": str(e)
                }
            )
            return False

    async def set_session_status(self, dev_session_id: DevSessionID, status: DevSessionStatus) -> Optional[DevSession]:
        """
        Updates the status of a development session (e.g., to 'paused' or 'completed').

        Args:
            dev_session_id: The ID of the session to update.
            status: The new DevSessionStatus.

        Returns:
            The updated DevSession object, or None if not found.
        """
        _ensure_uuid("dev_session_id", dev_session_id)
        if not isinstance(status, DevSessionStatus):
            raise TypeError("status must be an instance of DevSessionStatus")

        session = await self.get_session(dev_session_id)
        if not session:
            return None

        session.status = status
        if status == DevSessionStatus.COMPLETED:
            session.end_time = datetime.now(timezone.utc)

        # Map to event type safely
        try:
            event_type = DevSessionEventType[f"SESSION_{status.name}"]
        except KeyError:
            # Fallback to a generic status change event if mapping not present
            event_type = DevSessionEventType.SESSION_UPDATED  # Assuming enum exists; otherwise the following line is still informative
        session.add_event(
            event_type=event_type,
            content=f"Session status changed to {status.value}"
        )
        await self._save_session(session)

        if status in [DevSessionStatus.COMPLETED, DevSessionStatus.ARCHIVED, DevSessionStatus.PROMOTED]:
            async with self.cache_lock:
                self.active_sessions.pop(dev_session_id, None)
        return session

    async def _save_session(self, session: DevSession):
        """
        Persists the entire DevSession object to the MemoryManager.
        This uses an "upsert" pattern based on the memory_id.

        Args:
            session: The DevSession object to save.
        """
        # Robust persistence with retries/backoff and timeout
        if not isinstance(session, DevSession):
            raise TypeError("session must be an instance of DevSession")

        memory_content = session.json()
        memory_type: Union[MemoryType, str] = self._resolve_dev_session_memory_type()
        tags = ["dev_session", session.title][:2]

        backoff = _INITIAL_BACKOFF_SECONDS
        last_exc: Optional[BaseException] = None
        for attempt in range(1, _SAVE_RETRIES + 1):
            try:
                await asyncio.wait_for(
                    self.memory_manager.store_memory(
                        user_id=session.user_id,
                        content=memory_content,
                        memory_type=memory_type,
                        importance=MemoryImportance.HIGH,
                        scope=MemoryScope.PRIVATE,
                        tags=tags,
                        metadata={
                            'dev_session_id': str(session.dev_session_id),
                            'session_override_id': str(session.dev_session_id)
                        }
                    ),
                    timeout=_IO_TIMEOUT_SECONDS,
                )
                logger.debug(
                    "Dev session persisted",
                    extra={"dev_session_id": str(session.dev_session_id), "attempt": attempt}
                )
                return
            except asyncio.TimeoutError as e:
                last_exc = e
                logger.warning(
                    "Timeout while saving dev session; will retry",
                    extra={"dev_session_id": str(session.dev_session_id), "attempt": attempt}
                )
            except Exception as e:
                last_exc = e
                logger.error(
                    "Error while saving dev session; will retry",
                    extra={"dev_session_id": str(session.dev_session_id), "attempt": attempt, "error": str(e)}
                )

            if attempt < _SAVE_RETRIES:
                await asyncio.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

        # Exhausted retries
        assert last_exc is not None
        raise RuntimeError(f"Failed to persist DevSession {session.dev_session_id}") from last_exc

    async def _load_session(self, dev_session_id: DevSessionID) -> Optional[DevSession]:
        """
        Loads a DevSession object from the MemoryManager.

        Args:
            dev_session_id: The ID of the session to load.

        Returns:
            A DevSession object if found, otherwise None.
        """
        _ensure_uuid("dev_session_id", dev_session_id)

        backoff = _INITIAL_BACKOFF_SECONDS
        last_exc: Optional[BaseException] = None
        for attempt in range(1, _LOAD_RETRIES + 1):
            try:
                # Search for dev session by metadata since we don't have get_memory_by_id with custom IDs
                memory_entries = await asyncio.wait_for(
                    self.memory_manager.retrieve_memories(
                        user_id="*",
                        memory_types=[self._resolve_dev_session_memory_type()],
                        limit=100
                    ),
                    timeout=_IO_TIMEOUT_SECONDS,
                )
                
                # Find the specific dev session
                memory_entry = None
                for entry in memory_entries:
                    if (entry.get('metadata', {}).get('dev_session_id') == str(dev_session_id) or
                        entry.get('metadata', {}).get('session_override_id') == str(dev_session_id)):
                        memory_entry = entry
                        break
                if memory_entry and memory_entry.get('content'):
                    try:
                        session_data = json.loads(memory_entry['content'])
                        session = DevSession(**session_data)
                        logger.debug(
                            "Dev session loaded from storage",
                            extra={"dev_session_id": str(dev_session_id), "attempt": attempt}
                        )
                        return session
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.error(
                            "Failed to decode stored dev session",
                            extra={"dev_session_id": str(dev_session_id), "error": str(e)}
                        )
                        return None
                # Not found is not an error; no retry
                logger.warning(
                    "Dev session not found in storage",
                    extra={"dev_session_id": str(dev_session_id)}
                )
                return None
            except asyncio.TimeoutError as e:
                last_exc = e
                logger.warning(
                    "Timeout while loading dev session; will retry",
                    extra={"dev_session_id": str(dev_session_id), "attempt": attempt}
                )
            except Exception as e:
                last_exc = e
                logger.error(
                    "Error while loading dev session; will retry",
                    extra={"dev_session_id": str(dev_session_id), "attempt": attempt, "error": str(e)}
                )

            if attempt < _LOAD_RETRIES:
                await asyncio.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

        # Retries exhausted
        assert last_exc is not None
        raise RuntimeError(f"Failed to load DevSession {dev_session_id}") from last_exc

    def _resolve_dev_session_memory_type(self) -> Union[MemoryType, str]:
        # Prefer a proper enum if available, fallback to string for compatibility
        try:
            return MemoryType.DEV_SESSION
        except Exception:
            return "dev_session"


