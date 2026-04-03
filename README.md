# 🖥 Telegram Hosting Bot (Telethon)

A Telegram bot that acts as a mini hosting server — lets users add GitHub/GitLab repos and deploy/stop them directly from Telegram.

---

## ✨ Features

- `/start` — Dashboard showing all added repos as buttons (🟢 running / 🔴 stopped)
- **Add Repo** — Paste any Git clone URL; the bot extracts the name automatically
- **Deploy** — Clones the repo and starts it (auto-detects start file)
- **Stop** — Kills the running process cleanly
- **View Logs** — Shows last 30 lines of output logs
- **Remove** — Stops and removes the repo from your dashboard
- Data persisted in `repos.json` across restarts

---

## 🚀 Setup

### 1. Get credentials
- **API_ID & API_HASH** → https://my.telegram.org → App API
- **BOT_TOKEN** → Talk to [@BotFather](https://t.me/BotFather) → `/newbot`

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env with your API_ID, API_HASH, BOT_TOKEN
export $(cat .env | xargs)
```

### 4. Run
```bash
python bot.py
```

---

## 📁 Supported Start Files

The bot auto-detects which file to run in this order:

| File | Command |
|------|---------|
| `start.sh` | `bash start.sh` |
| `run.sh` | `bash run.sh` |
| `main.py` | `python3 main.py` |
| `app.py` | `python3 app.py` |
| `index.js` | `node index.js` |

---

## 🗂 File Structure

```
hosting_bot/
├── bot.py           # Main bot code
├── requirements.txt
├── .env.example
├── repos.json       # Auto-created, stores user data
└── deployments/     # Auto-created, stores cloned repos
    └── {user_id}/
        └── {repo_name}/
            └── deploy.log
```

---

## 💬 Commands

| Command | Description |
|---------|-------------|
| `/start` | Open hosting dashboard |
| `/help` | Show help message |
| `/add <git_url>` | Quickly add a repo |
| `/list` | List all repos with status |
| `/logs <repo_name>` | View logs for a repo |

---

## 🔒 Notes

- Each user's repos and deployments are isolated by `user_id`
- Processes are started in new sessions (`start_new_session=True`) so they survive in background
- Logs are written to `deployments/{user_id}/{repo_name}/deploy.log`
- Stop uses `SIGTERM` on the process group for clean shutdown
