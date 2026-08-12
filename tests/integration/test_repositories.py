"""SQLite Repository 的写入、查询、空结果和事务回滚集成测试。"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.models import MaterialRow
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.synthetic.generator import generate_synthetic_dataset

FIXED_TIME = datetime(2026, 8, 12, tzinfo=UTC)


@pytest.fixture
def repository():
    """每个测试使用独立内存 SQLite，避免状态互相污染。"""
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    with session_scope(engine) as session:
        yield SqlAlchemyInventoryRepository(session)
    engine.dispose()


def test_repository_persists_all_five_entity_types(repository) -> None:
    """固定 seed 数据写入后，五类表记录数必须与领域数据集一致。"""
    dataset = generate_synthetic_dataset(seed=20260812, generated_at=FIXED_TIME)

    repository.add_dataset(dataset)

    assert repository.count_records() == {
        "materials": len(dataset.materials),
        "warehouses": len(dataset.warehouses),
        "movements": len(dataset.movements),
        "purchase_orders": len(dataset.purchase_orders),
        "production_orders": len(dataset.production_orders),
    }


def test_repository_round_trips_domain_entities(repository) -> None:
    """数据库行转换回 Pydantic 实体后，应保留 Decimal、枚举和日期类型。"""
    dataset = generate_synthetic_dataset(seed=7, generated_at=FIXED_TIME)
    repository.add_dataset(dataset)

    material = repository.get_material("MAT-SYN-NORMAL")
    warehouse = repository.get_warehouse("WH-SYN-01")

    assert material is not None
    assert material.material_id == "MAT-SYN-NORMAL"
    assert material.unit_cost == next(
        item.unit_cost for item in dataset.materials if item.material_id == material.material_id
    )
    assert warehouse is not None
    assert warehouse.region == "华东"


def test_repository_filters_movements_by_material_warehouse_and_as_of_date(repository) -> None:
    """查询只能返回指定粒度且不晚于分析日期的已过账流水。"""
    dataset = generate_synthetic_dataset(seed=7, generated_at=FIXED_TIME)
    repository.add_dataset(dataset)

    movements = repository.list_movements(
        material_id="MAT-SYN-NORMAL",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 1),
    )

    assert movements
    assert all(item.material_id == "MAT-SYN-NORMAL" for item in movements)
    assert all(item.warehouse_id == "WH-SYN-01" for item in movements)
    assert all(item.posted_at.date() <= date(2026, 3, 1) for item in movements)


def test_repository_returns_none_and_empty_lists_for_missing_data(repository) -> None:
    """查询成功但没有匹配记录不是数据库错误，应使用 None 或空集合表达。"""
    assert repository.get_material("MAT-SYN-MISSING") is None
    assert repository.list_movements(
        material_id="MAT-SYN-MISSING",
        warehouse_id="WH-SYN-01",
        as_of_date=date(2026, 3, 31),
    ) == []
    assert repository.list_purchase_orders("MAT-SYN-MISSING", "WH-SYN-01") == []
    assert repository.list_production_orders("MAT-SYN-MISSING", "WH-SYN-01") == []


def test_repository_lists_materials_with_optional_category_filter(repository) -> None:
    """风险清单所需的物料查询支持类别筛选，并保持主键稳定排序。"""
    dataset = generate_synthetic_dataset(seed=7, generated_at=FIXED_TIME)
    repository.add_dataset(dataset)

    materials = repository.list_materials(category="场景物料")

    assert len(materials) == 6
    assert all(material.category == "场景物料" for material in materials)
    assert [item.material_id for item in materials] == sorted(
        item.material_id for item in materials
    )


def test_session_scope_rolls_back_failed_transaction() -> None:
    """同一事务出现主键冲突时，之前尚未提交的写入也必须回滚。"""
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)

    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add(MaterialRow(
                material_id="MAT-SYN-DUP",
                name="第一次写入",
                category="测试",
                unit_cost="1.00",
                created_date=date(2026, 1, 1),
                synthetic=True,
            ))
            session.add(MaterialRow(
                material_id="MAT-SYN-DUP",
                name="重复写入",
                category="测试",
                unit_cost="2.00",
                created_date=date(2026, 1, 2),
                synthetic=True,
            ))

    with session_scope(engine) as session:
        count = session.scalar(select(func.count()).select_from(MaterialRow))
        assert count == 0
    engine.dispose()
