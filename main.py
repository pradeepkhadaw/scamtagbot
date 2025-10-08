import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# --- Libraries ---
from pymongo import MongoClient
from pymongo.collection import Collection
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("HybridShieldBot")

# --- Configuration (Full) ---
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
except (KeyError, ValueError) as e:
    log.error(f"FATAL: Environment Variable galat hai ya set nahi hai: {e}")
    sys.exit(1)

# --- MongoDB Setup ---
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client["ultimate_hybrid_shieldbot"]
    JOBS: Collection = db["jobs"]
    CONFIG: Collection = db["config"]
    log.info("Successfully connected to MongoDB.")
except Exception as e:
    log.error(f"Failed to connect to MongoDB: {e}")
    sys.exit(1)


# --- Helper Functions ---
def now() -> datetime: return datetime.now(timezone.utc)

def set_config(key: str, value: Any):
    CONFIG.update_one({"key": key}, {"$set": {"value": value, "updated_at": now()}}, upsert=True)

def get_config(key: str, default=None) -> Any:
    doc = CONFIG.find_one({"key": key})
    return doc.get("value", default) if doc else default

def extract_content(msg: Message) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"kind": "text"}
    if msg.text: payload["text"] = msg.text
    elif msg.caption: payload["text"] = msg.caption
    if msg.photo:
        payload["kind"] = "photo"; payload["file_id"] = msg.photo.file_id
    elif msg.video:
        payload["kind"] = "video"; payload["file_id"] = msg.video.file_id
    elif msg.document:
        payload["kind"] = "document"; payload["file_id"] = msg.document.file_id
    if msg.reply_markup and isinstance(msg.reply_markup, InlineKeyboardMarkup):
        payload["buttons"] = [[{"text": b.text, "url": b.url} for b in row] for row in msg.reply_markup.inline_keyboard]
    return payload

def build_reply_markup(button_rows) -> Optional[InlineKeyboardMarkup]:
    if not button_rows: return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(text=b["text"], url=b.get("url")) for b in row] for row in button_rows])


# =================================================================
# === CLIENTS DEFINITION (BOT + USER) ===
# =================================================================
# Bot Client (Commands aur Group mein replies ke liye)
bot_app = Client(
    "bot_session",
    api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN
)

# User Client (DMs receive karne aur protected message bhejne ke liye)
# Yeh None se start hoga aur session milne par start hoga
user_app: Optional[Client] = None
session_string = get_config("SESSION_STRING")
if session_string:
    user_app = Client(
        "user_session",
        api_id=API_ID, api_hash=API_HASH, session_string=session_string
    )
# =================================================================


