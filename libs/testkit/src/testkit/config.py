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
    base_url: str
    ui_url: str
    demo_username: str
    demo_password: str

    @classmethod
    def from_env(cls) -> "Settings":
        base = os.getenv("TASKFLOW_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        return cls(
            base_url=base,
            ui_url=f"{base}/app",
            demo_username=os.getenv("TASKFLOW_DEMO_USER", "demo"),
            demo_password=os.getenv("TASKFLOW_DEMO_PASSWORD", "demo1234"),
        )


settings = Settings.from_env()
