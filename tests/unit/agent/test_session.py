"""会话 TTL、轮数上限与清理测试。"""

from datetime import UTC, date, datetime, timedelta

from app.agent.models import AgentParameters, AnalysisIntent
from app.agent.session import InMemorySessionStore


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 14, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_session_expires_and_does_not_reuse_old_parameters() -> None:
    clock = MutableClock()
    store = InMemorySessionStore(ttl_seconds=30, max_turns=6, clock=clock)
    store.save(
        session_id="expired",
        parameters=AgentParameters(
            material_id="MAT-SYN-MULTI",
            as_of_date=date(2026, 3, 31),
        ),
        intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
    )
    clock.now += timedelta(seconds=31)

    assert store.get("expired") is None
    assert store.cleanup() == 0


def test_session_stops_reuse_at_max_turns() -> None:
    store = InMemorySessionStore(ttl_seconds=60, max_turns=1)
    store.save(
        session_id="one-turn",
        parameters=AgentParameters(material_id="MAT-SYN-MULTI"),
        intent=AnalysisIntent.ANALYZE_MATERIAL_ROOT_CAUSE,
    )

    assert store.get("one-turn") is None
