# VM Lab Architecture Map

**How to use this document:** Start at the question matching the work. Every
branch begins at a current owner and anchor, traces the next authority, and
ends with lateral routes. This is a traversal map—not a system summary.

**Promoted baseline:** snapshot `v0.1`, closed P1 protocol authority, live
internal P2 daemon/registry ownership, and physically verified P3 image
storage. P4 now has a bounded daemon-wired QEMU/QMP runtime owner and an explicit
non-public physical-gate coordinator: descriptor-bound storage handoff, journal,
process/log guards, QMP fdset/block graph, doctor, orphan recovery, one-shot
disposable launch authority, canonical 17-scenario matrix preparation, exact
preflight, strict serial execution/resume policy, checkpoint/recovery sealing,
and canonical QMP response-history capture all have direct non-launch proof.
The exact fixture is restored and current local aggregate is 40/0/0. Valid
full-matrix Daeron authority, explicit production exclusions, checkpoint
execution, and disposable machine-truth `GATE-P4` remain open. The public host CLI remains read-only.
Current code outranks this map if an anchor drifts.

---

## ROOT: What is VM Lab now?

VM Lab is the Python reference boundary for a persistent Somnus AIPC. The
public host remains deliberately read-only: it validates configuration, reports
topology and host preflight, and emits a non-mutating QEMU plan. Guest bootstrap
is a separate local entrypoint. Behind that public boundary, a separately
invoked internal daemon now owns the durable SQLite registry, authenticated
Unix control transport, base-image import, and independent sparse-overlay
materialization. The daemon composition also wires an internal P4 runtime owner
for descriptor-bound QEMU/QMP launch journalling, process/log observation,
fdset/block-graph identity, one-shot non-production permits, and restart
recovery. All bounded P4 phase items are implemented, but the repository still
contains `src/somnus_vm/host/p4_gate.py` and the non-public
`scripts/run_gate_p4.py` driver for the daemon-owned 17-scenario
checkpoint/QMP evidence matrix. The matrix is prepared and preflightable but
has not executed. No disposable-QEMU machine truth exists:
`GATE-P4` remains open, and the public boundary exposes no lifecycle mutation.

**Enter through these five layers:**

1. Operator contract: `AGENTS.md:1` → authority, scope, and physical gates.
2. Shared contracts: `src/somnus_protocol/__init__.py:1` → pure protocol root.
3. Public runtime: `src/somnus_vm/cli.py:76` → `main()`.
4. Internal mutation root: `src/somnus_vm/daemon_runtime.py` → `run()`.
5. P4 launch membrane: `src/somnus_vm/host/launch_authority.py` →
   `src/somnus_vm/host/p4_gate.py` →
   `mint_disposable_launch_permit()`.
6. P4 runtime owner: `src/somnus_vm/host/qemu_runtime.py` →
   `QemuRuntimeOwner`.
7. Direct consumed proof: `test/vm_lab/test_vm_lab.py` → `main()`.

**→ To follow a public command:** BRANCH A.
**→ To understand promoted authority:** BRANCH B.
**→ To change schemas or migration:** BRANCH C.
**→ To change strict host configuration:** BRANCH D.
**→ To understand planning or bootstrap:** BRANCH E or F.
**→ To locate current proof:** BRANCH G.
**→ To trace daemon/registry/storage mutation:** BRANCH I.
**→ To trace the internal P4 runtime owner:** BRANCH J.

---

## BRANCH A: How does a public host command run?

**Entry point:** `src/somnus_vm/cli.py:76` → `main()`

1. `src/somnus_vm/cli.py:30` builds only `doctor`, `topology`, and `plan`.
2. `src/somnus_vm/config.py:748` loads strict profile/configuration ownership.
3. `topology` renders the registered component disposition from
   `src/somnus_vm/topology.py:48`.
4. `doctor` delegates to `src/somnus_vm/doctor.py:123`.
5. `plan` delegates to `src/somnus_vm/host/planner.py:30`.
6. Expected contract/config/plan errors remain bounded CLI failures; there is no
   `start`, `stop`, `destroy`, snapshot, or image-build command.

**→ For config roots and host truth:** BRANCH D.
**→ For plan composition:** BRANCH E.
**→ For the public proof boundary:** BRANCH G.

---

## BRANCH B: What code may execute?

