import os
import sys
import logging
import asyncio
from pyrogram import Client, filters, idle
from pyrogram.types import Message  # <-- YEH LINE MISSING THI, AB ADD KAR DI HAI

# --- Basic Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("DEBUG_BOT")

log.info("--- SCRIPT STARTED ---")

# --- Environment Variable Loading ---
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    OWNER_ID = int(os.environ["OWNER_ID"])
    log.info("Environment variables loaded successfully.")
except (KeyError, ValueError) as e:
    log.error(f"FATAL: Environment Variable galat hai: {e}")
    sys.exit(1)

# --- Pyrogram Client ---
app = Client(
    "my_debug_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- HANDLER 1: Public command jo sabke liye chalega ---
@app.on_message(filters.command("ping"))
async def ping_handler(client: Client, message: Message):
    log.info("✅✅✅ '/ping' HANDLER TRIGGER HUA! ✅✅✅")
    await message.reply_text(f"Pong! Bot is responding.\nYour User ID: `{message.from_user.id}`")

# --- HANDLER 2: Sirf owner ke liye ---
@app.on_message(filters.command("authping") & filters.user(OWNER_ID))
async def auth_ping_handler(client: Client, message: Message):
    log.info("✅✅✅ '/authping' OWNER HANDLER TRIGGER HUA! ✅✅✅")
    await message.reply_text("Pong from authenticated command! Your OWNER_ID is correct.")

# --- HANDLER 3: Koi bhi private message pakadne ke liye ---
@app.on_message(filters.private & ~filters.command(["ping", "authping"]))
async def catch_all_private_handler(client: Client, message: Message):
    log.info(f"☑️☑️☑️ CATCH-ALL HANDLER TRIGGER HUA! Text: '{message.text}' ☑️☑️☑️")
    await message.reply_text(f"Received your message, but it was not a valid command or your OWNER_ID did not match for '/authping'.\n\nYour User ID is: `{message.from_user.id}`\nConfigured OWNER_ID is: `{OWNER_ID}`")

async def main():
    log.info("Bot ko start kar raha hoon...")
    await app.start()
    me = await app.get_me()
    log.info(f"Bot '{me.username}' start ho gaya hai. Ab commands ka intezar hai.")
    await idle()
    log.info("Bot band ho raha hai.")
    await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot band kar diya gaya.")
        
