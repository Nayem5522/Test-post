# -*- coding: utf-8 -*-

# 🔹 Core/Standard Library Imports
import os
import io
import re
import logging
import threading

# 🔹 Third-party Library Imports
import requests
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask

# 🔹 Logging Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 🔹 Configuration from Environment Variables
try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    MONGO_URL = os.environ.get("MONGO_URL")
    AUTH_CHANNEL = int(os.environ.get("AUTH_CHANNEL"))
    OWNER_ID = int(os.environ.get("OWNER_ID"))
    TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
except (ValueError, TypeError) as e:
    logger.critical(f"A critical environment variable is missing or invalid: {e}")
    exit()

# 🔹 Database and Bot Initialization
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["PostGeneratorBotDB"]
users_db = db["users"]
reactions_db = db["reactions"]
app = Client("PostGeneratorPrimeBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# 🔹 In-memory Session Storage
user_sessions = {}

# 🔹 Flask App for Health Check
flask_app = Flask(__name__)
@flask_app.route("/")
def index(): return "Bot is active and running!", 200
def run_flask(): flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_flask, daemon=True).start()


# =====================================================================================
# SECTION 1: CORE HELPER FUNCTIONS
# =====================================================================================

async def is_subscribed(bot, user_id):
    try: await bot.get_chat_member(AUTH_CHANNEL, user_id); return True
    except Exception: return False

async def ensure_bot_admin_rights(bot: Client, channel_id: int):
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel_id, me.id)
        if member.status == enums.ChatMemberStatus.ADMINISTRATOR:
            return member.privileges.can_post_messages and member.privileges.can_edit_messages
        return member.status == enums.ChatMemberStatus.OWNER
    except Exception: return False

async def get_user_settings(user_id: int):
    return await users_db.find_one({"user_id": user_id}) or {}

def download_cascade():
    cascade_file = "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_file):
        logger.info(f"Downloading {cascade_file}...")
        url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
        try:
            r = requests.get(url, timeout=20, allow_redirects=True); r.raise_for_status()
            with open(cascade_file, 'wb') as f: f.write(r.content)
            logger.info("Cascade file downloaded.")
        except Exception as e:
            logger.error(f"Could not download cascade file. Face detection disabled. Error: {e}"); return None
    return cascade_file

CASCADE_PATH = download_cascade()


# =====================================================================================
# SECTION 2: TMDB & POSTER GENERATION
# =====================================================================================

def fetch_tmdb_results(query: str):
    year, name = None, query.strip()
    if match := re.search(r'(.+?)\s*\(?(\d{4})\)?$', query): name, year = match.group(1).strip(), match.group(2)
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={name}" + (f"&year={year}" if year else "")
    try:
        r = requests.get(url, timeout=10); r.raise_for_status()
        return [res for res in r.json().get("results", []) if res.get("media_type") in ["movie", "tv"]][:5]
    except Exception as e: logger.error(f"TMDB Search Error: {e}"); return []

def get_media_details(media_type: str, media_id: int):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url, timeout=10); r.raise_for_status(); return r.json()
    except Exception as e: logger.error(f"TMDB Details Fetch Error: {e}"); return None

def create_poster(poster_url: str, watermark_text: str, badge_text: str = None):
    if not poster_url: return None, "Poster URL not found."
    try:
        img_data = requests.get(f"https://image.tmdb.org/t/p/w500{poster_url}", timeout=20).content
        original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        img = Image.new("RGBA", original_img.size); img.paste(original_img)
        draw = ImageDraw.Draw(img)

        if badge_text:
            font_size = int(img.width / 9)
            try: font = ImageFont.truetype("arialbd.ttf", font_size)
            except IOError: font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), badge_text, font=font)
            text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x, y_pos = (img.width - text_width) / 2, img.height * 0.03
            if CASCADE_PATH:
                try:
                    gray = cv2.cvtColor(np.array(original_img.convert('RGB')), cv2.COLOR_RGB2GRAY)
                    faces = cv2.CascadeClassifier(CASCADE_PATH).detectMultiScale(gray, 1.1, 5)
                    if any(y_pos < (fy + fh) for (fx, fy, fw, fh) in faces): y_pos = img.height * 0.25
                except Exception as e: logger.error(f"Face detection failed: {e}")
            padding = int(font_size * 0.1)
            rect_layer = Image.new('RGBA', img.size, (0,0,0,0))
            ImageDraw.Draw(rect_layer).rectangle(((x - padding, y_pos - padding), (x + text_width + padding, y_pos + text_height + padding)), fill=(0, 0, 0, 140))
            img = Image.alpha_composite(img, rect_layer)
            ImageDraw.Draw(img).text((x, y_pos), badge_text, font=font, fill=(255, 255, 0, 255))
            
        if watermark_text:
            font_size = int(img.width / 12)
            try: font = ImageFont.truetype("arial.ttf", font_size)
            except IOError: font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), watermark_text, font=font)
            text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            wx, wy = (img.width - text_width) / 2, img.height - text_height - (img.height * 0.05)
            draw.text((wx + 2, wy + 2), watermark_text, font=font, fill=(0, 0, 0, 128))
            draw.text((wx, wy), watermark_text, font=font, fill=(255, 255, 255, 230))

        buffer = io.BytesIO(); buffer.name = "poster.png"; img.convert("RGB").save(buffer, "PNG"); buffer.seek(0)
        return buffer, None
    except Exception as e: logger.error(f"Image processing error: {e}"); return None, f"Image processing error: {e}"

