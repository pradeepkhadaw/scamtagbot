import os
import sys
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("SessionGeneratorBot")

# --- Configuration ---
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    OWNER_ID = int(os.environ["OWNER_ID"])
except (KeyError, ValueError) as e:
    log.error(f"FATAL: Zaroori Environment Variables set nahi hain (API_ID, API_HASH, BOT_TOKEN, OWNER_ID): {e}")
    sys.exit(1)

# --- Bot Client ---
app = Client(
    "session_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# --- Data Store (Conversation state ke liye) ---
# Yeh store karega ki hum user se kya expect kar rahe hain (phone, otp, ya password)
user_data = {}


# --- Handlers ---
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
async def start_command(client: Client, message: Message):
    """Start command ka simple reply deta hai"""
    user_id = message.from_user.id
    if user_id in user_data:
        del user_data[user_id] # Puraana process cancel karein
        
    await message.reply_text(
        "Hello! Main aapki User Account Session String generate karne mein madad karunga.\n\n"
        "Shuru karne ke liye /generate command bhejein."
    )

@app.on_message(filters.command("generate") & filters.user(OWNER_ID))
async def generate_command(client: Client, message: Message):
    """Session generate karne ka process shuru karta hai"""
    user_id = message.from_user.id
    if user_id in user_data:
        del user_data[user_id]
        
    await message.reply_text("Theek hai, shuru karte hain...\n\nApna Telegram phone number country code ke saath bhejein (e.g., +919876543210):")
    # User ka state set karna
    user_data[user_id] = {"step": "phone"}


@app.on_message(filters.private & filters.user(OWNER_ID) & ~filters.command(["start", "generate"]))
async def message_handler(client: Client, message: Message):
    """Commands ke alawa baaki sabhi messages ko handle karta hai"""
    user_id = message.from_user.id
    state = user_data.get(user_id)

    if not state:
        return

    # --- Step 1: Phone Number Handle Karna ---
    if state["step"] == "phone":
        phone_number = message.text
        try:
            temp_client = Client(name="user_gen", api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await temp_client.connect()
            
            sent_code = await temp_client.send_code(phone_number)
            
            # State update karna
            user_data[user_id]["step"] = "otp"
            user_data[user_id]["phone"] = phone_number
            user_data[user_id]["hash"] = sent_code.phone_code_hash
            user_data[user_id]["client"] = temp_client
            
            await message.reply_text("Aapke number ya Telegram app par bheja gaya OTP code yahan daalein:")
        except Exception as e:
            await message.reply_text(f"❌ Phone number ke saath error aaya:\n`{e}`")
            del user_data[user_id]
    
    # --- Step 2: OTP Handle Karna ---
    elif state["step"] == "otp":
        otp = message.text
        temp_client = state["client"]
        try:
            await temp_client.sign_in(state["phone"], state["hash"], otp)
            # Agar sign in safal raha (2FA nahi hai)
            session_string = await temp_client.export_session_string()
            await temp_client.disconnect()

            await message.reply_text(f"✅ Session String generate ho gayi hai:\n\n`{session_string}`")
            del user_data[user_id]

        except SessionPasswordNeeded:
            # Agar 2FA hai
            user_data[user_id]["step"] = "password"
            await message.reply_text("Aapka account 2FA (Two-Step Verification) se protected hai.\n\nApna password daalein:")
        
        except PhoneCodeInvalid:
            await message.reply_text("❌ OTP galat hai. Kripya /generate command se dobara shuru karein.")
            del user_data[user_id]
            await temp_client.disconnect()

        except Exception as e:
            await message.reply_text(f"❌ OTP ke saath error aaya:\n`{e}`")
            del user_data[user_id]
            await temp_client.disconnect()

    # --- Step 3: Password Handle Karna ---
    elif state["step"] == "password":
        password = message.text
        temp_client = state["client"]
        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            await temp_client.disconnect()
            
            await message.reply_text(f"✅ Session String generate ho gayi hai:\n\n`{session_string}`")
            del user_data[user_id]

        except PasswordHashInvalid:
            await message.reply_text("❌ Password galat hai. Kripya /generate command se dobara shuru karein.")
            del user_data[user_id]
            await temp_client.disconnect()
            
        except Exception as e:
            await message.reply_text(f"❌ Password ke saath error aaya:\n`{e}`")
            del user_data[user_id]
            await temp_client.disconnect()


# --- Bot ko chalana ---
if __name__ == "__main__":
    log.info("Session Generator Bot start ho raha hai...")
    app.run()
        
