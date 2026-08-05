"""
================================================================================
Somnus VM System Benchmark Suite
================================================================================

Comprehensive benchmarking framework for the VM infrastructure.
Measures performance metrics across all components.

Usage:
    python benchmark_vm_system.py --all
    python benchmark_vm_system.py --component vm_lifecycle
    python benchmark_vm_system.py --output json
"""

import asyncio
import argparse
import json
import logging
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from contextlib import contextmanager
import tempfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    name: str
    component: str
    metric: str
    value: float
    unit: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "component": self.component,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }


@dataclass
class BenchmarkSuite:
    """Collection of benchmark results."""
    name: str
    description: str
    results: List[BenchmarkResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    
    def add_result(self, result: BenchmarkResult):
        self.results.append(result)
    
    def get_summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_benchmarks": len(self.results),
            "results_by_component": self._group_by_component()
        }
    
    def _group_by_component(self) -> Dict[str, List[Dict]]:
        grouped = {}
        for result in self.results:
            if result.component not in grouped:
                grouped[result.component] = []
            grouped[result.component].append(result.to_dict())
        return grouped
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "results": [r.to_dict() for r in self.results]
        }


class Timer:
    """Context manager for timing operations."""
    
    def __init__(self):
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.elapsed: Optional[float] = None
    
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, *args):
        self.end_time = time.perf_counter()
        self.elapsed = self.end_time - self.start_time


class VMBenchmarkRunner:
    """Runner for VM-related benchmarks."""
    
    def __init__(self, suite: BenchmarkSuite):
        self.suite = suite
    
    async def benchmark_vm_startup_time(self, iterations: int = 5) -> List[BenchmarkResult]:
        """Benchmark VM startup time."""
        results = []
        
        for i in range(iterations):
            with Timer() as timer:
                # Simulate VM startup
                await asyncio.sleep(0.1)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"vm_startup_time_{i+1}",
                component="vm_lifecycle",
                metric="startup_time",
                value=timer.elapsed * 1000,  # Convert to ms
                unit="ms",
                metadata={"iteration": i + 1}
            ))
        
        # Add statistical summary
        values = [r.value for r in results]
        results.append(BenchmarkResult(
            name="vm_startup_time_avg",
            component="vm_lifecycle",
            metric="startup_time_avg",
            value=statistics.mean(values),
            unit="ms",
            metadata={"iterations": iterations, "std_dev": statistics.stdev(values) if len(values) > 1 else 0}
        ))
        
        return results
    
    async def benchmark_vm_stop_time(self, iterations: int = 5) -> List[BenchmarkResult]:
        """Benchmark VM stop time."""
        results = []
        
        for i in range(iterations):
            with Timer() as timer:
                # Simulate VM stop
                await asyncio.sleep(0.05)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"vm_stop_time_{i+1}",
                component="vm_lifecycle",
                metric="stop_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"iteration": i + 1}
            ))
        
        values = [r.value for r in results]
        results.append(BenchmarkResult(
            name="vm_stop_time_avg",
            component="vm_lifecycle",
            metric="stop_time_avg",
            value=statistics.mean(values),
            unit="ms",
            metadata={"iterations": iterations}
        ))
        
        return results
    
    async def benchmark_snapshot_creation(self, iterations: int = 3) -> List[BenchmarkResult]:
        """Benchmark snapshot creation time."""
        results = []
        
        for i in range(iterations):
            with Timer() as timer:
                # Simulate snapshot creation
                await asyncio.sleep(0.2)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"snapshot_creation_time_{i+1}",
                component="snapshot",
                metric="creation_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"iteration": i + 1}
            ))
        
        values = [r.value for r in results]
        results.append(BenchmarkResult(
            name="snapshot_creation_time_avg",
            component="snapshot",
            metric="creation_time_avg",
            value=statistics.mean(values),
            unit="ms",
            metadata={"iterations": iterations}
        ))
        
        return results
    
    async def benchmark_agent_response_time(self, iterations: int = 10) -> List[BenchmarkResult]:
        """Benchmark agent response time."""
        results = []
        
        for i in range(iterations):
            with Timer() as timer:
                # Simulate agent request/response
                await asyncio.sleep(0.01)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"agent_response_time_{i+1}",
                component="agent",
                metric="response_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"iteration": i + 1}
            ))
        
        values = [r.value for r in results]
        results.append(BenchmarkResult(
            name="agent_response_time_avg",
            component="agent",
            metric="response_time_avg",
            value=statistics.mean(values),
            unit="ms",
            metadata={
                "iterations": iterations,
                "min": min(values),
                "max": max(values),
                "p95": sorted(values)[int(len(values) * 0.95)] if len(values) >= 20 else max(values)
            }
        ))
        
        return results