async def shorten_link(user_id: int, long_url: str):
    settings = await get_user_settings(user_id)
    api_key, base_url = settings.get('shortener_api'), settings.get('shortener_url')
    if not api_key or not base_url: return long_url
    api_url = f"https://{base_url}/api?api={api_key}&url={long_url}"
    try:
        r = requests.get(api_url, timeout=10); r.raise_for_status(); data = r.json()
        return data.get("shortenedUrl") if data.get("status") == "success" else long_url
    except Exception as e: logger.error(f"Link shortener API error for user {user_id}: {e}"); return long_url

def format_tmdb_caption(data, language, links, tutorial_link):
    info = {"title": data.get("title") or data.get("name"), "year": (data.get("release_date") or data.get("first_air_date") or "----")[:4], "genres": ", ".join([g["name"] for g in data.get("genres", [])[:3]]),"rating": f"{data.get('vote_average', 0):.1f}"}
    caption_parts = [f"🎬 **{info['title']} ({info['year']})**", "━━━━━━━━━━━━━━━━━━━━━━━", f"⭐ **Rating:** {info['rating']}/10", f"🎭 **Genre:** {info['genres']}", f"🔊 **Language:** {language}", "━━━━━━━━━━━━━━━━━━━━━━━", "", "📥 **Download Links** 👇", ""]
    if 'release_date' in data:
        for quality in ["480p", "720p", "1080p"]:
            if link := links.get(quality): caption_parts.append(f"✅ **[{quality} Link]({link})**")
    else:
        for season in sorted(links.keys(), key=int): caption_parts.append(f"✅ **[Season {season} Link]({links[season]})**")
    if tutorial_link: caption_parts.extend(["", f"❓ **How to Download:** [Watch Tutorial]({tutorial_link})"])
    return "\n".join(caption_parts)

# =====================================================================================
# SECTION 3: COMMAND HANDLERS
# =====================================================================================

