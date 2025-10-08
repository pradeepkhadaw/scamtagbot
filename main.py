import os
import sys
from pyrogram import Client, filters
from pyrogram.types import Message

# --- Configuration ---
# Sirf 3 zaroori variables
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
    await message.reply_text("Bot chal raha hai. (app.run version)")


# --- Bot ko chalane ka sabse simple tarika ---
if __name__ == "__main__":
    print("Bot start ho raha hai...")
    # Yeh line bot ko start karti hai aur hamesha chalu rakhti hai
    app.run()
    print("Bot band ho gaya.")
    
