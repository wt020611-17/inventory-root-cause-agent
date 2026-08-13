"""固定 seed 的纯合成库存数据生成器。

七个关键场景由明确标签和固定业务模式构造；随机数只用于填充非关键物料、成本和日期，
避免测试依赖“随机碰巧”产生风险。所有标识均带 `SYN`，所有实体均强制 synthetic=true。
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from random import Random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities import (
    InventoryMovement,
    Material,
    ProductionOrder,
    PurchaseOrder,
    Warehouse,
)
from app.domain.enums import (
    InventoryRiskLevel,
    MovementType,
    ProductionOrderStatus,
    PurchaseOrderStatus,
    ResultStatus,
    RootCauseType,
)


class ScenarioTarget(BaseModel):
    """把场景标签与可查询的物料仓库、预期状态和根因显式绑定。"""

    model_config = ConfigDict(extra="forbid")

    material_id: str = Field(min_length=1)
    warehouse_id: str = Field(min_length=1)
    expected_status: ResultStatus
    expected_risk: InventoryRiskLevel | None = None
    expected_causes: list[RootCauseType] = Field(default_factory=list)


class SyntheticDataset(BaseModel):
    """一次可持久化的完整合成数据集及其场景元数据。"""

    model_config = ConfigDict(extra="forbid")

    synthetic: Literal[True] = True
    dataset_version: str = Field(min_length=1)
    seed: int
    generated_at: datetime
    as_of_date: date
    materials: list[Material]
    warehouses: list[Warehouse]
    movements: list[InventoryMovement]
    purchase_orders: list[PurchaseOrder]
    production_orders: list[ProductionOrder]
    scenario_targets: dict[str, ScenarioTarget]

    def business_facts(self) -> dict:
        """返回排除运行时间后的业务快照，用于验证固定 seed 的可复现性。"""
        return self.model_dump(
            mode="json",
            exclude={"generated_at"},
        )


def _at(day: date, hour: int = 8) -> datetime:
    """把业务日期转换为固定 UTC 过账时间，避免本机时区影响事实。"""
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def generate_synthetic_dataset(
    *,
    seed: int,
    generated_at: datetime | None = None,
    dataset_version: str = "mvp-v0.2",
) -> SyntheticDataset:
    """生成包含七个显式场景的 Phase 1 基线数据集。"""
    rng = Random(seed)
    generated_at = generated_at or datetime.now(UTC)
    as_of_date = date(2026, 3, 31)

    warehouses = [
        Warehouse(warehouse_id="WH-SYN-01", name="合成华东仓", region="华东"),
        Warehouse(warehouse_id="WH-SYN-02", name="合成华南仓", region="华南"),
    ]

    scenario_materials = [
        ("MAT-SYN-NORMAL", "持续消耗物料"),
        ("MAT-SYN-DEMAND", "需求下降物料"),
        ("MAT-SYN-OVERBUY", "超量采购物料"),
        ("MAT-SYN-PROD", "生产延期物料"),
        ("MAT-SYN-MULTI", "多根因物料"),
        ("MAT-SYN-BLOCKED", "质量阻断物料"),
    ]
    materials = [
        Material(
            material_id=material_id,
            name=name,
            category="场景物料",
            unit_cost=Decimal(str(rng.randint(10, 80))),
            created_date=date(2025, 1, 1),
        )
        for material_id, name in scenario_materials
    ]
    for index in range(7, 19):
        materials.append(
            Material(
                material_id=f"MAT-SYN-{index:03d}",
                name=f"合成填充物料{index:03d}",
                category="通用零件" if index % 2 else "电子元件",
                unit_cost=Decimal(str(rng.randint(5, 120))),
                created_date=date(2025, 1, 1) + timedelta(days=rng.randint(0, 180)),
            )
        )

    movements: list[InventoryMovement] = []

    def add_movement(
        material_id: str,
        movement_type: MovementType,
        quantity: Decimal | int | str,
        posted_date: date,
        source_doc_id: str | None,
    ) -> None:
        movements.append(
            InventoryMovement(
                movement_id=f"MOV-SYN-{len(movements) + 1:04d}",
                material_id=material_id,
                warehouse_id="WH-SYN-01",
                movement_type=movement_type,
                quantity=quantity,
                posted_at=_at(posted_date),
                source_doc_id=source_doc_id,
            )
        )

    # 六个核心物料先建立库存；只有明确持久化的采购单使用 PO-SYN 来源引用。
    receipt_sources = {
        "MAT-SYN-OVERBUY": "PO-SYN-OVERBUY",
        "MAT-SYN-MULTI": "PO-SYN-MULTI",
    }
    for material_id, _ in scenario_materials[:-1]:
        add_movement(
            material_id,
            MovementType.PURCHASE_RECEIPT,
            300,
            date(2025, 9, 1),
            receipt_sources.get(material_id),
        )
    add_movement(
        "MAT-SYN-BLOCKED",
        MovementType.PURCHASE_RECEIPT,
        10,
        date(2026, 1, 1),
        None,
    )
    add_movement("MAT-SYN-BLOCKED", MovementType.SALES_ISSUE, -20, date(2026, 2, 1), "SO-SYN-BLOCK")

    # 正常场景在分析日前持续消耗；需求下降场景前窗多、近窗少。
    for offset in range(0, 180, 10):
        add_movement(
            "MAT-SYN-NORMAL",
            MovementType.SALES_ISSUE,
            -10,
            as_of_date - timedelta(days=offset),
            f"SO-SYN-NORMAL-{offset:03d}",
        )
    for offset in range(181, 271, 10):
        add_movement(
            "MAT-SYN-DEMAND",
            MovementType.SALES_ISSUE,
            -10,
            as_of_date - timedelta(days=offset),
            f"SO-SYN-DEMAND-OLD-{offset:03d}",
        )
    for offset in (91, 120, 150):
        add_movement(
            "MAT-SYN-DEMAND",
            MovementType.SALES_ISSUE,
            -2,
            as_of_date - timedelta(days=offset),
            f"SO-SYN-DEMAND-NEW-{offset:03d}",
        )

    # 为剩余记录补足确定规模。不同 seed 会改变物料选择、数量与日期，但都保持引用有效。
    filler_ids = [material.material_id for material in materials[6:]]
    filler_stock = {material_id: 0 for material_id in filler_ids}
    while len(movements) < 360:
        material_id = rng.choice(filler_ids)
        day_offset = rng.randint(1, 300)
        # 没有库存时强制先入库；有库存时出库量也不超过当前余额。
        is_receipt = filler_stock[material_id] == 0 or rng.random() < 0.55
        movement_type = (
            MovementType.PURCHASE_RECEIPT if is_receipt else MovementType.SALES_ISSUE
        )
        if is_receipt:
            quantity = rng.randint(1, 20)
        else:
            quantity = -rng.randint(1, min(20, filler_stock[material_id]))
        filler_stock[material_id] += quantity
        add_movement(
            material_id,
            movement_type,
            quantity,
            as_of_date - timedelta(days=day_offset),
            f"DOC-SYN-{len(movements) + 1:04d}",
        )

    purchase_orders = [
        PurchaseOrder(
            po_id="PO-SYN-OVERBUY",
            material_id="MAT-SYN-OVERBUY",
            warehouse_id="WH-SYN-01",
            ordered_qty=Decimal("500"),
            received_qty=Decimal("500"),
            planned_date=date(2025, 9, 1),
            actual_date=date(2025, 9, 1),
            status=PurchaseOrderStatus.RECEIVED,
        ),
        PurchaseOrder(
            po_id="PO-SYN-MULTI",
            material_id="MAT-SYN-MULTI",
            warehouse_id="WH-SYN-01",
            ordered_qty=Decimal("500"),
            received_qty=Decimal("500"),
            planned_date=date(2025, 9, 1),
            actual_date=date(2025, 9, 2),
            status=PurchaseOrderStatus.RECEIVED,
        ),
    ]
    while len(purchase_orders) < 24:
        index = len(purchase_orders) + 1
        quantity = Decimal(str(rng.randint(20, 100)))
        purchase_orders.append(
            PurchaseOrder(
                po_id=f"PO-SYN-{index:03d}",
                material_id=rng.choice(filler_ids),
                warehouse_id=rng.choice(["WH-SYN-01", "WH-SYN-02"]),
                ordered_qty=quantity,
                received_qty=quantity,
                planned_date=date(2026, 1, 1) + timedelta(days=index),
                actual_date=date(2026, 1, 2) + timedelta(days=index),
                status=PurchaseOrderStatus.RECEIVED,
            )
        )

    production_orders = [
        ProductionOrder(
            production_order_id="PRD-SYN-DELAY",
            material_id="MAT-SYN-PROD",
            warehouse_id="WH-SYN-01",
            planned_consumption_qty=Decimal("80"),
            status=ProductionOrderStatus.RELEASED,
            planned_start=date(2026, 3, 1),
            actual_start=None,
            due_date=date(2026, 3, 20),
            closed_at=None,
        ),
        ProductionOrder(
            production_order_id="PRD-SYN-MULTI",
            material_id="MAT-SYN-MULTI",
            warehouse_id="WH-SYN-01",
            planned_consumption_qty=Decimal("100"),
            status=ProductionOrderStatus.RELEASED,
            planned_start=date(2026, 2, 20),
            actual_start=None,
            due_date=date(2026, 3, 15),
            closed_at=None,
        ),
    ]
    while len(production_orders) < 18:
        index = len(production_orders) + 1
        planned_start = date(2026, 1, 1) + timedelta(days=index)
        production_orders.append(
            ProductionOrder(
                production_order_id=f"PRD-SYN-{index:03d}",
                material_id=rng.choice(filler_ids),
                warehouse_id=rng.choice(["WH-SYN-01", "WH-SYN-02"]),
                planned_consumption_qty=Decimal(str(rng.randint(5, 50))),
                status=ProductionOrderStatus.CLOSED,
                planned_start=planned_start,
                actual_start=planned_start,
                due_date=planned_start + timedelta(days=5),
                closed_at=planned_start + timedelta(days=4),
            )
        )

    scenario_targets = {
        "SYN-NORMAL-01": ScenarioTarget(
            material_id="MAT-SYN-NORMAL",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.OK,
            expected_risk=InventoryRiskLevel.NORMAL,
        ),
        "SYN-DEMAND-DROP-01": ScenarioTarget(
            material_id="MAT-SYN-DEMAND",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.OK,
            expected_risk=InventoryRiskLevel.SLOW_MOVING,
            expected_causes=[RootCauseType.DEMAND_DROP],
        ),
        "SYN-OVERBUY-01": ScenarioTarget(
            material_id="MAT-SYN-OVERBUY",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.OK,
            expected_risk=InventoryRiskLevel.NON_MOVING,
            expected_causes=[RootCauseType.PURCHASE_EXCESS],
        ),
        "SYN-PROD-DELAY-01": ScenarioTarget(
            material_id="MAT-SYN-PROD",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.OK,
            expected_risk=InventoryRiskLevel.NON_MOVING,
            expected_causes=[RootCauseType.PRODUCTION_DELAY],
        ),
        "SYN-MULTI-CAUSE-01": ScenarioTarget(
            material_id="MAT-SYN-MULTI",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.OK,
            expected_risk=InventoryRiskLevel.NON_MOVING,
            expected_causes=[RootCauseType.PURCHASE_EXCESS, RootCauseType.PRODUCTION_DELAY],
        ),
        "SYN-EMPTY-01": ScenarioTarget(
            material_id="MAT-SYN-EMPTY",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.EMPTY,
        ),
        "SYN-BLOCKED-01": ScenarioTarget(
            material_id="MAT-SYN-BLOCKED",
            warehouse_id="WH-SYN-01",
            expected_status=ResultStatus.BLOCKED,
            expected_risk=InventoryRiskLevel.DATA_QUALITY_BLOCKED,
        ),
    }

    return SyntheticDataset(
        dataset_version=dataset_version,
        seed=seed,
        generated_at=generated_at,
        as_of_date=as_of_date,
        materials=materials,
        warehouses=warehouses,
        movements=movements,
        purchase_orders=purchase_orders,
        production_orders=production_orders,
        scenario_targets=scenario_targets,
    )
