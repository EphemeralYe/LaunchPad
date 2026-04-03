import asyncio
import os
import signal
import subprocess
import json
import logging
import sys
import shutil
import time
import hashlib
import threading
import psutil
from pathlib import Path
from telethon import TelegramClient, events, Button

# ─── CONFIG ─────────────────────────
API_ID = int(os.environ.get("API_ID", 767371))          # From https://my.telegram.org
API_HASH = os.environ.get("API_HASH", "1a13288b823e1ac0db1d8c3dfb49b95a")           # From https://my.telegram.org
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7880763749:AAEq8czTTs5YHXppwpFVGR1_rLbxFyD9Xio")         # From @BotFather
DEPLOY_DIR = Path("./deployments")
DATA_FILE  = Path("repos.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

# ─── DATA ───────────────────────────
waiting = {}
def load_data():
    if DATA_FILE.exists():
        return json.load(open(DATA_FILE))
    return {}

def save_data(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)

def get_user_repos(uid):
    return load_data().get(str(uid), {})

def set_user_repo(uid, name, info):
    data = load_data()
    data.setdefault(str(uid), {})[name] = info
    save_data(data)

# ─── STATS ──────────────────────────
def get_stats():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    uptime = time.time() - psutil.boot_time()
    return f"📊 CPU: {cpu}% | RAM: {ram}% | Uptime: {int(uptime//3600)}h"

# ─── LOG ROTATION ───────────────────
def rotate_log(path):
    if path.exists() and path.stat().st_size > 5_000_000:
        path.rename(path.with_suffix(".old"))

# ─── HASH CACHE ─────────────────────
def get_req_hash(repo):
    req = repo / "requirements.txt"
    if not req.exists():
        return None
    return hashlib.md5(req.read_bytes()).hexdigest()

# ─── VENV ───────────────────────────
def venv_python(repo):
    return repo / ".venv/bin/python"

def create_venv(repo):
    venv = repo / ".venv"
    py = venv / "bin/python"

    if venv.exists():
        if py.exists():
            subprocess.run([str(py), "-m", "ensurepip"], capture_output=True)
            return True
        shutil.rmtree(venv)

    subprocess.run([sys.executable, "-m", "venv", str(venv)])
    subprocess.run([str(py), "-m", "ensurepip"], capture_output=True)
    return True

# ─── INSTALL ────────────────────────
def install_deps(repo, log):
    py = venv_python(repo)

    def pip(args):
        return [str(py), "-m", "pip"] + args

    subprocess.run(pip(["install", "--upgrade", "pip"]))

    new_hash = get_req_hash(repo)
    hash_file = repo / ".req_hash"
    old_hash = hash_file.read_text() if hash_file.exists() else None

    if new_hash == old_hash:
        log.write("[pip] ⚡ Cache hit\n")
        return True

    req = repo / "requirements.txt"
    if req.exists():
        r = subprocess.run(pip(["install", "-r", str(req)]))
        if r.returncode != 0:
            return False

    hash_file.write_text(new_hash or "")
    return True

# ─── AUTO RESTART ───────────────────
def start_process(repo, log):
    py = str(venv_python(repo))

    def run():
        while True:
            try:
                p = subprocess.Popen(
                    [py, "main.py"],
                    cwd=str(repo),
                    stdout=log,
                    stderr=log
                )
                p.wait()
                log.write("🔁 Restarting...\n")
                log.flush()
                time.sleep(3)
            except Exception as e:
                log.write(f"[error] {e}\n")
                time.sleep(5)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return True, "Auto-restart enabled", 0

# ─── CLOUDFLARE ─────────────────────
def start_tunnel():
    try:
        p = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://localhost:8000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        for line in p.stdout:
            if "trycloudflare.com" in line:
                return line.strip()

        return "Tunnel started (URL not detected)"

    except FileNotFoundError:
        return "⚠️ cloudflared not installed""

# ─── DEPLOY ─────────────────────────
async def safe_deploy(*args):
    try:
        await deploy(*args)
    except Exception as e:
        print("DEPLOY ERROR:", e)
        
async def deploy(event, uid, name, url):
    repo = DEPLOY_DIR / str(uid) / name
    repo.mkdir(parents=True, exist_ok=True)

    log_path = repo / "deploy.log"
    rotate_log(log_path)

    with open(log_path, "a") as log:

        await event.edit("📥 Cloning...")
        subprocess.run(["git", "clone", url, str(repo)])

        await event.edit("🐍 Venv...")
        create_venv(repo)

        await event.edit("📦 Installing...")
        if not install_deps(repo, log):
            return await event.edit("❌ Install failed")

        await event.edit("🚀 Starting...")
        start_process(repo, log)

        url = start_tunnel()

    await event.edit(f"✅ Deployed!\n🌍 {url}")

# ─── CLEANUP ────────────────────────
def cleanup():
    for user in DEPLOY_DIR.iterdir():
        for repo in user.iterdir():
            if time.time() - repo.stat().st_mtime > 86400:
                shutil.rmtree(repo)

# ─── TELEGRAM ───────────────────────
client = TelegramClient("bot", API_ID, API_HASH)

def build_main_menu(user_id):
    repos = get_user_repos(user_id)
    buttons = []

    for name, info in repos.items():
        buttons.append([
            Button.inline(f"📦 {name}", data=f"repo:{name}")
        ])
    buttons.append([Button.inline("➕ Add Repo", data="add_repo")])
    return buttons

def build_repo_menu(name):
    return [
        [Button.inline("🚀 Deploy", data=f"deploy:{name}")],
        [Button.inline("⏹ Stop", data=f"stop:{name}")],
        [Button.inline("📋 Logs", data=f"logs:{name}")],
        [Button.inline("◀️ Back", data="back")]
    ]
    
@client.on(events.NewMessage(pattern="/start"))
async def start(e):
    await e.respond(
        "🖥 Hosting Dashboard",
        buttons=build_main_menu(e.sender_id)
    )

@client.on(events.CallbackQuery(pattern=b"repo:(.+)"))
async def repo_menu(e):
    name = e.data.decode().split(":")[1]

    await e.edit(
        f"📦 {name}\nChoose action:",
        buttons=build_repo_menu(name)
    )

@client.on(events.CallbackQuery(pattern=b"deploy:(.+)"))
async def cb_deploy(e):
    name = e.data.decode().split(":")[1]
    repo = get_user_repos(e.sender_id).get(name)

    msg = await e.edit(f"🚀 Deploying {name}...")
    asyncio.create_task(
        safe_deploy(msg, e.sender_id, name, repo["url"])
    )

@client.on(events.CallbackQuery(pattern=b"stop:(.+)"))
async def cb_stop(e):
    name = e.data.decode().split(":")[1]
    repo = get_user_repos(e.sender_id).get(name)

    pid = repo.get("pid")
    if pid:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    await e.edit(f"⏹ Stopped {name}")
    
@client.on(events.CallbackQuery(pattern=b"logs:(.+)"))
async def cb_logs(e):
    name = e.data.decode().split(":")[1]
    path = DEPLOY_DIR / str(e.sender_id) / name / "deploy.log"

    if not path.exists():
        return await e.answer("No logs yet", alert=True)

    with open(path) as f:
        data = f.readlines()[-30:]
    await e.edit(
        f"📋 Logs:\n\n```\n{''.join(data)[-3000:]}\n```",
        buttons=[[Button.inline("◀️ Back", data=f"repo:{name}")]]
    )

@client.on(events.CallbackQuery(data=b"back"))
async def back(e):
    await e.edit(
        "🖥 Hosting Dashboard",
        buttons=build_main_menu(e.sender_id)
    )

@client.on(events.CallbackQuery(data=b"add_repo"))
async def add_repo(e):
    waiting[e.sender_id] = True
    await e.edit("Send repo URL")

@client.on(events.NewMessage)
async def receive_repo(e):
    if not waiting.get(e.sender_id):
        return

    waiting.pop(e.sender_id)

    url = e.text
    name = url.split("/")[-1].replace(".git","")

    set_user_repo(e.sender_id, name, {"url": url, "pid":0})

    await e.respond("✅ Added!", buttons=build_main_menu(e.sender_id))
    
@client.on(events.NewMessage(pattern="/stats"))
async def stats(e):
    await e.respond(get_stats())

# ─── MAIN ───────────────────────────
async def main():
    await client.start(bot_token=BOT_TOKEN)
    print("Bot running...")
    while True:
        cleanup()
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
