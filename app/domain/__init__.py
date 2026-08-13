"""领域层公共入口：统一导出实体、枚举与分析阈值。

调用方可以从 `app.domain` 导入稳定的领域类型，而不需要了解它们分别存放在哪个文件。
本层只依赖 Python 标准库和 Pydantic，不依赖 API、数据库或 Agent 框架。
"""

from app.domain.entities import (
    InventoryMovement,
    Material,
    ProductionOrder,
    PurchaseOrder,
    Warehouse,
)
from app.domain.enums import (
    EvidenceNodeType,
    EvidenceRelationType,
    InventoryRiskLevel,
    MovementType,
    ProductionOrderStatus,
    PurchaseOrderStatus,
    ResultStatus,
    RootCauseType,
)
from app.domain.results import (
    AnalysisResult,
    EvidenceGraphEdge,
    EvidenceGraphNode,
    EvidenceGraphResult,
    EvidenceItem,
    EvidencePath,
    InventoryMetrics,
    MetricValue,
    ResultMetadata,
    RiskAssessment,
    RiskListResult,
    RootCauseCandidate,
)
from app.domain.thresholds import AnalysisThresholds

__all__ = [
    "AnalysisThresholds",
    "AnalysisResult",
    "EvidenceItem",
    "EvidenceGraphEdge",
    "EvidenceGraphNode",
    "EvidenceGraphResult",
    "EvidenceNodeType",
    "EvidencePath",
    "EvidenceRelationType",
    "InventoryMovement",
    "InventoryMetrics",
    "InventoryRiskLevel",
    "Material",
    "MovementType",
    "MetricValue",
    "ProductionOrder",
    "ProductionOrderStatus",
    "PurchaseOrder",
    "PurchaseOrderStatus",
    "ResultStatus",
    "ResultMetadata",
    "RiskAssessment",
    "RiskListResult",
    "RootCauseType",
    "RootCauseCandidate",
    "Warehouse",
]
