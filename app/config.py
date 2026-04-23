"""
Centralized Configuration — single source of truth for every tunable
parameter in the system.

Uses pydantic-settings to load from environment / .env with type-safe
defaults.  Every module imports from here — no hardcoded magic numbers
anywhere else in the codebase.
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
    """
    Application settings — every tunable parameter lives here.

    Sections:
        • LLM API Keys
        • LLM Generation
        • Retry & Resilience
        • Agent / ReAct Loop
        • Session Management
        • Server & API
        • SSE Streaming
        • Logging
    """

    # ── LLM API Keys ──────────────────────────────────────────────────
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    github_token: str = ""

    # ── Active provider & model ───────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.GEMINI
    llm_model: str = ""  # Empty → use DEFAULT_MODELS

    # ── LLM Generation ────────────────────────────────────────────────
    llm_temperature: float = 0          # Deterministic output
    llm_max_tokens: int = 2048          # Max response tokens per LLM call
    llm_request_timeout: float = 60.0   # HTTP timeout in seconds

    # ── Retry & Resilience ────────────────────────────────────────────
    llm_max_retries: int = 3                           # Max retry attempts on 429/5xx
    llm_retry_backoff: list[int] = [3, 8, 15, 30, 60]  # Backoff seconds per attempt

    # ── Agent / ReAct Loop ────────────────────────────────────────────
    max_agent_iterations: int = 5      # Max Reason→Act→Observe cycles
    max_observation_rows: int = 30     # Rows sent to LLM per tool observation
    max_history_messages: int = 20     # Conversation messages sent to LLM

    # ── Session Management ────────────────────────────────────────────
    session_ttl_minutes: int = 60      # Auto-expire sessions after N minutes

    # ── Server & API ──────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]    # Allowed CORS origins

    # ── SSE Streaming ─────────────────────────────────────────────────
    sse_chunk_size: int = 20              # Characters per token event
    sse_token_delay: float = 0.008        # Seconds between token chunks
    sse_thinking_delay: float = 0.015     # Seconds between thinking events
    sse_event_delay: float = 0.01         # Seconds between control events

    # ── Logging ───────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    log_date_format: str = "%H:%M:%S"

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
