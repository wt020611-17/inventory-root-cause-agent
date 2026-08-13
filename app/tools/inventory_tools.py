"""用例级 Agent Tools：只做强类型编排，不重复实现确定性算法。"""

from app.domain.results import AnalysisResult, EvidenceGraphResult, RiskListResult
from app.domain.thresholds import AnalysisThresholds
from app.repositories.protocols import InventoryRepository
from app.services.evidence_graph import EvidenceGraphService
from app.services.inventory_analysis import InventoryAnalysisService
from app.services.root_cause_analysis import RootCauseAnalysisService
from app.tools.models import (
    AnalyzeMaterialRootCauseInput,
    ListInventoryRisksInput,
    TraceEvidenceInput,
)


class InventoryAgentTools:
    """持有同一 Repository 的三个结构化 Tool，便于 Phase 3 注册到 Agent。"""

    def __init__(
        self,
        repository: InventoryRepository,
        thresholds: AnalysisThresholds | None = None,
    ) -> None:
        resolved = thresholds or AnalysisThresholds()
        self._inventory = InventoryAnalysisService(repository, resolved)
        self._root_cause = RootCauseAnalysisService(repository, resolved)
        self._graph = EvidenceGraphService(repository)

    def list_inventory_risks(self, payload: ListInventoryRisksInput) -> RiskListResult:
        """列出确定性风险，不输出自由文本生成的业务事实。"""
        return self._inventory.list_risks(
            warehouse_id=payload.warehouse_id,
            category=payload.category,
            as_of_date=payload.as_of_date,
            trace_id=payload.trace_id,
        )

    def analyze_material_root_cause(
        self,
        payload: AnalyzeMaterialRootCauseInput,
    ) -> AnalysisResult:
        """返回指标、风险、候选根因和可引用证据。"""
        return self._root_cause.analyze(
            material_id=payload.material_id,
            warehouse_id=payload.warehouse_id,
            as_of_date=payload.as_of_date,
            trace_id=payload.trace_id,
        )

    def trace_evidence(self, payload: TraceEvidenceInput) -> EvidenceGraphResult:
        """返回受跳数、节点数和时间限制的业务关系路径。"""
        return self._graph.trace(
            material_id=payload.material_id,
            warehouse_id=payload.warehouse_id,
            as_of_date=payload.as_of_date,
            trace_id=payload.trace_id,
            max_hops=payload.max_hops,
            max_nodes=payload.max_nodes,
            timeout_ms=payload.timeout_ms,
        )
