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
from pyrogram.enums import ChatType
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

# --- Configuration ---
# Environment variables load karo. Agar koi miss hua to script band ho jayegi.
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
except (KeyError, ValueError) as e:
    print(f"FATAL: Environment variable galat hai ya set nahi hai: {e}. Configuration check karein.")
    sys.exit(1)

# --- Logging Setup (Har step ko print karne ke liye) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("HybridShieldBot")

# --- MongoDB Setup ---
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.server_info()
    db = mongo_client["ultimate_hybrid_shieldbot"]
    JOBS: Collection = db["jobs"]
    CONFIG: Collection = db["config"]
    log.info("MongoDB se connection safal raha.")
except PyMongoError as e:
    log.error(f"MongoDB se connect nahi ho paya: {e}")
    sys.exit(1)

# --- Constants ---
STATUS_PENDING_REPLY = "PENDING_REPLY"
STATUS_COMPLETED = "COMPLETED"
STATUS_ERROR = "ERROR"

# --- Helper Functions ---
def now() -> datetime:
    return datetime.now(timezone.utc)

def set_config(key: str, value: Any):
    try:
        CONFIG.update_one({"key": key}, {"$set": {"value": value, "updated_at": now()}}, upsert=True)
        log.info(f"Config set kiya: '{key}'.")
    except PyMongoError as e:
        log.error(f"Config '{key}' set karte waqt error: {e}")

def get_config(key: str, default=None) -> Any:
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
        payload["kind"] = "photo"
        payload["file_id"] = msg.photo.file_id
    elif msg.video:
        payload["kind"] = "video"
        payload["file_id"] = msg.video.file_id
    elif msg.document:
        payload["kind"] = "document"
        payload["file_id"] = msg.document.file_id
    if msg.reply_markup and isinstance(msg.reply_markup, InlineKeyboardMarkup):
        payload["buttons"] = [[{"text": b.text, "url": b.url, "callback_data": b.callback_data} for b in row] for row in msg.reply_markup.inline_keyboard]
    return payload

def build_reply_markup(button_rows: Optional[List[List[Dict[str, Any]]]]) -> Optional[InlineKeyboardMarkup]:
    if not button_rows: return None
    return InlineKeyboardMarkup([ [InlineKeyboardButton(text=b["text"], url=b.get("url"), callback_data=b.get("callback_data")) for b in row] for row in button_rows])

