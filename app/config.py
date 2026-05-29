from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    frontend_origin: str = "http://localhost:5173"

    # Redis
    redis_url: str = "redis://localhost:6379"
    cache_ttl_seconds: int = 3600

    # External APIs
    eia_api_key: str = ""
    fred_api_key: str = ""
    finnhub_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""

    # Scheduler
    scheduler_timezone: str = "UTC"

    # Lightstreamer
    ls_user: str = ""
    ls_token: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
