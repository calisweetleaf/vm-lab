# Provenance

## Source boundary

- source archive: `vm_lab.zip`
- source bytes: `391028`
- source SHA256: `63f698f7af3ce989de8885a3b3b68b7214dedbffb1ac6671636b1f5d13829f94`
- source files: `33`
- extraction result: no absolute paths and no parent traversal entries
- preservation result: `33` source files mapped into archive, components, extras, quarantine, or lineage documentation

## Composition record

I did not overwrite the uploaded archive. I created a derivative reconstitution so the exact source remains independently recoverable. The new `src/somnus_vm` package is a COMPOSE surface derived from the source contracts and failure evidence, not a claim that the quarantined code was repaired invisibly.

The promoted package introduces no third-party runtime dependencies. Candidate dependencies are declared separately and remain outside boot.

## Authorship

- operator and architecture owner: Daeron
- reconstitution collaborator: Codex
- composition date: 2026-08-04
- snapshot: `v0.1`

## Snapshot lineage

| Snapshot/state | Date | Claim | Proof |
| --- | --- | --- | --- |
| uploaded `vm_lab.zip` | 2026-08-04 | 33-file source boundary | `source-manifest.json` |
| reconstituted `v0.1` | 2026-08-04 | non-mutating host planning plus verified guest bootstrap | `test/vm_lab/runs/20260805T024125Z/result.json` |
| CTMv3 living layer | 2026-08-04 | repository navigation, continuity, and event enforcement | `scripts/verify_repository.py` plus fresh smoke run |

The CTMv3 layer is post-snapshot repository infrastructure. It does not change
the runtime capability claim in `snapshots/v0.1/manifest.json`.

## Architectural decisions

### ADR-001 — Reconstitute behind one live package

The source archive is preserved exactly; `src/somnus_vm` is the sole promoted
package. Candidate, cold, lineage, and quarantine locations remain outside boot.

### ADR-002 — Withhold lifecycle mutation

Lifecycle, snapshots, scaling, image building, and guest readiness remain absent
until the physical QMP/registry/guest gates in `PLAN.md` pass.

### ADR-003 — Keep authority planes separate

The host, guest, Kerminal/operator, Artifact workers, and the external
`backend/virtual_machine` donor integrate only through explicit semantic
migration contracts. They do not absorb one another's lifecycle authority.

### ADR-004 — Use bounded living-repository ownership

`AGENTS.md` owns entry and operation; `filetree.md` owns navigation;
`ARCHITECTURE_MAP.md` owns traversal; `TOPOLOGY.md` owns invariant structure;
`FAILURE_GRAMMAR.md` owns suspicious-success recognition; `CONTEXT.md` owns
current state; `MEMORY.md` owns durable decisions; `TASK.md` owns one current
unit; this file owns chronology.

## Rejected alternatives

The canonical detailed graveyard is in `MEMORY.md` and the direct technical
evidence is in `docs/RECONSTITUTION_AUDIT.md`. Major rejections:

- restore the original supervisor/orchestrator as live;
- treat fixed ports as safe under one-active-VM policy;
- infer VM identity from PID or socket existence;
- expose snapshot rollback through qcow2 file copying;
- promote no-op scaling or sleep benchmarks;
- import guest cognition, operator shell, or file processors during host boot;
- convert missing physical metal into mocked or synthetic success.

## Open questions

- How are per-VM secrets provisioned without argv/TOML/log exposure?
- Which donor-semantic migration fixtures bind Kerminal and the external
  `backend/virtual_machine` lineage without repository merger or import?
- What snapshot generation model closes stopped rollback before live work?
- What exact QMP/process checkpoint model can adopt or clean a real child after
  daemon death without signaling a foreign PID?
- Which immutable disk-owner fields and mutable QEMU observations become
  separate registry authorities before the first writable boot?

## Integration history

| Date | Integration | Result |
| --- | --- | --- |
| 2026-08-04 | source archive reconstitution | 33 files preserved; four v0.1 capabilities promoted |
| 2026-08-04 | direct proof harness | 11 checks passed in sealed baseline run |
| 2026-08-04 | first private Git remote | `origin` set to `git@github.com:calisweetleaf/vm-lab.git`; `main` tracks `origin/main` |
| 2026-08-04 | CodeGraph local index | 47 files, 2,743 nodes, 5,683 edges; local index ignored by Git |
| 2026-08-04 | CTMv3 living repository | entry packet, topology, traversal, failure grammar, continuity, skill router, and CI added |
| 2026-08-05 | P0 execution baseline | strict phase DAG, decisions, platform/fixture contracts, and direct gate validation added |
| 2026-08-05 | P1 canonical protocols | pure shared authority, compatibility re-exports, exhaustive lifecycle grammar, and installed-wheel consumption |
| 2026-08-05 | P2 single mutation owner | authenticated Unix daemon, schema-v2 SQLite authority, 96 raced mutations, and deterministic metadata recovery |
| 2026-08-05 | P3 persistent storage | schema-v3 image authority, real pinned-base import, two sparse overlays, and exact checkpoint/adversarial recovery |
| 2026-08-07 | P4 internal composition reconciliation | current HEAD contains QEMU/process/QMP/runtime-journal/log-guardian and mutable-observation owners; physical `GATE-P4` remains open |
| 2026-08-07 | P4 bounded implementation closure | all 18 P4 items implemented: descriptor-bound storage handoff, fdset/block graph, doctor consumption, and orphan-emergency recovery; physical `GATE-P4` remains open |

