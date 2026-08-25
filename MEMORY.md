# VM Lab Durable Memory

This file preserves repository decisions, corrections, rejected paths, and
reusable failure lessons that should change future execution. Current state is
indexed in [`CONTEXT.md`](CONTEXT.md); chronological work belongs in
[`PROVENANCE.md`](PROVENANCE.md).

---

## Durable decisions

### 2026-08-04 — Reconstitute rather than overwrite

The uploaded `vm_lab.zip` remains an independently recoverable source boundary.
The live `src/somnus_vm` package is a composed replacement boundary, while every
one of the 33 source files remains represented exactly once in
[`source-manifest.json`](source-manifest.json).

**Future consequence:** never “clean up” archive, candidate, or quarantine
surfaces merely because a live replacement exists. Preservation and promotion
are different axes.

### 2026-08-04 — Withhold lifecycle until physical truth exists

The source lineage contained daemonized PID confusion, fixed port collisions,
fabricated guest addresses, unsafe snapshot rollback, no-op scaling, fake
orchestration, and synthetic benchmark evidence.

**Future consequence:** v0.1 exposes only doctor, topology, plan, and guest
bootstrap. The internal P2/P3 daemon may mutate registry and verified storage,
but lifecycle returns phase-by-phase through physical gates, never by restoring
old commands or broadening the public CLI ahead of proof.

### 2026-08-04 — Keep four authority planes separate

Host lifecycle, guest control/cognition, Kerminal agency, and disposable
Artifact processing are separate authorities. The external
`backend/virtual_machine` donor is first-party read-only semantic lineage, not
a host-lifecycle dependency.

**Future consequence:** integration occurs through explicit behavioral
contracts. Direct cross-imports or repository consolidation are not shortcuts.

### 2026-08-04 — Planning remains non-mutating

Port allocation in v0.1 is a bind-check that releases sockets. QEMU construction
returns argv only. `ports_reserved` is false.

**Future consequence:** durable leases and launch state belong to the one
daemon transaction. Do not make CLI planner invocations compete as lifecycle
owners.

### 2026-08-05 — Canonical protocols live outside the backend package

`src/somnus_protocol` is the dependency-free authority for version, control,
event, agent, settings, and VM lifecycle schemas.
`src/somnus_vm/contracts` is compatibility identity only.

**Future consequence:** host, guest, Kerminal, and alternate backends consume
the shared protocol. They do not fork the schemas or import backend ownership
to obtain them.

### 2026-08-05 — One daemon owns all durable mutation

The current `somnus-vm-daemon` is the only process allowed to write the SQLite
registry, resource leases, operation journal, immutable base slot, or
per-generation overlay. The Unix membrane uses bounded framing and kernel
`SO_PEERCRED`; planner/CLI calls remain non-mutating.

**Future consequence:** P4 QEMU process/QMP state must extend this daemon and
journal. It may not add a second supervisor, direct CLI writer, or operator
shell ownership path.

### 2026-08-05 — Image authority is measured, physical, and inode-bound

P3 promoted clean `images.py` and `storage.py` owners instead of the candidate
image manager. An image manifest separates asserted origin/vendor metadata from
host-measured bytes, format, virtual size, backing chain, allocation, and tool
identity. Published bases and overlays bind canonical path plus device/inode;
same bytes at a new inode are not the same owned object.

**Future consequence:** foreign finals are never adopted without exact
journal/manifest/marker authority. Recovery reuses only the checkpoint-bound
candidate, and a missing or replaced candidate blocks rather than recreates
past a physical checkpoint. Arbitrary import, clone, export, detach, and
deletion remain distinct future operations.

### 2026-08-05 — Split immutable disk ownership from runtime observations

P3 can bind exact allocation and `qemu-img` actual size only because no QEMU
writer exists. P4 must preserve immutable owner facts while moving allocation,
dirty/corrupt state, observation time, boot/lifecycle identity, and quiesce
state into a mutable observed-truth surface.

**Future consequence:** do not weaken P3 inode/path/base ownership checks merely
because a running guest changes legitimate qcow2 allocation.

