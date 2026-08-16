"""性能运行器的统计、限制和可复现性回归测试。"""

import pytest

from benchmarks.run_performance import calculate_latency_stats, run_benchmark


def test_latency_stats_use_nearest_rank_p95() -> None:
    stats = calculate_latency_stats([float(value) for value in range(1, 21)])

    assert stats.min_ms == 1
    assert stats.median_ms == 10.5
    assert stats.p95_ms == 19
    assert stats.max_ms == 20


def test_latency_stats_reject_empty_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        calculate_latency_stats([])


def test_fixed_local_benchmark_meets_target_and_reliability_contract() -> None:
    report = run_benchmark(warmup_runs=1, measured_runs=3)

    assert report.passed is True
    assert report.seed == 20260812
    assert report.dataset_counts == {
        "materials": 18,
        "warehouses": 2,
        "movements": 360,
        "purchase_orders": 24,
        "production_orders": 18,
    }
    assert set(report.metrics) == {
        "structured_analysis",
        "evidence_graph",
        "agent_no_llm",
    }
    assert all(report.target_checks.values())
    assert all(report.reliability_checks.values())
    assert report.graph_limits["max_hops_enforced"] is True
    assert report.graph_limits["max_nodes_enforced"] is True
    assert report.graph_limits["timeout_enforced"] is True
