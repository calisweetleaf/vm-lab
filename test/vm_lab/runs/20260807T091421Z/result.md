# VM Lab Integration Run

I ran 37 checks against the public read-only CLI, internal daemon/registry/storage boundary, and guest bootstrap.
I observed 33 passes, 4 failures, and 0 skips.

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
- `storage_p3`: fail — AssertionError: test_storage_p3.py failed
stdout:

stderr:
test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) ... FAIL
test_manifest_and_real_backend_reject_ambiguous_authority (__main__.StorageP3Tests.test_manifest_and_real_backend_reject_ambiguous_authority) ... FAIL
test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) ... FAIL
test_service_imports_one_base_and_materializes_two_sparse_overlays (__main__.StorageP3Tests.test_service_imports_one_base_and_materializes_two_sparse_overlays) ... FAIL
test_symlink_and_hardlink_aliases_never_gain_storage_authority (__main__.StorageP3Tests.test_symlink_and_hardlink_aliases_never_gain_storage_authority) ... FAIL
test_v2_registry_migrates_to_current_with_storage_constraints (__main__.StorageP3Tests.test_v2_registry_migrates_to_current_with_storage_constraints) ... FAIL

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 750, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_manifest_and_real_backend_reject_ambiguous_authority (__main__.StorageP3Tests.test_manifest_and_real_backend_reject_ambiguous_authority)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 750, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 750, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_service_imports_one_base_and_materializes_two_sparse_overlays (__main__.StorageP3Tests.test_service_imports_one_base_and_materializes_two_sparse_overlays)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 750, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_symlink_and_hardlink_aliases_never_gain_storage_authority (__main__.StorageP3Tests.test_symlink_and_hardlink_aliases_never_gain_storage_authority)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 750, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_v2_registry_migrates_to_current_with_storage_constraints (__main__.StorageP3Tests.test_v2_registry_migrates_to_current_with_storage_constraints)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 750, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

----------------------------------------------------------------------
Ran 6 tests in 0.001s

FAILED (failures=6)

- `storage_p3_adversarial`: fail — AssertionError: test_storage_p3_adversarial.py failed
stdout:

stderr:
test_foreign_final_base_without_manifest_is_not_adopted (__main__.StorageP3AdversarialTests.test_foreign_final_base_without_manifest_is_not_adopted) ... FAIL
test_foreign_final_overlay_without_marker_is_not_adopted (__main__.StorageP3AdversarialTests.test_foreign_final_overlay_without_marker_is_not_adopted) ... FAIL
test_missing_checkpointed_base_candidate_is_not_recreated (__main__.StorageP3AdversarialTests.test_missing_checkpointed_base_candidate_is_not_recreated) ... FAIL
test_parent_death_guard_kills_live_native_child (__main__.StorageP3AdversarialTests.test_parent_death_guard_kills_live_native_child) ... FAIL
test_post_publication_sqlite_conflict_is_never_terminal_failure (__main__.StorageP3AdversarialTests.test_post_publication_sqlite_conflict_is_never_terminal_failure) ... FAIL
test_replaced_checkpointed_overlay_candidate_is_not_adopted (__main__.StorageP3AdversarialTests.test_replaced_checkpointed_overlay_candidate_is_not_adopted) ... FAIL
test_same_bytes_new_inode_registered_base_blocks_use_and_startup (__main__.StorageP3AdversarialTests.test_same_bytes_new_inode_registered_base_blocks_use_and_startup) ... FAIL
test_stage_symlink_rejection_does_not_chmod_victim (__main__.StorageP3AdversarialTests.test_stage_symlink_rejection_does_not_chmod_victim) ... FAIL

======================================================================
FAIL: test_foreign_final_base_without_manifest_is_not_adopted (__main__.StorageP3AdversarialTests.test_foreign_final_base_without_manifest_is_not_adopted)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_foreign_final_overlay_without_marker_is_not_adopted (__main__.StorageP3AdversarialTests.test_foreign_final_overlay_without_marker_is_not_adopted)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_missing_checkpointed_base_candidate_is_not_recreated (__main__.StorageP3AdversarialTests.test_missing_checkpointed_base_candidate_is_not_recreated)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_parent_death_guard_kills_live_native_child (__main__.StorageP3AdversarialTests.test_parent_death_guard_kills_live_native_child)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_post_publication_sqlite_conflict_is_never_terminal_failure (__main__.StorageP3AdversarialTests.test_post_publication_sqlite_conflict_is_never_terminal_failure)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_replaced_checkpointed_overlay_candidate_is_not_adopted (__main__.StorageP3AdversarialTests.test_replaced_checkpointed_overlay_candidate_is_not_adopted)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_same_bytes_new_inode_registered_base_blocks_use_and_startup (__main__.StorageP3AdversarialTests.test_same_bytes_new_inode_registered_base_blocks_use_and_startup)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

