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

# 🟢 কাস্টম বাটন কমান্ডগুলো
@app.on_message(filters.private & filters.command("addbutton"))
async def add_button(bot, msg: Message):
    if len(msg.command) < 3:
        return await msg.reply_text(
            "⚠️ Usage: `/addbutton <text> <url>`\n\n"
            "💡 Example:\n"
            "`/addbutton WatchNow https://primecinezone.com`"
        )

    text = msg.command[1]
    url = msg.command[2]

    user = await users.find_one({"user_id": msg.from_user.id}) or {}
    buttons = user.get("custom_buttons", [])

    buttons.append({"text": text, "url": url})
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_buttons": buttons}}, upsert=True)

    await msg.reply_text(f"✅ Button **{text}** added successfully!\n\n💡 Use /mybuttons to see all buttons.")

@app.on_message(filters.private & filters.command("mybuttons"))
async def my_buttons(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_buttons"):
        return await msg.reply_text("📂 You don’t have any custom buttons yet.\n\n💡 Add with `/addbutton <text> <url>`")

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

# 🟢 Caption commands (unchanged: setcap, seecap, delcap)
@app.on_message(filters.private & filters.command("setcap"))
async def set_cap(bot, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Usage: /setcap <your caption>\n\n💡 Example:\n`/setcap My Custom Caption`")

    caption = msg.text.split(" ", 1)[1]
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": caption}}, upsert=True)
    await msg.reply_text("✅ Custom caption set successfully!")

@app.on_message(filters.private & filters.command("seecap"))
async def see_cap(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("custom_caption"):
        return await msg.reply_text("⚠️ You don’t have any custom caption set.\n\n💡 Use `/setcap <caption>`")
    await msg.reply_text(f"📝 Your caption:\n\n{user['custom_caption']}")

@app.on_message(filters.private & filters.command("delcap"))
async def del_cap(bot, msg: Message):
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": None}})
    await msg.reply_text("🗑 Custom caption deleted!")

# 🟢 Callback হ্যান্ডলার (চ্যানেল ডিলিট + বাটন ডিলিট + মিডিয়া পোস্ট)
@app.on_callback_query()
async def callback_handler(bot, cq: CallbackQuery):
    data = cq.data

    # চ্যানেল ডিলিট
    if data.startswith("delch_"):
        ch_id = int(data.split("_")[1])
        user = await users.find_one({"user_id": cq.from_user.id})
        new_channels = [ch for ch in user["channels"] if ch["id"] != ch_id]
        await users.update_one({"user_id": cq.from_user.id}, {"$set": {"channels": new_channels}})
        await cq.answer("🗑 Channel deleted!", show_alert=True)
        return

    # বাটন ডিলিট
    if data.startswith("delbtn_"):
        text = data.split("_", 1)[1]
        user = await users.find_one({"user_id": cq.from_user.id})
        new_buttons = [b for b in user["custom_buttons"] if b["text"] != text]
        await users.update_one({"user_id": cq.from_user.id}, {"$set": {"custom_buttons": new_buttons}})
        await cq.answer(f"🗑 Button '{text}' deleted!", show_alert=True)
        return

    # মিডিয়া পোস্ট
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

            # ইউজারের কাস্টম বাটন + আপনার ফিক্সড বাটন
            custom_btns = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user.get("custom_buttons", [])]
            fixed_btns = [
                [InlineKeyboardButton("👍", callback_data="like"),
                 InlineKeyboardButton("❤️", callback_data="love")],
                [InlineKeyboardButton("কিভাবে ডাউনলোড করবেন", url=REQUEST_GROUP_URL)]
            ]
            all_buttons = custom_btns + fixed_btns

            await media_msg.copy(
                chat_id=channel_id,
                caption=final_caption,
                reply_markup=InlineKeyboardMarkup(all_buttons)
            )

            await cq.answer("✅ Posted successfully!", show_alert=True)

        except Exception as e:
            logger.error(e)
            await cq.answer("❌ Failed to post!", show_alert=True)


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


# 🟢 রান
app.run()
