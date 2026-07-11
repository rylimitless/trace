from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://trace:trace@db:5432/trace"
    session_secret: str = "dev-secret-change-me"
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    telegram_bot_token: str = ""
    llm_justification_model: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
