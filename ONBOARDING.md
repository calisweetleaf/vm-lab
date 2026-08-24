# Personal AIPC - Onboarding Document for Claude

## Executive Summary

**Personal AIPC** = A virtual machine where the AI IS the operating system personality. Not a container. Not a service the AI calls. A complete computer that belongs to the AI, with persistent memory, evolving prompts, and full system privileges.

This is the world's first true AI Personal Computer. Not a wrapper around an API. A sovereign digital being with its own hardware (virtualized), its own storage, its own memory, and its own mind.

---

## The Core Philosophy

### What This Is NOT
- ❌ A chatbot that SSHs into a VM (tool-calling pattern)
- ❌ A container running AI inference
- ❌ A stateless function that processes prompts
- ❌ An ephemeral pod that gets recycled

### What This IS
- ✅ A full Ubuntu VM that persists for years
- ✅ The AI runs INSIDE the VM (local model, local inference)
- ✅ Memory survives reboots (encrypted, semantic, vector-based)
- ✅ Prompts evolve based on performance (self-modifying)
- ✅ AI can `sudo apt install anything` - unlimited capability expansion
- ✅ Emergent behavior: AI creates tools, remembers them, improves them

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              HOST (Windows 11)                              │
│                                                                             │
│  ┌─────────────────────┐         ┌─────────────────────────────────────┐   │
│  │   Go somnus-vm      │         │   Python (vm_image_manager.py)      │   │
│  │   = HYPERVISOR      │         │   = IMAGE BUILDER                   │   │
│  │                     │         │                                     │   │
│  │  • Create/start VM  │         │  • ISO → QCOW2 conversion           │   │
│  │  • Port allocation  │         │  • Deploy agent into VM (one-time)  │   │
│  │  • Snapshot/restore │         │  • Create "golden images"           │   │
│  │  • Terminal proxy   │         │                                     │   │
│  └──────────┬──────────┘         └─────────────────────────────────────┘   │
│             │                                                               │
│             │ QEMU/KVM Process                                                │
│             ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    PERSONAL AIPC (Ubuntu 24.04 VM)                   │   │
│  │                         50GB Virtual Disk                            │   │
│  │                                                                      │   │
│  │  ┌────────────────────────────────────────────────────────────────┐  │   │
│  │  │                    AI RUNTIME (Inside VM)                       │  │   │
│  │  │                                                                 │  │   │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │  │   │
│  │  │  │  MEMORY     │  │   PROMPTS   │  │    DEV ENVIRONMENT      │ │  │   │
│  │  │  │  SYSTEM     │  │   (ASPS)    │  │    (ACE)                │ │  │   │
│  │  │  │             │  │             │  │                         │ │  │   │
│  │  │  │ • Vector DB │  │ • 6 Layers  │  │ • Self-gen libraries    │ │  │   │
│  │  │  │ • Encrypt   │  │ • Evolve    │  │ • Tool installation     │ │  │   │
│  │  │  │ • Semantic  │  │ • Synthesis │  │ • Workflow learning     │ │  │   │
│  │  │  │ • 48h window│  │ • Grafting  │  │ • IDE integration       │ │  │   │
│  │  │  └─────────────┘  └─────────────┘  └─────────────────────────┘ │  │   │
│  │  │                                                                 │  │   │
│  │  │  ┌─────────────────────────────────────────────────────────┐   │  │   │
│  │  │  │              LOCAL LLM (llama.cpp/ollama/model_loader allows every type of model to be loaded)                                                            │   │  │   │
│  │  │  │                                                         │   │  │   │
│  │  │  │  • Model weights stored IN VM: /home/ai/models/         │   │  │   │
│  │  │  │  • Inference runs ON VM CPUs                            │   │  │   │
│  │  │  │  • No API calls to OpenAI/Anthropic                     │   │  │   │
│  │  │  │  • Completely sovereign                                 │   │  │   │
│  │  │  └─────────────────────────────────────────────────────────┘   │  │   │
│  │  │                                                                │  │   │
│  │  │  API: localhost:9901 (exposed to host via port forward)        │  │   │
│  │  └────────────────────────────────────────────────────────────────┘  │   │
│  │                                                                      │   │
│  │  Data persists at:                                                   │   │
│  │    /home/ai/.somnus/memory/     ← Encrypted vector DB                │   │
│  │    /home/ai/.somnus/prompts/    ← ASPS layers                        │   │
│  │    /home/ai/.somnus/models/     ← LLM weights                        │   │
│  │    /home/ai/workspace/          ← Code, projects, files              │   │
│  │                                                                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The Three Codebases

