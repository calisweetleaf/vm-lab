## Engagement Mode

- mode: COMPOSE
- target_module: `src/somnus_vm/`
- target_module_provenance: `vm_lab.zip` sha256 `63f698f7af3ce989de8885a3b3b68b7214dedbffb1ac6671636b1f5d13829f94`
- justification: I am composing the viable VM lineage into one contract-first host control plane while preserving guest, operator, file-processing, and quarantined code behind explicit runtime boundaries.
- author: daeron
- collaborator: Codex
- date: 2026-08-04

## Composition Scope

I include the following surfaces in this snapshot:

- canonical VM and guest-agent contracts;
- TOML configuration and read-only doctor;
- bind-checked, explicitly non-reserved plan-time port allocation;
- explicit, non-daemonized QEMU launch planning;
- JSON block-device encoding and AF_UNIX path-budget validation;
- install-safe default configuration and checkout-independent topology;
- local-only, hash/size-required, traversal-safe in-guest bootstrap;
- configuration-bound bootstrap markers and staged payload placement;
- migration of every source artifact into live, candidate, cold, lineage, or quarantine placement;
- direct-Python integration proof including a real wheel build/install and 33-file hash verification.

I do not promote lifecycle mutation, snapshots, image building, the digital-twin agent, memory/ASPS, the operator shell, native tools, or file processing in this pass. Their source is preserved, but their existing contracts do not pass the new boundary. In particular, `start`, `ready`, `stop`, and `destroy` remain withheld until the real-metal QMP gate.

## Dependency Decision

The promoted package remains standard-library only. Candidate dependency lists are isolated in `requirements-candidates.txt` and `requirements-file-processing.txt`; neither is imported during host boot.
