# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# 🔹 Core & Third-Party Library Imports
# ---------------------------------------------------------------------------
import os
import io
import re
import logging
import threading
import requests
from typing import List, Dict

# --- Environment Variable Loading ---
from dotenv import load_dotenv

# --- Media & Image Processing ---
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import cv2

# --- Telegram & Database ---
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient

# --- Web Server (for Keep-Alive) ---
from flask import Flask

# ---------------------------------------------------------------------------
# 🔹 Configuration and Initial Setup
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Bot & API Configuration ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URL = os.environ.get("MONGO_URL", "your_mongodb_url")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "your_tmdb_api_key")

# --- Channel & Owner Information ---
AUTH_CHANNEL = int(os.environ.get("AUTH_CHANNEL", "-1001234567890"))
OWNER_ID = int(os.environ.get("OWNER_ID", "5926160191"))

# ---------------------------------------------------------------------------
# 🔹 Global Variables & Client Initialization
# ---------------------------------------------------------------------------
# --- Database Setup ---
mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["UltimatePostBotDB"]
users_collection = db["users"]
reactions_collection = db["reactions"]
logger.info("✅ MongoDB database successfully connected.")

# --- Pyrogram Client ---
app = Client("UltimatePostBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- For storing user conversations and data ---
user_conversations = {}

# ---------------------------------------------------------------------------
# 🔹 Flask Web Server (for Keep-Alive)
# ---------------------------------------------------------------------------
flask_app = Flask(__name__)
@flask_app.route("/")
def index():
    return "Bot is running perfectly!", 200

def run_flask():
    try:
        flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    except Exception as e:
        logger.error(f"Flask app crashed: {e}")

threading.Thread(target=run_flask, daemon=True).start()
logger.info("🚀 Flask web server started.")

# ---------------------------------------------------------------------------
# 🔹 Helper Functions
# ---------------------------------------------------------------------------

async def is_subscribed(bot: Client, user_id: int):
    try:
        member = await bot.get_chat_member(AUTH_CHANNEL, user_id)
        return member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception:
        return False

async def ensure_bot_admin_rights(bot: Client, channel_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel_id, me.id)
        if member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            privileges = member.privileges
            return privileges and privileges.can_post_messages and privileges.can_edit_messages
        return False
    except Exception as e:
        logger.error(f"Bot admin check failed for channel {channel_id}: {e}")
        return False

async def save_channel(user_id: int, channel_id: int, channel_title: str):
    if not await ensure_bot_admin_rights(app, channel_id):
        raise ValueError("বটকে প্রথমে চ্যানেলে অ্যাডমিন বানান এবং 'Post Messages' ও 'Edit Messages' পারমিশন দিন।")

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        await users_collection.insert_one({"user_id": user_id, "channels": []})
        user = {"channels": []}

    if any(ch["id"] == channel_id for ch in user.get("channels", [])):
        return False

    await users_collection.update_one(
        {"user_id": user_id},
        {"$push": {"channels": {"id": channel_id, "title": channel_title}}},
        upsert=True
    )
    return True

async def shorten_link(user_id: int, long_url: str):
    user_data = await users_collection.find_one({'user_id': user_id})
    if not user_data or 'shortener_api' not in user_data or 'shortener_url' not in user_data:
        return long_url

    api_key = user_data['shortener_api']
    base_url = user_data['shortener_url']
    api_url = f"https://{base_url}/api?api={api_key}&url={long_url}"
    
    try:
        response = requests.get(api_url, timeout=10)
        data = response.json()
        if data.get("status") == "success":
            return data.get("shortenedUrl", long_url)
    except Exception as e:
        logger.error(f"Shortener API Error for user {user_id}: {e}")
    return long_url

def format_runtime(minutes: int):
    if not isinstance(minutes, int) or minutes <= 0: return "N/A"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

# ---------------------------------------------------------------------------
# 🔹 TMDB API & Smart Poster Generation
# ---------------------------------------------------------------------------
def search_tmdb(query: str):
    year, name = None, query.strip()
    match = re.search(r'(.+?)\s*\(?(\d{4})\)?$', query)
    if match: name, year = match.group(1).strip(), match.group(2)
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={name}" + (f"&year={year}" if year else "")
    try:
        r = requests.get(url, timeout=10)
        results = r.json().get("results", [])
        return [res for res in results if res.get("media_type") in ["movie", "tv"]][:5]
    except Exception as e:
        logger.error(f"TMDB Search Error: {e}"); return []

def get_tmdb_details(media_type: str, media_id: int):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url, timeout=10); return r.json()
    except Exception as e:
        logger.error(f"TMDB Details Error: {e}"); return None

def download_file(url: str, filename: str):
    if not os.path.exists(filename):
        logger.info(f"Downloading {filename}...")
        try:
            r = requests.get(url, timeout=20)
            with open(filename, 'wb') as f: f.write(r.content)
            logger.info(f"Downloaded {filename} successfully.")
        except Exception as e:
            logger.error(f"Could not download {filename}. Error: {e}")
            return False
    return True