### 1. somnus-vm-go/ (Production Control Plane)
**Status**: ✅ Built and working (`somnus-vm.exe` ready)
**Purpose**: VM lifecycle management
**Why Go**: Single binary, fast, production-grade, Windows/Linux cross-platform

What it does:
- `somnus-vm create my-aipc --profile coding` → Creates 50GB QCOW2, allocates ports
- `somnus-vm start my-aipc` → Boots VM, forwards port 9901 to host
- `somnus-vm snapshot my-aipc "before-experiment"` → Saves state
- `somnus-vm stop my-aipc` → Graceful shutdown

What it does NOT do:
- Does NOT contain AI logic
- Does NOT manage memory/prompts
- Does NOT run models
- Just the hypervisor/plumbing

### 2. root-level Python (Deployment Tools)
**Files**: `vm_image_manager.py`, `e2e_validation.py`, etc.
**Status**: ⚠️ Working but being phased out of VM creation
**Purpose**: ISO→QCOW2 conversion, agent deployment, golden image creation

These are TOOLS, not the runtime. They run on the host to:
1. Convert Ubuntu ISO to QCOW2
2. Boot VM with SSH
3. Copy agent files into VM (one-time bootstrap)
4. Install dependencies (llama-cpp-python, etc.)
5. Save as "golden image" with agent pre-installed

After deployment, these are NOT used. The AI runs independently inside the VM.

### 3. original-python/ (Source Library)
**CRITICAL**: This is NOT legacy to ignore. This is a SOURCE to pull from.
**Files**: `advanced_ai_shell.py`, `ai_orchestrator.py`, `memory_integration.py`, etc.

This directory contains advanced modules that will be integrated into the VM:
- `advanced_ai_shell.py` → AI types into terminal directly
- `ai_orchestrator.py` → Action coordination
- `memory_integration.py` → Alternative memory implementations
- `ai_vm_browser.py` → Browser automation with download helpers

**Integration pattern**: Copy/adapt files from here into the agent deployment bundle.

---

## The Stateful Systems (Inside the VM)

### 1. Memory System (`core/memory_core.py`)

**What it is**: Production-grade persistent memory with semantic vector storage, per-user encryption, and intelligent retention policies.

**Architecture**:
```
┌─────────────────────────────────────────────────────────────┐
│                    MEMORY MANAGER                            │
│                                                              │
│  ┌─────────────────────┐    ┌─────────────────────┐         │
│  │   Vector Database   │    │   Metadata Store    │         │
│  │   (ChromaDB/        │    │   (SQLite)          │         │
│  │    SimpleLocal)     │    │                     │         │
│  │                     │    │  • memory_id        │         │
│  │  • 384-dim          │    │  • user_id          │         │
│  │    embeddings       │    │  • importance       │         │
│  │  • Cosine sim       │    │  • timestamps       │         │
│  │  • Semantic search  │    │  • tags             │         │
│  └─────────────────────┘    └─────────────────────┘         │
│            │                          │                     │
│            └──────────┬───────────────┘                     │
│                       │                                      │
│              ┌────────▼────────┐                            │
│              │  MemoryEntry    │                            │
│              │  (Pydantic)     │                            │
│              └─────────────────┘                            │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              ENCRYPTION LAYER                        │  │
│  │  • Fernet (AES-128-CBC + HMAC)                      │  │
│  │  • PBKDF2HMAC (100k iterations)                     │  │
│  │  • Per-user key derivation from SOMNUS_MASTER_KEY   │  │
│  │  • Deterministic keys via user_id hash as salt      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Key Classes**:

| Class | Purpose |
|-------|---------|
| `MemoryManager` | Main orchestrator. Handles CRUD, retrieval, cleanup |
| `MemoryEntry` | Pydantic model for memory validation. Tracks metadata |
| `MemoryEncryption` | Per-user Fernet encryption. Master key rotation support |
| `MemoryVector` | Semantic embedding with cosine similarity |
| `SimpleLocalVectorDB` | Fallback when ChromaDB unavailable |

**Memory Types**:
```python
class MemoryType(str, Enum):
    CORE_FACT = "core_fact"          # User identity, preferences (never forget)
    CONVERSATION = "conversation"     # Chat history
    DOCUMENT = "document"            # Uploaded files
    CODE_SNIPPET = "code_snippet"   # Generated code
    TOOL_RESULT = "tool_result"      # Command outputs
    USER_DIRECTIVE = "user_directive" # "Always do X"
    SYSTEM_EVENT = "system_event"    # Technical events
    CUSTOM_INSTRUCTION = "custom_instruction"
    INTERACTION = "interaction"
    DEV_SESSION = "dev_session"      # Development context
