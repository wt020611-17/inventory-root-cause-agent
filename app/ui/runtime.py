"""为 Streamlit 演示页装配可复用的固定 seed 本地运行时。"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine

from app.agent import (
    AgentRequest,
    AgentResponse,
    AgentSettings,
    DisabledAgentLLM,
    InMemorySessionStore,
    create_agent_llm,
    invoke_agent,
)
from app.agent.llm import AgentLLM
from app.core import configure_audit_logging
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.synthetic.generator import generate_synthetic_dataset
from app.tools import InventoryAgentTools


@dataclass(slots=True)
class UIRuntime:
    """缓存于 Streamlit 进程中的数据库、模型适配器与会话存储。"""

    engine: Engine
    settings: AgentSettings
    llm: AgentLLM
    sessions: InMemorySessionStore
    seed: int

    def close(self) -> None:
        self.engine.dispose()


def create_ui_runtime(*, seed: int = 20260812) -> UIRuntime:
    """创建纯本地合成数据库；不需要先启动 FastAPI。"""
    configure_audit_logging()
    settings = AgentSettings()
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        repository.add_dataset(
            generate_synthetic_dataset(
                seed=seed,
                generated_at=datetime(2026, 8, 12, tzinfo=UTC),
            )
        )
    return UIRuntime(
        engine=engine,
        settings=settings,
        llm=create_agent_llm(settings),
        sessions=InMemorySessionStore(
            ttl_seconds=settings.session_ttl_seconds,
            max_turns=settings.session_max_turns,
        ),
        seed=seed,
    )


def list_demo_options(runtime: UIRuntime) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """读取合成物料和仓库选项，UI 不维护第二份硬编码主数据。"""
    with session_scope(runtime.engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        materials = [(item.material_id, item.name) for item in repository.list_materials()]
        warehouses = [(item.warehouse_id, item.name) for item in repository.list_warehouses()]
    return materials, warehouses


def invoke_ui_agent(
    runtime: UIRuntime,
    request: AgentRequest,
    *,
    disable_llm: bool,
) -> AgentResponse:
    """在一次独立事务中执行 Agent；禁用模型时仍返回确定性事实。"""
    with session_scope(runtime.engine) as session:
        tools = InventoryAgentTools(SqlAlchemyInventoryRepository(session))
        return invoke_agent(
            request,
            tools=tools,
            llm=DisabledAgentLLM() if disable_llm else runtime.llm,
            sessions=runtime.sessions,
            settings=runtime.settings,
        )
