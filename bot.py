import os
import time
import datetime
from datetime import timezone, timedelta
import threading
import telebot
from telebot import apihelper
from telebot.types import InputMediaPhoto
from telebot.apihelper import ApiTelegramException
import config
import database
import graphics
import games

# Force no proxy
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'
apihelper.proxy = None

bot = telebot.TeleBot(config.API_TOKEN)

# Global start time for uptime tracking
BOT_START_TIME = time.time()

# Initialize broadcast database
database.init_broadcast_db()

# ---------------------------------------------------------------------------
# SCHEDULER STATE
# ---------------------------------------------------------------------------

def load_scheduler():
    return database.load_json(config.SCHEDULER_FILE, {
        "enabled":    False,
        "interval":   60,
        "game_type":  "random",
        "window_start": config.SCHEDULER_WINDOW_START,
        "window_end":   config.SCHEDULER_WINDOW_END,
        "last_game":  0,
        "tagall_last": 0,
    })

def save_scheduler(data):
    database.save_json(bot, config.SCHEDULER_FILE, data)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def is_admin(user_id):
    return user_id == config.ADMIN_ID

def utc_to_local(utc_hour):
    return (utc_hour + 2) % 24

def local_hour():
    return (datetime.datetime.utcnow().hour + 2) % 24

def local_now():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)

def schedule_delete(chat_id, message_id, delay=config.AUTO_DELETE_DELAY):
    def delete():
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    timer = threading.Timer(delay, delete)
    timer.daemon = True
    timer.start()

# Safe edit without cache – always attempts edit
def safe_edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    try:
        bot.edit_message_text(text, chat_id, message_id,
                              reply_markup=reply_markup, parse_mode=parse_mode)
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            # Ignore – the user will get a callback response elsewhere
            return
        raise

def safe_edit_message_media(chat_id, message_id, media, reply_markup=None):
    try:
        bot.edit_message_media(chat_id=chat_id, message_id=message_id,
                              media=media, reply_markup=reply_markup)
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            return
        raise

# Welcome helper (MP4 first)
def send_welcome(bot, chat_id, caption):
    """Sends welcome animation – tries MP4 first, then GIF, then text."""
    import requests
    mp4_url = f"{config.GITHUB_RAW_BASE_URL}welcome.mp4"
    gif_url = f"{config.GITHUB_RAW_BASE_URL}welcome.gif"
    
    # Try MP4 first
    try:
        r = requests.head(mp4_url, timeout=5)
        if r.status_code == 200:
            bot.send_video(chat_id, mp4_url, caption=caption, parse_mode="Markdown", supports_streaming=True)
            return
    except Exception:
        pass
    
    # Try GIF as fallback
    try:
        r = requests.head(gif_url, timeout=5)
        if r.status_code == 200:
            bot.send_animation(chat_id, gif_url, caption=caption, parse_mode="Markdown")
            return
    except Exception:
        pass
    
    # Fallback to text only
    bot.send_message(chat_id, caption, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# HELP MENU (5 Categories)
# ---------------------------------------------------------------------------

def _build_help_menu():
    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🎮 Games", callback_data="help_games"),
        telebot.types.InlineKeyboardButton("🏆 Rankings & Stats", callback_data="help_rankings"),
        telebot.types.InlineKeyboardButton("🛒 Shop & Power-ups", callback_data="help_shop"),
        telebot.types.InlineKeyboardButton("📋 Info & Tools", callback_data="help_info"),
        telebot.types.InlineKeyboardButton("⚙️ Admin", callback_data="help_admin"),
    )
    return markup

def _get_help_text(category):
    texts = {
        "games": (
            "🎮 *GAMES*\n\n"
            "/game — Guess the Character\n"
            "/year — Guess the Release Year\n"
            "/picture — Scrambled Image Guessing\n"
            "/trivia — Trivia (choose category)\n"
            "/spin — Wheel of Fortune\n"
            "/versus @user — Challenge someone to a duel"
        ),
        "rankings": (
            "🏆 *RANKINGS & STATS*\n\n"
            "/leaderboard — View rankings\n"
            "/mystats — Your stats (text)\n"
            "/viewstats @user — Stats of mentioned user"
        ),
        "shop": (
            "🛒 *SHOP & POWER-UPS*\n\n"
            "/shop — Spend your points on titles & items\n"
            "/powerups — View your power-ups\n\n"
            "Power-ups:\n"
            "✂️ 50/50 — Removes two wrong trivia answers\n"
            "🧊 Streak Freeze — Protects your streak once\n"
            "⬆️ Double Down — Double points on next correct"
        ),
        "info": (
            "📋 *INFO & TOOLS*\n\n"
            "/table — League standings (image)\n"
            "/fixtures — Match fixtures (image)"
        ),
        "admin": (
            "⚙️ *ADMIN COMMANDS*\n\n"
            "/admin — Admin control panel\n"
            "/tagall — Tag all members\n"
            "/setschedule — Configure auto-game scheduler\n"
            "/mute @user 1h — Mute a user\n"
            "/unmute @user — Unmute a user\n"
            "/broadcast YYYY-MM-DD HH:MM msg — Schedule a broadcast\n"
            "/forcebroadcast — Force-send all pending broadcasts\n"
            "/checknow — Manually check for pending broadcasts\n"
            "/uploadtrivia — Upload trivia questions (file)\n"
            "/checkimages — Check for missing images\n"
            "/testbroadcast — Test broadcast system\n"
            "/rebuildcache — Rebuild image cache\n"
            "/testmorning — Test morning message\n"
            "/status — Bot status\n"
            "/listbroadcasts — List scheduled broadcasts"
        ),
    }
    return texts.get(category, "Unknown category.")

