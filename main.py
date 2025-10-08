import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# --- Failsafe Print Statements ---
print("--- SCRIPT EXECUTION STARTED ---")

# =================================================================
# === ALL IMPORTS (YAHAN SABHI IMPORTS HAIN) ===
# =================================================================
try:
    from pymongo import MongoClient
    from pymongo.collection import Collection
    from pymongo.errors import PyMongoError
    from pyrogram import Client, filters
    from pyrogram.enums import ChatType
    from pyrogram.handlers import MessageHandler
    from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
    from pyrogram.errors import FloodWait
except ImportError as e:
    print(f"❌ FATAL: Koi zaroori library install nahi hai: {e}")
    print("Please run: pip install pyrogram pymongo dnspython pyromod")
    sys.exit(1)
# =================================================================


# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("HybridShieldBot")

# --- Configuration Loading ---
try:
    print("STEP 1: Loading environment variables...")
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
    print("✅ STEP 1: Environment variables loaded successfully.")
    if not all([API_ID, API_HASH, BOT_TOKEN, MONGO_URI, OWNER_ID]):
        print("❌ FATAL: Ek ya ek se zyada environment variable loaded hai, lekin KHAALI (EMPTY) hai. Check karein.")
        sys.exit(1)
except (KeyError, ValueError) as e:
    print(f"❌ FATAL: Environment variable load nahi hua ya galat hai: {e}. Script band ho rahi hai.")
    sys.exit(1)

