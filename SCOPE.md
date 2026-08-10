## Engagement Mode

- mode: COMPOSE
- target_module: `src/somnus_vm/host/qemu.py`,
  `src/somnus_vm/host/launch_authority.py`,
  `src/somnus_vm/host/qemu_runtime.py`,
  `src/somnus_vm/host/qemu_exec_guard.py`,
  `src/somnus_vm/host/qemu_runtime_journal.py`,
  `src/somnus_vm/host/qmp.py`, `src/somnus_vm/host/qmp_identity.py`,
  `src/somnus_vm/doctor.py`, and their focused P4 integration tests
- target_module_provenance: `PLAN.md` Phase P4, `TASK-P4-019`, the closed
  P1 canonical lifecycle contracts, P2 single-owner journal, P3 physical image
  authority, and the existing non-mutating QEMU plan builder
- justification: all eighteen bounded P4 implementation items now compose one
  daemon-owned descriptor/QMP/process/recovery path; the remaining unit is the
  authority-gated disposable machine proof, not another component scaffold
- author: daeron
- collaborator: Codex
- date: 2026-08-07

## Compose Scope

The completed bounded implementation includes:

- opening the registered P3 overlay read/write and immutable base read-only as
  two validated descriptors before spawn;
- binding the executed argv through the existing two-fdset/four-node
  `-blockdev` plan rather than reopening storage paths;
- preserving and revalidating those descriptors through the target exec guard;
- consuming the existing QMP, process, log, journal, and recovery owners as one
  daemon launch path;
- focused integration checks for descriptor inheritance, exact QMP
  UUID/status/root-disk truth, and exact-child adoption or cleanup;
- a bounded selected-QEMU sandbox option probe through public doctor;
- durable orphan-emergency recovery when exact child exit cannot be proved.
- one-shot disposable-fixture authority consumed by the actual runtime launch
  and frozen into the durable launch journal;
- a Python-only daemon activation seam unavailable to the public CLI and
  authenticated AF_UNIX request vocabulary.

The non-public physical-gate coordinator is now implemented in
`src/somnus_vm/host/p4_gate.py`, with `scripts/run_gate_p4.py` as the explicit
operator driver. It prepares the canonical 17-scenario matrix (`00-success`
plus every `CHECKPOINT_ORDER` case), exact isolated compact roots, strict serial
execution/resume, stop-and-preserve failure behavior, sealed runtime-log bytes,
and aggregate validation. Its 8/8 focused proof covers matrix preflight,
private authorization, missing-source denial, exact result validation,
cleanup-scope enforcement, and fail-closed pre-daemon behavior. The exact
pinned source fixture is restored and full local aggregate proof is green. The
active unit still requires one later Daeron-authorized disposable QEMU matrix.
The deterministic phrase is only an accidental-execution fence, not
authentication or proof of authority. The coordinator consumes the existing
daemon activation, permit, runtime, and recovery owners rather than introducing
a second launch owner.

This unit does not expose public `start`, `stop`, `destroy`, snapshot, rollback,
image-build, or scaling commands; claim guest readiness; promote P5 networking;
mount an image; mutate a production AIPC; install a guest; or deploy model
weights, memory, ACE, ASPS, cache, or Kerminal code. Daeron remains the only
authority for real VM deployment and the physical `GATE-P4` launch.

## Dependency Decision

The existing public planner remains permanently non-mutating. QEMU process
ownership and QMP observation live behind the authenticated daemon and consume
the canonical protocol plus P3 registry authority. All eighteen bounded P4 phase items are implemented; the coordinator is
implemented and non-launch proven; the exact fixture is restored, while valid
full-matrix execute authority, explicit production exclusions, checkpoint
execution, and `GATE-P4` remain open.
The runtime remains Python-standard-library-only; QEMU and `qemu-img` are
external executables whose results must be independently re-observed.
