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
import profile_banner
import requests
import json
import base64

# Force no proxy
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'
apihelper.proxy = None

bot = telebot.TeleBot(config.API_TOKEN)
database.set_bot(bot)

BOT_START_TIME = time.time()

# ---------------------------------------------------------------------------
# SCHEDULER STATE (Local file)
# ---------------------------------------------------------------------------

def load_scheduler():
    return database.load_json(config.SCHEDULER_FILE, {
        "enabled": False,
        "interval": 60,
        "game_type": "random",
        "window_start": config.SCHEDULER_WINDOW_START,
        "window_end": config.SCHEDULER_WINDOW_END,
        "last_game": 0,
        "tagall_last": 0,
        "last_morning_date": "",
        "schedule_message_id": None,
    })

def save_scheduler(data):
    database.save_json(bot, config.SCHEDULER_FILE, data)
    
# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def is_admin(user_id):
    return user_id == config.ADMIN_ID

def is_group_admin(chat_id, user_id):
    """Check if a user is an admin of the group."""
    try:
        chat_member = bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ["administrator", "creator"]
    except Exception:
        return False

def is_authorized(chat_id, user_id):
    """Check if user is either THE Admin or a group admin."""
    if is_admin(user_id):
        return True
    if is_group_admin(chat_id, user_id):
        return True
    return False

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

def safe_edit_message(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            return False
        raise

def safe_edit_message_media(chat_id, message_id, media, reply_markup=None):
    try:
        bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media, reply_markup=reply_markup)
        return True
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            return False
        raise

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
        telebot.types.InlineKeyboardButton("⚙️ Admin (Group)", callback_data="help_admin_group"),
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
            "/versus @user — Challenge someone to a duel\n"
            "/guess — Character Quiz (text-based hints)\n"
            "/lightning — Lightning Round (high-risk trivia)"
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
            "/fixtures — Match fixtures (image)\n"
            "/feedback — Send feedback to the Captain"
        ),
        "admin_group": (
            "⚙️ *GROUP ADMIN COMMANDS*\n\n"
            "/tagall — Tag all members\n"
            "/setschedule — Configure auto-game scheduler\n"
            "/setwindow <start> <end> — Set game window (e.g., /setwindow 10 23)\n\n"
            "*Note:* These commands are available to group admins and the Captain."
        ),
    }
    return texts.get(category, "Unknown category.")

def show_help(message):
    chat_id = message.chat.id
    text = "📖 *ZA SORA GAME CLUB — HELP*\n\nChoose a category below:"
    markup = _build_help_menu()
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

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
    header_offset = 1 if ("home" in str(rows[0][home_idx]).lower() or rows[0][0].lower() in ["md", "matchday"]) else 0
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
        bot.send_message(message.chat.id, "📋 *FIXTURES*\n\nChoose how you want to browse:", reply_markup=markup, parse_mode="Markdown")
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
        bot.send_photo(message.chat.id, img, caption=caption, reply_markup=markup, parse_mode="Markdown")
        if hasattr(img, 'close'): img.close()
    else:
        bot.send_message(message.chat.id, "No scores yet!")

# ---------------------------------------------------------------------------
# SHOP
# ---------------------------------------------------------------------------

def show_shop(message):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    for item in config.SHOP_TITLES:
        markup.add(telebot.types.InlineKeyboardButton(f"{item['name']} — {item['cost']} pts", callback_data=f"shop_{item['id']}"))
    bot.send_message(message.chat.id, "🛒 *POINT SHOP*\n\nTitles expire after 30 days. Special items are instant!", reply_markup=markup, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# TAG ALL
# ---------------------------------------------------------------------------

def tag_all_members(message, custom_msg=""):
    if not is_authorized(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Only group admins and the Captain can use this command.")
        return
    sched = load_scheduler()
    now = time.time()
    # Only THE Admin bypasses cooldown
    if not is_admin(message.from_user.id):
        if now - sched.get("tagall_last", 0) < config.TAGALL_COOLDOWN_HOURS * 3600:
            remaining = int((config.TAGALL_COOLDOWN_HOURS * 3600 - (now - sched["tagall_last"])) / 60)
            bot.reply_to(message, f"⏳ Tag all on cooldown. {remaining} minutes remaining.")
            return
    markup = telebot.types.InlineKeyboardMarkup()
    safe_msg = custom_msg[:200].replace('"', "'")
    markup.row(
        telebot.types.InlineKeyboardButton("✅ Confirm Send", callback_data="tagall_confirm"),
        telebot.types.InlineKeyboardButton("❌ Cancel", callback_data="tagall_cancel")
    )
    sched["tagall_pending_msg"] = custom_msg
    sched["tagall_pending_chat"] = message.chat.id
    save_scheduler(sched)
    bot.reply_to(message, f"📢 *Tag All Preview:*\n\n{custom_msg}\n\nThis will tag all {len(database.get_all_members(message.chat.id))} tracked members. Confirm?", reply_markup=markup, parse_mode="Markdown")

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
            chunk = members[i:i+chunk_size]
            mentions = " ".join([f"[{name}](tg://user?id={uid})" for uid, name in chunk])
            bot.send_message(chat_id, mentions, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, full_msg, parse_mode="Markdown")
    sched = load_scheduler()
    sched["tagall_last"] = time.time()
    save_scheduler(sched)

# ---------------------------------------------------------------------------
# QUOTE MANAGEMENT
# ---------------------------------------------------------------------------

def show_quotes_page(chat_id, page=1):
    quotes = database.load_quotes(bot)
    per_page = 10
    total = len(quotes)
    pages = (total + per_page - 1) // per_page
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    chunk = quotes[start:start+per_page]
    text = f"📝 *Quotes — Page {page}/{pages}* ({total} total)\n\n"
    for q in chunk:
        preview = q["text"][:60] + "..." if len(q["text"]) > 60 else q["text"]
        text += f"*#{q['id']}* — {preview}\n_— {q['author']}_\n\n"
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    nav = []
    if page > 1:
        nav.append(telebot.types.InlineKeyboardButton("⬅️ Prev", callback_data=f"qpage_{page-1}"))
    if page < pages:
        nav.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"qpage_{page+1}"))
    if nav:
        markup.row(*nav)
    return text, markup

# ---------------------------------------------------------------------------
# ADMIN PANEL (Captain only)
# ---------------------------------------------------------------------------