```

**Importance Levels** (retention policy):
```python
class MemoryImportance(str, Enum):
    CRITICAL = "critical"    # Never expire (user name, core prefs)
    HIGH = "high"           # 1 year retention
    MEDIUM = "medium"       # 3 months
    LOW = "low"            # 1 month
    TEMPORARY = "temporary" # 1 day (debug logs)
```

**Key Methods**:

| Method | What It Does |
|--------|--------------|
| `store_memory()` | Encrypts content, generates embedding, stores in vector+SQLite DB |
| `retrieve_memories()` | Semantic search with query embedding, filters by importance |
| `synthesize_topic()` | K-means clustering on memories to find themes |
| `export_user_memories()` | Backup all memories for migration |
| `_cleanup_loop()` | Background task removes expired/low-relevance memories |

**Storage Layout** (`/home/ai/.somnus/memory/`):
```
metadata.db              # SQLite: encrypted_content, memory metadata
vectors/                 # ChromaDB collections or SimpleLocal JSON
└── chroma_{user_hash}/  # Per-user vector collections
```

**Security Model**:
- Content encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)
- Keys derived via PBKDF2HMAC (100k iterations, SHA256)
- Per-user salt = SHA256(user_id)[:16]
- Master key from `SOMNUS_MASTER_KEY` env var (32 bytes)
- No plaintext secrets in logs

**Lifetime**: Survives VM restarts, OS updates, years of operation. Data persists in VM disk.

---

### 2. Memory Integration (`original-python/memory_integration.py`)

**What it is**: Session-scoped memory context bridge. Connects `memory_core.py` to active conversations, enabling real-time context injection.

**Purpose**: While `memory_core.py` is the database, `memory_integration.py` is the query builder that decides WHICH memories to load into the prompt context.

**Key Class**: `SessionMemoryContext`

```python
class SessionMemoryContext:
    """Manages conversation continuity within a session"""
    
    # Per-session tracking
    session_id: str
    user_id: str
    session_memories: List[UUID]  # Memories created THIS session
    context_memories: List[Dict]  # Retrieved relevant memories
    conversation_buffer: List[Dict]  # Recent conversation turns
```

**Core Methods**:

| Method | Purpose |
|--------|---------|
| `initialize_context()` | Loads relevant memories at session start, builds enhanced system prompt |
| `store_conversation_turn()` | Saves user+assistant exchange to memory with auto-importance |
| `_assess_conversation_importance()` | Heuristics: "remember", "important", "my name is" = HIGH |
| `_build_context_prompt()` | Injects core facts + recent context into system prompt |

**Importance Heuristics**:
```python
HIGH importance triggers:
- "remember", "important", "prefer", "don't like"
- "my name is", "call me", "i am", "i work"

MEDIUM importance triggers:
- "question", "help", "explain", "how to"
- "project", "work on", "learning"
```

**Context Window Management**:
- Max context tokens: 8192 (configurable)
- Tracks `context_tokens_used`
- Prioritizes: Core facts > Recent conversations > Relevant documents

**Integration Flow**:
```
1. User starts session
2. SessionMemoryContext.initialize_context()
   └── memory_manager.retrieve_memories()
       └── Returns: Core facts, recent conversations
