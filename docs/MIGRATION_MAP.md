# VM Lab Migration Map

Every original file remains represented once in the reorganized tree. Placement is a promotion decision, not a deletion euphemism.

| Original surface | New surface | Disposition | Reason |
| --- | --- | --- | --- |
| `vm_supervisor.py` | `archive/lineage/host/vm_supervisor.py` | lineage | preserves architecture source; unsafe PID/network/snapshot semantics cannot boot |
| `vm_image_manager.py` | `components/host/vm_image_manager.py` | candidate | P3 replaced its narrow useful boundary with live `host/images.py` and `host/storage.py`; broad copy, SSH-install, mutable-metadata, and deletion behavior remains unpromoted |
| `vm_bootstrap.py` | `archive/lineage/guest/vm_bootstrap.py` | lineage | replaced by hash-required safe bootstrap |
| `ai_advanced_shell.py` | `components/operator/ai_advanced_shell.py` | candidate | monolith preserved; host/guest and Docker boundaries still need repair |
| `ai_action_orchestrator.py` | `components/operator/ai_action_orchestrator.py` | candidate | client/result shapes salvageable; embedded supervisor is not |
| `system_cache.py` | `components/guest/runtime/system_cache.py` | candidate | belongs in guest runtime, not host lifecycle |
| `src/core/*.py` | `components/guest/runtime/` | candidate | memory, ASPS, ACE, and interconnect preserved outside boot |
| `src/native_tools/*.py` | `components/operator/native_tools/` | candidate | useful tools require explicit adapters and lazy registration |
| `src/digital-twin/digital_twin.py` | `components/guest/agent/digital_twin.py` | candidate | probable guest-agent lineage; protocol and locking repair required |
| `schemas/*.py` | `components/contracts/` | candidate lineage | mixed schemas preserved while one live VM authority replaces duplicates |
| `universal_file_processors.py` | `extras/file_processing/processors/` | cold | useful broad processor library, never boot-imported |
| `advanced_files.py` | `extras/file_processing/processors/` | cold | strongest domain processors, pending lazy dispatcher |
| `sovereignty.py` | `extras/file_processing/sovereignty.py` | cold | capability-policy concept retained; validator requires rewrite |
| enhanced/queue/semantic files | `extras/file_processing/legacy/` | quarantine addon | disconnected contracts, eager dependencies, and unsafe scaling |
| `vm_orchestrator.py` | `quarantine/runtime/` | quarantine | mocks, log-only installs, double-supervisor authority |
| `vm_settings.py` | `quarantine/runtime/` | quarantine | missing imports, false-success initialization, duplicate authority |
| `collaboration_manager.py` | `quarantine/runtime/` | quarantine | absent dependencies and duplicate collaboration surface |
| `digital_twin_manager.py` | `quarantine/runtime/` | quarantine | distinct broken manager, not the guest daemon |
| `benchmark_vm_system.py` | `quarantine/tests/` | quarantine | simulated sleeps are not benchmarks |
| old 9/9 report | `quarantine/evidence/` | quarantine | validates absent files from another tree |
| architecture documents | `docs/lineage/` | lineage | design intent retained without treating claims as current proof |

## P3 image replacement settlement

[`GATE-P3`](../test/vm_lab/runs/20260805T211740Z/result.json) promotes the clean
replacement in `src/somnus_vm/host/images.py` and
`src/somnus_vm/host/storage.py`: a strict manifest, an argv-only observed
`qemu-img` backend, immutable base import, owned sparse overlay publication, and
checkpointed recovery under the one daemon.

`components/host/vm_image_manager.py` remains candidate donor evidence. P3
neither imports it nor promotes its whole-image copies, embedded boot/install
workflow, mutable best-effort JSON, broad exception returns, or unverified
deletion.
