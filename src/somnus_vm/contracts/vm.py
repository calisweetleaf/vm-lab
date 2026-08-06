"""Compatibility re-exports for the canonical Somnus VM protocol.

The live v0.1 host package historically exposed VM contracts from this module.
P1 moves their schema authority to :mod:`somnus_protocol.vm` so host, guest,
and future Kerminal consumers cannot drift into separately decoded record
formats.  This module intentionally defines **no** schema, lifecycle grammar,
or migration logic; every exported object below is the canonical object.

IO: None. Importing this compatibility surface performs no host, guest,
filesystem, process, socket, or secret work.
"""

from __future__ import annotations

from somnus_protocol.vm import (
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
    utc_now,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
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
    "utc_now",
]
