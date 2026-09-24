"""Component layer — CRUD over tasks, including ownership isolation.

The rules themselves are unit-tested in suites/unit. What is verified here is
that the HTTP layer actually *applies* them, persists correctly, and keeps
one user's data away from another's.
"""

import pytest

pytestmark = [pytest.mark.component]


# ------------------------------------------------------------------ create


def test_create_returns_the_task_in_todo(transport, user):
    response = transport.post(
        "/tasks",
        json={"title": "Write the report", "description": "By Friday"},
        headers=user.headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Write the report"
    assert body["description"] == "By Friday"
    assert body["status"] == "todo"
    assert body["owner_id"] == user.id


def test_created_task_is_retrievable(transport, user, tasks):
    task = tasks.create("Fetch me")

    response = transport.get(f"/tasks/{task.id}", headers=user.headers)

    assert response.status_code == 200
    assert response.json()["title"] == "Fetch me"


def test_title_is_normalised_on_create(transport, user):
    """The rule is unit-tested; this proves the endpoint applies it."""
    response = transport.post("/tasks", json={"title": "  spaced   out  "}, headers=user.headers)

    assert response.status_code == 201
    assert response.json()["title"] == "spaced out"


def test_whitespace_only_title_is_rejected(transport, user):
    response = transport.post("/tasks", json={"title": "   "}, headers=user.headers)

    assert response.status_code == 422
    assert response.json()["code"] == "title_empty"


def test_description_defaults_to_empty_string_not_null(transport, user):
    """The contract declares description as a non-nullable string; a null
    here would break every generated client."""
    response = transport.post("/tasks", json={"title": "No description"}, headers=user.headers)

    assert response.json()["description"] == ""


# -------------------------------------------------------------------- read


def test_list_returns_only_the_callers_tasks(transport, users):
    """Ownership isolation — the single most important CRUD behaviour."""
    from testkit.factories import TaskFactory

    alice = users.create()
    bob = users.create()
    TaskFactory(transport, alice).create("Alice's task")
    TaskFactory(transport, bob).create("Bob's task")

    titles = [t["title"] for t in transport.get("/tasks", headers=alice.headers).json()]

    assert titles == ["Alice's task"]


def test_list_is_newest_first(transport, user, tasks):
    for title in ("first", "second", "third"):
        tasks.create(title)

    titles = [t["title"] for t in transport.get("/tasks", headers=user.headers).json()]

    assert titles == ["third", "second", "first"]


def test_list_starts_empty_for_a_new_user(transport, user):
    assert transport.get("/tasks", headers=user.headers).json() == []


@pytest.mark.parametrize("query", ["milk", "MILK", "ilk"])
def test_search_filters_by_title(transport, user, tasks, query):
    tasks.create("Buy milk")
    tasks.create("Walk the dog")

    body = transport.get("/tasks", params={"q": query}, headers=user.headers).json()

    assert [t["title"] for t in body] == ["Buy milk"]


def test_status_filter_narrows_the_list(transport, user, tasks):
    done = tasks.create("Finished")
    tasks.create("Not started")
    tasks.advance_to(done, "done")

    body = transport.get("/tasks", params={"status_filter": "done"}, headers=user.headers).json()

    assert [t["title"] for t in body] == ["Finished"]


# ------------------------------------------------------------------ update


def test_update_changes_the_title(transport, user, tasks):
    task = tasks.create("Before")

    response = transport.patch(f"/tasks/{task.id}", json={"title": "After"}, headers=user.headers)

    assert response.status_code == 200
    assert response.json()["title"] == "After"
    assert transport.get(f"/tasks/{task.id}", headers=user.headers).json()["title"] == "After"


def test_partial_update_leaves_other_fields_alone(transport, user, tasks):
    task = tasks.create("Keep me", description="Keep this too")

    transport.patch(f"/tasks/{task.id}", json={"status": "in_progress"}, headers=user.headers)

    body = transport.get(f"/tasks/{task.id}", headers=user.headers).json()
    assert body["title"] == "Keep me"
    assert body["description"] == "Keep this too"
    assert body["status"] == "in_progress"


def test_legal_status_progression_is_persisted(transport, user, tasks):
    task = tasks.create()

    for target in ("in_progress", "done"):
        response = transport.patch(
            f"/tasks/{task.id}", json={"status": target}, headers=user.headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == target


def test_illegal_transition_is_rejected_by_the_endpoint(transport, user, tasks):
    task = tasks.create()

    response = transport.patch(f"/tasks/{task.id}", json={"status": "done"}, headers=user.headers)

    assert response.status_code == 422
    assert response.json()["code"] == "illegal_transition"


def test_rejected_transition_leaves_the_task_unchanged(transport, user, tasks):
    """A failed write must not partially apply."""
    task = tasks.create()

    transport.patch(f"/tasks/{task.id}", json={"status": "done"}, headers=user.headers)

    assert transport.get(f"/tasks/{task.id}", headers=user.headers).json()["status"] == "todo"


def test_updated_at_moves_but_created_at_does_not(transport, user, tasks):
    task = tasks.create()
    before = transport.get(f"/tasks/{task.id}", headers=user.headers).json()

    transport.patch(f"/tasks/{task.id}", json={"title": "Touched"}, headers=user.headers)
    after = transport.get(f"/tasks/{task.id}", headers=user.headers).json()

    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] >= before["updated_at"]


# ------------------------------------------------------------------ delete


def test_delete_removes_the_task(transport, user, tasks):
    task = tasks.create()

    assert transport.delete(f"/tasks/{task.id}", headers=user.headers).status_code == 204
    assert transport.get(f"/tasks/{task.id}", headers=user.headers).status_code == 404


def test_deleting_twice_returns_not_found(transport, user, tasks):
    task = tasks.create()
    transport.delete(f"/tasks/{task.id}", headers=user.headers)

    assert transport.delete(f"/tasks/{task.id}", headers=user.headers).status_code == 404


# --------------------------------------------------------------- ownership


@pytest.mark.security
@pytest.mark.parametrize(
    ("method", "payload"),
    [("GET", None), ("PATCH", {"title": "hijacked"}), ("DELETE", None)],
)
def test_another_users_task_is_not_reachable(transport, users, method, payload):
    """Cross-tenant access returns 404, never 403.

    403 would confirm the id exists, letting an attacker enumerate valid
    task ids belonging to other people.
    """
    from testkit.factories import TaskFactory

    owner = users.create()
    intruder = users.create()
    task = TaskFactory(transport, owner).create("Private")

    response = transport.request(
        method, f"/tasks/{task.id}", json=payload, headers=intruder.headers
    )

    assert response.status_code == 404
    assert response.json()["code"] == "task_not_found"


@pytest.mark.security
def test_a_failed_hijack_leaves_the_original_intact(transport, users):
    from testkit.factories import TaskFactory

    owner = users.create()
    intruder = users.create()
    task = TaskFactory(transport, owner).create("Private")

    transport.patch(f"/tasks/{task.id}", json={"title": "hijacked"}, headers=intruder.headers)

    assert transport.get(f"/tasks/{task.id}", headers=owner.headers).json()["title"] == "Private"
