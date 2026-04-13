from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "production"
    host: str = "0.0.0.0"
    port: int = 8000
    output_dir: Path = Field(default=Path("/app/output"))
    temp_dir: Path = Field(default=Path("/app/tmp"))
    auto_deploy: bool = False
    allow_url_fetch: bool = True
    caddy_target_file: Path = Field(default=Path("/deploy-target/Caddyfile"))
    caddy_container_name: str = "caddy"
    caddy_container_config_path: str = "/etc/caddy/Caddyfile"
    caddy_validate_and_reload: bool = True
    docker_socket_enabled: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
