"""
ListChangesTool — shows recent mutation history from the write-log.

Helps users review past changes and find action IDs for undo operations.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager


class ListChangesTool(BaseTool):
    name = "list_changes"
    description = (
        "List recent data mutations (inserts, updates, deletes) from the change log. "
        "Shows action IDs, timestamps, operations, and affected rows. "
        "Use this to review history or find an action_id for the undo tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Filter by dataset (optional). Omit to show all changes.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of entries to return. Default: 10.",
            },
        },
    }

    def __init__(self, data_manager: DataManager):
        self.dm = data_manager

    def execute(self, **kwargs) -> ToolResult:
        try:
            dataset = kwargs.get("dataset")
            limit = kwargs.get("limit", 10)

            history = self.dm.get_change_history(dataset=dataset, limit=limit)

            if not history:
                return ToolResult(
                    success=True,
                    data=[],
                    message="No mutations recorded yet.",
                )

            # Format for display
            lines = [f"📋 Recent changes ({len(history)} entries):\n"]
            for entry in history:
                status = "✅" if not entry.get("undone") else "↩️ (undone)"
                lines.append(
                    f"  {status} [{entry['action_id']}] {entry['operation'].upper()} "
                    f"on {entry['dataset']} — {entry['timestamp']}"
                )
                if entry["operation"] == "update":
                    count = len(entry.get("affected_rows", []))
                    lines.append(f"      {count} row(s) affected")
                elif entry["operation"] == "insert":
                    count = len(entry.get("affected_rows", []))
                    lines.append(f"      {count} row(s) inserted")
                elif entry["operation"] == "delete":
                    count = len(entry.get("affected_rows", []))
                    lines.append(f"      {count} row(s) deleted")

            return ToolResult(
                success=True,
                data=history,
                message="\n".join(lines),
            )

        except Exception as e:
            return ToolResult(success=False, message=f"Error listing changes: {e}")