## Session log

| Date | Agent | Last action | Topology drift | Next action |
| --- | --- | --- | --- | --- |
| 2026-08-04 | Codex + Daeron | Reconstituted and sealed v0.1 | initial topology | activate living repository |
| 2026-08-04 | Codex | Built and verified CTMv3 living repository layer | documented current v0.1; runtime unchanged | P0 is ready when a new runtime goal selects it |
| 2026-08-05 | Codex + Daeron | Closed P0 with machine plan and hostile-fixture validation | execution topology added; promoted v0.1 runtime unchanged | compose the first P1 protocol unit |
| 2026-08-05 | Codex + Daeron + Luna + Terra | Closed P1 after independent adversarial protocol review and activated P2 | canonical protocol/settings authority added; public v0.1 host capability unchanged | build the single mutation owner |
| 2026-08-05 | Codex + Daeron + Luna + Terra | Closed P2 and P3 through real daemon, SQLite, qemu-img, recovery, and adversarial gates; activated P4 | internal registry and storage mutation promoted; public CLI remains read-only; no VM launch | compose QMP and process identity without crossing Daeron's deployment authority |
| 2026-08-07 | Codex + Daeron + Luna + Terra | Reconciled continuity around the internally composed P4 frontier and operator correction; ran one current-HEAD aggregate | 33 fixture-independent lanes passed; four storage lanes failed setup because the pinned qcow2 source is absent; physical QEMU gate remains open | wire the existing descriptor plan into the daemon-owned runtime, then run the authorized disposable gate when the fixture is restored |
| 2026-08-07 | Codex + Daeron + Luna + Terra | Closed all bounded P4 implementation items and activated the physical-gate packet | runtime now consumes pinned P3 descriptors through the exec guard and QMP fdset/graph peer proof; doctor and orphan recovery close; no VM launched | restore the exact pinned fixture, record disposable-run authority, execute `TASK-P4-019` |
| 2026-09-05 | Cursor Cloud Agent + Daeron | Closed RECOVERY-R0 from declared-origin fixture identity and current 40/0/0 non-launch aggregate | kernel banner no longer treats historical 40/0/0 as current; fixture is host `/tmp` only | execute `TASK-P4-020` / `GATE-P4` matrix; do not add public lifecycle commands |

### CTMv3 activation evidence

- repository verifier: `PASS`, 404 checks, 0 failures;
- Somnus Python SOTA verifier for `scripts/verify_repository.py`: 16/16
  applicable checks passed; scope/snapshot checks correctly skipped because the
  repository infrastructure script is outside `tools/native/`;
- topology fingerprint:
  `sha256:8976b3cf33027fc181145ff71e058793856f43da7819f1e2a6e265c8beb490f6`;
- v0.1 smoke: 11 pass, 0 fail, 0 skip;
- fresh proof bundle: `test/vm_lab/runs/20260805T032541Z`;
- plan contract: 405 unique execution IDs across P0–P18;
- source preservation: 33/33 byte hashes verified;
- Git remote: `origin/main` configured, but GitHub CLI authentication was not
  available in this session; remote hook inventory and repository settings
  remain unverified;
- GitHub-owned event automation is defined locally in
  `.github/workflows/repository-integrity.yml` and will become active after this
  change is committed and pushed.

### Phase P0 execution-baseline evidence

- direct plan validator: 1,722 observations, 0 failures;
- negative plan fixtures: 8/8 rejected through the real verifier CLI;
- repository verifier before closure: 2,135 observations, 0 failures;
- consumed smoke: 13 pass, 0 fail, 0 skip;
- evidence bundle: `test/vm_lab/runs/20260805T054841Z`;
- Somnus SOTA verifier: 16/16 applicable checks passed for
  `scripts/verify_repository.py` and `test/vm_lab/test_vm_lab.py`;
- platform observation: Python 3.12.3, QEMU/qemu-img 8.2.2, Linux x86_64,
  operator KVM access present, canonical disposable base image absent;
