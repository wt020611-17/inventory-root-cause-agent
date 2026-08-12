"""整批合成数据的主键、外键、日期、场景和安全质量检查测试。"""

from datetime import UTC, datetime, timedelta

from app.domain.enums import MovementType, ResultStatus
from app.synthetic.generator import generate_synthetic_dataset
from app.synthetic.quality import validate_synthetic_dataset

FIXED_TIME = datetime(2026, 8, 12, tzinfo=UTC)


def make_dataset():
    """每个测试使用独立、可复现的数据集，避免修改相互污染。"""
    return generate_synthetic_dataset(seed=20260812, generated_at=FIXED_TIME)


def test_baseline_reports_only_expected_blocked_scenario() -> None:
    """基线唯一阻断应是专门构造的负库存场景，而不是生成器缺陷。"""
    report = validate_synthetic_dataset(make_dataset())

    assert report.status is ResultStatus.BLOCKED
    assert report.error_count == 0
    assert report.blocker_count == 1
    assert report.issues[0].code == "negative_current_stock"
    assert report.issues[0].record_id == "MAT-SYN-BLOCKED@WH-SYN-01"
    assert report.issues[0].expected is True


def test_duplicate_primary_key_is_reported_as_error() -> None:
    """同类实体主键重复会破坏持久化，应作为生成错误报告。"""
    dataset = make_dataset()
    dataset.materials.append(dataset.materials[0].model_copy())

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "duplicate_primary_key" for issue in report.issues)


def test_dangling_material_reference_is_reported() -> None:
    """流水引用不存在物料时必须阻止写库和后续库存计算。"""
    dataset = make_dataset()
    dataset.movements[0] = dataset.movements[0].model_copy(
        update={"material_id": "MAT-SYN-MISSING"}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "dangling_material_reference" for issue in report.issues)


def test_dangling_warehouse_reference_is_reported() -> None:
    """订单引用不存在仓库时必须明确指出外键问题。"""
    dataset = make_dataset()
    dataset.purchase_orders[0] = dataset.purchase_orders[0].model_copy(
        update={"warehouse_id": "WH-SYN-MISSING"}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "dangling_warehouse_reference" for issue in report.issues)


def test_dangling_persisted_source_document_is_reported() -> None:
    """明确使用 PO-SYN 前缀的来源必须能关联本数据集采购单。"""
    dataset = make_dataset()
    dataset.movements[0] = dataset.movements[0].model_copy(
        update={"source_doc_id": "PO-SYN-MISSING"}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "dangling_source_document" for issue in report.issues)


def test_persisted_source_document_must_match_movement_dimensions() -> None:
    """来源单据即使存在，也必须和流水的物料仓库一致。"""
    dataset = make_dataset()
    dataset.movements[0] = dataset.movements[0].model_copy(
        update={"source_doc_id": "PO-SYN-OVERBUY"}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "source_document_mismatch" for issue in report.issues)


def test_future_movement_is_reported() -> None:
    """晚于分析日期的流水不能进入当前库存事实。"""
    dataset = make_dataset()
    dataset.movements[0] = dataset.movements[0].model_copy(
        update={"posted_at": datetime.combine(
            dataset.as_of_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        )}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "future_dated_movement" for issue in report.issues)


def test_bypassed_invalid_enum_and_quantity_are_reported() -> None:
    """批量导入即使绕过实体构造，质量检查仍要重新执行字段契约。"""
    dataset = make_dataset()
    dataset.movements[0] = dataset.movements[0].model_copy(
        update={"movement_type": "UNKNOWN", "quantity": 0}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "invalid_entity_contract" for issue in report.issues)


def test_missing_first_receipt_without_consumption_is_blocked() -> None:
    """仅有调整流水且无有效消耗时，缺失首次入库必须阻断无消耗天数计算。"""
    dataset = make_dataset()
    target = next(
        movement
        for movement in dataset.movements
        if movement.material_id == "MAT-SYN-OVERBUY"
    )
    target_index = dataset.movements.index(target)
    dataset.movements[target_index] = target.model_copy(
        update={"movement_type": MovementType.ADJUSTMENT, "source_doc_id": None}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.BLOCKED
    assert any(issue.code == "missing_first_receipt" for issue in report.issues)


def test_missing_required_scenario_is_reported() -> None:
    """七个必造场景缺少任意一个都意味着数据集不满足验收契约。"""
    dataset = make_dataset()
    dataset.scenario_targets.pop("SYN-DEMAND-DROP-01")

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "missing_required_scenario" for issue in report.issues)


def test_non_synthetic_or_suspicious_text_is_reported() -> None:
    """即使绕过 Pydantic 构造，也不能让非合成标记或 URL 混入数据快照。"""
    dataset = make_dataset()
    dataset.materials[0] = dataset.materials[0].model_copy(
        update={"synthetic": False, "name": "https://internal.example.invalid"}
    )

    report = validate_synthetic_dataset(dataset)

    codes = {issue.code for issue in report.issues}
    assert "non_synthetic_record" in codes
    assert "suspicious_identifier" in codes


def test_invalid_synthetic_id_prefix_is_reported() -> None:
    """记录虽标记 synthetic=true，主键仍必须使用明显的合成前缀。"""
    dataset = make_dataset()
    dataset.materials[0] = dataset.materials[0].model_copy(
        update={"material_id": "REAL-001"}
    )

    report = validate_synthetic_dataset(dataset)

    assert report.status is ResultStatus.ERROR
    assert any(issue.code == "invalid_synthetic_id_prefix" for issue in report.issues)


def test_every_root_cause_has_positive_and_negative_scenario() -> None:
    """场景标签必须为三类根因同时提供正例与反例。"""
    report = validate_synthetic_dataset(make_dataset())

    assert not any(issue.code == "missing_root_cause_example" for issue in report.issues)