def watermark_poster(poster_url: str, watermark_text: str, badge_text: str = None):
    font_files = {
        "bold": ("Poppins-Bold.ttf", "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf"),
        "badge": ("HindSiliguri-Bold.ttf", "https://github.com/google/fonts/raw/main/ofl/hindsiliguri/HindSiliguri-Bold.ttf")
    }
    cascade_file = ("haarcascade_frontalface_default.xml", "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml")
    
    for key, (name, url) in font_files.items():
        if not download_file(url, name):
            logger.warning(f"{name} font not found or couldn't be downloaded.")
    
    cascade_path = cascade_file[0] if download_file(cascade_file[1], cascade_file[0]) else None

    if not poster_url: return None, "পোস্টারের URL পাওয়া যায়নি।"
    try:
        img_data = requests.get(poster_url, timeout=20).content
        original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        img = Image.new("RGBA", original_img.size)
        img.paste(original_img)
        draw = ImageDraw.Draw(img)

        if badge_text:
            badge_font_size = int(img.width / 9)
            badge_font = ImageFont.truetype(font_files["badge"][0], badge_font_size)
            bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x, y = (img.width - text_width) / 2, img.height * 0.03

            if cascade_path:
                try:
                    cv_image = np.array(original_img.convert('RGB'))
                    gray = cv2.cvtColor(cv_image, cv2.COLOR_RGB2GRAY)
                    face_cascade = cv2.CascadeClassifier(cascade_path)
                    faces = face_cascade.detectMultiScale(gray, 1.1, 5)
                    if any(y < (fy + fh) and (y + text_height) > fy for (fx, fy, fw, fh) in faces):
                        y = img.height * 0.25
                except Exception as e:
                    logger.error(f"Face detection failed: {e}")
            
            padding = int(badge_font_size * 0.1)
            rect_layer = Image.new('RGBA', img.size, (0,0,0,0))
            ImageDraw.Draw(rect_layer).rectangle((x - padding, y - padding, x + text_width + padding, y + text_height + padding), fill=(0, 0, 0, 140))
            img = Image.alpha_composite(img, rect_layer)
            draw = ImageDraw.Draw(img)
            
            mask = Image.new('L', (text_width, text_height), 0)
            ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), badge_text, font=badge_font, fill=255)
            img.paste((255, 255, 0), (int(x), int(y)), mask)

        if watermark_text:
            font_size = int(img.width / 12)
            font = ImageFont.truetype(font_files["bold"][0], font_size)
            bbox = draw.textbbox((0, 0), watermark_text, font=font)
            text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            wx, wy = (img.width - text_width) / 2, img.height - text_height - (img.height * 0.05)
            draw.text((wx + 2, wy + 2), watermark_text, font=font, fill=(0, 0, 0, 128))
            draw.text((wx, wy), watermark_text, font=font, fill=(255, 255, 255, 230))

        buffer = io.BytesIO()
        buffer.name = "poster.png"
        img.convert("RGB").save(buffer, "PNG")
        buffer.seek(0)
        return buffer, None
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        return None, f"ছবি তৈরিতে সমস্যা হয়েছে: {e}"