======================================================================
FAIL: test_stage_symlink_rejection_does_not_chmod_victim (__main__.StorageP3AdversarialTests.test_stage_symlink_rejection_does_not_chmod_victim)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3_adversarial.py", line 173, in setUp
    raise AssertionError("the required P3 fixture or manifest is absent")
AssertionError: the required P3 fixture or manifest is absent

----------------------------------------------------------------------
Ran 8 tests in 0.002s

FAILED (failures=8)

- `daemon_storage_p3`: fail — AssertionError: test_daemon_storage_p3.py failed
stdout:

stderr:
test_real_daemon_materializes_and_reconciles_two_overlays (__main__.DaemonStorageP3Gate.test_real_daemon_materializes_and_reconciles_two_overlays) ... FAIL

======================================================================
FAIL: test_real_daemon_materializes_and_reconciles_two_overlays (__main__.DaemonStorageP3Gate.test_real_daemon_materializes_and_reconciles_two_overlays)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_daemon_storage_p3.py", line 195, in test_real_daemon_materializes_and_reconciles_two_overlays
    self.assertTrue(FIXTURE_SOURCE.is_file())
AssertionError: False is not true

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (failures=1)

- `qemu_planning_p4`: pass — proved hardened QEMU planning through builder, planner, and public CLI
- `qemu_process_p4`: pass — proved exact Linux process identity, pidfiles, and signal revalidation
- `qemu_exec_guard_p4`: pass — proved journal-before-exec handoff, parent-death containment, and exact adoption
- `qemu_logs_p4`: pass — proved private redaction, bounded rotation, dual-pipe drain, and durable guardian release
- `qmp_p4`: pass — proved bounded QMP framing, negotiation, response/event separation, and identity
- `qmp_identity_p4`: pass — proved strict stabilized QMP identity composition and foreign-process rejection
- `registry_p4`: pass — proved registry-owned P4 process and disk-runtime observation authority
- `storage_runtime_p4`: fail — AssertionError: test_storage_runtime_p4.py failed
stdout:

stderr:
test_runtime_growth_is_append_only_but_structure_stays_exact (__main__.StorageRuntimeP4Tests.test_runtime_growth_is_append_only_but_structure_stays_exact) ... FAIL

======================================================================
FAIL: test_runtime_growth_is_append_only_but_structure_stays_exact (__main__.StorageRuntimeP4Tests.test_runtime_growth_is_append_only_but_structure_stays_exact)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_runtime_p4.py", line 63, in setUp
    raise AssertionError("the pinned P3 fixture authority is absent")
AssertionError: the pinned P3 fixture authority is absent

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (failures=1)

- `qemu_runtime_journal_p4`: pass — proved the internal runtime.launch journal, exact identity binding, and crash-safe adoption
- `qemu_runtime_p4`: pass — proved pidfd liveness, bounded pidfile readiness, and no-replace runtime-path retirement
- `daemon_runtime_p4`: pass — proved internal runtime reconciliation precedes serving while public lifecycle remains absent
- `plan_contract`: pass — validated 405 unique execution IDs across phases P0-P18
- `plan_index`: pass — validated live plan index with 1945 direct observations
- `plan_index_rejections`: pass — rejected 8 hostile plan fixtures: duplicate-json-key, missing-dependency, dependency-cycle, missing-gate, multiple-current-phases, completed-without-evidence, owner-path-escape, duplicate-plan-id
- `source_manifest`: pass — verified 33/33 preserved source files against their byte hashes

## What was surprising

I intentionally accept an unhealthy doctor report on hosts without QEMU. The test passes when the doctor names those blockers accurately. QEMU launch, QMP identity, and public lifecycle mutation remain withheld until their physical gates pass.
