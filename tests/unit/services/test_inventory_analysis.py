"""确定性库存查询服务的正常、空结果、阻断和依赖异常测试。"""

from datetime import UTC, date, datetime

import pytest

from app.domain.entities import InventoryMovement, Material, Warehouse
from app.domain.enums import InventoryRiskLevel, MovementType, ResultStatus
from app.domain.results import InventoryMetrics, MetricValue
from app.domain.thresholds import AnalysisThresholds
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.services.inventory_analysis import InventoryAnalysisService
from app.synthetic.generator import generate_synthetic_dataset

FIXED_TIME = datetime(2026, 8, 12, tzinfo=UTC)


@pytest.fixture
def service():
    """使用真实内存 Repository 测试服务计算，避免在单元测试复制查询行为。"""
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        repository.add_dataset(generate_synthetic_dataset(seed=20260812, generated_at=FIXED_TIME))
        yield InventoryAnalysisService(repository, AnalysisThresholds())
    engine.dispose()


def test_normal_scenario_returns_ok_metrics_and_normal_risk(service) -> None:
    """持续消耗场景应有确定性指标，且不误判为慢动库存。"""
    result = service.analyze(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-normal",
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.risk.risk_level is InventoryRiskLevel.NORMAL
    assert result.metrics.current_stock.value > 0
    assert result.metrics.infinite_coverage is False


def test_no_consumption_scenario_uses_null_coverage_and_infinite_flag(service) -> None:
    """平均消耗为零时覆盖天数必须为 null，并使用显式无限覆盖标记。"""
    result = service.analyze(
        material_id="MAT-SYN-OVERBUY",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-overbuy",
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.risk.risk_level is InventoryRiskLevel.NON_MOVING
    assert result.metrics.coverage_days.value is None
    assert result.metrics.infinite_coverage is True


def test_missing_material_returns_empty_not_error(service) -> None:
    """查询成功但无匹配物料必须返回 empty，不伪装成依赖失败。"""
    result = service.analyze(
        material_id="MAT-SYN-EMPTY",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-empty",
    )

    assert result.metadata.status is ResultStatus.EMPTY
    assert result.metrics is None
    assert result.errors == []


def test_negative_stock_returns_blocked_and_preserves_metrics(service) -> None:
    """负库存保留累计事实，但禁止输出普通风险结论。"""
    result = service.analyze(
        material_id="MAT-SYN-BLOCKED",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-blocked",
    )

    assert result.metadata.status is ResultStatus.BLOCKED
    assert result.metrics.current_stock.value < 0
    assert result.risk.risk_level is InventoryRiskLevel.DATA_QUALITY_BLOCKED
    assert result.blockers == ["negative_current_stock"]


class AdjustmentOnlyRepository:
    """模拟有库存调整但没有有效消耗和首次采购入库的坏数据。"""

    def get_material(self, material_id: str):
        return Material(
            material_id=material_id,
            name="无首次入库物料",
            category="测试",
            unit_cost="1",
            created_date=date(2026, 1, 1),
        )

    def get_warehouse(self, warehouse_id: str):
        return Warehouse(warehouse_id=warehouse_id, name="测试仓", region="测试")

    def list_movements(self, *, material_id: str, warehouse_id: str, as_of_date: date):
        return [
            InventoryMovement(
                movement_id="MOV-SYN-ADJUST-ONLY",
                material_id=material_id,
                warehouse_id=warehouse_id,
                movement_type=MovementType.ADJUSTMENT,
                quantity="10",
                posted_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        ]


def test_missing_first_receipt_blocks_when_no_consumption_exists() -> None:
    """无消耗且首次采购入库缺失时，不能凭调整流水推断无消耗天数。"""
    service = InventoryAnalysisService(AdjustmentOnlyRepository(), AnalysisThresholds())

    result = service.analyze(
        material_id="MAT-SYN-NO-RECEIPT",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-no-receipt",
    )

    assert result.metadata.status is ResultStatus.BLOCKED
    assert result.metrics.days_without_consumption.value is None
    assert result.metrics.days_without_consumption.complete is False
    assert result.blockers == ["missing_first_receipt"]


def test_partial_window_is_reported_for_recent_material(service) -> None:
    """物料历史少于配置窗口时必须暴露实际覆盖天数。"""
    result = service.analyze(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2025, 9, 15),
        trace_id="trace-syn-partial",
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.metrics.partial_window is True
    assert result.metrics.average_daily_consumption.observation_window_days < 90


class BrokenRepository:
    """模拟持久化依赖失败，不泄露底层异常文本到结果。"""

    def get_material(self, material_id: str):
        raise RuntimeError("database sensitive-detail-do-not-expose")


def test_repository_exception_returns_stable_error_result() -> None:
    """数据库异常必须变成稳定 error 类型，不能被误报为空结果。"""
    service = InventoryAnalysisService(BrokenRepository(), AnalysisThresholds())

    result = service.analyze(
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-error",
    )

    assert result.metadata.status is ResultStatus.ERROR
    assert result.errors == ["repository_error"]
    assert "sensitive-detail" not in result.message


class MissingCostRepository:
    """复用真实仓库事实，但模拟物料成本尚未提供。"""

    def __init__(self, wrapped) -> None:
        self._wrapped = wrapped

    def get_material(self, material_id: str):
        material = self._wrapped.get_material(material_id)
        if material is None:
            return None
        return Material(**{**material.model_dump(), "unit_cost": None})

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)


def test_missing_unit_cost_keeps_quantity_risk_and_marks_amount_incomplete(service) -> None:
    """成本缺失不阻断数量风险，但金额必须为 null 且 incomplete。"""
    no_cost_service = InventoryAnalysisService(
        MissingCostRepository(service._repository),
        AnalysisThresholds(),
    )

    result = no_cost_service.analyze(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-no-cost",
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.metrics.current_stock.value is not None
    assert result.metrics.stagnant_amount.value is None
    assert result.metrics.stagnant_amount.complete is False


def test_service_stock_matches_sum_of_repository_movements(service) -> None:
    """结构化查询库存必须与同粒度、同分析日期的流水累计值一致。"""
    movements = service._repository.list_movements(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
    )
    expected_stock = sum((item.quantity for item in movements), start=0)

    result = service.analyze(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-reconcile",
    )

    assert result.metrics.current_stock.value == expected_stock


def test_risk_list_filters_by_warehouse_and_category_and_sorts_amount(service) -> None:
    """风险清单按筛选条件查询，并将可计算呆滞金额从高到低排列。"""
    result = service.list_risks(
        warehouse_id="WH-SYN-01",
        category="场景物料",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-list",
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.items
    assert all(item.risk.risk_level is not InventoryRiskLevel.NORMAL for item in result.items)
    assert all(item.metrics.warehouse_id == "WH-SYN-01" for item in result.items)
    amounts = [item.metrics.stagnant_amount.value for item in result.items]
    assert amounts == sorted(amounts, reverse=True)


def test_risk_list_returns_empty_collection_for_unmatched_category(service) -> None:
    """筛选条件没有匹配物料时返回空列表，不制造错误结果。"""
    result = service.list_risks(
        warehouse_id="WH-SYN-01",
        category="不存在类别",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-no-category",
    )

    assert result.metadata.status is ResultStatus.EMPTY
    assert result.items == []
    assert result.errors == []


def test_risk_list_repository_failure_is_error_not_empty() -> None:
    """风险清单依赖失败必须返回 error，不能伪装成没有风险数据。"""
    service = InventoryAnalysisService(BrokenRepository(), AnalysisThresholds())

    result = service.list_risks(
        warehouse_id="WH-SYN-01",
        category=None,
        as_of_date=date(2026, 3, 31),
        trace_id="trace-syn-list-error",
    )

    assert result.metadata.status is ResultStatus.ERROR
    assert result.items == []
    assert result.errors == ["repository_error"]


def make_risk_metrics(*, days: str, coverage: str | None, average: str = "1"):
    """直接构造边界指标，隔离验证风险规则的包含关系。"""
    return InventoryMetrics(
        material_id="MAT-SYN-BOUNDARY",
        warehouse_id="WH-SYN-01",
        current_stock=MetricValue(value="120", unit="unit", complete=True),
        first_receipt_date=date(2025, 1, 1),
        days_without_consumption=MetricValue(value=days, unit="day", complete=True),
        average_daily_consumption=MetricValue(
            value=average,
            unit="unit/day",
            observation_window_days=90,
            complete=True,
        ),
        coverage_days=MetricValue(value=coverage, unit="day", complete=True),
        stagnant_amount=MetricValue(value="1200", unit="currency", complete=True),
        infinite_coverage=coverage is None,
    )


@pytest.mark.parametrize(
    ("days", "coverage", "expected"),
    [
        ("89", "120", InventoryRiskLevel.NORMAL),
        ("90", "119", InventoryRiskLevel.NORMAL),
        ("90", "120", InventoryRiskLevel.SLOW_MOVING),
        ("179", None, InventoryRiskLevel.SLOW_MOVING),
        ("180", None, InventoryRiskLevel.NON_MOVING),
    ],
)
def test_risk_boundaries_are_inclusive_only_at_configured_thresholds(
    days: str,
    coverage: str | None,
    expected: InventoryRiskLevel,
) -> None:
    """覆盖 89/90、179/180 与 119/120 三组精确边界。"""
    service = InventoryAnalysisService(BrokenRepository(), AnalysisThresholds())
    average = "0" if coverage is None else "1"

    result = service._assess_risk(
        make_risk_metrics(days=days, coverage=coverage, average=average)
    )

    assert result.risk_level is expected


class MovementRepository:
    """用给定流水验证保护期、调拨和未来事实边界。"""

    def __init__(self, movements) -> None:
        self._movements = movements

    def get_material(self, material_id: str):
        return Material(
            material_id=material_id,
            name="边界物料",
            category="测试",
            unit_cost="2",
            created_date=date(2025, 1, 1),
        )

    def get_warehouse(self, warehouse_id: str):
        return Warehouse(warehouse_id=warehouse_id, name="测试仓", region="测试")

    def list_movements(self, *, material_id: str, warehouse_id: str, as_of_date: date):
        return self._movements


def make_movement(identifier: str, movement_type: MovementType, quantity: str, day: date):
    return InventoryMovement(
        movement_id=identifier,
        material_id="MAT-SYN-BOUNDARY",
        warehouse_id="WH-SYN-01",
        movement_type=movement_type,
        quantity=quantity,
        posted_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
    )


def test_new_material_protection_suppresses_stagnant_risk_and_amount() -> None:
    movements = [
        make_movement(
            "MOV-SYN-RECEIPT", MovementType.PURCHASE_RECEIPT, "100", date(2026, 3, 10)
        )
    ]
    service = InventoryAnalysisService(MovementRepository(movements), AnalysisThresholds())

    result = service.analyze(
        material_id="MAT-SYN-BOUNDARY",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-protection",
    )

    assert result.metrics.new_material_protected is True
    assert result.metrics.stagnant_quantity.value == 0
    assert result.metrics.stagnant_amount.value == 0
    assert result.risk.risk_level is InventoryRiskLevel.NORMAL


def test_transfer_does_not_reset_last_effective_consumption_date() -> None:
    movements = [
        make_movement(
            "MOV-SYN-RECEIPT", MovementType.PURCHASE_RECEIPT, "100", date(2025, 1, 1)
        ),
        make_movement(
            "MOV-SYN-ISSUE", MovementType.SALES_ISSUE, "-10", date(2025, 10, 1)
        ),
        make_movement(
            "MOV-SYN-TRANSFER", MovementType.TRANSFER_IN, "10", date(2026, 3, 30)
        ),
    ]
    service = InventoryAnalysisService(MovementRepository(movements), AnalysisThresholds())

    result = service.analyze(
        material_id="MAT-SYN-BOUNDARY",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-transfer",
    )

    assert result.metrics.last_consumption_date == date(2025, 10, 1)
    assert result.metrics.days_without_consumption.value == 181


def test_future_consumption_is_quality_blocked() -> None:
    movements = [
        make_movement(
            "MOV-SYN-RECEIPT", MovementType.PURCHASE_RECEIPT, "100", date(2026, 1, 1)
        ),
        make_movement(
            "MOV-SYN-FUTURE", MovementType.SALES_ISSUE, "-1", date(2026, 4, 1)
        ),
    ]
    service = InventoryAnalysisService(MovementRepository(movements), AnalysisThresholds())

    result = service.analyze(
        material_id="MAT-SYN-BOUNDARY",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-future",
    )

    assert result.metadata.status is ResultStatus.BLOCKED
    assert "future_movement" in result.blockers


def test_zero_current_stock_has_zero_stagnant_quantity_and_amount() -> None:
    movements = [
        make_movement(
            "MOV-SYN-RECEIPT", MovementType.PURCHASE_RECEIPT, "100", date(2025, 1, 1)
        ),
        make_movement(
            "MOV-SYN-ISSUE", MovementType.SALES_ISSUE, "-100", date(2025, 1, 2)
        ),
    ]
    service = InventoryAnalysisService(MovementRepository(movements), AnalysisThresholds())

    result = service.analyze(
        material_id="MAT-SYN-BOUNDARY",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id="trace-zero-stock",
    )

    assert result.metrics.current_stock.value == 0
    assert result.metrics.stagnant_quantity.value == 0
    assert result.metrics.stagnant_amount.value == 0
    assert result.risk.risk_level is InventoryRiskLevel.NORMAL
