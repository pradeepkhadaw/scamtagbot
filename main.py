import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# --- Failsafe Print Statements ---
print("--- SCRIPT EXECUTION STARTED ---")

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("HybridShieldBot")

# --- Configuration Loading ---
try:
    print("STEP 1: Loading environment variables...")
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    MONGO_URI = os.environ["MONGO_URI"]
    OWNER_ID = int(os.environ["OWNER_ID"])
    print("✅ STEP 1: Environment variables loaded successfully.")
    # Check for empty values
    if not all([API_ID, API_HASH, BOT_TOKEN, MONGO_URI, OWNER_ID]):
        print("❌ FATAL: Ek ya ek se zyada environment variable loaded hai, lekin KHAALI (EMPTY) hai. Check karein.")
        sys.exit(1)
except (KeyError, ValueError) as e:
    print(f"❌ FATAL: Environment variable load nahi hua ya galat hai: {e}. Script band ho rahi hai.")
    sys.exit(1)

# --- Pyrogram Client Initialization ---
# Is baar hum clients ko pehle define kar rahe hain
bot_app = Client(
    "std-bot-client",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)
user_app: Optional[Client] = None

# --- MongoDB Variables (Abhi connect nahi karenge) ---
mongo_client = None
JOBS = None
CONFIG = None

# --- NEW PUBLIC HEALTH CHECK COMMAND ---
# YEH COMMAND BINA OWNER_ID CHECK KIYE CHALEGA
@bot_app.on_message(filters.command("health"))
async def health_check_cmd(client: Client, message: Message):
    log.info(">>> /health command received! Bot is alive. <<<")
    await message.reply_text(f"✅ Bot is running.\nYour User ID is: `{message.from_user.id}`\nOwner ID is set to: `{OWNER_ID}`")

# --- BOT COMMANDS (with OWNER_ID filter) ---
@bot_app.on_message(filters.private & filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client: Client, message: Message):
    log.info(">>> /start command received from OWNER <<<")
    await message.reply_text("Owner commands are working!")

# ... (baaki saare functions jaise pehle the, unhe yahan paste karein. For brevity, main unhe skip kar raha hoon,
# lekin aapko on_owner_reply, on_incoming_dm, generate_session_cmd, etc. sabhi functions yahan rakhne hain)
# IMPORTANT: Pehle wale code se baaki functions yahan copy-paste karein.

async def connect_to_db():
    """Function to connect to MongoDB."""
    global mongo_client, JOBS, CONFIG
    try:
        print("STEP 3: Connecting to MongoDB...")
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.server_info()  # Test connection
        db = mongo_client["ultimate_hybrid_shieldbot"]
        JOBS = db["jobs"]
        CONFIG = db["config"]
        print("✅ STEP 3: MongoDB connected successfully.")
        return True
    except Exception as e:
        print(f"❌ FATAL: MongoDB se connect nahi ho paya: {e}")
        print("WARNING: Please check your MONGO_URI and IP Whitelist in MongoDB Atlas.")
        return False

# --- MAIN EXECUTION ---
async def main():
    global user_app
    
    # STEP 2: BOT APP START KARO (DB SE PEHLE)
    try:
        print("\nSTEP 2: Starting Bot Client...")
        await bot_app.start()
        me = await bot_app.get_me()
        print(f"✅ STEP 2: Bot Client started successfully as @{me.username}")
        print("\n!!! IMPORTANT: Ab bot ko /health command bhejein !!!\n")
    except Exception as e:
        print(f"❌ FATAL: Bot Client start nahi ho paya: {e}")
        print("Please check your API_ID, API_HASH, and BOT_TOKEN again.")
        return # Aage nahi badhna

    # STEP 3: DATABASE CONNECT KARO
    if not await connect_to_db():
        # Agar DB connect nahi hua, to bot ko chalu rakho taaki commands test ho sakein
        print("DB connection failed, but bot will keep running for basic command testing.")
    
    # STEP 4: USER APP START KARO (agar DB connect hua aur session hai)
    if CONFIG is not None:
        print("STEP 4: Checking for User session string...")
        session_string_doc = CONFIG.find_one({"key": "SESSION_STRING"})
        session_string = session_string_doc.get("value") if session_string_doc else None
        
        if session_string:
            print("Session string found. Starting User Client...")
            user_app = Client(
                "user-client",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
            )
            # user_app.add_handler(...) # Yahan DM handler add hoga
            try:
                await user_app.start()
                me = await user_app.get_me()
                print(f"✅ STEP 4: User Client started successfully as {me.first_name}")
                # asyncio.create_task(job_processor()) # Background task yahan shuru hoga
            except Exception as e:
                print(f"❌ ERROR: User Client start nahi ho paya: {e}")
                user_app = None
        else:
            print("INFO: No session string found in DB. User Client not started. Use /generate_session.")
    else:
        print("INFO: Skipping User Client start because DB is not connected.")


    print("\n--- Bot is now fully running. Press Ctrl+C to stop. ---")
    await asyncio.Event().wait()
    
    print("\nShutting down...")
    await bot_app.stop()
    if user_app and user_app.is_connected:
        await user_app.stop()

# --- IMPORTANT ---
# Pehle wale code se baaki ke sabhi functions (helpers, handlers) ko upar `...` ki jagah paste karna zaroori hai
# for this script to be complete. This example only focuses on the startup logic.

if __name__ == "__main__":
    # Ensure you've pasted all your other functions from the previous code block before running
    print("WARNING: Make sure all helper and handler functions are pasted into this script.")
    asyncio.run(main())

