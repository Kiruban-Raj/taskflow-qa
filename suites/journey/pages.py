"""
Page objects - one class per page, describing what you can DO there.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Keep the mechanics of driving a browser out of the tests. A test should
    read like a description of what a person did ("sign in, add a task,
    complete it"), not like a list of clicks and CSS selectors.

HOW IT ACHIEVES IT
    Each page gets a class. Its methods are actions and questions, phrased
    in the language of the product. When the markup changes, exactly one
    file changes - the tests do not.

THE SELECTOR RULE, AND WHY IT IS ABSOLUTE
    Every selector comes from testkit.selectors, which is generated from
    contracts/ui-contract.yaml. There is not one literal CSS string in this
    file, and there should never be.

    The payoff: renaming a data-testid becomes an AttributeError at import
    time that names the exact attribute. Without it, the same rename is a
    thirty-second wait followed by "element not found", which tells you
    nothing about what actually changed.

A NOTE ON WAITING
    Playwright's expect() retries until a condition holds or it times out,
    so these methods do not sleep and do not poll by hand. Any place you
    see an explicit wait, it is because we are waiting for something
    Playwright cannot see on its own.
"""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect
from testkit.selectors import UI


class LoginPage:
    """The sign-in page.

    Deliberately small. Its only jobs are to get you signed in, and to let
    a test inspect the error when you should not be.
    """

    def __init__(self, page: Page, base_url: str):
        """Bind to a browser page and the customer's base address.

        base_url is passed in rather than read from config because each
        customer has their own address, and the same page object has to
        work for all of them.
        """
        self.page = page
        self.base_url = base_url

    def open(self) -> LoginPage:
        """Navigate to the login page and wait until the form is really there.

        Waiting for the form (rather than just for navigation) means the
        page is genuinely interactive before a test tries to type into it.
        Returns self so callers can chain: LoginPage(...).open().sign_in(...)
        """
        self.page.goto(f"{self.base_url}/index.html")
        expect(self.page.locator(UI.login.form_css)).to_be_visible()
        return self

    def sign_in(self, username: str, password: str) -> None:
        """Fill in the credentials and submit.

        Does NOT wait for the result, on purpose. Success navigates away
        and failure stays put with an error, so what to wait for depends
        entirely on what the test is checking. Waiting here would force one
        of those two outcomes on every caller.
        """
        self.page.fill(UI.login.username_css, username)
        self.page.fill(UI.login.password_css, password)
        self.page.click(UI.login.submit_css)

    @property
    def error(self) -> Locator:
        """The error message area.

        Returns a Locator rather than its text, so the test can use
        Playwright's auto-retrying assertions on it. Returning text would
        read the DOM once, immediately, and race the message appearing.
        """
        return self.page.locator(UI.login.error_css)


class TasksPage:
    """The main page: the task list, the filters, and the account controls."""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url

    def open(self) -> TasksPage:
        """Navigate to the tasks page without assuming it will load.

        Separate from wait_until_loaded() because one test visits this page
        precisely to check it REDIRECTS when signed out. That test must not
        be forced to wait for content that should never appear.
        """
        self.page.goto(f"{self.base_url}/tasks.html")
        return self

    def wait_until_loaded(self) -> TasksPage:
        """Wait until the page is ready to interact with."""
        expect(self.page.locator(UI.tasks.create_form_css)).to_be_visible()
        return self

    # --- questions the test can ask ---------------------------------------

    @property
    def items(self) -> Locator:
        """Every task row currently shown. Use with to_have_count()."""
        return self.page.locator(UI.tasks.item_css)

    @property
    def titles(self) -> Locator:
        """Every visible task title, for asserting content and order."""
        return self.page.locator(UI.tasks.item_title_css)

    @property
    def empty_state(self) -> Locator:
        """The "no tasks yet" message."""
        return self.page.locator(UI.tasks.empty_state_css)

    @property
    def error(self) -> Locator:
        """Where a rejected action reports why."""
        return self.page.locator(UI.tasks.error_css)

    @property
    def current_user(self) -> Locator:
        """The signed-in username in the top bar."""
        return self.page.locator(UI.tasks.current_user_css)

    @property
    def bulk_delete_button(self) -> Locator:
        """The optional bulk-delete control.

        Present only for customers who have bought the feature - which is
        what one of the journey tests checks. Note the SERVER-side refusal
        is tested separately, because a hidden button guards nothing.
        """
        return self.page.locator(UI.tasks.bulk_delete_css)

    def row(self, title: str) -> Locator:
        """The row for a task with this exact title.

        Found by its text rather than by position. Index-based lookup
        (items.nth(0)) silently breaks the moment sort order changes or
        another test leaves a row behind - and it makes the test unreadable
        besides.
        """
        return self.items.filter(has=self.page.get_by_text(title, exact=True))

    def status_of(self, title: str) -> Locator:
        """The status badge belonging to one named task."""
        return self.row(title).locator(UI.tasks.item_status_css)

    # --- things the test can do -------------------------------------------

    def add_task(self, title: str, description: str = "") -> None:
        """Fill in the new-task form and submit it.

        The description is only typed when supplied, so tests that do not
        care about it are not silently exercising a different code path
        from the one they mean to.
        """
        self.page.fill(UI.tasks.new_title_css, title)
        if description:
            self.page.fill(UI.tasks.new_description_css, description)
        self.page.click(UI.tasks.create_submit_css)

    def advance(self, title: str) -> None:
        """Move a task to its next status: start it, or complete it.

        The button's meaning depends on where the task currently is, which
        is exactly how it appears to a user - so the method is named after
        the intent rather than the destination.
        """
        self.row(title).locator(UI.tasks.item_advance_css).click()

    def delete(self, title: str) -> None:
        """Delete one named task."""
        self.row(title).locator(UI.tasks.item_delete_css).click()

    def bulk_delete_done(self) -> None:
        """Press the bulk-delete control, for customers that have it."""
        self.bulk_delete_button.click()

    def search(self, text: str) -> None:
        """Type into the search box, which filters the list as you type."""
        self.page.fill(UI.tasks.filter_search_css, text)

    def filter_status(self, status: str) -> None:
        """Narrow the list to one status using the dropdown."""
        self.page.select_option(UI.tasks.filter_status_css, status)

    def log_out(self) -> None:
        """Sign out.

        Worth remembering what this triggers: the page calls the API to
        revoke the token server-side, then clears local storage and
        redirects. That is why going back afterwards does not restore
        access - the token is genuinely dead, not merely forgotten.
        """
        self.page.click(UI.tasks.logout_button_css)