@app.on_message(filters.private & filters.command("start"))
async def start_handler(bot, msg: Message):
    if not await is_subscribed(bot, msg.from_user.id):
        try:
            chat = await bot.get_chat(AUTH_CHANNEL)
            invite_link = chat.invite_link or await bot.export_chat_invite_link(AUTH_CHANNEL)
            btns = [[InlineKeyboardButton(f"Join {chat.title}", url=invite_link)], [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_check")]]
            await msg.reply_photo("https://i.postimg.cc/xdkd1h4m/IMG-20250715-153124-952.jpg", caption=f"Hello {msg.from_user.mention},\nPlease join our channel to use this bot.", reply_markup=InlineKeyboardMarkup(btns))
        except Exception as e: logger.error(f"Could not get invite link for {AUTH_CHANNEL}: {e}"); await msg.reply_text("Could not get channel link. Contact owner.")
        return
    buttons = [[InlineKeyboardButton("📝 Generate Post", callback_data="generate_post_help"), InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")], [InlineKeyboardButton("📚 Help", callback_data="help_menu"), InlineKeyboardButton("ℹ️ About", callback_data="about_menu")], [InlineKeyboardButton("👑 Creator", url="https://t.me/Prime_Nayem")]]
    await msg.reply_photo("https://i.postimg.cc/fyrXmg6S/file-000000004e7461faaef2bd964cbbd408.png", caption=(f"👋 Hello {msg.from_user.mention},\n\nWelcome to **Post Generator Prime Bot** 🤖."), reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("help"))
async def help_command_handler(bot, msg: Message):
    await msg.reply_text(
        ("📚 **Help Guide**\n\n"
         "**Post Generation**\n"
         "• `/post <name>`: Generate a post using TMDB.\n"
         "• `Send Photo/Video`: Send media directly to post.\n"
         "• `/badge <text>`: Set a temporary badge for the next `/post`.\n\n"
         "**Channel Management**\n"
         "• `/addchannel <ID>`: Add a channel.\n"
         "• `Forward Message`: Forward from a channel to add it.\n"
         "• `/mychannels`: View your saved channels.\n"
         "• `/delchannel`: Remove a channel.\n\n"
         "**Content Customization**\n"
         "• `/setcap`: Set a custom caption for media posts.\n"
         "• `/seecap`, `/delcap`: View or delete caption.\n"
         "• `/addbutton`: Add a custom button.\n"
         "• `/mybuttons`, `/delbutton`, `/clearbuttons`: Manage buttons.\n"
         "• `/settings`: Access all settings via a menu.")
    )

@app.on_message(filters.private & filters.command("settings"))
async def settings_command_handler(bot, msg: Message):
    await msg.reply_text("Click the button to open the settings menu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Open Settings", callback_data="settings_menu")]]))

# --- Channel Management ---
@app.on_message(filters.private & filters.command("addchannel"))
async def add_channel_cmd(bot, msg: Message):
    if len(msg.command) < 2: return await msg.reply_text("Usage: `/addchannel -100...`")
    try: channel_id = int(msg.command[1])
    except ValueError: return await msg.reply_text("Invalid Channel ID.")
    try: chat = await bot.get_chat(channel_id)
    except Exception: return await msg.reply_text(f"Could not find channel. Ensure I am an admin there.")
    try:
        if await users_db.find_one({"user_id": msg.from_user.id, "channels.id": channel_id}): return await msg.reply_text("This channel is already in your list.")
        if not await ensure_bot_admin_rights(bot, channel_id): return await msg.reply_text("I need 'Post Messages' and 'Edit Messages' admin rights in that channel.")
        await users_db.update_one({"user_id": msg.from_user.id}, {"$push": {"channels": {"id": channel_id, "title": chat.title}}}, upsert=True)
        await msg.reply_text(f"✅ Channel **{chat.title}** added successfully!")
    except Exception as e: logger.error(f"Error adding channel {channel_id} for user {msg.from_user.id}: {e}"); await msg.reply_text("An unexpected error occurred.")

@app.on_message(filters.private & filters.command("mychannels"))
async def my_channels_cmd(bot, msg: Message):
    settings = await get_user_settings(msg.from_user.id)
    if not (channels := settings.get("channels")): return await msg.reply_text("You have no channels saved.")
    text = "📂 **Your Saved Channels:**\n\n" + "\n".join([f"• `{ch['title']}` (`{ch['id']}`)" for ch in channels])
    await msg.reply_text(text)

@app.on_message(filters.private & filters.command("delchannel"))
async def del_channel_cmd(bot, msg: Message):
    settings = await get_user_settings(msg.from_user.id)
    if not (channels := settings.get("channels")): return await msg.reply_text("You have no channels to delete.")
    buttons = [[InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")] for ch in channels]
    await msg.reply_text("🗑 Select a channel to delete:", reply_markup=InlineKeyboardMarkup(buttons))

# --- Custom Caption & Buttons ---
@app.on_message(filters.private & filters.command("setcap"))
async def set_cap_cmd(bot, msg: Message):
    if len(msg.command) < 2: return await msg.reply_text("Usage: `/setcap Your caption text`")
    caption = msg.text.split(" ", 1)[1]
    await users_db.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_caption": caption}}, upsert=True); await msg.reply_text("✅ Custom caption set!")

@app.on_message(filters.private & filters.command("seecap"))
async def see_cap_cmd(bot, msg: Message):
    settings = await get_user_settings(msg.from_user.id)
    if not (caption := settings.get("custom_caption")): return await msg.reply_text("You have no custom caption set.")
    await msg.reply_text(f"📝 **Your Caption:**\n\n{caption}")

@app.on_message(filters.private & filters.command("delcap"))
async def del_cap_cmd(bot, msg: Message):
    await users_db.update_one({"user_id": msg.from_user.id}, {"$unset": {"custom_caption": ""}}); await msg.reply_text("🗑 Custom caption deleted!")

@app.on_message(filters.private & filters.command("addbutton"))
async def add_button_cmd(bot, msg: Message):
    if "|" not in msg.text: return await msg.reply_text("Usage: `/addbutton Button Text | buttonurl.com`")
    try: text, url = [x.strip() for x in msg.text.split(" ", 1)[1].split("|", 1)]
    except ValueError: return await msg.reply_text("Invalid format. Use `text | url`.")
    await users_db.update_one({"user_id": msg.from_user.id}, {"$push": {"custom_buttons": {"text": text, "url": url}}}, upsert=True)
    await msg.reply_text(f"✅ Button **{text}** added!")

@app.on_message(filters.private & filters.command("mybuttons"))
async def my_buttons_cmd(bot, msg: Message):
    settings = await get_user_settings(msg.from_user.id)
    if not (buttons := settings.get("custom_buttons")): return await msg.reply_text("You have no custom buttons.")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(b['text'], url=b['url'])] for b in buttons])
    await msg.reply_text("📂 Your custom buttons:", reply_markup=markup)

