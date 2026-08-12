"""五个持久化实体对应的 SQLAlchemy 表模型。

数据库模型与 Pydantic 领域实体分开：本文件只描述 SQLite 存储结构，不承担业务校验。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Phase 1 所有 SQLAlchemy 表共享的声明式基类。"""


class MaterialRow(Base):
    """物料主数据表。"""

    __tablename__ = "materials"
    material_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    created_date: Mapped[date] = mapped_column(Date)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class WarehouseRow(Base):
    """仓库主数据表。"""

    __tablename__ = "warehouses"
    warehouse_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(100))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class InventoryMovementRow(Base):
    """库存增减事实表。"""

    __tablename__ = "inventory_movements"
    movement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    material_id: Mapped[str] = mapped_column(String(64), index=True)
    warehouse_id: Mapped[str] = mapped_column(String(64), index=True)
    movement_type: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_doc_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class PurchaseOrderRow(Base):
    """采购订单事实表。"""

    __tablename__ = "purchase_orders"
    po_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    material_id: Mapped[str] = mapped_column(String(64), index=True)
    warehouse_id: Mapped[str] = mapped_column(String(64), index=True)
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    received_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    planned_date: Mapped[date] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class ProductionOrderRow(Base):
    """生产订单事实表。"""

    __tablename__ = "production_orders"
    production_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    material_id: Mapped[str] = mapped_column(String(64), index=True)
    warehouse_id: Mapped[str] = mapped_column(String(64), index=True)
    planned_consumption_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(40))
    planned_start: Mapped[date] = mapped_column(Date)
    actual_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
