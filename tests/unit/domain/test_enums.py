"""领域枚举的契约测试。

这里不测试复杂算法，而是锁定枚举名称和值。以后若误删、改名或拼错状态，测试会立即失败，
避免数据库、API 和业务规则使用互不兼容的字符串。
"""

from enum import Enum

import pytest

from app.domain.enums import (
    InventoryRiskLevel,
    MovementType,
    ProductionOrderStatus,
    PurchaseOrderStatus,
    ResultStatus,
    RootCauseType,
)

# 这张“期望值表”是文档契约的可执行版本，也被下方参数化测试重复使用。
EXPECTED_ENUM_VALUES = {
    MovementType: {
        "PURCHASE_RECEIPT": "PURCHASE_RECEIPT",
        "SALES_ISSUE": "SALES_ISSUE",
        "PRODUCTION_ISSUE": "PRODUCTION_ISSUE",
        "TRANSFER_IN": "TRANSFER_IN",
        "TRANSFER_OUT": "TRANSFER_OUT",
        "ADJUSTMENT": "ADJUSTMENT",
    },
    PurchaseOrderStatus: {
        "PLANNED": "PLANNED",
        "PARTIALLY_RECEIVED": "PARTIALLY_RECEIVED",
        "RECEIVED": "RECEIVED",
        "CANCELLED": "CANCELLED",
    },
    ProductionOrderStatus: {
        "PLANNED": "PLANNED",
        "RELEASED": "RELEASED",
        "IN_PROGRESS": "IN_PROGRESS",
        "CLOSED": "CLOSED",
        "CANCELLED": "CANCELLED",
    },
    InventoryRiskLevel: {
        "NORMAL": "NORMAL",
        "SLOW_MOVING": "SLOW_MOVING",
        "NON_MOVING": "NON_MOVING",
        "DATA_QUALITY_BLOCKED": "DATA_QUALITY_BLOCKED",
    },
    RootCauseType: {
        "DEMAND_DROP": "DEMAND_DROP",
        "PURCHASE_EXCESS": "PURCHASE_EXCESS",
        "PRODUCTION_DELAY": "PRODUCTION_DELAY",
    },
    ResultStatus: {
        "OK": "ok",
        "EMPTY": "empty",
        "ERROR": "error",
        "BLOCKED": "blocked",
    },
}


@pytest.mark.parametrize(("enum_type", "expected"), EXPECTED_ENUM_VALUES.items())
def test_enum_values_are_stable(
    enum_type: type[Enum], expected: dict[str, str]
) -> None:
    """每组枚举都必须保留字符串能力、枚举约束和固定的名称到值映射。"""
    assert issubclass(enum_type, str)
    assert issubclass(enum_type, Enum)
    assert {member.name: member.value for member in enum_type} == expected


@pytest.mark.parametrize(("enum_type", "expected"), EXPECTED_ENUM_VALUES.items())
def test_every_documented_value_can_construct_enum(
    enum_type: type[Enum], expected: dict[str, str]
) -> None:
    """文档中声明的每个合法字符串都能成功构造对应枚举。"""
    assert [enum_type(value).value for value in expected.values()] == list(expected.values())


@pytest.mark.parametrize("enum_type", EXPECTED_ENUM_VALUES)
def test_unknown_enum_value_is_rejected(enum_type: type[Enum]) -> None:
    """所有枚举都拒绝未声明状态，防止错误字符串静默流入业务逻辑。"""
    with pytest.raises(ValueError):
        enum_type("UNKNOWN")
