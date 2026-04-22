"""
LLM Provider — Abstract base class and shared types.

All providers normalize to a common interface so the agent logic
never needs to know which LLM is behind the scenes.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Shared response types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool call requested by the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """
    Abstract LLM provider.

    Every provider must:
    1. Accept messages in OpenAI-style format: [{"role": ..., "content": ...}]
    2. Accept optional tool schemas (JSON-Schema based)
    3. Return a normalized LLMResponse
    """

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: Conversation history in OpenAI format.
            tools: Optional list of tool schemas for function calling.

        Returns:
            Normalized LLMResponse with content and/or tool calls.
        """
        ...

    def _build_tool_schemas(self, tools: list[dict[str, Any]] | None) -> list[dict] | None:
        """Convert our unified tool format to OpenAI-style function schemas."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    @staticmethod
    async def _retry_request(coro_factory, max_retries: int = 5):
        """
        Retry an async HTTP request with exponential backoff.

        Handles 429 (rate limit) and 5xx errors automatically.
        Respects the ``retry-after`` header when present.
        """
        import asyncio
        import httpx

        backoff_schedule = [3, 8, 15, 30, 60]  # seconds per retry attempt

        for attempt in range(max_retries):
            try:
                return await coro_factory()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    # Respect retry-after header if provided
                    retry_after = e.response.headers.get("retry-after")
                    if retry_after:
                        try:
                            wait = min(float(retry_after), 60)
                        except ValueError:
                            wait = backoff_schedule[attempt]
                    else:
                        wait = backoff_schedule[attempt]
                    import logging
                    logging.getLogger("llm").warning(
                        f"Rate limited (429). Retrying in {wait}s "
                        f"(attempt {attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff_schedule[attempt])
                    continue
                raise

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider(provider_name: str, api_key: str, model: str) -> BaseLLMProvider:
    """
    Factory function — returns the correct provider instance.

    Import is deferred to avoid loading unused SDKs.
    """
    from app.llm.gemini import GeminiProvider
    from app.llm.groq import GroqProvider
    from app.llm.openrouter import OpenRouterProvider
    from app.llm.github_models import GitHubModelsProvider

    providers = {
        "gemini": GeminiProvider,
        "groq": GroqProvider,
        "openrouter": OpenRouterProvider,
        "github_models": GitHubModelsProvider,
    }

    cls = providers.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            f"Available: {list(providers.keys())}"
        )
    return cls(api_key=api_key, model=model)
