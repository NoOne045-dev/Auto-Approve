<div align="center">
  # ⚡ Telegram Auto-Approval & Join Request Bot
  
  **A state-of-the-art Telegram bot built with [Kurigram](https://github.com/KurimuzonAkuma/kurigram) and Async MongoDB (Motor) for auto-approving join requests with custom rich-media welcome messages, CAPTCHA verification gateways, mass backlog management, and modern inline UI/UX.**

  <p>
    <a href="https://github.com"><img src="https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python" alt="Python 3.10+"/></a>
    <a href="https://github.com/KurimuzonAkuma/kurigram"><img src="https://img.shields.io/badge/Framework-Kurigram%20(Pyrogram)-orange.svg?style=flat-square" alt="Kurigram"/></a>
    <a href="https://www.mongodb.com"><img src="https://img.shields.io/badge/Database-MongoDB%20(Motor)-green.svg?style=flat-square&logo=mongodb" alt="MongoDB"/></a>
    <a href="https://www.docker.com"><img src="https://img.shields.io/badge/Docker-Ready-2496ED.svg?style=flat-square&logo=docker" alt="Docker"/></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="License"/></a>
  </p>
</div>

---

## 🌟 Key Highlights

- ⚡ **Multi-Channel & Multi-Group Support**: Automatically process join requests across unlimited channels and supergroups.
- 🛡️ **Anti-Spam & CAPTCHA Verification Gateways**: Verify users in DM via 1-Click Human Verification, Math arithmetic challenges, or Emoji matching before approving.
- ⏱️ **Flood Protection & Token-Bucket Rate Limiter**: Safely handles viral surges (10,000+ requests) without crashing from Telegram `FloodWait` (420) exceptions.
- 🎨 **Dynamic Rich-Media Welcome Engine**:
  - Support for Photos, HD Videos, GIFs/Animations, and Documents.
  - Dynamic template variables (`{mention}`, `{first_name}`, `{chat_title}`, `{chat_id}`, `{date}`, `{invite_link}`).
  - Interactive custom URL inline buttons (`[Button | https://example.com]`).
- ⚡ **Mass Backlog Actions**:
  - Approve all existing pending requests with live progress bar.
  - Purge / Decline all spam join requests.
  - Export pending join requests to CSV.
- 📢 **High-Speed Broadcast Suite**: Send announcements to all subscribers with live progress meter, ETA, and auto-cleanup of dead accounts.
- 🟢/🔴 **Modern Interactive Inline UI/UX**: Clean toggle switches, paginated channel lists, and zero chat flooding.
- 🗄️ **Async MongoDB Engine**: Scalable document storage using Motor with auto-indexing.

---

## 📂 Simplified Universal Project Structure

```
Auto-Approve-Bot/
├── main.py              # Main application entry point & lifecycle runner
├── config.py            # Centralized settings & access control
├── database.py          # Pure Async MongoDB driver (Motor)
├── helpers.py           # Keyboards, formatters, captcha & spam checks
├── plugins/             # Clean modular plugin handlers
│   ├── start.py         # /start, /help, /ping & main menu
│   ├── join_request.py  # ChatJoinRequest listener & approval queue worker
│   ├── captcha.py       # DM captcha verification callbacks
│   ├── admin.py         # Channel control center & toggle switches
│   ├── welcome.py       # Custom welcome message, media & button editor
│   ├── mass.py          # Bulk approvals, declines & CSV export
│   ├── broadcast.py     # High-speed broadcast suite with live progress
│   └── stats.py         # Global and chat analytics
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template
├── Dockerfile           # Docker container configuration
├── docker-compose.yml   # Multi-container orchestration
└── README.md            # Documentation
```

---

## 🚀 Quick Deployment Guide

### Option 1: Direct VPS / Local Deployment (Python 3.10+)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/Auto-Approve-Bot.git
   cd Auto-Approve-Bot
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate   # Linux/macOS
   .\venv\Scripts\activate    # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your BOT_TOKEN, API_ID, API_HASH, OWNER_ID, and MONGO_URL
   ```

5. **Start the bot:**
   ```bash
   python main.py
   ```

---

### Option 2: Docker Compose

1. **Configure `.env` file:**
   ```bash
   cp .env.example .env
   ```

2. **Launch with Docker Compose:**
   ```bash
   docker-compose up -d --build
   ```

3. **Check live logs:**
   ```bash
   docker-compose logs -f bot
   ```

---

## ⚙️ Environment Variables Reference

| Variable | Required | Description | Example |
| :--- | :---: | :--- | :--- |
| `BOT_TOKEN` | **Yes** | Telegram Bot Token from [@BotFather](https://t.me/BotFather) | `123456:ABC-DEF...` |
| `API_ID` | **Yes** | Telegram API ID from [my.telegram.org](https://my.telegram.org/apps) | `12345678` |
| `API_HASH` | **Yes** | Telegram API Hash from [my.telegram.org](https://my.telegram.org/apps) | `0123456789abcdef...` |
| `OWNER_ID` | **Yes** | Telegram User ID of Bot Owner | `123456789` |
| `MONGO_URL` | **Yes** | MongoDB Connection URI (e.g. MongoDB Atlas) | `mongodb+srv://...` |
| `ADMINS` | No | Additional Admin User IDs (separated by spaces) | `987654321` |
| `MAX_APPROVALS_PER_SECOND` | No | Token bucket approval throughput limit (default `25`) | `25` |
| `ENABLE_CAS_CHECK` | No | Enable Combot Anti-Spam lookup (default `true`) | `true` |
| `LOG_LEVEL` | No | Logging verbosity (`INFO`, `DEBUG`, `WARNING`) | `INFO` |

---

## 📖 Bot Commands Reference

| Command | Permission | Description |
| :--- | :--- | :--- |
| `/start` | All Users | Open the main menu, view bot status & quick links |
| `/help` | All Users | Step-by-step setup guide on adding bot to channels |
| `/admin` | Admins | Open the interactive channel settings control center |
| `/stats` | All Users | View global approval statistics & active users |
| `/ping` | All Users | Check bot latency and server uptime |
| `/broadcast` | Master Admin | Reply to any message to broadcast to all registered users |

---

## 📜 License

Distributed under the **MIT License**. See `LICENSE` for more information.

