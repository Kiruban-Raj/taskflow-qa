"""
Database connection and session handling.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Give the rest of the application one reliable way to reach the database,
    and make sure every connection is handed back afterwards. Nothing else -
    no queries, no tables, no rules.

HOW IT ACHIEVES IT
    One engine (the connection pool) and one session factory, created at
    import time and shared. Request handlers never build their own; they ask
    for the get_db dependency, which guarantees the cleanup.

WHY THE DATABASE URL COMES FROM THE ENVIRONMENT
    So the same code runs against a throwaway SQLite file in tests and a
    real server in production, with no branching here. A module that knows
    which environment it is in is a module you cannot test properly.
"""

import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./taskflow.db")

# check_same_thread is a SQLite-only quirk. SQLite normally refuses to let a
# connection be used from a different thread than the one that opened it,
# but both the test client and uvicorn legitimately do exactly that when
# handling requests. Other databases have no such restriction.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)

# expire_on_commit=False so objects stay readable after a commit. Without it,
# returning a freshly committed record triggers a surprise reload - and
# sometimes an error, if the session has already been closed.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        """Configure each new SQLite connection as it is opened.

        WHY THIS EXISTS - A REAL BUG, NOT A PRECAUTION
            By default SQLite serialises writers against readers. Under the
            browser tests, a page polling GET /tasks blocked a concurrent
            write until the client gave up waiting. The symptom was a server
            that had simply stopped responding, with nothing in any log.

        WHAT EACH SETTING DOES
            journal_mode=WAL  lets readers carry on while a write is in
                              progress, which is what fixed the stall.
            busy_timeout      makes a blocked writer WAIT up to 5 seconds for
                              the lock instead of failing instantly.
            foreign_keys=ON   SQLite ignores foreign keys unless asked. Without
                              this, the cascade delete on User.tasks would
                              silently leave orphaned rows behind.

        Registered as a connect listener because these are per-connection
        settings - setting them once at startup would not apply to
        connections the pool opens later.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    """Create any tables that do not exist yet.

    Enough for this project, and deliberately not a migration system. A real
    service needs Alembic, because create_all can add new tables but will
    never alter an existing one - a changed column would be silently ignored.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Hand a database session to one request, and always close it after.

    Used as a FastAPI dependency. The try/finally is the important part:
    the session is returned to the pool whether the handler succeeded,
    raised, or the client disconnected halfway through. Leaked sessions
    exhaust the pool, and then the whole service hangs waiting for one.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
