"""The pytest plugin.

Registered through the `pytest11` entry point in libs/testkit/pyproject.toml,
so installing testkit makes these fixtures available in every suite with no
conftest boilerplate anywhere.

The important fixture is `transport`: suites declare which layer they are by
setting `testkit_transport` in their own conftest, and everything downstream
(factories, users, tasks) is identical regardless. That is what lets the same
test body run in-process or over HTTP.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from .config import Settings
from .factories import TaskFactory, UserFactory
from .transport import HttpTransport, InProcessTransport


def pytest_addoption(parser):
    group = parser.getgroup("testkit")
    # Deliberately NOT --base-url: pytest-playwright (via pytest-base-url)
    # already registers that, and two plugins claiming one option string is
    # a hard argparse error that takes the whole session down.
    group.addoption(
        "--api-base-url",
        action="store",
        default=None,
        help="Base URL for HTTP-layer suites (default: $TASKFLOW_BASE_URL or http://127.0.0.1:8000)",
    )


@pytest.fixture(scope="session")
def settings(request) -> Settings:
    override = request.config.getoption("--api-base-url")
    if override:
        os.environ["TASKFLOW_BASE_URL"] = override
    return Settings.from_env()


@pytest.fixture(scope="session")
def testkit_transport() -> str:
    """Which transport this suite uses: "in_process" or "http".

    Overridden by a suite's conftest. Defaulting to in-process means a new
    suite is fast unless it deliberately opts into the slower layer.
    """
    return "in_process"


@pytest.fixture(scope="session")
def app_instance():
    """The ASGI app, with a throwaway database.

    Session-scoped because building it is expensive; isolation comes from
    every test provisioning its own user, not from rebuilding the app.
    """
    db_path = Path(tempfile.gettempdir()) / f"taskflow_test_{uuid.uuid4().hex[:8]}.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["ENABLE_TEST_ENDPOINTS"] = "true"

    from app.db import engine, init_db
    from app.main import app, seed_demo_users

    init_db()
    seed_demo_users()
    yield app

    # Release the file handle before unlinking: Windows refuses to delete a
    # file that still has an open handle, which shows up as a flaky teardown.
    engine.dispose()
    db_path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def transport(testkit_transport, settings, request):
    # Every branch yields: a `return` inside a generator fixture silently
    # hands the test None instead of a transport.
    if testkit_transport == "in_process":
        yield InProcessTransport(request.getfixturevalue("app_instance"))
    elif testkit_transport == "http":
        client = HttpTransport(settings.base_url)
        yield client
        client.close()
    else:
        raise ValueError(
            f"Unknown transport {testkit_transport!r}; expected 'in_process' or 'http'"
        )


@pytest.fixture
def users(transport) -> UserFactory:
    return UserFactory(transport)


@pytest.fixture
def user(users):
    """A fresh, logged-in user for this test alone."""
    return users.create()


@pytest.fixture
def tasks(transport, user) -> TaskFactory:
    return TaskFactory(transport, user)


@pytest.fixture
def auth_headers(user) -> dict[str, str]:
    return user.headers
