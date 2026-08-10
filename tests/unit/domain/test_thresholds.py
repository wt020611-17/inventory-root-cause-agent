"""`AnalysisThresholds` 的默认值、边界和错误输入测试。

测试采用 Arrange（准备输入）→ Act（构造模型）→ Assert（检查结果）的思路。
Pydantic 校验失败统一表现为 `ValidationError`，调用方无需理解内部校验实现。
"""

import pytest
from pydantic import ValidationError

from app.domain.thresholds import AnalysisThresholds

# 把业务文档 v0.2 的八个默认值固定成可执行契约，避免实现与文档悄悄漂移。
EXPECTED_DEFAULTS = {
    "analysis_window_days": 90,
    "slow_moving_days": 90,
    "non_moving_days": 180,
    "coverage_days_threshold": 120,
    "demand_drop_ratio": 0.40,
    "purchase_excess_days": 120,
    "production_delay_days": 14,
    "new_material_protection_days": 30,
}

# 所有以“天”为单位的字段共享正数约束，参数化测试可避免复制七段相同代码。
DAY_FIELDS = (
    "analysis_window_days",
    "slow_moving_days",
    "non_moving_days",
    "coverage_days_threshold",
    "purchase_excess_days",
    "production_delay_days",
    "new_material_protection_days",
)


def test_defaults_match_accepted_mvp_v02_business_rules() -> None:
    """不传配置时，应得到业务文档已经接受的完整默认值。"""
    assert AnalysisThresholds().model_dump() == EXPECTED_DEFAULTS


def test_valid_overrides_are_applied() -> None:
    """调用方可以一次覆盖多个阈值，合法值应原样保留。"""
    thresholds = AnalysisThresholds(
        analysis_window_days=60,
        slow_moving_days=120,
        non_moving_days=240,
        coverage_days_threshold=150,
        demand_drop_ratio=0.25,
        purchase_excess_days=180,
        production_delay_days=21,
        new_material_protection_days=45,
    )

    assert thresholds.model_dump() == {
        "analysis_window_days": 60,
        "slow_moving_days": 120,
        "non_moving_days": 240,
        "coverage_days_threshold": 150,
        "demand_drop_ratio": 0.25,
        "purchase_excess_days": 180,
        "production_delay_days": 21,
        "new_material_protection_days": 45,
    }


@pytest.mark.parametrize("field_name", DAY_FIELDS)
@pytest.mark.parametrize("invalid_value", [0, -1])
def test_day_thresholds_must_be_positive(field_name: str, invalid_value: int) -> None:
    """七个天数字段分别测试 0 和负数，因此这一函数会展开为 14 个用例。"""
    with pytest.raises(ValidationError):
        AnalysisThresholds(**{field_name: invalid_value})


def test_non_moving_days_cannot_be_less_than_slow_moving_days() -> None:
    """无动是更严重的风险，配置上不能比慢动更早触发。"""
    with pytest.raises(ValidationError, match="non_moving_days"):
        AnalysisThresholds(slow_moving_days=181, non_moving_days=180)


def test_equal_slow_and_non_moving_thresholds_are_valid() -> None:
    """业务约束是大于等于，因此慢动与无动阈值相等属于合法边界。"""
    thresholds = AnalysisThresholds(slow_moving_days=90, non_moving_days=90)

    assert thresholds.slow_moving_days == thresholds.non_moving_days == 90


@pytest.mark.parametrize("boundary", [0.0, 1.0])
def test_demand_drop_ratio_inclusive_boundaries_are_valid(boundary: float) -> None:
    """需求下降比例采用闭区间，所以 0% 和 100% 都允许配置。"""
    assert AnalysisThresholds(demand_drop_ratio=boundary).demand_drop_ratio == boundary


@pytest.mark.parametrize("invalid_ratio", [-0.0001, 1.0001])
def test_demand_drop_ratio_outside_closed_unit_interval_is_rejected(
    invalid_ratio: float,
) -> None:
    """小于 0 或大于 1 的比例没有业务含义，必须拒绝。"""
    with pytest.raises(ValidationError):
        AnalysisThresholds(demand_drop_ratio=invalid_ratio)


def test_unknown_fields_are_rejected() -> None:
    """`extra='forbid'` 应把拼错或尚未支持的字段显式报错。"""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AnalysisThresholds(unknown_threshold=10)


def test_single_override_preserves_other_defaults() -> None:
    """只覆盖一个阈值时，其余七个字段仍保持默认值。"""
    thresholds = AnalysisThresholds(production_delay_days=30)

    expected = {**EXPECTED_DEFAULTS, "production_delay_days": 30}
    assert thresholds.model_dump() == expected


def test_serialization_contains_only_declared_fields() -> None:
    """序列化结果只能包含八个声明字段，确保外部配置快照稳定。"""
    assert set(AnalysisThresholds().model_dump()) == set(EXPECTED_DEFAULTS)