# ---------------------------------------------------------------------------
# LEADERBOARD PAGINATION HELPERS
# ---------------------------------------------------------------------------

def _build_leaderboard_markup(mode, page, total_pages):
    markup = telebot.types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        telebot.types.InlineKeyboardButton("📅 Monthly", callback_data=f"lb_monthly_1"),
        telebot.types.InlineKeyboardButton("📆 Yearly",  callback_data=f"lb_yearly_1"),
        telebot.types.InlineKeyboardButton("🌟 All Time", callback_data=f"lb_alltime_1"),
    )
    if total_pages > 1:
        nav_btns = []
        if page > 1:
            nav_btns.append(telebot.types.InlineKeyboardButton("⬅️ Prev", callback_data=f"lb_{mode}_{page-1}"))
        nav_btns.append(telebot.types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="lb_nop"))
        if page < total_pages:
            nav_btns.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"lb_{mode}_{page+1}"))
        markup.row(*nav_btns)
    return markup

# ---------------------------------------------------------------------------
# LEAGUE TABLE & FIXTURES
# ---------------------------------------------------------------------------

def show_league_table(message):
    bot.send_chat_action(message.chat.id, 'upload_photo')
    try:
        img = graphics.generate_table_image(bot)
        if img:
            bot.send_photo(message.chat.id, img, caption="🏆 *ZA SORA ZENITH LEAGUE STANDINGS*", parse_mode="Markdown")
            if hasattr(img, 'close'): img.close()
        else:
            bot.reply_to(message, "❌ Standings unavailable.")
    except Exception as e:
        database.log_error_to_admin(bot, "Table Command", e)

def _build_fixtures_menu_markup(rows):
    home_idx, away_idx, _, _, _ = graphics.detect_fixtures_columns(rows)
    header_offset = 1 if (
        "home" in str(rows[0][home_idx]).lower() or
        rows[0][0].lower() in ["md", "matchday"]
    ) else 0

    teams = set()
    for row in rows[header_offset:]:
        if len(row) > max(home_idx, away_idx):
            h = row[home_idx].strip()
            a = row[away_idx].strip()
            if h and not h.isdigit(): teams.add(h)
            if a and not a.isdigit(): teams.add(a)

    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton("📅  View by Matchday", callback_data="fix_md_menu"),
        telebot.types.InlineKeyboardButton("👤  View by Player", callback_data="fix_pl_menu"),
    )
    return markup, sorted(list(teams))

