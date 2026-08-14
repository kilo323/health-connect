from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    admin_user: str = "admin@domain.com"
    admin_password: str = "changeme"
    database_url: str = "sqlite+aiosqlite:///./data/health_tracker.db"
    fernet_key: str | None = None
    llm_url: str = ""
    llm_api_token: str = ""
    llm_model: str = ""
    # Set to false to skip SSL certificate verification on LLM API calls
    # (e.g. behind a corporate TLS-inspecting proxy). Applies to both the
    # document-analysis LLM and the metric-library refresh LLM.
    llm_ssl_verify: bool = True

    # Metric library seeding. When true (default), library entries marked
    # "protected": true (e.g. the hand-tuned Google Health definitions) are not
    # overwritten when the library is applied or regenerated. Set the
    # METRIC_LIBRARY_PROTECT env var to "false"/"0" to allow overwriting them.
    metric_library_protect: bool = True

    # Google OAuth client credentials (app-level). When set, and the
    # google_oauth_config AppSettings row is missing or empty, these are used to
    # seed that config at startup. Set via GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.
    google_client_id: str = ""
    google_client_secret: str = ""

    # Google Health API webhook notifications (optional).
    # When webhook_secret is set, POST /api/webhooks/google-health accepts
    # push notifications instead of relying solely on scheduler polling.
    # The endpoint must be publicly reachable over HTTPS (TLS 1.2+).
    google_cloud_project_number: str = ""  # project NUMBER (not ID) for subscriber mgmt
    webhook_secret: str = ""               # expected Authorization header, e.g. "Bearer <random>"

    class Config:
        env_prefix = ""


settings = Settings()
