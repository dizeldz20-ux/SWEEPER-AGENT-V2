# Multi-Channel Approval Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Steps use checkbox (`- [ ]`) syntax.

**Goal:** כשהסוכן מזהה תקלה שדורשת אישור — לבקש אישור push בכל הערוצים (דאשבורד/טלגרם/סלאק) עם פרטי מכונה, לבצע לאחר אישור, להודיע על התוצאה לכולם, ולעשות escalation + חסימה בכישלון.

**Architecture:** הרחבת שכבת ה-notify הקיימת. ה-pipeline שולח `notify_approval_request` מיד אחרי `create_proposal`; ה-approve/reject endpoint שולח `notify_approval_result`. fan-out לכל בוט דרך התשתית הקיימת. dedup לפי `(action, server)`; חסימה בכישלון.

**Tech Stack:** Python 3.12, Flask, httpx, python-telegram-bot, pytest. Frontend: React + TS + Vite.

## Global Constraints

- הבדיקות רצות **רק דרך WSL** (fcntl/proc). לעולם לא Python native על Windows.
- כל הודעה בטמפלייט אחיד עברית+אנגלית, פסקאות לפי נושא, פרטי מכונה תמיד.
- שינויי schema **additive** — `server: str = ""` default; proposals קיימים נטענים ללא שבירה.
- fan-out לעולם לא זורק — כישלון בוט/ערוץ בודד לא מפיל את ה-pipeline.
- dedup: push אחד לכל `(action, server)`. כישלון → חסימה עד התערבות אנושית. reject ידני **לא** חוסם.
- pid מאומת (`_validate_pid`) בכל נתיב חדש.

---

## File Structure

- **Create** `src/ipracticom_sweeper/notify/templates.py` — פונקציות טהורות שבונות הודעת request/success/failure לכל ערוץ.
- **Create** `src/ipracticom_sweeper/notify/approvals.py` — `notify_approval_request`, `notify_approval_result` — fan-out + כפתורים.
- **Create** `src/ipracticom_sweeper/repair/block.py` — block-markers ל-escalation.
- **Modify** `src/ipracticom_sweeper/repair/pending.py` — שדה `server`; `find_pending(action, server)`.
- **Modify** `src/ipracticom_sweeper/pipeline.py` — server, dedup/blocked skip, קריאה ל-notify_approval_request.
- **Modify** `src/ipracticom_sweeper/agent_api.py` — approve/reject: notify_approval_result + block בכישלון; endpoints ל-blocked (list/delete).
- **Modify** `src/ipracticom_sweeper/slack_actions/handler.py`, `endpoint.py`, `commands.py` — approve/reject אמיתי.
- **Modify** `frontend/src/components/Approvals.tsx` (+ hooks/endpoints/types) — הצגת server, badge, הסרת חסימה.

---

## Task 1: notify/templates.py — טמפלייט אחיד

**Files:** Create `src/ipracticom_sweeper/notify/templates.py`; Test `tests/test_approval_templates.py`

**Interfaces — Produces:**
- `approval_request_text(proposal: dict) -> str` — טקסט Telegram-HTML/plain, כולל host/detected/fix/id.
- `approval_result_text(proposal: dict, result: dict, ok: bool) -> str` — הצלחה/כישלון; בכישלון כולל בלוק "לערב בן אדם".
- `approval_request_blocks(proposal: dict) -> list[dict]` — Slack Block Kit + `actions` block עם כפתורי approve/reject (`action_id`, `value=pid`).
- `approval_result_blocks(proposal: dict, result: dict, ok: bool) -> list[dict]` — Slack blocks לתוצאה.
- קבוע `HUMAN_ESCALATION_HE_EN` — טקסט האזהרה הדו-לשוני.

**Steps:**
- [ ] Write failing tests: request text מכיל hostname, action, `🆔`+pid, שורת עברית ושורת אנגלית; failure text מכיל "לערב בן אדם"/"Human Intervention"; blocks מחזיר actions block עם value==pid.
- [ ] Run → FAIL (module missing).
- [ ] Implement pure formatters (ראה טמפלייטים ב-spec §"הטמפלייט האחיד").
- [ ] Run → PASS. Commit.

