"""Telegram bot handlers — split by menu section (v0.4.2).

Each submodule owns one menu section's command + callback handlers:
  dashboard  /start, menu:main, menu:dashboard, dash:run_now
  history    menu:history, hist:metric:*, hist:range:*
  approvals  menu:approvals, appr:list, appr:approve:*, appr:reject:*
  connectors menu:connectors, conn:view:*, conn:add, conn:edit:*, conn:test:*, conn:delete:*
  fleet      menu:fleet, fleet:host:*
  settings   menu:settings, set:test:*

Each handler is a plain async function (update, context) -> dict[str, Any].
The dispatcher in bot.py wraps the dict in PTB's send_message/edit_message_text.
"""