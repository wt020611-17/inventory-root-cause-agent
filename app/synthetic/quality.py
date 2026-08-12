"""纯合成数据集的跨记录质量检查。

Pydantic 实体负责单条记录；本模块负责主键唯一、外键完整、分析日期、场景覆盖和
整批流水累计库存等只有观察完整数据集才能判断的规则。
"""

import re
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.enums import ResultStatus, RootCauseType
from app.synthetic.generator import SyntheticDataset


class DataQualityIssue(BaseModel):
    """一项可定位、可序列化的数据质量问题。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: Literal["blocker", "error"]
    entity_type: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    expected: bool = False


class DataQualityReport(BaseModel):
    """整批数据质量结论；错误优先于阻断，没有问题时为 ok。"""

    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    issues: list[DataQualityIssue] = Field(default_factory=list)

    @property
    def error_count(self) -> int:
        """返回结构错误数量。"""
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def blocker_count(self) -> int:
        """返回可保留事实但阻止业务结论的问题数量。"""
        return sum(issue.severity == "blocker" for issue in self.issues)


def _duplicates(values: list[str]) -> set[str]:
    """返回出现超过一次的标识集合。"""
    counts = Counter(values)
    return {value for value, count in counts.items() if count > 1}


def validate_synthetic_dataset(dataset: SyntheticDataset) -> DataQualityReport:
    """执行 Phase 1 数据集质量规则并返回结构化报告。"""
    issues: list[DataQualityIssue] = []
    material_ids = {material.material_id for material in dataset.materials}
    warehouse_ids = {warehouse.warehouse_id for warehouse in dataset.warehouses}

    entity_groups = [
        ("material", dataset.materials, "material_id"),
        ("warehouse", dataset.warehouses, "warehouse_id"),
        ("movement", dataset.movements, "movement_id"),
        ("purchase_order", dataset.purchase_orders, "po_id"),
        ("production_order", dataset.production_orders, "production_order_id"),
    ]
    required_prefixes = {
        "material": "MAT-SYN-",
        "warehouse": "WH-SYN-",
        "movement": "MOV-SYN-",
        "purchase_order": "PO-SYN-",
        "production_order": "PRD-SYN-",
    }

    for entity_type, records, id_field in entity_groups:
        identifiers = [getattr(record, id_field) for record in records]
        for duplicate in sorted(_duplicates(identifiers)):
            issues.append(
                DataQualityIssue(
                    code="duplicate_primary_key",
                    severity="error",
                    entity_type=entity_type,
                    record_id=duplicate,
                    message=f"{entity_type} primary key appears more than once",
                )
            )

        for record in records:
            record_id = getattr(record, id_field)
            if not record_id.startswith(required_prefixes[entity_type]):
                issues.append(
                    DataQualityIssue(
                        code="invalid_synthetic_id_prefix",
                        severity="error",
                        entity_type=entity_type,
                        record_id=record_id,
                        message="record ID does not use the required synthetic prefix",
                    )
                )
            raw_fields = dict(record.__dict__)
            try:
                type(record).model_validate(raw_fields)
            except ValidationError:
                issues.append(
                    DataQualityIssue(
                        code="invalid_entity_contract",
                        severity="error",
                        entity_type=entity_type,
                        record_id=record_id,
                        message="record violates enum, quantity, amount, date, or status rules",
                    )
                )
            if record.synthetic is not True:
                issues.append(
                    DataQualityIssue(
                        code="non_synthetic_record",
                        severity="error",
                        entity_type=entity_type,
                        record_id=record_id,
                        message="record must be visibly marked synthetic=true",
                    )
                )

            dumped_text = str(raw_fields)
            suspicious_pattern = r"https?://|(?:api[_-]?key|password|secret|token)\s*[:=]"
            if re.search(suspicious_pattern, dumped_text, re.I):
                issues.append(
                    DataQualityIssue(
                        code="suspicious_identifier",
                        severity="error",
                        entity_type=entity_type,
                        record_id=record_id,
                        message="record contains a URL or credential-like marker",
                    )
                )

    reference_groups = [
        ("movement", dataset.movements, "movement_id"),
        ("purchase_order", dataset.purchase_orders, "po_id"),
        ("production_order", dataset.production_orders, "production_order_id"),
    ]
    for entity_type, records, id_field in reference_groups:
        for record in records:
            record_id = getattr(record, id_field)
            if record.material_id not in material_ids:
                issues.append(
                    DataQualityIssue(
                        code="dangling_material_reference",
                        severity="error",
                        entity_type=entity_type,
                        record_id=record_id,
                        message=f"material {record.material_id} does not exist",
                    )
                )
            if record.warehouse_id not in warehouse_ids:
                issues.append(
                    DataQualityIssue(
                        code="dangling_warehouse_reference",
                        severity="error",
                        entity_type=entity_type,
                        record_id=record_id,
                        message=f"warehouse {record.warehouse_id} does not exist",
                    )
                )

    purchase_orders_by_id = {order.po_id: order for order in dataset.purchase_orders}
    production_orders_by_id = {
        order.production_order_id: order for order in dataset.production_orders
    }
    for movement in dataset.movements:
        source_id = movement.source_doc_id
        if source_id is None:
            continue
        if source_id.startswith("PO-SYN-"):
            source_order = purchase_orders_by_id.get(source_id)
            if source_order is None:
                issues.append(
                    DataQualityIssue(
                        code="dangling_source_document",
                        severity="error",
                        entity_type="movement",
                        record_id=movement.movement_id,
                        message=f"purchase source {source_id} does not exist",
                    )
                )
            elif (
                source_order.material_id != movement.material_id
                or source_order.warehouse_id != movement.warehouse_id
            ):
                issues.append(
                    DataQualityIssue(
                        code="source_document_mismatch",
                        severity="error",
                        entity_type="movement",
                        record_id=movement.movement_id,
                        message="purchase source does not match movement material and warehouse",
                    )
                )
        elif source_id.startswith("PRD-SYN-"):
            source_order = production_orders_by_id.get(source_id)
            if source_order is None:
                issues.append(
                    DataQualityIssue(
                        code="dangling_source_document",
                        severity="error",
                        entity_type="movement",
                        record_id=movement.movement_id,
                        message=f"production source {source_id} does not exist",
                    )
                )
            elif (
                source_order.material_id != movement.material_id
                or source_order.warehouse_id != movement.warehouse_id
            ):
                issues.append(
                    DataQualityIssue(
                        code="source_document_mismatch",
                        severity="error",
                        entity_type="movement",
                        record_id=movement.movement_id,
                        message="production source does not match movement material and warehouse",
                    )
                )

    stock_by_key: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for movement in dataset.movements:
        if movement.posted_at.date() > dataset.as_of_date:
            issues.append(
                DataQualityIssue(
                    code="future_dated_movement",
                    severity="error",
                    entity_type="movement",
                    record_id=movement.movement_id,
                    message="posted_at is later than dataset as_of_date",
                )
            )
        stock_by_key[(movement.material_id, movement.warehouse_id)] += movement.quantity

    blocked_target = dataset.scenario_targets.get("SYN-BLOCKED-01")
    blocked_key = (
        (blocked_target.material_id, blocked_target.warehouse_id) if blocked_target else None
    )
    for (material_id, warehouse_id), stock in sorted(stock_by_key.items()):
        if stock < 0:
            key = (material_id, warehouse_id)
            issues.append(
                DataQualityIssue(
                    code="negative_current_stock",
                    severity="blocker",
                    entity_type="inventory_balance",
                    record_id=f"{material_id}@{warehouse_id}",
                    message=f"cumulative stock is negative: {stock}",
                    expected=key == blocked_key,
                )
            )

    movements_by_key: defaultdict[tuple[str, str], list] = defaultdict(list)
    for movement in dataset.movements:
        movements_by_key[(movement.material_id, movement.warehouse_id)].append(movement)
    for (material_id, warehouse_id), grouped_movements in sorted(movements_by_key.items()):
        def movement_type_value(movement) -> str:
            value = movement.movement_type
            return value.value if hasattr(value, "value") else str(value)

        has_consumption = any(
            movement_type_value(movement) in {"SALES_ISSUE", "PRODUCTION_ISSUE"}
            and movement.quantity < 0
            for movement in grouped_movements
        )
        has_receipt = any(
            movement_type_value(movement) == "PURCHASE_RECEIPT" and movement.quantity > 0
            for movement in grouped_movements
        )
        if not has_consumption and not has_receipt:
            issues.append(
                DataQualityIssue(
                    code="missing_first_receipt",
                    severity="blocker",
                    entity_type="inventory_balance",
                    record_id=f"{material_id}@{warehouse_id}",
                    message="no effective consumption and no first receipt are available",
                )
            )

    required_scenarios = {
        "SYN-NORMAL-01",
        "SYN-DEMAND-DROP-01",
        "SYN-OVERBUY-01",
        "SYN-PROD-DELAY-01",
        "SYN-MULTI-CAUSE-01",
        "SYN-EMPTY-01",
        "SYN-BLOCKED-01",
    }
    for scenario_id in sorted(required_scenarios - set(dataset.scenario_targets)):
        issues.append(
            DataQualityIssue(
                code="missing_required_scenario",
                severity="error",
                entity_type="scenario",
                record_id=scenario_id,
                message="required scenario target is missing",
            )
        )

    scenario_causes = [
        set(target.expected_causes) for target in dataset.scenario_targets.values()
    ]
    for cause in RootCauseType:
        has_positive = any(cause in causes for causes in scenario_causes)
        has_negative = any(cause not in causes for causes in scenario_causes)
        if not has_positive or not has_negative:
            issues.append(
                DataQualityIssue(
                    code="missing_root_cause_example",
                    severity="error",
                    entity_type="scenario",
                    record_id=cause.value,
                    message="root cause requires at least one positive and one negative scenario",
                )
            )

    if any(issue.severity == "error" for issue in issues):
        status = ResultStatus.ERROR
    elif issues:
        status = ResultStatus.BLOCKED
    else:
        status = ResultStatus.OK
    return DataQualityReport(status=status, issues=issues)