def show_admin_panel(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Captain only.")
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
        telebot.types.InlineKeyboardButton("📌 Track Group",           callback_data="admin_trackgroup"),
        telebot.types.InlineKeyboardButton("🚫 Block User",            callback_data="admin_block"),
        telebot.types.InlineKeyboardButton("🔓 Unblock User",          callback_data="admin_unblock"),
        telebot.types.InlineKeyboardButton("📋 Group Schedules",       callback_data="admin_groupschedules"),
    )
    sched = load_scheduler()
    status_icon = "✅" if sched.get("enabled") else "❌"
    bot.send_message(message.chat.id, f"⚙️ *ADMIN PANEL*\n\nAuto-scheduler: {status_icon} {'ON' if sched.get('enabled') else 'OFF'}\nInterval: every {sched.get('interval', 60)} min\nGame type: {sched.get('game_type', 'random').title()}\nActive window: {sched.get('window_start', 10)}:00 — {sched.get('window_end', 23)}:00", reply_markup=markup, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# SCHEDULE PANEL – BUILD & SHOW (with fallback)
# ---------------------------------------------------------------------------

def _build_schedule_panel(chat_id):
    sched = load_scheduler()
    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    toggle_label = "❌ Disable" if sched.get("enabled") else "✅ Enable"
    markup.add(telebot.types.InlineKeyboardButton(toggle_label, callback_data="sched_toggle"))
    markup.add(*[telebot.types.InlineKeyboardButton(f"⏱ {m}min", callback_data=f"sched_interval_{m}") for m in config.SCHEDULE_INTERVALS])
    markup.add(*[telebot.types.InlineKeyboardButton(f"⏰ {s}s limit", callback_data=f"sched_timelimit_{s}") for s in [30, 45, 60, 90, 120]])
    markup.add(
        telebot.types.InlineKeyboardButton("🎮 Character", callback_data="sched_type_character"),
        telebot.types.InlineKeyboardButton("🎬 Year",      callback_data="sched_type_year"),
        telebot.types.InlineKeyboardButton("🖼️ Picture",   callback_data="sched_type_picture"),
        telebot.types.InlineKeyboardButton("❓ Trivia",    callback_data="sched_type_trivia"),
        telebot.types.InlineKeyboardButton("🎲 Random",    callback_data="sched_type_random"),
    )
    markup.add(telebot.types.InlineKeyboardButton("🔙 Back", callback_data="admin_back"))
    status_icon = "✅" if sched.get("enabled") else "❌"
    text = (
        f"📅 *SCHEDULE SETTINGS*\n\n"
        f"Status: {status_icon} {'ON' if sched.get('enabled') else 'OFF'}\n"
        f"Interval: every *{sched.get('interval', 60)} min*\n"
        f"Type: *{sched.get('game_type', 'random').title()}*\n"
        f"Window: *{sched.get('window_start',10)}:00 – {sched.get('window_end',23)}:00* (use /setwindow to change)\n"
        f"⏰ Answer time limit: *{sched.get('answer_time_limit', 60)}s*"
    )
    return text, markup

def show_schedule_panel(chat_id, edit_message_id=None):
    sched = load_scheduler()
    text, markup = _build_schedule_panel(chat_id)

    if edit_message_id:
        try:
            bot.edit_message_text(text, chat_id, edit_message_id, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception as e:
            print(f"Failed to edit schedule panel: {e}")

    msg = bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    sched["schedule_message_id"] = msg.message_id
    save_scheduler(sched)

# ---------------------------------------------------------------------------
# STATS
# ---------------------------------------------------------------------------

def show_stats(chat_id):
    data = database.load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    if chat_str not in data:
        bot.send_message(chat_id, "📊 No stats yet.")
        return
    users = data[chat_str]
    total_games = sum(u.get("games_played", 0) for u in users.values())
    total_pts = sum(u.get("alltime_points", 0) for u in users.values())
    most_active = max(users.values(), key=lambda u: u.get("games_played", 0), default=None)
    top_scorer = max(users.values(), key=lambda u: u.get("alltime_points", 0), default=None)
    best_streak = max(users.values(), key=lambda u: u.get("best_streak", 0), default=None)
    bot.send_message(chat_id, f"📊 *GROUP STATS*\n\n👥 Tracked members: {len(users)}\n🎮 Total games played: {total_games}\n💰 Total points distributed: {total_pts}\n🏃 Most active: {most_active.get('username','?')} ({most_active.get('games_played',0)} games)\n🏆 Top scorer: {top_scorer.get('username','?')} ({top_scorer.get('alltime_points',0)} pts)\n🔥 Best streak: {best_streak.get('username','?')} ({best_streak.get('best_streak',0)} in a row)", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# WEEKLY RECAP & MORNING MESSAGE
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
        msg = f"📊 *WEEKLY RECAP*\n\n🏆 *Monthly Top 3:*\n{top3}\nKeep up the great work, family! 🙏🔥"
        try:
            bot.send_message(group_id, msg, parse_mode="Markdown")
        except Exception as e:
            print(f"Weekly recap failed for {group_id}: {e}")

def send_morning_message(bot):
    try:
        print("🌅 send_morning_message() called")
        now = local_now()
        weekday = now.strftime("%A")
        date = now.strftime("%d %B %Y")
        quote = database.get_random_quote(bot)
        groups = database.get_all_groups()
        print(f"📊 Found {len(groups)} groups")

        for group_id in groups:
            try:
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
                    f"🏆 *Monthly Top 3:*\n{top3}\n\n"
                    f"🎮 *Start your adventure:*\n"
                    f"/start — Welcome to the crew!\n\n"
                    f"Let's have a great day! 🙏🔥"
                )

                members = database.get_all_members(group_id)
                if members:
                    tag_line = "🌱 _Sending love to the whole family_ 🌱\n" + " ".join(
                        [f"[{name}](tg://user?id={uid})" for uid, name in members]
                    )
                    full_msg = msg + "\n\n" + tag_line
                    if len(full_msg) > 4096:
                        truncated = members[:50]
                        tag_line_trunc = "🌱 _Sending love to the whole family_ 🌱\n" + " ".join(
                            [f"[{name}](tg://user?id={uid})" for uid, name in truncated]
                        )
                        if len(members) > 50:
                            tag_line_trunc += f"\n_and {len(members)-50} more..._"
                        full_msg = msg + "\n\n" + tag_line_trunc
                    bot.send_message(group_id, full_msg, parse_mode="Markdown")
                else:
                    bot.send_message(group_id, msg, parse_mode="Markdown")

                print(f"✅ Morning message sent to {group_id}")
            except Exception as e:
                print(f"❌ Morning message failed for {group_id}: {e}")
                database.log_error_to_admin(bot, "Morning Message", e)

        sched = load_scheduler()
        sched["last_morning_date"] = local_now().strftime("%Y-%m-%d")
        save_scheduler(sched)

    except Exception as e:
        print(f"❌ Morning message overall error: {e}")
        database.log_error_to_admin(bot, "Morning Message Overall", e)

# ---------------------------------------------------------------------------
# BROADCAST HELPERS (Always tag)
# ---------------------------------------------------------------------------

def _send_pending_broadcasts(bot):
    pending = database.get_pending_broadcasts()
    print(f"📢 [BROADCAST] Fetched {len(pending)} pending broadcasts.")
    if not pending:
        return
    for broadcast in pending:
        success = True
        sched = load_scheduler()
        if f"broadcast_tagall_{broadcast['send_time']}" not in sched:
            tag_all = True
            sched[f"broadcast_tagall_{broadcast['send_time']}"] = True
            save_scheduler(sched)
        else:
            tag_all = sched[f"broadcast_tagall_{broadcast['send_time']}"]
        
        if tag_all:
            print(f"   → Tag-all enabled for broadcast {broadcast['id']}")
        
        if broadcast["chat_id"] is None:
            groups = database.get_all_groups()
            print(f"   → Global broadcast – groups found: {len(groups)}")
            if not groups:
                print("   ⚠️ No groups tracked – broadcast will stay pending.")
                success = False
            else:
                for gid in groups:
                    try:
                        msg_to_send = broadcast["message"]
                        if tag_all:
                            members = database.get_all_members(gid)
                            if members:
                                tag_line = " ".join([f"[{name}](tg://user?id={uid})" for uid, name in members])
                                if len(msg_to_send) + len(tag_line) + 20 > 4096:
                                    bot.send_message(gid, msg_to_send, parse_mode="Markdown")
                                    bot.send_message(gid, f"📢 *Tagging everyone:*\n{tag_line}", parse_mode="Markdown")
                                else:
                                    bot.send_message(gid, f"{msg_to_send}\n\n📢 *Tagging everyone:*\n{tag_line}", parse_mode="Markdown")
                            else:
                                bot.send_message(gid, msg_to_send, parse_mode="Markdown")
                        else:
                            bot.send_message(gid, msg_to_send, parse_mode="Markdown")
                        print(f"   ✅ Sent to group {gid}")
                    except Exception as e:
                        print(f"   ❌ Failed to send to {gid}: {e}")
                        success = False
        else:
            try:
                bot.send_message(broadcast["chat_id"], broadcast["message"], parse_mode="Markdown")
                print(f"   ✅ Sent to chat {broadcast['chat_id']}")
            except Exception as e:
                print(f"   ❌ Failed: {e}")
                success = False
        
        if success:
            database.mark_broadcast_sent(broadcast["id"])
            if tag_all:
                sched.pop(f"broadcast_tagall_{broadcast['send_time']}", None)
                save_scheduler(sched)
            print(f"   ✅ Broadcast ID {broadcast['id']} marked as sent.")
        else:
            print(f"   ⚠️ Broadcast ID {broadcast['id']} NOT marked – will retry later.")

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
        
        if not new_questions:
            bot.reply_to(message, "❌ No questions found in file.")
            return
        cat = new_questions[0].get('category')
        if not cat:
            bot.reply_to(message, "❌ Missing 'category' field in questions.")
            return
        
        filename = config.TRIVIA_CATEGORY_FILES.get(cat)
        if not filename:
            bot.reply_to(message, f"❌ Unknown category: {cat}. Supported: {', '.join(config.TRIVIA_CATEGORY_FILES.keys())}")
            return
        
        url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/{config.TRIVIA_REMOTE_PATH}/{filename}?ref={config.TRIVIA_BRANCH}"
        headers = {"Authorization": f"token {config.GITHUB_TOKEN}"}
        resp = requests.get(url, headers=headers)
        existing_data = []
        sha = None
        if resp.status_code == 200:
            content_data = resp.json()
            decoded = base64.b64decode(content_data['content']).decode('utf-8')
            existing_data = json.loads(decoded)
            sha = content_data['sha']
        else:
            pass
        
        max_id = max([q['id'] for q in existing_data], default=0)
        for q in new_questions:
            max_id += 1
            q['id'] = max_id
            existing_data.append(q)
        
        payload = {
            "message": f"Add {len(new_questions)} trivia questions to {cat}",
            "content": base64.b64encode(json.dumps(existing_data, indent=2).encode()).decode(),
            "branch": config.TRIVIA_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        put_resp = requests.put(f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/{config.TRIVIA_REMOTE_PATH}/{filename}", headers=headers, json=payload)
        if put_resp.status_code in (200, 201):
            bot.reply_to(message, f"✅ Added {len(new_questions)} questions to {cat} category on the generated branch.")
            database.reload_trivia()
        else:
            bot.reply_to(message, f"❌ Failed to upload: {put_resp.text}")
        
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
        bot.send_message(message.chat.id, welcome, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    if not message.text:
        return

    user_id  = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    chat_id  = message.chat.id

    # Check if user is blocked
    if database.is_blocked(user_id):
        # Silently ignore blocked users
        return

    database.track_member(bot, chat_id, user_id, username)

    if games.check_user_answer(bot, message):
        return

    if not message.text.startswith('/'):
        return

    cmd = message.text.split()[0].split('@')[0].lower()
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if cmd == '/start':
        try:
            gif_url = "https://raw.githubusercontent.com/Gods-Grad1/za-sora-bot/main/images/welcome.gif"
            bot.send_animation(chat_id, gif_url, caption=config.WELCOME_MSG, parse_mode="Markdown")
        except Exception as e:
            print(f"Failed to send welcome GIF: {e}")
            bot.reply_to(message, config.WELCOME_MSG, parse_mode="Markdown")

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

    elif cmd == '/feedback':
        if not args:
            bot.reply_to(message, "📝 Usage: /feedback <your message>")
            return
        feedback_msg = " ".join(args)
        database.add_feedback(user_id, username, feedback_msg)
        bot.reply_to(message, "✅ Your feedback has been sent to the Captain! 🏴‍☠️")
        # Also forward to admin DM
        try:
            bot.send_message(config.ADMIN_ID, f"💬 *NEW FEEDBACK*\n\nFrom: {username} (ID: {user_id})\nChat: {chat_id}\n\n📝 {feedback_msg}", parse_mode="Markdown")
        except Exception:
            pass

    elif cmd == '/status':
        if is_admin(user_id):
            handle_status(message)
        else:
            bot.reply_to(message, "❌ Captain only.")

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

    elif cmd == '/guess':
        games.start_guess_game(bot, chat_id, user_id)

    elif cmd == '/lightning':
        games.start_lightning_round(bot, chat_id, user_id)

    elif cmd == '/hint':
        games.process_hint(bot, message=message)

    elif cmd == '/stop':
        if chat_id in games.active_games:
            # Game ended by stop – DO NOT update scheduler last_game
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
        if not is_authorized(chat_id, user_id):
            bot.reply_to(message, "❌ Only group admins and the Captain can use this command.")
            return
        custom_msg = " ".join(args) if args else "Attention everyone!"
        tag_all_members(message, custom_msg)

    elif cmd == '/clean':
        if not is_admin(user_id):
            bot.reply_to(message, "❌ Captain only.")
            return
        clean_bot_messages(chat_id, message)

    elif cmd == '/admin':
        if is_admin(user_id):
            show_admin_panel(message)
        else:
            bot.reply_to(message, "❌ Captain only.")

    elif cmd == '/testmorning':
        if is_admin(user_id):
            send_morning_message(bot)
            bot.reply_to(message, "✅ Morning message sent (test).")
        else:
            bot.reply_to(message, "❌ Captain only.")

    elif cmd == '/checknow' and is_admin(user_id):
        bot.reply_to(message, "🔄 Manually checking for pending broadcasts...")
        _send_pending_broadcasts(bot)
        bot.reply_to(message, "✅ Broadcast check completed.")
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
        _send_pending_broadcasts(bot)
        bot.reply_to(message, "✅ Force-send completed.")
        return

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
                              "The file should contain questions for a single category.\n"
                              "Use the 'category' field to indicate which category.\n"
                              "Supported formats: JSON or CSV.")
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
            print(f"📅 [BROADCAST] Scheduled: {time_str} -> send_time={send_time} (now={int(time.time())})")
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
        
        tag_all = True
        database.add_broadcast(bot, None, message_text, send_time)
        sched = load_scheduler()
        sched[f"broadcast_tagall_{send_time}"] = tag_all
        save_scheduler(sched)
        
        bot.reply_to(message, f"✅ Global broadcast scheduled for {time_str}.")
        if send_time - time.time() <= 10:
            bot.reply_to(message, "📤 Sending broadcast now (within 10 seconds)...")
            _send_pending_broadcasts(bot)
        return

    elif cmd == '/setwindow' and is_authorized(chat_id, user_id):
        if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
            bot.reply_to(message, "Usage: /setwindow <start_hour> <end_hour>\nExample: /setwindow 10 23")
            return
        start = int(args[0])
        end = int(args[1])
        if start < 0 or start > 23 or end < 1 or end > 24 or start >= end:
            bot.reply_to(message, "❌ Invalid hours. Start must be 0-23, end 1-24, and start must be less than end.")
            return
        sched = load_scheduler()
        sched["window_start"] = start
        sched["window_end"] = end
        save_scheduler(sched)
        msg_id = sched.get("schedule_message_id")
        if msg_id:
            try:
                show_schedule_panel(chat_id, edit_message_id=msg_id)
            except:
                show_schedule_panel(chat_id)
        else:
            show_schedule_panel(chat_id)
        bot.reply_to(message, f"✅ Window set to {start}:00 – {end}:00")

    # --- Debug commands ---
    elif cmd == '/listpending' and is_admin(user_id):
        pending = database.get_pending_broadcasts()
        if not pending:
            bot.reply_to(message, "📭 No pending broadcasts.")
        else:
            lines = [f"ID {b['id']} | chat: {b['chat_id']} | time: {b['send_time']} ({time.ctime(b['send_time'])})" for b in pending]
            bot.reply_to(message, "📋 Pending broadcasts:\n" + "\n".join(lines))

    elif cmd == '/trackgroup' and is_admin(user_id):
        database.track_member(bot, chat_id, user_id, username)
        bot.reply_to(message, "✅ This group is now tracked in the database.")

    elif cmd == '/generateall' and is_admin(user_id):
        bot.reply_to(message, "🖼️ Starting background banner generation for all users...")
        def generate_in_background():
            try:
                profile_banner.pre_generate_all_banners(bot)
                bot.send_message(chat_id, "✅ Banner generation completed for all users.")
            except Exception as e:
                bot.send_message(chat_id, f"❌ Banner generation failed: {e}")
        threading.Thread(target=generate_in_background, daemon=True).start()

    elif cmd == '/setupgenerated' and is_admin(user_id):
        status_msg = bot.reply_to(message, "🔧 Setting up generated branch structure...")
        try:
            themes_data = {
                "weeks": [
                    {"week": 1, "monday": {"character": "Kratos", "category": "Gaming"}, "tuesday": {"character": "Luffy", "category": "Anime"}, "wednesday": {"character": "Terminator", "category": "Movies"}, "thursday": {"character": "Gojo", "category": "General"}, "friday": {"character": "Iron Man", "category": "Technology"}, "saturday": {"character": "Flash", "category": "Sports"}, "sunday": {"character": "Bible Verse", "category": "Bible"}},
                    {"week": 2, "monday": {"character": "Master Chief", "category": "Gaming"}, "tuesday": {"character": "Goku", "category": "Anime"}, "wednesday": {"character": "Thanos", "category": "Movies"}, "thursday": {"character": "Professor X", "category": "General"}, "friday": {"character": "Cyborg", "category": "Technology"}, "saturday": {"character": "Isagi", "category": "Sports"}, "sunday": {"character": "Bible Verse", "category": "Bible"}},
                    {"week": 3, "monday": {"character": "Mario", "category": "Gaming"}, "tuesday": {"character": "Naruto", "category": "Anime"}, "wednesday": {"character": "Gandalf", "category": "Movies"}, "thursday": {"character": "Kakashi", "category": "General"}, "friday": {"character": "Mr. Terrific", "category": "Technology"}, "saturday": {"character": "Hinata", "category": "Sports"}, "sunday": {"character": "Bible Verse", "category": "Bible"}},
                    {"week": 4, "monday": {"character": "Dante", "category": "Gaming"}, "tuesday": {"character": "Ash Ketchum", "category": "Anime"}, "wednesday": {"character": "Timon & Pumbaa", "category": "Movies"}, "thursday": {"character": "Koro-sensei", "category": "General"}, "friday": {"character": "Optimus Prime", "category": "Technology"}, "saturday": {"character": "Tetsuya", "category": "Sports"}, "sunday": {"character": "Bible Verse", "category": "Bible"}}
                ],
                "current_week": 1,
                "last_updated": datetime.datetime.now().isoformat()
            }
            success = database.save_remote_json(config.DAILY_THEMES_FILE, themes_data)
            if success:
                bot.send_message(chat_id, "✅ daily_themes.json created/updated.")
            else:
                bot.send_message(chat_id, "❌ Failed to create daily_themes.json – check GITHUB_TOKEN and branch permissions.")

            from github_uploader import upload_image_to_github
            placeholder = b""
            folders = ["themes", "scrambled"]
            for folder in folders:
                try:
                    result = upload_image_to_github(bot, placeholder, ".gitkeep", folder, branch=config.TRIVIA_BRANCH)
                    if result:
                        bot.send_message(chat_id, f"✅ Folder images/{folder}/ created (with .gitkeep).")
                    else:
                        bot.send_message(chat_id, f"❌ Failed to create images/{folder}/ – check GITHUB_TOKEN and branch permissions.")
                except Exception as e:
                    bot.send_message(chat_id, f"❌ Error creating images/{folder}/: {e}")

            bot.send_message(chat_id, "✅ Setup complete. Check your generated branch on GitHub to confirm.")
        except Exception as e:
            bot.send_message(chat_id, f"❌ Setup failed: {e}")

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
        q = database.get_quote(bot, int(args[0]))
        if q:
            bot.reply_to(message, f"📖 *Preview — Quote #{q['id']}*\n\n_{q['text']}_\n\n— *{q['author']}*", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"❌ Quote #{args[0]} not found.")

    elif cmd == '/setschedule' and is_authorized(chat_id, user_id):
        show_schedule_panel(chat_id)

    elif cmd == '/block' and is_admin(user_id):
        if not args or not args[0].isdigit():
            bot.reply_to(message, "Usage: /block <user_id> [reason]")
            return
        target_id = int(args[0])
        reason = " ".join(args[1:]) if len(args) > 1 else "No reason provided"
        if database.block_user(target_id):
            bot.reply_to(message, f"✅ User `{target_id}` has been blocked.\n📝 Reason: {reason}")
        else:
            bot.reply_to(message, f"❌ User `{target_id}` is already blocked.")

    elif cmd == '/unblock' and is_admin(user_id):
        if not args or not args[0].isdigit():
            bot.reply_to(message, "Usage: /unblock <user_id>")
            return
        target_id = int(args[0])
        if database.unblock_user(target_id):
            bot.reply_to(message, f"✅ User `{target_id}` has been unblocked.")
        else:
            bot.reply_to(message, f"❌ User `{target_id}` was not blocked.")

    elif cmd == '/groupschedules' and is_admin(user_id):
        schedules = database.load_group_schedules()
        if not schedules:
            bot.reply_to(message, "📋 No group-specific schedules set. All groups use the global schedule.")
            return
        text = "📋 *GROUP SCHEDULES*\n\n"
        for gid, settings in schedules.items():
            text += f"📊 *Group {gid}:*\n"
            text += f"   Enabled: {'✅' if settings.get('enabled') else '❌'}\n"
            text += f"   Interval: {settings.get('interval', 60)} min\n"
            text += f"   Type: {settings.get('game_type', 'random').title()}\n"
            text += f"   Window: {settings.get('window_start', 10)}:00 – {settings.get('window_end', 23)}:00\n\n"
        bot.reply_to(message, text, parse_mode="Markdown")

    elif cmd == '/setschedule_group' and is_admin(user_id):
        if len(args) < 4:
            bot.reply_to(message, "Usage: /setschedule_group <group_id> <interval> <game_type> <enabled>\n\n"
                                  "Example: /setschedule_group -100123456 60 trivia true\n"
                                  "Game types: character, year, picture, trivia, random\n"
                                  "Enabled: true or false")
            return
        group_id = int(args[0])
        interval = int(args[1])
        game_type = args[2].lower()
        enabled = args[3].lower() in ["true", "yes", "1", "on"]
        
        if game_type not in ["character", "year", "picture", "trivia", "random"]:
            bot.reply_to(message, f"❌ Invalid game type. Choose from: character, year, picture, trivia, random")
            return
        
        settings = {
            "enabled": enabled,
            "interval": interval,
            "game_type": game_type,
            "window_start": 10,
            "window_end": 23
        }
        database.set_group_schedule(group_id, settings)
        bot.reply_to(message, f"✅ Group {group_id} schedule updated!")

    elif cmd == '/remove_schedule_group' and is_admin(user_id):
        if not args or not args[0].isdigit():
            bot.reply_to(message, "Usage: /remove_schedule_group <group_id>")
            return
        group_id = int(args[0])
        if database.remove_group_schedule(group_id):
            bot.reply_to(message, f"✅ Group {group_id} schedule removed. Now using global schedule.")
        else:
            bot.reply_to(message, f"❌ Group {group_id} had no custom schedule.")

# ---------------------------------------------------------------------------
# CLEAN BOT MESSAGES – ONLY DELETES THIS BOT'S OWN MESSAGES
# ---------------------------------------------------------------------------

def clean_bot_messages(chat_id, trigger_message):
    """Delete only this bot's own messages, except important ones."""
    # Check if bot has delete permission
    try:
        bot_member = bot.get_chat_member(chat_id, bot.get_me().id)
        can_delete = bot_member.can_delete_messages or bot_member.status == "creator"
    except Exception:
        can_delete = False
    
    if not can_delete:
        bot.reply_to(trigger_message, "❌ I don't have permission to delete messages!\n\n"
                                      "Please promote me to Admin with 'Delete Messages' permission.")
        return
    
    bot.reply_to(trigger_message, "🧹 Cleaning up my messages...")
    
    # Get the last 100 messages
    try:
        messages = bot.get_chat_history(chat_id, limit=100)
    except Exception as e:
        bot.reply_to(trigger_message, f"❌ Could not fetch messages: {e}")
        return
    
    bot_id = bot.get_me().id  # Get this bot's own ID
    deleted_count = 0
    kept_count = 0
    
    # Messages to KEEP (do not delete)
    keep_patterns = [
        "BROADCAST", "📢 *ANNOUNCEMENT*", "📢 *Tagging everyone*",
        "🌅 send_morning_message", "☀️ *Good Morning, Family!*",
        "📊 *WEEKLY RECAP*", "📅 *SUNDAY STANDINGS*",
        "🏆 *Monthly Results*", "🎊 *Yearly Champion*",
        "📊 *GROUP STATS*", "🏆 *ZA SORA ZENITH LEAGUE STANDINGS*",
        "📋 *FIXTURES*", "⚔️ *MATCH OVER*", "⚔️ *MATCH DRAWN*",
        "🏳️ *", "🤝 *MATCH DRAWN*",
        "/mystats", "/viewstats", "Your Stats", "Stats",
        "📖 *ZA SORA GAME CLUB — HELP*",
        "⚙️ *ADMIN PANEL*", "📅 *SCHEDULE SETTINGS*",
        "🛒 *POINT SHOP*", "🏆 *Leaderboard*",
        "📋 *FIXTURES*",
        "🎰 *WHEEL OF FORTUNE*",
        "✅ *Challenge Accepted*", "⚔️ *VERSUS CHALLENGE*",
        "💰 *BET RESULTS*"
    ]
    
    for msg in messages:
        # Skip the trigger message itself
        if msg.message_id == trigger_message.message_id:
            continue
        
        # Only delete messages sent by THIS bot (not other bots or users)
        if not msg.from_user or msg.from_user.id != bot_id:
            continue
        
        text = msg.text or msg.caption or ""
        should_keep = False
        
        # Check if message should be kept
        for pattern in keep_patterns:
            if pattern in text or pattern.lower() in text.lower():
                should_keep = True
                break
        
        # Also keep messages that are results (contain "won" or "wins" or "Final score")
        if "won" in text.lower() or "wins" in text.lower() or "final score" in text.lower():
            should_keep = True
        
        if not should_keep:
            try:
                bot.delete_message(chat_id, msg.message_id)
                deleted_count += 1
                time.sleep(0.05)  # Avoid rate limits
            except Exception as e:
                print(f"Failed to delete message {msg.message_id}: {e}")
        else:
            kept_count += 1
    
    bot.reply_to(trigger_message, f"🧹 *Cleanup Complete*\n\n🗑️ Deleted: {deleted_count} of my messages\n📌 Kept: {kept_count} important messages", parse_mode="Markdown")

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

    broadcast_alive = broadcast_checker_thread and broadcast_checker_thread.is_alive()
    scheduler_alive = scheduler_thread and scheduler_thread.is_alive()
    pending = database.get_pending_broadcasts()
    blocklist = database.load_blocklist()

    status_text = (
        f"🤖 *Bot Status*\n\n"
        f"✅ *Status:* Online\n"
        f"⏱️ *Uptime:* {uptime}\n"
        f"📊 *Groups:* {len(groups)}\n"
        f"👥 *Tracked members:* {total_members}\n"
        f"📦 *Total user entries:* {total_entries}\n"
        f"🚫 *Blocked users:* {len(blocklist)}\n"
        f"⏰ *Local time:* {local_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🔄 *Scheduler:* {'✅ Running' if scheduler_alive else '❌ Stopped'}\n"
        f"📢 *Broadcast checker:* {'✅ Running' if broadcast_alive else '❌ Stopped'}\n"
        f"📨 *Pending broadcasts:* {len(pending)}\n"
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

    if database.is_muted(bot, chat_id, user_id):
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
# CALLBACK HANDLER (UPDATED)
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: True)
def handle_all_callbacks(call):
    data    = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    try:
        # --- NEW: GUESS HINT callback ---
        if data.startswith("guess_hint_"):
            games.handle_guess_hint(bot, call)
            return

        # --- NEW: LIGHTNING ANSWER callback ---
        if data.startswith("lightning_ans_"):
            games.handle_lightning_answer(bot, call)
            return

        if data.startswith("charcat_"):
            cat = data.replace("charcat_", "")
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            games.start_character_game(bot, chat_id, category=cat, user_id=user_id)
            return

        if data.startswith("yearcat_"):
            cat = data.replace("yearcat_", "")
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            games.start_year_game(bot, chat_id, category=cat, user_id=user_id)
            return

        if data.startswith("triviacat_"):
            cat = data.replace("triviacat_", "")
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            games.start_trivia_game(bot, chat_id, category=cat, user_id=user_id)
            return

        if any(data.startswith(p) for p in ["trivia_", "year_ans_", "vs_", "vsbet_", "vsans_", "daily_", "hint_", "stopgame_", "nextgame_"]):
            games.handle_game_callback(bot, call)
            return

        if data == "admin_force_start" and is_admin(user_id):
            pending = games.pending_admin_actions.pop(chat_id, None)
            if pending:
                if chat_id in games.active_games:
                    del games.active_games[chat_id]
                if chat_id in games.versus_games:
                    del games.versus_games[chat_id]
                gtype = pending['type']
                cat = pending.get('category')
                if gtype == 'character':
                    games.start_character_game(bot, chat_id, category=cat)
                elif gtype == 'year':
                    games.start_year_game(bot, chat_id, category=cat)
                elif gtype == 'picture':
                    games.start_picture_game(bot, chat_id, category=cat)
                elif gtype == 'trivia':
                    games.start_trivia_game(bot, chat_id, category=cat)
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            return

        if data == "admin_cancel_start":
            games.pending_admin_actions.pop(chat_id, None)
            bot.answer_callback_query(call.id, "Cancelled.")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            return

        if data.startswith("lb_"):
            if data == "lb_nop":
                bot.answer_callback_query(call.id)
                return
            parts = data.split("_")
            if len(parts) == 3:
                _, mode, page_str = parts
                page = int(page_str)
            else:
                mode = parts[1] if len(parts) > 1 else "monthly"
                page = 1
            all_entries = database.get_leaderboard(chat_id, mode=mode, top_n=100)
            total_pages = (len(all_entries) + 9) // 10
            if page < 1:
                page = 1
            if page > total_pages and total_pages > 0:
                page = total_pages
            img = graphics.build_leaderboard_image(chat_id, mode, page)
            if img:
                caption = f"🏆 *Leaderboard — {mode.upper()}* (Page {page}/{total_pages})"
                markup = _build_leaderboard_markup(mode, page, total_pages)
                safe_edit_message_media(chat_id, call.message.message_id,
                                       InputMediaPhoto(img, caption=caption, parse_mode="Markdown"),
                                       reply_markup=markup)
                if hasattr(img, 'close'): img.close()
            else:
                safe_edit_message(chat_id, call.message.message_id, "No scores yet!")
            bot.answer_callback_query(call.id)
            return

        if data.startswith("shop_"):
            item_id  = data.replace("shop_", "")
            username = call.from_user.username or call.from_user.first_name
            ok, msg  = database.purchase_item(bot, chat_id, user_id, username, item_id)
            bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        if data.startswith("admin_") and is_admin(user_id):
            action = data.replace("admin_", "")
            if action == "startchar":
                bot.answer_callback_query(call.id)
                games.start_character_game(bot, chat_id)
            elif action == "startyear":
                bot.answer_callback_query(call.id)
                games.start_year_game(bot, chat_id)
            elif action == "startpicture":
                bot.answer_callback_query(call.id)
                games.start_picture_game(bot, chat_id)
            elif action == "starttrivia":
                bot.answer_callback_query(call.id)
                games.start_trivia_game(bot, chat_id)
            elif action == "schedule":
                bot.answer_callback_query(call.id)
                show_schedule_panel(chat_id)
            elif action == "tagall":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "📢 Use `/tagall Your message here` to tag all members.", parse_mode="Markdown")
            elif action == "leaderboard":
                bot.answer_callback_query(call.id)
                show_leaderboard(call.message)
            elif action == "rebuild":
                bot.answer_callback_query(call.id, "🔄 Rebuilding cache...")
                threading.Thread(target=graphics.clear_and_rebuild_disk_cache, args=(bot,), daemon=True).start()
                bot.send_message(chat_id, "🔄 Cache rebuild started in background.")
            elif action == "stats":
                bot.answer_callback_query(call.id)
                show_stats(chat_id)
            elif action == "mute":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "🔇 Use `/mute @username 1h` to mute a user.", parse_mode="Markdown")
            elif action == "broadcast":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "📢 Use `/broadcast YYYY-MM-DD HH:MM Your message here`", parse_mode="Markdown")
            elif action == "checkimages":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "🔍 Checking for missing images...")
                notify_missing_images()
                bot.send_message(chat_id, "✅ Check complete. Admin has been notified.")
            elif action == "trackgroup":
                bot.answer_callback_query(call.id)
                database.track_member(bot, chat_id, user_id, username)
                bot.send_message(chat_id, "✅ This group is now tracked in the database.")
            elif action == "block":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "🚫 Use `/block <user_id> [reason]` to block a user.", parse_mode="Markdown")
            elif action == "unblock":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "🔓 Use `/unblock <user_id>` to unblock a user.", parse_mode="Markdown")
            elif action == "groupschedules":
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, "📋 Use `/groupschedules` to list all group schedules.", parse_mode="Markdown")
            elif action == "back":
                sched = load_scheduler()
                msg_id = sched.get("schedule_message_id")
                if msg_id:
                    try:
                        bot.delete_message(chat_id, msg_id)
                        sched["schedule_message_id"] = None
                        save_scheduler(sched)
                    except Exception:
                        pass
                from types import SimpleNamespace
                dummy_msg = SimpleNamespace(chat=SimpleNamespace(id=chat_id), from_user=SimpleNamespace(id=user_id))
                show_admin_panel(dummy_msg)
            return

        # -------------------------------------------------------------------
        # SCHEDULER SETTINGS – Edit in place with fallback
        # -------------------------------------------------------------------
        if data.startswith("sched_") and is_authorized(chat_id, user_id):
            sched  = load_scheduler()
            action = data.replace("sched_", "")
            if action == "toggle":
                sched["enabled"] = not sched.get("enabled", False)
                save_scheduler(sched)
                status = "enabled ✅" if sched["enabled"] else "disabled ❌"
                bot.answer_callback_query(call.id, f"Scheduler {status}", show_alert=True)
            elif action.startswith("interval_"):
                sched["interval"] = int(action.replace("interval_", ""))
                save_scheduler(sched)
                bot.answer_callback_query(call.id, f"Interval set to {sched['interval']} min", show_alert=True)
            elif action.startswith("type_"):
                sched["game_type"] = action.replace("type_", "")
                save_scheduler(sched)
                bot.answer_callback_query(call.id, f"Game type: {sched['game_type'].title()}", show_alert=True)
            elif action.startswith("timelimit_"):
                sched["answer_time_limit"] = int(action.replace("timelimit_", ""))
                save_scheduler(sched)
                bot.answer_callback_query(call.id, f"Time limit set to {sched['answer_time_limit']}s", show_alert=True)

            msg_id = sched.get("schedule_message_id")
            if msg_id:
                try:
                    show_schedule_panel(chat_id, edit_message_id=msg_id)
                except Exception as e:
                    print(f"Editing stored msg {msg_id} failed: {e}")
                    show_schedule_panel(chat_id, edit_message_id=call.message.message_id)
            else:
                show_schedule_panel(chat_id, edit_message_id=call.message.message_id)
            return

        if data == "tagall_confirm" and is_authorized(chat_id, user_id):
            sched    = load_scheduler()
            msg      = sched.get("tagall_pending_msg", "")
            send_chat = sched.get("tagall_pending_chat", chat_id)
            bot.answer_callback_query(call.id)
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            _do_tag_all(send_chat, msg)
            return

        if data == "tagall_cancel":
            bot.answer_callback_query(call.id, "Cancelled.")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            return

        if data.startswith("qpage_") and is_admin(user_id):
            page = int(data.replace("qpage_", ""))
            text, markup = show_quotes_page(chat_id, page)
            safe_edit_message(chat_id, call.message.message_id, text, reply_markup=markup)
            bot.answer_callback_query(call.id)
            return

        # ── Help menu ──────────────────────────────────────────────────────
        if data == "help_main":
            text = "📖 *ZA SORA GAME CLUB — HELP*\n\nChoose a category below:"
            markup = _build_help_menu()
            bot.edit_message_text(text, chat_id, call.message.message_id,
                                  reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data.startswith("help_"):
            category = data.replace("help_", "")
            text = _get_help_text(category)
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton("🔙 Back", callback_data="help_main"))
            bot.edit_message_text(text, chat_id, call.message.message_id,
                                  reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data == "fix_back":
            text = "📋 *FIXTURES*\n\nChoose how you want to browse:"
            markup, _ = _build_fixtures_menu_markup(
                database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
            )
            safe_edit_message(chat_id, call.message.message_id, text,
                              reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data == "fix_md_menu":
            rows = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
            if not rows or len(rows) <= 1:
                bot.answer_callback_query(call.id, "No fixtures available.", show_alert=True)
                return

            home_idx, away_idx, _, _, _ = graphics.detect_fixtures_columns(rows)
            header_offset = 1 if ("home" in str(rows[0][home_idx]).lower() or rows[0][0].lower() in ["md", "matchday"]) else 0

            seen = set()
            matchdays = []
            for row in rows[header_offset:]:
                md = row[0].strip() if row else ""
                if md and md not in seen:
                    seen.add(md)
                    matchdays.append(md)

            if not matchdays:
                bot.answer_callback_query(call.id, "No matchdays found.", show_alert=True)
                return

            import re as _re
            def _sort_md(md):
                nums = _re.findall(r'\d+', md)
                return (0, int(nums[0])) if nums else (1, md.lower())
            matchdays = sorted(matchdays, key=_sort_md)

            markup = telebot.types.InlineKeyboardMarkup(row_width=3)
            markup.add(*[
                telebot.types.InlineKeyboardButton(
                    f"MD {md}" if md.isdigit() else md,
                    callback_data=f"fix_md_{md}"
                )
                for md in matchdays
            ])
            markup.add(telebot.types.InlineKeyboardButton("🔙 Back", callback_data="fix_back"))

            text = "📅 *SELECT MATCHDAY:*\n\nTap a matchday to see all fixtures:"
            safe_edit_message(chat_id, call.message.message_id, text,
                              reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data.startswith("fix_md_") and data != "fix_md_menu":
            matchday = data[len("fix_md_"):]
            rows     = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
            img      = graphics.generate_matchday_image(bot, rows, matchday)
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            if img:
                bot.send_photo(
                    chat_id, img,
                    caption=f"📅 *Matchday {matchday} — All Fixtures*",
                    parse_mode="Markdown"
                )
                if hasattr(img, 'close'): img.close()
            else:
                bot.send_message(chat_id, f"❌ No fixtures found for Matchday {matchday}.")
            bot.answer_callback_query(call.id)
            return

        if data == "fix_pl_menu":
            rows = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
            if not rows or len(rows) <= 1:
                bot.answer_callback_query(call.id, "No fixtures available.", show_alert=True)
                return
            _, teams = _build_fixtures_menu_markup(rows)
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(*[
                telebot.types.InlineKeyboardButton(t, callback_data=f"fix_pl_{t}")
                for t in teams
            ])
            markup.add(telebot.types.InlineKeyboardButton("🔙 Back", callback_data="fix_back"))
            text = "📋 *SELECT A PLAYER:*\n\nTap a player to view their fixtures:"
            safe_edit_message(chat_id, call.message.message_id, text,
                              reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data.startswith("fix_pl_") and data != "fix_pl_menu":
            player = data[len("fix_pl_"):]
            markup = telebot.types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                telebot.types.InlineKeyboardButton("🏠 Home", callback_data=f"fix_ctx_{player}_home"),
                telebot.types.InlineKeyboardButton("✈️ Away", callback_data=f"fix_ctx_{player}_away"),
                telebot.types.InlineKeyboardButton("🌍 All",  callback_data=f"fix_ctx_{player}_all"),
                telebot.types.InlineKeyboardButton("🔙 Back", callback_data="fix_pl_menu"),
            )
            text = f"🏟️ *{player.upper()} — SELECT MATCH TYPE:*"
            safe_edit_message(chat_id, call.message.message_id, text,
                              reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data.startswith("fix_ctx_"):
            remainder      = data[len("fix_ctx_"):]
            parts          = remainder.rsplit("_", 1)
            player, context = parts[0], parts[1]
            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                telebot.types.InlineKeyboardButton("📅 Upcoming",  callback_data=f"fix_v_{player}_{context}_upcoming"),
                telebot.types.InlineKeyboardButton("✅ Completed", callback_data=f"fix_v_{player}_{context}_completed"),
                telebot.types.InlineKeyboardButton("🔙 Back", callback_data=f"fix_pl_{player}"),
            )
            text = f"📊 *{player.upper()} — SELECT STATUS:*"
            safe_edit_message(chat_id, call.message.message_id, text,
                              reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
            return

        if data.startswith("fix_v_"):
            remainder        = data[len("fix_v_"):]
            parts            = remainder.rsplit("_", 2)
            player, context, status = parts[0], parts[1], parts[2]
            _serve_fixtures_page(chat_id, call.message.message_id, player, context, status, 1)
            bot.answer_callback_query(call.id)
            return

        if data.startswith("fix_pg_"):
            remainder            = data[len("fix_pg_"):]
            parts                = remainder.rsplit("_", 3)
            player, context, status, page = parts[0], parts[1], parts[2], int(parts[3])
            _serve_fixtures_page(chat_id, call.message.message_id, player, context, status, page)
            bot.answer_callback_query(call.id)
            return

    except Exception as e:
        print(f"Callback error: {e}")
        database.log_error_to_admin(bot, "Callback Handler", e)

def _serve_fixtures_page(chat_id, message_id, player, context, status, page):
    rows = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)
    img  = graphics.generate_fixtures_image(bot, rows, status, player, context, page)
    if not img:
        bot.send_message(chat_id, f"❌ No {status} matches found for {player.upper()} ({context.upper()}).")
        return
    try:
        markup   = telebot.types.InlineKeyboardMarkup()
        nav_btns = []
        prev_img = graphics.generate_fixtures_image(bot, rows, status, player, context, page - 1)
        next_img = graphics.generate_fixtures_image(bot, rows, status, player, context, page + 1)
        if prev_img:
            nav_btns.append(telebot.types.InlineKeyboardButton("⬅️ Prev", callback_data=f"fix_pg_{player}_{context}_{status}_{page-1}"))
            prev_img.close()
        if next_img:
            nav_btns.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"fix_pg_{player}_{context}_{status}_{page+1}"))
            next_img.close()
        if nav_btns:
            markup.row(*nav_btns)
        caption = f"📋 *{status.upper()} MATCHES*\n👤 {player.upper()} | 🏟️ {context.upper()} | 📄 Page {page}"
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
        bot.send_photo(chat_id, img, caption=caption, reply_markup=markup, parse_mode="Markdown")
    finally:
        if hasattr(img, 'close'):
            img.close()

# ---------------------------------------------------------------------------
# BROADCAST CHECKER
# ---------------------------------------------------------------------------

broadcast_checker_thread = None

def broadcast_checker():
    try:
        print("📢 Broadcast checker thread started!")
        while True:
            print("⏳ Broadcast check loop tick")
            try:
                _send_pending_broadcasts(bot)
            except Exception as e:
                print(f"❌ [BROADCAST] Checker error: {e}")
                database.log_error_to_admin(bot, "Broadcast Checker", e)
            time.sleep(5)
    except Exception as fatal:
        print(f"💥 Broadcast checker FATAL: {fatal}")
        database.log_error_to_admin(bot, "Broadcast Checker FATAL", fatal)

# ---------------------------------------------------------------------------
# BACKGROUND SCHEDULER
# ---------------------------------------------------------------------------

scheduler_thread = None

def background_scheduler():
    try:
        import random
        print("⏰ Scheduler thread started!")
        while True:
            try:
                now   = local_now()
                hour  = now.hour
                minute = now.minute

                sched = load_scheduler()

                if hour == config.MORNING_MSG_HOUR and minute == config.MORNING_MSG_MIN:
                    print("🌅 Sending morning message...")
                    send_morning_message(bot)
                    time.sleep(61)

                if now.weekday() == 0 and hour == 9 and minute == 0:
                    print("📊 Sending weekly recap...")
                    send_weekly_recap(bot)
                    time.sleep(61)

                if hour == config.DAILY_CHALLENGE_HOUR and minute == config.DAILY_CHALLENGE_MIN:
                    games.post_daily_challenge(bot)
                    time.sleep(61)

                if now.weekday() == 6 and hour == 0 and minute == 0:
                    graphics.clear_and_rebuild_disk_cache(bot)
                    time.sleep(61)

                if now.weekday() == 6 and hour == 12 and minute == 0:
                    groups  = database.get_all_groups()
                    img_data = graphics.generate_table_image(bot)
                    if img_data:
                        for g_id in groups:
                            try:
                                img_data.seek(0)
                                bot.send_photo(g_id, img_data, caption="📅 *SUNDAY STANDINGS*", parse_mode="Markdown")
                            except Exception:
                                pass
                        if hasattr(img_data, 'close'): img_data.close()
                    time.sleep(61)

                if hour == 0 and minute == 1:
                    database.check_and_run_monthly_reset(bot)
                    database.check_and_run_yearly_reset(bot)
                    time.sleep(61)

                if sched.get("enabled"):
                    # Check for per-group schedule override
                    groups = database.get_all_groups()
                    
                    for g_id in groups:
                        # Check if this group has custom schedule
                        group_sched = database.get_group_schedule(g_id)
                        
                        if group_sched:
                            window_start = group_sched.get("window_start", config.SCHEDULER_WINDOW_START)
                            window_end   = group_sched.get("window_end", config.SCHEDULER_WINDOW_END)
                            in_window    = window_start <= hour < window_end
                            interval_sec = group_sched.get("interval", 60) * 60
                            game_type    = group_sched.get("game_type", "random")
                            enabled      = group_sched.get("enabled", True)
                            
                            if not enabled:
                                continue
                        else:
                            # Use global schedule
                            window_start = sched.get("window_start", config.SCHEDULER_WINDOW_START)
                            window_end   = sched.get("window_end", config.SCHEDULER_WINDOW_END)
                            in_window    = window_start <= hour < window_end
                            interval_sec = sched.get("interval", 60) * 60
                            game_type    = sched.get("game_type", "random")
                        
                        last_game    = sched.get("last_game", 0)
                        now_ts       = time.time()
                        
                        if in_window and (now_ts - last_game) >= interval_sec:
                            print(f"⏳ Scheduler: group={g_id}, in_window={in_window}, diff={now_ts - last_game}, interval={interval_sec}")
                            
                            if game_type == "random":
                                game_type = random.choice(["character", "year", "picture", "trivia"])
                            
                            if games._is_game_active(g_id) or g_id in games.versus_games:
                                continue
                            
                            started = False
                            if game_type == "character":
                                games.start_character_game(bot, g_id)
                                started = True
                            elif game_type == "year":
                                games.start_year_game(bot, g_id)
                                started = True
                            elif game_type == "picture":
                                games.start_picture_game(bot, g_id)
                                started = True
                            elif game_type == "trivia":
                                games.start_trivia_game(bot, g_id)
                                started = True
                            
                            if started:
                                print(f"✅ Started {game_type} game in group {g_id}")
                            else:
                                print(f"ℹ️ No game started in group {g_id} (active game present)")

            except Exception as e:
                print(f"Scheduler error: {e}")
                database.log_error_to_admin(bot, "Scheduler", e)

            time.sleep(30)
    except Exception as fatal:
        print(f"💥 Scheduler FATAL: {fatal}")
        database.log_error_to_admin(bot, "Scheduler FATAL", fatal)

# ---------------------------------------------------------------------------
# THREAD SUPERVISOR
# ---------------------------------------------------------------------------

def start_broadcast_checker():
    global broadcast_checker_thread
    if broadcast_checker_thread and broadcast_checker_thread.is_alive():
        return
    print("🔄 Starting broadcast checker thread...")
    broadcast_checker_thread = threading.Thread(target=broadcast_checker, daemon=True)
    broadcast_checker_thread.start()

def start_scheduler():
    global scheduler_thread
    if scheduler_thread and scheduler_thread.is_alive():
        return
    print("🔄 Starting scheduler thread...")
    scheduler_thread = threading.Thread(target=background_scheduler, daemon=True)
    scheduler_thread.start()

def thread_supervisor():
    while True:
        try:
            if not broadcast_checker_thread or not broadcast_checker_thread.is_alive():
                print("⚠️ Broadcast checker thread is dead – restarting...")
                start_broadcast_checker()
                try:
                    bot.send_message(config.ADMIN_ID, "⚠️ Broadcast checker thread restarted.", parse_mode=None)
                except:
                    pass

            if not scheduler_thread or not scheduler_thread.is_alive():
                print("⚠️ Scheduler thread is dead – restarting...")
                start_scheduler()
                try:
                    bot.send_message(config.ADMIN_ID, "⚠️ Scheduler thread restarted.", parse_mode=None)
                except:
                    pass

        except Exception as e:
            print(f"Supervisor error: {e}")

        time.sleep(30)

# ---------------------------------------------------------------------------
# MISSING IMAGES NOTIFICATION
# ---------------------------------------------------------------------------

def notify_missing_images():
    import re

    def to_filename(name):
        return re.sub(r'[^a-zA-Z0-9._-]', '_', name).strip('_')

    def find_github(name, folder):
        safe_name = to_filename(name)
        if folder == config.LOCAL_CHAR_IMAGES_DIR:
            remote_folder = "characters"
        elif folder == config.LOCAL_MEDIA_IMAGES_DIR:
            remote_folder = "media"
        else:
            remote_folder = folder
        import requests
        url = f"{config.GITHUB_RAW_BASE_URL}{remote_folder}/{safe_name}.jpg"
        try:
            r = requests.head(url, timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    char_folder  = config.LOCAL_CHAR_IMAGES_DIR
    media_folder = config.LOCAL_MEDIA_IMAGES_DIR

    char_dbs = {
        "🌸 Anime":   config.CHAR_ANIME_DB,
        "🦸 DC":      config.CHAR_DC_DB,
        "⚡ Marvel":  config.CHAR_MARVEL_DB,
        "🎮 Gaming":  config.CHAR_GAMING_DB,
    }
    media_dbs = {
        "🎬 Movies":       config.MEDIA_DB,
        "📺 Anime Series": config.ANIME_SERIES_DB,
        "🎥 Anime Films":  config.ANIME_FILMS_DB,
        "🎨 Animation":    config.ANIMATION_DB,
    }

    missing_chars = {}
    missing_media = {}

    for cat, path in char_dbs.items():
        data = database.load_json(path, []) if os.path.exists(path) else []
        if isinstance(data, list):
            missing = [c['name'] for c in data if not find_github(c.get('name', ''), char_folder)]
            if missing:
                missing_chars[cat] = missing

    for cat, path in media_dbs.items():
        data = database.load_json(path, []) if os.path.exists(path) else []
        if isinstance(data, list):
            missing = [m['title'] for m in data if not find_github(m.get('title', ''), media_folder)]
            if missing:
                missing_media[cat] = missing

    total = sum(len(v) for v in missing_chars.values()) + sum(len(v) for v in missing_media.values())

    if total == 0:
        try:
            bot.send_message(config.ADMIN_ID,
                "✅ All images found on GitHub! No missing files.",
                parse_mode=None)
        except Exception:
            pass
        return

    msg_lines = []
    msg_lines.append(f"📁 Missing Images on GitHub — {total} total")
    msg_lines.append("(Bot will fall back to original URLs for these)")
    msg_lines.append("")

    if missing_chars:
        msg_lines.append("CHARACTERS:")
        for cat, names in missing_chars.items():
            msg_lines.append(f"\n{cat} ({len(names)} missing):")
            for name in names:
                msg_lines.append(f"  • {to_filename(name)}.jpg")

    if missing_media:
        msg_lines.append("\nMEDIA:")
        for cat, titles in missing_media.items():
            msg_lines.append(f"\n{cat} ({len(titles)} missing):")
            for title in titles:
                msg_lines.append(f"  • {to_filename(title)}.jpg")

    full_msg = "\n".join(msg_lines)
    chunk_size = 3500
    chunks = []
    lines = full_msg.splitlines(keepends=True)
    current = ""
    for line in lines:
        if len(current) + len(line) > chunk_size:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        try:
            prefix = f"(Part {i+1}/{len(chunks)})\n" if len(chunks) > 1 else ""
            bot.send_message(config.ADMIN_ID, prefix + chunk, parse_mode=None)
        except Exception as e:
            print(f"Missing images notification failed for part {i+1}: {e}")

# ---------------------------------------------------------------------------
# IMAGE UPLOAD VIA TELEGRAM (Admin DM only)
# ---------------------------------------------------------------------------

def handle_image_upload(message):
    import re
    import requests

    if message.chat.id != config.ADMIN_ID:
        return
    if not message.photo:
        bot.reply_to(message, "❌ Please attach a photo with the command as caption.")
        return

    caption = (message.caption or "").strip()
    if not caption.lower().startswith("/saveimage"):
        return

    parts = caption.split()
    if len(parts) < 3:
        bot.reply_to(message,
            "❌ Usage: `/saveimage Name Here characters` or `/saveimage Name Here media`",
            parse_mode="Markdown")
        return

    folder_arg = parts[-1].lower()
    name       = " ".join(parts[1:-1])

    if folder_arg in ("characters", "character", "char"):
        folder = "characters"
        label  = "characters"
    elif folder_arg in ("media", "movie", "anime", "animation"):
        folder = "media"
        label  = "media"
    else:
        bot.reply_to(message,
            "❌ Folder must be `characters` or `media`",
            parse_mode="Markdown")
        return

    file_id   = message.photo[-1].file_id
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', name).strip('_')
    filename  = f"{safe_name}.jpg"

    try:
        file_info = bot.get_file(file_id)
        dl_url    = f"https://api.telegram.org/file/bot{config.API_TOKEN}/{file_info.file_path}"
        response  = requests.get(dl_url, timeout=15, proxies={})
        response.raise_for_status()
        image_data = response.content

        from github_uploader import upload_image_to_github
        result = upload_image_to_github(bot, image_data, filename, folder)
        if result:
            bot.reply_to(message,
                f"✅ *Saved to GitHub!*\n"
                f"📁 `images/{folder}/{filename}`\n"
                f"🔗 {result}",
                parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Failed to upload to GitHub. Check logs.")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to save image: {e}")
        print(f"[IMG UPLOAD] Error: {e}")

# ---------------------------------------------------------------------------
# REGISTER COMMANDS WITH TELEGRAM
# ---------------------------------------------------------------------------

def register_commands():
    public_commands = [
        telebot.types.BotCommand("start",       "👋 Welcome message"),
        telebot.types.BotCommand("help",         "📖 Full command list"),
        telebot.types.BotCommand("game",         "👤 Guess the Character"),
        telebot.types.BotCommand("year",         "🎬 Guess the Release Year"),
        telebot.types.BotCommand("picture",      "🖼️ Scrambled Image Guessing"),
        telebot.types.BotCommand("trivia",       "❓ Trivia (choose category)"),
        telebot.types.BotCommand("guess",        "🔍 Character Quiz (text hints)"),
        telebot.types.BotCommand("lightning",    "⚡ High-risk rapid-fire trivia"),
        telebot.types.BotCommand("spin",         "🎰 Wheel of Fortune"),
        telebot.types.BotCommand("versus",       "⚔️ Challenge another player"),
        telebot.types.BotCommand("leaderboard",  "🏆 View rankings"),
        telebot.types.BotCommand("mystats",      "📊 Your stats (text)"),
        telebot.types.BotCommand("viewstats",    "📊 Stats of mentioned user"),
        telebot.types.BotCommand("shop",         "🛒 Spend your points"),
        telebot.types.BotCommand("powerups",     "⚡ View your power-ups"),
        telebot.types.BotCommand("table",        "📋 League standings"),
        telebot.types.BotCommand("fixtures",     "📅 Match fixtures"),
        telebot.types.BotCommand("feedback",     "💬 Send feedback to the Captain"),
    ]

    admin_commands = public_commands + [
        telebot.types.BotCommand("admin",        "⚙️ Admin control panel (Captain only)"),
        telebot.types.BotCommand("tagall",       "📢 Tag all members (Group admins)"),
        telebot.types.BotCommand("setschedule",  "🕐 Configure auto-game scheduler (Group admins)"),
        telebot.types.BotCommand("setwindow",    "⏰ Set game window (Group admins)"),
        telebot.types.BotCommand("mute",         "🔇 Mute a user (Captain only)"),
        telebot.types.BotCommand("unmute",       "🔊 Unmute a user (Captain only)"),
        telebot.types.BotCommand("broadcast",    "📢 Schedule a broadcast (Captain only)"),
        telebot.types.BotCommand("forcebroadcast", "📤 Force-send pending broadcasts (Captain only)"),
        telebot.types.BotCommand("checknow",     "🔄 Manually check for pending broadcasts (Captain only)"),
        telebot.types.BotCommand("uploadtrivia", "📤 Upload trivia questions (Captain only)"),
        telebot.types.BotCommand("checkimages",  "🔍 Check for missing images (Captain only)"),
        telebot.types.BotCommand("testbroadcast","🧪 Test broadcast system (Captain only)"),
        telebot.types.BotCommand("rebuildcache", "🔄 Rebuild image cache (Captain only)"),
        telebot.types.BotCommand("testmorning",  "🧪 Test morning message (Captain only)"),
        telebot.types.BotCommand("status",       "🤖 Bot status (Captain only)"),
        telebot.types.BotCommand("listbroadcasts","📋 List scheduled broadcasts (Captain only)"),
        telebot.types.BotCommand("addquote",     "➕ Add a quote (DM only, Captain only)"),
        telebot.types.BotCommand("listquotes",   "📝 List all quotes (DM only, Captain only)"),
        telebot.types.BotCommand("editquote",    "✏️ Edit a quote (DM only, Captain only)"),
        telebot.types.BotCommand("deletequote",  "🗑️ Delete a quote (DM only, Captain only)"),
        telebot.types.BotCommand("previewquote", "👁️ Preview a quote (DM only, Captain only)"),
        telebot.types.BotCommand("listpending",  "📋 List pending broadcasts (Captain only)"),
        telebot.types.BotCommand("trackgroup",   "📌 Track this group manually (Captain only)"),
        telebot.types.BotCommand("generateall",  "🖼️ Generate banners for all users (Captain only)"),
        telebot.types.BotCommand("setupgenerated", "🔧 Prepare generated branch (Captain only)"),
        telebot.types.BotCommand("clean",        "🧹 Clean up bot messages (Captain only)"),
        telebot.types.BotCommand("block",        "🚫 Block a user (Captain only)"),
        telebot.types.BotCommand("unblock",      "🔓 Unblock a user (Captain only)"),
        telebot.types.BotCommand("groupschedules","📋 List group schedules (Captain only)"),
        telebot.types.BotCommand("setschedule_group","📋 Set group schedule (Captain only)"),
        telebot.types.BotCommand("remove_schedule_group","🗑️ Remove group schedule (Captain only)"),
    ]

    try:
        bot.set_my_commands(public_commands)
        bot.set_my_commands(
            admin_commands,
            scope=telebot.types.BotCommandScopeChat(chat_id=config.ADMIN_ID)
        )
        print("✅ Bot commands registered with Telegram.")
    except Exception as e:
        print(f"⚠️ Failed to register commands: {e}")

# ---------------------------------------------------------------------------
# MY STATS COMMAND
# ---------------------------------------------------------------------------

def show_my_stats(message, target_id=None, target_name=None):
    chat_id = message.chat.id
    if target_id is None:
        target_id = message.from_user.id
        target_name = message.from_user.username or message.from_user.first_name

    data = database.load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(target_id)
    if chat_str not in data or user_str not in data[chat_str]:
        bot.reply_to(message, "❌ No stats found yet. Play a game first!")
        return

    u = data[chat_str][user_str]
    month_key = database._now_month_key()
    year_key = database._now_year_key()

    monthly = u.get("monthly_points", {}).get(month_key, 0)
    yearly = u.get("yearly_points", {}).get(year_key, 0)
    alltime = u.get("alltime_points", 0)
    streak = u.get("streak", 0)
    best = u.get("best_streak", 0)
    played = u.get("games_played", 0)
    correct = u.get("correct", 0)
    title = database._get_active_title(u) or "None"
    hints = u.get("hint_tokens", 0)
    badges = u.get("badges", [])
    accuracy = f"{int((correct / played) * 100)}%" if played > 0 else "N/A"

    lb = database.get_leaderboard(chat_id, mode="monthly", top_n=100)
    rank = next((r for r, name, *_ in lb if name == target_name), "?")

    double_xp = u.get("double_xp_until")
    xp_status = ""
    if double_xp and time.time() < double_xp:
        mins_left = int((double_xp - time.time()) / 60)
        xp_status = f"\n⚡ *Double XP active:* {mins_left} min remaining"

    badge_icons = " ".join([config.ACHIEVEMENTS.get(b, {}).get("icon", "🏅") for b in badges]) if badges else "None"
    powerups = u.get("powerups", {})
    powerup_str = ", ".join([f"{config.POWERUPS.get(k, {}).get('emoji', k)} {k.replace('_',' ').title()} x{v}" for k, v in powerups.items() if v > 0]) or "None"

    text = (
        f"📊 *{target_name}'s Stats*\n\n"
        f"🏅 *Title:* {title}\n"
        f"🏆 *Monthly rank:* #{rank}\n\n"
        f"💰 *Points*\n"
        f"This month: {monthly} pts\n"
        f"This year: {yearly} pts\n"
        f"All time: {alltime} pts\n\n"
        f"🎮 *Games*\n"
        f"Played: {played}\n"
        f"Correct: {correct}\n"
        f"Accuracy: {accuracy}\n\n"
        f"🔥 *Streak*\n"
        f"Current: {streak}\n"
        f"Best ever: {best}\n\n"
        f"💡 *Hint tokens:* {hints}\n"
        f"📛 *Badges:* {badge_icons}\n"
        f"⚡ *Power-ups:* {powerup_str}\n"
        f"{xp_status}"
    )

    try:
        banner_url_or_path = profile_banner.generate_profile_banner(bot, target_id, target_name, chat_id)
        if banner_url_or_path:
            if banner_url_or_path.startswith("http"):
                bot.send_photo(chat_id, banner_url_or_path, caption=text, parse_mode="Markdown")
            else:
                with open(banner_url_or_path, 'rb') as f:
                    bot.send_photo(chat_id, f, caption=text, parse_mode="Markdown")
            return
    except Exception as e:
        print(f"Banner generation failed for {target_id}: {e}")
    
    if target_id == message.from_user.id:
        bot.reply_to(message, text, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# STARTUP FALLBACKS
# ---------------------------------------------------------------------------

def check_startup_fallbacks():
    print("🔍 Checking startup fallbacks...")
    now = local_now()
    sched = load_scheduler()
    today = now.strftime("%Y-%m-%d")

    if sched.get("last_morning_date") != today and now.hour >= config.MORNING_MSG_HOUR:
        print("🌅 Sending morning message (startup fallback)...")
        send_morning_message(bot)

    _send_pending_broadcasts(bot)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("🚀 Za Sora Bot starting...")
    register_commands()
    games.precache_assets(bot)
    threading.Thread(target=graphics.clear_and_rebuild_disk_cache, args=(bot,), daemon=True).start()
    database.check_and_run_monthly_reset(bot)
    database.cleanup_expired_mutes(bot)
    check_startup_fallbacks()

    start_broadcast_checker()
    start_scheduler()

    threading.Thread(target=thread_supervisor, daemon=True).start()

    threading.Thread(target=notify_missing_images, daemon=True).start()

    print("✅ Bot is live!")
    bot.infinity_polling(timeout=20, long_polling_timeout=30)
