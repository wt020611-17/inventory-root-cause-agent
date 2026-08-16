"""测量固定 seed 本地数据上的结构化分析、证据图和无 LLM Agent 延迟。"""

import argparse
import json
import os
import platform
import statistics
import sys
from datetime import UTC, date, datetime
from itertools import count
from math import ceil
from pathlib import Path
from time import perf_counter_ns

from pydantic import BaseModel, ConfigDict, Field

from app.agent import (
    AgentParameters,
    AgentRequest,
    AgentSettings,
    AnalysisIntent,
    DisabledAgentLLM,
    InMemorySessionStore,
    invoke_agent,
)
from app.domain.enums import ResultStatus
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.services.evidence_graph import EvidenceGraphService
from app.synthetic.generator import generate_synthetic_dataset
from app.tools import (
    AnalyzeMaterialRootCauseInput,
    InventoryAgentTools,
    TraceEvidenceInput,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "benchmarks" / "results" / "performance_baseline.json"
SEED = 20260812
AS_OF_DATE = date(2026, 3, 31)
TARGET_MAX_MS = 2000.0


class _BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LatencyStats(_BenchmarkModel):
    runs: int = Field(gt=0)
    min_ms: float = Field(ge=0)
    median_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)
    mean_ms: float = Field(ge=0)


class PerformanceReport(_BenchmarkModel):
    benchmark_version: str
    measured_at: datetime
    passed: bool
    seed: int
    as_of_date: date
    warmup_runs: int
    measured_runs: int
    target_max_ms: float
    metrics: dict[str, LatencyStats]
    target_checks: dict[str, bool]
    reliability_checks: dict[str, bool]
    graph_limits: dict[str, int | bool]
    dataset_counts: dict[str, int]
    environment: dict[str, str | int]
    methodology: list[str]
    diagnostic: dict[str, str | float]


def calculate_latency_stats(samples_ms: list[float]) -> LatencyStats:
    """使用最近秩计算 P95，并保留真实测量值到微秒精度。"""
    if not samples_ms:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(samples_ms)
    p95_index = max(0, ceil(len(ordered) * 0.95) - 1)
    return LatencyStats(
        runs=len(ordered),
        min_ms=round(ordered[0], 3),
        median_ms=round(statistics.median(ordered), 3),
        p95_ms=round(ordered[p95_index], 3),
        max_ms=round(ordered[-1], 3),
        mean_ms=round(statistics.fmean(ordered), 3),
    )


def run_benchmark(*, warmup_runs: int = 5, measured_runs: int = 30) -> PerformanceReport:
    """初始化一次固定数据集，预热后连续测量三个本地链路。"""
    if warmup_runs < 0 or measured_runs < 1:
        raise ValueError("warmup_runs must be non-negative and measured_runs must be positive")
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    settings = AgentSettings(_env_file=None).model_copy(update={"session_max_turns": 50})
    sessions = InMemorySessionStore(
        ttl_seconds=settings.session_ttl_seconds,
        max_turns=settings.session_max_turns,
    )
    sequence = count()
    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        repository.add_dataset(
            generate_synthetic_dataset(
                seed=SEED,
                generated_at=datetime(2026, 8, 12, tzinfo=UTC),
            )
        )
        counts = repository.count_records()
        tools = InventoryAgentTools(repository)
        graph_service = EvidenceGraphService(repository)

        def structured_analysis():
            return tools.analyze_material_root_cause(
                AnalyzeMaterialRootCauseInput(
                    material_id="MAT-SYN-MULTI",
                    warehouse_id="WH-SYN-01",
                    as_of_date=AS_OF_DATE,
                    trace_id="benchmark-structured",
                )
            )

        def graph_trace():
            return tools.trace_evidence(
                TraceEvidenceInput(
                    material_id="MAT-SYN-MULTI",
                    warehouse_id="WH-SYN-01",
                    as_of_date=AS_OF_DATE,
                    trace_id="benchmark-graph",
                )
            )

        def agent_no_llm():
            index = next(sequence)
            return invoke_agent(
                AgentRequest(
                    question="分析该物料的库存根因",
                    confirmed_intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
                    parameters=AgentParameters(
                        material_id="MAT-SYN-MULTI",
                        warehouse_id="WH-SYN-01",
                        as_of_date=AS_OF_DATE,
                    ),
                    session_id=f"benchmark-{index}",
                    trace_id=f"benchmark-agent-{index}",
                ),
                tools=tools,
                llm=DisabledAgentLLM(),
                sessions=sessions,
                settings=settings,
            )

        callables = {
            "structured_analysis": structured_analysis,
            "evidence_graph": graph_trace,
            "agent_no_llm": agent_no_llm,
        }
        measurements: dict[str, list[float]] = {}
        fingerprints: dict[str, list[str]] = {}
        for name, operation in callables.items():
            for _ in range(warmup_runs):
                operation()
            samples: list[float] = []
            results: list[str] = []
            for _ in range(measured_runs):
                started = perf_counter_ns()
                result = operation()
                samples.append((perf_counter_ns() - started) / 1_000_000)
                results.append(_result_fingerprint(result))
            measurements[name] = samples
            fingerprints[name] = results

        graph_limits = _verify_graph_limits(graph_service)

    engine.dispose()
    metrics = {name: calculate_latency_stats(values) for name, values in measurements.items()}
    target_checks = {
        "structured_analysis_max_under_2000ms": (
            metrics["structured_analysis"].max_ms < TARGET_MAX_MS
        ),
        "evidence_graph_max_under_2000ms": metrics["evidence_graph"].max_ms < TARGET_MAX_MS,
        "agent_no_llm_max_under_2000ms": metrics["agent_no_llm"].max_ms < TARGET_MAX_MS,
    }
    reliability_checks = {
        name + "_stable": len(set(values)) == 1 for name, values in fingerprints.items()
    }
    reliability_checks["all_graph_limits_enforced"] = all(
        value is True for key, value in graph_limits.items() if key.endswith("_enforced")
    )
    slowest_name, slowest_stats = max(metrics.items(), key=lambda item: item[1].p95_ms)
    return PerformanceReport(
        benchmark_version="performance-baseline-v1",
        measured_at=datetime.now(UTC),
        passed=all(target_checks.values()) and all(reliability_checks.values()),
        seed=SEED,
        as_of_date=AS_OF_DATE,
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
        target_max_ms=TARGET_MAX_MS,
        metrics=metrics,
        target_checks=target_checks,
        reliability_checks=reliability_checks,
        graph_limits=graph_limits,
        dataset_counts=counts,
        environment=_environment(),
        methodology=[
            "SQLite in-memory StaticPool；初始化与合成数据写入不计入单次延迟。",
            "每条链路先预热，再使用 perf_counter_ns 连续串行测量。",
            "结构化分析固定为 MAT-SYN-MULTI × WH-SYN-01 × 2026-03-31。",
            "Agent 链路强制禁用 LLM，避免网络波动混入本地基线。",
            "目标使用最严格的实测最大值小于 2000ms，不使用虚构提升百分比。",
        ],
        diagnostic={
            "slowest_component_by_p95": slowest_name,
            "slowest_p95_ms": slowest_stats.p95_ms,
            "target_action": (
                "无需优化；所有本地链路达到目标。"
                if all(target_checks.values())
                else "根据三个分层指标定位数据库/图查询/Agent 编排瓶颈。"
            ),
        },
    )


