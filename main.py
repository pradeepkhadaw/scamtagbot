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
log = logging.getLogger("FixedBot")

# --- Configuration ---
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
except (KeyError, ValueError):
    print("FATAL: Environment Variables (API_ID, API_HASH, BOT_TOKEN) sahi se set nahi hain.")
    sys.exit(1)

# --- Bot Client ---
app = Client(
    "my_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- Sirf ek command: /start ---
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    """Jab bhi koi /start bhejega, yeh function chalega."""
    await message.reply_text("Bot chal raha hai. (Crash Fixed)")

# --- Bot ko chalane wala function ---
async def main():
    log.info("Bot start ho raha hai...")
    try:
        await app.start()
        log.info(f"Bot @{(await app.get_me()).username} online hai.")
        # Is line se bot hamesha chalu rahega
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot band ho raha hai.")
    finally:
        if app.is_running:
            await app.stop()

# --- Script ko run karne ka sahi tarika ---
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        # Agar loop pehle se chal raha hai, to iska matlab hum ek aese environment mein hain
        # jahan loop ko alag se start karne ki zaroorat nahi. Aisa kam hota hai.
        log.warning(f"Could not start a new event loop: {e}. Trying to run in existing loop.")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
        
