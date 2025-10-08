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
# Load environment variables. The script will exit if any are missing.
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
except (KeyError, ValueError) as e:
    print(f"FATAL: Missing or invalid environment variable: {e}. Please check your configuration.")
    sys.exit(1)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("HybridShieldBot")

# --- MongoDB Setup ---
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.server_info()  # Test connection
    db = mongo_client["ultimate_hybrid_shieldbot"]
    JOBS: Collection = db["jobs"]
    CONFIG: Collection = db["config"]
    log.info("Successfully connected to MongoDB.")
except PyMongoError as e:
    log.error("Failed to connect to MongoDB: %s", e)
    sys.exit(1)

# --- Constants ---
# Job Statuses
STATUS_NEW_DM = "NEW_DM"
STATUS_PENDING_REPLY = "PENDING_REPLY"
STATUS_READY_TO_SEND = "READY_TO_SEND"
STATUS_SENDING = "SENDING"
STATUS_COMPLETED = "COMPLETED"
STATUS_ERROR = "ERROR"

# Job Types
TYPE_DM_FLOW = "DM_FLOW"
TYPE_MANUAL_SEND = "MANUAL_SEND"

# --- Helper Functions (Shared) ---
def now() -> datetime:
    """Returns the current UTC datetime."""
    return datetime.now(timezone.utc)

def set_config(key: str, value: Any):
    """Saves a configuration key-value pair to the database."""
    try:
        CONFIG.update_one(
            {"key": key},
            {"$set": {"value": value, "updated_at": now()}},
            upsert=True,
        )
        log.info("Set config '%s'.", key)
    except PyMongoError as e:
        log.error("Failed to set config '%s': %s", key, e)

def get_config(key: str, default=None) -> Any:
    """Retrieves a configuration value from the database."""
    try:
        doc = CONFIG.find_one({"key": key})
        return doc.get("value", default) if doc else default
    except PyMongoError as e:
        log.error("Failed to get config '%s': %s", key, e)
        return default

def extract_content(msg: Message) -> Dict[str, Any]:
    """Extracts sendable content from a Pyrogram Message object."""
    payload: Dict[str, Any] = {}
    
    # Extract text/caption and determine the message kind
    kind = "text"
    text_content = msg.text or msg.caption or ""
    
    if msg.text:
        payload["text"] = msg.text
    elif msg.caption:
        payload["text"] = msg.caption

    if msg.photo:
        kind = "photo"
        payload["file_id"] = msg.photo.file_id
    elif msg.video:
        kind = "video"
        payload["file_id"] = msg.video.file_id
    elif msg.document:
        kind = "document"
        payload["file_id"] = msg.document.file_id
    
    payload["kind"] = kind

    # Extract inline keyboard buttons
    if msg.reply_markup and isinstance(msg.reply_markup, InlineKeyboardMarkup):
        payload["buttons"] = [
            [
                {"text": b.text, "url": b.url, "callback_data": b.callback_data}
                for b in row
            ]
            for row in msg.reply_markup.inline_keyboard
        ]
    return payload

def build_reply_markup(button_rows: Optional[List[List[Dict[str, Any]]]]) -> Optional[InlineKeyboardMarkup]:
    """Builds a Pyrogram InlineKeyboardMarkup from a list of button data."""
    if not button_rows:
        return None
    
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=b["text"],
                    url=b.get("url"),
                    callback_data=b.get("callback_data"),
                )
                for b in row
            ]
            for row in button_rows
        ]
    )

# --- Client Initialization ---
# The Bot Client handles commands and owner interactions in the group.
bot_app = Client(
    name="std-bot-client",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# The User Client handles incoming DMs and sends protected content.
# It will be initialized later in main() after fetching the session string.
user_app: Optional[Client] = None

# --- BOT HANDLERS (`bot_app`) ---

@bot_app.on_message(filters.private & filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "Bot is running! Available commands:\n"
        "/start - This message\n"
        "/status - Check setup\n"
        "/set_group - Set the inbox group\n"
        "/generate_session - Create user session\n"
        "/send_protected - Manual protected send"
    )
    log.info("Start command triggered by owner.")

@bot_app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def set_group_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP):
        await message.reply_text("This command must be used inside your target group.")
        return
    set_config("INBOX_GROUP_ID", message.chat.id)
    await message.reply_text(f"✅ Inbox Group saved successfully!\nID: `{message.chat.id}`")
    log.info("INBOX_GROUP_ID set to %s", message.chat.id)

@bot_app.on_message(filters.private & filters.command("status") & filters.user(OWNER_ID))
async def status_cmd(client: Client, message: Message):
    gid = get_config("INBOX_GROUP_ID")
    sess = bool(get_config("SESSION_STRING"))
    await message.reply_text(
        "⚙️ **Bot Status**:\n"
        f"• Session in DB: {'✅ Yes' if sess else '❌ No'}\n"
        f"• Inbox Group ID: {f'✅ `{gid}`' if gid else '❌ Not set'}"
    )

