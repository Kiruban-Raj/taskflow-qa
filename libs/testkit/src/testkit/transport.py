"""How tests talk to the application.

Two implementations behind one interface:

  InProcessTransport — calls the ASGI app directly. Milliseconds, no server,
                       no port. Used by the unit-adjacent and component suites.
  HttpTransport      — real HTTP over the network to a deployed instance.
                       Used by the journey and smoke suites.

Why bother with the abstraction: it means the *factories* are written once
and work at every layer. Without it, each suite grows its own copy of "create
a user, log in, create a task" — which is exactly how a test suite ends up
with six subtly different definitions of the same setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Response:
    """A transport-agnostic response, so assertions don't care how we called."""

    status_code: int
    body: Any
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return self.body

    def raise_for_status(self) -> Response:
        if not self.ok:
            raise AssertionError(f"Request failed with {self.status_code}: {self.body}")
        return self


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response: ...


class _Base:
    def get(self, path, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path, **kw) -> Response:
        return self.request("POST", path, **kw)

    def patch(self, path, **kw) -> Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw) -> Response:
        return self.request("DELETE", path, **kw)


def _decode(raw) -> Any:
    if raw.status_code == 204 or not raw.content:
        return None
    try:
        return raw.json()
    except ValueError:
        return raw.text


class InProcessTransport(_Base):
    """Drive the ASGI app directly — no socket, no server process."""

    def __init__(self, app):
        from fastapi.testclient import TestClient

        self._client = TestClient(app)

    def request(self, method, path, *, json=None, params=None, headers=None) -> Response:
        raw = self._client.request(method, path, json=json, params=params, headers=headers)
        return Response(raw.status_code, _decode(raw), dict(raw.headers))


class HttpTransport(_Base):
    """Drive a running instance over real HTTP.

    Exercises the network stack, serialization and routing — the faults an
    in-process client structurally cannot see.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        import httpx

        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def request(self, method, path, *, json=None, params=None, headers=None) -> Response:
        raw = self._client.request(method, path, json=json, params=params, headers=headers)
        return Response(raw.status_code, _decode(raw), dict(raw.headers))

    def close(self) -> None:
        self._client.close()
