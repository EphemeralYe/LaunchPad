"""
Telethon Hosting Bot
A Telegram bot that lets users manage and deploy GitHub repositories.
"""

import asyncio
import os
import subprocess
import json
import logging
from pathlib import Path
from telethon import TelegramClient, events, Button
from telethon.tl.types import Message

# ─── CONFIG ────────────────────────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", 767371))          # From https://my.telegram.org
API_HASH = os.environ.get("API_HASH", "1a13288b823e1ac0db1d8c3dfb49b95a")           # From https://my.telegram.org
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7880763749:AAEq8czTTs5YHXppwpFVGR1_rLbxFyD9Xio")         # From @BotFather
DEPLOY_DIR = Path(os.environ.get("DEPLOY_DIR", "./deployments"))
DATA_FILE = Path("repos.json")                       # Persists user repo data
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

# ─── DATA PERSISTENCE ──────────────────────────────────────────────────────────

def load_data() -> dict:
    """Load user data from JSON file."""
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}


def save_data(data: dict):
    """Save user data to JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user_repos(user_id: int) -> dict:
    """Get repos for a specific user. Returns {repo_name: {url, status, pid}}"""
    data = load_data()
    return data.get(str(user_id), {})


def set_user_repo(user_id: int, repo_name: str, info: dict):
    """Set/update a repo entry for a user."""
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid][repo_name] = info
    save_data(data)


def delete_user_repo(user_id: int, repo_name: str):
    """Remove a repo entry for a user."""
    data = load_data()
    uid = str(user_id)
    if uid in data and repo_name in data[uid]:
        del data[uid][repo_name]
        save_data(data)

# ─── DEPLOYMENT HELPERS ────────────────────────────────────────────────────────

def is_running(pid: int) -> bool:
    """Check if a process is still running."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def clone_repo(repo_url: str, dest: Path) -> tuple[bool, str]:
    """Clone a git repo. Returns (success, message)."""
    if dest.exists():
        return True, "Already cloned"
    result = subprocess.run(
        ["git", "clone", repo_url, str(dest)],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode == 0:
        return True, "Cloned successfully"
    return False, result.stderr.strip()


def start_process(repo_dir: Path) -> tuple[bool, str, int]:
    """
    Start a repo. Looks for: start.sh → run.sh → main.py → app.py → index.js
    Returns (success, message, pid)
    """
    starters = [
        (["bash", "start.sh"], "start.sh"),
        (["bash", "run.sh"], "run.sh"),
        (["python3", "main.py"], "main.py"),
        (["python3", "app.py"], "app.py"),
        (["node", "index.js"], "index.js"),
    ]

    for cmd, filename in starters:
        if (repo_dir / filename).exists():
            log_path = repo_dir / "deploy.log"
            with open(log_path, "a") as log_file:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(repo_dir),
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True
                )
            return True, f"Started via `{filename}` (PID: {proc.pid})", proc.pid

    return False, "No start file found (start.sh / run.sh / main.py / app.py / index.js)", 0


def stop_process(pid: int) -> tuple[bool, str]:
    """Kill a running process."""
    if not pid or not is_running(pid):
        return False, "Process not running"
    try:
        import signal
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True, f"Process {pid} stopped"
    except Exception as e:
        return False, str(e)

# ─── UI BUILDERS ───────────────────────────────────────────────────────────────

def build_main_menu(user_id: int):
    """Build the /start menu with repo buttons."""
    repos = get_user_repos(user_id)
    buttons = []

    if repos:
        for repo_name, info in repos.items():
            pid = info.get("pid", 0)
            running = is_running(pid)
            status_icon = "🟢" if running else "🔴"
            buttons.append([Button.inline(f"{status_icon} {repo_name}", data=f"repo:{repo_name}")])

    buttons.append([Button.inline("➕ Add Repository", data="add_repo")])
    return buttons


def build_repo_menu(repo_name: str, is_running_: bool):
    """Build the repo management menu."""
    buttons = []
    if is_running_:
        buttons.append([Button.inline("⏹ Stop Deployment", data=f"stop:{repo_name}")])
    else:
        buttons.append([Button.inline("🚀 Deploy", data=f"deploy:{repo_name}")])

    buttons.append([Button.inline("📋 View Logs", data=f"logs:{repo_name}")])
    buttons.append([Button.inline("🗑 Remove Repo", data=f"remove:{repo_name}")])
    buttons.append([Button.inline("◀️ Back", data="back")])
    return buttons

# ─── BOT INIT ──────────────────────────────────────────────────────────────────

client = TelegramClient("hosting_bot", API_ID, API_HASH)

# Track users waiting to send a repo URL  {user_id: True}
waiting_for_url: dict[int, bool] = {}

# ─── HANDLERS ──────────────────────────────────────────────────────────────────

