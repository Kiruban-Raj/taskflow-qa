"""Shared test substrate for TaskFlow.

One installable package holding the things every suite needs: how to talk to
the app (transports), how to make data (factories), what the UI is allowed to
depend on (selectors), and the pytest fixtures that wire them together.

The rule this package exists to enforce: a helper is written once, here, and
imported. The moment a factory or a selector is copy-pasted into a suite, the
copies drift and the suites start disagreeing about what the system does.
"""

from .config import Settings, settings
from .factories import TaskFactory, UserFactory
from .transport import HttpTransport, InProcessTransport, Response, Transport

__all__ = [
    "HttpTransport",
    "InProcessTransport",
    "Response",
    "Settings",
    "TaskFactory",
    "Transport",
    "UserFactory",
    "settings",
]
