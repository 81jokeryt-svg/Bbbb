# Fully Migrated to Kurigram by Gemini
from kurigram.errors import (
    InputUserDeactivated, 
    UserNotParticipant, 
    FloodWait, 
    UserIsBlocked, 
    PeerIdInvalid, 
    MessageNotModified
)
from info import *
from imdbkit import IMDBKit 
import asyncio
from kurigram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from kurigram import enums
from typing import Union, Optional, Dict, Any, List
from Script import script
import pytz
import random 
import re
import os
import time as time_module
from datetime import datetime, date, time, timedelta
import string
from database.users_chats_db import db
from bs4 import BeautifulSoup
import aiohttp
from shortzy import Shortzy
import http.client
import json
from logging_helper import LOGGER

# --- Regex & Constants ---
BTN_URL_REGEX = re.compile(
    r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))"
)

BAD_WORDS_REGEX = re.compile(
    '|'.join(map(re.escape, sorted(BAD_WORDS, key=len, reverse=True))), 
    flags=re.IGNORECASE
) if BAD_WORDS else None

imdb = IMDBKit() 
BANNED = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ('\'', '"', SMART_OPEN)

class temp(object):   
    BANNED_USERS = []
    BANNED_CHATS = []
    SETTINGS = {}
    SETTINGS_EXPIRY = {}
    ME = None
    CURRENT = int(os.environ.get("SKIP", 2))
    CANCEL = False
    B_USERS_CANCEL = False
    B_GROUPS_CANCEL = False 
    MELCOW = {}
    U_NAME = None
    B_NAME = None
    B_LINK = None
    GETALL = {}
    SHORT = {}
    IMDB_CAP = {}
    VERIFICATIONS = {}

# --- Broadcast Functions ---

async def users_broadcast(user_id, message, is_pin):
    try:
        m = await message.copy(chat_id=user_id)
        if is_pin:
            await m.pin(both_sides=True)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.value) # Kurigram uses .value
        return await users_broadcast(user_id, message, is_pin)
    except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
        await db.delete_user(int(user_id))
        return False, "Removed"
    except Exception:
        return False, "Error"

# --- Utility Functions ---

def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])

def clean_filename(filename):
    if not filename:
        return ""
    name = re.sub(r'[_\-\.\+]', ' ', filename.rsplit('.', 1)[0])  
    if BAD_WORDS_REGEX:
        name = BAD_WORDS_REGEX.sub('', name)
    name = re.sub(r'@\w+|#\w+|https?://\S+|www\.\S+|\[|\]|\(|\)', ' ', name)
    return ' '.join(w.capitalize() for w in name.split()).strip()

def listx_to_str(k):
    if not k: return "N/A"
    if not hasattr(k, '__iter__') or isinstance(k, (str, int, float)): return str(k)
    res = [str(e).strip() for e in k if e]
    return ", ".join(res[:int(MAX_LIST_ELM)]) if res else "N/A"

# --- IMDB Logic ---

async def get_poster(query, bulk=False, id=False, file=None):
    try:
        if not id:
            search = await asyncio.to_thread(imdb.search_movie, query)
            if not search or not search.titles: return None
            movieid = search.titles[0].imdb_id
        else:
            movieid = query

        movie = await asyncio.to_thread(imdb.get_movie, movieid)
        if not movie: return None
        
        plot = movie.plot[0] if isinstance(movie.plot, list) else movie.plot or ""
        return {
            'title': movie.title,
            'votes': getattr(movie, 'votes', 'N/A'),
            'imdb_id': movie.imdb_id,
            'rating': str(movie.rating),
            'genres': listx_to_str(movie.genres),
            'poster': movie.cover_url,
            'plot': plot[:800] + "..." if len(plot) > 800 else plot,
            'year': movie.year,
            'url': movie.url or f"https://www.imdb.com/title/{movie.imdb_id}",
            **locals() # Passes other needed fields to template
        }
    except Exception as e:
        LOGGER.error(f"IMDB Error: {e}")
        return None

# --- Main Caption & UI Logic ---

def parser(text, keyword):
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons, alerts, prev, i = [], [], 0, 0
    for match in BTN_URL_REGEX.finditer(text):
        note_data = text[prev:match.start(1)]
        prev = match.end(1)
        if match.group(3) == "buttonalert":
            btn = InlineKeyboardButton(text=match.group(2), callback_data=f"alertmessage:{i}:{keyword}")
            alerts.append(match.group(4))
            i += 1
        else:
            btn = InlineKeyboardButton(text=match.group(2), url=match.group(4).replace(" ", ""))
        
        if bool(match.group(5)) and buttons:
            buttons[-1].append(btn)
        else:
            buttons.append([btn])
    return text[prev:], buttons, (alerts if alerts else None)

async def get_cap(settings, remaining_seconds, files, query, total_results, search, offset):
    search_query = clean_filename(search)
    cap = ""
    if settings.get("imdb"):
        imdb_data = await get_poster(search_query, file=files[0].file_name)
        if imdb_data:
            cap = script.IMDB_TEMPLATE_TXT.format(**imdb_data)
        else:
            cap = f"<b>📂 Results for: {search_query}</b>\n\n"
    else:
        cap = f"<b>📂 Results for: {search_query}</b>\n\n"

    for file_num, file in enumerate(files, start=offset + 1):
        cap += f"\n<b>{file_num}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file.file_id}'>{get_size(file.file_size)} | {clean_filename(file.file_name)}</a></b>"
    
    return cap

def extract_user(message: Message) -> Union[int, str]:
    if message.reply_to_message:
        return message.reply_to_message.from_user.id, message.reply_to_message.from_user.first_name
    if len(message.command) > 1:
        user_id = message.command[1]
        try: return int(user_id), user_id
        except: return user_id, user_id
    return message.from_user.id, message.from_user.first_name

async def get_seconds(time_string):
    match = re.match(r"(\d+)\s*(s|min|hour|day|month|year)", time_string.lower())
    if not match: return 0
    value, unit = int(match.group(1)), match.group(2)
    multipliers = {'s': 1, 'min': 60, 'hour': 3600, 'day': 86400, 'month': 2592000, 'year': 31536000}
    return value * multipliers.get(unit, 0)
