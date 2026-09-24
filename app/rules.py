"""Pure domain rules — no database, no HTTP, no framework.

Everything in this module is a plain function over plain data. That is the
point: these are the rules most likely to be wrong, and keeping them free of
I/O means they can be tested in milliseconds at the leftmost layer instead of
through a full HTTP round-trip.

If you are looking for the shift-left argument in one file, it is this one.
The HTTP layer in main.py should read as plumbing that *calls* these rules,
never as the place where a rule is decided.
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


# The allowed state machine. Expressed as data so it can be enumerated by
# tests (and by a property-based test) rather than rediscovered from a pile
# of if-statements.
ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.TODO: frozenset({TaskStatus.IN_PROGRESS}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.TODO, TaskStatus.DONE}),
    # Reopening sends work back to in_progress, never straight to todo:
    # finished work that turned out to be wrong is still work in flight.
    TaskStatus.DONE: frozenset({TaskStatus.IN_PROGRESS}),
}

TITLE_MIN_LENGTH = 1
TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 2000


class RuleViolation(ValueError):
    """A domain rule was broken. Carries a stable code for the API layer."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalise_title(raw: str) -> str:
    """Collapse surrounding whitespace. Titles differing only by padding are
    the same title, and storing the padded form makes search and dedup lie."""
    return " ".join(raw.split())


def validate_title(raw: str) -> str:
    title = normalise_title(raw)
    if len(title) < TITLE_MIN_LENGTH:
        raise RuleViolation("title_empty", "Title must not be empty")
    if len(title) > TITLE_MAX_LENGTH:
        raise RuleViolation(
            "title_too_long", f"Title must be at most {TITLE_MAX_LENGTH} characters"
        )
    return title


def validate_description(raw: str | None) -> str:
    if raw is None:
        return ""
    if len(raw) > DESCRIPTION_MAX_LENGTH:
        raise RuleViolation(
            "description_too_long",
            f"Description must be at most {DESCRIPTION_MAX_LENGTH} characters",
        )
    return raw


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    if current == target:
        return True  # idempotent no-op: re-sending the current status is not an error
    return target in ALLOWED_TRANSITIONS[current]


def validate_transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if not can_transition(current, target):
        allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS[current]))
        raise RuleViolation(
            "illegal_transition",
            f"Cannot move a task from {current.value} to {target.value} (allowed: {allowed})",
        )
    return target


def is_terminal(status: TaskStatus) -> bool:
    """No status is truly terminal here — done can always be reopened."""
    return False


def matches_filter(
    task_status: TaskStatus,
    task_title: str,
    *,
    status: TaskStatus | None = None,
    query: str | None = None,
) -> bool:
    """Whether a task survives the list filters. Pure, so the filtering
    contract is tested without a database in the loop."""
    if status is not None and task_status != status:
        return False
    if query:
        return query.strip().casefold() in task_title.casefold()
    return True
