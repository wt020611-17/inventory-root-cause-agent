"""FastAPI 应用入口与 Phase 1 本地依赖装配。"""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api.models import (
    AnalysisRequest,
    HealthResponse,
    InvalidRequestResponse,
    RiskListRequest,
)
from app.domain.results import AnalysisResult, RiskListResult
from app.domain.thresholds import AnalysisThresholds
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.services.inventory_analysis import InventoryAnalysisService
from app.synthetic.generator import generate_synthetic_dataset


def create_app(
    *,
    database_url: str = "sqlite+pysqlite:///./inventory_agent.db",
    seed: int = 20260812,
) -> FastAPI:
    """创建应用，并在生命周期启动阶段初始化固定 seed 合成数据库。"""
    engine = create_sqlite_engine(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        create_tables(engine)
        with session_scope(engine) as session:
            repository = SqlAlchemyInventoryRepository(session)
            if repository.count_records()["materials"] == 0:
                repository.add_dataset(generate_synthetic_dataset(seed=seed))
        app.state.engine = engine
        yield
        engine.dispose()

    application = FastAPI(
        title="Inventory Root-Cause Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """把422校验错误映射为带追踪号的稳定API结构。"""
        del request
        payload = InvalidRequestResponse(
            trace_id=uuid4().hex,
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

    def get_session(request: Request) -> Iterator[Session]:
        """为一次 HTTP 请求提供独立事务和 Session。"""
        with session_scope(request.app.state.engine) as session:
            yield session

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """返回最小健康状态，不暴露数据库或环境信息。"""
        return HealthResponse(status="ok", version="0.1.0", trace_id=uuid4().hex)

    @application.post("/api/v1/analysis", response_model=AnalysisResult)
    def analyze(
        payload: AnalysisRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> AnalysisResult:
        """执行结构化库存分析，并返回统一领域结果。"""
        service = InventoryAnalysisService(
            SqlAlchemyInventoryRepository(session),
            AnalysisThresholds(),
        )
        return service.analyze(
            material_id=payload.material_id,
            warehouse_id=payload.warehouse_id,
            as_of_date=payload.as_of_date,
            trace_id=uuid4().hex,
        )

    @application.post("/api/v1/risks", response_model=RiskListResult)
    def list_risks(
        payload: RiskListRequest,
        session: Annotated[Session, Depends(get_session)],
    ) -> RiskListResult:
        """返回按呆滞金额降序排列的风险清单。"""
        trace_id = uuid4().hex
        service = InventoryAnalysisService(
            SqlAlchemyInventoryRepository(session),
            AnalysisThresholds(),
        )
        return service.list_risks(
            warehouse_id=payload.warehouse_id,
            category=payload.category,
            as_of_date=payload.as_of_date,
            trace_id=trace_id,
        )

    return application


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
