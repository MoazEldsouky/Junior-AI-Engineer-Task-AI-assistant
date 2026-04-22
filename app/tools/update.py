"""
UpdateTool — modifies existing rows in a dataset.

Validates the new values, generates a before/after preview of affected
rows, and requires explicit user confirmation before applying changes.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager
from app.data.validator import Validator


class UpdateTool(BaseTool):
    name = "update_data"
    description = (
        "Update existing rows in a dataset. Identifies rows using filters, "
        "validates new values, and shows a before/after preview of all affected rows. "
        "Requires user confirmation before applying changes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Dataset to update: 'real_estate_listings' or 'marketing_campaigns'",
            },
            "filters": {
                "type": "array",
                "description": "Filter conditions to identify rows to update.",
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
            "updates": {
                "type": "object",
                "description": "Dict of column → new value to apply to matching rows.",
            },
        },
        "required": ["dataset", "filters", "updates"],
    }

    def __init__(self, data_manager: DataManager, validator: Validator):
        self.dm = data_manager
        self.validator = validator

    def execute(self, **kwargs) -> ToolResult:
        try:
            dataset = kwargs.get("dataset", "")
            filters = kwargs.get("filters", [])
            updates = kwargs.get("updates") or kwargs.get("update_values", {})

            if not filters:
                return ToolResult(success=False, message="No filters provided — refusing to update all rows. Please specify which rows to update.")
            if not updates:
                return ToolResult(success=False, message="No update values provided.")

            key = self.dm.resolve_dataset(dataset)

            # Validate new values
            validation = self.validator.validate_update(key, updates)
            if not validation.is_valid:
                error_msg = "Validation failed:\n" + "\n".join(f"  ❌ {e}" for e in validation.errors)
                if validation.warnings:
                    error_msg += "\n\nWarnings:\n" + "\n".join(f"  ⚠️ {w}" for w in validation.warnings)
                return ToolResult(success=False, message=error_msg)

            # Generate preview
            preview_data = self.dm.get_update_preview(dataset, filters, updates)

            if preview_data["affected_count"] == 0:
                return ToolResult(success=True, data=preview_data, message="No rows match the specified filters. Nothing to update.")

            # Build human-readable preview
            from app.tools.preview_formatter import format_update_preview
            preview = format_update_preview(
                dataset_key=key,
                preview_data=preview_data,
                warnings=validation.warnings if validation.warnings else None,
            )

            return ToolResult(
                success=True,
                data={"dataset": key, "filters": filters, "updates": updates, "operation": "update", "affected_count": preview_data["affected_count"]},
                message=preview,
                requires_confirmation=True,
                preview=preview,
            )

        except ValueError as e:
            return ToolResult(success=False, message=str(e))
        except Exception as e:
            return ToolResult(success=False, message=f"Update error: {e}")