def show_fixtures_inline(chat_id, message_id=None):
    rows = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
    if not rows or len(rows) <= 1:
        bot.send_message(chat_id, "❌ Fixtures unavailable.")
        return
    markup, _ = _build_fixtures_menu_markup(rows)
    text = "📋 *FIXTURES*\n\nChoose how you want to browse:"
    if message_id:
        try:
            safe_edit_message(chat_id, message_id, text, reply_markup=markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def show_fixtures(message):
    try:
        rows = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
        if not rows or len(rows) <= 1:
            bot.reply_to(message, "❌ Fixtures unavailable.")
            return
        markup, _ = _build_fixtures_menu_markup(rows)
        bot.send_message(
            message.chat.id,
            "📋 *FIXTURES*\n\nChoose how you want to browse:",
            reply_markup=markup, parse_mode="Markdown"
        )
    except Exception as e:
        database.log_error_to_admin(bot, "Fixtures Command", e)
        bot.reply_to(message, "💥 Failed to fetch fixtures.")

# ---------------------------------------------------------------------------
# LEADERBOARD
# ---------------------------------------------------------------------------

def show_leaderboard(message):
    bot.send_chat_action(message.chat.id, 'upload_photo')
    mode = "monthly"
    page = 1
    all_entries = database.get_leaderboard(message.chat.id, mode=mode, top_n=100)
    total_pages = (len(all_entries) + 9) // 10
    img = graphics.build_leaderboard_image(message.chat.id, mode, page)
    if img:
        caption = f"🏆 *Leaderboard — {mode.upper()}* (Page {page}/{total_pages})"
        markup = _build_leaderboard_markup(mode, page, total_pages)
        bot.send_photo(message.chat.id, img, caption=caption,
                       reply_markup=markup, parse_mode="Markdown")
        if hasattr(img, 'close'): img.close()
    else:
        bot.send_message(message.chat.id, "No scores yet!")

# ---------------------------------------------------------------------------
# SHOP
# ---------------------------------------------------------------------------

def show_shop(message):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    for item in config.SHOP_TITLES:
        markup.add(telebot.types.InlineKeyboardButton(
            f"{item['name']} — {item['cost']} pts",
            callback_data=f"shop_{item['id']}"
        ))
    bot.send_message(
        message.chat.id,
        "🛒 *POINT SHOP*\n\nTitles expire after 30 days. Special items are instant!",
        reply_markup=markup, parse_mode="Markdown"
    )

# ---------------------------------------------------------------------------
# TAG ALL
# ---------------------------------------------------------------------------

def tag_all_members(message, custom_msg=""):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Admin only.")
        return

    sched = load_scheduler()
    now   = time.time()
    if now - sched.get("tagall_last", 0) < config.TAGALL_COOLDOWN_HOURS * 3600:
        remaining = int((config.TAGALL_COOLDOWN_HOURS * 3600 - (now - sched["tagall_last"])) / 60)
        bot.reply_to(message, f"⏳ Tag all on cooldown. {remaining} minutes remaining.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    safe_msg = custom_msg[:200].replace('"', "'")
    markup.row(
        telebot.types.InlineKeyboardButton("✅ Confirm Send", callback_data="tagall_confirm"),
        telebot.types.InlineKeyboardButton("❌ Cancel",       callback_data="tagall_cancel")
    )
    sched["tagall_pending_msg"] = custom_msg
    sched["tagall_pending_chat"] = message.chat.id
    save_scheduler(sched)

    bot.reply_to(
        message,
        f"📢 *Tag All Preview:*\n\n{custom_msg}\n\n"
        f"This will tag all {len(database.get_all_members(message.chat.id))} tracked members. Confirm?",
        reply_markup=markup, parse_mode="Markdown"
    )

def _do_tag_all(chat_id, custom_msg):
    members = database.get_all_members(chat_id)
    if not members:
        bot.send_message(chat_id, "❌ No members tracked yet.")
        return

    mentions = " ".join([f"[{name}](tg://user?id={uid})" for uid, name in members])
    full_msg = f"📢 *ANNOUNCEMENT*\n\n{custom_msg}\n\n{mentions}"

    if len(full_msg) > 4096:
        bot.send_message(chat_id, f"📢 *ANNOUNCEMENT*\n\n{custom_msg}", parse_mode="Markdown")
        chunk_size = 30
        for i in range(0, len(members), chunk_size):
            chunk    = members[i:i + chunk_size]
            mentions = " ".join([f"[{name}](tg://user?id={uid})" for uid, name in chunk])
            bot.send_message(chat_id, mentions, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, full_msg, parse_mode="Markdown")

    sched = load_scheduler()
    sched["tagall_last"] = time.time()
    save_scheduler(sched)

# ---------------------------------------------------------------------------
# QUOTE MANAGEMENT (Admin DM only)
# ---------------------------------------------------------------------------

def show_quotes_page(chat_id, page=1):
    quotes   = database.load_quotes()
    per_page = 10
    total    = len(quotes)
    pages    = (total + per_page - 1) // per_page
    page     = max(1, min(page, pages))
    start    = (page - 1) * per_page
    chunk    = quotes[start:start + per_page]

    text = f"📝 *Quotes — Page {page}/{pages}* ({total} total)\n\n"
    for q in chunk:
        preview = q["text"][:60] + "..." if len(q["text"]) > 60 else q["text"]
        text   += f"*#{q['id']}* — {preview}\n"
        text   += f"_— {q['author']}_\n\n"

    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    nav    = []
    if page > 1:
        nav.append(telebot.types.InlineKeyboardButton("⬅️ Prev", callback_data=f"qpage_{page-1}"))
    if page < pages:
        nav.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"qpage_{page+1}"))
    if nav:
        markup.row(*nav)

    return text, markup

# ---------------------------------------------------------------------------
# ADMIN PANEL
# ---------------------------------------------------------------------------

def show_admin_panel(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Admin only.")
        return
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🎮 Start Character Game", callback_data="admin_startchar"),
        telebot.types.InlineKeyboardButton("🎬 Start Year Game",      callback_data="admin_startyear"),
        telebot.types.InlineKeyboardButton("🖼️ Start Picture Game",   callback_data="admin_startpicture"),
        telebot.types.InlineKeyboardButton("❓ Start Trivia",          callback_data="admin_starttrivia"),
        telebot.types.InlineKeyboardButton("📅 Set Schedule",          callback_data="admin_schedule"),
        telebot.types.InlineKeyboardButton("📢 Tag All",               callback_data="admin_tagall"),
        telebot.types.InlineKeyboardButton("🏆 Leaderboard",           callback_data="admin_leaderboard"),
        telebot.types.InlineKeyboardButton("🔄 Rebuild Cache",         callback_data="admin_rebuild"),
        telebot.types.InlineKeyboardButton("📊 Stats",                 callback_data="admin_stats"),
        telebot.types.InlineKeyboardButton("🔇 Mute User",             callback_data="admin_mute"),
        telebot.types.InlineKeyboardButton("📢 Broadcast",             callback_data="admin_broadcast"),
        telebot.types.InlineKeyboardButton("🔍 Check Images",          callback_data="admin_checkimages"),
    )
    sched = load_scheduler()
    status_icon = "✅" if sched.get("enabled") else "❌"
    bot.send_message(
        message.chat.id,
        f"⚙️ *ADMIN PANEL*\n\n"
        f"Auto-scheduler: {status_icon} {'ON' if sched.get('enabled') else 'OFF'}\n"
        f"Interval: every {sched.get('interval', 60)} min\n"
        f"Game type: {sched.get('game_type', 'random').title()}\n"
        f"Active window: {sched.get('window_start', 18)}:00 — {sched.get('window_end', 23)}:00",
        reply_markup=markup, parse_mode="Markdown"
    )