## Task 2: pending.py — server + dedup

**Files:** Modify `src/ipracticom_sweeper/repair/pending.py`; Test `tests/test_repair_pending.py` (extend)

**Interfaces:**
- Consumes: `RepairProposal`, `create_proposal`, `list_pending`.
- Produces: `RepairProposal.server: str = ""`; `create_proposal(..., server: str = "")`; `find_pending(action: str, server: str) -> RepairProposal | None`.

**Steps:**
- [ ] Write failing tests: create_proposal עם server נשמר ונטען; proposal ישן בלי server נטען עם `server==""`; `find_pending` מוצא pending תואם ומחזיר None כשאין.
- [ ] Run → FAIL.
- [ ] Add `server` field (default `""`), param ב-create_proposal, `find_pending` (סורק pending בלבד, action+server).
- [ ] Run → PASS. Commit.

## Task 3: repair/block.py — חסימת escalation

**Files:** Create `src/ipracticom_sweeper/repair/block.py`; Test `tests/test_repair_block.py`

**Interfaces — Produces:**
- `block_key(action: str, server: str) -> str` — `sha1(f"{action}@{server}").hexdigest()`.
- `block(action: str, server: str, reason: str) -> None`; `is_blocked(action: str, server: str) -> bool`; `unblock_key(key: str) -> bool`; `list_blocked() -> list[dict]`.
- דיר: `<state>/pending_repairs/blocked/<key>.json` = `{action, server, reason, blocked_at}`.

**Steps:**
- [ ] Write failing tests: block→is_blocked True; unblock_key→is_blocked False; list_blocked מחזיר את הרשומה; key יציב.
- [ ] Run → FAIL.
- [ ] Implement (משתמש ב-`IPRACTICOM_SWEEPER_STATE_DIR` כמו pending.py).
- [ ] Run → PASS. Commit.

## Task 4: notify/approvals.py — fan-out

**Files:** Create `src/ipracticom_sweeper/notify/approvals.py`; Test `tests/test_notify_approvals.py`

**Interfaces:**
- Consumes: `notify.store.telegram_bots()/slack_bots()`, `notify.legacy._send_telegram_to/_send_slack_app` + legacy env senders, `templates.*`.
- Produces: `async notify_approval_request(proposal: dict) -> dict[str,bool]`; `async notify_approval_result(proposal: dict, result: dict, ok: bool) -> dict[str,bool]`.
- Telegram: `sendMessage` עם `reply_markup` inline keyboard — callback_data `appr:approve:<pid>` / `appr:reject:<pid>` (תואם handler קיים).
- Slack: `chat.postMessage` עם `blocks` מ-templates.

**Steps:**
- [ ] Write failing tests: store ריק + אין legacy env → `{}` (אין שליחה); שני בוטי telegram → שתי משימות שליחה (senders מדומים); sender שזורק → נתפס, לא מפיל; keyboard מכיל pid.
- [ ] Run → FAIL.
- [ ] Implement fan-out (מקביל ל-`_store_fanout_tasks`), Telegram inline keyboard נשלח דרך helper חדש `_send_telegram_kb`, Slack blocks דרך `_send_slack_app`.
- [ ] Run → PASS. Commit.

## Task 5: pipeline.py — חיווט push

**Files:** Modify `src/ipracticom_sweeper/pipeline.py`; Test `tests/test_pipeline.py` (extend)

**Interfaces:** Consumes Task 2/3/4.

**Steps:**
- [ ] Write failing tests: בעיה שדורשת אישור → `create_proposal` נקרא עם server==get_server_id, ו-`notify_approval_request` נקרא (mock); בעיה חוזרת עם pending קיים → לא נוצר proposal שני; `(action,server)` חסום → דילוג.
- [ ] Run → FAIL.
- [ ] בבלוק `needs_approval`: dedup (`find_pending`) + `is_blocked` → skip; אחרת create_proposal(server=...) + `asyncio.run(notify_approval_request(...))` בתוך try/except.
- [ ] Run → PASS. Commit.

