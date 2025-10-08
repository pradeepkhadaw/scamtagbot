import os
import sys
import logging
from pyrogram import Client, filters
from pyrogram.errors import SessionPasswordNeeded

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("SessionGeneratorBot")

# --- Configuration ---
# Is bot ke liye sirf yeh 4 variables chahiye
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
    in_memory=True # Is bot ke liye session file ki zaroorat nahi
)

# --- Handlers ---
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
async def start_command(client, message):
    """Start command ka simple reply deta hai"""
    await message.reply_text(
        "Hello! Main aapki User Account Session String generate karne mein madad karunga.\n\n"
        "Shuru karne ke liye /generate command bhejein."
    )

@app.on_message(filters.command("generate") & filters.user(OWNER_ID))
async def generate_session(client, message):
    """Session generate karne ka process shuru karta hai"""
    try:
        await message.reply_text("Theek hai, shuru karte hain...")
        
        # Ek temporary user client banate hain
        # Yeh aapke account se login karega
        user_client = Client(
            name="user_session_generator",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )

        await user_client.connect()
        
        # User se phone number maangna
        phone_msg = await client.ask(
            chat_id=message.chat.id,
            text="Apna Telegram phone number country code ke saath bhejein (e.g., +919876543210):",
            timeout=300
        )
        phone_number = phone_msg.text

        # Telegram ko code bhejne ke liye kehna
        sent_code = await user_client.send_code(phone_number)
        
        # User se OTP maangna
        otp_msg = await client.ask(
            chat_id=message.chat.id,
            text="Aapke number ya Telegram app par bheja gaya OTP code yahan daalein:",
            timeout=300
        )
        otp = otp_msg.text

        # Sign in karna
        await user_client.sign_in(phone_number, sent_code.phone_code_hash, otp)

    except SessionPasswordNeeded:
        # Agar 2FA enabled hai
        password_msg = await client.ask(
            chat_id=message.chat.id,
            text="Aapka account 2FA (Two-Step Verification) se protected hai.\n\nApna password daalein:",
            timeout=300
        )
        password = password_msg.text
        await user_client.check_password(password)
    
    except Exception as e:
        await message.reply_text(f"❌ Ek error aa gaya:\n\n`{e}`")
        if 'user_client' in locals() and user_client.is_connected:
            await user_client.disconnect()
        return

    # Session String nikalna
    session_string = await user_client.export_session_string()
    await user_client.disconnect()
    
    await message.reply_text(
        "✅ Session String safaltapoorvak generate ho gayi hai.\n\n"
        "Neeche diye gaye message se use copy karke safe jagah rakh lein. **Yeh aapke password jaisa hai, kisi se share na karein.**"
    )
    # Session string ko alag message mein bhejna taaki copy karna aasan ho
    await client.send_message(message.chat.id, f"`{session_string}`")

# --- Bot ko chalana ---
if __name__ == "__main__":
    log.info("Session Generator Bot start ho raha hai...")
    app.run()
    
