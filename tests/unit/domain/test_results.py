"""领域结果模型的正常、空结果、阻断、错误和序列化测试。"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.enums import InventoryRiskLevel, ResultStatus, RootCauseType
from app.domain.results import (
    AnalysisResult,
    EvidenceItem,
    InventoryMetrics,
    MetricValue,
    ResultMetadata,
    RiskAssessment,
    RootCauseCandidate,
)


def make_metadata(status: ResultStatus) -> ResultMetadata:
    """为测试创建固定日期和追踪号，保证序列化结果可复现。"""
    return ResultMetadata(
        status=status,
        trace_id="trace-syn-001",
        as_of_date=date(2026, 3, 31),
    )


def make_metrics(*, cost_complete: bool = True) -> InventoryMetrics:
    """创建一组可复用的物料仓库指标。"""
    return InventoryMetrics(
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        current_stock=MetricValue(value="120", unit="unit", complete=True),
        days_without_consumption=MetricValue(value="95", unit="day", complete=True),
        average_daily_consumption=MetricValue(
            value="1.5",
            unit="unit/day",
            observation_window_days=90,
            complete=True,
        ),
        coverage_days=MetricValue(value="80", unit="day", complete=True),
        stagnant_amount=MetricValue(
            value="1200" if cost_complete else None,
            unit="currency",
            complete=cost_complete,
        ),
        infinite_coverage=False,
        partial_window=False,
    )


def make_risk() -> RiskAssessment:
    """创建一个由确定性规则得出的慢动风险判断。"""
    return RiskAssessment(
        risk_level=InventoryRiskLevel.SLOW_MOVING,
        conclusion_allowed=True,
        matched_rules=["days_without_consumption", "coverage_days"],
    )


def make_evidence() -> EvidenceItem:
    """创建不包含真实企业标识的合成证据。"""
    return EvidenceItem(
        evidence_id="EVI-SYN-001",
        source_type="purchase_order",
        source_id="PO-SYN-001",
        summary="合成采购单形成较高库存覆盖",
    )


def test_ok_result_serializes_metrics_risk_causes_and_evidence() -> None:
    """成功结果应完整保留确定性指标、风险、候选根因和证据。"""
    evidence = make_evidence()
    cause = RootCauseCandidate(
        cause_type=RootCauseType.PURCHASE_EXCESS,
        score="0.80",
        hits=["purchase_coverage_exceeds_threshold"],
        evidence=[evidence],
        insufficient_evidence=False,
    )
    result = AnalysisResult(
        metadata=make_metadata(ResultStatus.OK),
        message="分析完成",
        metrics=make_metrics(),
        risk=make_risk(),
        root_causes=[cause],
        evidence=[evidence],
    )

    dumped = result.model_dump(mode="json")

    assert dumped["metadata"]["status"] == "ok"
    assert dumped["metadata"]["trace_id"] == "trace-syn-001"
    assert dumped["metrics"]["current_stock"]["value"] == "120"
    assert dumped["risk"]["risk_level"] == "SLOW_MOVING"
    assert dumped["root_causes"][0]["cause_type"] == "PURCHASE_EXCESS"


def test_empty_result_does_not_fake_business_facts() -> None:
    """无匹配数据是成功执行后的空结果，不能伪造成错误或业务结论。"""
    result = AnalysisResult(
        metadata=make_metadata(ResultStatus.EMPTY),
        message="未找到匹配的物料仓库记录",
    )

    assert result.metadata.status is ResultStatus.EMPTY
    assert result.metrics is None
    assert result.risk is None
    assert result.root_causes == []
    assert result.errors == []


def test_blocked_result_retains_metrics_but_rejects_root_cause_conclusion() -> None:
    """质量阻断可以保留已计算事实，但不能继续输出候选根因。"""
    result = AnalysisResult(
        metadata=make_metadata(ResultStatus.BLOCKED),
        message="负库存阻止风险结论",
        metrics=make_metrics(),
        risk=RiskAssessment(
            risk_level=InventoryRiskLevel.DATA_QUALITY_BLOCKED,
            conclusion_allowed=False,
            matched_rules=[],
        ),
        blockers=["negative_current_stock"],
    )

    assert result.metrics is not None
    assert result.risk.risk_level is InventoryRiskLevel.DATA_QUALITY_BLOCKED
    assert result.root_causes == []
    assert result.blockers == ["negative_current_stock"]


def test_error_result_contains_error_summary_without_business_conclusion() -> None:
    """执行失败与无数据不同，只返回错误摘要和 Trace ID。"""
    result = AnalysisResult(
        metadata=make_metadata(ResultStatus.ERROR),
        message="库存查询失败",
        errors=["repository_unavailable"],
    )

    assert result.metadata.status is ResultStatus.ERROR
    assert result.errors == ["repository_unavailable"]
    assert result.metrics is None
    assert result.risk is None


def test_missing_unit_cost_preserves_quantity_and_marks_amount_incomplete() -> None:
    """成本缺失不能抹掉数量结论，但呆滞金额必须明确标记不完整。"""
    metrics = make_metrics(cost_complete=False)

    assert metrics.current_stock.value == Decimal("120")
    assert metrics.stagnant_amount.value is None
    assert metrics.stagnant_amount.complete is False


def test_complete_metric_may_use_null_when_business_value_is_undefined() -> None:
    """事实齐全但数学值未定义时可使用 null，例如零消耗下的覆盖天数。"""
    metric = MetricValue(value=None, unit="day", complete=True)

    assert metric.value is None
    assert metric.complete is True


@pytest.mark.parametrize(
    ("status", "extra_fields"),
    [
        (ResultStatus.OK, {}),
        (ResultStatus.EMPTY, {"metrics": make_metrics()}),
        (ResultStatus.BLOCKED, {"blockers": []}),
        (ResultStatus.ERROR, {"errors": []}),
    ],
)
def test_result_rejects_incomplete_or_contradictory_status_payloads(
    status: ResultStatus,
    extra_fields: dict,
) -> None:
    """每种状态必须携带自己需要的数据，且不能混入相互矛盾的事实。"""
    with pytest.raises(ValidationError):
        AnalysisResult(
            metadata=make_metadata(status),
            message="非法组合",
            **extra_fields,
        )


def test_root_cause_score_must_be_between_zero_and_one() -> None:
    """候选根因分数用于稳定排序，必须限制在闭区间零到一。"""
    with pytest.raises(ValidationError):
        RootCauseCandidate(
            cause_type=RootCauseType.DEMAND_DROP,
            score=Decimal("1.01"),
            hits=["demand_drop"],
            evidence=[],
            insufficient_evidence=True,
        )


@pytest.mark.parametrize(
    "model_type, valid_data",
    [
        (
            ResultMetadata,
            {
                "status": ResultStatus.EMPTY,
                "trace_id": "trace-syn-001",
                "as_of_date": date(2026, 3, 31),
            },
        ),
        (MetricValue, {"value": Decimal("1"), "unit": "unit", "complete": True}),
        (
            EvidenceItem,
            {
                "evidence_id": "EVI-SYN-001",
                "source_type": "movement",
                "source_id": "MOV-SYN-001",
                "summary": "合成证据",
            },
        ),
    ],
)
def test_result_models_reject_unknown_fields(model_type: type, valid_data: dict) -> None:
    """结果模型和输入模型一样拒绝拼错或未声明字段。"""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model_type(**valid_data, unknown_field="unexpected")
