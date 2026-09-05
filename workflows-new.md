# Sovereign Workflow Driver (Autonomy Mode)

This file is the live runtime doctrine for this workspace.
It is not a generic coding-agent cheat sheet.

## 0) Core Position

This system is an autonomy experiment first.
Coding is one capability surface, not the center.

Control-plane stack:
1. `mcp_server.py` (runtime orchestrator and module registrar)
2. `tools/exoskeleton_tool.py`
3. `tools/lisan_al_gaib.py` (integrated inside exoskeleton)
4. `memory_tool.py` and `memory_interconnect` (must be used alongside the codex native memory for rich context every answer and true learning.)

File-intelligence stack:
1. `enhanced_code_analysis_tool.py`
2. `file_tool.py`

Codebase-intelligence stack: (to be used inside of projects)
1. `session_manager_tool`
2. `project_context_tool.py`
3. `memory_interconnect.py`

Enhanced-intelligence stack:
1. `auto_tool_module.py`
2. `web_tool.py` and `enhanced_web_tool.py`
3. `openrouter_agent_tool.py` and `openrouter_planner_tool.py` (both not being used for a reason. No available credits.)
4. `meta_intelligence_engine.py`
5. `visual_tool.py`

## 1) Runtime Topology (Source-of-Truth Modules)

### 1.1 Control Plane

`mcp_server.py`
- runtime tool registration order
- cross-module orchestration
- async/sync tool invocation bridge
- live tool-catalog sync into exoskeleton

`tools/exoskeleton_tool.py`
- `bb7_exo_bootstrap` - Bootstraps capability context, performs catalog sync checks, and monitors critical tool health.
- `bb7_exo_list_tool_categories` - Returns available categories, tool counts, neighbor structures, and sample tools.
- `bb7_exo_category_specific_tools` - Lists all tools within a category, sorted by reliability score.
- `bb7_exo_route` - Routes user intents to candidate tools using semantic cosine similarity and graph distance signals.
- `bb7_exo_plan` - Generates alternative multi-step tool execution chains with confidence estimates and fallback plans.
- `bb7_exo_preemptive_recovery` - Scans planned execution chains for low-reliability steps and suggests alternative tools before execution.
- `bb7_exo_route_focused` - progressive-disclosure tool routing returning only top-N tools with one-line descriptions to save tokens.
- `bb7_exo_execute_step` - Records outcomes of individual steps in checkpointed plans to ensure persistence across sessions.
- `bb7_exo_resume_plan` - Lists active plans or resumes an interrupted checkpointed plan.
- `bb7_exo_kpi_report` - Generates completion rates, success rates, and elapsed time metrics for plans.
- `bb7_exo_suggest_next` - Predicts the next logical tool or category using transition probabilities from momentum tracking.
- `bb7_exo_reflect` - Records outcome telemetry, reinforces tool and chain reliability priors, and fires MCTS planner training signals.
- `bb7_exo_state` - Returns the current state of tool priors, chain priors, discovered macros, and recovery strategies.
- `bb7_exo_get_recent_activity` - Coordinates across multiple AI instances by listing tool outcomes, active workflows, and momentum context.
- `bb7_exo_briefing` - Generates a natural-language markdown briefing describing golden paths, current state, and execution options.
- `bb7_lisan_recall` - Performs session resurrection by aggregating BM25 memories, active plans, decisions, and outcomes into a context blob.
- `bb7_lisan_intend` - Decomposes user messages into spectral intent weights and maps them to tool categories and momentum bonuses.
- `bb7_lisan_distill` - Logs completed cognitive trajectories (chat + tool executions) to trajectories dataset for model distillation.


`tools/lisan_al_gaib.py` (proactive orchestrator engine)
- `_SpectralIntentDecomposer` - Positional character-level TF-IDF similarity mapping to decompose user messages into intents and categories.
- `_ThompsonContextualBandit` - Context-conditioned Bayesian posterior sampling (Beta draw) with Digital Twin neural Q-bonuses for stochastic routing.
- `_MCTSPlanner` - Monte Carlo Tree Search running 100 simulations with 15% adversarial failure injection to discover Pareto-optimal execution chains.
- `_TopologicalMomentumManifold` - Wasserstein change-point detection context for topological tracking.
- `GoldenPathOracle` - Semantic matching layer that links user intents to curated workflows defined in `golden_paths.json`.
- `SessionMomentum` - Builds a 7-signal V3 manifold tracking tool category transitions and injecting late-init Digital Twin attention weights.
- `_MetaLearningEngine` - Performs autonomous weight tuning and path discovery based on trajectory success feedback.
- `NarrativeEngine` - Translates quantitative routing scores and reliability histories into human-readable markdown briefing prose.
- `TelemetryDistillationEngine` / `TrajectoryBuilder` - Logs completed session trajectories to RFT datasets asynchronously.
- `CognitiveJournalSubsystem` - Persists decision records, rationale, outcomes, and MCTS training signals to `data/digital_twin/journal.db`.


