# VM Lab Context Index

**Current promoted snapshot:** [`v0.1`](SNAPSHOT.md)
**Closed composition:** P1 protocol, P2 local daemon/registry, and P3 verified
image/storage authority
**Next phase:** P4 QEMU process ownership and QMP truth; see
[`TASK.md`](TASK.md) and [`STATE.md`](STATE.md).
**Source of current truth:** live source, direct proof, then this index.

This is a structured current-state index, not a second plan. Use
[`filetree.md`](filetree.md) to navigate and [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md)
for exact source anchors.

---

## Live boundary

VM Lab is a persistent-Somnus-AIPC reference boundary with these currently
promoted capabilities:

- **Pure shared protocol:** `src/somnus_protocol/` is the canonical,
  standard-library-only authority for version negotiation, strict validation,
  VM lifecycle records/transitions/migration, guest-agent result shapes,
  control envelopes, error envelopes, and ordered events. Importing it performs
  no host, guest, operator, filesystem, socket, process, or secret work.
- **Public protocol root:** supported values and
  `ProtocolValidationError` are root-public through an exact `__all__`; a
  consumer does not import the private validation module.
- **Compatibility consumption:** `src/somnus_vm/contracts/` re-exports protocol
  types for established imports and owns no independent schema.
- **Host CLI:** `doctor`, `topology`, and non-mutating `plan` only.
- **Host planning:** strict configuration, ephemeral port availability checks,
  and deterministic shell-free QEMU argv.
- **Internal P2 control authority:** bounded AF_UNIX framing, Linux
  `SO_PEERCRED`, a private daemon listener/client, one executable composition
  root, a schema-v3 SQLite registry, serialized mutation, idempotency,
  checkpoints, and deterministic recovery.
- **Internal P3 image/storage authority:** manifest-bound `qemu-img`
  observation, one immutable base slot, independently owned sparse overlays,
  crash-safe publication/reconciliation, and a native parent-death guard.
- **Guest bootstrap:** a separate local-only, one-read hash/size-bounded payload
  installer with hostile archive checks and atomic placement.
- **Current proof:** direct P1/P2/P3 modules plus the 26/26 aggregate integration
  and installed-wheel bundle at
  `test/vm_lab/runs/20260805T211740Z/`.

Public host lifecycle mutation remains absent by design. P1 defines its strict
evidence grammar; P2 durably owns local control and registry state; P3 creates
and reconciles only the verified base/overlay storage declared by that owner.
No current public command starts or stops a VM, and no closed gate launches
QEMU, mounts an image, queries QMP, deploys a resident runtime, or claims guest
readiness.

```mermaid
flowchart TD
    CLI["Public CLI: doctor / topology / plan"] --> PLAN["non-mutating host planner"]
    CLIENT["Internal local client"] --> TRANSPORT["AF_UNIX + SO_PEERCRED"]
    TRANSPORT --> DAEMON["daemon shell + daemon_runtime"]
    DAEMON --> REG["SQLite registry + mutation service"]
    DAEMON --> STORE["qemu-img provenance + persistent storage"]
    PLAN --> COMPAT["contracts compatibility re-exports"]
    REG --> COMPAT
    STORE --> REG
    COMPAT --> PROTO["src/somnus_protocol: canonical pure contracts"]
    BOOT["Guest bootstrap entrypoint"] --> GP["verified local payload placement"]
    CAND["components"] -. donor only .-> LIVE["named promotion gate"]
    COLD["extras"] -. explicit adapter only .-> LIVE
    LINE["archive + docs/lineage"] -. historical evidence .-> CAND
    QUAR["quarantine"] -. never import .-> LINE
```