@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event: Message):
    user_id = event.sender_id
    repos = get_user_repos(user_id)

    if repos:
        text = (
            "🖥 **Hosting Dashboard**\n\n"
            "Your repositories are listed below.\n"
            "🟢 = Running  |  🔴 = Stopped\n\n"
            "Tap a repo to manage it, or add a new one."
        )
    else:
        text = (
            "🖥 **Hosting Dashboard**\n\n"
            "You haven't added any repositories yet.\n"
            "Tap **➕ Add Repository** to get started!"
        )

    await event.respond(text, buttons=build_main_menu(user_id))


@client.on(events.NewMessage(pattern="/help"))
async def help_handler(event: Message):
    await event.respond(
        "**🤖 Hosting Bot Help**\n\n"
        "• `/start` — Open your hosting dashboard\n"
        "• `/add <git_url>` — Quickly add a repository\n"
        "• `/list` — List all your repos\n"
        "• `/logs <repo_name>` — View last 30 lines of logs\n\n"
        "**How it works:**\n"
        "1. Add a GitHub/GitLab repo URL\n"
        "2. Tap the repo → Deploy\n"
        "3. The bot clones & starts your app\n"
        "4. Stop anytime from the dashboard\n\n"
        "**Supported start files:**\n"
        "`start.sh`, `run.sh`, `main.py`, `app.py`, `index.js`"
    )


@client.on(events.NewMessage(pattern=r"/add (.+)"))
async def quick_add_handler(event: Message):
    repo_url = event.pattern_match.group(1).strip()
    await process_add_repo(event, event.sender_id, repo_url)


@client.on(events.NewMessage(pattern="/list"))
async def list_handler(event: Message):
    user_id = event.sender_id
    repos = get_user_repos(user_id)
    if not repos:
        await event.respond("📭 You have no repositories added yet.")
        return

    lines = ["📦 **Your Repositories:**\n"]
    for name, info in repos.items():
        pid = info.get("pid", 0)
        status = "🟢 Running" if is_running(pid) else "🔴 Stopped"
        lines.append(f"• `{name}` — {status}")
    await event.respond("\n".join(lines))


@client.on(events.NewMessage(pattern=r"/logs (.+)"))
async def logs_cmd_handler(event: Message):
    user_id = event.sender_id
    repo_name = event.pattern_match.group(1).strip()
    repos = get_user_repos(user_id)
    if repo_name not in repos:
        await event.respond(f"❌ Repo `{repo_name}` not found.")
        return
    await send_logs(event, user_id, repo_name)


@client.on(events.NewMessage())
async def text_handler(event: Message):
    """Handle repo URL input when bot is waiting for it."""
    user_id = event.sender_id
    if not waiting_for_url.get(user_id):
        return
    if event.text.startswith("/"):
        return

    repo_url = event.text.strip()
    waiting_for_url.pop(user_id, None)
    await process_add_repo(event, user_id, repo_url)


# ─── CALLBACK HANDLERS ─────────────────────────────────────────────────────────

@client.on(events.CallbackQuery(data=b"add_repo"))
async def cb_add_repo(event):
    user_id = event.sender_id
    waiting_for_url[user_id] = True
    await event.edit(
        "📎 **Add a Repository**\n\n"
        "Send me the Git clone URL of your repository.\n\n"
        "Example:\n`https://github.com/username/myapp.git`",
        buttons=[[Button.inline("❌ Cancel", data="cancel_add")]]
    )


@client.on(events.CallbackQuery(data=b"cancel_add"))
async def cb_cancel_add(event):
    user_id = event.sender_id
    waiting_for_url.pop(user_id, None)
    repos = get_user_repos(user_id)
    text = "🖥 **Hosting Dashboard**\n\nOperation cancelled." if not repos else "🖥 **Hosting Dashboard**"
    await event.edit(text, buttons=build_main_menu(user_id))


@client.on(events.CallbackQuery(data=b"back"))
async def cb_back(event):
    user_id = event.sender_id
    repos = get_user_repos(user_id)
    text = "🖥 **Hosting Dashboard**\n\n🟢 = Running  |  🔴 = Stopped" if repos else (
        "🖥 **Hosting Dashboard**\n\nNo repositories yet. Add one below!"
    )
    await event.edit(text, buttons=build_main_menu(user_id))


@client.on(events.CallbackQuery(pattern=b"repo:(.+)"))
async def cb_repo(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("repo:", 1)[1]
    repos = get_user_repos(user_id)

    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True)
        return

    info = repos[repo_name]
    pid = info.get("pid", 0)
    running = is_running(pid)
    status = "🟢 **Running**" if running else "🔴 **Stopped**"

    await event.edit(
        f"📦 **{repo_name}**\n\n"
        f"Status: {status}\n"
        f"URL: `{info.get('url', 'N/A')}`\n\n"
        "Choose an action:",
        buttons=build_repo_menu(repo_name, running)
    )