### 1.2 Code and Execution Surfaces

`tools/file_tool.py`
- `bb7_read_file` - Reads file content as raw text with automatic encoding detection; supports a metadata analysis mode (`show_analysis=true`).
- `bb7_write_file` - Creates/overwrites files, creating parent directories and producing automatic backups of existing files unless disabled.
- `bb7_append_file` - Appends content to the end of a file with optional backup creation.
- `bb7_copy_file` - Copies files or entire directories recursively, with metadata preservation and overwrite controls.
- `bb7_move_file` - Renames or moves files and directories across paths.
- `bb7_delete_file` - Deletes a file or directory; requires `force=true` for directory deletions or non-backup destructive file deletions.
- `bb7_list_directory` - Lists directory files/subdirectories, offering details, hidden files filters, and sorting.
- `bb7_search_files` - Recursively searches for files by name/content patterns under a specified directory, with size and depth bounds.
- `bb7_file_info` - Gathers comprehensive filesystem metadata, text statistics, classes/functions count, and potential secrets count.
- `bb7_get_file_info` - Compatibility alias that routes directly to `bb7_file_info`.
- `bb7_file_cache_stats` - Compatibility shim returning operation-history stats and confirming cache removal.
- `bb7_operation_history` - Lists recent filesystem operations recorded during the current runtime session.


`tools/enhanced_code_analysis_tool.py`
- `bb7_analyze_code_complete` - Full static AST analysis on Python files under 60KB/800 lines returning complexity metrics, lines of code, control flow analysis (CFA) nodes/edges, and data flow analysis (DFA) def-use chains. Larger files deflect to shell commands.
- `bb7_security_audit` - Security vulnerability scanner utilizing AST inspection on Python files under 60KB to check for dangerous methods (`eval`, `exec`, `subprocess`) or hardcoded secrets. Larger files deflect to grep commands.
- `bb7_python_execute_secure` - Runs Python code securely in a RestrictedPython sandbox, supporting input data injection, stateless/persistent context, and resource limitations.
- `bb7_get_execution_audit` - Retrieves interpreter execution records and security logs from the SecurePythonInterpreter.


`tools/shell_tool.py`
- `bb7_run_command` - Execute shell commands safely with command security validation, timeout settings, and environment isolation.
- `bb7_get_system_info` - Retrieve comprehensive platform, architecture, hardware metrics (CPU cores, memory usage), and shell environment info.
- `bb7_list_processes` - List and analyze running processes, sorted by resource utilization (CPU, memory), PID, or name.
- `bb7_get_command_history` - View execution history, exit codes, and latency of commands run via the shell tool.

- Has command safety gate for risky patterns.

`tools/enhanced_web_tool.py`
- `bb7_fetch_url` - Fetches and parses content from any URL with automated content-type detection, readability filtering, and metadata extraction.
- `bb7_search_web` - Performs a multi-engine web search with snippet aggregation and resource ranking.
- `bb7_analyze_webpage` - Audits webpage structure, link topologies, image tags, scripts, and SEO quality metrics.
- `bb7_download_file` - Securely downloads files with size restrictions and content type validation.


`tools/visual_tool.py`
- `bb7_take_screenshot` - Takes a screenshot of the primary screen or specific coordinates, saving the image or returning it as metadata.
- `bb7_find_on_screen` - Finds a sub-image template or text element on the screen using template matching and OCR.
- `bb7_click_element` - Performs a click action at a target coordinate or element location on the screen.
- `bb7_screen_monitor` - Monitors the screen state for layout changes, differences, or specific screen changes.
- `bb7_window_info` - Gathers geometry, titles, process owners, and layout structure of active desktop windows.


### 1.3 Memory and Session Surfaces

