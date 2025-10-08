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
                }
                for b in row
            ])
        payload["buttons"] = rows
    kind = "text"
    if msg.photo:
        kind = "photo"; payload["file_id"] = msg.photo.file_id
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
                content_in = doc["content_in"]
                kind = content_in.get("kind", "text")
                text = content_in.get("text")
                file_id = content_in.get("file_id")
                markup = build_reply_markup(content_in.get("buttons"))
                sent: Optional[Message] = None
                try:
                    if kind == "text":
                        sent = await client.send_message(group_id, text=text or "(no text)", reply_markup=markup)
                    elif kind == "photo":
                        sent = await client.send_photo(group_id, photo=file_id, caption=text, reply_markup=markup)
                    JOBS.find_one_and_update(
                        {"_id": res.inserted_id},
                        {"$set": {
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
