"""
Request and response shapes.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Define exactly what the API accepts and exactly what it returns, and
    have that definition enforced automatically rather than by hand.

HOW IT ACHIEVES IT
    Pydantic models. FastAPI validates incoming JSON against the request
    models before a handler runs, and serialises outgoing objects through
    the response models.

WHY THE RESPONSE MODELS MATTER MORE THAN THEY LOOK
    Pydantic emits ONLY the fields declared here. So adding a sensitive
    column to a database table cannot accidentally leak it through the API -
    it simply will not appear unless somebody also adds it to the model.
    That makes these classes a genuine safety net, not just documentation.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .rules import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH, TaskStatus


class LoginRequest(BaseModel):
    """Credentials submitted at sign-in.

    Length limits are here so absurd input is rejected before it reaches
    the password hashing step, which is deliberately slow and would
    otherwise be an easy way to waste server time.
    """

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """What a successful login hands back.

    token_type is spelled out rather than assumed, because clients build
    the Authorization header from it.
    """

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """A user, as the API describes them.

    Note what is absent: no password, no hash, not even a hint of one. The
    response model is the last line of defence against accidentally
    serialising a sensitive column, because Pydantic emits only the fields
    declared here - adding a column to the table cannot leak it by accident.
    """

    id: str
    username: str
    tenant_id: str

    model_config = ConfigDict(from_attributes=True)


class TenantResponse(BaseModel):
    """What the current customer looks like, and what they have bought.

    Read by the web page to render branding, and by the tests to check the
    server agrees with the tenant file on disk.
    """

    id: str
    display_name: str
    product_name: str
    accent_colour: str
    features: list[str]


class TaskCreateRequest(BaseModel):
    """What a client must send to create a task.

    Note the absence of a status field. Everything starts as `todo`, so
    there is no way to create a task that is already finished - the state
    machine cannot be skipped by inventing a starting point.
    """

    # Length bounds mirror app.rules so the framework rejects the obvious
    # cases early; rules.py remains the authority and is what the unit tests
    # assert against.
    title: str = Field(max_length=TITLE_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class TaskUpdateRequest(BaseModel):
    """A partial update - every field optional.

    None means "leave this alone", not "set it to empty". That is what
    makes PATCH work properly: a client changing only the status does not
    have to resend the title and risk clobbering a concurrent edit.
    """

    title: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    status: TaskStatus | None = None


class TaskResponse(BaseModel):
    """A task as the API describes it.

    from_attributes lets FastAPI build this straight from the SQLAlchemy
    row object, so handlers can return the database record directly and
    still get exactly these fields and no others.
    """

    id: str
    owner_id: str
    title: str
    description: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    """Every 4xx uses this shape, with a stable machine-readable code.

    Clients and tests should branch on `code`, never on `detail` prose.
    """

    code: str
    detail: str
