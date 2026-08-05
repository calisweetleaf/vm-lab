# VM Lab Durable Memory

This file preserves repository decisions, corrections, rejected paths, and
reusable failure lessons that should change future execution. Current state is
indexed in [`CONTEXT.md`](CONTEXT.md); chronological work belongs in
[`PROVENANCE.md`](PROVENANCE.md).

---

## Durable decisions

### 2026-08-04 — Reconstitute rather than overwrite

The uploaded `vm_lab.zip` remains an independently recoverable source boundary.
The live `src/somnus_vm` package is a composed replacement boundary, while every
one of the 33 source files remains represented exactly once in
[`source-manifest.json`](source-manifest.json).

**Future consequence:** never “clean up” archive, candidate, or quarantine
surfaces merely because a live replacement exists. Preservation and promotion
are different axes.

### 2026-08-04 — Withhold lifecycle until physical truth exists

The source lineage contained daemonized PID confusion, fixed port collisions,
fabricated guest addresses, unsafe snapshot rollback, no-op scaling, fake
orchestration, and synthetic benchmark evidence.

**Future consequence:** v0.1 exposes only doctor, topology, plan, and guest
bootstrap. Lifecycle returns phase-by-phase through physical gates, never by
restoring old commands.

### 2026-08-04 — Keep four authority planes separate

Host lifecycle, guest control/cognition, Kerminal agency, and disposable
Artifact processing are separate authorities. VM-Go also remains a separate
host-lifecycle project.

**Future consequence:** integration occurs through explicit behavioral
contracts. Direct cross-imports or repository consolidation are not shortcuts.

### 2026-08-04 — Planning remains non-mutating

Port allocation in v0.1 is a bind-check that releases sockets. QEMU construction
returns argv only. `ports_reserved` is false.

**Future consequence:** durable leases and launch state belong to one future
daemon transaction. Do not make CLI planner invocations compete as lifecycle
owners.

### 2026-08-04 — Guest bootstrap uses one-read verified bytes

Bootstrap copies and hashes each exact-sized local payload once into a private
verified location, then installs from that copy. It rejects network acquisition,
arbitrary post-install commands, and service mutation.

**Future consequence:** in-place update work must extend this trust chain rather
than bypass it for convenience.

### 2026-08-04 — Living repository surfaces are bounded, not duplicated

`AGENTS.md` is the entry kernel, `filetree.md` is the navigation/dependency map,
`ARCHITECTURE_MAP.md` is source traversal, `CONTEXT.md` is current state,
`MEMORY.md` is durable reasoning, `PROVENANCE.md` is chronology, `PLAN.md` is
the destination, and `TASK.md` is the single current unit.

**Future consequence:** update the owning surface only. Do not copy the whole
architecture into every Markdown file.

---

## Rejected paths

| Rejected path | Why it was rejected | Re-entry condition |
| --- | --- | --- |
| Restore old supervisor as runtime | wrong PID/network/snapshot/scaling semantics | salvage named concepts behind new P2–P4 owners and gates |
| Wrap old `vm_orchestrator.py` | mock sessions, log-only work, duplicate supervisor | no direct re-entry; compose behavior from verified owners |
| Fixed ports because one VM is normal | one-active policy is not resource-isolation correctness | durable transactional allocator |
| Fabricated `192.168.122.x` guest address | incompatible with QEMU user networking | explicit separate bridge/tap backend |
| Snapshot by qcow2 file copy | can corrupt backing chain | QMP block-graph switching and stopped rollback proof |
| No-op scaling API | returns intent as success | before/after QMP resource observation |
| Digital twin as readiness agent | missing endpoints and recursive lock behavior | small authenticated agent first; cognition optional |
| Generic post-install shell commands | destroys bootstrap policy and auditability | declarative known templates with rollback/readiness gate |
| Sleep benchmark | measures scheduler delay, not VM work | timed real QEMU/QMP/guest operation |
| Import file processors during boot | no current caller; eager dependency cost | external disposable service plus ingress contract |
| Treat architecture reports as proof | prior report validated absent files | current tree plus executable evidence only |

---

## Reusable failure lessons

1. A PID identifies a process only after start-time and executable correlation;
   it never proves VM identity without QMP.
2. A socket pathname proves filesystem state, not a live protocol.
3. A TCP connection proves reachability, not authenticated guest readiness.
4. A safe plan proves representable intent, not ownership or execution.
5. A successful schema round-trip proves structure, not physical semantics.
6. A passing test proves only its actual observation boundary.
7. A missing physical prerequisite should remain a visible blocked gate.
8. Candidate sophistication is a cognitive attractor; disposition outranks
   apparent completeness.
9. Snapshot metadata is immutable evidence, not a convenient state file.
10. Navigation that points to stale source is an operational failure, not a
    documentation cosmetic.

---

## Hardware constraints

Primary operating targets are CPU-only Linux hosts:

- Ryzen 5 2400G / 12 GB RAM / no GPU;
- Ryzen 5 5625U / 16 GB RAM / no GPU.

VM profiles, test concurrency, base-image choices, and optional cognition must
fit these machines by default. GPU/cloud paths may exist only as optional
enhancements.

---

## Open architectural variables

These remain unresolved and must not be silently collapsed:

- exact daemon IPC framing and service lifecycle;
- SQLite journal mode after filesystem measurement;
- first canonical disposable cloud image and checksum;
- per-VM secret provisioning mechanism;
- exact Kerminal and VM-Go compatibility contracts;
- guest agent transport/authentication construction;
- stopped-VM snapshot generation strategy before live snapshot work;
- Artifact ingress manifest schema;
- whether this Python backend earns reference, alternate, or parity status
  relative to VM-Go.

The phase that resolves each variable must record the decision and its proof in
[`PROVENANCE.md`](PROVENANCE.md).
