from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .rules import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH, TaskStatus


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    username: str

    model_config = ConfigDict(from_attributes=True)


class TaskCreateRequest(BaseModel):
    # Length bounds mirror app.rules so the framework rejects the obvious
    # cases early; rules.py remains the authority and is what the unit tests
    # assert against.
    title: str = Field(max_length=TITLE_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    status: TaskStatus | None = None


class TaskResponse(BaseModel):
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
