import os
import sys
import logging
from datetime import datetime, timezone
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pyromod import listen

# Configuration from environment variables
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
    db = client["shieldbot"]
    CONFIG = db["config"]
    CONFIG.create_index("key", unique=True)
except PyMongoError as e:
    log.error("Failed to connect to MongoDB: %s", e)
    sys.exit(1)

# Helper functions
def now():
    return datetime.now(timezone.utc)

def set_config(key: str, value: any):
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

# Pyrogram bot client
app = Client(
    name="std-bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# Command handlers
@app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("start"))
async def start_cmd(client: Client, message: Message):
    try:
        await message.reply_text("Bot is running! Available commands:\n/start - This message\n/status - Check setup\n/set_group - Set inbox group\n/generate_session - Create user session")
        log.info("Start command triggered by owner %s", OWNER_ID)
    except Exception as e:
        log.exception("Error in start_cmd: %s", e)
        await message.reply_text(f"Error: {e}")

@app.on_message(filters.command("set_group") & filters.user(OWNER_ID))
async def cmd_set_group(client: Client, message: Message):
    try:
        log.info("Set_group command triggered")
        if message.chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP):
            return await message.reply_text("Run /set_group **inside** a group or supergroup.")
        set_config("INBOX_GROUP_ID", message.chat.id)
        await message.reply_text(f"Inbox Group saved: <code>{message.chat.id}</code>")
        log.info("INBOX_GROUP_ID set to %s", message.chat.id)
    except Exception as e:
        log.exception("Error in set_group: %s", e)
        await message.reply_text(f"Error: {e}")

@app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("status"))
async def cmd_status(client: Client, message: Message):
    try:
        log.info("Status command triggered")
        group_id = get_config("INBOX_GROUP_ID")
        sess = bool(get_config("SESSION_STRING"))
        await message.reply_text(
            f"⚙️ Status:\n"
            f"• Session in DB: {'✅' if sess else '❌'}\n"
            f"• Inbox Group ID: {group_id if group_id else '❌ not set'}"
        )
    except Exception as e:
        log.exception("Error in status: %s", e)
        await message.reply_text(f"Error: {e}")

@app.on_message(filters.private & filters.user(OWNER_ID) & filters.command("generate_session"))
async def generate_session(client: Client, message: Message):
    try:
        log.info("Generate_session command triggered")
        chat = message.chat
        phone_msg = await chat.ask("📲 Send your phone number with country code (e.g., +91xxxxxxxxxx):")
        phone = phone_msg.text.strip()

        temp = Client(name="temp-session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        async with temp:
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
            await chat.send_message("✅ Session saved to DB. You can /status to verify.")
            log.info("Session generated and saved for phone %s", phone)
    except FloodWait as e:
        log.warning("FloodWait in session gen: %s seconds", e.value)
        await asyncio.sleep(e.value)
        await chat.send_message("⏳ Retrying after flood wait...")
    except Exception as e:
        log.exception("Session generation error: %s", e)
        await chat.send_message(f"❌ Error: {e}")

# Main entry point
if __name__ == "__main__":
    log.info("Starting bot...")
    try:
        app.run()
    except Exception as e:
        log.error("Failed to start bot: %s", e)
        sys.exit(1)
