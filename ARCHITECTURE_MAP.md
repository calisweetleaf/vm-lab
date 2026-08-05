# VM Lab Architecture Map

**How to use this document:** Start at the question matching the work. Every
branch begins at a current file and line anchor, traces the next owner, and ends
with lateral routes. This is a traversal map—not a system summary.

**Indexed baseline:** snapshot `v0.1`, commit `a2c7b9d`, 2026-08-04.

---

## ROOT: What is VM Lab?

VM Lab is a Python 3.12 experimental reference boundary for a persistent Somnus
AIPC. At v0.1 it validates contracts/configuration, reports topology and host
preflight, emits non-mutating QEMU plans, and separately installs verified local
guest payloads; it intentionally has no public VM lifecycle.

**Enter through these three layers:**

1. Operator contract: `AGENTS.md:1` → rules, routes, promotion boundary.
2. Public runtime: `src/somnus_vm/cli.py:76` → `main()`.
3. Direct proof: `test/vm_lab/test_vm_lab.py:643` → `main()`.

**→ To follow a public command:** BRANCH A.  
**→ To understand what may run:** BRANCH B.  
**→ To understand the safe guest installer:** BRANCH E.  
**→ To identify current work:** BRANCH G.

---

## BRANCH A: How does a public host command run?

**Entry point:** `src/somnus_vm/cli.py:76` → `main()`

1. `src/somnus_vm/cli.py:30` builds only `doctor`, `topology`, and `plan`.
2. Configuration comes from `src/somnus_vm/config.py:129`, or the installed
   default from `src/somnus_vm/config.py:211`.
3. `topology` renders the disposition registry beginning at
   `src/somnus_vm/topology.py:48`.
4. `doctor` delegates to `src/somnus_vm/doctor.py:123`.
5. `plan` delegates to `src/somnus_vm/host/planner.py:31`.
6. Expected contract/config/plan errors become bounded CLI failures; no command
   starts or stops a VM.

**→ For config and doctor truth:** BRANCH C.  
**→ For plan construction:** BRANCH D.  
**→ For proof of the CLI boundary:** BRANCH F.

---

## BRANCH B: What code is live, candidate, cold, lineage, or quarantined?

**Entry point:** `src/somnus_vm/topology.py:48` → `COMPONENTS`

1. Each `ComponentSpec` names an execution boundary, disposition, path,
   `boot_import`, and reason.
2. `src/somnus_vm/topology.py:71` validates that registered paths exist.
3. `src/somnus_vm/` is the promoted package.
4. `components/` is donor/candidate code; `extras/` is cold/legacy;
   `archive/` and `docs/lineage/` are historical; `quarantine/` is rejected.
5. `docs/MIGRATION_MAP.md:1` maps every original source to its placement.
6. `source-manifest.json:1` binds all 33 source files to preserved hashes.

**→ For why surfaces were cut:** BRANCH H.  
**→ For promotion rules:** `AGENTS.md:205`.  
**→ For current state:** BRANCH G.

---

## BRANCH C: How are configuration and host truth established?

**Entry point:** `src/somnus_vm/config.py:129` → `load_configuration()`

1. TOML must contain strict `vm_lab`, `host`, and `components` tables.
2. `src/somnus_vm/config.py:88` resolves paths; port intervals are validated at
   `src/somnus_vm/config.py:23`.
3. Candidate features have dependency rules; quarantined runtime cannot be
   enabled by configuration.
4. `src/somnus_vm/doctor.py:123` observes Python, topology, writable roots, QMP
   path budget, QEMU binaries, qcow2 image format, KVM, and promotion policy.
5. Missing required host metal produces `FAIL`, not a synthetic fallback.

**→ For the actual QEMU intent:** BRANCH D.  
**→ If doctor looks too green:** `FAILURE_GRAMMAR.md:14`.  
**→ For configured profiles:** `configs/lab.toml:1` and `configs/host.toml:1`.

---

## BRANCH D: How is a non-mutating QEMU plan produced?