**Entry point:** `src/somnus_vm/topology.py:48` → `COMPONENTS`

1. Each `ComponentSpec` names a boundary, disposition, path, boot-import
   policy, and reason.
2. `src/somnus_protocol/` is **LIVE** and is the canonical pure shared schema
   authority. It must not perform filesystem, process, socket, secret, host,
   guest, or operator work at import.
3. `src/somnus_vm/contracts/` is **LIVE compatibility only**: it re-exports
   canonical protocol types and owns no competing schema.
4. `src/somnus_vm/` is **LIVE** for host CLI/planning, separate bootstrap, and
   the separately invoked internal daemon/registry/storage boundary. This does
   not imply a public lifecycle command.
5. `components/` is donor/candidate code; `extras/` is cold/legacy;
   `archive/` and `docs/lineage/` are historical; `quarantine/` is rejected.
6. `docs/MIGRATION_MAP.md:1` maps preserved original sources; it does not
   promote them.

**→ For candidate promotion rules:** `AGENTS.md` § Engineering rules.
**→ For source placement:** `filetree.md:110`.
**→ For a topology mismatch:** `FAILURE_GRAMMAR.md:1` then the direct owner.

---

## BRANCH C: How do canonical protocol contracts work?

**Entry point:** `src/somnus_protocol/__init__.py:1`

1. `src/somnus_protocol/_validation.py:1` supplies stdlib-only strict-object,
   exact-type, bounded JSON, immutable mapping, and canonical serialization
   primitives. `ProtocolValidationError` is deliberately exported at
   `src/somnus_protocol/__init__.py:10` and listed in the exact public
   `__all__` at `:77`; consumers need not import private `_validation`.
2. `SemanticVersion` and `ProtocolVersionRange` begin at
   `src/somnus_protocol/version.py:60` and `:110`. Pairwise negotiation has two
   layers: `ProtocolCapabilities` (`:246`) commits an explicit same-ID offer,
   while `ProtocolNegotiation` (`:397`) commits both offer fingerprints and the
   highest shared selection. Fingerprints detect transcript mismatch, not
   authentication. Separately, ordinary envelopes enforce the current
   package's supported range at `:212` and the one current schema at `:228`.
3. `src/somnus_protocol/vm.py:65` defines immutable ports and `:151` the
   declaration. `VMState` at `:282` contains 16 observed states; the legal-edge
   table at `:323` and edge requirements at `:562` describe exactly 48 edges.
   Resources, storage, image, process, snapshot, observation, cause, evidence,
   and transition values begin at `:831`, `:889`, `:1022`, `:1152`, `:1283`,
   `:1422`, `:1827`, `:1919`, and `:2121`.
4. `VMRecord` at `src/somnus_protocol/vm.py:2341` is secret-free, versioned,
   immutable lifecycle truth. Its declaration `intent_digest` binds planned
   runtime and migration provenance; history rejects reused evidence,
   observation, boot, process, and error-cause identities, and requires every
   later authoritative observation of the active process core to have a
   strictly newer process timestamp. Exact v0 migration starts at `:3314` and
   dispatches at `:3427`; only a historyless `DECLARED` source can migrate, and
   its raw token is omitted with explicit provenance.
5. `src/somnus_protocol/agent.py:222` defines seven fixed guest endpoints.
   `AgentHealth` (`:235`), `CommandResult` (`:308`), and `FileWriteResult`
   (`:407`) are immutable, bounded result contracts; they do not implement or
   authenticate a guest daemon.
6. `ControlRequest` at `src/somnus_protocol/control.py:449` and
   `ControlResponse` at `:572` carry bounded authenticated machine-keyed
   `payload`/`result` bags. Those generic bags are operation-owned and are not
   public or log-safe projections.
7. `DiagnosticReference` (`src/somnus_protocol/control.py:685`) is only a rigid
   `diag:<non-nil UUID>` locator. `ErrorEvidence` (`:723`) and `ErrorEnvelope`
   (`:771`) derive every public phrase, retry decision, remediation, evidence
   summary, category, and exit class from closed registries; caller prose is
   absent and P1 `details` must be empty. `ControlEvent` at
   `src/somnus_protocol/events.py:47` provides the ordered correlation
   envelope.
