---
applyTo: "src/somnus_vm/**/*.py,test/vm_lab/**/*.py,configs/*.toml"
---

# Live VM Lab runtime

- `src/somnus_vm` is the only promoted package.
- Preserve standard-library-only imports in the live runtime.
- Keep host planning non-mutating until the daemon/registry/QMP gates pass.
- Keep QEMU argv shell-free, non-daemonized, JSON-blockdev-based, and
  loopback-forwarded.
- Keep guest bootstrap local-only, one-read verified, bounded, and atomic.
- Extend direct behavioral evidence with every claim.
- Run `PYTHONPATH=src python test/vm_lab/smoke.py`.

Full authority: [`../../AGENTS.md`](../../AGENTS.md).
