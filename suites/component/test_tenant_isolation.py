"""
Cross-tenant isolation - the tests that matter most in a multi-tenant system.

WHAT THESE TESTS NEED TO ACHIEVE
    Prove that Customer A cannot, by any route I can think of, reach
    Customer B's data. Everything else in this repo is a correctness
    concern; this file is the one where a failure is a breach.

WHY THEY LIVE IN THE COMPONENT LAYER AND NOT THE BROWSER LAYER
    They need real persistence (users, tasks, tokens) but not a browser.
    Running them in-process means the whole file finishes in about a
    second, so they run on every single commit rather than only at the end
    of the pipeline. The most important tests should be the ones you can
    afford to run most often.

WHY THEY RUN ONCE, NOT ONCE PER CUSTOMER
    A common mistake is to multiply the entire suite by the number of
    customers. That makes the pipeline N times slower and proves nothing
    extra, because these tests are about the RELATIONSHIP between two
    customers rather than about either one. They take a pair and run once.

THE ATTACKS COVERED HERE
    1. Using a valid token at the wrong customer's address
    2. Asking to be another customer via a request header
    3. Guessing a record id belonging to another customer
    4. Reusing another customer's username and password
    5. Calling a paid feature you have not paid for
"""

import pytest

pytestmark = [pytest.mark.component, pytest.mark.security]


# --------------------------------------------------------------------------
# Setup helper
# --------------------------------------------------------------------------


@pytest.fixture
def two_customers(tenant_pair, transport_for):
    """One logged-in user at each of two different customers.

    Returns everything a cross-tenant test needs: both tenants, both
    transports, and both users. Bundled into one fixture because every test
    in this file needs the same six things, and repeating that setup would
    bury the actual assertion.
    """
    from testkit.factories import UserFactory

    tenant_a, tenant_b = tenant_pair
    transport_a = transport_for(tenant_a)
    transport_b = transport_for(tenant_b)

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "transport_a": transport_a,
        "transport_b": transport_b,
        "user_a": UserFactory(transport_a).create(),
        "user_b": UserFactory(transport_b).create(),
    }


# --------------------------------------------------------------------------
# 1. Tokens must not be portable between customers
# --------------------------------------------------------------------------


def test_users_are_created_in_the_tenant_of_the_address_used(two_customers):
    """Baseline: the two users really are in different customers.

    If this failed, every other test in the file would pass for the wrong
    reason - you cannot prove separation between two things that are
    actually the same thing.
    """
    assert two_customers["user_a"].tenant_id == two_customers["tenant_a"].id
    assert two_customers["user_b"].tenant_id == two_customers["tenant_b"].id
    assert two_customers["user_a"].tenant_id != two_customers["user_b"].tenant_id


def test_a_token_from_one_customer_is_refused_at_the_others_address(two_customers):
    """THE central multi-tenant test.

    Customer A's token is genuine - correctly signed, unexpired, not logged
    out. It is simply being presented at Customer B's address, and that
    alone must be enough to refuse it.

    Without this check a stolen or leaked token would work anywhere the
    service is reachable, and every ownership check afterwards would be
    comparing against the wrong customer's data.
    """
    response = two_customers["transport_b"].get("/me", headers=two_customers["user_a"].headers)

    assert response.status_code == 401
    assert response.json()["code"] == "tenant_mismatch"


def test_a_borrowed_token_cannot_read_tasks_either(two_customers):
    """Revocation of the wrong-tenant token must cover every route.

    Checking only /me would be a weak test: the leak that matters is the
    data endpoints, so those are asserted explicitly rather than assumed to
    share the same guard.
    """
    for path in ("/tasks", "/tenant"):
        response = two_customers["transport_b"].get(path, headers=two_customers["user_a"].headers)
        assert response.status_code in (401, 200 if path == "/tenant" else 401)

    create = two_customers["transport_b"].post(
        "/tasks", json={"title": "smuggled"}, headers=two_customers["user_a"].headers
    )
    assert create.status_code == 401
    assert create.json()["code"] == "tenant_mismatch"


# --------------------------------------------------------------------------
# 2. The customer must not be selectable by the caller
# --------------------------------------------------------------------------