8. `src/somnus_vm/contracts/vm.py:1` and
   `src/somnus_vm/contracts/agent.py:1` preserve historical imports as exact
   canonical object re-exports. New schema and public root exports belong in
   `somnus_protocol`, not in the compatibility package.

The protocol package records **what must be observed** before a transition
becomes truth. It does not itself observe QMP, authenticate a peer, persist a
registry, or launch QEMU. The separately composed daemon now also constructs the
internal P4 runtime owner, but its direct non-launch proof has not satisfied the
external disposable-QEMU `GATE-P4`; guest evidence remains later work.

**→ For physical success rules:** `TOPOLOGY.md` § LBC 3.
**→ For rejected/unsafe state changes:** `FAILURE_GRAMMAR.md:1`.
**→ For direct protocol proof:** BRANCH G.

---

## BRANCH D: How are configuration and host truth established?

**Entry point:** `src/somnus_vm/config.py:748` → `load_configuration()`

1. Strict scalar/table helpers begin at `src/somnus_vm/config.py:108` and
   reject coercion and unknown keys.
2. Disjoint ownership settings are explicit: daemon (`:358`), storage (`:389`),
   loopback network (`:416`), guest-agent timing (`:456`), secret references
   only (`:472`), and one-active-AIPC policy (`:502`).
3. `HostConfiguration` at `:519` owns their composed boundary; `LabConfiguration`
   at `:659` keeps profile and source provenance.
4. `named_profile()` at `:1057` generates install-safe profile-scoped roots and
   runtime sockets; `default_configuration()` at `:1082` remains local TCG.
5. `src/somnus_vm/doctor.py:123` observes Python, topology, writable roots,
   QMP path capacity, QEMU binaries, qcow2 image format, KVM, and promotion
   policy. Missing required metal is `FAIL`, never fallback success.

**→ For an actual QEMU intent:** BRANCH E.
**→ If doctor looks too green:** `FAILURE_GRAMMAR.md` § Pre-failure signatures.
**→ For profile proof:** `test/vm_lab/test_protocol_settings.py:492`.

---

## BRANCH E: How is a non-mutating QEMU plan produced?

**Entry point:** `src/somnus_vm/host/planner.py:30` → `VMPlanner.preview()`

1. The planner imports compatibility aliases whose authority is now
   `somnus_protocol.vm`; it creates the canonical secret-free `VMRecord` at
   `src/somnus_vm/host/planner.py:53`.
2. `src/somnus_vm/host/ports.py:33` bind-checks SSH, agent, and VNC candidates.
3. `src/somnus_vm/host/qemu.py:43` builds shell-free, non-daemonized argv.
4. The planner replaces only immutable plan locations and releases temporary
   sockets before return (`src/somnus_vm/host/planner.py:61-69`).

**DENSE edge:** availability is not durable allocation; a `declared` record is
not evidence that a machine exists. The internal mutation owner now exists for
registry and image storage, but the public planner does not call it and it does
not launch QEMU.

**→ For VM truth/migration:** BRANCH C.
**→ For planner false success:** `FAILURE_GRAMMAR.md` § FS-VM-02.
**→ For internal daemon/storage ownership:** BRANCH I.
**→ For P4 QEMU process work:** BRANCH J, then the active phase in `PLAN.md`.

---

## BRANCH F: How does verified guest bootstrap work?

**Entry point:** `src/somnus_vm/guest/bootstrap.py:502` → `main()`

1. `src/somnus_vm/guest/bootstrap.py:149` loads strict local-only TOML intent.
2. `src/somnus_vm/guest/bootstrap.py:336` bounds, hashes, fsyncs, and materializes source bytes once
   into a private verified file.
3. ZIP and TAR validation begins at lines `247` and `283`; traversal, links,
   devices, duplicates, collisions, and undeclared expansion are rejected.
4. `src/somnus_vm/guest/bootstrap.py:468` stages the complete target, writes recovery state
   atomically, and honors configuration-bound idempotency.
5. Network URLs, arbitrary post-install commands, and service-body mutation
   remain outside v0.1.

P1 control/agent envelopes define future authenticated guest communication but
do not cause bootstrap to become a remote installer.

**→ For hostile input:** `FAILURE_GRAMMAR.md` § Bootstrap.
**→ For direct bootstrap evidence:** BRANCH G.
**→ For guest agent and provisioning promotion:** `PLAN.md` § Phase P6 and
§ Phase P7.

