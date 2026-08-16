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


def test_agent_tools_endpoint_lists_three_controlled_tools(client: TestClient) -> None:
    response = client.get("/api/v1/tools")

    assert response.status_code == 200
    body = response.json()
    assert {item["name"] for item in body} == {
        "list_inventory_risks",
        "analyze_material_root_cause",
        "trace_evidence",
    }
    assert all(item["required_fields"] for item in body)


def test_chat_extracts_parameters_and_degrades_without_llm_key(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat",
        json={
            "question": "分析 MAT-SYN-MULTI 在 WH-SYN-01 截至 2026-03-31 的根因",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["selected_tool"] == "analyze_material_root_cause"
    assert body["parameters"] == {
        "material_id": "MAT-SYN-MULTI",
        "warehouse_id": "WH-SYN-01",
        "as_of_date": "2026-03-31",
        "category": None,
    }
    assert body["result"]["metadata"]["trace_id"] == body["trace_id"]
    assert body["evidence"]
    assert body["llm_used"] is False
    assert "reasoning_content" not in response.text
    assert "chain-of-thought" not in response.text.lower()


def test_chat_two_turns_only_asks_for_missing_context(client: TestClient) -> None:
    first = client.post(
        "/api/v1/chat",
        json={
            "question": "分析 MAT-SYN-MULTI 的根因",
            "session_id": "api-two-turn",
        },
    )
    second = client.post(
        "/api/v1/chat",
        json={
            "question": "仓库 WH-SYN-01，日期 2026-03-31",
            "session_id": "api-two-turn",
        },
    )

    assert first.json()["status"] == "needs_input"
    assert first.json()["missing_fields"] == ["warehouse_id", "as_of_date"]
    assert second.json()["status"] == "ok"
    assert second.json()["parameters"]["material_id"] == "MAT-SYN-MULTI"


def test_chat_rejects_unknown_input_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat",
        json={"question": "分析库存", "api_key": "must-not-be-accepted"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert "must-not-be-accepted" not in response.text
