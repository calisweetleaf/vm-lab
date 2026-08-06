# ADR 0003: Protocol, Host, and Guest Package Authorities

**Status:** accepted  
**Date:** 2026-08-05  
**Decision owner:** Daeron

## Decision

- `somnus_protocol` owns pure, versioned, dependency-light schemas and result
  envelopes with no files, sockets, processes, secrets, or side effects.
- `somnus_vm` owns the permanently non-mutating public host planner and CLI plus
  the separately invoked daemon's registry, storage, and recovery authority.
  QEMU/QMP, networking, and lifecycle implementations remain host-owned but
  gated until their own physical evidence exists.
- `somnus_guest` owns authenticated in-guest readiness, execution, file
  transfer, quiesce, bootstrap, and in-place release activation.
- Kerminal, VM-Go, cognitive runtime, and Artifact processing remain separate
  consumers or services connected through explicit contracts.

Host boot cannot import guest cognition, operator tooling, models, browsers, or
file processors. Guest boot cannot import host lifecycle or QMP owners.

## Current realization

P2 establishes the authenticated daemon and transactional registry as the only
internal mutation owner. P3 composes verified immutable-base import and owned
sparse-overlay creation beneath that same owner. Its physical evidence is
[`test/vm_lab/runs/20260805T211740Z`](../../test/vm_lab/runs/20260805T211740Z).

Neither phase changes the public planner into a mutation path. P3 invokes
`qemu-img` for measured storage work only; it does not launch QEMU, negotiate
QMP, authenticate a guest, or expose a public VM lifecycle command.

## Rejected

- one recursive package containing every Somnus surface;
- using candidate directory placement as promotion;
- guest readiness depending on cognitive-runtime health;
- Kerminal or VM-Go constructing Python-lab QEMU processes.
