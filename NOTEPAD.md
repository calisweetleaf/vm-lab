# Codex Operator Notepad

**Purpose:** active external reasoning surface for VM Lab work  
**Authority:** scratch only; current code, `AGENTS.md`, `TASK.md`, `PLAN.md`,
`STATE.md`, and explicit operator corrections outrank this file  
**Update rule:** record questions, errors, hypotheses, code observations, and
integration ideas here instead of carrying them implicitly. Move settled
decisions into the owning canonical document and mark the note resolved.

---

## 2026-08-07 P4 launch-authority integration / gate-runner correction

- `QemuRuntimeOwner.launch()` now refuses absent or invalid disposable launch
  authority before launch mutation. The one-shot permit binds the exact private
  fixture, configuration-root identities, P3 overlay/base facts, canonical
  marker, and explicit production exclusions; it burns on failure and returns
  a canonical receipt persisted in the runtime launch journal.
- `daemon_runtime.run()` now has a Python-only activation seam after recovery
  and before listener service. The normal daemon CLI and AF_UNIX request
  vocabulary still expose no launch operation.
- Focused non-launch proof is green: disposable authority 5/5, runtime journal
  9/9, daemon/runtime composition 4/4. No `qemu-system` process ran.
- Earlier wording that the exact fixture and authority were the only remaining
  blockers was incomplete. The explicit physical-gate coordinator is also
  missing. It must drive daemon-owned launch, raw QMP/log capture, all sixteen
  checkpoint kill/restart cases, exact adoption/cleanup, foreign-process
  non-signaling, and sealed result artifacts without becoming a second owner.
- `TASK-P4-019` remains active. All eighteen bounded phase items are implemented;
  `GATE-P4` is not runnable or closed until the coordinator exists, the exact
  pinned qcow2 source is restored, and Daeron authorizes the disposable run.

## 2026-08-07 P4 bounded implementation closure — next operator packet

- **Execute next:** restore the exact qcow2 source bound by
  `ubuntu-minimal-noble-amd64-20260801.json`, record Daeron's authority for one
  disposable QEMU run, then execute `TASK-P4-019` through the daemon-owned
  runtime. Do not substitute another image, expose a public lifecycle command,
  or claim guest readiness.
- **Current implementation truth:** all 18 P4 items are complete. The launch
  owner now pins overlay/base/owner-marker descriptors, uses the exact
  two-fdset/four-node argv rewrite, passes all authorities through the exec
  guard, and journals the descriptor and QMP graph evidence. Doctor consumes
  the selected QEMU sandbox option set. Ambiguous child recovery persists an
  orphan emergency with exact replay identity.
- **Physical truth still absent:** no `qemu-system` VM was launched. The actual
  QEMU 8.2.2 rendering of named block nodes and recursive blockstats, serial
  output, checkpoint-kill adoption/cleanup, and complete `GATE-P4` evidence
  remain unobserved.
- **Focused evidence:** exec guard 8/8; QMP identity 10/10; QMP transport 10/10;
  runtime journal 9/9; daemon/runtime composition 4/4; runtime cleanup 4/4;
  doctor and plan-contract focused checks green. Real `qemu-img` created and
  imported a tiny local qcow2 only for the descriptor-consumption composition
  test; it is not the pinned P3 fixture and not a gate substitute.
  Current-HEAD aggregate `20260807T114854Z` is 35 pass, 4 fail, 0 skip; every
  fixture-independent lane passed and the four exact storage lanes fail loudly
  because the pinned source bytes are absent.
- **Known aggregate blocker:** the pinned 264,306,688-byte qcow2 source remains
  absent. Four storage lanes therefore fail loudly during setup. Preserve this
  failure; do not skip, mock, or synthesize it away.
- **Non-gate static-quality note:** the optional generic Somnus Python verifier
  is not fully green across every large P4 owner. It misclassifies standard
  modules such as `selectors`/`fcntl` for import grouping and reports missing
  docstrings in existing dense dataclass/serializer surfaces; `qmp.py` also has
  three broad exception catches that require semantic cleanup-path review
  before any rewrite. Do not change runtime behavior merely to appease the
  generic checker, but do not hide the findings. This does not replace or block
  the physical `TASK-P4-019` gate unless review finds swallowed machine truth.

---

## 2026-08-07 operator correction and continuity checkpoint

- **Current repository fact:** HEAD is `3dff065dbf031605a90ed6d1f8a11fa85e46b42a`
  (`v3-updates`). P0-P3 are closed at their recorded gates. P4 is the active
  phase and its internal QEMU/process/QMP/journal/log/observation substrate is
  substantially composed in the source tree.
- **Still unresolved:** `GATE-P4` physical QEMU execution and machine-truth
  capture remain open. No disposable QEMU launch, guest readiness, P5, or
  later capability is claimed here.
- **Operator correction:** Codex should manage the work as an operator and
  technical collaborator: keep the phase destination visible, decompose work
  into bounded lanes, integrate results, and update the repository continuity
  surfaces while work advances. Do not vanish into a single worker loop.
- **Overproof correction:** the prior P4 pass spent too much effort expanding
  adversarial evidence before maintaining the compiled docs. The descriptor and
  release-fence findings below remain useful constraints, but they are scratch
  evidence, not a reason to claim P4 closed or to repeat proof after it stops
  changing the next implementation decision.
- **Fresh evidence:** current-HEAD aggregate
  `test/vm_lab/runs/20260807T091421Z/result.json` is 33 pass, 4 fail, 0 skip.
  All fixture-independent P4 lanes passed. The four storage lanes stopped in
  setup because only the tracked fixture manifest is present; the pinned
  qcow2 source bytes are absent. This is not `GATE-P4`.
- **Compiled phase progress:** `PLAN.md` and `docs/plan-index.json` now record
  15/18 bounded P4 implementation items complete. The remaining items are the
  consumed launch handoff (`P4-001`), doctor/host incompatibility consumption
  (`P4-004`), and named orphan-emergency authority (`P4-017`); the actual-QEMU
  gate remains open.

---

## Locked system model

These are the current working invariants supplied or corrected by Daeron:

1. `Somnus-Core-Ideals.md` is governing product intent, not passive historical
   lineage. Runtime evidence determines implementation status, not whether the
   architecture remains wanted.
