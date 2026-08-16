"""Agent 评测集覆盖范围与运行器回归测试。"""

from evals.runners.agent_eval import evaluate_dataset, load_dataset


def test_agent_eval_dataset_has_required_coverage() -> None:
    dataset = load_dataset()
    tags = {tag for case in dataset.cases for tag in case.tags}

    assert len(dataset.cases) >= 12
    assert {
        "normal",
        "demand_drop",
        "purchase_excess",
        "production_delay",
        "multi_cause",
        "missing_parameters",
        "invalid_parameters",
        "two_turn",
        "session_expiry",
        "empty",
        "blocked",
        "dependency_failure",
        "no_llm",
    } <= tags


def test_agent_eval_baseline_is_fully_reproducible() -> None:
    report = evaluate_dataset(load_dataset())

    assert report.passed is True
    assert all(metric.rate == 1 for metric in report.metrics.values())
    assert all(outcome.passed for outcome in report.cases)
