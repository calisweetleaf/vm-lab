"""Shared host and guest contracts for the Somnus VM lab."""

from __future__ import annotations

from .agent import AgentEndpoint, AgentHealth, CommandResult, FileWriteResult
from .vm import VMDefinition, VMPorts, VMRecord, VMState

__all__ = [
    "AgentEndpoint",
    "AgentHealth",
    "CommandResult",
    "FileWriteResult",
    "VMDefinition",
    "VMPorts",
    "VMRecord",
    "VMState",
]
