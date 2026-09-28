"""
Test-data factories - every test brings its own data.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Make it a one-liner for a test to get a logged-in user, or a task in a
    particular state, without knowing anything about how the API works.

HOW IT ACHIEVES IT
    Plain classes written against the Transport interface. Because they
    depend on the interface and not a concrete client, the SAME factory
    serves a millisecond in-process component test and a real over-HTTP
    browser test. Nothing is written twice.

WHY EVERY TEST MAKES ITS OWN DATA
    The alternative - a shared set of fixture users seeded once - is the
    root of most flaky suites. Tests start depending on the order they run
    in, one test's edits leak into the next, and nothing can be
    parallelised. Provisioning per test costs milliseconds and removes that
    entire class of problem.

TENANCY
    Nothing here mentions a tenant, and that is deliberate. The customer is
    already baked into the transport (via its Host header), so a factory
    creates data in whichever customer the transport is pointed at. One
    fewer thing for every caller to remember to pass.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .transport import Transport

DEFAULT_PASSWORD = "Passw0rd!"


@dataclass(frozen=True)
class User:
    """A user a test can act as - already logged in, with their token.

    Holds the plain password as well as the token, because some tests need
    to log in again (to prove two sessions are independent, for example).
    """

    id: str
    username: str
    password: str
    token: str
    tenant_id: str

    @property
    def headers(self) -> dict[str, str]:
        """The Authorization header for this user. Saves rebuilding it everywhere."""
        return {"Authorization": f"Bearer {self.token}"}


@dataclass(frozen=True)
class Task:
    """Just enough of a created task for a test to refer to it afterwards."""

    id: str
    title: str
    status: str
    owner_id: str


class UserFactory:
    """Creates users in whichever customer the transport points at."""

    def __init__(self, transport: Transport):
        self._t = transport

    def create(self, *, username: str | None = None, password: str = DEFAULT_PASSWORD) -> User:
        """Provision a user and return them already logged in.

        Returning a logged-in user rather than a bare record is deliberate:
        almost every test needs the token, and a factory that stops one step
        short is a factory every caller has to finish by hand.
        """
        username = username or f"user_{uuid.uuid4().hex[:10]}"
        created = (
            self._t.post("/test/users", params={"username": username, "password": password})
            .raise_for_status()
            .json()
        )

        token = self.login(username, password)
        return User(
            id=created["id"],
            username=username,
            password=password,
            token=token,
            tenant_id=created["tenant_id"],
        )

    def login(self, username: str, password: str) -> str:
        """Sign in and return just the token.

        Exposed separately from create() because some tests need a SECOND
        session for a user who already exists - proving that logging out on
        one device leaves the other signed in, for example.
        """
        body = (
            self._t.post("/auth/login", json={"username": username, "password": password})
            .raise_for_status()
            .json()
        )
        return body["access_token"]


class TaskFactory:
    """Creates tasks owned by one particular user."""

    def __init__(self, transport: Transport, user: User):
        self._t = transport
        self._user = user

    def create(self, title: str | None = None, description: str = "") -> Task:
        """Create one task, with a generated title unless one is given.

        The random default matters: tests that do not care about the title
        still get a unique one, so a search or filter assertion elsewhere
        cannot accidentally match leftover data.
        """
        body = (
            self._t.post(
                "/tasks",
                json={"title": title or f"Task {uuid.uuid4().hex[:8]}", "description": description},
                headers=self._user.headers,
            )
            .raise_for_status()
            .json()
        )
        return Task(
            id=body["id"], title=body["title"], status=body["status"], owner_id=body["owner_id"]
        )

    def create_many(self, count: int) -> list[Task]:
        """Create several tasks at once, for list and pagination checks."""
        return [self.create() for _ in range(count)]

    def advance_to(self, task: Task, status: str) -> Task:
        """Walk a task to a target status through legal transitions only.

        Tests that need a `done` task should not have to know the state
        machine — and must not be able to cheat past it, or they would be
        setting up states the application can never produce.
        """
        route = {"todo": [], "in_progress": ["in_progress"], "done": ["in_progress", "done"]}
        if status not in route:
            raise ValueError(f"Unknown status: {status}")

        current = task
        for step in route[status]:
            body = (
                self._t.patch(
                    f"/tasks/{task.id}", json={"status": step}, headers=self._user.headers
                )
                .raise_for_status()
                .json()
            )
            current = Task(body["id"], body["title"], body["status"], body["owner_id"])
        return current
