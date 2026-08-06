# VM Lab Target Platform Matrix

## Supported targets

| Target | Required baseline | Promotion evidence |
| --- | --- | --- |
| Linux x86_64 TCG | Python 3.12+, QEMU/qemu-img with qcow2 and QMP | complete disposable TCG lifecycle |
| Linux x86_64 KVM | TCG baseline plus readable/writable `/dev/kvm` and compatible CPU virtualization | complete disposable KVM lifecycle with acceleration proof |

TCG and KVM are explicit profiles. TCG is not reported as a KVM fallback, and a
TCG pass never closes the KVM gate.

## QEMU and qcow2 contract

The promoted feature set will use QMP greeting and capability negotiation,
`query-status`, `query-name`, `query-uuid`, `query-block`,
`query-cpus-fast`, `system_powerdown`, `quit`, required block-graph
transactions, JSON `-blockdev`, non-daemonized child ownership, qcow2 backing
chains, sparse overlays, and `qemu-img info/check`.

Every physical run records the QEMU and qemu-img versions, command hash,
acceleration, machine profile, image hash, backing chain, and exact supported
commands. Unsupported required features block the profile; they are not
silently removed.

## Current observed host

Observed on 2026-08-05:

- architecture: Linux x86_64;
- Python: 3.12.3;
- QEMU: 8.2.2;
- qemu-img: 8.2.2 at `/usr/bin/qemu-img`;
- `/dev/kvm`: readable and writable by the operator through group `kvm`;
- current default profile: explicit TCG;
- P3 disposable source:
  `/tmp/vm-lab-p3-fixture-20260801.qcow2`, observed as 264,306,688 bytes;
- repository authority:
  `test/vm_lab/fixtures/images/ubuntu-minimal-noble-amd64-20260801.json`;
- pinned source SHA256:
  `b3064efb500d71d6ccbe619b1716062b803e285116e040627b430aaee14cced6`.

## Current physical proof boundary

[`GATE-P3`](../test/vm_lab/runs/20260805T211740Z/result.json) used the pinned
Ubuntu Minimal 24.04 amd64 source and the local `qemu-img` 8.2.2 executable to
import one verified immutable base and create two independently measured sparse
100 GiB overlays. The run passed 26 checks with no failures or skips, including
full backing-chain and virtual-size inspection, alias rejection, checkpoint
recovery, and daemon startup reconciliation.

This is **storage-only** physical evidence. The source and test roots are
disposable and are not a production AIPC disk. The gate did not launch
`qemu-system-x86_64`, negotiate QMP, boot Ubuntu, prove guest readiness, or close
either the TCG or KVM lifecycle promotion evidence in the table above.
