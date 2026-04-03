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
import psutil
from pathlib import Path
from telethon import TelegramClient, events

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
            p = subprocess.Popen([py, "main.py"], cwd=str(repo),
                                 stdout=log, stderr=log)
            p.wait()
            log.write("🔁 Restarting...\n")
            time.sleep(3)

    subprocess.Popen(["python3", "-c",
                      f"import threading; threading.Thread(target={run}).start()"])

    return True, "Started"

# ─── CLOUDFLARE ─────────────────────
def start_tunnel():
    p = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", "http://localhost:8000"],
        stdout=subprocess.PIPE, text=True
    )
    for line in p.stdout:
        if "trycloudflare.com" in line:
            return line.strip()
    return "No URL"

# ─── DEPLOY ─────────────────────────
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

@client.on(events.NewMessage(pattern="/start"))
async def start(e):
    await e.respond("Send repo URL")

@client.on(events.NewMessage(pattern="/stats"))
async def stats(e):
    await e.respond(get_stats())

@client.on(events.NewMessage)
async def add(e):
    if "github.com" in e.text:
        name = e.text.split("/")[-1].replace(".git","")
        set_user_repo(e.sender_id, name, {"url": e.text})
        await e.respond(f"Added {name}")

@client.on(events.NewMessage(pattern="/deploy (.+)"))
async def deploy_cmd(e):
    name = e.pattern_match.group(1)
    repo = get_user_repos(e.sender_id).get(name)

    msg = await e.respond("Starting...")
    asyncio.create_task(deploy(msg, e.sender_id, name, repo["url"]))

# ─── MAIN ───────────────────────────
async def main():
    await client.start(bot_token=BOT_TOKEN)
    print("Bot running...")
    while True:
        cleanup()
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
