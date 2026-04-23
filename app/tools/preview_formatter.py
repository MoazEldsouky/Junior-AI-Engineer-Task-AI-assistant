"""
Preview Formatter — builds rich table-formatted staged previews for mutations.

Generates professional, easy-to-read confirmation messages that show
file context, affected rows, and before/after diffs in a structured table.
"""

from __future__ import annotations

from typing import Any

from app.data.manager import DATASET_CONFIG


def _fmt_value(val: Any) -> str:
    """Format a value for display — numbers get commas, strings get quotes."""
    if val is None:
        return "null"
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val == int(val):
            val = int(val)
        return f"{val:,}"
    return f'"{val}"'


def _get_dataset_meta(dataset_key: str) -> tuple[str, str, str]:
    """Return (filename, display_name, id_column) for a dataset."""
    config = DATASET_CONFIG.get(dataset_key, {})
    filename = config.get("file", "")
    if hasattr(filename, "name"):
        filename = filename.name
    else:
        filename = str(filename).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    display_name = config.get("display_name", dataset_key)
    id_col = config.get("id_column", "ID")
    return filename, display_name, id_col


def format_update_preview(
    dataset_key: str,
    preview_data: dict[str, Any],
    warnings: list[str] | None = None,
) -> str:
    """
    Build a rich staged-update preview.

    Example output:
        📊 CONFIRMATION: STAGED EXCEL UPDATE (2 Rows)
        ──────────────────────────────────────────────────
        📁 File: Real Estate Listings.xlsx

         Row  | ID        | Field        | Before       → After
        ──────────────────────────────────────────────────
         1    | LST-5002  | List Price   | 709,000      → 750,000
         2    | LST-5003  | Status       | "Active"     → "Sold"

        Apply these 2 changes? (yes/no)
    """
    filename, display_name, id_col = _get_dataset_meta(dataset_key)
    count = preview_data["affected_count"]
    rows = preview_data.get("preview", [])
    sep = "──────────────────────────────────────────────────"

    lines = [
        f"📊 CONFIRMATION: STAGED EXCEL UPDATE ({count} Row{'s' if count != 1 else ''})",
        sep,
        f"📁 File: {filename}",
        "",
        f" {'Row':<5}| {'ID':<10}| {'Field':<13}| {'Before':<13}→ After",
        sep,
    ]

    row_num = 0
    for row in rows:
        row_id = str(row.get("row_id", "?"))
        for col, change in row.get("changes", {}).items():
            row_num += 1
            before = _fmt_value(change.get("before"))
            after = _fmt_value(change.get("after"))
            lines.append(
                f" {row_num:<5}| {row_id:<10}| {col:<13}| {before:<13}→ {after}"
            )

    if warnings:
        lines.append("")
        lines.append("⚠️  Warnings:")
        for w in warnings:
            lines.append(f"    • {w}")

    lines.append("")
    lines.append(f"Apply {'these ' + str(count) + ' changes' if count > 1 else 'this change'}? (yes/no)")

    return "\n".join(lines)


def format_insert_preview(
    dataset_key: str,
    rows: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> str:
    """
    Build a rich staged-insert preview.

    Example output:
        📊 CONFIRMATION: STAGED EXCEL INSERT (1 Row)
        ──────────────────────────────────────────────────
        📁 File: Real Estate Listings.xlsx

         Row 1:
           Listing ID : "LST-9999"
           City       : "Cairo"
           List Price : 500,000

        Insert this row? (yes/no)
    """
    filename, display_name, id_col = _get_dataset_meta(dataset_key)
    count = len(rows)
    sep = "──────────────────────────────────────────────────"

    lines = [
        f"📊 CONFIRMATION: STAGED EXCEL INSERT ({count} Row{'s' if count != 1 else ''})",
        sep,
        f"📁 File: {filename}",
    ]

    for i, row in enumerate(rows):
        lines.append("")
        lines.append(f" Row {i + 1}:")
        max_key_len = max((len(str(k)) for k in row.keys()), default=0)
        for col, val in row.items():
            lines.append(f"   {col:<{max_key_len + 1}}: {_fmt_value(val)}")

    if warnings:
        lines.append("")
        lines.append("⚠️  Warnings:")
        for w in warnings:
            lines.append(f"    • {w}")

    lines.append("")
    lines.append(f"Insert {'these ' + str(count) + ' rows' if count > 1 else 'this row'}? (yes/no)")

    return "\n".join(lines)


def format_delete_preview(
    dataset_key: str,
    preview_data: dict[str, Any],
    id_col: str | None = None,
) -> str:
    """
    Build a rich staged-delete preview.

    Example output:
        📊 CONFIRMATION: STAGED EXCEL DELETE (3 Rows)
        ──────────────────────────────────────────────────
        📁 File: Marketing Campaigns.xlsx

         #   | ID         | Key Columns
        ──────────────────────────────────────────────────
         1   | CMP-0042   | Channel: "Facebook", Budget: 12,000
         2   | CMP-0051   | Channel: "Google",   Budget: 8,500
         3   | CMP-0099   | Channel: "Email",    Budget: 3,200

        Permanently delete these 3 rows? (yes/no)
    """
    filename, display_name, resolved_id_col = _get_dataset_meta(dataset_key)
    id_col = id_col or resolved_id_col
    count = preview_data["affected_count"]
    rows = preview_data.get("rows", [])
    sep = "──────────────────────────────────────────────────"

    lines = [
        f"📊 CONFIRMATION: STAGED EXCEL DELETE ({count} Row{'s' if count != 1 else ''})",
        sep,
        f"📁 File: {filename}",
        "",
        f" {'#':<4}| {'ID':<11}| Key Columns",
        sep,
    ]

    display_rows = rows[:10]
    for i, row in enumerate(display_rows, 1):
        row_id = str(row.get(id_col, "?"))
        # Show up to 3 other key columns
        other_cols = {k: v for k, v in row.items() if k != id_col}
        col_snippets = []
        for col, val in list(other_cols.items())[:3]:
            col_snippets.append(f"{col}: {_fmt_value(val)}")
        key_info = ", ".join(col_snippets) if col_snippets else "—"
        lines.append(f" {i:<4}| {row_id:<11}| {key_info}")

    if count > 10:
        lines.append(f" ... and {count - 10} more rows")

    lines.append("")
    lines.append(
        f"Permanently delete {'these ' + str(count) + ' rows' if count > 1 else 'this row'}? (yes/no)"
    )

    return "\n".join(lines)
