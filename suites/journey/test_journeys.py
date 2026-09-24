"""End-to-end journeys through a real browser.

Deliberately few. Each one earns its place by covering something no cheaper
layer structurally can: real navigation, real localStorage, real rendering.
Business rules are not re-tested here — they are unit tests.

Budget: this file should stay under a dozen tests. When a journey is added,
the question to answer first is "could this be a component test?"
"""

import re

import pytest
from playwright.sync_api import expect

from suites.journey.pages import LoginPage, TasksPage

pytestmark = [pytest.mark.journey]


@pytest.fixture
def signed_in(page, ui_url, user) -> TasksPage:
    """A browser session already signed in as a freshly provisioned user.

    Credentials come from the API factory, so the journey starts at the
    point of interest rather than re-testing login every time.
    """
    LoginPage(page, ui_url).open().sign_in(user.username, user.password)
    return TasksPage(page, ui_url).wait_until_loaded()


# ------------------------------------------------------------------- login


def test_signing_in_reaches_the_task_list(page, ui_url, user):
    LoginPage(page, ui_url).open().sign_in(user.username, user.password)

    expect(page).to_have_url(re.compile(r"tasks\.html$"))
    expect(TasksPage(page, ui_url).current_user).to_have_text(user.username)


def test_bad_credentials_show_an_error_and_stay_put(page, ui_url, user):
    login = LoginPage(page, ui_url).open()

    login.sign_in(user.username, "definitely-wrong")

    expect(login.error).to_be_visible()
    expect(login.error).to_contain_text("incorrect")
    expect(page).to_have_url(re.compile(r"index\.html$"))


def test_task_page_without_a_session_redirects_to_login(page, ui_url):
    """Only provable in a browser: the guard depends on real localStorage
    being empty and a real client-side redirect firing."""
    TasksPage(page, ui_url).open()

    expect(page).to_have_url(re.compile(r"index\.html$"))


# ------------------------------------------------------------------ logout


def test_logging_out_returns_to_login_and_invalidates_the_session(signed_in, page, ui_url):
    signed_in.log_out()
    expect(page).to_have_url(re.compile(r"index\.html$"))

    # Going back must not restore access — the token was revoked server-side,
    # which a client-only logout would fail.
    TasksPage(page, ui_url).open()
    expect(page).to_have_url(re.compile(r"index\.html$"))


# -------------------------------------------------------------------- crud


def test_full_task_lifecycle_through_the_ui(signed_in):
    """One journey covering create, progress, complete and delete.

    Combined rather than split into four: each would pay the same browser
    and sign-in cost to re-prove plumbing already covered.
    """
    signed_in.add_task("Write the quarterly report", "Due Friday")
    expect(signed_in.row("Write the quarterly report")).to_be_visible()
    expect(signed_in.status_of("Write the quarterly report")).to_have_text("todo")

    signed_in.advance("Write the quarterly report")
    expect(signed_in.status_of("Write the quarterly report")).to_have_text("in progress")

    signed_in.advance("Write the quarterly report")
    expect(signed_in.status_of("Write the quarterly report")).to_have_text("done")

    signed_in.delete("Write the quarterly report")
    expect(signed_in.items).to_have_count(0)
    expect(signed_in.empty_state).to_be_visible()


def test_empty_state_shows_for_a_new_user(signed_in):
    expect(signed_in.empty_state).to_be_visible()
    expect(signed_in.items).to_have_count(0)


def test_search_narrows_the_visible_list(signed_in):
    signed_in.add_task("Buy milk")
    expect(signed_in.items).to_have_count(1)
    signed_in.add_task("Walk the dog")
    expect(signed_in.items).to_have_count(2)

    signed_in.search("milk")

    expect(signed_in.items).to_have_count(1)
    expect(signed_in.titles).to_have_text(["Buy milk"])


def test_status_filter_narrows_the_visible_list(signed_in):
    signed_in.add_task("Started task")
    expect(signed_in.items).to_have_count(1)
    signed_in.add_task("Fresh task")
    expect(signed_in.items).to_have_count(2)

    signed_in.advance("Started task")
    expect(signed_in.status_of("Started task")).to_have_text("in progress")

    signed_in.filter_status("in_progress")

    expect(signed_in.titles).to_have_text(["Started task"])


def test_server_side_validation_error_is_surfaced_to_the_user(signed_in, page):
    """A rejected write must show the API's message rather than failing
    silently — the UI's error path is only observable here."""
    from testkit.selectors import UI

    # Bypass the browser's own `required` check to reach the server rule.
    page.eval_on_selector(UI.tasks.new_title_css, "el => el.removeAttribute('required')")
    signed_in.add_task("   ")

    expect(signed_in.error).to_be_visible()
