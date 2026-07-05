"""Connectors handler — SSM CRUD + form flow (v0.4.2).

Owns:
  - menu:connectors: list every configured connector
  - conn:view:<name>: per-connector detail + actions
  - conn:add: start a form flow (handled via states.ConnectorFormState)
  - conn:edit:<name>: same form flow, prefilled
  - conn:test:<name>: POST /api/connectors/<name>/test
  - conn:delete:<name>: DELETE /api/connectors/<name>

The form flow uses context.user_data to persist step + values across
messages. If the user sends an unrelated command mid-flow, we drop
the form state and handle the command normally.
"""
from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ipracticom_sweeper.telegram_bot.formatter import (
    format_connector_detail,
    format_connectors_list,
    format_error,
)
from ipracticom_sweeper.telegram_bot.keyboards import (
    back_to_main,
    connector_actions_kb,
    connectors_menu,
)
from ipracticom_sweeper.telegram_bot.services.agent_client import (
    AgentAPIError,
    AgentClient,
)
from ipracticom_sweeper.telegram_bot.states import (
    ConnectorField,
    ConnectorFormState,
    clear_connector_form,
    get_connector_form,
    set_connector_form,
)
from ipracticom_sweeper._log import log_suppressed


def _agent(context) -> AgentClient:
    return context.bot_data["agent"]


# ---------- menu:connectors ----------

async def connectors(update, context) -> dict[str, Any]:
    """List every connector with its status emoji.

    v0.4.3: Connectors are remote AWS instances the sweeper monitors
    via SSM. If you didn't add any, the seed list is just example
    data (clearly marked) — you can delete them with the 🗑️ button.
    """
    try:
        data = await _agent(context).list_fleet()
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    connectors_list = [h for h in (data.get("hosts") or []) if h.get("kind") == "connector"]

    # v0.4.3: header now explains what a connector is + how to recognize
    # the seed data. Without this, operators see "3 connectors" and
    # think they accidentally added servers they never set up.
    header = (
        "🔌 <b>מחברים (SSM)</b>\n"
        "<i>שרתים מרוחקים שהסוכן אוסף מהם נתונים דרך AWS SSM.</i>\n"
    )
    if connectors_list:
        # Any connector with last_error = SSM error → likely seed data
        # (real configs would have last_collected_at populated).
        all_seed = all(c.get("last_error") and not c.get("last_collected_at") for c in connectors_list)
        if all_seed:
            header += (
                "\n⚠️ <b>כל הרשומות נראות כמו seed data</b> "
                "(שגיאת SSM, לא נאסף מעולם).\n"
                "<i>לחץ על כל אחד → 🗑️ מחק כדי להסיר.</i>\n"
            )
    header += "\n"

    body = format_connectors_list(connectors_list)
    return {
        "text": header + body,
        "reply_markup": connectors_menu(connectors_list),
    }


# ---------- conn:view:<name> ----------

async def connector_view(update, context) -> dict[str, Any]:
    """Show one connector's detail + actions."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מחבר חסר", "reply_markup": back_to_main()}

    try:
        host = await _agent(context).get_fleet_host(name)
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": back_to_main()}

    if host.get("kind") != "connector":
        return {
            "text": f"❌ <code>{name}</code> אינו מחבר (סוג: {host.get('kind', '?')})",
            "reply_markup": back_to_main(),
        }

    return {
        "text": format_connector_detail(host),
        "reply_markup": connector_actions_kb(name),
    }


# ---------- conn:test:<name> ----------

async def connector_test(update, context) -> dict[str, Any]:
    """Trigger a test collection from the agent_api."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מחבר חסר", "reply_markup": back_to_main()}

    # The agent_client wrapper doesn't have a test-connector endpoint
    # because it's a separate route — call it via the lower-level _post.
    agent = _agent(context)
    try:
        result = await agent._post(f"/api/connectors/{name}/test")  # type: ignore[attr-defined]
    except AgentAPIError as e:
        return {"text": format_error(str(e)), "reply_markup": connector_actions_kb(name)}

    ok = bool(result.get("ok", False))
    emoji = "✅" if ok else "❌"
    err = result.get("error", "")
    msg = f"{emoji} <b>בדיקת {name}</b>: {'הצליחה' if ok else 'נכשלה'}"
    if err:
        msg += f"\n<i>{err[:200]}</i>"
    return {"text": msg, "reply_markup": connector_actions_kb(name)}


