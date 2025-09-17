import os
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask
import threading

# 🔹 Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 🔹 Config
API_ID = int(os.environ.get("API_ID", "12345")) # Default to a string to avoid int conversion error if not set
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_url")
REQUEST_GROUP_URL = os.environ.get("REQUEST_GROUP_URL", "https://t.me/Prime_Movie_Watch_Dawnload/71") # Make it configurable
AUTH_CHANNEL = int(os.environ.get("AUTH_CHANNEL", "-1002245813234")) # Make it configurable and ensure int type
OWNER_ID = int(os.environ.get("OWNER_ID", "5926160191")) # Make it configurable via env variable

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

def run_flask():
    try:
        flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    except Exception as e:
        logger.error(f"Flask app crashed: {e}")

threading.Thread(target=run_flask).start()

# 🔹 Helpers
async def is_subscribed(bot, user_id, channels):
    if isinstance(channels, int): 
        channels = [channels]
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel, user_id)
            # User is considered subscribed if they are a member, administrator, or owner.
            if member.status in [
                enums.ChatMemberStatus.MEMBER,
                enums.ChatMemberStatus.ADMINISTRATOR,
                enums.ChatMemberStatus.OWNER
            ]:
                return True
        except Exception as e:
            # If get_chat_member raises an error (e.g., UserNotParticipant), they are not subscribed.
            logger.debug(f"Subscription check failed for user {user_id} in channel {channel}: {e}")
            pass # Continue to check other channels if multiple are provided, though in this case it's usually one auth channel.
    return False

async def is_admin(bot, user_id: int, chat_id: int):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception as e:
        logger.error(f"Error checking if user {user_id} is admin in {chat_id}: {e}")
        return False

async def ensure_bot_admin_rights(bot: Client, channel_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel_id, me.id)
        if member.status == enums.ChatMemberStatus.OWNER:
            return True
        if member.status == enums.ChatMemberStatus.ADMINISTRATOR:
            # Check for specific necessary privileges
            if hasattr(member, "privileges") and member.privileges:
                # We need to post messages and ideally manage messages for reactions
                return member.privileges.can_post_messages and member.privileges.can_edit_messages
            # Fallback for older Pyrogram versions or if privileges not directly available
            if hasattr(member, "can_post_messages") and hasattr(member, "can_edit_messages"):
                return member.can_post_messages and member.can_edit_messages
        return False
    except Exception as e:
        logger.error(f"Bot admin check failed for channel {channel_id}: {e}")
        return False

