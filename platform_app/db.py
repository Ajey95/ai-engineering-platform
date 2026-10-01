from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from platform_app.config import settings
from platform_app.model_base import Base, new_id, utcnow

__all__ = ["Base", "new_id", "utcnow", "engine", "SessionLocal", "session_scope"]


engine = create_engine(settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def session_scope() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
