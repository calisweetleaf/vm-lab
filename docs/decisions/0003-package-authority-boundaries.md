# ADR 0003: Protocol, Host, and Guest Package Authorities

**Status:** accepted  
**Date:** 2026-08-05  
**Decision owner:** Daeron

## Decision

- `somnus_protocol` owns pure, versioned, dependency-light schemas and result
  envelopes with no files, sockets, processes, secrets, or side effects.
- `somnus_vm` owns host planning, the future daemon, registry, QEMU/QMP,
  storage, networking, lifecycle, and recovery.
- `somnus_guest` owns authenticated in-guest readiness, execution, file
  transfer, quiesce, bootstrap, and in-place release activation.
- Kerminal, VM-Go, cognitive runtime, and Artifact processing remain separate
  consumers or services connected through explicit contracts.

Host boot cannot import guest cognition, operator tooling, models, browsers, or
file processors. Guest boot cannot import host lifecycle or QMP owners.

## Rejected

- one recursive package containing every Somnus surface;
- using candidate directory placement as promotion;
- guest readiness depending on cognitive-runtime health;
- Kerminal or VM-Go constructing Python-lab QEMU processes.
