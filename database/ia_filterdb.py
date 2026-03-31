import asyncio
from struct import pack
import re
import base64
from typing import Dict, List, Tuple, Optional
from kurigram.file_id import FileId # Kurigram Migration
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import *
from utils import get_settings, save_group_settings, clean_filename
from collections import defaultdict
from datetime import datetime, timedelta
from logging_helper import LOGGER
import time
from functools import lru_cache

# --- Database Setup ---
client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

if MULTIPLE_DB:
    client2 = AsyncIOMotorClient(DATABASE_URI2)
    db2 = client2[DATABASE_NAME]
    instance2 = Instance.from_db(db2)
else:
    instance2 = instance

@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    class Meta:
        indexes = ('$file_name', )
        collection_name = COLLECTION_NAME

if MULTIPLE_DB:
    @instance2.register
    class Media2(Document):
        file_id = fields.StrField(attribute='_id')
        file_ref = fields.StrField(allow_none=True)
        file_name = fields.StrField(required=True)
        file_size = fields.IntField(required=True)
        file_type = fields.StrField(allow_none=True)
        mime_type = fields.StrField(allow_none=True)
        caption = fields.StrField(allow_none=True)
        class Meta:
            indexes = ('$file_name', )
            collection_name = COLLECTION_NAME

# --- Caching Logic ---
_db_size_cache = {'time': 0, 'size': 0}
DB_SIZE_CACHE_DURATION = 60 

@lru_cache(maxsize=512)
def get_regex_pattern(query):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r"(\b|[\.\+\-_])" + re.escape(query) + r"(\b|[\.\+\-_])"
    else:
        parts = query.split(' ')
        new_parts = [r"(\b|[\.\+\-_])" + re.escape(part) + r"(\b|[\.\+\-_])" for part in parts]
        raw_pattern = r".*[\s\.\+\-_()\[\]]".join(new_parts)
    try:
        return re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        return None

# --- DB Size & Storage Management ---

async def check_db_size(silentdb):
    try:
        global _db_size_cache
        current_time = time.time()
        is_primary = False

        if hasattr(silentdb, 'name') and silentdb.name == db.name:
            is_primary = True
        elif hasattr(silentdb, 'db') and silentdb.db.name == db.name:
            is_primary = True

        if is_primary and (current_time - _db_size_cache['time'] < DB_SIZE_CACHE_DURATION):
            return _db_size_cache['size']

        stats = await silentdb.command("dbstats") if hasattr(silentdb, 'command') else None
        size = stats.get('dataSize', 0) if stats else 0

        if is_primary:
            _db_size_cache['time'] = current_time
            _db_size_cache['size'] = size
        return size
    except Exception as e:
        LOGGER.error(f"Error checking DB size: {e}")
        return 0

async def save_file(media) -> Tuple[bool, int]:
    try:
        file_id, file_ref = unpack_new_file_id(media.file_id)
        file_name = clean_filename(media.file_name)
        use_secondary = False
        saveMedia = Media

        if MULTIPLE_DB:
            primary_db_size = await check_db_size(db)
            if primary_db_size >= (DB_CHANGE_LIMIT * 1024 * 1024):
                saveMedia = Media2
                use_secondary = True

        # Duplicate Check
        if use_secondary:
            exists_in_primary, exists_in_secondary = await asyncio.gather(
                Media.find_one({'_id': file_id}),
                Media2.find_one({'_id': file_id})
            )
            if exists_in_primary or exists_in_secondary: return False, 0
        else:
            exists = await Media.find_one({'_id': file_id})
            if exists: return False, 0

        file = saveMedia(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption else None,
        )
        await file.commit()
        return True, 1
    except DuplicateKeyError:
        return False, 0
    except Exception as e:
        LOGGER.error(f"Save File Error: {e}")
        return False, 3

# --- Search Logic ---

