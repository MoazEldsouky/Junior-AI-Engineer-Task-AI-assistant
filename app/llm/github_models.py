"""
GitHub Models LLM Provider — OpenAI-compatible endpoint on Azure.

Uses the free GPT-4o access via GitHub token.
Base URL: https://models.inference.ai.azure.com

All request/response logic is inherited from OpenAICompatibleProvider.
"""

from __future__ import annotations

from app.llm.base import OpenAICompatibleProvider


class GitHubModelsProvider(OpenAICompatibleProvider):
    """GitHub Models provider — GPT-4o via Azure free inference."""

    _api_url = "https://models.inference.ai.azure.com/chat/completions"
