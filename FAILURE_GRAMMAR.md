# VM Lab Failure Grammar

This is not an exception catalogue. It names what wrong looks like before a
definitive failure is available, especially the false-success patterns that
previous VM Lab lineage already demonstrated.

For architecture and owners, see [`TOPOLOGY.md`](TOPOLOGY.md). For the complete
gates, see [`PLAN.md`](PLAN.md).

---

## Pre-failure signatures

| Smell | Likely mechanism | First discriminating check |
| --- | --- | --- |
| “Running” is derived from one PID | daemonized wrapper PID, PID reuse, or foreign process | correlate process start time, executable, command hash, QMP UUID/status |
| QMP socket exists before success | stale/foreign socket or unnegotiated QMP | read greeting, negotiate capabilities, query UUID and status |
| TCP accepts before guest readiness | wrong service or unauthenticated endpoint | authenticate; compare VM ID, boot ID, protocol version |
| Two independent plans choose one port | bind-check was mistaken for a lease | race independent processes against durable allocator |
| User networking returns `192.168.122.x` | libvirt address model leaked into QEMU user networking | inspect actual loopback `hostfwd` endpoint |
| Snapshot completes without active-chain inspection | child image created but never switched | `query-block` plus `qemu-img --backing-chain` |
| Rollback uses file copy | child may overwrite its backing file | stop immediately; inspect complete chain before mutation |
| Scaling returns quickly with no QMP observation | intent-only method | compare QMP CPU/memory observations before and after |
| VM benchmark is extremely fast and stable | synthetic sleep or log-only work | identify actual QEMU/QMP/guest operation in timed interval |
| Host import loads torch/browser/memory modules | candidate code crossed boundary | inspect import graph from `somnus_vm` entrypoint |
| Doctor becomes green without QEMU/image/KVM | failures were downgraded or skipped | compare check inputs with host filesystem and binaries |
| Bootstrap reopens original payload after hash | TOCTOU restored | trace bytes from source read to installed destination |
| Clean JSON with no warnings after complex mutation | broad exception or intended state normalized to success | inspect raw stderr/events and unexpected exception handling |
| Architecture link lands on another symbol | map anchor drift | run `python scripts/verify_repository.py`, then inspect symbol name |

These smells require investigation. They are not themselves proof.

---

## False-success patterns

### FS-VM-01: Launcher PID masquerades as QEMU identity

**Historical mechanism:** lineage used `-daemonize` while retaining the launcher
PID. The recorded process exited while QEMU continued elsewhere.

**Damage:** pause, stop, and recovery can target nothing or a reused foreign PID.

**Recovery:** keep QEMU non-daemonized; record Popen PID, pidfile PID, process
start time, executable, command hash, VM UUID, and QMP UUID; require agreement.

### FS-VM-02: Availability probe masquerades as reservation

**Mechanism:** a planner binds a candidate port, releases it, and reports the
number. Another process can claim it immediately.

**Damage:** two plans look valid and collide at launch.

**Recovery:** retain `ports_reserved: false`; allocate durable leases inside the
single registry transaction owned by the daemon; recheck OS bind immediately
before launch.

### FS-VM-03: Socket or HTTP reachability masquerades as readiness

**Mechanism:** path existence, connect success, or generic health JSON is treated
as proof of the intended VM and boot.

**Damage:** stale endpoints or wrong processes satisfy readiness.

**Recovery:** authenticate and bind readiness to VM ID, boot ID, protocol
version, service version, and live QMP identity.

### FS-VM-04: Snapshot creation masquerades as active snapshot

**Historical mechanism:** lineage created a qcow2 child but did not switch the
running block graph, then copied the child over its backing disk during rollback.

**Damage:** the snapshot captures no subsequent writes and rollback risks chain
corruption.

**Recovery:** keep API absent until guest quiesce, QMP block switch, full backing
chain, measured file mutation, stopped rollback, and post-rollback hash proof all
pass.

### FS-VM-05: Method return masquerades as machine mutation

**Historical mechanism:** resource scaling returned true without applying CPU or
memory changes; orchestration logged installs it did not execute.

**Damage:** state and UI claim capability that the machine never acquired.

**Recovery:** every public success derives from before/after observation at the
physical owner. If no observer exists, withhold the command.

### FS-VM-06: Preserved code masquerades as integrated code

**Mechanism:** a sophisticated module under `components/`, `extras/`, or
`archive/` is imported because it appears more complete than the live boundary.

**Damage:** duplicate lifecycle authority, eager dependencies, unsafe semantics,
or incompatible contracts return to boot.

**Recovery:** keep disposition explicit; promote only the named concept through a
new owner, tests, and phase gate. Never change a directory label as substitute
for integration.

### FS-VM-07: Structural proof masquerades as physical proof

**Mechanism:** schemas round-trip, a plan is safe, or a smoke harness passes, so
the report claims VM lifecycle works.

