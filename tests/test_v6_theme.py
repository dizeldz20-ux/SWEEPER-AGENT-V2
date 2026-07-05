"""v0.6.0 slice 5.1 — theme tokens & v6 utility classes present in style.css.

Additive regression: existing styles still parse, new tokens ship and
loadable by the static handler without breaking `/`.
"""
from pathlib import Path


CSS_PATH = Path(__file__).resolve().parents[1] / "src" / "ipracticom_sweeper" / "static" / "style.css"


def _css() -> str:
    assert CSS_PATH.exists(), f"style.css missing at {CSS_PATH}"
    return CSS_PATH.read_text(encoding="utf-8")


def test_v6_slate_tokens_present():
    """Slate-950 palette defined as CSS custom properties."""
    css = _css()
    for tok in ("--slate-950", "--slate-900", "--slate-800", "--slate-700"):
        assert tok in css, f"missing palette token {tok}"


def test_v6_accent_palette_present():
    """Indigo / rose / amber / emerald / sky tokens defined."""
    css = _css()
    for tok in ("--v6-indigo", "--v6-rose", "--v6-amber", "--v6-emerald", "--v6-sky"):
        assert tok in css, f"missing accent token {tok}"


def test_v6_radius_default():
    """Default card radius 1.5rem (rounded-3xl equivalent)."""
    css = _css()
    assert "--radius-v6-card: 1.5rem" in css


def test_v6_utility_classes_defined():
    """v6 utility classes present for slice 5.2/5.3 to consume."""
    css = _css()
    for cls in (
        ".v6-surface", ".v6-surface-elevated",
        ".v6-pill", ".v6-pill-indigo", ".v6-pill-rose", ".v6-pill-amber",
        ".v6-pill-emerald", ".v6-pill-sky",
        ".v6-pulse", ".v6-pulse-fast",
        ".v6-badge", ".v6-badge-urgent", ".v6-badge-high",
        ".v6-badge-medium", ".v6-badge-low",
    ):
        assert cls in css, f"missing utility class {cls}"


def test_v6_pulse_keyframes_have_reduced_motion_guard():
    """Pulse animation respects prefers-reduced-motion (a11y)."""
    css = _css()
    assert "@keyframes v6-pulse-kf" in css
    assert "prefers-reduced-motion" in css
    assert ".v6-pulse, .v6-pulse-fast { animation: none" in css


def test_v6_does_not_break_legacy_heebo_font_stack():
    """Existing Heebo body font-family preserved (additive contract)."""
    css = _css()
    assert "'Heebo'" in css


def test_static_style_css_serves_via_flask():
    """`/static/style.css` actually serves the file with new tokens."""
    from ipracticom_sweeper.dashboard import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/static/style.css")
    assert r.status_code == 200, "static asset route broken"
    body = r.get_data(as_text=True)
    assert "--slate-950" in body
    assert "v6-pulse-kf" in body
