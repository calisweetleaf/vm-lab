"""Somnus VM lab control plane.

Source: vm_lab.zip
Integrated: 2026-08-04
Purpose: Exposes the contract-first host runtime without importing lineage or
    optional guest components at package import time.
"""

from __future__ import annotations

from .contracts.vm import VMDefinition, VMPorts, VMRecord, VMState

__all__ = ["VMDefinition", "VMPorts", "VMRecord", "VMState"]
__version__ = "0.1.0"