| Plane | Current authority | Later physical handoff |
| --- | --- | --- |
| Shared protocol | versioned pure data/validation membrane | used by host, guest, Kerminal, and parity clients |
| Public host | read-only doctor/topology and non-mutating plan | lifecycle actions only after their physical gates |
| Internal host | one AF_UNIX daemon, schema-v3 registry/service, durable leases, and verified base/overlay storage | P4 QEMU process ownership and QMP observation |
| Guest | verified payload bootstrap | authenticated agent plus separately promoted runtime |
| Operator | internal control client plus preserved candidate advanced shell | Kerminal consumer; never QEMU owner |
| Artifact processing | cold external code | disposable worker plus recorded ingress manifest |
| VM-Go | separate project | measured behavior/protocol compatibility |

---

## P1 protocol composition, current truth

`src/somnus_protocol/` is now the live canonical protocol package:

| Owner | Current responsibility |
| --- | --- |
| `_validation.py` | strict exact types, closed objects, bounded JSON, immutable mappings, canonical JSON; `ProtocolValidationError` is exported by the public root |
| `version.py` | semantic versions; exact same-ID pairwise offer/transcript selection; independent current-package range and schema enforcement |
| `vm.py` | immutable definition/ports/resources/storage/image/process/snapshot/observation values; 16 states and exactly 48 evidence-keyed edges; intent/identity binding; replay-resistant history; secret-free record; exact DECLARED-only legacy v0 migration |
| `agent.py` | seven fixed endpoints and bounded `AgentHealth`, `CommandResult`, and `FileWriteResult` contracts |
| `control.py` | correlated requests/successes carrying operation-owned machine bags; registry-derived public failures with rigid diagnostic references |
| `events.py` | ordered control/lifecycle event envelope |

The protocol has explicit schema and protocol versions, rejects unknown fields
and coercion, accepts only bounded serializable values, and must not carry raw
credentials. Pairwise negotiation commits both explicit range offers and the
highest shared version, but its fingerprints detect mismatch rather than
authenticate a peer. P2 authenticates its local Unix peers with kernel
credentials and persists idempotency/replay state in SQLite; remote
cryptographic authentication remains outside the local-only boundary. Every
ordinary decoded envelope still must fit the current package's supported range
and exact schema.

`VMRecord` binds every transition to VM ID, generation, boot/process identity,
and an `intent_digest` covering declaration, planned runtime, and migration
provenance. Evidence, observation, boot, process, and error-cause identities
cannot be replayed across history. A subsequent authoritative observation of
the active process core must have a strictly later process-observation time;
equal-time reuse is rejected as false freshness. Historical v0 VM records are
migration input, not proof of their weaker `starting`/`running`/`ready` labels.
Only an exact historyless `DECLARED` record migrates; its raw token is removed,
its `legacy_v0` source and reprovision note are retained, and every stronger
legacy state is rejected for later reconciliation.

Control request `payload` and successful response `result` bags are bounded,
canonical, authenticated machine data, not public or log-safe projections.
Their operation-specific schema owns meaning and redaction. Public errors take
the opposite approach: code, category, exit class, detail, retryability,
remediation, evidence kind, and evidence summary all come from closed
registries; caller-controlled prose is absent, P1 `details` is empty, and raw
diagnostics remain behind `diag:<non-nil UUID>` identifiers. The future private
diagnostic store owns resolution and authorization; possession of the
identifier proves neither.

`src/somnus_vm/host/planner.py` now consumes the canonical secret-free immutable
record through compatibility imports. It is still a preview: ports are released,
and returned plan locations do not constitute a durable lifecycle registry.

### Current P1 direct proof owners

- `test/vm_lab/test_protocol_vm_primitives.py` — strict VM definition/ports.
- `test/vm_lab/test_protocol_agent.py` — agent endpoints/results.
- `test/vm_lab/test_protocol_control.py` — validation, pairwise negotiation,
  current-package enforcement, request/response machine bags, closed
  errors/diagnostic references, events, public exports, and cold import.
- `test/vm_lab/test_protocol_settings.py` — typed settings and profile roots.
- `test/vm_lab/test_protocol_host_consumption.py` — planner/CLI canonical-record
  consumption and no protocol secrets.
