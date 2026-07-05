"""Tests for catalogue module + dashboard routes (v0.5.0 slice 1.2)."""
import pytest
from unittest.mock import patch
from ipracticom_sweeper.dashboard import app
from ipracticom_sweeper.catalogue import (
    CHECK_REGISTRY,
    get_check,
    render_catalogue,
    render_check,
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# --- catalogue module pure-function tests ----------------------------------


def test_registry_has_known_modules():
    keys = {e["key"] for e in CHECK_REGISTRY}
    # Must include the 9 snapshot module keys we know about
    for required in ("cpu", "memory", "disk", "network", "services"):
        assert required in keys


def test_registry_entries_have_required_fields():
    for e in CHECK_REGISTRY:
        assert "key" in e and isinstance(e["key"], str)
        assert "label_he" in e and isinstance(e["label_he"], str)
        assert "description_he" in e and isinstance(e["description_he"], str)
        assert isinstance(e.get("rule_keys", []), list)


def test_rule_keys_have_type_and_description():
    for e in CHECK_REGISTRY:
        for rk in e.get("rule_keys", []):
            assert "name" in rk
            assert rk.get("type") in {"int", "float", "str", "list"}
            assert "description_he" in rk


def test_get_check_known():
    cpu = get_check("cpu")
    assert cpu is not None
    assert cpu["label_he"] == "מעבד (CPU)"


def test_get_check_unknown_returns_none():
    assert get_check("does-not-exist") is None


def test_render_catalogue_includes_param_counts():
    rules = {"cpu": {"load_avg_5min_warn": 5.0}}
    rows = render_catalogue(rules)
    cpu_row = next(r for r in rows if r["key"] == "cpu")
    assert cpu_row["param_count"] == 4
    assert cpu_row["current_param_count"] == 1


def test_render_catalogue_handles_empty_rules():
    rows = render_catalogue({})
    assert all(r["current_param_count"] == 0 for r in rows)
    # Every entry still produced
    assert len(rows) == len(CHECK_REGISTRY)


def test_render_check_returns_params_with_current_values():
    rules = {"memory": {"used_percent_warn": 80.0, "swap_used_percent_warn": 50.0}}
    data = render_check("memory", rules)
    assert data is not None
    by_name = {p["name"]: p for p in data["params"]}
    assert by_name["used_percent_warn"]["current_value"] == 80.0
    assert by_name["swap_used_percent_warn"]["current_value"] == 50.0
    assert by_name["used_percent_crit"]["current_value"] is None


def test_render_check_unknown_returns_none():
    assert render_check("nope", {}) is None


# --- dashboard route smoke tests -------------------------------------------


def test_catalogue_index_loads(client):
    r = client.get("/catalogue")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "קטלוג בדיקות" in body
    # At least the cpu row shows up
    assert "<code>cpu</code>" in body


def test_catalogue_module_loads_known(client):
    r = client.get("/catalogue/cpu")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "מעבד" in body
    assert "load_avg_5min_warn" in body


def test_catalogue_module_unknown_returns_404(client):
    r = client.get("/catalogue/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["error"] == "unknown_module"


def test_catalogue_module_handles_load_rules_failure(client):
    """When load_rules throws, page still renders with empty current values."""
    with patch("ipracticom_sweeper.config.load_rules", side_effect=OSError("boom")):
        r = client.get("/catalogue/cpu")
    assert r.status_code == 200
    assert "לא מוגדר" in r.get_data(as_text=True)


def test_base_html_links_to_catalogue(client):
    r = client.get("/")
    body = r.get_data(as_text=True)
    assert 'href="/catalogue"' in body