- runtime claim: unchanged v0.1; no lifecycle mutation command was added;
- snapshot claim: `snapshots/v0.1/manifest.json` remained immutable.
- closure correction: after P0 transitioned to complete, the hostile
  multiple-current-phase fixture still targeted P1 and therefore stopped being
  hostile; failed runs `20260805T055202Z` and `20260805T055206Z` preserved the
  detection. The fixture now promotes pending P2 alongside ready P1, making the
  rejection independent of the previously current phase. Run
  `20260805T055238Z` then exposed the same stale-phase assumption in the
  completed-without-evidence fixture; it now completes unevidenced P1 work
  instead of reasserting already evidenced P0 work.
- final post-transition validation: 1,733 plan observations, 2,174 repository
  observations, and 13/13 consumed checks passed;
- final closure bundle: `test/vm_lab/runs/20260805T055444Z`;
- evidence reconciliation: `SOTA_RUN.md`, its JSON result, the topology
  fingerprint, the P0-complete/P1-ready transition, and both sealed manifests
  agree.

### Foundational-intent ingestion and frontier preparation

- Daeron supplied `Somnus-Core-Ideals.md`, the 2025 foundational architecture
  source, and `tree-codebase.md`, a generated point-in-time inventory;
- the living entry and navigation surfaces now link both artifacts without
  confusing the generated inventory with semantic ownership;
- `CONTEXT.md` records the durable AIPC split: provision/install the persistent
  computer, then deploy and operate versioned guest runtime payloads inside it;
- the operator shell remains a high-value P12/Kerminal candidate for human and
  model operation, while the one-mutation-owner and no-QEMU-from-operator
  boundaries remain intact;
- dated scale, warm-pool, provider, Docker, and one-VM-per-chat examples remain
  unimplemented intent until a current gate and physical evidence promote them;
- runtime, snapshot, active task, and sealed v0.1 claims were not changed.

### Operator correction — foundational intent and dual harness

- Daeron corrected the classification of `Somnus-Core-Ideals.md`: it is
  governing product intent, not passive historical lineage;
- the target is one persistent qcow2 bound to one model, installed as a real
  Ubuntu computer and then populated or updated through a distinct resident
  weight/harness deployment pass;
- QEMU and Docker remain implementation mechanisms for the persistent computer
  and one-Artifact-per-execution boundaries rather than the product definition;
- cognition remains distributed across two memory systems, ACE, ASPS, cache,
  model weights, files, and an operational digital twin; it must not be
  collapsed into one generic cognition module;
- the advanced terminal is the intended Master Control Panel for a broad human
  harness and a narrower model harness;
- `NOTEPAD.md` was created as Codex's repository-local scratch surface for
  questions, errors, hypotheses, and ideas. It is explicitly non-authoritative,
  and settled conclusions must move to their owning canonical documents;
- no candidate code, runtime capability, lifecycle command, or sealed snapshot
  claim was promoted by this correction.

### Phase P1 protocol closure and P2 activation

- `src/somnus_protocol/` is now the dependency-free canonical shared protocol
  authority; `src/somnus_vm/contracts/` contains exact compatibility re-exports
  rather than a second implementation;
- the consumed boundary contains strict control, error, event, agent, settings,
  and VM records, including 12 VM schemas, 16 states, 48 legal edges, and 48
  exact evidence-requirement entries;
- the real host planner and CLI consume canonical immutable records, and the
  packaging proof imports the same identities from an installed wheel outside
  the checkout;
- independent Terra review found and Luna/root closed malformed-timezone error
  translation, missing package-root exports, and equal-time process
  false-freshness rather than treating the first green run as closure;
- P1 gate evidence is
  `test/vm_lab/runs/20260805T182129Z/result.json` with 19/19 consumed checks;
- the empty `20260805T182614Z` attempt is preserved: scope validation correctly
  rejected the unsupported `COMPOSE + EDIT` mode before proof execution, and
  the harness now validates scope before creating a run directory;
- failed run `test/vm_lab/runs/20260805T182738Z/result.json` is preserved: it
  exposed a phase-specific hostile fixture after P2 activation, and the fixture
  now selects a conflicting pending phase independently of the active phase;
- final post-transition evidence is
  `test/vm_lab/runs/20260805T182839Z/result.json`: 19 pass, 0 fail, 0 skip;
- the post-transition plan validator recorded 1,808 observations with zero
  failures, and the repository verifier recorded 2,222 checks with zero
  failures;
- the reconciled topology fingerprint is
  `sha256:f80e23e071f7fdf8242f8d43a30888fca5cae56377ba4901c3ca91b2bd8ecb3f`;