## Task 6: agent_api.py — תוצאה + escalation

**Files:** Modify `src/ipracticom_sweeper/agent_api.py`; Test `tests/test_approvals_route.py` (extend)

**Interfaces:** Consumes Task 3/4.

**Steps:**
- [ ] Write failing tests: approve שמצליח → `notify_approval_result(ok=True)` נקרא (mock); approve שנכשל → `block.block(...)` נקרא + `notify_approval_result(ok=False)`; reject → notify_approval_result(rejected). `GET /api/approvals/blocked` מחזיר רשימה; `DELETE /api/approvals/blocked/<key>` מסיר.
- [ ] Run → FAIL.
- [ ] הוסף קריאות notify (async→asyncio.run, try/except) אחרי archive; block בכישלון; שני endpoints ל-blocked.
- [ ] Run → PASS. Commit.

## Task 7: Slack — כפתורים אמיתיים + commands

**Files:** Modify `slack_actions/handler.py`, `slack_actions/endpoint.py`, `slack_actions/commands.py`; Test `tests/test_slack_commands.py` (extend) + `tests/test_slack_approvals.py` (new)

**Interfaces:**
- `SlackActionType.APPROVE="approve"`, `REJECT="reject"`; `payload_to_action` ממפה action_id approve/reject עם value=pid.
- handler מבצע approve/reject אמיתי דרך `pending` (execute_repair/archive/log) + escalation זהה ל-Task 6.
- `commands.cmd_approve` קורא ל-approve האמיתי; `cmd_reject` חדש.

**Steps:**
- [ ] Write failing tests: block_actions approve→execute+archive; reject→archive rejected; `/approve <id>` מבצע (לא marker); `/reject <id> <reason>` עובד.
- [ ] Run → FAIL.
- [ ] Implement; שיתוף לוגיקת approve/reject עם Task 6 דרך helper משותף `repair.decide` (execute+archive+notify+block) כדי לא לשכפל.
- [ ] Run → PASS. Commit.

## Task 8: frontend — server + badge + unblock

**Files:** Modify `frontend/src/components/Approvals.tsx`, `hooks/useApprovals.ts`, `services/endpoints.ts`, types.

**Steps:**
- [ ] הצג `approval.server` (שם מכונה) בכל כרטיס.
- [ ] Badge מונה בסיידבר (מספר pending) — polling קל ב-useApprovals (interval ~15s).
- [ ] סקשן "חסומים" עם כפתור "אפשר ניסיון חוזר" → `DELETE /api/approvals/blocked/<key>`.
- [ ] `tsc --noEmit` + `vite build` נקיים.
- [ ] Commit.

## Refactor note (Task 6+7 shared logic)

כדי למנוע כפילות בין ה-approve endpoint (Task 6) לבין Slack handler (Task 7), מחלצים helper `repair/decide.py`:
`decide(pid, actor, kind) -> dict` — flock, status-check, execute_repair, archive, log_audit, block-on-fail, ואז מחזיר result (ה-notify נקרא ע"י ה-caller ליד ה-request context, או פנימית). זה ייכתב ב-Task 6 ויימשך ב-Task 7.

---

## Self-Review

- **כיסוי spec:** push(§ארכיטקטורה)→T4,T5; תוצאה+escalation→T6; server→T2; dedup→T2,T5; block→T3,T5,T6; Slack→T7; טמפלייט→T1; frontend/badge→T8. ✓
- **Placeholders:** אין. הקוד המדויק ייכתב בביצוע TDD; interfaces וחתימות מוגדרים.
- **Type consistency:** `server` str אחיד; `block_key/is_blocked/block/unblock_key` שמות אחידים בין T3/T6/T7; callback_data `appr:approve|reject:<pid>` תואם handler קיים.
