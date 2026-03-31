# Updated for Kurigram Engine - Lucia Autofilter
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

# --- Constants & Regex ---
BTN_URL_REGEX = re.compile(
    r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))"
)

BAD_WORDS_REGEX = re.compile(
    '|'.join(map(re.escape, sorted(BAD_WORDS, key=len, reverse=True))), 
    flags=re.IGNORECASE
) if BAD_WORDS else None

imdb_client = IMDBKit() 
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

# --- Admin & Broadcast Functions ---

async def is_check_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception:
        return False
    
async def users_broadcast(user_id, message, is_pin):
    try:
        m = await message.copy(chat_id=user_id)
        if is_pin:
            await m.pin(both_sides=True)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.value)
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
    return "%.2f %s" % (size, units[i-1] if i > 0 else units[0])

def clean_filename(filename):
    if not filename: return ""
    name = re.sub(r'[_\-\.\+]', ' ', filename.rsplit('.', 1)[0])  
    if BAD_WORDS_REGEX: name = BAD_WORDS_REGEX.sub('', name)
    name = re.sub(r'@\w+|#\w+|https?://\S+|www\.\S+|\[|\]|\(|\)', ' ', name)
    return ' '.join(w.capitalize() for w in name.split()).strip()

# --- Parser Functions ---

def parser(text, keyword):
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons = []
    note_data = ""
    prev = 0
    i = 0
    alerts = []
    for match in BTN_URL_REGEX.finditer(text):
        n_escapes = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1
        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            if match.group(3) == "buttonalert":
                if bool(match.group(5)) and buttons:
                    buttons[-1].append(InlineKeyboardButton(text=match.group(2), callback_data=f"alertmessage:{i}:{keyword}"))
                else:
                    buttons.append([InlineKeyboardButton(text=match.group(2), callback_data=f"alertmessage:{i}:{keyword}")])
                i += 1
                alerts.append(match.group(4))
            else:
                if bool(match.group(5)) and buttons:
                    buttons[-1].append(InlineKeyboardButton(text=match.group(2), url=match.group(4).replace(" ", "")))
                else:
                    buttons.append([InlineKeyboardButton(text=match.group(2), url=match.group(4).replace(" ", ""))])
        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    note_data += text[prev:]
    return note_data, buttons, (alerts if alerts else None)

# --- IMDB & Caption Logic ---

async def get_poster(query, bulk=False, id=False, file=None):
    try:
        if not id:
            search_result = await asyncio.to_thread(imdb_client.search_movie, str(query))
            if not search_result or not search_result.titles: return None
            movieid_str = search_result.titles[0].imdb_id
        else:
            movieid_str = query

        movie = await asyncio.to_thread(imdb_client.get_movie, movieid_str)
        if not movie: return None
        
        plot = movie.plot[0] if isinstance(movie.plot, list) else movie.plot or ""
        return {
            'title': movie.title,
            'imdb_id': movie.imdb_id,
            'poster': movie.cover_url,
            'rating': str(movie.rating),
            'genres': ", ".join(movie.genres) if movie.genres else "N/A",
            'plot': plot[:800] + "..." if len(plot) > 800 else plot,
            'year': movie.year,
            'url': movie.url or f"https://www.imdb.com/title/{movie.imdb_id}",
            'votes': getattr(movie, 'votes', 'N/A'),
            'director': ", ".join(movie.directors) if getattr(movie, 'directors', None) else "N/A"
        }
    except Exception as e:
        LOGGER.error(f"IMDB Error: {e}")
        return None

async def get_cap(settings, remaining_seconds, files, query, total_results, search, offset):
    search_query = clean_filename(search)
    cap = ""
    if settings.get("imdb"):
        imdb_data = await get_poster(search_query, file=files[0].file_name)
        if imdb_data:
            cap = script.IMDB_TEMPLATE_TXT.format(
                query=search_query,
                title=imdb_data['title'],
                imdb_id=imdb_data['imdb_id'],
                rating=imdb_data['rating'],
                genres=imdb_data['genres'],
                plot=imdb_data['plot'],
                year=imdb_data['year'],
                url=imdb_data['url'],
                **locals()
            )
        else:
            cap = f"<b>📂 Results for: {search_query}</b>\n\n"
    else:
        cap = f"<b>📂 Results for: {search_query}</b>\n\n"

    for file_num, file in enumerate(files, start=offset + 1):
        cap += f"\n<b>{file_num}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file.file_id}'>{get_size(file.file_size)} | {clean_filename(file.file_name)}</a></b>"
    
    return cap

# --- Time & Helper Functions ---

async def get_seconds(time_string):
    match = re.match(r"(\d+)\s*(s|min|hour|day|month|year)", time_string.lower())
    if not match: return 0
    value, unit = int(match.group(1)), match.group(2)
    multipliers = {'s': 1, 'min': 60, 'hour': 3600, 'day': 86400, 'month': 2592000, 'year': 31536000}
    return value * multipliers.get(unit, 0)

async def get_settings(group_id):
    settings = temp.SETTINGS.get(group_id)
    if settings and time_module.time() < temp.SETTINGS_EXPIRY.get(group_id, 0):
        return settings
    settings = await db.get_settings(group_id)
    temp.SETTINGS[group_id] = settings
    temp.SETTINGS_EXPIRY[group_id] = time_module.time() + 300
    return settings

def extract_user(message: Message) -> Union[int, str]:
    if message.reply_to_message:
        return message.reply_to_message.from_user.id, message.reply_to_message.from_user.first_name
    if len(message.command) > 1:
        user_id = message.command[1]
        try: return int(user_id), user_id
        except: return user_id, user_id
    return message.from_user.id, message.from_user.first_name