def _measure_fingerprint_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
        if "trace_id" in payload:
            payload["trace_id"] = "<normalized>"
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and "trace_id" in metadata:
            metadata["trace_id"] = "<normalized>"
        result = payload.get("result")
        if isinstance(result, dict):
            result_metadata = result.get("metadata")
            if isinstance(result_metadata, dict) and "trace_id" in result_metadata:
                result_metadata["trace_id"] = "<normalized>"
        payload.pop("session_id", None)
        return payload
    return value


def _result_fingerprint(value: object) -> str:
    return json.dumps(
        _measure_fingerprint_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _verify_graph_limits(service: EvidenceGraphService) -> dict[str, int | bool]:
    common = {
        "material_id": "MAT-SYN-MULTI",
        "warehouse_id": "WH-SYN-01",
        "as_of_date": AS_OF_DATE,
        "trace_id": "benchmark-graph-limit",
    }
    hop_limited = service.trace(**common, max_hops=1)
    node_limited = service.trace(**common, max_nodes=2)
    timeout_limited = service.trace(**common, timeout_ms=-1)
    return {
        "configured_max_hops": 2,
        "configured_max_nodes": 50,
        "configured_timeout_ms": 100,
        "max_hops_enforced": bool(
            hop_limited.metadata.status is ResultStatus.OK
            and all(len(path.node_ids) - 1 <= 1 for path in hop_limited.paths)
        ),
        "max_nodes_enforced": bool(
            node_limited.metadata.status is ResultStatus.BLOCKED
            and node_limited.blockers == ["graph_node_limit_exceeded"]
        ),
        "timeout_enforced": bool(
            timeout_limited.metadata.status is ResultStatus.BLOCKED
            and timeout_limited.blockers == ["graph_query_timeout"]
        ),
    }


def _environment() -> dict[str, str | int]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine() or "unavailable",
        "processor": platform.processor() or "unavailable",
        "logical_cpu_count": os.cpu_count() or 0,
        "database": "SQLite in-memory StaticPool",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmup-runs", type=int, default=5)
    parser.add_argument("--measured-runs", type=int, default=30)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_benchmark(
        warmup_runs=args.warmup_runs,
        measured_runs=args.measured_runs,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"benchmark={report.benchmark_version} passed={report.passed}")
    for name, stats in report.metrics.items():
        print(
            f"{name}: median={stats.median_ms:.3f}ms "
            f"p95={stats.p95_ms:.3f}ms max={stats.max_ms:.3f}ms"
        )
    print(f"report={args.output}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
