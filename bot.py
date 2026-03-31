import sys
import time
import asyncio
import threading
from datetime import date, datetime
from pathlib import Path
import importlib.util
import requests
import pytz
from aiohttp import web
from PIL import Image 

# Library change: Pyrogram/Pyrofork to Kurigram
from pyrogram import Client, idle, __version__
from pyrogram.raw.all import layer
import pyrogram.utils

from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import *
from utils import temp
from Script import script
from plugins import web_server, check_expired_premium
from Lucia.Bot import SilentX
from Lucia.util.keepalive import ping_server
from Lucia.Bot.clients import initialize_clients
from logging_helper import LOGGER

botStartTime = time.time()

# Kurigram/Pyrogram compatibility for long channel IDs
pyrogram.utils.MIN_CHANNEL_ID = -1009147483647

def ping_loop():
    while True:
        try:
            if URL:
                r = requests.get(URL, timeout=10)
                if r.status_code == 200:
                    LOGGER.info("✅ Ping Successful")
                else:
                    LOGGER.error(f"⚠️ Ping Failed: {r.status_code}")
        except Exception as e:
            LOGGER.error(f"❌ Exception During Ping: {e}")
        time.sleep(120)

if URL:
    threading.Thread(target=ping_loop, daemon=True).start()

def silentx_plugins_handler(app, plugins_dir: str | Path = "plugins", package_name: str = "plugins"):
    plugins_dir = Path(plugins_dir)
    loaded_plugins = []

    if not plugins_dir.exists():
        LOGGER.warning(f"Plugins Directory '{plugins_dir}' Does Not Exist.")
        return loaded_plugins

    for file in sorted(plugins_dir.rglob("*.py")):
        if file.name == "__init__.py":
            continue

        rel_path = file.relative_to(plugins_dir).with_suffix("")
        import_path = package_name + "." + ".".join(list(rel_path.parts))

        try:
            spec = importlib.util.spec_from_file_location(import_path, file)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules[import_path] = module
                loaded_plugins.append(import_path)
                LOGGER.info(f"🔌 Loaded plugin: {import_path.split('.')[-1]}")
        except Exception:
            LOGGER.exception(f"Failed To Import Plugin: {import_path}")
    
    return loaded_plugins

async def SilentXBotz_start():
    # Basic Checks
    if MULTIPLE_DATABASE and not DATABASE_URI2:
        LOGGER.error("DATABASE_URI2 missing but MULTIPLE_DATABASE is True!")
        sys.exit(1)
    
    if not API_ID or not API_HASH or not BOT_TOKEN:
        LOGGER.error("API_ID, API_HASH, or BOT_TOKEN missing!")
        sys.exit(1)

    LOGGER.info("🚀 Initializing Lucia Bot on Kurigram Engine...")
    
    # Starting Main Client
    await SilentX.start()
    
    # Initializing other background clients (Clones)
    await initialize_clients()
    
    # Loading Plugins for Kurigram
    silentx_plugins_handler(SilentX)

    # Fetching Banned Data
    try:
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats
    except Exception as e:
        LOGGER.error(f"Error fetching banned data: {e}")

    # Indexing Database
    try:
        await Media.ensure_indexes()
        if MULTIPLE_DATABASE:
            await Media2.ensure_indexes()
            LOGGER.info("Multiple DB Mode: Enabled")
    except Exception as e:
        LOGGER.error(f"DB Index Error: {e}")

    me = await SilentX.get_me()
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    SilentX.username = "@" + me.username
    
    # Starting background tasks
    SilentX.loop.create_task(check_expired_premium(SilentX))
    if ON_HEROKU:
        asyncio.create_task(ping_server())

    LOGGER.info(f"✨ {me.first_name} started on Kurigram (Layer {layer})")
    LOGGER.info(script.LOGO)

    # Log Restart
    tz = pytz.timezone("Asia/Kolkata")
    time_str = datetime.now(tz).strftime("%H:%M:%S %p")
    try:
        await SilentX.send_message(
            chat_id=LOG_CHANNEL,
            text=script.RESTART_TXT.format(temp.B_LINK, date.today(), time_str)
        )
    except Exception as e:
        LOGGER.error(f"Log Channel Error: {e}")

    # Web Server for Hosting
    app_runner = web.AppRunner(await web_server())
    await app_runner.setup()
    await web.TCPSite(app_runner, "0.0.0.0", PORT).start()

    await idle()

# Handling shutdown with **kwargs for flexibility
async def stop_bot(**kwargs):
    reason = kwargs.get("reason", "Manual Shutdown")
    LOGGER.info(f"Stopping Service... Reason: {reason}")
    await SilentX.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(SilentXBotz_start())
    except KeyboardInterrupt:
        loop.run_until_complete(stop_bot(reason="Keyboard Interrupt"))
        LOGGER.info("Service Stopped Successfully! 👋")
