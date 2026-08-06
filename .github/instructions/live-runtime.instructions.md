---
applyTo: "src/somnus_protocol/**/*.py,src/somnus_vm/**/*.py,test/vm_lab/**/*.py,configs/*.toml"
---

# Live VM Lab runtime

- `src/somnus_protocol` is the pure shared authority and `src/somnus_vm` is the
  promoted host/guest implementation package.
- Preserve standard-library-only imports in the live runtime.
- Keep the public CLI and host planner permanently non-mutating. Internal
  mutation belongs only to the separately invoked daemon.
- P3 permits only daemon-owned verified base import and sparse-overlay creation;
  it does not launch QEMU, negotiate QMP, or establish guest readiness.
- Keep QEMU argv shell-free, non-daemonized, JSON-blockdev-based, and
  loopback-forwarded.
- Keep guest bootstrap local-only, one-read verified, bounded, and atomic.
- Extend direct behavioral evidence with every claim.
- Run `PYTHONPATH=src python test/vm_lab/smoke.py`.

Full authority: [`../../AGENTS.md`](../../AGENTS.md).
