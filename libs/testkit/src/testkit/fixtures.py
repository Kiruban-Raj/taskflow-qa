"""
The pytest plugin - everything a test can ask for by name.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Give every suite the same building blocks (a way to call the app, a
    logged-in user, a task factory, the current customer) without any suite
    having to set them up itself.

HOW IT ACHIEVES IT
    It is registered as a pytest plugin through the `pytest11` entry point
    in libs/testkit/pyproject.toml. That single line is why there is no
    conftest boilerplate and no sys.path juggling anywhere in this repo:
    installing the package makes these fixtures available everywhere.

THE ONE IDEA THAT MAKES THE LAYERS WORK
    A suite says WHICH LAYER IT IS by overriding one fixture:

        # suites/journey/conftest.py
        @pytest.fixture(scope="session")
        def testkit_transport() -> str:
            return "http"

    Everything downstream - factories, users, tasks - is then identical.
    That is what lets the same helper serve a millisecond component test
    and a real browser test.

HOW TENANCY FITS IN
    The customer is a parameter, not a global. `tenant` decides which
    customer a test runs as, and `transport` is built to speak to that
    customer's address. `tenant_pair` hands a test two customers at once,
    which is the only way to test that they are properly separated.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from .config import Settings
from .factories import TaskFactory, UserFactory
from .tenants import TenantConfig, get_tenant, tenant_ids
from .transport import HttpTransport, InProcessTransport


def pytest_addoption(parser):
    """Register the command-line options this plugin understands."""
    group = parser.getgroup("testkit")

    # Deliberately NOT --base-url: pytest-playwright (through pytest-base-url)
    # already registers that name, and two plugins claiming one option string
    # is a hard argparse error that takes the entire session down before a
    # single test runs.
    group.addoption(
        "--api-base-url",
        action="store",
        default=None,
        help="Base URL for HTTP-layer suites (default: $TASKFLOW_BASE_URL or http://127.0.0.1:8000)",
    )
    group.addoption(
        "--tenant",
        action="store",
        default=None,
        help="Which customer to run as, e.g. --tenant custa. "
        "Defaults to the first tenant in tenants/. CI runs one job per tenant.",
    )


@pytest.fixture(scope="session")
def settings(request) -> Settings:
    """Environment configuration for the run."""
    override = request.config.getoption("--api-base-url")
    if override:
        os.environ["TASKFLOW_BASE_URL"] = override
    return Settings.from_env()


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tenant(request) -> TenantConfig:
    """The customer this test run is acting as.

    Chosen by --tenant, falling back to the first tenant alphabetically so
    that running `pytest` with no arguments still works.

    Session-scoped because switching customers mid-session would mean
    rebuilding the transport, and a test that needs two customers should
    use `tenant_pair` instead - which is explicit about what it is doing.
    """
    chosen = request.config.getoption("--tenant") or tenant_ids()[0]
    return get_tenant(chosen)


@pytest.fixture(scope="session")
def tenant_pair() -> tuple[TenantConfig, TenantConfig]:
    """Two different customers, for testing that they stay separated.

    WHY THIS EXISTS AS ITS OWN FIXTURE
        The most valuable multi-tenant tests need two customers in the same
        test - "a token from A must be refused at B's address" cannot be
        written with one. A single `tenant` fixture cannot express that, so
        rather than bend it, this returns a pair.

    Skips rather than fails when only one customer is configured, because
    that is a legitimate setup, not a broken one.
    """
    ids = tenant_ids()
    if len(ids) < 2:
        pytest.skip("cross-tenant tests need at least two tenants configured")
    return get_tenant(ids[0]), get_tenant(ids[1])


# --------------------------------------------------------------------------
# Talking to the application
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def testkit_transport() -> str:
    """Which transport this suite uses: "in_process" or "http".

    Overridden by a suite's own conftest. The default is the fast one, so a
    newly added suite is quick unless it deliberately opts into the slower
    layer - the incentive points the right way.
    """
    return "in_process"


@pytest.fixture(scope="session")
def app_instance():
    """The application object, wired to a throwaway database.

    Session-scoped because constructing it is relatively expensive.
    Isolation between tests does NOT come from rebuilding this - it comes
    from every test provisioning its own user through the factory. That is
    the cheaper and more reliable way round.
    """
    db_path = Path(tempfile.gettempdir()) / f"taskflow_test_{uuid.uuid4().hex[:8]}.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["ENABLE_TEST_ENDPOINTS"] = "true"

    from app.db import engine, init_db
    from app.main import app, seed_demo_users

    init_db()
    seed_demo_users()
    yield app

    # Release the file handle before unlinking. Windows refuses to delete a
    # file that still has an open handle, and it shows up as a flaky
    # teardown rather than an obvious error.
    engine.dispose()
    db_path.unlink(missing_ok=True)


def _build_transport(kind: str, tenant: TenantConfig, settings: Settings, request):
    """Construct the right transport for this suite and customer.

    Factored out of the fixture so that `transport` and `transport_for` can
    share it without duplicating the branch - there is exactly one place
    that knows how each transport is built.
    """
    if kind == "in_process":
        # Host header carries the customer, exactly as it would in production.
        return InProcessTransport(request.getfixturevalue("app_instance"), host=tenant.hostname)
    if kind == "http":
        # Connect to the real address, but claim the customer's hostname.
        return HttpTransport(settings.base_url, host_header=tenant.hostname)
    raise ValueError(f"Unknown transport {kind!r}; expected 'in_process' or 'http'")


@pytest.fixture(scope="session")
def transport(testkit_transport, tenant, settings, request):
    """How this test suite reaches the application, as the current customer.

    Note every branch yields. A bare `return` inside a generator fixture
    silently hands the test None instead of a transport, and the resulting
    AttributeError points nowhere near the cause.
    """
    client = _build_transport(testkit_transport, tenant, settings, request)
    yield client
    if hasattr(client, "close"):
        client.close()


@pytest.fixture(scope="session")
def transport_for(testkit_transport, settings, request):
    """Build a transport for an ARBITRARY customer, on demand.

    Needed by the cross-tenant tests: they hold two customers at once, so
    they cannot use the single session-wide `transport`. Returns a function
    rather than a value, so a test can ask for whichever customer it needs.

    Clients are cached per tenant and closed together at the end, so a test
    calling this repeatedly does not leak connections.
    """
    cache: dict[str, object] = {}

    def _for(tenant: TenantConfig):
        """Return a transport aimed at this customer, building it if needed."""
        if tenant.id not in cache:
            cache[tenant.id] = _build_transport(testkit_transport, tenant, settings, request)
        return cache[tenant.id]

    yield _for

    for client in cache.values():
        if hasattr(client, "close"):
            client.close()


# --------------------------------------------------------------------------
# Test data
# --------------------------------------------------------------------------


@pytest.fixture
def users(transport) -> UserFactory:
    """Creates users in the current customer's tenant."""
    return UserFactory(transport)


@pytest.fixture
def user(users):
    """A fresh, already-logged-in user belonging to this test alone.

    Function-scoped on purpose: sharing a user between tests is how suites
    become order-dependent and stop being safe to run in parallel.
    """
    return users.create()


@pytest.fixture
def tasks(transport, user) -> TaskFactory:
    """Creates tasks owned by the `user` fixture."""
    return TaskFactory(transport, user)


@pytest.fixture
def auth_headers(user) -> dict[str, str]:
    """Ready-made Authorization header for the current user."""
    return user.headers
