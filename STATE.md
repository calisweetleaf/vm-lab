# VM Lab Execution State

**Schema:** 1
**Current phase:** `P4`
**Exact unit:** `TASK-P4-020` — execute the disposable GATE-P4 QEMU/QMP matrix
**Status:** ready

## Outcome lock

> Deliver `GATE-P4` machine truth; done when the coordinator seals the 17-scenario
> disposable matrix and the public CLI still has no lifecycle command.

## Live execution

- current aggregate: `test/vm_lab/runs/20260905T111215Z/result.json` — 40 pass,
  0 fail, 0 skip; `SOTA_RUN.md` is authoritative for this run.
- RECOVERY-R0 closed: `BASE-017` acquired the exact declared-origin fixture;
  `BASE-018` published the current 40/0/0 non-launch bundle.
- fixture identity on this host: `/tmp/vm-lab-p3-fixture-20260801.qcow2` size
  264,306,688, mode 0600, uid 1000, nlink 1, SHA-256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`; Canonical
  `SHA256SUMS` for release-20260801 agrees.
- preserved failure evidence: `20260825T011700Z`, `20260825T043503Z`,
  `20260905T101438Z`, `20260905T101913Z`, and `20260905T104044Z` remain; none
  were erased.
- 2026-09-05 push-trigger review of `1b0121c..1d0e118` (PR #2 merge): docs-only
  push, hard bans held, trigger metadata matched observed git truth. Verifier
  2628/2628; plan DAG 2042/2042; then-current aggregate `20260905T104044Z`
  reproduced the pushed `20260905T101913Z` 36/4 fixture-absence claim. Repaired
  one stale kernel anchor: AGENTS.md §9 framed sealed bundle `20260807T123747Z`
  as "current-HEAD"; it is a named historical fixture-present claim routing
  current-proof authority to `SOTA_RUN.md`.
- resolved R0 source branch: all 33 source-manifest entries now pass. The
  manifest-bound `internal_browser.py` bytes were restored atomically from two
  matching retained archives and matching original Git evidence. The later
  divergent blob remains preserved in Git history and current `vm-lab.zip`
  evidence.
- historical evidence: P3's sealed 20260805T211740Z bundle and P4's historical
  20260807T123747Z 40/0/0 bundle remain provenance only.
- next consumed action: prepare and preflight a new private P4 matrix on this
  host, record this host's production exclusions, then execute the matrix only
  through the non-public driver. No qemu-system process has run.
- reconstitution: 2026-09-05 restored operator-kernel docs from `ae0b077` after
  `b11880c` deleted them; later the same day repaired §3.1 ID grammar and
  closed fixture authority from declared origin.

## Evidence

- promoted historical floor: `snapshots/v0.1/manifest.json`
- current passing ledger: `SOTA_RUN.md` and `test/vm_lab/runs/20260905T111215Z/`
- preserved fixture-absence ledgers: `test/vm_lab/runs/20260905T104044Z/`,
  `test/vm_lab/runs/20260905T101913Z/`, and earlier 36/4 bundles
- preserved plan-contract failure: `test/vm_lab/runs/20260905T101438Z/`
- resolved source authority: `source-manifest.json`, `archive/vm-lab-first-zip.zip`,
  `/home/daeron/Downloads/vm_lab_reorganized.zip`, and original matching Git blob
- fixture authority: `test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`
- execution authority: `PLAN.md` §12 `GATE-P4` and `TASK.md`

This state records current evidence only. Historical gates stay historical;
`PLAN.md` owns the complete destination and `TASK.md` owns the ready unit.
