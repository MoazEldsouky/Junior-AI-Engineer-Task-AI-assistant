"""
OpenRouter LLM Provider — OpenAI-compatible REST API via httpx.

OpenRouter provides access to many models through a single API.
Default model: google/gemini-2.0-flash-exp:free
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.base import BaseLLMProvider, LLMResponse, ToolCall


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider — multi-model gateway."""

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        self._client = httpx.AsyncClient(timeout=60.0)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ai-excel-assistant",
            "X-Title": "AI Excel Assistant",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 4096,
        }

        tool_schemas = self._build_tool_schemas(tools)
        if tool_schemas:
            payload["tools"] = tool_schemas
            payload["tool_choice"] = "auto"

        async def _do_request():
            response = await self._client.post(
                OPENROUTER_API_URL, headers=headers, json=payload
            )
            response.raise_for_status()
            return response.json()

        data = await self._retry_request(_do_request)
        return self._parse_openai_response(data)

    def _parse_openai_response(self, data: dict) -> LLMResponse:
        """Parse an OpenAI-compatible response."""
        choice = data["choices"][0]
        message = choice["message"]

        content = message.get("content")
        tool_calls = []

        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                func = tc["function"]
                args = func.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        name=func["name"],
                        arguments=args,
                    )
                )

        usage = {}
        if "usage" in data:
            u = data["usage"]
            usage = {
                "prompt_tokens": u.get("prompt_tokens", 0),
                "completion_tokens": u.get("completion_tokens", 0),
                "total_tokens": u.get("total_tokens", 0),
            }

        return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)
