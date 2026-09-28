"""
Journey layer setup - a real browser against a real server, per customer.

WHAT THIS FILE NEEDS TO ACHIEVE
    Three things the cheaper layers do not need:

      1. A server actually listening on a port.
      2. A browser that can resolve custa.taskflow.local and
         custb.taskflow.local, so each customer is reached at their own
         address exactly as in production.
      3. Tests that run once per customer where that matters, and only
         once where it does not.

HOW IT ACHIEVES IT
    - `live_server` starts uvicorn on a free port and waits for it.
    - `browser_type_launch_args` tells Chromium to map those hostnames to
      127.0.0.1 itself, so no hosts file and no admin rights are needed.
    - `testkit_transport` switches to "http" so the API factories talk to
      the very same server the browser is using.

A NOTE ON HOW FEW TESTS LIVE HERE
    Journeys are the slowest and most fragile layer, so each test has to
    justify itself by covering something no cheaper layer structurally can
    - real navigation, real browser storage, real rendering. Anything
    provable further left belongs further left.
"""

import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from testkit.config import repo_root
from testkit.tenants import get_tenant, load_tenants, tenant_ids


def _free_port() -> int:
    """Ask the OS for a port nobody is using.

    Binding to port 0 and reading back what we were given avoids the race
    of picking a number and hoping - which bites hardest in CI, where
    several jobs share one machine.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(
    url: str, process: subprocess.Popen, log_path: Path, timeout: float = 45.0
) -> None:
    """Block until the server answers, or explain why it never will.

    Two failure modes, deliberately reported differently:
      - the process died       -> show its log at once, no point waiting
      - alive but not replying -> keep polling, then time out WITH the log

    Both include the log, because a server that failed to start with no
    visible output is the most frustrating thing to debug in CI.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Server exited early with code {process.returncode}:\n"
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.25)

    raise TimeoutError(
        f"Server did not become ready at {url} within {timeout}s. Log:\n"
        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
    )


@pytest.fixture(scope="session")
def server_port() -> int:
    """The port the test server listens on.

    Kept separate from `live_server` because the browser launch arguments
    need the port before any page is opened, and a fixture should not have
    to parse it back out of a URL string.
    """
    return _free_port()


@pytest.fixture(scope="session")
def live_server(server_port) -> str:
    """A running instance, started here unless one is already provided.

    Honours TASKFLOW_BASE_URL so the same suite can be pointed at a
    deployed environment without changing a line of test code.
    """
    external = os.getenv("TASKFLOW_BASE_URL")
    if external:
        yield external.rstrip("/")
        return

    base_url = f"http://127.0.0.1:{server_port}"
    db_path = Path(tempfile.gettempdir()) / f"taskflow_journey_{uuid.uuid4().hex[:8]}.db"

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "ENABLE_TEST_ENDPOINTS": "true",
    }

    # Server output goes to a FILE, never to subprocess.PIPE.
    #
    # Not a style preference - a bug that cost real time. Nothing would be
    # draining a pipe while the tests run, so once the OS buffer filled
    # with access logs, uvicorn blocked on write and stopped answering. The
    # symptom is a server that is running, accepting connections, and never
    # replying: five tests pass and the rest time out. A file also means
    # the log survives for reading after a failure.
    log_path = Path(tempfile.gettempdir()) / f"taskflow_journey_{uuid.uuid4().hex[:8]}.log"
    log_file = log_path.open("w", encoding="utf-8")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
        ],
        cwd=repo_root(),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_until_ready(f"{base_url}/openapi.json", process, log_path)
        yield base_url
    finally:
        log_file.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

        # Windows refuses to unlink a file the exiting server still holds
        # open. It is in the temp directory and the OS will reclaim it -
        # never fail an otherwise green run on tidy-up.
        with contextlib.suppress(PermissionError):
            db_path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, server_port):
    """Teach Chromium to resolve our customer hostnames by itself.

    THE PROBLEM
        Each customer is reached at their own hostname, because that is how
        the server tells them apart. But custa.taskflow.local does not
        exist in DNS, and adding it to the machine's hosts file needs
        administrator rights and differs on every laptop and CI runner.

    THE SOLUTION
        Chromium's --host-resolver-rules maps hostnames to addresses inside
        the browser, for this session only. Nothing on the machine changes,
        no privileges are needed, and the browser genuinely sends
        `Host: custa.taskflow.local` - so the server routes exactly as it
        would in production.

    The rule is generated from the tenant files, so onboarding a customer
    needs no change here either.
    """
    rules = ",".join(
        f"MAP {tenant.hostname} 127.0.0.1:{server_port}" for tenant in load_tenants().values()
    )
    args = [*browser_type_launch_args.get("args", []), f"--host-resolver-rules={rules}"]
    return {**browser_type_launch_args, "args": args}