2. The AIPC is a real persistent computer. The target pairing is **one qcow2,
   one model**, with identity and resident state intended to remain bound.
3. Delivery has two distinct passes:
   - **install/provision:** create the Ubuntu Server 24.04 computer, install the
     VM substrate and GUI/operator necessities, reboot, and prove that the
     computer returns;
   - **resident-system deployment:** reuse that computer, stream the model
     weights through the VM, and place/version the guest harness and tool files
     inside the qcow2 without rebuilding the operating system.
4. QEMU and Docker are mechanisms:
   - QEMU supplies the persistent full-computer boundary and GUI;
   - Docker supplies disposable execution for the one-Artifact-per-execution
     principle;
   - neither mechanism is the product architecture by itself.
5. There are two harnesses:
   - the **human harness**, centered on the advanced terminal/Master Control
     Panel and allowed a broader operator capability set;
   - the **model harness**, kept close to the resident model and exposed only
     the capabilities intended for model operation.
6. There is not one monolithic cognition module. The resident cognitive system
   is composed from multiple cooperating owners:
   - `memory_core.py`;
   - `memory_interconnect.py`;
   - `ace_dev_environment.py`, supervising and enabling filesystem-mediated
     development;
   - `modifying_prompts.py` / ASPS;
   - `system_cache.py`;
   - the model weights and local files they organize around.
7. The VM agent/digital twin exists to establish and maintain operational
   truth. It is not the owner of cognition and must not become a second host
   lifecycle authority.
8. `components/operator/ai_advanced_shell.py` is the lineage for the eventual
   Master Control Panel. “AI” denotes automation and model-capable operation,
   not a model-only shell.
9. The shell may support MCP servers and a human-only capability superset.
   Native copies of BB7-like tools are not automatically necessary merely
   because the shell can load them.
10. Preserve candidate code until its actual role is classified. Do not drag
    everything into the live import graph, but do not delete sophisticated
    systems merely because modern infrastructure can replace one old
    integration mechanism.

---

## Current frontier

- Phase: `P4`
- Unit: `TASK-P4-019`
- State: active
- P1 through P3 are closed at their recorded gates. Public host capability
  remains `doctor`, `topology`, and non-mutating `plan`.
- All 18 bounded P4 implementation items are complete; only the authorized
  disposable-machine gate remains.
- No disposable QEMU VM has been authorized or launched in the current P4
  execution.

---

## Direct code observations

These are candidate-source observations, not promoted capability claims:

- `ai_advanced_shell.py::AIShellSettings` already names VM connection, Docker,
  native tools, memory, cache, autonomous prompting, and file-processing
  surfaces. That makes the file real Master Control Panel lineage rather than
  a generic terminal donor.
- `AdvancedVMManager.execute_in_vm()` currently describes host execution,
  prefers `artifact_system.run_command()`, and otherwise uses
  `asyncio.create_subprocess_shell()`. Its name is misleading and its current
  command-string path cannot become the authenticated guest execution
  contract unchanged.
- `AdvancedVMManager.initialize()` currently initializes Docker eagerly.
  Eventual Master Control Panel startup should not fail merely because the
  optional Artifact/container overlay is unavailable.
- `modifying_prompts.py::PromptLayer` already distinguishes core identity,
  persistent memory, contextual frame, working memory, active reasoning, and
  performance metadata. Preserve that layered intent when the resident
  ordering is made explicit.
- `digital_twin.py` currently allows missing authentication configuration and
  binds its development server to `0.0.0.0`. It is valuable operational
  lineage, but P6 cannot install that network/auth behavior unchanged.
- None of the candidate observations above changes their current disposition:
  they remain outside the promoted boot import graph until their named gate.

---

## Operator resolutions and remaining variables

These corrections are authoritative. They do not block P1 unless a concrete
wire contract truly requires a field or state decision.

1. **No fixed resident ordering.** The earlier linear order was a metaphor.
   Preserve the modular systems and determine useful interaction topology by
   running and experimenting with the production code.
2. **Cognitive path.** The working conceptual path is
   `weights -> Advanced Kerminal -> ASPS cognitive workspace <-> memory
   systems`. ASPS is the central cognitive-workspace concept and must later
   integrate more deeply with both memory systems and result flow.
3. **ACE is an OS-agency pillar.** ACE is not a memory mediator and does not
   sit near the model in the cognitive graph. It teaches/enables the persistent
   system to operate its real OS, including human-like system administration.
   Credential changes remain an intelligent resident action assigned by
   Daeron, not an automatic bootstrap trick.
4. **System cache is parallel infrastructure.** It carries execution and result
   flow; it is not another model-near cognition layer.
5. **Persistent pairing remains.** Model weights live inside the qcow2 so they
   can operate with resident memory. Exact immutable-version semantics are not
   a present blocker and may later intersect the Ghost Model system.
6. **No automation of non-tool cognition.** The runtime must not encode
   scripted substitutes for intelligent autonomous decisions merely to make a
   workflow deterministic.
7. **Locality contract.** Local operation is canonical. A non-local path is
   acceptable only with complete functional parity and no architectural
   shortcuts.
8. **Capability membrane is configurable.** Human-only, model-callable, guest,
   host, and Artifact-owned capability access will be configuration rather
   than a hard-coded guess made during P1.
9. **Local network is structural.** The shell calls the local FastAPI/network
   boundary; filesystem execution, memory connection, system-cache result flow,
   model communication, and future swarm operation use that connective tissue.
   Unrealistic scale limits do not justify deleting multi-agent correctness.
10. **Model loader:** do not pre-salvage or delete it. Observe it when its lane
    becomes active; replacement is likely later.
11. **Three native operator tools remain distinct:** `internal_browser.py` is
    the fast browser, `web_search_research.py` is the deeper research engine,
    and `git_integration_module.py` owns git operations.

Remaining later-gate variables:

- the exact resident deployment manifest for weights and harness files;
- the precise configurable capability policy schema;
- the Artifact result/ingress manifest carried through FastAPI, filesystem,
  memory, and system-cache boundaries;
- the future Ghost Model relationship to the persistent weight identity.

---

## Error and idea log

Use dated entries during execution:

```text
YYYY-MM-DD HH:MM — OBSERVATION | HYPOTHESIS | QUESTION | ERROR | IDEA
Owner:
Evidence:
Effect on outcome lock:
Next discriminating action:
Resolution:
```

