"""
UndoTool — reverts a previous mutation.

Shows a preview of the revert action (current vs. restored state)
and requires confirmation before proceeding.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager


class UndoTool(BaseTool):
    name = "undo_change"
    description = (
        "Undo a previous data mutation (insert, update, or delete). "
        "Can undo the most recent change (latest=true) or a specific change "
        "by its action_id. Shows a preview of the revert action and requires "
        "user confirmation before proceeding."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action_id": {
                "type": "string",
                "description": "The ID of a specific action to undo (e.g. 'act_abc12345'). Use 'list_changes' tool to find action IDs.",
            },
            "latest": {
                "type": "boolean",
                "description": "If true, undo the most recent (non-undone) mutation. Default: false.",
            },
        },
    }

    def __init__(self, data_manager: DataManager):
        self.dm = data_manager

    def execute(self, **kwargs) -> ToolResult:
        try:
            action_id = kwargs.get("action_id")
            latest = kwargs.get("latest", False)

            if not action_id and not latest:
                return ToolResult(
                    success=False,
                    message="Please specify either an action_id or set latest=true to undo the most recent change.",
                )

            # Get preview
            preview_data = self.dm.get_undo_preview(
                action_id=action_id, latest=latest
            )

            if "error" in preview_data:
                return ToolResult(success=False, message=preview_data["error"])

            # Build human-readable preview
            op = preview_data["operation"]
            aid = preview_data["action_id"]
            ds = preview_data["dataset"]
            affected = preview_data["affected_rows"]

            preview_lines = [
                f"⏪ Undo {op} (action: {aid}) on {ds}:\n"
            ]

            if op == "update":
                preview_lines.append("Reverting values:")
                for row in affected[:10]:
                    preview_lines.append(f"\n  Record '{row['row_id']}':")
                    for col, change in row["changes"].items():
                        preview_lines.append(
                            f"    {col}: {change['after']} → {change['before']} (restoring original)"
                        )
            elif op == "insert":
                preview_lines.append(
                    f"Will remove {len(affected)} inserted row(s)."
                )
            elif op == "delete":
                preview_lines.append(
                    f"Will restore {len(affected)} deleted row(s)."
                )

            preview_lines.append("\nProceed with undo? (yes/no)")
            preview = "\n".join(preview_lines)

            return ToolResult(
                success=True,
                data={
                    "operation": "undo",
                    "target_action_id": aid,
                    "target_operation": op,
                    "dataset": ds,
                },
                message=preview,
                requires_confirmation=True,
                preview=preview,
            )

        except Exception as e:
            return ToolResult(success=False, message=f"Undo error: {e}")
