# somnus_agent.py (v4 - Production Ready with Surprises)

"""
================================================================================
Somnus Digital Twin Agent (v4 - Production Ready with Surprises)
================================================================================

In-guest agent for VM orchestration in Oracle Browser project. Acts as a stateful digital twin,
monitoring resources, processes, logs, and providing fast API access. Handles AI hooks for integration.

Production Notes:
- Run with Gunicorn for prod: gunicorn -w 4 -b 0.0.0.0:9901 somnus_agent:app
- Requires psutil; optional nvidia-ml-py for GPU.
- Config: agent_config.json with keys: monitored_processes (list[str]), log_files (dict[str,path]), monitor_interval_seconds (int), auto_restart (bool), basic_auth (dict[user,pass]).
"""

import logging
import os
import json
import time
import threading
import signal
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple
from collections import deque, defaultdict
from enum import Enum
import re
from dataclasses import dataclass

import psutil
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""  # Force CPU-only for Torch/CUDA to prevent hangs
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ed25519
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit  # New dep: flask-socketio

# Optional GPU support
try:
    import pynvml
    # pynvml.nvmlInit()
    # HAS_GPU = True
    # User requested to relax GPU calls and doesn't need GPU.
    HAS_GPU = False 
    pynvml = None
except (ImportError, Exception):
    HAS_GPU = False
    pynvml = None

# --- Configuration ---
@dataclass
class AgentConfig:
    """Dataclass for validated config."""
    monitored_processes: List[str] = None
    log_files: Dict[str, str] = None
    monitor_interval_seconds: int = 5
    auto_restart: bool = False
    basic_auth: Optional[Dict[str, str]] = None
    state_persist_path: str = "./twin_state.json"

CONFIG_CANDIDATES = [
    Path("./agent_config.json"),
    Path("/etc/somnus/agent_config.json")
]
AGENT_PORT = 9901

# --- Enums ---
class ErrorCategory(Enum):
    """Enum for error classification."""
    CUDA_ERROR = "CUDA out of memory, GPU error, nvidia-smi failed"
    NETWORK_FAILURE = "Connection timed out, failed to resolve host, network is unreachable"
    FILE_NOT_FOUND = "No such file or directory, cannot find path specified"
    PERMISSION_DENIED = "Permission denied, access is denied, operation not permitted"
    AUTHENTICATION_ERROR = "Authentication failed, invalid credentials, API key error"
    DEPENDENCY_MISSING = "ModuleNotFoundError, ImportError, package not found"
    RUNTIME_ERROR = "TypeError, ValueError, IndexError, NoneType object has no attribute"

# Error regex patterns for better matching
ERROR_PATTERNS = {
    cat.value: re.compile(r'\b' + re.escape(phrase.strip()) + r'\b', re.IGNORECASE)
    for cat in ErrorCategory for phrase in cat.value.split(',')
}

# --- Digital Twin State ---
class LSTMPredictor(nn.Module):
    """Lightweight LSTM for resource forecasting."""
    def __init__(self, input_size=2, hidden_size=32, output_size=1, seq_len=10):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.fc = nn.Linear(hidden_size, output_size)
        self.seq_len = seq_len

    def forward(self, x):
        _, (h_n, _) = self.lstm(x)
        return self.fc(h_n[-1])