# ---------------------------------------------------------------------------
# 🔹 Start & General Command Handlers
# ---------------------------------------------------------------------------
@app.on_message(filters.private & filters.command("start"))
async def start_handler(bot, msg: Message):
    if not await is_subscribed(bot, msg.from_user.id):
        try:
            chat = await bot.get_chat(AUTH_CHANNEL)
            invite_link = chat.invite_link or await bot.export_chat_invite_link(AUTH_CHANNEL)
            btns = [[InlineKeyboardButton(f"✇ {chat.title} এ যোগ দিন ✇", url=invite_link)],
                    [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_check")]]
            return await msg.reply_photo(
                photo="https://i.postimg.cc/xdkd1h4m/IMG-20250715-153124-952.jpg",
                caption=f"👋 Hello {msg.from_user.mention},\n\nবটটি ব্যবহার করার জন্য আমাদের চ্যানেলে যোগ দিন।",
                reply_markup=InlineKeyboardMarkup(btns)
            )
        except Exception as e:
            logger.error(f"Could not get invite link for AUTH_CHANNEL {AUTH_CHANNEL}: {e}")
            return await msg.reply_text("দুঃখিত, চ্যানেলে যোগদানের লিঙ্ক তৈরি করতে সমস্যা হচ্ছে।")

    buttons = [
        [InlineKeyboardButton("🎬 পোস্ট তৈরি করুন (/post)", callback_data="create_post_help")],
        [InlineKeyboardButton("⚙️ সেটিংস", callback_data="settings_menu"), InlineKeyboardButton("📚 সাহায্য", callback_data="help_menu")],
        [InlineKeyboardButton("👨‍💻 ডেভেলপার", url="https://t.me/Prime_Nayem")]
    ]
    await msg.reply_photo(
        photo="https://i.postimg.cc/gjNQNCGK/IMG-20251104-062650-153.jpg",
        caption=(
            f"👋 স্বাগতম, {msg.from_user.mention}!\n\n"
            "আমি একটি অ্যাডভান্সড পোস্ট জেনারেটর বট। আমার মাধ্যমে আপনি মুভি ও সিরিজের জন্য আকর্ষণীয় পোস্ট তৈরি করতে পারবেন।\n\n"
            "**আমার বৈশিষ্ট্যসমূহ:**\n"
            "✅ TMDB থেকে স্বয়ংক্রিয়ভাবে তথ্য সংগ্রহ।\n"
            "🖼️ ওয়াটারমার্ক এবং ব্যাজসহ স্মার্ট পোস্টার তৈরি।\n"
            "🔗 লিংক শর্টনার ইন্টিগ্রেশন।\n"
            "👍 পোস্টে লাইক/লাভ রিঅ্যাকশন সিস্টেম।\n"
            "🔘 আপনার নিজস্ব কাস্টম বাটন যোগ করার সুবিধা।\n\n"
            "শুরু করতে নিচের বাটনগুলো ব্যবহার করুন!"
        ),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ---------------------------------------------------------------------------
# 🔹 Auto Add Channel on Forward (Re-integrated Feature)
# ---------------------------------------------------------------------------
@app.on_message(filters.private & filters.forwarded)
async def forward_handler(bot, msg: Message):
    if not msg.forward_from_chat:
        return
    
    channel = msg.forward_from_chat
    if channel.type != enums.ChatType.CHANNEL:
        return

    try:
        saved = await save_channel(msg.from_user.id, channel.id, channel.title)
        if saved:
            await msg.reply_text(f"✅ চ্যানেল **{channel.title}** সফলভাবে যোগ করা হয়েছে!")
        else:
            await msg.reply_text("⚠️ এই চ্যানেলটি আপনার তালিকায় আগে থেকেই আছে।")
    except ValueError as e:
        await msg.reply_text(f"❌ চ্যানেল যোগ করতে সমস্যা: {e}")
    except Exception as e:
        logger.error(f"Error saving forwarded channel {channel.id} for user {msg.from_user.id}: {e}")
        await msg.reply_text("❌ একটি অপ্রত্যাশিত ত্রুটি ঘটেছে।")
        
# ---------------------------------------------------------------------------
# 🔹 /post Command & Post Creation Flow (New Feature)
# ---------------------------------------------------------------------------
@app.on_message(filters.private & filters.command("post"))
async def post_command_handler(bot, msg: Message):
    if len(msg.command) == 1:
        return await msg.reply_text("💡 **ব্যবহার:** `/post Movie or Series Name (Year)`\n**উদাহরণ:** `/post Avatar (2009)`")
    
    query = " ".join(msg.command[1:])
    processing_msg = await msg.reply_text(f"🔍 `{query}` এর জন্য খোঁজা হচ্ছে...")
    results = search_tmdb(query)
    if not results:
        return await processing_msg.edit_text("❌ কোনো ফলাফল পাওয়া যায়নি। অনুগ্রহ করে নাম ও সাল ঠিকভাবে লিখুন।")
    
    buttons = []
    for r in results:
        media_icon = '🎬' if r['media_type'] == 'movie' else '📺'
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----').split('-')[0]
        buttons.append([InlineKeyboardButton(f"{media_icon} {title} ({year})", callback_data=f"select_post_{r['media_type']}_{r['id']}")])
    
    buttons.append([InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel_process")])
    await processing_msg.edit_text("**👇 ফলাফল থেকে বেছে নিন:**", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("^select_post_"))
async def select_post_callback(bot, cq: CallbackQuery):
    try:
        # [*] FIXED: Correctly parse callback data
        parts = cq.data.split("_")
        media_type = parts[2]
        mid = int(parts[3])
    except (ValueError, IndexError):
        return await cq.message.edit_text("❌ ভুল কলব্যাক ডেটা।")

    await cq.answer("⏳ তথ্য সংগ্রহ করা হচ্ছে...", show_alert=False)
    details = get_tmdb_details(media_type, mid)
    if not details:
        return await cq.message.edit_text("❌ TMDB থেকে তথ্য আনতে সমস্যা হয়েছে।")

    uid = cq.from_user.id
    user_conversations[uid] = {"details": details, "links": {}, "state": "wait_lang"}
    
    await cq.message.edit_text(f"✅ **{details.get('title') or details.get('name')}** বেছে নেওয়া হয়েছে।\n\n"
                               f"💬 এখন `{details.get('original_language')}` ছাড়া অন্য কোন ভাষায় পোস্ট করতে চাইলে সেই ভাষার নাম লিখুন (যেমন: Bengali, Hindi)।\n\n"
                               f"অথবা ডিফল্ট ভাষা রাখতে **skip** লিখুন।")

@app.on_message(filters.private & filters.text & filters.create(lambda _, __, m: m.from_user.id in user_conversations))
async def conversation_handler(bot, msg: Message):
    uid = msg.from_user.id
    text = msg.text.strip()
    convo = user_conversations.get(uid)
    if not convo or "state" not in convo: return

    state = convo["state"]
    media_type = "movie" if "release_date" in convo["details"] else "tv"

    async def process_link(quality: str, next_state: str, next_prompt: str):
        if text.lower() != 'skip':
            shortened = await shorten_link(uid, text)
            convo["links"][quality] = shortened
            await msg.reply_text(f"✅ {quality} লিংক যোগ করা হয়েছে।")
        else:
            await msg.reply_text(f"☑️ {quality} লিংক স্কিপ করা হয়েছে।")
        
        convo["state"] = next_state
        await msg.reply_text(next_prompt)

    if state == "wait_lang":
        convo["language"] = text if text.lower() != 'skip' else convo["details"].get('original_language', 'en').capitalize()
        if media_type == "movie":
            convo["state"] = "wait_480p"
            await msg.reply_text("✅ ভাষা সেট করা হয়েছে। এখন **480p** লিংক পাঠান অথবা `skip` লিখুন।")
        else:
            convo["state"] = "wait_season_number"
            await msg.reply_text("✅ ভাষা সেট করা হয়েছে। এখন সিজন নম্বর দিন (যেমন: 1, 2)।")

    elif state == "wait_480p":
        await process_link("480p", "wait_720p", "এখন **720p** লিংক পাঠান অথবা `skip` লিখুন।")
    elif state == "wait_720p":
        await process_link("720p", "wait_1080p", "এখন **1080p** লিংক পাঠান অথবা `skip` লিখুন।")
    elif state == "wait_1080p":
        if text.lower() != 'skip':
            convo["links"]["1080p"] = await shorten_link(uid, text)
        convo["state"] = "generating_post"
        status_msg = await msg.reply_text("✅ তথ্য সংগ্রহ সম্পন্ন। পোস্ট তৈরি করা হচ্ছে...")
        await generate_final_post_preview(bot, uid, msg.chat.id, status_msg)

    elif state == "wait_season_number":
        if text.lower() == 'done':
            if not convo.get('seasons'): return await msg.reply_text("⚠️ আপনি কোনো সিজনের লিংক যোগ করেননি।")
            convo['links'] = convo['seasons']
            convo["state"] = "generating_post"
            status_msg = await msg.reply_text("✅ সকল সিজনের তথ্য সংগ্রহ সম্পন্ন। পোস্ট তৈরি করা হচ্ছে...")
            await generate_final_post_preview(bot, uid, msg.chat.id, status_msg)
            return
        if not text.isdigit() or int(text) <= 0: return await msg.reply_text("❌ ভুল নম্বর। দয়া করে সঠিক সিজন নম্বর দিন।")
        convo['current_season'] = text; convo['state'] = 'wait_season_link'
        await msg.reply_text(f"👍 আচ্ছা। এখন **সিজন {text}** এর ডাউনলোড লিংক পাঠান।")
    
    elif state == "wait_season_link":
        season_num = convo.get('current_season')
        shortened = await shorten_link(uid, text)
        convo.setdefault('seasons', {})[season_num] = shortened
        convo['state'] = 'wait_season_number'
        await msg.reply_text(f"✅ সিজন {season_num} এর লিংক যোগ করা হয়েছে।\n\n**👉 পরবর্তী সিজন নম্বর দিন, অথবা শেষ করতে `done` লিখুন।**")

# ---------------------------------------------------------------------------
# 🔹 Final Post Preview & Posting
# ---------------------------------------------------------------------------
async def generate_final_post_preview(bot, uid, chat_id, status_msg: Message):
    convo = user_conversations.get(uid)
    if not convo: return await status_msg.edit_text("❌ সেশন শেষ হয়ে গেছে।")

    user_data = await users_collection.find_one({'user_id': uid}) or {}
    
    await status_msg.edit_text("🖼️ স্মার্ট পোস্টার তৈরি করা হচ্ছে...")
    poster_url = f"https://image.tmdb.org/t/p/w500{convo['details']['poster_path']}" if convo['details'].get('poster_path') else None
    badge_text = convo.pop('temp_badge_text', None)
    watermark = user_data.get('watermark_text')
    poster, error = watermark_poster(poster_url, watermark, badge_text=badge_text)
    
    if error: await bot.send_message(chat_id, f"⚠️ **পোস্টার তৈরিতে সমস্যা:** `{error}`")

    await status_msg.edit_text("📝 ক্যাপশন এবং বাটন তৈরি করা হচ্ছে...")
    caption = await generate_channel_caption(convo, user_data)
    
    inline_keyboard = []
    reaction_row = [
        InlineKeyboardButton("👍 0", callback_data="react_DUMMY_like"),
        InlineKeyboardButton("❤️ 0", callback_data="react_DUMMY_love")
    ]
    inline_keyboard.append(reaction_row)

    custom_buttons = user_data.get("custom_buttons", [])
    for btn in custom_buttons:
        inline_keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])

    if user_data.get('tutorial_link'):
        inline_keyboard.append([InlineKeyboardButton("🎥 কিভাবে ডাউনলোড করবেন", url=user_data['tutorial_link'])])

    reply_markup = InlineKeyboardMarkup(inline_keyboard)
    
    await status_msg.delete()
    
    preview_msg = await bot.send_photo(
        chat_id=chat_id,
        photo=poster if poster else "https://via.placeholder.com/500x750.png?text=No+Poster",
        caption=caption,
        reply_markup=reply_markup
    )
    
    convo['final_post'] = {
        'caption': caption,
        'poster': poster.getvalue() if poster else None,
        'buttons': inline_keyboard
    }
    
    saved_channels = user_data.get('channels', [])
    if saved_channels:
        channel_buttons = []
        for channel in saved_channels:
            if await ensure_bot_admin_rights(bot, channel['id']):
                channel_buttons.append([InlineKeyboardButton(f"📢 {channel['title']} এ পোস্ট করুন", callback_data=f"postto_{channel['id']}")])
        
        if channel_buttons:
            await preview_msg.reply_text(
                "**👆 এটি একটি প্রিভিউ। পোস্ট করতে আপনার চ্যানেল বেছে নিন:**",
                reply_markup=InlineKeyboardMarkup(channel_buttons)
            )
        else:
             await preview_msg.reply_text("⚠️ আপনার কোনো চ্যানেলে পোস্ট করার পারমিশন নেই। বটকে অ্যাডমিন বানান।")
    else:
        await preview_msg.reply_text("✅ প্রিভিউ তৈরি হয়েছে। আপনার কোনো চ্যানেল সেভ করা নেই। `/addchannel` ব্যবহার করে যোগ করুন।")

async def generate_channel_caption(convo: dict, user_data: dict):
    data = convo["details"]
    links = convo["links"]
    is_tv = "first_air_date" in data

    info = {
        "title": data.get("title") or data.get("name") or "N/A",
        "year": (data.get("release_date") or data.get("first_air_date") or "----")[:4],
        "genres": ", ".join([g["name"] for g in data.get("genres", [])[:3]]) or "N/A",
        "rating": f"{data.get('vote_average', 0):.1f}",
        "language": convo.get('language', 'N/A'),
        "runtime": format_runtime(data.get("runtime") if not is_tv else (data.get("episode_run_time") or [0])[0]),
    }

    caption_header = f"🎬 **{info['title']} ({info['year']})**\n━━━━━━━━━━━━━━━━━━━━━━━\n⭐ **Rating:** {info['rating']}/10\n🎭 **Genre:** {info['genres']}\n🔊 **Language:** {info['language']}\n⏰ **Runtime:** {info['runtime']}\n━━━━━━━━━━━━━━━━━━━━━━━"
    download_section_header = "📥 **ডাউনলোড লিংক** 📥"
    
    download_links = ""
    if is_tv:
        sorted_seasons = sorted(links.keys(), key=lambda x: int(re.search(r'\d+', str(x)).group()))
        season_links = [f"✅ **[Download Season {s}]({links[s]})**" for s in sorted_seasons]
        download_links = "\n".join(season_links)
    else:
        movie_links = []
        if links.get('480p'): movie_links.append(f"**[Download 480p]({links['480p']})**")
        if links.get('720p'): movie_links.append(f"**[Download 720p]({links['720p']})**")
        if links.get('1080p'): movie_links.append(f"**[Download 1080p]({links['1080p']})**")
        download_links = "\n".join(movie_links)

    custom_caption = user_data.get('custom_caption', '')
    
    final_caption_parts = [caption_header]
    if download_links:
        final_caption_parts.extend([download_section_header, download_links])
    if custom_caption:
        final_caption_parts.append(custom_caption)
        
    final_caption_parts.append("✨ **পোস্ট করেছেন:** @Post_Generator_PrimeXBot")

    return "\n\n".join(final_caption_parts)

@app.on_callback_query(filters.regex("^postto_"))
async def post_to_channel_callback(bot, cq: CallbackQuery):
    uid = cq.from_user.id
    channel_id = int(cq.data.split("_")[1])
    convo = user_conversations.get(uid)
    if not convo or 'final_post' not in convo:
        return await cq.answer("❌ সেশন শেষ হয়ে গেছে! আবার শুরু করুন।", show_alert=True)

    await cq.answer("⏳ চ্যানেলে পোস্ট করা হচ্ছে...", show_alert=False)
    
    final_post = convo['final_post']
    try:
        posted_msg = await bot.send_photo(
            chat_id=channel_id,
            photo=io.BytesIO(final_post['poster']) if final_post['poster'] else "https://via.placeholder.com/500x750.png?text=No+Poster",
            caption=final_post['caption']
        )

        await reactions_collection.insert_one({"message_id": posted_msg.id, "chat_id": channel_id, "reactions": {"like": [], "love": []}})
        
        final_buttons = final_post['buttons']
        reaction_row = final_buttons[0]
        reaction_row[0].callback_data = f"react_{posted_msg.id}_like"
        reaction_row[1].callback_data = f"react_{posted_msg.id}_love"
        
        await posted_msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(final_buttons))
        
        chat = await bot.get_chat(channel_id)
        await cq.message.edit_text(f"✅ **সফলভাবে '{chat.title}' চ্যানেলে পোস্ট করা হয়েছে!**")
    except Exception as e:
        logger.error(f"Failed to post to channel {channel_id} for user {uid}. Error: {e}")
        await cq.message.edit_text(f"❌ **'{channel_id}' চ্যানেলে পোস্ট করতে সমস্যা হয়েছে।**\n\n**ত্রুটি:** `{e}`")
    finally:
        if uid in user_conversations:
            del user_conversations[uid]

# ---------------------------------------------------------------------------
# 🔹 Direct Media Posting (Old Feature Integration)
# ---------------------------------------------------------------------------
@app.on_message(filters.private & (filters.photo | filters.video))
async def direct_media_handler(bot, msg: Message):
    if msg.from_user.id in user_conversations:
        return

    user = await users_collection.find_one({"user_id": msg.from_user.id})
    if not user or not user.get("channels"):
        return await msg.reply_text("⚠️ আপনি এখনও কোনো চ্যানেল যোগ করেননি। প্রথমে `/addchannel` ব্যবহার করুন।")

    buttons = []
    for ch in user["channels"]:
        if await ensure_bot_admin_rights(bot, ch['id']):
            buttons.append([InlineKeyboardButton(ch["title"], callback_data=f"sendto_{msg.id}_{ch['id']}")])

    if not buttons:
        return await msg.reply_text("⚠️ আপনার সেভ করা কোনো চ্যানেলেই আমার পোস্ট করার অনুমতি নেই। অনুগ্রহ করে বটকে অ্যাডমিন বানান।")

    await msg.reply_text("📤 **পোস্ট করার জন্য একটি চ্যানেল বেছে নিন:**", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("^sendto_"))
async def direct_media_post_callback(bot, cq: CallbackQuery):
    _, msg_id, channel_id = cq.data.split("_")
    msg_id = int(msg_id)
    channel_id = int(channel_id)
    user_id = cq.from_user.id

    if not await ensure_bot_admin_rights(bot, channel_id):
        return await cq.answer("❌ বট অ্যাডমিন নয় অথবা 'Post Messages' পারমিশন নেই!", show_alert=True)

    try:
        await cq.answer("✅ পোস্ট করা হচ্ছে...", show_alert=False)
        media_msg = await bot.get_messages(user_id, msg_id)
        user_data = await users_collection.find_one({"user_id": user_id}) or {}
        
        user_caption = user_data.get("custom_caption", "")
        bot_signature = "✨ **পোস্ট করেছেন:** @Post_Generator_PrimeXBot"

        final_caption = ""
        if media_msg.caption:
            final_caption += media_msg.caption.html + "\n\n"
        if user_caption:
            final_caption += user_caption + "\n\n"
        final_caption += bot_signature

        all_buttons = []
        reaction_row = [
            InlineKeyboardButton("👍 0", callback_data=f"react_DUMMY_like"),
            InlineKeyboardButton("❤️ 0", callback_data=f"react_DUMMY_love")
        ]
        all_buttons.append(reaction_row)

        custom_buttons = user_data.get("custom_buttons", [])
        for btn in custom_buttons:
            all_buttons.append([InlineKeyboardButton(btn["text"], url=btn["url"])])

        if user_data.get('tutorial_link'):
            all_buttons.append([InlineKeyboardButton("🎥 কিভাবে ডাউনলোড করবেন", url=user_data['tutorial_link'])])

        copied_msg = await media_msg.copy(
            chat_id=channel_id,
            caption=final_caption,
        )
        
        await reactions_collection.insert_one({"message_id": copied_msg.id, "chat_id": channel_id, "reactions": {"like": [], "love": []}})
        
        reaction_row[0].callback_data = f"react_{copied_msg.id}_like"
        reaction_row[1].callback_data = f"react_{copied_msg.id}_love"

        await copied_msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(all_buttons))
        
        await cq.message.edit_text("✅ সফলভাবে পোস্ট করা হয়েছে!")

    except Exception as e:
        logger.error(f"Failed to post direct media: {e}")
        await cq.message.edit_text("❌ পোস্ট করতে ব্যর্থ হয়েছে।")

# ---------------------------------------------------------------------------
# 🔹 Reaction Handler
# ---------------------------------------------------------------------------
@app.on_callback_query(filters.regex("^react_"))
async def reaction_handler(bot, cq: CallbackQuery):
    try:
        data_parts = cq.data.split("_", 2)
        if len(data_parts) < 3 or "DUMMY" in data_parts[1]:
            return await cq.answer("এটি একটি প্রিভিউ বাটন।", show_alert=True)

        _, msg_id, reaction = data_parts
        msg_id = int(msg_id)
        user_id = cq.from_user.id

        post = await reactions_collection.find_one({"message_id": msg_id})
        if not post:
            return await cq.answer("দুঃখিত, এই পোস্টের রিঅ্যাকশন তথ্য পাওয়া যায়নি।", show_alert=True)
        
        has_reacted_before = False
        for r_type, users in post["reactions"].items():
            if user_id in users:
                if r_type == reaction:
                    users.remove(user_id)
                    has_reacted_before = True
                else:
                    users.remove(user_id)
        
        if not has_reacted_before:
            post["reactions"].setdefault(reaction, []).append(user_id)
        
        await reactions_collection.update_one({"message_id": msg_id}, {"$set": {"reactions": post["reactions"]}})

        like_count = len(post["reactions"].get("like", []))
        love_count = len(post["reactions"].get("love", []))

        current_keyboard = cq.message.reply_markup.inline_keyboard
        current_keyboard[0] = [
            InlineKeyboardButton(f"👍 {like_count}", callback_data=f"react_{msg_id}_like"),
            InlineKeyboardButton(f"❤️ {love_count}", callback_data=f"react_{msg_id}_love")
        ]
        
        await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(current_keyboard))
        await cq.answer("✅ আপনার রিঅ্যাকশন আপডেট হয়েছে!", show_alert=False)

    except Exception as e:
        logger.error(f"Reaction handling error: {e}")
        await cq.answer("❌ রিঅ্যাকশন দিতে সমস্যা হয়েছে।", show_alert=True)

