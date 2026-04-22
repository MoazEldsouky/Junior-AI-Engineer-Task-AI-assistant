"""
Data Validator — validates data before mutations.

Checks type compatibility, required fields, value ranges, and enum
constraints. Returns clear error/warning messages to guide the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ValidationResult:
    """Result of a validation check."""
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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


class Validator:
    """Validates data before insert/update operations."""

    def validate_insert(
        self, dataset: str, rows: list[dict[str, Any]]
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
                    self._validate_field(result, row_label, col, value, schema[col])

        result.is_valid = len(result.errors) == 0
        return result

    def validate_update(
        self, dataset: str, updates: dict[str, Any]
    ) -> ValidationResult:
        """Validate update values."""
        result = ValidationResult()
        schema = COLUMN_SCHEMAS.get(dataset, {})

        if not schema:
            result.warnings.append(f"No validation schema for dataset: {dataset}")
            return result

        for col, value in updates.items():
            if col in schema:
                self._validate_field(result, "Update", col, value, schema[col])
            else:
                result.warnings.append(f"Column '{col}' not in known schema — skipping validation")

        result.is_valid = len(result.errors) == 0
        return result

    def _validate_field(
        self,
        result: ValidationResult,
        label: str,
        col: str,
        value: Any,
        constraints: dict[str, Any],
    ):
        """Validate a single field value against its constraints."""
        expected_type = constraints.get("type", "str")

        # Type checking
        if expected_type == "int":
            if not isinstance(value, (int, float)):
                try:
                    int(value)
                except (ValueError, TypeError):
                    result.errors.append(
                        f"{label}: '{col}' must be an integer, got {type(value).__name__}: {value!r}"
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
                    # Try other common formats
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
                # Case-insensitive check
                valid_lower = {v.lower(): v for v in valid}
                if str_val.lower() in valid_lower:
                    result.warnings.append(
                        f"{label}: '{col}' case mismatch — "
                        f"'{str_val}' will be corrected to '{valid_lower[str_val.lower()]}'"
                    )
                else:
                    result.errors.append(
                        f"{label}: '{col}' must be one of {valid}, got {str_val!r}"
                    )

        # Range constraints
        if "min" in constraints and isinstance(value, (int, float)):
            if value < constraints["min"]:
                result.errors.append(
                    f"{label}: '{col}' must be >= {constraints['min']}, got {value}"
                )

        if "max" in constraints and isinstance(value, (int, float)):
            if value > constraints["max"]:
                result.errors.append(
                    f"{label}: '{col}' must be <= {constraints['max']}, got {value}"
                )