- P2 is active under `TASK-P2-001`; no daemon, registry mutation, QEMU
  lifecycle, guest readiness, or physical-machine capability was promoted by
  P1 closure.

### Phase P2 single-owner closure and P3 activation

- `somnus-vm-daemon` is the one unprivileged local composition root for the
  kernel-authenticated AF_UNIX transport, schema-v2 SQLite registry, and
  serialized mutation service;
- the daemon persists operation intent, idempotency, owned resources,
  checkpoints, and observed metadata completion in one authority rather than
  growing a second supervisor or writer;
- the physical gate used twenty-four independent declarers and ninety-six
  total client-process mutations, then killed the real daemon at all twelve
  metadata checkpoint windows;
- registry integrity, migration backup, process-held writer exclusion,
  multi-VM collision safety, append-only events, and deterministic recovery
  remained consistent;
- P2 gate evidence is
  `test/vm_lab/runs/20260805T194938Z/result.json`: 23 pass, 0 fail, 0 skip;
- P2 promoted metadata mutation only. It did not create a qcow2, launch QEMU,
  assert process identity, mutate a lifecycle, or expose a public lifecycle
  command.

### Phase P3 image/storage closure and P4 activation

- the clean live replacement for the candidate image manager is split between
  `src/somnus_vm/host/images.py` (manifest and bounded `qemu-img` truth) and
  `src/somnus_vm/host/storage.py` (daemon-owned publication and recovery);