# ---------------------------------------------------------------------------
# 🔹 Settings, Menus & Other Commands
# ---------------------------------------------------------------------------
@app.on_callback_query(filters.regex("^(settings_menu|help_menu|start_menu|create_post_help|cancel_process)$"))
async def navigation_handler(bot, cq: CallbackQuery):
    data = cq.data
    uid = cq.from_user.id

    if uid in user_conversations:
        del user_conversations[uid]
    
    if data == "cancel_process":
        return await cq.message.edit_text("✅ প্রক্রিয়াটি বাতিল করা হয়েছে।")

    buttons = [
        [InlineKeyboardButton("🎬 পোস্ট তৈরি করুন (/post)", callback_data="create_post_help")],
        [InlineKeyboardButton("⚙️ সেটিংস", callback_data="settings_menu"), InlineKeyboardButton("📚 সাহায্য", callback_data="help_menu")],
        [InlineKeyboardButton("👨‍💻 ডেভেলপার", url="https://t.me/Prime_Nayem")]
    ]
    start_caption = f"👋 স্বাগতম, {cq.from_user.mention}! কিভাবে সাহায্য করতে পারি?"
    
    if data == "start_menu":
        await cq.message.edit_caption(caption=start_caption, reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data == "help_menu" or data == "create_post_help":
        help_text = (
            "📚 **সাহায্য মেনু**\n\n"
            "**পোস্ট তৈরি:**\n"
            "🔹 `/post <নাম>` - মুভি/সিরিজের তথ্যসহ পোস্ট তৈরি করুন।\n"
            "   (উদাহরণ: `/post Avatar 2009`)\n"
            "🔹 **সরাসরি ফটো/ভিডিও পাঠান** - সাধারণ পোস্ট তৈরি করতে।\n\n"
            "**চ্যানেল ম্যানেজমেন্ট:**\n"
            "🔹 `/addchannel <ID>` - চ্যানেল যোগ করুন।\n"
            "🔹 `/delchannel` - চ্যানেল মুছুন।\n"
            "🔹 `/mychannels` - আপনার চ্যানেলগুলো দেখুন।\n\n"
            "**ক্যাপশন ও বাটন:**\n"
            "🔹 `/setcap <ক্যাপশন>` - কাস্টম ক্যাপশন সেট করুন।\n"
            "🔹 `/seecap` - আপনার বর্তমান ক্যাপশন দেখুন।\n"
-            "🔹 `/delcap` - কাস্টম ক্যাপশন মুছুন।\n"
-            "🔹 `/addbutton <নাম | লিংক>` - কাস্টম বাটন যোগ করুন।\n"
-            "🔹 `/mybuttons` - আপনার সেভ করা বাটনগুলো দেখুন।\n"
-            "🔹 `/clearbuttons` - সব কাস্টম বাটন মুছুন।\n\n"
-            "**পোস্টার ও লিংক:**\n"
+            "🔹 `/delcap` - কাস্টম ক্যাপশন মুছুন।\n"
+            "🔹 `/addbutton <নাম | লিংক>` - কাস্টম বাটন যোগ করুন।\n"
+            "🔹 `/mybuttons` - আপনার সেভ করা বাটনগুলো দেখুন।\n"
+            "🔹 `/clearbuttons` - সব কাস্টম বাটন মুছুন।\n\n"
+            "**পোস্টার ও লিংক:**\n"
             "🔹 `/setwatermark <নাম>` - পোস্টারে ওয়াটারমার্ক দিন।\n"
             "🔹 `/badge <টেক্সট>` - পরবর্তী পোস্টের জন্য ব্যাজ দিন।\n"
             "🔹 `/setapi <API>` - শর্টনার API Key সেট করুন।\n"
             "🔹 `/setdomain <ডোমেইন>` - শর্টনার ডোমেইন সেট করুন।\n"
             "🔹 `/settutorial <লিংক>` - টিউটোরিয়াল লিংক সেট করুন।"
         )
        await cq.message.edit_caption(caption=help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⌫ পিছনে", callback_data="start_menu")]])
        )

    elif data == "settings_menu":
        user_data = await users_collection.find_one({'user_id': uid}) or {}
        watermark = user_data.get('watermark_text', 'সেট করা নেই')
        api = "******" + user_data.get('shortener_api', ' ')[-4:] if user_data.get('shortener_api') else 'সেট করা নেই'
        domain = user_data.get('shortener_url', 'সেট করা নেই')
        tutorial = user_data.get('tutorial_link', 'সেট করা নেই')
        settings_text = (
            "⚙️ **আপনার বর্তমান সেটিংস:**\n\n"
            f"💧 **ওয়াটারমার্ক:** `{watermark}`\n"
            f"🔗 **শর্টনার API:** `{api}`\n"
            f"🌐 **শর্টনার ডোমেইন:** `{domain}`\n"
            f"🎥 **টিউটোরিয়াল লিংক:** `{tutorial}`\n\n"
            "`/help` কমান্ড ব্যবহার করে এগুলো পরিবর্তন করার নিয়ম দেখুন।"
        )
        await cq.message.edit_caption(caption=settings_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⌫ পিছনে", callback_data="start_menu")]])
        )
    await cq.answer()

@app.on_callback_query(filters.regex("refresh_check"))
async def refresh_callback(bot, cq: CallbackQuery):
    if await is_subscribed(bot, cq.from_user.id):
        await cq.message.delete()
        await start_handler(bot, cq.message)
    else:
        await cq.answer("❌ আপনি এখনও চ্যানেলে যোগ দেননি।", show_alert=True)

# --- Channel, Caption, Button Management Commands ---
@app.on_message(filters.private & filters.command(["addchannel", "mychannels", "delchannel"]))
async def channel_management(bot: Client, msg: Message):
    command, user_id = msg.command[0].lower(), msg.from_user.id
    if command == "addchannel":
        if len(msg.command) < 2: return await msg.reply_text("⚠️ **ব্যবহার:** `/addchannel [চ্যানেল আইডি]`")
        try: channel_id = int(msg.command[1])
        except ValueError: return await msg.reply_text("⚠️ ইনভ্যালিড চ্যানেল আইডি।")
        try:
            chat = await bot.get_chat(channel_id)
            if chat.type != enums.ChatType.CHANNEL: return await msg.reply_text("⚠️ এই আইডিটি কোনো চ্যানেলের নয়।")
            saved = await save_channel(user_id, channel_id, chat.title)
            await msg.reply_text(f"✅ **{chat.title}** চ্যানেলটি যোগ করা হয়েছে!" if saved else "⚠️ এই চ্যানেলটি আগে থেকেই আছে।")
        except ValueError as e: await msg.reply_text(f"❌ সমস্যা: {e}")
        except Exception as e: logger.error(e); await msg.reply_text("❌ চ্যানেল খুঁজে পাওয়া যায়নি।")
    elif command == "mychannels":
        user = await users_collection.find_one({"user_id": user_id})
        if not user or not user.get("channels"): return await msg.reply_text("📂 আপনার কোনো চ্যানেল সেভ করা নেই।")
        text = "📂 **আপনার সেভ করা চ্যানেলসমূহ:**\n" + "\n".join([f"🔹 **{ch['title']}** (`{ch['id']}`)" for ch in user["channels"]])
        await msg.reply_text(text)
    elif command == "delchannel":
        user = await users_collection.find_one({"user_id": user_id})
        if not user or not user.get("channels"): return await msg.reply_text("📂 মুছে ফেলার মতো কোনো চ্যানেল নেই।")
        buttons = [[InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"delch_{ch['id']}")] for ch in user["channels"]]
        await msg.reply_text("🗑️ মুছে ফেলার জন্য একটি চ্যানেল বেছে নিন:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.private & filters.command(["setcap", "delcap", "seecap"]))
async def caption_commands(bot: Client, msg: Message):
    command, user_id = msg.command[0].lower(), msg.from_user.id
    if command == "setcap":
        caption = msg.text.split(" ", 1)[1] if len(msg.command) > 1 else None
        if not caption: return await msg.reply_text("⚠️ **ব্যবহার:** `/setcap [আপনার ক্যাপশন]`")
        await users_collection.update_one({"user_id": user_id}, {"$set": {"custom_caption": caption}}, upsert=True)
        await msg.reply_text("✅ কাস্টম ক্যাপশন সেট করা হয়েছে!")
    elif command == "seecap":
        user = await users_collection.find_one({"user_id": user_id})
        if not user or not user.get("custom_caption"): return await msg.reply_text("⚠️ আপনার কোনো কাস্টম ক্যাপশন নেই।")
        await msg.reply_text(f"📝 **আপনার বর্তমান ক্যাপশন:**\n\n{user['custom_caption']}")
    elif command == "delcap":
        await users_collection.update_one({"user_id": user_id}, {"$unset": {"custom_caption": ""}})
        await msg.reply_text("🗑️ কাস্টম ক্যাপশন মুছে ফেলা হয়েছে!")

@app.on_message(filters.private & filters.command(["addbutton", "mybuttons", "delbutton", "clearbuttons"]))
async def button_commands(bot: Client, msg: Message):
    command, user_id = msg.command[0].lower(), msg.from_user.id
    if command == "addbutton":
        if "|" not in msg.text: return await msg.reply_text("⚠️ **ব্যবহার:** `/addbutton নাম | http://link.com`")
        try:
            text, url = [part.strip() for part in msg.text.split(" ", 1)[1].split("|", 1)]
            if not text or not url.startswith(('http://', 'https://')): raise ValueError
            await users_collection.update_one({"user_id": user_id}, {"$push": {"custom_buttons": {"text": text, "url": url}}}, upsert=True)
            await msg.reply_text(f"✅ **{text}** বাটনটি যোগ করা হয়েছে!")
        except ValueError: await msg.reply_text("⚠️ ভুল ফরম্যাট।")
    elif command == "mybuttons":
        user = await users_collection.find_one({"user_id": user_id})
        if not user or not user.get("custom_buttons"): return await msg.reply_text("📂 আপনার কোনো কাস্টম বাটন নেই।")
        buttons = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in user["custom_buttons"]]
        await msg.reply_text("📂 **আপনার কাস্টম বাটনসমূহ:**", reply_markup=InlineKeyboardMarkup(buttons))
    elif command == "delbutton":
        user = await users_collection.find_one({"user_id": user_id})
        if not user or not user.get("custom_buttons"): return await msg.reply_text("📂 মুছে ফেলার মতো কোনো বাটন নেই।")
        buttons = [[InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"delbtn_{b['text']}")] for b in user["custom_buttons"]]
        await msg.reply_text("🗑️ মুছে ফেলার জন্য একটি বাটন বেছে নিন:", reply_markup=InlineKeyboardMarkup(buttons))
    elif command == "clearbuttons":
        await users_collection.update_one({"user_id": user_id}, {"$set": {"custom_buttons": []}})
        await msg.reply_text("🗑️ সব কাস্টম বাটন মুছে ফেলা হয়েছে!")

