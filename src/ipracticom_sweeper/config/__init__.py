"""Config subsystem: hot-reload, secrets, token rotation, legacy rules."""
from .loader import ConfigLoader
from .secrets import load_token, TokenInfo
from .validator import validate, is_valid, SCHEMA
from . import legacy
from . import paths as _paths
from . import connectors as _connectors
from .connectors import (
    Connector,
    load_all as load_connectors,
    save_all as save_connectors,
    add as add_connector,
    update as update_connector,
    remove as remove_connector,
    get_by_name as get_connector,
    mark_collected as mark_connector_collected,
    mark_error as mark_connector_error,
    connectors_file,
    state_dir,
)

# Re-export centralized filesystem paths so callers can do
# `from ipracticom_sweeper.config import paths` and use one source of truth.
paths = _paths  # submodule reference
state_dir = _paths.state_dir  # preferred over the older connectors.state_dir
maintenance_dir = _paths.maintenance_dir
fleet_snapshots = _paths.fleet_snapshots
pending_repairs = _paths.pending_repairs
approved_repairs = _paths.approved_repairs
rejected_repairs = _paths.rejected_repairs
audit_log = _paths.audit_log
ntp_history = _paths.ntp_history
token_health = _paths.token_health

# Re-export legacy functions for backward compatibility
get_server_id = legacy.get_server_id
load_rules = legacy.load_rules
load_rules_override = legacy.load_rules_override
save_rules_override = legacy.save_rules_override
rules_override_file = legacy.rules_override_file
slack_webhook_url = legacy.slack_webhook_url
telegram_bot_token = legacy.telegram_bot_token
telegram_chat_id = legacy.telegram_chat_id
notifications_enabled = legacy.notifications_enabled
DEFCON_LEVELS = legacy.DEFCON_LEVELS

__all__ = [
    "ConfigLoader",
    "load_token",
    "TokenInfo",
    "validate",
    "is_valid",
    "SCHEMA",
    "get_server_id",
    "load_rules",
    "load_rules_override",
    "save_rules_override",
    "rules_override_file",
    "slack_webhook_url",
    "telegram_bot_token",
    "telegram_chat_id",
    "notifications_enabled",
    "DEFCON_LEVELS",
]
