from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    admin_user: str = "admin@domain.com"
    admin_password: str = "changeme"
    database_url: str = "sqlite+aiosqlite:///./data/health_tracker.db"
    fernet_key: str | None = None

    class Config:
        env_prefix = ""


settings = Settings()