### 2026-08-04 — Guest bootstrap uses one-read verified bytes

Bootstrap copies and hashes each exact-sized local payload once into a private
verified location, then installs from that copy. It rejects network acquisition,
arbitrary post-install commands, and service mutation.

**Future consequence:** in-place update work must extend this trust chain rather
than bypass it for convenience.

### 2026-08-04 — Living repository surfaces are bounded, not duplicated

`AGENTS.md` is the entry kernel, `filetree.md` is the navigation/dependency map,
`ARCHITECTURE_MAP.md` is source traversal, `CONTEXT.md` is current state,
`MEMORY.md` is durable reasoning, `PROVENANCE.md` is chronology, `PLAN.md` is
the destination, and `TASK.md` is the single current unit.

**Future consequence:** update the owning surface only. Do not copy the whole
architecture into every Markdown file.

### 2026-08-07 — P4 internal frontier and manager/operator correction

At repository HEAD `3dff065dbf031605a90ed6d1f8a11fa85e46b42a` (`v3-updates`),
P0, P1, P2, and P3 remain closed at their recorded gates. The live source tree
already contains substantial internal P4 composition: QEMU command/runtime
owners, QMP framing and identity normalization, process identity and guard
paths, runtime journal/checkpoint code, redacted log-guardian surfaces, and
mutable disk-observation support. These are implementation surfaces, not a
closed physical capability. `TASK-P4-001` remains active; no disposable QEMU
launch has been authorized or performed, `GATE-P4` remains open, and no P5 or
later capability is promoted.

The operator correction is durable: Codex must manage the phase as an operator
and technical collaborator, not disappear into an isolated worker loop. A phase
should be decomposed into bounded lanes, advanced against the real destination,
and have its repository continuity surfaces updated as state changes. Existing
adversarial findings remain valuable guards, but exhaustive evidence collection
is not completion and must not displace integrated progress, phase management,
or documentation updates.

**Future consequence:** distinguish source-level/internal P4 composition from
`GATE-P4` physical proof in every state surface. Preserve useful adversarial
constraints, but stop repeating them once they no longer discriminate the next
implementation decision; update the owning task/state/provenance surfaces and
then continue the next bounded lane.

The same-day current-HEAD aggregate
`test/vm_lab/runs/20260807T091421Z/result.json` recorded 33 pass, 4 fail, and
0 skip. Every fixture-independent P4 component lane passed. All four failures
were setup failures caused by the absent pinned qcow2 source; the tracked
manifest is present. This does not reopen historical P3 closure, but current
physical reproducibility and the later P4 disposable-machine gate require the
authorized source fixture to be restored.

The phase ledger now records 15 of 18 bounded P4 implementation items complete.
`P4-001` remains open at the actual P3-descriptor-to-runtime handoff, `P4-004`
remains open at incompatible-host/doctor consumption, and `P4-017` remains open
because unknown cleanup is not the named orphan-emergency authority. `GATE-P4`
remains open. Component completion is useful project progress; phase promotion
still belongs to the physical gate.

### 2026-08-07 — P4 bounded implementation closure

The preceding 15/18 handoff is superseded. All 18 bounded P4 implementation
items are now complete in current source:

- `QemuRuntimeOwner.launch()` pins the P3 overlay read/write, immutable base
  read-only, and owner marker read-only; the exec guard receives exact
  descriptor metadata plus immutable hashes, revalidates at READY and release,
  and exposes only overlay/base to QEMU;
- the executed argv is the exact two-fdset/four-node rewrite of the
  non-mutating plan, and the durable journal records both planned and executed
  command identities plus descriptor authority;
- authenticated QMP observations normalize `query-fdsets`,
  `query-named-block-nodes`, and recursive `query-blockstats`, then bind the
  QEMU-internal fd numbers through `/proc/<peer-pid>/fd` and `fdinfo` to the
  storage-owned device/inode/access roles;
- doctor interrogates the selected QEMU's sandbox option projection without
  launching a VM;
- an unproved child persists `cleanup_state=orphaned` and
  `recovery_disposition=orphan_emergency`, including exact process identity
  across SQLite replay.