@app.on_message(filters.private & filters.command(["setwatermark", "setapi", "setdomain", "settutorial", "settings", "badge"]))
async def settings_commands(bot: Client, msg: Message):
    command, user_id = msg.command[0].lower(), msg.from_user.id
    if len(msg.command) > 1:
        value = msg.text.split(" ", 1)[1]
        if command == "setwatermark": await users_collection.update_one({"user_id": user_id}, {"$set": {"watermark_text": value}}, upsert=True); await msg.reply_text(f"✅ ওয়াটারমার্ক সেট করা হয়েছে: `{value}`")
        elif command == "setapi": await users_collection.update_one({"user_id": user_id}, {"$set": {"shortener_api": value}}, upsert=True); await msg.reply_text("✅ শর্টনার API Key সেট করা হয়েছে।")
        elif command == "setdomain": value = value.replace("https://", "").replace("http://", ""); await users_collection.update_one({"user_id": user_id}, {"$set": {"shortener_url": value}}, upsert=True); await msg.reply_text(f"✅ শর্টনার ডোমেইন সেট করা হয়েছে: `{value}`")
        elif command == "settutorial": await users_collection.update_one({"user_id": user_id}, {"$set": {"tutorial_link": value}}, upsert=True); await msg.reply_text("✅ টিউটোরিয়াল লিংক সেট করা হয়েছে।")
        elif command == "badge": user_conversations.setdefault(user_id, {})['temp_badge_text'] = value; await msg.reply_text(f"✅ পরবর্তী পোস্টের জন্য ব্যাজ সেট করা হয়েছে: `{value}`")
    else:
        if command == "settings": await navigation_handler(bot, msg); return
        elif command == "setwatermark": await users_collection.update_one({"user_id": user_id}, {"$unset": {"watermark_text": ""}}); await msg.reply_text("🗑️ ওয়াটারমার্ক মুছে ফেলা হয়েছে।")
        elif command == "badge": user_conversations.get(user_id, {}).pop('temp_badge_text', None); await msg.reply_text("🗑️ অস্থায়ী ব্যাজ টেক্সট মুছে ফেলা হয়েছে।")
        else: await msg.reply_text("⚠️ এই কমান্ডের জন্য একটি ভ্যালু প্রয়োজন।")

