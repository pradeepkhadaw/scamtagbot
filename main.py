import os
import sys
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message

# --- Sirf 3 zaroori variables ---
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
except (KeyError, ValueError):
    print("FATAL: Environment Variables (API_ID, API_HASH, BOT_TOKEN) sahi se set nahi hain.")
    sys.exit(1)

# --- Bot Client (Bina kisi extra setting ke) ---
app = Client(
    "my_bot_session",  # Session file ka naam
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- Sirf ek command: /start ---
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    """Jab bhi koi /start bhejega, yeh function chalega."""
    await message.reply_text("Bot chal raha hai.")

# --- Bot ko chalane wala function ---
async def main():
    print("Bot start ho raha hai...")
    await app.start()
    print("Bot online hai.")
    await asyncio.Event().wait() # Bot ko chalu rakhega

if __name__ == "__main__":
    asyncio.run(main())
    