This closes `P4-001`, `P4-004`, and `P4-017` as bounded implementation items.
It does not close `GATE-P4`: the exact pinned qcow2 source is absent locally,
no disposable QEMU execution was performed, and the actual QEMU 8.2.2 response
shape and daemon-kill adoption matrix remain unobserved.

Current-HEAD aggregate `test/vm_lab/runs/20260807T114854Z/result.json` records
35 pass, 4 fail, and 0 skip. Every fixture-independent lane passed. The four
failures remain the exact P3/P4 storage lanes that fail loudly in setup because
the pinned source bytes are absent; no substitute fixture was accepted.

---

## Rejected paths

| Rejected path | Why it was rejected | Re-entry condition |
| --- | --- | --- |
| Restore old supervisor as runtime | wrong PID/network/snapshot/scaling semantics | salvage named concepts behind new P2–P4 owners and gates |
| Wrap old `vm_orchestrator.py` | mock sessions, log-only work, duplicate supervisor | no direct re-entry; compose behavior from verified owners |
| Fixed ports because one VM is normal | one-active policy is not resource-isolation correctness | durable transactional allocator |
| Fabricated `192.168.122.x` guest address | incompatible with QEMU user networking | explicit separate bridge/tap backend |
| Snapshot by qcow2 file copy | can corrupt backing chain | QMP block-graph switching and stopped rollback proof |
| No-op scaling API | returns intent as success | before/after QMP resource observation |
| Digital twin as readiness agent | missing endpoints and recursive lock behavior | small authenticated agent first; cognition optional |
| Generic post-install shell commands | destroys bootstrap policy and auditability | declarative known templates with rollback/readiness gate |
| Sleep benchmark | measures scheduler delay, not VM work | timed real QEMU/QMP/guest operation |
| Import file processors during boot | no current caller; eager dependency cost | external disposable service plus ingress contract |
| Treat architecture reports as proof | prior report validated absent files | current tree plus executable evidence only |
| Promote candidate image manager | broad copies, SSH install, mutable JSON, exception returns, unsafe delete | no direct promotion; salvage only named behavior behind clean owners |
| Adopt a byte-identical replacement image | hash equality does not preserve physical ownership | explicit future transfer operation with registry migration |

---

## Reusable failure lessons

1. A PID identifies a process only after start-time and executable correlation;
   it never proves VM identity without QMP.
2. A socket pathname proves filesystem state, not a live protocol.
3. A TCP connection proves reachability, not authenticated guest readiness.
4. A safe plan proves representable intent, not ownership or execution.
5. A successful schema round-trip proves structure, not physical semantics.
6. A passing test proves only its actual observation boundary.
7. A missing physical prerequisite should remain a visible blocked gate.
8. Candidate sophistication is a cognitive attractor; disposition outranks
   apparent completeness.
9. Snapshot metadata is immutable evidence, not a convenient state file.
10. Navigation that points to stale source is an operational failure, not a
    documentation cosmetic.
11. A foreign final without its exact manifest or owner marker is not
    recoverable state and must never be adopted.
12. A checkpoint name is not sufficient recovery authority; its exact payload,
    physical device/inode, and expected path must still agree.
13. Failure after physical publication is `recovery_required`, not an excuse
    to report ordinary terminal failure or delete the published object.
14. Parent death must physically terminate a native child; process-group intent
    alone is not containment evidence.
15. Adversarial evidence is a discriminator, not a project phase. Keep a
    finding when it changes an owner or acceptance boundary; otherwise do not
    let repeated proof work replace integrated implementation or live docs.

---

## Hardware constraints

Primary operating targets are CPU-only Linux hosts:

- Ryzen 5 2400G / 12 GB RAM / no GPU;
- Ryzen 5 5625U / 16 GB RAM / no GPU.

VM profiles, test concurrency, base-image choices, and optional cognition must
fit these machines by default. GPU/cloud paths may exist only as optional
enhancements.

---

## Open architectural variables

These remain unresolved and must not be silently collapsed:

- actual QEMU 8.2.2 fdset/named-node/blockstats rendering and the physical
  launch-checkpoint adoption matrix;
- immutable disk-owner versus mutable runtime-observation schema migration;
- per-VM secret provisioning mechanism;
- exact Kerminal and VM-Go compatibility contracts;
- guest agent transport/authentication construction;
- stopped-VM snapshot generation strategy before live snapshot work;
- Artifact ingress manifest schema;
- whether this Python backend earns reference, alternate, or parity status
  relative to VM-Go.

The phase that resolves each variable must record the decision and its proof in
[`PROVENANCE.md`](PROVENANCE.md).

### 2026-08-07 — P4 gate-readiness correction: launch authority is consumed; coordinator remains

The bounded P4 owner is now safer and more directly consumable than the earlier
implementation-closure note recorded. `launch_authority.py` defines a typed,
one-shot disposable permit minted only from a private exact fixture, the six
configured runtime/storage/security roots, the registered P3 overlay and base
identity, a canonical fixture marker, and explicit operator-supplied production
exclusions. `QemuRuntimeOwner.launch()` requires and consumes that permit before
launch mutation. Success or failure burns it. The returned canonical receipt is
embedded in the durable `runtime.launch` intent so recovery does not infer
launch authority from an ambient path, profile, environment variable, or test
location.

`daemon_runtime.run()` also accepts an in-process Python-only activation hook
after ordinary runtime recovery and before listener service. The installed
`somnus-vm-daemon` CLI and authenticated AF_UNIX operation vocabulary expose no
hook selector and no launch request. Focused non-launch proof passed: disposable
authority 5/5, runtime journal 9/9, and daemon/runtime composition 4/4.

**Correction to the earlier handoff:** all eighteen bounded P4 phase items remain
implemented, but that does not mean the physical gate is executable. The
repository still lacks the explicit coordinator that prepares the exact
fixture, mints the permit, drives `QemuRuntimeOwner.launch()` inside the daemon,
seals raw QMP/log/process evidence, kills/restarts the daemon at every journal
checkpoint, and proves exact adoption or cleanup. The exact pinned source is
also absent and Daeron has not authorized a physical run. Therefore
`GATE-P4` remains open and no physical QEMU claim exists.

### 2026-08-07 — P4 physical coordinator is implemented; machine gate remains open

`src/somnus_vm/host/p4_gate.py` now owns the explicit non-public physical-gate
coordinator over the existing daemon activation seam. `scripts/run_gate_p4.py`
is the operator driver for prepare/preflight/execute/recovery/result sealing.
The coordinator validates the exact private marked fixture, the tracked
264,306,688-byte source and executable identities, production exclusions, and
one-shot authorization; it records canonical QMP response history, process/log
observations, checkpoint/recovery outcomes, and sealed result manifests without
creating a second lifecycle owner. `test_p4_gate_coordinator.py` proves 5/5
non-launch cases, while direct QMP proof is 14/14.

This is implementation and preflight closure, not physical gate closure. The
exact source is absent, no valid execution phrase or Daeron physical launch
authorization is supplied, no qemu-system VM or checkpoint matrix has run, and
`GATE-P4` plus P5+ remain open. Current aggregate
`test/vm_lab/runs/20260807T121437Z/` is 36 pass, 4 fail, 0 skip across 40 checks;
the four failures are preserved exact fixture-absence setup failures.

### 2026-08-07 — P4 source restored; local aggregate fully green, physical gate open

The exact manifest-bound qcow2 source is restored at
`/tmp/vm-lab-p3-fixture-20260801.qcow2` and verified size 264,306,688 bytes,
mode `0600`, uid `1000`, nlink `1`, SHA-256
`b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`. Fresh
aggregate `test/vm_lab/runs/20260807T123747Z/` is 40 pass, 0 fail, 0 skip, and
plan/index validation is 2,037/2,037. The earlier 36/4 `20260807T121437Z` run
is retained as historical failure evidence only.