# --- Client Initialization ---
bot_app = Client("std-bot-client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_app: Optional[Client] = None

# --- CORE SENDING FUNCTION (Isi function se message jayega) ---
async def send_protected_message(job_id, job_data):
    """
    Yeh function database se job details leta hai aur message bhejta hai.
    """
    log.info(f"Job ID '{job_id}' ke liye message bhejne ki koshish.")
    try:
        content = job_data.get("content_out", {})
        kind = content.get("kind", "text")
        text = content.get("text")
        file_id = content.get("file_id")
        markup = build_reply_markup(content.get("buttons"))
        
        # Target user ID nikalo (DM flow ya manual send se)
        target_id = job_data.get("target_chat_id") or job_data.get("sender_id")
        if not target_id:
            log.error(f"Job ID '{job_id}' ke liye target user ID nahi mila.")
            JOBS.update_one({"_id": job_id}, {"$set": {"status": STATUS_ERROR, "error": "Target ID missing", "updated_at": now()}})
            return

        log.info(f"User client se Target ID '{target_id}' ko '{kind}' type ka message bhej raha hai.")

        if kind == "text":
            await user_app.send_message(target_id, text, protect_content=True, reply_markup=markup)
        elif kind == "photo":
            await user_app.send_photo(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        elif kind == "video":
            await user_app.send_video(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        elif kind == "document":
             await user_app.send_document(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
        
        JOBS.update_one({"_id": job_id}, {"$set": {"status": STATUS_COMPLETED, "updated_at": now()}})
        log.info(f"✅ Job ID '{job_id}' safaltapoorvak poora hua.")

    except Exception as e:
        log.exception(f"Message bhejte waqt Job ID '{job_id}' fail ho gaya: {e}")
        JOBS.update_one({"_id": job_id}, {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}})


# --- BOT HANDLERS (`bot_app`) ---

@bot_app.on_message(filters.private & filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    log.info("Owner ne /start command use kiya.")
    await message.reply_text(
        "Bot chal raha hai!\n"
        "/status - Setup check karein\n"
        "/set_group - Inbox group set karein\n"
        "/generate_session - User session banayein\n"
        "/send_protected - Manual message bhejein"
    )

@bot_app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def set_group_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP):
        await message.reply_text("Yeh command group ke andar hi use karein.")
        return
    set_config("INBOX_GROUP_ID", message.chat.id)
    await message.reply_text(f"✅ Inbox Group save ho gaya!\nID: `{message.chat.id}`")
    log.info(f"INBOX_GROUP_ID set hua: {message.chat.id}")

@bot_app.on_message(filters.private & filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(client: Client, message: Message):
    gid = get_config("INBOX_GROUP_ID")
    sess = bool(get_config("SESSION_STRING"))
    log.info(f"Status check kiya: Session: {sess}, Group ID: {gid}")
    await message.reply_text(
        "⚙️ **Bot Status**:\n"
        f"• Session Database mein: {'✅ Yes' if sess else '❌ No'}\n"
        f"• Inbox Group ID: {f'✅ `{gid}`' if gid else '❌ Set nahi hai'}"
    )

@bot_app.on_message(filters.private & filters.command("generate_session") & filters.user(OWNER_ID))
async def generate_session_cmd(client: Client, message: Message):
    # (Yeh function pehle jaisa hi hai, ismein koi badlav nahi)
    try:
        chat_id = message.chat.id
        ask_phone = await client.ask(chat_id, "📲 Apna phone number country code ke saath bhejein (e.g., +919876543210).", timeout=300)
        phone = ask_phone.text.strip()
        temp_client = Client("temp-session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await temp_client.connect()
        try:
            code_info = await temp_client.send_code(phone)
            ask_code = await client.ask(chat_id, "🔐 Aapke number par bheja gaya OTP dalein.", timeout=300)
            code = ask_code.text.strip()
            await temp_client.sign_in(phone, code_info.phone_code_hash, code)
        except FloodWait as e:
            await client.send_message(chat_id, f"⏳ Flood wait: kripya {e.value} seconds intezar karein.")
            return
        except Exception as e:
            if "SESSION_PASSWORD_NEEDED" in str(e):
                ask_pwd = await client.ask(chat_id, "🔑 2FA password dalein.", timeout=300)
                await temp_client.check_password(ask_pwd.text.strip())
            else: raise e
        session_string = await temp_client.export_session_string()
        set_config("SESSION_STRING", session_string)
        await temp_client.disconnect()
        await client.send_message(chat_id, "✅ Session string database mein save ho gaya hai. Bot restart karein.")
        log.info(f"Session generate aur save kiya gaya.")
    except Exception as e:
        log.exception(f"Session generate karte waqt error: {e}")
        await message.reply_text(f"❌ Error aaya: {e}")

@bot_app.on_message(filters.group & filters.user(OWNER_ID) & filters.reply)
async def on_owner_reply(client: Client, message: Message):
    log.info(f"Owner se group ({message.chat.id}) mein ek reply aaya.")
    
    # Check karo ki yeh message configured group se hai ya nahi
    inbox_group_id = get_config("INBOX_GROUP_ID")
    if not inbox_group_id or message.chat.id != inbox_group_id:
        log.info("Reply configured inbox group se nahi tha, isliye ignore kar raha hoon.")
        return
    
    try:
        log.info(f"Reply to message ID '{message.reply_to_message_id}' ke liye job dhoond raha hoon.")
        job = JOBS.find_one({"group_message_id": message.reply_to_message_id, "status": STATUS_PENDING_REPLY})
        
        if not job:
            log.warning("Is reply ke liye koi pending job nahi mila. Ignore kar raha hoon.")
            return

        log.info(f"Job ID '{job['_id']}' mil gaya. Reply content nikal raha hoon.")
        content = extract_content(message)
        
        # Job data update karo
        updated_job_data = {
            "content_out": content,
            "updated_at": now()
        }
        JOBS.update_one({"_id": job["_id"]}, {"$set": updated_job_data})
        
        # Ab message bhejne wala function sidhe call karo
        full_job_data = {**job, **updated_job_data}
        asyncio.create_task(send_protected_message(job["_id"], full_job_data))

        await message.reply_text(f"✅ User `{job.get('sender_id')}` ko reply bheja ja raha hai.", quote=True)

    except Exception as e:
        log.exception(f"Owner ke reply ko process karte waqt error: {e}")
        await message.reply_text(f"Reply process karne mein error: {e}")

@bot_app.on_message(filters.private & filters.command("send_protected") & filters.user(OWNER_ID))
async def send_protected_cmd(client: Client, message: Message):
    log.info("Owner ne /send_protected command use kiya.")
    try:
        if not message.reply_to_message:
            return await message.reply_text("Jis content ko bhejna hai, uspar reply karke command likhein.")
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2: return await message.reply_text("Usage: `/send_protected <TARGET_USER_ID>`")
        try: target_id = int(parts[1].strip())
        except ValueError: return await message.reply_text("User ID ek number hona chahiye.")

        content = extract_content(message.reply_to_message)
        doc = { "target_chat_id": target_id, "content_out": content, "created_at": now(), "updated_at": now() }
        res = JOBS.insert_one(doc)
        
        log.info(f"Manual send job banaya ID: {res.inserted_id} Target: {target_id}")
        
        # Message bhejne wala function sidhe call karo
        asyncio.create_task(send_protected_message(res.inserted_id, doc))

        await message.reply_text(f"✅ Manual protected message bheja ja raha hai. Job ID: `{res.inserted_id}`")
    except Exception as e:
        log.exception(f"/send_protected mein error: {e}")
        await message.reply_text(f"Error: {e}")

# --- USER HANDLERS (`user_app`) ---
async def on_incoming_dm(client: Client, message: Message):
    if not message.from_user or message.from_user.is_self or message.from_user.is_bot: return

    log.info(f"User ID: {message.from_user.id} se naya DM aaya.")
    try:
        group_id = get_config("INBOX_GROUP_ID")
        if not group_id:
            log.warning(f"INBOX_GROUP_ID set nahi hai. User {message.from_user.id} ka DM process nahi kar sakta.")
            return
        
        # 1. Database mein job banayo
        job_doc = { "status": STATUS_PENDING_REPLY, "sender_id": message.from_user.id, "dm_message_id": message.id, "content_in": extract_content(message), "created_at": now(), "updated_at": now() }
        job_res = JOBS.insert_one(job_doc)
        log.info(f"Naya DM job banaya. ID: {job_res.inserted_id} User: {message.from_user.id}")

        # 2. Message ko group mein forward karo
        sender_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
        header = f"📩 **Naya message aaya hai:** `{sender_name}`\n👤 **User ID:** `{message.from_user.id}`\n\n---\n*Neeche diye gaye message par reply karke jawab dein.*"
        
        fwd_msg = await message.forward(group_id)
        log.info(f"Message ko Group ID {group_id} mein forward kiya. Naya Message ID: {fwd_msg.id}")
        
        await bot_app.send_message(chat_id=group_id, text=header, reply_to_message_id=fwd_msg.id)

        # 3. Job ko forward kiye gaye message ID se update karo
        JOBS.update_one({"_id": job_res.inserted_id}, {"$set": {"group_message_id": fwd_msg.id, "updated_at": now()}})
        log.info(f"Job ID {job_res.inserted_id} ko group message ID {fwd_msg.id} se update kiya.")

    except Exception as e:
        log.exception(f"Naye DM ko process karte waqt error (from user {message.from_user.id}): {e}")

# --- MAIN EXECUTION ---
async def main():
    global user_app
    
    await bot_app.start()
    log.info("Bot client shuru ho gaya hai.")

    session_string = get_config("SESSION_STRING")
    if session_string:
        log.info("Session string mili, user client shuru kar raha hoon...")
        user_app = Client("user-client", api_id=API_ID, api_hash=API_HASH, session_string=session_string)
        user_app.add_handler(MessageHandler(on_incoming_dm, filters.private & ~filters.me))
        try:
            await user_app.start()
            me = await user_app.get_me()
            log.info(f"User client shuru ho gaya hai: {me.first_name}.")
        except Exception as e:
            log.error(f"User client shuru nahi ho paya: {e}. User client kaam nahi karega.")
            user_app = None
    else:
        log.warning("Database mein SESSION_STRING nahi mila. User client shuru nahi hua. /generate_session command use karein.")

    log.info("Bot ab poori tarah se chal raha hai. Band karne ke liye Ctrl+C dabayein.")
    await asyncio.Event().wait()
    
    log.info("Bot band ho raha hai...")
    await bot_app.stop()
    if user_app and user_app.is_connected: await user_app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot user dwara band kiya gaya.")
    except Exception as e:
        log.exception(f"Bot chalane mein Badi Galti aayi: {e}")
        
