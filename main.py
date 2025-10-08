import os
import sys
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("SimpleBot")

# --- Configuration ---
# Is bot ke liye sirf yeh 3 variables chahiye
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
except (KeyError, ValueError) as e:
    log.error(f"FATAL: Environment Variable galat hai ya set nahi hai: {e}")
    sys.exit(1)

# --- Bot Client (Session file nahi banayega) ---
app = Client(
    "my_simple_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True  # Session ki bakchodi khatm
)

# --- Start Command Handler ---
@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    # Jab bhi koi /start bhejega, yeh function chalega
    log.info(f"'/start' command mila from User ID: {message.from_user.id}")
    await message.reply_text("Hello! Main ek simple sa bot hoon aur main kaam kar raha hoon.")

# --- Main Function to Run the Bot ---
async def main():
    log.info("Simple Bot start ho raha hai...")
    try:
        await app.start()
        me = await app.get_me()
        log.info(f"Bot @{me.username} online hai aur messages ka intezar kar raha hai.")
        
        # Bot ko hamesha chalu rakhega
        await asyncio.Event().wait()
        
    except Exception as e:
        log.error(f"Bot start hone mein error aaya: {e}")
    finally:
        if app.is_running:
            await app.stop()
        log.info("Bot band ho gaya hai.")

if __name__ == "__main__":
    log.info("Script shuru hui.")
    asyncio.run(main())

