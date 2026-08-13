"""三个用例级 Agent Tool 的严格输入契约。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    as_of_date: date
    trace_id: str = Field(min_length=1, max_length=128)


class ListInventoryRisksInput(_ToolInput):
    warehouse_id: str | None = Field(default=None, min_length=1, max_length=64)
    category: str | None = Field(default=None, min_length=1, max_length=100)


class AnalyzeMaterialRootCauseInput(_ToolInput):
    material_id: str = Field(min_length=1, max_length=64)
    warehouse_id: str = Field(min_length=1, max_length=64)


class TraceEvidenceInput(AnalyzeMaterialRootCauseInput):
    max_hops: int = Field(default=2, ge=1, le=5)
    max_nodes: int = Field(default=50, ge=2, le=200)
    timeout_ms: int = Field(default=100, ge=1, le=5000)
