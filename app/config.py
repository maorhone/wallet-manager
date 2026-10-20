from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_database_url: str = ""
    app_timezone: str = "Asia/Jerusalem"
    dev_auth: bool = False
    seed_file: str = "seed/dev.yaml"
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
    # Onboarding portal (SPEC §9.2); empty user audiences disable the endpoints.
    adfs_jwks_url: str = ""
    adfs_issuer: str = ""
    onboarding_audience: str = ""
    onboarding_client_ids: list[str] = []
    onboarding_user_audiences: list[str] = []
    dev_jwt_secret: str = ""  # HS256 for local tests; refused unless DEV_AUTH=1
    litellm_public_url: str = "https://litellm.corp.example"
    outbox_poll_s: float = 1
    spend_snapshot_interval_s: float = 300
    overalloc_interval_s: float = 300
    scheduler_grants_interval_s: float = 60
    scheduler_notify_interval_s: float = 900
    notify_email_enabled: bool = False
    smtp_host: str = "smtp.corp.example"
    smtp_port: int = 25
    notify_email_from: str = "wallet-manager@corp.example"
    seed_grants: bool = False  # dev seed adds one active grant with a share (Step 2.4)
    reconcile_interval_min: float = 5  # used by Step 2.5
    scheduler_lock_timeout_s: float = 300
