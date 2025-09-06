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
AUTH_CHANNEL = -1002323796637

# 🔹 MongoDB
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["postbot"]
users = db["users"]
reactions_collection = db["reactions"]

# 🔹 Pyrogram Bot
app = Client("ChannelPostBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# 🔹 Flask health check
flask_app = Flask(__name__)
@flask_app.route("/")
def index():
    return "Bot is running!", 200
threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).start()

# 🔹 Helpers
async def is_subscribed(bot, user_id, channels):
    if isinstance(channels, int): channels = [channels]
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ["kicked", "banned"]:
                return False
        except Exception:
            return False
    return True

async def is_admin(bot, user_id: int, chat_id: int):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception:
        return False

async def ensure_admin(bot: Client, channel_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel_id, me.id)
        if member.status == enums.ChatMemberStatus.OWNER: return True
        if member.status == enums.ChatMemberStatus.ADMINISTRATOR:
            if hasattr(member, "privileges") and member.privileges: return member.privileges.can_post_messages
            if hasattr(member, "can_post_messages"): return member.can_post_messages
        return False
    except Exception as e:
        logger.error(f"Admin check failed for {channel_id}: {e}")
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
    subscribed = await is_subscribed(bot, msg.from_user.id, AUTH_CHANNEL)
    if not subscribed:
        chat = await bot.get_chat(AUTH_CHANNEL)
        invite_link = chat.invite_link or await bot.export_chat_invite_link(AUTH_CHANNEL)
        btns = [[InlineKeyboardButton(f"✇ Join {chat.title} ✇", url=invite_link)],
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_check")]]
        await msg.reply_photo(
            photo="https://i.postimg.cc/fyrXmg6S/file-000000004e7461faaef2bd964cbbd408.png",
            caption=f"👋 Hello {msg.from_user.mention},\n\nJoin our channel to use the bot.",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        return
    buttons = [
        [
            InlineKeyboardButton("✪ ꜱᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ ✪", url="https://t.me/Prime_Support_group"),
            InlineKeyboardButton("〄 ᴍᴏᴠɪᴇ ᴄʜᴀɴɴᴇʟ 〄", url="https://t.me/PrimeCineZone")
        ],
        [InlineKeyboardButton("〄 ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ 〄", url="https://t.me/PrimeXBots")],
        [
            InlineKeyboardButton("〆 ʜᴇʟᴘ 〆", callback_data="help_btn"),
            InlineKeyboardButton("〆 ᴀʙᴏᴜᴛ 〆", callback_data="about_btn")
        ],
        [InlineKeyboardButton("✧ ᴄʀᴇᴀᴛᴏʀ ✧", url="https://t.me/Prime_Nayem")]
    ]
    await msg.reply_photo(
        photo="https://i.postimg.cc/fyrXmg6S/file-000000004e7461faaef2bd964cbbd408.png",
        caption=f"👋 Hi {msg.from_user.mention},\nI am **Post Generator Prime Bot** 🤖\n\nUse the buttons below to navigate.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# 🟢 /help callback
@app.on_message(filters.private & filters.command("help"))
async def help_handler(bot, msg: Message):
    help_text = (
        "📚 **Help Menu**\n\n"
        "➕ /addchannel <id> → Add a channel\n"
        "📌 Forward a post → Save channel automatically\n"
        "📂 /mychannels → See saved channels\n"
        "🗑 /delchannel → Delete channel\n\n"
        "✍️ /setcap <caption> → Set custom caption\n"
        "👀 /seecap → View caption\n"
        "❌ /delcap → Delete caption\n\n"
        "🔘 /addbutton <text> <url> → Add custom button\n"
        "📂 /mybuttons → View custom buttons\n"
        "🗑 /delbutton → Delete a button\n"
        "♻️ /clearbuttons → Clear all buttons\n\n"
        "📤 Send photo/video → Select channel to post\n"
        "👍 React to posts with Like ❤️ Love"
    )
    await msg.reply_text(help_text)

# 🟢 /about callback button
@app.on_callback_query(filters.regex("about_btn"))
async def about_callback(bot, cq: CallbackQuery):
    user = cq.from_user
    about_text = f"""<b><blockquote>⍟───[  <a href='https://t.me/PrimeXBots'>MY ᴅᴇᴛᴀɪʟꜱ ʙy ᴘʀɪᴍᴇXʙᴏᴛs</a> ]───⍟</blockquote>
    
‣ ᴍʏ ɴᴀᴍᴇ : <a href=https://t.me/{user.username}>{user.first_name}</a>
‣ ᴍʏ ʙᴇsᴛ ғʀɪᴇɴᴅ : <a href='tg://settings'>ᴛʜɪs ᴘᴇʀsᴏɴ</a>
‣ ᴅᴇᴠᴇʟᴏᴘᴇʀ : <a href='https://t.me/Prime_Nayem'>ᴍʀ.ᴘʀɪᴍᴇ</a>
‣ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ : <a href='https://t.me/PrimeXBots'>ᴘʀɪᴍᴇXʙᴏᴛꜱ</a>
‣ ᴍᴀɪɴ ᴄʜᴀɴɴᴇʟ : <a href='https://t.me/PrimeCineZone'>Pʀɪᴍᴇ Cɪɴᴇᴢᴏɴᴇ</a>
‣ ѕᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ : <a href='https://t.me/Prime_Support_group'>ᴘʀɪᴍᴇ X ѕᴜᴘᴘᴏʀᴛ</a>
‣ ᴅᴀᴛᴀ ʙᴀsᴇ : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏ ᴅʙ</a>
‣ ʙᴏᴛ sᴇʀᴠᴇʀ : <a href='https://heroku.com'>ʜᴇʀᴏᴋᴜ</a>
‣ ʙᴜɪʟᴅ sᴛᴀᴛᴜs : ᴠ2.7.1 [sᴛᴀʙʟᴇ]</b>"""
    
    await cq.message.edit_text(about_text, disable_web_page_preview=True)
    await cq.answer()

# 🟢 /help callback button
@app.on_callback_query(filters.regex("help_btn"))
async def help_callback(bot, cq: CallbackQuery):
    help_text = (
        "📚 **Help Menu**\n\n"
        "➕ /addchannel <id> → Add a channel\n"
        "📌 Forward a post → Save channel automatically\n"
        "📂 /mychannels → See saved channels\n"
        "🗑 /delchannel → Delete channel\n\n"
        "✍️ /setcap <caption> → Set custom caption\n"
        "👀 /seecap → View caption\n"
        "❌ /delcap → Delete caption\n\n"
        "🔘 /addbutton <text> <url> → Add custom button\n"
        "📂 /mybuttons → View custom buttons\n"
        "🗑 /delbutton → Delete a button\n"
        "♻️ /clearbuttons → Clear all buttons\n\n"
        "📤 Send photo/video → Select channel to post\n"
        "👍 React to posts with Like ❤️ Love"
    )
    await cq.message.edit_text(help_text)
    await cq.answer()

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
            "⚠️ Usage: `/addbutton text url`\n\n"
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
        return await msg.reply_text("⚠️ Usage: `/setcap your caption Here`")
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

# 🟢 Subscription refresh
@app.on_callback_query(filters.regex("refresh_check"))
async def refresh_callback(bot, cq: CallbackQuery):
    subscribed = await is_subscribed(bot, cq.from_user.id, AUTH_CHANNEL)
    if subscribed:
        await cq.message.delete()
        await cq.message.reply_text("✅ Thank you for joining! Now you can use the bot.")
    else:
        await cq.answer("❌ You have not joined yet. Please join first, then refresh.", show_alert=True)


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

        # 🔹 এখানে admin rights refresh check
        if not await ensure_admin(bot, channel_id):
            return await cq.answer("❌ Bot is not admin or missing 'Post Messages' rights!", show_alert=True)

        try:
            media_msg = await bot.get_messages(cq.from_user.id, msg_id)
            user_caption = user.get("custom_caption") or ""
            fixed_caption = (
                "ʙʏ:<a href='https://t.me/PrimeXBots'>@ᴘʀɪᴍᴇXʙᴏᴛꜱ</a>"
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

            #fixed_row = [InlineKeyboardButton("কিভাবে ডাউনলোড করবেন", url=REQUEST_GROUP_URL)]
            all_buttons = [reaction_row] + custom_btns# + [fixed_row]

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