---

## BRANCH G: What proves current consumed behavior?

**Entry point:** `test/vm_lab/test_vm_lab.py:1063` → `main()`

The harness runs canonical protocol and v0.1 checks, then the independently
invoked P2/P3 physical modules:

- `test/vm_lab/test_protocol_vm_primitives.py:28` — strict VM definition/ports;
- `test/vm_lab/test_protocol_agent.py:332` — endpoints and bounded result contracts;
- `test/vm_lab/test_protocol_control.py:788` — validation, pairwise negotiation,
  current-package enforcement, request/response/error/event contracts, public
  root exports, and cold import;
- `test/vm_lab/test_protocol_settings.py:492` — strict owned configuration roots/profile
  generation;
- `test/vm_lab/test_protocol_host_consumption.py:84` — planner/CLI consume the canonical
  secret-free record;
- `test/vm_lab/test_protocol_vm_lifecycle.py:1690` — lifecycle schemas,
  intent/identity-bound evidence, replay resistance, destroyed generations, and
  exact DECLARED-only v0 migration;
- `test/vm_lab/test_vm_lab.py:138` onward — compatibility, configuration, ports, QEMU argv,
  hostile bootstrap, doctor, CLI, installed wheel, plan index, and source
  preservation;
- `test/vm_lab/test_storage_p3.py:687` through `:1136` — six core storage tests:
  manifest/backend ambiguity, v2→v3 registry migration, pinned-base import,
  two independent sparse overlays, alias rejection, and recovery across all
  persisted base/overlay checkpoints;
- `test/vm_lab/test_storage_p3_adversarial.py:194` through `:624` — eight
  adversarial tests: foreign base/overlay non-adoption, symlink safety, missing
  or replaced checkpoint candidate rejection, registered-base inode binding,
  post-publication SQLite conflict recovery, and native-child containment;
- `test/vm_lab/test_daemon_storage_p3.py:194` — one real daemon/client
  materialization plus startup physical reconciliation test.
- `test/vm_lab/test_qemu_runtime_p4.py`, `test_daemon_runtime_p4.py`,
  `test_qemu_runtime_journal_p4.py`, `test_qmp_p4.py`,
  `test_p4_gate_coordinator.py`,
  `test_qmp_identity_p4.py`, `test_qemu_process_p4.py`,
  `test_qemu_logs_p4.py`, `test_qemu_exec_guard_p4.py`, and
  `test_storage_runtime_p4.py` — direct P4 owner, daemon composition, journal,
  QMP, process, log, guard, and storage-runtime tests. These are non-launch
  implementation proof, not `GATE-P4` closure. `test_p4_gate_coordinator.py`
  proves exact non-launch matrix behavior 8/8; `scripts/run_gate_p4.py` is the
  non-public prepare/preflight/execute-matrix surface. Its deterministic phrase
  is an accidental-execution fence only, not authentication or Daeron proof.

The focused P3 gate is 15 tests: six core, eight adversarial, and one
daemon-composition test. It kills and resumes at 7 base plus 7 overlay
checkpoints, invokes real `/usr/bin/qemu-img` 8.2.2, pins fixture SHA256
`b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`,
and materializes two independent sparse 100-GiB overlays. The aggregate bundle
at `test/vm_lab/runs/20260805T211740Z/` records 26/26 passes with no skip.

`test/vm_lab/test_vm_lab.py:982` writes JSON, Markdown, and a terminal log;
`test/vm_lab/smoke.py:1` is the external entry. This proves the specified
protocol, public read-only, packaging, internal daemon/registry, and initial
physical-storage boundaries. The later P4 module tests prove their bounded
implementation owners only. No current proof establishes a disposable
`qemu-system-*` run, external QMP machine truth, filesystem mount, guest
readiness, guest-visible writes, or public lifecycle mutation.

**→ For latest evidence:** `SOTA_RUN.md:1`.
**→ For repository packet integrity:** `scripts/verify_repository.py:1`.
**→ For internal storage flow:** BRANCH I.
**→ For P4 and later machine gates:** `PLAN.md:1`.

---

## BRANCH H: Where is current state and why are advanced surfaces bounded?

