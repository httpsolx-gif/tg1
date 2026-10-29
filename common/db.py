from typing import Generator, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from common.config import settings

# SQLModel metadata alias
SQLALCHEMY_METADATA = SQLModel.metadata

# Lazily initialized singletons
_engine = None  # type: Optional[object]
_SessionLocal = None  # type: Optional[sessionmaker]


def get_engine(echo: bool = False):
    """Return a singleton SQLAlchemy engine.

    Supports SQLite (dev) and Postgres (prod).
    For SQLite we set check_same_thread=False.
    For network DBs we enable pool_pre_ping to avoid stale connections.
    """
    global _engine
    if _engine is not None:
        return _engine

    url = settings.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL is not set")

    if url.startswith("sqlite"):
        _engine = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
        )
    else:
        _engine = create_engine(
            url,
            echo=echo,
            future=True,
            pool_pre_ping=True,
            # без таймаутов недоступная БД вешала event loop на минуты (синхронные вызовы)
            # и не давала сторожу лидер-лока вовремя остановить процесс
            connect_args={"connect_timeout": 5, "keepalives": 1, "keepalives_idle": 10,
                          "keepalives_interval": 5, "keepalives_count": 3},
        )
    return _engine


def get_sessionmaker() -> sessionmaker:
    """Return a singleton sessionmaker bound to the engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


# --- Module-level aliases for convenience / backward-compat ---
engine = get_engine()
SessionLocal = get_sessionmaker()


def get_session() -> Generator:
    """Provide a DB session (context-manager style generator)."""
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Backward compatibility alias (if used somewhere)
get_sync_engine = get_engine
