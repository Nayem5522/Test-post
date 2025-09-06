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
REQUEST_GROUP_URL = "https://t.me/Prime_Movies4U"

# 🔹 MongoDB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["postbot"]
users = db["users"]

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

# Flask আলাদা থ্রেডে চালানো হবে
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
        await users.insert_one({"user_id": user_id, "channels": []})
        user = {"user_id": user_id, "channels": []}

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
        "➕ Use /addchannel <id> to add a channel\n"
        "📌 Or just forward a post from your channel.\n"
        "📂 Use /mychannels to check your saved channels."
    )

# 🟢 /addchannel <id>
@app.on_message(filters.private & filters.command("addchannel"))
async def add_channel_cmd(bot, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Please give a channel ID.\nExample: `/addchannel -1001234567890`")

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

# 🟢 ফরওয়ার্ড পোস্ট থেকে অ্যাড
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

# 🟢 /mychannels
@app.on_message(filters.private & filters.command("mychannels"))
async def my_channels(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user["channels"]:
        return await msg.reply_text("📂 You don’t have any channels saved yet.")

    buttons = [
        [InlineKeyboardButton(ch["title"], callback_data=f"postto_{ch['id']}")]
        for ch in user["channels"]
    ]

    await msg.reply_text("📂 Your saved channels:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 মিডিয়া হ্যান্ডলার
@app.on_message(filters.private & (filters.photo | filters.video))
async def media_handler(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("⚠️ You have no channels set. Use /addchannel first.")

    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"last_media": msg.id}})

    buttons = [
        [InlineKeyboardButton(ch["title"], callback_data=f"sendto_{msg.id}_{ch['id']}")]
        for ch in user["channels"]
    ]

    await msg.reply_text("📤 Select a channel to post:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Callback হ্যান্ডলার
@app.on_callback_query()
async def callback_handler(bot, cq: CallbackQuery):
    data = cq.data

    if data.startswith("sendto_"):
        _, msg_id, channel_id = data.split("_")
        msg_id = int(msg_id)
        channel_id = int(channel_id)

        user = await users.find_one({"user_id": cq.from_user.id})
        if not user or not user.get("last_media"):
            return await cq.answer("⚠️ Media not found!", show_alert=True)

        try:
            media_msg = await bot.get_messages(cq.from_user.id, msg_id)

            fixed_caption = (
                "🔥 Quality: HDTS\n"
                "📌 Indian User Use 1.1.1.1 VPN\n"
                "👉 Visit Site"
            )

            final_caption = f"{media_msg.caption}\n\n{fixed_caption}" if media_msg.caption else fixed_caption

            buttons = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("👍", callback_data="like"),
                     InlineKeyboardButton("❤️", callback_data="love")],
                    [InlineKeyboardButton("কিভাবে ডাউনলোড করবেন", url=REQUEST_GROUP_URL)]
                ]
            )

            await media_msg.copy(
                chat_id=channel_id,
                caption=final_caption,
                reply_markup=buttons
            )

            await cq.answer("✅ Posted successfully!", show_alert=True)

        except Exception as e:
            logger.error(e)
            await cq.answer("❌ Failed to post!", show_alert=True)

# 🟢 রান
app.run()