### 2026-08-05 (time not retained) — ERROR

**Owner:** root P1 integration lane  
**Evidence:** A temporary-directory cleanup command containing
`rm -rf "$tmp"` was rejected before execution by the command safety hook
because the destructive target depended on an unresolved shell variable.  
**Effect on outcome lock:** None; no filesystem mutation occurred and no P1
claim depended on the rejected command.  
**Next discriminating action:** Use an owning temporary-directory API rather
than a shell-expanded destructive cleanup target.  
**Resolution:** Re-ran the plan-boundary proof through Python
`tempfile.TemporaryDirectory`; cleanup was scoped and automatic, and the
non-mutating public plan evidence passed.

### 2026-08-05 12:03 CDT — IDEA

**Owner:** root swarm integration lane  
**Evidence:** The canonical protocol work separates cleanly into immutable VM
lifecycle authority, real host consumption, control/event envelopes, typed
settings, and wire-format/public-package lanes with disjoint file ownership.  
**Effect on outcome lock:** Positive; independent implementation can proceed
without turning tests or planning into the deliverable.  
**Next discriminating action:** Keep cycling completed Terra/Luna workers into
non-overlapping production lanes, then converge only through the installed
wheel and actual CLI consumer.  
**Resolution:** The disjoint swarm converged. Luna owned VM lifecycle repairs
and repository documentation; Terra independently attacked control and VM
contracts; root owned integration, installed-wheel consumption, gate evidence,
and phase transition. The first green smoke was not promoted: adversarial
findings were repaired and re-probed before `GATE-P1` closed.

### 2026-08-05 12:18 CDT — ERROR

**Owner:** P1 control-envelope authority  
**Evidence:** An independent Terra audit constructed valid control/error
objects whose canonical JSON exceeded the decoder's 65,536-byte limit; one
maximal error shape also exceeded its own serializer item budget. The same
audit proved ADR-0005 could be bypassed through public text surfaces and
unregistered error-code strings, and found that `ControlEvent.sequence` had no
encoded ordering domain.  
**Effect on outcome lock:** `GATE-P1` remains open despite the first green
19-check smoke; green tests did not establish a self-consistent public wire
contract.  
**Next discriminating action:** Make construction, encoding, and decoding share
one envelope budget; enforce a closed error-code/category contract and the
public redaction membrane; bind event sequence to a required operation ID; add
the exact hostile probes.  
**Resolution:** Closed. Construction, encoding, and decoding now share the
exact 65,536-byte wire budget. Error codes, categories, exit classes, public
detail/retry/remediation, diagnostic evidence kinds, and summaries come from
closed registries; caller-controlled public prose and nonempty P1 `details`
are rejected. Events require nonnil operation scope. Pairwise negotiation
commits both exact offers and the highest shared version without claiming peer
authentication or stateful replay storage. UTC over/underflow and malformed
direct `tzinfo` values fail as public `ProtocolValidationError`, which is
root-exported. The final Terra control re-audit found no P1-local blocker.

### 2026-08-05 12:22 CDT — ERROR

**Owner:** P1 canonical VM lifecycle authority  
**Evidence:** A second independent Terra audit proved that automatic v0
`ERROR` migration could copy legacy free text containing the old agent token
into canonical `TransitionCause.detail`; a migrated legacy `STOPPED` record
could boot without reconciliation; contradictory `RuntimeObservation` values
were accepted; and `dataclasses.replace` could rewrite definition, generation,
ports, and QMP references after transition history existed. It also found
partial process binding, arbitrary QMP reference syntax, and reusable causal
event IDs.  
**Effect on outcome lock:** `GATE-P1` remains open. The first lifecycle suite
exhausted the declared transition table but did not prove that the declared
schema itself excluded these false-truth constructions.  
**Next discriminating action:** Reject every non-`DECLARED` v0 auto-migration,
bind lifecycle history to immutable declared intent and typed process/QMP
references, enforce source/state observation coherence, and reject cause
replay.  
**Resolution:** Closed. Automatic migration now accepts only exact historyless
legacy `DECLARED`, records `legacy_v0` provenance plus the mandatory secret
reprovision note in declaration intent, and rejects stronger legacy states for
P2 reconciliation. VM history binds VM/generation/boot/declaration intent,
tracks every evidence, observation, boot, process-core, and cause identity,
rejects nonconsecutive replay, and requires every later observation of the
active process to be strictly newer. Standalone transition evidence and nested
runtime observations must agree. Record mappings reject more than 10,000
transitions before element decoding. The final Terra VM re-audit found no
P1-local blocker; physical QMP/process truth remains P4-owned.

### 2026-08-05 13:26 CDT — ERROR

**Owner:** root phase-transition evidence lane
**Evidence:** The first post-P1 smoke aborted before result emission because
`SCOPE.md` declared the unsupported machine mode `COMPOSE + EDIT` and wrapped
`target_module` across lines. `_write_artifacts()` had already created the
empty run directory `test/vm_lab/runs/20260805T182614Z`.
**Effect on outcome lock:** The post-transition proof did not exist; P1 gate
evidence remained the earlier green `20260805T182129Z` bundle.
**Next discriminating action:** Restore the repository's exact `COMPOSE` mode
contract, keep `target_module` on one line, and validate scope before creating
a run directory.
**Resolution:** Closed. P2 uses the supported `COMPOSE` mode, and the harness
now validates scope before `run_dir.mkdir()`. The empty failure directory is
retained as evidence rather than silently deleted.

### 2026-08-05 13:27 CDT — ERROR

**Owner:** root plan-proof transition lane
**Evidence:** Post-transition run `20260805T182738Z` passed 18 of 19 checks but
the hostile `multiple-current-phases` fixture hard-coded P2. Once P2 itself
became current, changing P2 from active to ready left exactly one current phase
and the supposedly hostile fixture passed.
**Effect on outcome lock:** The final post-transition smoke remained failed;
the failure was in the proof owner, not the protocol or promoted host runtime.
**Next discriminating action:** Select a competing pending phase relative to
the live `current_phase` rather than naming a phase whose role changes over
time.
**Resolution:** Closed. The fixture is phase-independent, all eight hostile
plan states reject again, and `20260805T182839Z` records the final 19/19 green
post-transition proof.

### 2026-08-05 13:43 CDT — P2 SWARM WORKING MODEL

