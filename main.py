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
log = logging.getLogger("ProtectorUserBot")

# --- Configuration ---
# Is bot ke liye sirf yeh 3 variables chahiye
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
except (KeyError, ValueError) as e:
    log.error(f"FATAL: Environment Variables (API_ID, API_HASH, SESSION_STRING) sahi se set nahi hain: {e}")
    sys.exit(1)

# --- Userbot Client ---
app = Client(
    "my_user_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)


async def resend_protected(client: Client, message: Message):
    """
    Message ka type check karke use protect karke waapis bhejta hai.
    """
    chat_id = message.chat.id
    reply_id = message.reply_to_message_id or None

    try:
        if message.text:
            await client.send_message(chat_id, message.text, protect_content=True, reply_to_message_id=reply_id)
        elif message.photo:
            await client.send_photo(chat_id, message.photo.file_id, caption=message.caption, protect_content=True, reply_to_message_id=reply_id)
        elif message.video:
            await client.send_video(chat_id, message.video.file_id, caption=message.caption, protect_content=True, reply_to_message_id=reply_id)
        elif message.animation:  # GIFs
            await client.send_animation(chat_id, message.animation.file_id, caption=message.caption, protect_content=True, reply_to_message_id=reply_id)
        elif message.document:
            await client.send_document(chat_id, message.document.file_id, caption=message.caption, protect_content=True, reply_to_message_id=reply_id)
        elif message.audio:
            await client.send_audio(chat_id, message.audio.file_id, caption=message.caption, protect_content=True, reply_to_message_id=reply_id)
        elif message.voice:
            await client.send_voice(chat_id, message.voice.file_id, caption=message.caption, protect_content=True, reply_to_message_id=reply_id)
        elif message.sticker:
            await client.send_sticker(chat_id, message.sticker.file_id, reply_to_message_id=reply_id)
        else:
            log.warning(f"Unsupported message type in chat {chat_id}, cannot protect.")

    except Exception as e:
        log.error(f"Message ko resend nahi kar paya (Chat ID: {chat_id}): {e}")


@app.on_message(filters.me & ~filters.service)
async def protect_my_message(client: Client, message: Message):
    """
    Aapke message ko delete karke, use protected content ke roop mein waapis bhejta hai.
    """
    # "Light-fast speed" ke liye, delete aur resend ek saath run honge
    try:
        await asyncio.gather(
            message.delete(),
            resend_protected(client, message)
        )
        log.info(f"Message protected in chat {message.chat.id}")
    except Exception as e:
        log.error(f"Message ko protect karte waqt error: {e}")


# --- Userbot ko chalane ka simple tarika ---
if __name__ == "__main__":
    log.info("Protector Userbot start ho raha hai...")
    app.run()
    log.info("Userbot band ho gaya.")
  
