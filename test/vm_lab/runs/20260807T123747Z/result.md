# VM Lab Integration Run

I ran 40 checks against the public read-only CLI, internal daemon/registry/storage boundary, and guest bootstrap.
I observed 40 passes, 0 failures, and 0 skips.

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
- `doctor_truthfulness`: pass — reported 10 source-aware checks with healthy=False
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
- `doctor_p4`: pass — proved read-only QEMU sandbox option consumption and structured incompatibility evidence
- `disposable_launch_authority_p4`: pass — proved one-shot non-production fixture authority and canonical launch receipt without launching QEMU
- `p4_gate_coordinator`: pass — proved exact physical-gate preparation and pre-daemon denial without launching QEMU
- `qemu_process_p4`: pass — proved exact Linux process identity, pidfiles, and signal revalidation
- `qemu_exec_guard_p4`: pass — proved journal-before-exec handoff, parent-death containment, and exact adoption
- `qemu_logs_p4`: pass — proved private redaction, bounded rotation, dual-pipe drain, and durable guardian release
- `qmp_p4`: pass — proved bounded QMP framing, negotiation, response/event separation, and identity
- `qmp_identity_p4`: pass — proved strict stabilized QMP identity composition and foreign-process rejection
- `registry_p4`: pass — proved registry-owned P4 process and disk-runtime observation authority
- `storage_runtime_p4`: pass — proved real qcow2 growth remains append-only runtime evidence without rewriting P3 ownership
- `qemu_runtime_journal_p4`: pass — proved the internal runtime.launch journal, exact identity binding, and crash-safe adoption
- `qemu_runtime_p4`: pass — proved pidfd liveness, bounded pidfile readiness, and no-replace runtime-path retirement
- `daemon_runtime_p4`: pass — proved internal runtime reconciliation precedes serving while public lifecycle remains absent
- `plan_contract`: pass — validated 405 unique execution IDs across phases P0-P18
- `plan_index`: pass — validated live plan index with 2040 direct observations
- `plan_index_rejections`: pass — rejected 8 hostile plan fixtures: duplicate-json-key, missing-dependency, dependency-cycle, missing-gate, multiple-current-phases, completed-without-evidence, owner-path-escape, duplicate-plan-id
- `source_manifest`: pass — verified 33/33 preserved source files against their byte hashes

## What was surprising

I intentionally accept an unhealthy doctor report on hosts without QEMU. The test passes when the doctor names those blockers accurately. QEMU launch, QMP identity, and public lifecycle mutation remain withheld until their physical gates pass.
