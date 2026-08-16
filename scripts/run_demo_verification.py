"""自动执行 Phase 4 演示场景并保存结构化验收报告。"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.agent import AgentParameters, AgentRequest, AnalysisIntent
from app.ui.runtime import create_ui_runtime, invoke_ui_agent

SEED = 20260812
AS_OF_DATE = date(2026, 3, 31)
WAREHOUSE_ID = "WH-SYN-01"

DEMO_CASES = (
    {
        "case_id": "normal_material",
        "title": "正常物料",
        "material_id": "MAT-SYN-NORMAL",
        "intent": AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
        "expected_status": "ok",
        "expected_root_causes": [],
    },
    {
        "case_id": "multi_cause_material",
        "title": "多根因物料",
        "material_id": "MAT-SYN-MULTI",
        "intent": AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
        "expected_status": "ok",
        "expected_root_causes": ["PURCHASE_EXCESS", "PRODUCTION_DELAY"],
    },
    {
        "case_id": "multi_cause_evidence",
        "title": "多根因证据路径",
        "material_id": "MAT-SYN-MULTI",
        "intent": AnalysisIntent.TRACE_EVIDENCE,
        "expected_status": "ok",
        "minimum_path_count": 1,
    },
    {
        "case_id": "empty_material",
        "title": "空结果与无 LLM 降级",
        "material_id": "MAT-SYN-EMPTY",
        "intent": AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
        "expected_status": "empty",
        "expected_root_causes": [],
    },
)


def _metric_values(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    names = (
        "current_stock",
        "days_without_consumption",
        "coverage_days",
        "stagnant_amount",
    )
    return {
        name: metrics[name].get("value")
        for name in names
        if isinstance(metrics.get(name), dict)
    }


def _run_case(runtime: Any, definition: dict[str, Any]) -> dict[str, Any]:
    material_id = definition["material_id"]
    intent = definition["intent"]
    question = (
        f"分析 {material_id} 在 {WAREHOUSE_ID} 截至 {AS_OF_DATE.isoformat()} 的根因"
        if intent is AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE
        else f"追溯 {material_id} 在 {WAREHOUSE_ID} 截至 {AS_OF_DATE.isoformat()} 的证据路径"
    )
    response = invoke_ui_agent(
        runtime,
        AgentRequest(
            question=question,
            confirmed_intent=intent,
            parameters=AgentParameters(
                material_id=material_id,
                warehouse_id=WAREHOUSE_ID,
                as_of_date=AS_OF_DATE,
            ),
            session_id=f"demo-{definition['case_id']}",
            trace_id=f"demo-{definition['case_id']}",
        ),
        disable_llm=True,
    )
    result = response.result.model_dump(mode="json") if response.result else {}
    candidates = result.get("root_causes", [])
    root_causes = [
        item["cause_type"]
        for item in candidates
        if not item.get("insufficient_evidence", False)
    ]
    path_count = len(result.get("paths", []))
    evidence_ids = [item.get("evidence_id") for item in response.evidence]

    checks = {
        "status": response.status.value == definition["expected_status"],
        "llm_disabled": response.llm_used is False,
        "selected_tool": response.selected_tool == intent.value,
    }
    if "expected_root_causes" in definition:
        checks["root_causes"] = root_causes == definition["expected_root_causes"]
    if "minimum_path_count" in definition:
        checks["evidence_paths"] = path_count >= definition["minimum_path_count"]

    risk = result.get("risk") or {}
    return {
        "case_id": definition["case_id"],
        "title": definition["title"],
        "question": question,
        "status": response.status.value,
        "selected_tool": response.selected_tool,
        "llm_used": response.llm_used,
        "message": response.message,
        "metrics": _metric_values(result),
        "risk_level": risk.get("risk_level"),
        "root_causes": root_causes,
        "all_candidates": [
            {
                "cause_type": item["cause_type"],
                "score": item["score"],
                "insufficient_evidence": item.get("insufficient_evidence", False),
            }
            for item in candidates
        ],
        "evidence_ids": evidence_ids,
        "evidence_count": len(response.evidence),
        "path_count": path_count,
        "action_summaries": response.action_summaries,
        "checks": checks,
        "passed": all(checks.values()),
    }


def run_demo_verification(*, seed: int = SEED) -> dict[str, Any]:
    """在强制无 LLM 模式下执行全部固定演示场景。"""
    runtime = create_ui_runtime(seed=seed)
    try:
        cases = [_run_case(runtime, definition) for definition in DEMO_CASES]
    finally:
        runtime.close()
    passed_count = sum(case["passed"] for case in cases)
    return {
        "schema_version": "1.0",
        "verified_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "warehouse_id": WAREHOUSE_ID,
        "as_of_date": AS_OF_DATE.isoformat(),
        "execution_mode": "forced_no_llm",
        "summary": {
            "case_count": len(cases),
            "passed_count": passed_count,
            "failed_count": len(cases) - passed_count,
            "all_passed": passed_count == len(cases),
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic demo verification")
    parser.add_argument(
        "--output",
        default="reports/demo_verification.json",
        help="JSON report path",
    )
    args = parser.parse_args()

    report = run_demo_verification()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for case in report["cases"]:
        print(
            f"{case['case_id']}: status={case['status']} "
            f"llm_used={case['llm_used']} roots={case['root_causes']} "
            f"evidence={case['evidence_count']} paths={case['path_count']} "
            f"passed={case['passed']}"
        )
    print(
        f"demo_verification={report['summary']['all_passed']} "
        f"passed={report['summary']['passed_count']}/{report['summary']['case_count']} "
        f"output={output_path.as_posix()}"
    )
    if not report["summary"]["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