- `test/vm_lab/test_protocol_vm_lifecycle.py` — lifecycle values, all legal-edge
  evidence, intent/identity binding, equal/stale process-observation rejection,
  replay resistance, terminal/re-generation behavior, strict decode, and exact
  DECLARED-only v0 migration.
- `test/vm_lab/test_vm_lab.py` — aggregates the direct protocol checks with
  CLI/config/plan/bootstrap/doctor/wheel/document/index/source proof.

These are consumed-boundary contract and packaging checks. They do not claim a
physical VM lifecycle gate; those gates remain in [`PLAN.md`](PLAN.md).

---

## P2/P3 host composition, current truth

The public host facade still exports only the non-mutating planner. The live
internal owner graph is:

| Owner | Closed responsibility |
| --- | --- |
| [`transport.py`](src/somnus_vm/transport.py) | bounded AF_UNIX frames and Linux peer credentials |
| [`client.py`](src/somnus_vm/client.py) | one-request identity-matched local control client |
| [`daemon.py`](src/somnus_vm/daemon.py) | private listener, exclusive endpoint ownership, authenticated bounded dispatch |
| [`daemon_runtime.py`](src/somnus_vm/daemon_runtime.py) | single composition root for daemon, registry, mutation service, and storage |
| [`registry.py`](src/somnus_vm/host/registry.py) | single-writer schema-v3 SQLite records, resources, journal, migration, and recovery state |
| [`service.py`](src/somnus_vm/host/service.py) | serialized authenticated metadata/storage mutations, checkpoints, idempotency, and replay/recovery |
| [`images.py`](src/somnus_vm/host/images.py) | strict manifests and shell-free `qemu-img` provenance/inspection primitives |
| [`storage.py`](src/somnus_vm/host/storage.py) | immutable-base and sparse-overlay publication, ownership markers, adoption rejection, and reconciliation |
| [`exec_guard.py`](src/somnus_vm/host/exec_guard.py) | Linux parent-death containment for daemon-owned native image subprocesses |

### Current P2/P3 direct proof owners

- [`test_daemon_transport_p2.py`](test/vm_lab/test_daemon_transport_p2.py),
  [`test_registry_p2.py`](test/vm_lab/test_registry_p2.py),
  [`test_registry_service_p2.py`](test/vm_lab/test_registry_service_p2.py), and
  [`test_daemon_registry_p2.py`](test/vm_lab/test_daemon_registry_p2.py) — real Unix peers, exclusive writer
  ownership, schema/migration/integrity, concurrent processes, abrupt exit, and
  checkpoint recovery.
- [`test_storage_p3.py`](test/vm_lab/test_storage_p3.py) — six core cases including strict manifest
  authority, two independent sparse 100 GiB overlays, schema-v2-to-v3 storage
  migration, and seven base plus seven overlay kill checkpoints.
- [`test_storage_p3_adversarial.py`](test/vm_lab/test_storage_p3_adversarial.py) — eight adversarial cases covering
  foreign-image non-adoption, alias/tamper rejection, and native child
  parent-death containment.
- [`test_daemon_storage_p3.py`](test/vm_lab/test_daemon_storage_p3.py) — one real daemon/client storage
  composition and startup physical-reconciliation case.
- [`ubuntu-minimal-noble-amd64-20260801.json`](test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json) — pinned
  qemu-img 8.2.2 fixture manifest for SHA-256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.
- [`20260805T211740Z`](test/vm_lab/runs/20260805T211740Z/) — sealed 26/26 aggregate bundle with zero
  failures and zero skips.

The focused P3 total is 15 tests: six core, eight adversarial, and one
real-daemon case. These prove internal local-control, registry, image, storage,
and recovery authority. They explicitly do not prove a QEMU launch, mount, QMP
identity, guest readiness, deployment, or public lifecycle.

---

## Foundational intent and two-stage AIPC delivery

[`Somnus-Core-Ideals.md`](Somnus-Core-Ideals.md) is the operator-supplied
foundational architecture source. It is governing product intent, not passive
lineage. Current code and physical evidence determine what is implemented; they
do not demote the intended architecture. Durable constraints include:

