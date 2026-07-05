# עיצוב: זרימת אישורים רב-ערוצית מקצה-לקצה

תאריך: 2026-07-04
Branch: `feat/multichannel-approvals`
סטטוס: מאושר לעיצוב, ממתין לתכנית יישום

## מטרה

כשהסוכן מזהה תקלה במכונה שדורשת תיקון לא-בטוח, עליו **לבקש אישור באופן יזום (push) בכל הערוצים** — דאשבורד, טלגרם וסלאק — עם הצעת תיקון, פרטי המכונה, וכפתורי אישור/דחייה. לאחר אישור: לבצע בפועל, ולהודיע לכל הערוצים על התוצאה. בכישלון: להתריע שהתיקון נכשל, לבקש התערבות אנושית, ולחסום ניסיון חוזר אוטומטי עד שאדם מתערב. כל ההודעות בטמפלייט אחיד, מסודר, עברית+אנגלית.

## המצב הקיים (מה כבר עובד ומה לא)

**קיים:**
- `pipeline.py` יוצר `RepairProposal` ב-`pending_repairs/` כשפעולה דורשת אישור (`needs_approval`).
- API: `GET /api/approvals`, `POST /api/approvals/<pid>/approve` (מבצע `execute_repair`, מארכב, כותב audit), `POST /api/approvals/<pid>/reject` (דורש `reason`).
- דאשבורד: מסך "תור אישורים" (`Approvals.tsx`) עם כפתורי אשר/דחה.
- טלגרם: תפריט אישורים (`handlers/approvals.py`) — רשימה → פירוט → אשר/דחה, מחובר ל-API האמיתי.
- סלאק: תשתית `block_actions` קיימת (`slack_actions/endpoint.py`) אך ה-handler מחזיר stubs בזיכרון; slash `/approve` כותב marker file ולא מבצע.
- שכבת notify רב-בוטית: `notify/store.py` (bots.json), `_store_fanout_tasks`, `send_admin_alert` — fan-out לכל בוט Telegram/Slack + legacy env.

**לא קיים (הפערים):**
1. **אין push יזום.** ה-proposal יושב בתור עד שנכנסים לבדוק. ההתראה הכללית (`notify_pipeline_result`) היא סיכום DEFCON בלי proposal ובלי כפתורים. `notify_human` רק "רושם כוונה".
2. **אין מעגל סגור.** תוצאת האישור חוזרת רק למי שלחץ; אין הודעת "תוקן"/"נכשל" לשאר הערוצים.
3. **אין escalation.** כישלון לא מפעיל "לערב בן אדם" ולא חוסם ניסיון חוזר.
4. **פרטי מכונה חסרים.** `RepairProposal` לא שומר `server`.
5. **סלאק מנותק.** כפתורים לא מחוברים לאישור אמיתי; `/approve` שבור; אין `/reject`.
6. **טמפלייטים לא אחידים** וההתראה הכללית באנגלית.

## החלטות עיצוב (אושרו)

- **Push:** אחד לכל תקלה — dedup לפי `(action, server)`. כל עוד יש proposal ממתין לאותו צירוף, לא נוצר proposal נוסף ולא נשלח push חוזר.
- **כישלון:** התראת "נדרשת התערבות אנושית" לכל הערוצים **+ חסימה** — הסוכן לא ייצור proposal אוטומטי חדש לאותו `(action, server)` עד שאדם מסיר את החסימה.
- **סלאק:** כפתורים אינטראקטיביים (Block Kit `block_actions`), זהה לטלגרם.

## ארכיטקטורה

גישה: **הרחבת שכבת ה-notify הקיימת עם push יזום מה-pipeline.** מתלבש על התשתית הקיימת (fan-out רב-בוטי, callback של הבוטים), push מיידי, בלי תהליכים נוספים.

### זרימת נתונים

```
[pipeline] מזהה בעיה שדורשת אישור
    │
    ├─ dedup: יש כבר pending ל-(action,server)?  ── כן ──▶ דלג (אין proposal, אין push)
    ├─ blocked: (action,server) חסום?            ── כן ──▶ דלג
    │
    ├─ create_proposal(..., server=<id>)
    └─ notify_approval_request(proposal)  ──▶ fan-out: Telegram + Slack + (Dashboard מציג מהתור)
                                                כפתורים: approve/reject עם pid

[operator] לוחץ אשר/דחה  (בכל אחד מהערוצים)
    │
    ├─ Dashboard → POST /api/approvals/<pid>/approve|reject
    ├─ Telegram  → callback appr:approve|reject:<pid> → אותו endpoint
    └─ Slack     → block_actions value=<pid> → אותו endpoint
    │
    ▼
[approve endpoint] flock per-pid → execute_repair
    ├─ הצלחה ──▶ notify_approval_result(proposal, ok=True)   ──▶ fan-out "✅ תוקן"
    └─ כישלון ─▶ block_mark(action,server)
                 notify_approval_result(proposal, ok=False)  ──▶ fan-out "🚨 נכשל — לערב בן אדם"
```

### רכיבים

