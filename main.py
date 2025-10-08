import os
import sys
import logging
import asyncio

# Zaroori libraries
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatAction

# Google Gemini AI library
import google.generativeai as genai

# --- Logging Setup (DEBUG level par set karna) ---
# Isse Pyrogram ke internal messages bhi dikhenge
logging.basicConfig(
    level=logging.INFO, # Pehle INFO rakhein, agar isse baat na bane to DEBUG kar denge
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("AIUserBot_DEBUG")

log.info("--- SCRIPT STARTED (DEBUG MODE) ---")

# --- Configuration ---
try:
    log.info("STEP 1: Loading environment variables...")
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    log.info("✅ STEP 1: Environment variables loaded successfully.")
except KeyError as e:
    log.error(f"❌ FATAL: Environment Variable set nahi hai: {e}")
    sys.exit(1)

# --- AI Setup ---
try:
    log.info("STEP 2: Configuring Google Gemini AI...")
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-pro')
    log.info("✅ STEP 2: Google Gemini AI model successfully configured.")
except Exception as e:
    log.error(f"❌ FATAL: AI model configure karte waqt error aaya: {e}")
    sys.exit(1)

# --- Userbot Client ---
log.info("STEP 3: Initializing Pyrogram Client...")
app = Client(
    "ai_user_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)
log.info("✅ STEP 3: Pyrogram Client initialized.")


# --- Helper for long messages ---
async def send_long_message(message: Message, text: str):
    # (Yeh function pehle jaisa hi hai)
    if len(text) <= 4096:
        await message.reply_text(text)
    else:
        # ... (splitting logic) ...
        pass


# --- Message Handler (with detailed logging) ---
@app.on_message(filters.private & ~filters.me)
async def handle_ai_dm(client: Client, message: Message):
    sender_id = message.from_user.id
    log.info(f"--- HANDLER TRIGGERED for user {sender_id} ---")
    
    if not message.text:
        log.warning("Handler triggered, but message has no text. Ignoring.")
        return
        
    log.info(f"Message text: '{message.text}'")

    try:
        log.info("Sending 'typing' action...")
        await client.send_chat_action(chat_id=sender_id, action=ChatAction.TYPING)
        log.info("✅ 'Typing' action sent.")

        log.info("Calling Google Gemini AI for a response...")
        response = ai_model.generate_content(message.text)
        log.info("✅ AI ne response generate kar diya hai.")
        
        # Check if response is empty
        if not response.text:
            log.warning("AI ne response to diya, lekin usmein text khaali hai.")
            await message.reply_text("Maaf kijiye, main is par koi टिप्पणी nahi kar sakta.")
            return

        log.info(f"AI Response Text: '{response.text[:100]}...'") # Sirf pehle 100 characters log karna
        
        log.info("Ab AI ka jawab bheja ja raha hai...")
        await send_long_message(message, response.text)
        log.info("✅ Poora jawab user ko bhej diya gaya hai.")

    except Exception as e:
        # YEH SABSE ZAROORI HAI: HAR ERROR KO LOG KARNA
        log.exception(f"❌❌❌ HANDLER KE ANDAR EK UNEXPECTED ERROR AAYA ❌❌❌")
        await message.reply_text("Maaf kijiye, ek technical samasya aa gayi hai. Main isey theek karne ki koshish kar raha hoon.")


@app.on_message(filters.command("alive") & filters.me)
async def alive_command(client: Client, message: Message):
    log.info("--- ALIVE COMMAND TRIGGERED ---")
    await message.edit_text("✅ **AI Userbot is running (DEBUG MODE).**")


# --- Userbot ko chalana ---
if __name__ == "__main__":
    log.info("STEP 4: Starting the Userbot...")
    app.run()
    log.info("--- SCRIPT STOPPED ---")
    
