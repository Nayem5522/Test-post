import os
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask
import threading

# 🔹 Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔹 Config
API_ID = int(os.environ.get("API_ID", 12345))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_url")
REQUEST_GROUP_URL = "https://t.me/PrimeCineZone/31"

# 🔹 MongoDB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["postbot"]
users = db["users"]
reactions_col = db["reactions"]

# 🔹 Bot client
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

# 🟢 Helper functions
async def is_admin(bot: Client, user_id: int, chat_id: int):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except:
        return False

async def save_channel(user_id: int, channel_id: int, channel_title: str):
    user = await users.find_one({"user_id": user_id})
    if not user:
        await users.insert_one({"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []})
        user = {"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []}
    if any(ch["id"] == channel_id for ch in user["channels"]):
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

# 🟢 Add channel via command
@app.on_message(filters.private & filters.command("addchannel"))
async def add_channel_cmd(bot, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Usage: /addchannel -1001234567890")
    channel_id = int(msg.command[1])
    chat = await bot.get_chat(channel_id)
    user = await users.find_one({"user_id": msg.from_user.id}) or {"channels": []}
    if any(ch["id"] == channel_id for ch in user["channels"]):
        return await msg.reply_text("⚠️ This channel is already added.")
    me = await bot.get_me()
    if not await is_admin(bot, me.id, channel_id):
        return await msg.reply_text("❌ Give me admin rights first!")
    await save_channel(msg.from_user.id, channel_id, chat.title)
    await msg.reply_text(f"✅ Channel **{chat.title}** added successfully!")

# 🟢 Forwarded post to add channel
@app.on_message(filters.private & filters.forwarded)
async def forward_handler(bot, msg: Message):
    if not msg.forward_from_chat:
        return await msg.reply_text("⚠️ Not a valid channel post!")
    channel = msg.forward_from_chat
    user = await users.find_one({"user_id": msg.from_user.id}) or {"channels": []}
    if any(ch["id"] == channel.id for ch in user["channels"]):
        return await msg.reply_text("⚠️ This channel is already added.")
    me = await bot.get_me()
    if not await is_admin(bot, me.id, channel.id):
        return await msg.reply_text("❌ Give me admin rights first!")
    await save_channel(msg.from_user.id, channel.id, channel.title)
    await msg.reply_text(f"✅ Channel **{channel.title}** added successfully!")

# 🟢 /mychannels
@app.on_message(filters.private & filters.command("mychannels"))
async def my_channels(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user["channels"]:
        return await msg.reply_text("📂 You have no channels yet.")
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"postto_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("📂 Your saved channels:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 /delchannel
@app.on_message(filters.private & filters.command("delchannel"))
async def del_channel(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user["channels"]:
        return await msg.reply_text("📂 No channels to delete.")
    buttons = [[InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("🗑 Select a channel to delete:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Media handler
@app.on_message(filters.private & (filters.photo | filters.video))
async def media_handler(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("⚠️ No channels set. Use /addchannel first.")
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"last_media": msg.id}})
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"sendto_{msg.id}_{ch['id']}")] for ch in user["channels"]]
    await msg.reply_text("📤 Select a channel to post:", reply_markup=InlineKeyboardMarkup(buttons))

# 🟢 Callback handler (Delete channel/button, reactions, media post)
@app.on_callback_query()
async def callback_handler(bot, cq: CallbackQuery):
    data = cq.data
    user = await users.find_one({"user_id": cq.from_user.id})

    # Reaction system
    if data in ["like", "love"]:
        chat_id, post_id, user_id = cq.message.chat.id, cq.message.id, cq.from_user.id
        doc = await reactions_col.find_one({"chat_id": chat_id, "post_id": post_id})
        if not doc:
            doc = {"chat_id": chat_id, "post_id": post_id, "reactions": {"like": [], "love": []}}
            await reactions_col.insert_one(doc)
        for r in ["like", "love"]:
            if user_id in doc["reactions"][r]:
                doc["reactions"][r].remove(user_id)
        doc["reactions"][data].append(user_id)
        await reactions_col.update_one({"chat_id": chat_id, "post_id": post_id}, {"$set": {"reactions": doc["reactions"]}})
        like_count = len(doc["reactions"]["like"])
        love_count = len(doc["reactions"]["love"])
        def fmt_btn(emoji, count, cdata): return InlineKeyboardButton(f"{emoji} {count}" if count>0 else emoji, callback_data=cdata)
        orig_buttons = cq.message.reply_markup.inline_keyboard if cq.message.reply_markup else []
        new_buttons = []
        for row in orig_buttons:
            new_row = []
            for btn in row:
                if btn.callback_data in ["like","love"]:
                    new_row.append(fmt_btn("👍" if btn.callback_data=="like" else "❤️", like_count if btn.callback_data=="like" else love_count, btn.callback_data))
                else:
                    new_row.append(btn)
            new_buttons.append(new_row)
        await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(new_buttons))
        return await cq.answer("✅ Reaction updated!")

    # Delete channel
    if data.startswith("delch_"):
        ch_id = int(data.split("_")[1])
        new_channels = [ch for ch in user["channels"] if ch["id"] != ch_id]
        await users.update_one({"user_id": cq.from_user.id}, {"$set": {"channels": new_channels}})
        return await cq.answer("🗑 Channel deleted!", show_alert=True)

    # Delete button
    if data.startswith("delbtn_"):
        text = data.split("_",1)[1]
        new_buttons = [b for b in user.get("custom_buttons",[]) if b["text"] != text]
        await users.update_one({"user_id": cq.from_user.id}, {"$set": {"custom_buttons": new_buttons}})
        return await cq.answer(f"🗑 Button '{text}' deleted!", show_alert=True)

    # Media post
    if data.startswith("sendto_"):
        _, msg_id, channel_id = data.split("_")
        msg_id, channel_id = int(msg_id), int(channel_id)
        if not user or not user.get("last_media"):
            return await cq.answer("⚠️ Media not found!", show_alert=True)
        try:
            media_msg = await bot.get_messages(cq.from_user.id, msg_id)
            user_caption = user.get("custom_caption") or ""
            fixed_caption = "🔥 Quality: HDTS\n📌 Indian User Use 1.1.1.1 VPN\n👉 Visit Site"
            final_caption = f"{media_msg.caption or ''}\n\n{user_caption}\n\n{fixed_caption}".strip()
            custom_btns = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user.get("custom_buttons",[])]
            fixed_btns = [[InlineKeyboardButton("👍", callback_data="like"), InlineKeyboardButton("❤️", callback_data="love")],[InlineKeyboardButton("কিভাবে ডাউনলোড করবেন", url=REQUEST_GROUP_URL)]]
            all_buttons = custom_btns + fixed_btns
            await media_msg.copy(chat_id=channel_id, caption=final_caption, reply_markup=InlineKeyboardMarkup(all_buttons))
            return await cq.answer("✅ Posted successfully!", show_alert=True)
        except Exception as e:
            logger.error(e)
            return await cq.answer("❌ Failed to post!", show_alert=True)

# 🟢 Run bot
app.run()