@app.on_message(filters.private & filters.command(["stats", "broadcast"]) & filters.user(OWNER_ID))
async def owner_commands(bot: Client, msg: Message):
    if msg.command[0].lower() == "stats":
        total_users = await users_collection.count_documents({})
        pipeline = [{"$project": {"channel_count": {"$size": {"$ifNull": ["$channels", []]}}}}]
        total_channels = sum(doc["channel_count"] async for doc in users_collection.aggregate(pipeline))
        await msg.reply_text(f"📊 **বটের পরিসংখ্যান:**\n\n👤 **মোট ব্যবহারকারী:** {total_users}\n📂 **মোট সেভ করা চ্যানেল:** {total_channels}")
    elif msg.command[0].lower() == "broadcast":
        if not msg.reply_to_message: return await msg.reply_text("⚠️ ব্রডকাস্ট করার জন্য একটি মেসেজে রিপ্লাই করুন।")
        sent, failed = 0, 0
        status_msg = await msg.reply_text("📢 ব্রডকাস্ট শুরু হচ্ছে...")
        async for user_id in users_collection.distinct("user_id"):
            try: await msg.reply_to_message.copy(user_id); sent += 1
            except Exception: failed += 1
        await status_msg.edit_text(f"✅ **ব্রডকাস্ট সম্পন্ন!**\n\n📤 **সফল:** {sent}\n❌ **ব্যর্থ:** {failed}")

@app.on_callback_query(filters.regex("^(delch_|delbtn_)"))
async def delete_callback_handler(bot: Client, cq: CallbackQuery):
    user_id = cq.from_user.id
    if cq.data.startswith("delch_"):
        ch_id = int(cq.data.split("_")[1])
        await users_collection.update_one({"user_id": user_id}, {"$pull": {"channels": {"id": ch_id}}})
        await cq.answer("🗑️ চ্যানেল মুছে ফেলা হয়েছে!", show_alert=True)
    elif cq.data.startswith("delbtn_"):
        text = cq.data.split("_", 1)[1]
        await users_collection.update_one({"user_id": user_id}, {"$pull": {"custom_buttons": {"text": text}}})
        await cq.answer(f"🗑️ '{text}' বাটনটি মুছে ফেলা হয়েছে!", show_alert=True)
    await cq.message.delete()


# ---------------------------------------------------------------------------
# 🔹 Run The Bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("✅ Bot is starting...")
    app.run()
    logger.info("👋 Bot has stopped.")