**Entry point:** `src/somnus_vm/host/planner.py:31` → `VMPlanner.preview()`

1. A validated `VMDefinition` begins at
   `src/somnus_vm/contracts/vm.py:84`.
2. `src/somnus_vm/host/ports.py:33` bind-checks SSH, agent, and VNC candidates.
3. `src/somnus_vm/host/qemu.py:43` builds shell-free, non-daemonized argv.
4. Disk paths enter a JSON `-blockdev`; networking uses loopback host forwards.
5. QMP path length and VNC display semantics are checked before return.
6. The allocator releases its temporary sockets; reported ports are explicitly
   not reserved.

**DENSE edge:** availability is not ownership. Durable allocation belongs to
the future single mutation owner.

**→ For VM identity/state contracts:** BRANCH I.  
**→ For planner false-success modes:** `FAILURE_GRAMMAR.md:51`.  
**→ For the future daemon path:** `PLAN.md:322`.

---

## BRANCH E: How does verified guest bootstrap work?

**Entry point:** `src/somnus_vm/guest/bootstrap.py:502` → `main()`

1. `src/somnus_vm/guest/bootstrap.py:149` loads strict local-only TOML intent.
2. `src/somnus_vm/guest/bootstrap.py:336` reads, bounds, hashes, fsyncs, and
   materializes each source once into a private verified file.
3. ZIP and TAR validation begins at lines `247` and `283`; traversal, links,
   devices, duplicates, collisions, and undeclared expansion are rejected.
4. `src/somnus_vm/guest/bootstrap.py:468` stages the complete target, writes
   recovery state atomically, and honors configuration-bound idempotency.
5. Network URLs, arbitrary post-install commands, and service mutation remain
   outside v0.1.

**→ For hostile-input smells:** `FAILURE_GRAMMAR.md:132`.  
**→ For direct bootstrap checks:** BRANCH F.  
**→ For future in-place updates:** `PLAN.md:607`.

---

## BRANCH F: What proves v0.1 behavior?

**Entry point:** `test/vm_lab/test_vm_lab.py:643` → `main()`

The direct harness runs independent checks for:

- contracts and legal transitions at `test/vm_lab/test_vm_lab.py:120`;
- strict configuration at line `138`;
- concurrent plan-time port behavior at line `163`;
- QEMU argv safety at line `177`;
- hostile archive extraction at line `196`;
- bootstrap idempotency at line `232`;
- doctor honesty at line `328`;
- CLI execution at line `356`;
- outside-checkout wheel behavior at line `396`;
- source preservation at line `487`;
- plan/document contract at line `502`.

`test/vm_lab/test_vm_lab.py:572` seals JSON, Markdown, and log outputs.

**→ For the latest sealed run:** `SOTA_RUN.md:1`.  
**→ For what the proof does not claim:** BRANCH H.  
**→ For integrity of the living packet:** `scripts/verify_repository.py:1`.

---

## BRANCH G: Where is current state and what should happen next?

**Entry point:** `TASK.md:1` → one current unit and bounded queue

1. `PLAN.md:1` owns the complete destination and phase gates.
2. `TASK.md:1` owns the current execution unit only.
3. `CONTEXT.md:1` indexes current architecture and verification state.
4. `MEMORY.md:1` retains durable decisions and rejected paths.
5. `PROVENANCE.md:1` records lineage and chronological sessions.
6. `.sovereign/session_state.json:1` is a last-close hint; its topology hash must
   match `.sovereign/topology_fingerprint.txt:1`.

**→ To resume:** read the active task, then only its linked plan section.  
**→ If continuity conflicts with code:** current code wins; update the stale owner.  
**→ If the fingerprint fails:** repair changed topology/map lanes and rehash.

---

## BRANCH H: Why were advanced surfaces withheld?

**Entry point:** `docs/RECONSTITUTION_AUDIT.md:15` → evidence that forced the cut

