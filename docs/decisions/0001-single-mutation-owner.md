# ADR 0001: One Host Mutation Owner

**Status:** accepted  
**Date:** 2026-08-05  
**Decision owner:** Daeron

## Decision

`somnus-vm-daemon` will be the only process authorized to mutate the VM
registry, durable leases, owned storage, QEMU processes, or lifecycle state.
The CLI retains standalone read-only `doctor`, `topology`, and `plan` commands.
Every lifecycle command becomes a client request to the daemon.

The daemon constructs exactly one supervisor and one registry owner. Guest,
Kerminal, VM-Go, Artifact, candidate, lineage, and quarantine code cannot create
a second supervisor or launch QEMU directly.

## Consequences

- A CLI process cannot become an emergency lifecycle owner.
- Daemon unavailability is a structured failure, not permission for local
  mutation.
- Recovery is owned by the daemon journal and observed machine truth.
- The exact local IPC framing remains a P1/P2 implementation decision, but it
  cannot weaken the ownership boundary.

## Rejected

- importing the lineage supervisor;
- letting each CLI invocation own a registry and child process;
- reporting intended commands as completed machine work.