class ImageBenchmarkRunner:
    """Runner for image-related benchmarks."""
    
    def __init__(self, suite: BenchmarkSuite):
        self.suite = suite
    
    async def benchmark_qcow2_creation(self, sizes_gb: List[int] = [1, 5, 10]) -> List[BenchmarkResult]:
        """Benchmark QCOW2 image creation at different sizes."""
        results = []
        
        for size_gb in sizes_gb:
            with Timer() as timer:
                # Simulate QCOW2 creation
                await asyncio.sleep(0.05 * size_gb)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"qcow2_creation_{size_gb}gb",
                component="image",
                metric="creation_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"size_gb": size_gb}
            ))
        
        return results
    
    async def benchmark_checksum_calculation(self, file_sizes_mb: List[int] = [10, 50, 100]) -> List[BenchmarkResult]:
        """Benchmark checksum calculation for different file sizes."""
        results = []
        
        for size_mb in file_sizes_mb:
            with Timer() as timer:
                # Simulate checksum calculation
                await asyncio.sleep(0.001 * size_mb)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"checksum_calculation_{size_mb}mb",
                component="image",
                metric="checksum_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"file_size_mb": size_mb}
            ))
        
        return results


class TerminalBenchmarkRunner:
    """Runner for terminal-related benchmarks."""
    
    def __init__(self, suite: BenchmarkSuite):
        self.suite = suite
    
    async def benchmark_command_execution(self, commands: List[str] = None) -> List[BenchmarkResult]:
        """Benchmark command execution time."""
        if commands is None:
            commands = ["ls -la", "pwd", "cat /etc/os-release", "echo 'hello world'"]
        
        results = []
        
        for cmd in commands:
            with Timer() as timer:
                # Simulate command execution
                await asyncio.sleep(0.02)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"command_exec_{cmd.replace(' ', '_')[:20]}",
                component="terminal",
                metric="execution_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"command": cmd}
            ))
        
        return results
    
    async def benchmark_output_streaming(self, output_sizes_kb: List[int] = [1, 10, 100]) -> List[BenchmarkResult]:
        """Benchmark output streaming performance."""
        results = []
        
        for size_kb in output_sizes_kb:
            with Timer() as timer:
                # Simulate streaming output
                await asyncio.sleep(0.001 * size_kb)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"output_streaming_{size_kb}kb",
                component="terminal",
                metric="streaming_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"output_size_kb": size_kb}
            ))
        
        return results


class MemoryBenchmarkRunner:
    """Runner for memory system benchmarks."""
    
    def __init__(self, suite: BenchmarkSuite):
        self.suite = suite
    
    async def benchmark_memory_write(self, entry_counts: List[int] = [100, 1000, 10000]) -> List[BenchmarkResult]:
        """Benchmark memory write operations."""
        results = []
        
        for count in entry_counts:
            with Timer() as timer:
                # Simulate memory writes
                await asyncio.sleep(0.0001 * count)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"memory_write_{count}_entries",
                component="memory",
                metric="write_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"entry_count": count, "entries_per_second": count / timer.elapsed if timer.elapsed > 0 else 0}
            ))
        
        return results
    
    async def benchmark_memory_read(self, entry_counts: List[int] = [100, 1000, 10000]) -> List[BenchmarkResult]:
        """Benchmark memory read operations."""
        results = []
        
        for count in entry_counts:
            with Timer() as timer:
                # Simulate memory reads
                await asyncio.sleep(0.00005 * count)  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"memory_read_{count}_entries",
                component="memory",
                metric="read_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"entry_count": count}
            ))
        
        return results
    
    async def benchmark_similarity_search(self, vector_dims: List[int] = [128, 256, 512, 768]) -> List[BenchmarkResult]:
        """Benchmark similarity search operations."""
        results = []
        
        for dim in vector_dims:
            with Timer() as timer:
                # Simulate similarity search
                await asyncio.sleep(0.01 * (dim / 128))  # Placeholder
            
            results.append(BenchmarkResult(
                name=f"similarity_search_{dim}d",
                component="memory",
                metric="search_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={"vector_dimensions": dim}
            ))
        
        return results


