#!/usr/bin/env python3
"""
SOMNUS Persistent Processing Queue System
Production-grade queue with cache persistence and intelligent scaling
"""

import asyncio
import logging
import threading
import time
import psutil
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum, IntEnum
from pathlib import Path
from queue import PriorityQueue, Empty
from typing import Dict, List, Optional, Any, Callable, Set, AsyncIterator
from uuid import UUID, uuid4
import weakref
import pickle
import json

logger = logging.getLogger(__name__)


class TaskPriority(IntEnum):
    """Task priority levels (lower = higher priority)"""
    CRITICAL = 1      # User-blocking operations
    HIGH = 2          # Interactive uploads
    MEDIUM = 3        # Batch operations
    LOW = 4           # Background processing
    MAINTENANCE = 5   # System cleanup tasks


class TaskStatus(str, Enum):
    """Task processing status"""
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"
    EXPIRED = "expired"


class TaskType(str, Enum):
    """Task type classification"""
    FILE_UPLOAD = "file_upload"
    CONTENT_EXTRACTION = "content_extraction"
    EMBEDDING_GENERATION = "embedding_generation"
    SECURITY_SCAN = "security_scan"
    THUMBNAIL_GENERATION = "thumbnail_generation"
    BATCH_PROCESSING = "batch_processing"
    SYSTEM_MAINTENANCE = "system_maintenance"


