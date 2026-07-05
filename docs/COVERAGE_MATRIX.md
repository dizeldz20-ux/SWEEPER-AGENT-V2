# Coverage Matrix — iPracticom Sweeper v1.0.0

Status legend: ✅ covered (test exists, passing) · ⚠️ partial (check exists, no test) · ❌ missing (no check)

## § FreeSWITCH checks (40)

| ID | Check | Status | Test file |
|---|---|---|---|
| FS-01 | Process running | ✅ | `tests/test_monitor_freeswitch_tier1.py::test_fs01_*` |
| FS-02 | Systemd active | ✅ | `tests/test_monitor_freeswitch_tier1.py::test_fs02_*` |
| FS-03 | SIP port 5060 | ✅ | `tests/test_monitor_freeswitch_tier1.py::test_fs03_*` |
| FS-04 | SIPS port 5080 | ✅ | `tests/test_monitor_freeswitch_tier1.py::test_fs04_*` |
| FS-05 | fs_cli reachable | ✅ | `tests/test_monitor_freeswitch_tier1.py::test_fs05_*` |
| FS-06 | SIP peers count | ✅ | `tests/test_monitor_freeswitch_tier2.py::test_fs06_*` |
| FS-07 | Registrations | ✅ | `tests/test_monitor_freeswitch_tier2.py::test_fs07_*` |
| FS-08 | SIP gateway status | ✅ | `tests/test_monitor_freeswitch_tier2.py::test_fs08_*` |
| FS-09 | RTP port range | ✅ | `tests/test_monitor_freeswitch_tier2.py::test_fs09_*` |
| FS-10 | fs_cli latency | ✅ | `tests/test_monitor_freeswitch_tier3.py::test_fs10_*` |
| FS-11 | Active calls count | ✅ | `tests/test_monitor_freeswitch_tier3.py::test_fs11_*` |
| FS-12 | Active channels | ✅ | `tests/test_monitor_freeswitch_tier3.py::test_fs12_*` |
| FS-13 | Log disk % | ✅ | `tests/test_monitor_freeswitch_tier3.py::test_fs13_*` |
| FS-14 | Config drift days | ✅ | `tests/test_monitor_freeswitch_tier3.py::test_fs14_*` |
| FS-15 | Baseline drift | ✅ | `tests/test_monitor_freeswitch_tier3.py::test_fs15_*` |
| FS-16 | CDR backup age | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs16_*` |
| FS-17 | Recordings age | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs17_*` |
| FS-18 | RTP packet loss | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs18_*` |
| FS-19 | Media quality MOS | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs19_*` |
| FS-20 | Call completion rate | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs20_*` |
| FS-21 | FS RSS memory | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs21_*` |
| FS-22 | FS CPU % | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs22_*` |
| FS-23 | TCP retransmits | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs23_*` |
| FS-24 | FS log errors/min | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs24_*` |
| FS-25 | mod_event_socket presence | ✅ | `tests/test_monitor_freeswitch_tier4.py::test_fs25_*` |
| FS-26 | INVITE auth failures | ✅ | `tests/test_monitor_freeswitch_v2_26_28.py::test_fs26_*` |
| FS-27 | Call drop rate | ✅ | `tests/test_monitor_freeswitch_v2_26_28.py::test_fs27_*` |
| FS-28 | NAT binding failures | ✅ | `tests/test_monitor_freeswitch_v2_26_28.py::test_fs28_*` |
| FS-29 | RTP silence | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs29_*` |
| FS-30 | SIP OPTIONS keepalive | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs30_*` |
| FS-31 | SIP parse errors | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs31_*` |
| FS-32 | Dialplan latency p95 | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs32_*` |
| FS-33 | Conference participants | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs33_*` |
| FS-34 | Voicemail quota | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs34_*` |
| FS-35 | mod_* load health | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs35_*` |
| FS-36 | ESL event queue backlog | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs36_*` |
| FS-37 | Registered vs max-procs | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs37_*` |
| FS-38 | CDR DB connection pool | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs38_*` |
| FS-39 | License vs active calls | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs39_*` |
| FS-40 | SIP trunk TPS | ✅ | `tests/test_monitor_freeswitch_v2_29_40.py::test_fs40_*` |

## § Self-resilience (5)

| ID | Feature | Status | Test file |
|---|---|---|---|
| 8.1 | External watchdog | ✅ | `tests/test_watchdog.py` |
| 8.2 | State-dir disk monitor | ✅ | `tests/test_self_disk.py` |
| 8.3 | Telegram token health | ✅ | `tests/test_telegram_health.py` |
| 8.4 | Audit log rotation | ✅ | `tests/test_audit_rotation.py` |
| 8.5 | Self-monitor snapshot | ✅ | `tests/test_self_snapshot.py` |

## § Repairs (10)

| Repair | Status | Test file |
|---|---|---|
| drop_caches | ✅ | `tests/test_repair.py` |
| log_truncate_journald | ✅ | `tests/test_repair.py` |
| service_restart | ✅ | `tests/test_repair.py` |
| top_processes_snapshot | ✅ | `tests/test_repair.py` |
| notify_human | ✅ | `tests/test_repair.py` |
| dns_cache_purge | ✅ | `tests/test_repair_sprint15.py` |
| fs_inode_warn_clear | ✅ | `tests/test_repair_sprint15.py` |
| rotate_audit_now | ✅ | `tests/test_repair_sprint15.py` |
| telegram_token_revalidate | ✅ | `tests/test_repair_sprint15.py` |
| self_healthz_ping | ✅ | `tests/test_repair_sprint15.py` |

## § Runbooks (5)

| Runbook | Status | Test file |
|---|---|---|
| disk_cleanup | ✅ | `tests/runbooks/test_engine.py` |
| memory_pressure | ✅ | `tests/runbooks/test_engine.py` |
| zombie_processes | ✅ | `tests/runbooks/test_engine.py` |
| audit_pressure | ✅ | `tests/test_runbooks_sprint15.py` |
| self_health_recovery | ✅ | `tests/test_runbooks_sprint15.py` |

## § Forecasting (3 models)

| Model | Status | Test file |
|---|---|---|
| Linear regression (v1) | ✅ | `tests/predict/test_analyzer.py` |
| Trend + Seasonal + Anomaly (v2) | ✅ | `tests/predict/test_v2.py` |
| Confidence bands + Ensemble (v2) | ✅ | `tests/predict/test_v2.py` |

## § Tally

- **Total failure modes addressed**: 40 (FS-01..FS-40) + 5 self-resilience + 10 repairs + 5 runbooks + 3 forecast models = **63**
- **All with tests**: ✅
- **Coverage**: 100% of implemented features have at least one test
