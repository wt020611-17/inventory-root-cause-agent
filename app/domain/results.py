"""库存分析共用的结构化结果模型。

本模块统一服务、API 和后续 Agent Tool 的输出语义，尤其保证 `empty`（成功但无数据）、
`blocked`（事实存在但质量阻止结论）与 `error`（执行失败）不会互相伪装。
"""

from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import (
    EvidenceNodeType,
    EvidenceRelationType,
    InventoryRiskLevel,
    ResultStatus,
    RootCauseType,
)


class _ResultModel(BaseModel):
    """结果模型公共配置：拒绝未知字段并清理字符串两侧空白。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResultMetadata(_ResultModel):
    """贯穿一次分析的状态、追踪号和业务分析日期。"""

    status: ResultStatus = Field(description="执行结果状态")
    trace_id: str = Field(min_length=1, description="跨层追踪一次请求的稳定标识")
    as_of_date: date = Field(description="库存事实截取到的业务日期")


class MetricValue(_ResultModel):
    """带单位、观察窗口和完整性标记的十进制指标值。"""

    value: Decimal | None = Field(description="可用指标值；无法计算时为 None")
    unit: str = Field(min_length=1, description="指标单位")
    observation_window_days: int | None = Field(
        default=None,
        gt=0,
        description="窗口型指标实际覆盖的天数",
    )
    complete: bool = Field(description="该指标是否拥有计算所需的全部事实")


class InventoryMetrics(_ResultModel):
    """物料 × 仓库 × 分析日期粒度的确定性库存指标。"""

    material_id: str = Field(min_length=1, description="被分析的合成物料标识")
    warehouse_id: str = Field(min_length=1, description="被分析的合成仓库标识")
    current_stock: MetricValue = Field(description="截至分析日期的库存数量")
    first_receipt_date: date | None = Field(
        default=None,
        description="首笔有效采购入库日期；缺失时不能推断无消耗天数",
    )
    last_consumption_date: date | None = Field(
        default=None,
        description="最近一笔销售出库或生产领料日期；调拨与调整不计入",
    )
    days_without_consumption: MetricValue = Field(description="无有效消耗天数")
    effective_consumption_quantity: MetricValue = Field(
        default_factory=lambda: MetricValue(
            value=Decimal("0"),
            unit="unit",
            complete=True,
        ),
        description="观察窗口内销售出库与生产领料的绝对数量",
    )
    average_daily_consumption: MetricValue = Field(description="观察窗口平均日消耗")
    coverage_days: MetricValue = Field(description="当前库存可覆盖的预计消耗天数")
    stagnant_quantity: MetricValue = Field(
        default_factory=lambda: MetricValue(
            value=Decimal("0"),
            unit="unit",
            complete=True,
        ),
        description="满足当前呆滞规则且不在保护期内的库存数量",
    )
    stagnant_amount: MetricValue = Field(description="风险库存数量乘以单位成本")
    infinite_coverage: bool = Field(
        default=False,
        description="平均消耗为零时为真，此时不把无穷大写入数值字段",
    )
    partial_window: bool = Field(
        default=False,
        description="实际观察天数少于配置窗口时为真",
    )
    new_material_protected: bool = Field(
        default=False,
        description="首次入库后仍处于新物料保护期",
    )

    @model_validator(mode="after")
    def validate_coverage_representation(self) -> Self:
        """无限覆盖必须用布尔标记表达，而不能保存伪造的大数或无穷大。"""
        if self.infinite_coverage and self.coverage_days.value is not None:
            raise ValueError("infinite coverage must use a null coverage_days value")
        if not self.infinite_coverage and self.coverage_days.value is None:
            raise ValueError("finite coverage requires a coverage_days value")
        return self


class RiskAssessment(_ResultModel):
    """由确定性规则产生的库存风险等级和命中规则。"""

    risk_level: InventoryRiskLevel = Field(description="库存风险等级")
    conclusion_allowed: bool = Field(description="当前数据是否允许输出业务风险结论")
    matched_rules: list[str] = Field(default_factory=list, description="命中的确定性规则标识")

    @model_validator(mode="after")
    def validate_blocked_semantics(self) -> Self:
        """质量阻断等级与“是否允许结论”必须表达一致。"""
        is_blocked = self.risk_level is InventoryRiskLevel.DATA_QUALITY_BLOCKED
        if is_blocked == self.conclusion_allowed:
            raise ValueError(
                "blocked risk must disallow conclusions and other risks must allow them"
            )
        return self


class EvidenceItem(_ResultModel):
    """支持指标或候选根因的可引用业务事实摘要。"""

    evidence_id: str = Field(min_length=1, description="本次分析中的证据标识")
    source_type: str = Field(min_length=1, description="证据来源类型")
    source_id: str = Field(min_length=1, description="合成流水或单据标识")
    summary: str = Field(min_length=1, description="可展示的事实摘要，不包含私有推理")
    facts: dict[str, str | int | Decimal | date | None] = Field(
        default_factory=dict,
        description="可机器核对的受控证据字段",
    )


class RootCauseCandidate(_ResultModel):
    """确定性规则支持的候选根因、排序分数和直接证据。"""

    cause_type: RootCauseType = Field(description="受控候选根因类型")
    score: Decimal = Field(ge=0, le=1, description="零到一之间的确定性排序分数")
    hits: list[str] = Field(min_length=1, description="命中的规则或证据条件")
    evidence: list[EvidenceItem] = Field(default_factory=list, description="直接支持该候选的证据")
    insufficient_evidence: bool = Field(
        default=False,
        description="当前证据是否不足以支持稳定候选结论",
    )

    @model_validator(mode="after")
    def validate_evidence_flag(self) -> Self:
        """证据充足的候选至少需要一条可引用证据。"""
        if not self.insufficient_evidence and not self.evidence:
            raise ValueError("candidate with sufficient evidence requires evidence items")
        return self


class AnalysisResult(_ResultModel):
    """库存分析的统一领域输出，并强制四种状态的负载保持一致。"""

    metadata: ResultMetadata = Field(description="状态、追踪号和分析日期")
    message: str = Field(min_length=1, description="面向调用方的简短结果说明")
    metrics: InventoryMetrics | None = Field(default=None, description="已计算库存指标")
    risk: RiskAssessment | None = Field(default=None, description="风险判断")
    root_causes: list[RootCauseCandidate] = Field(
        default_factory=list,
        description="按分数排序的候选根因",
    )
    evidence: list[EvidenceItem] = Field(default_factory=list, description="结果级事实证据")
    blockers: list[str] = Field(default_factory=list, description="阻止业务结论的质量问题")
    errors: list[str] = Field(default_factory=list, description="稳定错误类别或摘要")

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """根据状态限制负载，避免无数据、阻断和失败互相伪装。"""
        status = self.metadata.status

        if status is ResultStatus.OK:
            if self.metrics is None or self.risk is None:
                raise ValueError("ok result requires metrics and risk")
            if not self.risk.conclusion_allowed:
                raise ValueError("ok result requires an allowed risk conclusion")
            if self.blockers or self.errors:
                raise ValueError("ok result must not contain blockers or errors")

        elif status is ResultStatus.EMPTY:
            if any(
                (
                    self.metrics is not None,
                    self.risk is not None,
                    bool(self.root_causes),
                    bool(self.evidence),
                    bool(self.blockers),
                    bool(self.errors),
                )
            ):
                raise ValueError("empty result must not fabricate facts or failures")

        elif status is ResultStatus.BLOCKED:
            if self.risk is None or self.risk.conclusion_allowed:
                raise ValueError("blocked result requires a blocked risk assessment")
            if not self.blockers:
                raise ValueError("blocked result requires at least one blocker")
            if self.root_causes or self.errors:
                raise ValueError("blocked result must not contain conclusions or execution errors")

        elif status is ResultStatus.ERROR:
            if not self.errors:
                raise ValueError("error result requires at least one error summary")
            if any(
                (
                    self.metrics is not None,
                    self.risk is not None,
                    bool(self.root_causes),
                    bool(self.evidence),
                    bool(self.blockers),
                )
            ):
                raise ValueError("error result must not contain business facts or conclusions")

        return self


class RiskListResult(_ResultModel):
    """风险清单统一领域结果，明确区分有数据、无数据和执行失败。"""

    metadata: ResultMetadata
    items: list[AnalysisResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """清单状态必须与条目和错误摘要保持一致。"""
        status = self.metadata.status
        if status is ResultStatus.OK:
            if not self.items or self.errors:
                raise ValueError("ok risk list requires items and no errors")
        elif status is ResultStatus.EMPTY:
            if self.items or self.errors:
                raise ValueError("empty risk list must not contain items or errors")
        elif status is ResultStatus.ERROR:
            if self.items or not self.errors:
                raise ValueError("error risk list requires errors and no items")
        else:
            raise ValueError("risk list does not use blocked as an aggregate status")
        return self


class EvidenceGraphNode(_ResultModel):
    """证据图节点；来源与 synthetic 标记不可省略。"""

    node_id: str = Field(min_length=1)
    node_type: EvidenceNodeType
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    synthetic: bool


class EvidenceGraphEdge(_ResultModel):
    """证据图边；关系类型固定且保存来源标识。"""

    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    relation_type: EvidenceRelationType
    source_id: str = Field(min_length=1)


class EvidencePath(_ResultModel):
    """从目标物料可追溯的一条受限业务路径。"""

    node_ids: list[str] = Field(min_length=2)
    relation_types: list[EvidenceRelationType] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_path_shape(self) -> Self:
        if len(self.relation_types) != len(self.node_ids) - 1:
            raise ValueError("path must contain exactly one relation per hop")
        return self


class EvidenceGraphResult(_ResultModel):
    """受限制的图查询结果，统一表达 ok、empty、blocked 与 error。"""

    metadata: ResultMetadata
    message: str = Field(min_length=1)
    nodes: list[EvidenceGraphNode] = Field(default_factory=list)
    edges: list[EvidenceGraphEdge] = Field(default_factory=list)
    paths: list[EvidencePath] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        status = self.metadata.status
        if status is ResultStatus.OK:
            if not self.nodes or not self.edges or not self.paths or self.blockers or self.errors:
                raise ValueError("ok graph result requires graph facts only")
        elif status is ResultStatus.EMPTY:
            if self.nodes or self.edges or self.paths or self.blockers or self.errors:
                raise ValueError("empty graph result must not fabricate facts")
        elif status is ResultStatus.BLOCKED:
            if not self.blockers or self.paths or self.errors:
                raise ValueError("blocked graph result requires blockers and no paths")
        elif status is ResultStatus.ERROR:
            if not self.errors or self.nodes or self.edges or self.paths or self.blockers:
                raise ValueError("error graph result requires errors only")
        return self
