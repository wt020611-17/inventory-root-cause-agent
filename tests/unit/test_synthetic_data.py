"""固定 seed 合成数据生成器的规模、场景与可复现性测试。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.synthetic.generator import generate_synthetic_dataset

FIXED_GENERATED_AT = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)


def test_generator_creates_expected_mvp_scale_and_metadata() -> None:
    """基线数据量必须落在文档范围，并记录版本、种子和生成时间。"""
    dataset = generate_synthetic_dataset(seed=20260812, generated_at=FIXED_GENERATED_AT)

    assert dataset.synthetic is True
    assert dataset.dataset_version == "mvp-v0.2"
    assert dataset.seed == 20260812
    assert dataset.generated_at == FIXED_GENERATED_AT
    assert 15 <= len(dataset.materials) <= 20
    assert len(dataset.warehouses) == 2
    assert 300 <= len(dataset.movements) <= 600
    assert 20 <= len(dataset.purchase_orders) <= 30
    assert 15 <= len(dataset.production_orders) <= 25


def test_same_version_and_seed_produce_identical_business_facts() -> None:
    """生成时间属于运行元数据；固定版本和 seed 的五类记录与场景必须完全相同。"""
    first = generate_synthetic_dataset(
        seed=7,
        generated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    second = generate_synthetic_dataset(
        seed=7,
        generated_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert first.business_facts() == second.business_facts()
    assert first.generated_at != second.generated_at


def test_different_seed_changes_generated_business_facts() -> None:
    """seed 必须真正参与数据生成，而不是只被写入元数据。"""
    first = generate_synthetic_dataset(seed=7, generated_at=FIXED_GENERATED_AT)
    second = generate_synthetic_dataset(seed=8, generated_at=FIXED_GENERATED_AT)

    assert first.business_facts() != second.business_facts()


def test_generator_contains_all_seven_named_scenarios() -> None:
    """测试依赖显式场景标签，不能依赖随机数据碰巧形成风险。"""
    dataset = generate_synthetic_dataset(seed=20260812, generated_at=FIXED_GENERATED_AT)

    assert set(dataset.scenario_targets) == {
        "SYN-NORMAL-01",
        "SYN-DEMAND-DROP-01",
        "SYN-OVERBUY-01",
        "SYN-PROD-DELAY-01",
        "SYN-MULTI-CAUSE-01",
        "SYN-EMPTY-01",
        "SYN-BLOCKED-01",
    }
    assert dataset.scenario_targets["SYN-EMPTY-01"].expected_status.value == "empty"
    assert dataset.scenario_targets["SYN-BLOCKED-01"].expected_status.value == "blocked"


def test_every_persisted_record_is_visibly_synthetic() -> None:
    """五类持久化记录必须同时具有 synthetic=true 和明显的 SYN 标识。"""
    dataset = generate_synthetic_dataset(seed=20260812, generated_at=FIXED_GENERATED_AT)
    records = [
        *dataset.materials,
        *dataset.warehouses,
        *dataset.movements,
        *dataset.purchase_orders,
        *dataset.production_orders,
    ]

    assert all(record.synthetic is True for record in records)
    first_values = [
        next(iter(record.model_dump(exclude={"synthetic"}).values())) for record in records
    ]
    assert all("SYN" in value for value in first_values)


def test_empty_target_is_not_materialized_as_business_data() -> None:
    """空结果场景只保存查询目标，不创建同名物料或流水。"""
    dataset = generate_synthetic_dataset(seed=20260812, generated_at=FIXED_GENERATED_AT)
    empty_target = dataset.scenario_targets["SYN-EMPTY-01"]

    assert empty_target.material_id == "MAT-SYN-EMPTY"
    assert all(material.material_id != empty_target.material_id for material in dataset.materials)
    assert all(movement.material_id != empty_target.material_id for movement in dataset.movements)


def test_dataset_rejects_non_synthetic_marker() -> None:
    """数据集元数据与内部实体一样，不能被改成 synthetic=false。"""
    dataset = generate_synthetic_dataset(seed=7, generated_at=FIXED_GENERATED_AT)

    with pytest.raises(ValidationError):
        type(dataset)(**{**dataset.model_dump(), "synthetic": False})