def show_schedule_panel(chat_id):
    sched  = load_scheduler()
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    toggle_label = "❌ Disable" if sched.get("enabled") else "✅ Enable"
    markup.add(telebot.types.InlineKeyboardButton(toggle_label, callback_data="sched_toggle"))
    markup.add(*[
        telebot.types.InlineKeyboardButton(f"⏱ {m}min", callback_data=f"sched_interval_{m}")
        for m in config.SCHEDULE_INTERVALS
    ])
    markup.add(*[
        telebot.types.InlineKeyboardButton(f"⏰ {s}s limit", callback_data=f"sched_timelimit_{s}")
        for s in [30, 45, 60, 90, 120]
    ])
    markup.add(
        telebot.types.InlineKeyboardButton("🎮 Character", callback_data="sched_type_character"),
        telebot.types.InlineKeyboardButton("🎬 Year",      callback_data="sched_type_year"),
        telebot.types.InlineKeyboardButton("🖼️ Picture",   callback_data="sched_type_picture"),
        telebot.types.InlineKeyboardButton("❓ Trivia",    callback_data="sched_type_trivia"),
        telebot.types.InlineKeyboardButton("🎲 Random",    callback_data="sched_type_random"),
    )
    markup.add(telebot.types.InlineKeyboardButton("🔙 Back", callback_data="admin_back"))

    status_icon = "✅" if sched.get("enabled") else "❌"
    bot.send_message(
        chat_id,
        f"📅 *SCHEDULE SETTINGS*\n\n"
        f"Status: {status_icon} {'ON' if sched.get('enabled') else 'OFF'}\n"
        f"Interval: every *{sched.get('interval', 60)} min*\n"
        f"Type: *{sched.get('game_type', 'random').title()}*\n"
        f"Window: *{sched.get('window_start',18)}:00 – {sched.get('window_end',23)}:00*\n"
        f"⏰ Answer time limit: *{sched.get('answer_time_limit', 60)}s*",
        reply_markup=markup, parse_mode="Markdown"
    )

def show_stats(chat_id):
    data = database.load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    if chat_str not in data:
        bot.send_message(chat_id, "📊 No stats yet.")
        return
    users = data[chat_str]
    total_games  = sum(u.get("games_played", 0) for u in users.values())
    total_pts    = sum(u.get("alltime_points", 0) for u in users.values())
    most_active  = max(users.values(), key=lambda u: u.get("games_played", 0), default=None)
    top_scorer   = max(users.values(), key=lambda u: u.get("alltime_points", 0), default=None)
    best_streak  = max(users.values(), key=lambda u: u.get("best_streak", 0), default=None)
    bot.send_message(
        chat_id,
        f"📊 *GROUP STATS*\n\n"
        f"👥 Tracked members: {len(users)}\n"
        f"🎮 Total games played: {total_games}\n"
        f"💰 Total points distributed: {total_pts}\n"
        f"🏃 Most active: {most_active.get('username','?')} ({most_active.get('games_played',0)} games)\n"
        f"🏆 Top scorer: {top_scorer.get('username','?')} ({top_scorer.get('alltime_points',0)} pts)\n"
        f"🔥 Best streak: {best_streak.get('username','?')} ({best_streak.get('best_streak',0)} in a row)",
        parse_mode="Markdown"
    )

# ---------------------------------------------------------------------------
# WEEKLY RECAP
# ---------------------------------------------------------------------------

def send_weekly_recap(bot):
    groups = database.get_all_groups()
    for group_id in groups:
        lb = database.get_leaderboard(group_id, mode="monthly", top_n=3)
        top3 = ""
        if lb:
            medals = ["🥇", "🥈", "🥉"]
            for rank, username, points, streak, title in lb:
                top3 += f"{medals[rank-1]} {username} — {points} pts\n"
        else:
            top3 = "No scores yet this month!\n"
        msg = (
            f"📊 *WEEKLY RECAP*\n\n"
            f"🏆 *Monthly Top 3:*\n{top3}\n"
            f"Keep up the great work, family! 🙏🔥"
        )
        try:
            bot.send_message(group_id, msg, parse_mode="Markdown")
        except Exception as e:
            print(f"Weekly recap failed for {group_id}: {e}")

# ---------------------------------------------------------------------------
# MORNING MESSAGE
# ---------------------------------------------------------------------------

