# Latest SOTA Run

<!-- SOTA_RUN_LATEST_START -->
- mode: COMPOSE
- domain: vm_lab
- scope: SCOPE.md
- target_module: PLAN.md, docs/plan-index.json, TASK.md, STATE.md, and
- smoke_harness: test/vm_lab/smoke.py
- run_dir: test/vm_lab/runs/20260905T124449Z
- status: pass
- pass_count: 40
- fail_count: 0
- skip_count: 0
- result_json: test/vm_lab/runs/20260905T124449Z/result.json
- result_md: test/vm_lab/runs/20260905T124449Z/result.md
- result_log: test/vm_lab/runs/20260905T124449Z/result.log
<!-- SOTA_RUN_LATEST_END -->

Interpretation: RECOVERY-R0 is fully satisfied. The exact manifest-bound fixture
was located at its declared origin URL
(`https://cloud-images.ubuntu.com/minimal/releases/noble/release-20260801/ubuntu-24.04-minimal-cloudimg-amd64.img`),
verified at exactly 264,306,688 bytes and SHA-256
`b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`, placed at
`/tmp/vm-lab-p3-fixture-20260801.qcow2` with mode 0600 uid 1000 nlink 1. All 40
checks pass: every P1/P2/P3/P4 direct owner, plan contract (410 unique IDs),
plan index (2042 observations), and source manifest (33/33). This is current
non-launch proof; it unblocks the P4 physical gate but does not close it.
`GATE-P4` still requires Daeron's explicit disposable-matrix authority,
production-exclusion paths, and an authorized `qemu-system-x86_64` run.
The previous 36/4 aggregates (`20260905T101438Z`, `20260905T101913Z`) are
preserved as fixture-absence evidence; the pre-repair plan-contract failure is
in `20260905T101438Z`.
