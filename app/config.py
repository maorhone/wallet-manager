from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_database_url: str = ""
    app_timezone: str = "Asia/Jerusalem"
    dev_auth: bool = False
    session_secret: str = "change-me"
    cookie_secure: bool = False
    oidc_discovery_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_upn_claim: str = "upn"
    oidc_name_claim: str = "name"
    oidc_groups_claim: str = "groups"
    jwt_issuer: str = ""
    outbox_poll_s: float = 1
    spend_snapshot_interval_s: float = 300
    overalloc_interval_s: float = 300