@bot_app.on_message(filters.private & filters.command("generate_session") & filters.user(OWNER_ID))
async def generate_session_cmd(client: Client, message: Message):
    try:
        chat_id = message.chat.id
        ask_phone = await client.ask(chat_id, "📲 Please send your phone number with the country code (e.g., +12223334444).", timeout=300)
        phone = ask_phone.text.strip()

        temp_client = Client(name="temp-session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await temp_client.connect()

        try:
            code_info = await temp_client.send_code(phone)
            ask_code = await client.ask(chat_id, "🔐 An OTP has been sent to you. Please enter it.", timeout=300)
            code = ask_code.text.strip()
            await temp_client.sign_in(phone, code_info.phone_code_hash, code)

        except FloodWait as e:
            log.warning("FloodWait during session generation: sleeping for %s seconds.", e.value)
            await client.send_message(chat_id, f"⏳ Flood wait: please wait for {e.value} seconds before trying again.")
            await temp_client.disconnect()
            return
        
        except Exception as e:
            if "SESSION_PASSWORD_NEEDED" in str(e):
                ask_pwd = await client.ask(chat_id, "🔑 Your account has 2FA enabled. Please enter your password.", timeout=300)
                await temp_client.check_password(ask_pwd.text.strip())
            else:
                raise e

        session_string = await temp_client.export_session_string()
        set_config("SESSION_STRING", session_string)
        await temp_client.disconnect()

        await client.send_message(chat_id, "✅ Session string has been saved to the database. The user client will start on the next restart.")
        log.info("Session generated and saved for phone %s", phone)

    except Exception as e:
        log.exception("Error during session generation: %s", e)
        await message.reply_text(f"❌ An error occurred: {e}")

@bot_app.on_message(
    filters.group & 
    filters.user(OWNER_ID) & 
    filters.reply
)
async def on_owner_reply(client: Client, message: Message):
    # DYNAMICALLY check if the message is from the configured group
    inbox_group_id = get_config("INBOX_GROUP_ID")
    if not inbox_group_id or message.chat.id != inbox_group_id:
        return
        
    try:
        # Find the job associated with the message the owner replied to
        job = JOBS.find_one({
            "group_message_id": message.reply_to_message_id,
            "status": STATUS_PENDING_REPLY
        })
        if not job:
            return # This is a regular reply, not for a DM job.

        content = extract_content(message)
        JOBS.update_one(
            {"_id": job["_id"]},
            {"$set": {"content_out": content, "status": STATUS_READY_TO_SEND, "updated_at": now()}},
        )
        
        sender_info = job.get('sender_id', 'Unknown User')
        await message.reply_text(f"✅ Queued reply for protected sending to user `{sender_info}`.", quote=True)
        log.info("Job %s marked as READY_TO_SEND for user %s.", job["_id"], sender_info)

    except Exception as e:
        log.exception("Error in on_owner_reply: %s", e)
        await message.reply_text(f"Error queuing reply: {e}")

@bot_app.on_message(filters.private & filters.command("send_protected") & filters.user(OWNER_ID))
async def send_protected_cmd(client: Client, message: Message):
    try:
        if not message.reply_to_message:
            return await message.reply_text("Please REPLY to the content you want to send.")
        
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply_text("Usage: `/send_protected <TARGET_USER_ID>`")
        
        try:
            target_id = int(parts[1].strip())
        except ValueError:
            return await message.reply_text("The User ID must be a number.")

        content = extract_content(message.reply_to_message)
        doc = {
            "type": TYPE_MANUAL_SEND,
            "status": STATUS_READY_TO_SEND,
            "target_chat_id": target_id,
            "content_out": content,
            "created_at": now(),
            "updated_at": now(),
        }
        res = JOBS.insert_one(doc)
        await message.reply_text(f"✅ Manual protected send queued. Job ID: `{res.inserted_id}`")
        log.info("Manual send job created: %s for target %s", res.inserted_id, target_id)

    except Exception as e:
        log.exception("Error in send_protected: %s", e)
        await message.reply_text(f"Error: {e}")

# --- USER HANDLERS (`user_app`) ---

async def on_incoming_dm(client: Client, message: Message):
    try:
        # Ignore messages from bots or from self
        if not message.from_user or message.from_user.is_self or message.from_user.is_bot:
            return

        group_id = get_config("INBOX_GROUP_ID")
        if not group_id:
            log.warning("INBOX_GROUP_ID not set. Cannot process new DM from user %s.", message.from_user.id)
            return
        
        # 1. Create a job document
        job_doc = {
            "type": TYPE_DM_FLOW,
            "status": STATUS_NEW_DM,
            "sender_id": message.from_user.id,
            "dm_message_id": message.id,
            "content_in": extract_content(message),
            "created_at": now(),
            "updated_at": now(),
        }
        job_res = JOBS.insert_one(job_doc)
        log.info("New DM job %s created for user %s.", job_res.inserted_id, message.from_user.id)

        # 2. Forward the message to the inbox group and send a header
        sender_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
        header = f"📩 **New message from:** `{sender_name}`\n👤 **User ID:** `{message.from_user.id}`\n\n---\n*Reply to the message below to send a protected reply.*"
        
        # We use the user_app to forward the original message for perfect quality
        fwd_msg = await message.forward(group_id)
        
        # Send a header message and reply to the forwarded content
        await bot_app.send_message(
            chat_id=group_id,
            text=header,
            reply_to_message_id=fwd_msg.id
        )

        # 3. Update job with the group message ID and set status to pending
        JOBS.update_one(
            {"_id": job_res.inserted_id},
            {"$set": {
                "group_message_id": fwd_msg.id,
                "status": STATUS_PENDING_REPLY,
                "updated_at": now()
            }},
        )
        log.info("Mirrored DM for job %s to group. Group message ID: %s.", job_res.inserted_id, fwd_msg.id)

    except Exception as e:
        log.exception("Error in on_incoming_dm: %s", e)

# --- BACKGROUND JOB PROCESSOR ---

async def job_processor():
    """
    This is the core background worker. It continuously checks MongoDB for jobs
    that are READY_TO_SEND and processes them using the user_app client.
    """
    log.info("✅ Job processor started.")
    while user_app and user_app.is_connected:
        try:
            # Atomically find a job and update its status to prevent race conditions
            job = JOBS.find_one_and_update(
                {"status": STATUS_READY_TO_SEND},
                {"$set": {"status": STATUS_SENDING, "updated_at": now()}}
            )

            if job:
                log.info("Processing job %s.", job["_id"])
                content = job.get("content_out", {})
                kind = content.get("kind", "text")
                text = content.get("text")
                file_id = content.get("file_id")
                markup = build_reply_markup(content.get("buttons"))
                target_id = job.get("target_chat_id") or job.get("sender_id")

                try:
                    # Send the message using the User Client
                    if kind == "text":
                        await user_app.send_message(target_id, text, protect_content=True, reply_markup=markup)
                    elif kind == "photo":
                        await user_app.send_photo(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "video":
                        await user_app.send_video(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
                    elif kind == "document":
                         await user_app.send_document(target_id, file_id, caption=text, protect_content=True, reply_markup=markup)
                    
                    JOBS.update_one({"_id": job["_id"]}, {"$set": {"status": STATUS_COMPLETED, "updated_at": now()}})
                    log.info("✅ Successfully completed job %s.", job["_id"])

                except Exception as e:
                    log.error("Failed to send message for job %s: %s", job["_id"], e)
                    JOBS.update_one(
                        {"_id": job["_id"]},
                        {"$set": {"status": STATUS_ERROR, "error": str(e), "updated_at": now()}}
                    )
            
            # Wait for 5 seconds before checking for the next job
            await asyncio.sleep(5)

        except Exception as e:
            log.exception("An error occurred in the job_processor loop: %s", e)
            await asyncio.sleep(15) # Wait longer if a major error occurs


# --- MAIN EXECUTION ---

async def main():
    global user_app
    
    # Start the bot client first
    await bot_app.start()
    log.info("Bot client started.")

    # Check for a session string to start the user client
    session_string = get_config("SESSION_STRING")
    if session_string:
        log.info("Session string found, attempting to start user client...")
        user_app = Client(
            name="user-client",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
        )
        # Add the DM handler to the user client
        user_app.add_handler(
            MessageHandler(on_incoming_dm, filters.private & ~filters.me)
        )
        try:
            await user_app.start()
            me = await user_app.get_me()
            log.info("User client started as %s.", me.first_name)
            
            # Start the background job processor task
            asyncio.create_task(job_processor())

        except Exception as e:
            log.error("Failed to start user client: %s. It will be unavailable.", e)
            user_app = None # Ensure user_app is None if it fails to start
    else:
        log.warning("No SESSION_STRING found in DB. User client not started. Use /generate_session to create one.")

    # Idle indefinitely
    log.info("Bot is now running. Press Ctrl+C to stop.")
    await asyncio.Event().wait()
    
    # Graceful shutdown
    log.info("Shutting down...")
    await bot_app.stop()
    if user_app and user_app.is_connected:
        await user_app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped by user.")
    except Exception as e:
        log.exception("Critical error in main execution: %s", e)

