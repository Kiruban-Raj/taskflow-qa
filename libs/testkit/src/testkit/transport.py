"""
How tests talk to the application.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Let one piece of test code run at two very different speeds, without
    that code knowing or caring which:

      - in-process: call the app object directly. Milliseconds. No server,
        no port, no network. Used by the unit-adjacent and component layers.
      - over HTTP: a real request down a real socket to a real server. Used
        by the journey and smoke layers, because only that path exercises
        routing, serialisation and connection handling.

HOW IT ACHIEVES IT
    Both hide behind one small interface - .get/.post/.patch/.delete, all
    returning the same Response object.

WHY BOTHER WITH THE ABSTRACTION
    So the FACTORIES are written once. Without it, every suite grows its own
    copy of "create a user, log in, create a task", those copies slowly
    drift, and eventually two suites disagree about how the system behaves.
    With it, a component test and a browser test create a user with the
    identical line of code.

THE MULTI-TENANT PART
    Both transports can send a specific Host header, because that is how the
    application decides which customer a request belongs to. The HTTP
    transport deliberately CONNECTS to one address while CLAIMING to be
    another - see its docstring for why that is right, not a hack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Response:
    """A reply, with the transport's fingerprints wiped off.

    Whether the request went through a socket or straight into the app
    object, the test sees this same object. Frozen because a response is a
    record of what happened - nothing should edit it after the fact.
    """

    status_code: int
    body: Any
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        """True for any 2xx. Saves repeating the range check everywhere."""
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        """The decoded body. Named json() to match what httpx users expect."""
        return self.body

    def raise_for_status(self) -> Response:
        """Blow up unless the call succeeded, and return self so it chains.

        Used by the factories. When setup fails, the test should fail right
        there with the real status and body - not three lines later with a
        confusing KeyError because the response was an error payload.
        """
        if not self.ok:
            raise AssertionError(f"Request failed with {self.status_code}: {self.body}")
        return self


class Transport(Protocol):
    """The contract both transports satisfy.

    A Protocol rather than a base class: the two implementations share no
    code worth inheriting, and this way a type checker still verifies that
    anything passed as a Transport has the right shape.
    """

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Send one request and return a normalised Response.

        The single method both transports must provide; everything else in
        _Base is a convenience wrapper around it.
        """
        ...


class _Base:
    """Shorthand verbs, implemented once for both transports.

    Each just forwards to request(), so neither implementation has to repeat
    four near-identical wrappers.
    """

    def get(self, path, **kw) -> Response:
        """Read something."""
        return self.request("GET", path, **kw)

    def post(self, path, **kw) -> Response:
        """Create something, or perform an action."""
        return self.request("POST", path, **kw)

    def patch(self, path, **kw) -> Response:
        """Change part of something."""
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw) -> Response:
        """Remove something."""
        return self.request("DELETE", path, **kw)


def _decode(raw) -> Any:
    """Turn a raw library response body into something usable.

    Three cases, all of which really happen:
      - 204 No Content, or an empty body -> None, rather than a crash
      - valid JSON                       -> the parsed object
      - anything else, such as an HTML error page -> the raw text, so the
        failure message shows what actually came back instead of a
        JSONDecodeError hiding it
    """
    if raw.status_code == 204 or not raw.content:
        return None
    try:
        return raw.json()
    except ValueError:
        return raw.text


class InProcessTransport(_Base):
    """Drive the app object directly - no socket, no server process.

    This is what makes the component layer fast enough to run on every save.
    The request never leaves the Python process.

    `host` sets the Host header on every request, which is how the
    application works out which customer is calling. Passing it through
    TestClient's base_url is the tidiest route, because TestClient derives
    the Host header from it automatically.
    """

    def __init__(self, app, host: str | None = None):
        """Wrap the app in a test client that claims to be `host`."""
        from fastapi.testclient import TestClient

        base_url = f"http://{host}" if host else "http://testserver"
        self._client = TestClient(app, base_url=base_url)

    def request(self, method, path, *, json=None, params=None, headers=None) -> Response:
        """Call the app directly and normalise whatever comes back."""
        raw = self._client.request(method, path, json=json, params=params, headers=headers)
        return Response(raw.status_code, _decode(raw), dict(raw.headers))


class HttpTransport(_Base):
    """Drive a running server over real HTTP.

    Slower, and worth it: the only transport that exercises the network
    stack, real serialisation and real connection handling - the faults an
    in-process client structurally cannot see.

    CONNECTING TO ONE ADDRESS WHILE CLAIMING TO BE ANOTHER
        In production, Customer A's traffic arrives at custa.example.com
        because DNS says so. On a test machine there is no DNS for these
        names, and adding some would mean editing the hosts file - which
        needs administrator rights and differs on every machine.

        So: connect to 127.0.0.1 on whichever port the test server grabbed,
        but send `Host: custa.taskflow.local`. The server sees exactly what
        it would see in production and routes accordingly.

        This is not a trick for the tests' convenience. It is precisely what
        a reverse proxy does in front of a real deployment.
    """

    def __init__(self, base_url: str, host_header: str | None = None, timeout: float = 30.0):
        """Open a client that connects to base_url but claims host_header.

        The timeout is generous because a CI runner under load is much
        slower than a laptop, and a flaky timeout is worse than a slow test.
        """
        import httpx

        headers = {"Host": host_header} if host_header else None
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def request(self, method, path, *, json=None, params=None, headers=None) -> Response:
        """Send a real HTTP request and normalise whatever comes back."""
        raw = self._client.request(method, path, json=json, params=params, headers=headers)
        return Response(raw.status_code, _decode(raw), dict(raw.headers))

    def close(self) -> None:
        """Release the connection pool. Called by the fixture's teardown."""
        self._client.close()
