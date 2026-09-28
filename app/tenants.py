"""
Tenant registry - who is asking, and what are they allowed to do?

WHAT THIS MODULE NEEDS TO ACHIEVE
    One deployment of this application serves several customers. Customer A
    arrives on one hostname, Customer B on another, and they must never be
    able to see each other's data. This module is the single place that
    answers three questions:

        1. Which customer does this request belong to?
        2. What is that customer configured to look like?
        3. Which optional features have they paid for?

HOW IT ACHIEVES IT
    Each customer is described by one YAML file in tenants/. On startup we
    load them all, validate them, and index them by hostname. Every incoming
    request is then mapped to a tenant by looking at the Host header.

    The test suite loads the very same files. That is deliberate: if the
    tests read their expectations from the same source the application obeys,
    the two can never quietly disagree.

WHY VALIDATION HAPPENS AT STARTUP
    A broken tenant file should stop the service from booting, loudly. The
    alternative is far worse - the service starts, and some time later makes
    a wrong authorisation decision for a real customer because a config key
    was misspelled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# tenants/ sits at the repository root, one level above app/.
# TENANTS_DIR lets tests point at a fixture directory instead of the real one.
DEFAULT_TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"


class TenantConfigError(ValueError):
    """Raised when a tenant file is missing keys or contains something unusable.

    Its own exception type rather than a bare ValueError so that a
    misconfiguration is obviously distinguishable from a runtime bug when it
    shows up in a stack trace at boot.
    """


@dataclass(frozen=True)
class Tenant:
    """One customer's complete configuration.

    Frozen (immutable) on purpose. Tenant config is read constantly and
    written never; making it immutable removes any chance that one request
    handler mutates a shared object and changes behaviour for everyone else.
    """

    id: str
    display_name: str
    hostname: str
    product_name: str
    accent_colour: str
    features: frozenset[str] = field(default_factory=frozenset)

    def has_feature(self, name: str) -> bool:
        """Is this optional feature switched on for this customer?

        Used by the endpoints that guard paid features. Kept as a method
        rather than callers poking at `.features` directly, so that if the
        rule ever grows (trial periods, expiry dates) there is one place to
        change.
        """
        return name in self.features


def _require(data: dict, key: str, source: Path):
    """Fetch a required key, or explain exactly which file is wrong.

    The error message names the file, because "KeyError: 'hostname'" at boot
    tells you nothing when there are twenty tenant files.
    """
    if key not in data:
        raise TenantConfigError(f"{source.name}: missing required key {key!r}")
    return data[key]


def _parse(raw: dict, source: Path) -> Tenant:
    """Turn one YAML document into a validated Tenant.

    Every field is checked here rather than trusted, because this is the
    boundary between "text somebody wrote" and "decisions the application
    will make about access control".
    """
    branding = raw.get("branding") or {}

    tenant = Tenant(
        id=str(_require(raw, "id", source)),
        display_name=str(_require(raw, "display_name", source)),
        hostname=str(_require(raw, "hostname", source)).lower(),
        product_name=str(branding.get("product_name", raw.get("display_name", ""))),
        accent_colour=str(branding.get("accent_colour", "#2a6df4")),
        features=frozenset(str(f) for f in (raw.get("features") or [])),
    )

    # The filename is the tenant's identity everywhere else - it is what the
    # CI matrix iterates over and what --tenant expects. If the filename and
    # the id inside disagree, something will silently target the wrong
    # customer, so refuse to start instead.
    if tenant.id != source.stem:
        raise TenantConfigError(
            f"{source.name}: id {tenant.id!r} does not match the filename. "
            "The filename is the tenant's identity in CI, so they must agree."
        )
    return tenant


# Loaded once and cached. Tenant config does not change while the process is
# running, and re-reading the disk on every request would be wasteful.
_by_id: dict[str, Tenant] | None = None
_by_hostname: dict[str, Tenant] | None = None


def tenants_dir() -> Path:
    """Where the tenant files live.

    Overridable through TENANTS_DIR so a test can point at a fixture
    directory - for example to check that a deliberately broken tenant file
    is rejected, without putting a broken file in the real directory.
    """
    return Path(os.getenv("TENANTS_DIR", DEFAULT_TENANTS_DIR))


def load_tenants(force: bool = False) -> dict[str, Tenant]:
    """Read and validate every tenant file, keyed by id.

    `force=True` bypasses the cache, which the tests use when they point
    TENANTS_DIR somewhere else mid-session.
    """
    global _by_id, _by_hostname
    if _by_id is not None and not force:
        return _by_id

    directory = tenants_dir()
    by_id: dict[str, Tenant] = {}
    by_hostname: dict[str, Tenant] = {}

    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            tenant = _parse(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path)

            # Two customers sharing a hostname would make routing ambiguous,
            # and "ambiguous" in a multi-tenant system means one customer
            # eventually sees another's data.
            if tenant.hostname in by_hostname:
                raise TenantConfigError(
                    f"{path.name}: hostname {tenant.hostname!r} is already used by "
                    f"{by_hostname[tenant.hostname].id!r}. Hostnames must be unique."
                )
            by_id[tenant.id] = tenant
            by_hostname[tenant.hostname] = tenant

    _by_id, _by_hostname = by_id, by_hostname
    return by_id


def get_tenant(tenant_id: str) -> Tenant | None:
    """Look a tenant up by id. Returns None if there is no such customer."""
    return load_tenants().get(tenant_id)


def tenant_ids() -> list[str]:
    """Every configured tenant id, sorted. This is what the CI matrix expands."""
    return sorted(load_tenants())


def resolve_tenant(host_header: str | None) -> Tenant | None:
    """Work out which customer a request belongs to, from the Host header only.

    THIS FUNCTION IS THE SECURITY BOUNDARY OF THE WHOLE MULTI-TENANT DESIGN,
    so it is worth being precise about why it looks like this.

    The tenant is derived from the Host header and nothing else. It is
    deliberately NOT read from a query parameter, a request body field, or a
    convenient custom header like X-Tenant-Id. Any of those would be chosen
    by the caller, which would mean Customer A could simply ask to be treated
    as Customer B - and every data-isolation guarantee in the system would
    become decorative.

    The Host header is different in kind: in a real deployment it is fixed by
    DNS and the reverse proxy in front of us, not by whoever is making the
    request. There is a test in the suite that tries the X-Tenant-Id attack
    explicitly and asserts it does nothing.

    The port is stripped before matching, because a Host header legitimately
    carries one (custa.taskflow.local:8000) while our config stores the
    hostname alone.
    """
    if not host_header:
        return None

    hostname = host_header.split(":")[0].strip().lower()
    load_tenants()
    assert _by_hostname is not None  # populated by load_tenants
    return _by_hostname.get(hostname)
