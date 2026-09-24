import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./taskflow.db")

# check_same_thread is a SQLite-only quirk: TestClient and uvicorn both touch
# the connection from more than one thread.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        """Make SQLite tolerate concurrent readers and writers.

        By default SQLite serialises writers against readers, so a browser
        polling GET /tasks blocks a concurrent POST until the client gives
        up. That surfaced as bare request timeouts under the journey suite
        — no error message, just a server that stopped answering.

        WAL lets readers proceed during a write; busy_timeout makes a
        writer wait for the lock rather than failing immediately.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
