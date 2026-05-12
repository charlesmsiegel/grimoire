from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_root() -> Path:
    # backend/src/grimoire/config.py → repo_root / data
    return Path(__file__).resolve().parents[3] / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRIMOIRE_", env_file=".env", extra="ignore")

    data_root: Path = _default_data_root()
    host: str = "127.0.0.1"
    port: int = 8000


settings = Settings()
