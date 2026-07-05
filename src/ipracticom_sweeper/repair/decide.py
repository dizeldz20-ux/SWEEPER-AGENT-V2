"""Shared approval-decision escalation + notification.

Every channel executes an approved repair its own way, but they share the
SAME follow-up so behaviour is identical everywhere:

  * on failure → block the (action, server) pair so it isn't silently
    re-proposed on the next sweep (escalation), then
  * fan out the outcome (success / failure + human escalation) to every
    channel.

:func:`escalate_and_notify` and :func:`notify_rejected` are the reusable
side-effects the dashboard HTML routes and the JSON API call after their
own audit/archive steps. :func:`approve` / :func:`reject` are the
self-contained flows used by callers that don't have one (the Slack
interactive handler).
"""
from __future__ import annotations

from typing import Any

import structlog

from ipracticom_sweeper.repair import block as block_mod

logger = structlog.get_logger()


def _push_result(proposal_dict: dict, result: dict, ok: bool) -> None:
    try:
        import asyncio
        from ipracticom_sweeper.notify.approvals import notify_approval_result

        asyncio.run(notify_approval_result(proposal_dict, result, ok))
    except Exception as e:
        logger.warning("approval_result_push_failed", error=str(e))


def _push_rejected(proposal_dict: dict, reason: str, actor: str) -> None:
    try:
        import asyncio
        from ipracticom_sweeper.notify.approvals import notify_approval_rejected

        asyncio.run(notify_approval_rejected(proposal_dict, reason, actor))
    except Exception as e:
        logger.warning("approval_rejected_push_failed", error=str(e))


def _as_result_dict(result) -> dict:
    """Normalise a RepairResult object (or dict) into the notify result dict."""
    if isinstance(result, dict):
        return result
    return {
        "action": getattr(result, "action", ""),
        "target": getattr(result, "target", ""),
        "success": getattr(result, "success", False),
        "message": getattr(result, "message", ""),
        "error": getattr(result, "error", None),
    }


def escalate_and_notify(proposal, result, ok: bool, actor: str = "operator") -> None:
    """Block (action, server) on failure, then fan out the outcome.

    ``proposal`` is a ``RepairProposal`` (has ``.action``, ``.server``,
    ``.to_dict()``); ``result`` may be a RepairResult object or a dict.
    Never raises — a notify/block failure must not turn a successful repair
    into an error for the caller.
    """
    pd = proposal.to_dict()
    result = _as_result_dict(result)
    pd["operator"] = actor
    result = {**result, "operator": actor}
    if not ok:
        try:
            block_mod.block(
                proposal.action,
                getattr(proposal, "server", "") or "",
                reason=str(result.get("error") or result.get("message") or "repair failed"),
            )
        except Exception as e:
            logger.warning("escalation_block_failed", error=str(e))
    _push_result(pd, result, ok)


def notify_rejected(proposal, reason: str, actor: str = "operator") -> None:
    """Fan out a rejection note. Never raises."""
    _push_rejected(proposal.to_dict(), reason, actor)


def approve(pid: str, actor: str = "operator") -> dict[str, Any]:
    """Self-contained approve + execute for callers without their own flow.

    Returns ``{ok, status, result?}`` or ``{ok: False, error: ...}``.
    Escalates + blocks on failure and fans out the outcome.
    """
    from ipracticom_sweeper.repair import actions as actions_mod
    from ipracticom_sweeper.repair import pending as pending_mod

    try:
        lock_cm = pending_mod._proposal_lock(pid)
    except ValueError:
        return {"ok": False, "error": "not_found"}

    with lock_cm:
        proposal = pending_mod.get_proposal(pid)
        if proposal is None:
            return {"ok": False, "error": "not_found"}
        if proposal.status != "pending":
            return {"ok": False, "error": "already_decided", "status": proposal.status}

        try:
            result = actions_mod.execute_repair(proposal.action, **proposal.kwargs)
            result_dict = {
                "action": result.action,
                "target": result.target,
                "success": result.success,
                "message": result.message,
                "error": result.error,
                "rollback_available": result.rollback_available,
            }
            new_status = "executed" if result.success else "failed"
        except Exception:
            logger.exception("approval_execute_failed", pid=pid)
            result_dict = {"action": proposal.action, "success": False, "error": "internal_error"}
            new_status = "failed"

        pending_mod.set_status(pid, new_status)
        pending_mod.archive(pid, "approved")
        pending_mod.log_audit({
            "kind": "repair_executed",
            "actor": actor,
            "proposal_id": pid,
            "action": proposal.action,
            "status": new_status,
            "result": result_dict,
        })
        ok = bool(result_dict.get("success"))
        escalate_and_notify(proposal, result_dict, ok, actor)
        return {"ok": ok, "status": new_status, "result": result_dict}


def reject(pid: str, actor: str = "operator", reason: str = "") -> dict[str, Any]:
    """Self-contained reject (archive, no execution, no block, notify)."""
    from ipracticom_sweeper.repair import pending as pending_mod

    proposal = pending_mod.get_proposal(pid)
    if proposal is None:
        return {"ok": False, "error": "not_found"}
    reason = (reason or "").strip()
    if not reason:
        return {"ok": False, "error": "reason_required"}
    if proposal.status != "pending":
        return {"ok": False, "error": "already_decided", "status": proposal.status}

    pending_mod.set_status(pid, "rejected")
    pending_mod.archive(pid, "rejected")
    pending_mod.log_audit({
        "kind": "repair_rejected",
        "actor": actor,
        "proposal_id": pid,
        "action": proposal.action,
        "reason": reason,
    })
    notify_rejected(proposal, reason, actor)
    return {"ok": True, "status": "rejected"}
