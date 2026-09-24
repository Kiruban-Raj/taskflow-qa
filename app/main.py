"""HTTP layer.

Deliberately thin. Every domain decision lives in app/rules.py; this module
routes, authenticates, persists and translates RuleViolation into HTTP. If a
business rule starts creeping in here, it has escaped the layer where it can
be tested cheaply.
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

from . import auth
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
    TokenResponse,
    UserResponse,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    seed_demo_users()
    yield


app = FastAPI(
    title="TaskFlow API",
    version="1.0.0",
    description="A deliberately small task manager: login, logout, and CRUD over tasks.",
    lifespan=lifespan,
)


@app.exception_handler(RuleViolation)
async def _rule_violation_handler(_request: Request, exc: RuleViolation) -> JSONResponse:
    """One place translating domain errors into the ErrorResponse contract."""
    return JSONResponse(status_code=422, content={"code": exc.code, "detail": exc.message})


@app.exception_handler(HTTPException)
async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Flatten our {code, detail} payloads instead of letting FastAPI nest
    them under another "detail" key. Without this, every 4xx would violate
    the ErrorResponse contract the OpenAPI spec publishes."""
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        body = exc.detail
    else:
        body = {"code": "http_error", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _error(status_code: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "detail": detail})


# --------------------------------------------------------------------- auth


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _error(401, "missing_token", "Authorization header with a bearer token is required")

    token = authorization.split(" ", 1)[1].strip()
    # "Bearer " with nothing after it is a missing credential, not a
    # malformed one. Saying so points the caller at the actual bug.
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

    user = db.get(User, payload["sub"])
    if user is None:
        raise _error(401, "unknown_user", "Token refers to a user that no longer exists")
    return user


def _current_jti(authorization: str | None = Header(default=None)) -> str:
    token = (authorization or "").split(" ", 1)[-1].strip()
    return auth.decode_token(token)["jti"]


@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=payload.username).first()

    # Same error whether the username is unknown or the password is wrong —
    # distinguishing them hands an attacker a user-enumeration oracle.
    if user is None or not auth.verify_password(payload.password, user.password_hash):
        raise _error(401, "invalid_credentials", "Username or password is incorrect")

    token, _jti = auth.create_token(user.id, user.username)
    return TokenResponse(access_token=token)


@app.post("/auth/logout", status_code=204, tags=["auth"])
def logout(
    user: User = Depends(current_user),
    jti: str = Depends(_current_jti),
    db: Session = Depends(get_db),
):
    """Revoke the presented token server-side. Idempotent."""
    if db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti))
        db.commit()
    return None


@app.get("/me", response_model=UserResponse, tags=["auth"])
def me(user: User = Depends(current_user)):
    return user


# -------------------------------------------------------------------- tasks


@app.post("/tasks", response_model=TaskResponse, status_code=201, tags=["tasks"])
def create_task(
    payload: TaskCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    task = Task(
        id=f"tsk_{uuid.uuid4().hex[:12]}",
        owner_id=user.id,
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
    tasks = db.query(Task).filter_by(owner_id=user.id).order_by(Task.created_at.desc()).all()
    # Filtering is applied by the pure rule, so the API and the unit tests
    # cannot disagree about what a filter means.
    return [
        t
        for t in tasks
        if matches_filter(TaskStatus(t.status), t.title, status=status_filter, query=q)
    ]


def _owned_task(task_id: str, user: User, db: Session) -> Task:
    task = db.get(Task, task_id)
    # 404 rather than 403 for someone else's task: confirming existence would
    # leak that the id is real.
    if task is None or task.owner_id != user.id:
        raise _error(404, "task_not_found", "Task not found")
    return task


@app.get("/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
def get_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _owned_task(task_id, user, db)


@app.patch("/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
def update_task(
    task_id: str,
    payload: TaskUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
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
    task = _owned_task(task_id, user, db)
    db.delete(task)
    db.commit()
    return None


# ------------------------------------------------------------- test support


@app.post("/test/users", response_model=UserResponse, tags=["test-support"])
def create_test_user(
    username: str | None = None, password: str = "Passw0rd!", db: Session = Depends(get_db)
):
    """Test-data factory so each test provisions its own isolated user.

    Returns 404 unless ENABLE_TEST_ENDPOINTS=true, and there is a test that
    asserts exactly that — an unauthenticated user factory reachable in a
    real deployment would be a critical hole.
    """
    if os.getenv("ENABLE_TEST_ENDPOINTS") != "true":
        raise _error(404, "not_found", "Not found")

    user = User(
        id=f"usr_{uuid.uuid4().hex[:12]}",
        username=username or f"user_{uuid.uuid4().hex[:8]}",
        password_hash=auth.hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def seed_demo_users() -> None:
    """A known account so the UI is explorable by hand."""
    from .db import SessionLocal

    session = SessionLocal()
    try:
        if session.query(User).filter_by(username="demo").first() is None:
            session.add(
                User(
                    id="usr_demo",
                    username="demo",
                    password_hash=auth.hash_password("demo1234"),
                )
            )
            session.commit()
    finally:
        session.close()


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
