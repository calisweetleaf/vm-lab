# VM Lab Active Task

**Rule:** at most one task may be `ACTIVE`. When no task is active, exactly one
task may be `READY`. `PLAN.md` owns the complete destination; this file owns the
smallest selected or ready execution unit.

---

## COMPLETE — `TASK-CTM-001`: Activate the living repository

**Outcome lock**

> Deliver the cold-entry living-repository kernel for `vm-lab`; done when a
> fresh operator can enter through `AGENTS.md`, navigate every owning Markdown
> and source surface, identify the single active task and promotion boundary,
> and both the structural verifier and current v0.1 proof pass.

**Scope**

- [x] Archaeologize the config spine, live runtime, proof harness, source
  manifest, snapshot boundary, plan, and reconstitution decisions.
- [x] Replace the empty `AGENTS.md` with a repository-specific operator kernel.
- [x] Build `TOPOLOGY.md`, `FAILURE_GRAMMAR.md`, and
  `ARCHITECTURE_MAP.md`.
- [x] Establish bounded `CONTEXT.md`, `MEMORY.md`, and this task state.
- [x] Convert `filetree.md` from a flat generated listing into living navigation.
- [x] Install the thin repo-local Codex skill router.
- [x] Initialize `.sovereign/` continuity and topology fingerprint.
- [x] Add GitHub Actions and optional local pre-commit enforcement.
- [x] Run `python scripts/verify_repository.py` — 404 checks passed.
- [x] Run `PYTHONPATH=src python test/vm_lab/smoke.py` — 11 checks passed.
- [x] Record final hashes/evidence in `PROVENANCE.md` and `.sovereign/`.

**Acceptance evidence**

1. `python scripts/verify_repository.py` exits 0.
2. `PYTHONPATH=src python test/vm_lab/smoke.py` exits 0 and emits a fresh
   JSON/Markdown/log bundle.
3. The Somnus Python SOTA verifier accepts `scripts/verify_repository.py`.
4. `git diff --check` exits 0.
5. Every unresolved external boundary is explicit—especially GitHub
   authentication and any third-party webhook receiver.

---

## COMPLETE — `TASK-P0-001`: Lock the execution baseline

**Outcome lock**

> Deliver the Phase P0 execution baseline; done when a direct plan-validation
> run proves unique plan IDs, complete dependencies and gates, exactly one
> current phase, evidence for every completed item, and zero falsely completed
> physical capabilities.

**Plan owner:** [`PLAN.md`](PLAN.md) §8, Phase P0.

**Completed unit**

1. [x] created `STATE.md` for runtime phase state;
2. [x] declared and preserved the exact P0 COMPOSE and EDIT scopes;
3. [x] built `docs/plan-index.json`;
4. [x] added plan-ID/dependency/gate/evidence validation to the harness;
5. [x] rejected eight hostile plan fixtures through the real CLI;
6. [x] closed `GATE-P0` without promoting runtime mutation.

**Acceptance evidence**

- gate-time plan validator: 1,722 observations, 0 failures;
- final post-transition plan validator: 1,733 observations, 0 failures;
- final repository verifier: 2,174 observations, 0 failures;
- gate evidence: `test/vm_lab/runs/20260805T054841Z/result.json`;
- final closure evidence: `test/vm_lab/runs/20260805T055444Z/result.json`;
- smoke result: 13 pass, 0 fail, 0 skip;
- Somnus SOTA verifier: 16/16 applicable checks on both edited Python owners.

---

## COMPLETE — `TASK-P1-001`: Compose the canonical protocol boundary

**Outcome lock**

> Deliver the pure `somnus_protocol` boundary and compatibility handoff; done
> when every current VM and agent schema round-trips through the canonical
> package, host and guest imports remain side-effect free, unknown fields and
> unsupported versions fail loudly, and the v0.1 public commands still pass.

**Plan owner:** [`PLAN.md`](PLAN.md) §9, Phase P1.

