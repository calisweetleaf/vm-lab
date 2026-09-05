# VM Lab Execution State

**Schema:** 1
**Current phase:** `P4`
**Exact unit:** `TASK-P4-019` — establish exact fixture authority before physical QEMU truth
**Status:** active

## Outcome lock

> Deliver authoritative current P3/P4 prerequisite evidence; done when the
> exact fixture is restored from named authority, current fixture-dependent
> proof passes, and no physical VM action was taken.

## Live execution

- current aggregate: `test/vm_lab/runs/20260905T124449Z/result.json` — 40 pass,
  0 fail, 0 skip; `SOTA_RUN.md` is authoritative for this run.
- RECOVERY-R0 complete: the exact manifest-bound fixture was downloaded from its
  declared origin URL, verified at 264,306,688 bytes and SHA-256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`, placed at
  `/tmp/vm-lab-p3-fixture-20260801.qcow2` mode 0600 uid 1000 nlink 1. All 40
  checks now pass including all four previously fixture-absent lanes.
- 2026-09-05 push-trigger review: verifier 2628/2628; latent plan-contract drift
  from `ae0b077` was repaired (§3.1 recovery items carry `BASE-014`...`BASE-018`).
  Pre-repair failure preserved in `test/vm_lab/runs/20260905T101438Z/`.
- resolved R0 source branch: all 33 source-manifest entries pass. `internal_browser.py`
  bytes restored from two retained archives and original Git evidence.
- historical evidence: P3's sealed 20260805T211740Z bundle and P4's historical
  20260807T123747Z 40/0/0 bundle remain preserved. No qemu-system VM or matrix
  has run.
- active blocker: `GATE-P4` requires Daeron's explicit disposable-matrix
  authority and production-exclusion paths before `scripts/run_gate_p4.py
  execute-matrix` may be invoked.
- reconstitution: 2026-09-05 restored `PLAN.md`, `CONTEXT.md`, `MEMORY.md`,
  `SOTA_RUN.md`, `Somnus-Core-Ideals.md`, `OPSEC.md`, `workflows-new.md`,
  `.codex/skills/vm-lab/SKILL.md`, `.gitignore`, and stdlib-only packaging from
  `ae0b077` after `b11880c` deleted them. Cloud `.cursor/` environment kept.
- next consumed action: obtain Daeron's explicit disposable-matrix authority for
  matrix `b9aca76a-a016-4d0c-9002-f0a90f383b21` and production-exclusion paths;
  run `scripts/run_gate_p4.py prepare-matrix` (new matrix since `/tmp` cleared),
  then `preflight-matrix` and `execute-matrix`; verify the sealed bundle;
  update continuity. The fixture authority step is consumed: 40/0/0 on
  2026-09-05.

## Evidence

- promoted historical floor: `snapshots/v0.1/manifest.json`
- current passing ledger: `SOTA_RUN.md` and `test/vm_lab/runs/20260905T124449Z/` (40/0/0)
- preserved fixture-absence evidence: `test/vm_lab/runs/20260905T101913Z/` (36/4)
- preserved plan-contract failure: `test/vm_lab/runs/20260905T101438Z/`
- resolved source authority: `source-manifest.json`, `archive/vm-lab-first-zip.zip`,
  `/home/daeron/Downloads/vm_lab_reorganized.zip`, and original matching Git blob
- fixture authority: `test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`;
  fixture at `/tmp/vm-lab-p3-fixture-20260801.qcow2` verified from declared origin
- execution authority: `PLAN.md` §12 Phase P4 `GATE-P4` — Daeron's explicit
  disposable-matrix authority and production exclusions required

This state records current evidence only. Historical gates stay historical;
`PLAN.md` owns the complete destination and `TASK.md` owns the active unit.
