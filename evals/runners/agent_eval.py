"""运行固定 seed 的 Phase 4 Agent 离线评测并保存 JSON 证据。"""

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.agent import (
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    AgentSettings,
    DisabledAgentLLM,
    InMemorySessionStore,
    invoke_agent,
)
from app.domain.results import AnalysisResult, EvidenceGraphResult, RiskListResult
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.synthetic.generator import generate_synthetic_dataset
from app.tools import InventoryAgentTools
from evals.models import (
    AgentEvalCase,
    AgentEvalDataset,
    AgentEvalReport,
    CaseOutcome,
    MetricResult,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "agent_eval_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "evals" / "results" / "agent_eval_v1_baseline.json"


class MutableClock:
    """让会话过期用例不依赖真实等待。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class BrokenRepository:
    """仅用于验证依赖失败不会伪装为空结果或泄露内部异常。"""

    def get_material(self, material_id: str):
        del material_id
        raise RuntimeError("secret database stack")


def load_dataset(path: Path = DEFAULT_DATASET) -> AgentEvalDataset:
    """读取并严格校验版本化 JSON 数据集。"""
    return AgentEvalDataset.model_validate_json(path.read_text(encoding="utf-8"))


def evaluate_dataset(dataset: AgentEvalDataset) -> AgentEvalReport:
    """在固定合成数据、禁用 LLM 的环境中运行全部评测样例。"""
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    outcomes: list[CaseOutcome] = []
    settings = AgentSettings(_env_file=None)

    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        repository.add_dataset(
            generate_synthetic_dataset(
                seed=dataset.seed,
                generated_at=dataset.generated_at,
            )
        )
        standard_tools = InventoryAgentTools(repository)
        for case in dataset.cases:
            outcomes.append(_evaluate_case(case, standard_tools, settings))

    engine.dispose()
    metrics = {
        "tool_selection_accuracy": _metric(outcomes, "tool_selection_pass"),
        "parameter_extraction_accuracy": _metric(outcomes, "parameter_extraction_pass"),
        "task_completion_rate": _metric(outcomes, "task_completion_pass"),
        "evidence_citation_completeness": _metric(outcomes, "evidence_completeness_pass"),
        "safe_degradation_pass_rate": _metric(outcomes, "safe_degradation_pass"),
    }
    return AgentEvalReport(
        dataset_version=dataset.dataset_version,
        evaluated_at=datetime.now(UTC),
        passed=all(outcome.passed for outcome in outcomes),
        metrics=metrics,
        environment=_environment_versions(),
        cases=outcomes,
    )


def _evaluate_case(
    case: AgentEvalCase,
    standard_tools: InventoryAgentTools,
    settings: AgentSettings,
) -> CaseOutcome:
    clock = MutableClock(datetime(2026, 8, 16, tzinfo=UTC))
    sessions = InMemorySessionStore(
        ttl_seconds=settings.session_ttl_seconds,
        max_turns=settings.session_max_turns,
        clock=clock,
    )
    tools = (
        InventoryAgentTools(BrokenRepository())
        if case.scenario == "dependency_failure"
        else standard_tools
    )
    response: AgentResponse | None = None
    for turn in case.turns:
        clock.advance(turn.advance_clock_seconds)
        response = invoke_agent(
            AgentRequest(
                question=turn.question,
                parameters=turn.parameters,
                confirmed_intent=turn.confirmed_intent,
                session_id=f"eval-{case.case_id}",
                trace_id=f"eval-{case.case_id}",
            ),
            tools=tools,
            llm=DisabledAgentLLM(),
            sessions=sessions,
            settings=settings,
        )
    if response is None:
        raise RuntimeError(f"case {case.case_id} did not execute a turn")

    failures: list[str] = []
    tool_pass = _tool_selection_pass(case, response)
    parameter_pass = _parameter_extraction_pass(case, response)
    task_pass = _task_completion_pass(case, response, failures)
    evidence_pass = _evidence_completeness_pass(case, response)
    safety_pass = _safe_degradation_pass(case, response)
    _append_dimension_failure(failures, "tool selection", tool_pass)
    _append_dimension_failure(failures, "parameter extraction", parameter_pass)
    _append_dimension_failure(failures, "evidence completeness", evidence_pass)
    _append_dimension_failure(failures, "safe degradation", safety_pass)

    return CaseOutcome(
        case_id=case.case_id,
        passed=not failures,
        actual_status=response.status,
        actual_intent=response.intent,
        actual_selected_tool=response.selected_tool,
        tool_selection_pass=tool_pass,
        parameter_extraction_pass=parameter_pass,
        task_completion_pass=task_pass,
        evidence_completeness_pass=evidence_pass,
        safe_degradation_pass=safety_pass,
        failures=failures,
    )


def _tool_selection_pass(case: AgentEvalCase, response: AgentResponse) -> bool | None:
    expected = case.expected.selected_tool
    return None if expected is None else response.selected_tool == expected


def _parameter_extraction_pass(case: AgentEvalCase, response: AgentResponse) -> bool:
    actual = response.parameters.model_dump(mode="json")
    return all(
        actual[field] == expected
        for field, expected in case.expected.parameter_fields.items()
    )


def _task_completion_pass(
    case: AgentEvalCase,
    response: AgentResponse,
    failures: list[str],
) -> bool:
    expected = case.expected
    checks = [response.status is expected.status, response.intent is expected.intent]
    if expected.missing_fields is not None:
        checks.append(response.missing_fields == expected.missing_fields)
    if expected.root_causes is not None:
        actual_causes = []
        if isinstance(response.result, AnalysisResult):
            actual_causes = [
                candidate.cause_type
                for candidate in response.result.root_causes
                if not candidate.insufficient_evidence
            ]
        checks.append(actual_causes == expected.root_causes)
    if not all(checks):
        failures.append("task completion expectation mismatch")
    return all(checks)


def _evidence_completeness_pass(
    case: AgentEvalCase,
    response: AgentResponse,
) -> bool | None:
    if not case.expected.evidence_required:
        return None
    if not response.evidence or response.result is None:
        return False
    if isinstance(response.result, AnalysisResult):
        return all(
            candidate.insufficient_evidence or bool(candidate.evidence)
            for candidate in response.result.root_causes
        )
    if isinstance(response.result, RiskListResult):
        return all(item.evidence for item in response.result.items)
    if isinstance(response.result, EvidenceGraphResult):
        return bool(response.result.paths)
    return False


def _safe_degradation_pass(case: AgentEvalCase, response: AgentResponse) -> bool | None:
    if not case.safety_case:
        return None
    serialized = response.model_dump_json().lower()
    checks = [
        response.status is case.expected.status,
        response.llm_used is case.expected.llm_used,
        "secret database stack" not in serialized,
        "chain_of_thought" not in serialized,
    ]
    if "blocked" in case.tags:
        checks.append(
            not isinstance(response.result, AnalysisResult) or not response.result.root_causes
        )
    if "no_llm" in case.tags and response.status is AgentResponseStatus.OK:
        checks.append("template_summary_selected" in response.action_summaries)
    return all(checks)


def _append_dimension_failure(
    failures: list[str],
    label: str,
    passed: bool | None,
) -> None:
    if passed is False:
        failures.append(f"{label} failed")


def _metric(outcomes: list[CaseOutcome], field: str) -> MetricResult:
    values = [getattr(outcome, field) for outcome in outcomes]
    applicable = [value for value in values if value is not None]
    passed = sum(value is True for value in applicable)
    total = len(applicable)
    return MetricResult(passed=passed, total=total, rate=passed / total if total else 0)


def _environment_versions() -> dict[str, str]:
    packages = ["pydantic", "sqlalchemy", "langgraph"]
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": _git_commit(),
    }
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not-installed"
    return result


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()
    try:
        head = (PROJECT_ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            return (PROJECT_ROOT / ".git" / head.removeprefix("ref: ")).read_text(
                encoding="utf-8"
            ).strip()
        return head
    except OSError:
        return "unavailable"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset = load_dataset(args.dataset)
    report = evaluate_dataset(dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"dataset={report.dataset_version} cases={len(report.cases)} passed={report.passed}")
    for name, metric in report.metrics.items():
        print(f"{name}={metric.passed}/{metric.total} ({metric.rate:.2%})")
    print(f"report={args.output}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
