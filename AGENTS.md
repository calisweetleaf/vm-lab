# VM Lab Operator Kernel

> **Repository:** `calisweetleaf/vm-lab`
> **Latest sealed snapshot:** [`v0.1`](SNAPSHOT.md)
> **Working runtime status:** P1/P2/P3 closed: canonical protocol, private
> single-owner daemon/registry, verified qcow2 base/overlay storage, read-only
> public host planning, and separate guest bootstrap. P4 now has a daemon-wired
> internal QEMU/QMP runtime owner, one-shot disposable launch authority, a
> fail-closed non-public physical-gate coordinator, and direct non-launch proof
> modules. All eighteen bounded phase items are implemented. Current published
> aggregate [`20260905T101913Z`](test/vm_lab/runs/20260905T101913Z/) is 36 pass /
> 4 fail because that host lacked the exact fixture. Historical 40/0/0 bundle
> [`20260807T123747Z`](test/vm_lab/runs/20260807T123747Z/) and matrix
> `b9aca76a-a016-4d0c-9002-f0a90f383b21` remain provenance, not current machine
> truth. Active unit: [`TASK-P4-019`](TASK.md) / RECOVERY-R0 (`BASE-017`,
> `BASE-018`). `GATE-P4` remains open.
> The public host boundary remains non-mutating.
> **Canonical active work:** [`TASK.md`](TASK.md)
> **Long-range execution authority:** [`PLAN.md`](PLAN.md)

This file is the repository entry packet. It is intentionally both an operating
kernel and a navigation surface: read it first, then follow only the links for
the task in front of you. Do not bulk-read the repository or assume that a file
is live because it exists.

---

## 1. Outcome lock

For every implementation or diagnosis, keep one internal sentence:

> **Deliver `<result>`; done when `<nearest consumed-boundary evidence>`.**

Retain it until the result is verified or Daeron explicitly replaces it.

- A plan, audit, backup, route, tool response, clean diff, or test count is not
  the requested implementation.
- A correction invalidates every dependent assumption immediately.
- Continue safe, reversible, in-scope work without asking.
- Ask only at a real authority boundary: destructive external state, secrets,
  publication/deployment, or materially different architectural directions.
- Report **changed**, **verified**, and **unresolved** separately.
- Never promote intent, a PID, an open port, a socket pathname, a zero exit
  status, or a schema-valid payload as semantic success.

The repository-specific success rule is stronger:

> **No VM capability exists publicly until external machine truth proves it.**

For v0.1, the public host boundary is deliberately non-mutating. Do not add
`start`, `stop`, `destroy`, snapshot, scaling, or image-build commands until
their named physical gate in [`PLAN.md`](PLAN.md) passes.

---

## 2. Authority and evidence order

When sources disagree, use this order:

1. Daeron's current explicit instruction.
2. Observed runtime truth and the current working tree.
3. This [`AGENTS.md`](AGENTS.md).
4. The active unit in [`TASK.md`](TASK.md).
5. The complete phase and gate model in [`PLAN.md`](PLAN.md).
6. The current promotion boundary in [`SNAPSHOT.md`](SNAPSHOT.md) and
   [`snapshots/v0.1/manifest.json`](snapshots/v0.1/manifest.json).
7. Current architecture in [`TOPOLOGY.md`](TOPOLOGY.md),
   [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md), and
   [`CONTEXT.md`](CONTEXT.md).
8. Decisions and rejected paths in [`PROVENANCE.md`](PROVENANCE.md),
   [`MEMORY.md`](MEMORY.md), and
   [`docs/RECONSTITUTION_AUDIT.md`](docs/RECONSTITUTION_AUDIT.md).
9. Historical design intent under [`docs/lineage/`](docs/lineage/) and
   [`archive/`](archive/).
10. [TOPOLOGY.md]

Current code and physical observations outrank prose. Snapshot manifests are
claims about a sealed baseline, not mutable progress ledgers.

---

## 3. What this repository is

VM Lab is the experimental Python reference boundary for a persistent Somnus
AIPC: a full virtual computer whose disk, identity, guest services, state, and
recovery history must survive the host process controlling it.

The repository currently contains the following bounded live surfaces.
Promotion is closed through P3; the P4 row is internal implementation whose
physical gate remains open:

| Boundary | Live capability or implementation | Direct owner |
| --- | --- | --- |
| Shared protocol | versioned VM, agent, control, and event schemas | [`src/somnus_protocol/`](src/somnus_protocol/) |
| Host CLI | `doctor` | [`src/somnus_vm/doctor.py`](src/somnus_vm/doctor.py) |
| Host CLI | `topology` | [`src/somnus_vm/topology.py`](src/somnus_vm/topology.py) |
| Host CLI | non-mutating `plan` | [`src/somnus_vm/host/planner.py`](src/somnus_vm/host/planner.py) |
| Internal local control | bounded AF_UNIX framing/client/listener with Linux peer credentials | [`src/somnus_vm/transport.py`](src/somnus_vm/transport.py) → [`src/somnus_vm/daemon.py`](src/somnus_vm/daemon.py) |
| Internal mutation authority | schema-v3 SQLite registry, serialized metadata/storage operations, idempotency, checkpoints, and recovery | [`src/somnus_vm/daemon_runtime.py`](src/somnus_vm/daemon_runtime.py) → [`src/somnus_vm/host/registry.py`](src/somnus_vm/host/registry.py) → [`src/somnus_vm/host/service.py`](src/somnus_vm/host/service.py) |
| Internal image/storage | pinned qcow2 provenance, immutable base import, sparse overlay publication, and parent-death containment | [`src/somnus_vm/host/images.py`](src/somnus_vm/host/images.py) → [`src/somnus_vm/host/storage.py`](src/somnus_vm/host/storage.py) |
| Internal P4 runtime | daemon-wired QEMU process/QMP ownership, one-shot non-production launch permit and receipt, frozen launch journal, logs, and restart recovery | [`src/somnus_vm/daemon_runtime.py`](src/somnus_vm/daemon_runtime.py) → [`src/somnus_vm/host/launch_authority.py`](src/somnus_vm/host/launch_authority.py) → [`src/somnus_vm/host/qemu_runtime.py`](src/somnus_vm/host/qemu_runtime.py) → [`src/somnus_vm/host/qemu_runtime_journal.py`](src/somnus_vm/host/qemu_runtime_journal.py) |
| Guest CLI | local verified bootstrap | [`src/somnus_vm/guest/bootstrap.py`](src/somnus_vm/guest/bootstrap.py) |

P2 promotes a real, unprivileged, single-writer **metadata authority**, not a VM
lifecycle. Its private mode-`0600` Unix socket authenticates both ends with
Linux peer credentials; its normalized SQLite registry owns exact records,
leases, disk claims, events, snapshots, process identities, idempotency,
migrations, and checkpoints. Its P2 metadata mutation vocabulary is closed to
`registry.declare`, `registry.lease`, `registry.update`, and `registry.cancel`;
those operations allocate and release durable metadata but do not launch QEMU
or assert a lifecycle transition.

P3 extends that exact composition root with the only current physical image
mutations: `storage.import_base` and `storage.create_overlay`. A strict pinned
manifest is checked with real `qemu-img` 8.2.2; one immutable base and
independently owned sparse 100 GiB overlays are published with exact backing,
inode, marker, journal, and recovery evidence. The native exec guard contains
image subprocesses if their owner dies. P3 does not launch or mount a VM disk,
query QMP, establish guest readiness, or make storage/lifecycle operations
public through the `vm-lab` CLI.

P4 now supplies the bounded internal runtime-owner implementation: the daemon
composition constructs `QemuRuntimeOwner`, settles incomplete/completed runtime
launch records before generic storage reconciliation and listener binding, pins
the P3 overlay/base/owner-marker authority across the exec guard, and validates
the two-fdset/four-node block graph through authenticated QMP and peer-process
descriptor evidence. The same owner retains logs, exact process identity,
durable orphan-emergency state, and restart recovery. Doctor interrogates the
selected QEMU's closed sandbox option set without launching a VM.
`launch_authority.py` requires a private exact fixture topology, explicit
production exclusions, and a one-shot permit whose canonical receipt is
embedded in the launch journal before child release. `daemon_runtime` exposes
that owner only through a Python callback hook unavailable to its normal CLI or
AF_UNIX request vocabulary. `p4_gate.py` now provides the explicit preflight, authorization, activation,
full 17-scenario matrix preparation, strict serial execution/resume policy,
checkpoint/recovery validation, and sealed-result contract; `scripts/run_gate_p4.py` exposes `prepare-matrix`, `preflight-matrix`, and
`execute-matrix` as the non-public operator driver. The deterministic execution
phrase is only an accidental-execution fence, never authentication or proof of
Daeron authority. These suites are implementation/non-launch proof; they do
**not** close `GATE-P4`: the exact source is restored, but no valid execute
authority/Daeron-authorized `qemu-system-*` run, explicit production exclusions,
or checkpoint matrix has established external machine truth, and no public
lifecycle command exists.

P1 supplies the strict lifecycle **contract**: 16 observed states, 48 legal
edges, edge-specific evidence, immutable transition history, and exact identity
binding across VM ID, generation, boot ID, process identity, and declaration
intent. Evidence, observation, boot, process, and error-cause identifiers cannot
be replayed across histories, and any later authoritative observation of the
active process core must have a strictly newer process-observation timestamp
(equal time is replay, not freshness). Exact legacy-v0 migration accepts only a
historyless `DECLARED` record, records its source, removes the raw agent token,
and rejects every stronger legacy state for later reconciliation. Neither the
schema nor the closed P2/P3 daemon/storage authority operates a VM. The
following is still the future physical lifecycle that must be observed before
public actions exist:

```text
declare -> provision -> start -> QMP verified -> guest ready -> execute
-> write -> quiesce -> snapshot -> mutate -> rollback -> verify
-> graceful stop -> supervisor restart -> reconcile -> explicit destroy
```