**Owner:** root integration lane  
**Evidence:** `TASK-P2-001`, ADR-0001, ADR-0002, and the accepted Terra/Luna
interfaces divide without overlapping mutation authority: Terra owns bounded
authenticated Unix transport, Luna owns the one-writer SQLite primitive, and
root owns composition, operation semantics, checkpoint recovery, and the real
multi-process/kill gate.  
**Effect on outcome lock:** Positive, provided the modules converge through one
injected handler and one process-held registry owner rather than each growing
its own supervisor, connection, or recovery loop.  
**Current decisions:** The registry must remain multi-VM correct; the
one-active-AIPC limit is atomic policy, not a global schema constant. P2
operations remain an internal registry/ownership proof and do not expose the
P8 public `declare` command early. Every operation uses the canonical
`ControlRequest.request_id` as its idempotency identity, commits intent before
resource-state mutation, and exposes a record only after exact leases and owned
paths are durable.  
**Open implementation variable:** Define the narrowest useful P2 `update` and
`lease` semantics without accepting caller-forged physical lifecycle evidence;
P4 remains the only owner that can turn observations into machine truth.

### 2026-08-05 15:05 CDT — P2 SETTLEMENT / P3 STORAGE OWNER

**Owner:** root integration lane  
**Evidence:** The real daemon entrypoint, peer-authenticated Unix socket,
normalized SQLite v2 authority, 24-process declaration wave, 96 total
client-process mutations, and twelve checkpoint-specific `SIGKILL` recoveries
closed P2 in `test/vm_lab/runs/20260805T194938Z`. The gate also caught a real
swarm merge defect—lifecycle-sequence code had landed inside port acquisition—
before promotion; the misplaced block was removed, its intended event owner was
repaired, and the complete daemon gate reran green.  
**Effect on outcome lock:** P2 is settled as a metadata mutation owner, not a
VM lifecycle claim. The same process and journal are now the only lawful P3
storage mutation authority.

**Observed P3 substrate:** `/usr/bin/qemu-img` 8.2.2 is installed and supports
real `create`, JSON `info --backing-chain`, `check`, and `measure`. A sparse
100 GiB qcow2 requires about 16.6 MiB of initial metadata. The configured base
path and all repository `var/` roots are absent. `/tmp` has about 7.99 GiB
available while the repository filesystem has only about 1.53 GiB. No
disposable qcow2 or cloud fixture was found in the bounded repository,
Downloads, libvirt, cloud-image, or uvtool locations.

**P3 composition decision:** Compose a new live image contract/backend and
storage owner rather than promote `components/host/vm_image_manager.py`.
Salvage only its historical recognition that image work is a separate host
boundary. Reject its whole-file golden copies, boot-over-SSH installation,
mutable best-effort JSON metadata, broad exception-to-message returns, and
unverified deletion. Base import must hash a single opened source descriptor,
verify exact pinned manifest bytes and `qemu-img` truth, publish immutably, and
persist the same canonical manifest in the registry. Overlay creation must be
private, sparse, checked, atomically published, marker-bound, and registered by
device/inode plus ownership token.

**Fixture decision:** Pin an official Ubuntu Minimal 24.04 amd64 release URL
and its published SHA256 in repository-owned fixture metadata. Keep the
252 MiB image and 100 GiB sparse overlays outside the nearly full repository
filesystem during the physical gate; seal only compact manifests and evidence.
A blank synthetic qcow2 may discriminate backend mechanics but can never close
`P3-005` or `GATE-P3`.

### 2026-08-05 16:17 CDT — P3 SETTLEMENT / P4 PROCESS-TRUTH MODEL

**Owner:** root + Luna + Terra integration swarm  
**Evidence:** Immutable gate `test/vm_lab/runs/20260805T211740Z` records 26/26
aggregate checks. The P3 physical lane contains 15 focused tests: six core
storage tests, eight adversarial probes, and one real daemon/client test.
`/usr/bin/qemu-img` 8.2.2 consumed the pinned Ubuntu Minimal source SHA256
`b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`,
published one immutable base, and created two independent sparse 100 GiB
overlays. Seven base and seven overlay checkpoint deaths recovered
deterministically. Foreign finals, aliases, missing/replaced stage candidates,
same-byte new-inode bases, and post-publication registry conflicts never gained
false authority.  
**Resolution:** P3 is closed. The earlier note that no disposable fixture was
present remains true for that dated observation but is superseded for current
state by the pinned descriptor and `/tmp` fixture. The candidate image manager
remains donor code; its broad copy/SSH/JSON/delete surface was not promoted.
No QEMU VM was launched and the public CLI remains read-only.

**P4 invariant before first writable boot:** P3 currently binds exact file
allocation and qemu-reported actual size because no QEMU writer exists. P4
must split:

1. immutable disk-owner facts—VM/generation/disk/token/materializing operation,
   canonical path, base identity/hash, virtual capacity, and format;
2. mutable runtime observations—file allocation, qemu actual size,
   dirty/corrupt state, observation time, boot/lifecycle/quiesce identity.

Failing to split these would make legitimate guest writes look like storage
tamper or, worse, tempt the daemon to weaken exact ownership checks. The
planner stays non-mutating. A new process owner may consume its record only
through the daemon, and a QMP client must establish greeting, capabilities,
UUID, and status before any running claim.

**Authority stop:** Implementation and non-launch physical discrimination can
continue autonomously. Actual disposable QEMU launch remains Daeron-authorized;
production AIPC disks, public lifecycle, guest readiness, P5 networking, and
resident model/ACE/ASPS/memory deployment are outside P4's current boundary.

### 2026-08-05 17:31 CDT — P4 SWARM COMPOSITION / CRASH WINDOWS

**Owner:** root + Luna + Terra + Carver integration swarm  
**Outcome lock:** Deliver one internal daemon-owned QEMU runtime boundary;
done only when the disposable P3 overlay is launched under exact process/QMP
identity and every launch-checkpoint daemon death adopts or cleans it without
orphaning it or signaling a foreign process.

