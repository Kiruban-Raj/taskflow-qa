"""Component layer — login, logout and token handling against real storage.

These need persistence (token revocation is a database fact), so they cannot
be unit tests. They still run in-process: no server, no browser, no port.
"""

import pytest

pytestmark = [pytest.mark.component, pytest.mark.security]


def test_login_with_valid_credentials_returns_a_token(transport, user):
    response = transport.post(
        "/auth/login", json={"username": user.username, "password": user.password}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"


def test_token_identifies_the_right_user(transport, user):
    response = transport.get("/me", headers=user.headers)

    assert response.status_code == 200
    assert response.json()["username"] == user.username


@pytest.mark.parametrize(
    ("username_source", "password"),
    [("real", "wrong-password"), ("unknown", "Passw0rd!")],
)
def test_bad_credentials_are_indistinguishable(transport, user, username_source, password):
    """Unknown username and wrong password must return the identical code.

    Any difference is a user-enumeration oracle: an attacker learns which
    accounts exist by diffing the responses.
    """
    username = user.username if username_source == "real" else "nobody_at_all"
    response = transport.post("/auth/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_password_is_never_echoed_back(transport, user):
    """Neither the login response nor /me may leak credential material."""
    login = transport.post(
        "/auth/login", json={"username": user.username, "password": user.password}
    )
    body = str(login.json()) + str(transport.get("/me", headers=user.headers).json())

    assert user.password not in body
    assert "password" not in body.lower()


def test_request_without_a_token_is_rejected(transport):
    response = transport.get("/me")

    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"


@pytest.mark.parametrize(
    ("header", "expected_code"),
    [
        ("Bearer not-a-jwt", "invalid_token"),
        ("Bearer ", "missing_token"),
        ("Basic dXNlcjpwYXNz", "missing_token"),
        ("", "missing_token"),
    ],
)
def test_malformed_authorization_headers_are_rejected(transport, header, expected_code):
    response = transport.get("/me", headers={"Authorization": header} if header else {})

    assert response.status_code == 401
    assert response.json()["code"] == expected_code


def test_a_tampered_token_is_rejected(transport, user):
    """Flipping the last character invalidates the signature."""
    tampered = user.token[:-1] + ("a" if user.token[-1] != "a" else "b")

    response = transport.get("/me", headers={"Authorization": f"Bearer {tampered}"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


# ------------------------------------------------------------------ logout


def test_logout_revokes_the_token_server_side(transport, user):
    """The core of why logout is a real endpoint and not a client-side
    localStorage clear: the token must stop working."""
    assert transport.get("/me", headers=user.headers).status_code == 200

    logout = transport.post("/auth/logout", headers=user.headers)
    assert logout.status_code == 204

    after = transport.get("/me", headers=user.headers)
    assert after.status_code == 401
    assert after.json()["code"] == "token_revoked"


def test_logout_is_idempotent(transport, user):
    """A client retrying logout after a dropped connection must not 500."""
    assert transport.post("/auth/logout", headers=user.headers).status_code == 204

    second = transport.post("/auth/logout", headers=user.headers)
    assert second.status_code == 401
    assert second.json()["code"] == "token_revoked"


def test_logging_out_one_session_leaves_another_working(transport, users):
    """Revocation is per-token, not per-user. Signing out on a laptop must
    not sign you out on a phone."""
    user = users.create()
    second_token = users.login(user.username, user.password)
    second_headers = {"Authorization": f"Bearer {second_token}"}

    transport.post("/auth/logout", headers=user.headers)

    assert transport.get("/me", headers=user.headers).status_code == 401
    assert transport.get("/me", headers=second_headers).status_code == 200


def test_revoked_token_cannot_be_used_for_task_access(transport, user, tasks):
    """Revocation must cover every authenticated route, not just /me."""
    task = tasks.create()
    transport.post("/auth/logout", headers=user.headers)

    assert transport.get("/tasks", headers=user.headers).status_code == 401
    assert transport.get(f"/tasks/{task.id}", headers=user.headers).status_code == 401
    assert transport.post("/tasks", json={"title": "x"}, headers=user.headers).status_code == 401
