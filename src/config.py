"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MemoryModeName = Literal["on", "off"]


class Settings(BaseSettings):
    """Runtime settings.

    Unknown keys in the environment are ignored so that unrelated shell
    variables never break startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Hindsight (memory layer) ---
    hindsight_api_key: SecretStr = SecretStr("")
    hindsight_base_url: str = "https://api.hindsight.vectorize.io"
    hindsight_bank_id: str = "dejaops-prod"
    hindsight_timeout_seconds: float = 30.0
    hindsight_max_retries: int = 3
    hindsight_backoff_base_seconds: float = 0.5
    # Recall results below this final score are treated as "no relevant
    # memory found". Calibrated on the seeded NimbusPay bank: relevant top
    # scores were 0.353-1.087, irrelevant top scores 0.000-0.089.
    hindsight_min_final_score: float = 0.2

    # --- Memory mode (on/off comparison) ---
    memory_mode: MemoryModeName = "on"

    # --- Groq (LLM) ---
    groq_api_key: SecretStr = SecretStr("")
    groq_model_primary: str = "openai/gpt-oss-120b"
    groq_model_fallback: str = "qwen/qwen3.8-27b"
    groq_timeout_seconds: float = 60.0

    # --- Local storage / API ---
    sqlite_path: str = "data/store/dejaops.db"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def memory_enabled(self) -> bool:
        return self.memory_mode == "on"


@lru_cache
def get_settings() -> Settings:
    return Settings()
