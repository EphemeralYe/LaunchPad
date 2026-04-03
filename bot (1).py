"""
Telethon Hosting Bot
A Telegram bot that acts as a mini hosting server.
Automatically creates a virtualenv and installs requirements before deploying.
"""

import asyncio
import os
import signal
import subprocess
import json
import logging
import sys
import shutil
from pathlib import Path
from telethon import TelegramClient, events, Button

# ─── CONFIG ────────────────────────────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", 767371))          # From https://my.telegram.org
API_HASH = os.environ.get("API_HASH", "1a13288b823e1ac0db1d8c3dfb49b95a")           # From https://my.telegram.org
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7880763749:AAEq8czTTs5YHXppwpFVGR1_rLbxFyD9Xio")         # From @BotFather
DEPLOY_DIR = Path(os.environ.get("DEPLOY_DIR", "./deployments"))
DATA_FILE  = Path("repos.json")
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

# ─── DATA PERSISTENCE ──────────────────────────────────────────────────────────

def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user_repos(user_id: int) -> dict:
    return load_data().get(str(user_id), {})

def set_user_repo(user_id: int, repo_name: str, info: dict):
    data = load_data()
    data.setdefault(str(user_id), {})[repo_name] = info
    save_data(data)

def delete_user_repo(user_id: int, repo_name: str):
    data = load_data()
    uid = str(user_id)
    if uid in data and repo_name in data[uid]:
        del data[uid][repo_name]
        save_data(data)

# ─── PROCESS HELPERS ───────────────────────────────────────────────────────────

