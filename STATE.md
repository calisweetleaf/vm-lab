# VM Lab Execution State

**Schema:** 1  
**Current phase:** `P1`  
**Exact unit:** `TASK-P1-001` — compose the canonical protocol boundary  
**Status:** ready

## Outcome lock

> Deliver the pure `somnus_protocol` boundary and compatibility handoff; done
> when every current VM and agent schema round-trips through the canonical
> package, host and guest imports remain side-effect free, unknown fields and
> unsupported versions fail loudly, and the v0.1 public commands still pass.

## Live execution

- last command: `PYTHONPATH=src python test/vm_lab/smoke.py`
- last observed result: `13 passed, 0 failed, 0 skipped`
- current blocker: none for the first P1 protocol unit; the canonical
  disposable base image remains unresolved for the later P3 physical gate
- next action: declare the P1 COMPOSE scope and create the dependency-free
  `somnus_protocol` package spine
- dirty files: inspect with `git status --short`; unrelated operator changes
  must remain untouched

## Evidence

- committed entry baseline: `f13fb3d`
- promoted runtime floor: `snapshots/v0.1/manifest.json`
- P0 gate proof: `test/vm_lab/runs/20260805T054841Z/result.json`
- final closure proof: `test/vm_lab/runs/20260805T055444Z/result.json`
- current plan validation: 1,733 observations, 0 failures
- current repository verification: 2,174 observations, 0 failures

This file records active execution state only. `TASK.md` owns task selection,
`PLAN.md` owns the destination, and sealed snapshots remain immutable claims.