@app.on_message(filters.private & filters.command("delbutton"))
async def del_button_cmd(bot, msg: Message):
    settings = await get_user_settings(msg.from_user.id)
    if not (buttons := settings.get("custom_buttons")): return await msg.reply_text("You have no buttons to delete.")
    del_buttons = [[InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"delbtn_{i}")] for i, b in enumerate(buttons)]
    await msg.reply_text("🗑 Select a button to delete:", reply_markup=InlineKeyboardMarkup(del_buttons))

@app.on_message(filters.private & filters.command("clearbuttons"))
async def clear_buttons_cmd(bot, msg: Message):
    await users_db.update_one({"user_id": msg.from_user.id}, {"$set": {"custom_buttons": []}}); await msg.reply_text("🗑 All custom buttons cleared!")

# --- TMDB Post Flow ---
@app.on_message(filters.private & filters.command("post"))
async def post_command_handler(bot, msg: Message):
    if not TMDB_API_KEY: return await msg.reply_text("This feature is currently disabled.")
    if len(msg.command) == 1: return await msg.reply_text("Usage: `/post Movie Name (Year)`")
    query = " ".join(msg.command[1:]); pr_msg = await msg.reply_text(f"🔍 Searching for `{query}`...")
    results = fetch_tmdb_results(query)
    if not results: return await pr_msg.edit_text("❌ No results found.")
    buttons = [[InlineKeyboardButton(f"{'🎬' if r['media_type'] == 'movie' else '📺'} {r.get('title') or r.get('name')} ({(r.get('release_date') or r.get('first_air_date') or '----')[:4]})", callback_data=f"select_{r['media_type']}_{r['id']}")] for r in results]
    await pr_msg.edit_text("**👇 Choose a result:**", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command("badge"))
async def badge_command_handler(bot, msg: Message):
    if len(msg.command) == 1:
        if user_sessions.get(msg.from_user.id, {}).get('badge_text'):
            del user_sessions[msg.from_user.id]['badge_text']
            return await msg.reply_text("✅ Temporary badge removed for the next post.")
        return await msg.reply_text("Usage: `/badge Your Badge Text`. This applies only to the next `/post`.")
    badge_text = msg.text.split(" ", 1)[1]
    user_sessions.setdefault(msg.from_user.id, {})['badge_text'] = badge_text
    await msg.reply_text(f"✅ Badge text **'{badge_text}'** will be applied to your next generated post.")

# --- Owner Commands ---
@app.on_message(filters.private & filters.command("stats") & filters.user(OWNER_ID))
async def stats_handler(bot, msg: Message):
    total_users = await users_db.count_documents({})
    pipeline = [{"$project": {"channel_count": {"$ifNull": [{"$size": "$channels"}, 0]}}}, {"$group": {"_id": None, "total": {"$sum": "$channel_count"}}}]
    total_channels_cursor = await users_db.aggregate(pipeline).to_list(1)
    total_channels = total_channels_cursor[0]['total'] if total_channels_cursor else 0
    await msg.reply_text(f"📊 **Bot Stats:**\n\n👤 Total Users: {total_users}\n📂 Total Channels: {total_channels}")

