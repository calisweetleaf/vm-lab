"""Compatibility exports for the canonical guest-agent protocol family.

Source: promoted ``somnus_vm.contracts.agent`` v0.1 boundary
Integrated: 2026-08-04
Modified: 2026-08-05
Modified by: daeron and Codex
Purpose: Preserves existing import paths while ``somnus_protocol.agent`` owns
    the strict, dependency-free endpoint and result schemas.
IO: None.
Provenance: PROVENANCE.md, TASK-P1-001 agent schema-family composition.
"""

from __future__ import annotations

from somnus_protocol.agent import (
    AgentContractError,
    AgentEndpoint,
    AgentHealth,
    CommandResult,
    FileWriteResult,
)

__all__ = [
    "AgentContractError",
    "AgentEndpoint",
    "AgentHealth",
    "CommandResult",
    "FileWriteResult",
]
