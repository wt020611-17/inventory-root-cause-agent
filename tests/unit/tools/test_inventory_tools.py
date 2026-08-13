"""三个 Tool 的参数、正常、空结果、阻断与依赖失败契约测试。"""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.domain.enums import ResultStatus, RootCauseType
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.synthetic.generator import generate_synthetic_dataset
from app.tools import (
    AnalyzeMaterialRootCauseInput,
    InventoryAgentTools,
    ListInventoryRisksInput,
    TraceEvidenceInput,
)


@pytest.fixture
def tools():
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
        yield InventoryAgentTools(repository)
    engine.dispose()


def test_list_inventory_risks_returns_typed_sorted_result(tools) -> None:
    result = tools.list_inventory_risks(
        ListInventoryRisksInput(
            warehouse_id="WH-SYN-01",
            category="场景物料",
            as_of_date=date(2026, 3, 31),
            trace_id="trace-risk-tool",
        )
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.metadata.trace_id == "trace-risk-tool"
    assert all(item.evidence for item in result.items)
    amounts = [item.metrics.stagnant_amount.value for item in result.items]
    assert amounts == sorted(amounts, reverse=True)


def test_analyze_root_cause_returns_structured_multi_cause_evidence(tools) -> None:
    result = tools.analyze_material_root_cause(
        AnalyzeMaterialRootCauseInput(
            material_id="MAT-SYN-MULTI",
            warehouse_id="WH-SYN-01",
            as_of_date=date(2026, 3, 31),
            trace_id="trace-analysis-tool",
        )
    )

    assert result.metadata.status is ResultStatus.OK
    assert {item.cause_type for item in result.root_causes if item.evidence} >= {
        RootCauseType.PURCHASE_EXCESS,
        RootCauseType.PRODUCTION_DELAY,
    }
    assert result.evidence


def test_trace_evidence_returns_typed_paths_and_trace(tools) -> None:
    result = tools.trace_evidence(
        TraceEvidenceInput(
            material_id="MAT-SYN-MULTI",
            warehouse_id="WH-SYN-01",
            as_of_date=date(2026, 3, 31),
            trace_id="trace-graph-tool",
        )
    )

    assert result.metadata.status is ResultStatus.OK
    assert result.metadata.trace_id == "trace-graph-tool"
    assert result.paths and result.edges


def test_tools_preserve_empty_and_blocked_semantics(tools) -> None:
    empty = tools.analyze_material_root_cause(
        AnalyzeMaterialRootCauseInput(
            material_id="MAT-SYN-EMPTY",
            warehouse_id="WH-SYN-01",
            as_of_date=date(2026, 3, 31),
            trace_id="trace-empty-tool",
        )
    )
    blocked = tools.analyze_material_root_cause(
        AnalyzeMaterialRootCauseInput(
            material_id="MAT-SYN-BLOCKED",
            warehouse_id="WH-SYN-01",
            as_of_date=date(2026, 3, 31),
            trace_id="trace-blocked-tool",
        )
    )

    assert empty.metadata.status is ResultStatus.EMPTY
    assert blocked.metadata.status is ResultStatus.BLOCKED


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            AnalyzeMaterialRootCauseInput,
            {
                "material_id": "",
                "warehouse_id": "WH-SYN-01",
                "as_of_date": "2026-03-31",
                "trace_id": "trace-invalid",
            },
        ),
        (
            TraceEvidenceInput,
            {
                "material_id": "MAT-SYN-MULTI",
                "warehouse_id": "WH-SYN-01",
                "as_of_date": "2026-03-31",
                "trace_id": "trace-invalid",
                "max_hops": 0,
            },
        ),
        (
            ListInventoryRisksInput,
            {
                "as_of_date": "2026-03-31",
                "trace_id": "trace-invalid",
                "unknown": "rejected",
            },
        ),
    ],
)
def test_tool_inputs_reject_invalid_or_unknown_fields(model, values) -> None:
    with pytest.raises(ValidationError):
        model(**values)


class BrokenRepository:
    def get_material(self, material_id: str):
        raise RuntimeError("sensitive database detail")

    def list_materials(self, category=None):
        raise RuntimeError("sensitive database detail")


def test_dependency_failure_is_stable_error_without_stack_or_detail() -> None:
    tools = InventoryAgentTools(BrokenRepository())
    result = tools.analyze_material_root_cause(
        AnalyzeMaterialRootCauseInput(
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            as_of_date=date(2026, 3, 31),
            trace_id="trace-error-tool",
        )
    )

    assert result.metadata.status is ResultStatus.ERROR
    assert result.errors == ["repository_error"]
    assert "sensitive" not in result.model_dump_json()
