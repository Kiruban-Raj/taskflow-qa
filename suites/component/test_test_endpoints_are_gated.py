"""The test-data factory must be unreachable unless explicitly enabled.

/test/users mints a user without authentication. If it ever shipped enabled,
anyone could create an account at will. The guard is one line in main.py —
which is exactly the kind of line that gets refactored away by someone who
doesn't know why it's there, so it gets its own test.

Deliberately placed in the component suite rather than alongside the happy
paths: this is the negative case the whole test-support surface rests on.
"""

import pytest

pytestmark = [pytest.mark.component, pytest.mark.security]


def test_factory_returns_404_when_the_flag_is_unset(transport, monkeypatch):
    monkeypatch.delenv("ENABLE_TEST_ENDPOINTS", raising=False)

    response = transport.post("/test/users", params={"username": "sneaky"})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize("value", ["false", "True", "1", "yes", ""])
def test_factory_only_opens_for_the_exact_string_true(transport, monkeypatch, value):
    """Fail closed. Anything other than exactly "true" must stay shut —
    "True" and "1" are the values most likely to be set by accident."""
    monkeypatch.setenv("ENABLE_TEST_ENDPOINTS", value)

    response = transport.post("/test/users", params={"username": "sneaky"})

    assert response.status_code == 404


def test_factory_works_when_correctly_enabled(transport, monkeypatch):
    """The positive control: without this, the tests above could pass
    because the endpoint is broken rather than because it is gated."""
    monkeypatch.setenv("ENABLE_TEST_ENDPOINTS", "true")

    response = transport.post("/test/users", params={"username": "legitimate_user"})

    assert response.status_code == 200
    assert response.json()["username"] == "legitimate_user"
