"""
InsertTool — adds new rows to a dataset.

Validates data before insertion, generates a preview of the rows to be
added, and requires explicit user confirmation before proceeding.
"""

from __future__ import annotations

import json
from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager
from app.data.validator import Validator


class InsertTool(BaseTool):
    name = "insert_data"
    description = (
        "Insert new rows into a dataset. Validates data types, required fields, "
        "and value constraints before insertion. Returns a preview of the rows "
        "to be added and requires user confirmation before applying the change."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Dataset to insert into: 'real_estate_listings' or 'marketing_campaigns'",
            },
            "rows": {
                "type": "array",
                "description": "List of row objects to insert. Each object is a dict of column → value.",
                "items": {"type": "object"},
            },
        },
        "required": ["dataset", "rows"],
    }

    def __init__(self, data_manager: DataManager, validator: Validator):
        self.dm = data_manager
        self.validator = validator

    def execute(self, **kwargs) -> ToolResult:
        try:
            dataset = kwargs.get("dataset", "")
            rows = kwargs.get("rows", [])

            if not rows:
                return ToolResult(success=False, message="No rows provided to insert.")

            # Resolve dataset
            key = self.dm.resolve_dataset(dataset)

            # Validate
            validation = self.validator.validate_insert(key, rows)

            if not validation.is_valid:
                error_msg = "Validation failed:\n" + "\n".join(f"  ❌ {e}" for e in validation.errors)
                if validation.warnings:
                    error_msg += "\n\nWarnings:\n" + "\n".join(f"  ⚠️ {w}" for w in validation.warnings)
                return ToolResult(success=False, message=error_msg)

            # Build preview
            from app.tools.preview_formatter import format_insert_preview
            preview = format_insert_preview(
                dataset_key=key,
                rows=rows,
                warnings=validation.warnings if validation.warnings else None,
            )

            return ToolResult(
                success=True,
                data={"dataset": key, "rows": rows, "operation": "insert"},
                message=preview,
                requires_confirmation=True,
                preview=preview,
            )

        except ValueError as e:
            return ToolResult(success=False, message=str(e))
        except Exception as e:
            return ToolResult(success=False, message=f"Insert error: {e}")
