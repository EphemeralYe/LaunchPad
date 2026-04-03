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

# ─── CONFIG ─────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", 767371))          # From https://my.telegram.org
API_HASH = os.environ.get("API_HASH", "1a13288b823e1ac0db1d8c3dfb49b95a")           # From https://my.telegram.org
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7880763749:AAEq8czTTs5YHXppwpFVGR1_rLbxFyD9Xio")         # From @BotFather
DEPLOY_DIR = Path("./deployments")
DATA_FILE  = Path("repos.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

# ─── DATA ───────────────────────────────────────────
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

def delete_user_repo(uid, name):
    data = load_data()
    if str(uid) in data and name in data[str(uid)]:
        del data[str(uid)][name]
        save_data(data)

# ─── PROCESS ────────────────────────────────────────
def is_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except:
        return False

def stop_process(pid):
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except:
        return False

# ─── GIT ────────────────────────────────────────────
def clone_or_pull(url, dest, log):
    if (dest / ".git").exists():
        r = subprocess.run(["git", "-C", str(dest), "pull"], capture_output=True, text=True)
        return r.returncode == 0, r.stdout
    r = subprocess.run(["git", "clone", url, str(dest)], capture_output=True, text=True)
    return r.returncode == 0, r.stdout or r.stderr

# ─── VENV (FIXED) ───────────────────────────────────
def venv_python(repo):
    return repo / ".venv/bin/python"

def create_venv(repo, log):
    venv = repo / ".venv"
    py = venv / "bin/python"

    if venv.exists():
        if py.exists():
            subprocess.run([str(py), "-m", "ensurepip"], capture_output=True)
            return True, "Reused venv"
        shutil.rmtree(venv)

    r = subprocess.run([sys.executable, "-m", "venv", str(venv)])
    if r.returncode != 0:
        return False, "venv failed"

    subprocess.run([str(py), "-m", "ensurepip"], capture_output=True)
    return True, "Created venv"

# ─── INSTALL (FIXED) ────────────────────────────────
def install_python_deps(repo, log):
    py = venv_python(repo)
    if not py.exists():
        return False, "No python in venv"

    def pip(args):
        return [str(py), "-m", "pip"] + args

    subprocess.run(pip(["install", "--upgrade", "pip", "setuptools"]))

    req = repo / "requirements.txt"
    if req.exists():
        r = subprocess.run(pip(["install", "-r", str(req)]), capture_output=True, text=True)
        if r.returncode != 0:
            return False, r.stderr

    return True, "Deps installed"

# ─── START ──────────────────────────────────────────
def start_process(repo, log):
    py = str(venv_python(repo)) if venv_python(repo).exists() else "python3"

    for cmd, file in [
        ([py, "main.py"], "main.py"),
        ([py, "app.py"], "app.py"),
        (["node", "index.js"], "index.js"),
    ]:
        if (repo / file).exists():
            p = subprocess.Popen(cmd, cwd=str(repo),
                                 stdout=log, stderr=log,
                                 start_new_session=True)
            return True, file, p.pid

    return False, "No start file", 0

# ─── PIPELINE ───────────────────────────────────────
async def run_deploy_pipeline(event, uid, name, url):
    repo = DEPLOY_DIR / str(uid) / name
    repo.mkdir(parents=True, exist_ok=True)

    log_path = repo / "deploy.log"

    with open(log_path, "a") as log:

        await event.edit("📥 Cloning...")
        ok, msg = clone_or_pull(url, repo, log)
        if not ok:
            return False, msg, 0

        await event.edit("🐍 Creating venv...")
        ok, msg = create_venv(repo, log)
        if not ok:
            return False, msg, 0

        await event.edit("📦 Installing deps...")
        ok, msg = install_python_deps(repo, log)
        if not ok:
            return False, msg, 0

        await event.edit("🚀 Starting...")
        ok, msg, pid = start_process(repo, log)
        if not ok:
            return False, msg, 0

    return True, "Deployed", pid

# ─── TELEGRAM ───────────────────────────────────────
client = TelegramClient("bot", API_ID, API_HASH)

@client.on(events.NewMessage(pattern="/start"))
async def start(e):
    await e.respond("Send repo URL")

@client.on(events.NewMessage)
async def add(e):
    if "github.com" in e.text:
        name = e.text.split("/")[-1].replace(".git","")
        set_user_repo(e.sender_id, name, {"url": e.text, "pid":0})
        await e.respond(f"Added {name}")

@client.on(events.NewMessage(pattern="/deploy (.+)"))
async def deploy(e):
    name = e.pattern_match.group(1)
    repo = get_user_repos(e.sender_id).get(name)
    if not repo:
        return await e.respond("Not found")

    msg = await e.respond("Starting...")
    ok, detail, pid = await run_deploy_pipeline(msg, e.sender_id, name, repo["url"])

    if ok:
        repo["pid"] = pid
        set_user_repo(e.sender_id, name, repo)
        await msg.edit("✅ Deployed")
    else:
        await msg.edit(f"❌ {detail}")

# ─── MAIN ───────────────────────────────────────────
async def main():
    await client.start(bot_token=BOT_TOKEN)
    print("Bot running...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