def send_morning_message(bot):
    print("🌅 send_morning_message() called")
    now     = local_now()
    weekday = now.strftime("%A")
    date    = now.strftime("%d %B %Y")
    quote   = database.get_random_quote()
    groups  = database.get_all_groups()
    print(f"📊 Found {len(groups)} groups")

    for group_id in groups:
        lb = database.get_leaderboard(group_id, mode="monthly", top_n=3)
        top3 = ""
        if lb:
            medals = ["🥇", "🥈", "🥉"]
            for rank, username, points, streak, title in lb:
                top3 += f"{medals[rank-1]} {username} — {points} pts\n"
        else:
            top3 = "No scores yet this month!\n"

        msg = (
            f"☀️ *Good Morning, Family!*\n"
            f"_{weekday}, {date}_\n\n"
        )
        if quote:
            msg += f"💬 *{quote['text']}*\n_— {quote['author']}_\n\n"

        msg += (
            f"🏆 *Monthly Top 3:*\n{top3}\n"
            f"🎮 *Game Commands:*\n"
            f"/game — Guess the Character\n"
            f"/year — Guess the Year\n"
            f"/picture — Scrambled Image\n"
            f"/trivia — Trivia Quiz\n"
            f"/spin — Wheel of Fortune\n"
            f"/leaderboard — Rankings\n"
            f"/shop — Point Shop\n\n"
            f"Let's have a great day! 🙏🔥"
        )
        members = database.get_all_members(group_id)
        if members:
            tag_line = " ".join([f"[{name}](tg://user?id={uid})" for uid, name in members])
            msg += f"\n\n🌱 _Sending love to the whole family_ 🌱\n{tag_line}"
        try:
            bot.send_message(group_id, msg, parse_mode="Markdown")
            print(f"✅ Morning message sent to group {group_id}")
        except Exception as e:
            print(f"❌ Morning message failed for {group_id}: {e}")

# ---------------------------------------------------------------------------
# COMMAND ROUTER
# ---------------------------------------------------------------------------

