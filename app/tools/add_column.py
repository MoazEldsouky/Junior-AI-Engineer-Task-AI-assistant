"""
AddColumnTool — adds a new column to a dataset.

The column values can be computed from an arithmetic formula referencing
existing column names (e.g. "Bedrooms + Bathrooms") or set to a constant
default value for all rows.

Formula evaluation uses pandas df.eval(), which supports arithmetic,
comparison, and boolean operations on existing column names — no arbitrary
Python execution is possible.

A preview of the new column (sample values from the first 5 rows) is
shown to the user before any change is made, requiring explicit confirmation.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager


class AddColumnTool(BaseTool):
    name = "add_column"
    description = (
        "Add a new column to a dataset. Column values can be computed from a formula "
        "using existing column names (e.g. 'Bedrooms + Bathrooms') or set to a constant "
        "default value for all rows. Shows a preview with sample values and requires "
        "user confirmation before applying the change."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Dataset to modify: 'real_estate_listings' or 'marketing_campaigns'",
            },
            "column_name": {
                "type": "string",
                "description": "Name of the new column to add.",
            },
            "formula": {
                "type": "string",
                "description": (
                    "Arithmetic formula using existing column names "
                    "(e.g. 'Bedrooms + Bathrooms', 'List Price * 0.9'). "
                    "Supports +, -, *, / and column references. "
                    "Omit if using default_value instead."
                ),
            },
            "default_value": {
                "description": (
                    "Constant value to fill all rows when no formula is provided. "
                    "Can be a number, string, or null."
                ),
            },
        },
        "required": ["dataset", "column_name"],
    }

    def __init__(self, data_manager: DataManager):
        self.dm = data_manager

    def execute(self, **kwargs) -> ToolResult:
        try:
            dataset = kwargs.get("dataset", "")
            column_name = kwargs.get("column_name", "").strip()
            formula: str | None = kwargs.get("formula")
            default_value: Any = kwargs.get("default_value")

            if not column_name:
                return ToolResult(success=False, message="column_name is required.")

            if not formula and default_value is None:
                return ToolResult(
                    success=False,
                    message="Provide either a formula (e.g. 'Bedrooms + Bathrooms') or a default_value.",
                )

            key = self.dm.resolve_dataset(dataset)
            preview_data = self.dm.get_add_column_preview(
                key, column_name, formula=formula, default_value=default_value
            )

            if "error" in preview_data:
                return ToolResult(success=False, message=preview_data["error"])

            from app.tools.preview_formatter import format_add_column_preview
            preview = format_add_column_preview(
                dataset_key=key,
                column_name=column_name,
                formula=formula,
                default_value=default_value,
                preview_data=preview_data,
            )

            return ToolResult(
                success=True,
                data={
                    "operation": "add_column",
                    "dataset": key,
                    "column_name": column_name,
                    "formula": formula,
                    "default_value": default_value,
                },
                message=preview,
                requires_confirmation=True,
                preview=preview,
            )

        except ValueError as e:
            return ToolResult(success=False, message=str(e))
        except Exception as e:
            return ToolResult(success=False, message=f"Add column error: {e}")
