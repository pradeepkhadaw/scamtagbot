import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from pyromod import listen

# Configuration
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
except KeyError as e:
    print(f"Missing environment variable: {e}")
    sys.exit(1)
except ValueError as e:
    print(f"Invalid environment variable format: {e}")
    sys.exit(1)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("shieldbot")

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
TYPE_MANUAL_SEND = "MANUAL_SEND"

# Create MongoDB indexes
try:
    JOBS.create_index("status")
    JOBS.create_index("created_at")
    JOBS.create_index("updated_at")
    JOBS.create_index([("sender_id", 1), ("group_topic_id", 1)])
    JOBS.create_index("group_message_id")
    JOBS.create_index("dm_message_id")
    CONFIG.create_index("key", unique=True)
except PyMongoError as e:
    log.error("Failed to create MongoDB indexes: %s", e)

def now():
    return datetime.now(timezone.utc)

def set_config(key: str, value: Any):
    try:
        CONFIG.find_one_and_update(
            {"key": key},
            {"$set": {"key": key, "value": value, "updated_at": now()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        log.info("Set config %s", key)
    except PyMongoError as e:
        log.error("Failed to set config %s: %s", key, e)

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

# Standard bot client
std_app = Client(
    name="std-bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("start"))
async def start_cmd(client: Client, message: Message):
    try:
        await client.send_message(
            chat_id=message.chat.id,
            text="Bot is running! Available commands:\n/start - This message\n/status - Check setup\n/generate_session - Create user session\n/send_protected - Manual protected send"
        )
        log.info("Start command triggered by owner %s", OWNER_ID)
    except Exception as e:
        log.exception("Error in start_cmd: %s", e)
        await client.send_message(message.chat.id, f"Error: {e}")

@std_app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def cmd_set_group(client: Client, message: Message):
    try:
        log.info("Set_group command triggered")
        if message.chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP):
            return await client.send_message(message.chat.id, "Run /set_group **inside** the Inbox Group (forum-enabled).")
        set_config("INBOX_GROUP_ID", message.chat.id)
        await client.send_message(message.chat.id, f"Inbox Group saved to DB: <code>{message.chat.id}</code>")
        log.info("INBOX_GROUP_ID set to %s", message.chat.id)
    except Exception as e:
        log.exception("Error in set_group: %s", e)
        await client.send_message(message.chat.id, f"Error: {e}")

@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("status"))
async def cmd_status(client: Client, message: Message):
    try:
        log.info("Status command triggered")
        gid = get_config("INBOX_GROUP_ID")
        sess = bool(get_config("SESSION_STRING"))
        await client.send_message(
            message.chat.id,
            "\n".join([
                "⚙️ Status:",
                f"• Session in DB: {'✅' if sess else '❌'}",
                f"• Inbox Group ID: {gid if gid else '❌ not set'}",
            ])
        )
    except Exception as e:
        log.exception("Error in status: %s", e)
        await client.send_message(message.chat.id, f"Error: {e}")

@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("generate_session"))
async def generate_session(client: Client, message: Message):
    try:
        log.info("Generate_session command triggered")
        chat_id = message.chat.id
        phone_msg = await client.listen(chat_id=chat_id, filters=filters.text, timeout=60)
        phone = phone_msg.text.strip()

        temp = Client(name="temp-session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        async with temp:
            sent = await temp.send_code(phone)
            code_msg = await client.listen(chat_id=chat_id, filters=filters.text, timeout=60)
            code = code_msg.text.strip()

            try:
                await temp.sign_in(phone, sent.phone_code_hash, code)
            except Exception as e:
                if "SESSION_PASSWORD_NEEDED" in str(e):
                    pwd_msg = await client.listen(chat_id=chat_id, filters=filters.text, timeout=60)
                    await temp.check_password(pwd_msg.text.strip())
                else:
                    raise

            session_string = await temp.export_session_string()
            set_config("SESSION_STRING", session_string)
            await client.send_message(chat_id, "✅ Session saved to DB. The user client will start automatically.\nYou can /status to verify.")
            log.info("Session generated and saved for phone %s", phone)
    except FloodWait as e:
        log.warning("FloodWait in session gen: %s seconds", e.value)
        await asyncio.sleep(e.value)
        await client.send_message(message.chat.id, "⏳ Retrying after flood wait...")
    except Exception as e:
        log.exception("Session generation error: %s", e)
        await client.send_message(message.chat.id, f"❌ Error: {e}")

@std_app.on_message(
    filters.chat(lambda _, __, m: get_config("INBOX_GROUP_ID") == (m.chat.id if m.chat else None)) & filters.user(OWNER_ID)
)
async def owner_group_replies(client: Client, message: Message):
    try:
        log.info("Owner group reply detected")
        if not message.reply_to_message:
            return
        group_message_id = message.reply_to_message.id
        job = JOBS.find_one({"status": STATUS_PENDING_REPLY, "group_message_id": group_message_id})
        if not job:
            return
        content = extract_content(message)
        JOBS.find_one_and_update(
            {"_id": job["_id"]},
            {"$set": {"content_out": content, "status": STATUS_READY_TO_SEND, "updated_at": now()}},
        )
        await client.send_message(message.chat.id, "Queued for protected send by user client ✅")
        log.info("Job %s marked ready to send", job["_id"])
    except Exception as e:
        log.exception("Error in owner_group_replies: %s", e)
        await client.send_message(message.chat.id, f"Error: {e}")

@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("send_protected"))
async def cmd_send_protected(client: Client, message: Message):
    try:
        log.info("Send_protected command triggered")
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return await client.send_message(message.chat.id, "Usage: reply to content with\n/send_protected <TARGET_CHAT_ID>")
        try:
            target_id = int(parts[1].strip())
        except ValueError:
            return await client.send_message(message.chat.id, "TARGET_CHAT_ID must be numeric.")
        if not message.reply_to_message:
            return await client.send_message(message.chat.id, "Please REPLY to the content you want to send protected.")
        content = extract_content(message.reply_to_message)
        doc = {
            "type": TYPE_MANUAL_SEND,
            "status": STATUS_READY_TO_SEND,
            "sender_id": OWNER_ID,
            "target_chat_id": target_id,
            "dm_message_id": None,
            "group_topic_id": None,
            "group_message_id": None,
            "content_in": None,
            "content_out": content,
            "created_at": now(),
            "updated_at": now(),
        }
        res = JOBS.insert_one(doc)
        await client.send_message(message.chat.id, f"Manual protected send queued. Job: <code>{res.inserted_id}</code>")
        log.info("Manual send job created: %s", res.inserted_id)
    except Exception as e:
        log.exception("Error in send_protected: %s", e)
        await client.send_message(message.chat.id, f"Error: {e}")

async def std_background():
    log.info("STD bot background loop started")
    while True:
        try:
            group_id = get_config("INBOX_GROUP_ID")
            if not group_id:
                await asyncio.sleep(2)
                continue
            job = JOBS.find_one_and_update(
                {"status": STATUS_NEW_DM},
                {"$set": {"status": STATUS_PENDING_REPLY, "updated_at": now()}},
            )
            if not job:
                await asyncio.sleep(1.2)
                continue
            sender_id = job["sender_id"]
            topic_id = job.get("group_topic_id")
            if not topic_id:
                topic_title = f"DM {sender_id}"
                try:
                    topic = await std_app.create_forum_topic(group_id, topic_title)
                    topic_id = topic.message_thread_id
                except Exception as e:
                    log.warning("Topic creation failed, falling back to no topic: %s", e)
                    topic_id = None
            content_in = job.get("content_in") or {}
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
                    sent = await std_app.send_message(**send_kwargs, text=text or "(no text)")
                elif kind == "photo":
                    sent = await std_app.send_photo(**send_kwargs, photo=file_id, caption=text)
                elif kind == "video":
                    sent = await std_app.send_video(**send_kwargs, video=file_id, caption=text)
                elif kind == "document":
                    sent = await std_app.send_document(**send_kwargs, document=file_id, caption=text)
                elif kind == "sticker":
                    sent = await std_app.send_sticker(**send_kwargs, sticker=file_id)
                elif kind == "animation":
                    sent = await std_app.send_animation(**send_kwargs, animation=file_id, caption=text)
                elif kind == "audio":
                    sent = await std_app.send_audio(**send_kwargs, audio=file_id, caption=text)
                elif kind == "voice":
                    sent = await std_app.send_voice(**send_kwargs, voice=file_id, caption=text)
                elif kind == "video_note":
                    sent = await std_app.send_video_note(**send_kwargs, video_note=file_id)
                else:
                    sent = await std_app.send_message(**send_kwargs, text=text or "(unsupported kind treated as text)")
                JOBS.find_one_and_update(
                    {"_id": job["_id"]},
                    {"$set": {
                        "group_topic_id": topic_id,
                        "group_message_id": sent.id if sent else None,
                        "status": STATUS_PENDING_REPLY,
                        "updated_at": now()
                    }},
                )
                log.info("Mirrored DM to group for job %s", job["_id"])
            except Exception as e:
                log.exception("Mirror to group failed: %s", e)
                JOBS.find_one_and_update(
                    {"_id": job["_id"]},
                    {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}}
                )
        except Exception as loop_err:
            log.exception("STD loop error: %s", loop_err)
            await asyncio.sleep(2)

user_app = None

async def run_user_client_loop():
    global user_app
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
            async with user_app:
                log.info("USER client started and idling")
                bg = asyncio.create_task(user_background())
                try:
                    await asyncio.Event().wait()
                finally:
                    bg.cancel()
                    await bg
        except Exception as e:
            log.exception("USER client failed: %s", e)
            await asyncio.sleep(5)

async def run_std():
    async with std_app:
        bg = asyncio.create_task(std_background())
        log.info("STD worker running")
        try:
            await asyncio.Event().wait()
        finally:
            bg.cancel()
            await bg

async def run_both():
    std_task = asyncio.create_task(run_std())
    user_task = asyncio.create_task(run_user_client_loop())
    await asyncio.gather(std_task, user_task)

if __name__ == "__main__":
    print("🚀 Entrypoint Triggered:", sys.argv)
    if len(sys.argv) < 2:
        print("Usage: python main.py [std|both]")
        sys.exit(1)
    role = sys.argv[1].strip().lower()
    if role == "std":
        asyncio.run(run_std())
    elif role == "both":
        asyncio.run(run_both())
    else:
        print("Invalid role: must be 'std' or 'both'")
        sys.exit(1)
