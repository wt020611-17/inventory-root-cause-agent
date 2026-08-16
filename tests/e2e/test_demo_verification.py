"""自动演示验收必须覆盖正常、多根因、证据路径和空结果。"""

from scripts.run_demo_verification import run_demo_verification


def test_demo_verification_covers_required_scenarios_without_llm() -> None:
    report = run_demo_verification()
    cases = {item["case_id"]: item for item in report["cases"]}

    assert report["summary"] == {
        "case_count": 4,
        "passed_count": 4,
        "failed_count": 0,
        "all_passed": True,
    }
    assert cases["normal_material"]["status"] == "ok"
    assert cases["normal_material"]["root_causes"] == []
    assert cases["multi_cause_material"]["root_causes"][:2] == [
        "PURCHASE_EXCESS",
        "PRODUCTION_DELAY",
    ]
    assert cases["multi_cause_material"]["evidence_count"] >= 2
    assert cases["multi_cause_evidence"]["path_count"] >= 1
    assert cases["empty_material"]["status"] == "empty"
    assert all(case["llm_used"] is False for case in cases.values())
