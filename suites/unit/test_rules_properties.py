"""Property-based tests over the domain rules.

Example-based tests check the cases you thought of. These check *invariants*
across inputs Hypothesis invents, and it is very good at finding the ones you
didn't — unicode whitespace, boundary lengths, adversarial strings.

This is a cheap shift-left win: the same class of edge case would otherwise
be found by a customer, or not at all.
"""

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.rules import (
    ALLOWED_TRANSITIONS,
    TITLE_MAX_LENGTH,
    RuleViolation,
    TaskStatus,
    matches_filter,
    normalise_title,
    validate_title,
)

pytestmark = [pytest.mark.unit]

statuses = st.sampled_from(list(TaskStatus))


# ------------------------------------------------------------------- titles


@given(st.text())
def test_normalising_is_idempotent(raw):
    """Normalising twice must equal normalising once, or stored titles drift
    every time a record is re-saved."""
    once = normalise_title(raw)
    assert normalise_title(once) == once


@given(st.text())
def test_a_normalised_title_never_has_leading_or_trailing_space(raw):
    result = normalise_title(raw)
    assert result == result.strip()


@given(st.text())
def test_normalising_never_lengthens_a_title(raw):
    """Collapsing whitespace can only shorten. If this ever fails, a title
    could pass validation and then exceed the column width on write."""
    assert len(normalise_title(raw)) <= len(raw)


@given(st.text(min_size=1, max_size=TITLE_MAX_LENGTH))
def test_validate_title_either_returns_a_normalised_title_or_raises(raw):
    """Total function: no input produces a half-valid result."""
    try:
        result = validate_title(raw)
    except RuleViolation as exc:
        # The try/except/else structure IS the property under test: the
        # function is total, so every input yields exactly one branch.
        # pytest.raises cannot express "either outcome is acceptable".
        assert exc.code in {"title_empty", "title_too_long"}  # noqa: PT017
    else:
        assert result == normalise_title(raw)
        assert 1 <= len(result) <= TITLE_MAX_LENGTH


@given(st.text(min_size=TITLE_MAX_LENGTH + 1, max_size=TITLE_MAX_LENGTH + 50))
def test_titles_over_the_limit_always_raise_unless_whitespace_collapses_them(raw):
    normalised = normalise_title(raw)
    if len(normalised) > TITLE_MAX_LENGTH:
        with pytest.raises(RuleViolation):
            validate_title(raw)


# -------------------------------------------------------------- transitions


@given(statuses, statuses)
def test_transition_validation_agrees_with_the_declared_table(current, target):
    """The imperative check and the declarative table must never disagree."""
    from app.rules import can_transition

    expected = current == target or target in ALLOWED_TRANSITIONS[current]
    assert can_transition(current, target) is expected


@given(statuses)
def test_every_status_is_reachable_from_every_other(start):
    """No task can get stranded. Breadth-first over the declared table —
    if someone adds a status with no inbound edge, this fails."""
    seen = {start}
    frontier = [start]
    while frontier:
        for nxt in ALLOWED_TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(TaskStatus), f"unreachable from {start.value}: {set(TaskStatus) - seen}"


# ----------------------------------------------------------------- filtering


@given(statuses, st.text(min_size=1), st.text(min_size=1))
def test_a_query_matching_the_title_always_matches(status, title, fragment):
    """If the fragment really is in the title, filtering must keep the task.
    Catches casefold asymmetries across unicode."""
    assume(fragment.strip())
    combined = title + fragment
    assume(fragment.strip().casefold() in combined.casefold())
    assert matches_filter(status, combined, query=fragment)


@given(statuses, st.text())
def test_status_filter_is_exact(status, title):
    for candidate in TaskStatus:
        assert matches_filter(status, title, status=candidate) is (status == candidate)
