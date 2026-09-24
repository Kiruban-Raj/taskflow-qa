"""Unit layer — pure domain rules, no I/O of any kind.

Budget: under one second for the whole file. Nothing here may touch a
database, a socket or the FastAPI app. If a test needs any of those, it
belongs in suites/component.
"""

import pytest

from app.rules import (
    ALLOWED_TRANSITIONS,
    DESCRIPTION_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    RuleViolation,
    TaskStatus,
    can_transition,
    matches_filter,
    normalise_title,
    validate_description,
    validate_title,
    validate_transition,
)

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------- titles


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Buy milk", "Buy milk"),
        ("  Buy milk  ", "Buy milk"),
        ("Buy    milk", "Buy milk"),
        ("Buy\tmilk\n", "Buy milk"),
    ],
)
def test_titles_are_normalised(raw, expected):
    assert normalise_title(raw) == expected


def test_title_at_the_maximum_length_is_accepted():
    assert validate_title("x" * TITLE_MAX_LENGTH) == "x" * TITLE_MAX_LENGTH


def test_title_one_over_the_maximum_is_rejected():
    with pytest.raises(RuleViolation) as exc:
        validate_title("x" * (TITLE_MAX_LENGTH + 1))
    assert exc.value.code == "title_too_long"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_whitespace_only_titles_are_rejected(raw):
    """Whitespace-only must fail *after* normalisation, not before —
    otherwise "   " slips through as a 3-character title."""
    with pytest.raises(RuleViolation) as exc:
        validate_title(raw)
    assert exc.value.code == "title_empty"


def test_missing_description_becomes_empty_string():
    assert validate_description(None) == ""


def test_description_over_the_maximum_is_rejected():
    with pytest.raises(RuleViolation) as exc:
        validate_description("x" * (DESCRIPTION_MAX_LENGTH + 1))
    assert exc.value.code == "description_too_long"


# -------------------------------------------------------------- transitions


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.TODO, TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, TaskStatus.DONE),
        (TaskStatus.IN_PROGRESS, TaskStatus.TODO),
        (TaskStatus.DONE, TaskStatus.IN_PROGRESS),
    ],
)
def test_legal_transitions_are_allowed(current, target):
    assert validate_transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.TODO, TaskStatus.DONE),  # work cannot be finished before it is started
        (TaskStatus.DONE, TaskStatus.TODO),  # reopening returns to in_progress, not todo
    ],
)
def test_illegal_transitions_are_rejected(current, target):
    with pytest.raises(RuleViolation) as exc:
        validate_transition(current, target)
    assert exc.value.code == "illegal_transition"


@pytest.mark.parametrize("status", list(TaskStatus))
def test_restating_the_current_status_is_a_no_op(status):
    """PATCHing the status a task already has must not 422 — clients resend
    unchanged fields all the time."""
    assert can_transition(status, status)


def test_every_status_has_a_declared_transition_set():
    """Guards the state machine against a status added to the enum but
    forgotten in ALLOWED_TRANSITIONS, which would KeyError at runtime."""
    assert set(ALLOWED_TRANSITIONS) == set(TaskStatus)


def test_no_status_is_a_dead_end():
    for status, targets in ALLOWED_TRANSITIONS.items():
        assert targets, f"{status.value} has no way out"


# ----------------------------------------------------------------- filtering


def test_filter_with_no_criteria_matches_everything():
    assert matches_filter(TaskStatus.TODO, "anything")


def test_status_filter_matches_only_that_status():
    assert matches_filter(TaskStatus.DONE, "t", status=TaskStatus.DONE)
    assert not matches_filter(TaskStatus.TODO, "t", status=TaskStatus.DONE)


@pytest.mark.parametrize("query", ["milk", "MILK", "  milk  ", "ilk"])
def test_query_matching_is_case_insensitive_and_trimmed(query):
    assert matches_filter(TaskStatus.TODO, "Buy Milk", query=query)


def test_query_that_does_not_appear_excludes_the_task():
    assert not matches_filter(TaskStatus.TODO, "Buy milk", query="bread")


def test_empty_query_is_ignored_rather_than_matching_nothing():
    assert matches_filter(TaskStatus.TODO, "Buy milk", query="")