This does not close `GATE-P4`: no valid execution phrase or Daeron physical launch
authority is supplied, explicit production exclusions must be recorded, no
qemu-system VM has launched, and the actual success/checkpoint adoption/cleanup
matrix remains unobserved. P5+ stay pending.

### 2026-08-07 — Full P4 matrix is implemented and fail-closed; no physical run

The P4 coordinator now defines the canonical full matrix over 17 isolated
compact roots: `00-success` plus each exact `CHECKPOINT_ORDER` kill scenario.
`prepare-matrix` creates exact scenario specs; `preflight-matrix` validates
shared manifest/source/tool identity, private ownership, exclusions, and all
scenario specs without creating a process; `execute-matrix` runs scenarios
strictly serially, resumes only sealed validated scenarios, stops on failure,
preserves the failed matrix, and seals an aggregate result. Runtime-log bytes
from successful scenarios are sealed. Scenario validation requires exact
checkpoint-prefix evidence, conditional adoption/cleanup evidence, and cleanup
scope `runtime-process-only;fixture-retained`; no fixture/P3 storage deletion is
performed or claimed.

The deterministic execution phrase is only an accidental-execution fence. It is
not authentication and cannot prove Daeron's authority. The non-launch suite is
8/8, and aggregate `test/vm_lab/runs/20260807T123747Z/` is 40 pass, 0 fail,
0 skip with plan/index 2,040 observations. No qemu-system VM or matrix has run;
full-matrix authority, explicit exclusions, physical success/checkpoint truth,
and `GATE-P4` remain open.

### 2026-08-07 — Matrix phrase ordering is not an authority input

The full-matrix execution phrase is derived from the UUID of a prepared
`P4GateMatrixSpec`. It therefore does not exist before `prepare-matrix` and
must never be requested from Daeron as if it were external authority. The
external inputs are Daeron's explicit approval for one disposable physical
matrix and exact existing production-exclusion paths. Preparation freezes the
matrix and emits the accidental-invocation phrase; preflight and execution then
consume it. This ordering correction supersedes handoff prose that grouped a
“valid phrase” with authority before preparation.

### 2026-08-07 — Prepared P4 matrix identity

Read-only host discovery located no deployed production qcow2, libvirt domain,
active QEMU process, or Somnus/AIPC production state. The only qcow2 on mounted
storage is the exact disposable P3 source. The non-launch coordinator therefore
used conservative existing exclusions for all of `/home/daeron` and
`/media/daeron/usb-128gb`, then prepared and preflighted the exact 17-scenario
matrix at `/tmp/vm-lab-gate-p4-matrix-20260807T124705Z`. Its matrix ID is
`b9aca76a-a016-4d0c-9002-f0a90f383b21` and canonical spec SHA-256 is
`9567494af6899c1d2ce5353845950b78a087f4fcf4deb22d67db54950542a104`.
This is frozen non-launch state, not physical authority or gate evidence.

### 2026-08-24 — RECOVERY-R0 and true donor-boundary correction

Current disk/proof truth supersedes stale continuity prose: the exact P3 fixture
is absent; the latest aggregate is 35 pass / 5 fail; and one preserved-source
hash diverges. P3 remains a sealed historical gate, but current reproducibility
must be rebuilt through RECOVERY-R0 before physical P4 work. The relevant
first-party donor is the external `backend/virtual_machine` system, not VM-Go.
It supplies persistent-AIPC, guest, operator, reversible-evolution, cognition,
and Artifact semantics; it is read-only lineage and never a VM Lab dependency
or import source. Its PID/shell/default-auth/fallback/snapshot-copy mechanisms
are rejected by design.

### 2026-08-24 — RECOVERY-R0 source-preservation branch closed

The source-preservation branch is now verified: `internal_browser.py` was
restored to manifest hash `d27779…cf644e` only after two retained local archives
and the original Git blob agreed. The later `198917…29a81` bytes remain in Git
history and `vm-lab.zip` as divergence evidence. Fresh aggregate
`20260825T043503Z` proves 36 pass / 4 fail; every remaining failure is exact
fixture absence. This does not close RECOVERY-R0, P3 current reproducibility,
or P4; fixture authority remains the sole blocker.
