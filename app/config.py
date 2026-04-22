"""
Application configuration — loads settings from .env file.

Uses pydantic-settings for type-safe environment variable loading with
sensible defaults. At least one LLM provider API key must be configured.
"""

import os
from pathlib import Path
from enum import Enum

from pydantic_settings import BaseSettings
from pydantic import model_validator


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Project root is the parent of the `app/` package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
WRITE_LOG_PATH = DATA_DIR / "write_log.json"


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    GITHUB_MODELS = "github_models"


# Default model per provider — chosen for best free-tier performance
DEFAULT_MODELS: dict[str, str] = {
    LLMProvider.GEMINI: "gemini-2.5-flash",
    LLMProvider.GROQ: "llama-3.3-70b-versatile",
    LLMProvider.OPENROUTER: "google/gemma-4-31b-it:free",
    LLMProvider.GITHUB_MODELS: "gpt-4o",
}


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # --- LLM API Keys ---
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    github_token: str = ""

    # --- Active provider & model ---
    llm_provider: LLMProvider = LLMProvider.GEMINI
    llm_model: str = ""  # Empty → use DEFAULT_MODELS

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Agent ---
    max_agent_iterations: int = 10
    session_ttl_minutes: int = 60
    max_history_messages: int = 20

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def check_api_key_configured(self) -> "Settings":
        """Ensure at least one LLM provider has an API key."""
        keys = {
            LLMProvider.GEMINI: self.gemini_api_key,
            LLMProvider.GROQ: self.groq_api_key,
            LLMProvider.OPENROUTER: self.openrouter_api_key,
            LLMProvider.GITHUB_MODELS: self.github_token,
        }
        if not any(keys.values()):
            raise ValueError(
                "No LLM API key configured. Set at least one of: "
                "GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, GITHUB_TOKEN "
                "in your .env file."
            )
        # Validate that the active provider has a key
        active_key = keys.get(self.llm_provider, "")
        if not active_key:
            # Auto-switch to a provider that has a key
            for provider, key in keys.items():
                if key:
                    self.llm_provider = provider
                    break
        return self

    @property
    def active_model(self) -> str:
        """Return the configured model or the default for the active provider."""
        return self.llm_model or DEFAULT_MODELS[self.llm_provider]

    @property
    def active_api_key(self) -> str:
        """Return the API key for the active provider."""
        key_map = {
            LLMProvider.GEMINI: self.gemini_api_key,
            LLMProvider.GROQ: self.groq_api_key,
            LLMProvider.OPENROUTER: self.openrouter_api_key,
            LLMProvider.GITHUB_MODELS: self.github_token,
        }
        return key_map[self.llm_provider]


# ---------------------------------------------------------------------------
# Singleton — import `settings` directly
# ---------------------------------------------------------------------------
settings = Settings()


# Ensure runtime directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
