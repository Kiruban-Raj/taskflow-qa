"""Test-data factories.

Written once, against the Transport interface, so the same factory serves an
in-process component test and an over-HTTP journey test. Every test
provisions its own data — no shared fixtures, no ordering dependencies, and
therefore safe to parallelise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .transport import Transport

DEFAULT_PASSWORD = "Passw0rd!"


@dataclass(frozen=True)
class User:
    id: str
    username: str
    password: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    status: str
    owner_id: str


class UserFactory:
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
        return User(id=created["id"], username=username, password=password, token=token)

    def login(self, username: str, password: str) -> str:
        body = (
            self._t.post("/auth/login", json={"username": username, "password": password})
            .raise_for_status()
            .json()
        )
        return body["access_token"]


class TaskFactory:
    def __init__(self, transport: Transport, user: User):
        self._t = transport
        self._user = user

    def create(self, title: str | None = None, description: str = "") -> Task:
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
