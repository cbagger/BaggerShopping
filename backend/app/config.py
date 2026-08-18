from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    samsung_list_id: str | None = None

    # Preferred long-term auth path
    samsung_account_email: str | None = None
    samsung_account_password: str | None = None

    # Optional one-time/manual fallback while automatic login is being validated
    samsung_food_token: str | None = None

    samsung_food_api_base: str = "https://api.whisk.com"
    samsung_food_web_base: str = "https://app.samsungfood.com"
    samsung_food_app_version: str = "2.87.0"

    request_timeout_seconds: float = 20.0
    auth_headless: bool = True
    auth_timeout_seconds: float = 90.0
    auth_state_path: str = "/data/auth_state.json"
    browser_user_data_dir: str = "/data/chromium-profile"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def auth_state_file(self) -> Path:
        return Path(self.auth_state_path)


settings = Settings()
