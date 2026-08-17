"""FastAPI 应用入口与 Phase 1 本地依赖装配。"""

import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app import __version__
from app.agent import (
    TOOL_DESCRIPTIONS,
    AgentRequest,
    AgentResponse,
    AgentSettings,
    AnalysisIntent,
    InMemorySessionStore,
    ToolDescriptor,
    create_agent_llm,
    invoke_agent,
)
from app.agent.llm import AgentLLM
from app.api.models import (
    AnalysisRequest,
    HealthResponse,
    InternalErrorResponse,
    InvalidRequestResponse,
    RiskListRequest,
)
from app.core import audit_event, configure_audit_logging
from app.domain.results import AnalysisResult, RiskListResult
from app.domain.thresholds import AnalysisThresholds
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.services.inventory_analysis import InventoryAnalysisService
from app.synthetic.generator import generate_synthetic_dataset
from app.tools import InventoryAgentTools


def create_app(
    *,
    database_url: str | None = None,
    seed: int = 20260812,
    agent_settings: AgentSettings | None = None,
    agent_llm: AgentLLM | None = None,
    session_store: InMemorySessionStore | None = None,
) -> FastAPI:
    """创建应用，并在生命周期启动阶段初始化固定 seed 合成数据库。"""
    configure_audit_logging()
    resolved_database_url = (
        database_url
        or os.getenv("DATABASE_URL")
        or "sqlite+pysqlite:///./inventory_agent.db"
    )
    engine = create_sqlite_engine(resolved_database_url)
    resolved_settings = agent_settings or AgentSettings()
    resolved_llm = agent_llm or create_agent_llm(resolved_settings)
    resolved_sessions = session_store or InMemorySessionStore(
        ttl_seconds=resolved_settings.session_ttl_seconds,
        max_turns=resolved_settings.session_max_turns,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        create_tables(engine)
        with session_scope(engine) as session:
            repository = SqlAlchemyInventoryRepository(session)
            if repository.count_records()["materials"] == 0:
                repository.add_dataset(generate_synthetic_dataset(seed=seed))
        app.state.engine = engine
        app.state.agent_settings = resolved_settings
        app.state.agent_llm = resolved_llm
        app.state.agent_sessions = resolved_sessions
        yield
        engine.dispose()

    application = FastAPI(
        title="Inventory Root-Cause Agent",
        version=__version__,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def audit_http_request(request: Request, call_next):
        """记录接口元数据；不读取或记录请求体。"""
        started_at = perf_counter()
        trace_id = uuid4().hex
        request.state.trace_id = trace_id
        request.state.result_status = None
        request.state.tool_name = None
        request.state.error_category = None
        try:
            response = await call_next(request)
        except Exception:
            audit_event(
                "api_request_failed",
                trace_id=trace_id,
                level=40,
                method=request.method,
                route=request.url.path,
                http_status=500,
                elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
                result_status="error",
                error_category="unhandled_exception",
            )
            raise
        response.headers["X-Trace-ID"] = trace_id
        audit_event(
            "api_request_completed",
            trace_id=trace_id,
            method=request.method,
            route=request.url.path,
            http_status=response.status_code,
            elapsed_ms=round((perf_counter() - started_at) * 1000, 3),
            tool_name=request.state.tool_name,
            result_status=request.state.result_status or "ok",
            error_category=request.state.error_category,
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """把422校验错误映射为带追踪号的稳定API结构。"""
        trace_id = request.state.trace_id
        request.state.result_status = "error"
        request.state.error_category = "invalid_request"
        payload = InvalidRequestResponse(
            trace_id=trace_id,
            details=[
                {
                    "type": error["type"],
                    "loc": list(error["loc"]),
                    "msg": error["msg"],
                }
                for error in exc.errors()
            ],
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """把未处理异常映射为不泄露细节的稳定 500 响应。"""
        del exc
        trace_id = getattr(request.state, "trace_id", uuid4().hex)
        request.state.result_status = "error"
        request.state.error_category = "unhandled_exception"
        payload = InternalErrorResponse(trace_id=trace_id)
        return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))

    def get_session(request: Request) -> Iterator[Session]:
        """为一次 HTTP 请求提供独立事务和 Session。"""
        with session_scope(request.app.state.engine) as session:
            yield session

    @application.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        """返回最小健康状态，不暴露数据库或环境信息。"""
        request.state.result_status = "ok"
        return HealthResponse(
            status="ok",
            version=__version__,
            trace_id=request.state.trace_id,
        )

    @application.post("/api/v1/analysis", response_model=AnalysisResult)
    def analyze(
        payload: AnalysisRequest,
        session: Annotated[Session, Depends(get_session)],
        request: Request,
    ) -> AnalysisResult:
        """执行结构化库存分析，并返回统一领域结果。"""
        service = InventoryAnalysisService(
            SqlAlchemyInventoryRepository(session),
            AnalysisThresholds(),
        )
        result = service.analyze(
            material_id=payload.material_id,
            warehouse_id=payload.warehouse_id,
            as_of_date=payload.as_of_date,
            trace_id=request.state.trace_id,
        )
        _set_request_outcome(request, result.metadata.status.value, "structured_analysis")
        return result

    @application.post("/api/v1/risks", response_model=RiskListResult)
    def list_risks(
        payload: RiskListRequest,
        session: Annotated[Session, Depends(get_session)],
        request: Request,
    ) -> RiskListResult:
        """返回按呆滞金额降序排列的风险清单。"""
        trace_id = request.state.trace_id
        service = InventoryAnalysisService(
            SqlAlchemyInventoryRepository(session),
            AnalysisThresholds(),
        )
        result = service.list_risks(
            warehouse_id=payload.warehouse_id,
            category=payload.category,
            as_of_date=payload.as_of_date,
            trace_id=trace_id,
        )
        _set_request_outcome(request, result.metadata.status.value, "list_inventory_risks")
        return result

    @application.get("/api/v1/tools", response_model=list[ToolDescriptor])
    def list_agent_tools(request: Request) -> list[ToolDescriptor]:
        """返回 Agent 可选择的受控工具，不暴露内部提示词或密钥。"""
        requirements = {
            AnalysisIntent.LIST_INVENTORY_RISKS: ["as_of_date"],
            AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE: [
                "material_id",
                "warehouse_id",
                "as_of_date",
            ],
            AnalysisIntent.TRACE_EVIDENCE: [
                "material_id",
                "warehouse_id",
                "as_of_date",
            ],
        }
        request.state.result_status = "ok"
        return [
            ToolDescriptor(
                name=intent.value,
                description=description,
                required_fields=requirements[intent],
            )
            for intent, description in TOOL_DESCRIPTIONS.items()
        ]

    @application.post("/api/v1/chat", response_model=AgentResponse)
    def chat(
        payload: AgentRequest,
        session: Annotated[Session, Depends(get_session)],
        request: Request,
    ) -> AgentResponse:
        """执行自然语言库存分析；无模型密钥时自动使用确定性降级路径。"""
        repository = SqlAlchemyInventoryRepository(session)
        response = invoke_agent(
            payload.model_copy(update={"trace_id": request.state.trace_id}),
            tools=InventoryAgentTools(repository),
            llm=request.app.state.agent_llm,
            sessions=request.app.state.agent_sessions,
            settings=request.app.state.agent_settings,
        )
        _set_request_outcome(
            request,
            response.status.value,
            response.selected_tool,
            _agent_error_category(response),
        )
        return response

    return application


def _set_request_outcome(
    request: Request,
    result_status: str,
    tool_name: str | None,
    error_category: str | None = None,
) -> None:
    request.state.result_status = result_status
    request.state.tool_name = tool_name
    request.state.error_category = error_category


def _agent_error_category(response: AgentResponse) -> str | None:
    if response.status.value == "needs_input":
        return "missing_parameters"
    if response.status.value == "degraded":
        return "execution_limit_reached"
    result = response.result
    if result is None:
        return "tool_execution_error" if response.status.value == "error" else None
    errors = getattr(result, "errors", [])
    if errors:
        return str(errors[0])
    blockers = getattr(result, "blockers", [])
    return str(blockers[0]) if blockers else None


def initialize_database(
    database_url: str,
    *,
    seed: int = 20260812,
    generated_at: datetime | None = None,
) -> dict[str, int]:
    """创建 SQLite 并写入固定 seed 数据，返回五张表记录数。"""
    engine: Engine = create_sqlite_engine(database_url)
    create_tables(engine)
    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        if repository.count_records()["materials"] == 0:
            repository.add_dataset(
                generate_synthetic_dataset(
                    seed=seed,
                    generated_at=generated_at or datetime.now(UTC),
                )
            )
        counts = repository.count_records()
    engine.dispose()
    return counts


app = create_app()
