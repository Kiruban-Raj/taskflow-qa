"""Environment configuration, read once and shared."""

import os
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    """Walk up until the directory holding contracts/ — works no matter
    which suite directory pytest was invoked from."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "contracts").is_dir():
            return candidate
    raise RuntimeError("Could not locate the repository root (no contracts/ directory found)")


@dataclass(frozen=True)
class Settings:
    """Everything the test run needs to know about its environment.

    Read from environment variables once, then passed around as a value.
    The alternative - calling os.getenv() wherever it is needed - makes it
    impossible to see at a glance what the suite can be configured with.
    """

    base_url: str
    ui_url: str
    demo_username: str
    demo_password: str

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the environment, with workable defaults.

        Defaults matter: `pytest` with no setup at all should still run.
        Anyone needing something different sets TASKFLOW_BASE_URL, and the
        same suite then points at a deployed environment unchanged.
        """
        base = os.getenv("TASKFLOW_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        return cls(
            base_url=base,
            ui_url=f"{base}/app",
            demo_username=os.getenv("TASKFLOW_DEMO_USER", "demo"),
            demo_password=os.getenv("TASKFLOW_DEMO_PASSWORD", "demo1234"),
        )


settings = Settings.from_env()
