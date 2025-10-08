import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

# Configuration
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
except KeyError as e:
    print(f"Missing environment variable: {e}")
    sys.exit(1)
except ValueError as e:
    print(f"Invalid environment variable format: %s", e)
    sys.exit(1)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("user_bot")

# MongoDB setup
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()  # Test connection
    db = client["ultimate_hybrid_shieldbot"]
    JOBS: Collection = db["jobs"]
    CONFIG: Collection = db["config"]
except PyMongoError as e:
    log.error("Failed to connect to MongoDB: %s", e)
    sys.exit(1)

# Constants
STATUS_NEW_DM = "NEW_DM"
STATUS_PENDING_REPLY = "PENDING_REPLY"
STATUS_READY_TO_SEND = "READY_TO_SEND"
STATUS_COMPLETED = "COMPLETED"
STATUS_ERROR = "ERROR"
STATUS_SENDING = "SENDING"

TYPE_DM_FLOW = "DM_FLOW"

def now():
    return datetime.now(timezone.utc)

def get_config(key: str, default=None):
    try:
        doc = CONFIG.find_one({"key": key})
        return doc["value"] if doc and "value" in doc else default
    except PyMongoError as e:
        log.error("Failed to get config %s: %s", key, e)
        return default

def extract_content(msg: Message) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if msg.text or msg.caption:
        payload["text"] = msg.text or msg.caption
    if msg.reply_markup and isinstance(msg.reply_markup, InlineKeyboardMarkup):
        rows = []
        for row in msg.reply_markup.inline_keyboard:
            rows.append([
                {
                    "text": b.text,
                    "url": getattr(b, "url", None),
                    "callback_data": getattr(b, "callback_data", None),
                    "switch_inline_query": getattr(b, "switch_inline_query", None),
                    "switch_inline_query_current_chat": getattr(b, "switch_inline_query_current_chat", None),
                }
                for b in row
            ])
        payload["buttons"] = rows
    kind = "text"
    if msg.photo:
        kind = "photo"; payload["file_id"] = msg.photo.file_id
    elif msg.video:
        kind = "video"; payload["file_id"] = msg.video.file_id
    elif msg.document:
        kind = "document"; payload["file_id"] = msg.document.file_id
    elif msg.sticker:
        kind = "sticker"; payload["file_id"] = msg.sticker.file_id
    elif msg.animation:
        kind = "animation"; payload["file_id"] = msg.animation.file_id
    elif msg.audio:
        kind = "audio"; payload["file_id"] = msg.audio.file_id
    elif msg.voice:
        kind = "voice"; payload["file_id"] = msg.voice.file_id
    elif msg.video_note:
        kind = "video_note"; payload["file_id"] = msg.video_note.file_id
    payload["kind"] = kind
    return payload

def build_reply_markup(button_rows: Optional[List[List[Dict[str, Any]]]]) -> Optional[InlineKeyboardMarkup]:
    if not button_rows:
        return None
    rows = []
    for row in button_rows:
        btns = []
        for b in row:
            if b.get("url"):
                btns.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
            elif b.get("callback_data"):
                btns.append(InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]))
            elif b.get("switch_inline_query"):
                btns.append(InlineKeyboardButton(text=b["text"], switch_inline_query=b["switch_inline_query"]))
            elif b.get("switch_inline_query_current_chat"):
                btns.append(InlineKeyboardButton(text=b["text"], switch_inline_query_current_chat=b["switch_inline_query_current_chat"]))
            else:
                btns.append(InlineKeyboardButton(text=b.get("text", "Button"), callback_data="noop"))
        rows.append(btns)
    return InlineKeyboardMarkup(rows)