# --- Core Sending Function ---
async def send_protected_message(job_id, job_data):
    if not user_app or not user_app.is_connected:
        log.error(f"User client connected nahi hai. Job {job_id} fail hua.")
        return
    # (Full logic for sending message as before)
    target_id = job_data.get("sender_id")
    content = job_data.get("content_out", {})
    kind, text, file_id, markup = content.get("kind"), content.get("text"), content.get("file_id"), build_reply_markup(content.get("buttons"))
    
    try:
        if kind == "text": await user_app.send_message(target_id, text, protect_content=True, reply_markup=markup)
        elif kind == "photo": await user_app.send_photo(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        elif kind == "video": await user_app.send_video(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        elif kind == "document": await user_app.send_document(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        JOBS.update_one({"_id": job_id}, {"$set": {"status": "COMPLETED"}})
        log.info(f"✅ Job {job_id} safaltapoorvak poora hua.")
    except Exception as e:
        log.error(f"Message bhejte waqt Job ID '{job_id}' fail ho gaya: {e}")
        JOBS.update_one({"_id": job_id}, {"$set": {"status": "ERROR", "error": str(e)}})


# --- BOT HANDLERS (`bot_app`) ---
@bot_app.on_message(filters.private & filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    await message.reply_text("Bot chal raha hai! Commands:\n/set_group\n/generate_session")

@bot_app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def set_group_cmd(client: Client, message: Message):
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        set_config("INBOX_GROUP_ID", message.chat.id)
        await message.reply_text(f"✅ Inbox Group save ho gaya: `{message.chat.id}`")
    else:
        await message.reply_text("Yeh command group ke andar use karein.")

@bot_app.on_message(filters.private & filters.command("generate_session") & filters.user(OWNER_ID))
async def generate_session_cmd(client: Client, message: Message):
    # (Full logic for generating session as before)
    try:
        ask_phone = await client.ask(message.chat.id, "📲 Apna phone number country code ke saath bhejein.", timeout=300)
        phone = ask_phone.text.strip()
        temp_client = Client("temp_session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await temp_client.connect()
        try:
            code_info = await temp_client.send_code(phone)
            ask_code = await client.ask(message.chat.id, "🔐 OTP dalein.", timeout=300)
            code = ask_code.text.strip()
            await temp_client.sign_in(phone, code_info.phone_code_hash, code)
        except Exception as e:
            if "SESSION_PASSWORD_NEEDED" in str(e):
                ask_pwd = await client.ask(message.chat.id, "🔑 2FA password dalein.", timeout=300)
                await temp_client.check_password(ask_pwd.text.strip())
            else: raise e
        session_string = await temp_client.export_session_string()
        set_config("SESSION_STRING", session_string)
        await temp_client.disconnect()
        await message.reply_text("✅ Session save ho gaya hai. Bot restart karein.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")

@bot_app.on_message(filters.group & filters.user(OWNER_ID) & filters.reply)
async def on_owner_reply(client: Client, message: Message):
    inbox_group_id = get_config("INBOX_GROUP_ID")
    if not inbox_group_id or message.chat.id != inbox_group_id: return
    
    job = JOBS.find_one({"group_message_id": message.reply_to_message_id, "status": "PENDING_REPLY"})
    if not job: return
        
    content = extract_content(message)
    updated_job_data = {"content_out": content, "updated_at": now()}
    JOBS.update_one({"_id": job["_id"]}, {"$set": updated_job_data})
    
    full_job_data = {**job, **updated_job_data}
    asyncio.create_task(send_protected_message(job["_id"], full_job_data))
    await message.reply_text(f"✅ User `{job.get('sender_id')}` ko reply bheja ja raha hai.", quote=True)


# --- USER HANDLER (`user_app`) ---
async def on_incoming_dm(client: Client, message: Message):
    if not message.from_user or message.from_user.is_self: return

    group_id = get_config("INBOX_GROUP_ID")
    if not group_id: return log.warning("INBOX_GROUP_ID set nahi hai.")

    job_doc = {"status": "PENDING_REPLY", "sender_id": message.from_user.id, "content_in": extract_content(message), "created_at": now()}
    job_res = JOBS.insert_one(job_doc)
    
    sender_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
    header = f"📩 Naya message: `{sender_name}`\n👤 User ID: `{message.from_user.id}`"
    fwd_msg = await message.forward(group_id)
    await bot_app.send_message(group_id, header, reply_to_message_id=fwd_msg.id)
    JOBS.update_one({"_id": job_res.inserted_id}, {"$set": {"group_message_id": fwd_msg.id}})

# Handler ko user_app se jodna (agar user_app hai toh)
if user_app:
    user_app.add_handler(MessageHandler(on_incoming_dm, filters.private & ~filters.me))


# --- Sabse Simple Start Karne ka Tarika ---
async def main():
    """Dono clients ko ek saath start aur run karta hai"""
    log.info("Starting clients...")
    clients_to_run = [bot_app]
    if user_app:
        clients_to_run.append(user_app)
    
    # Yeh Pyrogram ka official tarika hai multiple clients chalane ka
    await Client.compose(clients_to_run)


if __name__ == "__main__":
    # Yeh line `main` function ko run karti hai jo sab kuch chalu rakhega
    asyncio.run(main())

