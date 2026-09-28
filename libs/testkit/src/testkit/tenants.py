"""
Tenant definitions, as the TESTS see them.

WHAT THIS MODULE NEEDS TO ACHIEVE
    Give the test suite a description of each customer - their hostname,
    their branding, their paid features - so tests can assert the right
    things for whichever customer they are running against.

HOW IT ACHIEVES IT, AND WHY THAT MATTERS
    It reads the exact same tenants/*.yaml files the application reads.

    That is the whole design. The obvious alternative is to hardcode the
    expected values in the tests ("Customer A should see 'Acme Tasks'").
    Those copies then drift: somebody edits the tenant file, the app changes
    behaviour, and the test still asserts the old value - so it either fails
    for the wrong reason or, far worse, passes while checking nothing real.

    By reading one source, a test cannot assert something the application
    was never configured to do.

WHY THIS IS SEPARATE FROM app/tenants.py
    The application's copy is about making access decisions and is loaded
    into the running service. This one is about what to expect, and is
    deliberately dependency-free so it can be imported by a test that has
    not started the application at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import repo_root


@dataclass(frozen=True)
class TenantConfig:
    """One customer, as a test needs to know them."""

    id: str
    display_name: str
    hostname: str
    product_name: str
    accent_colour: str
    features: frozenset[str]

    def base_url(self, port: int | None = None) -> str:
        """The URL a test should call for this customer.

        The port is supplied separately because it is decided at runtime -
        the journey suite starts a server on whatever port is free. The
        hostname comes from config; the port comes from the environment.
        """
        host = self.hostname if port is None else f"{self.hostname}:{port}"
        return f"http://{host}"

    def ui_url(self, port: int | None = None) -> str:
        """Where the browser should navigate for this customer."""
        return f"{self.base_url(port)}/app"

    def has_feature(self, name: str) -> bool:
        """Has this customer bought the named optional feature?

        Tests use it to assert BOTH directions - that an enabled feature
        works and a disabled one is refused. Checking only one direction
        would let a permanently broken endpoint pass.
        """
        return name in self.features


def tenants_dir() -> Path:
    """Where the tenant files live. Overridable so tests can use fixtures."""
    return Path(os.getenv("TENANTS_DIR", repo_root() / "tenants"))


def load_tenants() -> dict[str, TenantConfig]:
    """Read every tenant file into a dict keyed by id.

    Not cached, unlike the application's loader. A test run is short, the
    files are tiny, and a caching bug here would be far more confusing than
    the microseconds it saves.
    """
    result: dict[str, TenantConfig] = {}
    directory = tenants_dir()
    if not directory.is_dir():
        return result

    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        branding = raw.get("branding") or {}
        result[raw["id"]] = TenantConfig(
            id=raw["id"],
            display_name=raw["display_name"],
            hostname=str(raw["hostname"]).lower(),
            product_name=branding.get("product_name", raw["display_name"]),
            accent_colour=branding.get("accent_colour", "#2a6df4"),
            features=frozenset(raw.get("features") or []),
        )
    return result


def tenant_ids() -> list[str]:
    """Every tenant id, sorted.

    This is what the CI matrix expands over and what parameterises the
    per-tenant tests. Because it is derived from the directory listing,
    adding tenants/custc.yaml adds CI coverage with no code change at all.
    """
    return sorted(load_tenants())


def get_tenant(tenant_id: str) -> TenantConfig:
    """Fetch one tenant, failing loudly and helpfully if the id is wrong.

    A plain KeyError here would be reported as an obscure fixture error, so
    the message lists what is actually available instead.
    """
    everyone = load_tenants()
    if tenant_id not in everyone:
        available = ", ".join(sorted(everyone)) or "none found"
        raise KeyError(f"Unknown tenant {tenant_id!r}. Available: {available}")
    return everyone[tenant_id]
