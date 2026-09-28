"""
The HTTP layer - URLs in, JSON out.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Expose the application over HTTP for several customers at once, while
    guaranteeing no customer can ever reach another's data. Concretely,
    every request has to answer four questions, in order:

        1. Which customer is this? ......... resolved from the Host header
        2. Who is the user? ................ resolved from the bearer token
        3. Do those two agree? ............. token's tenant must match the host
        4. Do they own what they asked for?  ownership check per record

HOW IT ACHIEVES IT
    Those questions are answered by FastAPI dependencies, so no handler can
    forget one - they are declared in the signature rather than rewritten in
    every function body.

A DELIBERATE CONSTRAINT ON THIS FILE
    There are no business rules here. What makes a title valid, or which
    status change is legal, lives in app/rules.py. This module should read
    as plumbing that calls those rules and turns their errors into HTTP. If
    a rule starts creeping in here, it has escaped the layer where it can be
    tested in milliseconds.
"""

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import auth, tenants
from .db import get_db, init_db
from .models import RevokedToken, Task, User
from .rules import (
    RuleViolation,
    TaskStatus,
    matches_filter,
    validate_description,
    validate_title,
    validate_transition,
)
from .schemas import (
    LoginRequest,
    TaskCreateRequest,
    TaskResponse,
    TaskUpdateRequest,
    TenantResponse,
    TokenResponse,
    UserResponse,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup work, run once before the first request is served.

    Loading tenants here rather than lazily is intentional: a broken tenant
    file should stop the service starting, loudly and immediately, instead
    of surfacing later as a wrong access decision for a real customer.
    """
    init_db()
    tenants.load_tenants()
    seed_demo_users()
    yield


app = FastAPI(
    title="TaskFlow API",
    version="1.1.0",
    description="A small multi-tenant task manager: login, logout, and CRUD over tasks.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# Error handling
#
# Goal: every 4xx this service produces has exactly one shape, {code, detail}.
# Clients and tests branch on `code`; `detail` is prose for humans and can be
# reworded at any time without breaking anybody.
# --------------------------------------------------------------------------


@app.exception_handler(RuleViolation)
async def _rule_violation_handler(_request: Request, exc: RuleViolation) -> JSONResponse:
    """Translate a broken domain rule into a 422, in one place.

    Because this exists, rules.py can raise a plain Python exception and stay
    completely unaware that HTTP exists.
    """
    return JSONResponse(status_code=422, content={"code": exc.code, "detail": exc.message})


@app.exception_handler(HTTPException)
async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Keep the error shape flat.

    FastAPI wraps whatever you pass as `detail` inside another "detail" key.
    Since we pass a {code, detail} dict, the default would produce
    {"detail": {"code": ..., "detail": ...}} and quietly break the contract
    published in contracts/openapi.yaml. This unwraps it.
    """
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        body = exc.detail
    else:
        body = {"code": "http_error", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Add two baseline security headers to every response.

    nosniff stops a browser guessing that a JSON response is really HTML and
    executing it. Referrer-Policy stops our URLs, which contain record ids,
    leaking to other sites in the Referer header.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _error(status_code: int, code: str, detail: str) -> HTTPException:
    """Build an error in our standard shape. Saves repeating the dict."""
    return HTTPException(status_code=status_code, detail={"code": code, "detail": detail})


# --------------------------------------------------------------------------
# Question 1: which customer is this?
# --------------------------------------------------------------------------


def current_tenant(host: str | None = Header(default=None)) -> tenants.Tenant:
    """Identify the customer from the Host header.

    WHY IT IS A DEPENDENCY
        Declaring it in a handler's signature means the tenant is resolved
        before that handler runs, every single time. A helper each handler
        had to remember to call would eventually be forgotten in one of them
        - and that one would be a data leak.

    WHY AN UNKNOWN HOST IS 404 AND NOT A DEFAULT
        Falling back to some default tenant would be friendlier and much
        more dangerous: one wrong DNS entry would silently serve a
        customer's data under somebody else's address. Refusing outright
        turns that into an obvious, immediate failure.
    """
    tenant = tenants.resolve_tenant(host)
    if tenant is None:
        raise _error(404, "unknown_tenant", "No customer is configured for this address")
    return tenant


@app.get("/tenant", response_model=TenantResponse, tags=["tenant"])
def describe_tenant(tenant: tenants.Tenant = Depends(current_tenant)):
    """Publish this customer's branding and enabled features.

    Two consumers, and that is the point:
      - the web page reads it to render the right product name and buttons,
        so branding is configuration rather than three copies of the HTML;
      - the tests read it to check the server agrees with the tenant file.
    """
    return TenantResponse(
        id=tenant.id,
        display_name=tenant.display_name,
        product_name=tenant.product_name,
        accent_colour=tenant.accent_colour,
        features=sorted(tenant.features),
    )


# --------------------------------------------------------------------------
# Questions 2 and 3: who is the user, and do they belong to this customer?
# --------------------------------------------------------------------------


def current_user(
    authorization: str | None = Header(default=None),
    tenant: tenants.Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate the caller, and confirm they belong to this customer.

    THE STEPS, IN ORDER, AND WHY EACH EXISTS
        1. Is there a bearer token at all?          -> 401 missing_token
        2. Well-formed, signed, unexpired?          -> 401 invalid/expired
        3. Has it been logged out?                  -> 401 token_revoked
        4. Does its tenant match this hostname?     -> 401 tenant_mismatch
        5. Does the user still exist?               -> 401 unknown_user

    STEP 4 IS WHAT MAKES MULTI-TENANCY REAL
        Without it, a valid Customer A token presented at Customer B's
        address would authenticate successfully, and every later ownership
        check would be comparing against the wrong customer's world.
        Matching the token's tenant claim against the host is what stops a
        token being portable between customers.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _error(401, "missing_token", "Authorization header with a bearer token is required")

    token = authorization.split(" ", 1)[1].strip()

    # "Bearer " with nothing after it is a MISSING credential, not a
    # malformed one. Saying so points the caller at their actual bug.
    if not token:
        raise _error(401, "missing_token", "Authorization header with a bearer token is required")

    try:
        payload = auth.decode_token(token)
    except jwt.ExpiredSignatureError:
        raise _error(401, "token_expired", "Token has expired") from None
    except jwt.PyJWTError:
        raise _error(401, "invalid_token", "Token is not valid") from None

    if db.get(RevokedToken, payload["jti"]) is not None:
        raise _error(401, "token_revoked", "Token has been revoked by logout")

    if payload.get("tenant") != tenant.id:
        # The wording does not confirm the token is otherwise valid. Saying
        # "valid token, wrong address" would tell an attacker their stolen
        # token is genuine and only being used in the wrong place.
        raise _error(401, "tenant_mismatch", "Token is not valid for this address")

    user = db.get(User, payload["sub"])
    if user is None:
        raise _error(401, "unknown_user", "Token refers to a user that no longer exists")

    # Belt and braces. The token claimed the right tenant, but the stored
    # user is the real authority. If these two ever disagree something is
    # badly wrong, and continuing would be worse than refusing.
    if user.tenant_id != tenant.id:
        raise _error(401, "tenant_mismatch", "Token is not valid for this address")

    return user


def _current_jti(authorization: str | None = Header(default=None)) -> str:
    """Pull the token's unique id out, so logout knows what to revoke.

    Safe to decode without error handling, because this dependency only ever
    runs alongside current_user, which has already validated the token.
    """
    token = (authorization or "").split(" ", 1)[-1].strip()
    return auth.decode_token(token)["jti"]


@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(
    payload: LoginRequest,
    tenant: tenants.Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
):
    """Exchange a username and password for a token.

    THE LOOKUP IS SCOPED TO THE TENANT
        Filtering by tenant_id as well as username lets two customers each
        have a user called "admin" without collision - and means Customer
        A's credentials simply do not exist at Customer B's address.

    WHY BOTH FAILURES RETURN THE SAME ERROR
        Unknown username and wrong password both give 401
        invalid_credentials. If they differed, anyone could discover which
        usernames exist by watching which error came back. That is a
        user-enumeration oracle, and there is a test asserting the two
        responses are identical.
    """
    user = db.query(User).filter_by(username=payload.username, tenant_id=tenant.id).first()

    if user is None or not auth.verify_password(payload.password, user.password_hash):
        raise _error(401, "invalid_credentials", "Username or password is incorrect")

    token, _jti = auth.create_token(user.id, user.username, tenant.id)
    return TokenResponse(access_token=token)


@app.post("/auth/logout", status_code=204, tags=["auth"])
def logout(
    user: User = Depends(current_user),
    jti: str = Depends(_current_jti),
    db: Session = Depends(get_db),
):
    """Cancel the token used to make this request.

    Only this one token is revoked, not every token the user holds. Signing
    out on a laptop must not sign you out on your phone - there is a test
    for exactly that.

    Writing only if absent makes a retry harmless, which matters because a
    client whose connection dropped will reasonably try logout again.
    """
    if db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti))
        db.commit()
    return None


@app.get("/me", response_model=UserResponse, tags=["auth"])
def me(user: User = Depends(current_user)):
    """Who am I? Used by the web page to show the signed-in username."""
    return user


# --------------------------------------------------------------------------
# Tasks - the CRUD surface
# --------------------------------------------------------------------------


@app.post("/tasks", response_model=TaskResponse, status_code=201, tags=["tasks"])
def create_task(
    payload: TaskCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Create a task owned by the caller.

    Title and description go through the rules module rather than being
    trusted. Note the status is not accepted from the client at all -
    everything starts as `todo`, so there is no way to create a task that is
    already finished.
    """
    task = Task(
        id=f"tsk_{uuid.uuid4().hex[:12]}",
        owner_id=user.id,
        tenant_id=user.tenant_id,
        title=validate_title(payload.title),
        description=validate_description(payload.description),
        status=TaskStatus.TODO.value,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@app.get("/tasks", response_model=list[TaskResponse], tags=["tasks"])
def list_tasks(
    status_filter: TaskStatus | None = None,
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """List the caller's tasks, newest first.

    The query filters on owner AND tenant. Owner alone would be enough
    today, since a user belongs to one customer - but writing both means a
    future bug in user lookup cannot become a cross-customer data leak.
    Defence in depth costs one extra clause here.

    Search and status filtering are applied by matches_filter() from the
    rules module, so what a filter means is defined once and unit-tested
    without a database in the loop.
    """
    tasks = (
        db.query(Task)
        .filter_by(owner_id=user.id, tenant_id=user.tenant_id)
        .order_by(Task.created_at.desc())
        .all()
    )
    return [
        t
        for t in tasks
        if matches_filter(TaskStatus(t.status), t.title, status=status_filter, query=q)
    ]


def _owned_task(task_id: str, user: User, db: Session) -> Task:
    """Fetch a task, or refuse if it is not this user's to see.

    WHY 404 AND NOT 403
        403 means "this exists, but you may not have it" - which confirms
        the id is real. An attacker could then guess ids and learn which
        belong to other people. 404 reveals nothing: a task that is not
        yours is indistinguishable from one that does not exist.

    Tenant is checked as well as owner, for the same defence-in-depth reason
    as list_tasks.
    """
    task = db.get(Task, task_id)
    if task is None or task.owner_id != user.id or task.tenant_id != user.tenant_id:
        raise _error(404, "task_not_found", "Task not found")
    return task


@app.get("/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
def get_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Fetch one task the caller owns."""
    return _owned_task(task_id, user, db)


@app.patch("/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
def update_task(
    task_id: str,
    payload: TaskUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Update part of a task.

    PATCH rather than PUT, because clients should be able to send only what
    changed. Each field is applied only when present, so omitting one leaves
    it alone rather than blanking it.

    A rejected status change raises before anything is committed, so a
    refused update leaves the task exactly as it was. There is a test for
    that, because a half-applied write is far worse than a clean refusal.
    """
    task = _owned_task(task_id, user, db)

    if payload.title is not None:
        task.title = validate_title(payload.title)
    if payload.description is not None:
        task.description = validate_description(payload.description)
    if payload.status is not None:
        task.status = validate_transition(TaskStatus(task.status), payload.status).value

    db.commit()
    db.refresh(task)
    return task


@app.delete("/tasks/{task_id}", status_code=204, tags=["tasks"])
def delete_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete one task the caller owns."""
    task = _owned_task(task_id, user, db)
    db.delete(task)
    db.commit()
    return None


@app.post("/tasks/bulk-delete", tags=["tasks"])
def bulk_delete_done(
    tenant: tenants.Tenant = Depends(current_tenant),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Delete all of the caller's finished tasks at once.

    THIS ENDPOINT EXISTS TO PROVE A POINT ABOUT FEATURE GATING
        It is an optional, paid feature. Customer A has it; Customer B does
        not. The check is HERE, on the server.

        The common mistake is to gate a feature by hiding its button in the
        interface. That is not access control, it is decoration - anyone can
        call the endpoint directly. So the suite deliberately calls this as
        Customer B and asserts a 403, rather than only checking the button
        is missing from the page.
    """
    if not tenant.has_feature("bulk_delete"):
        raise _error(403, "feature_not_enabled", "Bulk delete is not enabled for this customer")

    deleted = (
        db.query(Task)
        .filter_by(owner_id=user.id, tenant_id=user.tenant_id, status=TaskStatus.DONE.value)
        .delete()
    )
    db.commit()
    return {"deleted": deleted}


# --------------------------------------------------------------------------
# Test support
# --------------------------------------------------------------------------


@app.post("/test/users", response_model=UserResponse, tags=["test-support"])
def create_test_user(
    username: str | None = None,
    password: str = "Passw0rd!",
    tenant: tenants.Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
):
    """Create a user for a test to use, in the tenant of the calling address.

    WHY THIS ENDPOINT EXISTS
        Every test should bring its own data. Sharing a fixed set of users
        makes tests order-dependent and impossible to parallelise, because
        one test's changes leak into the next.

    WHY IT IS DANGEROUS, AND WHAT GUARDS IT
        It creates an account with no authentication at all. Shipped
        enabled, anyone could mint themselves a user. So it returns 404
        unless ENABLE_TEST_ENDPOINTS is exactly "true" - and there is a test
        asserting "True", "1" and "yes" all keep it shut, because those are
        the values most likely to be set by accident.

        That guard is one line, which is exactly the kind of line someone
        deletes during a tidy-up without knowing why it was there. Hence the
        test.
    """
    if os.getenv("ENABLE_TEST_ENDPOINTS") != "true":
        raise _error(404, "not_found", "Not found")

    user = User(
        id=f"usr_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant.id,
        username=username or f"user_{uuid.uuid4().hex[:8]}",
        password_hash=auth.hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def seed_demo_users() -> None:
    """Create one known account per customer, so the app can be explored by hand.

    Without this you could not open the page and look around without first
    writing a script to create yourself a user.
    """
    from .db import SessionLocal

    session = SessionLocal()
    try:
        for tenant in tenants.load_tenants().values():
            existing = session.query(User).filter_by(username="demo", tenant_id=tenant.id).first()
            if existing is None:
                session.add(
                    User(
                        id=f"usr_demo_{tenant.id}",
                        tenant_id=tenant.id,
                        username="demo",
                        password_hash=auth.hash_password("demo1234"),
                    )
                )
        session.commit()
    finally:
        session.close()


# The browser UI. Mounted last so it cannot shadow any API route above.
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
