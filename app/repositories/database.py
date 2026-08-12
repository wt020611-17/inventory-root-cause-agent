"""SQLite 引擎、建表和事务生命周期工具。"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.repositories.models import Base


def create_sqlite_engine(database_url: str) -> Engine:
    """创建 SQLite 引擎；内存库使用静态连接池以跨 Session 保留数据。"""
    options: dict = {"connect_args": {"check_same_thread": False}}
    if database_url.endswith(":memory:"):
        options["poolclass"] = StaticPool
    return create_engine(database_url, **options)


def create_tables(engine: Engine) -> None:
    """创建 Phase 1 基线表；表结构开始演进时再引入迁移工具。"""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """提供自动提交、异常回滚和关闭的事务边界。"""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