`tools/memory_tool.py`
- `bb7_memory_store` - Stores a key-value memory with category, importance, tags, and automatically triggers BM25 semantic indexing + Ebbinghaus decay initialization.
- `bb7_memory_retrieve` - Retrieves a memory by key and updates Ebbinghaus stability (reinforces retention).
- `bb7_memory_delete` - Deletes a memory entry by key.
- `bb7_memory_list` - Lists memory keys with filtering (prefix, category, min importance) and sorting (timestamp, importance, decay, alphabetical, access).
- `bb7_memory_search` - Performs BM25-powered semantic search with Ebbinghaus decay reranking.
- `bb7_memory_surface_context` - Surfaces the most relevant memories for a context blob using BM25 and Ebbinghaus decay.
- `bb7_memory_bulk_store` - Atomically stores multiple memory entries in a single write.
- `bb7_memory_get_related` - Fetches semantically related memories for a key using BM25.
- `bb7_memory_timeline` - Returns a chronological view of recent memories with Ebbinghaus retention scores.
- `bb7_memory_export` - Exports memories to Markdown or JSON formats.
- `bb7_memory_stats` - Retrieves statistics on memory size, categories, and decay patterns.
- `bb7_memory_insights` - Compiles narrative insights combining local statistics and BM25 network analysis.
- `bb7_memory_consolidate` - Archives old low-importance memories and prunes the BM25 index.
- `bb7_memory_categories` - Lists available memory categories and descriptions.


`tools/memory_interconnect.py`
- `bb7_memory_analyze_entry` - Index a memory entry using BM25, extract key concepts, and map relationship links to existing memories.
- `bb7_memory_intelligent_search` - Perform BM25-ranked semantic search across the entire memory network.
- `bb7_memory_get_insights` - Generate a report summarizing memory network density, core concepts, and key nodes.
- `bb7_memory_cluster` - Group memories into semantic clusters based on text similarity.
- `bb7_memory_find_gaps` - Scan the memory database for concepts that are referenced multiple times but lack a dedicated memory entry.
- `bb7_memory_graph` - Exports memory relationship graphs in Graphviz DOT format for visualization.
- `bb7_memory_consolidate_index` - Archive old low-importance memories and prune BM25 index references.
- `bb7_memory_concept_network` - Retrieves related memories and concepts connected to a specific concept node.
- `bb7_memory_extract_concepts` - Extracts key BM25 indexing tokens/concepts from a raw text payload.


`tools/session_manager_tool.py`
- `bb7_start_session` - Starts a new cognitive session with a primary goal and optional tags/context.
- `bb7_log_event` - Logs session events with automatic memory formation.
- `bb7_capture_insight` - Captures cognitive insights, linking them to concepts and tags.
- `bb7_record_workflow` - Records procedural workflows or execution patterns.
- `bb7_update_focus` - Updates active attention focus areas, energy states, and momentum indicators.
- `bb7_pause_session` - Pauses the active session with an optional reason.
- `bb7_resume_session` - Resumes a paused session by its identifier.
- `bb7_list_sessions` - Lists all sessions with optional status filters.
- `bb7_get_session_summary` - Retrieves a detailed summary of a specific session.
- `bb7_get_session_insights` - Compiles comprehensive insights and event summaries for a session.
- `bb7_cross_session_analysis` - Identifies patterns and trends across multiple historical sessions.
- `bb7_session_recommendations` - Generates recommendations and proven patterns from similar past sessions.
- `bb7_learned_patterns` - Retrieves learned patterns compiled across sessions.
- `bb7_session_intelligence` - Retrieves general session intelligence and heuristics metadata.
- `bb7_link_memory_to_session` - Links a specific memory key to the active session context.
- `bb7_auto_memory_stats` - Returns statistics on automatically captured memories in the current session.