@bot.message_handler(content_types=['photo'])
def handle_photo_messages(message):
    if message.chat.id == config.ADMIN_ID and message.caption:
        if message.caption.strip().lower().startswith("/saveimage"):
            handle_image_upload(message)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.chat.id != config.ADMIN_ID:
        return
    state = database.load_json("upload_state.json", {})
    if not state.get("pending"):
        return
    import requests
    file_info = bot.get_file(message.document.file_id)
    dl_url = f"https://api.telegram.org/file/bot{config.API_TOKEN}/{file_info.file_path}"
    try:
        response = requests.get(dl_url, timeout=30, proxies={})
        response.raise_for_status()
        content = response.content.decode('utf-8')
        filename = message.document.file_name.lower()
        if filename.endswith('.json'):
            import json
            new_questions = json.loads(content)
        elif filename.endswith('.csv'):
            import csv
            lines = content.splitlines()
            reader = csv.DictReader(lines)
            new_questions = [{"category": row["category"], "question": row["question"], "options": [row["optionA"], row["optionB"], row["optionC"], row["optionD"]], "answer": row["answer"]} for row in reader]
        else:
            bot.reply_to(message, "❌ Unsupported file format. Use JSON or CSV.")
            return
        trivia = database.load_json(config.TRIVIA_DB, [])
        existing_ids = {q["id"] for q in trivia}
        new_id = max(existing_ids) + 1 if existing_ids else 1
        for q in new_questions:
            q["id"] = new_id
            new_id += 1
            trivia.append(q)
        database.save_json(bot, config.TRIVIA_DB, trivia)
        bot.reply_to(message, f"✅ Added {len(new_questions)} new trivia questions!")
        database.save_json(bot, "upload_state.json", {"pending": False})
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_member(message):
    for member in message.new_chat_members:
        if member.is_bot:
            continue
        username = member.username or member.first_name
        database.track_member(bot, message.chat.id, member.id, username)
        members  = database.get_all_members(message.chat.id)
        tag_line = " ".join([f"[{n}](tg://user?id={uid})" for uid, n in members if uid != member.id])
        welcome = (
            f"{config.WELCOME_MSG}\n\n"
            f"👋 *Welcome [{username}](tg://user?id={member.id})!* "
            f"Say hi to the family 🌱\n{tag_line}"
        )
        send_welcome(bot, message.chat.id, welcome)

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    if not message.text:
        return

    user_id  = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    chat_id  = message.chat.id

    database.track_member(bot, chat_id, user_id, username)

    # Game answer check first
    if games.check_user_answer(bot, message):
        return

    if not message.text.startswith('/'):
        return

    cmd = message.text.split()[0].split('@')[0].lower()
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if cmd == '/start':
        send_welcome(bot, chat_id, config.WELCOME_MSG)

    elif cmd == '/help':
        show_help(message)

    elif cmd == '/mystats':
        show_my_stats(message)

    elif cmd == '/viewstats':
        if args and args[0].startswith('@'):
            target_mention = args[0].lstrip('@')
            members = database.get_all_members(chat_id)
            target = next((m for m in members if m[1].lower() == target_mention.lower()), None)
            if not target:
                bot.reply_to(message, "❌ User not found.")
                return
            target_id, target_name = target
            show_my_stats(message, target_id, target_name)
        else:
            bot.reply_to(message, "Usage: /viewstats @username")

    elif cmd == '/status':
        if is_admin(user_id):
            handle_status(message)
        else:
            bot.reply_to(message, "❌ Admin only.")

    elif cmd == '/table':
        show_league_table(message)

    elif cmd == '/fixtures':
        show_fixtures(message)

    elif cmd in ['/game', '/startgame', '/quiz']:
        games.send_character_category_picker(bot, chat_id)

    elif cmd in ['/year', '/startyear', '/yeargame']:
        games.send_year_category_picker(bot, chat_id)

    elif cmd == '/picture':
        games.send_character_category_picker(bot, chat_id)

    elif cmd == '/trivia':
        games.send_trivia_category_picker(bot, chat_id)

    elif cmd == '/hint':
        games.process_hint(bot, message=message)

    elif cmd == '/stop':
        if chat_id in games.active_games:
            del games.active_games[chat_id]
            bot.reply_to(message, "🛑 Game stopped.")

    elif cmd == '/leaderboard':
        show_leaderboard(message)

    elif cmd == '/shop':
        show_shop(message)

    elif cmd == '/powerups':
        data = database.load_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(user_id)
        if chat_str not in data or user_str not in data[chat_str]:
            bot.reply_to(message, "💡 You don't have any power-ups.\n\nPurchase them from the shop!")
            return
        u = data[chat_str][user_str]
        powerups = u.get("powerups", {})
        if not powerups:
            bot.reply_to(message, "💡 You don't have any power-ups.\n\nPurchase them from the shop!")
            return
        text = "⚡ *Your Power-Ups*\n\n"
        for pid, count in powerups.items():
            if count > 0:
                name = config.POWERUPS.get(pid, {}).get("emoji", "⚡") + " " + pid.replace("_", " ").title()
                text += f"{name}: x{count}\n"
        bot.reply_to(message, text, parse_mode="Markdown")

    elif cmd == '/spin':
        handle_spin(message)

    elif cmd == '/versus':
        if not args:
            bot.reply_to(message, "Usage: /versus @username")
            return
        target_mention = args[0].lstrip('@')
        members = database.get_all_members(chat_id)
        target  = next((m for m in members if m[1].lower() == target_mention.lower()), None)
        if not target:
            bot.reply_to(message, "❌ User not found in tracked members.")
            return
        games.start_versus(bot, chat_id, user_id, username, target[0], target[1])

    elif cmd == '/forfeit':
        games.handle_versus_forfeit(bot, message)

    elif cmd == '/tagall':
        custom_msg = " ".join(args) if args else "Attention everyone!"
        tag_all_members(message, custom_msg)

    elif cmd == '/admin':
        if is_admin(user_id):
            show_admin_panel(message)
        else:
            bot.reply_to(message, "❌ Admin only.")

    elif cmd == '/testmorning':
        if is_admin(user_id):
            send_morning_message(bot)
            bot.reply_to(message, "✅ Morning message sent (test).")
        else:
            bot.reply_to(message, "❌ Admin only.")

    elif cmd == '/checknow' and is_admin(user_id):
        bot.reply_to(message, "🔄 Manually checking for pending broadcasts...")
        pending = database.get_pending_broadcasts()
        if pending:
            bot.reply_to(message, f"📢 Found {len(pending)} pending broadcasts. They will be sent shortly.")
        else:
            bot.reply_to(message, "📭 No pending broadcasts.")
        return

    elif cmd == '/listbroadcasts' and is_admin(user_id):
        broadcasts = database.get_all_broadcasts()
        if not broadcasts:
            bot.reply_to(message, "📭 No broadcasts scheduled.")
            return
        text = "📋 *Scheduled Broadcasts*\n\n"
        for i, b in enumerate(broadcasts):
            status = "✅ Sent" if b["sent"] else "⏳ Pending"
            dt = datetime.datetime.fromtimestamp(b["send_time"]).strftime("%Y-%m-%d %H:%M")
            target = "All Groups" if b["chat_id"] is None else f"Chat {b['chat_id']}"
            text += f"{i+1}. {dt} – {b['message'][:30]}... ({status}) – Target: {target}\n"
        bot.reply_to(message, text, parse_mode="Markdown")

    elif cmd == '/checkimages' and is_admin(user_id):
        bot.reply_to(message, "🔍 Checking for missing images...")
        notify_missing_images()
        bot.reply_to(message, "✅ Check complete. Admin has been notified of any missing images.")

    elif cmd == '/testbroadcast' and is_admin(user_id):
        msg = "🧪 *Test Broadcast*\n\nThis is a test of the broadcast system. If you received this, it's working! 🎉"
        bot.reply_to(message, "📤 Sending test broadcast...")
        groups = database.get_all_groups()
        count = 0
        for gid in groups:
            try:
                bot.send_message(gid, msg, parse_mode="Markdown")
                count += 1
            except Exception as e:
                print(f"Test broadcast failed for {gid}: {e}")
        bot.reply_to(message, f"✅ Test broadcast sent to {count} groups.")

    elif cmd == '/forcebroadcast' and is_admin(user_id):
        bot.reply_to(message, "📤 Force-sending all unsent broadcasts...")
        pending = database.get_pending_broadcasts()
        if not pending:
            bot.reply_to(message, "📭 No unsent broadcasts.")
            return
        count = 0
        for broadcast in pending:
            try:
                if broadcast["chat_id"] is None:
                    groups = database.get_all_groups()
                    for gid in groups:
                        try:
                            bot.send_message(gid, broadcast["message"], parse_mode="Markdown")
                        except Exception as e:
                            print(f"❌ [FORCE] Failed to send to {gid}: {e}")
                else:
                    bot.send_message(broadcast["chat_id"], broadcast["message"], parse_mode="Markdown")
                database.mark_broadcast_sent(bot, broadcast["id"])
                count += 1
            except Exception as e:
                print(f"Force broadcast failed: {e}")
        bot.reply_to(message, f"✅ Force-sent {count} broadcasts.")

    elif cmd == '/rebuildcache' and is_admin(user_id):
        bot.reply_to(message, "🔄 Rebuilding image cache...")
        threading.Thread(target=graphics.clear_and_rebuild_disk_cache, args=(bot,), daemon=True).start()
        bot.reply_to(message, "🔄 Cache rebuild started in background.")

    elif cmd == '/mute' and is_admin(user_id):
        if len(args) < 2:
            bot.reply_to(message, "Usage: /mute @username 1h  (or 10m, 24h, etc.)")
            return
        target_mention = args[0].lstrip('@')
        duration_str = args[1]
        import re
        match = re.match(r'(\d+)([mh])', duration_str)
        if not match:
            bot.reply_to(message, "❌ Invalid duration. Use like: 10m, 1h, 24h")
            return
        num, unit = int(match.group(1)), match.group(2)
        seconds = num * 60 if unit == 'm' else num * 3600
        if seconds < 60 or seconds > 86400:
            bot.reply_to(message, "❌ Duration must be between 1m and 24h.")
            return
        members = database.get_all_members(chat_id)
        target = next((m for m in members if m[1].lower() == target_mention.lower()), None)
        if not target:
            bot.reply_to(message, "❌ User not found.")
            return
        target_id, target_name = target
        database.mute_user(bot, chat_id, target_id, target_name, seconds)
        bot.reply_to(message, f"✅ Muted {target_name} for {num}{unit}.")

    elif cmd == '/unmute' and is_admin(user_id):
        if not args:
            bot.reply_to(message, "Usage: /unmute @username")
            return
        target_mention = args[0].lstrip('@')
        members = database.get_all_members(chat_id)
        target = next((m for m in members if m[1].lower() == target_mention.lower()), None)
        if not target:
            bot.reply_to(message, "❌ User not found.")
            return
        target_id, target_name = target
        if database.unmute_user(bot, chat_id, target_id):
            bot.reply_to(message, f"✅ Unmuted {target_name}.")
        else:
            bot.reply_to(message, f"❌ {target_name} was not muted.")

    elif cmd == '/uploadtrivia' and is_admin(user_id):
        bot.reply_to(message, "📤 Send me a JSON or CSV file with trivia questions.\n\n"
                              "JSON format: `[{\"category\":\"Gaming\",\"question\":\"...\",\"options\":[\"A\",\"B\",\"C\",\"D\"],\"answer\":\"A\"}]`\n"
                              "CSV format: `category,question,optionA,optionB,optionC,optionD,answer`")
        database.save_json(bot, "upload_state.json", {"user_id": user_id, "chat_id": chat_id, "pending": True})
        return

    elif cmd == '/broadcast' and is_admin(user_id):
        if len(args) < 2:
            bot.reply_to(message, "Usage: /broadcast [time] [message]\n\n"
                                  "Time format: '2024-12-25 08:00' (UTC+2)\n"
                                  "Example: /broadcast 2024-12-25 08:00 Merry Christmas everyone!")
            return
        time_str = args[0] + " " + args[1]
        tz = timezone(timedelta(hours=2))
        try:
            dt = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            send_time = int(dt.timestamp())
        except ValueError:
            bot.reply_to(message, "❌ Invalid time format. Use: YYYY-MM-DD HH:MM")
            return
        if send_time < time.time():
            bot.reply_to(message, "❌ Broadcast time must be in the future.")
            return
        message_text = " ".join(args[2:])
        if not message_text:
            bot.reply_to(message, "❌ Please provide a message.")
            return
        # Global broadcast (send to all groups)
        database.add_broadcast(bot, None, message_text, send_time)
        bot.reply_to(message, f"✅ Global broadcast scheduled for {time_str}.")

    elif cmd == '/addquote' and chat_id == user_id and is_admin(user_id):
        if not args:
            bot.reply_to(message, "Usage: /addquote [quote text]")
            return
        text    = " ".join(args)
        new_id  = database.add_quote(bot, text)
        bot.reply_to(message, f"✅ Quote #{new_id} added!")

    elif cmd == '/listquotes' and is_admin(user_id):
        text, markup = show_quotes_page(chat_id, 1)
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    elif cmd == '/deletequote' and is_admin(user_id):
        if not args or not args[0].isdigit():
            bot.reply_to(message, "Usage: /deletequote [id]")
            return
        if database.delete_quote(bot, int(args[0])):
            bot.reply_to(message, f"✅ Quote #{args[0]} deleted.")
        else:
            bot.reply_to(message, f"❌ Quote #{args[0]} not found.")

    elif cmd == '/editquote' and is_admin(user_id):
        if len(args) < 2 or not args[0].isdigit():
            bot.reply_to(message, "Usage: /editquote [id] [new text]")
            return
        new_text = " ".join(args[1:])
        if database.edit_quote(bot, int(args[0]), new_text):
            bot.reply_to(message, f"✅ Quote #{args[0]} updated.")
        else:
            bot.reply_to(message, f"❌ Quote #{args[0]} not found.")

    elif cmd == '/previewquote' and is_admin(user_id):
        if not args or not args[0].isdigit():
            bot.reply_to(message, "Usage: /previewquote [id]")
            return
        q = database.get_quote(int(args[0]))
        if q:
            bot.reply_to(message, f"📖 *Preview — Quote #{q['id']}*\n\n_{q['text']}_\n\n— *{q['author']}*", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"❌ Quote #{args[0]} not found.")

    elif cmd == '/setschedule' and is_admin(user_id):
        show_schedule_panel(chat_id)