async def save_channel(user_id: int, channel_id: int, channel_title: str):
    user = await users.find_one({"user_id": user_id})
    if not user:
        await users.insert_one({"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []})
        user = {"user_id": user_id, "channels": [], "custom_caption": None, "custom_buttons": []}
    
    # Check if channel already exists in the list
    if any(ch["id"] == channel_id for ch in user["channels"]):
        return False
    
    # Check bot's admin rights before saving
    if not await ensure_bot_admin_rights(app, channel_id):
        raise ValueError("Bot does not have sufficient admin rights in the channel (post messages, edit messages).")

    user["channels"].append({"id": channel_id, "title": channel_title})
    await users.update_one({"user_id": user_id}, {"$set": {"channels": user["channels"]}})
    return True

# 🟢 /start
@app.on_message(filters.private & filters.command("start"))
async def start_handler(bot, msg: Message):
    subscribed = await is_subscribed(bot, msg.from_user.id, AUTH_CHANNEL)
    if not subscribed:
        try:
            chat = await bot.get_chat(AUTH_CHANNEL)
            invite_link = chat.invite_link
            if not invite_link:
                # Bot needs to be admin with 'can_invite_users' privilege to export link
                if await ensure_bot_admin_rights(bot, AUTH_CHANNEL): # This is a partial check, ideally needs can_invite_users
                    invite_link = await bot.export_chat_invite_link(AUTH_CHANNEL)
                else:
                    invite_link = "https://t.me/PrimeXBots" # Fallback to a general link if invite link cannot be obtained
                    await msg.reply_text("⚠️ Bot needs 'Invite Users' privilege in Auth Channel to generate invite link automatically. Using a fallback link.")
        except Exception as e:
            logger.error(f"Could not get invite link for AUTH_CHANNEL {AUTH_CHANNEL}: {e}")
            invite_link = "https://t.me/PrimeXBots" # Fallback if chat itself cannot be accessed
        
        btns = [[InlineKeyboardButton(f"✇ Join {chat.title if chat else 'Channel'} ✇", url=invite_link)],
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_check")]]
        await msg.reply_photo(
            photo="https://i.postimg.cc/xdkd1h4m/IMG-20250715-153124-952.jpg",
            caption=f"👋 Hello {msg.from_user.mention},\n\nJoin our channel to use the bot.",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        return
    
    # If subscribed, show main menu
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
        caption=(
            f"👋 Hello {msg.from_user.mention},\n\n"
            "✨ Welcome to **Post Generator Prime Bot** 🤖\n\n"
            "With me, you can:\n"
            "➕ Add & manage your channels\n"
            "✍️ Set custom captions\n"
            "🔘 Create your own buttons\n"
            "📤 Post photos & videos directly\n"
            "👍 Get reactions (Like ❤️ Love) on your posts\n\n"
            "━━━━━━━━━━━━━━━\n"
            "⚡ Use the buttons below to navigate and get started!"
        ),
        reply_markup=InlineKeyboardMarkup(buttons)
        )
    

# 🟢 /help command
@app.on_message(filters.private & filters.command("help"))
async def help_command_handler(bot, msg: Message):
    help_text = (
        "📚 **Help Menu**\n\n"
        "➕ `/addchannel Channelid` → Add a channel\n"
        "📌 Forward a post → Save channel automatically\n"
        "📂 `/mychannels` → See saved channels\n"
        "🗑 `/delchannel` → Delete channel\n\n"
        "✍️ `/setcap Your caption` → Set custom caption\n"
        "👀 `/seecap` → View caption\n"
        "❌ `/delcap` → Delete caption\n\n"
        "🔘 `/addbutton text | url` → Add custom button (Note: Use `|` as separator)\n"
        "📂 `/mybuttons` → View custom buttons\n"
        "🗑 `/delbutton` → Delete a button\n"
        "♻️ `/clearbuttons` → Clear all buttons\n\n"
        "📤 Send photo/video → Select channel to post\n"
        "👍 React to posts with Like ❤️ Love"
    )
    await msg.reply_text(help_text)

# 🟢 /about callback button
@app.on_callback_query(filters.regex("about_btn"))
async def about_callback(bot, cq: CallbackQuery):
    about_text = (
        "<b>✦✗✦ <a href='https://t.me/PrimeXBots'>ᴍy ᴅᴇᴛᴀɪʟꜱ ʙy ᴘʀɪᴍᴇXʙᴏᴛs</a> ✦✗✦</b>\n\n"
        "‣ ᴍʏ ɴᴀᴍᴇ : @Post_Generator_PrimeXBot\n"
        "‣ ᴍʏ ʙᴇsᴛ ғʀɪᴇɴᴅ : <a href='tg://settings'>ᴛʜɪs ᴘᴇʀsᴏɴ</a>\n"
        "‣ ᴅᴇᴠᴇʟᴏᴘᴇʀ : <a href='https://t.me/Prime_Nayem'>ᴍʀ.ᴘʀɪᴍᴇ</a>\n"
        "‣ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ : <a href='https://t.me/PrimeXBots'>ᴘʀɪᴍᴇXʙᴏᴛꜱ</a>\n"
        "‣ ᴍᴀɪɴ ᴄʜᴀɴɴᴇʟ : <a href='https://t.me/PrimeCineZone'>Pʀɪᴍᴇ Cɪɴᴇᴢᴏɴᴇ</a>\n"
        "‣ ѕᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ : <a href='https://t.me/Prime_Support_group'>ᴘʀɪᴍᴇ X ѕᴜᴘᴘᴏʀᴛ</a>\n"
        "‣ ᴅᴀᴛᴀ ʙᴀsᴇ : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏ ᴅʙ</a>\n"
        "‣ ʙᴏᴛ sᴇʀᴠᴇʀ : <a href='https://heroku.com'>ʜᴇʀᴏᴋᴜ</a>\n"
        "‣ ʙᴜɪʟᴅ sᴛᴀᴛᴜs : ᴠ2.7.1 [sᴛᴀʙʟᴇ]"
    )
    
    await cq.message.edit_text(
        about_text,
        disable_web_page_preview=True,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⌫ Back", callback_data="start_menu")]])
    )
    await cq.answer()

# 🟢 /help callback button
@app.on_callback_query(filters.regex("help_btn"))
async def help_callback(bot, cq: CallbackQuery):
    help_text = (
        "📚 **Help Menu**\n\n"
        "➕ `/addchannel channelid` → Add a channel\n"
        "📌 Forward a post → Save channel automatically\n"
        "📂 `/mychannels` → See saved channels\n"
        "🗑 `/delchannel` → Delete channel\n\n"
        "✍️ `/setcap your caption text` → Set custom caption\n"
        "👀 `/seecap` → View caption\n"
        "❌ `/delcap` → Delete caption\n\n"
        "🔘 `/addbutton Your button text | Your button url` → Add custom button (Note: Use `|` as separator)\n"
        "📂 `/mybuttons` → View custom buttons\n"
        "🗑 `/delbutton` → Delete a button\n"
        "♻️ `/clearbuttons` → Clear all buttons\n\n"
        "📤 Send photo/video → Select channel to post\n"
        "👍 React to posts with Like ❤️ Love"
    )
    await cq.message.edit_text(
        help_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⌫ Back", callback_data="start_menu")]]) # Added back button
    )
    await cq.answer()

# 🟢 Back to start menu
@app.on_callback_query(filters.regex("start_menu"))
async def back_to_start_menu(bot, cq: CallbackQuery):
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
    await cq.message.edit_caption(
        f"👋 Hi {cq.from_user.mention},\nI am **Post Generator Prime Bot** 🤖\n\nUse the buttons below to navigate.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    await cq.answer()

# 🟢 Channel & Button Commands
@app.on_message(filters.private & filters.command("addchannel"))
async def add_channel_cmd(bot, msg: Message):
    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Please give a channel ID.\nExample: `/addchannel -1001234567891`")
    
    try:
        channel_id = int(msg.command[1])
    except ValueError:
        return await msg.reply_text("⚠️ Invalid channel ID. Please provide a numeric ID.")

    try:
        chat = await bot.get_chat(channel_id)
        if chat.type != enums.ChatType.CHANNEL:
            return await msg.reply_text("⚠️ This ID does not belong to a channel.")
    except Exception as e:
        logger.error(f"Error getting chat for {channel_id}: {e}")
        return await msg.reply_text(f"❌ Could not find channel with ID {channel_id}. Make sure the bot is in the channel.")

    try:
        if not await ensure_bot_admin_rights(bot, channel_id):
            return await msg.reply_text("❌ Please give me **Admin Rights** in that channel first! I need 'Post Messages' and 'Edit Messages' privileges.")
    except Exception as e:
        logger.error(f"Failed to check bot's admin rights for {channel_id}: {e}")
        return await msg.reply_text(f"❌ An error occurred while checking bot's admin rights. Please try again or check logs.")

    try:
        saved = await save_channel(msg.from_user.id, channel_id, chat.title)
        if saved:
            await msg.reply_text(f"✅ Channel **{chat.title}** has been set successfully!")
        else:
            await msg.reply_text("⚠️ This channel is already in your list.")
    except ValueError as e:
        await msg.reply_text(f"❌ Failed to save channel: {e}")
    except Exception as e:
        logger.error(f"Error saving channel {channel_id} for user {msg.from_user.id}: {e}")
        await msg.reply_text("❌ An unexpected error occurred while saving the channel.")


@app.on_message(filters.private & filters.forwarded)
async def forward_handler(bot, msg: Message):
    if not msg.forward_from_chat:
        return await msg.reply_text("⚠️ This is not a valid channel post!")
    
    channel = msg.forward_from_chat

    if channel.type != enums.ChatType.CHANNEL:
        return await msg.reply_text("⚠️ Forwarded message is not from a channel.")

    try:
        # Check bot's admin rights here as well
        if not await ensure_bot_admin_rights(bot, channel.id):
            return await msg.reply_text(f"❌ Please give me **Admin Rights** in channel **{channel.title}** first! I need 'Post Messages' and 'Edit Messages' privileges.")
    except Exception as e:
        logger.error(f"Failed to check bot's admin rights for forwarded channel {channel.id}: {e}")
        return await msg.reply_text(f"❌ An error occurred while checking bot's admin rights for **{channel.title}**. Please try again.")

    try:
        saved = await save_channel(msg.from_user.id, channel.id, channel.title)
        if saved:
            await msg.reply_text(f"✅ Channel **{channel.title}** has been set successfully!")
        else:
            await msg.reply_text("⚠️ This channel is already in your list.")
    except ValueError as e:
        await msg.reply_text(f"❌ Failed to save channel: {e}")
    except Exception as e:
        logger.error(f"Error saving forwarded channel {channel.id} for user {msg.from_user.id}: {e}")
        await msg.reply_text("❌ An unexpected error occurred while saving the channel.")

@app.on_message(filters.private & filters.command("mychannels"))
async def my_channels(bot, msg: Message):
    user = await users.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("📂 You don’t have any channels saved yet.")
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"dummy_{ch['id']}")] for ch in user["channels"]] # Dummy callback for listing
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
    if len(msg.command) < 2:
        return await msg.reply_text(
            "⚠️ Usage: `/addbutton Your button text | Your button url`\n\n"
            "💡 Example: `/addbutton Prime Cine Zone | https://t.me/PrimeXBots`"
        )

    full_args = msg.text.split(" ", 1)[1]
    if "|" not in full_args:
        return await msg.reply_text(
            "⚠️ Invalid format. Please use `/addbutton Your button text | Your button url`"
        )
    
    parts = full_args.split("|", 1)
    text = parts[0].strip()
    url = parts[1].strip()

    if not text or not url:
        return await msg.reply_text("⚠️ Both text and URL are required!")

    user = await users.find_one({"user_id": msg.from_user.id}) or {}
    buttons = user.get("custom_buttons", [])
    
    # Optional: limit number of buttons
    if len(buttons) >= 10: # Example limit
        return await msg.reply_text("⚠️ You can add a maximum of 10 custom buttons.")

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
    
    # এইটা আগের মতোই থাকবে (চ্যানেল অ্যাড করা নেই)
    if not user or not user.get("channels"):
        return await msg.reply_text("⚠️ You have no channels set. Use /addchannel first.")
    
    # Store the message ID of the media to be posted
    await users.update_one({"user_id": msg.from_user.id}, {"$set": {"last_media_id": msg.id}})
    
    buttons = []
    for ch in user["channels"]:
        if await ensure_bot_admin_rights(bot, ch['id']):
            buttons.append([InlineKeyboardButton(ch["title"], callback_data=f"sendto_{msg.id}_{ch['id']}")])
        else:
            logger.warning(f"Bot lacks admin rights for channel {ch['title']} ({ch['id']}). Not listing for post.")
    
    # এখানে আমরা আপনার চাওয়া সুন্দর নোটিস+ছবি দেব
    if not buttons:
        return await msg.reply_photo(
            "https://i.postimg.cc/q7M6tQhy/IMG-20250918-053921-379.jpg",
            caption=(
                "⚠️ **নোটিস / Notice** ⚠️\n\n"
                "বট আপনার চ্যানেলগুলিতে বর্তমানে অ্যাডমিন পারমিশন যাচাই করতে পারছে না।\n\n"
                "📝 **করনীয় (বাংলা):**\n"
                "• দয়া করে বটকে নতুন করে চ্যানেলে **অ্যাডমিন** হিসেবে যুক্ত করুন এবং *Post Messages* ও *Edit Messages* পারমিশন দিন।\n"
                "• অথবা আপনার চ্যানেল থেকে একটি **মেসেজ ফরওয়ার্ড করে এখানে পাঠান**, যাতে আমি আবার অ্যাডমিন পারমিশন যাচাই করতে পারি।\n"
                "• এরপর আপনার কনটেন্ট আবার পাঠালে আমি সেটি চ্যানেলে পাঠাতে সাহায্য করব। ধন্যবাদ ❤️\n\n"
                "📝 **Steps (English):**\n"
                "• Please re-add the bot as **Admin** in your channel with *Post Messages* and *Edit Messages* permissions.\n"
                "• Or simply **forward a message** from your channel here so I can re-check admin permissions.\n"
                "• After that, send your content again — I’ll help you post it to your channel. Thank you! ❤️"
            )
        )
    
    await msg.reply_text(
        "📤 **Select a channel to post:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    
# 🟢 /stats
@app.on_message(filters.private & filters.command("stats"))
async def stats_handler(bot, msg: Message):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply_text("❌ You are not authorized to use this command!")

    total_users = await users.count_documents({})
    total_channels = 0
    async for user in users.find({}):
        total_channels += len(user.get("channels", []))

    await msg.reply_text(
        f"📊 Bot Stats:\n\n"
        f"👤 Total Users: {total_users}\n"
        f"📂 Total Channels Saved: {total_channels}"
    )

# 🟢 /broadcast
@app.on_message(filters.private & filters.command("broadcast"))
async def broadcast_handler(bot, msg: Message):
    if msg.from_user.id != OWNER_ID:
        return await msg.reply_text("❌ You are not authorized to use this command!")

    if len(msg.command) < 2:
        return await msg.reply_text("⚠️ Usage: `/broadcast Your message here`")

    broadcast_text = msg.text.split(" ", 1)[1]

    sent_count = 0
    failed_count = 0

    user_ids = await users.distinct("user_id") # Get all unique user IDs
    
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, broadcast_text)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {user_id}: {e}")
            failed_count += 1

    await msg.reply_text(
        f"✅ Broadcast completed!\n\n"
        f"📤 Sent: {sent_count}\n"
        f"❌ Failed: {failed_count}"
    )

# 🟢 Subscription refresh
@app.on_callback_query(filters.regex("refresh_check"))
async def refresh_callback(bot, cq: CallbackQuery):
    subscribed = await is_subscribed(bot, cq.from_user.id, AUTH_CHANNEL)
    if subscribed:
        await cq.message.delete()
        # Optionally, send the start message again
        await start_handler(bot, cq.message) # Re-trigger start handler
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
        if user:
            new_channels = [ch for ch in user["channels"] if ch["id"] != ch_id]
            await users.update_one({"user_id": cq.from_user.id}, {"$set": {"channels": new_channels}})
            await cq.answer("🗑 Channel deleted!", show_alert=True)
        return

    # Button Delete
    if data.startswith("delbtn_"):
        text = data.split("_", 1)[1]
        user = await users.find_one({"user_id": cq.from_user.id})
        if user:
            new_buttons = [b for b in user["custom_buttons"] if b["text"] != text]
            await users.update_one({"user_id": cq.from_user.id}, {"$set": {"custom_buttons": new_buttons}})
            await cq.answer(f"🗑 Button '{text}' deleted!", show_alert=True)
        return

    # Media Post
    if data.startswith("sendto_"):
        _, msg_id, channel_id = data.split("_")
        msg_id = int(msg_id)
        channel_id = int(channel_id)

        user = await users.find_one({"user_id": cq.from_user.id})
        if not user or not user.get("last_media_id"):
            return await cq.answer("⚠️ Media not found!", show_alert=True)

        # Check bot rights
        if not await ensure_bot_admin_rights(bot, channel_id):
            return await cq.answer("❌ Bot is not admin or missing 'Post Messages' rights!", show_alert=True)

        try:
            media_msg = await bot.get_messages(cq.from_user.id, msg_id)
            user_caption = user.get("custom_caption") or ""
            fixed_caption = "ʙʏ:<a href='https://t.me/PrimeXBots'>@ᴘʀɪᴍᴇXʙᴏᴛꜱ</a>"

            final_caption = ""
            if media_msg.caption:
                final_caption += media_msg.caption + "\n\n"
            if user_caption:
                final_caption += user_caption + "\n\n"
            final_caption #+= fixed_caption

            # Custom buttons
            custom_btns = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user.get("custom_buttons", [])]

            # Initial reaction row
            reaction_row = [
                InlineKeyboardButton("👍", callback_data=f"react_{msg_id}_like"),
                InlineKeyboardButton("❤️", callback_data=f"react_{msg_id}_love")
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
            logger.error(f"Failed to post media: {e}")
            await cq.answer("❌ Failed to post!", show_alert=True)
        return

    # Reactions
    # Reactions
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
        await reactions_collection.update_one(
            {"message_id": msg_id}, {"$set": {"reactions": post["reactions"]}}
        )

        like_count = len(post["reactions"].get("like", []))
        love_count = len(post["reactions"].get("love", []))

        # Preserve custom + fixed buttons without duplicating reaction row
        current_buttons = cq.message.reply_markup.inline_keyboard if cq.message.reply_markup else []

        # remove first row if it was reaction row
        if current_buttons and all(
            btn.callback_data and btn.callback_data.startswith("react_")
            for btn in current_buttons[0]
        ):
            current_buttons = current_buttons[1:]

        custom_buttons = current_buttons[:-1] if len(current_buttons) > 1 else []
        fixed_row = current_buttons[-1] if current_buttons else []

        # New reaction row
        reaction_row = [
            InlineKeyboardButton(f"👍 {like_count}", callback_data=f"react_{msg_id}_like"),
            InlineKeyboardButton(f"❤️ {love_count}", callback_data=f"react_{msg_id}_love")
        ]

        new_keyboard = [reaction_row] + custom_buttons + ([fixed_row] if fixed_row else [])
        await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
        await cq.answer("✅ Your reaction updated!", show_alert=False)
        return


# 🟢 Run
app.run()