**Completed unit**

1. [x] declared the P1 COMPOSE scope;
2. [x] created the dependency-free canonical `somnus_protocol` package;
3. [x] replaced live VM and agent implementations with exact compatibility
   re-exports;
4. [x] composed strict control/version/event envelopes and typed host settings;
5. [x] bound the sixteen-state, forty-eight-edge VM lifecycle to declaration,
   boot, observation, process, migration, and replay invariants;
6. [x] consumed the canonical record through the real planner, CLI, and an
   installed wheel;
7. [x] closed independent Terra adversarial findings rather than promoting the
   first green smoke.

**Acceptance evidence**

- gate bundle: `test/vm_lab/runs/20260805T182129Z`;
- final post-transition bundle: `test/vm_lab/runs/20260805T182839Z`;
- consumed smoke: 19 pass, 0 fail, 0 skip;
- post-transition plan validator: 1,808 observations, 0 failures;
- direct lifecycle authority: 12 schemas, 16 states, 48 legal edges, 48 exact
  evidence entries;
- public host boundary remains `doctor`, `topology`, and non-mutating `plan`;
- real QMP, daemon ownership, registry authority, and lifecycle mutation remain
  unpromoted.

Do not begin the daemon, registry, QEMU launch, guest service, or lifecycle CLI
inside this unit.

---

## COMPLETE — `TASK-P2-001`: Build the single mutation owner

**Outcome lock**

> Deliver the Phase P2 single-owner daemon, transactional registry, and
> resumable operation journal; done when at least twenty independent real
> client processes race declare, lease, update, and cancel through one
> authenticated Unix-socket daemon with zero lost rows, duplicate ports,
> duplicate disks, stale locks, or contradictory events, and daemon termination
> at every persisted checkpoint recovers deterministically.

**Plan owner:** [`PLAN.md`](PLAN.md) §10, Phase P2.

**First bounded unit**

1. [x] declared a P2 COMPOSE scope around the daemon, client, registry,
   ownership, and recovery surfaces;
2. [x] implemented the versioned normalized SQLite authority and fail-loud migration,
   backup, integrity, and recovery modes;
3. [x] established the one-daemon Unix-socket membrane with filesystem permissions,
   peer credentials, bounded framing, and no second supervisor or registry
   writer;
4. [x] journaled operation intent, owned resources, idempotency, checkpoints, and
   observed completion in the same transactional authority;
5. [x] drove the implementation through real multi-process races and process-kill
   checkpoint recovery.

Do not add public QEMU lifecycle commands, construct disks, or claim physical
machine truth inside this unit.

**Acceptance evidence**

- consumed gate bundle: `test/vm_lab/runs/20260805T194938Z`;
- full smoke: 23 pass, 0 fail, 0 skip;
- real daemon console entry point and clean SIGTERM shutdown;
- 24 simultaneous independent declarers and 96 total client-process mutations;
- 12 real `SIGKILL` recovery windows: four metadata operations times three
  durable checkpoints;
- one normalized SQLite writer, 16 focused P2 tests outside the smoke wrapper,
  and no fabricated qcow2, QEMU, process, lifecycle, or snapshot truth.

---

## COMPLETE — `TASK-P3-001`: Build verified persistent AIPC storage

**Outcome lock**

> Deliver Phase P3 image provenance and persistent AIPC storage; done when a
> real `qemu-img` process creates two independent sparse overlays from one
> hash-bound base image, verifies their complete backing chains and virtual
> sizes, rejects symlink/hard-link alias ownership, and process termination at
> every create checkpoint leaves no unowned or partial image.

**Plan owner:** [`PLAN.md`](PLAN.md) §11, Phase P3.

**Completed unit**

1. [x] declared the exact P3 COMPOSE scope around live image contracts, a
   shell-free `qemu-img` backend, registry ownership, and recovery;
2. [x] defined a strict versioned image manifest that binds asserted
   provenance separately from host-measured bytes, format, virtual size,
   backing chain, payload metadata, and tool identity;
