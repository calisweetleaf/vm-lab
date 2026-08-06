"""Compatibility exports for the canonical shared Somnus contracts.

The schema authority is :mod:`somnus_protocol`; this package path remains for
v0.1 callers while P1 moves host and guest consumers onto the same objects.
"""

from __future__ import annotations

from .agent import AgentEndpoint, AgentHealth, CommandResult, FileWriteResult
from .vm import (
    ALLOWED_TRANSITIONS,
    IDEMPOTENT_STATES,
    TRANSITION_REQUIREMENTS,
    ImageProvenance,
    ObservationSource,
    ProcessIdentity,
    RuntimeObservation,
    SnapshotReference,
    StorageReference,
    TransitionCause,
    TransitionEvidence,
    TransitionRecord,
    VMContractError,
    VMDefinition,
    VMMigrationError,
    VMPorts,
    VMRecord,
    VMResourceSpec,
    VMState,
    VMTransitionError,
    migrate_vm_record,
)

__all__ = [
    "AgentEndpoint",
    "AgentHealth",
    "ALLOWED_TRANSITIONS",
    "CommandResult",
    "FileWriteResult",
    "IDEMPOTENT_STATES",
    "ImageProvenance",
    "ObservationSource",
    "ProcessIdentity",
    "RuntimeObservation",
    "SnapshotReference",
    "StorageReference",
    "TRANSITION_REQUIREMENTS",
    "TransitionCause",
    "TransitionEvidence",
    "TransitionRecord",
    "VMContractError",
    "VMDefinition",
    "VMMigrationError",
    "VMPorts",
    "VMRecord",
    "VMResourceSpec",
    "VMState",
    "VMTransitionError",
    "migrate_vm_record",
]
