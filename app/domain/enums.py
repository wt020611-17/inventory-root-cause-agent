"""项目受控枚举。

受控枚举把业务状态集中定义在一个位置，避免在不同模块中散落容易拼错的字符串。
`StrEnum` 同时具有字符串和枚举特性，既能被 JSON/Pydantic 稳定序列化，也能限制非法值。
"""

from enum import StrEnum


class MovementType(StrEnum):
    """库存移动类型，用于说明库存数量为什么增加或减少。"""

    # 采购收货会增加仓库库存。
    PURCHASE_RECEIPT = "PURCHASE_RECEIPT"

    # 销售出库与生产领料代表真实业务消耗，后续会参与消耗速度计算。
    SALES_ISSUE = "SALES_ISSUE"
    PRODUCTION_ISSUE = "PRODUCTION_ISSUE"

    # 调拨必须成对理解：调出仓库减少，调入仓库增加。
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"

    # 调整用于合成数据中的盘点或纠偏，不自动视为业务消耗。
    ADJUSTMENT = "ADJUSTMENT"


class PurchaseOrderStatus(StrEnum):
    """采购订单生命周期状态，用于判断订单是否仍可能继续到货。"""

    PLANNED = "PLANNED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class ProductionOrderStatus(StrEnum):
    """生产订单生命周期状态，用于识别计划是否启动、进行、关闭或取消。"""

    PLANNED = "PLANNED"
    RELEASED = "RELEASED"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class InventoryRiskLevel(StrEnum):
    """库存风险等级，由确定性规则计算，而不是由 LLM 自由判断。"""

    # 正常、慢动、无动按风险严重程度递进。
    NORMAL = "NORMAL"
    SLOW_MOVING = "SLOW_MOVING"
    NON_MOVING = "NON_MOVING"

    # 关键数据缺失或矛盾时阻断结论，避免基于坏数据误判。
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"


class RootCauseType(StrEnum):
    """MVP 支持的三类候选根因；命中只代表需要进一步核对。"""

    DEMAND_DROP = "DEMAND_DROP"
    PURCHASE_EXCESS = "PURCHASE_EXCESS"
    PRODUCTION_DELAY = "PRODUCTION_DELAY"


class ResultStatus(StrEnum):
    """所有服务和 API 共用的结果状态，重点区分无数据与执行失败。"""

    # ok：成功且有数据；empty：成功但没有匹配数据。
    OK = "ok"
    EMPTY = "empty"

    # error：执行失败；blocked：事实可保留，但因质量问题不能输出业务结论。
    ERROR = "error"
    BLOCKED = "blocked"


class EvidenceNodeType(StrEnum):
    """证据图只允许四类业务节点。"""

    MATERIAL = "MATERIAL"
    WAREHOUSE = "WAREHOUSE"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    PRODUCTION_ORDER = "PRODUCTION_ORDER"


class EvidenceRelationType(StrEnum):
    """从业务外键派生的三类受控关系。"""

    PURCHASES = "PURCHASES"
    CONSUMES = "CONSUMES"
    STORED_IN = "STORED_IN"