The audit records daemonized PID confusion, fixed ports, fabricated guest
addresses, unsafe snapshot rollback, no-op scaling, fake orchestration, missing
guest agent, lock/protocol mismatch, sleep benchmarks, stale reports, and
unconnected file processors.

`docs/MIGRATION_MAP.md:1` then gives each source one explicit disposition.
`SNAPSHOT.md:1` states what v0.1 promotes and withholds.

**→ For failure mechanisms and recovery:** `FAILURE_GRAMMAR.md:1`.  
**→ For future gates that can reverse a withholding:** `PLAN.md:1`.  
**→ For current machine-readable disposition:** BRANCH B.

---

## BRANCH I: How do VM identity and state contracts work?

**Entry point:** `src/somnus_vm/contracts/vm.py:121` → `VMRecord`

1. `src/somnus_vm/contracts/vm.py:29` defines the intentionally minimal v0.1
   state enum.
2. `src/somnus_vm/contracts/vm.py:61` validates distinct unprivileged ports.
3. `src/somnus_vm/contracts/vm.py:84` validates VM name, resources, disk path,
   and KVM intent.
4. `src/somnus_vm/contracts/vm.py:147` permits only declared transitions and
   timestamps each accepted change.
5. Serialization at lines `168` and `187` keeps the contract machine-readable.

The richer lifecycle state machine is a P1 deliverable, not silently present.

**→ For current tests:** BRANCH F.  
**→ For the target lifecycle states:** `PLAN.md:281`.  
**→ For identity false-success patterns:** `FAILURE_GRAMMAR.md:38`.

---

## BRANCH J: Where is X?

| Question | Go to |
| --- | --- |
| Repository rules and routes | `AGENTS.md:1` |
| Active unit | `TASK.md:1` |
| Long-range plan | `PLAN.md:1` |
| CLI parser/dispatch | `src/somnus_vm/cli.py:30`, `src/somnus_vm/cli.py:76` |
| Configuration loader | `src/somnus_vm/config.py:129` |
| Installed default config | `src/somnus_vm/config.py:211` |
| VM state/record | `src/somnus_vm/contracts/vm.py:29`, `src/somnus_vm/contracts/vm.py:121` |
| Guest response contracts | `src/somnus_vm/contracts/agent.py:20` |
| Doctor | `src/somnus_vm/doctor.py:123` |
| Disposition registry | `src/somnus_vm/topology.py:48` |
| Planner | `src/somnus_vm/host/planner.py:31` |
| Port probe | `src/somnus_vm/host/ports.py:33` |
| QEMU argv | `src/somnus_vm/host/qemu.py:43` |
| Bootstrap config | `src/somnus_vm/guest/bootstrap.py:149` |
| Private verified payload | `src/somnus_vm/guest/bootstrap.py:336` |
| Bootstrap operation | `src/somnus_vm/guest/bootstrap.py:468` |
| Direct proof harness | `test/vm_lab/test_vm_lab.py:643` |
| Latest proof ledger | `SOTA_RUN.md:1` |
| Source preservation | `source-manifest.json:1` |
| Snapshot claim | `snapshots/v0.1/manifest.json:1` |
| Why code was quarantined | `docs/RECONSTITUTION_AUDIT.md:15` |
| Original-to-current placement | `docs/MIGRATION_MAP.md:1` |
| Living repository verifier | `scripts/verify_repository.py:1` |

---

## Anomalies and gotchas

- `doctor` failing on missing QEMU/KVM/image may be the correct result.
- `ports_reserved: false` is intentional and must remain explicit.
- `guest/bootstrap.py` is live but not imported during host boot; its separate
  entrypoint is the boundary.
- The most feature-rich code is not necessarily the most authoritative code.
  Candidate and quarantine trees exist to preserve evidence without execution.
- `PLAN.md` describes many absent capabilities. Its unchecked items are gates,
  not undocumented live features.
- Line anchors are verified for existence, not symbol semantics. After source
  movement, repair this map in the same architectural change.