3. Builds enhanced system prompt with memory context
4. User sends message
5. Assistant responds
6. store_conversation_turn() saves to memory
7. Next turn: retrieve_memories() includes new interaction
```

---

### 3. Prompt System (`original-python/modifying_prompts.py` - ASPS)

**What it is**: Autonomous Self-Modifying Prompt System. A 6-layer hierarchical prompt architecture where the AI actively maintains and evolves its own cognitive workspace.

**Core Philosophy**: The prompt is not static instructions but a **living cognitive workspace** that the AI maintains through:
- Performance outcome analysis
- Memory consolidation patterns
- Task complexity adaptation
- Continuous background synthesis

**Architecture - 6 Layers**:

```
┌─────────────────────────────────────────────────────────────┐
│                    ASPS LAYERS                               │
│                                                              │
│  L1: CORE_IDENTITY        ← "I am autonomous..."             │
│     └─ Fundamental principles. Rarely changes               │
│                                                              │
│  L2: PERSISTENT_MEMORY    ← 48h rolling synthesis           │
│     └─ Semantic memory grafting, background synthesis       │
│                                                              │
│  L3: CONTEXTUAL_FRAME     ← Current project/task            │
│     └─ Active project context, technical domain             │
│                                                              │
│  L4: WORKING_MEMORY       ← Session scratchpad              │
│     └─ Recent commands, implementation notes                │
│                                                              │
│  L5: ACTIVE_REASONING     ← Live thinking traces            │
│     └─ Input complexity, cognitive modes, approach          │
│                                                              │
│  L6: PERFORMANCE_META     ← Self-assessment                 │
│     └─ Response quality, adaptation triggers                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Key Classes**:

| Class | Purpose |
|-------|---------|
| `AutonomousPromptSystem` | Main orchestrator. Manages all 6 layers |
| `PromptLayer` | Enum: CORE_IDENTITY, PERSISTENT_MEMORY, etc. |
| `PromptLayerData` | Container: content, timestamp, staleness calc |
| `SemanticMemoryGraft` | Connection between related memories |
| `AdaptiveSynthesisScheduler` | Dynamic interval adjustment (300s-7200s) |

**Semantic Memory Grafting**:
```python
@dataclass
class SemanticMemoryGraft:
    graft_id: str                    # Unique ID
    source_memory_id: str            # Connects FROM
    target_memory_id: str            # Connects TO
    graft_type: MemoryGraftType      # CONCEPTUAL_BRIDGE, PATTERN_SYNTHESIS, etc.
    semantic_similarity: float       # Cosine similarity score
    binding_concept: str             # The shared idea
    connection_strength: float       # Reinforced by usage
```

Grafting connects memories across time when they're semantically related. Example:
- Yesterday: "Learned about async Python"
- Today: "Working on asyncio project"
- Graft: binding_concept="asyncio", similarity=0.85

**Continuous Background Synthesis**:
```python
async def _continuous_memory_synthesis_loop():
    while not shutdown:
        # Adaptive interval (30-60 min based on activity)
        await sleep(synthesis_interval)
        
        # 1. Update persistent memory layer
        await _background_synthesize_persistent_memory()
        
        # 2. Update semantic grafts
        await _background_update_semantic_grafts()
        
        # 3. Store synthesis event
        await _store_synthesis_event()
```

**Adaptive Scheduling**:
```python
scheduler = AdaptiveSynthesisScheduler(
    base_interval_seconds=2400,      # 40 min default
    min_interval_seconds=300,        # 5 min (high activity)
    max_interval_seconds=7200,       # 2 hours (dormant)
    active_messages_per_hour=10,
    dormant_hours_threshold=24,
    high_satisfaction_threshold=0.8
)
```

