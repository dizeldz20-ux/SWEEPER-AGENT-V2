"""Conversation state for multi-step Telegram bot flows.

Used by handlers that need more than one user message to complete a
flow — e.g. the ``connectors`` handler asks for a connector name, then
an instance id, then a region, then a tag set. We persist the in-flight
state in ``context.user_data`` so the bot can resume mid-flow if the
user sends another command or the polling loop restarts mid-conversation.

This module is intentionally tiny: a single dataclass + a couple of
getter/setter helpers. Keeping state in context.user_data (a plain
dict) means we don't need a database — but the dataclass gives us type
safety + validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConnectorField(str, Enum):
    """Form fields the connectors handler prompts for, in order."""

    NAME = "name"
    INSTANCE_ID = "instance_id"
    REGION = "region"
    TAGS = "tags"


@dataclass
class ConnectorFormState:
    """In-flight data for the connector create/update form.

    ``editing`` is None for new connectors, set to the connector name
    when editing an existing one (PATCH flow).
    """

    step: ConnectorField
    values: dict[str, Any] = field(default_factory=dict)
    editing: str | None = None  # connector name being edited, if any

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step.value,
            "values": dict(self.values),
            "editing": self.editing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectorFormState":
        # Require an explicit step in the payload — a dict without one
        # is corrupt, not "starting fresh".
        if "step" not in data:
            raise ValueError("missing required field: step")
        step_raw = data["step"]
        if not isinstance(step_raw, str):
            raise ValueError(f"step must be a string, got {type(step_raw).__name__}")
        try:
            step = ConnectorField(step_raw)
        except ValueError as e:
            raise ValueError(f"unknown step: {step_raw!r}") from e
        return cls(
            step=step,
            values=dict(data.get("values") or {}),
            editing=data.get("editing"),
        )


# Key used to stash state under context.user_data. Exposed as a constant
# so handlers + tests reference the same string.
CONNECTOR_FORM_KEY = "connector_form"


def get_connector_form(user_data: dict[str, Any]) -> ConnectorFormState | None:
    """Read the active connector form from user_data, if any."""
    raw = user_data.get(CONNECTOR_FORM_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return ConnectorFormState.from_dict(raw)
    except (ValueError, KeyError):
        return None


def set_connector_form(user_data: dict[str, Any], state: ConnectorFormState) -> None:
    """Persist the connector form state into user_data."""
    user_data[CONNECTOR_FORM_KEY] = state.to_dict()


def clear_connector_form(user_data: dict[str, Any]) -> None:
    """Drop any active connector form state."""
    user_data.pop(CONNECTOR_FORM_KEY, None)