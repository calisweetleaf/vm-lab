# VM Agent Integration Validation Report

- Started (UTC): 2026-02-09T00:38:07.205062+00:00
- Ended (UTC): 2026-02-09T00:38:07.555952+00:00
- Total checks: 9
- Passed: 9
- Failed: 0

## Check Results

| Check | Status | Duration (ms) | Details |
|---|---|---:|---|
| compile:vm_agent.py | PASS | 76.70 | Compiled: vm_agent.py |
| compile:memory_core.py | PASS | 59.09 | Compiled: memory_core.py |
| compile:memory_integration.py | PASS | 19.40 | Compiled: memory_integration.py |
| methods:MemoryManager | PASS | 44.14 | MemoryManager methods present: initialize, store_memory, store_event, fetch_recent, retrieve_memories, synthesize_topic |
| methods:AsyncLoopRunner | PASS | 63.36 | AsyncLoopRunner methods present: start, submit, run, stop |
| methods:VMAgent | PASS | 65.21 | VMAgent methods present: _run_async, _submit_async, _enqueue_event, _event_writer_loop, _detect_metric_anomalies, _can_execute_action, _runtime_snapshot, _record_event, _self_training_loop, action_pause_training, shutdown |
| methods:VMDigitalTwinMemoryBridge | PASS | 20.17 | VMDigitalTwinMemoryBridge methods present: store_event, fetch_recent, build_training_batch, summarize_recent |
| text:vm_agent_async_patterns | PASS | 0.68 | Text assertions passed for vm_agent.py |
| text:memory_integration_import_fallback | PASS | 0.65 | Text assertions passed for memory_integration.py |
