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

## READY — `TASK-P0-001`: Lock the execution baseline

This is ready, not active. It begins only when a new goal selects runtime
implementation.

**Plan owner:** [`PLAN.md`](PLAN.md) §8, Phase P0.

Expected first unit:

1. create `STATE.md` for runtime phase state;
2. declare the exact P0 `SCOPE.md` unit;
3. build `docs/plan-index.json`;
4. add plan-ID/dependency/gate validation to the harness;
5. close `GATE-P0` before P1 implementation.

Do not skip directly to snapshots, guest cognition, file processing, or shell
wiring.

---

## Queue

| Order | Unit | Depends on | Gate |
| --- | --- | --- | --- |
| 1 | P0 execution baseline | living repository activation | `GATE-P0` |
| 2 | P1 protocol/state/settings | P0 | `GATE-P1` |
| 3 | P2 daemon/registry/ownership | P1 | `GATE-P2` |
| 4 | P3 owned overlay storage | P2 | `GATE-P3` |
| 5 | P4 QMP/process identity | P3 | `GATE-P4` |
| 6 | first real QEMU boot | P4 + disposable fixture | physical run |

All later work remains in [`PLAN.md`](PLAN.md), not duplicated here.
