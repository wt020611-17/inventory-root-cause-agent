"""三类库存候选根因的确定性规则、证据与稳定排序。"""

from datetime import date, timedelta
from decimal import Decimal

from app.domain.entities import InventoryMovement, ProductionOrder, PurchaseOrder
from app.domain.enums import (
    MovementType,
    ProductionOrderStatus,
    PurchaseOrderStatus,
    ResultStatus,
    RootCauseType,
)
from app.domain.results import AnalysisResult, EvidenceItem, RootCauseCandidate
from app.domain.thresholds import AnalysisThresholds
from app.repositories.protocols import InventoryRepository
from app.services.inventory_analysis import InventoryAnalysisService

_CAUSE_ORDER = {
    RootCauseType.DEMAND_DROP: 0,
    RootCauseType.PURCHASE_EXCESS: 1,
    RootCauseType.PRODUCTION_DELAY: 2,
}
_CONSUMPTION_TYPES = {MovementType.SALES_ISSUE, MovementType.PRODUCTION_ISSUE}


class RootCauseAnalysisService:
    """组合库存指标与三条规则，输出可重复、可追溯的结构化结果。"""

    def __init__(self, repository: InventoryRepository, thresholds: AnalysisThresholds) -> None:
        self._repository = repository
        self._thresholds = thresholds
        self._inventory = InventoryAnalysisService(repository, thresholds)

    def analyze(
        self,
        *,
        material_id: str,
        warehouse_id: str,
        as_of_date: date,
        trace_id: str,
    ) -> AnalysisResult:
        """先计算库存事实；仅在事实状态为 ok 时执行根因规则。"""
        base = self._inventory.analyze(
            material_id=material_id,
            warehouse_id=warehouse_id,
            as_of_date=as_of_date,
            trace_id=trace_id,
        )
        if base.metadata.status is not ResultStatus.OK or base.metrics is None:
            return base

        try:
            movements = self._repository.list_movements(
                material_id=material_id,
                warehouse_id=warehouse_id,
                as_of_date=as_of_date,
            )
            purchase_orders = self._repository.list_purchase_orders(material_id, warehouse_id)
            production_orders = self._repository.list_production_orders(
                material_id, warehouse_id
            )
            candidates = [
                self._demand_drop(movements, as_of_date),
                self._purchase_excess(
                    purchase_orders,
                    as_of_date,
                    base.metrics.current_stock.value or Decimal("0"),
                    base.metrics.average_daily_consumption.value or Decimal("0"),
                ),
                self._production_delay(
                    production_orders,
                    as_of_date,
                    base.metrics.current_stock.value or Decimal("0"),
                ),
            ]
        except Exception:
            return AnalysisResult(
                metadata=base.metadata.model_copy(update={"status": ResultStatus.ERROR}),
                message="根因分析执行失败",
                errors=["repository_error"],
            )

        visible = [candidate for candidate in candidates if candidate is not None]
        visible.sort(key=lambda item: (-item.score, _CAUSE_ORDER[item.cause_type]))
        evidence = [item for candidate in visible for item in candidate.evidence]
        return base.model_copy(
            update={
                "message": "库存指标、风险与确定性候选根因分析完成",
                "root_causes": visible,
                "evidence": evidence,
            }
        )

    def _demand_drop(
        self,
        movements: list[InventoryMovement],
        as_of_date: date,
    ) -> RootCauseCandidate | None:
        window = self._thresholds.analysis_window_days
        recent_start = as_of_date - timedelta(days=window - 1)
        prior_start = recent_start - timedelta(days=window)
        prior_end = recent_start - timedelta(days=1)
        effective = [
            item
            for item in movements
            if item.movement_type in _CONSUMPTION_TYPES and item.quantity < 0
        ]
        if not effective or min(item.posted_at.date() for item in movements) > prior_start:
            return self._insufficient(
                RootCauseType.DEMAND_DROP,
                "insufficient_adjacent_consumption_windows",
            )

        recent = sum(
            (
                -item.quantity
                for item in effective
                if recent_start <= item.posted_at.date() <= as_of_date
            ),
            start=Decimal("0"),
        )
        prior = sum(
            (
                -item.quantity
                for item in effective
                if prior_start <= item.posted_at.date() <= prior_end
            ),
            start=Decimal("0"),
        )
        if prior <= 0:
            return self._insufficient(
                RootCauseType.DEMAND_DROP,
                "prior_window_has_no_effective_consumption",
            )
        drop_ratio = max(Decimal("0"), (prior - recent) / prior)
        if drop_ratio < Decimal(str(self._thresholds.demand_drop_ratio)):
            return None
        evidence = EvidenceItem(
            evidence_id="EVI-DEMAND-WINDOWS",
            source_type="inventory_movement_window",
            source_id=f"{movements[0].material_id}:{movements[0].warehouse_id}",
            summary="相邻观察窗口的有效消耗下降达到配置阈值",
            facts={
                "prior_window_start": prior_start,
                "prior_window_end": prior_end,
                "prior_consumption": prior,
                "recent_window_start": recent_start,
                "recent_window_end": as_of_date,
                "recent_consumption": recent,
                "drop_ratio": drop_ratio,
            },
        )
        return RootCauseCandidate(
            cause_type=RootCauseType.DEMAND_DROP,
            score=min(Decimal("1"), drop_ratio),
            hits=["RC-DEMAND-001:drop_ratio_at_or_above_threshold"],
            evidence=[evidence],
        )

    def _purchase_excess(
        self,
        orders: list[PurchaseOrder],
        as_of_date: date,
        current_stock: Decimal,
        average_daily: Decimal,
    ) -> RootCauseCandidate | None:
        eligible = [
            order
            for order in orders
            if order.status
            in {PurchaseOrderStatus.PARTIALLY_RECEIVED, PurchaseOrderStatus.RECEIVED}
            and order.actual_date is not None
            and order.actual_date <= as_of_date
            and order.received_qty > 0
        ]
        if not eligible:
            return self._insufficient(
                RootCauseType.PURCHASE_EXCESS,
                "no_received_purchase_order_evidence",
            )
        arrived = sum((order.received_qty for order in eligible), start=Decimal("0"))
        remaining = min(max(current_stock, Decimal("0")), arrived)
        if remaining <= 0:
            return None
        purchase_coverage = remaining / average_daily if average_daily > 0 else None
        if not (
            purchase_coverage is None
            or purchase_coverage >= self._thresholds.purchase_excess_days
        ):
            return None
        score = (
            Decimal("1")
            if purchase_coverage is None
            else min(
                Decimal("1"),
                purchase_coverage / Decimal(self._thresholds.purchase_excess_days * 2),
            )
        )
        evidence = [
            EvidenceItem(
                evidence_id=f"EVI-PO-{order.po_id}",
                source_type="purchase_order",
                source_id=order.po_id,
                summary="关联采购单到货形成当前剩余库存覆盖",
                facts={
                    "ordered_qty": order.ordered_qty,
                    "received_qty": order.received_qty,
                    "actual_date": order.actual_date,
                    "status": order.status.value,
                    "attributed_remaining_qty": remaining,
                    "purchase_coverage_days": purchase_coverage,
                },
            )
            for order in eligible
        ]
        return RootCauseCandidate(
            cause_type=RootCauseType.PURCHASE_EXCESS,
            score=score,
            hits=["RC-PURCHASE-001:purchase_coverage_above_threshold"],
            evidence=evidence,
        )

    def _production_delay(
        self,
        orders: list[ProductionOrder],
        as_of_date: date,
        current_stock: Decimal,
    ) -> RootCauseCandidate | None:
        relevant = [
            order
            for order in orders
            if order.status
            in {
                ProductionOrderStatus.PLANNED,
                ProductionOrderStatus.RELEASED,
                ProductionOrderStatus.IN_PROGRESS,
            }
            and order.planned_start <= as_of_date
        ]
        if not relevant:
            return self._insufficient(
                RootCauseType.PRODUCTION_DELAY,
                "no_open_production_order_evidence",
            )
        delayed: list[tuple[ProductionOrder, int]] = []
        for order in relevant:
            effective_start = order.actual_start or as_of_date
            delay_days = (effective_start - order.planned_start).days
            if (
                delay_days > self._thresholds.production_delay_days
                and order.planned_consumption_qty > 0
                and current_stock > 0
            ):
                delayed.append((order, delay_days))
        if not delayed:
            return None
        max_delay = max(delay for _, delay in delayed)
        evidence = [
            EvidenceItem(
                evidence_id=f"EVI-PRD-{order.production_order_id}",
                source_type="production_order",
                source_id=order.production_order_id,
                summary="生产计划开工延期且仍有待消耗库存",
                facts={
                    "status": order.status.value,
                    "planned_start": order.planned_start,
                    "actual_start": order.actual_start,
                    "delay_days": delay_days,
                    "planned_consumption_qty": order.planned_consumption_qty,
                    "current_stock": current_stock,
                },
            )
            for order, delay_days in delayed
        ]
        score = min(
            Decimal("1"),
            Decimal(max_delay) / Decimal(self._thresholds.production_delay_days * 2),
        )
        return RootCauseCandidate(
            cause_type=RootCauseType.PRODUCTION_DELAY,
            score=score,
            hits=["RC-PRODUCTION-001:open_order_delay_above_threshold"],
            evidence=evidence,
        )

    @staticmethod
    def _insufficient(cause_type: RootCauseType, hit: str) -> RootCauseCandidate:
        return RootCauseCandidate(
            cause_type=cause_type,
            score=Decimal("0"),
            hits=[hit],
            evidence=[],
            insufficient_evidence=True,
        )
