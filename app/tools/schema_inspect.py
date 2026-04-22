"""
SchemaInspectTool — describes available datasets and their structure.

Helps the agent understand what data is available before querying.
"""

from __future__ import annotations

import json
from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager


class SchemaInspectTool(BaseTool):
    name = "inspect_schema"
    description = (
        "Inspect the structure and metadata of available datasets. "
        "Returns column names, data types, sample values, row counts, "
        "and unique values for categorical columns. "
        "Call with no dataset to list all available datasets, or with "
        "a specific dataset name for detailed schema info."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Dataset to inspect: 'real_estate_listings' or 'marketing_campaigns'. Omit to list all datasets.",
            },
        },
    }

    def __init__(self, data_manager: DataManager):
        self.dm = data_manager

    def execute(self, **kwargs) -> ToolResult:
        try:
            dataset = kwargs.get("dataset")

            if not dataset:
                datasets = self.dm.list_datasets()
                return ToolResult(
                    success=True,
                    data=datasets,
                    message=f"Found {len(datasets)} datasets available.",
                )

            schema = self.dm.get_schema(dataset)
            return ToolResult(
                success=True,
                data=schema,
                message=f"Schema for '{schema['display_name']}': {len(schema['columns'])} columns, {schema['total_rows']} rows.",
            )
        except ValueError as e:
            return ToolResult(success=False, message=str(e))
        except Exception as e:
            return ToolResult(success=False, message=f"Schema inspection error: {e}")
