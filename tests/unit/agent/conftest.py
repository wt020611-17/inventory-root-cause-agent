"""Agent 单元测试的固定 seed 合成仓库。"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from app.agent import AgentSettings, InMemorySessionStore
from app.repositories.database import create_sqlite_engine, create_tables, session_scope
from app.repositories.sqlalchemy_repository import SqlAlchemyInventoryRepository
from app.synthetic.generator import generate_synthetic_dataset
from app.tools import InventoryAgentTools


@pytest.fixture
def agent_tools() -> Iterator[InventoryAgentTools]:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    create_tables(engine)
    with session_scope(engine) as session:
        repository = SqlAlchemyInventoryRepository(session)
        repository.add_dataset(
            generate_synthetic_dataset(
                seed=20260812,
                generated_at=datetime(2026, 8, 12, tzinfo=UTC),
            )
        )
        yield InventoryAgentTools(repository)
    engine.dispose()


@pytest.fixture
def agent_settings() -> AgentSettings:
    return AgentSettings(_env_file=None)


@pytest.fixture
def session_store(agent_settings: AgentSettings) -> InMemorySessionStore:
    return InMemorySessionStore(
        ttl_seconds=agent_settings.session_ttl_seconds,
        max_turns=agent_settings.session_max_turns,
    )
