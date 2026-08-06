"""Somnus VM lab control plane.

Source: vm_lab.zip
Integrated: 2026-08-04
Purpose: Exposes the contract-first host runtime without importing lineage or
    optional guest components at package import time.
"""

from __future__ import annotations

from .contracts.vm import (
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
    "ImageProvenance",
    "ObservationSource",
    "ProcessIdentity",
    "RuntimeObservation",
    "SnapshotReference",
    "StorageReference",
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
__version__ = "0.1.0"
