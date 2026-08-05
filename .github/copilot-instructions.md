# VM Lab GitHub Agent Context

Canonical instructions: [`../AGENTS.md`](../AGENTS.md). This file is a bounded
projection for GitHub agents, not a second constitution.

## What this repository is

VM Lab is a Python 3.12 experimental reference boundary for a persistent Somnus
AIPC. Snapshot v0.1 promotes strict contracts/configuration, truthful doctor,
machine-readable topology, non-mutating QEMU planning, and a separately invoked
verified local guest bootstrap.

## What cannot be wrong

1. Only `src/somnus_vm/` is live. `components/`, `extras/`, `archive/`, and
   `quarantine/` do not become runtime by import.
2. A plan, PID, socket, open port, zero exit status, or schema-valid object is
   not VM lifecycle truth.
3. Host lifecycle, guest control/cognition, Kerminal agency, Artifact processing,
   and VM-Go remain separate authorities.
4. Plan-time ports are bind-checked and released; `ports_reserved` is false.
5. Missing real QEMU/KVM/image/guest metal is an honest blocked gate.

## Entry

1. Read [`../TASK.md`](../TASK.md).
2. Navigate via [`../filetree.md`](../filetree.md) or
   [`../ARCHITECTURE_MAP.md`](../ARCHITECTURE_MAP.md).
3. Read the relevant failure signatures in
   [`../FAILURE_GRAMMAR.md`](../FAILURE_GRAMMAR.md).
4. Verify with `python scripts/verify_repository.py` and the proportional direct
   runtime test.

Do not add lifecycle commands until their physical gate in
[`../PLAN.md`](../PLAN.md) passes.
