import time
import re
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import ChannelInvalid, ChatAdminRequired, UsernameInvalid, UsernameNotModified
from info import ADMINS, INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp, get_readable_time, clean_filename # clean_filename import kiya
from math import ceil
from logging_helper import LOGGER

lock = asyncio.Lock()

@Client.on_callback_query(filters.regex(r'^index'))
async def index_files(bot, query):
    if query.data.startswith('index_cancel'):
        temp.CANCEL = True
        return await query.answer("Cancelling Indexing...")
    
    data = query.data.split("#")
    if len(data) < 5: return
    
    _, raju, chat, lst_msg_id, from_user = data
    
    if raju == 'reject':
        await query.message.delete()
        await bot.send_message(int(from_user),
                               f'Your Submission for indexing {chat} has been declined.',
                               reply_to_message_id=int(lst_msg_id))
        return

    if lock.locked():
        return await query.answer('Wait until previous process complete.', show_alert=True)
    
    msg = query.message
    await query.answer('Starting Indexing...⏳', show_alert=True)
    
    if int(from_user) not in ADMINS:
        await bot.send_message(int(from_user),
                               f'Your Submission for indexing {chat} has been accepted.',
                               reply_to_message_id=int(lst_msg_id))
                               
    await msg.edit(
        "<b>Indexing Started...</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Cancel', callback_data='index_cancel')]])
    )
    
    try:
        chat = int(chat) if chat.strip().startswith("-100") or chat.isnumeric() else chat
    except:
        pass
        
    await index_files_to_db(int(lst_msg_id), chat, msg, bot)

@Client.on_message((filters.forwarded | (filters.regex(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")) & filters.text ) & filters.private & filters.incoming)
async def send_for_index(bot, message):
    # Link validation logic (Same as before)
    if message.text:
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(message.text)
        if not match: return
        chat_id = match.group(4)
        last_msg_id = int(match.group(5))
        if chat_id.isnumeric(): chat_id = int(("-100" + chat_id))
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = message.forward_from_message_id
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id
    else: return

    # Admin check and request logic (Same as before)
    buttons = [[InlineKeyboardButton('Accept Index', callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')]]
    await message.reply(f"Do you want to index <code>{chat_id}</code>?", reply_markup=InlineKeyboardMarkup(buttons))

async def index_files_to_db(lst_msg_id, chat, msg, bot):
    total_files, duplicate, errors, deleted, no_media, unsupported = 0, 0, 0, 0, 0, 0
    BATCH_SIZE = 200
    start_time = time.time()

    async with lock:
        try:
            current = temp.CURRENT
            temp.CANCEL = False
            total_fetch = lst_msg_id - current
            
            for batch_start_id in range(current, lst_msg_id, BATCH_SIZE):
                if temp.CANCEL: break
                
                end_id = min(batch_start_id + BATCH_SIZE, lst_msg_id)
                message_ids = list(range(batch_start_id + 1, end_id + 1))
                
                try:
                    messages = await bot.get_messages(chat, message_ids)
                except FloodWait as e:
                    await asyncio.sleep(e.value) # Kurigram Fix
                    messages = await bot.get_messages(chat, message_ids)
                except Exception:
                    errors += len(message_ids)
                    continue

                save_tasks = []
                for message in messages:
                    current += 1
                    if message.empty: deleted += 1; continue
                    if not message.media: no_media += 1; continue
                    
                    # Sirf Video, Audio aur Document index honge
                    if message.media not in [enums.MessageMediaType.VIDEO, enums.MessageMediaType.AUDIO, enums.MessageMediaType.DOCUMENT]:
                        unsupported += 1; continue
                    
                    media = getattr(message, message.media.value, None)
                    if not media: unsupported += 1; continue
                    
                    # --- CRITICAL FIX START ---
                    # Yahan hum file name ko database mein save karne se pehle clean karenge
                    # Taaki "Ishq" ka "q" bache aur extensions sahi ho jayein
                    file_name = getattr(media, 'file_name', 'None')
                    media.file_name = clean_filename(file_name) 
                    # --------------------------
                    
                    media.file_type = message.media.value
                    media.caption = message.caption
                    save_tasks.append(save_file(media))

                results = await asyncio.gather(*save_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception): errors += 1
                    else:
                        ok, code = result
                        if ok: total_files += 1
                        elif code == 0: duplicate += 1
                        else: errors += 1

                # Progress Update logic...
                percentage = (current / lst_msg_id) * 100
                await msg.edit(f"📊 Indexing: {percentage:.1f}%\nSaved: {total_files}\nDuplicate: {duplicate}")

            await msg.edit(f"✅ Indexing Completed!\nSaved: {total_files}\nDuplicates: {duplicate}")
        except Exception as e:
            LOGGER.error(f"Index Error: {e}")
