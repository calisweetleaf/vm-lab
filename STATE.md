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

- current aggregate: `test/vm_lab/runs/20260905T101913Z/result.json` — 36 pass,
  4 fail, 0 skip; `SOTA_RUN.md` is authoritative for this run.
- current failures: only `storage_p3`, `storage_p3_adversarial`,
  `daemon_storage_p3`, and `storage_runtime_p4`; all fail loudly because
  `/tmp/vm-lab-p3-fixture-20260801.qcow2` is absent.
- 2026-09-05 push-trigger review: verifier 2628/2628 on the Cloud Agent Linux
  host; found and repaired latent plan-contract drift from `ae0b077` (§3.1
  recovery items carried out-of-grammar `RECOVERY-R0-00x` IDs; now `BASE-014`
  …`BASE-018`). Pre-repair failure evidence preserved in
  `test/vm_lab/runs/20260905T101438Z/`. No runtime source changed; no QEMU
  process launched.
- resolved R0 branch: all 33 source-manifest entries now pass. The manifest-bound
  `internal_browser.py` bytes were restored atomically from two matching retained
  archives and matching original Git evidence. The later divergent blob remains
  preserved in Git history and current `vm-lab.zip` evidence.
- historical evidence: P3's sealed 20260805T211740Z bundle and P4's historical
  20260807T123747Z 40/0/0 bundle remain preserved but do not establish current
  reproducibility. No qemu-system VM or matrix has run.
- active blocker: the exact fixture must come from declared origin or separately
  authenticated byte-identical authority. No substitute/rebuilt/newer image is
  admissible.
- reconstitution: 2026-09-05 restored `PLAN.md`, `CONTEXT.md`, `MEMORY.md`,
  `SOTA_RUN.md`, `Somnus-Core-Ideals.md`, `OPSEC.md`, `workflows-new.md`,
  `.codex/skills/vm-lab/SKILL.md`, `.gitignore`, and stdlib-only packaging from
  `ae0b077` after `b11880c` deleted them. Cloud `.cursor/` environment kept.
- next consumed action: acquire/locate the exact fixture from declared origin
  or separately authenticated byte-identical authority, rerun the four
  fixture-dependent P3/P4 owners, publish a fresh bundle, then obtain Daeron's
  explicit physical authority for the P4 matrix. The Linux/Cloud Agent
  verifier step is consumed: 2628/2628 on 2026-09-05.

## Evidence

- promoted historical floor: `snapshots/v0.1/manifest.json`
- current failing ledger: `SOTA_RUN.md` and `test/vm_lab/runs/20260905T101913Z/`
- preserved plan-contract failure: `test/vm_lab/runs/20260905T101438Z/`
- resolved source authority: `source-manifest.json`, `archive/vm-lab-first-zip.zip`,
  `/home/daeron/Downloads/vm_lab_reorganized.zip`, and original matching Git blob
- fixture authority: `test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`
- execution authority: `PLAN.md` §3.1 RECOVERY-R0 and `TASK.md`

This state records current evidence only. Historical gates stay historical;
`PLAN.md` owns the complete destination and `TASK.md` owns the active unit.
