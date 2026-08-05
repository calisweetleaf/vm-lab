"""
MORPHEUS CHAT - Development Session Schemas
Pydantic models for the "Session-as-Repo" feature.

This module defines the data structures for tracking lightweight, conversational
development sessions, including events, states, and the overall session object.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4
from collections import defaultdict

from pydantic import BaseModel, Field, validator

# Forward-referencing for type hints within models
UserID = str
SessionID = UUID

class DevSessionStatus(str, Enum):
    """Lifecycle status of a development session."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    PROMOTED = "promoted" # Indicates the session was promoted to a full project

class DevSessionEventType(str, Enum):
    """Types of events that can occur within a development session."""
    SESSION_START = "session_start"
    SESSION_UPDATED = "session_updated" 
    USER_PROMPT = "user_prompt"
    AI_RESPONSE = "ai_response"
    CODE_BLOCK_CREATED = "code_block_created"
    CODE_BLOCK_MODIFIED = "code_block_modified"
    USER_FEEDBACK = "user_feedback"
    SESSION_PAUSED = "session_paused"
    SESSION_RESUMED = "session_resumed"
    SESSION_COMPLETED = "session_completed"
    SESSION_ARCHIVED = "session_archived"
    SESSION_PROMOTED = "session_promoted"
    TITLE_CHANGED = "title_changed"
    SNAPSHOT_CREATED = "snapshot_created"

class CodeBlock(BaseModel):
    """Represents a single, trackable block of code within a session."""
    block_id: UUID = Field(default_factory=uuid4, description="Unique identifier for this code block.")
    language: str = Field(..., description="Programming language of the code block (e.g., 'python', 'javascript').")
    content: str = Field(..., description="The full content of the code block.")
    version: int = Field(default=1, description="Version number, incremented on each modification.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_modified: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class DevSessionEvent(BaseModel):
    """
    Represents a single, timestamped event in the development session's history.
    This is the equivalent of a single 'commit' in a Git repository.
    """
    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: DevSessionEventType = Field(..., description="The type of event that occurred.")
    
    # Content can vary based on event type
    # For CODE_BLOCK_MODIFIED, this would store a diff patch.
    # For others, it could be a user message or a JSON payload.
    content: str = Field(description="The primary data associated with the event (e.g., code diff, user message).")
    
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional structured data about the event.")
    
    # Link to a specific code block if applicable
    target_block_id: Optional[UUID] = Field(None, description="The ID of the CodeBlock this event pertains to.")

    @validator('content')
    def validate_content_length(cls, v):
        """Ensure content is not excessively long to maintain performance."""
        # A generous limit, as diffs are usually small.
        if len(v) > 50_000: 
            raise ValueError("Event content exceeds the 50KB size limit.")
        return v

class DevSession(BaseModel):
    """
    The main schema for a "Session-as-Repo".
    This object encapsulates the entire lifecycle and state of a conversational
    development project, acting as a lightweight, in-memory repository.
    """
    dev_session_id: UUID = Field(default_factory=uuid4, description="Unique identifier for this development session.")
    chat_session_id: SessionID = Field(..., description="The parent chat session where this dev session originated.")
    user_id: UserID = Field(..., description="The user who initiated the session.")
    
    title: str = Field(default="Untitled Dev Session", max_length=150, description="A user-defined title for the session.")
    status: DevSessionStatus = Field(default=DevSessionStatus.ACTIVE, description="The current lifecycle status of the session.")
    
    # Timestamps
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = Field(None, description="Timestamp when the session was marked as completed.")
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Core Data
    event_log: List[DevSessionEvent] = Field(default_factory=list, description="A chronological log of all events, akin to a commit history.")
    code_blocks: Dict[UUID, CodeBlock] = Field(default_factory=dict, description="The current state of all code blocks, addressable by their unique ID.")
    
    # Promotion metadata
    promoted_to_project_id: Optional[UUID] = Field(None, description="If promoted, this holds the ID of the full Project.")

    def add_event(self, event_type: DevSessionEventType, content: str, metadata: Optional[Dict[str, Any]] = None, target_block_id: Optional[UUID] = None):
        """
        Adds a new event to the session's log and updates activity timestamp.
        
        Args:
            event_type: The type of the event.
            content: The data for the event (e.g., diff, message).
            metadata: Optional dictionary for extra data.
            target_block_id: The ID of the code block this event targets.
        """
        event = DevSessionEvent(
            event_type=event_type,
            content=content,
            metadata=metadata or {},
            target_block_id=target_block_id
        )
        self.event_log.append(event)
        self.last_activity = event.timestamp

    def get_code_block(self, block_id: UUID) -> Optional[CodeBlock]:
        """
        Retrieves the current state of a specific code block by its ID.
        """
        return self.code_blocks.get(block_id)

    def update_code_block(self, block_id: UUID, new_content: str) -> str:
        """
        Updates a code block with new content, generates a diff for the event log,
        and returns the diff patch.
        
        Args:
            block_id: The UUID of the code block to update.
            new_content: The full new content of the code block.
        
        Returns:
            A string containing the diff patch of the changes.
        """
        import difflib

        if block_id not in self.code_blocks:
            raise KeyError(f"Code block with ID {block_id} not found in this session.")
        
        block = self.code_blocks[block_id]
        old_content_lines = block.content.splitlines(keepends=True)
        new_content_lines = new_content.splitlines(keepends=True)
        
        # Generate the diff
        diff = difflib.unified_diff(
            old_content_lines,
            new_content_lines,
            fromfile=f"a/{block.language}",
            tofile=f"b/{block.language}",
            fromfiledate=block.last_modified.isoformat(),
            tofiledate=datetime.now(timezone.utc).isoformat()
        )
        diff_patch = "".join(diff)

        # Update the block's state
        block.content = new_content
        block.version += 1
        block.last_modified = datetime.now(timezone.utc)
        
        return diff_patch

    def add_new_code_block(self, content: str, language: str) -> CodeBlock:
        """
        Adds a completely new code block to the session.
        
        Args:
            content: The initial content of the new code block.
            language: The programming language of the block.
        
        Returns:
            The newly created CodeBlock object.
        """
        new_block = CodeBlock(language=language, content=content)
        self.code_blocks[new_block.block_id] = new_block
        return new_block
        
    def get_summary(self) -> Dict[str, Any]:
        """
        Generates a summary of the development session.
        """
        duration_seconds = (self.last_activity - self.start_time).total_seconds()
        
        code_stats = defaultdict(lambda: {'count': 0, 'lines': 0})
        for block in self.code_blocks.values():
            stats = code_stats[block.language]
            stats['count'] += 1
            stats['lines'] += len(block.content.splitlines())
        
        return {
            "dev_session_id": str(self.dev_session_id),
            "title": self.title,
            "status": self.status.value,
            "duration_minutes": round(duration_seconds / 60, 2),
            "total_events": len(self.event_log),
            "code_block_count": len(self.code_blocks),
            "code_summary": dict(code_stats)
        }


