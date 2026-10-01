from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIP_", env_file=".env", extra="forbid")

    database_url: str = "sqlite:///./aiplatform.db"
    environment: str = "development"
    dev_actor: str = "local-developer"
    dev_tenant: str = "local-tenant"
    dev_token: str = ""
    public_base_url: str = "http://localhost:8000"
    artifact_dir: str = "./artifacts"
    dev_evaluation_dir: str = "./artifacts/evaluation-v2"
    max_run_spend_usd: float = Field(default=5.0, gt=0)
    max_model_calls: int = Field(default=40, ge=1)
    max_tool_calls: int = Field(default=80, ge=1)
    max_patch_attempts: int = Field(default=3, ge=1)
    active_timeout_seconds: int = Field(default=1800, ge=30)
    lease_seconds: int = Field(default=60, ge=10)

    @model_validator(mode="after")
    def require_production_database_and_auth(self) -> "Settings":
        if self.environment != "development":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("PostgreSQL is required outside development")
            raise ValueError(
                "Hosted identity and project roles are not implemented; refusing startup"
            )
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