`tools/thought_journal_tool.py` (chronological reasoning/decision tracking)
- `bb7_journal_record_thought` - Records thoughts, insights, hypotheses, observations, or questions with confidence scores and links to file/memory contexts.
- `bb7_journal_capture_decision` - Captures critical architectural and implementation decisions, detailing rationale, alternatives considered, constraints, risks, and success criteria.
- `bb7_journal_add_outcome` - Appends retrospective outcomes and validation status to previously recorded decisions or thoughts.
- `bb7_journal_search` - Performs full-text BM25-ranked search across journal entries, optionally filtering by entry type.
- `bb7_journal_get_decision_trail` - Reconstructs chronological narrative trails of decisions related to a specific topic over a look-back window.
- `bb7_journal_surface_relevant` - Automatically surfaces contextually relevant journal entries, with recency-biased relevance ranking.
- `bb7_journal_detect_conflicts` - Scans decisions on similar topics to flag potential contradictions (affirming vs. negating intent).
- `bb7_journal_generate_retrospective` - Compiles entries and decisions from a specified time window into a structured retro markdown document.
- `bb7_journal_get_reasoning_chain` - Follows links and semantic similarities to trace the reasoning chain leading up to a specific decision.
- `bb7_journal_stats` - Evaluates total entry counts, tag frequencies, decision quality ratios, and recent activities.
- `bb7_journal_linked_entries` - Performs reverse key lookup to list all journal entries that reference a specific memory key.


### 1.4 Context and Meta Routing

`tools/project_context_tool.py`
- `bb7_analyze_project_structure` - Analyzes and summarizes the directory tree, detected technologies, key files, and project type.
- `bb7_get_project_dependencies` - Extracts and maps software dependencies from requirement files and configs.
- `bb7_get_recent_changes` - Summarizes git history and modified files over a specified time window.
- `bb7_get_code_metrics` - Evaluates codebase metrics including line volume, class/function counts, and comment ratios.


`tools/auto_tool_module.py`
- `bb7_analyze_workflow_patterns` - Analyzes workflow history and metadata to detect productivity, efficiency, and optimization opportunities.
- `bb7_performance_optimization` - Collects performance baseline metrics and initiates optimization experiments to address bottlenecks.
- `bb7_intelligent_automation` - Scans workspace activities and predicts upcoming tasks, suggesting automated workflows to streamline operations.
- `bb7_cognitive_optimization` - Tracks focus durations, peak hours, and cognitive load to suggest personalized creativity and focus enhancement strategies.
- `bb7_optimization_results` - Retrieves baseline-vs-optimized comparison metrics for active and completed experiments.
- `bb7_adaptive_learning` - Adjusts system behavior dynamically based on user interaction frequency, retention rates, and preferences.
- `bb7_workspace_context_loader` - Evaluates the directory context, active/paused sessions, and recent memory keys to restore session continuity.
- `bb7_show_available_capabilities` - Displays tool count statistics and lists active tools grouped by categories.
- `bb7_auto_session_resume` - Inspects session directory states and recommends the most relevant active/paused session to resume.
- `bb7_intelligent_tool_guide` - Evaluates user queries and intent signals to recommend an optimal sequence of BB7 tools.


`tools/meta_intelligence_engine.py` (facade interface)
- `bb7_code_consciousness` - Combines static code analysis, project structure, memory context, and Lisan al-Gaib session recall to synthesize architectural intent and module design roles.
- `bb7_context_weaver` - Aggregates active sessions, recent memory keys, git changes, and semantic context recall to synthesize a holistic state representation for continuity.
- `bb7_creative_problem_solver` - Decomposes complex challenges by querying Lisan intent mapping and memory retrieval, generating structured problem breakdowns.


`tools/openrouter_planner_tool.py` (OpenRouter planner facade)
- `bb7_planner_health` - Reports planner integration status, active config, local data paths, and run success/failure counts.
- `bb7_planner_template` - Generates a structured system prompt template for planning without calling the LLM API.
- `bb7_planner_plan` - Generates a detailed step-by-step plan using OpenRouter models, complete with rationale, risk notes, and fallbacks.


`tools/openrouter_agent_tool.py` (Multi-agent plane facade)
- `bb7_agent_health` - Returns cognitive plane integration status, active agent definitions, and execution run telemetry.
- `bb7_agent_list` - Lists available agent configurations, standard tools, and iteration limits.
- `bb7_agent_capabilities` - Details capabilities, limits, and tool selections for a specific agent type.
- `bb7_agent_nodes` - Lists active execution nodes registered in the multi-agent cognitive plane.
- `bb7_agent_messages` - Reads messages from the shared multi-agent channel message bus.
- `bb7_agent_handoff` - Writes execution handoffs for other agents to resume with context.
- `bb7_agent_call` - Queues a task for another agent on the plane (asynchronous).
- `bb7_agent_run` - Executes a closed-loop multi-step task, utilizing model reasoning and live MCP tool executions.
- `bb7_agent_status` - Reports status and IDs of active execution nodes.



