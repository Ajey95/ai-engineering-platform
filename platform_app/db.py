from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from platform_app.config import settings
from platform_app.model_base import Base, new_id, utcnow

__all__ = ["Base", "new_id", "utcnow", "engine", "SessionLocal", "session_scope"]


config = settings()
database_url = config.database_url
engine_options = {"pool_pre_ping": True}
if database_url.startswith("postgresql+psycopg://"):
    # A stopped database must fail promptly so API requests cannot hang behind
    # the driver's default connection timeout.
    engine_options.update(
        connect_args={"connect_timeout": 3}, pool_timeout=5,
        pool_size=config.db_pool_size, max_overflow=config.db_max_overflow,
    )
engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def session_scope() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