async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=None) -> Tuple[List, int, int]:
    if chat_id:
        settings = await get_settings(int(chat_id))
        max_results = 10 if settings.get('max_btn') else int(MAX_B_TN)

    regex = get_regex_pattern(query)
    if not regex: return [], 0, 0

    if not isinstance(filter, dict):
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]} if USE_CAPTION_FILTER else {'file_name': regex}
    if file_type: filter['file_type'] = file_type

    projection = {'file_name': 1, 'file_size': 1, 'file_id': 1, 'file_type': 1, 'caption': 1, '_id': 1}

    # Parallel Execution for Speed
    cursor1 = Media.find(filter, projection).sort('$natural', -1).skip(offset).limit(max_results)
    files = await cursor1.to_list(length=max_results)
    
    if not MULTIPLE_DB:
        total_results = await Media.count_documents(filter)
    else:
        count_db1, count_db2 = await asyncio.gather(Media.count_documents(filter), Media2.count_documents(filter))
        total_results = count_db1 + count_db2

        if len(files) < max_results:
            remaining = max_results - len(files)
            if len(files) > 0:
                cursor2 = Media2.find(filter, projection).sort('$natural', -1).limit(remaining)
                files.extend(await cursor2.to_list(length=remaining))
            elif offset >= count_db1:
                cursor2 = Media2.find(filter, projection).sort('$natural', -1).skip(offset - count_db1).limit(max_results)
                files = await cursor2.to_list(length=max_results)

    next_offset = offset + len(files)
    if next_offset >= total_results or not files: next_offset = 0
    return files, next_offset, total_results

# --- File ID Management (Kurigram Fix) ---

def encode_file_id(s: bytes) -> str:
    r, n = b"", 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0: n += 1
        else:
            if n: r += b"\x00" + bytes([n]); n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")

def unpack_new_file_id(new_file_id):
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(pack("<iiqq", int(decoded.file_type), decoded.dc_id, decoded.media_id, decoded.access_hash))
    file_ref = base64.urlsafe_b64encode(decoded.file_reference).decode().rstrip("=")
    return file_id, file_ref

# --- SilentX Visuals (Movies & Series Lists) ---

async def siletxbotz_fetch_media(limit: int) -> List[dict]:
    _TITLE_PROJECTION = {'file_name': 1, 'caption': 1, '_id': 0}
    if MULTIPLE_DB:
        half = limit // 2
        res = await asyncio.gather(
            Media.find({}, _TITLE_PROJECTION).sort("$natural", -1).limit(half).to_list(length=half),
            Media2.find({}, _TITLE_PROJECTION).sort("$natural", -1).limit(limit - half).to_list(length=limit - half)
        )
        return res[0] + res[1]
    return await Media.find({}, _TITLE_PROJECTION).sort("$natural", -1).limit(limit).to_list(length=limit)

async def silentxbotz_clean_title(filename: str, is_series: bool = False) -> str:
    if not filename: return ""
    filename = clean_filename(filename)
    # Series logic for S01, Season 1 etc.
    if is_series:
        match = re.search(r"(.*?)(?:S(\d{1,2})|Season\s*(\d+))", filename, re.IGNORECASE)
        if match: return f"{match.group(1).strip().title()} S{int(match.group(2) or match.group(3)):02}"
    return filename.strip().title()

async def siletxbotz_get_movies(limit: int = 20) -> List[str]:
    candidates = await siletxbotz_fetch_media(limit * 2)
    results = set()
    pattern = r"(?:s\d{1,2}|season\s*\d+)(?:\s*e\d{1,2}|episode\s*\d+)?\b"
    for file in candidates:
        name = file.get("file_name", "")
        if re.search(pattern, name, re.IGNORECASE): continue
        title = await silentxbotz_clean_title(name)
        if title: results.add(title)
        if len(results) >= limit: break
    return sorted(list(results))[:limit]

async def siletxbotz_get_series(limit: int = 30) -> Dict[str, List[int]]:
    candidates = await siletxbotz_fetch_media(limit * 3)
    grouped = defaultdict(list)
    pattern = r"(.*?)(?:S(\d{1,2})|Season\s*(\d+))"
    for file in candidates:
        name = file.get("file_name", "")
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            title = await silentxbotz_clean_title(match.group(1))
            s_num = int(match.group(2) or match.group(3))
            if s_num not in grouped[title]: grouped[title].append(s_num)
    return {t: sorted(s) for t, s in list(grouped.items())[:limit]}
