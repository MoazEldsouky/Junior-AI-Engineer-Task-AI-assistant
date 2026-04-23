"""
UpdateTool — modifies existing rows in a dataset.

Validates the new values, generates a before/after preview of affected
rows, and requires explicit user confirmation before applying changes.

Enum Extension
--------------
If an update sets a column to a value not in its allowed list, the tool
surfaces a confirmation request that warns the user they are about to add
a brand-new property type to the dataset schema.

Range Override
--------------
If an update sets a column to a value outside its min/max range, the tool
surfaces a confirmation request warning the user. If they insist, the
update proceeds with type checks only (range check is skipped).
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
            enum_extension_confirmed: bool = kwargs.get("enum_extension_confirmed", False)
            range_override_confirmed: bool = kwargs.get("range_override_confirmed", False)

            if not filters:
                return ToolResult(success=False, message="No filters provided — refusing to update all rows. Please specify which rows to update.")
            if not updates:
                return ToolResult(success=False, message="No update values provided.")

            key = self.dm.resolve_dataset(dataset)

            # Validate new values
            validation = self.validator.validate_update(
                key, updates,
                allow_new_enum_values=enum_extension_confirmed,
                allow_out_of_range=range_override_confirmed,
            )

            # Hard validation errors
            if validation.errors:
                error_msg = "Validation failed:\n" + "\n".join(f"  ❌ {e}" for e in validation.errors)
                if validation.warnings:
                    error_msg += "\n\nWarnings:\n" + "\n".join(f"  ⚠️ {w}" for w in validation.warnings)
                return ToolResult(success=False, message=error_msg)

            # Enum extension proposals — must get user confirmation first
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
                        "filters": filters,
                        "updates": updates,
                        "operation": "update",
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

            # Range override proposals — must get user confirmation first
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
                        "filters": filters,
                        "updates": updates,
                        "operation": "update",
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