- the pinned Ubuntu 24.04 Minimal amd64 fixture descriptor binds source SHA256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`;
- real `/usr/bin/qemu-img` 8.2.2 verified one immutable base and created two
  independent sparse 100 GiB overlays with complete backing-chain,
  virtual-size, allocation, mode, marker, device/inode, generation,
  ownership-token, and schema-v3 registry evidence;
- six core, eight adversarial, and one real daemon/client storage tests covered
  seven base-import plus seven overlay-create termination checkpoints,
  symlink/hard-link and same-byte/new-inode rejection, foreign-final
  non-adoption, missing/replaced-stage refusal, post-publication recovery, and
  native-child parent-death containment;
- `P3-013` and `P3-014` close as a narrow executable membrane: arbitrary disk
  import, clone, export, detach, and deletion remain distinct unimplemented
  operations; the service accepts only `storage.import_base` and
  `storage.create_overlay`;
- the immutable P3 gate is
  `test/vm_lab/runs/20260805T211740Z/result.json`: 26 pass, 0 fail, 0 skip;
- post-transition proof
  `test/vm_lab/runs/20260805T212927Z/result.json` reran the same 26 consumed
  boundaries with P3 complete and P4 the sole active phase; its plan validator
  recorded 1,945 direct observations with zero failures;
- the final living-repository verifier recorded 2,449 observations with zero
  failures after navigation, projections, the generated inventory, session
  state, and topology fingerprint were reconciled;
- failed pre-gate bundle `test/vm_lab/runs/20260805T211613Z/result.json` is
  preserved because its installed-wheel assertion still described the P2
  registry-only daemon after image ownership was promoted; the consumed
  packaging boundary was corrected and rerun rather than hidden;
- P3 did not launch QEMU, mount an image, boot Ubuntu, authenticate a guest,
  mutate a production AIPC, or promote a public lifecycle command. P4 now owns
  the still-unproven process/QMP boundary, and Daeron remains the only real VM
  deployment authority;
- the P4-ready topology fingerprint is
  `sha256:6bebc32cb5341c2b318e210093675843d62e0ba9a71f5bd92c913323b833b05e`.

### 2026-08-07 — Manager/operator correction and P4 continuity reconciliation

- Current repository HEAD is `3dff065dbf031605a90ed6d1f8a11fa85e46b42a`
  (`v3-updates`). The source tree includes the internal P4 owners for QEMU
  planning/runtime, QMP transport and identity, process identity/guarding,
  launch journaling/checkpoints, redacted log handling, and mutable disk
  observations. This establishes substantial internal composition, not the
  physical gate.
- P0 through P3 remain the settled promoted baseline at their recorded gates.
  The sealed P3 gate is
  `test/vm_lab/runs/20260805T211740Z/result.json` (26 pass, 0 fail, 0 skip);
  the recorded post-transition run is
  `test/vm_lab/runs/20260805T212927Z/result.json`.
- `TASK-P4-001` remains the sole active unit. No disposable QEMU launch, QMP
  machine-truth capture, guest readiness, public lifecycle command, P5
  networking, or later phase was claimed. The physical `GATE-P4` response
  capture remains authority-gated and open.
- Daeron corrected the operating posture: Codex is the manager/operator and
  technical collaborator, not merely a worker lost inside one evidence lane.
  The preceding P4 effort over-weighted adversarial descriptor/evidence work
  and failed to keep the compiled repository continuity surfaces current. The
  useful findings remain retained as constraints; the overproof direction is
  discarded as a substitute for phase management, integrated progress, or docs.
- Current-HEAD aggregate
  `test/vm_lab/runs/20260807T091421Z/result.json` recorded 33 pass, 4 fail,
  0 skip. All fixture-independent P4 component lanes passed. The four storage
  lanes stopped at setup because the pinned 264,306,688-byte qcow2 source is
  absent from `test/vm_lab/fixtures/images/`; its tracked manifest remains.
  This is an honest current reproducibility blocker, not a retroactive
  invalidation of the sealed P3 gate.
- Final post-write verification passed 2,566 repository observations and 2,007
  plan-only observations with zero failures; `git diff --check` also passed.
  No physical QEMU result is asserted.

### 2026-08-07 — P4 implementation closure and physical-gate handoff

- `P4-001` closed in current source. `QemuRuntimeOwner.launch()` opens and
  validates the registry-owned overlay as read/write, the immutable base as
  read-only, and the owner marker as read-only; it hashes base/marker from
  pinned descriptors, records complete descriptor authority, binds the exact
  overlay/base FD numbers into the executed argv, and passes all authorities
  through the exec guard without reopening storage paths there.
- The exec guard now revalidates device, inode, uid, gid, mode, link count,
  size, allocation, mtime, ctime, and access mode at READY and immediately
  before exec. It rehashes immutable base/marker content at release and makes
  only the overlay/base descriptors inheritable by QEMU.
- QMP consumption now issues typed `query-fdsets`,
  `query-named-block-nodes`, and recursive `query-blockstats` calls. The
  runtime requires two exact opaque fdset roles, four exact named nodes, the
  root-to-overlay/base graph edges, and `/proc/<authenticated-peer-pid>/fd`
  device/inode/access agreement with P3 storage authority across both
  stabilization samples.
- `P4-004` closed through public doctor consumption of the selected QEMU's TCG
  or KVM sandbox option projection. The real local QEMU 8.2.2 parser accepted
  the TCG projection without launching a VM; hostile unavailable, rejecting,
  silent, and output-flood executables fail with structured remediation.
- `P4-017` closed through durable `orphan_emergency` recovery. An ambiguous
  child persists `cleanup_state=orphaned`, exact process identity, and the
  recovery disposition across a fresh SQLite replay rather than losing the
  child under generic unknown cleanup.
- Focused proof passed: exec guard 8/8, runtime journal 9/9, QMP transport 10/10,
  QMP identity 10/10, runtime cleanup 4/4, and daemon/runtime composition 4/4.
  The descriptor-composition proof uses real `qemu-img` and real SQLite/P3
  mutation against a tiny local qcow2 without launching `qemu-system`; it is
  not a replacement for the tracked P3 fixture.
- All 18 P4 implementation items are now complete in `PLAN.md` and
  `docs/plan-index.json`. `GATE-P4` remains open because the exact pinned
  264,306,688-byte source is absent, no disposable QEMU execution was
  performed, and the actual QEMU response/adoption matrix remains unobserved.
- Current-HEAD aggregate
  `test/vm_lab/runs/20260807T114854Z/result.json` recorded 35 pass, 4 fail,
  and 0 skip. Every fixture-independent lane passed, including all bounded P4
  owners and the complete plan/index contract. The four failures are exactly
  the P3/P4 storage lanes that reject the absent pinned source during setup;
  no substitute fixture or synthetic gate evidence was introduced.
- Final living-repository validation passed 2,599 observations with zero
  failures; plan-only validation passed 2,026 observations with zero failures;
  `git diff --check` passed. The synchronized topology fingerprint is
  `sha256:a46748e3d8a8c2637f5341923233485a303e02a43ae0eb79d4e1c2ec5585210e`.

### 2026-08-07 — Disposable launch membrane consumed and gate-readiness truth corrected

- Added `src/somnus_vm/host/launch_authority.py` as the typed pre-launch safety
  owner. A permit is minted only from one private exact fixture topology, six
  bound configuration roots, the registered P3 overlay/base identity, a
  canonical fixture marker, and at least one explicit production exclusion.
  It is one-shot and burns before revalidation, including on failure.
- Integrated that authority at the actual `QemuRuntimeOwner.launch()` consumed
  boundary before executable/storage pinning or journal mutation. Launch now
  rejects an absent/invalid permit and embeds the resulting canonical receipt
  in both the durable launch intent and owned-resource record.
- Extended `daemon_runtime.run()` with a Python-only activation hook that
  receives the already-composed registry/service/runtime owners after startup
  recovery and before listener service. The installed daemon CLI and
  authenticated AF_UNIX vocabulary remain unchanged and cannot request launch.
- Focused non-launch verification passed: disposable launch authority 5/5,
  runtime launch journal 9/9, daemon/runtime composition 4/4, Python compilation,
  and exact missing-permit registry preservation. No QEMU VM was launched.
- Corrected the active continuity packet: all eighteen bounded P4 phase items
  are implemented, but the physical gate coordinator is still absent. The
  coordinator must drive the daemon-owned successful launch, raw QMP/log/process
  capture, all journal-checkpoint kill/restart cases, exact adoption/cleanup,
  foreign-process non-signaling, and sealed evidence bundle. The exact pinned
  qcow2 source and Daeron's physical-run authority are also absent.
- Therefore `TASK-P4-019` remains active, `GATE-P4` remains open, P5+ remains
  pending, and no public lifecycle command or physical machine-truth claim was
  promoted.
- Fresh aggregate `test/vm_lab/runs/20260807T114854Z/result.json` registered the
  disposable-authority lane and recorded 35 pass, 4 exact missing-fixture
  failures, and 0 skip across 39 checks. Final repository validation passed
  2,606 observations; plan-only validation passed 2,030; `git diff --check`
  passed. The reconciled topology fingerprint is
  `sha256:739eccc5ce602e8f5945ab2a921ec00cb50a88d1fa08ef37a2610b2aaa5c849b`.

### 2026-08-07 — P4 physical-gate coordinator implementation

- Added `src/somnus_vm/host/p4_gate.py` as the non-public coordinator contract
  over the existing daemon activation, disposable permit, runtime, journal,
  process, log, QMP, and recovery owners. It performs exact private-fixture
  preflight, source/tool identity checks, production exclusions, one-shot
  authorization, checkpoint/recovery callbacks, canonical QMP response-history
  capture, and sealed result validation without creating a second lifecycle
  authority.
- Added `scripts/run_gate_p4.py` as the explicit prepare/preflight/execute/
  recovery operator surface. It is not installed and does not widen the public
  CLI or AF_UNIX vocabulary.
- Focused non-launch coordinator proof passed 5/5; QMP direct proof is now
  14/14, and the current aggregate `test/vm_lab/runs/20260807T121437Z/`
  records 36 pass, 4 fail, and 0 skip across 40 checks. The four failures are
  the exact P3/P4 storage lanes that fail because the pinned source is absent.
- The coordinator is implemented and fail-closed. The exact
  264,306,688-byte `b3064...` source, valid execution phrase/Daeron physical launch
  authority, checkpoint matrix, and actual qemu-system run remain absent;
  `GATE-P4` and P5+ remain open. No public lifecycle command was promoted.

### 2026-08-07 — Exact P3 fixture restored and current aggregate green

- Restored the exact manifest-bound source at
  `/tmp/vm-lab-p3-fixture-20260801.qcow2`; verified size 264,306,688 bytes,
  mode `0600`, uid `1000`, nlink `1`, and SHA-256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.
- Fresh current-HEAD aggregate `test/vm_lab/runs/20260807T123747Z/` passed
  40/40 with zero failures and zero skips; plan/index validation passed
  2,037/2,037. The preceding `20260807T121437Z` 36/4 run remains historical
  evidence only.
- `GATE-P4` remains open despite full local proof. The remaining boundaries are
  valid execution phrase/Daeron physical launch authority, explicit production
  exclusion paths, actual qemu-system success truth, and the complete
  checkpoint kill/restart adoption/cleanup matrix. No VM was launched and no
  public lifecycle command was promoted.

- Final post-sync repository verifier passed `2,625/2,625` with zero failures;
  direct plan-only validation passed `2,040/2,040` after the final doc/index
  sync (the aggregate bundle records 2,037 plan-index observations);
  `git diff --check` remains the final diff hygiene check. Topology fingerprint is
  `sha256:353d8129bdb59bf1c45abf77268ca9de18e6c15d931ef10b38dc2699cdddbdf1`.

### 2026-08-07 — Canonical full P4 matrix coordinator landed

- Extended `src/somnus_vm/host/p4_gate.py` with the canonical full-matrix
  specification, exact matrix preflight/result validators, and preparation of
  17 isolated compact roots: `00-success` plus every exact `CHECKPOINT_ORDER`
  scenario. Each scenario retains its own exact fixture/config/runtime roots.
- Extended `scripts/run_gate_p4.py` with `prepare-matrix`, `preflight-matrix`,
  and `execute-matrix`. Execution is strictly serial; resume is permitted only
  from sealed validated scenario results; failures stop execution and preserve
  the matrix failure record. Successful runtime-log bytes are sealed in each
  scenario result and the aggregate matrix result.
- Per-scenario validation now requires exact checkpoint prefixes, conditional
  adoption/cleanup evidence, and cleanup scope
  `runtime-process-only;fixture-retained`; no fixture/P3 storage deletion is
  claimed. The deterministic phrase is only an accidental-execution fence,
  not authentication or proof of Daeron authority.
- Non-launch coordinator proof is now 8/8. Fresh aggregate
  `test/vm_lab/runs/20260807T123747Z/` is 40/40 pass, 0 fail, 0 skip; plan/index
  records 2,040 observations. No qemu-system VM or matrix has launched;
  full-matrix authority, explicit production exclusions, physical execution,
  and `GATE-P4` remain open.

- Post-matrix documentation verification: repository verifier passed 2,625/2,625;
  direct plan-only validation passed 2,040/2,040; `git diff --check` passed.
  Final topology fingerprint: `sha256:8eedcd0a4dfc60623b7c0d86182d100f4b15988c1b77d6d8a29952e8afbdb449`.

### 2026-08-07 — Attachment adoption and matrix-authority ordering audit

- Re-read all three original goal attachments and verified their requirements
  are adopted in `AGENTS.md`: exhausted-owner SOTA++, causal density,
  operator-agency/thoroughness, fail-loud/no-fallback engineering,
  workflow/continuity synchronization, clean imperative handoff, and filetree
  anti-tunnelvision.
- Corrected current handoff ordering. Daeron authority and exact production
  exclusions precede `prepare-matrix`; preparation creates the frozen matrix
  UUID and therefore the deterministic execution phrase. The phrase is not an
  external credential, authentication, or proof of authority.
- No runtime/source implementation changed and no QEMU process launched. The
  correction changes only governing continuity and the next operator action;
  `GATE-P4` remains open.

### 2026-08-07 — Real P4 matrix preparation and preflight

- Performed read-only host ownership discovery across mounts, libvirt/QEMU
  process state, `/home/daeron`, `/media`, `/mnt`, `/var/lib/libvirt`, `/srv`,
  `/opt`, and `/tmp`. No deployed VM/domain/production qcow2 was present; the
  manifest-bound disposable source was the only qcow2.
- Selected conservative, directly observed exclusion owners
  `/home/daeron` and `/media/daeron/usb-128gb`. Prepared the matrix under the
  separate `/tmp` tmpfs without inferring a nonexistent production AIPC path.
- `prepare-matrix` created matrix
  `b9aca76a-a016-4d0c-9002-f0a90f383b21` at
  `/tmp/vm-lab-gate-p4-matrix-20260807T124705Z`; canonical spec SHA-256 is
  `9567494af6899c1d2ce5353845950b78a087f4fcf4deb22d67db54950542a104`.
  `preflight-matrix` validated all 17 scenario identities and reported
  `qemu_launched: false`.
- A direct scan proved no registry, control/QMP socket, PID, result, or
  qemu-system process exists in the matrix. This crosses the non-launch
  preparation boundary only; Daeron physical execution authority and
  `GATE-P4` remain open.

### 2026-08-24 — Canonical completion-plan and evidence-authority correction

- Daeron authorized a no-fallback completion plan rather than a parallel roadmap.
  `PLAN.md` now names RECOVERY-R0 as a non-promoting prerequisite to P4 physical
  execution and retains the P0–P18 DAG as the complete destination.
- Live evidence, not August 7 continuity claims, is authoritative: the exact
  fixture is absent, `SOTA_RUN.md` records 35 pass / 5 fail, and the source
  manifest diverges at one tracked preserved artifact. Historical P3/P4 bundles
  remain preserved provenance only.
- The true external first-party donor is `backend/virtual_machine`. Its AIPC
  semantics are mapped to P5–P14; it is never imported or treated as live
  dependency. Its weak lifecycle and fallback mechanisms are explicit
  non-migration examples.
- No runtime module, source artifact, fixture, snapshot, QEMU process, or
  external service was mutated by this plan packet.

### 2026-08-24 — RECOVERY-R0 source preservation repaired; fixture remains

- Read-only authority comparison found the root `vm-lab.zip` is a later bundle
  (`b90a74…38e1`) containing the divergent browser bytes, not the manifest's
  declared source archive. Two independent retained local archives and original
  Git blob `0c1383d6c4e634689d99871a11952471d569bafe` instead agreed on the
  manifest-bound `d27779…cf644e` bytes.
- Restored only that preserved candidate artifact atomically. The later bytes
  remain retained in Git history and the later zip; `source-manifest.json` was
  not rebaselined.
- Fresh aggregate `20260825T043503Z` records 36 pass, 4 fail, 0 skip. Source
  preservation and plan contract pass; the four remaining failures are only the
  absent exact P3 fixture. No QEMU, matrix, snapshot, or external action ran.

### 2026-09-05 — Operator-kernel restoration after b11880c deletion

- `b11880c` ("Add file tree documentation and update requirements files")
  deleted `PLAN.md`, `CONTEXT.md`, `MEMORY.md`, `SOTA_RUN.md`,
  `Somnus-Core-Ideals.md`, `OPSEC.md`, `workflows-new.md`, the repo-local
  skill, and `.gitignore`, and emptied the stdlib-only packaging contract —
  4,433 lines of execution authority removed by an unrelated-looking commit.
- `1b0121c` restored the last-good copies from `ae0b077`, kept the Cloud Agent
  `.cursor/` environment from PR #1, moved the added filetree/requirements
  docs under `docs/old-filetrees/` and `docs/requirements-singles/`, and
  retargeted every affected link. Residual diff `ae0b077..HEAD` contains no
  runtime-source change.
- Recorded here retroactively by the push-trigger review; the restoration
  commit itself carried no provenance row.

### 2026-09-05 — Push-trigger drift review; plan-contract repair

- Trigger metadata was synthetic (zero commits, placeholder SHAs); the review
  ran against observed git truth at HEAD `1b0121c`. Verifier: 2628/2628 on the
  Cloud Agent Linux host (Python 3.12.3, qemu-img/qemu-system 8.2.2).
- First smoke of the restored tree, `test/vm_lab/runs/20260905T101438Z/`
  (35 pass / 5 fail), exposed latent `ae0b077` drift: PLAN.md §3.1 completed
  `RECOVERY-R0-00x` IDs were outside the closed plan-contract grammar and
  unbacked by the 19-phase index. All sealed bundles predate that PLAN.md
  edit, so the violation was never previously observed.
- Repaired the smallest owning layer: §3.1 items renamed to `BASE-014`…
  `BASE-018` with the mapping and failure evidence recorded in PLAN.md. No
  validator, index schema, runtime source, fixture, or snapshot changed.
- Fresh aggregate `test/vm_lab/runs/20260905T101913Z/` is 36 pass, 4 fail,
  0 skip; `plan_contract` validates 410 unique execution IDs; the four
  failures remain exactly the fixture-absence lanes. The pre-repair bundle is
  preserved as drift evidence. No QEMU process launched; `GATE-P4` remains
  open; the public CLI remains read-only.

### 2026-09-05 — Push-trigger review of PR #2 merge; kernel anchor repair

- Reviewed push `1b0121c..1d0e118` (the plan-grammar repair and continuity
  from the prior push-trigger run, merged as PR #2). Docs-only; hard bans
  held; trigger metadata matched observed git truth for the first time.
- Independently reproduced the pushed proof on this host: verifier
  2628/2628, plan DAG 2042/2042, fresh aggregate
  `test/vm_lab/runs/20260905T104044Z/` at 36 pass / 4 fail / 0 skip with
  `plan_contract` green at 410 unique execution IDs; the four failures are
  exactly the fixture-absence lanes.
- Repaired stale AGENTS.md §9 proof prose: sealed bundle `20260807T123747Z`
  was still framed as "The current-HEAD aggregate" with the fixture
  present-tense "restored", contradicting the 2026-08-24 RECOVERY-R0
  correction already carried by every other living surface. It is now a
  named sealed fixture-present historical claim; current-proof authority
  routes to `SOTA_RUN.md`. Docs-only; no runtime, gate, or routing change.
- No QEMU process launched; `GATE-P4` remains open; the public CLI remains
  read-only; `TASK-P4-019` remained the active unit at that review.

### 2026-09-05 — RECOVERY-R0 fixture authority closed from declared origin

- Canonical `SHA256SUMS` for Ubuntu minimal noble `release-20260801`
  `ubuntu-24.04-minimal-cloudimg-amd64.img` equals the repository manifest
  SHA-256 `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.
  Host file `/tmp/vm-lab-p3-fixture-20260801.qcow2` is regular, mode `0600`,
  uid 1000, nlink 1, size 264,306,688; `qemu-img` reports qcow2 virtual-size
  3,758,096,384 with no backing file. This is declared-origin acquisition, not
  a substitute image and not a repository blob.
- `python scripts/verify_repository.py` passed 2633/2633 on the kernel-repair
  revision. `PYTHONPATH=src python test/vm_lab/smoke.py` published
  `test/vm_lab/runs/20260905T111215Z/` at 40 pass, 0 fail, 0 skip. Failed
  fixture-absence bundles including `20260905T104044Z` remain.
- `BASE-017` and `BASE-018` are checked. `TASK-P4-019` is complete.
  `TASK-P4-020` is ready. No qemu-system process ran; `GATE-P4` remains open;
  the public CLI remains read-only.
