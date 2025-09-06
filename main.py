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

# 🔹 MongoDB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["postbot"]
users = db["users"]
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
    user = await users.find_one({"user_id": user_id})
    if not user:
        await users.insert_one({"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []})
        user = {"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []}

    for ch in user["channels"]:
        if ch["id"] == channel_id:
            return False

    user["channels"].append({"id": channel_id, "title": channel_title})
    await users.update_one({"user_id": user_id}, {"$set": {"channels": user["channels"]}})
    return True

# 🟢 /start
@app.on_message(filters.private & filters.command("start"))
async def start_handler(bot, msg: Message):
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

# 🟢 Channel & Button Commands
@app.on_message(filters.private & filters.command("addchannel"))
async def add_channel_cmd(bot, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Please give a channel ID.\nExample: `/addchannel -1001234567891`")
    channel_id = int(msg.command[1])
    chat = await bot.get_chat(channel_id)
    user = await users.find_one({"user_id": msg.from_user.id}) or {"channels": []}
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
    if not msg.forward_from_chat:
        return await msg.reply_text("⚠️ This is not a valid channel post!")
    channel = msg.forward_from_chat
    user = await users.find_one({"user_id": msg.from_user.id}) or {"channels": []}
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
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("📂 You don’t have any channels saved yet.")
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"sendto_{ch['id']}_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("📂 Your saved channels:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("delchannel"))
async def del_channel(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("📂 You don’t have any channels saved yet.")
    buttons = [[InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("🗑 Select a channel to delete:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Custom Button Commands
@app.on_message(filters.private & filters.command("addbutton"))
async def add_button(bot, msg: Message):
    if not msg.text or len(msg.text.split()) < 3:
        return await msg.reply_text(
            "⚠️ Usage: <code>/addbutton <text> <url></code>\n\n"
            "💡 Example: `/addbutton PrimeCineZone https://t.me/PrimeXBots`"
        )

    parts = msg.text.split(maxsplit=2)
    text = parts[1]
    url = parts[2]

    user = await users.find_one({"user_id": msg.from_user.id}) or {}
    buttons = user.get("custom_buttons", [])
    buttons.append({"text": text, "url": url})

    await users.update_one(
        {"user_id": msg.from_user.id},
        {"$set": {"custom_buttons": buttons}},
        upsert=True
    )

    await msg.reply_text(f"✅ Button **{text}** added successfully!")
    
@app.on_message(filters.private & filters.command("mybuttons"))
async def my_buttons(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_buttons"):
        return await msg.reply_text("📂 You don’t have any custom buttons yet.")
    buttons = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user["custom_buttons"]]
    await msg.reply_text("📂 Your custom buttons:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("delbutton"))
async def del_button(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_buttons"):
        return await msg.reply_text("📂 You don’t have any custom buttons to delete.")
    buttons = [[InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"delbtn_{b['text']}")] for b in user["custom_buttons"]]
    await msg.reply_text("🗑 Select a button to delete:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("clearbuttons"))
async def clear_buttons(bot, msg: Message):
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_buttons": []}})
    await msg.reply_text("🗑 All custom buttons cleared!")

# 🟢 Caption Commands
@app.on_message(filters.private & filters.command("setcap"))
async def set_cap(bot, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Usage: <code>/setcap <your caption></code>")
    caption = msg.text.split(" ", 1)[1]
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": caption}}, upsert=True)
    await msg.reply_text("✅ Custom caption set successfully!")

@app.on_message(filters.private & filters.command("seecap"))
async def see_cap(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_caption"):
        return await msg.reply_text("⚠️ You don’t have any custom caption set.")
    await msg.reply_text(f"📝 Your caption:\n\n{user['custom_caption']}")

@app.on_message(filters.private & filters.command("delcap"))
async def del_cap(bot, msg: Message):
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": None}})
    await msg.reply_text("🗑 Custom caption deleted!")

# 🟢 Media Handler
@app.on_message(filters.private & (filters.photo | filters.video))
async def media_handler(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("⚠️ You have no channels set. Use /addchannel first.")
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"last_media": msg.id}})
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"sendto_{msg.id}_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("📤 Select a channel to post:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Callback Handler (Channel Delete, Button Delete, Media Post, Reactions)
@app.on_callback_query()
async def callback_handler(bot, cq: CallbackQuery):
    data = cq.data

    # Channel Delete
    if data.startswith("delch_"):
        ch_id = int(data.split("_")[1])
        user = await users.find_one({"user_id": cq.from_user.id})
        new_channels = [ch for ch in user["channels"] if ch["id"] != ch_id]
        await users.update_one({"user_id": cq.from_user.id}, {"$set": {"channels": new_channels}})
        await cq.answer("🗑 Channel deleted!", show_alert=True)
        return

    # Button Delete
    if data.startswith("delbtn_"):
        text = data.split("_", 1)[1]
        user = await users.find_one({"user_id": cq.from_user.id})
        new_buttons = [b for b in user["custom_buttons"] if b["text"] != text]
        await users.update_one({"user_id": cq.from_user.id}, {"$set": {"custom_buttons": new_buttons}})
        await cq.answer(f"🗑 Button '{text}' deleted!", show_alert=True)
        return

    # Reaction System
    if data.startswith("react_"):
        _, msg_id, reaction = data.split("_")
        msg_id = int(msg_id)
        user_id = cq.from_user.id

        post = await reactions_collection.find_one({"message_id": msg_id})
        if not post:
            post = {"message_id": msg_id, "reactions": {"like": [], "love": []}}
            await reactions_collection.insert_one(post)

        # Remove previous reaction
        for r_type in post["reactions"]:
            if user_id in post["reactions"][r_type]:
                post["reactions"][r_type].remove(user_id)

        # Add new reaction
        post["reactions"].setdefault(reaction, []).append(user_id)
        await reactions_collection.update_one({"message_id": msg_id}, {"$set": {"reactions": post["reactions"]}})

        like_count = len(post["reactions"].get("like", []))
        love_count = len(post["reactions"].get("love", []))

        # Preserve custom and fixed buttons
        current_buttons = cq.message.reply_markup.inline_keyboard
        custom_buttons = current_buttons[1:-1] if len(current_buttons) > 2 else []
        fixed_row = current_buttons[-1] if current_buttons else [InlineKeyboardButton("কিভাবে ডাউনলোড করবেন", url=REQUEST_GROUP_URL)]

        reaction_row = [
            InlineKeyboardButton(f"👍 {like_count}", callback_data=f"react_{msg_id}_like"),
            InlineKeyboardButton(f"❤️ {love_count}", callback_data=f"react_{msg_id}_love")
        ]
        new_keyboard = [reaction_row] + custom_buttons + [fixed_row]
        await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
        await cq.answer("✅ Your reaction updated!", show_alert=False)
        return

    # Media Post
    if data.startswith("sendto_"):
        _, msg_id, channel_id = data.split("_")
        msg_id = int(msg_id)
        channel_id = int(channel_id)
        user = await users.find_one({"user_id": cq.from_user.id})
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
            logger.error(e)
            await cq.answer("❌ Failed to post!", show_alert=True)

# 🟢 Run
app.run()
