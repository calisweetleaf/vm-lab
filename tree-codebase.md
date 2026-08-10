# File Tree: vm-lab

**Generated:** 2026-08-07 12:37:47 CDT
**Root Path:** `/home/daeron/Repositories/vm-lab`

This is a point-in-time filesystem inventory. It records presence only; `AGENTS.md`, `filetree.md`, and `src/somnus_vm/topology.py` remain the authority for disposition and execution. Generated caches are omitted; sealed snapshot claims are listed but not modified by this inventory.

```text
.
├── .codex
│   └── skills
│       └── vm-lab
│           └── SKILL.md
├── .github
│   ├── instructions
│   │   ├── live-runtime.instructions.md
│   │   └── preserved-surfaces.instructions.md
│   ├── workflows
│   │   └── repository-integrity.yml
│   └── copilot-instructions.md
├── .sovereign
│   ├── PROVENANCE.md -> ../PROVENANCE.md
│   ├── golden_paths.json
│   ├── session_state.json
│   └── topology_fingerprint.txt
├── archive
│   ├── lineage
│   │   ├── guest
│   │   │   └── vm_bootstrap.py
│   │   └── host
│   │       └── vm_supervisor.py
│   └── vm-lab-first-zip.zip
├── components
│   ├── contracts
│   │   ├── dev_session_schemas.py
│   │   ├── model_schemas.py
│   │   ├── session.py
│   │   └── session_manager.py
│   ├── guest
│   │   ├── agent
│   │   │   └── digital_twin.py
│   │   └── runtime
│   │       ├── ace_dev_environment.py
│   │       ├── memory_core.py
│   │       ├── memory_interconnect.py
│   │       ├── modifying_prompts.py
│   │       └── system_cache.py
│   ├── host
│   │   └── vm_image_manager.py
│   └── operator
│       ├── native_tools
│       │   ├── git_integration_module.py
│       │   ├── internal_browser.py
│       │   └── web_search_research.py
│       ├── ai_action_orchestrator.py
│       └── ai_advanced_shell.py
├── configs
│   ├── guest-bootstrap.example.toml
│   ├── host.toml
│   └── lab.toml
├── docs
│   ├── decisions
│   │   ├── 0001-single-mutation-owner.md
│   │   ├── 0002-sqlite-registry.md
│   │   ├── 0003-package-authority-boundaries.md
│   │   ├── 0004-stopped-vm-rollback.md
│   │   └── 0005-errors-and-exit-codes.md
│   ├── lineage
│   │   ├── somnus_vm_architecture.md
│   │   └── vm_architecture_iteration_2_additions.md
│   ├── DISPOSABLE_FIXTURE_POLICY.md
│   ├── MIGRATION_MAP.md
│   ├── PLATFORM_MATRIX.md
│   ├── RECONSTITUTION_AUDIT.md
│   └── plan-index.json
├── extras
│   └── file_processing
│       ├── legacy
│       │   ├── enhanced_file_manager.py
│       │   ├── persistent_processing_queue.py
│       │   ├── semantic_chunking.py
│       │   └── semantic_chunking_rewrite.py
│       ├── processors
│       │   ├── advanced_files.py
│       │   └── universal_file_processors.py
│       └── sovereignty.py
├── quarantine
│   ├── evidence
│   │   └── vm_agent_integration_report.md
│   ├── runtime
│   │   ├── collaboration_manager.py
│   │   ├── digital_twin_manager.py
│   │   ├── vm_orchestrator.py
│   │   └── vm_settings.py
│   └── tests
│       └── benchmark_vm_system.py
├── scripts
│   ├── run_gate_p4.py
│   └── verify_repository.py
├── snapshots
│   └── v0.1
│       └── manifest.json
├── src
│   ├── somnus_protocol
│   │   ├── __init__.py
│   │   ├── _validation.py
│   │   ├── agent.py
│   │   ├── control.py
│   │   ├── events.py
│   │   ├── version.py
│   │   └── vm.py
│   └── somnus_vm
│       ├── contracts
│       │   ├── __init__.py
│       │   ├── agent.py
│       │   └── vm.py
│       ├── guest
│       │   ├── __init__.py
│       │   └── bootstrap.py
│       ├── host
│       │   ├── __init__.py
│       │   ├── exec_guard.py
│       │   ├── images.py
│       │   ├── launch_authority.py
│       │   ├── p4_gate.py
│       │   ├── planner.py
│       │   ├── ports.py
│       │   ├── qemu.py
│       │   ├── qemu_exec_guard.py
│       │   ├── qemu_log_guard.py
│       │   ├── qemu_logs.py
│       │   ├── qemu_process.py
│       │   ├── qemu_runtime.py
│       │   ├── qemu_runtime_journal.py
│       │   ├── qmp.py
│       │   ├── qmp_identity.py
│       │   ├── registry.py
│       │   ├── service.py
│       │   └── storage.py
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── client.py
│       ├── config.py
│       ├── daemon.py
│       ├── daemon_runtime.py
│       ├── doctor.py
│       ├── topology.py
│       └── transport.py
├── test
│   └── vm_lab
│       ├── fixtures
│       │   ├── images
│       │   │   └── ubuntu-minimal-noble-amd64-20260801.json
│       │   └── protocol
│       │       └── v0_vm_record.json
│       ├── runs
│       │   ├── 20260805T024125Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T032541Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T054741Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T054841Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T055202Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T055206Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T055238Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T055436Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T055444Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T164457Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T165125Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T165201Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T170028Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T170837Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T170916Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T171215Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T180544Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T182129Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T182614Z
│       │   ├── 20260805T182738Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T182839Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T185253Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T194231Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T194938Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T211613Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T211740Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T212927Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T214212Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T215543Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T220504Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260805T234424Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260806T000350Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260807T091421Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260807T110705Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260807T110922Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       │   ├── 20260807T111859Z
│       │   │   ├── result.json
│       │   │   ├── result.log
│       │   │   └── result.md
│       ├── 20260807T114854Z
│       │   ├── result.json
│       │   ├── result.log
│       │   └── result.md
│       ├── 20260807T121437Z
│       │   ├── result.json
│       │   ├── result.log
│       │   └── result.md
│       ├── 20260807T121940Z
│       │   ├── result.json
│       │   ├── result.log
│       │   └── result.md
│       └── 20260807T123747Z
│           ├── result.json
│           ├── result.log
│           └── result.md
│       ├── smoke.py
│       ├── test_daemon_registry_p2.py
│       ├── test_daemon_runtime_p4.py
│       ├── test_daemon_storage_p3.py
│       ├── test_daemon_transport_p2.py
│       ├── test_disposable_launch_authority_p4.py
│       ├── test_doctor_p4.py
│       ├── test_p4_gate_coordinator.py
│       ├── test_protocol_agent.py
│       ├── test_protocol_control.py
│       ├── test_protocol_host_consumption.py
│       ├── test_protocol_settings.py
│       ├── test_protocol_vm_lifecycle.py
│       ├── test_protocol_vm_primitives.py
│       ├── test_qemu_exec_guard_p4.py
│       ├── test_qemu_logs_p4.py
│       ├── test_qemu_planning_p4.py
│       ├── test_qemu_process_p4.py
│       ├── test_qemu_runtime_journal_p4.py
│       ├── test_qemu_runtime_p4.py
│       ├── test_qmp_identity_p4.py
│       ├── test_qmp_p4.py
│       ├── test_registry_p2.py
│       ├── test_registry_p4.py
│       ├── test_registry_service_p2.py
│       ├── test_storage_p3.py
│       ├── test_storage_p3_adversarial.py
│       ├── test_storage_runtime_p4.py
│       └── test_vm_lab.py
├── .gitignore
├── .pre-commit-config.yaml
├── AGENTS.md
├── ARCHITECTURE_MAP.md
├── CONTEXT.md
├── FAILURE_GRAMMAR.md
├── MEMORY.md
├── NOTEPAD.md
├── PLAN.md
├── PROVENANCE.md
├── README.md
├── SCOPE.md
├── SNAPSHOT.md
├── SOTA_RUN.md
├── STATE.md
├── Somnus-Core-Ideals.md
├── TASK.md
├── TOPOLOGY.md
├── filetree.md
├── pyproject.toml
├── requirements-candidates.txt
├── requirements-file-processing.txt
├── requirements.txt
├── source-manifest.json
├── tree-codebase.md
└── workflows-new.md

83 directories, 259 files
```
