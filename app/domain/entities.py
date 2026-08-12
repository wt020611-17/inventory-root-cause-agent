"""库存归因 MVP 的五个持久化领域实体。

本模块只表达单条业务记录自身可以判断的输入契约，例如非空标识、数量边界、
日期顺序和订单状态组合。主键唯一、外键存在等需要观察整批数据的规则，放在后续
数据质量检查中，避免实体层假装能够访问数据库或其他记录。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import MovementType, ProductionOrderStatus, PurchaseOrderStatus


class _DomainEntity(BaseModel):
    """五个实体共用的输入安全配置。

    `extra="forbid"` 会拒绝拼错或尚未支持的字段；`str_strip_whitespace` 先清理
    文本两侧空白，再由各字段的最小长度约束拒绝纯空白内容。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # 项目严禁混入真实记录；Literal[True] 使 false 无法通过领域模型校验。
    synthetic: Literal[True] = Field(default=True, description="明确标记为纯合成数据")


class Material(_DomainEntity):
    """物料主数据，为库存数量提供名称、类别和合成标准成本。"""

    # ID 的跨记录唯一性由数据集质量检查负责；实体只保证它不是空字符串。
    material_id: str = Field(min_length=1, description="合成物料唯一标识")
    name: str = Field(min_length=1, description="物料显示名称")
    category: str = Field(min_length=1, description="物料业务类别")

    # 金额使用 Decimal，避免二进制浮点数给金额计算带来不可见误差。
    unit_cost: Decimal | None = Field(
        default=None,
        ge=0,
        description="可选合成标准单位成本；缺失时仍允许数量分析",
    )
    created_date: date = Field(description="物料主数据创建日期")


class Warehouse(_DomainEntity):
    """仓库主数据，限定库存分析发生的物理或逻辑位置。"""

    warehouse_id: str = Field(min_length=1, description="合成仓库唯一标识")
    name: str = Field(min_length=1, description="仓库显示名称")
    region: str = Field(min_length=1, description="仓库所属区域")


class InventoryMovement(_DomainEntity):
    """已经过账的库存增减事实，是后续聚合当前库存的唯一来源。"""

    movement_id: str = Field(min_length=1, description="合成库存流水唯一标识")
    material_id: str = Field(min_length=1, description="关联物料标识")
    warehouse_id: str = Field(min_length=1, description="关联仓库标识")
    movement_type: MovementType = Field(description="受控库存移动类型")

    # 数量使用带正负号的 Decimal：正数增加库存，负数减少库存；零流水没有意义。
    quantity: Decimal = Field(description="带方向的库存移动数量")
    posted_at: datetime = Field(description="流水实际过账时间")

    # 调整类流水可能没有外部单据；一旦提供来源标识，则不能是空白文本。
    source_doc_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选的采购、销售、生产或调整来源单据标识",
    )

    @field_validator("quantity")
    @classmethod
    def validate_non_zero_quantity(cls, quantity: Decimal) -> Decimal:
        """拒绝不会改变库存余额的零数量流水。"""
        if quantity == 0:
            raise ValueError("quantity must be non-zero")
        return quantity


class PurchaseOrder(_DomainEntity):
    """采购供给记录，用于判断到货是否造成库存覆盖过高。"""

    po_id: str = Field(min_length=1, description="合成采购订单唯一标识")
    material_id: str = Field(min_length=1, description="采购物料标识")
    warehouse_id: str = Field(min_length=1, description="计划收货仓库标识")

    # 订购量必须为正；累计收货量允许为零，但不能超过订购量。
    ordered_qty: Decimal = Field(gt=0, description="采购订购数量")
    received_qty: Decimal = Field(ge=0, description="累计实际收货数量")
    planned_date: date = Field(description="计划收货日期")
    actual_date: date | None = Field(default=None, description="首次或最终实际收货日期")
    status: PurchaseOrderStatus = Field(description="采购订单生命周期状态")

    @model_validator(mode="after")
    def validate_quantity_and_status(self) -> Self:
        """确保数量、实际日期和采购状态描述同一个业务事实。"""
        if self.received_qty > self.ordered_qty:
            raise ValueError("received_qty must not exceed ordered_qty")

        if self.status is PurchaseOrderStatus.PLANNED:
            if self.received_qty != 0 or self.actual_date is not None:
                raise ValueError("PLANNED order must have zero receipt and no actual_date")

        elif self.status is PurchaseOrderStatus.PARTIALLY_RECEIVED:
            if not 0 < self.received_qty < self.ordered_qty:
                raise ValueError(
                    "PARTIALLY_RECEIVED order requires receipt between zero and ordered_qty"
                )
            if self.actual_date is None:
                raise ValueError("PARTIALLY_RECEIVED order requires actual_date")

        elif self.status is PurchaseOrderStatus.RECEIVED:
            if self.received_qty != self.ordered_qty:
                raise ValueError("RECEIVED order requires received_qty equal to ordered_qty")
            if self.actual_date is None:
                raise ValueError("RECEIVED order requires actual_date")

        # 取消可能发生在未收货或部分收货之后，因此只应用上面的通用数量边界。
        return self


class ProductionOrder(_DomainEntity):
    """准备消耗一种物料的生产计划，用于提供生产延期证据。

    `material_id` 直接表示本生产计划准备消耗的一种物料；MVP 不在此实体中扩展
    BOM、工序、领料明细或产出物料。
    """

    production_order_id: str = Field(min_length=1, description="合成生产订单唯一标识")
    material_id: str = Field(min_length=1, description="计划消耗的一种物料标识")
    warehouse_id: str = Field(min_length=1, description="计划消耗物料所在仓库标识")
    planned_consumption_qty: Decimal = Field(ge=0, description="计划消耗数量")
    status: ProductionOrderStatus = Field(description="生产订单生命周期状态")
    planned_start: date = Field(description="计划开工日期")
    actual_start: date | None = Field(default=None, description="实际开工日期")
    due_date: date = Field(description="计划完成日期")
    closed_at: date | None = Field(default=None, description="实际关闭日期")

    @model_validator(mode="after")
    def validate_date_and_status(self) -> Self:
        """校验日期顺序，并确保状态与实际开始、关闭事实一致。"""
        if self.due_date < self.planned_start:
            raise ValueError("due_date must be on or after planned_start")

        if (
            self.actual_start is not None
            and self.closed_at is not None
            and self.closed_at < self.actual_start
        ):
            raise ValueError("closed_at must be on or after actual_start")

        if self.status in {
            ProductionOrderStatus.PLANNED,
            ProductionOrderStatus.RELEASED,
        }:
            if self.actual_start is not None:
                raise ValueError("PLANNED or RELEASED order must not have actual_start")

        elif self.status is ProductionOrderStatus.IN_PROGRESS:
            if self.actual_start is None:
                raise ValueError("IN_PROGRESS order requires actual_start")

        elif self.status is ProductionOrderStatus.CLOSED:
            if self.actual_start is None or self.closed_at is None:
                raise ValueError("CLOSED order requires actual_start and closed_at")

        if self.status is not ProductionOrderStatus.CLOSED and self.closed_at is not None:
            raise ValueError("only CLOSED order may have closed_at")

        # 取消可能发生在开工前或开工后，因此 CANCELLED 允许保留可选 actual_start。
        return self
