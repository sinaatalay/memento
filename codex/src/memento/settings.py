"""Local configuration. Credentials and personal data never belong in the repo."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Settings(BaseModel):
    data_dir: Path
    jev_api_key: str = Field(default="", repr=False)
    river_api_key: str = Field(default="", repr=False)
    river_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    email_recipient: str = ""
    timezone: str = "America/Los_Angeles"
    sync_seconds: float = 20.0
    gbrain_enabled: bool = False
    gbrain_checkout: Path = Path.home() / ".local/share/memento-gbrain-research/checkout"
    gbrain_home: Path = Path.home() / ".local/share/memento-gbrain-research/home"

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        workspace = Path(__file__).resolve().parents[3]
        path = Path(env_file) if env_file else workspace / ".context/memento/credentials.env"
        load_dotenv(path, override=False)
        data_dir = Path(os.getenv("MEMENTO_DATA_DIR", str(workspace / ".context/memento/runtime")))
        data_dir.mkdir(parents=True, exist_ok=True)
        data_dir.chmod(0o700)
        return cls(
            data_dir=data_dir,
            jev_api_key=os.getenv("JEV_API_KEY", ""),
            river_api_key=os.getenv("RIVER_API_KEY", ""),
            river_model=os.getenv("RIVER_MODEL", "Qwen/Qwen3.6-35B-A3B-FP8"),
            email_recipient=os.getenv("MEMENTO_EMAIL_TO", ""),
            timezone=os.getenv("MEMENTO_TIMEZONE", "America/Los_Angeles"),
            sync_seconds=float(os.getenv("MEMENTO_SYNC_SECONDS", "20")),
            gbrain_enabled=os.getenv("MEMENTO_GBRAIN_ENABLED", "0") == "1",
            gbrain_checkout=Path(os.getenv("MEMENTO_GBRAIN_CHECKOUT", str(cls.model_fields["gbrain_checkout"].default))),
            gbrain_home=Path(os.getenv("MEMENTO_GBRAIN_HOME", str(cls.model_fields["gbrain_home"].default))),
        )