**Damage:** documentation outruns executable reality.

**Recovery:** label proof by boundary: contract, plan, package, QMP, guest,
storage, lifecycle, or certification. Do not generalize upward.

### FS-VM-08: “Helpful” environmental fallback

**Mechanism:** missing QEMU, image, KVM, or guest fixture is converted to a mock,
skip, warning, or synthetic number.

**Damage:** the exact physical gate disappears.

**Recovery:** fail honestly with remediation. Continue independent non-physical
checks, but leave the capability unpromoted.

---

## Security and destructive-action signatures

### Storage

- a writable disk has no exact owner record;
- two paths alias through symlinks or hard links;
- a base image lives beneath an instance-owned directory;
- deletion discovers targets by glob rather than recorded ownership;
- external imported disks are treated as daemon-owned by default;
- fsync/atomic publish boundaries are absent.

### Guest control

- tokens appear in argv, TOML, logs, registry exports, or world-readable
  cloud-init;
- execution accepts a shell string rather than argv;
- timeout kills only the parent, not the process group;
- cwd and path validation occur before symlink resolution only;
- file-write success is returned before readback hash verification;
- liveness depends on cognitive runtime health.

### Bootstrap

- URL acquisition is enabled without scheme, redirect, size, hash, deadline,
  and destination policy;
- archive extraction happens before all members are validated;
- duplicate destinations or file/directory collisions are tolerated;
- links, devices, or absolute/parent paths are extracted;
- arbitrary post-install commands or unit bodies enter configuration;
- an interrupted placement makes all future runs permanently refuse recovery.

---

## Ecosystem failure signatures

| Failure | Detection | Repair |
| --- | --- | --- |
| `AGENTS.md` references missing owners | repository verifier / direct link check | rebuild affected route from current topology |
| `ARCHITECTURE_MAP.md` anchor beyond EOF | anchor verifier | repair anchor; preserve branch if topology still holds |
| fingerprint says warm but source topology changed | hash mismatch or provenance delta | targeted archaeology, update topology/map, rehash |
| `TASK.md` contains multiple active units | task-state review | lock one unit; demote others to queue |
| `PLAN.md` checkbox lacks proof | missing evidence path | revert checkbox or attach fresh sealed evidence |
| CI enforces aspirational capability | workflow fails on absent future feature | enforce only current structural/runtime contracts |
| docs claim a missing CLI command | compare parser surface | remove claim or finish and gate implementation |
| source manifest changed during ordinary work | hash mismatch | restore sealed bytes; create a new snapshot process if intentional |

---

## Failure classification

When a smell becomes a failure, classify it before fixing:

1. **Contract failure:** pure schema, state, or serialization invariant.
2. **Planning failure:** unsafe or ambiguous intent before mutation.
3. **Ownership failure:** multiple writers or unowned resource.
4. **Process/QMP failure:** identity, framing, event, timeout, or adoption.
5. **Storage failure:** image provenance, backing chain, atomicity, ownership.
6. **Guest protocol failure:** authentication, readiness, execution, file, bounds.
7. **Bootstrap failure:** payload trust, extraction, activation, recovery.
8. **Integration failure:** correct module wired through the wrong boundary.
9. **Evidence failure:** report or test proves something weaker than claimed.
10. **Environment block:** required metal or fixture is genuinely absent.

Fix the owning layer. Do not catch an ownership failure in CLI formatting or
hide an environment block in test scaffolding.

---

## Recovery protocol

1. Preserve raw observable evidence without mutating the suspect resource.
2. Restate the claimed success and its actual evidence source.
3. Run the smallest check that separates the leading mechanisms.
4. Classify the failure using the list above.
5. Repair the direct owner, not the symptom surface.
6. Re-run the failed boundary and its immediate regression boundary.
7. If a public claim was false, retract it from CLI/docs/state before continuing.
8. Record a durable lesson in [`MEMORY.md`](MEMORY.md) only when it will change
   future execution; record chronological facts in
   [`PROVENANCE.md`](PROVENANCE.md).

For a physically blocked gate, stop at the gate. Improve deterministic contracts,
fixtures, diagnostics, and cleanup if useful, but never check the capability as
complete.

---

## Quick reference

| Signal | Severity | First owner |
| --- | --- | --- |
| PID-only running state | Critical | future process/QMP backend |
| snapshot file copying | Critical | future storage/snapshot backend |
| candidate import on host boot | Critical | topology/import boundary |
| fixed or duplicate port | High | allocator/registry |
| unauthenticated readiness | Critical | guest protocol |
| doctor passes without required metal | High | `doctor.py` |
| bootstrap source reopened | Critical | `guest/bootstrap.py` |
| plan described as reservation | High | CLI/docs/planner |
| synthetic performance evidence | High | test harness |
| stale navigation anchor | Medium | `ARCHITECTURE_MAP.md` |

