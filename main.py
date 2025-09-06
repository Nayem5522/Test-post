import os
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask
import threading

# 🔹 লগিং
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔹 কনফিগ
API_ID = int(os.environ.get("API_ID", 12345))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_url")
REQUEST_GROUP_URL = "https://t.me/PrimeCineZone/31"

# ❗ নতুন যোগ করা হয়েছে: ফোর্স সাবস্ক্রিপশন চ্যানেল আইডি এবং অ্যাডমিন আইডি
FSUB_CHANNELS = [int(x) for x in os.environ.get("FSUB_CHANNELS", "-1002323796637 -1002690380584").split()] # একাধিক চ্যানেল আইডি স্পেস দিয়ে আলাদা করুন
ADMINS = [int(x) for x in os.environ.get("ADMINS", "5926160191").split()] # একাধিক অ্যাডমিন আইডি স্পেস দিয়ে আলাদা করুন

# 🔹 MongoDB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["postbot"]
users_collection = db["users"] # 'users' থেকে 'users_collection' করা হলো নামকরণের ধারাবাহিকতা বজায় রাখতে
reactions_collection = db["reactions"]  # For reaction system

# 🔹 বট ক্লায়েন্ট
app = Client("ChannelPostBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# 🔹 Flask health server
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask).start()

# 🟢 হেল্পার ফাংশন
async def is_admin(bot: Client, user_id: int, chat_id: int):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception:
        return False

async def save_channel(user_id: int, channel_id: int, channel_title: str):
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        await users_collection.insert_one({"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []})
        user = {"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []}

    for ch in user["channels"]:
        if ch["id"] == channel_id:
            return False

    user["channels"].append({"id": channel_id, "title": channel_title})
    await users_collection.update_one({"user_id": user_id}, {"$set": {"channels": user["channels"]}})
    return True

# ❗ নতুন যোগ করা হয়েছে: ফোর্স সাবস্ক্রিপশন চেক ফাংশন
async def check_fsub(bot: Client, user_id: int):
    if not FSUB_CHANNELS:
        return True, None

    not_joined_channels = []
    keyboard = []
    
    for channel_id in FSUB_CHANNELS:
        try:
            chat_member = await bot.get_chat_member(channel_id, user_id)
            if chat_member.status in [
                enums.ChatMemberStatus.BANNED,
                enums.ChatMemberStatus.LEFT,
                enums.ChatMemberStatus.RESTRICTED  # Added restricted for thorough check
            ]:
                not_joined_channels.append(channel_id)
        except Exception as e:
            # User might not have started bot or channel is private and no request sent yet
            logger.warning(f"Error checking FSub for user {user_id} in channel {channel_id}: {e}")
            not_joined_channels.append(channel_id)

    if not not_joined_channels:
        return True, None # All channels joined

    # Construct force subscribe buttons for not joined channels
    for channel_id in not_joined_channels:
        try:
            chat = await bot.get_chat(channel_id)
            if chat.type == enums.ChatType.PRIVATE:
                # Private channel, request to join link
                invite_link = await bot.create_chat_invite_link(chat_id=channel_id, member_limit=1) # Temporary invite link
                keyboard.append([InlineKeyboardButton(f"➕ {chat.title}", url=invite_link.invite_link)])
            else:
                # Public channel, direct link
                keyboard.append([InlineKeyboardButton(f"➕ {chat.title}", url=chat.invite_link or chat.username or f"https://t.me/{chat.id}")])
        except Exception as e:
            logger.error(f"Error getting chat info or creating invite link for {channel_id}: {e}")
            # Fallback for unknown error, try with just ID
            keyboard.append([InlineKeyboardButton(f"➕ Channel {channel_id}", url=f"https://t.me/{channel_id}")])

    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="fsub_refresh")])
    return False, InlineKeyboardMarkup(keyboard)

