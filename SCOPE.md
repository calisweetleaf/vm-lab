## Engagement Mode

- mode: COMPOSE
- target_module: `src/somnus_vm/host/qmp.py`, `src/somnus_vm/host/qemu_process.py`, `src/somnus_vm/host/registry.py`, `src/somnus_vm/host/service.py`, and `src/somnus_vm/daemon_runtime.py`
- target_module_provenance: `PLAN.md` Phase P4, `TASK-P4-001`, the closed
  P1 canonical lifecycle contracts, P2 single-owner journal, P3 physical image
  authority, and the existing non-mutating QEMU plan builder
- justification: QEMU launch must become a separately owned, QMP-observed
  daemon operation without turning planner intent, a PID, a socket pathname,
  or a child process into false machine truth
- author: daeron
- collaborator: Codex
- date: 2026-08-05

## Compose Scope

This unit includes:

- a required schema split between immutable disk ownership
  (`vm_id`, generation, disk ID, ownership token, materializing operation,
  canonical path, base identity/hash, virtual size, and format) and mutable
  runtime observations (allocated size, QEMU actual size, dirty/corrupt state,
  observation time, boot/lifecycle identity, and quiesce state);
- a bounded Unix-socket QMP client that parses the greeting, negotiates
  capabilities, correlates response IDs, records asynchronous events
  separately, and rejects malformed, oversized, mismatched, or timed-out
  traffic;
- one argv-only, non-daemonized QEMU process owner that consumes validated
  planner and P3 registry records without shell interpolation;
- explicit PID, pidfile, executable, process-start-time, command-hash,
  VM-UUID, QMP-UUID, and QMP-status agreement before runtime truth is
  published;
- bounded serial/QEMU logs, process-group termination, orphan emergency
  records, and identity revalidation before any post-restart signal;
- daemon-journaled launch checkpoints and deterministic adoption-or-cleanup
  behavior for a Daeron-authorized disposable P3 overlay.

This unit does not expose public `start`, `stop`, `destroy`, snapshot, rollback,
image-build, or scaling commands; claim guest readiness; promote P5 networking;
mount an image; mutate a production AIPC; install a guest; or deploy model
weights, memory, ACE, ASPS, cache, or Kerminal code. Daeron remains the only
authority for real VM deployment and the physical `GATE-P4` launch.

## Dependency Decision

The existing planner remains permanently non-mutating. QEMU process ownership
and QMP observation live behind the authenticated daemon and consume the
canonical protocol plus P3 registry authority. The runtime remains
Python-standard-library-only; QEMU and `qemu-img` are external executables whose
results must be independently re-observed. Candidate and lineage supervisors
remain unpromoted because PID liveness, fabricated addresses, implicit
daemonization, and synthetic waits cannot satisfy P4 identity.