@dataclass
class ProcessingTask:
    """Enhanced processing task with comprehensive metadata"""
    task_id: UUID = field(default_factory=uuid4)
    task_type: TaskType = TaskType.FILE_UPLOAD
    priority: TaskPriority = TaskPriority.MEDIUM
    
    # Task data
    data: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    
    # Ownership and session
    user_id: str = ""
    session_id: UUID = field(default_factory=uuid4)
    
    # Timing and lifecycle
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    
    # Status and progress
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    status_message: str = ""
    
    # Error handling and retry
    error_count: int = 0
    max_retries: int = 3
    retry_delay_seconds: int = 30
    last_error: Optional[str] = None
    
    # Dependencies and relationships
    depends_on: List[UUID] = field(default_factory=list)
    parent_task_id: Optional[UUID] = None
    child_tasks: List[UUID] = field(default_factory=list)
    
    # Processing requirements
    estimated_duration_seconds: float = 60.0
    memory_requirement_mb: int = 100
    cpu_intensive: bool = False
    
    # Callbacks and notifications
    progress_callback: Optional[Callable[[float, str], None]] = None
    completion_callback: Optional[Callable[['ProcessingTask'], None]] = None
    
    def __lt__(self, other):
        """Enable priority queue ordering"""
        if not isinstance(other, ProcessingTask):
            return NotImplemented
        
        # First by priority, then by creation time
        return (self.priority.value, self.created_at) < (other.priority.value, other.created_at)
    
    @property
    def processing_time(self) -> Optional[float]:
        """Calculate actual processing time"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    @property
    def wait_time(self) -> Optional[float]:
        """Calculate time spent waiting in queue"""
        if self.started_at:
            return (self.started_at - self.created_at).total_seconds()
        return None
    
    @property
    def can_retry(self) -> bool:
        """Check if task can be retried"""
        return (self.error_count < self.max_retries and 
                self.status == TaskStatus.FAILED and
                not self.is_expired)
    
    @property
    def is_expired(self) -> bool:
        """Check if task has expired"""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize task for persistence"""
        return {
            'task_id': str(self.task_id),
            'task_type': self.task_type.value,
            'priority': self.priority.value,
            'data': self.data,
            'context': self.context,
            'user_id': self.user_id,
            'session_id': str(self.session_id),
            'created_at': self.created_at.isoformat(),
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'status': self.status.value,
            'progress': self.progress,
            'status_message': self.status_message,
            'error_count': self.error_count,
            'max_retries': self.max_retries,
            'retry_delay_seconds': self.retry_delay_seconds,
            'last_error': self.last_error,
            'depends_on': [str(dep) for dep in self.depends_on],
            'parent_task_id': str(self.parent_task_id) if self.parent_task_id else None,
            'child_tasks': [str(child) for child in self.child_tasks],
            'estimated_duration_seconds': self.estimated_duration_seconds,
            'memory_requirement_mb': self.memory_requirement_mb,
            'cpu_intensive': self.cpu_intensive
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProcessingTask':
        """Deserialize task from persistence"""
        task = cls(
            task_id=UUID(data['task_id']),
            task_type=TaskType(data['task_type']),
            priority=TaskPriority(data['priority']),
            data=data['data'],
            context=data['context'],
            user_id=data['user_id'],
            session_id=UUID(data['session_id']),
            created_at=datetime.fromisoformat(data['created_at']),
            scheduled_at=datetime.fromisoformat(data['scheduled_at']) if data.get('scheduled_at') else None,
            started_at=datetime.fromisoformat(data['started_at']) if data.get('started_at') else None,
            completed_at=datetime.fromisoformat(data['completed_at']) if data.get('completed_at') else None,
            expires_at=datetime.fromisoformat(data['expires_at']) if data.get('expires_at') else None,
            status=TaskStatus(data['status']),
            progress=data['progress'],
            status_message=data['status_message'],
            error_count=data['error_count'],
            max_retries=data['max_retries'],
            retry_delay_seconds=data['retry_delay_seconds'],
            last_error=data.get('last_error'),
            depends_on=[UUID(dep) for dep in data['depends_on']],
            parent_task_id=UUID(data['parent_task_id']) if data.get('parent_task_id') else None,
            child_tasks=[UUID(child) for child in data['child_tasks']],
            estimated_duration_seconds=data['estimated_duration_seconds'],
            memory_requirement_mb=data['memory_requirement_mb'],
            cpu_intensive=data['cpu_intensive']
        )
        return task


class ResourceMonitor:
    """Real-time system resource monitoring for adaptive queue management"""
    
    def __init__(self, update_interval: float = 2.0):
        self.update_interval = update_interval
        self.current_metrics = {}
        self.metrics_history = []
        self.max_history = 300  # Keep 10 minutes of history
        self._monitoring = False
        self._monitor_task = None
    
    async def start(self):
        """Start resource monitoring"""
        if not self._monitoring:
            self._monitoring = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
    
    async def stop(self):
        """Stop resource monitoring"""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
    
    async def _monitor_loop(self):
        """Continuous monitoring loop"""
        while self._monitoring:
            try:
                metrics = {
                    'timestamp': datetime.now(timezone.utc),
                    'cpu_percent': psutil.cpu_percent(interval=None),
                    'memory_percent': psutil.virtual_memory().percent,
                    'memory_available_gb': psutil.virtual_memory().available / (1024**3),
                    'disk_io_busy': self._get_disk_io_busy(),
                    'load_average': psutil.getloadavg()[0] if hasattr(psutil, 'getloadavg') else 0,
                    'active_threads': threading.active_count(),
                    'active_processes': len(psutil.pids())
                }
                
                self.current_metrics = metrics
                self.metrics_history.append(metrics)
                
                # Maintain history size
                if len(self.metrics_history) > self.max_history:
                    self.metrics_history.pop(0)
                
                await asyncio.sleep(self.update_interval)
                
            except Exception as e:
                logger.warning(f"Resource monitoring error: {e}")
                await asyncio.sleep(self.update_interval)
    
    def _get_disk_io_busy(self) -> float:
        """Calculate disk I/O busy percentage"""
        try:
            disk_io = psutil.disk_io_counters()
            if disk_io:
                # Simple heuristic based on I/O operations
                return min(100, (disk_io.read_count + disk_io.write_count) / 1000)
            return 0
        except (OSError, AttributeError, psutil.Error) as e:
            logger.debug(f"Disk I/O metrics unavailable: {e}")
            return 0
    
    def get_load_factor(self) -> float:
        """Calculate overall system load factor (0.0 = idle, 1.0 = overloaded)"""
        if not self.current_metrics:
            return 0.5
        
        cpu_load = self.current_metrics.get('cpu_percent', 0) / 100
        memory_load = self.current_metrics.get('memory_percent', 0) / 100
        disk_load = self.current_metrics.get('disk_io_busy', 0) / 100
        
        # Weighted load calculation
        return (cpu_load * 0.4) + (memory_load * 0.4) + (disk_load * 0.2)
    
    def get_optimal_workers(self, base_workers: int, task_type: TaskType) -> int:
        """Calculate optimal worker count based on system load and task type"""
        load_factor = self.get_load_factor()
        
        # Adjust based on task type
        if task_type in [TaskType.CONTENT_EXTRACTION, TaskType.EMBEDDING_GENERATION]:
            # CPU intensive tasks
            cpu_factor = 1.0 - (self.current_metrics.get('cpu_percent', 0) / 100)
            optimal = int(base_workers * cpu_factor * (2.0 - load_factor))
        elif task_type == TaskType.FILE_UPLOAD:
            # I/O intensive tasks
            io_factor = 1.0 - (self.current_metrics.get('disk_io_busy', 0) / 100)
            optimal = int(base_workers * io_factor * (1.5 - load_factor * 0.5))
        else:
            # General tasks
            optimal = int(base_workers * (1.5 - load_factor))
        
        return max(1, min(optimal, base_workers * 2))
    
    def can_accept_task(self, task: ProcessingTask) -> bool:
        """Check if system can accept new task based on resources"""
        if not self.current_metrics:
            return True
        
        # Memory check
        available_memory_gb = self.current_metrics.get('memory_available_gb', 4.0)
        required_memory_gb = task.memory_requirement_mb / 1024
        
        if required_memory_gb > available_memory_gb * 0.8:  # Keep 20% buffer
            return False
        
        # CPU check for intensive tasks
        if task.cpu_intensive:
            cpu_usage = self.current_metrics.get('cpu_percent', 0)
            if cpu_usage > 85:
                return False
        
        # Overall load check
        if self.get_load_factor() > 0.9:
            return False
        
        return True


class PersistentQueue:
    """Persistent priority queue backed by SOMNUS cache"""
    
    def __init__(self, cache_engine, queue_name: str = "default"):
        self.cache = cache_engine
        self.queue_name = queue_name
        self.memory_queue = PriorityQueue()
        self.task_index = {}  # task_id -> task mapping
        self.lock = asyncio.Lock()
        
        # Queue state tracking
        self.queue_metrics = {
            'tasks_queued': 0,
            'tasks_processed': 0,
            'tasks_failed': 0,
            'average_wait_time': 0.0,
            'average_processing_time': 0.0
        }
    
    async def initialize(self):
        """Load persisted tasks from cache"""
        if not self.cache:
            return
        
        try:
            # Load queue metadata
            queue_data = await self.cache.get(f"queue:{self.queue_name}:metadata")
            if queue_data:
                self.queue_metrics.update(queue_data)
            
            # Load active tasks
            task_ids = await self.cache.get(f"queue:{self.queue_name}:tasks") or []
            
            loaded_count = 0
            for task_id in task_ids:
                task_data = await self.cache.get(f"queue_task:{task_id}")
                if task_data:
                    try:
                        task = ProcessingTask.from_dict(task_data)
                        
                        # Only reload pending and queued tasks
                        if task.status in [TaskStatus.PENDING, TaskStatus.QUEUED]:
                            await self._add_to_memory_queue(task)
                            loaded_count += 1
                    except Exception as e:
                        logger.error(f"Failed to load task {task_id}: {e}")
            
            logger.info(f"Loaded {loaded_count} tasks from persistent queue")
            
        except Exception as e:
            logger.error(f"Queue initialization failed: {e}")
    
    async def put(self, task: ProcessingTask):
        """Add task to queue with persistence"""
        async with self.lock:
            # Set queue status
            task.status = TaskStatus.QUEUED
            
            # Persist task
            if self.cache:
                await self.cache.set(
                    f"queue_task:{task.task_id}",
                    task.to_dict(),
                    ttl_seconds=86400 * 7  # 7 days
                )
                
                # Update task list
                task_ids = await self.cache.get(f"queue:{self.queue_name}:tasks") or []
                if str(task.task_id) not in task_ids:
                    task_ids.append(str(task.task_id))
                    await self.cache.set(f"queue:{self.queue_name}:tasks", task_ids)
            
            # Add to memory queue
            await self._add_to_memory_queue(task)
            
            self.queue_metrics['tasks_queued'] += 1
            await self._save_metrics()
    
    async def get(self, timeout: float = 1.0) -> Optional[ProcessingTask]:
        """Get next task from queue"""
        try:
            task = self.memory_queue.get(timeout=timeout)
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now(timezone.utc)
            
            # Update persistence
            if self.cache:
                await self.cache.set(f"queue_task:{task.task_id}", task.to_dict())
            
            return task
            
        except Empty:
            return None
    
    async def complete_task(self, task: ProcessingTask, success: bool = True):
        """Mark task as completed and update metrics"""
        async with self.lock:
            task.completed_at = datetime.now(timezone.utc)
            task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
            
            # Update metrics
            if success:
                self.queue_metrics['tasks_processed'] += 1
            else:
                self.queue_metrics['tasks_failed'] += 1
                task.error_count += 1
            
            # Update average times
            if task.processing_time:
                current_avg = self.queue_metrics['average_processing_time']
                processed = self.queue_metrics['tasks_processed']
                if processed > 1:
                    self.queue_metrics['average_processing_time'] = (
                        (current_avg * (processed - 1) + task.processing_time) / processed
                    )
                else:
                    self.queue_metrics['average_processing_time'] = task.processing_time
            
            if task.wait_time:
                current_avg = self.queue_metrics['average_wait_time']
                total_tasks = self.queue_metrics['tasks_processed'] + self.queue_metrics['tasks_failed']
                if total_tasks > 1:
                    self.queue_metrics['average_wait_time'] = (
                        (current_avg * (total_tasks - 1) + task.wait_time) / total_tasks
                    )
                else:
                    self.queue_metrics['average_wait_time'] = task.wait_time
            
            # Persist final state
            if self.cache:
                await self.cache.set(f"queue_task:{task.task_id}", task.to_dict())
                await self._save_metrics()
            
            # Remove from active task list if completed
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                await self._remove_from_active_tasks(task.task_id)
    
    async def retry_task(self, task: ProcessingTask):
        """Retry failed task with backoff"""
        if not task.can_retry:
            return False
        
        # Calculate retry delay with exponential backoff
        delay = task.retry_delay_seconds * (2 ** task.error_count)
        delay = min(delay, 3600)  # Max 1 hour delay
        
        # Schedule retry
        task.status = TaskStatus.RETRYING
        task.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        
        # Re-queue after delay
        async def schedule_retry():
            await asyncio.sleep(delay)
            task.status = TaskStatus.PENDING
            task.scheduled_at = None
            await self._add_to_memory_queue(task)
        
        asyncio.create_task(schedule_retry())
        
        if self.cache:
            await self.cache.set(f"queue_task:{task.task_id}", task.to_dict())
        
        logger.info(f"Scheduled retry for task {task.task_id} in {delay} seconds")
        return True
    
    async def cancel_task(self, task_id: UUID) -> bool:
        """Cancel pending task"""
        if str(task_id) in self.task_index:
            task = self.task_index[str(task_id)]
            if task.status in [TaskStatus.PENDING, TaskStatus.QUEUED]:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc)
                
                if self.cache:
                    await self.cache.set(f"queue_task:{task_id}", task.to_dict())
                
                await self._remove_from_active_tasks(task_id)
                return True
        
        return False
    
    async def _add_to_memory_queue(self, task: ProcessingTask):
        """Add task to in-memory priority queue"""
        self.memory_queue.put_nowait(task)
        self.task_index[str(task.task_id)] = task
    
    async def _remove_from_active_tasks(self, task_id: UUID):
        """Remove task from active task tracking"""
        self.task_index.pop(str(task_id), None)
        
        if self.cache:
            task_ids = await self.cache.get(f"queue:{self.queue_name}:tasks") or []
            if str(task_id) in task_ids:
                task_ids.remove(str(task_id))
                await self.cache.set(f"queue:{self.queue_name}:tasks", task_ids)
    
    async def _save_metrics(self):
        """Persist queue metrics"""
        if self.cache:
            await self.cache.set(f"queue:{self.queue_name}:metadata", self.queue_metrics)
    
    def get_status(self) -> Dict[str, Any]:
        """Get queue status and metrics"""
        return {
            'queue_name': self.queue_name,
            'pending_tasks': self.memory_queue.qsize(),
            'active_tasks': len(self.task_index),
            'metrics': self.queue_metrics.copy()
        }
    
    async def cleanup_expired(self):
        """Remove expired tasks from queue"""
        current_time = datetime.now(timezone.utc)
        expired_tasks = []
        
        for task_id, task in list(self.task_index.items()):
            if task.is_expired:
                task.status = TaskStatus.EXPIRED
                task.completed_at = current_time
                expired_tasks.append(task.task_id)
        
        # Remove expired tasks
        for task_id in expired_tasks:
            await self._remove_from_active_tasks(task_id)
        
        if expired_tasks:
            logger.info(f"Cleaned up {len(expired_tasks)} expired tasks")
        
        return len(expired_tasks)