Every arrow must become observed truth, not merely an implemented method.

### The three things you cannot get wrong

1. **Disposition is executable authority.** `src/somnus_protocol/` and
   `src/somnus_vm/` are promoted runtime. `src/somnus_protocol/` is the pure
   shared authority; `src/somnus_vm/contracts/` only re-exports it for
   compatibility. `components/`, `extras/`, `archive/`, and `quarantine/` are donor,
   cold, historical, or rejected surfaces. Moving or importing one does not
   promote it.
2. **The host/guest/operator boundary is physical.** The host owns VM lifecycle
   and QEMU/QMP; the guest owns authenticated in-guest control and cognition;
   Kerminal/operator code owns agency. No layer silently constructs another
   layer's authority.
3. **Observed truth is the success source.** QMP identity/status, authenticated
   guest readiness, measured file hashes, durable registry ownership, and
   actual process exit are evidence. Plans, ports that briefly bound, PIDs,
   logs of intended commands, and synthetic sleeps are not.

The shared protocol has two different version surfaces that must not be
collapsed. `ProtocolCapabilities` and `ProtocolNegotiation` commit two explicit
same-ID range offers to the highest shared version; their hashes detect
transcript mismatch but do not authenticate it. Ordinary decoded values still
pass the current package's `ensure_supported_protocol_version()` and exact
schema check. P2 authenticates local transport with `SO_PEERCRED` and stores
request replay/idempotency in the registry; cryptographic remote
authentication remains outside the current local-only boundary.

Public failures are also closed protocol data. Error code, category, exit class,
detail, retryability, remediation, evidence kind, and evidence summary come
from registries. Diagnostic evidence is only a `diag:<non-nil UUID>` reference
to a future private store, P1 `details` is empty, and callers cannot inject
public prose. In contrast, successful control `result` and request `payload`
bags are bounded authenticated machine data whose operation-specific consumer
owns semantics; they are **not** safe to log wholesale.

Full definitions, common misunderstandings, and verification tests:
[`TOPOLOGY.md`](TOPOLOGY.md).

---

## 4. Cold and warm entry

### Normal entry

1. Read this file.
2. Read [`TASK.md`](TASK.md) for the one active unit.
3. Use [`filetree.md`](filetree.md) to choose the owning surface.
4. Follow one task route below.
5. Inspect the exact owner and current diff before mutation.

### Warm-state check

