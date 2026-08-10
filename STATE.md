# VM Lab Execution State

**Schema:** 1
**Current phase:** `P4`
**Exact unit:** `TASK-P4-019` — prove disposable QEMU machine truth
**Status:** active

## Outcome lock

> Deliver Phase P4 QEMU process truth and QMP; done when actual QEMU launches
> one disposable P3-owned overlay through the daemon-owned runtime, QMP proves
> the expected machine UUID, running state, and root-disk attachment, serial
> output is retained, and daemon restart adopts or cleans the exact child
> without touching a foreign process.

## Live execution

- last command: `python scripts/verify_repository.py`
- last observed result: non-launch `prepare-matrix` and `preflight-matrix`
  created and validated matrix `b9aca76a-a016-4d0c-9002-f0a90f383b21` at
  `/tmp/vm-lab-gate-p4-matrix-20260807T124705Z`; its canonical spec SHA-256 is
  `9567494af6899c1d2ce5353845950b78a087f4fcf4deb22d67db54950542a104`,
  it contains all 17 exact scenarios, and no registry, socket, PID, result, or
  qemu-system process exists. Repository validation passed 2,625/2,625 after current aggregate and
  documentation synchronization plus fingerprint reconciliation. Plan-only validation for the latest aggregate passed 2,037/2,037; the
  post-sync direct verifier plan-only lane passed 2,040/2,040. Disposable launch authority 5/5, physical-gate coordinator 8/8, runtime journal
  9/9, daemon/runtime composition 4/4, and QMP direct proof 14/14 are green. No `qemu-system` process was launched. The latest
  aggregate `test/vm_lab/runs/20260807T123747Z/result.json` records 40 pass,
  0 fail, and 0 skip. The exact pinned qcow2 source is restored at
  `/tmp/vm-lab-p3-fixture-20260801.qcow2` with size 264,306,688, mode 0600,
  uid 1000, nlink 1, and SHA-256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.
- current blocker: all 18 bounded P4 phase items and the non-public
  coordinator are implemented, the exact source is restored, and a frozen
  matrix is prepared/preflighted with conservative exclusions for the observed
  operator-owned roots `/home/daeron` and `/media/daeron/usb-128gb`. Read-only
  host discovery found no deployed qcow2, libvirt domain, QEMU process, or
  Somnus/AIPC production state; the exact disposable source is the only qcow2
  on mounted storage. The remaining physical boundary is Daeron's explicit
  authority to execute this prepared disposable matrix, an actual qemu-system
  run, and all 17 success/checkpoint adoption/cleanup outcomes. `GATE-P4`
  remains open and no physical machine truth has been claimed.
- next action: after Daeron explicitly authorizes execution of prepared matrix
  `b9aca76a-a016-4d0c-9002-f0a90f383b21`, run `execute-matrix` with its generated
  accidental-invocation phrase through the daemon-owned
  QEMU/QMP/checkpoint/recovery matrix; keep `GATE-P4` open until sealed
  machine-truth evidence exists
- dirty files: inspect with `git status --short`; unrelated operator changes
  and every retained adversarial run bundle must remain untouched

## Evidence

- committed entry baseline: `f13fb3d`
- promoted runtime floor: `snapshots/v0.1/manifest.json`
- P0 gate proof: `test/vm_lab/runs/20260805T054841Z/result.json`
- P1 gate proof: `test/vm_lab/runs/20260805T182129Z/result.json`
- P2 gate proof: `test/vm_lab/runs/20260805T194938Z/result.json`
- P3 gate proof: `test/vm_lab/runs/20260805T211740Z/result.json`
- last fully green aggregate before the current HEAD:
  `test/vm_lab/runs/20260805T234424Z/result.json` — 37 pass, 0 fail, 0 skip
- current aggregate: `test/vm_lab/runs/20260807T123747Z/result.json` — 40 pass,
  0 fail, 0 skip; exact fixture-dependent storage lanes now pass
- P1 consumed boundary: 12 VM schemas, 16 states, 48 legal edges, 48 exact
  evidence requirements, strict control/agent/settings protocols, real
  planner/CLI consumption, and an installed wheel
- P2 consumed boundary: real daemon entry point; authenticated Unix socket;
  normalized SQLite authority; 24 simultaneous declarers; 96 total
  client-process mutations; 12 real SIGKILL checkpoint recoveries; no QEMU,
  qcow2, process, lifecycle, or snapshot fabrication
- P3 consumed boundary: one checksum-bound immutable base; two independent
  sparse 100 GiB overlays; complete `qemu-img` backing-chain, virtual-size,
  allocation, and registry observations; 7 base plus 7 overlay termination
  checkpoints; 15 focused physical tests; no QEMU launch or mount
- current structural validation after documentation/fingerprint
  synchronization: 2,625 observations, 0 failures; direct plan-only validation
  passed 2,040 observations (the aggregate bundle records 2,037 plan-index
  observations)
- P4 plan ledger: all 18 implementation items complete; `GATE-P4` remains open
  and P5+ remains pending
- P4 consumed implementation boundary: exact P3 overlay/base/marker descriptor
  pinning; guard READY/release metadata and immutable-content fences; two
  QEMU fdsets and four explicit block nodes; authenticated QMP fdset,
  named-node, blockstats, UUID/status/root-disk, and `/proc` peer-descriptor
  validation; durable orphan-emergency recovery; selected-QEMU sandbox doctor
  interrogation without VM launch; one-shot disposable-fixture authority with
  explicit production exclusions and a canonical durable receipt; Python-only
  activation inside the ordinary daemon composition without public lifecycle
  vocabulary
- P4 gate-readiness boundary: the physical coordinator defines and preflight-tests
  17 isolated scenarios (success plus every checkpoint), the exact pinned source
  is restored, and matrix `b9aca76a-a016-4d0c-9002-f0a90f383b21` is frozen and
  preflighted with conservative operator-root exclusions. No Daeron physical
  execution authority has been supplied and no physical QEMU/matrix has run
- public host capability: unchanged `doctor`, `topology`, and non-mutating
  `plan`; the daemon is an internal registry and image-storage authority and no
  VM lifecycle command is promoted yet

This file records active execution state only. `TASK.md` owns task selection,
`PLAN.md` owns the destination, and sealed snapshots remain immutable claims.
