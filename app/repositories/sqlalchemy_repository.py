"""库存领域 Repository 的 SQLAlchemy/SQLite 实现。"""

from datetime import UTC, date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.entities import (
    InventoryMovement,
    Material,
    ProductionOrder,
    PurchaseOrder,
    Warehouse,
)
from app.repositories.models import (
    InventoryMovementRow,
    MaterialRow,
    ProductionOrderRow,
    PurchaseOrderRow,
    WarehouseRow,
)
from app.synthetic.generator import SyntheticDataset


class SqlAlchemyInventoryRepository:
    """持久化并还原领域实体；只负责数据访问，不实现风险判断。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_dataset(self, dataset: SyntheticDataset) -> None:
        """在当前事务中批量写入完整合成数据集。"""
        self._session.add_all(MaterialRow(**item.model_dump()) for item in dataset.materials)
        self._session.add_all(WarehouseRow(**item.model_dump()) for item in dataset.warehouses)
        self._session.add_all(
            InventoryMovementRow(
                **item.model_dump(exclude={"movement_type"}),
                movement_type=item.movement_type.value,
            )
            for item in dataset.movements
        )
        self._session.add_all(
            PurchaseOrderRow(
                **item.model_dump(exclude={"status"}),
                status=item.status.value,
            )
            for item in dataset.purchase_orders
        )
        self._session.add_all(
            ProductionOrderRow(
                **item.model_dump(exclude={"status"}),
                status=item.status.value,
            )
            for item in dataset.production_orders
        )
        self._session.flush()

    def count_records(self) -> dict[str, int]:
        """返回五张表记录数，供初始化命令和测试核对。"""
        rows = {
            "materials": MaterialRow,
            "warehouses": WarehouseRow,
            "movements": InventoryMovementRow,
            "purchase_orders": PurchaseOrderRow,
            "production_orders": ProductionOrderRow,
        }
        return {
            name: self._session.scalar(select(func.count()).select_from(model)) or 0
            for name, model in rows.items()
        }

    def get_material(self, material_id: str) -> Material | None:
        """按主键读取物料；无匹配时返回 None。"""
        row = self._session.get(MaterialRow, material_id)
        return Material.model_validate(row, from_attributes=True) if row else None

    def get_warehouse(self, warehouse_id: str) -> Warehouse | None:
        """按主键读取仓库；无匹配时返回 None。"""
        row = self._session.get(WarehouseRow, warehouse_id)
        return Warehouse.model_validate(row, from_attributes=True) if row else None

    def list_materials(self, category: str | None = None) -> list[Material]:
        """读取物料清单；提供类别时在数据库层过滤。"""
        statement = select(MaterialRow)
        if category is not None:
            statement = statement.where(MaterialRow.category == category)
        statement = statement.order_by(MaterialRow.material_id)
        return [
            Material.model_validate(row, from_attributes=True)
            for row in self._session.scalars(statement)
        ]

    def list_warehouses(self) -> list[Warehouse]:
        """读取全部仓库并按主键排序。"""
        statement = select(WarehouseRow).order_by(WarehouseRow.warehouse_id)
        return [
            Warehouse.model_validate(row, from_attributes=True)
            for row in self._session.scalars(statement)
        ]

    def list_movements(
        self,
        *,
        material_id: str,
        warehouse_id: str,
        as_of_date: date,
    ) -> list[InventoryMovement]:
        """按物料、仓库和分析日期读取流水，并按过账时间排序。"""
        end_of_day = datetime.combine(as_of_date, time.max, tzinfo=UTC)
        statement = (
            select(InventoryMovementRow)
            .where(
                InventoryMovementRow.material_id == material_id,
                InventoryMovementRow.warehouse_id == warehouse_id,
                InventoryMovementRow.posted_at <= end_of_day,
            )
            .order_by(InventoryMovementRow.posted_at, InventoryMovementRow.movement_id)
        )
        return [
            InventoryMovement.model_validate(row, from_attributes=True)
            for row in self._session.scalars(statement)
        ]

    def list_purchase_orders(
        self,
        material_id: str,
        warehouse_id: str,
    ) -> list[PurchaseOrder]:
        """读取物料仓库相关采购订单。"""
        statement = select(PurchaseOrderRow).where(
            PurchaseOrderRow.material_id == material_id,
            PurchaseOrderRow.warehouse_id == warehouse_id,
        )
        return [
            PurchaseOrder.model_validate(row, from_attributes=True)
            for row in self._session.scalars(statement)
        ]

    def list_production_orders(
        self,
        material_id: str,
        warehouse_id: str,
    ) -> list[ProductionOrder]:
        """读取物料仓库相关生产订单。"""
        statement = select(ProductionOrderRow).where(
            ProductionOrderRow.material_id == material_id,
            ProductionOrderRow.warehouse_id == warehouse_id,
        )
        return [
            ProductionOrder.model_validate(row, from_attributes=True)
            for row in self._session.scalars(statement)
        ]