class TaskProcessor:
    """Individual task processor with error handling and callbacks"""
    
    def __init__(self, processor_id: str, task_handlers: Dict[TaskType, Callable]):
        self.processor_id = processor_id
        self.task_handlers = task_handlers
        self.current_task: Optional[ProcessingTask] = None
        self.processed_count = 0
        self.error_count = 0
        self.start_time = time.time()
    
    async def process_task(self, task: ProcessingTask) -> bool:
        """Process individual task with comprehensive error handling"""
        self.current_task = task
        
        try:
            # Check if handler exists for task type
            if task.task_type not in self.task_handlers:
                raise ValueError(f"No handler for task type: {task.task_type}")
            
            handler = self.task_handlers[task.task_type]
            
            # Update progress callback
            if task.progress_callback:
                task.progress_callback(10.0, f"Starting {task.task_type.value} processing")
            
            # Execute task
            logger.info(f"Processor {self.processor_id} starting task {task.task_id}")
            
            start_time = time.time()
            result = await handler(task)
            processing_time = time.time() - start_time
            
            # Update progress
            task.progress = 100.0
            task.status_message = "Processing completed"
            
            if task.progress_callback:
                task.progress_callback(100.0, "Processing completed")
            
            # Call completion callback
            if task.completion_callback:
                try:
                    task.completion_callback(task)
                except Exception as e:
                    logger.error(f"Completion callback failed: {e}")
            
            self.processed_count += 1
            
            logger.info(f"Processor {self.processor_id} completed task {task.task_id} in {processing_time:.2f}s")
            
            return True
            
        except Exception as e:
            logger.error(f"Processor {self.processor_id} failed task {task.task_id}: {e}")
            
            task.last_error = str(e)
            task.status_message = f"Processing failed: {str(e)}"
            
            if task.progress_callback:
                task.progress_callback(0.0, f"Processing failed: {str(e)}")
            
            self.error_count += 1
            
            return False
        
        finally:
            self.current_task = None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get processor statistics"""
        uptime = time.time() - self.start_time
        
        return {
            'processor_id': self.processor_id,
            'uptime_seconds': uptime,
            'processed_count': self.processed_count,
            'error_count': self.error_count,
            'success_rate': (self.processed_count / max(1, self.processed_count + self.error_count)) * 100,
            'current_task': str(self.current_task.task_id) if self.current_task else None,
            'supported_task_types': list(self.task_handlers.keys())
        }


class PersistentProcessingEngine:
    """Main processing engine with persistent queues and adaptive scaling"""
    
    def __init__(
        self,
        cache_engine,
        task_handlers: Dict[TaskType, Callable],
        max_workers: int = 4,
        enable_monitoring: bool = True
    ):
        self.cache = cache_engine
        self.task_handlers = task_handlers
        self.max_workers = max_workers
        self.enable_monitoring = enable_monitoring
        
        # Core components
        self.queue = PersistentQueue(cache_engine, "main_processing")
        self.resource_monitor = ResourceMonitor() if enable_monitoring else None
        
        # Worker management
        self.processors: Dict[str, TaskProcessor] = {}
        self.worker_tasks: Dict[str, asyncio.Task] = {}
        self.active_workers = 0
        
        # Engine state
        self.running = False
        self.shutdown_event = asyncio.Event()
        
        # Background tasks
        self.scaling_task: Optional[asyncio.Task] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        
        # Rate limiting
        self.user_rate_limits: Dict[str, List[float]] = {}
        self.max_tasks_per_user_per_minute = 20
        
        logger.info(f"Persistent processing engine initialized with {max_workers} max workers")
    
    async def start(self):
        """Start the processing engine"""
        if self.running:
            return
        
        self.running = True
        self.shutdown_event.clear()
        
        # Initialize components
        await self.queue.initialize()
        
        if self.resource_monitor:
            await self.resource_monitor.start()
        
        # Start initial workers
        await self._scale_workers(min(2, self.max_workers))
        
        # Start background tasks
        self.scaling_task = asyncio.create_task(self._adaptive_scaling_loop())
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.info("Persistent processing engine started")
    
    async def stop(self):
        """Gracefully stop the processing engine"""
        if not self.running:
            return
        
        logger.info("Stopping persistent processing engine...")
        
        self.running = False
        self.shutdown_event.set()
        
        # Stop background tasks
        if self.scaling_task:
            self.scaling_task.cancel()
        if self.cleanup_task:
            self.cleanup_task.cancel()
        
        # Stop workers
        for task in self.worker_tasks.values():
            task.cancel()
        
        # Wait for workers to finish
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks.values(), return_exceptions=True)
        
        # Stop resource monitoring
        if self.resource_monitor:
            await self.resource_monitor.stop()
        
        logger.info("Persistent processing engine stopped")
    
    async def submit_task(
        self,
        task_type: TaskType,
        data: Dict[str, Any],
        user_id: str,
        session_id: UUID,
        priority: TaskPriority = TaskPriority.MEDIUM,
        **kwargs
    ) -> UUID:
        """Submit task for processing with rate limiting"""
        
        # Rate limiting check
        if not await self._check_rate_limit(user_id):
            raise ValueError(f"Rate limit exceeded for user {user_id}")
        
        # Create task
        task = ProcessingTask(
            task_type=task_type,
            priority=priority,
            data=data,
            user_id=user_id,
            session_id=session_id,
            **kwargs
        )
        
        # Resource availability check
        if self.resource_monitor and not self.resource_monitor.can_accept_task(task):
            # Queue for later when resources available
            task.scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        
        # Add to queue
        await self.queue.put(task)
        
        logger.info(f"Submitted task {task.task_id} ({task_type.value}) for user {user_id}")
        
        return task.task_id
    
    async def get_task_status(self, task_id: UUID) -> Optional[Dict[str, Any]]:
        """Get task status"""
        if not self.cache:
            return None
        
        task_data = await self.cache.get(f"queue_task:{task_id}")
        if task_data:
            return {
                'task_id': task_id,
                'status': task_data['status'],
                'progress': task_data['progress'],
                'status_message': task_data['status_message'],
                'created_at': task_data['created_at'],
                'started_at': task_data.get('started_at'),
                'completed_at': task_data.get('completed_at'),
                'error_count': task_data['error_count'],
                'last_error': task_data.get('last_error')
            }
        
        return None
    
    async def cancel_task(self, task_id: UUID, user_id: str) -> bool:
        """Cancel task (only by owner)"""
        if not self.cache:
            return False
        
        task_data = await self.cache.get(f"queue_task:{task_id}")
        if task_data and task_data['user_id'] == user_id:
            return await self.queue.cancel_task(task_id)
        
        return False
    
    async def _check_rate_limit(self, user_id: str) -> bool:
        """Check user rate limiting"""
        current_time = time.time()
        
        # Initialize user rate limit tracking
        if user_id not in self.user_rate_limits:
            self.user_rate_limits[user_id] = []
        
        # Clean old entries (older than 1 minute)
        cutoff_time = current_time - 60
        self.user_rate_limits[user_id] = [
            t for t in self.user_rate_limits[user_id] if t > cutoff_time
        ]
        
        # Check rate limit
        if len(self.user_rate_limits[user_id]) >= self.max_tasks_per_user_per_minute:
            return False
        
        # Add current request
        self.user_rate_limits[user_id].append(current_time)
        
        return True
    
    async def _worker_loop(self, processor: TaskProcessor):
        """Main worker loop for processing tasks"""
        processor_id = processor.processor_id
        
        logger.info(f"Worker {processor_id} started")
        
        while self.running and not self.shutdown_event.is_set():
            try:
                # Get next task
                task = await self.queue.get(timeout=1.0)
                
                if task is None:
                    continue
                
                # Check if task is scheduled for later
                if task.scheduled_at and datetime.now(timezone.utc) < task.scheduled_at:
                    # Put back in queue
                    await self.queue.put(task)
                    await asyncio.sleep(1)
                    continue
                
                # Process task
                success = await processor.process_task(task)
                
                # Complete task in queue
                await self.queue.complete_task(task, success)
                
                # Handle retry if failed
                if not success and task.can_retry:
                    await self.queue.retry_task(task)
                
            except Exception as e:
                logger.error(f"Worker {processor_id} error: {e}")
                await asyncio.sleep(1)
        
        logger.info(f"Worker {processor_id} stopped")
    
    async def _scale_workers(self, target_workers: int):
        """Scale worker count to target"""
        current_workers = len(self.processors)
        
        if target_workers > current_workers:
            # Add workers
            for i in range(target_workers - current_workers):
                processor_id = f"processor_{len(self.processors)}"
                processor = TaskProcessor(processor_id, self.task_handlers)
                
                self.processors[processor_id] = processor
                self.worker_tasks[processor_id] = asyncio.create_task(
                    self._worker_loop(processor)
                )
            
            logger.info(f"Scaled up to {target_workers} workers")
        
        elif target_workers < current_workers:
            # Remove workers
            workers_to_remove = current_workers - target_workers
            processor_ids = list(self.processors.keys())[-workers_to_remove:]
            
            for processor_id in processor_ids:
                # Cancel worker task
                if processor_id in self.worker_tasks:
                    self.worker_tasks[processor_id].cancel()
                    del self.worker_tasks[processor_id]
                
                # Remove processor
                del self.processors[processor_id]
            
            logger.info(f"Scaled down to {target_workers} workers")
        
        self.active_workers = len(self.processors)
    
    async def _adaptive_scaling_loop(self):
        """Continuously adapt worker count based on queue and system load"""
        while self.running and not self.shutdown_event.is_set():
            try:
                queue_status = self.queue.get_status()
                pending_tasks = queue_status['pending_tasks']
                
                # Determine optimal worker count
                if self.resource_monitor:
                    base_workers = self.resource_monitor.get_optimal_workers(
                        self.max_workers, TaskType.FILE_UPLOAD
                    )
                else:
                    base_workers = self.max_workers
                
                # Adjust based on queue length
                if pending_tasks > 10:
                    target_workers = min(base_workers, self.max_workers)
                elif pending_tasks > 5:
                    target_workers = min(base_workers // 2 + 1, self.max_workers)
                elif pending_tasks == 0:
                    target_workers = max(1, base_workers // 4)
                else:
                    target_workers = max(2, base_workers // 2)
                
                # Scale if needed
                if target_workers != self.active_workers:
                    await self._scale_workers(target_workers)
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"Scaling loop error: {e}")
                await asyncio.sleep(10)
    
    async def _cleanup_loop(self):
        """Periodic cleanup of expired tasks and metrics"""
        while self.running and not self.shutdown_event.is_set():
            try:
                # Cleanup expired tasks
                await self.queue.cleanup_expired()
                
                # Cleanup old rate limit entries
                current_time = time.time()
                cutoff_time = current_time - 300  # 5 minutes
                
                for user_id in list(self.user_rate_limits.keys()):
                    self.user_rate_limits[user_id] = [
                        t for t in self.user_rate_limits[user_id] if t > cutoff_time
                    ]
                    
                    # Remove empty entries
                    if not self.user_rate_limits[user_id]:
                        del self.user_rate_limits[user_id]
                
                await asyncio.sleep(300)  # Every 5 minutes
                
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")
                await asyncio.sleep(300)
    
    def get_engine_status(self) -> Dict[str, Any]:
        """Get comprehensive engine status"""
        queue_status = self.queue.get_status()
        
        processor_stats = [p.get_stats() for p in self.processors.values()]
        
        system_metrics = {}
        if self.resource_monitor:
            system_metrics = self.resource_monitor.current_metrics
        
        return {
            'running': self.running,
            'active_workers': self.active_workers,
            'max_workers': self.max_workers,
            'queue_status': queue_status,
            'processor_stats': processor_stats,
            'system_metrics': system_metrics,
            'active_rate_limited_users': len(self.user_rate_limits),
            'supported_task_types': list(self.task_handlers.keys())
        }


# Factory function for easy setup
def create_processing_engine(
    cache_engine,
    task_handlers: Dict[TaskType, Callable],
    max_workers: int = 4
) -> PersistentProcessingEngine:
    """Create persistent processing engine with production configuration"""
    
    return PersistentProcessingEngine(
        cache_engine=cache_engine,
        task_handlers=task_handlers,
        max_workers=max_workers,
        enable_monitoring=True
    )


# Example usage
if __name__ == "__main__":
    async def main():
        # Mock cache for testing
        class MockCache:
            def __init__(self):
                self.data = {}
            
            async def get(self, key):
                return self.data.get(key)
            
            async def set(self, key, value, ttl_seconds=None):
                self.data[key] = value
            
            async def delete(self, key):
                self.data.pop(key, None)
        
        # Mock task handlers
        async def file_upload_handler(task: ProcessingTask) -> bool:
            await asyncio.sleep(2)  # Simulate processing
            return True
        
        async def content_extraction_handler(task: ProcessingTask) -> bool:
            await asyncio.sleep(1)  # Simulate processing
            return True
        
        task_handlers = {
            TaskType.FILE_UPLOAD: file_upload_handler,
            TaskType.CONTENT_EXTRACTION: content_extraction_handler
        }
        
        # Create engine
        cache = MockCache()
        engine = create_processing_engine(cache, task_handlers, max_workers=2)
        
        try:
            # Start engine
            await engine.start()
            
            # Submit test tasks
            task_id1 = await engine.submit_task(
                TaskType.FILE_UPLOAD,
                {"filename": "test.txt"},
                user_id="test_user",
                session_id=uuid4(),
                priority=TaskPriority.HIGH
            )
            
            task_id2 = await engine.submit_task(
                TaskType.CONTENT_EXTRACTION,
                {"content": "test content"},
                user_id="test_user",
                session_id=uuid4(),
                priority=TaskPriority.MEDIUM
            )
            
            print(f"Submitted tasks: {task_id1}, {task_id2}")
            
            # Wait for processing
            await asyncio.sleep(5)
            
            # Check status
            status1 = await engine.get_task_status(task_id1)
            status2 = await engine.get_task_status(task_id2)
            
            print(f"Task 1 status: {status1}")
            print(f"Task 2 status: {status2}")
            
            # Engine status
            engine_status = engine.get_engine_status()
            print(f"Engine status: {engine_status}")
            
        finally:
            await engine.stop()
    
    asyncio.run(main())