`TASK.md:1` owns one execution unit; `PLAN.md:1` owns the complete phase/gate
model; `STATE.md:1` records active execution state; `docs/plan-index.json:1`
owns the validated phase DAG. `CONTEXT.md:1` indexes current architecture;
`NOTEPAD.md:1` holds unresolved observations; `MEMORY.md:1` and
`PROVENANCE.md:1` preserve decisions/chronology.

`docs/RECONSTITUTION_AUDIT.md:15` records why old daemon, snapshot, scaling,
agent, lock/protocol, benchmark, and processor paths were withheld. Candidate
code stays valuable lineage, not runnable authority, until a named owner and
physical gate promote a narrow replacement.

The foundational direction in `Somnus-Core-Ideals.md:1` is governing product
intent: persistent AIPC, distinct host/guest/operator/Artifact boundaries, two
memory owners, ASPS as cognitive workspace, ACE as OS-agency pillar, cache as
parallel result infrastructure, and Kerminal as the operator surface. It does
not silently promote its candidate implementations.

---

## BRANCH I: How does the live internal registry/storage path run?

**Entry point:** `src/somnus_vm/daemon_runtime.py:42` → `run()`

1. `run()` loads strict host configuration, opens
   `SQLiteRegistry` at `src/somnus_vm/host/registry.py:1792`, and composes
   `RegistryMutationService` at `src/somnus_vm/host/service.py:289` plus
   `QemuRuntimeOwner` at `src/somnus_vm/host/qemu_runtime.py:978`.
2. Before any listener exists, it settles incomplete and completed P4 launch
   records through `QemuRuntimeOwner`, then calls
   `recover_incomplete_operations()` (`src/somnus_vm/host/service.py:1542`) and
   `reconcile_storage_authority()` (`:1569`) while excluding live runtime VM
   identities. A recovery or physical mismatch aborts startup instead of
   exposing a clean socket.
3. `UnixControlDaemon` at `src/somnus_vm/daemon.py:375` owns one private Unix
   socket, a process-held ownership lock, bounded framing/workers/deadlines, and
   a UID allowlist. Kernel peer identity comes from `SO_PEERCRED` at
   `src/somnus_vm/transport.py:292`; `UnixControlClient` begins at
   `src/somnus_vm/client.py:95`.
4. `RegistryMutationService` serializes mutation through its single in-process
   lock. The currently live operations are registry
   declare/lease/update/cancel/status/get/list plus `storage.import_base` and
   `storage.create_overlay`; there is no start/stop/destroy/snapshot/QMP
   operation.
5. `SQLiteRegistry` at `src/somnus_vm/host/registry.py:1771` owns normalized
   schema migration, one process-held writer, CAS revisions, idempotency,
   operation intent/checkpoints, leases, base records, and disk records. Base
   registration and disk materialization commit at `:2992` and `:3206`.
6. `ImageStore` at `src/somnus_vm/host/storage.py:435` copies and hashes the
   declared local source once, verifies and atomically publishes the immutable
   base (`:808`), then re-observes every registry-bound base fact (`:1003`).
   Overlay creation at `:1090` binds VM, generation, disk, ownership token,
   base identity, virtual size, marker, filesystem identity, and backing chain.
7. `QemuImgBackend` at `src/somnus_vm/host/images.py:696` is the only native
   image-tool adapter. Its bounded argv-only execution begins at `:774`;
   `create_overlay()` at `:1087` and `verify_overlay()` at `:1145` call real
   `qemu-img`. `src/somnus_vm/host/exec_guard.py:23` arms
   `PR_SET_PDEATHSIG(SIGKILL)` immediately before `exec` at `:43`.
8. Every storage stage appends an exact ordinal/name/canonical-payload/hash
   checkpoint at `src/somnus_vm/host/service.py:1159`. Recovery uses only the
   persisted prefix (`:1194`): a missing or replaced checkpointed candidate is
   recovery-required, never silently recreated or adopted. Every
   post-intent storage exception remains recovery-required until reconciliation.

**Consumed boundary:** internal daemon/client requests produce durable registry
rows and physically observed base/overlay evidence. The public
`python -m somnus_vm` parser does not expose this mutation surface.

**Hard stop:** storage ownership does not establish a machine. P4 implementation
now has a distinct journal/runtime owner (BRANCH J), but no disposable QEMU run
has yet established external QMP machine truth. This branch does not establish a
mount, guest agent, model/runtime readiness, or public lifecycle action.

