"""证据图的单路径、多路径、空路径与限制测试。"""

from datetime import UTC, date, datetime

import pytest

from app.domain.enums import EvidenceRelationType, ResultStatus
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.services.evidence_graph import EvidenceGraphService
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
        yield EvidenceGraphService(repository)
    engine.dispose()


def trace(service, material_id: str, **limits):
    return service.trace(
        material_id=material_id,
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
        trace_id=f"trace-{material_id}",
        **limits,
    )


def test_purchase_material_has_purchase_and_storage_paths(service) -> None:
    result = trace(service, "MAT-SYN-OVERBUY")

    assert result.metadata.status is ResultStatus.OK
    assert {edge.relation_type for edge in result.edges} == {
        EvidenceRelationType.PURCHASES,
        EvidenceRelationType.STORED_IN,
    }
    assert all(node.synthetic is True for node in result.nodes)
    assert all(edge.source_id for edge in result.edges)


def test_multi_cause_material_has_all_three_controlled_relations(service) -> None:
    result = trace(service, "MAT-SYN-MULTI")

    assert result.metadata.status is ResultStatus.OK
    assert {edge.relation_type for edge in result.edges} == set(EvidenceRelationType)
    assert len(result.paths) >= 3


def test_material_without_movements_before_date_returns_empty_path(service) -> None:
    result = service.trace(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2025, 1, 1),
        trace_id="trace-empty-path",
    )

    assert result.metadata.status is ResultStatus.EMPTY
    assert result.paths == []


def test_missing_material_returns_empty_not_error(service) -> None:
    result = trace(service, "MAT-SYN-EMPTY")

    assert result.metadata.status is ResultStatus.EMPTY
    assert result.errors == []


def test_node_limit_blocks_without_partial_paths(service) -> None:
    result = trace(service, "MAT-SYN-MULTI", max_nodes=2)

    assert result.metadata.status is ResultStatus.BLOCKED
    assert result.blockers == ["graph_node_limit_exceeded"]
    assert result.paths == []


def test_hop_limit_can_produce_empty(service) -> None:
    result = trace(service, "MAT-SYN-MULTI", max_hops=0)

    assert result.metadata.status is ResultStatus.EMPTY


def test_timeout_limit_blocks_query(service) -> None:
    result = trace(service, "MAT-SYN-MULTI", timeout_ms=-1)

    assert result.metadata.status is ResultStatus.BLOCKED
    assert result.blockers == ["graph_query_timeout"]