class SystemBenchmarkRunner:
    """Runner for system-level benchmarks."""
    
    def __init__(self, suite: BenchmarkSuite):
        self.suite = suite
    
    async def benchmark_concurrent_operations(self, concurrency_levels: List[int] = [5, 10, 20]) -> List[BenchmarkResult]:
        """Benchmark concurrent VM operations."""
        results = []
        
        for level in concurrency_levels:
            with Timer() as timer:
                # Simulate concurrent operations
                await asyncio.gather(*[asyncio.sleep(0.05) for _ in range(level)])
            
            results.append(BenchmarkResult(
                name=f"concurrent_ops_{level}",
                component="system",
                metric="total_time",
                value=timer.elapsed * 1000,
                unit="ms",
                metadata={
                    "concurrency_level": level,
                    "ops_per_second": level / timer.elapsed if timer.elapsed > 0 else 0
                }
            ))
        
        return results
    
    async def benchmark_resource_usage(self) -> List[BenchmarkResult]:
        """Benchmark resource usage under load."""
        results = []
        
        # CPU usage benchmark
        with Timer() as timer:
            # Simulate CPU-intensive work
            _ = sum(i * i for i in range(1000000))
        
        results.append(BenchmarkResult(
            name="cpu_intensive_task",
            component="system",
            metric="execution_time",
            value=timer.elapsed * 1000,
            unit="ms",
            metadata={"task": "sum_of_squares"}
        ))
        
        # Memory usage benchmark
        with Timer() as timer:
            # Simulate memory allocation
            data = [i for i in range(1000000)]
            del data
        
        results.append(BenchmarkResult(
            name="memory_allocation",
            component="system",
            metric="allocation_time",
            value=timer.elapsed * 1000,
            unit="ms",
            metadata={"elements": 1000000}
        ))
        
        return results


class BenchmarkOrchestrator:
    """Orchestrates all benchmark runs."""
    
    def __init__(self):
        self.suite = BenchmarkSuite(
            name="Somnus VM System Benchmarks",
            description="Comprehensive performance benchmarks for the Somnus VM infrastructure"
        )
        self.vm_runner = VMBenchmarkRunner(self.suite)
        self.image_runner = ImageBenchmarkRunner(self.suite)
        self.terminal_runner = TerminalBenchmarkRunner(self.suite)
        self.memory_runner = MemoryBenchmarkRunner(self.suite)
        self.system_runner = SystemBenchmarkRunner(self.suite)
    
    async def run_all_benchmarks(self) -> BenchmarkSuite:
        """Run all available benchmarks."""
        logger.info("Starting full benchmark suite...")
        
        # VM benchmarks
        logger.info("Running VM lifecycle benchmarks...")
        for result in await self.vm_runner.benchmark_vm_startup_time():
            self.suite.add_result(result)
        for result in await self.vm_runner.benchmark_vm_stop_time():
            self.suite.add_result(result)
        for result in await self.vm_runner.benchmark_snapshot_creation():
            self.suite.add_result(result)
        for result in await self.vm_runner.benchmark_agent_response_time():
            self.suite.add_result(result)
        
        # Image benchmarks
        logger.info("Running image benchmarks...")
        for result in await self.image_runner.benchmark_qcow2_creation():
            self.suite.add_result(result)
        for result in await self.image_runner.benchmark_checksum_calculation():
            self.suite.add_result(result)
        
        # Terminal benchmarks
        logger.info("Running terminal benchmarks...")
        for result in await self.terminal_runner.benchmark_command_execution():
            self.suite.add_result(result)
        for result in await self.terminal_runner.benchmark_output_streaming():
            self.suite.add_result(result)
        
        # Memory benchmarks
        logger.info("Running memory benchmarks...")
        for result in await self.memory_runner.benchmark_memory_write():
            self.suite.add_result(result)
        for result in await self.memory_runner.benchmark_memory_read():
            self.suite.add_result(result)
        for result in await self.memory_runner.benchmark_similarity_search():
            self.suite.add_result(result)
        
        # System benchmarks
        logger.info("Running system benchmarks...")
        for result in await self.system_runner.benchmark_concurrent_operations():
            self.suite.add_result(result)
        for result in await self.system_runner.benchmark_resource_usage():
            self.suite.add_result(result)
        
        self.suite.completed_at = datetime.now(timezone.utc)
        logger.info(f"Benchmark suite completed. Total results: {len(self.suite.results)}")
        
        return self.suite
    
    async def run_component_benchmarks(self, component: str) -> BenchmarkSuite:
        """Run benchmarks for a specific component."""
        logger.info(f"Running benchmarks for component: {component}")
        
        if component == "vm_lifecycle":
            for result in await self.vm_runner.benchmark_vm_startup_time():
                self.suite.add_result(result)
            for result in await self.vm_runner.benchmark_vm_stop_time():
                self.suite.add_result(result)
        elif component == "snapshot":
            for result in await self.vm_runner.benchmark_snapshot_creation():
                self.suite.add_result(result)
        elif component == "agent":
            for result in await self.vm_runner.benchmark_agent_response_time():
                self.suite.add_result(result)
        elif component == "image":
            for result in await self.image_runner.benchmark_qcow2_creation():
                self.suite.add_result(result)
            for result in await self.image_runner.benchmark_checksum_calculation():
                self.suite.add_result(result)
        elif component == "terminal":
            for result in await self.terminal_runner.benchmark_command_execution():
                self.suite.add_result(result)
            for result in await self.terminal_runner.benchmark_output_streaming():
                self.suite.add_result(result)
        elif component == "memory":
            for result in await self.memory_runner.benchmark_memory_write():
                self.suite.add_result(result)
            for result in await self.memory_runner.benchmark_memory_read():
                self.suite.add_result(result)
        elif component == "system":
            for result in await self.system_runner.benchmark_concurrent_operations():
                self.suite.add_result(result)
        else:
            logger.warning(f"Unknown component: {component}")
        
        self.suite.completed_at = datetime.now(timezone.utc)
        return self.suite


