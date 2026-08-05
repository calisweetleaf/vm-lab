# ADR 0004: Stopped-VM Rollback Before Live Rollback

**Status:** accepted  
**Date:** 2026-08-05  
**Decision owner:** Daeron

## Decision

The first VM rollback capability is stopped-only. It gracefully stops the VM,
validates the complete qcow2 backing chain, transactionally selects an owned
prior disk generation, boots, and verifies guest evidence. A snapshot is not
successful until QMP and `qemu-img` agree about the active block graph.

Copying a child image over its backing image is permanently rejected. Live
rollback remains absent until a separate block-graph gate proves it.

Guest release activation also retains a previous known-good release and returns
to it when the new service fails readiness. That product recovery capability is
distinct from codebase evolution: implementation work fixes forward and never
restores unsafe lineage or replaces the live architecture wholesale.

## Rejected

- qcow2 file-copy rollback;
- a child image that was never activated;
- live rollback inferred from stopped-VM evidence;
- removing recovery because implementation changes must not be reverted.
