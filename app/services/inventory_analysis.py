"""不依赖 LLM 的确定性库存指标与基础风险查询服务。"""

from datetime import date, timedelta
from decimal import Decimal

from app.domain.enums import InventoryRiskLevel, MovementType, ResultStatus
from app.domain.results import (
    AnalysisResult,
    InventoryMetrics,
    MetricValue,
    ResultMetadata,
    RiskAssessment,
    RiskListResult,
)
from app.domain.thresholds import AnalysisThresholds
from app.repositories.protocols import InventoryRepository


class InventoryAnalysisService:
    """按物料 × 仓库 × 分析日期计算确定性指标和基础风险。"""

    def __init__(self, repository: InventoryRepository, thresholds: AnalysisThresholds) -> None:
        self._repository = repository
        self._thresholds = thresholds

    def analyze(
        self,
        *,
        material_id: str,
        warehouse_id: str,
        as_of_date: date,
        trace_id: str,
    ) -> AnalysisResult:
        """查询事实并返回 ok、empty、blocked 或 error 的统一领域结果。"""
        metadata_values = {"trace_id": trace_id, "as_of_date": as_of_date}
        try:
            material = self._repository.get_material(material_id)
            warehouse = self._repository.get_warehouse(warehouse_id)
            if material is None or warehouse is None:
                return AnalysisResult(
                    metadata=ResultMetadata(status=ResultStatus.EMPTY, **metadata_values),
                    message="未找到匹配的物料或仓库",
                )

            movements = self._repository.list_movements(
                material_id=material_id,
                warehouse_id=warehouse_id,
                as_of_date=as_of_date,
            )
            if not movements:
                return AnalysisResult(
                    metadata=ResultMetadata(status=ResultStatus.EMPTY, **metadata_values),
                    message="指定分析日期前没有匹配的库存流水",
                )

            metrics = self._calculate_metrics(
                material_id=material_id,
                warehouse_id=warehouse_id,
                as_of_date=as_of_date,
                unit_cost=material.unit_cost,
                movements=movements,
            )
            blockers: list[str] = []
            if metrics.current_stock.value is not None and metrics.current_stock.value < 0:
                blockers.append("negative_current_stock")
            if metrics.days_without_consumption.value is None:
                blockers.append("missing_first_receipt")
            if blockers:
                return AnalysisResult(
                    metadata=ResultMetadata(status=ResultStatus.BLOCKED, **metadata_values),
                    message="数据质量问题阻止风险结论",
                    metrics=metrics,
                    risk=RiskAssessment(
                        risk_level=InventoryRiskLevel.DATA_QUALITY_BLOCKED,
                        conclusion_allowed=False,
                    ),
                    blockers=blockers,
                )

            return AnalysisResult(
                metadata=ResultMetadata(status=ResultStatus.OK, **metadata_values),
                message="库存指标与基础风险计算完成",
                metrics=metrics,
                risk=self._assess_risk(metrics),
            )
        except Exception:
            # 外部结果只暴露稳定错误类别；底层异常细节进入受控日志而不是响应。
            return AnalysisResult(
                metadata=ResultMetadata(status=ResultStatus.ERROR, **metadata_values),
                message="库存查询执行失败",
                errors=["repository_error"],
            )

    def list_risks(
        self,
        *,
        warehouse_id: str | None,
        category: str | None,
        as_of_date: date,
        trace_id: str,
    ) -> RiskListResult:
        """按仓库和类别筛选风险结果，并按呆滞金额降序排列。"""
        metadata_values = {"trace_id": trace_id, "as_of_date": as_of_date}
        try:
            materials = self._repository.list_materials(category=category)
            warehouses = (
                [self._repository.get_warehouse(warehouse_id)]
                if warehouse_id is not None
                else self._repository.list_warehouses()
            )
        except Exception:
            return RiskListResult(
                metadata=ResultMetadata(status=ResultStatus.ERROR, **metadata_values),
                errors=["repository_error"],
            )

        results: list[AnalysisResult] = []
        for material in materials:
            for warehouse in warehouses:
                if warehouse is None:
                    continue
                result = self.analyze(
                    material_id=material.material_id,
                    warehouse_id=warehouse.warehouse_id,
                    as_of_date=as_of_date,
                    trace_id=trace_id,
                )
                if (
                    result.metadata.status in {ResultStatus.OK, ResultStatus.BLOCKED}
                    and result.risk is not None
                    and result.risk.risk_level is not InventoryRiskLevel.NORMAL
                ):
                    results.append(result)

        def amount(result: AnalysisResult) -> Decimal:
            if result.metrics is None or result.metrics.stagnant_amount.value is None:
                return Decimal("-1")
            return result.metrics.stagnant_amount.value

        sorted_results = sorted(results, key=amount, reverse=True)
        status = ResultStatus.OK if sorted_results else ResultStatus.EMPTY
        return RiskListResult(
            metadata=ResultMetadata(status=status, **metadata_values),
            items=sorted_results,
        )

    def _calculate_metrics(
        self,
        *,
        material_id: str,
        warehouse_id: str,
        as_of_date: date,
        unit_cost: Decimal | None,
        movements: list,
    ) -> InventoryMetrics:
        """根据已过滤流水计算库存、消耗、覆盖和金额。"""
        current_stock = sum((item.quantity for item in movements), start=Decimal("0"))
        first_date = min(item.posted_at.date() for item in movements)
        actual_window_days = min(
            self._thresholds.analysis_window_days,
            max(1, (as_of_date - first_date).days + 1),
        )
        window_start = as_of_date - timedelta(days=actual_window_days - 1)
        consumption_types = {MovementType.SALES_ISSUE, MovementType.PRODUCTION_ISSUE}
        effective_consumption = [
            item
            for item in movements
            if item.movement_type in consumption_types and item.quantity < 0
        ]
        window_consumption = sum(
            (
                -item.quantity
                for item in effective_consumption
                if item.posted_at.date() >= window_start
            ),
            start=Decimal("0"),
        )
        average_daily = window_consumption / Decimal(actual_window_days)

        receipt_dates = [
            item.posted_at.date()
            for item in movements
            if item.movement_type is MovementType.PURCHASE_RECEIPT and item.quantity > 0
        ]
        if effective_consumption:
            reference_date = max(item.posted_at.date() for item in effective_consumption)
        elif receipt_dates:
            reference_date = min(receipt_dates)
        else:
            reference_date = None
        days_without_consumption = (
            Decimal((as_of_date - reference_date).days) if reference_date else None
        )
        infinite_coverage = average_daily == 0 and current_stock > 0
        coverage_value = (
            None
            if infinite_coverage
            else (current_stock / average_daily if average_daily > 0 else Decimal("0"))
        )

        return InventoryMetrics(
            material_id=material_id,
            warehouse_id=warehouse_id,
            current_stock=MetricValue(value=current_stock, unit="unit", complete=True),
            days_without_consumption=MetricValue(
                value=days_without_consumption,
                unit="day",
                complete=reference_date is not None,
            ),
            average_daily_consumption=MetricValue(
                value=average_daily,
                unit="unit/day",
                observation_window_days=actual_window_days,
                complete=True,
            ),
            coverage_days=MetricValue(
                value=coverage_value, unit="day", complete=True
            ),
            stagnant_amount=MetricValue(
                value=current_stock * unit_cost if unit_cost is not None else None,
                unit="currency",
                complete=unit_cost is not None,
            ),
            infinite_coverage=infinite_coverage,
            partial_window=actual_window_days < self._thresholds.analysis_window_days,
        )

    def _assess_risk(self, metrics: InventoryMetrics) -> RiskAssessment:
        """应用业务口径中的基础风险条件，不执行 Phase 2 根因评分。"""
        stock = metrics.current_stock.value or Decimal("0")
        days = metrics.days_without_consumption.value or Decimal("0")
        average = metrics.average_daily_consumption.value or Decimal("0")
        coverage = metrics.coverage_days.value
        base_risk = (
            stock > 0
            and days >= self._thresholds.slow_moving_days
            and (
                average == 0
                or (
                    coverage is not None
                    and coverage >= self._thresholds.coverage_days_threshold
                )
            )
        )

        if not base_risk:
            level = InventoryRiskLevel.NORMAL
            matched_rules: list[str] = []
        elif days >= self._thresholds.non_moving_days and average == 0:
            level = InventoryRiskLevel.NON_MOVING
            matched_rules = ["days_without_consumption", "zero_average_consumption"]
        else:
            level = InventoryRiskLevel.SLOW_MOVING
            matched_rules = ["days_without_consumption", "high_or_infinite_coverage"]

        return RiskAssessment(
            risk_level=level,
            conclusion_allowed=True,
            matched_rules=matched_rules,
        )