# ---------- conn:delete:<name> ----------

async def connector_delete(update, context) -> dict[str, Any]:
    """Delete a connector (with confirmation prompt)."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מחבר חסר", "reply_markup": back_to_main()}
    # Show a confirmation prompt; actual delete is a different callback.
    return {
        "text": f"⚠️ למחוק את <b>{name}</b>?",
        "reply_markup": InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ כן, מחק", callback_data=f"conn:delete_confirm:{name}"),
                InlineKeyboardButton("❌ בטל", callback_data=f"conn:view:{name}"),
            ],
        ]),
    }


async def connector_delete_confirm(update, context) -> dict[str, Any]:
    """Actually delete (called after confirmation)."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ שם מחבר חסר", "reply_markup": back_to_main()}
    agent = _agent(context)
    try:
        resp = await agent._http.delete(  # type: ignore[attr-defined]
            agent._url(f"/api/connectors/{name}"),  # type: ignore[attr-defined]
            headers=agent._headers(),  # type: ignore[attr-defined]
        )
    except Exception as e:
        return {"text": format_error(str(e)), "reply_markup": connector_actions_kb(name)}

    if resp.status_code == 404:
        # Already gone — show a friendly "already deleted" + refresh.
        return {
            "text": f"ℹ️ <code>{name}</code> כבר לא היה קיים.",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ חזור למחברים", callback_data="menu:connectors")],
            ]),
        }
    if resp.status_code not in (200, 204):
        return {
            "text": f"❌ מחיקה נכשלה: HTTP {resp.status_code}\n<i>{(resp.text or '')[:200]}</i>",
            "reply_markup": connector_actions_kb(name),
        }

    # Successfully deleted. Acknowledge via toast and refresh the list.
    if cq is not None:
        try:
            await cq.answer(f"🗑️ {name} נמחק")
        except Exception as e:
            log_suppressed("connectors_delete_toast", e)
    return {
        "text": f"✅ מחבר <b>{name}</b> הוסר.",
        "reply_markup": InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ חזור למחברים", callback_data="menu:connectors")],
        ]),
    }


# ---------- conn:add / conn:edit:<name> ----------

async def connector_add(update, context) -> dict[str, Any]:
    """Begin the 'add connector' form flow."""
    user_data = getattr(context, "user_data", None) or {}
    set_connector_form(user_data, ConnectorFormState(step=ConnectorField.NAME))
    return {
        "text": (
            "➕ <b>Add new connector (SSM)</b>\n\n"
            "Step 1/4 — <b>name</b>:\n"
            "<i>English letters, digits, dashes (-), underscores (_) only.</i>\n"
            "<i>Example: prod-web-1</i>"
        ),
        "reply_markup": InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:connectors")],
        ]),
    }


async def connector_edit(update, context) -> dict[str, Any]:
    """Begin the 'edit connector' form flow, prefilled with current values."""
    cq = getattr(update, "callback_query", None)
    data = (getattr(cq, "data", "") or "")
    name = data.split(":", 2)[2] if data.count(":") >= 2 else ""
    if not name:
        return {"text": "❌ Missing connector name", "reply_markup": back_to_main()}

    user_data = getattr(context, "user_data", None) or {}
    set_connector_form(
        user_data,
        ConnectorFormState(
            step=ConnectorField.INSTANCE_ID,
            values={"name": name},
            editing=name,
        ),
    )
    return {
        "text": (
            f"✏️ <b>Edit {name}</b>\n\n"
            "Send new <b>instance_id</b> (or '-' to keep current):"
        ),
        "reply_markup": InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"conn:view:{name}")],
        ]),
    }


# ---------- Free-text handler for the form flow ----------

