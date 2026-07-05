"""Predict integration: read time-series DB, predict threshold crossings.

This is the bridge between the storage layer (Slice 2.0) and the
predict layer (analyzer.py). Runs on each sweep to surface "disk
will fill in X days" type warnings.

`Prediction` lives in `predict.analyzer` (canonical); re-exported
here so legacy `from predict.integration import Prediction` keeps
working.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from ipracticom_sweeper.predict.analyzer import Prediction, predict_crossing
from ipracticom_sweeper.predict.linear import linear_regression

__all__ = ["Prediction", "collect_predictions", "DEFAULT_THRESHOLDS"]

# Re-export the canonical class for back-compat with code that did
# `from ipracticom_sweeper.predict.integration import Prediction`.
# No second class definition here — see tests/test_v6_predict_merge.py.


# Default thresholds for common metrics. User can override via
# rules.predict.thresholds.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "disk.used_percent": 95.0,        # any disk mount
    "memory.used_percent": 95.0,
    "fd_check.used_percent": 95.0,
    "cpu.load_5min": 8.0,             # per-core critical
}


def collect_predictions(
    db_path: Path | str,
    host: str = "localhost",
    thresholds: dict[str, float] | None = None,
    min_samples: int = 5,
) -> list[Prediction]:
    """Read time-series DB and produce predictions for configured metrics.

    For each (metric, threshold) pair:
    - Pull last `min_samples` samples from DB
    - Run linear regression to find slope
    - Predict when the threshold will be crossed
    - Return one Prediction per metric

    Returns empty list if DB doesn't exist or has no data.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    from ipracticom_sweeper.storage import TimeSeriesDB
    db = TimeSeriesDB(db_path)
    try:
        thresholds = thresholds or DEFAULT_THRESHOLDS
        results: list[Prediction] = []

        for metric_pattern, threshold in thresholds.items():
            # Special case: disk.used_percent matches all per-mount disk
            # metrics (disk.used_percent./, disk.used_percent./var, etc.)
            if metric_pattern == "disk.used_percent":
                series_list = _query_disk_mounts(db, host, min_samples * 4)
            else:
                # Single-metric case: rows is a list of {ts, value} dicts.
                # Wrap to match the (name, [dicts]) tuple format.
                rows = db.query(host=host, metric=metric_pattern, limit=min_samples * 4)
                series_list = [(metric_pattern, rows)] if rows else []

            for sub_metric, sub_rows in series_list:
                if len(sub_rows) < min_samples:
                    continue
                # sub_rows is always a list of {ts, value} dicts
                values = []
                for r in sub_rows:
                    if isinstance(r, dict):
                        values.append((float(r["ts"]), float(r["value"])))
                    else:
                        values.append((float(r[0]), float(r[1])))
                pred = predict_crossing(values, threshold=threshold,
                                        metric_name=sub_metric)
                if pred is None:
                    continue
                # `pred` is already the canonical Prediction dataclass with
                # `to_dict()`; no field copy needed since v1.5.11 merge.
                results.append(pred)

        return results
    finally:
        db.close()


def _query_disk_mounts(db, host: str, limit: int) -> list[tuple[str, list]]:
    """Find all disk mount metrics (disk.used_percent.*) for a host."""
    # SQLite doesn't have a native prefix LIKE; we do it in Python.
    rows = db.query(host=host, metric="disk.used_percent./", limit=limit)
    if rows:
        return [("disk.used_percent./", rows)]
    # Fallback: query a few likely mount points
    candidates = ["/", "/var", "/home", "/tmp", "/opt"]
    found = []
    for mount in candidates:
        rows = db.query(host=host, metric=f"disk.used_percent.{mount}", limit=limit)
        if rows:
            found.append((f"disk.used_percent.{mount}", rows))
    return found
