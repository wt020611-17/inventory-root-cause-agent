"""五个领域实体的正常、边界和非法输入测试。

测试先固定实体自己的输入契约：非空标识、Decimal 数量、日期关系和状态组合。
跨记录的主键唯一与外键存在性需要同时观察多条数据，因此留给后续数据集质量检查。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.entities import (
    InventoryMovement,
    Material,
    ProductionOrder,
    PurchaseOrder,
    Warehouse,
)
from app.domain.enums import MovementType, ProductionOrderStatus, PurchaseOrderStatus


def test_material_accepts_decimal_cost_and_strips_text() -> None:
    """物料应保留明确的 Decimal 成本，并清理文本两侧空白。"""
    material = Material(
        material_id=" MAT-SYN-001 ",
        name=" 合成轴承 ",
        category=" 机械件 ",
        unit_cost="12.50",
        created_date=date(2026, 1, 1),
    )

    assert material.material_id == "MAT-SYN-001"
    assert material.name == "合成轴承"
    assert material.unit_cost == Decimal("12.50")
    assert isinstance(material.unit_cost, Decimal)


def test_material_allows_zero_cost_boundary() -> None:
    """免费样品或尚未计价的合成物料允许使用零成本。"""
    material = Material(
        material_id="MAT-SYN-002",
        name="合成样品",
        category="样品",
        unit_cost=Decimal("0"),
        created_date=date(2026, 1, 2),
    )

    assert material.unit_cost == Decimal("0")


def test_material_allows_missing_cost_for_quantity_only_analysis() -> None:
    """单位成本缺失时仍可保存物料，后续金额指标应标记不完整。"""
    material = Material(
        material_id="MAT-SYN-NO-COST",
        name="待计价合成物料",
        category="测试",
        unit_cost=None,
        created_date=date(2026, 1, 2),
    )

    assert material.unit_cost is None


def test_material_rejects_negative_cost() -> None:
    """负成本属于数据质量错误，必须在实体入口拒绝。"""
    with pytest.raises(ValidationError):
        Material(
            material_id="MAT-SYN-003",
            name="错误成本物料",
            category="测试",
            unit_cost=Decimal("-0.01"),
            created_date=date(2026, 1, 3),
        )


@pytest.mark.parametrize("field_name", ["material_id", "name", "category"])
def test_material_rejects_blank_required_text(field_name: str) -> None:
    """物料标识、名称和类别不能是空白字符串。"""
    data = {
        "material_id": "MAT-SYN-004",
        "name": "合成物料",
        "category": "测试",
        "unit_cost": Decimal("1"),
        "created_date": date(2026, 1, 4),
    }
    data[field_name] = "   "

    with pytest.raises(ValidationError):
        Material(**data)


def test_warehouse_accepts_non_blank_master_data() -> None:
    """仓库实体只保存最小主数据，并清理文本空白。"""
    warehouse = Warehouse(
        warehouse_id=" WH-SYN-01 ",
        name=" 合成华东仓 ",
        region=" 华东 ",
    )

    assert warehouse.model_dump() == {
        "synthetic": True,
        "warehouse_id": "WH-SYN-01",
        "name": "合成华东仓",
        "region": "华东",
    }


@pytest.mark.parametrize("field_name", ["warehouse_id", "name", "region"])
def test_warehouse_rejects_blank_required_text(field_name: str) -> None:
    """仓库标识、名称和区域都必须包含有效文本。"""
    data = {
        "warehouse_id": "WH-SYN-01",
        "name": "合成仓库",
        "region": "华东",
    }
    data[field_name] = ""

    with pytest.raises(ValidationError):
        Warehouse(**data)


def test_inventory_movement_accepts_signed_non_zero_quantity() -> None:
    """出库流水使用负数量，并保留枚举和带时区的过账时间。"""
    movement = InventoryMovement(
        movement_id="MOV-SYN-001",
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        movement_type=MovementType.SALES_ISSUE,
        quantity="-5.5",
        posted_at=datetime(2026, 2, 1, 8, 30, tzinfo=UTC),
        source_doc_id="SO-SYN-001",
    )

    assert movement.quantity == Decimal("-5.5")
    assert movement.movement_type is MovementType.SALES_ISSUE
    assert movement.posted_at.tzinfo is not None


def test_inventory_movement_allows_missing_source_document() -> None:
    """盘点调整可能没有外部来源单据，因此来源标识允许为空。"""
    movement = InventoryMovement(
        movement_id="MOV-SYN-002",
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        movement_type=MovementType.ADJUSTMENT,
        quantity=Decimal("1"),
        posted_at=datetime(2026, 2, 1, 9, 0, tzinfo=UTC),
    )

    assert movement.source_doc_id is None


def test_inventory_movement_rejects_zero_quantity() -> None:
    """零数量流水不会改变库存且没有分析价值，应直接拒绝。"""
    with pytest.raises(ValidationError):
        InventoryMovement(
            movement_id="MOV-SYN-003",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            movement_type=MovementType.ADJUSTMENT,
            quantity=Decimal("0"),
            posted_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        )


def test_inventory_movement_rejects_unknown_movement_type() -> None:
    """实体只能接收受控移动枚举，不能让未知字符串进入库存聚合。"""
    with pytest.raises(ValidationError):
        InventoryMovement(
            movement_id="MOV-SYN-004",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            movement_type="UNKNOWN_MOVEMENT",
            quantity=Decimal("1"),
            posted_at=datetime(2026, 2, 1, 10, 30, tzinfo=UTC),
        )


def test_inventory_movement_rejects_blank_source_document_when_supplied() -> None:
    """来源单据可以缺省，但一旦提供就不能只是空白。"""
    with pytest.raises(ValidationError):
        InventoryMovement(
            movement_id="MOV-SYN-005",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            movement_type=MovementType.PURCHASE_RECEIPT,
            quantity=Decimal("10"),
            posted_at=datetime(2026, 2, 1, 11, 0, tzinfo=UTC),
            source_doc_id="   ",
        )


def test_purchase_order_accepts_partial_receipt() -> None:
    """部分收货必须大于零、小于订购量，并具有实际收货日期。"""
    order = PurchaseOrder(
        po_id="PO-SYN-001",
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        ordered_qty="10",
        received_qty="4",
        planned_date=date(2026, 2, 10),
        actual_date=date(2026, 2, 8),
        status=PurchaseOrderStatus.PARTIALLY_RECEIVED,
    )

    assert order.ordered_qty == Decimal("10")
    assert order.received_qty == Decimal("4")
    assert order.actual_date == date(2026, 2, 8)


def test_purchase_order_accepts_planned_zero_receipt_boundary() -> None:
    """计划状态尚未收货，零收货量和空实际日期是合法边界。"""
    order = PurchaseOrder(
        po_id="PO-SYN-002",
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        ordered_qty=Decimal("10"),
        received_qty=Decimal("0"),
        planned_date=date(2026, 2, 10),
        actual_date=None,
        status=PurchaseOrderStatus.PLANNED,
    )

    assert order.received_qty == Decimal("0")


@pytest.mark.parametrize(
    ("ordered_qty", "received_qty"),
    [
        (Decimal("0"), Decimal("0")),
        (Decimal("10"), Decimal("-1")),
        (Decimal("10"), Decimal("11")),
    ],
)
def test_purchase_order_rejects_invalid_quantities(
    ordered_qty: Decimal,
    received_qty: Decimal,
) -> None:
    """订购量必须为正，收货量必须位于零和订购量之间。"""
    with pytest.raises(ValidationError):
        PurchaseOrder(
            po_id="PO-SYN-003",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            ordered_qty=ordered_qty,
            received_qty=received_qty,
            planned_date=date(2026, 2, 10),
            actual_date=None,
            status=PurchaseOrderStatus.PLANNED,
        )


@pytest.mark.parametrize(
    ("status", "received_qty", "actual_date"),
    [
        (PurchaseOrderStatus.PLANNED, Decimal("1"), None),
        (PurchaseOrderStatus.PLANNED, Decimal("0"), date(2026, 2, 10)),
        (PurchaseOrderStatus.PARTIALLY_RECEIVED, Decimal("0"), date(2026, 2, 10)),
        (PurchaseOrderStatus.PARTIALLY_RECEIVED, Decimal("5"), None),
        (PurchaseOrderStatus.RECEIVED, Decimal("9"), date(2026, 2, 10)),
        (PurchaseOrderStatus.RECEIVED, Decimal("10"), None),
    ],
)
def test_purchase_order_rejects_inconsistent_status_combinations(
    status: PurchaseOrderStatus,
    received_qty: Decimal,
    actual_date: date | None,
) -> None:
    """采购状态、累计收货量和实际收货日期必须表达同一个业务事实。"""
    with pytest.raises(ValidationError):
        PurchaseOrder(
            po_id="PO-SYN-004",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            ordered_qty=Decimal("10"),
            received_qty=received_qty,
            planned_date=date(2026, 2, 10),
            actual_date=actual_date,
            status=status,
        )


def test_production_order_accepts_planned_zero_consumption_boundary() -> None:
    """计划消耗量允许为零，但这类订单不会命中生产延期根因。"""
    order = ProductionOrder(
        production_order_id="PRD-SYN-001",
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        planned_consumption_qty=Decimal("0"),
        status=ProductionOrderStatus.PLANNED,
        planned_start=date(2026, 3, 1),
        actual_start=None,
        due_date=date(2026, 3, 5),
        closed_at=None,
    )

    assert order.planned_consumption_qty == Decimal("0")


def test_production_order_accepts_closed_date_sequence() -> None:
    """关闭订单需要实际开始与关闭日期，并保持开始、到期、关闭顺序可解释。"""
    order = ProductionOrder(
        production_order_id="PRD-SYN-002",
        material_id="MAT-SYN-001",
        warehouse_id="WH-SYN-01",
        planned_consumption_qty="25",
        status=ProductionOrderStatus.CLOSED,
        planned_start=date(2026, 3, 1),
        actual_start=date(2026, 3, 3),
        due_date=date(2026, 3, 6),
        closed_at=date(2026, 3, 7),
    )

    assert order.planned_consumption_qty == Decimal("25")
    assert order.closed_at == date(2026, 3, 7)


@pytest.mark.parametrize(
    ("status", "actual_start", "closed_at"),
    [
        (ProductionOrderStatus.PLANNED, date(2026, 3, 2), None),
        (ProductionOrderStatus.RELEASED, date(2026, 3, 2), None),
        (ProductionOrderStatus.IN_PROGRESS, None, None),
        (ProductionOrderStatus.CLOSED, None, date(2026, 3, 7)),
        (ProductionOrderStatus.CLOSED, date(2026, 3, 2), None),
        (ProductionOrderStatus.CANCELLED, None, date(2026, 3, 7)),
    ],
)
def test_production_order_rejects_inconsistent_status_dates(
    status: ProductionOrderStatus,
    actual_start: date | None,
    closed_at: date | None,
) -> None:
    """生产状态必须和是否已实际开始、是否已关闭保持一致。"""
    with pytest.raises(ValidationError):
        ProductionOrder(
            production_order_id="PRD-SYN-003",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            planned_consumption_qty=Decimal("10"),
            status=status,
            planned_start=date(2026, 3, 1),
            actual_start=actual_start,
            due_date=date(2026, 3, 6),
            closed_at=closed_at,
        )


@pytest.mark.parametrize(
    ("planned_start", "actual_start", "due_date", "closed_at"),
    [
        (date(2026, 3, 2), None, date(2026, 3, 1), None),
        (
            date(2026, 3, 1),
            date(2026, 3, 5),
            date(2026, 3, 6),
            date(2026, 3, 4),
        ),
    ],
)
def test_production_order_rejects_invalid_date_order(
    planned_start: date,
    actual_start: date | None,
    due_date: date,
    closed_at: date | None,
) -> None:
    """到期不能早于计划开始，关闭也不能早于实际开始。"""
    status = ProductionOrderStatus.CLOSED if closed_at else ProductionOrderStatus.PLANNED

    with pytest.raises(ValidationError):
        ProductionOrder(
            production_order_id="PRD-SYN-004",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            planned_consumption_qty=Decimal("10"),
            status=status,
            planned_start=planned_start,
            actual_start=actual_start,
            due_date=due_date,
            closed_at=closed_at,
        )


def test_production_order_rejects_negative_planned_consumption() -> None:
    """生产计划消耗数量允许为零，但不能为负数。"""
    with pytest.raises(ValidationError):
        ProductionOrder(
            production_order_id="PRD-SYN-005",
            material_id="MAT-SYN-001",
            warehouse_id="WH-SYN-01",
            planned_consumption_qty=Decimal("-0.01"),
            status=ProductionOrderStatus.PLANNED,
            planned_start=date(2026, 3, 1),
            actual_start=None,
            due_date=date(2026, 3, 6),
            closed_at=None,
        )


@pytest.mark.parametrize(
    ("model_type", "valid_data"),
    [
        (
            Material,
            {
                "material_id": "MAT-SYN-001",
                "name": "合成物料",
                "category": "测试",
                "unit_cost": Decimal("1"),
                "created_date": date(2026, 1, 1),
            },
        ),
        (
            Warehouse,
            {
                "warehouse_id": "WH-SYN-01",
                "name": "合成仓库",
                "region": "华东",
            },
        ),
        (
            InventoryMovement,
            {
                "movement_id": "MOV-SYN-001",
                "material_id": "MAT-SYN-001",
                "warehouse_id": "WH-SYN-01",
                "movement_type": MovementType.ADJUSTMENT,
                "quantity": Decimal("1"),
                "posted_at": datetime(2026, 2, 1, tzinfo=UTC),
            },
        ),
        (
            PurchaseOrder,
            {
                "po_id": "PO-SYN-001",
                "material_id": "MAT-SYN-001",
                "warehouse_id": "WH-SYN-01",
                "ordered_qty": Decimal("10"),
                "received_qty": Decimal("0"),
                "planned_date": date(2026, 2, 10),
                "actual_date": None,
                "status": PurchaseOrderStatus.PLANNED,
            },
        ),
        (
            ProductionOrder,
            {
                "production_order_id": "PRD-SYN-001",
                "material_id": "MAT-SYN-001",
                "warehouse_id": "WH-SYN-01",
                "planned_consumption_qty": Decimal("10"),
                "status": ProductionOrderStatus.PLANNED,
                "planned_start": date(2026, 3, 1),
                "actual_start": None,
                "due_date": date(2026, 3, 6),
                "closed_at": None,
            },
        ),
    ],
)
def test_all_entities_reject_unknown_fields(model_type: type, valid_data: dict) -> None:
    """五个实体都应拒绝拼错或尚未声明的输入字段。"""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model_type(**valid_data, unknown_field="unexpected")


@pytest.mark.parametrize(
    "model_type",
    [Material, Warehouse, InventoryMovement, PurchaseOrder, ProductionOrder],
)
def test_all_entities_reject_non_synthetic_records(model_type: type) -> None:
    """五个持久化实体都不能接受 `synthetic=false`，防止真实记录混入项目。"""
    field_defaults = {
        Material: {
            "material_id": "MAT-SYN-001",
            "name": "合成物料",
            "category": "测试",
            "unit_cost": Decimal("1"),
            "created_date": date(2026, 1, 1),
        },
        Warehouse: {
            "warehouse_id": "WH-SYN-01",
            "name": "合成仓库",
            "region": "华东",
        },
        InventoryMovement: {
            "movement_id": "MOV-SYN-001",
            "material_id": "MAT-SYN-001",
            "warehouse_id": "WH-SYN-01",
            "movement_type": MovementType.ADJUSTMENT,
            "quantity": Decimal("1"),
            "posted_at": datetime(2026, 2, 1, tzinfo=UTC),
        },
        PurchaseOrder: {
            "po_id": "PO-SYN-001",
            "material_id": "MAT-SYN-001",
            "warehouse_id": "WH-SYN-01",
            "ordered_qty": Decimal("10"),
            "received_qty": Decimal("0"),
            "planned_date": date(2026, 2, 10),
            "actual_date": None,
            "status": PurchaseOrderStatus.PLANNED,
        },
        ProductionOrder: {
            "production_order_id": "PRD-SYN-001",
            "material_id": "MAT-SYN-001",
            "warehouse_id": "WH-SYN-01",
            "planned_consumption_qty": Decimal("10"),
            "status": ProductionOrderStatus.PLANNED,
            "planned_start": date(2026, 3, 1),
            "actual_start": None,
            "due_date": date(2026, 3, 6),
            "closed_at": None,
        },
    }

    with pytest.raises(ValidationError):
        model_type(**field_defaults[model_type], synthetic=False)
