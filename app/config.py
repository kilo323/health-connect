from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    admin_user: str = "admin@domain.com"
    admin_password: str = "changeme"
    database_url: str = "sqlite+aiosqlite:///./data/health_tracker.db"
    fernet_key: str | None = None
    llm_url: str = ""
    llm_api_token: str = ""
    llm_model: str = ""

    # Google Health API webhook notifications (optional).
    # When webhook_secret is set, POST /api/webhooks/google-health accepts
    # push notifications instead of relying solely on scheduler polling.
    # The endpoint must be publicly reachable over HTTPS (TLS 1.2+).
    google_cloud_project_number: str = ""  # project NUMBER (not ID) for subscriber mgmt
    webhook_secret: str = ""               # expected Authorization header, e.g. "Bearer <random>"

    class Config:
        env_prefix = ""


settings = Settings()