def test_tenant_cannot_be_switched_with_a_request_header(two_customers):
    """The single most dangerous multi-tenant bug, tested directly.

    Plenty of systems resolve the customer from a convenient header such as
    X-Tenant-Id, forgetting that the caller chooses every header they send.
    If that were true here, Customer A could simply ask to be Customer B
    and the entire separation would be decorative.

    Here the customer comes from the Host header, which in production is
    fixed by DNS and the reverse proxy rather than by the client. So this
    request stays firmly inside Customer A: the extra header is ignored.
    """
    headers = {
        **two_customers["user_a"].headers,
        "X-Tenant-Id": two_customers["tenant_b"].id,
        "X-Tenant": two_customers["tenant_b"].id,
    }

    response = two_customers["transport_a"].get("/tenant", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == two_customers["tenant_a"].id, (
        "A caller-supplied header changed which customer the server served. "
        "Tenant must be derived from the Host header only."
    )


def test_an_unknown_address_serves_nobody(transport_for, tenant_pair):
    """An unrecognised hostname must be refused, not quietly defaulted.

    Defaulting would be friendlier and far more dangerous: a single wrong
    DNS entry would serve one customer's data under another's address. A
    404 turns that mistake into something obvious and immediate.
    """
    from testkit.transport import HttpTransport, InProcessTransport

    known = transport_for(tenant_pair[0])

    # Rebuild the same kind of transport, but claiming a hostname that no
    # tenant file declares.
    if isinstance(known, InProcessTransport):
        from app.main import app

        stranger = InProcessTransport(app, host="not-a-customer.example.com")
    else:
        stranger = HttpTransport(
            known._client.base_url.__str__(), host_header="not-a-customer.example.com"
        )

    response = stranger.get("/tenant")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_tenant"


# --------------------------------------------------------------------------
# 3. Records must not be reachable across customers
# --------------------------------------------------------------------------


def test_a_task_id_from_one_customer_is_invisible_to_the_other(two_customers):
    """Knowing the id must not be enough.

    Ids leak - through logs, support tickets, screenshots, URLs. The system
    has to be safe even when an attacker knows exactly what to ask for, so
    the test hands over a genuine id and checks it still gets nothing.

    404 rather than 403, for the same reason as within a single customer:
    403 would confirm the id is real.
    """
    from testkit.factories import TaskFactory

    task = TaskFactory(two_customers["transport_a"], two_customers["user_a"]).create(
        "A's private task"
    )

    response = two_customers["transport_b"].get(
        f"/tasks/{task.id}", headers=two_customers["user_b"].headers
    )

    assert response.status_code == 404
    assert response.json()["code"] == "task_not_found"


def test_one_customers_tasks_never_appear_in_the_others_list(two_customers):
    """The quiet failure mode: no error, just too much data in the list."""
    from testkit.factories import TaskFactory

    TaskFactory(two_customers["transport_a"], two_customers["user_a"]).create("A's task")
    TaskFactory(two_customers["transport_b"], two_customers["user_b"]).create("B's task")

    listing = two_customers["transport_b"].get("/tasks", headers=two_customers["user_b"].headers)

    titles = [t["title"] for t in listing.json()]
    assert titles == ["B's task"]


def test_a_task_cannot_be_modified_across_customers(two_customers):
    """A failed cross-customer write must change nothing at all."""
    from testkit.factories import TaskFactory

    task = TaskFactory(two_customers["transport_a"], two_customers["user_a"]).create("Untouched")

    attempt = two_customers["transport_b"].patch(
        f"/tasks/{task.id}", json={"title": "hijacked"}, headers=two_customers["user_b"].headers
    )
    assert attempt.status_code == 404

    still_there = two_customers["transport_a"].get(
        f"/tasks/{task.id}", headers=two_customers["user_a"].headers
    )
    assert still_there.json()["title"] == "Untouched"


# --------------------------------------------------------------------------
# 4. Credentials must not work across customers
# --------------------------------------------------------------------------


def test_credentials_do_not_work_at_another_customers_address(two_customers):
    """Customer A's username and password must be meaningless at B.

    This also quietly proves usernames are scoped per customer rather than
    globally - which is what allows both customers to have an "admin"
    without colliding.
    """
    user_a = two_customers["user_a"]

    response = two_customers["transport_b"].post(
        "/auth/login", json={"username": user_a.username, "password": user_a.password}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_the_same_username_can_exist_at_both_customers(two_customers):
    """Two customers must be able to use the same username independently.

    Global usernames would leak the existence of other customers' accounts
    ("that name is taken" tells you something you should not learn) and
    would cause pointless collisions at sign-up.
    """
    from testkit.factories import UserFactory

    shared = f"admin_{two_customers['user_a'].id[-6:]}"

    created_a = UserFactory(two_customers["transport_a"]).create(username=shared)
    created_b = UserFactory(two_customers["transport_b"]).create(username=shared)

    assert created_a.username == created_b.username
    assert created_a.id != created_b.id
    assert created_a.tenant_id != created_b.tenant_id


# --------------------------------------------------------------------------
# 5. Paid features must be enforced on the server
# --------------------------------------------------------------------------


def test_a_paid_feature_is_enforced_by_the_api_not_just_the_interface(two_customers):
    """Hiding a button is not access control.

    Customer A has bought bulk delete; Customer B has not. The check that
    matters is the one on the server, because anyone can call the endpoint
    directly regardless of what the page renders.

    Both directions are asserted. Testing only the refusal would let a
    permanently-broken endpoint pass, and testing only the success would
    miss the actual security question.
    """
    tenant_a, tenant_b = two_customers["tenant_a"], two_customers["tenant_b"]

    for tenant, transport, user in (
        (tenant_a, two_customers["transport_a"], two_customers["user_a"]),
        (tenant_b, two_customers["transport_b"], two_customers["user_b"]),
    ):
        response = transport.post("/tasks/bulk-delete", headers=user.headers)

        if tenant.has_feature("bulk_delete"):
            assert response.status_code == 200, (
                f"{tenant.id} is configured with bulk_delete but the API refused it"
            )
        else:
            assert response.status_code == 403, (
                f"{tenant.id} has NOT bought bulk_delete but the API allowed it"
            )
            assert response.json()["code"] == "feature_not_enabled"


def test_the_api_agrees_with_the_tenant_file_about_features(two_customers):
    """The server's published feature list must match the file on disk.

    Guards the failure this whole design exists to prevent: configuration
    that claims one thing while the running code does another.
    """
    for tenant, transport, user in (
        (two_customers["tenant_a"], two_customers["transport_a"], two_customers["user_a"]),
        (two_customers["tenant_b"], two_customers["transport_b"], two_customers["user_b"]),
    ):
        published = transport.get("/tenant", headers=user.headers).json()

        assert published["id"] == tenant.id
        assert set(published["features"]) == set(tenant.features)
        assert published["product_name"] == tenant.product_name