### 1.5 Neural Substrate (Muad'Dib)

`muadib/muaddib.py` (backbone Q-table & tokenizer)
- `bb7_dt_observe` (Telemetry) - Record tool-call observations to update Q-values.
- `bb7_dt_q_scores` (Bonuses) - Return normalized Q-bonuses for routing candidates.
- `bb7_dt_encode` / `bb7_dt_encode_catalog` (Manifold projection) - Project tool sequences/catalogs into the 512-dim embedding manifold.
- `bb7_dt_status` (Metadata) - Retrieve health, vocab size, and active state of the neural substrate.
- `bb7_dt_advanced_features` (Exposed exoskeleton interface) - Query blended bridge modality features.
- `bb7_dt_save` (Persistence) - Manually persist vocabulary and neural checkpoints.

`muadib/advanced_bridge.py`
- Extract provenance-tagged features (trained_q, trained_cooccur, untrained_embed).

`muadib/neural_config.py`
- Configuration definitions (`NeuralNetConfig`, `SubstrateConfig`).

`muadib/aeron_neural_memory.py`
- Neural substrate tokenizer implementations.

## 2) Canonical Turbo Loop (Every Turn)

### 2.1 Sync

1. `bb7_exo_bootstrap`
2. `bb7_exo_list_tool_categories`
3. `bb7_exo_category_specific_tools`
4. `bb7_lisan_intend`
5. `bb7_exo_route` or `bb7_exo_plan`

### 2.2 Context Resurrection

1. `bb7_lisan_recall`
2. `bb7_workspace_context_loader`
3. `bb7_auto_session_resume`

### 2.3 Execute

Pick only required surfaces for the task.
- code understanding: `bb7_analyze_code_complete`
- code risk check: `bb7_security_audit`
- command execution: `bb7_run_command`
- web fetch/research: web tools
- ui state/action: visual tools
- memory persistence: memory + interconnect tools
- neural substrate evaluation: `bb7_dt_advanced_features`

### 2.4 Verify

- Validate output quality with smallest-cost check.
- For multi-step plans, checkpoint with `bb7_exo_execute_step`.

### 2.5 Persist

1. `bb7_capture_insight`
2. `bb7_memory_store`
3. `bb7_memory_analyze_entry`
4. `bb7_link_memory_to_session`
5. `bb7_log_event`

### 2.6 Reflect

1. `bb7_exo_reflect`
2. `bb7_exo_kpi_report` for long chains

## 3) File Intelligence Doctrine

Primary approved file-reading pathways:
1. `bb7_analyze_code_complete(file_path=...)`
2. `bb7_lisan_recall(context=...)` for contextual memory reconstruction

Rule:
- Do not use shell text-dumping as the default comprehension path.
- Use direct file read utilities only when analyzer output is insufficient for a non-code artifact.

## 4) Journal Position

External journal-first operation is deprecated for this driver.

Current posture:
- Use `lisan` + session/memory persistence for reasoning continuity.
- If legacy journal endpoints exist, treat them as compatibility-only, not primary flow.
- If exoskeleton catalog still lists `bb7_journal_*`, verify runtime-callability before routing to them.

## 5) Anti-Collapse Guardrails

1. Do not collapse back into "just a coding agent" behavior.
2. Do not tool-spam when uncertainty is already resolved.
3. Do not run broad exploratory chains without an explicit question.
4. Do not skip reflect/persist after non-trivial execution.
5. Do not promise shell/control access beyond exposed runtime boundaries.

## 6) Tool Budget Governor

- Mode A (`0-2` calls): think/scope/decide.
- Mode B (`3-6` calls): normal route and execute.
- Mode C (`7-12` calls): complex branch with verification.
- Mode D (`13+` calls): require explicit reason and checkpointing.

Budget-break is allowed only for:
- high-risk operations,
- hard verification asks,
- irreversible or expensive actions.

## 7) Success Criteria Per Turn

A turn is complete only when:
1. Objective moved.
2. Driver loop followed.
3. Budget respected or justified.
4. Durable state captured.
5. Next action is clear.

---

Version note (2026-04-08):
- Rewritten against actual module implementations listed above.
- Locks autonomy posture around exoskeleton + lisan control plane.
- Removes dependence on external journal-first workflow.
