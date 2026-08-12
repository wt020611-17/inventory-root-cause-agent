"""FastAPI 健康检查、结构化分析和错误状态映射集成测试。"""

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    """通过上下文进入 FastAPI lifespan，执行建表和固定 seed 初始化。"""
    with TestClient(
        create_app(database_url="sqlite+pysqlite:///:memory:", seed=7)
    ) as test_client:
        yield test_client


def test_health_returns_version_without_internal_paths(client: TestClient) -> None:
    """健康检查应稳定可用，且不泄露数据库路径或密钥。"""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"
    assert response.json()["trace_id"]
    assert "sqlite" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_analysis_ok_response_contains_trace_and_metrics(client: TestClient) -> None:
    """正常场景返回结构化指标、风险和服务生成的 trace_id。"""
    response = client.post(
        "/api/v1/analysis",
        json={
            "material_id": "MAT-SYN-NORMAL",
            "warehouse_id": "WH-SYN-01",
            "as_of_date": "2026-03-31",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "ok"
    assert body["metadata"]["trace_id"]
    assert body["metrics"]["material_id"] == "MAT-SYN-NORMAL"


def test_analysis_empty_response_is_not_error(client: TestClient) -> None:
    """不存在的查询目标返回200与 empty，不伪装成500。"""
    response = client.post(
        "/api/v1/analysis",
        json={
            "material_id": "MAT-SYN-EMPTY",
            "warehouse_id": "WH-SYN-01",
            "as_of_date": "2026-03-31",
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["status"] == "empty"


def test_analysis_blocked_response_preserves_facts(client: TestClient) -> None:
    """负库存质量问题返回 blocked，保留指标并禁止普通结论。"""
    response = client.post(
        "/api/v1/analysis",
        json={
            "material_id": "MAT-SYN-BLOCKED",
            "warehouse_id": "WH-SYN-01",
            "as_of_date": "2026-03-31",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "blocked"
    assert body["metrics"]["current_stock"]["value"].startswith("-")
    assert body["blockers"] == ["negative_current_stock"]


def test_request_rejects_unknown_fields_and_invalid_date(client: TestClient) -> None:
    """请求模型拒绝未知字段和非法日期，且此时领域服务尚未执行。"""
    extra_response = client.post(
        "/api/v1/analysis",
        json={
            "material_id": "MAT-SYN-NORMAL",
            "warehouse_id": "WH-SYN-01",
            "as_of_date": "2026-03-31",
            "unknown_field": "unexpected",
        },
    )
    invalid_date_response = client.post(
        "/api/v1/analysis",
        json={
            "material_id": "MAT-SYN-NORMAL",
            "warehouse_id": "WH-SYN-01",
            "as_of_date": "not-a-date",
        },
    )

    assert extra_response.status_code == 422
    assert invalid_date_response.status_code == 422
    assert "metadata" not in extra_response.json()
    assert extra_response.json()["code"] == "invalid_request"
    assert extra_response.json()["trace_id"]
    assert invalid_date_response.json()["trace_id"]


def test_risk_list_filters_category_and_sorts_stagnant_amount(client: TestClient) -> None:
    """风险接口复用服务筛选和排序，不在路由中重复业务规则。"""
    response = client.post(
        "/api/v1/risks",
        json={
            "warehouse_id": "WH-SYN-01",
            "category": "场景物料",
            "as_of_date": "2026-03-31",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "ok"
    assert body["metadata"]["trace_id"]
    assert body["items"]
    amounts = [
        float(item["metrics"]["stagnant_amount"]["value"])
        for item in body["items"]
    ]
    assert amounts == sorted(amounts, reverse=True)


def test_empty_risk_list_still_contains_trace_id(client: TestClient) -> None:
    """风险清单没有匹配条目时仍返回 empty 元数据和追踪号。"""
    response = client.post(
        "/api/v1/risks",
        json={"category": "不存在类别", "as_of_date": "2026-03-31"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "empty"
    assert body["metadata"]["trace_id"]
    assert body["items"] == []


def test_database_failure_maps_to_error_without_leaking_details(client: TestClient) -> None:
    """数据库表异常应映射为稳定 error 响应，不泄露 SQL 或内部路径。"""
    with client.app.state.engine.begin() as connection:
        connection.execute(text("DROP TABLE materials"))

    response = client.post(
        "/api/v1/analysis",
        json={
            "material_id": "MAT-SYN-NORMAL",
            "warehouse_id": "WH-SYN-01",
            "as_of_date": "2026-03-31",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "error"
    assert body["errors"] == ["repository_error"]
    assert "sql" not in response.text.lower()
