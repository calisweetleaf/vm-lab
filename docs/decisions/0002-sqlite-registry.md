# ADR 0002: Transactional SQLite Registry

**Status:** accepted  
**Date:** 2026-08-05  
**Decision owner:** Daeron

## Decision

The daemon owns a standard-library SQLite registry containing normalized VM,
lifecycle-event, lease, disk, snapshot, process-identity, migration, and
idempotency records. Foreign keys, deletion restrictions, and unique
constraints enforce exact ownership.

Every external mutation is bracketed by a durable operation journal: intent and
owned resources are recorded before mutation, and completion is recorded only
after the nearest external authority confirms success.

## Journal mode and corruption

Journal mode is measured on the actual state filesystem. WAL is not silently
selected or silently replaced by another mode. Migration begins with an atomic
registry backup. Corruption or an unsupported schema enters read-only recovery;
the daemon does not overwrite the damaged registry.

## Rejected

- JSON files as concurrent lifecycle state;
- one connection per CLI acting as an independent owner;
- optimistic success followed by best-effort registry repair;
- silent creation of a fresh registry over corrupt state.
