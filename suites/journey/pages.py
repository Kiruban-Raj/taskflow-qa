"""Page objects.

Every selector comes from testkit.selectors, which is generated from
contracts/ui-contract.yaml — no literal CSS in this file. Renaming a
data-testid becomes an import-time AttributeError naming the attribute,
instead of a thirty-second timeout in a browser.

Page objects expose *intent* ("sign in", "add task"), never mechanics. A
test that reads like a sequence of clicks has the abstraction in the wrong
place.
"""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect
from testkit.selectors import UI


class LoginPage:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url

    def open(self) -> LoginPage:
        self.page.goto(f"{self.base_url}/index.html")
        expect(self.page.locator(UI.login.form_css)).to_be_visible()
        return self

    def sign_in(self, username: str, password: str) -> None:
        self.page.fill(UI.login.username_css, username)
        self.page.fill(UI.login.password_css, password)
        self.page.click(UI.login.submit_css)

    @property
    def error(self) -> Locator:
        return self.page.locator(UI.login.error_css)


class TasksPage:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url

    def open(self) -> TasksPage:
        self.page.goto(f"{self.base_url}/tasks.html")
        return self

    def wait_until_loaded(self) -> TasksPage:
        expect(self.page.locator(UI.tasks.create_form_css)).to_be_visible()
        return self

    # --- queries -----------------------------------------------------------

    @property
    def items(self) -> Locator:
        return self.page.locator(UI.tasks.item_css)

    @property
    def titles(self) -> Locator:
        return self.page.locator(UI.tasks.item_title_css)

    @property
    def empty_state(self) -> Locator:
        return self.page.locator(UI.tasks.empty_state_css)

    @property
    def error(self) -> Locator:
        return self.page.locator(UI.tasks.error_css)

    @property
    def current_user(self) -> Locator:
        return self.page.locator(UI.tasks.current_user_css)

    def row(self, title: str) -> Locator:
        """The row for a given title. Filtering by child text rather than by
        index keeps tests independent of ordering."""
        return self.items.filter(has=self.page.get_by_text(title, exact=True))

    def status_of(self, title: str) -> Locator:
        return self.row(title).locator(UI.tasks.item_status_css)

    # --- actions -----------------------------------------------------------

    def add_task(self, title: str, description: str = "") -> None:
        self.page.fill(UI.tasks.new_title_css, title)
        if description:
            self.page.fill(UI.tasks.new_description_css, description)
        self.page.click(UI.tasks.create_submit_css)

    def advance(self, title: str) -> None:
        self.row(title).locator(UI.tasks.item_advance_css).click()

    def delete(self, title: str) -> None:
        self.row(title).locator(UI.tasks.item_delete_css).click()

    def search(self, text: str) -> None:
        self.page.fill(UI.tasks.filter_search_css, text)

    def filter_status(self, status: str) -> None:
        self.page.select_option(UI.tasks.filter_status_css, status)

    def log_out(self) -> None:
        self.page.click(UI.tasks.logout_button_css)
