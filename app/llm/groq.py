"""
Groq LLM Provider — OpenAI-compatible REST API.

Groq offers extremely fast inference on open-source models.
Default model: llama-3.3-70b-versatile (free tier, supports function calling).

All request/response logic is inherited from OpenAICompatibleProvider.
"""

from __future__ import annotations

from app.llm.base import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    """Groq provider — fast open-source model inference."""

    _api_url = "https://api.groq.com/openai/v1/chat/completions"
