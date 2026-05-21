from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from grimoire.export.config import ExportConfig
from grimoire.llm_gateway.settings import GatewaySettings


def _default_data_root() -> Path:
    # User-scoped, so multiple clones of the repo share one library / campaigns.
    # Override with the GRIMOIRE_DATA_ROOT env var when you want a per-clone dir.
    return Path.home() / ".grimoire"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GRIMOIRE_", env_file=".env", extra="ignore")

    # default_factory so Path.home() runs on Settings() construction, not
    # on module import — tests that monkey-patch Path.home() and then
    # re-instantiate Settings() pick up the override. Without this the
    # default was frozen at the moment grimoire.config first imported.
    data_root: Path = Field(default_factory=_default_data_root)
    host: str = "127.0.0.1"
    port: int = 8173

    database_path: Path | None = None
    db_pool_size: int = 5
    enable_wal: bool = True

    llm_gateway: GatewaySettings = GatewaySettings()
    export: ExportConfig = Field(default_factory=ExportConfig)

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path or self.data_root / "campaigns.sqlite"


settings = Settings()
