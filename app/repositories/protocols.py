"""应用服务依赖的 Repository 结构接口，避免直接耦合 SQLAlchemy 实现。"""

from datetime import date
from typing import Protocol

from app.domain.entities import (
    InventoryMovement,
    Material,
    ProductionOrder,
    PurchaseOrder,
    Warehouse,
)


class InventoryRepository(Protocol):
    """库存分析服务所需的最小只读数据访问能力。"""

    def get_material(self, material_id: str) -> Material | None: ...

    def get_warehouse(self, warehouse_id: str) -> Warehouse | None: ...

    def list_materials(self, category: str | None = None) -> list[Material]: ...

    def list_warehouses(self) -> list[Warehouse]: ...

    def list_movements(
        self, *, material_id: str, warehouse_id: str, as_of_date: date
    ) -> list[InventoryMovement]: ...

    def list_purchase_orders(
        self, material_id: str, warehouse_id: str
    ) -> list[PurchaseOrder]: ...

    def list_production_orders(
        self, material_id: str, warehouse_id: str
    ) -> list[ProductionOrder]: ...
