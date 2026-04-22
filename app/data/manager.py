"""
Data Manager — loads, queries, and mutates Excel data via pandas.

Provides a clean interface for all data operations. DataFrames are cached
in memory for fast access, with write-through to Excel files on mutations.
Thread-safe via a simple lock.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT, DATA_DIR, WRITE_LOG_PATH


# ---------------------------------------------------------------------------
# Dataset configuration
# ---------------------------------------------------------------------------

DATASET_CONFIG: dict[str, dict[str, Any]] = {
    "real_estate_listings": {
        "file": DATA_DIR / "Real Estate Listings.xlsx",
        "id_column": "Listing ID",
        "display_name": "Real Estate Listings",
    },
    "marketing_campaigns": {
        "file": DATA_DIR / "Marketing Campaigns.xlsx",
        "id_column": "Campaign ID",
        "display_name": "Marketing Campaigns",
    },
}

DATASET_ALIASES: dict[str, str] = {
    "real estate": "real_estate_listings",
    "real_estate": "real_estate_listings",
    "listings": "real_estate_listings",
    "properties": "real_estate_listings",
    "houses": "real_estate_listings",
    "real estate listings": "real_estate_listings",
    "real_estate_listings": "real_estate_listings",
    "marketing": "marketing_campaigns",
    "campaigns": "marketing_campaigns",
    "marketing campaigns": "marketing_campaigns",
    "marketing_campaigns": "marketing_campaigns",
}


class DataManager:
    """Manages loading, querying, and mutating Excel datasets."""

    def __init__(self):
        self._lock = threading.Lock()
        self._dataframes: dict[str, pd.DataFrame] = {}
        self._load_all()

    def _load_all(self):
        for key, config in DATASET_CONFIG.items():
            filepath = config["file"]
            if filepath.exists():
                self._dataframes[key] = pd.read_excel(filepath, engine="openpyxl")
            else:
                raise FileNotFoundError(f"Dataset file not found: {filepath}")

    def reload(self, dataset: str | None = None):
        with self._lock:
            if dataset:
                key = self.resolve_dataset(dataset)
                config = DATASET_CONFIG[key]
                self._dataframes[key] = pd.read_excel(config["file"], engine="openpyxl")
            else:
                self._load_all()

    # -----------------------------------------------------------------
    # Dataset resolution
    # -----------------------------------------------------------------

    def resolve_dataset(self, name: str) -> str:
        normalized = name.strip().lower().replace("-", "_")
        if normalized in DATASET_CONFIG:
            return normalized
        if normalized in DATASET_ALIASES:
            return DATASET_ALIASES[normalized]
        for alias, key in DATASET_ALIASES.items():
            if alias in normalized or normalized in alias:
                return key
        raise ValueError(
            f"Unknown dataset: {name!r}. Available: {list(DATASET_CONFIG.keys())}"
        )

    def list_datasets(self) -> list[dict[str, Any]]:
        result = []
        for key, config in DATASET_CONFIG.items():
            df = self._dataframes[key]
            result.append({
                "key": key,
                "display_name": config["display_name"],
                "id_column": config["id_column"],
                "rows": len(df),
                "columns": list(df.columns),
            })
        return result

    # -----------------------------------------------------------------
    # Schema inspection
    # -----------------------------------------------------------------

    def get_schema(self, dataset: str) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        df = self._dataframes[key]
        config = DATASET_CONFIG[key]

        columns_info = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            info: dict[str, Any] = {
                "name": col,
                "type": self._pandas_type_to_friendly(dtype),
                "non_null_count": int(df[col].notna().sum()),
                "null_count": int(df[col].isna().sum()),
            }
            nunique = df[col].nunique()
            if nunique <= 10 and dtype == "object":
                info["unique_values"] = sorted(df[col].dropna().unique().tolist())
            if df[col].dtype.kind in ("i", "f"):
                info["min"] = float(df[col].min())
                info["max"] = float(df[col].max())
            columns_info.append(info)

        return {
            "dataset": key,
            "display_name": config["display_name"],
            "id_column": config["id_column"],
            "total_rows": len(df),
            "columns": columns_info,
        }

    # -----------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------

    def query(
        self,
        dataset: str,
        filters: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        limit: int | None = None,
        aggregation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        df = self._dataframes[key]

        if filters:
            df = self._apply_filters(df, filters)

        if aggregation:
            return self._apply_aggregation(df, aggregation)

        if columns:
            existing = [c for c in columns if c in df.columns]
            if existing:
                df = df[existing]

        if sort_by and sort_by in df.columns:
            df = df.sort_values(sort_by, ascending=(sort_order.lower() == "asc"))

        if limit:
            df = df.head(limit)

        total_matching = len(df)
        records = df.to_dict(orient="records")
        for record in records:
            for k, v in record.items():
                if isinstance(v, pd.Timestamp):
                    record[k] = v.strftime("%Y-%m-%d")
        return {
            "total_matching": total_matching,
            "rows_returned": len(records),
            "data": records,
        }

    def _apply_filters(self, df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
        for f in filters:
            col = f.get("column", "")
            op = f.get("operator", "eq")
            val = f.get("value")
            if col not in df.columns:
                matches = [c for c in df.columns if c.lower() == col.lower()]
                if matches:
                    col = matches[0]
                else:
                    continue
            if op == "eq":
                df = df[df[col] == val]
            elif op == "ne":
                df = df[df[col] != val]
            elif op == "gt":
                df = df[df[col] > val]
            elif op == "gte":
                df = df[df[col] >= val]
            elif op == "lt":
                df = df[df[col] < val]
            elif op == "lte":
                df = df[df[col] <= val]
            elif op == "contains":
                df = df[df[col].astype(str).str.contains(str(val), case=False, na=False)]
            elif op == "in":
                if isinstance(val, list):
                    df = df[df[col].isin(val)]
            elif op == "not_in":
                if isinstance(val, list):
                    df = df[~df[col].isin(val)]
        return df

    def _apply_aggregation(self, df: pd.DataFrame, aggregation: dict) -> dict[str, Any]:
        func = aggregation.get("function", "count")
        column = aggregation.get("column")
        group_by = aggregation.get("group_by")

        if group_by:
            if isinstance(group_by, str):
                group_by = [group_by]
            valid_groups = [g for g in group_by if g in df.columns]
            if not valid_groups:
                return {"error": f"Group-by columns not found: {group_by}"}
            grouped = df.groupby(valid_groups)
            if func == "count":
                result = grouped.size().reset_index(name="count")
            elif func in ("sum", "avg", "min", "max") and column:
                if column not in df.columns:
                    return {"error": f"Column not found: {column}"}
                agg_map = {"sum": "sum", "avg": "mean", "min": "min", "max": "max"}
                result = grouped[column].agg(agg_map[func]).reset_index(name=f"{func}_{column}")
            else:
                result = grouped.size().reset_index(name="count")
            records = result.to_dict(orient="records")
            return {"aggregation": func, "data": records, "total_groups": len(records)}
        else:
            if func == "count":
                return {"aggregation": "count", "value": len(df)}
            elif column and column in df.columns:
                agg_map = {"sum": "sum", "avg": "mean", "min": "min", "max": "max"}
                value = getattr(df[column], agg_map.get(func, "sum"))()
                return {"aggregation": func, "column": column, "value": round(float(value), 2)}
            else:
                return {"aggregation": "count", "value": len(df)}

    # -----------------------------------------------------------------
    # Insert
    # -----------------------------------------------------------------

    def insert_rows(self, dataset: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        with self._lock:
            df = self._dataframes[key]
            new_df = pd.DataFrame(rows)
            for col in new_df.columns:
                if col in df.columns and df[col].dtype == "datetime64[ns]":
                    new_df[col] = pd.to_datetime(new_df[col], errors="coerce")
            self._dataframes[key] = pd.concat([df, new_df], ignore_index=True)
            self._save(key)
        action_id = f"act_{uuid.uuid4().hex[:8]}"
        log_entry = {
            "action_id": action_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "insert",
            "dataset": key,
            "affected_rows": rows,
            "undone": False,
        }
        self._append_write_log(log_entry)
        return {"action_id": action_id, "inserted_count": len(rows)}

    # -----------------------------------------------------------------
    # Update
    # -----------------------------------------------------------------

    def get_update_preview(
        self, dataset: str, filters: list[dict[str, Any]], updates: dict[str, Any],
    ) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        config = DATASET_CONFIG[key]
        df = self._dataframes[key]
        id_col = config["id_column"]
        filtered = self._apply_filters(df, filters)
        if filtered.empty:
            return {"affected_count": 0, "preview": []}
        preview = []
        for _, row in filtered.iterrows():
            row_preview = {"row_id": str(row.get(id_col, "unknown"))}
            changes = {}
            for col, new_val in updates.items():
                if col in df.columns:
                    old_val = row[col]
                    if isinstance(old_val, pd.Timestamp):
                        old_val = old_val.strftime("%Y-%m-%d")
                    changes[col] = {"before": old_val, "after": new_val}
            row_preview["changes"] = changes
            preview.append(row_preview)
        return {"affected_count": len(preview), "preview": preview}

    def update_rows(
        self, dataset: str, filters: list[dict[str, Any]], updates: dict[str, Any],
    ) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        config = DATASET_CONFIG[key]
        id_col = config["id_column"]
        with self._lock:
            df = self._dataframes[key]
            mask = self._get_filter_mask(df, filters)
            affected = df[mask].copy()
            if affected.empty:
                return {"action_id": None, "updated_count": 0}
            affected_rows_log = []
            for _, row in affected.iterrows():
                changes = {}
                for col, new_val in updates.items():
                    if col in df.columns:
                        old_val = row[col]
                        if isinstance(old_val, pd.Timestamp):
                            old_val = old_val.strftime("%Y-%m-%d")
                        changes[col] = {"before": old_val, "after": new_val}
                affected_rows_log.append({
                    "row_id": str(row.get(id_col, "unknown")),
                    "changes": changes,
                })
            for col, new_val in updates.items():
                if col in df.columns:
                    if df[col].dtype == "datetime64[ns]":
                        new_val = pd.to_datetime(new_val, errors="coerce")
                    df.loc[mask, col] = new_val
            self._dataframes[key] = df
            self._save(key)
        action_id = f"act_{uuid.uuid4().hex[:8]}"
        log_entry = {
            "action_id": action_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "update",
            "dataset": key,
            "affected_rows": affected_rows_log,
            "undone": False,
        }
        self._append_write_log(log_entry)
        return {"action_id": action_id, "updated_count": len(affected_rows_log)}

    # -----------------------------------------------------------------
    # Delete
    # -----------------------------------------------------------------

    def get_delete_preview(self, dataset: str, filters: list[dict[str, Any]]) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        df = self._dataframes[key]
        filtered = self._apply_filters(df, filters)
        records = filtered.to_dict(orient="records")
        for record in records:
            for k, v in record.items():
                if isinstance(v, pd.Timestamp):
                    record[k] = v.strftime("%Y-%m-%d")
        return {"affected_count": len(records), "rows": records}

    def delete_rows(self, dataset: str, filters: list[dict[str, Any]]) -> dict[str, Any]:
        key = self.resolve_dataset(dataset)
        config = DATASET_CONFIG[key]
        id_col = config["id_column"]
        with self._lock:
            df = self._dataframes[key]
            mask = self._get_filter_mask(df, filters)
            deleted = df[mask].copy()
            if deleted.empty:
                return {"action_id": None, "deleted_count": 0}
            deleted_records = deleted.to_dict(orient="records")
            for record in deleted_records:
                for k, v in record.items():
                    if isinstance(v, pd.Timestamp):
                        record[k] = v.strftime("%Y-%m-%d")
            self._dataframes[key] = df[~mask].reset_index(drop=True)
            self._save(key)
        action_id = f"act_{uuid.uuid4().hex[:8]}"
        log_entry = {
            "action_id": action_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "delete",
            "dataset": key,
            "affected_rows": deleted_records,
            "undone": False,
        }
        self._append_write_log(log_entry)
        return {"action_id": action_id, "deleted_count": len(deleted_records)}

    # -----------------------------------------------------------------
    # Undo
    # -----------------------------------------------------------------

    def get_undo_preview(self, action_id: str | None = None, latest: bool = False) -> dict[str, Any]:
        log = self._read_write_log()
        if not log:
            return {"error": "No mutations to undo."}
        if latest:
            entry = None
            for e in reversed(log):
                if not e.get("undone", False):
                    entry = e
                    break
            if not entry:
                return {"error": "No undoable mutations found."}
        elif action_id:
            entry = next((e for e in log if e["action_id"] == action_id), None)
            if not entry:
                return {"error": f"Action {action_id} not found."}
            if entry.get("undone"):
                return {"error": f"Action {action_id} has already been undone."}
        else:
            return {"error": "Specify action_id or set latest=true."}
        return {
            "action_id": entry["action_id"],
            "operation": entry["operation"],
            "dataset": entry["dataset"],
            "timestamp": entry["timestamp"],
            "affected_rows": entry["affected_rows"],
        }

    def undo(self, action_id: str | None = None, latest: bool = False) -> dict[str, Any]:
        log = self._read_write_log()
        if not log:
            return {"error": "No mutations to undo."}
        entry, entry_idx = None, None
        if latest:
            for i in range(len(log) - 1, -1, -1):
                if not log[i].get("undone", False):
                    entry, entry_idx = log[i], i
                    break
        elif action_id:
            for i, e in enumerate(log):
                if e["action_id"] == action_id:
                    entry, entry_idx = e, i
                    break
        if not entry:
            return {"error": "No undoable mutation found."}
        if entry.get("undone"):
            return {"error": f"Action {entry['action_id']} already undone."}

        key = entry["dataset"]
        operation = entry["operation"]
        with self._lock:
            df = self._dataframes[key]
            config = DATASET_CONFIG[key]
            id_col = config["id_column"]
            if operation == "insert":
                inserted_ids = [r.get(id_col) for r in entry["affected_rows"] if id_col in r]
                if inserted_ids:
                    df = df[~df[id_col].isin(inserted_ids)]
            elif operation == "update":
                for row_info in entry["affected_rows"]:
                    row_id = row_info["row_id"]
                    mask = df[id_col] == row_id
                    for col, change in row_info["changes"].items():
                        old_val = change["before"]
                        if df[col].dtype == "datetime64[ns]" and isinstance(old_val, str):
                            old_val = pd.to_datetime(old_val, errors="coerce")
                        df.loc[mask, col] = old_val
            elif operation == "delete":
                restored = pd.DataFrame(entry["affected_rows"])
                for col in restored.columns:
                    if col in df.columns and df[col].dtype == "datetime64[ns]":
                        restored[col] = pd.to_datetime(restored[col], errors="coerce")
                df = pd.concat([df, restored], ignore_index=True)
            self._dataframes[key] = df.reset_index(drop=True)
            self._save(key)
        log[entry_idx]["undone"] = True
        self._write_log(log)
        return {
            "action_id": entry["action_id"],
            "operation": f"undo_{operation}",
            "undone_count": len(entry["affected_rows"]),
        }

    # -----------------------------------------------------------------
    # Write-log management
    # -----------------------------------------------------------------

    def _read_write_log(self) -> list[dict]:
        if WRITE_LOG_PATH.exists():
            with open(WRITE_LOG_PATH, "r") as f:
                return json.load(f)
        return []

    def _append_write_log(self, entry: dict):
        log = self._read_write_log()
        log.append(entry)
        self._write_log(log)

    def _write_log(self, log: list[dict]):
        WRITE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(WRITE_LOG_PATH, "w") as f:
            json.dump(log, f, indent=2, default=str)

    def get_change_history(self, dataset: str | None = None, limit: int = 10) -> list[dict]:
        log = self._read_write_log()
        if dataset:
            key = self.resolve_dataset(dataset)
            log = [e for e in log if e["dataset"] == key]
        return log[-limit:]

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _get_filter_mask(self, df: pd.DataFrame, filters: list[dict]) -> pd.Series:
        mask = pd.Series([True] * len(df), index=df.index)
        for f in filters:
            col = f.get("column", "")
            op = f.get("operator", "eq")
            val = f.get("value")
            if col not in df.columns:
                matches = [c for c in df.columns if c.lower() == col.lower()]
                if matches:
                    col = matches[0]
                else:
                    continue
            if op == "eq":
                mask &= df[col] == val
            elif op == "ne":
                mask &= df[col] != val
            elif op == "gt":
                mask &= df[col] > val
            elif op == "gte":
                mask &= df[col] >= val
            elif op == "lt":
                mask &= df[col] < val
            elif op == "lte":
                mask &= df[col] <= val
            elif op == "contains":
                mask &= df[col].astype(str).str.contains(str(val), case=False, na=False)
            elif op == "in":
                if isinstance(val, list):
                    mask &= df[col].isin(val)
            elif op == "not_in":
                if isinstance(val, list):
                    mask &= ~df[col].isin(val)
        return mask

    def _save(self, key: str):
        config = DATASET_CONFIG[key]
        df = self._dataframes[key]
        df.to_excel(config["file"], index=False, engine="openpyxl")

    @staticmethod
    def _pandas_type_to_friendly(dtype: str) -> str:
        mapping = {
            "int64": "integer",
            "float64": "decimal",
            "object": "text",
            "datetime64[ns]": "date",
            "bool": "boolean",
        }
        return mapping.get(dtype, dtype)