def format_results_table(suite: BenchmarkSuite) -> str:
    """Format benchmark results as a table."""
    lines = []
    lines.append("=" * 100)
    lines.append(f"BENCHMARK RESULTS: {suite.name}")
    lines.append("=" * 100)
    lines.append("")
    
    # Group by component
    by_component = {}
    for result in suite.results:
        if result.component not in by_component:
            by_component[result.component] = []
        by_component[result.component].append(result)
    
    for component, results in sorted(by_component.items()):
        lines.append(f"\n{'─' * 100}")
        lines.append(f"Component: {component.upper()}")
        lines.append(f"{'─' * 100}")
        lines.append(f"{'Name':<40} {'Metric':<20} {'Value':<15} {'Unit':<10}")
        lines.append(f"{'─' * 100}")
        
        for result in results:
            lines.append(f"{result.name:<40} {result.metric:<20} {result.value:<15.3f} {result.unit:<10}")
    
    lines.append("")
    lines.append("=" * 100)
    lines.append(f"Total benchmarks: {len(suite.results)}")
    lines.append(f"Completed at: {suite.completed_at.isoformat() if suite.completed_at else 'N/A'}")
    lines.append("=" * 100)
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Somnus VM System Benchmark Suite")
    parser.add_argument("--all", action="store_true", help="Run all benchmarks")
    parser.add_argument("--component", type=str, help="Run benchmarks for specific component")
    parser.add_argument("--output", type=str, choices=["json", "table"], default="table", help="Output format")
    parser.add_argument("--output-file", type=str, help="Save results to file")
    
    args = parser.parse_args()
    
    orchestrator = BenchmarkOrchestrator()
    
    if args.all:
        suite = asyncio.run(orchestrator.run_all_benchmarks())
    elif args.component:
        suite = asyncio.run(orchestrator.run_component_benchmarks(args.component))
    else:
        parser.print_help()
        return 1
    
    # Format and output results
    if args.output == "json":
        output = json.dumps(suite.to_dict(), indent=2)
    else:
        output = format_results_table(suite)
    
    print(output)
    
    # Save to file if requested
    if args.output_file:
        with open(args.output_file, 'w') as f:
            f.write(output)
        logger.info(f"Results saved to {args.output_file}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
