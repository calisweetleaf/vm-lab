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

The host, guest, Kerminal/operator, Artifact workers, and VM-Go integrate through
contracts. They do not absorb one another's lifecycle authority.

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
- What behavior contracts bind Kerminal and VM-Go without repository merger?
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

## Session log

| Date | Agent | Last action | Topology drift | Next action |
| --- | --- | --- | --- | --- |
| 2026-08-04 | Codex + Daeron | Reconstituted and sealed v0.1 | initial topology | activate living repository |
| 2026-08-04 | Codex | Built and verified CTMv3 living repository layer | documented current v0.1; runtime unchanged | P0 is ready when a new runtime goal selects it |
| 2026-08-05 | Codex + Daeron | Closed P0 with machine plan and hostile-fixture validation | execution topology added; promoted v0.1 runtime unchanged | compose the first P1 protocol unit |
| 2026-08-05 | Codex + Daeron + Luna + Terra | Closed P1 after independent adversarial protocol review and activated P2 | canonical protocol/settings authority added; public v0.1 host capability unchanged | build the single mutation owner |
| 2026-08-05 | Codex + Daeron + Luna + Terra | Closed P2 and P3 through real daemon, SQLite, qemu-img, recovery, and adversarial gates; activated P4 | internal registry and storage mutation promoted; public CLI remains read-only; no VM launch | compose QMP and process identity without crossing Daeron's deployment authority |

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