| רכיב | תפקיד | תלוי ב |
|---|---|---|
| `notify/templates.py` **(חדש)** | טמפלייט אחיד עברית+אנגלית. פונקציות טהורות שמחזירות מבנה הודעה לכל ערוץ (Telegram HTML/text, Slack blocks). קלט: proposal dict + סוג (request/success/failure). ללא I/O. | — |
| `notify/approvals.py` **(חדש)** | `notify_approval_request(proposal)` ו-`notify_approval_result(proposal, result, ok)`. בונה מ-templates, מוסיף כפתורים, עושה fan-out לכל בוט (Telegram `sendMessage`+`reply_markup`, Slack `chat.postMessage`+blocks). לעולם לא זורק. | `templates`, `notify/store`, `notify/legacy` (senders) |
| `repair/pending.py` (הרחבה) | שדה `server: str = ""` ב-`RepairProposal`; `find_pending(action, server)` ל-dedup. | — |
| `repair/block.py` **(חדש)** | block-markers: `is_blocked(action, server)`, `block(action, server, reason)`, `unblock(...)`, `list_blocked()`. קבצים תחת `<state>/pending_repairs/blocked/`. | `paths` |
| `pipeline.py` (הרחבה) | מעביר `server=get_server_id()`; מדלג ב-dedup/blocked; קורא `notify_approval_request` אחרי `create_proposal`. | `pending`, `block`, `notify/approvals` |
| `agent_api.py` approve/reject (הרחבה) | אחרי execute: `notify_approval_result`; בכישלון גם `block.block(...)`. reject → notify_approval_result(rejected). | `notify/approvals`, `block` |
| `slack_actions/handler.py` + `endpoint.py` (הרחבה) | action_id `approve`/`reject` עם `value=<pid>` → קריאה ל-`pending` האמיתי (execute/archive), עם אותה escalation. `commands.py`: תיקון `/approve` שיקרא ל-approve האמיתי + הוספת `/reject`. | `pending`, `notify/approvals`, `block` |
| `frontend/Approvals.tsx` (הרחבה) | הצגת `server` (שם מכונה) בכל כרטיס; badge מונה בסיידבר; רענון אוטומטי (polling קל). מסך/פעולה להסרת חסימה. | endpoints |

### Schema — `RepairProposal` (additive)

מתווסף שדה `server: str = ""` (ברירת מחדל ריקה → proposals קיימים נטענים ללא שבירה). נכתב ב-`create_proposal`, מוצג בכל הערוצים.

### Block-markers

- מיקום: `<state>/pending_repairs/blocked/<sha1(action@server)>.json` — `{action, server, reason, blocked_at}`.
- `pipeline` בודק `is_blocked` לפני יצירת proposal.
- הסרה: ידנית דרך הדאשבורד ("אשר ניסיון חוזר") או API `DELETE /api/approvals/blocked/<key>`.
- דחייה ידנית (reject) **לא** יוצרת block — רק כישלון ביצוע יוצר.

## טיפול בשגיאות ומקרי קצה

- **push נכשל בערוץ אחד:** fan-out לא זורק; כל בוט עצמאי; כישלון בערוץ אחד לא מונע אחרים ולא מפיל את ה-pipeline (כמו `send_admin_alert` היום).
- **דחיפות כפולות / race:** dedup לפי `(action,server)` לפני יצירה; `create_proposal` + בדיקת קיום תחת אותו flock מנגנון קיים.
- **אישור כפול (double-tap):** נשען על `_proposal_lock` הקיים + בדיקת `status != pending` שמחזירה 409.
- **proposals ישנים בלי `server`:** נטענים עם `server=""`; מוצגים כ-"unknown host".
- **בוטים חדשים שנוספו אחרי push:** לא מקבלים push רטרואקטיבי (מקבלים את התוצאה אם נוספו לפני ההחלטה). מקובל.
- **escalation וה-block:** אם המכונה חסומה והבעיה נמשכת — ה-pipeline מדלג בשקט (הבעיה כבר נשלחה כ-needs_human בלוג). אין הצפה.

## אבטחה

- pid עובר `_validate_pid` (path-traversal) בכל הנתיבים החדשים כמו הקיימים.
- Slack: אימות חתימה (`SLACK_SIGNING_SECRET`) כבר קיים ב-`slack_events`; block_actions עובר דרכו.
- ערכי `value` בכפתורי Slack = pid בלבד (מאומת), לא פקודות.
- סודות ממוסכים ב-audit (השימוש הקיים ב-`_redact_secrets`).

## אסטרטגיית בדיקות

- **`notify/templates.py`:** בדיקות יחידה טהורות — request/success/failure מייצרים את הפורמט הנכון לכל ערוץ, כולל עברית+אנגלית ופרטי מכונה.
- **`notify/approvals.py`:** fan-out בונה משימות לכל בוט ב-store; store ריק → אין שליחה; כישלון בוט בודד לא זורק (מדמים senders).
- **`repair/block.py`:** block/unblock/is_blocked round-trip; מפתח יציב.
- **`pipeline`:** dedup — בעיה חוזרת לא יוצרת proposal שני; blocked → דילוג; proposal חדש → קריאה ל-notify_approval_request (מדומה).
- **`agent_api` approve/reject:** הצלחה → notify_approval_result(ok=True); כישלון → block + notify(ok=False); כל דרך אישור (dashboard/telegram/slack) מגיעה לאותה תוצאה.
- **Slack:** block_actions עם value=pid → approve אמיתי; `/reject` חדש; `/approve` מתוקן.
- כל הבדיקות רצות דרך WSL (לפי מדיניות הפרויקט — fcntl/proc).

## מחוץ להיקף (YAGNI)

- אין outbox/event-bus, אין תור התמדה עם retry.
- אין two-operator quorum חדש (approvals_v2 הקיים לא משתנה).
- אין תזכורות push תקופתיות (נבחר push אחד לכל תקלה).
- אין push רטרואקטיבי לבוטים שנוספו אחרי היצירה.
