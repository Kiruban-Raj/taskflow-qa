"""
Database tables.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Describe the three things we store - users, tasks, and cancelled tokens -
    and encode the relationships between them so the database itself helps
    enforce correctness rather than leaving it all to application code.

HOW IT ACHIEVES IT
    SQLAlchemy's declarative models. Each class is one table. Where a rule
    can be expressed as a database constraint (uniqueness, foreign keys,
    NOT NULL) it is expressed here, because a constraint is enforced even
    when somebody forgets the matching `if` statement in a request handler.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .rules import TaskStatus


class Base(DeclarativeBase):
    """Shared base class. SQLAlchemy collects every table definition through it."""


def _now() -> datetime:
    """Timestamps are always UTC.

    Storing local time is a bug waiting for the next clock change or the
    first server in another region. One helper, used everywhere, so there is
    no chance of a naive datetime sneaking into one column.
    """
    return datetime.now(UTC)


class User(Base):
    """A person who can log in.

    Every user belongs to exactly one tenant. That single column is what
    makes the whole data-isolation story possible: tasks belong to users,
    users belong to tenants, so a task can always be traced to one customer.
    """

    __tablename__ = "users"

    # Constraint before columns so the intent is the first thing read:
    # usernames are unique WITHIN a customer, not globally. Two different
    # customers can both legitimately have an "admin" - forcing global
    # uniqueness would leak the existence of other customers' accounts and
    # cause pointless collisions at sign-up.
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Only ever the hash, never the password. See app/auth.py for how it is
    # derived and why the format keeps its own parameters.
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    tasks: Mapped[list[Task]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Task(Base):
    """One item of work belonging to one user."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    # A task reaches its tenant through its owner, but the tenant is also
    # stored directly. Denormalised on purpose: it lets any query filter by
    # customer without a join, and it means a cross-tenant leak would need
    # two separate mistakes rather than one.
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default=TaskStatus.TODO.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # onupdate means SQLAlchemy refreshes this automatically on every write,
    # so no handler can forget to touch it.
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    owner: Mapped[User] = relationship(back_populates="tasks")


class RevokedToken(Base):
    """Tokens that have been logged out.

    WHAT PROBLEM THIS SOLVES
        A JWT is valid until it expires - the server does not "remember"
        issuing it. So without this table, logging out would just mean the
        browser throwing away a token it could perfectly well keep using.
        That is not a logout; it is a UI gesture.

    HOW IT SOLVES IT
        Every token carries a unique id (its `jti`). Logging out writes that
        id here, and every authenticated request checks the table first. The
        token still exists, but we now refuse it.

        This also makes logout genuinely testable as backend behaviour,
        which is why the suite can prove that logging out on one device
        leaves another device signed in.
    """

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