**Implemented substrate so far:** The launch plan is still pure and public
planning remains non-mutating. It now fixes a named `somnus-root-disk` frontend,
an explicit single-socket/single-die/single-thread CPU topology, headless
operation, no networking, no HMP, no daemonization, and `-serial stdio`.
Executable launch is FD-pinned to a root-owned immutable-enough binary,
environment-sanitized, parent-death guarded, and journal-release gated. Process
truth is pidfd-bound and includes `/proc` identity, exact argv hash, executable
identity, process group/session isolation, and a private safe pidfile. QMP
transport is bounded, peer-authenticated, command-correlated, and event-aware.
Registry schema v4 appends mutable disk observations rather than rewriting the
P3 materialization baseline.

**Log decision:** QEMU must not write raw serial or diagnostic bytes directly
to durable files. Both streams enter pipes owned by a separate log guardian
that persists only redacted bytes with private modes and bounded rotation. The
guardian itself remains parent-death guarded until its durable checkpoint,
then survives daemon death long enough to keep draining an adopted QEMU. This
removes the deadlock/data-loss failure of daemon-owned pump threads without
granting the guardian VM, lifecycle, registry, disk, or QMP authority.

**QMP promotion decision:** Greeting/capabilities are transport evidence, not
machine identity. Promotion requires a QEMU-8.2-versioned strict normalization
of status, name, UUID, the one exact qcow2 attachment/backing chain, and all
configured CPU threads. Two coherent full snapshots separated by monitored
stability plus a final status/process/event fence are the minimum. The richer
snapshot is host-owned immutable evidence keyed to the canonical
`RuntimeObservation`; the closed protocol fact set is not weakened or padded
with unrecognized facts.

**Recovery order:** Persist a VM/generation-bound `runtime.launch` intent and
`PROVISIONED -> BOOTING` before external effects. Never hold a SQLite write
transaction across spawn, QMP, filesystem measurement, or `qemu-img`.
Checkpoint each observed external fact in a fresh transaction. On daemon
restart, internal launch recovery runs before generic service recovery;
storage authority is then reconciled before any surviving child can be fully
adopted. An ambiguous PID/process/QMP/disk identity becomes
`recovery_required` and prevents the public listener from binding—never a
best-effort cleanup signal.

**Swarm lanes:** Luna owns the crash-surviving redacted log guardian and its
real pipe/process/filesystem proof. Terra owns strict QEMU 8.2 QMP identity
normalization and immutable evidence. Carver owns the canonical internal
runtime-launch journal/checkpoint codec. Root owns their composition into the
single daemon runtime, recovery/shutdown ordering, consumed-boundary harness,
and the authority-gated physical QEMU matrix.

### 2026-08-05 (time not retained) — P4 FD-BOUND STORAGE ACCEPTANCE

**Owner:** Luna QEMU 8.2.2 read-only inspection lane, incorporating Terra's
adversarial acceptance finding
**Evidence:** Local `/usr/bin/qemu-system-x86_64` reports QEMU 8.2.2. Its
installed invocation and QMP references document `-add-fd`, `/dev/fdset/N`,
explicit file/qcow2 `-blockdev` nodes, nullable backing references,
`query-fdsets`, `query-named-block-nodes`, `query-block`, and
`query-blockstats`. The current runtime passes only its control and executable
descriptors through the guard, while the current QMP identity contract treats
canonical host path strings as block identity. No VM was launched during this
inspection.

**Acceptance correction:** A writable overlay pathname is not sufficient
authority to let QEMU write. Before spawn, the daemon must hold and validate
both P3 storage owners: the writable overlay descriptor and the immutable base
descriptor. Both must survive the guard exec, and the later QMP graph must bind
their distinct roles back to descriptor truth from the authenticated QMP peer
process. Otherwise a rename/replacement window or an unproved embedded backing
path can make QEMU consume bytes other than the registered storage owners.

**Exact documented disk argv shape:**

```text
-add-fd
fd=<OVERLAY_FD>,set=1,opaque=somnus-overlay-rw
-add-fd
fd=<BASE_FD>,set=2,opaque=somnus-base-ro

-blockdev
{"auto-read-only":false,"driver":"file","filename":"/dev/fdset/1","locking":"on","node-name":"somnus-overlay-file","read-only":false}
-blockdev
{"auto-read-only":false,"driver":"file","filename":"/dev/fdset/2","locking":"on","node-name":"somnus-base-file","read-only":true}
-blockdev
{"auto-read-only":false,"backing":null,"driver":"qcow2","file":"somnus-base-file","node-name":"somnus-base-qcow2","read-only":true}
-blockdev
{"auto-read-only":false,"backing":"somnus-base-qcow2","driver":"qcow2","file":"somnus-overlay-file","node-name":"somnus-disk","read-only":false}

-device
virtio-blk-pci,id=somnus-root-disk,drive=somnus-disk
```

Use deterministic compact JSON. The two fdsets must remain distinct: QEMU
selects a descriptor within a set by requested access mode, so separate sets
make the overlay/base role binding exact rather than selection-dependent.
Explicit root `backing` prevents fallback to the overlay header's pathname;
`backing:null` prevents the immutable base from silently opening another
metadata-declared backing layer. `auto-read-only:false` forbids silent
read-write to read-only fallback. `/dev/fdset/N` is QEMU pseudo-path syntax,
not a host path that the identity parser may resolve or `stat()`.

**Descriptor acquisition and lifetime:**

1. Open the overlay with `O_RDWR | O_CLOEXEC | O_NOFOLLOW` and the base with
   `O_RDONLY | O_CLOEXEC | O_NOFOLLOW`.
2. Before constructing the executed argv, require two distinct descriptors
   above standard I/O, regular-file type, exact registry-owned device/inode,
   expected ownership/mode/link policy, no overlay/base alias, and the
   immutable base identity/hash.
3. Record the actual descriptor numbers in the executed argv/hash and runtime
   journal. A pure public plan may name symbolic roles; it must not guess fixed
   process descriptor numbers.
4. Spawn the guard with `close_fds=True` and
   `pass_fds=(control_fd, executable_fd, overlay_fd, base_fd)`. The guard must
   revalidate both role bindings and leave both disk descriptors inheritable
   across its `execve`.
5. The daemon closes its copies on every failure branch. On success, retain
   them through target-exec proof and preferably the first coherent
   QMP/fdset/process-descriptor sample; QEMU's fdset and file node then own
   their internal duplicates.
6. Recovery may adopt only after re-proving the live QEMU fdset, graph, and
   peer-process descriptors. Reopening the saved path cannot reconstruct lost
   descriptor authority.