**→ For P3 false-success mechanisms:** `FAILURE_GRAMMAR.md` § P3 storage
authority and recovery failures.
**→ For P3 proof:** BRANCH G.
**→ For the P4 implementation boundary:** BRANCH J.
**→ For the physical machine gate:** the active P4 phase in `PLAN.md`.

---

## BRANCH J: Where is the internal P4 QEMU/QMP runtime owner?

**Entry point:** `src/somnus_vm/daemon_runtime.py:49` → `run()` →
`src/somnus_vm/host/qemu_runtime.py:1447` → `QemuRuntimeOwner`

1. The daemon composition constructs one `QemuRuntimeOwner`, settles its
   incomplete/completed launch records before generic recovery and listener bind,
   and releases daemon-local runtime state only after request handling ends.
2. `qemu_runtime.py` owns the closed runtime path: registry-row selection,
   pinned overlay/base/owner-marker descriptors, exact fd-bound execution argv,
   process/QMP observation, journal advancement, completion, and restart
   recovery. `qemu_runtime_journal.py` owns the durable frozen launch prefix,
   descriptor authority, QMP graph evidence, and checkpoint facts.
3. `qmp.py` and `qmp_identity.py` own the QMP client/protocol and machine
   identity interpretation, including exact fdsets, named-node inventory, and
   recursive blockstats edges; `qemu_process.py`, `qemu_logs.py`,
   `qemu_exec_guard.py`, and `qemu_log_guard.py` own process identity, logs, and
   containment. `qemu_runtime.py` binds QEMU-internal fd numbers through the
   authenticated peer's `/proc` table to P3 storage inodes. `doctor.py`
   separately consumes the selected sandbox option projection without launch.
4. The direct owners are covered by the P4 modules listed in BRANCH G. They are
   implementation and recovery proofs, not a substitute for a real disposable
   `qemu-system-*` machine run. There is no public `start`, `stop`, `destroy`,
   snapshot, or guest-readiness command.

**→ For physical-gate requirements:** `PLAN.md` Phase P4.
**→ For false-success recognition:** `FAILURE_GRAMMAR.md`.
**→ For active state:** `TASK.md` and `STATE.md`.

---

## Quick owner index

| Need | Direct owner |
| --- | --- |
| Public command dispatch | `src/somnus_vm/cli.py:30`, `:76` |
| Canonical protocol root | `src/somnus_protocol/__init__.py:1` |
| Public validation type/exports | `src/somnus_protocol/__init__.py:10`, `:77` |
| Pairwise negotiation/current enforcement | `src/somnus_protocol/version.py:246`, `:397`, `:212` |
| VM lifecycle and migration | `src/somnus_protocol/vm.py:282`, `:2341`, `:3314`, `:3427` |
| Agent endpoints/results | `src/somnus_protocol/agent.py:222`, `:235`, `:308`, `:407` |
| Control/error/event envelopes | `src/somnus_protocol/control.py:449`, `:572`, `:685`, `:771`; `src/somnus_protocol/events.py:47` |
| Compatibility imports | `src/somnus_vm/contracts/vm.py:1`, `src/somnus_vm/contracts/agent.py:1` |
| Strict config owners | `src/somnus_vm/config.py:358-659`, `:748` |
| Planner | `src/somnus_vm/host/planner.py:20`, `:30` |
| Port probe/QEMU argv | `src/somnus_vm/host/ports.py:33`; `src/somnus_vm/host/qemu.py:43` |
| Internal composition root | `src/somnus_vm/daemon_runtime.py:49` |
| Internal P4 runtime owner/journal | `src/somnus_vm/host/qemu_runtime.py:1447`; `src/somnus_vm/host/qemu_runtime_journal.py:3035` |
| Physical-gate coordinator | `src/somnus_vm/host/p4_gate.py:267`; `scripts/run_gate_p4.py:1` |
| QMP/process/log runtime modules | `src/somnus_vm/host/qmp.py:1`; `src/somnus_vm/host/qmp_identity.py:1`; `src/somnus_vm/host/qemu_process.py:1`; `src/somnus_vm/host/qemu_logs.py:1`; `src/somnus_vm/host/qemu_exec_guard.py:1`; `src/somnus_vm/host/qemu_log_guard.py:1`; live disk observation in `qemu_runtime.py` |
| Unix daemon/client/peer auth | `src/somnus_vm/daemon.py:375`; `src/somnus_vm/client.py:95`; `src/somnus_vm/transport.py:292` |
| Durable registry authority | `src/somnus_vm/host/registry.py:1771`, `:2992`, `:3206` |
| Registry/storage operation owner | `src/somnus_vm/host/service.py:289`, `:1159`, `:1542`, `:1569` |
| Physical image store | `src/somnus_vm/host/storage.py:435`, `:808`, `:1003`, `:1090`, `:1308` |
| qemu-img adapter/parent-death guard | `src/somnus_vm/host/images.py:696`, `:774`, `:1087`, `:1145`; `src/somnus_vm/host/exec_guard.py:23` |
| Guest bootstrap | `src/somnus_vm/guest/bootstrap.py:149`, `:336`, `:468` |
| Disposition registry | `src/somnus_vm/topology.py:48` |
| P1 direct checks | `test/vm_lab/test_protocol_*.py` |
| P3 core/adversarial/daemon proof | `test/vm_lab/test_storage_p3.py:687`; `test/vm_lab/test_storage_p3_adversarial.py:194`; `test/vm_lab/test_daemon_storage_p3.py:194` |
| P4 direct non-launch proof | `test/vm_lab/test_p4_gate_coordinator.py` (8/8); `test/vm_lab/test_qemu_runtime_p4.py`; `test_daemon_runtime_p4.py`; `test_qemu_runtime_journal_p4.py`; `test_qmp_p4.py`; `test_qmp_identity_p4.py`; `test_qemu_process_p4.py`; `test_qemu_logs_p4.py`; `test_qemu_exec_guard_p4.py`; `test_storage_runtime_p4.py` |
| Integration smoke | `test/vm_lab/test_vm_lab.py:1196` |
| Repository verifier | `scripts/verify_repository.py:1` |