@client.on(events.CallbackQuery(pattern=b"deploy:(.+)"))
async def cb_deploy(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("deploy:", 1)[1]
    repos = get_user_repos(user_id)

    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True)
        return

    await event.edit(f"⚙️ Deploying **{repo_name}**...\n\nThis may take a moment.")

    info = repos[repo_name]
    repo_url = info["url"]
    repo_dir = DEPLOY_DIR / str(user_id) / repo_name

    # Clone if not already done
    cloned, clone_msg = clone_repo(repo_url, repo_dir)
    if not cloned:
        await event.edit(
            f"❌ **Clone Failed**\n\n```\n{clone_msg}\n```",
            buttons=[[Button.inline("◀️ Back", data=f"repo:{repo_name}")]]
        )
        return

    # Start process
    started, start_msg, pid = start_process(repo_dir)
    if started:
        info["pid"] = pid
        info["status"] = "running"
        set_user_repo(user_id, repo_name, info)
        await event.edit(
            f"✅ **{repo_name}** Deployed!\n\n{start_msg}",
            buttons=build_repo_menu(repo_name, True)
        )
    else:
        await event.edit(
            f"❌ **Start Failed**\n\n{start_msg}\n\n"
            "Make sure your repo has a `start.sh`, `run.sh`, `main.py`, `app.py`, or `index.js`.",
            buttons=build_repo_menu(repo_name, False)
        )


@client.on(events.CallbackQuery(pattern=b"stop:(.+)"))
async def cb_stop(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("stop:", 1)[1]
    repos = get_user_repos(user_id)

    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True)
        return

    info = repos[repo_name]
    pid = info.get("pid", 0)
    stopped, msg = stop_process(pid)

    info["pid"] = 0
    info["status"] = "stopped"
    set_user_repo(user_id, repo_name, info)

    status_text = f"✅ Stopped: {msg}" if stopped else f"ℹ️ {msg}"
    await event.edit(
        f"⏹ **{repo_name}**\n\n{status_text}",
        buttons=build_repo_menu(repo_name, False)
    )


@client.on(events.CallbackQuery(pattern=b"logs:(.+)"))
async def cb_logs(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("logs:", 1)[1]
    await send_logs(event, user_id, repo_name, edit=True)


@client.on(events.CallbackQuery(pattern=b"remove:(.+)"))
async def cb_remove(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("remove:", 1)[1]
    repos = get_user_repos(user_id)

    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True)
        return

    # Stop first if running
    info = repos[repo_name]
    pid = info.get("pid", 0)
    if is_running(pid):
        stop_process(pid)

    delete_user_repo(user_id, repo_name)
    await event.answer(f"🗑 {repo_name} removed!", alert=False)
    await event.edit(
        "🖥 **Hosting Dashboard**\n\n🟢 = Running  |  🔴 = Stopped",
        buttons=build_main_menu(user_id)
    )

# ─── HELPERS ───────────────────────────────────────────────────────────────────

async def process_add_repo(event, user_id: int, repo_url: str):
    """Validate URL, extract repo name, and save."""
    if not (repo_url.startswith("http") and ".git" in repo_url or "github.com" in repo_url or "gitlab.com" in repo_url):
        await event.respond(
            "❌ Invalid URL. Please send a valid Git clone URL.\n\n"
            "Example: `https://github.com/user/repo.git`"
        )
        return

    # Extract repo name from URL
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    repos = get_user_repos(user_id)

    if repo_name in repos:
        await event.respond(
            f"⚠️ A repo named `{repo_name}` already exists.\n"
            "Remove it first before adding again.",
            buttons=[[Button.inline("◀️ Dashboard", data="back")]]
        )
        return

    set_user_repo(user_id, repo_name, {"url": repo_url, "pid": 0, "status": "stopped"})
    await event.respond(
        f"✅ **Repository Added!**\n\n"
        f"📦 Name: `{repo_name}`\n"
        f"🔗 URL: `{repo_url}`\n\n"
        "Go to your dashboard to deploy it.",
        buttons=[[Button.inline("🖥 Open Dashboard", data="back")]]
    )


async def send_logs(event, user_id: int, repo_name: str, edit: bool = False):
    """Send last 30 lines of deploy.log for a repo."""
    log_path = DEPLOY_DIR / str(user_id) / repo_name / "deploy.log"
    back_btn = [[Button.inline("◀️ Back", data=f"repo:{repo_name}")]]

    if not log_path.exists():
        text = f"📋 **{repo_name} Logs**\n\nNo logs yet. Deploy the repo first."
    else:
        with open(log_path) as f:
            lines = f.readlines()
        last_lines = "".join(lines[-30:]).strip() or "Log file is empty."
        text = f"📋 **{repo_name} — Last 30 lines:**\n\n```\n{last_lines[-3500:]}\n```"

    if edit:
        await event.edit(text, buttons=back_btn)
    else:
        await event.respond(text, buttons=back_btn)

# ─── MAIN ──────────────────────────────────────────────────────────────────────

async def main():
    logger.info("Starting Hosting Bot...")
    await client.start(bot_token=BOT_TOKEN)
    logger.info("Bot is running! Press Ctrl+C to stop.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        print("❌ Missing credentials! Set API_ID, API_HASH, BOT_TOKEN as environment variables.")
        exit(1)
    asyncio.run(main())
