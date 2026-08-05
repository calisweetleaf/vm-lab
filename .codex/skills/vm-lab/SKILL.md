---
name: vm-lab
description: >
  Repository-local cognitive topology router for the Somnus VM Lab. Use on
  entry, implementation, diagnosis, promotion, snapshot, or continuity work.
---

# VM Lab Repository Skill

This skill is deliberately thin. Root Markdown artifacts are canonical so the
repository has one living topology rather than duplicated skill copies.

## Semantic router

| Task | Load |
| --- | --- |
| Cold or ordinary entry | [`AGENTS.md`](../../../AGENTS.md) → [`TASK.md`](../../../TASK.md) |
| Locate code or trace a flow | [`ARCHITECTURE_MAP.md`](../../../ARCHITECTURE_MAP.md) |
| Understand invariant structure | [`TOPOLOGY.md`](../../../TOPOLOGY.md) |
| Diagnose suspicious behavior | [`FAILURE_GRAMMAR.md`](../../../FAILURE_GRAMMAR.md) first |
| Resume current work | [`TASK.md`](../../../TASK.md) → relevant [`PLAN.md`](../../../PLAN.md) phase |
| Promote candidate code | [`docs/MIGRATION_MAP.md`](../../../docs/MIGRATION_MAP.md) → [`MEMORY.md`](../../../MEMORY.md) → gate |
| Reconstruct current state | [`CONTEXT.md`](../../../CONTEXT.md) |
| Recover prior decisions | [`MEMORY.md`](../../../MEMORY.md) → [`PROVENANCE.md`](../../../PROVENANCE.md) |
| Change topology or entrypoints | topology → architecture map → provenance → fingerprint |
| Prepare a snapshot | [`PLAN.md`](../../../PLAN.md) sign-off → [`SNAPSHOT.md`](../../../SNAPSHOT.md) |

## Non-negotiable kernel

1. Only `src/somnus_vm/` is live at v0.1.
2. Host planning is non-mutating.
3. External observed truth owns every VM success claim.
4. Host, guest, Kerminal/operator, Artifact worker, and VM-Go boundaries remain
   separate.
5. Candidate, cold, lineage, and quarantine locations are evidence—not
   capability.
6. Exactly one active unit lives in [`TASK.md`](../../../TASK.md).

## Verification

```bash
python scripts/verify_repository.py
PYTHONPATH=src python test/vm_lab/smoke.py
```

Use the first for the living repository contract and the second for promoted
v0.1 runtime behavior. A future physical gate requires its own direct evidence.