Read [`.sovereign/session_state.json`](.sovereign/session_state.json) as a hint,
then verify [`.sovereign/topology_fingerprint.txt`](.sovereign/topology_fingerprint.txt)
against [`TOPOLOGY.md`](TOPOLOGY.md) and
[`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md). `warm_start_valid: true` is never
self-authenticating.

Run:

```bash
python scripts/verify_repository.py
```

If it passes and the active task is clear, do not repeat archaeology. If it
fails, follow the failure's named owner. If the topology fingerprint drifted,
read the latest [`PROVENANCE.md`](PROVENANCE.md) session entry and repair only
the affected topology lane.

---

## 5. Task routes

| Task | Read in this order | Direct verification |
| --- | --- | --- |
| Understand the system | [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) → relevant source | Follow file and line anchors |
| Resume active work | [`TASK.md`](TASK.md) → relevant [`PLAN.md`](PLAN.md) phase → owner | Task acceptance gate |
| Change CLI/config | [`CONTEXT.md`](CONTEXT.md) § Live boundary → `src/somnus_vm/cli.py` / `config.py` | CLI subprocess plus smoke |
| Change contracts/state | [`TOPOLOGY.md`](TOPOLOGY.md) → `src/somnus_protocol/` → affected compatibility consumer | Root export identity, round-trip, negotiation, migration, replay, and illegal-transition evidence |
| Change daemon/registry | [`docs/decisions/0001-single-mutation-owner.md`](docs/decisions/0001-single-mutation-owner.md) → [`docs/decisions/0002-sqlite-registry.md`](docs/decisions/0002-sqlite-registry.md) → [`transport.py`](src/somnus_vm/transport.py) / [`daemon.py`](src/somnus_vm/daemon.py) / [`daemon_runtime.py`](src/somnus_vm/daemon_runtime.py) / [`registry.py`](src/somnus_vm/host/registry.py) / [`service.py`](src/somnus_vm/host/service.py) | [`test_daemon_transport_p2.py`](test/vm_lab/test_daemon_transport_p2.py), [`test_registry_p2.py`](test/vm_lab/test_registry_p2.py), [`test_registry_service_p2.py`](test/vm_lab/test_registry_service_p2.py), and [`test_daemon_registry_p2.py`](test/vm_lab/test_daemon_registry_p2.py) with real peers/processes/SQLite/checkpoint kills |
| Change image/storage | [`PLAN.md`](PLAN.md) Phase P3 → [`images.py`](src/somnus_vm/host/images.py) / [`storage.py`](src/somnus_vm/host/storage.py) / [`exec_guard.py`](src/somnus_vm/host/exec_guard.py) → [fixture manifest](test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json) | [`test_storage_p3.py`](test/vm_lab/test_storage_p3.py), [`test_storage_p3_adversarial.py`](test/vm_lab/test_storage_p3_adversarial.py), and [`test_daemon_storage_p3.py`](test/vm_lab/test_daemon_storage_p3.py); real `qemu-img`, all 14 kill checkpoints, foreign-adoption rejection, and daemon reconciliation |
| Change P4 QEMU/QMP runtime | [`PLAN.md`](PLAN.md) Phase P4 → [`daemon_runtime.py`](src/somnus_vm/daemon_runtime.py) → [`launch_authority.py`](src/somnus_vm/host/launch_authority.py), [`qemu_runtime.py`](src/somnus_vm/host/qemu_runtime.py), [`qemu_runtime_journal.py`](src/somnus_vm/host/qemu_runtime_journal.py), [`p4_gate.py`](src/somnus_vm/host/p4_gate.py), [`qmp.py`](src/somnus_vm/host/qmp.py), [`qmp_identity.py`](src/somnus_vm/host/qmp_identity.py), [`qemu_process.py`](src/somnus_vm/host/qemu_process.py), [`qemu_logs.py`](src/somnus_vm/host/qemu_logs.py), and guards | `test_disposable_launch_authority_p4.py`, `test_p4_gate_coordinator.py` (8/8), `test_qemu_runtime_p4.py`, `test_daemon_runtime_p4.py`, `test_qemu_runtime_journal_p4.py`, `test_qmp_p4.py`, `test_qmp_identity_p4.py`, `test_qemu_process_p4.py`, `test_qemu_logs_p4.py`, `test_qemu_exec_guard_p4.py`, and `test_storage_runtime_p4.py`; `test_p4_gate_coordinator.py` proves exact preflight and denial; these non-launch proofs do not close `GATE-P4` |
| Change QEMU planning | [`FAILURE_GRAMMAR.md`](FAILURE_GRAMMAR.md) → [`qemu.py`](src/somnus_vm/host/qemu.py) | Exact argv, unsafe-input, and no-mutation evidence |
| Change guest bootstrap | [`FAILURE_GRAMMAR.md`](FAILURE_GRAMMAR.md) → `src/somnus_vm/guest/bootstrap.py` | Hostile archive, TOCTOU, hash, atomic placement, idempotency |
| Promote candidate code | [`docs/MIGRATION_MAP.md`](docs/MIGRATION_MAP.md) → [`PROVENANCE.md`](PROVENANCE.md) → relevant gate | New integration proof; never import-by-convenience |
| Diagnose a suspicious success | [`FAILURE_GRAMMAR.md`](FAILURE_GRAMMAR.md) first | Smallest discriminating physical check |
| Update architecture | [`TOPOLOGY.md`](TOPOLOGY.md) → [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) | Repository verifier plus fingerprint |
| Prepare a snapshot | [`PLAN.md`](PLAN.md) sign-off → [`SNAPSHOT.md`](SNAPSHOT.md) | Fresh proof bundle and post-write hashes |
| Review history | [`MEMORY.md`](MEMORY.md) → [`PROVENANCE.md`](PROVENANCE.md) | Recheck drift-prone facts live |

Do not load `MEMORY.md`, lineage, candidate code, or all plan phases merely
because they exist.

---

## 6. Runtime and disposition boundaries

| Tree | Disposition | Rule |
| --- | --- | --- |
| [`src/somnus_protocol/`](src/somnus_protocol/) | **LIVE P1** | Canonical pure shared protocol authority |
| [`src/somnus_vm/`](src/somnus_vm/) | **LIVE v0.1 + P1/P2/P3 + internal P4 implementation/coordinator** | Public read-only CLI/planning, authenticated internal daemon/client, transactional registry/service, verified qcow2 storage, daemon-wired QEMU/QMP runtime ownership and recovery modules, bootstrap, and compatibility re-exports; `GATE-P4` remains open |
| [`test/vm_lab/`](test/vm_lab/) | **CURRENT PROOF** | Direct integration harness and sealed run bundles |
| [`configs/`](configs/) | **CURRENT INPUT** | Validated profiles; no secrets |
| [`components/`](components/) | **CANDIDATE** | Donor code; never boot-imported until a gate promotes it |
| [`extras/`](extras/) | **COLD / LEGACY** | Explicit future adapter only |
| [`archive/`](archive/) | **LINEAGE** | Historical source, not runtime |
| [`docs/lineage/`](docs/lineage/) | **LINEAGE** | Historical architecture claims, not proof |
| [`quarantine/`](quarantine/) | **REJECTED RUNTIME** | Preserve as failure evidence; never import |
| [`snapshots/`](snapshots/) | **SEALED CLAIMS** | Immutable baseline metadata, not task state |

The machine-readable owner is
[`src/somnus_vm/topology.py`](src/somnus_vm/topology.py). If the table above
and that registry diverge, stop and reconcile before coding.

---

## 7. Baked-in design choices

These are current architectural walls, not suggestions:

- **Persistent full computer:** the AIPC is not a disposable container. The
  target disk is about 100 GB unless a profile explicitly chooses otherwise.
- **One active AIPC policy, multi-VM correctness:** normal policy allows one
  active AIPC, while identifiers, ports, storage, and state must still never
  collide.
- **One mutation owner:** the daemon composition root owns registry writes,
  leases, base import, overlay publication, storage recovery, and the internal
  P4 QEMU runtime owner/recovery path. No CLI, guest, Kerminal, donor, or
  fallback becomes a second lifecycle authority. This implementation does not
  itself promote a public lifecycle or close `GATE-P4`. Physical-gate code must
  enter through the Python-only daemon activation hook and present a consumed
  `DisposableLaunchPermit`; no profile name, path convention, environment
  variable, or test location is launch authority.
- **Non-daemonized child:** QEMU remains a directly owned child; no
  `-daemonize`, no shell interpolation.
- **QMP before truth:** socket existence and PID liveness never establish VM
  identity. QMP greeting, capabilities, UUID, and status are required.
- **Local-first:** boot, lifecycle, and guest control cannot require cloud APIs
  or external credentials.
- **Guest bootstrap is payload-only in v0.1:** network URLs, arbitrary
  post-install commands, and service-body mutation remain rejected.
- **Standard-library live runtime:** `pyproject.toml` declares no runtime
  dependencies. Candidate dependency lists do not affect boot.
- **VM-Go remains separate:** parity is behavioral and measured. This lab does
  not absorb VM-Go by declaration.
- **Kerminal remains the agency surface:** it will call a control API; it never
  owns QEMU.
- **Artifact processors remain outside the AIPC:** approved outputs cross a
  recorded ingress boundary; processors do not own persistent VM identity.

Changing one of these requires an explicit replacement decision, migration
path, new gate, and provenance entry.

---

## 8. Engineering rules inside this repository

### Narrow changes

- Inspect the named owner before editing.
- Reuse verified live code through explicit interfaces; do not copy donor code
  into the runtime and call that integration.
- Keep shared contracts pure, dependency-light, serializable, versioned, and
  side-effect free.
- Export consumer-facing validation failures through the public protocol root;
  consumers must not import private `_validation` helpers.
- Keep control request/response machine bags authenticated, bounded, and
  operation-owned. Never treat their generic shape as public redaction or
  logging permission.
- Keep public error prose and behavior registry-derived; raw diagnostics remain
  behind rigid references owned by a separate access-controlled store.
- Keep host boot free of guest cognition, prompt, model, browser, shell, and
  file-processing imports.
- Keep guest code free of host registry, QMP, image, and lease ownership.
- Use argv arrays; never introduce shell command strings.
- Catch domain-specific failures. Unexpected failures must remain visible.
- Preserve unrelated worktree state and every source artifact unless deletion
  is explicitly authorized.

### Promotion

A candidate becomes live only when:

1. its owner and boundary are explicit;
2. its direct dependencies are acceptable on Daeron's CPU-only hosts;
3. it cannot create a second lifecycle authority;
4. its failure modes are encoded;
5. its actual consumer reaches it;
6. the named `GATE-*` in [`PLAN.md`](PLAN.md) passes;
7. snapshot, topology, provenance, docs, and evidence agree.

### Physical tests

- Use real local files, sockets, SQLite databases, archives, subprocesses, QMP,
  and disposable VM images at the layer under test.
- Do not use mocks to establish physical lifecycle claims.
- A missing QEMU binary, base image, KVM device, or guest fixture is a truthful
  blocked gate—not a reason to fabricate a pass.
- Physical tests may never point at a production AIPC disk.

### Exhausted-owner SOTA++ doctrine

Production-grade means **exhausted, not exhaustive**. Judge a file or module by
whether the ownership boundary it represents has been driven as far as that
layer can truthfully take it—not by line count, conventional decomposition
instinct, the existence of tests, or the happy path working.

- Exhaust the local design space: capabilities, failure semantics, state
  interaction, recovery, persistence, performance, instrumentation, extension
  seams, runtime consumption, and consequences belong in the real owner until
  responsibility genuinely crosses an ontological boundary.
- Exhaust existing structure before adding managers, helpers, databases, or
  modules merely because the current owner became difficult.
- Integrate causally. Imports, registrations, telemetry, schemas, and adjacent
  tests are not integration unless one subsystem's state changes the other's
  behavior through the actual runtime path.
- Close loops: input → state mutation → consequence → feedback → changed future
  behavior. Unconsumed telemetry and unattached evidence are unfinished.
- Preserve causal locality when the component is intentionally the single
  computational or ownership surface. Size alone never requires a split.
- When rediscovering an older mechanism, identify the architectural dimension
  it exhausted, then prove whether the current organism exhausts that dimension
  better, lost it, or must reconcile both.
- Stop only when the remaining work truthfully belongs to another owner.

SOTA++ here means the strongest plausible implementation of the idea after
assuming the obvious version has already been tried. Intelligence and system
quality scale through **causal density, not component count or storage
density**. Never simplify behavior, narrow the destination, or manufacture an
easier test boundary to obtain green output.

### Operator agency and thoroughness

Do not optimize substantive work for short replies, few tool calls, or
token/compute conservation.

- Execute the work: inspect, edit, run, diagnose, repair, and re-run. Hand
  Daeron a checklist only when credentials, a consequential physical action, or
  an external product surface genuinely requires him.
- Read the newest [`NOTEPAD.md`](NOTEPAD.md) entry before production edits.
- Use CodeGraph before grep/read loops for source flow, owner, caller, and
  impact questions; use parallel agents for disjoint bounded lanes when
  Daeron's task or the active packet authorizes them.
- Finish the whole active packet in the session while safe in-scope work
  remains. A plan, partial implementation, or “what I would do next” is not a
  done condition.
- Tests pass only because the behavior is fully implemented at their consumed
  boundary. Never weaken behavior, fixtures, assertions, validation, or
  failure semantics to make a test green.

### Fail-loud / no-fallback engineering doctrine

Production posture is **fail loud, log evidence, preserve the proof wall**.
Silent recovery is a boundary defect.

- No silent fallbacks, cached substitutions, regex shortcuts, default-value
  repairs, best-effort continuation, or implicit downgrade lanes may replace a
  failed contract, authority, lease, manifest, process, QMP observation, guest
  result, or gate.
- Catch only domain-specific failures to add boundary context, then raise a
  typed error or return a failing process status. Unexpected failures remain
  visible; broad catches must re-raise with evidence.
- Deferred, harness-only, lineage, candidate, and unpromoted surfaces must be
  named as such and cannot masquerade as runtime capability.
- Record proof-relevant failure at the owner: command output, QMP evidence,
  operation/checkpoint state, manifest drift, exact denial reason, or run
  artifact.
- Negative tests assert the failure mode and unchanged authority, not merely
  the absence of success.
- Every editor, generator, or agent change is either accepted into the
  documented/validated working state or deliberately reverted. Do not leave
  unexplained drift for the next operator.

### Runtime workflow, continuity, and clean handoff

At the start of a substantive or resumed execution session, read
[`workflows-new.md`](workflows-new.md) once and use its canonical
sync → context → execute → verify → persist → reflect loop when SovereignMCP is
mounted. Do not repeat bootstrap mechanically after the session is hydrated.
When BB7 is not callable, preserve the same semantics through the repository's
own `NOTEPAD.md`, `TASK.md`, `CONTEXT.md`, `MEMORY.md`, CodeGraph, native
execution, and verification surfaces; report the exact unavailable BB7 surface
rather than simulating it.

After a turn changes runtime state, routing, implementation, or gate status:

| Surface | Required action |
| --- | --- |
| [`NOTEPAD.md`](NOTEPAD.md) | Append one factual peer-log entry; never rewrite earlier entries |
| [`TASK.md`](TASK.md) | Mark landed work and publish one imperative next packet when priority changes |
| [`STATE.md`](STATE.md) | Record exact phase, command, result, blocker, and next consumed action |
| [`CONTEXT.md`](CONTEXT.md) | Update the current implementation/proof snapshot |
| [`MEMORY.md`](MEMORY.md) | Append durable decisions, corrections, and reusable failure lessons |
| [`PROVENANCE.md`](PROVENANCE.md) | Record material implementation lineage and verification |
| [`filetree.md`](filetree.md) / [`tree-codebase.md`](docs/old-filetrees/tree-codebase.md) | Refresh after structural add/move/delete; use `filetree.md` as the anti-tunnelvision topology reset |
| [`SOTA_RUN.md`](SOTA_RUN.md) | Keep the latest aggregate result and its exact interpretation synchronized |
| snapshot/source manifests | Change only when their owned promoted boundary actually changes |

Continuity documents are orders to the next operator, not brainstorming notes.
State what **is landed**, what **failed**, and what **is next**. Do not use
“optional,” “suggested,” “consider,” “if you want,” or “when possible” in the
active handoff. Pin the commit, proof counts, exact commands, artifacts, and
real blockers. End the newest `NOTEPAD.md` entry with one imperative next
packet, its owner files, verification commands, and hard bans so Daeron never
has to retranslate between agents.

---

## 9. Commands and acceptance surfaces

All commands run from the repository root.

### Fast living-repository integrity

```bash
python scripts/verify_repository.py
```

Checks the linked entry packet, topology fingerprint, Markdown targets,
architecture-map anchors, JSON state, source preservation, and required
ecosystem files.

### Full current local proof

```bash
PYTHONPATH=src python test/vm_lab/smoke.py
```

This is the consumed-boundary regression harness. Its sealed P1/P2/P3 bundle
aggregates canonical protocol checks, v0.1 CLI/config/planner/bootstrap, the
installed-wheel boundary, P2 daemon/registry proofs, and P3 image/storage
proofs. The sealed
closure bundle
[`20260805T211740Z`](test/vm_lab/runs/20260805T211740Z/) records 26/26 aggregate
checks with zero failures and zero skips. Its P3 lane uses real `qemu-img`
8.2.2, the pinned fixture SHA-256
`b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`,
two independent sparse 100 GiB overlays, all seven base and seven overlay
checkpoint kills, adversarial ownership/tamper checks, and one real
daemon/client storage composition. That sealed bundle predates P4
implementation work and does not establish QEMU launch, mounting, QMP identity,
guest readiness, deployment, or public VM lifecycle.

The current published aggregate
[`20260905T101913Z`](test/vm_lab/runs/20260905T101913Z/) records 36 pass,
4 fail, and 0 skip: every failure is exact fixture absence on that host. The
historical aggregate
[`20260807T123747Z`](test/vm_lab/runs/20260807T123747Z/) recorded 40/0/0 after
then-fixture restoration; it is sealed provenance, not current readiness.
`TASK-P4-019` re-establishes current fixture authority from the declared origin
in [`ubuntu-minimal-noble-amd64-20260801.json`](test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json)
(264,306,688 bytes, SHA-256
`b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`) and only a
fresh bundle may correct current-state documents. That is neither a P4 gate
pass nor a retroactive invalidation of sealed P3 evidence: valid execute
authority, explicit production exclusions, actual qemu-system execution, and
the checkpoint/adoption matrix remain unconsumed.

### P4 direct implementation modules (gate remains open)

```bash
PYTHONPATH=src python test/vm_lab/test_qemu_runtime_p4.py
PYTHONPATH=src python test/vm_lab/test_daemon_runtime_p4.py
PYTHONPATH=src python test/vm_lab/test_qemu_runtime_journal_p4.py
PYTHONPATH=src python test/vm_lab/test_qmp_p4.py
PYTHONPATH=src python test/vm_lab/test_qmp_identity_p4.py
PYTHONPATH=src python test/vm_lab/test_qemu_process_p4.py
PYTHONPATH=src python test/vm_lab/test_qemu_logs_p4.py
PYTHONPATH=src python test/vm_lab/test_qemu_exec_guard_p4.py
PYTHONPATH=src python test/vm_lab/test_storage_runtime_p4.py
PYTHONPATH=src python test/vm_lab/test_doctor_p4.py
PYTHONPATH=src python test/vm_lab/test_p4_gate_coordinator.py
```

These are direct non-launch owner/journal/QMP/process/log/recovery and
selected-QEMU parser-consumption checks. They are not evidence of a disposable
QEMU machine run and must not be described as `GATE-P4` closure.

### Focused direct test modules

```bash
PYTHONPATH=src python test/vm_lab/test_vm_lab.py
PYTHONPATH=src python test/vm_lab/test_storage_p3.py
PYTHONPATH=src python test/vm_lab/test_storage_p3_adversarial.py
PYTHONPATH=src python test/vm_lab/test_daemon_storage_p3.py
```

The P3 commands contain 15 tests total: six core, eight adversarial, and one
real-daemon case. The exact pinned input owner is
[`test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`](test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json).

### Public surfaces

```bash
PYTHONPATH=src python -m somnus_vm topology
PYTHONPATH=src python -m somnus_vm doctor --json
PYTHONPATH=src python -m somnus_vm plan \
  --name disposable-lab \
  --disk /absolute/path/to/disposable.qcow2 \
  --memory-mib 4096 \
  --vcpus 2 \
  --tcg \
  --json
```

`doctor` is expected to fail honestly when host metal is absent. `plan` binds
candidate ports only long enough to prove availability and then releases them;
`ports_reserved` must remain false.

The installed `somnus-vm-daemon` entrypoint is a live internal composition
boundary for authenticated registry/storage requests and the daemon-wired P4
runtime recovery owner. It is not a public VM lifecycle CLI: it exposes no
QEMU start/stop, QMP, guest-readiness, deployment, snapshot, or destroy claim.

### Packaging boundary

The smoke harness builds and installs the wheel outside the checkout. A command
working only with `PYTHONPATH=src` is not packaging proof.

---

## 10. Planning and state contract

- [`PLAN.md`](PLAN.md) owns the complete destination, phases, invariants,
  dependencies, and physical gates.
- [`TASK.md`](TASK.md) owns at most one active execution unit and a bounded
  queue. When no unit is active, it names exactly one ready next unit. Do not
  turn it into a second master plan.
- [`CONTEXT.md`](CONTEXT.md) is the structured current-state index.
- [`MEMORY.md`](MEMORY.md) holds durable decisions, corrections, rejected
  paths, and reusable failure lessons.
- [`PROVENANCE.md`](PROVENANCE.md) is the chronological lineage and session
  ledger.
- [`SCOPE.md`](SCOPE.md) records the current WRAP / EDIT / COMPOSE boundary for
  runtime implementation.
- [`STATE.md`](STATE.md) records the current phase, exact unit, last command,
  blocker, next action, dirty files, and evidence.
- [`docs/plan-index.json`](docs/plan-index.json) is the machine-readable phase
  DAG. Validate it directly with
  `python scripts/verify_repository.py --plan-only --json`.

At session close after substantive mutation:

1. update the active task status;
2. update only the topology/context/memory surfaces that actually changed;
3. append one factual `PROVENANCE.md` session row;
4. recompute `.sovereign/topology_fingerprint.txt`;
5. run `python scripts/verify_repository.py`;
6. run the proportional runtime proof;
7. leave unresolved physical gates explicitly unresolved.

---

## 11. Repository ecosystem

| Surface | Role |
| --- | --- |
| [`filetree.md`](filetree.md) | Living navigation and dependency map |
| [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) | Question-oriented traversal with source anchors |
| [`STATE.md`](STATE.md) | Live execution phase and exact unit |
| [`docs/plan-index.json`](docs/plan-index.json) | Strict phase/dependency/gate/evidence DAG |
| [`TOPOLOGY.md`](TOPOLOGY.md) | Invariants, interfaces, complexity, anti-concepts |
| [`FAILURE_GRAMMAR.md`](FAILURE_GRAMMAR.md) | Pre-failure smells and false-success taxonomy |
| [`docs/decisions/0005-errors-and-exit-codes.md`](docs/decisions/0005-errors-and-exit-codes.md) | Closed public error and exit-code contract |
| [`.codex/skills/vm-lab/SKILL.md`](.codex/skills/vm-lab/SKILL.md) | Conditional repository skill router |
| [`.sovereign/session_state.json`](.sovereign/session_state.json) | Last-close continuity hint |
| [`.sovereign/golden_paths.json`](.sovereign/golden_paths.json) | Repository-specific known-good entry routes |
| [`.github/workflows/repository-integrity.yml`](.github/workflows/repository-integrity.yml) | Push, PR, and manual integrity enforcement |
| [`.github/copilot-instructions.md`](.github/copilot-instructions.md) | Bounded GitHub-agent projection of this kernel |
| [`.pre-commit-config.yaml`](.pre-commit-config.yaml) | Optional local structural gate |

GitHub Actions is the repository-owned event automation. A third-party webhook
requires a concrete receiver URL, event contract, and secret owner; never invent
one or send repository data to an unspecified endpoint.

---

## 12. Failure stop-signs

Read [`FAILURE_GRAMMAR.md`](FAILURE_GRAMMAR.md) immediately if any of these
appear:

- a command says a VM is running because a PID exists;
- a P4 journal, QMP fixture, process observation, or direct test is described as
  an actual disposable QEMU machine run;
- readiness is inferred from an open TCP port;
- user networking is paired with a fabricated `192.168.122.x` guest address;
- a plan-time port is described as reserved;
- snapshot rollback copies a child over its backing image;
- a capability returns success without an observed mutation;
- a test measures `sleep`, a log line, or a mock and calls it VM performance;
- candidate/quarantine code enters the live import graph;
- `doctor` is changed to pass on a host missing required metal;
- documentation claims a command not exposed by the CLI;
- source, snapshot, topology, and proof hashes disagree.

Do not patch around these. Surface the false-success mechanism and return to the
nearest physical owner.

---

## 13. Full linked kit

- [Repository overview](README.md)
- [Foundational Somnus system intent](Somnus-Core-Ideals.md)
- [Repository runtime workflow driver](workflows-new.md)
- [Living file tree](filetree.md)
- [Generated point-in-time file inventory](docs/old-filetrees/tree-codebase.md)
- [Architecture traversal map](ARCHITECTURE_MAP.md)
- [Cognitive topology](TOPOLOGY.md)
- [Failure grammar](FAILURE_GRAMMAR.md)
- [Current context index](CONTEXT.md)
- [Codex operator notepad](NOTEPAD.md)
- [Durable repository memory](MEMORY.md)
- [Active task](TASK.md)
- [Execution state](STATE.md)
- [Full execution plan](PLAN.md)
- [Machine plan index](docs/plan-index.json)
- [Target platform matrix](docs/PLATFORM_MATRIX.md)
- [Disposable fixture policy](docs/DISPOSABLE_FIXTURE_POLICY.md)
- [Architecture decisions](docs/decisions/)
- [Current implementation scope](SCOPE.md)
- [Snapshot boundary](SNAPSHOT.md)
- [Latest proof ledger](SOTA_RUN.md)
- [Provenance and session ledger](PROVENANCE.md)
- [Reconstitution audit](docs/RECONSTITUTION_AUDIT.md)
- [Migration map](docs/MIGRATION_MAP.md)
- [Source preservation manifest](source-manifest.json)
- [v0.1 snapshot manifest](snapshots/v0.1/manifest.json)

If this packet points to a missing owner, if an anchor is stale, or if the
verifier passes while consumed behavior fails, the packet is invalid. Repair
the smallest owning layer; never preserve misleading navigation for cosmetic
continuity.