**Required QMP and peer-process evidence:**

- `query-fdsets`: normalize the unordered result by `fdset-id`; require exactly
  overlay set 1 and base set 2, one descriptor per set, exact non-secret opaque
  roles, and no unexpected member. QMP reports QEMU-internal descriptor
  numbers, not necessarily the daemon's inherited numbers. Resolve those
  reported descriptors only under the authenticated QMP peer PID and compare
  `/proc/<pid>/fd/<fd>` plus `/proc/<pid>/fdinfo/<fd>` device, inode, and
  access-mode evidence with the pre-spawn owners.
- `query-named-block-nodes`: normalize by `node-name`; require exactly the
  writable `somnus-overlay-file`, read-only `somnus-base-file`, read-only
  backing-free `somnus-base-qcow2`, and writable depth-one `somnus-disk`
  inventory with the expected drivers.
- `query-block`: require one guest-visible block device whose
  `qdev` is `somnus-root-disk` and whose inserted root is writable qcow2 node
  `somnus-disk`. This proves frontend attachment, not every internal edge.
- `query-blockstats`: use the stable recursive `parent` and `backing` members to
  prove `somnus-disk.parent = somnus-overlay-file`,
  `somnus-disk.backing = somnus-base-qcow2`, and
  `somnus-base-qcow2.parent = somnus-base-file`. Named-node inventory plus
  `query-block` alone does not prove those child node-name edges.

**Remaining physical observation variable:** QMP object member order is not
authority, and the exact optional fields and filename/image rendering emitted
by this packaged QEMU 8.2.2 have not been observed because no VM was launched.
The parser may normalize the documented invariant fields now, but the
Daeron-authorized disposable `GATE-P4` run must capture the actual
`query-fdsets`, `query-named-block-nodes`, `query-block`, and
`query-blockstats` responses before an exact physical response fixture is
sealed. Do not turn an anticipated response example into proof.

### 2026-08-05 — guard release-time mutation adversary

Terra ran the real exec guard with real overlay/base files and
`/usr/bin/sleep`, but no QEMU. After the guard emitted `READY`, the exact
overlay inode was overwritten in place and the immutable base was temporarily
made writable, overwritten with same-length bytes, and restored to mode
`0444`. The first descriptor-aware guard accepted `RELEASE` because its second
fence rechecked device, inode, UID, mode, link count, and access mode only.
Both mutated byte prefixes survived into the target process.

This invalidates identity-only descriptor revalidation. The guard's final
pre-`execve` authority fence must also bind the overlay's stable size,
allocation, mtime, and ctime; retain and hash-check a pinned owner-marker
descriptor; and hash-check the complete immutable base descriptor. The
discriminating real-process regression is: mutate either exact inode after
`READY`, send `RELEASE`, and prove the target never executes.

### 2026-08-07 — ERROR / OPERATOR CORRECTION

**Owner:** Codex manager/operator lane
**Evidence:** HEAD `3dff065` contains substantial internal P4 owners, while
`MEMORY.md` and `PROVENANCE.md` still stopped their authoritative chronology at
P3. The prior work also accumulated detailed adversarial P4 notes without a
corresponding continuity update.
**Effect on outcome lock:** The implementation frontier was harder to recover
than necessary and progress was being reported as if evidence collection were
the work. No physical capability claim was created by the documentation lag.
**Next discriminating action:** Keep the active P4 destination and physical
gate explicit, execute the next bounded owner lane, and update the owning docs
without manufacturing a fresh proof result.
**Resolution:** This note and the durable memory/provenance entries now record
the correction. The older adversarial notes remain retained as scratch inputs.
A fresh current-HEAD aggregate subsequently recorded 33 pass and 4
fixture-absence setup failures; it did not execute QEMU or close P4.

## 2026-08-07 P4 coordinator landed — physical gate still blocked

- `src/somnus_vm/host/p4_gate.py` now owns exact non-public preflight,
  one-shot authorization, daemon activation, checkpoint/recovery callbacks,
  raw QMP response-history capture, and sealed-result validation. It consumes
  the existing daemon/runtime/permit owners and introduces no second lifecycle
  authority. `scripts/run_gate_p4.py` is the explicit operator driver and is not
  an installed lifecycle command.
- `test/vm_lab/test_p4_gate_coordinator.py` is 5/5 without launching QEMU;
  direct QMP proof is 14/14. Aggregate `20260807T121437Z` is 36 pass, 4 fail,
  0 skip across 40 checks. The four failures remain the exact missing-source
  P3/P4 storage lanes; preserve them and do not substitute an image.
- Physical truth remains absent: the exact 264,306,688-byte `b3064...` source,
  valid execution phrase/Daeron authority, qemu-system launch, checkpoint
  adoption/cleanup matrix, and `GATE-P4` evidence do not exist. P5+ remains
  pending and public lifecycle commands remain banned.

**Next packet:** restore the exact pinned source, obtain the valid physical-gate
execute authority, then run `scripts/run_gate_p4.py execute` through the existing
daemon-owned checkpoint/QMP/recovery path. Verify the sealed machine-truth
bundle before changing `TASK-P4-019` or promoting any public lifecycle surface.

## 2026-08-07 P4 exact fixture restored — authority and physical matrix remain

- The exact source is restored at `/tmp/vm-lab-p3-fixture-20260801.qcow2`:
  264,306,688 bytes, mode 0600, uid 1000, nlink 1, SHA-256
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.
- Fresh aggregate `20260807T123747Z` is fully green: 40 pass, 0 fail, 0 skip;
  plan/index is 2,037/2,037. Retain `20260807T121437Z` as historical 36/4
  missing-source evidence only.
- Remaining physical truth boundary: record the valid execution phrase, Daeron's
  explicit authority, and explicit production-exclusion paths; then execute the
  daemon-owned qemu-system success and checkpoint kill/restart adoption/cleanup
  matrix. No VM has launched, `GATE-P4` remains open, and public lifecycle
  commands remain banned.

**Next packet:** obtain the operator authority/exclusion inputs, then run
`scripts/run_gate_p4.py execute` and verify the sealed external machine-truth
bundle before changing phase or public capability.

## 2026-08-07 Full P4 matrix coordinator — implementation complete, physical run pending