async def connector_text_input(update, context) -> dict[str, Any] | None:
    """Handle a free-text message during the connector form flow.

    Returns None if no form is active (so the dispatcher treats it as
    a no-op). Otherwise advances the form state and returns the next
    prompt.
    """
    user_data = getattr(context, "user_data", None) or {}
    state = get_connector_form(user_data)
    if state is None:
        return None

    msg_text = ""
    if getattr(update, "message", None):
        msg_text = (update.message.text or "").strip()

    editing = state.editing

    if state.step == ConnectorField.NAME:
        if not msg_text or not msg_text.replace("-", "").replace("_", "").isalnum():
            return {
                "text": "❌ Invalid name.\nUse English letters, digits, dashes (-), and underscores (_) only.\nExample: prod-web-1",
                "reply_markup": InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="menu:connectors")],
                ]),
            }
        state.values["name"] = msg_text
        state.step = ConnectorField.INSTANCE_ID
        set_connector_form(user_data, state)
        return {
            "text": f"✅ name: <code>{msg_text}</code>\n\n"
                    f"Step 2/4 — <b>instance_id</b>:\n"
                    f"<i>Must start with 'i-' and be at least 10 characters.</i>\n"
                    f"<i>Example: i-0123456789abcdef0</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="menu:connectors")],
            ]),
        }

    if state.step == ConnectorField.INSTANCE_ID:
        if editing and msg_text == "-":
            pass  # keep existing
        elif not msg_text.startswith("i-") or len(msg_text) < 10:
            return {
                "text": "❌ Invalid instance_id.\nMust start with 'i-' and be at least 10 characters.\nExample: i-0123456789abcdef0",
                "reply_markup": InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="menu:connectors")],
                ]),
            }
        else:
            state.values["instance_id"] = msg_text
        state.step = ConnectorField.REGION
        set_connector_form(user_data, state)
        return {
            "text": f"✅ instance_id: <code>{state.values.get('instance_id', '?')}</code>\n\n"
                    f"Step 3/4 — <b>region</b>:\n"
                    f"<i>AWS region (e.g. il-central-1, eu-west-1, us-east-1).</i>\n"
                    f"<i>Send '-' to use the default (il-central-1).</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="menu:connectors")],
            ]),
        }

    if state.step == ConnectorField.REGION:
        if msg_text and msg_text != "-":
            state.values["region"] = msg_text
        state.step = ConnectorField.TAGS
        set_connector_form(user_data, state)
        return {
            "text": "✅ region: <code>" + str(state.values.get("region", "il-central-1")) + "</code>\n\n"
                    "Step 4/4 — <b>tags</b>:\n"
                    "<i>Optional. Format: key=value,key=value</i>\n"
                    "<i>Example: env=prod,role=web</i>\n"
                    "<i>Send '-' to skip.</i>",
            "reply_markup": InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="menu:connectors")],
            ]),
        }

    if state.step == ConnectorField.TAGS:
        tags: dict[str, str] = {}
        if msg_text and msg_text != "-":
            for pair in msg_text.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    tags[k.strip()] = v.strip()
        state.values["tags"] = tags
        # All fields collected — POST/PATCH.
        agent = _agent(context)
        try:
            if editing:
                # PATCH — only send fields the user actually changed.
                await agent._http.patch(  # type: ignore[attr-defined]
                    agent._url(f"/api/connectors/{editing}"),  # type: ignore[attr-defined]
                    json={
                        k: v for k, v in state.values.items()
                        if k != "name"  # name is immutable
                    },
                    headers=agent._headers(),  # type: ignore[attr-defined]
                )
                msg = f"✅ מחבר <b>{editing}</b> עודכן"
            else:
                await agent._http.post(  # type: ignore[attr-defined]
                    agent._url("/api/connectors"),  # type: ignore[attr-defined]
                    json=state.values,
                    headers=agent._headers(),  # type: ignore[attr-defined]
                )
                msg = f"✅ מחבר <b>{state.values['name']}</b> נוצר"
        except Exception as e:
            clear_connector_form(user_data)
            return {"text": format_error(str(e)), "reply_markup": connectors_menu([])}

        clear_connector_form(user_data)
        return {
            "text": msg,
            "reply_markup": connectors_menu([]),
        }

    # Unknown step — reset to be safe.
    clear_connector_form(user_data)
    return None