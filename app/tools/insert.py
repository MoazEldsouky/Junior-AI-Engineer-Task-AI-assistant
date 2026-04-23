"""
InsertTool — adds new rows to a dataset.

Validates data before insertion, generates a preview of the rows to be
added, and requires explicit user confirmation before proceeding.

Enum Extension
--------------
If a row contains an unknown enum value (e.g. "Twitter" for Channel),
the tool returns a special requires_enum_confirmation response that
surfaces a clear warning to the user: they are about to add a brand-new
property type that doesn't currently exist in the dataset schema.
The user must explicitly agree before the schema is extended and the
insert proceeds.

Range Override
--------------
If a row contains a value outside the column's min/max range, the tool
surfaces a confirmation request warning the user. If they insist, the
insert proceeds with type checks only (range check is skipped).
"""

from __future__ import annotations

import json
from typing import Any

from app.tools.base import BaseTool, ToolResult
from app.data.manager import DataManager
from app.data.validator import Validator, extend_enum


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
            "enum_extension_confirmed": {
                "type": "boolean",
                "description": (
                    "Set to true ONLY after the user has explicitly confirmed they want to add "
                    "a brand-new property type (enum value) to the dataset. Never set this "
                    "proactively — always ask the user first."
                ),
            },
            "range_override_confirmed": {
                "type": "boolean",
                "description": (
                    "Set to true ONLY after the user has explicitly confirmed they want to use "
                    "a value outside the expected min/max range. Never set this proactively."
                ),
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
            enum_extension_confirmed: bool = kwargs.get("enum_extension_confirmed", False)
            range_override_confirmed: bool = kwargs.get("range_override_confirmed", False)

            if not rows:
                return ToolResult(success=False, message="No rows provided to insert.")

            # Resolve dataset
            key = self.dm.resolve_dataset(dataset)

            # Validate — allow overrides only if user has already confirmed
            validation = self.validator.validate_insert(
                key, rows,
                allow_new_enum_values=enum_extension_confirmed,
                allow_out_of_range=range_override_confirmed,
            )

            # Hard validation errors (type mismatches, missing required fields, etc.)
            if validation.errors:
                error_msg = "Validation failed:\n" + "\n".join(f"  ❌ {e}" for e in validation.errors)
                if validation.warnings:
                    error_msg += "\n\nWarnings:\n" + "\n".join(f"  ⚠️ {w}" for w in validation.warnings)
                return ToolResult(success=False, message=error_msg)

            # Enum extension proposals — ask the user before proceeding
            if validation.has_enum_proposals:
                proposal_lines = [
                    "⚠️ **New Property Type Warning**\n",
                    "The value(s) you provided are not in the current allowed list:\n",
                ]
                for p in validation.new_enum_proposals:
                    proposal_lines.append(
                        f"  • **{p.column}**: \"{p.proposed_value}\" is not one of "
                        f"{p.current_values}"
                    )
                proposal_lines += [
                    "\nAdding this value will **permanently extend** the dataset's schema "
                    "for all future entries.",
                    "\nDo you confirm adding this new property type? (yes/no)",
                ]
                message = "\n".join(proposal_lines)

                return ToolResult(
                    success=True,
                    data={
                        "dataset": key,
                        "rows": rows,
                        "operation": "insert",
                        "pending_enum_proposals": [
                            {
                                "column": p.column,
                                "proposed_value": p.proposed_value,
                                "current_values": p.current_values,
                            }
                            for p in validation.new_enum_proposals
                        ],
                    },
                    message=message,
                    requires_confirmation=True,
                    preview=message,
                )

            # Range override proposals — ask the user before proceeding
            if validation.has_range_proposals:
                proposal_lines = [
                    "⚠️ **Out-of-Range Warning**\n",
                    "The value(s) you provided fall outside the expected range:\n",
                ]
                for p in validation.range_proposals:
                    if p.violation == "below_min":
                        proposal_lines.append(
                            f"  • **{p.column}**: {p.proposed_value} is below the "
                            f"minimum ({p.min_allowed})"
                        )
                    else:
                        proposal_lines.append(
                            f"  • **{p.column}**: {p.proposed_value} exceeds the "
                            f"maximum ({p.max_allowed})"
                        )
                proposal_lines += [
                    "\nThis value is significantly outside the typical range for this column.",
                    "\nDo you want to proceed anyway? (yes/no)",
                ]
                message = "\n".join(proposal_lines)

                return ToolResult(
                    success=True,
                    data={
                        "dataset": key,
                        "rows": rows,
                        "operation": "insert",
                        "pending_range_proposals": [
                            {
                                "column": p.column,
                                "proposed_value": p.proposed_value,
                                "min_allowed": p.min_allowed,
                                "max_allowed": p.max_allowed,
                                "violation": p.violation,
                            }
                            for p in validation.range_proposals
                        ],
                    },
                    message=message,
                    requires_confirmation=True,
                    preview=message,
                )

            # Build preview (standard mutation confirmation)
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