3. [x] verified imported base bytes and `qemu-img info --output=json
   --backing-chain` before immutable publication;
4. [x] created sparse per-AIPC overlays in private temporary directories and
   published them atomically only after independent info/check evidence;
5. [x] drove all storage claims through the P2 daemon journal and proved
   deterministic recovery under real process termination.

Do not launch QEMU, mount production AIPC disks, mutate the sealed v0.1
snapshot, or treat a missing disposable image fixture as a passing gate.

**Acceptance evidence**

- immutable gate bundle: `test/vm_lab/runs/20260805T211740Z`;
- full smoke: 26 pass, 0 fail, 0 skip;
- 15 focused P3 tests: 6 core storage, 8 adversarial storage, and 1 real
  daemon/client storage gate;
- real `/usr/bin/qemu-img` 8.2.2 and pinned Ubuntu 24.04 Minimal source SHA256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`;
- one immutable verified base and two independent sparse 100 GiB overlays with
  complete backing-chain, virtual-size, allocation, and registry evidence;
- 7 base-import plus 7 overlay-create process-termination checkpoints;
- foreign-final non-adoption, symlink/hard-link and new-inode rejection,
  post-publication recovery, and native child parent-death containment;
- no QEMU VM launch, mount, guest-ready claim, production disk mutation, or
  public lifecycle promotion.

`P3-013` and `P3-014` are closed as an executable membrane, not as fabricated
future APIs: arbitrary disk import, clone, export, detach, and deletion remain
separate unimplemented operations, and only the two named P3 storage mutations
are accepted.

---

## ACTIVE — `TASK-P4-019`: Re-establish current P3 evidence authority before P4 machine truth

**Outcome lock**

> Deliver current, authoritative P3 reproducibility evidence; done when the
> source-preservation divergence and exact fixture authority are resolved by
> their named sources, current P3/P4 fixture-dependent proof is green, and no
> physical VM action has occurred.

**Plan owner:** [`PLAN.md`](PLAN.md) §3.1 RECOVERY-R0, then §12 Phase P4.

**Current truth**

The historical P3 gate and 2026-08-07 40/0/0 aggregate remain sealed historical
claims. Current published proof is `20260905T101913Z` at 36 pass / 4 fixture-only
failures. RECOVERY-R0 resolved the browser preservation divergence from matching
retained archives and original Git evidence. This host now has declared-origin
bytes at `/tmp/vm-lab-p3-fixture-20260801.qcow2` with matching size, SHA-256,
regular-file, mode `0600`, uid, and nlink identity; `BASE-018` is unconsumed
until the four fixture-dependent owners and a fresh aggregate pass. The P4
runtime, permit, coordinator, and non-launch owners remain source-complete, but
the physical gate stays frozen until that current proof exists.

**Active bounded unit**

1. retain every failed aggregate and freeze P4 execute-matrix/public promotion;
2. locate or supply only the exact manifest-bound P3 fixture or an authenticated
   byte-identical copy; reject all substitutes;
3. re-run the fixture-dependent P3/P4 owners, publish a fresh bundle, and
   update current-state documents only from it;
4. return to the Daeron-authorized P4 matrix only after R0 passes.

**Hard bans**

Do not launch QEMU, execute the prepared matrix, introduce a public lifecycle
command, create a substitute fixture, import the external donor, erase failed
artifacts, or reinterpret historical green output as current success.

---

## Queue

| Order | Unit | Depends on | Gate |
| --- | --- | --- | --- |
| 1 | GATE-P4 disposable QEMU/QMP checkpoint matrix | RECOVERY-R0 / `BASE-018` | `GATE-P4` |
| 2 | P5 durable networking | `GATE-P4` | `GATE-P5` |
| 3 | authenticated guest readiness | P5 | `GATE-P6` |

All later work remains in [`PLAN.md`](PLAN.md), not duplicated here.