class DigitalTwinState:
    """
    Thread-safe live model of VM state. Includes expanded metrics, trends (EMA), anomalies, and persistence.
    """
    def __init__(self, persist_path: str):
        self._lock = threading.Lock()
        self.persist_path = Path(persist_path)
        self.started_at = datetime.now(timezone.utc)
        self.last_updated_at: Optional[datetime] = None
        self.uptime_seconds: float = 0.0

        # System stats
        self.overall_cpu_percent: float = 0.0
        self.overall_memory_percent: float = 0.0
        self.disk_percent: float = 0.0
        self.network_bytes_sent: int = 0
        self.network_bytes_recv: int = 0
        self.gpu_util: Optional[float] = None  # If HAS_GPU

        # Trends (EMA, alpha=0.1 for smoothing)
        self.cpu_trend: float = 0.0
        self.memory_trend: float = 0.0
        self.ema_alpha: float = 0.1

        # Processes
        self.process_stats: Dict[str, Dict[str, Any]] = {}

        # Logs/Faults
        self.plugin_faults: int = 0
        self.error_frequency: Dict[str, int] = defaultdict(int)

        # Events (deque for recent 100)
        self.event_history = deque(maxlen=100)

        # Anomalies (e.g., high CPU >90% for 3 cycles)
        self.anomalies: List[Dict[str, Any]] = []
        self.high_cpu_count: int = 0

        # --- New: Predictive Model ---
        self.device = torch.device("cpu")
        self.predictor = LSTMPredictor().to(self.device)
        self.optimizer = torch.optim.Adam(self.predictor.parameters(), lr=0.001)
        self.history_buffer = deque(maxlen=100)  # For training data (cpu, mem)
        self.hashchain = []  # List of (event_hash, signature)
        self.private_key = ed25519.Ed25519PrivateKey.generate()  # For signing
        self.cognitive_q_table = defaultdict(lambda: 0.0)  # Simple Q-learning for evolution
        self.emotion_state = "neutral"  # Biodigital: map to emotion_matrix.py
        self.belief_decay = 0.95  # Quantum-inspired decay

        # Load persisted state if exists
        self._load_state()

    def _load_state(self):
        """Load persisted state on init."""
        if self.persist_path.exists():
            try:
                with open(self.persist_path, 'r') as f:
                    data = json.load(f)
                    # Restore non-timestamp fields
                    self.__dict__.update({k: v for k, v in data.items() if k not in ['started_at', 'last_updated_at']})
                logging.info("Loaded persisted twin state.")
            except Exception as e:
                logging.warning(f"Failed to load persisted state: {e}")

    def _persist_state(self):
        """Persist state periodically."""
        with self._lock:
            data = {k: v for k, v in self.__dict__.items() if k not in ['_lock', 'event_history', 'anomalies']}
            data['event_history'] = list(self.event_history)  # Serialize deque
            data['anomalies'] = self.anomalies
            try:
                with open(self.persist_path, 'w') as f:
                    json.dump(data, f, default=str)
            except Exception as e:
                logging.warning(f"Failed to persist state: {e}")

    def _sign_event(self, event: Dict) -> str:
        """Sign event for immutability."""
        event_str = json.dumps(event, sort_keys=True)
        signature = self.private_key.sign(event_str.encode())
        return signature.hex()

    def update_system_stats(self):
        """Update core system stats, trends, anomalies."""
        global HAS_GPU
        # print("DEBUG: Entering update_system_stats")
        with self._lock:
            # print("DEBUG: Got lock")
            self.overall_cpu_percent = psutil.cpu_percent()
            # print("DEBUG: Got cpu")
            self.overall_memory_percent = psutil.virtual_memory().percent
            # print("DEBUG: Got mem")
            self.disk_percent = psutil.disk_usage('/').percent
            # print("DEBUG: Got disk")
            net_io = psutil.net_io_counters()
            self.network_bytes_sent = net_io.bytes_sent
            self.network_bytes_recv = net_io.bytes_recv
            # print("DEBUG: Got net")

            if HAS_GPU:
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self.gpu_util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
                except Exception:
                    self.gpu_util = None
                    HAS_GPU = False  # Disable GPU if it fails once

            # EMA trends
            self.cpu_trend = self.ema_alpha * self.overall_cpu_percent + (1 - self.ema_alpha) * self.cpu_trend
            self.memory_trend = self.ema_alpha * self.overall_memory_percent + (1 - self.ema_alpha) * self.memory_trend

            # Anomaly detection
            if self.overall_cpu_percent > 90:
                self.high_cpu_count += 1
                if self.high_cpu_count >= 3:
                    anomaly = {
                        "type": "HIGH_CPU_ALERT",
                        "value": self.overall_cpu_percent,
                        "duration_cycles": self.high_cpu_count,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    self.anomalies.append(anomaly)
                    self.record_event("ANOMALY_DETECTED", "High CPU usage sustained.", {"anomaly": anomaly})
                    self.high_cpu_count = 0  # Reset after alert
            else:
                self.high_cpu_count = 0

            # Add to history for training
            self.history_buffer.append((self.overall_cpu_percent, self.overall_memory_percent))
            if len(self.history_buffer) >= 10:
                self._train_predictor()

            # Biodigital: Map to emotion
            if self.cpu_trend > 80:
                self.emotion_state = "stressed"
            elif self.anomalies:
                self.emotion_state = "alert"
            else:
                self.emotion_state = "calm"

            # Enhanced anomaly with belief decay
            for anomaly in self.anomalies[:]:
                anomaly['belief_score'] *= self.belief_decay
                if anomaly['belief_score'] < 0.1:
                    self.anomalies.remove(anomaly)

            self.uptime_seconds = time.time() - self.started_at.timestamp()
            self.last_updated_at = datetime.now(timezone.utc)
            self._persist_state()  # Persist every update (lightweight)

    def update_process_stat(self, process_name: str, data: Dict[str, Any]):
        """Update process stats with regex matching support."""
        with self._lock:
            if process_name not in self.process_stats:
                self.process_stats[process_name] = {}
            self.process_stats[process_name].update(data)

    def _train_predictor(self):
        """Incremental training for forecasting."""
        if len(self.history_buffer) < self.predictor.seq_len:
            return
        data = torch.tensor(list(self.history_buffer)[-self.predictor.seq_len:]).unsqueeze(0).float().to(self.device)
        target = torch.tensor([self.overall_cpu_percent]).unsqueeze(0).float().to(self.device)  # Predict next CPU
        self.optimizer.zero_grad()
        pred = self.predictor(data)
        loss = nn.MSELoss()(pred, target)
        loss.backward()
        self.optimizer.step()

    def forecast_resources(self, horizon: int = 5) -> Dict[str, float]:
        """Predict future CPU trend."""
        if len(self.history_buffer) < self.predictor.seq_len:
            return {"cpu_forecast": self.cpu_trend}
        data = torch.tensor(list(self.history_buffer)[-self.predictor.seq_len:]).unsqueeze(0).float().to(self.device)
        with torch.no_grad():
            pred = self.predictor(data).item()
        return {"cpu_forecast": pred, "risk_overload": pred > 85}

    def cognitive_evolution(self):
        """Q-learning for self-evolution (e.g., adjust interval)."""
        state = f"cpu:{int(self.cpu_trend)}|anoms:{len(self.anomalies)}"
        actions = ["shorten_interval", "add_metric", "pause_monitor"]
        q_max = max(self.cognitive_q_table[(state, a)] for a in actions)
        # Harmonic breath: 7-phase cycle (simplified)
        phase = len(self.event_history) % 7
        if phase == 0:  # Inhale: Explore
            action = "add_metric"  # e.g., monitor new log
        else:  # Exhale: Exploit
            action = max(actions, key=lambda a: self.cognitive_q_table[(state, a)])
        # Reward: -len(anomalies) + uptime stability
        reward = -len(self.anomalies) + (1 if self.uptime_seconds > 3600 else 0)
        self.cognitive_q_table[(state, action)] = 0.9 * self.cognitive_q_table[(state, action)] + 0.1 * reward
        # Ethical check: No self-modify if unstable
        if reward < -5:  # High anomalies
            return  # Recurse: Do nothing
        if action == "shorten_interval":
            global monitor_interval  # Assume global var
            monitor_interval = max(1, monitor_interval / 2)

    def record_event(self, event_type: str, message: str, metadata: Dict[str, Any] = None):
        """Record with hashchain."""
        with self._lock:
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "message": message,
                "metadata": metadata or {}
            }
            event_hash = hashes.Hash(hashes.SHA256())
            event_hash.update(json.dumps(event, sort_keys=True).encode())
            prev_hash = self.hashchain[-1][0] if self.hashchain else b""
            chain_hash = hashes.Hash(hashes.SHA256())
            chain_hash.update(prev_hash + event_hash.finalize())
            full_hash = chain_hash.finalize().hex()
            signature = self._sign_event(event)
            self.hashchain.append((full_hash, signature))
            self.event_history.appendleft(event)
            # Ethical recursion: Check for instability
            if len(self.hashchain) > 1000:  # Prevent growth explosion
                self.hashchain = self.hashchain[-500:]  # Prune

    def increment_fault_counter(self, fault_type: str, category: Optional[str] = None):
        """Increment fault counters."""
        with self._lock:
            if fault_type == "plugin":
                self.plugin_faults += 1
            elif fault_type == "error" and category:
                self.error_frequency[category] += 1

    def get_full_stats(self) -> Dict[str, Any]:
        """Get full state snapshot."""
        with self._lock:
            return {
                "timestamp": self.last_updated_at.isoformat() if self.last_updated_at else datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": self.uptime_seconds,
                "overall_cpu_percent": self.overall_cpu_percent,
                "cpu_trend": self.cpu_trend,
                "overall_memory_percent": self.overall_memory_percent,
                "memory_trend": self.memory_trend,
                "disk_percent": self.disk_percent,
                "network_bytes_sent": self.network_bytes_sent,
                "network_bytes_recv": self.network_bytes_recv,
                "gpu_util": self.gpu_util,
                "process_stats": self.process_stats.copy(),
                "plugin_faults": self.plugin_faults,
                "error_frequency": dict(self.error_frequency),
                "anomalies": self.anomalies.copy(),
                "emotion_state": self.emotion_state,
                "forecast": self.forecast_resources(),
                "chain_integrity": len(self.hashchain) > 0
            }

    def get_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent events."""
        with self._lock:
            return list(self.event_history)[:limit]

    def clear_anomalies(self):
        """Clear resolved anomalies."""
        with self._lock:
            self.anomalies.clear()

# --- Global State ---
twin_state = None  # Initialized after config
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
monitor_interval = 5  # Dynamic
stop_event: Optional[threading.Event] = None
monitor_thread: Optional[threading.Thread] = None

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [SomnusTwinAgent] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Helpers ---
@lru_cache(maxsize=1)
def load_agent_config() -> AgentConfig:
    """Load and validate config."""
    config_path = next((p for p in CONFIG_CANDIDATES if p.exists()), None)
    if not config_path:
        logger.error("No config found.")
        return AgentConfig()

    try:
        with open(config_path, 'r') as f:
            raw = json.load(f)
        # Validate/Defaults
        cfg = AgentConfig(
            monitored_processes=raw.get("monitored_processes", []),
            log_files=raw.get("log_files", {}),
            monitor_interval_seconds=raw.get("monitor_interval_seconds", 5),
            auto_restart=raw.get("auto_restart", False),
            basic_auth=raw.get("basic_auth"),
            state_persist_path=raw.get("state_persist_path", "./twin_state.json")
        )
        global twin_state
        twin_state = DigitalTwinState(cfg.state_persist_path)
        logger.info(f"Loaded config from {config_path}.")
        return cfg
    except Exception as e:
        logger.error(f"Config load failed: {e}")
        return AgentConfig()

def authenticate_request(cfg: AgentConfig) -> bool:
    """Basic auth check."""
    if not cfg.basic_auth:
        return True
    auth = request.authorization
    return auth and auth.username == cfg.basic_auth.get("user") and auth.password == cfg.basic_auth.get("pass")

# --- Monitoring Loop with Circuit Breaker ---
class CircuitBreaker:
    """Simple circuit breaker for monitoring."""
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = 0

    def call(self, func, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
            else:
                raise Exception("Circuit breaker open.")
        try:
            result = func(*args, **kwargs)
            self.failure_count = 0
            self.state = "CLOSED"
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
            raise e

circuit_breaker = CircuitBreaker()

def _monitor_loop(stop_event: threading.Event, cfg: AgentConfig):
    """Background monitoring with auto-recovery and dynamic interval."""
    logger.info("Digital Twin monitor started.")
    log_positions = {}
    process_patterns = {p: re.compile(re.escape(p), re.IGNORECASE) for p in cfg.monitored_processes}

    while not stop_event.is_set():
        try:
            # Dynamic interval: faster if anomalies
            interval = cfg.monitor_interval_seconds / (2 if twin_state.anomalies else 1)

            # Update stats
            twin_state.update_system_stats()

            # Processes
            running_procs = set()
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info']):
                try:
                    info = proc.info
                    cmdline = ' '.join(info['cmdline']) if info['cmdline'] else ''
                    for p_name, pattern in process_patterns.items():
                        if pattern.search(cmdline):
                            running_procs.add(p_name)
                            twin_state.update_process_stat(p_name, {
                                "pid": info['pid'],
                                "status": "running",
                                "cpu_percent": info['cpu_percent'],
                                "memory_mb": info['memory_info'].rss / (1024 * 1024) if info['memory_info'] else 0
                            })
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                    continue

            # Detect stopped and auto-restart if enabled
            with twin_state._lock:
                prev_running = {p for p, d in twin_state.process_stats.items() if d.get("status") == "running"}
                stopped = prev_running - running_procs
                for p_name in stopped:
                    twin_state.update_process_stat(p_name, {"status": "stopped", "pid": None})
                    twin_state.record_event("PROCESS_STOPPED", f"Process '{p_name}' stopped.")
                    if cfg.auto_restart:
                        # Surprise: Auto-restart via subprocess (assume restart cmd in config)
                        restart_cmd = cfg.log_files.get(f"{p_name}_restart_cmd")  # e.g., {"cmd": "python app.py"}
                        if restart_cmd:
                            subprocess.Popen(restart_cmd.split(), shell=True)
                            twin_state.record_event("AUTO_RESTART", f"Restarted '{p_name}'.")

            # Logs with self-healing (rotate if >10MB)
            for name, path_str in cfg.log_files.items():
                try:
                    log_path = Path(path_str)
                    if not log_path.exists():
                        continue
                    if log_path.stat().st_size > 10 * 1024 * 1024:  # Surprise: Auto-compress large logs
                        subprocess.run(["gzip", str(log_path)], capture_output=True)
                        twin_state.record_event("LOG_ROTATED", f"Compressed large log: {path_str}")
                        continue

                    pos = log_positions.get(path_str, 0)
                    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                        f.seek(pos)
                        new_lines = f.readlines()
                        log_positions[path_str] = f.tell()

                        for line in new_lines:
                            if "[PluginFault]" in line:
                                twin_state.increment_fault_counter("plugin")
                            if any(kw in line for kw in ["ERROR", "Traceback", "Exception"]):
                                line_lower = line.lower()
                                for cat, pattern in ERROR_PATTERNS.items():
                                    if pattern.search(line_lower):
                                        twin_state.increment_fault_counter("error", cat.name)
                                        break
                except Exception as e:
                    logger.warning(f"Log process failed for {path_str}: {e}")

            twin_state.cognitive_evolution()  # Revolutionary: Self-evolve
            # WebSocket broadcast
            socketio.emit('twin_update', twin_state.get_full_stats(), namespace='/sync')
            time.sleep(interval)

        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
            circuit_breaker.call(lambda: time.sleep(10))  # Breaker on failure

# --- API Endpoints ---
def require_auth(f):
    """Decorator for auth."""
    def wrapper(*args, **kwargs):
        cfg = load_agent_config()
        if not authenticate_request(cfg):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

def setup_routes():
    """Conditionally setup Flask routes to avoid conflicts when imported for testing."""
    @app.route('/status', methods=['GET'])
    @require_auth
    def get_status():
        """Agent health and summary."""
        cfg = load_agent_config()
        running_count = sum(1 for p, d in twin_state.process_stats.items() if d.get("status") == "running")
        return jsonify({
            "agent_status": "ok",
            "digital_twin_last_updated": twin_state.last_updated_at.isoformat() if twin_state.last_updated_at else None,
            "monitored_processes_total": len(cfg.monitored_processes),
            "monitored_processes_running": running_count,
            "uptime_seconds": twin_state.uptime_seconds
        })

    @app.route('/stats', methods=['GET'])
    @require_auth
    def get_runtime_stats():
        """Full runtime stats."""
        return jsonify(twin_state.get_full_stats())

    @app.route('/events', methods=['GET'])
    @require_auth
    def get_events():
        """Recent events (surprise endpoint)."""
        limit = request.args.get('limit', 50, type=int)
        return jsonify({"events": twin_state.get_events(limit)})

    @app.route('/anomalies', methods=['GET', 'POST'])
    @require_auth
    def handle_anomalies():
        """Get/clear anomalies (surprise). POST to clear."""
        if request.method == 'POST':
            twin_state.clear_anomalies()
            return jsonify({"status": "cleared"})
        return jsonify({"anomalies": twin_state.anomalies})

    @app.route('/soft-reboot', methods=['POST'])
    @require_auth
    def soft_reboot():
        """Terminate monitored processes."""
        logger.info("Soft reboot initiated.")
        terminated_pids = []
        for process_name, data in twin_state.process_stats.items():
            if data.get("status") == "running" and data.get("pid"):
                pid = data["pid"]
                try:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    terminated_pids.append(pid)
                    twin_state.record_event("SOFT_REBOOT", f"Terminated '{process_name}' (PID: {pid}).")
                    logger.info(f"Terminated PID {pid} ({process_name}).")
                except Exception as e:
                    logger.error(f"Termination failed for {pid}: {e}")
        return jsonify({"status": "rebooting", "terminated_pids": terminated_pids})

    @app.route('/hook/ai', methods=['POST'])
    @require_auth
    def ai_hook():
        """Surprise: AI integration hook - execute custom command from supervisor."""
        data = request.json or {}
        cmd = data.get("command")
        if cmd == "inject_to_rene":  # Tie to rene_brain.py
            state = twin_state.get_full_stats()
            # Simulate: Send to shared memory (e.g., via file or Redis)
            with open("./rene_injection.json", "w") as f:
                json.dump({"vm_state": state, "emotion": twin_state.emotion_state}, f)
            twin_state.record_event("RENE_INJECT", "State sent to consciousness lattice.")
            return jsonify({"status": "injected"})
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            twin_state.record_event("AI_HOOK_EXEC", f"Executed: {cmd}", {"stdout": result.stdout, "stderr": result.stderr})
            return jsonify({"result": result.stdout, "success": result.returncode == 0})
        except Exception as e:
            logger.error(f"AI hook failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/health/live', methods=['GET'])
    def health_live():
        """Liveness probe."""
        return jsonify({"status": "live"})

    @app.route('/health/ready', methods=['GET'])
    def health_ready():
        """Readiness probe."""
        cfg = load_agent_config()
        config_loaded = bool(cfg.monitored_processes)
        twin_updated = twin_state.last_updated_at is not None
        ready = config_loaded and twin_updated
        return jsonify({
            "status": "ready" if ready else "not_ready",
            "config_loaded": config_loaded,
            "digital_twin_active": twin_updated
        }), (200 if ready else 503)

    @app.route('/forecast', methods=['GET'])
    @require_auth
    def get_forecast():
        """Revolutionary: Predictive insights."""
        return jsonify(twin_state.forecast_resources())

    @app.route('/evolve', methods=['POST'])
    @require_auth
    def trigger_evolution():
        """Force cognitive loop."""
        twin_state.cognitive_evolution()
        return jsonify({"status": "evolved", "q_table_size": len(twin_state.cognitive_q_table)})

    @app.route('/cluster/leader', methods=['POST'])
    @require_auth
    def elect_leader():
        """Multi-VM orchestration: Elect leader."""
        data = request.json or {}
        vm_id = data.get('vm_id', 'default')
        # Simple election: Lowest PID wins (expand for consensus)
        if not hasattr(elect_leader, 'leader_id'):
            elect_leader.leader_id = vm_id
        return jsonify({"leader": elect_leader.leader_id, "is_leader": vm_id == elect_leader.leader_id})

# Setup routes only when running as main application
if __name__ == "__main__":
    setup_routes()

# --- Main ---
def signal_handler(sig, frame):
    """Clean shutdown."""
    logger.info("Shutdown signal received.")
    stop_event.set()
    if monitor_thread.is_alive():
        monitor_thread.join(timeout=5)

def run_agent():
    """Run the Somnus Digital Twin Agent."""
    setup_routes()  # Set up routes only when running the agent
    cfg = load_agent_config()
    if not twin_state:
        logger.error("Failed to init twin state.")
        exit(1)

    global stop_event, monitor_thread
    stop_event = threading.Event()
    monitor_thread = threading.Thread(target=_monitor_loop, args=(stop_event, cfg), daemon=True)
    monitor_thread.start()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"Somnus Digital Twin Agent v4 starting on port {AGENT_PORT}.")
    logger.info("Enhanced with trends, auto-recovery, AI hooks, and anomaly detection.")

    # For prod, use: socketio.run(app, host="0.0.0.0", port=AGENT_PORT, threaded=True)
    app.run(host="0.0.0.0", port=AGENT_PORT, threaded=True)