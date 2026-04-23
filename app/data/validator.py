"""
Data Validator — validates data before mutations.

Checks type compatibility, required fields, value ranges, and enum
constraints. Returns clear error/warning messages to guide the user.

Enum Extension Policy
---------------------
By default, unknown enum values are rejected as validation errors.
However, the validator can be called with ``allow_new_enum_values=True``
after the user has explicitly confirmed they want to extend the dataset's
allowed values.

The flow for enum extension is:

  1. LLM calls insert_data / update_data with a new enum value.
  2. Validator rejects it and marks ValidationResult.new_enum_proposals
     with the (column, value) pairs that need confirmation.
  3. The tool returns requires_enum_confirmation=True in its ToolResult.
  4. The agent presents the proposal to the user with an explicit warning
     that they are about to add a brand-new property type.
  5. If the user confirms, the tool re-validates with allow_new_enum_values=True
     and the COLUMN_SCHEMAS enum list is extended in-place.

Range Override Policy
---------------------
By default, values outside a column's min/max range are hard errors.
However, the validator can be called with ``allow_out_of_range=True``
after the user has explicitly insisted on using an extreme value.

The flow mirrors enum extension:

  1. LLM calls insert_data / update_data with an out-of-range value.
  2. Validator populates ValidationResult.range_proposals (NOT errors).
  3. The tool surfaces a confirmation prompt warning about the range.
  4. If the user confirms, the tool re-validates with allow_out_of_range=True
     and the range check is skipped (type checks still apply).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Technical limits — values beyond these overflow int64/float64 and corrupt
# the Excel file (openpyxl cannot serialise numpy object-dtype columns).
_INT64_MAX = 9_223_372_036_854_775_807
_INT64_MIN = -9_223_372_036_854_775_808
_FLOAT64_MAX = 1.7976931348623157e+308


@dataclass
class EnumProposal:
    """A proposed new enum value that requires user confirmation."""
    dataset: str
    column: str
    proposed_value: str
    current_values: list[str]


@dataclass
class RangeProposal:
    """A proposed out-of-range value that requires user confirmation."""
    dataset: str
    column: str
    proposed_value: float | int
    min_allowed: float | int | None
    max_allowed: float | int | None
    violation: str   # "below_min" or "above_max"


@dataclass
class ValidationResult:
    """Result of a validation check."""
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Populated when an unknown enum value is encountered
    new_enum_proposals: list[EnumProposal] = field(default_factory=list)
    # Populated when a value exceeds the defined min/max range
    range_proposals: list[RangeProposal] = field(default_factory=list)

    @property
    def has_enum_proposals(self) -> bool:
        return len(self.new_enum_proposals) > 0

    @property
    def has_range_proposals(self) -> bool:
        return len(self.range_proposals) > 0


# ---------------------------------------------------------------------------
# Column schemas — define constraints per dataset
# ---------------------------------------------------------------------------

COLUMN_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "real_estate_listings": {
        "Listing ID": {"type": "str", "required": True, "pattern": "LST-XXXX"},
        "Property Type": {
            "type": "str",
            "required": True,
            "enum": ["House", "Condo", "Apartment", "Townhouse"],
        },
        "City": {"type": "str", "required": True},
        "State": {"type": "str", "required": True},
        "Bedrooms": {"type": "int", "min": 0, "max": 20},
        "Bathrooms": {"type": "float", "min": 0, "max": 20},
        "Square Footage": {"type": "int", "min": 100, "max": 100000},
        "Year Built": {"type": "int", "min": 1800, "max": datetime.now().year},
        "List Price": {"type": "int", "min": 1},
        "Sale Price": {"type": "int", "min": 0},
        "Listing Status": {
            "type": "str",
            "enum": ["Active", "Pending", "Sold"],
        },
    },
    "marketing_campaigns": {
        "Campaign ID": {"type": "str", "required": True, "pattern": "CMP-XXXX"},
        "Campaign Name": {"type": "str", "required": True},
        "Channel": {
            "type": "str",
            "required": True,
            "enum": ["Facebook", "Instagram", "LinkedIn", "Google Ads", "Email"],
        },
        "Start Date": {"type": "date", "required": True},
        "End Date": {"type": "date"},
        "Budget Allocated": {"type": "float", "min": 0},
        "Amount Spent": {"type": "float", "min": 0},
        "Impressions": {"type": "int", "min": 0},
        "Clicks": {"type": "int", "min": 0},
        "Conversions": {"type": "int", "min": 0},
        "Revenue Generated": {"type": "float", "min": 0},
    },
}


def extend_enum(dataset: str, column: str, new_value: str) -> None:
    """
    Permanently add a new value to an enum column's allowed list.

    Called only after the user has explicitly confirmed they want to
    add a brand-new property type to the dataset schema.
    """
    schema = COLUMN_SCHEMAS.get(dataset, {})
    col_schema = schema.get(column)
    if col_schema and "enum" in col_schema:
        if new_value not in col_schema["enum"]:
            col_schema["enum"].append(new_value)


class Validator:
    """Validates data before insert/update operations."""

    def validate_insert(
        self,
        dataset: str,
        rows: list[dict[str, Any]],
        allow_new_enum_values: bool = False,
        allow_out_of_range: bool = False,
    ) -> ValidationResult:
        """Validate rows to be inserted."""
        result = ValidationResult()
        schema = COLUMN_SCHEMAS.get(dataset, {})

        if not schema:
            result.warnings.append(f"No validation schema for dataset: {dataset}")
            return result

        for i, row in enumerate(rows):
            row_label = f"Row {i + 1}"

            # Check required fields
            for col, constraints in schema.items():
                if constraints.get("required") and col not in row:
                    result.errors.append(f"{row_label}: Missing required field '{col}'")

            # Validate each provided field
            for col, value in row.items():
                if col in schema:
                    self._validate_field(
                        result, row_label, col, value, schema[col],
                        dataset=dataset,
                        allow_new_enum_values=allow_new_enum_values,
                        allow_out_of_range=allow_out_of_range,
                    )

        result.is_valid = (
            len(result.errors) == 0
            and not result.has_enum_proposals
            and not result.has_range_proposals
        )
        return result

    def validate_update(
        self,
        dataset: str,
        updates: dict[str, Any],
        allow_new_enum_values: bool = False,
        allow_out_of_range: bool = False,
    ) -> ValidationResult:
        """Validate update values."""
        result = ValidationResult()
        schema = COLUMN_SCHEMAS.get(dataset, {})

        if not schema:
            result.warnings.append(f"No validation schema for dataset: {dataset}")
            return result

        for col, value in updates.items():
            if col in schema:
                self._validate_field(
                    result, "Update", col, value, schema[col],
                    dataset=dataset,
                    allow_new_enum_values=allow_new_enum_values,
                    allow_out_of_range=allow_out_of_range,
                )
            else:
                result.warnings.append(f"Column '{col}' not in known schema — skipping validation")

        result.is_valid = (
            len(result.errors) == 0
            and not result.has_enum_proposals
            and not result.has_range_proposals
        )
        return result

    def _validate_field(
        self,
        result: ValidationResult,
        label: str,
        col: str,
        value: Any,
        constraints: dict[str, Any],
        dataset: str = "",
        allow_new_enum_values: bool = False,
        allow_out_of_range: bool = False,
    ):
        """Validate a single field value against its constraints."""
        expected_type = constraints.get("type", "str")

        # Type checking
        if expected_type == "int":
            if not isinstance(value, (int, float)):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    result.errors.append(
                        f"{label}: '{col}' must be an integer, got {type(value).__name__}: {value!r}"
                    )
                    return
            # Overflow guard — Excel/int64 cannot store values beyond ±9.2×10¹⁸
            numeric_val = int(value) if isinstance(value, float) else value
            if isinstance(numeric_val, int) and (numeric_val > _INT64_MAX or numeric_val < _INT64_MIN):
                result.errors.append(
                    f"{label}: '{col}' value {value} exceeds the 64-bit integer limit "
                    f"(max ±{_INT64_MAX:,}). Excel cannot store this value."
                )
                return
            if isinstance(value, float) and value != int(value):
                result.warnings.append(
                    f"{label}: '{col}' is a decimal ({value}), will be truncated to {int(value)}"
                )

        elif expected_type == "float":
            if not isinstance(value, (int, float)):
                try:
                    float(value)
                except (ValueError, TypeError):
                    result.errors.append(
                        f"{label}: '{col}' must be a number, got {type(value).__name__}: {value!r}"
                    )
                    return
            # Overflow guard — Excel/float64 cannot store values beyond ±1.8×10³⁰⁸
            if isinstance(value, (int, float)) and abs(float(value)) > _FLOAT64_MAX:
                result.errors.append(
                    f"{label}: '{col}' value {value} exceeds the 64-bit float limit. "
                    f"Excel cannot store this value."
                )
                return

        elif expected_type == "date":
            if not isinstance(value, (datetime, str)):
                result.errors.append(
                    f"{label}: '{col}' must be a date string (YYYY-MM-DD), got {type(value).__name__}"
                )
                return
            if isinstance(value, str):
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    parsed = False
                    for fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%B %d, %Y"):
                        try:
                            datetime.strptime(value, fmt)
                            parsed = True
                            break
                        except ValueError:
                            continue
                    if not parsed:
                        result.errors.append(
                            f"{label}: '{col}' has invalid date format: {value!r}. "
                            f"Use YYYY-MM-DD format."
                        )
                        return

        elif expected_type == "str":
            if not isinstance(value, str):
                result.warnings.append(
                    f"{label}: '{col}' expected text, got {type(value).__name__} — will be converted"
                )

        # Enum constraints
        if "enum" in constraints:
            str_val = str(value)
            valid = constraints["enum"]
            if str_val not in valid:
                # Case-insensitive match — treat as a casing warning, not an error
                valid_lower = {v.lower(): v for v in valid}
                if str_val.lower() in valid_lower:
                    result.warnings.append(
                        f"{label}: '{col}' case mismatch — "
                        f"'{str_val}' will be corrected to '{valid_lower[str_val.lower()]}'"
                    )
                elif allow_new_enum_values:
                    # User already confirmed — extend the schema live
                    extend_enum(dataset, col, str_val)
                    result.warnings.append(
                        f"{label}: '{col}' — new value '{str_val}' added to allowed list."
                    )
                else:
                    # Unknown value — propose enum extension instead of hard error
                    result.new_enum_proposals.append(
                        EnumProposal(
                            dataset=dataset,
                            column=col,
                            proposed_value=str_val,
                            current_values=list(valid),
                        )
                    )
                    # Do NOT add an error here — the tool will handle it separately

        # Range constraints
        if "min" in constraints and isinstance(value, (int, float)):
            if value < constraints["min"]:
                if allow_out_of_range:
                    result.warnings.append(
                        f"{label}: '{col}' = {value} is below the expected "
                        f"minimum ({constraints['min']}). Proceeding as confirmed."
                    )
                else:
                    result.range_proposals.append(
                        RangeProposal(
                            dataset=dataset,
                            column=col,
                            proposed_value=value,
                            min_allowed=constraints["min"],
                            max_allowed=constraints.get("max"),
                            violation="below_min",
                        )
                    )

        if "max" in constraints and isinstance(value, (int, float)):
            if value > constraints["max"]:
                if allow_out_of_range:
                    result.warnings.append(
                        f"{label}: '{col}' = {value} exceeds the expected "
                        f"maximum ({constraints['max']}). Proceeding as confirmed."
                    )
                else:
                    result.range_proposals.append(
                        RangeProposal(
                            dataset=dataset,
                            column=col,
                            proposed_value=value,
                            min_allowed=constraints.get("min"),
                            max_allowed=constraints["max"],
                            violation="above_max",
                        )
                    )