def is_running(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False

def stop_process(pid: int) -> tuple[bool, str]:
    if not pid or not is_running(pid):
        return False, "Process not running"
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True, f"Process {pid} stopped"
    except Exception as e:
        return False, str(e)

# ─── GIT HELPERS ───────────────────────────────────────────────────────────────

def clone_or_pull(repo_url: str, dest: Path, log_file) -> tuple[bool, str]:
    if dest.exists():
        log_file.write("[git] Repo exists - pulling latest changes...\n"); log_file.flush()
        r = subprocess.run(["git", "-C", str(dest), "pull"], capture_output=True, text=True, timeout=120)
        msg = r.stdout.strip() or r.stderr.strip()
        log_file.write(f"[git] {msg}\n"); log_file.flush()
        return r.returncode == 0, msg

    log_file.write(f"[git] Cloning {repo_url}...\n"); log_file.flush()
    r = subprocess.run(["git", "clone", repo_url, str(dest)], capture_output=True, text=True, timeout=180)
    if r.returncode == 0:
        log_file.write("[git] Cloned successfully.\n"); log_file.flush()
        return True, "Cloned successfully"
    log_file.write(f"[git] Clone failed:\n{r.stderr}\n"); log_file.flush()
    return False, r.stderr.strip()

# ─── VENV & DEP HELPERS ────────────────────────────────────────────────────────

def venv_python(repo_dir: Path) -> Path:
    return repo_dir / ".venv" / "bin" / "python"

def venv_pip(repo_dir: Path) -> Path:
    return repo_dir / ".venv" / "bin" / "pip"

def create_venv(repo_dir: Path, log_file) -> tuple[bool, str]:
    venv_dir = repo_dir / ".venv"
    if venv_dir.exists():
        log_file.write("[venv] Virtual environment already exists - skipping.\n"); log_file.flush()
        return True, "Already exists"
    log_file.write("[venv] Creating virtual environment...\n"); log_file.flush()
    r = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        log_file.write("[venv] Virtual environment created.\n"); log_file.flush()
        return True, "Created"
    log_file.write(f"[venv] Failed:\n{r.stderr}\n"); log_file.flush()
    return False, r.stderr.strip()

def detect_req_files(repo_dir: Path) -> list[Path]:
    candidates = [
        "requirements.txt", "requirements-prod.txt",
        "requirements/base.txt", "requirements/prod.txt",
        "requirements/common.txt", "pyproject.toml", "setup.py",
    ]
    return [repo_dir / c for c in candidates if (repo_dir / c).exists()]

def install_python_deps(repo_dir: Path, log_file) -> tuple[bool, str]:
    pip = venv_pip(repo_dir)
    req_files = detect_req_files(repo_dir)
    if not req_files:
        log_file.write("[pip] No requirements file found - skipping.\n"); log_file.flush()
        return True, "No requirements file"

    log_file.write("[pip] Upgrading pip...\n"); log_file.flush()
    subprocess.run([str(pip), "install", "--upgrade", "pip", "--quiet"], capture_output=True, timeout=60)

    installed = []
    for req_file in req_files:
        rel = req_file.relative_to(repo_dir)
        log_file.write(f"[pip] Installing from {rel}...\n"); log_file.flush()
        cmd = ([str(pip), "install", ".", "--quiet"]
               if req_file.name in ("pyproject.toml", "setup.py")
               else [str(pip), "install", "-r", str(req_file), "--quiet"])
        r = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            log_file.write(f"[pip] {rel} installed OK.\n"); log_file.flush()
            installed.append(str(rel))
        else:
            err = (r.stderr or r.stdout).strip()[-600:]
            log_file.write(f"[pip] {rel} failed:\n{err}\n"); log_file.flush()
            return False, f"Failed on `{rel}`:\n```\n{err}\n```"
    return True, "Installed: " + ", ".join(f"`{f}`" for f in installed)

def install_node_deps(repo_dir: Path, log_file) -> tuple[bool, str]:
    if not (repo_dir / "package.json").exists():
        return True, "No package.json - skipped"
    log_file.write("[npm] Running npm install...\n"); log_file.flush()
    r = subprocess.run(["npm", "install", "--silent"], cwd=str(repo_dir), capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        log_file.write("[npm] npm install OK.\n"); log_file.flush()
        return True, "npm install OK"
    err = r.stderr.strip()[-500:]
    log_file.write(f"[npm] npm install failed:\n{err}\n"); log_file.flush()
    return False, f"npm install failed:\n```\n{err}\n```"

# ─── START PROCESS ─────────────────────────────────────────────────────────────

def start_process(repo_dir: Path, log_file) -> tuple[bool, str, int]:
    py = str(venv_python(repo_dir)) if venv_python(repo_dir).exists() else "python3"
    starters = [
        (["bash", "start.sh"], "start.sh"),
        (["bash", "run.sh"],   "run.sh"),
        ([py, "main.py"],      "main.py"),
        ([py, "app.py"],       "app.py"),
        (["node", "index.js"], "index.js"),
    ]
    for cmd, fname in starters:
        if (repo_dir / fname).exists():
            log_file.write(f"[start] Launching via {fname}...\n"); log_file.flush()
            proc = subprocess.Popen(cmd, cwd=str(repo_dir), stdout=log_file, stderr=log_file, start_new_session=True)
            log_file.write(f"[start] PID {proc.pid}\n"); log_file.flush()
            return True, f"Started via `{fname}` (PID: {proc.pid})", proc.pid
    log_file.write("[start] No start file found.\n"); log_file.flush()
    return False, "No start file found.\nRepo must have: `start.sh`, `run.sh`, `main.py`, `app.py`, or `index.js`", 0

# ─── DEPLOY PIPELINE ───────────────────────────────────────────────────────────

async def run_deploy_pipeline(event, user_id: int, repo_name: str, repo_url: str) -> tuple[bool, str, int]:
    repo_dir = DEPLOY_DIR / str(user_id) / repo_name
    repo_dir.mkdir(parents=True, exist_ok=True)
    log_path = repo_dir / "deploy.log"

    with open(log_path, "a") as lf:
        lf.write(f"\n{'='*50}\n[deploy] Pipeline start: {repo_name}\n{'='*50}\n")

        # 1. Git
        await event.edit(f"⚙️ **Deploying {repo_name}**\n\n`[1/4]` 📥 Cloning / pulling repo...")
        ok, msg = clone_or_pull(repo_url, repo_dir, lf)
        if not ok:
            return False, f"**Git failed:**\n{msg}", 0

        is_python = bool(detect_req_files(repo_dir)) or any((repo_dir / f).exists() for f in ["main.py", "app.py", "start.sh", "run.sh"])
        is_node   = (repo_dir / "package.json").exists()

        # 2. Venv
        if is_python:
            await event.edit(f"⚙️ **Deploying {repo_name}**\n\n`[2/4]` 🐍 Creating virtual environment...")
            ok, msg = create_venv(repo_dir, lf)
            if not ok:
                return False, f"**Venv failed:**\n{msg}", 0
        else:
            lf.write("[venv] Node project - skipping venv.\n")

        # 3. Install deps
        await event.edit(f"⚙️ **Deploying {repo_name}**\n\n`[3/4]` 📦 Installing dependencies...")
        if is_python:
            ok, msg = install_python_deps(repo_dir, lf)
            if not ok:
                return False, f"**pip install failed:**\n{msg}", 0
        if is_node:
            ok, msg = install_node_deps(repo_dir, lf)
            if not ok:
                return False, f"**npm install failed:**\n{msg}", 0

        # 4. Start
        await event.edit(f"⚙️ **Deploying {repo_name}**\n\n`[4/4]` 🚀 Starting application...")
        ok, msg, pid = start_process(repo_dir, lf)
        if not ok:
            return False, msg, 0

    summary = "\n".join(filter(None, [
        "📥 Repo ready",
        "🐍 Venv + deps installed" if is_python else "",
        "📦 npm install done" if is_node else "",
        f"🚀 {msg}",
    ]))
    return True, summary, pid

# ─── UI BUILDERS ───────────────────────────────────────────────────────────────

def build_main_menu(user_id: int):
    repos = get_user_repos(user_id)
    buttons = []
    for repo_name, info in repos.items():
        icon = "🟢" if is_running(info.get("pid", 0)) else "🔴"
        buttons.append([Button.inline(f"{icon} {repo_name}", data=f"repo:{repo_name}")])
    buttons.append([Button.inline("➕ Add Repository", data="add_repo")])
    return buttons

def build_repo_menu(repo_name: str, running: bool):
    return [
        [Button.inline("⏹ Stop", data=f"stop:{repo_name}")] if running else [Button.inline("🚀 Deploy", data=f"deploy:{repo_name}")],
        [Button.inline("🔄 Redeploy (pull + reinstall)", data=f"redeploy:{repo_name}")],
        [Button.inline("📋 View Logs", data=f"logs:{repo_name}")],
        [Button.inline("🗑 Remove Repo", data=f"remove:{repo_name}")],
        [Button.inline("◀️ Back", data="back")],
    ]

# ─── BOT CLIENT ────────────────────────────────────────────────────────────────

client = TelegramClient("hosting_bot", API_ID, API_HASH)
waiting_for_url: dict[int, bool] = {}

# ─── COMMAND HANDLERS ──────────────────────────────────────────────────────────

@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    user_id = event.sender_id
    repos = get_user_repos(user_id)
    text = (
        "🖥 **Hosting Dashboard**\n\n🟢 = Running  |  🔴 = Stopped\n\nTap a repo to manage it."
        if repos else
        "🖥 **Hosting Dashboard**\n\nNo repositories yet.\nTap **➕ Add Repository** to get started!"
    )
    await event.respond(text, buttons=build_main_menu(user_id))

@client.on(events.NewMessage(pattern="/help"))
async def help_handler(event):
    await event.respond(
        "**🤖 Hosting Bot Help**\n\n"
        "**Commands:** `/start` · `/add <url>` · `/list` · `/logs <name>`\n\n"
        "**Auto-deploy pipeline:**\n"
        "1. `git clone` or `git pull`\n"
        "2. Create `.venv` virtualenv (Python)\n"
        "3. `pip install` from requirements file(s)\n"
        "   OR `npm install` (Node.js)\n"
        "4. Launch your app\n\n"
        "**Supported launchers:**\n"
        "`start.sh` · `run.sh` · `main.py` · `app.py` · `index.js`\n\n"
        "**Supported requirement files:**\n"
        "`requirements.txt` · `requirements/*.txt` · `pyproject.toml` · `setup.py`"
    )

@client.on(events.NewMessage(pattern=r"/add (.+)"))
async def quick_add_handler(event):
    await process_add_repo(event, event.sender_id, event.pattern_match.group(1).strip())

@client.on(events.NewMessage(pattern="/list"))
async def list_handler(event):
    repos = get_user_repos(event.sender_id)
    if not repos:
        await event.respond("📭 No repositories yet.")
        return
    lines = ["📦 **Your Repositories:**\n"]
    for name, info in repos.items():
        status = "🟢 Running" if is_running(info.get("pid", 0)) else "🔴 Stopped"
        venv = "🐍 venv" if (DEPLOY_DIR / str(event.sender_id) / name / ".venv").exists() else "no venv"
        lines.append(f"• `{name}` — {status} | {venv}")
    await event.respond("\n".join(lines))

@client.on(events.NewMessage(pattern=r"/logs (.+)"))
async def logs_cmd_handler(event):
    user_id = event.sender_id
    repo_name = event.pattern_match.group(1).strip()
    if repo_name not in get_user_repos(user_id):
        await event.respond(f"❌ Repo `{repo_name}` not found.")
        return
    await send_logs(event, user_id, repo_name)

@client.on(events.NewMessage())
async def text_handler(event):
    user_id = event.sender_id
    if not waiting_for_url.get(user_id) or event.text.startswith("/"):
        return
    waiting_for_url.pop(user_id, None)
    await process_add_repo(event, user_id, event.text.strip())

# ─── CALLBACK HANDLERS ─────────────────────────────────────────────────────────

@client.on(events.CallbackQuery(data=b"add_repo"))
async def cb_add_repo(event):
    waiting_for_url[event.sender_id] = True
    await event.edit(
        "📎 **Add a Repository**\n\nSend me your Git clone URL.\n\nExample:\n`https://github.com/user/myapp.git`",
        buttons=[[Button.inline("❌ Cancel", data="cancel_add")]]
    )

@client.on(events.CallbackQuery(data=b"cancel_add"))
async def cb_cancel_add(event):
    waiting_for_url.pop(event.sender_id, None)
    await event.edit("🖥 **Hosting Dashboard**\n\nCancelled.", buttons=build_main_menu(event.sender_id))

@client.on(events.CallbackQuery(data=b"back"))
async def cb_back(event):
    user_id = event.sender_id
    repos = get_user_repos(user_id)
    text = "🖥 **Hosting Dashboard**\n\n🟢 = Running  |  🔴 = Stopped" if repos else "🖥 **Hosting Dashboard**\n\nNo repos yet."
    await event.edit(text, buttons=build_main_menu(user_id))

@client.on(events.CallbackQuery(pattern=b"repo:(.+)"))
async def cb_repo(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("repo:", 1)[1]
    repos = get_user_repos(user_id)
    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True); return
    info = repos[repo_name]
    running = is_running(info.get("pid", 0))
    venv_ready = (DEPLOY_DIR / str(user_id) / repo_name / ".venv").exists()
    await event.edit(
        f"📦 **{repo_name}**\n\n"
        f"Status: {'🟢 **Running**' if running else '🔴 **Stopped**'}\n"
        f"Venv:   {'✅ Ready' if venv_ready else '⚠️ Not installed yet'}\n"
        f"URL:    `{info.get('url', 'N/A')}`\n\nChoose an action:",
        buttons=build_repo_menu(repo_name, running)
    )

@client.on(events.CallbackQuery(pattern=b"deploy:(.+)"))
async def cb_deploy(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("deploy:", 1)[1]
    repos = get_user_repos(user_id)
    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True); return
    info = repos[repo_name]
    ok, detail, pid = await run_deploy_pipeline(event, user_id, repo_name, info["url"])
    if ok:
        info.update({"pid": pid, "status": "running"})
        set_user_repo(user_id, repo_name, info)
        await event.edit(f"✅ **{repo_name}** Deployed!\n\n{detail}", buttons=build_repo_menu(repo_name, True))
    else:
        await event.edit(f"❌ **Deploy Failed**\n\n{detail}", buttons=build_repo_menu(repo_name, False))

@client.on(events.CallbackQuery(pattern=b"redeploy:(.+)"))
async def cb_redeploy(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("redeploy:", 1)[1]
    repos = get_user_repos(user_id)
    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True); return
    info = repos[repo_name]

    if is_running(info.get("pid", 0)):
        stop_process(info["pid"])

    venv_dir = DEPLOY_DIR / str(user_id) / repo_name / ".venv"
    if venv_dir.exists():
        await event.edit(f"🔄 **Redeploying {repo_name}**\n\n`[0/4]` 🗑 Wiping old virtualenv...")
        shutil.rmtree(venv_dir)

    ok, detail, pid = await run_deploy_pipeline(event, user_id, repo_name, info["url"])
    if ok:
        info.update({"pid": pid, "status": "running"})
        set_user_repo(user_id, repo_name, info)
        await event.edit(f"🔄 **{repo_name}** Redeployed!\n\n{detail}", buttons=build_repo_menu(repo_name, True))
    else:
        await event.edit(f"❌ **Redeploy Failed**\n\n{detail}", buttons=build_repo_menu(repo_name, False))

@client.on(events.CallbackQuery(pattern=b"stop:(.+)"))
async def cb_stop(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("stop:", 1)[1]
    repos = get_user_repos(user_id)
    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True); return
    info = repos[repo_name]
    stopped, msg = stop_process(info.get("pid", 0))
    info.update({"pid": 0, "status": "stopped"})
    set_user_repo(user_id, repo_name, info)
    await event.edit(
        f"⏹ **{repo_name}**\n\n{'✅ ' + msg if stopped else 'ℹ️ ' + msg}",
        buttons=build_repo_menu(repo_name, False)
    )

@client.on(events.CallbackQuery(pattern=b"logs:(.+)"))
async def cb_logs(event):
    repo_name = event.data.decode().split("logs:", 1)[1]
    await send_logs(event, event.sender_id, repo_name, edit=True)

@client.on(events.CallbackQuery(pattern=b"remove:(.+)"))
async def cb_remove(event):
    user_id = event.sender_id
    repo_name = event.data.decode().split("remove:", 1)[1]
    repos = get_user_repos(user_id)
    if repo_name not in repos:
        await event.answer("Repo not found!", alert=True); return
    info = repos[repo_name]
    if is_running(info.get("pid", 0)):
        stop_process(info["pid"])
    delete_user_repo(user_id, repo_name)
    await event.answer(f"🗑 {repo_name} removed!")
    await event.edit("🖥 **Hosting Dashboard**", buttons=build_main_menu(user_id))

# ─── SHARED HELPERS ────────────────────────────────────────────────────────────

async def process_add_repo(event, user_id: int, repo_url: str):
    if not ("github.com" in repo_url or "gitlab.com" in repo_url or ".git" in repo_url):
        await event.respond("❌ Invalid URL. Example: `https://github.com/user/repo.git`")
        return
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    if repo_name in get_user_repos(user_id):
        await event.respond(f"⚠️ `{repo_name}` already exists. Remove it first.", buttons=[[Button.inline("◀️ Dashboard", data="back")]])
        return
    set_user_repo(user_id, repo_name, {"url": repo_url, "pid": 0, "status": "stopped"})
    await event.respond(
        f"✅ **Repository Added!**\n\n📦 `{repo_name}`\n🔗 `{repo_url}`\n\nOpen dashboard to deploy.",
        buttons=[[Button.inline("🖥 Open Dashboard", data="back")]]
    )

async def send_logs(event, user_id: int, repo_name: str, edit: bool = False):
    log_path = DEPLOY_DIR / str(user_id) / repo_name / "deploy.log"
    back_btn = [[Button.inline("◀️ Back", data=f"repo:{repo_name}")]]
    if not log_path.exists():
        text = f"📋 **{repo_name} Logs**\n\nNo logs yet — deploy first."
    else:
        with open(log_path) as f:
            lines = f.readlines()
        last = "".join(lines[-40:]).strip() or "Log file is empty."
        text = f"📋 **{repo_name} — Last 40 lines:**\n\n```\n{last[-3800:]}\n```"
    if edit:
        await event.edit(text, buttons=back_btn)
    else:
        await event.respond(text, buttons=back_btn)
 
# ─── MAIN ──────────────────────────────────────────────────────────────────────
 
async def main():
    logger.info("Starting Hosting Bot...")
    await client.start(bot_token=BOT_TOKEN)
    logger.info("Bot is online!")
    await client.run_until_disconnected()
 
if __name__ == "__main__":
    if not all([API_ID, API_HASH, BOT_TOKEN]):
        print("Set API_ID, API_HASH, BOT_TOKEN as environment variables.")
        sys.exit(1)
    asyncio.run(main())
