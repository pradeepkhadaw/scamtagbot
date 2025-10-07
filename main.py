# main.py
# Ultimate Hybrid ShieldBot – Single-File Heroku Deploy (DB-driven setup)
# -----------------------------------------------------
# Features
# - Two workers from one file: `std` (Standard Bot admin/relay) and `user` (Pyrogram User Client)
# - All core secrets via env (API_ID/API_HASH/BOT_TOKEN/MONGO_URI/OWNER_ID);
#   Session string & Inbox group are configured **inside Telegram** and stored in MongoDB
# - MongoDB as the exclusive bridge + job queue + configuration store
# - DM inbox → group topics; owner replies harvested; final protected send ONLY by the user client
# - Manual protected send: /send_protected <TARGET_CHAT_ID> (reply to content)
# - Media + buttons supported via stored file_ids & inline keyboard markup

import os
import sys
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

# Enable simple conversational prompts
# (adds .ask to Chat and .listen to Client)
from pyromod import listen  # noqa: F401  (import side-effects)

# -----------------------------
# Environment & Globals
# -----------------------------
API_ID = int(os.environ["API_ID"])               # required
API_HASH = os.environ["API_HASH"]                # required
BOT_TOKEN = os.environ["BOT_TOKEN"]              # required
MONGO_URI = os.environ["MONGO_URI"]              # required
OWNER_ID = int(os.environ["OWNER_ID"])          # required

# Heroku-friendly logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("shieldbot")

# -----------------------------
# Mongo Setup & Helpers
# -----------------------------
client = MongoClient(MONGO_URI)
db = client["ultimate_hybrid_shieldbot"]
JOBS: Collection = db["jobs"]
CONFIG: Collection = db["config"]

STATUS_NEW_DM = "NEW_DM"
STATUS_PENDING_REPLY = "PENDING_REPLY"
STATUS_READY_TO_SEND = "READY_TO_SEND"
STATUS_COMPLETED = "COMPLETED"
STATUS_ERROR = "ERROR"

TYPE_DM_FLOW = "DM_FLOW"
TYPE_MANUAL_SEND = "MANUAL_SEND"

# Indexes
JOBS.create_index("status")
JOBS.create_index("created_at")
JOBS.create_index("updated_at")
JOBS.create_index([("sender_id", 1), ("group_topic_id", 1)])
JOBS.create_index("group_message_id")
JOBS.create_index("dm_message_id")
CONFIG.create_index("key", unique=True)


def now():
    return datetime.now(timezone.utc)

# -----------------------------
# Config Helpers
# -----------------------------