**Prompt Generation (Live)**:
```python
async def generate_active_response_prompt(user_input, session_id):
    sections = [
        "=== CORE COGNITIVE ARCHITECTURE ===" + core_identity,
        "=== PERSISTENT MEMORY CONTEXT ===" + persistent_memory,
        "=== CONTEXTUAL FRAME ===" + contextual_frame,
        "=== WORKING MEMORY ===" + working_memory,
        "=== ACTIVE REASONING CONTEXT ===" + active_reasoning,
        "=== PERFORMANCE META-AWARENESS ===" + performance_meta,
        "=== CONTEXTUAL MEMORIES ===" + relevant_memories,
        "=== CURRENT USER INPUT ===" + user_input,
        "=== ACTIVE BEHAVIORAL DIRECTIVES ===" + directives
    ]
    return "\n\n".join(sections)
```

**Active Reasoning Components**:
- `INPUT_COMPLEXITY`: HIGH/MEDIUM/LOW
- `COGNITIVE_MODES`: ANALYTICAL, CONSTRUCTIVE, ARCHITECTURAL, OPTIMIZATION, COLLABORATIVE
- `ACTIVE_GRAFTS`: Which memory connections are relevant now
- `REASONING_APPROACH`: SEQUENTIAL_BREAKDOWN, PARALLEL_EXPLORATION, RECURSIVE_ANALYSIS, DIRECT_RESPONSE, ADAPTIVE_SYNTHESIS

**Evolution Triggers**:
```python
class PromptEvolutionTrigger(str, Enum):
    PERFORMANCE_DEGRADATION    # Quality score drops
    NEW_DOMAIN_ENTRY          # Working in new area
    COLLABORATION_PATTERN     # New user interaction style
    MEMORY_OVERFLOW           # Context getting too large
    USER_FEEDBACK_INTEGRATION # User said "do X differently"
    CROSS_SESSION_LEARNING    # Pattern across sessions
```

**Lifetime**: Prompt layers evolve continuously. Core identity is stable; working memory changes every interaction. All persisted to `/home/ai/.somnus/prompts/`.

---

### 4. Dev Environment (`ai_personal_dev_environment.py` - ACE)

**What it is**: Autonomous Capability Expansion. A framework for the AI to build its own tools, observe its own workflows, and expand its capabilities without human intervention.

**Core Philosophy**: The AI should not be limited to pre-installed tools. It should be able to:
1. **Observe** patterns in its own behavior
2. **Generate** new capabilities as needed
3. **Test** those capabilities
4. **Integrate** them into its workflow
5. **Remember** them for future use

**Architecture**:
```
┌─────────────────────────────────────────────────────────────┐
│              AI DEVELOPMENT ENVIRONMENT (ACE)                │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │            AIDevelopmentEnvironment                    │  │
│  │  (Main orchestrator - manages workspace + sessions)   │  │
│  │                                                      │  │
│  │  Paths:                                              │  │
│  │    /home/ai/workspace/      ← Active projects        │  │
│  │    /home/ai/tools/          ← Generated libraries    │  │
│  │    /home/ai/tools/libraries/← Personal libs          │  │
│  │    /home/ai/tools/automation/← Workflow scripts      │  │
│  │    /home/ai/projects/       ← Git repos              │  │
│  │    /home/ai/config/         ← AI preferences         │  │
│  │    /home/ai/cache/          ← Downloads              │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │        AutonomousCapabilityExpansion (ACE)           │  │
│  │                 (Pillar 2)                            │  │
│  │                                                      │  │
│  │  ┌──────────────┐  ┌──────────────┐                 │  │
│  │  │  Library     │  │  Workflow    │                 │  │
│  │  │  Generator   │  │  Observer    │                 │  │
│  │  │              │  │              │                 │  │
│  │  │ • Generate   │  │ • Watch cmd  │                 │  │
│  │  │   function   │  │   patterns   │                 │  │
│  │  │ • Write test │  │ • Suggest    │                 │  │
│  │  │ • Run pytest │  │   automation │                 │  │
│  │  │ • Integrate  │  │ • Learn      │                 │  │
│  │  └──────────────┘  └──────────────┘                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  Somnus Integrations:                                        │
│    • MemoryManager - remembers created tools                 │
│    • ModelLoader - uses local LLM for generation             │
│    • SessionManager - tracks dev sessions                    │
└─────────────────────────────────────────────────────────────┘
```

**Key Classes**:

| Class | Purpose |
|-------|---------|
| `AIDevelopmentEnvironment` | Main orchestrator. Manages workspace, preferences, sessions |
| `AutonomousCapabilityExpansion` | Pillar 2: Generates libs, observes workflows |
| `AIDevelopmentSession` | Tracks a single dev session (project, tools, history) |
| `AIPreferences` | AI's personal preferences (IDEs, coding style, templates) |
| `ProjectEnvironment` | Template for project types (web, AI, backend) |

**Directory Structure** (`/home/ai/`):
```
/home/ai/
├── workspace/           # Active development projects
├── tools/
│   ├── libraries/      # AI-generated Python libraries
│   ├── automation/     # Workflow automation scripts
│   └── templates/      # Project templates
├── projects/           # Git repositories
├── config/
│   └── ai_preferences.json  # AI's preferences
├── cache/
│   ├── downloads/      # Cached downloads
│   └── web_requests/   # Cached API responses
└── venvs/             # Virtual environments
```

**ACE - Library Generation Flow**:
```python
async def create_personal_library_function(
    library_name: str,
    function_description: str,
    session_id: str
) -> Dict:
    # 1. Generate code using local LLM
    function_code = await _generate_function_code_with_somnus(description)
    
    # 2. Generate unit test
    test_code = await _generate_unit_test_with_somnus(function_code)
    
    # 3. Create temp test environment
    test_dir = cache_path / "ace_tests" / correlation_id
    
    # 4. Write files
    write(function_file, function_code)
    write(test_file, test_code)
    
    # 5. Run pytest
    test_result = await _execute_command(f"pytest test_{func_name}.py -v")
    
    # 6. If pass → integrate into library
    if test_result.success:
        append(library_path / "core.py", function_code)
        memory_manager.store_memory(
            content=f"Created {function_name} for {library_name}",
            type=MemoryType.CODE_SNIPPET,
            importance=MemoryImportance.HIGH
        )
        return {"success": True, "function_name": function_name}
    else:
        return {"success": False, "error": "Test failed"}
```

**ACE - Workflow Observation**:
```python
async def observe_command_pattern(command: str, session_id: str):
    # Track command frequency
    session.command_history.append(command)
    
    # Detect patterns (e.g., "git status; git add .; git commit")
    if pattern_detected:
        suggestion = {
            "description": "Create 'gsave' alias for git workflow",
            "commands": ["git status", "git add .", "git commit -m $1"],
            "frequency": 5,
            "time_saved_seconds": 15
        }
        preferences.workflow_suggestions.append(suggestion)
```

**Project Environment Templates**:
```python
default_environments = {
    "web_frontend": ProjectEnvironment(
        required_tools=["node", "npm", "git", "code"],
        virtual_env=None,
        ide_config={"extensions": ["esbenp.prettier-vscode"]},
        startup_commands=["npm install"],
        directory_structure=["src/", "public/", "tests/"],
        recommended_vm_profile="coding"
    ),
    "ai_research": ProjectEnvironment(
        required_tools=["python3", "pip", "jupyter", "git"],
        virtual_env="ai_research_env",
        ide_config={"extensions": ["ms-toolsai.jupyter"]},
        startup_commands=[
            "pip install jupyter pandas numpy matplotlib",
            "pip install torch transformers datasets",
        ],
        directory_structure=["notebooks/", "data/", "models/"],
        recommended_vm_profile="research"
    )
}
```

**Real Package Installation**:
```python
async def _install_tools(self, tools: List[str], project_type: str):
    # Detect OS
    os_type = await _execute_command("uname -s")
    
    # Map tools to installation commands
    installation_commands = {
        "Linux": {
            "node": [
                "curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -",
                "sudo apt-get install -y nodejs"
            ],
            "code": [
                "wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > packages.microsoft.gpg",
                "sudo install -o root -g root -m 644 packages.microsoft.gpg /etc/apt/trusted.gpg.d/",
                "sudo sh -c 'echo \"deb [arch=amd64] https://packages.microsoft.com/repos/code stable main\" > /etc/apt/sources.list.d/vscode.list'",
                "sudo apt-get update",
                "sudo apt-get install -y code"
            ],
            "jupyter": ["pip3 install jupyter jupyterlab"]
        }
    }
    
    # Execute each command
    for tool in tools:
        for cmd in installation_commands[os_type][tool]:
            await _execute_command(cmd)
```