## Anomalies and gotchas

- A declared record, an open port, a PID, a QMP socket path, or a test pass is
  not VM readiness. The contract requires the evidence but does not observe it.
- `ports_reserved: false` is intentional and must stay explicit.
- The compatibility package must never diverge into a second protocol owner.
- An offer fingerprint or decoded negotiation transcript detects mismatch; it
  does not authenticate a peer or provide replay storage. The live Unix
  transport authenticates the local peer UID; offer authentication and
  negotiation replay remain separate unsupplied semantics.
- A generic control request `payload` or success `result` is authenticated,
  bounded machine data, not permission to log or publish it.
- Public error text, retryability, remediation, category, exit class, and
  evidence summaries are registry-owned. A diagnostic UUID is neither a path
  nor proof that the caller may resolve it.
- Exact legacy v0 decoding migrates only semantically safe records; historical
  weak lifecycle labels cannot be promoted as stronger observed truth.
- A valid base or overlay at the expected pathname is not owned until its exact
  manifest/marker, registry record, operation lineage, device/inode, mode,
  allocation, and qemu backing evidence agree.
- Matching base bytes at a new inode are foreign replacement, not registered
  continuity. Checkpoint recovery likewise may not recreate a missing candidate
  once a physical checkpoint claims it.
- A storage operation that faults after intent persistence cannot become
  terminal `failed`: publication may already have happened, so startup must
  recover/reconcile or refuse service.
- Sparse proof uses qcow2 logical size, filesystem `st_blocks * 512`, and qemu
  `actual-size`; `st_size` alone proves nothing about physical allocation.
- `exec_guard.py` contains a Linux-specific parent-death membrane for native
  image children. Removing it would let a writer outlive the single owner.
- P3 overlays have never been established as mounted or written by a proven
  disposable QEMU machine run. P4 now has a separate runtime journal and
  write-aware observation path, but its direct non-launch tests do not replace
  the `GATE-P4` machine-truth run. The coordinator is implemented and the exact
  source is restored, but full-matrix authority, production exclusions, and
  checkpoint execution remain unconsumed.
- The most feature-rich source may be candidate or lineage. `components/` and
  `quarantine/` do not enter live import graphs by convenience.
- Repair anchors in this document whenever its owner moves; do not preserve
  misleading navigation just because the link checker accepts it.
