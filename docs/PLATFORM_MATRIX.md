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
- qemu-img: 8.2.2;
- `/dev/kvm`: readable and writable by the operator through group `kvm`;
- current default profile: explicit TCG;
- canonical disposable base image: absent.

The absent image is a truthful P3 blocker. No production AIPC disk, arbitrary
qcow2 file, mock image, or synthetic boot may replace it.
