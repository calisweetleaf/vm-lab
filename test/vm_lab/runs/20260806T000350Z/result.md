# VM Lab Integration Run

I ran 37 checks against the public read-only CLI, internal daemon/registry/storage boundary, and guest bootstrap.
I observed 36 passes, 1 failures, and 0 skips.

## What I exercised

- `protocol_vm_primitives`: pass — round-tripped strict VM definition/ports, rejected coercion and unknown fields, preserved aliases, and proved cold import isolation
- `protocol_agent`: pass — round-tripped and adversarially validated 8 agent protocol surfaces
- `protocol_control`: pass — round-tripped and adversarially bounded 24 version/control/event protocol cases with a cold-import proof
- `protocol_settings`: pass — validated 9 strict typed-settings cases across legacy, split, generated-profile, root-ownership, and secret-reference paths
- `protocol_host_consumption`: pass — proved canonical VMRecord identity through compatibility imports and the real non-mutating planner/CLI; public plan output is versioned, secret-free, and leaves no host state or port reservation
- `protocol_vm_lifecycle`: pass — round-tripped every canonical VM schema; exhaustively enforced all lifecycle edges and typed evidence; rejected contradictory runtime observations, intent/history drift, process-identity substitution, nonconsecutive boot/process/observation replay, unsafe QMP paths, duplicate error causes, migration-provenance tampering, and unverified v0 states; proved immutable records, exact DECLARED migration, terminal destruction/new generation, idempotent operations, and cold import isolation
- `contract_roundtrip`: pass — round-tripped one canonical secret-free record, rejected an illegal state jump, and fixed guest endpoint names
- `configuration`: pass — loaded resolved configs and rejected truthy booleans plus ambiguous QMP paths
- `port_allocation`: pass — bind-checked 24 distinct ephemeral ports across eight threads
- `qemu_plan`: pass — encoded a comma-bearing disk with JSON blockdev, closed sandbox, headless/no-network argv, separate logs, and exact command identity
- `bootstrap_extraction`: pass — extracted bounded data and rejected traversal, links, duplicates, and size drift
- `bootstrap_idempotency`: pass — installed/recovered bounded payloads and rejected stale, ambiguous, mislabeled, or colliding intent
- `doctor_truthfulness`: pass — reported 9 source-aware checks with healthy=False
- `cli_entrypoint`: pass — ran checkout-independent topology, redacted planning, and clean port-exhaustion errors
- `wheel_runtime`: pass — installed a wheel with canonical/compatibility protocol identity, checkout-free CLI, the registry/storage daemon entry point, and clean malformed-payload exit behavior
- `daemon_transport_p2`: pass — proved bounded SO_PEERCRED Unix transport and exclusive daemon ownership
- `registry_p2`: pass — proved normalized SQLite ownership, integrity, migration, and writer locking
- `registry_service_p2`: pass — proved strict metadata operations and abrupt writer-exit recovery
- `daemon_registry_p2`: pass — proved 24-process operation races and real SIGKILL recovery at every checkpoint
- `storage_p3`: pass — proved pinned-base import, two real 100 GiB overlays, and exact checkpoint recovery
- `storage_p3_adversarial`: pass — proved foreign-image non-adoption, checkpoint tamper rejection, and parent-death containment
- `daemon_storage_p3`: pass — proved real daemon/client storage composition and startup physical reconciliation
- `qemu_planning_p4`: pass — proved hardened QEMU planning through builder, planner, and public CLI
- `qemu_process_p4`: pass — proved exact Linux process identity, pidfiles, and signal revalidation
- `qemu_exec_guard_p4`: pass — proved journal-before-exec handoff, parent-death containment, and exact adoption
- `qemu_logs_p4`: pass — proved private redaction, bounded rotation, dual-pipe drain, and durable guardian release
- `qmp_p4`: pass — proved bounded QMP framing, negotiation, response/event separation, and identity
- `qmp_identity_p4`: pass — proved strict stabilized QMP identity composition and foreign-process rejection
- `registry_p4`: pass — proved registry-owned P4 process and disk-runtime observation authority
- `storage_runtime_p4`: pass — proved real qcow2 growth remains append-only runtime evidence without rewriting P3 ownership
- `qemu_runtime_journal_p4`: fail — AssertionError: test_qemu_runtime_journal_p4.py failed
stdout:

stderr:
EEEEEEEE
======================================================================
ERROR: test_every_prefix_has_one_explicit_crash_recovery_disposition (__main__.QemuRuntimeJournalP4Tests.test_every_prefix_has_one_explicit_crash_recovery_disposition)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_intent_pregenerates_unique_authority_and_distinguishes_execution (__main__.QemuRuntimeJournalP4Tests.test_intent_pregenerates_unique_authority_and_distinguishes_execution)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_observed_process_access_is_typed_restricted_and_copy_safe (__main__.QemuRuntimeJournalP4Tests.test_observed_process_access_is_typed_restricted_and_copy_safe)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_pidfile_and_qmp_socket_accessors_are_unlink_safe_and_frozen (__main__.QemuRuntimeJournalP4Tests.test_pidfile_and_qmp_socket_accessors_are_unlink_safe_and_frozen)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_real_registry_consumes_writes_and_replays_full_qmp_evidence (__main__.QemuRuntimeJournalP4Tests.test_real_registry_consumes_writes_and_replays_full_qmp_evidence)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_replay_rejects_tampered_full_qmp_evidence_even_with_new_hash (__main__.QemuRuntimeJournalP4Tests.test_replay_rejects_tampered_full_qmp_evidence_even_with_new_hash)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_reused_ids_command_drift_and_unproven_terminal_failure_reject (__main__.QemuRuntimeJournalP4Tests.test_reused_ids_command_drift_and_unproven_terminal_failure_reject)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

======================================================================
ERROR: test_skipped_reused_out_of_order_and_incoherent_facts_fail_closed (__main__.QemuRuntimeJournalP4Tests.test_skipped_reused_out_of_order_and_incoherent_facts_fail_closed)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 516, in setUp
    self.authority = _Authority(self.root)
                     ^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_qemu_runtime_journal_p4.py", line 265, in __init__
    self.intent = RuntimeLaunchIntent.from_runtime(
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: RuntimeLaunchIntent.from_runtime() missing 1 required keyword-only argument: 'executed_argv'

----------------------------------------------------------------------
Ran 8 tests in 0.031s

FAILED (errors=8)

- `qemu_runtime_p4`: pass — proved pidfd liveness, bounded pidfile readiness, and no-replace runtime-path retirement
- `daemon_runtime_p4`: pass — proved internal runtime reconciliation precedes serving while public lifecycle remains absent
- `plan_contract`: pass — validated 405 unique execution IDs across phases P0-P18
- `plan_index`: pass — validated live plan index with 1945 direct observations
- `plan_index_rejections`: pass — rejected 8 hostile plan fixtures: duplicate-json-key, missing-dependency, dependency-cycle, missing-gate, multiple-current-phases, completed-without-evidence, owner-path-escape, duplicate-plan-id
- `source_manifest`: pass — verified 33/33 preserved source files against their byte hashes

## What was surprising

I intentionally accept an unhealthy doctor report on hosts without QEMU. The test passes when the doctor names those blockers accurately. QEMU launch, QMP identity, and public lifecycle mutation remain withheld until their physical gates pass.
