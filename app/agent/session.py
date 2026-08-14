"""只保存已确认最小上下文的可替换内存会话存储。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

from app.agent.models import AgentParameters, AnalysisIntent


class SessionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    parameters: AgentParameters = Field(default_factory=AgentParameters)
    intent: AnalysisIntent | None = None
    turn_count: int = Field(default=0, ge=0)
    updated_at: datetime


class InMemorySessionStore:
    """线程安全、带 TTL 和最大轮数的最小会话实现。"""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_turns: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_turns = max_turns
        self._clock = clock or (lambda: datetime.now(UTC))
        self._items: dict[str, SessionContext] = {}
        self._lock = RLock()

    def get(self, session_id: str) -> SessionContext | None:
        with self._lock:
            self.cleanup()
            context = self._items.get(session_id)
            if context is None or context.turn_count >= self._max_turns:
                self._items.pop(session_id, None)
                return None
            return context.model_copy(deep=True)

    def save(
        self,
        *,
        session_id: str,
        parameters: AgentParameters,
        intent: AnalysisIntent | None,
    ) -> SessionContext:
        with self._lock:
            previous = self._items.get(session_id)
            turn_count = 1 if previous is None else previous.turn_count + 1
            context = SessionContext(
                session_id=session_id,
                parameters=parameters,
                intent=intent,
                turn_count=turn_count,
                updated_at=self._clock(),
            )
            self._items[session_id] = context
            return context.model_copy(deep=True)

    def cleanup(self) -> int:
        with self._lock:
            cutoff = self._clock() - self._ttl
            expired = [key for key, value in self._items.items() if value.updated_at <= cutoff]
            for key in expired:
                self._items.pop(key, None)
            return len(expired)