# 🟢 /start
@app.on_message(filters.private & filters.command("start"))
async def start_handler(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "👋 Welcome!\n\n"
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    await msg.reply_text(
        "👋 Welcome!\n\n"
        "➕ Use /addchannel <id> → Add a channel\n"
        "📌 Or forward a post from your channel\n"
        "📂 /mychannels → See saved channels\n"
        "🗑 /delchannel → Delete channel\n\n"
        "✍️ /setcap <caption> → Set custom caption\n"
        "👀 /seecap → See caption\n"
        "❌ /delcap → Delete caption\n\n"
        "🔘 /addbutton <text> <url> → Add custom button\n"
        "📂 /mybuttons → See your buttons\n"
        "🗑 /delbutton → Delete a button\n"
        "♻️ /clearbuttons → Clear all buttons"
    )

# ❗ নতুন যোগ করা হয়েছে: /stats কমাণ্ড
@app.on_message(filters.private & filters.command("stats") & filters.user(ADMINS))
async def stats_handler(bot, msg: Message):
    users_count = await users_collection.count_documents({})
    await msg.reply_text(f"📊 Current bot users: {users_count}")

# ❗ নতুন যোগ করা হয়েছে: /broadcast কমাণ্ড
@app.on_message(filters.private & filters.command("broadcast") & filters.user(ADMINS))
async def broadcast_handler(bot, msg: Message):
    if msg.reply_to_message:
        sent_count = 0
        failed_count = 0
        total_users = await users_collection.count_documents({})
        
        status_msg = await msg.reply_text(f"🚀 Broadcasting message to {total_users} users...")

        async for user_doc in users_collection.find({}):
            user_id = user_doc["user_id"]
            try:
                await msg.reply_to_message.copy(user_id)
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to {user_id}: {e}")
                failed_count += 1
            # Optionally add a small delay to avoid rate limits
            # await asyncio.sleep(0.1) 
        
        await status_msg.edit_text(f"✅ Broadcast finished!\n\nSent to: {sent_count}\nFailed to send: {failed_count}")
    else:
        await msg.reply_text("⚠️ Reply to a message to broadcast it.")

# 🟢 Channel & Button Commands
@app.on_message(filters.private & filters.command("addchannel"))
async def add_channel_cmd(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Please give a channel ID.\nExample: `/addchannel -1001234567891`")
    channel_id = int(msg.command[1])
    chat = await bot.get_chat(channel_id)
    user = await users_collection.find_one({"user_id": msg.from_user.id}) or {"channels": []}
    already_saved = any(ch["id"] == channel_id for ch in user["channels"])
    if already_saved:
        await msg.reply_text("⚠️ This channel is already in your list.")
        return
    me = await bot.get_me()
    if not await is_admin(bot, me.id, channel_id):
        return await msg.reply_text("❌ Please give me admin rights in that channel first!")
    await save_channel(msg.from_user.id, channel_id, chat.title)
    await msg.reply_text(f"✅ Channel **{chat.title}** has been set successfully!")

@app.on_message(filters.private & filters.forwarded)
async def forward_handler(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    if not msg.forward_from_chat:
        return await msg.reply_text("⚠️ This is not a valid channel post!")
    channel = msg.forward_from_chat
    user = await users_collection.find_one({"user_id": msg.from_user.id}) or {"channels": []}
    already_saved = any(ch["id"] == channel.id for ch in user["channels"])
    if not already_saved:
        me = await bot.get_me()
        if not await is_admin(bot, me.id, channel.id):
            return await msg.reply_text("❌ Please give me admin rights in that channel first!")
        await save_channel(msg.from_user.id, channel.id, channel.title)
        await msg.reply_text(f"✅ Channel **{channel.title}** has been set successfully!")
    else:
        await msg.reply_text("⚠️ This channel is already in your list.")

@app.on_message(filters.private & filters.command("mychannels"))
async def my_channels(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("📂 You don’t have any channels saved yet.")
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"sendto_{ch['id']}_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("📂 Your saved channels:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("delchannel"))
async def del_channel(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("📂 You don’t have any channels saved yet.")
    buttons = [[InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("🗑 Select a channel to delete:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Custom Button Commands
@app.on_message(filters.private & filters.command("addbutton"))
async def add_button(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    if not msg.text or len(msg.text.split()) < 3:
        return await msg.reply_text(
            "⚠️ Usage: `/addbutton text url`\n\n"
            "💡 Example: `/addbutton PrimeCineZone https://t.me/PrimeXBots`"
        )

    parts = msg.text.split(maxsplit=2)
    text = parts[1]
    url = parts[2]

    user = await users_collection.find_one({"user_id": msg.from_user.id}) or {}
    buttons = user.get("custom_buttons", [])
    buttons.append({"text": text, "url": url})

    await users_collection.update_one(
        {"user_id": msg.from_user.id},
        {"$set": {"custom_buttons": buttons}},
        upsert=True
    )

    await msg.reply_text(f"✅ Button **{text}** added successfully!")
    
@app.on_message(filters.private & filters.command("mybuttons"))
async def my_buttons(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_buttons"):
        return await msg.reply_text("📂 You don’t have any custom buttons yet.")
    buttons = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user["custom_buttons"]]
    await msg.reply_text("📂 Your custom buttons:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("delbutton"))
async def del_button(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_buttons"):
        return await msg.reply_text("📂 You don’t have any custom buttons to delete.")
    buttons = [[InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"delbtn_{b['text']}")] for b in user["custom_buttons"]]
    await msg.reply_text("🗑 Select a button to delete:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("clearbuttons"))
async def clear_buttons(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    await users_collection.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_buttons": []}})
    await msg.reply_text("🗑 All custom buttons cleared!")

# 🟢 Caption Commands
@app.on_message(filters.private & filters.command("setcap"))
async def set_cap(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Usage: `/setcap your caption Here`")
    caption = msg.text.split(" ", 1)[1]
    await users_collection.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": caption}}, upsert=True)
    await msg.reply_text("✅ Custom caption set successfully!")

@app.on_message(filters.private & filters.command("seecap"))
async def see_cap(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_caption"):
        return await msg.reply_text("⚠️ You don’t have any custom caption set.")
    await msg.reply_text(f"📝 Your caption:\n\n{user['custom_caption']}")

@app.on_message(filters.private & filters.command("delcap"))
async def del_cap(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    await users_collection.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": None}})
    await msg.reply_text("🗑 Custom caption deleted!")

# 🟢 Media Handler
@app.on_message(filters.private & (filters.photo | filters.video))
async def media_handler(bot, msg: Message):
    # ❗ ফোর্স সাবস্ক্রিপশন চেক যোগ করা হয়েছে
    is_joined, keyboard = await check_fsub(bot, msg.from_user.id)
    if not is_joined:
        await msg.reply_text(
            "⚠️ You must join our channels to use this bot.",
            reply_markup=keyboard
        )
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("⚠️ You have no channels set. Use /addchannel first.")
    await users_collection.update_one({"user_id": msg.from_user.id}, {"$set": {"last_media": msg.id}})
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"sendto_{msg.id}_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("📤 Select a channel to post:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Callback Handler (Channel Delete, Button Delete, Media Post, Reactions, Force Sub Refresh)
@app.on_callback_query()
async def callback_handler(bot, cq: CallbackQuery):
    data = cq.data

    # ❗ নতুন যোগ করা হয়েছে: ফোর্স সাবস্ক্রিপশন রিফ্রেশ
    if data == "fsub_refresh":
        is_joined, keyboard = await check_fsub(bot, cq.from_user.id)
        if is_joined:
            await cq.message.edit_text("✅ Thank you for joining! Now you can use this bot.", reply_markup=None)
            # Optionally, send the /start message again after successful join
            await start_handler(bot, cq.message) # Calling start handler to show main menu
        else:
            await cq.answer("⚠️ আপনি এখনো আমাদের সবগুলো চ্যানেলে জয়েন করে নেই তাই দয়া করে জয়েন করে রিফ্রেশ করুন।", show_alert=True)
        return

    # ❗ অন্যান্য কমাণ্ড চালানোর আগে ফোর্স সাবস্ক্রিপশন চেক
    is_joined, keyboard = await check_fsub(bot, cq.from_user.id)
    if not is_joined:
        # If user is not joined, inform them and don't process further commands
        await cq.answer("⚠️ You must join our channels to use this bot. Please refresh after joining.", show_alert=True)
        return

    # Channel Delete
    if data.startswith("delch_"):
        ch_id = int(data.split("_")[1])
        user = await users_collection.find_one({"user_id": cq.from_user.id})
        new_channels = [ch for ch in user["channels"] if ch["id"] != ch_id]
        await users_collection.update_one({"user_id": cq.from_user.id}, {"$set": {"channels": new_channels}})
        await cq.answer("🗑 Channel deleted!", show_alert=True)
        # Update the message with remaining channels or a message that no channels are left
        if new_channels:
            buttons = [[InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")] for ch in new_channels]
            await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await cq.message.edit_text("🗑 All channels deleted!")
        return
        
    # Button Delete
    if data.startswith("delbtn_"):
        text = data.split("_", 1)[1]
        user = await users_collection.find_one({"user_id": cq.from_user.id})
        new_buttons = [b for b in user["custom_buttons"] if b["text"] != text]
        await users_collection.update_one({"user_id": cq.from_user.id}, {"$set": {"custom_buttons": new_buttons}})
        await cq.answer(f"🗑 Button '{text}' deleted!", show_alert=True)
        # Update the message with remaining buttons or a message that no buttons are left
        if new_buttons:
            buttons = [[InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"delbtn_{b['text']}")] for b in new_buttons]
            await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await cq.message.edit_text("🗑 All custom buttons deleted!")
        return

    # Reaction System
    if data.startswith("react_"):
        _, msg_id, reaction = data.split("_")
        msg_id = int(msg_id)
        user_id = cq.from_user.id

        post = await reactions_collection.find_one({"message_id": msg_id})
        if not post:
            post = {"message_id": msg_id, "reactions": {"like": [], "love": []}}
            # If post not found in DB, it might be an older post. Initialize it.
            await reactions_collection.insert_one(post)

        # Remove previous reaction
        for r_type in post["reactions"]:
            if user_id in post["reactions"][r_type]:
                post["reactions"][r_type].remove(user_id)

        # Add new reaction if it's not the same reaction being un-selected
        if user_id not in post["reactions"].get(reaction, []):
            post["reactions"].setdefault(reaction, []).append(user_id)
        
        await reactions_collection.update_one({"message_id": msg_id}, {"$set": {"reactions": post["reactions"]}})

        like_count = len(post["reactions"].get("like", []))
        love_count = len(post["reactions"].get("love", []))

        # Preserve custom and fixed buttons
        current_buttons = cq.message.reply_markup.inline_keyboard
        
        # Determine the number of rows before reaction row for custom buttons
        # Assuming reaction row is always the first, and fixed row is always the last.
        # This means custom buttons are in between.
        
        # Check if the current buttons contain the fixed row.
        # This helps in adapting to posts that might not have custom buttons.
        fixed_row_exists = False
        fixed_button_text = "কিভাবে ডাউনলোড করবেন"
        for row in current_buttons:
            for button in row:
                if button.text == fixed_button_text:
                    fixed_row_exists = True
                    break
            if fixed_row_exists:
                break
        
        custom_buttons = []
        if len(current_buttons) > 1: # If there's more than just the reaction row
            if fixed_row_exists: # If fixed row is present, take everything between first and last
                custom_buttons = current_buttons[1:-1]
            else: # If fixed row is NOT present, then all other rows are custom buttons
                custom_buttons = current_buttons[1:]

        reaction_row = [
            InlineKeyboardButton(f"👍 {like_count}", callback_data=f"react_{msg_id}_like"),
            InlineKeyboardButton(f"❤️ {love_count}", callback_data=f"react_{msg_id}_love")
        ]
        
        new_keyboard_rows = [reaction_row] + custom_buttons
        if fixed_row_exists:
            new_keyboard_rows.append(current_buttons[-1]) # Add the original fixed row back

        await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard_rows))
        await cq.answer("✅ Your reaction updated!", show_alert=False)
        return

    # Media Post
    if data.startswith("sendto_"):
        _, msg_id, channel_id = data.split("_")
        msg_id = int(msg_id)
        channel_id = int(channel_id)
        user = await users_collection.find_one({"user_id": cq.from_user.id})
        if not user or not user.get("last_media"):
            return await cq.answer("⚠️ Media not found!", show_alert=True)
        try:
            media_msg = await bot.get_messages(cq.from_user.id, msg_id)
            user_caption = user.get("custom_caption") or ""
            fixed_caption = (
                "🔥 Quality: HDTS\n"
                "📌 Indian User Use 1.1.1.1 VPN\n"
                "👉 Visit Site"
            )
            final_caption = ""
            if media_msg.caption:
                final_caption += media_msg.caption + "\n\n"
            if user_caption:
                final_caption += user_caption + "\n\n"
            final_caption += fixed_caption

            # Custom buttons
            custom_btns = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user.get("custom_buttons", [])]

            # Initial reaction row
            reaction_row = [
                InlineKeyboardButton("👍 ", callback_data=f"react_{msg_id}_like"),
                InlineKeyboardButton("❤️ ", callback_data=f"react_{msg_id}_love")
            ]

            fixed_row = [InlineKeyboardButton("কিভাবে ডাউনলোড করবেন", url=REQUEST_GROUP_URL)]
            all_buttons = [reaction_row] + custom_btns + [fixed_row]

            copied_msg = await media_msg.copy(
                chat_id=channel_id,
                caption=final_caption,
                reply_markup=InlineKeyboardMarkup(all_buttons)
            )

            # Initialize reactions in DB
            await reactions_collection.update_one(
                {"message_id": copied_msg.id},
                {"$set": {"reactions": {"like": [], "love": []}}},
                upsert=True
            )

            await cq.answer("✅ Posted successfully!", show_alert=True)

        except Exception as e:
            logger.error(f"Failed to post media: {e}")
            await cq.answer(f"❌ Failed to post! Error: {e}", show_alert=True)

# 🟢 Run
app.run()
