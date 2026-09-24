"""Journey layer configuration.

Two things differ from the cheaper layers:

  1. transport is "http" — journeys run against a real server over a real
     socket, because that is the only way to catch wiring, serialization and
     browser-context faults.
  2. a live server is started automatically unless TASKFLOW_BASE_URL already
     points somewhere, so `pytest suites/journey` just works locally.

Journeys are deliberately few. They are the slowest and least specific layer;
anything provable further left belongs further left.
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(
    url: str, process: subprocess.Popen, log_path: Path, timeout: float = 45.0
) -> None:
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
def live_server() -> str:
    """Base URL of a running instance.

    Reuses TASKFLOW_BASE_URL when set (CI, or pointing at a deployment);
    otherwise starts uvicorn on a free port against a throwaway database.
    """
    external = os.getenv("TASKFLOW_BASE_URL")
    if external:
        yield external.rstrip("/")
        return

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    db_path = Path(tempfile.gettempdir()) / f"taskflow_journey_{uuid.uuid4().hex[:8]}.db"

    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "ENABLE_TEST_ENDPOINTS": "true",
    }
    # Server output goes to a file, never to subprocess.PIPE. Nobody would
    # be draining a pipe while the tests run, so once the OS buffer filled
    # with access logs uvicorn would block on write and silently stop
    # answering — which looks exactly like a hung server. A file also means
    # the log survives for diagnosis after a failure.
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
            str(port),
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
        # Windows refuses to unlink a file the exiting server still has
        # open. It is in the temp directory and the OS will reclaim it —
        # never fail a green run on tidy-up.
        with contextlib.suppress(PermissionError):
            db_path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def testkit_transport() -> str:
    return "http"


@pytest.fixture(scope="session", autouse=True)
def _point_testkit_at_the_live_server(live_server):
    """Make the factories talk to the same instance the browser does.

    Set before the session-scoped `transport` fixture is built, so seeding a
    user via the API and then logging in through the UI hit one server.
    """
    os.environ["TASKFLOW_BASE_URL"] = live_server


@pytest.fixture
def ui_url(live_server) -> str:
    return f"{live_server}/app"