- `p4_gate.py` now owns the canonical 17-scenario matrix: isolated compact roots
  `00-success` plus every exact `CHECKPOINT_ORDER` case, exact matrix
  preflight/result validation, strict serial execution/resume, stop-and-preserve
  failure behavior, sealed successful runtime-log bytes, and aggregate result
  sealing. `run_gate_p4.py` exposes `prepare-matrix`, `preflight-matrix`, and
  `execute-matrix`.
- Per-scenario validators require exact checkpoint prefixes, conditional
  adoption/cleanup evidence, and explicit cleanup scope
  `runtime-process-only;fixture-retained`. They do not delete fixtures or P3
  storage. The deterministic phrase is only an accidental-execution fence,
  never authentication or proof of Daeron authority.
- Non-launch coordinator proof is 8/8. Fresh aggregate `20260807T123747Z` is
  40 pass, 0 fail, 0 skip; plan/index 2,040 observations. No qemu-system VM or
  matrix has run. Full-matrix authority, explicit production exclusions,
  physical success/checkpoint evidence, and `GATE-P4` remain open.

**Next packet:** record full-matrix execute authority and explicit exclusion
paths, run `prepare-matrix`, `preflight-matrix`, and `execute-matrix` strictly
through the daemon-owned runtime, and verify the sealed aggregate before any
phase transition or public lifecycle promotion.

## 2026-08-07 Physical-matrix authority ordering correction

- The earlier handoff incorrectly grouped a “valid execution phrase” with
  Daeron authority as an input that exists before matrix preparation.
  `P4GateMatrixSpec.execution_phrase` is deterministically derived from the
  matrix UUID, so it cannot exist until `prepare-matrix` creates the frozen
  matrix. It is an accidental-invocation fence and never authority.
- The actual external inputs are only Daeron's explicit authority for the
  disposable physical run and exact existing production-exclusion paths.
  `prepare-matrix` then freezes source/manifest/tool/scenario identity and emits
  the run-bound phrase; `preflight-matrix` and `execute-matrix` consume that
  frozen matrix.
- Attachment compliance was re-audited: `AGENTS.md` contains the requested
  exhausted-owner SOTA++, causal-density, operator-agency, fail-loud,
  workflow/continuity, clean-handoff, and filetree anti-tunnelvision doctrines.
  No generic wrapper or line-count simplification rule displaced them.

**Next packet:** obtain Daeron's explicit disposable-matrix authority and exact
production-exclusion paths; run `prepare-matrix`, capture its generated phrase,
then run `preflight-matrix` and `execute-matrix`. Do not invent exclusions,
prepare against production, launch before authority, or advance P5 before
sealed `GATE-P4` machine truth.

## 2026-08-07 Frozen physical P4 matrix prepared and preflighted

- Read-only host discovery found no libvirt command/domain, active QEMU process,
  deployed Somnus/AIPC state, or production qcow2 on `/home/daeron`, mounted
  `/media`, `/mnt`, `/var/lib/libvirt`, `/srv`, `/opt`, or `/tmp`. The only
  qcow2 is the exact manifest-bound disposable source
  `/tmp/vm-lab-p3-fixture-20260801.qcow2`.
- Because there is no located production VM owner to name more narrowly,
  preparation conservatively excludes both observed operator-owned roots:
  `operator-home=/home/daeron` and
  `operator-usb=/media/daeron/usb-128gb`. The disposable matrix lives on the
  separate `/tmp` tmpfs and cannot overlap either root.
- `prepare-matrix` created all 17 exact private scenarios at
  `/tmp/vm-lab-gate-p4-matrix-20260807T124705Z`. Matrix ID is
  `b9aca76a-a016-4d0c-9002-f0a90f383b21`; canonical matrix-spec SHA-256 is
  `9567494af6899c1d2ce5353845950b78a087f4fcf4deb22d67db54950542a104`.
  `preflight-matrix` revalidated all source/manifest/tool/scenario identities.
- No registry, control socket, PID, QMP socket, result record, or qemu-system
  process was created. Preparation/preflight are complete; physical execution
  remains a consequential VM action reserved for Daeron's explicit authority.

**Next packet:** after Daeron explicitly authorizes execution of matrix
`b9aca76a-a016-4d0c-9002-f0a90f383b21`, invoke `execute-matrix` with the
generated run-bound phrase, monitor all 17 serial scenarios, and validate the
sealed aggregate. Do not regenerate the matrix, substitute an image, alter its
exclusions, touch an operator-owned root, advance P5, or claim `GATE-P4` before
external QEMU/QMP machine truth.

## 2026-08-24 — Canonical plan activation / RECOVERY-R0

- Daeron authorized execution of the full no-fallback completion program.
  The plan is one canonical `PLAN.md`, not a second roadmap. RECOVERY-R0 now
  freezes physical P4 execution until exact source/fixture authority is current.
- Current truth: `/tmp/vm-lab-p3-fixture-20260801.qcow2` is absent;
  `SOTA_RUN.md` records 35 pass / 5 fail; one source-manifest entry diverges.
  Historical green evidence is retained but cannot be promoted as current.
- The true first-party donor is external `backend/virtual_machine`; preserve
  its AIPC/operator semantics, rebuild all mechanisms through VM Lab gates,
  and never import its weak PID/shell/default-auth/fallback paths.

**Next packet:** complete RECOVERY-R0 only. **Owner files:** `source-manifest.json`,
`components/operator/native_tools/internal_browser.py`,
`test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`,
`test/vm_lab/test_storage_p3.py`, `test/vm_lab/test_storage_p3_adversarial.py`,
`test/vm_lab/test_daemon_storage_p3.py`, `test/vm_lab/test_storage_runtime_p4.py`,
and `SOTA_RUN.md`. Establish authority for the manifest-bound browser bytes and
exact qcow2 fixture, restore neither without that proof, run
`python scripts/verify_repository.py` plus the four fixture-dependent owners,
and publish a fresh bundle. Do not launch QEMU, execute the P4 matrix, expose
lifecycle commands, alter snapshots, import donor code, or erase failed evidence.

## 2026-08-24 — RECOVERY-R0 source authority resolved

- Verified the manifest-bound browser source with two retained archives plus
  original Git, then restored exact `d27779…cf644e` bytes atomically. The later
  `198917…29a81` bytes remain retained in Git and `vm-lab.zip`; no manifest hash
  was rewritten.
