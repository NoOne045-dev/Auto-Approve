# 🗂️ Target Structure — Auto / Pending Approval Bot

This is the structure your current `Auto-Approve-Bot` repo needs to grow into to
support the four feature pillars: **Pending Approval**, **Scheduled Approval**,
**Channel Customization**, and **Plan & Quota**. It extends the existing
Kurigram + Motor (async MongoDB) codebase — nothing here requires a rewrite,
only additive modules.

```
Auto-Approve-Bot/
├── main.py                      # Entry point & lifecycle runner (extend: supervisor loop, graceful shutdown)
├── config.py                    # Settings & access control (extend: plan tiers, scheduler tz, quota reset hour)
├── database.py                  # Motor driver (extend: schedules / plans / quotas collections)
├── helpers.py                   # Keyboards, formatters, captcha & spam checks (extend: shared callback-data namespacing)
│
├── plugins/
│   ├── start.py                 # /start, /help, /ping, main menu
│   ├── join_request.py          # ChatJoinRequest listener & approval queue worker
│   ├── captcha.py                # DM captcha verification callbacks
│   ├── admin.py                  # Channel toggle switches (feeds into managechnls.py)
│   ├── welcome.py                # Welcome message/media/button editor (feeds into managechnls.py)
│   ├── leave.py                  # NEW — ChatMemberLeft/Kicked listener → goodbye message
│   ├── pending_request.py        # EXISTING but broken — /approveall, /queue; audit & rebuild (Steps 1 & 3)
│   ├── session.py                # NEW — /login, /logout, /sessions (secure session connect flow)
│   ├── schedule.py               # NEW — /schedule (date/time approval automation)
│   ├── managechnls.py            # NEW — unified channel control center (wraps admin.py + welcome.py + leave.py)
│   ├── plan.py                   # NEW — /plan, /quota (plan dashboard, usage bars, lifetime stats)
│   ├── mass.py                   # Bulk approve/decline/CSV export (reused by pending.py)
│   ├── broadcast.py              # High-speed broadcast suite
│   └── stats.py                  # Global/chat analytics (extend: engine trust score, feeds /plan)
│
├── core/                         # NEW package — shared engine logic, imported by multiple plugins
│   ├── __init__.py
│   ├── queue_manager.py          # Token-bucket queue: enqueue(), get_status(), is_running()
│   ├── scheduler.py              # Job scheduling + persistence, timezone handling
│   ├── quota.py                  # Daily/weekly/monthly usage tracking + reset logic
│   ├── cache.py                  # TTL cache for channel settings & plan lookups
│   ├── permissions.py            # Per-user / per-channel access + premium-feature gates
│   ├── recovery.py               # Auto-reconnect, crash recovery, health checks
│   └── session_manager.py        # NEW — Encrypt/decrypt & store user session strings safely
│
├── tests/                        # NEW — unit tests (mongomock or test DB)
│   ├── test_quota.py
│   ├── test_permissions.py
│   ├── test_scheduler_persistence.py
│   ├── test_queue_eta.py
│   └── test_session_manager.py   # NEW — confirms no plaintext session ever hits DB/logs
│
├── AUDIT.md                      # NEW — Step 1 findings: what's broken in pending_request.py & the session-save flow
├── requirements.txt              # + apscheduler (or chosen scheduling lib), cryptography
├── .env.example                  # + SCHEDULER_TIMEZONE, DEFAULT_PLAN, QUOTA_RESET_HOUR, CACHE_TTL_SECONDS, SESSION_ENCRYPTION_KEY
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## MongoDB collections (via `database.py`)

| Collection      | Status | Purpose                                                        |
|------------------|--------|------------------------------------------------------------------|
| `channels`       | existing | Per-channel settings (auto-approve, captcha, welcome/goodbye) |
| `users`          | existing | Registered bot users (for broadcast)                          |
| `join_requests`  | existing | Pending/processed join request log                            |
| `schedules`      | **NEW**  | Scheduled approval jobs: chat_id, limit, run_at, tz, status    |
| `plans`          | **NEW**  | Plan tier definitions: limits + feature flags                 |
| `quotas`         | **NEW**  | Per-user daily/weekly/monthly usage counters + reset timestamps |
| `sessions`       | **NEW**  | Encrypted per-user Telegram session strings (Fernet — never plaintext) |
| `stats`          | existing | Global/per-chat analytics, extended for engine trust score    |

## New env vars

| Variable | Description | Example |
|---|---|---|
| `SCHEDULER_TIMEZONE` | Default timezone for `/schedule` when user doesn't pick one | `UTC` |
| `DEFAULT_PLAN` | Plan tier assigned to new users | `FREE` |
| `QUOTA_RESET_HOUR` | Local hour at which daily quota resets | `0` |
| `CACHE_TTL_SECONDS` | TTL for `core/cache.py` channel-settings cache | `300` |
| `SESSION_ENCRYPTION_KEY` | Fernet key encrypting stored session strings — bot refuses to boot without it | `<output of Fernet.generate_key()>` |

## Feature → module map

| Target feature (from spec)        | Primary module(s) |
|---|---|
| `/approveall`, `/queue` (rebuild — currently broken) | `plugins/pending_request.py`, `core/queue_manager.py` |
| `/schedule`                       | `plugins/schedule.py`, `core/scheduler.py` |
| `/managechnls` (welcome/goodbye, live preview, per-user perms) | `plugins/managechnls.py`, `plugins/leave.py`, `core/permissions.py`, `core/cache.py` |
| Plan & quota dashboard, lifetime stats, engine trust | `plugins/plan.py`, `core/quota.py`, `plugins/stats.py` |
| Secure "save session" login (`/login`, `/logout`, `/sessions`) | `plugins/session.py`, `core/session_manager.py` |
| Flood protection / stability      | `core/recovery.py`, existing token-bucket in `join_request.py` |