# ---------------------------------------------------------------------------
# STATUS COMMAND
# ---------------------------------------------------------------------------

def handle_status(message):
    chat_id = message.chat.id
    uptime_seconds = int(time.time() - BOT_START_TIME)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    groups = database.get_all_groups()
    total_members = 0
    for gid in groups:
        total_members += len(database.get_all_members(gid))
    data = database.load_json(config.GROUP_DATA_FILE, {})
    total_entries = sum(len(u) for u in data.values())
    status_text = (
        f"🤖 *Bot Status*\n\n"
        f"✅ *Status:* Online\n"
        f"⏱️ *Uptime:* {uptime}\n"
        f"📊 *Groups:* {len(groups)}\n"
        f"👥 *Tracked members:* {total_members}\n"
        f"📦 *Total user entries:* {total_entries}\n"
        f"⏰ *Local time:* {local_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🔄 *Scheduler:* {'✅ Running' if load_scheduler().get('enabled') else '⏸️ Paused'}\n"
        f"📢 *Broadcast checker:* {'✅ Running' if broadcast_checker_thread and broadcast_checker_thread.is_alive() else '❌ Stopped'}\n"
        f"🔗 *GitHub repo:* [za-sora-bot](https://github.com/Gods-Grad1/za-sora-bot)"
    )
    bot.send_message(chat_id, status_text, parse_mode="Markdown", disable_web_page_preview=True)

