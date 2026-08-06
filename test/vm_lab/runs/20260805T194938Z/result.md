# VM Lab Integration Run

I ran 23 checks against the promoted non-mutating boundary and guest bootstrap.
I observed 23 passes, 0 failures, and 0 skips.

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
- `qemu_plan`: pass — encoded a comma-bearing disk path with JSON blockdev and a valid QMP path
- `bootstrap_extraction`: pass — extracted bounded data and rejected traversal, links, duplicates, and size drift
- `bootstrap_idempotency`: pass — installed/recovered bounded payloads and rejected stale, ambiguous, mislabeled, or colliding intent
- `doctor_truthfulness`: pass — reported 9 source-aware checks with healthy=False
- `cli_entrypoint`: pass — ran checkout-independent topology, redacted planning, and clean port-exhaustion errors
- `wheel_runtime`: pass — installed a wheel with canonical/compatibility protocol identity, checkout-free CLI, the registry-only daemon entry point, and clean malformed-payload exit behavior
- `daemon_transport_p2`: pass — proved bounded SO_PEERCRED Unix transport and exclusive daemon ownership
- `registry_p2`: pass — proved normalized SQLite ownership, integrity, migration, and writer locking
- `registry_service_p2`: pass — proved strict metadata operations and abrupt writer-exit recovery
- `daemon_registry_p2`: pass — proved 24-process operation races and real SIGKILL recovery at every checkpoint
- `plan_contract`: pass — validated 405 unique execution IDs across phases P0-P18
- `plan_index`: pass — validated live plan index with 1808 direct observations
- `plan_index_rejections`: pass — rejected 8 hostile plan fixtures: duplicate-json-key, missing-dependency, dependency-cycle, missing-gate, multiple-current-phases, completed-without-evidence, owner-path-escape, duplicate-plan-id
- `source_manifest`: pass — verified 33/33 preserved source files against their byte hashes

## What was surprising

I intentionally accept an unhealthy doctor report on hosts without QEMU. The test passes when the doctor names those blockers accurately. Lifecycle mutation is withheld until a real-metal QMP gate exists.
