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

## READY — `TASK-P1-001`: Compose the canonical protocol boundary

**Outcome lock**

> Deliver the pure `somnus_protocol` boundary and compatibility handoff; done
> when every current VM and agent schema round-trips through the canonical
> package, host and guest imports remain side-effect free, unknown fields and
> unsupported versions fail loudly, and the v0.1 public commands still pass.

**Plan owner:** [`PLAN.md`](PLAN.md) §9, Phase P1.

**First bounded unit**

1. declare a new P1 COMPOSE scope;
2. create the dependency-free `somnus_protocol` package spine;
3. move one schema family at a time behind explicit compatibility imports;
4. close direct serialization and cold-import fixtures before the next family.

Do not begin the daemon, registry, QEMU launch, guest service, or lifecycle CLI
inside this unit.

---

## Queue

| Order | Unit | Depends on | Gate |
| --- | --- | --- | --- |
| 1 | P1 protocol/state/settings | P0 | `GATE-P1` |
| 2 | P2 daemon/registry/ownership | P1 | `GATE-P2` |
| 3 | P3 owned overlay storage | P2 | `GATE-P3` |
| 4 | P4 QMP/process identity | P3 | `GATE-P4` |
| 5 | first real QEMU boot | P4 + disposable fixture | physical run |

All later work remains in [`PLAN.md`](PLAN.md), not duplicated here.
