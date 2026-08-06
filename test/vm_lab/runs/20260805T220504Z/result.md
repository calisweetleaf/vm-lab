# VM Lab Integration Run

I ran 30 checks against the public read-only CLI, internal daemon/registry/storage boundary, and guest bootstrap.
I observed 29 passes, 1 failures, and 0 skips.

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
test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) ... 
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='intent_persisted') ... FAIL
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_stage_prepared') ... FAIL
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_bytes_staged') ... FAIL
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_verified') ... FAIL
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_published') ... FAIL
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_registered') ... FAIL
  test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='completion_observed') ... FAIL
test_manifest_and_real_backend_reject_ambiguous_authority (__main__.StorageP3Tests.test_manifest_and_real_backend_reject_ambiguous_authority) ... FAIL
test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) ... 
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='intent_persisted') ... FAIL
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_stage_prepared') ... FAIL
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_created') ... FAIL
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_verified') ... FAIL
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_published') ... FAIL
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='disk_materialized') ... FAIL
  test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='completion_observed') ... FAIL
test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) ... FAIL
test_service_imports_one_base_and_materializes_two_sparse_overlays (__main__.StorageP3Tests.test_service_imports_one_base_and_materializes_two_sparse_overlays) ... FAIL
test_symlink_and_hardlink_aliases_never_gain_storage_authority (__main__.StorageP3Tests.test_symlink_and_hardlink_aliases_never_gain_storage_authority) ... FAIL
test_v2_registry_migrates_to_v3_with_storage_constraints (__main__.StorageP3Tests.test_v2_registry_migrates_to_v3_with_storage_constraints) ... ERROR

======================================================================
ERROR: test_v2_registry_migrates_to_v3_with_storage_constraints (__main__.StorageP3Tests.test_v2_registry_migrates_to_v3_with_storage_constraints)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 791, in test_v2_registry_migrates_to_v3_with_storage_constraints
    vm_id, disk_id = _create_v2_registry(self.root, database)
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 485, in _create_v2_registry
    connection.execute("ALTER TABLE disks_v2 RENAME TO disks")
sqlite3.OperationalError: error in trigger disk_runtime_observation_owner_insert: no such table: main.disks

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='intent_persisted')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_stage_prepared')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_bytes_staged')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_verified')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_published')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='base_registered')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_base_import_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_base_import_recovers_after_every_persisted_checkpoint) (checkpoint='completion_observed')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1114, in test_base_import_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_manifest_and_real_backend_reject_ambiguous_authority (__main__.StorageP3Tests.test_manifest_and_real_backend_reject_ambiguous_authority)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 787, in test_manifest_and_real_backend_reject_ambiguous_authority
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='intent_persisted')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_stage_prepared')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_created')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_verified')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='overlay_published')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='disk_materialized')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint) (checkpoint='completion_observed')
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1189, in test_overlay_recovers_after_every_persisted_checkpoint
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_overlay_recovers_after_every_persisted_checkpoint (__main__.StorageP3Tests.test_overlay_recovers_after_every_persisted_checkpoint)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1213, in test_overlay_recovers_after_every_persisted_checkpoint
    self.assertEqual(len(recovered_disks), len(OVERLAY_CHECKPOINTS))
AssertionError: 0 != 7

======================================================================
FAIL: test_service_imports_one_base_and_materializes_two_sparse_overlays (__main__.StorageP3Tests.test_service_imports_one_base_and_materializes_two_sparse_overlays)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 963, in test_service_imports_one_base_and_materializes_two_sparse_overlays
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

======================================================================
FAIL: test_symlink_and_hardlink_aliases_never_gain_storage_authority (__main__.StorageP3Tests.test_symlink_and_hardlink_aliases_never_gain_storage_authority)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 1078, in test_symlink_and_hardlink_aliases_never_gain_storage_authority
    _assert_schema_v3(database, registry)
  File "/home/daeron/Repositories/vm-lab/test/vm_lab/test_storage_p3.py", line 407, in _assert_schema_v3
    raise AssertionError("registry is not writable schema version 3")
AssertionError: registry is not writable schema version 3

----------------------------------------------------------------------
Ran 6 tests in 30.763s

FAILED (failures=18, errors=1)

- `storage_p3_adversarial`: pass — proved foreign-image non-adoption, checkpoint tamper rejection, and parent-death containment
- `daemon_storage_p3`: pass — proved real daemon/client storage composition and startup physical reconciliation
- `qemu_planning_p4`: pass — proved hardened QEMU planning through builder, planner, and public CLI
- `qemu_process_p4`: pass — proved exact Linux process identity, pidfiles, and signal revalidation
- `qmp_p4`: pass — proved bounded QMP framing, negotiation, response/event separation, and identity
- `registry_p4`: pass — proved registry-owned P4 process and disk-runtime observation authority
- `plan_contract`: pass — validated 405 unique execution IDs across phases P0-P18
- `plan_index`: pass — validated live plan index with 1945 direct observations
- `plan_index_rejections`: pass — rejected 8 hostile plan fixtures: duplicate-json-key, missing-dependency, dependency-cycle, missing-gate, multiple-current-phases, completed-without-evidence, owner-path-escape, duplicate-plan-id
- `source_manifest`: pass — verified 33/33 preserved source files against their byte hashes

## What was surprising

I intentionally accept an unhealthy doctor report on hosts without QEMU. The test passes when the doctor names those blockers accurately. QEMU launch, QMP identity, and public lifecycle mutation remain withheld until their physical gates pass.