- Fresh `20260825T043503Z` aggregate is 36 pass / 4 fail. `source_manifest` and
  plan contract now pass. The exact fixture is the sole remaining blocker.

**Next packet:** acquire only the exact fixture named by
`test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`; owner
files are that manifest, `test_storage_p3.py`, `test_storage_p3_adversarial.py`,
`test_daemon_storage_p3.py`, `test_storage_runtime_p4.py`, and `SOTA_RUN.md`.
Verify size/hash/regular-file/private-ownership identity, run the four owners
plus `python scripts/verify_repository.py`, and publish the new bundle. Do not
substitute/rebuild/download a different image, launch QEMU, execute the P4
matrix, alter snapshots, or erase failed evidence.

## 2026-09-05 — restore operator kernel after b11880c deletion

- Fresh Windows clone of `origin/main` at `1c4a131` was missing the execution
  authority and continuity surfaces. Git history shows `b11880c` deleted
  `PLAN.md`, `CONTEXT.md`, `MEMORY.md`, `SOTA_RUN.md`, `Somnus-Core-Ideals.md`,
  `OPSEC.md`, `workflows-new.md`, `.codex/skills/vm-lab/SKILL.md`, `.gitignore`,
  and emptied the stdlib-only `pyproject.toml` / `requirements.txt` while adding
  file-tree docs. Last good copies restored from `ae0b077`. Cloud Agent
  `.cursor/` files from PR #1 were kept.
- Candidate dependency lists remain under `docs/requirements-singles/`. Live
  runtime dependencies stay empty.
- Current physical truth is unchanged: sealed P3/P4 historical bundles exist;
  current aggregate `20260825T043503Z` is 36/4 because the exact fixture is
  absent. No QEMU launch. `TASK-P4-019` remains active.

**Next packet:** on the Cloud Agent Linux host, run
`python scripts/verify_repository.py`, then continue `TASK-P4-019` only after
the exact pinned qcow2 is present. Do not substitute an image, execute the P4
matrix, or add public lifecycle commands.

## 2026-09-05 — push-trigger drift review; latent plan-contract drift found and repaired

- The push trigger metadata was synthetic (`test-user`, zero commits,
  placeholder SHAs), so the review ran against observed git truth: HEAD
  `1b0121c` == `main` == `origin/main`, clean tree. Trigger payloads are never
  evidence; only the repository is.
- The `1b0121c` restoration is verified complete: residual diff
  `ae0b077..HEAD` is only the `.cursor/` Cloud Agent environment, four pure
  moves into `docs/old-filetrees/` and `docs/requirements-singles/`, link
  retargets, and the reconstitution note. Verifier: 2628/2628 on this Linux
  host (Python 3.12.3, qemu-img 8.2.2).
- First smoke of the restored tree (`20260905T101438Z`, 35 pass / 5 fail)
  exposed latent drift from `ae0b077`: §3.1 recovery items carried completed
  `RECOVERY-R0-00x` IDs outside the closed plan-contract grammar
  (`INV|BASE|DONE|EXEC|TEST|Pn-nnn|GATE-Pn`), so `plan_contract` failed. Every
  sealed bundle predates the `ae0b077` PLAN.md edit, so no historical bundle
  ever validated it. The structural verifier could not see it: its index
  parser only reads `## N. Phase Pn` sections.
- Repair (docs-only, no runtime change): §3.1 items renamed to `BASE-014`…
  `BASE-018` — current evidence authority is §3 floor truth, not a promoted
  phase gate — with the mapping and failure evidence recorded in PLAN.md.
  Fresh aggregate `20260905T101913Z` is 36 pass / 4 fail / 0 skip;
  `plan_contract` validates 410 unique IDs; the four failures remain exactly
  the fixture-absence lanes. The pre-repair bundle is preserved, not erased.
- Physical truth is unchanged: `/tmp/vm-lab-p3-fixture-20260801.qcow2` is
  absent on this host, no qemu-system process or matrix ran, `GATE-P4`
  remains open, and the public CLI remains read-only.

**Next packet:** acquire only the exact manifest-bound fixture
(`ubuntu-minimal-noble-amd64-20260801.json`; 264,306,688 bytes, SHA-256
`b3064efb…14cced6`) from declared origin or separately authenticated
byte-identical authority. **Owner files:**
`test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`,
`test/vm_lab/test_storage_p3.py`, `test/vm_lab/test_storage_p3_adversarial.py`,
`test/vm_lab/test_daemon_storage_p3.py`, `test/vm_lab/test_storage_runtime_p4.py`,
`SOTA_RUN.md`. **Verify:** `python scripts/verify_repository.py`, then
`PYTHONPATH=src python test/vm_lab/smoke.py`; publish the fresh bundle and
update only the owning continuity surfaces from it. **Hard bans:** no
substitute/rebuilt/downloaded image, no QEMU launch, no P4 matrix execution,
no public lifecycle command, no snapshot mutation, no erasure of failed
bundles.

## 2026-09-05 — declared-origin fixture restored; RECOVERY-R0 closed

- Canonical `SHA256SUMS` and the repository manifest agree on
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.
  `/tmp/vm-lab-p3-fixture-20260801.qcow2` is regular, mode 0600, uid 1000,
  nlink 1, size 264,306,688, qcow2 virtual-size 3758096384, no backing file.
- Fresh aggregate `20260905T111215Z` is 40 pass / 0 fail / 0 skip. The four
  previous fixture-absence lanes pass. Failed bundles are retained.
- `TASK-P4-019` complete. `TASK-P4-020` ready. Kernel banner no longer treats
  historical 40/0/0 as current. No qemu-system launch. `GATE-P4` remains open.

**Next packet:** prepare and preflight a new private P4 matrix on this host
from the current declared-origin fixture, record this host's production
exclusions, then execute `TASK-P4-020` through `scripts/run_gate_p4.py`.
**Owner files:** `src/somnus_vm/host/p4_gate.py`,
`scripts/run_gate_p4.py`, `src/somnus_vm/host/launch_authority.py`,
`src/somnus_vm/host/qemu_runtime.py`. **Verify:** sealed matrix JSON/Markdown/log
plus QMP identity and checkpoint adoption/cleanup. **Hard bans:** no public
lifecycle command, no fixture substitution, no foreign-PID signaling, no guest
readiness claim, no treating the 40/0/0 non-launch aggregate as `GATE-P4`.