# ---------------------------------------------------------------------------
# SPIN WHEEL HANDLER
# ---------------------------------------------------------------------------

def handle_spin(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    if database.is_muted(chat_id, user_id):
        bot.reply_to(message, "🔇 You are muted! Wait until your mute expires.")
        return

    data = database.load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = database.get_user(data, chat_str, user_str, username)
    last_spin = u.get("last_spin", 0)
    if time.time() - last_spin < 86400:
        remaining = int((last_spin + 86400 - time.time()) / 60)
        bot.reply_to(message, f"⏳ You already spun today! Come back in {remaining} minutes.")
        return

    import random
    slots = config.WHEEL_SLOTS
    total_weight = sum(slot["weight"] for slot in slots)
    roll = random.randint(1, total_weight)
    cumulative = 0
    result = None
    for slot in slots:
        cumulative += slot["weight"]
        if roll <= cumulative:
            result = slot
            break
    if not result:
        result = slots[0]

    u["last_spin"] = time.time()
    response = "🎰 *WHEEL OF FORTUNE*\n\n"

    if result.get("points"):
        points = result["points"]
        if points > 0:
            u["points"] += points
            u["alltime_points"] += points
            month_key = database._now_month_key()
            year_key = database._now_year_key()
            u["monthly_points"][month_key] = u["monthly_points"].get(month_key, 0) + points
            u["yearly_points"][year_key] = u["yearly_points"].get(year_key, 0) + points
            database.save_json(bot, config.GROUP_DATA_FILE, data)
            response += f"🎉 You won *{points} points*!"
        elif points < 0:
            u["points"] = max(0, u["points"] + points)
            database.save_json(bot, config.GROUP_DATA_FILE, data)
            response += f"💸 You lost *{abs(points)} points*! 😱"
        else:
            response += f"😐 Nothing! Try again tomorrow."
    elif result.get("hint_token"):
        tokens = result["hint_token"]
        u["hint_tokens"] = u.get("hint_tokens", 0) + tokens
        database.save_json(bot, config.GROUP_DATA_FILE, data)
        response += f"💡 You won *{tokens} hint token(s)*!"
    elif result.get("double_xp"):
        duration = result["double_xp"]
        u["double_xp_until"] = time.time() + duration
        database.save_json(bot, config.GROUP_DATA_FILE, data)
        response += f"⚡ You won *Double XP for 1 hour*!"
    elif result.get("bankrupt"):
        u["points"] = max(0, u["points"] - 10)
        database.save_json(bot, config.GROUP_DATA_FILE, data)
        response += f"💸 *BANKRUPT!* You lost 10 points. 😱"
    else:
        response += f"🎁 You won *{result['name']}*!"

    bot.reply_to(message, response, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# (Part 1 ends – continue to Part 2)
