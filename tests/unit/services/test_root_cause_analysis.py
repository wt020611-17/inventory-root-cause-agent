"""三类根因的正例、反例、证据不足与多根因稳定排序测试。"""

from datetime import UTC, date, datetime

import pytest

from app.domain.entities import ProductionOrder, PurchaseOrder
from app.domain.enums import (
    ProductionOrderStatus,
    PurchaseOrderStatus,
    ResultStatus,
    RootCauseType,
)
from app.domain.thresholds import AnalysisThresholds
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.services.root_cause_analysis import RootCauseAnalysisService
from app.synthetic.generator import generate_synthetic_dataset


@pytest.fixture
def service():
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        repository.add_dataset(
            generate_synthetic_dataset(
                seed=20260812,
                generated_at=datetime(2026, 8, 12, tzinfo=UTC),
            )
        )
        yield RootCauseAnalysisService(repository, AnalysisThresholds())
    engine.dispose()


def analyze(service, material_id: str):
    return service.analyze(
        material_id=material_id,
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id=f"trace-{material_id}",
    )


def by_type(result, cause_type: RootCauseType):
    return next((item for item in result.root_causes if item.cause_type is cause_type), None)


def test_demand_drop_positive_has_window_facts(service) -> None:
    result = analyze(service, "MAT-SYN-DEMAND")
    candidate = by_type(result, RootCauseType.DEMAND_DROP)

    assert result.metadata.status is ResultStatus.OK
    assert candidate is not None and candidate.insufficient_evidence is False
    assert candidate.score >= AnalysisThresholds().demand_drop_ratio
    assert candidate.evidence[0].facts["drop_ratio"] >= AnalysisThresholds().demand_drop_ratio


def test_normal_material_is_demand_drop_negative(service) -> None:
    result = analyze(service, "MAT-SYN-NORMAL")

    assert by_type(result, RootCauseType.DEMAND_DROP) is None


def test_missing_prior_window_is_explicitly_insufficient(service) -> None:
    result = service.analyze(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2025, 9, 15),
        trace_id="trace-short-history",
    )
    candidate = by_type(result, RootCauseType.DEMAND_DROP)

    assert candidate is not None and candidate.insufficient_evidence is True
    assert candidate.score == 0


def test_purchase_excess_positive_cites_purchase_order(service) -> None:
    result = analyze(service, "MAT-SYN-OVERBUY")
    candidate = by_type(result, RootCauseType.PURCHASE_EXCESS)

    assert candidate is not None and candidate.insufficient_evidence is False
    assert candidate.evidence[0].source_id == "PO-SYN-OVERBUY"
    assert candidate.evidence[0].facts["received_qty"] == 500


def test_material_without_purchase_order_is_insufficient_not_hit(service) -> None:
    result = analyze(service, "MAT-SYN-PROD")
    candidate = by_type(result, RootCauseType.PURCHASE_EXCESS)

    assert candidate is not None and candidate.insufficient_evidence is True


def test_received_purchase_with_low_coverage_is_not_a_hit(service) -> None:
    order = PurchaseOrder(
        po_id="PO-SYN-SMALL",
        material_id="MAT-SYN-TEST",
        warehouse_id="WH-SYN-01",
        ordered_qty="10",
        received_qty="10",
        planned_date=date(2026, 1, 1),
        actual_date=date(2026, 1, 2),
        status=PurchaseOrderStatus.RECEIVED,
    )

    assert service._purchase_excess([order], date(2026, 3, 31), 10, 1) is None


def test_production_delay_positive_cites_dates_status_and_quantity(service) -> None:
    result = analyze(service, "MAT-SYN-PROD")
    candidate = by_type(result, RootCauseType.PRODUCTION_DELAY)
    facts = candidate.evidence[0].facts

    assert candidate is not None and candidate.insufficient_evidence is False
    assert facts["delay_days"] > AnalysisThresholds().production_delay_days
    assert facts["status"] == "RELEASED"
    assert facts["planned_consumption_qty"] == 80


def test_material_without_open_production_order_is_insufficient(service) -> None:
    result = analyze(service, "MAT-SYN-OVERBUY")
    candidate = by_type(result, RootCauseType.PRODUCTION_DELAY)

    assert candidate is not None and candidate.insufficient_evidence is True


def test_open_on_time_production_order_is_not_a_hit(service) -> None:
    order = ProductionOrder(
        production_order_id="PRD-SYN-ON-TIME",
        material_id="MAT-SYN-TEST",
        warehouse_id="WH-SYN-01",
        planned_consumption_qty="10",
        status=ProductionOrderStatus.IN_PROGRESS,
        planned_start=date(2026, 3, 20),
        actual_start=date(2026, 3, 21),
        due_date=date(2026, 3, 31),
    )

    assert service._production_delay([order], date(2026, 3, 31), 10) is None


def test_multi_cause_contains_two_hits_and_stable_order(service) -> None:
    first = analyze(service, "MAT-SYN-MULTI")
    second = analyze(service, "MAT-SYN-MULTI")
    first_hits = [item for item in first.root_causes if not item.insufficient_evidence]

    assert {item.cause_type for item in first_hits} >= {
        RootCauseType.PURCHASE_EXCESS,
        RootCauseType.PRODUCTION_DELAY,
    }
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.root_causes == sorted(
        first.root_causes,
        key=lambda item: (-item.score, list(RootCauseType).index(item.cause_type)),
    )


def test_empty_and_blocked_stop_before_root_cause_conclusions(service) -> None:
    empty = analyze(service, "MAT-SYN-EMPTY")
    blocked = analyze(service, "MAT-SYN-BLOCKED")

    assert empty.metadata.status is ResultStatus.EMPTY and empty.root_causes == []
    assert blocked.metadata.status is ResultStatus.BLOCKED and blocked.root_causes == []
