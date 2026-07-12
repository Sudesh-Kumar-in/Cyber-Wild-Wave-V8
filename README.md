# 🔥 CYBER WILD WAVE

> **Production-ready Premium OSINT Telegram Bot** with subscription system, admin panel, 7 search types, payment verification, and full auto-cleanup chat experience.

---

## Features

| Feature | Description |
|---|---|
| 📱 Number Lookup | Mobile number intelligence |
| 📞 Telegram Lookup | Telegram user data |
| 🪪 Aadhaar Lookup | Aadhaar linked data |
| 👨‍👩‍👧‍👦 Family Lookup | Family member data |
| 📍 Pincode Lookup | Area & post office info |
| 🏦 IFSC Lookup | Bank branch details |
| 🚗 Vehicle Lookup | Vehicle registration info |
| 💎 Premium System | Plans, key activation, payment verification |
| 👑 Admin Panel | Full management (ban, broadcast, stats, logs) |
| 🧹 Auto Cleanup | Only user query + result remain in chat |
| 📊 Analytics | Live stats, search logs, export |
| ⏸ Freeze System | Pause/resume premium timers |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Run

```bash
python main.py
```

---

## Environment Variables

All API URLs support the `{query}` placeholder, which is replaced at runtime with the user's search input.

**Example:** `https://api.example.com/lookup?q={query}`

No URLs or API keys are ever hardcoded. All values must be set via environment variables, `.env` file, Railway Variables, Render Environment Variables, or Replit Secrets.

If a lookup API is not configured, the bot logs a warning and continues running — that lookup type will return no results until the variable is set.

### Required

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram Bot token from @BotFather |
| `ADMIN_IDS` | Comma-separated Telegram user IDs for admins (e.g. `123456789,987654321`) |
| `SESSION_SECRET` | Random secret string for session security |

### Per-Service API Config

Each lookup service has its own URL and key. Both are optional — the bot runs safely without them.

| Service | URL Variable | Key Variable |
|---|---|---|
| 📱 Number Lookup | `NUMBER_API_URL` | `NUMBER_API_KEY` |
| 🪪 Aadhaar Lookup | `AADHAAR_API_URL` | `AADHAAR_API_KEY` |
| 👨‍👩‍👧‍👦 Family Lookup | `FAMILY_API_URL` | `FAMILY_API_KEY` |
| 🏦 IFSC Lookup | `IFSC_API_URL` | `IFSC_API_KEY` |
| 📍 Pincode Lookup | `PINCODE_API_URL` | `PINCODE_API_KEY` |
| 📞 Telegram Lookup | `TELEGRAM_API_URL` | `TELEGRAM_API_KEY` |
| 🚗 Vehicle Lookup | `VEHICLE_API_URL` | `VEHICLE_API_KEY` |

### Optional Settings

| Variable | Default | Description |
|---|---|---|
| `DATABASE_PATH` | `bot.db` | SQLite database file path |
| `REQUIRED_CHANNEL` | _(blank)_ | Channel username to force-join (e.g. `@mychannel`). Leave blank to disable. |
| `CHANNEL_INVITE_LINK` | _(blank)_ | Invite link shown to users who haven't joined |
| `FREE_DAILY_SEARCHES` | `5` | Free searches per user per day |
| `RATE_LIMIT_SECONDS` | `3` | Minimum seconds between requests per user |
| `CACHE_TTL` | `300` | API response cache TTL in seconds |

---

## Project Structure

```
bot/
├── main.py                  # Entry point & handler routing
├── config.py                # All configuration & env loading
├── database.py              # Async SQLite database layer
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
│
├── handlers/
│   ├── start.py             # /start, welcome, main menu buttons
│   ├── search.py            # Search keyboard flow + cleanup
│   ├── premium.py           # Premium plans, key redemption
│   ├── admin.py             # Admin panel (all admin actions)
│   ├── account.py           # My Account view
│   └── payment.py           # Screenshot upload & approval
│
├── keyboards/
│   ├── main_kb.py           # User / Admin / Search keyboards
│   ├── admin_kb.py          # Admin inline keyboards
│   ├── premium_kb.py        # Premium inline keyboards
│   └── search_kb.py         # Search back-button keyboard
│
├── services/
│   └── api_service.py       # OSINT API calls (aiohttp, per-service keys)
│
├── utils/
│   ├── formatters.py        # Result text formatters
│   ├── helpers.py           # Shared utilities
│   ├── logger.py            # Structured logging setup
│   ├── msg_tracker.py       # Bot message cleanup tracker
│   └── rate_limiter.py      # Per-user rate limiting
│
├── payment_qr/
│   └── qr.jpg               # Place your payment QR code here
│
└── assets/screenshots/      # Payment screenshots (auto-created)
```

---

## Deployment

### Railway

1. Push to GitHub
2. New Project → Deploy from GitHub repo
3. Add all environment variables in **Railway → Variables**
4. Deploy

### Render

1. New Web Service → connect GitHub repo
2. Build command: `pip install -r requirements.txt`
3. Start command: `python main.py`
4. Add all environment variables in **Render → Environment**
5. Deploy

### Replit

Add all variables in **Replit → Secrets** panel.

### VPS

```bash
cp .env.example .env
# Fill in .env, then:
screen -S cww-bot
python main.py
# Ctrl+A, D to detach
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

---

## Admin Commands

| Command / Button | Action |
|---|---|
| `/start` | Open bot with correct keyboard |
| `/admin` | Open admin panel (admin only) |
| 👑 Admin Panel | Switch to admin submenu keyboard |
| 🔙 Back | Return to main keyboard |
| 💎 Grant Premium | Grant premium to a user |
| 🚫 Revoke Premium | Revoke premium |
| 📢 Broadcast | Send message to all users |
| 🔐 Ban User | Ban a user by ID |
| ✅ Unban User | Unban a user |
| 📊 Live Statistics | View live bot stats |
| 📝 Logs | View recent search logs |
| ⚡ Server Status | CPU, RAM, disk usage |
| 📂 Export Users | Download user list as TXT |
| 🔄 Lifetime Update | View premium time report |
| ⏸ Freeze (Bot Control) | Pause all premium timers |

---

## Premium Plans

| Plan | Price |
|---|---|
| 1 Day | ₹49 |
| 3 Days | ₹99 |
| 7 Days | ₹149 |
| 15 Days | ₹199 |
| 1 Month | ₹299 |
| 2 Months | ₹449 |
| 3 Months | ₹599 |
| 6 Months | ₹799 |
| 1 Year | ₹1199 |

---

## License

Private. All rights reserved. © CYBER WILD WAVE
