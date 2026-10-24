from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_database_url: str = ""
    app_timezone: str = "Asia/Jerusalem"
    # Client hook header carrying "project:<name>[,tool:<name>]"; LiteLLM stores it as request_tags.
    project_tag_header: str = "x-litellm-tags"
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
    outbox_backoff_max_s: float = 60  # cap for the worker's retry backoff after a LiteLLM outage
    spend_snapshot_interval_s: float = 300
    overalloc_interval_s: float = 300
    scheduler_grants_interval_s: float = 60
    scheduler_notify_interval_s: float = 900
    scheduler_report_alerts_interval_s: float = 86400
    notify_email_enabled: bool = False
    smtp_host: str = "smtp.corp.example"
    smtp_port: int = 25
    notify_email_from: str = "wallet-manager@corp.example"
    seed_grants: bool = False  # dev seed adds one active grant with a share (Step 2.4)
    reconcile_interval_min: float = 5  # used by Step 2.5
    reconcile_drift_alert_count: int = 3
    reconcile_drift_window_h: float = 24
    scheduler_lock_timeout_s: float = 300
    # Directory (Step 3.1): "fixture" stays the dev/test default; "ldap" is the production backend.
    directory_backend: Literal["fixture", "ldap"] = "fixture"
    ldap_url: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_base: str = ""
    ldap_timeout_s: float = 5
    ldap_cache_ttl_s: float = 300
