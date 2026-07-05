"""Repair layer — execute and audit repair actions.

Public API:
    execute_repair(action, **kwargs) -> RepairResult
    list_available_repairs() -> list[str]
"""

from ipracticom_sweeper.repair.actions import (
    REPAIRS,
    RepairResult,
    Snapshot,
    execute_repair,
    list_available_repairs,
    repair_drop_caches,
    repair_log_truncate_journald,
    repair_notify_human,
    repair_service_restart,
    repair_top_processes_snapshot,
    # Sprint 15 additions
    repair_dns_cache_purge,
    repair_fs_inode_warn_clear,
    repair_rotate_audit_now,
    repair_telegram_token_revalidate,
    repair_self_healthz_ping,
)

__all__ = [
    "REPAIRS",
    "RepairResult",
    "Snapshot",
    "execute_repair",
    "list_available_repairs",
    "repair_drop_caches",
    "repair_log_truncate_journald",
    "repair_notify_human",
    "repair_service_restart",
    "repair_top_processes_snapshot",
    "repair_dns_cache_purge",
    "repair_fs_inode_warn_clear",
    "repair_rotate_audit_now",
    "repair_telegram_token_revalidate",
    "repair_self_healthz_ping",
]