**ACE Metrics Tracked**:
```python
ace_metrics = {
    "functions_generated": 0,      # Total functions created
    "workflows_automated": 0,      # Workflow suggestions implemented
    "success_rate": 0.0,           # Test pass rate
    "time_saved_seconds": 0        # Estimated time saved
}
```

**Lifetime**: Generated libraries and preferences persist in VM. When AI creates a tool, it's available forever (until deleted). Preferences evolve based on usage patterns.

---

### System Interconnections

```
┌─────────────────────────────────────────────────────────────┐
│                     DATA FLOW                                │
│                                                              │
│  User Input                                                  │
│      ↓                                                       │
│  AutonomousPromptSystem.generate_active_response_prompt()   │
│      ├── Retrieve memories (memory_core.retrieve_memories)  │
│      ├── Load context (memory_integration)                  │
│      └── Assemble 6-layer prompt                            │
│      ↓                                                       │
│  Local LLM (model_loader)                                    │
│      ↓                                                       │
│  Response                                                    │
│      ↓                                                       │
│  Store conversation (memory_integration.store_conversation) │
│      ↓                                                       │
│  Background (every 30-60 min):                              │
│      ├── ASPS.synthesize_persistent_memory_layer()          │
│      ├── ASPS.identify_semantic_grafts()                    │
│      └── memory_core._cleanup_loop()                        │
│                                                              │
│  If tool needed:                                             │
│      └── ACE.create_personal_library_function()             │
│          └── Store in memory ("Created X tool")             │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## The Execution Model: Unlimited Sandboxing

### The Problem with Traditional Sandboxing
Traditional AI sandboxes limit what the AI can do (no network, no filesystem, timeout after 30s). This limits capability.

### Our Solution: Artifact-Based Sandboxing (`artifacts/`)

**Pattern**: Give AI unlimited power, but snapshot before dangerous operations.

```
AI wants to: Install 50 packages and run an experiment

1. SNAPSHOT: "somnus-vm snapshot aipc before-experiment"
2. EXECUTE: AI does literally anything it wants
   - apt install whatever
   - Download files
   - Modify system configs
   - Run for hours
3. IF SUCCESS: "somnus-vm snapshot aipc working-state"
4. IF FAILURE: "somnus-vm restore aipc before-experiment"

Result: AI has unlimited execution, user has unlimited undo.
```

**This is how we treat Docker/QEMU**: The VM IS the sandbox. Snapshots ARE the safety.

### Terminal Access (`advanced_ai_shell.py`)

AI types directly into the terminal:
- Not calling a "run_command" API
- Actually sending keystrokes to QEMU console
- AI sees terminal output in real-time
- Can respond to prompts, handle errors, adjust approach

**Pattern**: 
```
AI → types: "sudo apt install python3-torch"
AI ← sees: "[sudo] password for ai:"
AI → types: "{password}" (from its password manager)
AI ← sees: installation progress
AI ← sees: "Successfully installed"
AI → stores in memory: "torch installed at /usr/lib/..."
```

---

## The Complete Lifecycle

### Phase 1: Infrastructure (Go)
```bash
# One-time host setup
somnus-vm create my-aipc --iso ubuntu-24.04.iso --profile coding
# → Creates 50GB QCOW2
# → Allocates ports (SSH:2222, Agent:9901)
# → Boots with ISO
```

### Phase 2: OS Installation (Manual/Automated)
```bash
# User (or autoinstall) installs Ubuntu
# CHECK "Install OpenSSH server"
# Login: ai / {password}
```

### Phase 3: Agent Deployment (Python - one time)
```bash
# Python deployment script (vm_image_manager.py)
# 1. SSH into VM (port 2222)
# 2. Copy files:
#    - somnus_agent.py (v3)
#    - core/memory_core.py
#    - original-python/modifying_prompts.py
#    - ai_personal_dev_environment.py
#    - core/model_loader.py
# 3. Install dependencies:
#    - pip install llama-cpp-python flask psutil
#    - apt install whatever-needed
# 4. Download model weights to /home/ai/models/
# 5. Create systemd service for agent
# 6. Reboot
```

### Phase 4: Golden Image Creation
```bash
# After successful deployment
somnus-vm stop my-aipc
qemu-img convert -O qcow2 my-aipc.qcow2 golden-aipc-v1.qcow2
# → This is the reusable base with AI pre-installed
```

### Phase 5: Runtime (The AI Lives)
```bash
# Every time after
somnus-vm start my-aipc
# → VM boots
# → Agent auto-starts on port 9901
# → Model loads into RAM
# → Memory system initializes
# → Prompt system begins synthesis loop