@pytest.fixture(scope="session")
def testkit_transport() -> str:
    """This suite talks to a real server over a real socket."""
    return "http"


@pytest.fixture(scope="session", autouse=True)
def _point_testkit_at_the_live_server(live_server):
    """Make the API factories use the same server the browser does.

    Autouse, and set before the session-scoped transport is built, so that
    seeding a user through the API and then signing in through the UI are
    guaranteed to reach one server rather than two.
    """
    os.environ["TASKFLOW_BASE_URL"] = live_server


@pytest.fixture
def ui_url(tenant, server_port) -> str:
    """Where the browser should go for the customer under test.

    Uses the customer's own hostname rather than 127.0.0.1, because the
    hostname is what the server reads to decide which customer this is.
    Navigating to the loopback address would bypass the very thing we want
    to exercise.
    """
    return f"http://{tenant.hostname}:{server_port}/app"


def pytest_generate_tests(metafunc):
    """Run a journey once per customer when it asks for `tenant`.

    WHY THIS HOOK EXISTS
        Journeys are the one layer where customers genuinely differ - the
        address, the branding, the visible features. So a journey that
        takes `tenant` is expanded into one test per tenant file.

    WHY IT IS NOT APPLIED EVERYWHERE
        The cheaper layers run ONCE. Multiplying the whole suite by the
        number of customers would make the pipeline N times slower while
        proving nothing extra, because business rules do not vary by
        customer. Only this layer pays the multiplier, and it is the
        smallest layer by design.

        Adding tenants/custc.yaml therefore adds browser coverage with no
        code change at all.
    """
    if "tenant" not in metafunc.fixturenames:
        return

    # --tenant narrows the expansion to one customer. This is what makes the
    # CI matrix worth having: each job runs its own customer only, instead
    # of every job redundantly running all of them.
    selected = metafunc.config.getoption("--tenant")
    ids = [selected] if selected else tenant_ids()
    metafunc.parametrize("tenant", ids, indirect=True, ids=ids)


@pytest.fixture
def tenant(request):
    """Override of testkit's `tenant`, driven by the parametrisation above.

    Function-scoped here, unlike the session-scoped default, because each
    parametrised case needs its own value.

    When pytest_generate_tests supplies a value it arrives as request.param;
    when it does not - a test that never mentions customers - it falls back
    to --tenant, then to the first configured customer.
    """
    chosen = getattr(request, "param", None) or request.config.getoption("--tenant")
    return get_tenant(chosen or tenant_ids()[0])


@pytest.fixture
def transport(transport_for, tenant):
    """Per-customer transport, overriding testkit's session-scoped one.

    WHY THE OVERRIDE IS NEEDED
        testkit's default `transport` is session-scoped, which is right for
        suites that run as a single customer. Here `tenant` varies per test
        because of the parametrisation above, and pytest forbids a
        session-scoped fixture from depending on a function-scoped one.

        `transport_for` keeps one client per customer behind the scenes, so
        making this function-scoped costs nothing - it hands back the same
        cached client for repeat visits to the same customer.
    """
    return transport_for(tenant)
