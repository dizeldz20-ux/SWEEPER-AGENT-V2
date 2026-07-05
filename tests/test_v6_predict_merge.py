"""v1.5.11 — Predict class merge.

The old `predict.analyzer` and `predict.integration` both defined a
`Prediction` dataclass with identical fields (metric, current_value,
predicted_time_hours, slope, threshold). Integration's version also
had `to_dict()`.

After the merge, `Prediction` lives only in `analyzer.py`, gains
`to_dict()`, and is re-exported from both `predict` and
`predict.integration` so old call sites keep working.
"""

from __future__ import annotations

from dataclasses import fields


def test_only_one_prediction_class_in_predict_package() -> None:
    """`class Prediction` must be defined in exactly one module under `predict/`."""
    import re
    from pathlib import Path

    pkg = Path("src/ipracticom_sweeper/predict")
    pattern = re.compile(r"^class Prediction\b", re.MULTILINE)
    hits: list[Path] = []
    for py in sorted(pkg.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(py)

    assert hits == [pkg / "analyzer.py"], (
        f"expected Prediction defined only in predict/analyzer.py; "
        f"found: {hits}"
    )


def test_prediction_has_to_dict_method() -> None:
    """The unified Prediction must carry `to_dict()` (used by
    `collect_predictions` and the `/api/predictions` route)."""
    from ipracticom_sweeper.predict.analyzer import Prediction

    p = Prediction(
        metric="disk.used_percent./",
        current_value=80.0,
        predicted_time_hours=2.5,
        slope=0.05,
        threshold=95.0,
    )
    d = p.to_dict()
    assert d["metric"] == "disk.used_percent./"
    assert d["current_value"] == 80.0
    assert d["predicted_time_hours"] == 2.5
    assert d["slope"] == 0.05
    assert d["threshold"] == 95.0


def test_prediction_to_dict_handles_none_predicted_time() -> None:
    """`predicted_time_hours=None` round-trips through `to_dict()`."""
    from ipracticom_sweeper.predict.analyzer import Prediction

    p = Prediction(
        metric="memory.used_percent",
        current_value=85.0,
        predicted_time_hours=None,
        slope=0.0,
        threshold=95.0,
    )
    d = p.to_dict()
    assert d["predicted_time_hours"] is None


def test_integration_re_exports_unified_prediction() -> None:
    """`from ipracticom_sweeper.predict.integration import Prediction`
    must return the same class as `from .analyzer import Prediction` —
    preserving the public API while only defining one canonical class."""
    from ipracticom_sweeper.predict import analyzer as _a
    from ipracticom_sweeper.predict import integration as _i

    assert _i.Prediction is _a.Prediction, (
        "predict.integration.Prediction must be the re-exported "
        "analyzer.Prediction (same class object)"
    )


def test_only_one_prediction_in_init_and_predict_package() -> None:
    """Public re-export surfaces must point at exactly one class object."""
    from ipracticom_sweeper.predict import analyzer
    from ipracticom_sweeper.predict import integration

    # Public re-export from package `__init__.py`
    from ipracticom_sweeper import predict as _p
    assert _p.Prediction is analyzer.Prediction

    # Backwards-compat: integration still exposes it (re-export only)
    assert integration.Prediction is analyzer.Prediction
