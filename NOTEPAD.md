# Codex Operator Notepad

**Purpose:** active external reasoning surface for VM Lab work  
**Authority:** scratch only; current code, `AGENTS.md`, `TASK.md`, `PLAN.md`,
`STATE.md`, and explicit operator corrections outrank this file  
**Update rule:** record questions, errors, hypotheses, code observations, and
integration ideas here instead of carrying them implicitly. Move settled
decisions into the owning canonical document and mark the note resolved.

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

- Phase: `P1`
- Unit: `TASK-P1-001`
- State: active
- P1 COMPOSE scope declared on 2026-08-05.
- P1 must define protocols capable of naming the persistent computer, the
  resident deployment, the operational agent, and the human/model harness
  boundary without implementing all those later phases inside P1.

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
