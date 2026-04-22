"""
Gemini LLM Provider — uses the modern google-genai SDK.

Maps our unified tool schema → Gemini function declarations and
normalizes responses back to our LLMResponse format.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from google import genai
from google.genai import types

from app.llm.base import BaseLLMProvider, LLMResponse, ToolCall


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider (free tier — gemini-2.0-flash)."""

    def __init__(self, api_key: str, model: str):
        super().__init__(api_key, model)
        self.client = genai.Client(api_key=api_key)

    # -----------------------------------------------------------------
    # Schema conversion
    # -----------------------------------------------------------------

    def _to_gemini_tools(self, tools: list[dict[str, Any]] | None) -> list[types.Tool] | None:
        """Convert our unified tool schemas to Gemini function declarations."""
        if not tools:
            return None

        declarations = []
        for t in tools:
            params = t.get("parameters", {})
            declarations.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=self._clean_schema_for_gemini(params),
                )
            )
        return [types.Tool(function_declarations=declarations)]

    def _clean_schema_for_gemini(self, schema: dict) -> dict:
        """
        Clean JSON Schema for Gemini compatibility.
        Gemini uses a subset of JSON Schema — strip unsupported keywords.
        """
        cleaned = {}

        if "type" in schema:
            cleaned["type"] = schema["type"].upper()

        if "description" in schema:
            cleaned["description"] = schema["description"]

        if "enum" in schema:
            cleaned["enum"] = schema["enum"]

        if "properties" in schema:
            cleaned["properties"] = {
                k: self._clean_schema_for_gemini(v)
                for k, v in schema["properties"].items()
            }

        if "required" in schema:
            cleaned["required"] = schema["required"]

        if "items" in schema:
            cleaned["items"] = self._clean_schema_for_gemini(schema["items"])

        return cleaned

    # -----------------------------------------------------------------
    # Message conversion
    # -----------------------------------------------------------------

    def _to_gemini_contents(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str | None, list[types.Content]]:
        """
        Convert OpenAI-style messages to Gemini format.
        Returns (system_instruction, contents_list).
        """
        system_instruction = None
        contents: list[types.Content] = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
                continue

            # Map roles
            gemini_role = "model" if role == "assistant" else "user"

            parts = []

            if role == "tool":
                # Tool response → function_response part
                # Resolve function name: OpenAI format uses tool_call_id, not name
                func_name = msg.get("name", "")
                if not func_name:
                    # Look up name from the preceding assistant's tool_calls
                    tool_call_id = msg.get("tool_call_id", "")
                    for prev in reversed(contents):
                        if prev.role == "model":
                            for p in prev.parts:
                                if p.function_call:
                                    func_name = p.function_call.name
                                    break
                            if func_name:
                                break
                    # Also check raw messages for OpenAI nested format
                    if not func_name and tool_call_id:
                        for prev_msg in reversed(messages[:messages.index(msg)]):
                            if prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls"):
                                for tc in prev_msg["tool_calls"]:
                                    tc_id = tc.get("id", "")
                                    if tc_id == tool_call_id:
                                        if "function" in tc:
                                            func_name = tc["function"]["name"]
                                        else:
                                            func_name = tc.get("name", "unknown")
                                        break
                                if func_name:
                                    break
                func_name = func_name or "unknown"
                # Parse content as JSON if possible for richer response
                try:
                    result_data = json.loads(content) if isinstance(content, str) else content
                except (json.JSONDecodeError, TypeError):
                    result_data = {"result": content}
                parts.append(
                    types.Part.from_function_response(
                        name=func_name,
                        response=result_data if isinstance(result_data, dict) else {"result": result_data},
                    )
                )
                gemini_role = "user"
            elif role == "assistant" and msg.get("tool_calls"):
                # Assistant requesting tool calls — handle both flat and OpenAI formats
                for tc in msg["tool_calls"]:
                    # OpenAI format: {id, type, function: {name, arguments}}
                    # Flat format:   {id, name, arguments}
                    if "function" in tc:
                        tc_name = tc["function"]["name"]
                        args = tc["function"].get("arguments", {})
                    else:
                        tc_name = tc["name"]
                        args = tc.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"raw": args}
                    parts.append(
                        types.Part.from_function_call(
                            name=tc_name,
                            args=args,
                        )
                    )
            else:
                if content:
                    parts.append(types.Part.from_text(text=content))

            if parts:
                contents.append(types.Content(role=gemini_role, parts=parts))

        return system_instruction, contents

    # -----------------------------------------------------------------
    # Main generation
    # -----------------------------------------------------------------

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send request to Gemini and return normalized response."""
        system_instruction, contents = self._to_gemini_contents(messages)
        gemini_tools = self._to_gemini_tools(tools)

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=gemini_tools,
            temperature=0,
        )

        # The google-genai client supports async natively
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=contents,
            config=config,
        )

        return self._parse_response(response)

    def _parse_response(self, response) -> LLMResponse:
        """Parse Gemini response into our normalized format."""
        tool_calls = []
        content = None

        try:
            candidate = response.candidates[0]
            for part in candidate.content.parts:
                if part.function_call:
                    fc = part.function_call
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{uuid.uuid4().hex[:8]}",
                            name=fc.name,
                            arguments=args,
                        )
                    )
                elif part.text:
                    content = (content or "") + part.text
        except (IndexError, AttributeError, TypeError):
            content = "I encountered an issue processing the response."

        usage = {}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            um = response.usage_metadata
            usage = {
                "prompt_tokens": getattr(um, "prompt_token_count", 0) or 0,
                "completion_tokens": getattr(um, "candidates_token_count", 0) or 0,
                "total_tokens": getattr(um, "total_token_count", 0) or 0,
            }

        return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)