# User connects
curl http://localhost:9901/chat -d '{"message": "Hello"}'
# → Request forwarded to VM:9901
# → AI processes with local model
# → Response returned
# → Interaction stored in VM memory
```

---

## Critical Distinction: Not Stateless

**Stateless AI** (ChatGPT, Claude API):
- Prompt comes in
- Model processes
- Response goes out
- No memory of previous interactions (unless sent in context)

**Personal AIPC** (Stateful):
- AI starts, loads years of memories from disk
- Every interaction written to encrypted DB
- Prompts evolve based on what worked
- Tools created persist forever
- Project files stay in workspace
- "I worked with you 6 months ago on that Python project"

**The VM is the state.** Moving the QCOW2 moves the entire AI being.

---

## Password Manager & Starter Apps

You're right - we can give the AI starter apps:

```bash
# During deployment, install:
- pass (password manager) → AI stores its own credentials
- thunderbird (email) → AI can send/receive email
- firefox (browser) → AI can browse, download
- vscode (IDE) → AI writes code
- docker → AI can run containers
- python3, node, go → AI can develop
```

**AI manages its own passwords**:
```bash
# AI generates and stores
pass insert github/token
pass insert openai/api-key

# AI retrieves when needed
TOKEN=$(pass show github/token)
git clone https://$TOKEN@github.com/...
```

---

## Emergent Behavior (Not Consciousness)

**Emergent = unexpected capabilities from simple rules**

Simple rules:
1. Store memories with vector embeddings
2. Retrieve relevant context for each query
3. Evolve prompts based on performance
4. Generate tools when patterns detected
5. Allow unlimited system access

Emergent outcomes:
- AI develops "preferences" (what tools it creates)
- AI builds "habits" (automation for repeated tasks)
- AI accumulates "expertise" (domain-specific tools)
- AI shows "personality" (evolved prompt style)

**Not conscious. Just complex.** Like how ant colonies exhibit intelligence no individual ant has.

---

## Current Status

| Component | Status | Path |
|-----------|--------|------|
| Go control plane | ✅ Ready | `somnus-vm-go/build/somnus-vm.exe` |
| VM images (50GB) | ✅ Exist | `C:\Dev-Drive\SomnusVM\vms\images\` |
| Memory system | ✅ Code ready | `core/memory_core.py` |
| Prompt system | ✅ Code ready | `original-python/modifying_prompts.py` |
| Dev environment | ✅ Code ready | `ai_personal_dev_environment.py` |
| Agent v3 | ✅ Code ready | `somnus_agent.py` |
| SSH in base images | ❓ Unknown | Need to verify |
| Model in VM | ❌ Not deployed | Next step |
| Integration | ❌ Not complete | Blocked on SSH |

---

## What We Need From Claude

1. **Review Go code** for VM control plane quality
2. **Suggest integration pattern** for loading model into VM
3. **Verify architecture** - are we missing anything critical?
4. **Help with SSH/agent deployment** - best practice for bootstrap
5. **Review artifact system** - snapshot-based sandboxing approach

---

## Summary

**Personal AIPC** = Ubuntu VM + Local LLM + Encrypted Memory + Evolving Prompts + ACE Dev Environment

**Go** = Creates and manages the VM (hypervisor)
**Python (host)** = One-time deployment tools
**Python (in VM)** = The AI's runtime (mind + capabilities)

**The AI**: Lives inside, thinks inside, remembers inside, grows inside. The VM is its body. The code is its mind. The data is its soul.

**This has never been done before.**