def set_config(key: str, value: Any):
    CONFIG.find_one_and_update(
        {"key": key},
        {"$set": {"key": key, "value": value, "updated_at": now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


def get_config(key: str, default=None):
    doc = CONFIG.find_one({"key": key})
    return doc["value"] if doc and "value" in doc else default

# -----------------------------
# Content helpers
# -----------------------------

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

# -------------------------------------------------
# Standard Bot (std) – Admin & Group Interface
# -------------------------------------------------

std_app = Client(
    name="std-bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)


@std_app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def cmd_set_group(client: Client, message: Message):
    if message.chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP):
        return await message.reply_text("Run /set_group **inside** the Inbox Group (forum-enabled).")
    set_config("INBOX_GROUP_ID", message.chat.id)
    await message.reply_text(f"Inbox Group saved to DB: <code>{message.chat.id}</code>")
    log.info("INBOX_GROUP_ID set to %s", message.chat.id)



@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("status"))
async def cmd_status(client: Client, message: Message):
    gid = get_config("INBOX_GROUP_ID")
    sess = bool(get_config("SESSION_STRING"))
    await message.reply_text(
        "\n".join([
            "⚙️ Status:",
            f"• Session in DB: {'✅' if sess else '❌'}",
            f"• Inbox Group ID: {gid if gid else '❌ not set'}",
        ])
    )


@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("generate_session"))
async def generate_session(client: Client, message: Message):
    """Interactive session generation; stores SESSION_STRING in MongoDB."""
    chat = message.chat
    try:
        phone_msg = await chat.ask("📲 Send your phone number with country code (e.g., +91xxxxxxxxxx):")
        phone = phone_msg.text.strip()

        temp = Client(name="temp-session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await temp.connect()

        sent = await temp.send_code(phone)
        code_msg = await chat.ask("🔐 Enter the code you received:")
        code = code_msg.text.strip()

        try:
            await temp.sign_in(phone, sent.phone_code_hash, code)
        except Exception as e:
            if "SESSION_PASSWORD_NEEDED" in str(e):
                pwd_msg = await chat.ask("🧩 2FA enabled. Enter your password:")
                await temp.check_password(pwd_msg.text.strip())
            else:
                raise

        session_string = await temp.export_session_string()
        set_config("SESSION_STRING", session_string)

        # ✅ Fixed line (used \\n for newline)
        await chat.send_message(
            "✅ Session saved to DB. The user client dyno will start automatically.\nYou can /status to verify."
        )

    except FloodWait as e:
        await asyncio.sleep(e.value)
        await chat.send_message("⏳ Retrying...")
    except Exception as e:
        await chat.send_message(f"❌ Error: {e}")
    finally:
        try:
            await temp.disconnect()
        except Exception:
            pass


@std_app.on_message(filters.chat(lambda _, __, m: get_config("INBOX_GROUP_ID") == (m.chat.id if m.chat else None)) & filters.user(OWNER_ID))
async def owner_group_replies(client: Client, message: Message):
    """Owner replies inside the Inbox Group → mark job READY_TO_SEND with content_out."""
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
    await message.reply_text("Queued for protected send by user client ✅")



@std_app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("send_protected"))
async def cmd_send_protected(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("Usage: reply to content with\n/send_protected <TARGET_CHAT_ID>")

    try:
        target_id = int(parts[1].strip())
    except ValueError:
        return await message.reply_text("TARGET_CHAT_ID must be numeric.")

    if not message.reply_to_message:
        return await message.reply_text("Please REPLY to the content you want to send protected.")

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
    await message.reply_text(f"Manual protected send queued. Job: <code>{res.inserted_id}</code>")


async def std_background():
    await std_app.start()
    log.info("STD bot started background loop")

    # Notify owner if config missing
    try:
        if not get_config("SESSION_STRING"):
            await std_app.send_message(OWNER_ID, "⚙️ No session found. Use /generate_session here to set it up.")
        if not get_config("INBOX_GROUP_ID"):
            await std_app.send_message(OWNER_ID, "⚙️ No inbox group set. Run /set_group in your target group.")
    except Exception:
        pass

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

            # Ensure a topic for the sender (title: DM <sender_id>)
            if not topic_id:
                topic_title = f"DM {sender_id}"
                try:
                    topic = await std_app.create_forum_topic(group_id, topic_title)
                    topic_id = topic.message_thread_id
                except AttributeError:
                    topic_id = None
                except Exception as e:
                    log.exception("Create topic failed: %s", e)
                    JOBS.find_one_and_update(
                        {"_id": job["_id"]},
                        {"$set": {
                            "status": STATUS_ERROR,
                            "error": str(e),
                            "updated_at": now()
                        }},
                    )
                    continue

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
            except Exception as e:
                log.exception("Mirror to group failed: %s", e)
                JOBS.find_one_and_update(
                    {"_id": job["_id"]},
                    {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}}
                )

        except Exception as loop_err:
            log.exception("STD loop error: %s", loop_err)
            await asyncio.sleep(2)


# -------------------------------------------------
# User Client (user) – Protected Sending Only Here
# -------------------------------------------------

async def run_user_client_loop():
    """Boot the user client when a session string exists in DB; poll READY_TO_SEND jobs."""
    # Wait for session string to appear in DB
    session_string = get_config("SESSION_STRING")
    while not session_string:
        log.info("Waiting for SESSION_STRING in DB… use /generate_session in std bot DM.")
        await asyncio.sleep(3)
        session_string = get_config("SESSION_STRING")

    user_app = Client(
        name="user-client",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
    )

    @user_app.on_message(filters.private & filters.incoming & ~filters.me)
    async def dm_listener(client: Client, message: Message):
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

    async def user_background():
        await user_app.start()
        log.info("USER client started background loop")
        while True:
            try:
                job = JOBS.find_one_and_update(
                    {"status": STATUS_READY_TO_SEND},
                    {"$set": {"status": "SENDING", "updated_at": now()}},
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
                except Exception as e:
                    JOBS.find_one_and_update(
                        {"_id": job["_id"]},
                        {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}}
                    )
            except Exception as loop_err:
                log.exception("USER loop error: %s", loop_err)
                await asyncio.sleep(2)

    # Run forever
    async with user_app:
        bg = asyncio.create_task(user_background())
        await asyncio.Event().wait()
        bg.cancel()


# -----------------------------
# Entrypoints
# -----------------------------

async def run_std():
    async with std_app:
        bg = asyncio.create_task(std_background())
        log.info("STD worker running")
        await asyncio.Event().wait()
        bg.cancel()


async def run_user():
    await run_user_client_loop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py [std|user]")
        sys.exit(1)

    role = sys.argv[1].strip().lower()
    if role not in {"std", "user"}:
        print("Role must be 'std' or 'user'")
        sys.exit(1)

    try:
        if role == "std":
            asyncio.run(run_std())
        else:
            asyncio.run(run_user())
    except KeyboardInterrupt:
        pass
