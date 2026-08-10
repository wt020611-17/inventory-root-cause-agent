"""库存分析阈值配置。

本模块只负责集中保存和校验业务阈值，不实现风险判定公式。这样可以避免业务代码中
散落“魔法数字”，也方便合成数据测试覆盖默认值、边界值和不同配置。
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalysisThresholds(BaseModel):
    """合成数据 MVP v0.2 使用的可配置分析阈值。

    输入是调用方提供的零个或多个阈值覆盖项；输出是经过 Pydantic 校验的完整配置对象。
    当前默认值只服务于合成数据和可复现测试，不代表行业或企业统一标准。
    """

    # 禁止未知字段，及时暴露拼写错误或尚未支持的配置项。
    model_config = ConfigDict(extra="forbid")

    # 指标观察窗口：平均消耗等窗口型指标只读取这段时间内的事实。
    analysis_window_days: int = Field(default=90, gt=0, description="平均消耗观察窗口天数")

    # 风险分级阈值：达到慢动天数后进入风险判断，达到无动天数后优先判为无动。
    slow_moving_days: int = Field(default=90, gt=0, description="慢动风险阈值天数")
    non_moving_days: int = Field(default=180, gt=0, description="无动风险阈值天数")
    coverage_days_threshold: int = Field(
        default=120,
        gt=0,
        description="高库存覆盖风险阈值天数",
    )

    # 三类根因各自使用独立阈值；比例允许 0 和 1 两个业务边界。
    demand_drop_ratio: float = Field(
        default=0.40,
        ge=0,
        le=1,
        description="近期消耗相对前期消耗的下降比例阈值",
    )
    purchase_excess_days: int = Field(
        default=120,
        gt=0,
        description="采购剩余量对应的覆盖天数阈值",
    )
    production_delay_days: int = Field(default=14, gt=0, description="生产延期阈值天数")

    # 新物料在保护期内积累的库存不立即判为呆滞。
    new_material_protection_days: int = Field(
        default=30,
        gt=0,
        description="新物料首次入库后的保护期天数",
    )

    @model_validator(mode="after")
    def validate_movement_threshold_order(self) -> Self:
        """确保更严重的“无动”阈值不会早于“慢动”阈值。"""
        # mode="after" 可以同时读取两个已完成基础校验的字段，适合跨字段约束。
        if self.non_moving_days < self.slow_moving_days:
            raise ValueError("non_moving_days must be greater than or equal to slow_moving_days")
        return self