# --- Client Initialization ---
bot_app = Client("std-bot-client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_app: Optional[Client] = None

# --- MongoDB Variables ---
mongo_client = None
JOBS: Optional[Collection] = None
CONFIG: Optional[Collection] = None

# --- Constants ---
STATUS_PENDING_REPLY = "PENDING_REPLY"
STATUS_COMPLETED = "COMPLETED"
STATUS_ERROR = "ERROR"

# --- Helper Functions ---
def now() -> datetime: return datetime.now(timezone.utc)

def set_config(key: str, value: Any):
    if CONFIG is None: log.warning("DB not connected, cannot set config."); return
    try:
        CONFIG.update_one({"key": key}, {"$set": {"value": value, "updated_at": now()}}, upsert=True)
    except PyMongoError as e:
        log.error(f"Config '{key}' set karte waqt error: {e}")

def get_config(key: str, default=None) -> Any:
    if CONFIG is None: return default
    try:
        doc = CONFIG.find_one({"key": key})
        return doc.get("value", default) if doc else default
    except PyMongoError as e:
        log.error(f"Config '{key}' get karte waqt error: {e}")
        return default

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
        payload["buttons"] = [[{"text": b.text, "url": b.url, "callback_data": b.callback_data} for b in row] for row in msg.reply_markup.inline_keyboard]
    return payload

def build_reply_markup(button_rows: Optional[List[List[Dict[str, Any]]]]) -> Optional[InlineKeyboardMarkup]:
    if not button_rows: return None
    return InlineKeyboardMarkup([ [InlineKeyboardButton(text=b["text"], url=b.get("url"), callback_data=b.get("callback_data")) for b in row] for row in button_rows])


# --- Core Sending Function ---
async def send_protected_message(job_id, job_data):
    log.info(f"Job ID '{job_id}' ke liye message bhejne ki koshish.")
    try:
        if not user_app or not user_app.is_connected:
            log.error(f"User client connected nahi hai. Job {job_id} fail hua.")
            return

        content = job_data.get("content_out", {})
        target_id = job_data.get("target_chat_id") or job_data.get("sender_id")
        if not target_id:
            log.error(f"Job ID '{job_id}' ke liye target user ID nahi mila.")
            if JOBS: JOBS.update_one({"_id": job_id}, {"$set": {"status": STATUS_ERROR, "error": "Target ID missing"}})
            return

        kind = content.get("kind", "text")
        text = content.get("text")
        file_id = content.get("file_id")
        markup = build_reply_markup(content.get("buttons"))

        log.info(f"Target ID '{target_id}' ko '{kind}' type ka message bhej raha hai.")
        if kind == "text": await user_app.send_message(target_id, text, protect_content=True, reply_markup=markup)
        elif kind == "photo": await user_app.send_photo(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        elif kind == "video": await user_app.send_video(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        elif kind == "document": await user_app.send_document(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        
        if JOBS: JOBS.update_one({"_id": job_id}, {"$set": {"status": STATUS_COMPLETED, "updated_at": now()}})
        log.info(f"✅ Job ID '{job_id}' safaltapoorvak poora hua.")
    except Exception as e:
        log.exception(f"Message bhejte waqt Job ID '{job_id}' fail ho gaya: {e}")
        if JOBS: JOBS.update_one({"_id": job_id}, {"$set": {"status": STATUS_ERROR, "error": str(e)}})

# --- BOT HANDLERS ---
@bot_app.on_message(filters.command("health"))
async def health_check_cmd(client: Client, message: Message):
    log.info(">>> /health command received! <<<")
    db_status = "Connected" if mongo_client and CONFIG is not None else "Disconnected"
    user_app_status = "Running" if user_app and user_app.is_connected else "Not Running"
    await message.reply_text(
        f"✅ Bot is running.\n"
        f"- DB Status: `{db_status}`\n"
        f"- User Client: `{user_app_status}`\n\n"
        f"Your User ID is: `{message.from_user.id}`\n"
        f"Configured Owner ID is: `{OWNER_ID}`"
    )

@bot_app.on_message(filters.private & filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    log.info(">>> /start command received from OWNER <<<")
    await message.reply_text(
        "Bot is running! Available commands:\n"
        "/health - Bot ka status check karein\n"
        "/set_group - Inbox group set karein\n"
        "/generate_session - User session banayein"
    )

@bot_app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def set_group_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("This command must be used inside a group.")
        return
    set_config("INBOX_GROUP_ID", message.chat.id)
    await message.reply_text(f"✅ Inbox Group saved successfully!\nID: `{message.chat.id}`")
    log.info("INBOX_GROUP_ID set to %s", message.chat.id)

@bot_app.on_message(filters.private & filters.command("generate_session") & filters.user(OWNER_ID))
async def generate_session_cmd(client: Client, message: Message):
    if CONFIG is None: return await message.reply_text("Database connected nahi hai.")
    try:
        ask_phone = await client.ask(message.chat.id, "📲 Apna phone number country code ke saath bhejein (e.g., +919876543210).", timeout=300)
        phone = ask_phone.text.strip()
        temp_client = Client("temp-session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await temp_client.connect()
        try:
            code_info = await temp_client.send_code(phone)
            ask_code = await client.ask(message.chat.id, "🔐 Aapke number par bheja gaya OTP dalein.", timeout=300)
            code = ask_code.text.strip()
            await temp_client.sign_in(phone, code_info.phone_code_hash, code)
        except FloodWait as e:
            await message.reply_text(f"⏳ Flood wait: kripya {e.value} seconds intezar karein.")
            return
        except Exception as e:
            if "SESSION_PASSWORD_NEEDED" in str(e):
                ask_pwd = await client.ask(message.chat.id, "🔑 2FA password dalein.", timeout=300)
                await temp_client.check_password(ask_pwd.text.strip())
            else: raise e
        session_string = await temp_client.export_session_string()
        set_config("SESSION_STRING", session_string)
        await temp_client.disconnect()
        await message.reply_text("✅ Session string database mein save ho gaya hai. Bot restart karein.")
        log.info("Session generated and saved.")
    except Exception as e:
        log.exception(f"Session generate karte waqt error: {e}")
        await message.reply_text(f"❌ Error aaya: {e}")

@bot_app.on_message(filters.group & filters.user(OWNER_ID) & filters.reply)
async def on_owner_reply(client: Client, message: Message):
    log.info(f"Owner se group ({message.chat.id}) mein ek reply aaya.")
    if JOBS is None: return
    
    inbox_group_id = get_config("INBOX_GROUP_ID")
    if not inbox_group_id or message.chat.id != inbox_group_id:
        return
        
    try:
        job = JOBS.find_one({"group_message_id": message.reply_to_message_id, "status": STATUS_PENDING_REPLY})
        if not job: return

        log.info(f"Job ID '{job['_id']}' mil gaya. Reply content nikal raha hoon.")
        content = extract_content(message)
        
        updated_job_data = {"content_out": content, "updated_at": now()}
        JOBS.update_one({"_id": job["_id"]}, {"$set": updated_job_data})
        
        full_job_data = {**job, **updated_job_data}
        asyncio.create_task(send_protected_message(job["_id"], full_job_data))

        await message.reply_text(f"✅ User `{job.get('sender_id')}` ko reply bheja ja raha hai.", quote=True)
    except Exception as e:
        log.exception(f"Owner ke reply ko process karte waqt error: {e}")

# --- USER HANDLER ---
async def on_incoming_dm(client: Client, message: Message):
    if not message.from_user or message.from_user.is_self or message.from_user.is_bot: return
    if JOBS is None: return

    log.info(f"User ID: {message.from_user.id} se naya DM aaya.")
    try:
        group_id = get_config("INBOX_GROUP_ID")
        if not group_id:
            log.warning(f"INBOX_GROUP_ID set nahi hai. User {message.from_user.id} ka DM process nahi kar sakta.")
            return
        
        job_doc = {"status": STATUS_PENDING_REPLY, "sender_id": message.from_user.id, "content_in": extract_content(message), "created_at": now()}
        job_res = JOBS.insert_one(job_doc)
        log.info(f"Naya DM job banaya. ID: {job_res.inserted_id}")

        sender_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
        header = f"📩 **Naya message:** `{sender_name}`\n👤 **User ID:** `{message.from_user.id}`"
        
        fwd_msg = await message.forward(group_id)
        await bot_app.send_message(chat_id=group_id, text=header, reply_to_message_id=fwd_msg.id)

        JOBS.update_one({"_id": job_res.inserted_id}, {"$set": {"group_message_id": fwd_msg.id}})
        log.info(f"Job ID {job_res.inserted_id} ko group message ID {fwd_msg.id} se update kiya.")
    except Exception as e:
        log.exception(f"Naye DM ko process karte waqt error (from user {message.from_user.id}): {e}")

# --- DB Connection Function ---
async def connect_to_db():
    global mongo_client, JOBS, CONFIG
    try:
        print("STEP 3: Connecting to MongoDB...")
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.server_info()
        db = mongo_client["ultimate_hybrid_shieldbot"]
        JOBS = db["jobs"]
        CONFIG = db["config"]
        print("✅ STEP 3: MongoDB connected successfully.")
        return True
    except Exception as e:
        print(f"❌ FATAL: MongoDB se connect nahi ho paya: {e}")
        print("WARNING: Please check your MONGO_URI and IP Whitelist in MongoDB Atlas.")
        return False

# --- MAIN EXECUTION ---
async def main():
    global user_app
    
    # STEP 2: BOT APP START KARO
    try:
        print("\nSTEP 2: Starting Bot Client...")
        await bot_app.start()
        me = await bot_app.get_me()
        print(f"✅ STEP 2: Bot Client started successfully as @{me.username}")
        print("\n!!! IMPORTANT: Ab bot ko /health command bhejein !!!\n")
    except Exception as e:
        print(f"❌ FATAL: Bot Client start nahi ho paya: {e}"); return

    # STEP 3: DATABASE CONNECT KARO
    if not await connect_to_db():
        print("DB connection failed, bot will run with limited functionality.")
    
    # STEP 4: USER APP START KARO
    if CONFIG is not None:
        print("STEP 4: Checking for User session string...")
        session_string = get_config("SESSION_STRING")
        if session_string:
            print("Session string found. Starting User Client...")
            user_app = Client("user-client", api_id=API_ID, api_hash=API_HASH, session_string=session_string)
            user_app.add_handler(MessageHandler(on_incoming_dm, filters.private & ~filters.me))
            try:
                await user_app.start()
                me = await user_app.get_me()
                print(f"✅ STEP 4: User Client started successfully as {me.first_name}")
            except Exception as e:
                print(f"❌ ERROR: User Client start nahi ho paya: {e}"); user_app = None
        else:
            print("INFO: No session string found. User Client not started. Use /generate_session.")
    else:
        print("INFO: Skipping User Client start because DB is not connected.")

    print("\n--- Bot is now fully running. Press Ctrl+C to stop. ---")
    await asyncio.Event().wait()
    
    print("\nShutting down...")
    await bot_app.stop()
    if user_app and user_app.is_connected: await user_app.stop()

if __name__ == "__main__":
    asyncio.run(main())
                               
