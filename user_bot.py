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

user_app = None

async def start_user_client():
    global user_app
    session_string = get_config("SESSION_STRING")
    if not session_string:
        log.info("No SESSION_STRING found, user client not started. Use /generate_session in std bot.")
        return
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
                group_id = get_config("INBOX_GROUP_ID")
                if not group_id:
                    log.warning("No INBOX_GROUP_ID set, cannot forward DM")
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
                res = JOBS.insert_one(doc)
                log.info("New DM job created for sender %s, job %s", message.from_user.id, res.inserted_id)

                # Forward to group
                sender_id = message.from_user.id
                topic_id = None
                try:
                    topic = await client.send_message(group_id, f"Creating topic for DM {sender_id}")
                    topic_id = topic.message_thread_id
                    log.info("Created topic %s for sender %s", topic_id, sender_id)
                except Exception as e:
                    log.warning("Topic creation failed, falling back to no topic: %s", e)

                content_in = doc["content_in"]
                kind = content_in.get("kind", "text")
                text = content_in.get("text")
                file_id = content_in.get("file_id")
                markup = build_reply_markup(content_in.get("buttons"))
                send_kwargs = {"chat_id": group_id, "reply_markup": markup}
                if topic_id:
                    send_kwargs["message_thread_id"] = topic_id

                sent: Optional[Message] = None
                try:
                    if kind == "text":
                        sent = await client.send_message(**send_kwargs, text=text or "(no text)")
                    elif kind == "photo":
                        sent = await client.send_photo(**send_kwargs, photo=file_id, caption=text)
                    elif kind == "video":
                        sent = await client.send_video(**send_kwargs, video=file_id, caption=text)
                    elif kind == "document":
                        sent = await client.send_document(**send_kwargs, document=file_id, caption=text)
                    elif kind == "sticker":
                        sent = await client.send_sticker(**send_kwargs, sticker=file_id)
                    elif kind == "animation":
                        sent = await client.send_animation(**send_kwargs, animation=file_id, caption=text)
                    elif kind == "audio":
                        sent = await client.send_audio(**send_kwargs, audio=file_id, caption=text)
                    elif kind == "voice":
                        sent = await client.send_voice(**send_kwargs, voice=file_id, caption=text)
                    elif kind == "video_note":
                        sent = await client.send_video_note(**send_kwargs, video_note=file_id)
                    else:
                        sent = await client.send_message(**send_kwargs, text=text or "(unsupported kind treated as text)")
                    JOBS.find_one_and_update(
                        {"_id": res.inserted_id},
                        {"$set": {
                            "group_topic_id": topic_id,
                            "group_message_id": sent.id if sent else None,
                            "status": STATUS_PENDING_REPLY,
                            "updated_at": now()
                        }},
                    )
                    log.info("Mirrored DM to group for job %s", res.inserted_id)
                except Exception as e:
                    log.exception("Mirror to group failed for job %s: %s", res.inserted_id, e)
                    JOBS.find_one_and_update(
                        {"_id": res.inserted_id},
                        {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}}
                    )
            except Exception as e:
                log.exception("Error in dm_listener: %s", e)

        @user_app.on_message(filters.all)
        async def process_jobs(client: Client, message: Message):
            try:
                job = JOBS.find_one({"status": STATUS_READY_TO_SEND})
                if not job:
                    return
                JOBS.find_one_and_update(
                    {"_id": job["_id"]},
                    {"$set": {"status": STATUS_SENDING, "updated_at": now()}},
                )
                content = job.get("content_out") or {}
                kind = content.get("kind", "text")
                text = content.get("text")
                file_id = content.get("file_id")
                markup = build_reply_markup(content.get("buttons"))
                target_id = job.get("target_chat_id") or job.get("sender_id")
                try:
                    if kind == "text":
                        await client.send_message(target_id, text=text or "(no text)", protect_content=True, reply_markup=markup)
                    elif kind == "photo":
                        await client.send_photo(target_id, photo=file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "video":
                        await client.send_video(target_id, video=file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "document":
                        await client.send_document(target_id, document=file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "sticker":
                        await client.send_sticker(target_id, sticker=file_id, protect_content=True)
                    elif kind == "animation":
                        await client.send_animation(target_id, animation=file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "audio":
                        await client.send_audio(target_id, audio=file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "voice":
                        await client.send_voice(target_id, voice=file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "video_note":
                        await client.send_video_note(target_id, video_note=file_id, protect_content=True)
                    else:
                        await client.send_message(target_id, text=text or "(unsupported kind treated as text)", protect_content=True, reply_markup=markup)
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
            except Exception as e:
                log.exception("Error in process_jobs: %s", e)

        await user_app.start()
        log.info("USER client started and idling")
    except Exception as e:
        log.exception("Failed to start USER client: %s", e)
        user_app = None

async def main():
    await start_user_client()
    try:
        await asyncio.Event().wait()
    finally:
        if user_app:
            await user_app.stop()

if __name__ == "__main__":
    log.info("Starting USER bot...")
    try:
        asyncio.run(main())
    except Exception as e:
        log.error("Failed to start USER bot: %s", e)
        sys.exit(1)
