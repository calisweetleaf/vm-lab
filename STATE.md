# VM Lab Execution State

**Schema:** 1
**Current phase:** `P4`
**Exact unit:** `TASK-P4-001` — prove QEMU process truth and QMP identity
**Status:** active

## Outcome lock

> Deliver Phase P4 QEMU process truth and QMP; done when actual QEMU launches
> one disposable P3-owned overlay with both the writable overlay and immutable
> base descriptor-bound through the process guard into the proved QEMU block
> graph, completes greeting/capabilities and matching executable/start-time/
> pidfile/VM-UUID/QMP-UUID/status observations, captures serial output, and
> daemon death at each launch checkpoint adopts or cleans the child without
> orphaning it or signaling a foreign process.

## Live execution

- last command: `python scripts/verify_repository.py`
- last observed result: 2,449 repository observations, 0 failures; the latest
  consumed-boundary smoke is 26 pass, 0 fail, 0 skip in
  `test/vm_lab/runs/20260805T212927Z`, while the immutable P3 gate remains
  `test/vm_lab/runs/20260805T211740Z`
- current blocker: the live launch path does not yet pin both the writable
  overlay and its immutable base before QEMU can write, inherit both descriptors
  through the guard, or prove their exact fdset roles and four-node block edges
  through QMP plus the QMP peer PID's `/proc` descriptor truth; a real
  no-QEMU adversarial run also proved that same-inode bytes can change after
  guard `READY` unless the release fence revalidates full overlay metadata, a
  pinned owner marker, and the immutable base digest; no disposable physical
  QEMU launch has been authorized or performed
- next action: bind both P3 storage owners to validated descriptors, carry
  those descriptors plus the owner-marker proof through the guard's final
  content/metadata fence into the explicit fdset-backed block graph, and close
  the non-launch contract/parser tests before requesting the authority-gated
  physical response capture
- dirty files: inspect with `git status --short`; unrelated operator changes
  and every retained adversarial run bundle must remain untouched

## Evidence

- committed entry baseline: `f13fb3d`
- promoted runtime floor: `snapshots/v0.1/manifest.json`
- P0 gate proof: `test/vm_lab/runs/20260805T054841Z/result.json`
- P1 gate proof: `test/vm_lab/runs/20260805T182129Z/result.json`
- P2 gate proof: `test/vm_lab/runs/20260805T194938Z/result.json`
- P3 gate proof: `test/vm_lab/runs/20260805T211740Z/result.json`
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
- current plan validation: 1,945 direct observations, 0 failures, with P4 as
  the only active phase
- public host capability: unchanged `doctor`, `topology`, and non-mutating
  `plan`; the daemon is an internal registry and image-storage authority and no
  VM lifecycle command is promoted yet

This file records active execution state only. `TASK.md` owns task selection,
`PLAN.md` owns the destination, and sealed snapshots remain immutable claims.
