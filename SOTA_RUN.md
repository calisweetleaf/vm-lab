# Latest SOTA Run

<!-- SOTA_RUN_LATEST_START -->
- mode: COMPOSE
- domain: vm_lab
- scope: SCOPE.md
- target_module: PLAN.md, docs/plan-index.json, TASK.md, STATE.md, and
- smoke_harness: test/vm_lab/smoke.py
- run_dir: test/vm_lab/runs/20260905T111215Z
- status: pass
- pass_count: 40
- fail_count: 0
- skip_count: 0
- result_json: test/vm_lab/runs/20260905T111215Z/result.json
- result_md: test/vm_lab/runs/20260905T111215Z/result.md
- result_log: test/vm_lab/runs/20260905T111215Z/result.log
<!-- SOTA_RUN_LATEST_END -->

Interpretation: RECOVERY-R0 is closed. The four previously failing
fixture-absence lanes (`storage_p3`, `storage_p3_adversarial`,
`daemon_storage_p3`, `storage_runtime_p4`) now pass against the declared-origin
qcow2 at `/tmp/vm-lab-p3-fixture-20260801.qcow2`. Failed aggregates
`20260905T104044Z`, `20260905T101913Z`, and earlier remain preserved. This is
a current non-launch aggregate, not `GATE-P4` evidence. No qemu-system process
ran. Note: the harness rewrites this file to the bare ledger block on every
run; this interpretation is operator-owned and re-appended after each run.
