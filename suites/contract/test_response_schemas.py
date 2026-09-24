"""Provider verification — do real responses validate against the contract?

test_contract_drift.py checks the *shape of the spec* against the app's
routes. This file checks actual response payloads against the declared
schemas, which is what catches a field that changed type or went missing.
"""

import jsonschema
import pytest
import yaml
from testkit.config import repo_root

pytestmark = [pytest.mark.contract]


@pytest.fixture(scope="session")
def spec() -> dict:
    return yaml.safe_load((repo_root() / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def validate(spec):
    """Validate a payload against a named schema from the contract.

    The components block is attached to each schema so internal $ref lookups
    resolve without needing a network-aware resolver.
    """

    def _validate(payload, schema_name: str):
        schema = dict(spec["components"]["schemas"][schema_name])
        schema["components"] = spec["components"]
        jsonschema.validate(payload, schema)

    return _validate


def test_login_response_matches_contract(transport, user, validate):
    response = transport.post(
        "/auth/login", json={"username": user.username, "password": user.password}
    )
    assert response.status_code == 200
    validate(response.json(), "TokenResponse")


def test_me_response_matches_contract(transport, user, validate):
    response = transport.get("/me", headers=user.headers)
    assert response.status_code == 200
    validate(response.json(), "UserResponse")


def test_created_task_matches_contract(transport, user, validate):
    response = transport.post("/tasks", json={"title": "Contract check"}, headers=user.headers)
    assert response.status_code == 201
    validate(response.json(), "TaskResponse")


def test_task_list_entries_match_contract(transport, user, tasks, validate):
    tasks.create_many(3)
    response = transport.get("/tasks", headers=user.headers)
    assert response.status_code == 200

    body = response.json()
    assert len(body) == 3
    for item in body:
        validate(item, "TaskResponse")


@pytest.mark.parametrize(
    ("description", "call"),
    [
        (
            "bad credentials",
            lambda t, u: t.post("/auth/login", json={"username": u.username, "password": "wrong"}),
        ),
        ("no token", lambda t, u: t.get("/me")),
        ("unknown task", lambda t, u: t.get("/tasks/tsk_does_not_exist", headers=u.headers)),
        (
            "illegal transition",
            lambda t, u: t.patch(
                f"/tasks/{t.post('/tasks', json={'title': 'x'}, headers=u.headers).json()['id']}",
                json={"status": "done"},
                headers=u.headers,
            ),
        ),
    ],
)
def test_every_error_path_returns_the_declared_error_shape(
    transport, user, validate, description, call
):
    """The error contract is the one most likely to rot, because error paths
    are the least exercised by hand."""
    response = call(transport, user)

    assert 400 <= response.status_code < 500, f"{description} should be a 4xx"
    validate(response.json(), "ErrorResponse")
    assert response.json()["code"], f"{description} returned an empty code"
