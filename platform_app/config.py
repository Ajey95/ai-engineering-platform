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
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""
    browser_session_secret: str = ""
    public_base_url: str = "http://localhost:8000"
    private_media_bucket: str = ""
    private_media_kms_key_id: str = ""
    cloudfront_key_pair_id: str = ""
    cloudfront_private_key_b64: str = ""
    cloudfront_distribution_id: str = ""
    artifact_dir: str = "./artifacts"
    memgraph_uri: str = ""
    memgraph_user: str = ""
    memgraph_password: str = ""
    dev_evaluation_dir: str = "./artifacts/evaluation-v2"
    otlp_traces_endpoint: str = ""
    pager_webhook_url: str = ""
    pager_webhook_secret: str = ""
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
            if (
                not self.oidc_issuer.startswith("https://")
                or not self.oidc_audience
                or not self.oidc_jwks_url.startswith("https://")
            ):
                raise ValueError("Hosted OIDC issuer, audience and HTTPS JWKS URL are required")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
