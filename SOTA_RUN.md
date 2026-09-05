# Latest SOTA Run

<!-- SOTA_RUN_LATEST_START -->
- mode: COMPOSE
- domain: vm_lab
- scope: SCOPE.md
- target_module: PLAN.md, docs/plan-index.json, TASK.md, STATE.md, and
- smoke_harness: test/vm_lab/smoke.py
- run_dir: test/vm_lab/runs/20260905T101913Z
- status: fail
- pass_count: 36
- fail_count: 4
- skip_count: 0
- result_json: test/vm_lab/runs/20260905T101913Z/result.json
- result_md: test/vm_lab/runs/20260905T101913Z/result.md
- result_log: test/vm_lab/runs/20260905T101913Z/result.log
<!-- SOTA_RUN_LATEST_END -->

Interpretation: the four failures are exactly the fixture-absence lanes
(`storage_p3`, `storage_p3_adversarial`, `daemon_storage_p3`,
`storage_runtime_p4`); the pinned 264,306,688-byte qcow2 source is absent from
this host. `plan_contract` passes again (410 unique execution IDs) after the
2026-09-05 §3.1 ID-family repair; the pre-repair failure is preserved in
`test/vm_lab/runs/20260905T101438Z/`. This is a current non-launch aggregate,
not `GATE-P4` evidence.