@app.on_message(filters.private & filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_handler(bot, msg: Message):
    if not msg.reply_to_message: return await msg.reply_text("Reply to a message to broadcast.")
    pr_msg = await msg.reply_text("Broadcasting..."); sent, failed = 0, 0
    user_ids = await users_db.distinct("user_id")
    for user_id in user_ids:
        try: await msg.reply_to_message.copy(user_id); sent += 1
        except Exception: failed += 1
    await pr_msg.edit_text(f"✅ **Broadcast Complete!**\n\n📤 Sent: {sent}\n❌ Failed: {failed}")

# =====================================================================================
# SECTION 4: MESSAGE HANDLERS (Non-command)
# =====================================================================================

@app.on_message(filters.private & filters.forwarded)
async def forward_handler(bot, msg: Message):
    if not msg.forward_from_chat or msg.forward_from_chat.type != enums.ChatType.CHANNEL: return
    channel = msg.forward_from_chat
    try:
        if await users_db.find_one({"user_id": msg.from_user.id, "channels.id": channel.id}): return await msg.reply_text("This channel is already in your list.")
        if not await ensure_bot_admin_rights(bot, channel.id): return await msg.reply_text(f"I need admin rights in **{channel.title}** to post messages.")
        await users_db.update_one({"user_id": msg.from_user.id}, {"$push": {"channels": {"id": channel.id, "title": channel.title}}}, upsert=True)
        await msg.reply_text(f"✅ Channel **{channel.title}** added successfully!")
    except Exception as e: logger.error(f"Error saving forwarded channel {channel.id} for {msg.from_user.id}: {e}"); await msg.reply_text("An error occurred.")

@app.on_message(filters.private & (filters.photo | filters.video))
async def legacy_media_handler(bot, msg: Message):
    settings = await get_user_settings(msg.from_user.id)
    if not (channels := settings.get("channels")): return await msg.reply_text("You have no channels set. Use `/addchannel` first.")
    await users_db.update_one({"user_id": msg.from_user.id}, {"$set": {"last_media_message_id": msg.id}})
    buttons = [[InlineKeyboardButton(ch["title"], callback_data=f"send_legacy_{ch['id']}")] for ch in channels if await ensure_bot_admin_rights(bot, ch['id'])]
    if not buttons: return await msg.reply_text("I can't verify my admin permissions in your channels. Please re-add me as admin.")
    await msg.reply_text("📤 **Select a channel to post this media:**", reply_markup=InlineKeyboardMarkup(buttons))

# This handler must be last to not interfere with commands
@app.on_message(filters.private & filters.text)
async def conversation_handler(bot, msg: Message):
    user_id = msg.from_user.id
    if not (session := user_sessions.get(user_id)) or "state" not in session: return
    state, text = session["state"], msg.text.strip()
    
    if text.lower() == "/cancel": del user_sessions[user_id]; return await msg.reply_text("Process cancelled.")

    async def process_link(quality, next_state, next_prompt):
        if text.lower() != 'skip': session["links"][quality] = await shorten_link(user_id, text)
        session["state"] = next_state; await msg.reply_text(f"✅ Link added. Now, {next_prompt}")

    if state.startswith("set_"):
        setting_key = state.split("_", 1)[1]
        if text.lower() == "remove": await users_db.update_one({"user_id": user_id}, {"$unset": {setting_key: ""}}); await msg.reply_text(f"✅ {setting_key.replace('_', ' ').title()} has been removed.")
        else: await users_db.update_one({"user_id": user_id}, {"$set": {setting_key: text}}, upsert=True); await msg.reply_text(f"✅ {setting_key.replace('_', ' ').title()} has been set.")
        del user_sessions[user_id]
    elif state == "wait_movie_lang": session["language"] = text; session["state"] = "wait_480p"; await msg.reply_text("✅ Language set. Send **480p** link or `skip`.")
    elif state == "wait_480p": await process_link("480p", "wait_720p", "send **720p** link or `skip`.")
    elif state == "wait_720p": await process_link("720p", "wait_1080p", "send **1080p** link or `skip`.")
    elif state == "wait_1080p":
        if text.lower() != 'skip': session["links"]["1080p"] = await shorten_link(user_id, text)
        await generate_final_post_preview(bot, user_id, await msg.reply_text("✅ Data collection complete! Generating preview..."))
    elif state == "wait_tv_lang": session["language"] = text; session["state"] = "wait_season_number"; await msg.reply_text("✅ Language set. Enter season number (e.g., 1).")
    elif state == "wait_season_number":
        if text.lower() == 'done':
            if not session.get('links'): return await msg.reply_text("You haven't added any season links.")
            await generate_final_post_preview(bot, user_id, await msg.reply_text("✅ All season data collected! Generating preview..."))
        elif text.isdigit() and int(text) > 0: session['current_season'] = text; session['state'] = 'wait_season_link'; await msg.reply_text(f"OK. Send download link for **Season {text}**.")
        else: await msg.reply_text("Invalid number. Enter a valid season number or `done`.")
    elif state == "wait_season_link":
        session.setdefault('links', {})[session.get('current_season')] = await shorten_link(user_id, text)
        session['state'] = 'wait_season_number'; await msg.reply_text(f"✅ Link added. Enter next season number, or `done`.")

async def generate_final_post_preview(bot, user_id, pr_msg: Message):
    if not (session := user_sessions.get(user_id)): return
    settings = await get_user_settings(user_id)
    badge_text = session.pop('badge_text', None)
    
    await pr_msg.edit_text("🖼️ Generating smart poster...")
    poster, error = create_poster(session["details"].get('poster_path'), settings.get('watermark_text'), badge_text)
    if error: return await pr_msg.edit_text(f"⚠️ Poster creation failed: `{error}`")
    
    caption = format_tmdb_caption(session["details"], session["language"], session["links"], settings.get("tutorial_link"))
    session['final_post'] = {'caption': caption, 'poster': poster, 'custom_buttons': settings.get("custom_buttons", [])}
    
    await pr_msg.delete()
    preview_msg = await bot.send_photo(user_id, io.BytesIO(poster.getvalue()), caption=caption)

    if channels := settings.get("channels"):
        buttons = [[InlineKeyboardButton(f"📢 Post to {ch['title']}", callback_data=f"postto_{ch['id']}")] for ch in channels]
        await bot.send_message(user_id, "**👆 Preview. Choose a channel to post:**", reply_to_message_id=preview_msg.id, reply_markup=InlineKeyboardMarkup(buttons))
    else: await bot.send_message(user_id, "✅ Preview generated. No channels saved.", reply_to_message_id=preview_msg.id)

# =====================================================================================
# SECTION 5: CALLBACK QUERY HANDLER
# =====================================================================================

@app.on_callback_query()
async def main_callback_router(bot, cq: CallbackQuery):
    user_id = cq.from_user.id; data = cq.data

    if data == "main_menu": await cq.answer(); await start_handler(bot, cq.message)
    elif data == "refresh_check":
        if await is_subscribed(bot, user_id): await cq.message.delete(); await start_handler(bot, cq.message)
        else: await cq.answer("❌ You are not subscribed yet.", show_alert=True)
    elif data.startswith("set_"):
        await cq.answer(); key = data.split("_", 1)[1]
        user_sessions[user_id] = {"state": data}
        await cq.message.edit_caption(f"Enter the new value for **{key.replace('_', ' ').title()}**.\nSend `remove` to delete it.")
    elif data.startswith("delch_"):
        ch_id = int(data.split("_")[1]); await users_db.update_one({"user_id": user_id}, {"$pull": {"channels": {"id": ch_id}}})
        await cq.answer("🗑 Channel deleted!", show_alert=True); await cq.message.delete()
    elif data.startswith("delbtn_"):
        btn_index = int(data.split("_")[1])
        await users_db.update_one({"user_id": user_id}, {"$unset": {f"custom_buttons.{btn_index}": 1}})
        await users_db.update_one({"user_id": user_id}, {"$pull": {"custom_buttons": None}})
        await cq.answer("🗑 Button deleted!", show_alert=True); await cq.message.delete()
    elif data.startswith("select_"):
        await cq.answer("Fetching details...", show_alert=False); _, media_type, media_id = data.split("_", 2)
        if not (details := get_media_details(media_type, int(media_id))): return await cq.message.edit_text("❌ Failed to fetch details.")
        user_sessions.setdefault(user_id, {})["details"] = details
        state_key = "wait_tv_lang" if media_type == "tv" else "wait_movie_lang"
        prompt = f"**{'Series' if media_type == 'tv' else 'Movie'} Post:** Enter the language."
        user_sessions[user_id]["state"] = state_key; await cq.message.edit_text(prompt)
    elif data.startswith("send_legacy_"):
        channel_id = int(data.split("_")[1]); settings = await get_user_settings(user_id)
        if not (last_media_id := settings.get("last_media_message_id")): return await cq.answer("⚠️ Media not found!", show_alert=True)
        try:
            await cq.answer("✅ Posting...", show_alert=False); media_msg = await bot.get_messages(user_id, last_media_id)
            final_caption = (media_msg.caption or "") + "\n\n" + (settings.get("custom_caption", ""))
            buttons = [[InlineKeyboardButton(b['text'], url=b['url'])] for b in settings.get("custom_buttons", [])]
            sent_msg = await media_msg.copy(channel_id, caption=final_caption.strip())
            reaction_row = [InlineKeyboardButton("👍 0", callback_data=f"react_{sent_msg.id}_{channel_id}_like"), InlineKeyboardButton("❤️ 0", callback_data=f"react_{sent_msg.id}_{channel_id}_love")]
            await sent_msg.edit_reply_markup(InlineKeyboardMarkup([reaction_row] + buttons))
            await reactions_db.insert_one({"message_id": sent_msg.id, "chat_id": channel_id, "reactions": {"like": [], "love": []}})
            await cq.message.edit_text("✅ Posted successfully!")
        except Exception as e: logger.error(f"Failed to post legacy media: {e}"); await cq.answer("❌ Failed to post!", show_alert=True)
    elif data.startswith("postto_"):
        channel_id = int(data.split("_")[1])
        if not (session := user_sessions.get(user_id)) or 'final_post' not in session: return await cq.answer("❌ Session expired!", show_alert=True)
        await cq.answer("⏳ Posting...", show_alert=False); post_data = session['final_post']
        try:
            chat = await bot.get_chat(channel_id)
            sent_msg = await bot.send_photo(channel_id, io.BytesIO(post_data['poster'].getvalue()), caption=post_data['caption'])
            reaction_row = [InlineKeyboardButton("👍 0", callback_data=f"react_{sent_msg.id}_{channel_id}_like"), InlineKeyboardButton("❤️ 0", callback_data=f"react_{sent_msg.id}_{channel_id}_love")]
            custom_buttons = [[InlineKeyboardButton(b['text'], url=b['url'])] for b in post_data.get("custom_buttons", [])]
            await sent_msg.edit_reply_markup(InlineKeyboardMarkup([reaction_row] + custom_buttons))
            await reactions_db.insert_one({"message_id": sent_msg.id, "chat_id": channel_id, "reactions": {"like": [], "love": []}})
            await cq.message.edit_text(f"✅ **Posted to '{chat.title}'!**")
        except Exception as e: logger.error(f"Failed to post to channel {channel_id}: {e}"); await cq.message.edit_text(f"❌ **Failed to post.**\nError: `{e}`")
        finally:
            if user_id in user_sessions: del user_sessions[user_id]
    elif data.startswith("react_"):
        _, msg_id, chat_id, r_type = data.split("_"); msg_id, chat_id = int(msg_id), int(chat_id)
        if not (post := await reactions_db.find_one({"message_id": msg_id, "chat_id": chat_id})): return
        reactions = post.get("reactions", {"like": [], "love": []})
        if user_id in reactions.get(r_type, []): reactions[r_type].remove(user_id); await cq.answer("Reaction removed.")
        else:
            for k in reactions: reactions[k].remove(user_id) if user_id in reactions[k] else None
            reactions.setdefault(r_type, []).append(user_id); await cq.answer("Reaction added!")
        await reactions_db.update_one({"message_id": msg_id, "chat_id": chat_id}, {"$set": {"reactions": reactions}})
        like_count, love_count = len(reactions.get("like", [])), len(reactions.get("love", []))
        current_markup = cq.message.reply_markup.inline_keyboard if cq.message.reply_markup else []
        other_buttons = [row for row in current_markup if not row[0].callback_data.startswith("react_")]
        new_markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"👍 {like_count}", callback_data=f"react_{msg_id}_{chat_id}_like"), InlineKeyboardButton(f"❤️ {love_count}", callback_data=f"react_{msg_id}_{chat_id}_love")]] + other_buttons)
        try: await cq.message.edit_reply_markup(new_markup)
        except Exception: pass

# =====================================================================================
# 🟢 RUN THE BOT
# =====================================================================================
if __name__ == "__main__":
    logger.info("🚀 Bot is starting...")
    app.run()
    logger.info("👋 Bot has stopped.")