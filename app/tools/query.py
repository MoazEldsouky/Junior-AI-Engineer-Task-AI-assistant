"""
QueryTool — filters, sorts, aggregates, and searches dataset rows.

This is the most frequently used tool. It translates natural language
query intent into structured filter/aggregation operations on pandas.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager


class QueryTool(BaseTool):
    name = "query_data"
    description = (
        "Query and search data from a dataset. Supports filtering by column values, "
        "sorting, limiting results, and aggregations (count, sum, avg, min, max) with "
        "optional group_by. Use this for any read-only data request."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset": {
                "type": "string",
                "description": "Dataset to query: 'real_estate_listings' or 'marketing_campaigns'",
            },
            "filters": {
                "type": "array",
                "description": "Filter conditions. Each filter has 'column', 'operator' (eq, ne, gt, gte, lt, lte, contains, in, not_in), and 'value'.",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "operator": {"type": "string", "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "not_in"]},
                        "value": {},
                    },
                    "required": ["column", "operator", "value"],
                },
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific columns to return. Omit to return all columns.",
            },
            "sort_by": {
                "type": "string",
                "description": "Column to sort results by.",
            },
            "sort_order": {
                "type": "string",
                "enum": ["asc", "desc"],
                "description": "Sort direction. Default: 'asc'.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of rows to return.",
            },
            "aggregation": {
                "type": "object",
                "description": "Aggregation operation. Use 'function' (count, sum, avg, min, max), optional 'column' for the target, and optional 'group_by' column(s).",
                "properties": {
                    "function": {"type": "string", "enum": ["count", "sum", "avg", "min", "max"]},
                    "column": {"type": "string"},
                    "group_by": {
                        "description": "Column name or list of column names to group by.",
                    },
                },
            },
        },
        "required": ["dataset"],
    }

    def __init__(self, data_manager: DataManager):
        self.dm = data_manager

    def execute(self, **kwargs) -> ToolResult:
        try:
            result = self.dm.query(
                dataset=kwargs.get("dataset", ""),
                filters=kwargs.get("filters"),
                columns=kwargs.get("columns"),
                sort_by=kwargs.get("sort_by"),
                sort_order=kwargs.get("sort_order", "asc"),
                limit=kwargs.get("limit"),
                aggregation=kwargs.get("aggregation"),
            )

            if "error" in result:
                return ToolResult(success=False, message=result["error"])

            return ToolResult(
                success=True,
                data=result,
                message=f"Query returned {result.get('rows_returned', result.get('total_groups', result.get('value', 'N/A')))} results.",
            )
        except ValueError as e:
            return ToolResult(success=False, message=str(e))
        except Exception as e:
            return ToolResult(success=False, message=f"Query error: {e}")
