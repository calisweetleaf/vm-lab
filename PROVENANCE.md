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

- Which disposable base image becomes the first physical fixture?
- What exact local control protocol and service manager own the daemon?
- Which SQLite journal mode is safe on the actual state filesystem?
- How are per-VM secrets provisioned without argv/TOML/log exposure?
- What behavior contracts bind Kerminal and VM-Go without repository merger?
- What snapshot generation model closes stopped rollback before live work?

## Integration history

| Date | Integration | Result |
| --- | --- | --- |
| 2026-08-04 | source archive reconstitution | 33 files preserved; four v0.1 capabilities promoted |
| 2026-08-04 | direct proof harness | 11 checks passed in sealed baseline run |
| 2026-08-04 | first private Git remote | `origin` set to `git@github.com:calisweetleaf/vm-lab.git`; `main` tracks `origin/main` |
| 2026-08-04 | CodeGraph local index | 47 files, 2,743 nodes, 5,683 edges; local index ignored by Git |
| 2026-08-04 | CTMv3 living repository | entry packet, topology, traversal, failure grammar, continuity, skill router, and CI added |
| 2026-08-05 | P0 execution baseline | strict phase DAG, decisions, platform/fixture contracts, and direct gate validation added |

## Session log

| Date | Agent | Last action | Topology drift | Next action |
| --- | --- | --- | --- | --- |
| 2026-08-04 | Codex + Daeron | Reconstituted and sealed v0.1 | initial topology | activate living repository |
| 2026-08-04 | Codex | Built and verified CTMv3 living repository layer | documented current v0.1; runtime unchanged | P0 is ready when a new runtime goal selects it |
| 2026-08-05 | Codex + Daeron | Closed P0 with machine plan and hostile-fixture validation | execution topology added; promoted v0.1 runtime unchanged | compose the first P1 protocol unit |

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
