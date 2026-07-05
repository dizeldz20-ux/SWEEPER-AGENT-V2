"""Tests for the Telegram bot's conversation state dataclass."""
from __future__ import annotations

from ipracticom_sweeper.telegram_bot.states import (
    CONNECTOR_FORM_KEY,
    ConnectorField,
    ConnectorFormState,
    clear_connector_form,
    get_connector_form,
    set_connector_form,
)


def test_connector_form_state_roundtrip():
    s = ConnectorFormState(step=ConnectorField.NAME, values={"region": "il-central-1"})
    d = s.to_dict()
    assert d["step"] == "name"
    assert d["values"] == {"region": "il-central-1"}
    assert d["editing"] is None
    restored = ConnectorFormState.from_dict(d)
    assert restored.step == ConnectorField.NAME
    assert restored.values == {"region": "il-central-1"}


def test_connector_form_state_with_editing():
    s = ConnectorFormState(
        step=ConnectorField.REGION,
        values={"name": "prod-web", "instance_id": "i-1234"},
        editing="prod-web",
    )
    d = s.to_dict()
    restored = ConnectorFormState.from_dict(d)
    assert restored.editing == "prod-web"
    assert restored.step == ConnectorField.REGION


def test_get_connector_form_empty_returns_none():
    assert get_connector_form({}) is None


def test_get_connector_form_invalid_dict_returns_none():
    assert get_connector_form({CONNECTOR_FORM_KEY: "not a dict"}) is None
    assert get_connector_form({CONNECTOR_FORM_KEY: {}}) is None  # missing step
    assert get_connector_form({CONNECTOR_FORM_KEY: {"step": "bogus"}}) is None


def test_set_and_get_roundtrip():
    user_data: dict = {}
    state = ConnectorFormState(step=ConnectorField.INSTANCE_ID, values={"name": "x"})
    set_connector_form(user_data, state)
    out = get_connector_form(user_data)
    assert out is not None
    assert out.step == ConnectorField.INSTANCE_ID
    assert out.values == {"name": "x"}


def test_clear_removes_state():
    user_data: dict = {}
    set_connector_form(user_data, ConnectorFormState(step=ConnectorField.NAME))
    assert CONNECTOR_FORM_KEY in user_data
    clear_connector_form(user_data)
    assert CONNECTOR_FORM_KEY not in user_data


def test_clear_no_op_when_absent():
    user_data: dict = {}
    clear_connector_form(user_data)  # should not raise
    assert user_data == {}