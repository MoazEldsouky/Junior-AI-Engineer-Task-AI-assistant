"""
OpenRouter LLM Provider — OpenAI-compatible REST API.

OpenRouter provides access to many models through a single API.
Default model: google/gemini-2.0-flash-exp:free

All request/response logic is inherited from OpenAICompatibleProvider.
OpenRouter requires two extra identification headers per their policy.
"""

from __future__ import annotations

from app.llm.base import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter provider — multi-model gateway."""

    _api_url = "https://openrouter.ai/api/v1/chat/completions"
    _extra_headers = {
        "HTTP-Referer": "https://github.com/ai-excel-assistant",
        "X-Title": "AI Excel Assistant",
    }
