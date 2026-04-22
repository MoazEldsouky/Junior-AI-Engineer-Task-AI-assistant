"""
DeleteTool — removes rows from a dataset.

Shows a preview of rows to be deleted and requires explicit user
confirmation before proceeding.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager


class DeleteTool(BaseTool):
    name = "delete_data"
    description = (
        "Delete rows from a dataset. Identifies rows using filters, "
        "shows a preview of rows to be deleted, and requires user confirmation "
        "before applying the deletion. Deleted rows can be restored using the undo tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Dataset to delete from: 'real_estate_listings' or 'marketing_campaigns'",
            },
            "filters": {
                "type": "array",
                "description": "Filter conditions to identify rows to delete.",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "operator": {"type": "string"},
                        "value": {},
                    },
                    "required": ["column", "operator", "value"],
                },
            },
        },
        "required": ["dataset", "filters"],
    }

    def __init__(self, data_manager: DataManager):
        self.dm = data_manager

    def execute(self, **kwargs) -> ToolResult:
        try:
            dataset = kwargs.get("dataset", "")
            filters = kwargs.get("filters", [])

            if not filters:
                return ToolResult(
                    success=False,
                    message="No filters provided — refusing to delete all rows. Please specify which rows to delete.",
                )

            key = self.dm.resolve_dataset(dataset)

            # Generate preview
            preview_data = self.dm.get_delete_preview(dataset, filters)

            if preview_data["affected_count"] == 0:
                return ToolResult(
                    success=True,
                    data=preview_data,
                    message="No rows match the specified filters. Nothing to delete.",
                )

            # Build human-readable preview
            from app.tools.preview_formatter import format_delete_preview
            preview = format_delete_preview(
                dataset_key=key,
                preview_data=preview_data,
            )

            return ToolResult(
                success=True,
                data={
                    "dataset": key,
                    "filters": filters,
                    "operation": "delete",
                    "affected_count": preview_data["affected_count"],
                },
                message=preview,
                requires_confirmation=True,
                preview=preview,
            )

        except ValueError as e:
            return ToolResult(success=False, message=str(e))
        except Exception as e:
            return ToolResult(success=False, message=f"Delete error: {e}")
