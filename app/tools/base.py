"""
Tool base classes — BaseTool and ToolRegistry.

Every tool extends BaseTool with a name, description, JSON-Schema parameters,
and an execute method. The ToolRegistry collects tools and generates schemas
for the LLM's function-calling interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result returned by a tool execution."""
    success: bool
    data: Any = None
    message: str = ""
    requires_confirmation: bool = False
    preview: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "message": self.message,
            "requires_confirmation": self.requires_confirmation,
            "preview": self.preview,
        }


class BaseTool(ABC):
    """Abstract base class for all tools."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    def get_schema(self) -> dict[str, Any]:
        """Return the tool schema for the LLM."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """Registry of available tools — used by the agent to discover and invoke tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool by its name."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def get_all(self) -> dict[str, BaseTool]:
        """Return all registered tools."""
        return dict(self._tools)

    def get_schemas(self) -> list[dict[str, Any]]:
        """Generate tool schemas for the LLM function-calling interface."""
        return [tool.get_schema() for tool in self._tools.values()]

    def list_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())