async def run_user_client_loop():
    while True:
        session_string = get_config("SESSION_STRING")
        if not session_string:
            log.info("Waiting for SESSION_STRING in DB… use /generate_session in std bot DM.")
            await asyncio.sleep(3)
            continue
        try:
            user_app = Client(
                name="user-client",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
                in_memory=True,
            )
            @user_app.on_message(filters.private & filters.incoming & ~filters.me)
            async def dm_listener(client: Client, message: Message):
                try:
                    if not message.from_user or message.from_user.is_bot:
                        return
                    doc = {
                        "type": TYPE_DM_FLOW,
                        "status": STATUS_NEW_DM,
                        "sender_id": message.from_user.id,
                        "target_chat_id": message.chat.id,
                        "dm_message_id": message.id,
                        "group_topic_id": None,
                        "group_message_id": None,
                        "content_in": extract_content(message),
                        "content_out": None,
                        "created_at": now(),
                        "updated_at": now(),
                    }
                    JOBS.insert_one(doc)
                    log.info("New DM job created for sender %s", message.from_user.id)
                except Exception as e:
                    log.exception("Error in dm_listener: %s", e)
            async def user_background():
                log.info("USER client background loop started")
                while True:
                    try:
                        job = JOBS.find_one_and_update(
                            {"status": STATUS_READY_TO_SEND},
                            {"$set": {"status": STATUS_SENDING, "updated_at": now()}},
                        )
                        if not job:
                            await asyncio.sleep(1.0)
                            continue
                        content = job.get("content_out") or {}
                        kind = content.get("kind", "text")
                        text = content.get("text")
                        file_id = content.get("file_id")
                        markup = build_reply_markup(content.get("buttons"))
                        target_id = job.get("target_chat_id") or job.get("sender_id")
                        try:
                            if kind == "text":
                                await user_app.send_message(target_id, text=text or "(no text)", protect_content=True, reply_markup=markup)
                            elif kind == "photo":
                                await user_app.send_photo(target_id, photo=file_id, caption=text, protect_content=True, reply_markup=markup)
                            elif kind == "video":
                                await user_app.send_video(target_id, video=file_id, caption=text, protect_content=True, reply_markup=markup)
                            elif kind == "document":
                                await user_app.send_document(target_id, document=file_id, caption=text, protect_content=True, reply_markup=markup)
                            elif kind == "sticker":
                                await user_app.send_sticker(target_id, sticker=file_id, protect_content=True)
                            elif kind == "animation":
                                await user_app.send_animation(target_id, animation=file_id, caption=text, protect_content=True, reply_markup=markup)
                            elif kind == "audio":
                                await user_app.send_audio(target_id, audio=file_id, caption=text, protect_content=True, reply_markup=markup)
                            elif kind == "voice":
                                await user_app.send_voice(target_id, voice=file_id, caption=text, protect_content=True, reply_markup=markup)
                            elif kind == "video_note":
                                await user_app.send_video_note(target_id, video_note=file_id, protect_content=True)
                            else:
                                await user_app.send_message(target_id, text=text or "(unsupported kind treated as text)", protect_content=True, reply_markup=markup)
                            JOBS.find_one_and_update(
                                {"_id": job["_id"]},
                                {"$set": {"status": STATUS_COMPLETED, "updated_at": now()}}
                            )
                            log.info("Protected send completed for job %s", job["_id"])
                        except Exception as e:
                            JOBS.find_one_and_update(
                                {"_id": job["_id"]},
                                {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}}
                            )
                            log.exception("Send failed for job %s: %s", job["_id"], e)
                    except Exception as loop_err:
                        log.exception("USER loop error: %s", loop_err)
                        await asyncio.sleep(2)
            await user_app.start()
            log.info("USER client started and idling")
            bg = asyncio.create_task(user_background())
            try:
                await asyncio.Event().wait()
            finally:
                bg.cancel()
                await user_app.stop()
        except Exception as e:
            log.exception("USER client failed: %s", e)
            await asyncio.sleep(5)

if __name__ == "__main__":
    log.info("Starting USER bot...")
    try:
        asyncio.run(run_user_client_loop())
    except Exception as e:
        log.error("Failed to start USER bot: %s", e)
        sys.exit(1)