- an AIPC is a persistent full virtual computer, not a disposable execution
  container;
- durable identity, installed capabilities, learned state, model weights, and
  model-adjacent memory reside with the VM;
- disposable containers/Artifacts absorb temporary UI, research, build, and
  file-processing work, returning only approved outputs through an ingress;
- host lifecycle, guest control/runtime, operator agency, and Artifact work are
  separate authorities;
- the in-VM agent supplies authenticated control that host PID/port/QMP facts
  alone cannot infer.

Delivery stays two-stage:

1. **Install/provision the computer.** Image provenance, qcow2 ownership,
   first boot, and service activation establish the persistent AIPC. This is
   not repeated for normal runtime changes.
2. **Deploy and operate its resident runtime.** Versioned in-place guest
   payloads, weights/harness, memory, authenticated control, and optional
   modules evolve inside the established computer subject to their future gates.

The target pairs one persistent qcow2 with one resident model. No P1 code
claims exact weight-hash immutability or deployment semantics beyond that
intent; those require their own manifest/binding authority. The immediate
architecture is explicitly plural, not a one-model-does-everything claim:

```text
resident model + Advanced Kerminal/model harness
    -> ASPS cognitive workspace <-> memory core + memory interconnect

ACE: separate OS-agency pillar (filesystem, login, operational literacy)
system cache: parallel result/cache infrastructure
digital twin: operations continuity
advanced shell: human + narrower model Master Control Panel
```

ACE is not a mediator between the two memory owners and cache is not model-near
cognition. `components/guest/runtime/` and `components/operator/` preserve the
relevant source as candidates. `components/operator/ai_advanced_shell.py` is
high-value Kerminal lineage but cannot acquire host lifecycle ownership;
`internal_browser.py`, `web_search_research.py`, and git integration remain
distinct candidate tool lanes until actual consumer evidence calls for adapters.

The configurable capability membrane, credential transition, resident deployment
manifest, Artifact ingress result format, and remote full-parity boundary remain
open design work. They are recorded as questions, not silently automated
behavior. Use [`NOTEPAD.md`](NOTEPAD.md) for active observations and move a
resolved decision into its named contract/plan/ADR owner.

---

## Physical gate boundary

The repository must not expose lifecycle merely because QEMU argv or protocol
types exist. P2 and P3 now establish one long-lived mutation owner, a
transactional registry with durable leases, and owned qcow2 base/overlay
storage with verified backing lineage. P4 and later still require:

- actual QEMU launch and process identity;
- QMP greeting, capabilities, UUID, and status;
- authenticated guest readiness tied to VM/boot/generation identity;
- graceful shutdown, crash adoption, and restart reconciliation;
- safe snapshot/rollback with measured hashes;
- only then, externally observed lifecycle commands through the control API.

The exact phase/gate order is [`PLAN.md`](PLAN.md). Closed P2/P3 authority must
not be inflated into satisfaction of P4 QEMU/QMP, later guest, snapshot, or
public lifecycle gates.

---

## Navigation and update rule

- [`AGENTS.md`](AGENTS.md) — execution kernel and authority.
- [`filetree.md`](filetree.md) — semantic dependency/navigation map.
- [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) — question-to-owner anchors.
- [`TOPOLOGY.md`](TOPOLOGY.md) — load-bearing invariants and anti-concepts.
- [`FAILURE_GRAMMAR.md`](FAILURE_GRAMMAR.md) — false-success recognition.
- [`TASK.md`](TASK.md), [`STATE.md`](STATE.md), and
  [`docs/plan-index.json`](docs/plan-index.json) — active unit and gates.
- [`tree-codebase.md`](tree-codebase.md) — point-in-time file inventory only.

Update this index when promoted authority, actual consumption, proof boundary,
or physical boundary changes. Do not use it to mark phases or gates complete;
that authority remains with the active task, plan index, and evidence owners.
