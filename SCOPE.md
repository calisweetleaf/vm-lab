## Engagement Mode

- mode: EDIT
- target_module: `scripts/verify_repository.py` and `test/vm_lab/test_vm_lab.py`
- target_module_provenance: `PLAN.md` Phase P0 and committed CTMv3 baseline `f13fb3d`
- justification: I am surgically extending the existing structural verifier and consumed smoke harness because a wrapper would duplicate their repository and evidence authority.
- author: daeron
- collaborator: Codex
- date: 2026-08-05

## Edit Scope

I include only the Phase P0 enforcement surfaces:

- strict duplicate-aware parsing of `docs/plan-index.json`;
- phase, dependency, gate, task-state, owner, and evidence validation;
- direct `--plan-only --json --root` fixture execution;
- live and hostile-fixture checks through the existing smoke harness;
- fail-loud, still-continue evidence without runtime mutation.

I do not change `src/somnus_vm/`, add lifecycle mutation, choose a base image,
launch QEMU, or modify the sealed v0.1 snapshot.

## Dependency Decision

Both edited Python files keep their original ownership and carry complete
Modified provenance blocks. Repository infrastructure remains outside the
immutable v0.1 runtime manifest; this edit is recorded in `PROVENANCE.md`.
