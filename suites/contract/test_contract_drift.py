"""Contract drift — does the implementation still match contracts/openapi.yaml?

contracts/openapi.yaml is hand-authored and authoritative. These tests fail
when the code and the contract disagree, which turns a breaking API change
into a red PR instead of a broken consumer.

This is the highest-leverage layer in the repo: it costs under a second and
replaces a whole category of integration tests whose only real job was
noticing that a field changed shape.
"""

import pytest
import yaml
from testkit.config import repo_root

pytestmark = [pytest.mark.contract]

# Paths the app exposes that are intentionally undeclared: framework-provided
# documentation endpoints and the static UI mount. Anything else appearing in
# one place but not the other is drift.
UNDECLARED_BY_DESIGN = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/app"}


@pytest.fixture(scope="session")
def contract() -> dict:
    path = repo_root() / "contracts" / "openapi.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def implemented(app_instance) -> dict:
    return app_instance.openapi()


def _operations(spec: dict) -> set[tuple[str, str]]:
    methods = {"get", "post", "put", "patch", "delete"}
    return {
        (path, method.upper())
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method in methods and path not in UNDECLARED_BY_DESIGN
    }


def test_no_undeclared_endpoints(contract, implemented):
    """An endpoint in the code but not the contract is an unreviewed API change."""
    extra = _operations(implemented) - _operations(contract)
    assert not extra, (
        "These operations exist in the app but are not declared in "
        f"contracts/openapi.yaml: {sorted(extra)}"
    )


def test_no_unimplemented_endpoints(contract, implemented):
    """An endpoint promised by the contract but absent from the code would
    break any consumer that generated a client from the spec."""
    missing = _operations(contract) - _operations(implemented)
    assert not missing, f"Declared in contracts/openapi.yaml but not implemented: {sorted(missing)}"


def test_error_response_schema_is_the_single_error_shape(contract):
    """Every documented 4xx must use ErrorResponse.

    Guards the property the UI relies on: that `code` is always present and
    clients never have to parse prose.
    """
    offenders = []
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for code, response in operation.get("responses", {}).items():
                if not code.startswith("4"):
                    continue
                schema = response.get("content", {}).get("application/json", {}).get("schema", {})
                if schema.get("$ref") != "#/components/schemas/ErrorResponse":
                    offenders.append(f"{method.upper()} {path} -> {code}")
    assert not offenders, f"4xx responses not using ErrorResponse: {offenders}"


def test_task_status_enum_matches_the_domain(contract):
    """The published enum and the code's enum must not drift apart."""
    from app.rules import TaskStatus

    declared = set(contract["components"]["schemas"]["TaskStatus"]["enum"])
    assert declared == {s.value for s in TaskStatus}


def test_declared_length_limits_match_the_domain_rules(contract):
    """A contract promising 120 characters while the code enforces 100 is a
    lie consumers will build against."""
    from app.rules import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH

    create = contract["components"]["schemas"]["TaskCreateRequest"]["properties"]
    assert create["title"]["maxLength"] == TITLE_MAX_LENGTH
    assert create["description"]["maxLength"] == DESCRIPTION_MAX_LENGTH
