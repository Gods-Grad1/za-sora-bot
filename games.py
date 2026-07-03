import os
import re
import time
import random
import json
import datetime
import threading
from io import BytesIO
from difflib import SequenceMatcher
import requests
import config
import database

# ---------------------------------------------------------------------------
# AUTO-DELETE HELPERS
# ---------------------------------------------------------------------------

def send_and_delete(bot, chat_id, text, parse_mode="Markdown", reply_markup=None, delay=config.AUTO_DELETE_DELAY):
    """Sends a message and schedules it for deletion."""
    msg = bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    def delete():
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass
    timer = threading.Timer(delay, delete)
    timer.daemon = True
    timer.start()
    return msg

def send_photo_and_delete(bot, chat_id, photo, caption="", reply_markup=None, parse_mode="Markdown", delay=config.AUTO_DELETE_DELAY):
    """Sends a photo and schedules it for deletion."""
    msg = bot.send_photo(chat_id, photo, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
    def delete():
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass
    timer = threading.Timer(delay, delete)
    timer.daemon = True
    timer.start()
    return msg

# ---------------------------------------------------------------------------
# IMAGE SYSTEM (GitHub → URL, with upload)
# ---------------------------------------------------------------------------

IMAGE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_image_cache")
LOCAL_CHAR_DIR  = config.LOCAL_CHAR_IMAGES_DIR
LOCAL_MEDIA_DIR = config.LOCAL_MEDIA_IMAGES_DIR
SUPPORTED_EXTS  = [".jpg", ".jpeg", ".png", ".webp"]

_IMG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.themoviedb.org/"
}

def _ensure_dirs():
    for d in [IMAGE_CACHE_DIR, LOCAL_CHAR_DIR, LOCAL_MEDIA_DIR]:
        os.makedirs(d, exist_ok=True)

def _name_to_filename(name):
    return re.sub(r'[^a-zA-Z0-9._-]', '_', name).strip('_')

def _download_image(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=_IMG_HEADERS, timeout=15, proxies={})
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10 * attempt))
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except requests.exceptions.HTTPError:
            return None
        except Exception as e:
            if attempt < retries:
                time.sleep(3 * attempt)
    return None

def get_image_bytes(bot, name, folder, url):
    _ensure_dirs()
    safe_name = _name_to_filename(name)

    # Determine remote folder
    if folder == config.LOCAL_CHAR_IMAGES_DIR:
        remote_folder = "characters"
    elif folder == config.LOCAL_MEDIA_IMAGES_DIR:
        remote_folder = "media"
    else:
        remote_folder = folder

    # 1. Try GitHub first
    github_url = f"{config.GITHUB_RAW_BASE_URL}{remote_folder}/{safe_name}.jpg"
    data = _download_image(github_url)
    if data:
        bio = BytesIO(data)
        bio.name = "image.jpg"
        bio.seek(0)
        return bio

    # 2. Fallback to original URL (and upload to GitHub)
    if url:
        data = _download_image(url)
        if data:
            # Upload to GitHub in the background
            import threading
            from github_uploader import upload_image_to_github
            def upload():
                upload_image_to_github(bot, data, f"{safe_name}.jpg", remote_folder)
            threading.Thread(target=upload, daemon=True).start()

            bio = BytesIO(data)
            bio.name = "image.jpg"
            bio.seek(0)
            return bio

    # 3. Total failure – notify admin
    try:
        bot.send_message(config.ADMIN_ID,
            f"❌ *Image unavailable:* `{name}` — sending text only.",
            parse_mode="Markdown")
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# PRECACHE
# ---------------------------------------------------------------------------

def load_json_file(file_path):
    if not os.path.exists(file_path):
        print(f"⚠️ File not found: {file_path}")
        return []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"🛑 JSON error in {file_path}: {e}")
        return None
    except Exception as e:
        print(f"💥 Error loading {file_path}: {e}")
        return []

def _load_all(db_list):
    result = []
    for path in db_list:
        data = load_json_file(path)
        if data:
            result.extend(data)
    return result

def _precache_all_images(bot):
    _ensure_dirs()
    print("⚡ [IMG CACHE] Starting pre-cache...")
    all_dbs = config.MEDIA_ALL_DBS + config.CHAR_ALL_DBS
    total = skipped = failed = 0
    for db_path in all_dbs:
        entries = load_json_file(db_path) or []
        for entry in entries:
            url = (entry.get('image') or entry.get('image_url') or
                   entry.get('img') or entry.get('poster'))
            if not url:
                continue
            # Try to get from GitHub
            safe_name = _name_to_filename(entry.get('name') or entry.get('title'))
            if entry in config.CHAR_ALL_DBS:
                remote_folder = "characters"
            else:
                remote_folder = "media"
            github_url = f"{config.GITHUB_RAW_BASE_URL}{remote_folder}/{safe_name}.jpg"
            if _download_image(github_url):
                skipped += 1
                continue
            # If not on GitHub, download from URL and upload
            data = _download_image(url)
            if data:
                from github_uploader import upload_image_to_github
                upload_image_to_github(bot, data, f"{safe_name}.jpg", remote_folder)
                total += 1
                time.sleep(0.3)
            else:
                failed += 1
    print(f"✅ [IMG CACHE] Done — uploaded:{total} cached:{skipped} failed:{failed}")

def precache_assets(bot):
    threading.Thread(target=_precache_all_images, args=(bot,), daemon=True).start()

# ---------------------------------------------------------------------------
# ACTIVE GAME STATE
# ---------------------------------------------------------------------------

active_games = {}
versus_games = {}
lightning_sessions = {}  # NEW: Track active lightning rounds per user
last_game_type = {}
last_game_category = {}
pending_admin_actions = {}

def _get_time_limit():
    try:
        sched = database.load_remote_json(config.SCHEDULER_FILE, {})
        return int(sched.get("answer_time_limit", config.DEFAULT_ANSWER_TIME))
    except Exception:
        return config.DEFAULT_ANSWER_TIME

def _is_game_active(chat_id):
    return chat_id in active_games

def _check_one_game_lock(bot, chat_id, user_id, game_type=None, category=None):
    if database.is_muted(bot, chat_id, user_id):
        bot.send_message(chat_id, "🔇 You are muted! Wait until your mute expires.")
        return True

    if not _is_game_active(chat_id) and chat_id not in versus_games:
        return False

    if user_id == config.ADMIN_ID:
        pending_admin_actions[chat_id] = {'type': game_type, 'category': category}
        import telebot
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(
            telebot.types.InlineKeyboardButton("✅ Yes, stop & start", callback_data="admin_force_start"),
            telebot.types.InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel_start"),
        )
        bot.send_message(chat_id,
                         "⚠️ A game is already in progress. Do you want to stop it and start a new one?",
                         reply_markup=markup, parse_mode="Markdown")
        return True

    if chat_id in versus_games:
        bot.send_message(chat_id, "⚠️ A versus match is currently in progress. Please wait until it finishes.")
        return True

    bot.send_message(chat_id, "⚠️ A game is already in progress! Answer it, use /stop to cancel, or wait for the timer.")
    return True

# ---------------------------------------------------------------------------
# SEND HELPERS (Hint button hidden for character games)
# ---------------------------------------------------------------------------

def _game_markup(chat_id, game_type):
    import telebot
    markup = telebot.types.InlineKeyboardMarkup()
    buttons = []
    
    # Hints are disabled for character guessing (too easy with the picture)
    if game_type != "character":
        buttons.append(telebot.types.InlineKeyboardButton("💡 Hint", callback_data=f"hint_{chat_id}"))
    
    buttons.append(telebot.types.InlineKeyboardButton("⏭️ Next", callback_data=f"nextgame_{chat_id}_{game_type}"))
    buttons.append(telebot.types.InlineKeyboardButton("🛑 Stop", callback_data=f"stopgame_{chat_id}"))
    markup.row(*buttons)
    return markup

def _send_game_message(bot, chat_id, text, name, folder, url, markup=None, delay=config.GAME_AUTO_DELETE_DELAY):
    img_data = get_image_bytes(bot, name, folder, url)
    kwargs   = {"parse_mode": "Markdown"}
    if markup:
        kwargs["reply_markup"] = markup
    if img_data:
        try:
            if len(text) <= 1024:
                send_photo_and_delete(bot, chat_id, img_data, caption=text, delay=delay, **kwargs)
            else:
                send_photo_and_delete(bot, chat_id, img_data, delay=delay)
                send_and_delete(bot, chat_id, text, delay=delay, **kwargs)
            return
        except Exception as e:
            print(f"⚠️ Photo send failed: {e}")
    send_and_delete(bot, chat_id, text, delay=delay, **kwargs)

# ---------------------------------------------------------------------------
# CATEGORY PICKER MENUS
# ---------------------------------------------------------------------------

def send_character_category_picker(bot, chat_id):
    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🌸 Anime",   callback_data="charcat_anime"),
        telebot.types.InlineKeyboardButton("🦸 DC",      callback_data="charcat_dc"),
        telebot.types.InlineKeyboardButton("⚡ Marvel",  callback_data="charcat_marvel"),
        telebot.types.InlineKeyboardButton("🎮 Gaming",  callback_data="charcat_gaming"),
        telebot.types.InlineKeyboardButton("🎲 Random",  callback_data="charcat_random"),
    )
    send_and_delete(bot, chat_id, "👤 *GUESS THE CHARACTER*\n\nChoose a category:",
                     reply_markup=markup, parse_mode="Markdown")

def send_year_category_picker(bot, chat_id):
    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🎬 Movies",       callback_data="yearcat_movies"),
        telebot.types.InlineKeyboardButton("📺 Anime Series", callback_data="yearcat_anime_series"),
        telebot.types.InlineKeyboardButton("🎥 Anime Films",  callback_data="yearcat_anime_films"),
        telebot.types.InlineKeyboardButton("🎨 Animation",    callback_data="yearcat_animation"),
        telebot.types.InlineKeyboardButton("🎲 Random",       callback_data="yearcat_random"),
    )
    send_and_delete(bot, chat_id, "🎬 *GUESS THE YEAR*\n\nChoose a category:",
                     reply_markup=markup, parse_mode="Markdown")

def send_trivia_category_picker(bot, chat_id):
    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    icons  = ["🎮","👤","🎬","🌍","🍕","⚽","💻","📖"]
    markup.add(*[
        telebot.types.InlineKeyboardButton(f"{icon} {cat}",
            callback_data=f"triviacat_{cat.lower()}")
        for icon, cat in zip(icons, config.TRIVIA_CATEGORIES)
    ])
    markup.add(telebot.types.InlineKeyboardButton("🎲 Random", callback_data="triviacat_random"))
    send_and_delete(bot, chat_id, "❓ *TRIVIA*\n\nChoose a category:",
                     reply_markup=markup, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# HINT SYSTEM (Blocks hints for character games)
# ---------------------------------------------------------------------------

def _get_hint(session, hint_num):
    game_type = session.get("type")
    if game_type in ("character", "picture"):
        hints_list = session.get("hints_list", [])
        if hint_num == 1:
            return f"💡 *Hint 1:* {hints_list[0]}" if hints_list else "💡 *Hint 1:* No extra hints available."
        elif hint_num == 2:
            return f"💡 *Hint 2:* The name starts with *{session['answer'][0].upper()}*"
        elif hint_num == 3:
            return f"💡 *Hint 3:* Category — *{session.get('category', 'Unknown')}*"
    elif game_type == "year":
        year = int(session["answer"])
        if hint_num == 1:
            return f"💡 *Hint 1:* Released in the *{(year//10)*10}s*"
        elif hint_num == 2:
            return f"💡 *Hint 2:* Between *{year-3}* and *{year+3}*"
        elif hint_num == 3:
            return f"💡 *Hint 3:* Type — *{session.get('media_type','Unknown')}*"
    return "💡 No more hints available."

def process_hint(bot, message=None, call=None):
    if call:
        chat_id  = call.message.chat.id
        user_id  = call.from_user.id
        username = call.from_user.username or call.from_user.first_name
        def reply(t): send_and_delete(bot, chat_id, t, parse_mode="Markdown")
    else:
        chat_id  = message.chat.id
        user_id  = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        def reply(t): bot.reply_to(message, t, parse_mode="Markdown")

    if database.is_muted(bot, chat_id, user_id):
        reply("🔇 You are muted! Wait until your mute expires.")
        return

    if chat_id not in active_games:
        reply("❌ No active game to hint.")
        return

    session = active_games[chat_id]
    
    # NEW: Block hints for character games
    if session.get("type") == "character":
        reply("💡 Hints are not available for Character Guessing games. Just look at the picture and guess!")
        return

    hints_used = session.get("hints_used", 0)

    if hints_used >= config.POINTS_HINT_MAX:
        reply(f"❌ Maximum {config.POINTS_HINT_MAX} hints already used this round.")
        return

    has_token = database.use_hint_token(bot, chat_id, user_id, username)
    if not has_token:
        data = database.load_remote_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(user_id)
        u = database.get_user(data, chat_str, user_str, username)
        current_pts = u.get("points", 0)
        if current_pts < config.POINTS_HINT_PENALTY:
            reply(f"❌ Not enough points for a hint. You have *{current_pts}* pts but need *{config.POINTS_HINT_PENALTY}* pts.")
            return
        database.deduct_points(bot, chat_id, user_id, username, config.POINTS_HINT_PENALTY)

    session["hints_used"] = hints_used + 1
    hint_text  = _get_hint(session, session["hints_used"])
    cost_note  = "*(Used hint token ✅)*" if has_token else f"*(-{config.POINTS_HINT_PENALTY} pts)*"
    reply(f"{hint_text}\n{cost_note}")

# ---------------------------------------------------------------------------
# CHARACTER GAME
# ---------------------------------------------------------------------------

def is_character_match(guess, answer, aliases=None):
    guess = guess.lower().strip()
    answer = answer.lower().strip()
    if guess == answer:
        return True
    if guess in answer or answer in guess:
        return True
    guess_words = set(guess.split())
    answer_words = set(answer.split())
    if guess_words & answer_words:
        return True
    if SequenceMatcher(None, guess, answer).ratio() >= 0.85:
        return True
    if aliases:
        for alias in aliases:
            alias_lower = alias.lower()
            if guess == alias_lower:
                return True
            if guess in alias_lower or alias_lower in guess:
                return True
            if set(guess.split()) & set(alias_lower.split()):
                return True
            if SequenceMatcher(None, guess, alias_lower).ratio() >= 0.85:
                return True
    return False

def start_character_game(bot, chat_id, category=None, user_id=None):
    if user_id and _check_one_game_lock(bot, chat_id, user_id, game_type="character", category=category):
        return None

    db_path    = config.CHAR_CATEGORIES.get((category or "random").lower())
    characters = load_json_file(db_path) if db_path and category != "random" else _load_all(config.CHAR_ALL_DBS)

    if not characters:
        send_and_delete(bot, chat_id, "⚠️ No characters found.")
        return None

    q       = random.choice(characters)
    name    = str(q.get('name', 'Unknown')).strip()
    hints   = q.get('hints') or q.get('hint', 'No hints available.')
    img_url = q.get('image') or q.get('image_url') or q.get('img')
    hints_list = hints if isinstance(hints, list) else [hints]
    aliases = q.get('aliases', [])
    cat_label  = category.title() if category and category != "random" else "Character"

    active_games[chat_id] = {
        "type":       "character",
        "answer":     name.lower(),
        "display":    name,
        "hints_list": hints_list,
        "category":   cat_label,
        "hints_used": 0,
        "img_url":    img_url,
        "aliases":    [a.lower() for a in aliases],
    }

    last_game_type[chat_id] = "character"
    last_game_category[chat_id] = category if category else "random"

    time_limit = _get_time_limit()
    text = f"👤 *GUESS THE CHARACTER* 👤\n\n"
    if isinstance(hints, list):
        for i, h in enumerate(hints, 1):
            text += f"🔍 *HINT {i}:* {h}\n"
    else:
        text += f"🔍 *HINT:* {hints}\n"
    text += f"\n⏱️ *{time_limit}s to answer!*  💡 /hint for a clue (-{config.POINTS_HINT_PENALTY} pts)"

    markup = _game_markup(chat_id, "character")
    _send_game_message(bot, chat_id, text, name, LOCAL_CHAR_DIR, img_url, markup)

    _start_timer(bot, chat_id, time_limit)
    return q

# ---------------------------------------------------------------------------
# YEAR GAME (Multiple Choice)
# ---------------------------------------------------------------------------

def start_year_game(bot, chat_id, category=None, user_id=None):
    if user_id and _check_one_game_lock(bot, chat_id, user_id, game_type="year", category=category):
        return None

    db_path = config.YEAR_CATEGORIES.get((category or "random").lower())
    items   = load_json_file(db_path) if db_path and category != "random" else _load_all(config.MEDIA_ALL_DBS)

    if not items:
        send_and_delete(bot, chat_id, "⚠️ No media found.")
        return None

    q          = random.choice(items)
    title      = q.get('title', 'Unknown Title')
    media_type = q.get('type', 'Media')
    year       = str(q.get('year', '????')).strip()
    img_url    = q.get('image') or q.get('image_url') or q.get('img') or q.get('poster')
    cat_label  = category.replace("_", " ").title() if category and category != "random" else media_type

    all_years = [item.get('year') for item in items if item.get('year') and item.get('year') != year]
    random.shuffle(all_years)
    distractors = all_years[:3]
    options = [year] + distractors
    random.shuffle(options)
    correct_index = options.index(year)

    active_games[chat_id] = {
        "type":          "year",
        "answer":        year,
        "display":       f"{title} ({year})",
        "media_type":    cat_label,
        "hints_used":    0,
        "img_url":       img_url,
        "options":       options,
        "correct_index": correct_index,
        "title":         title,
        "answered":      set(),
    }

    last_game_type[chat_id] = "year"
    last_game_category[chat_id] = category if category else "random"

    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    letters = ["A", "B", "C", "D"]
    for i, opt in enumerate(options):
        markup.add(telebot.types.InlineKeyboardButton(
            f"{letters[i]}. {opt}", callback_data=f"year_ans_{chat_id}_{i}"
        ))
    markup.row(
        telebot.types.InlineKeyboardButton("⏭️ Next",  callback_data=f"nextgame_{chat_id}_year"),
        telebot.types.InlineKeyboardButton("🛑 Stop",  callback_data=f"stopgame_{chat_id}"),
    )

    time_limit = _get_time_limit()
    text = (
        f"🎬 *GUESS THE RELEASE YEAR* 🎬\n\n"
        f"🍿 *Title:* {title}\n"
        f"🏷️ *Type:* {cat_label}\n\n"
        f"Tap the correct year below!\n"
        f"⏱️ *{time_limit}s to answer!*"
    )

    _send_game_message(bot, chat_id, text, title, LOCAL_MEDIA_DIR, img_url, markup)
    _start_timer(bot, chat_id, time_limit)
    return q

def handle_year_answer(bot, call):
    parts = call.data.split("_")
    chat_id = int(parts[2])
    chosen_index = int(parts[3])

    if chat_id not in active_games or active_games[chat_id].get("type") != "year":
        bot.answer_callback_query(call.id, "No active year round.")
        return

    session = active_games[chat_id]
    user_id = call.from_user.id
    username = call.from_user.username or call.from_user.first_name

    if user_id in session.get("answered", set()):
        bot.answer_callback_query(call.id, "❌ You already answered this round!", show_alert=True)
        return

    session.setdefault("answered", set()).add(user_id)

    if chosen_index == session["correct_index"]:
        pts, streak, mult, final = database.reward_user(
            bot, chat_id, user_id, username, config.POINTS_YEAR_GAME)
        streak_txt = f" 🔥 Streak x{int(mult)}!" if streak > 1 else ""
        bot.answer_callback_query(call.id, f"✅ CORRECT! +{final} pts{streak_txt}", show_alert=True)

        import telebot
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(telebot.types.InlineKeyboardButton(
            "⏭️ Next Game", callback_data=f"nextgame_{chat_id}_year"
        ))
        send_and_delete(
            bot,
            chat_id,
            f"🎉 *{username}* got it!\n"
            f"Answer: *{session['answer']}* for *{session['title']}*\n"
            f"+{final} pts{streak_txt}",
            reply_markup=markup, parse_mode="Markdown"
        )
        del active_games[chat_id]
    else:
        database.penalise_wrong(bot, chat_id, user_id, username)
        bot.answer_callback_query(call.id, "❌ Wrong! Streak broken.", show_alert=True)

# ---------------------------------------------------------------------------
# PICTURE GUESSING GAME
# ---------------------------------------------------------------------------

def start_picture_game(bot, chat_id, category=None, user_id=None):
    if user_id and _check_one_game_lock(bot, chat_id, user_id, game_type="picture", category=category):
        return None

    db_path = config.CHAR_CATEGORIES.get((category or "random").lower())
    if not db_path:
        db_path = config.CHAR_ANIME_DB
    items = load_json_file(db_path) if db_path and category != "random" else _load_all(config.CHAR_ALL_DBS)
    if not items:
        send_and_delete(bot, chat_id, "⚠️ No items found.")
        return None
    q = random.choice(items)
    name = str(q.get('name', 'Unknown')).strip()
    img_url = q.get('image') or q.get('image_url') or q.get('img')
    cat_label = category.title() if category and category != "random" else "Character"
    hints = q.get('hints') or q.get('hint', 'No hints available.')
    hints_list = hints if isinstance(hints, list) else [hints]

    img_data = get_image_bytes(bot, name, LOCAL_CHAR_DIR, img_url)
    if not img_data:
        send_and_delete(bot, chat_id, "❌ Could not load image for this character.")
        return None

    from PIL import Image
    img = Image.open(img_data)
    w, h = img.size
    small = img.resize((w//8, h//8), Image.Resampling.NEAREST)
    scrambled = small.resize((w, h), Image.Resampling.NEAREST)
    bio = BytesIO()
    scrambled.save(bio, 'PNG')
    bio.seek(0)
    bio.name = "scrambled.png"

    active_games[chat_id] = {
        "type": "picture",
        "answer": name.lower(),
        "display": name,
        "category": cat_label,
        "hints_used": 0,
        "img_url": img_url,
        "hints_list": hints_list,
    }

    last_game_type[chat_id] = "picture"
    last_game_category[chat_id] = category if category else "random"

    time_limit = _get_time_limit()
    text = f"🖼️ *GUESS THE CHARACTER* (Scrambled Image)\n\n"
    text += f"Category: *{cat_label}*\n\n"
    text += f"⏱️ *{time_limit}s to answer!*  💡 /hint for a clue"

    markup = _game_markup(chat_id, "picture")
    send_photo_and_delete(bot, chat_id, bio, caption=text, reply_markup=markup, parse_mode="Markdown")
    _start_timer(bot, chat_id, time_limit)
    return q

# ---------------------------------------------------------------------------
# TIMER
# ---------------------------------------------------------------------------

def _start_timer(bot, chat_id, seconds):
    session_snapshot = active_games.get(chat_id, {}).copy()
    def timeout():
        time.sleep(seconds)
        if chat_id in active_games and active_games[chat_id].get("answer") == session_snapshot.get("answer"):
            if active_games[chat_id].get("type") == "year":
                correct = active_games[chat_id].get("answer")
                title = active_games[chat_id].get("title", "")
                send_and_delete(
                    bot,
                    chat_id,
                    f"⏰ *Time's up!*\nThe correct year was: *{correct}* for *{title}*",
                    parse_mode="Markdown",
                    delay=config.GAME_AUTO_DELETE_DELAY
                )
            elif active_games[chat_id].get("type") in ("character", "picture"):
                answer = active_games[chat_id].get("display", "Unknown")
                send_and_delete(
                    bot,
                    chat_id,
                    f"⏰ *Time's up!*\nThe answer was: *{answer}*",
                    parse_mode="Markdown",
                    delay=config.GAME_AUTO_DELETE_DELAY
                )
            del active_games[chat_id]
            send_and_delete(
                bot,
                chat_id,
                f"⏰ *Time's up!* No one got it this round.\n\n"
                f"Start a new game with /game, /year or /trivia! 🎮",
                parse_mode="Markdown",
                delay=config.GAME_AUTO_DELETE_DELAY
            )
    threading.Thread(target=timeout, daemon=True).start()

# ---------------------------------------------------------------------------
# TRIVIA GAME (UPDATED: fifty-fifty fix)
# ---------------------------------------------------------------------------

def start_trivia_game(bot, chat_id, category=None, user_id=None):
    if user_id and _check_one_game_lock(bot, chat_id, user_id, game_type="trivia", category=category):
        return None

    trivia_data = database.load_trivia_from_github()
    if not trivia_data:
        send_and_delete(bot, chat_id, "⚠️ Trivia database not found.")
        return None

    pool = trivia_data
    if category and category.lower() not in ("random", "all"):
        pool = [q for q in trivia_data if q["category"].lower() == category.lower()]
    if not pool:
        send_and_delete(bot, chat_id, f"⚠️ No questions found for: {category}")
        return None

    q       = random.choice(pool)
    options = q["options"]
    answer  = q["answer"]

    # --- Fifty-Fifty power-up activation (FIXED) ---
    fifty_fifty_used = False
    if user_id:
        # Fetch username from the database
        data = database.load_remote_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(user_id)
        u = database.get_user(data, chat_str, user_str, "User")
        username = u.get("username", "User")
        
        if database.use_powerup(bot, chat_id, user_id, username, "fifty_fifty"):
            fifty_fifty_used = True
            correct_option = options[ord(answer) - ord("A")]
            wrong_options = [opt for opt in options if opt != correct_option]
            random.shuffle(wrong_options)
            trimmed_options = [correct_option] + wrong_options[:1]
            random.shuffle(trimmed_options)
            options = trimmed_options
            answer = chr(ord('A') + options.index(correct_option))
            send_and_delete(bot, chat_id, "✂️ *50/50 activated!* Two wrong answers removed.", parse_mode="Markdown")
    # --- end of fix ---

    active_games[chat_id] = {
        "type":     "trivia",
        "answer":   answer,
        "display":  options[ord(answer) - ord("A")],
        "question": q["question"],
        "options":  options,
        "category": q["category"],
        "answered": set(),
        "hints_used": 0,
        "fifty_fifty_used": fifty_fifty_used,
    }

    last_game_type[chat_id] = "trivia"
    last_game_category[chat_id] = category if category else "random"

    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    letters = ["A", "B", "C", "D"]
    markup.add(*[
        telebot.types.InlineKeyboardButton(
            f"{l}. {options[i]}", callback_data=f"trivia_{chat_id}_{l}"
        ) for i, l in enumerate(letters) if i < len(options)
    ])
    markup.row(
        telebot.types.InlineKeyboardButton("⏭️ Next",  callback_data=f"nextgame_{chat_id}_trivia"),
        telebot.types.InlineKeyboardButton("🛑 Stop",  callback_data=f"stopgame_{chat_id}"),
    )

    time_limit = _get_time_limit()
    text = (
        f"❓ *TRIVIA — {q['category'].upper()}*\n\n"
        f"*{q['question']}*\n\n"
    )
    for i, opt in enumerate(options):
        text += f"{letters[i]}. {opt}\n"
    text += f"\n🏆 First correct wins *{config.POINTS_TRIVIA} pts!*  ⏱️ {time_limit}s"

    send_and_delete(bot, chat_id, text, reply_markup=markup, parse_mode="Markdown")

    session_snapshot = active_games[chat_id].copy()
    def auto_reveal():
        time.sleep(time_limit)
        if chat_id in active_games and active_games[chat_id].get("answer") == session_snapshot.get("answer"):
            send_and_delete(
                bot,
                chat_id,
                f"⏰ *Time's up!*\nThe answer was: *{session_snapshot['answer']}. {session_snapshot['display']}*",
                parse_mode="Markdown",
                delay=config.GAME_AUTO_DELETE_DELAY
            )
            if chat_id in active_games:
                del active_games[chat_id]
    threading.Thread(target=auto_reveal, daemon=True).start()
    return q

def handle_trivia_answer(bot, call):
    parts   = call.data.split("_")
    chat_id = int(parts[1])
    chosen  = parts[2]

    if chat_id not in active_games or active_games[chat_id].get("type") != "trivia":
        bot.answer_callback_query(call.id, "No active trivia round.")
        return

    session  = active_games[chat_id]
    user_id  = call.from_user.id
    username = call.from_user.username or call.from_user.first_name

    if user_id in session["answered"]:
        bot.answer_callback_query(call.id, "❌ You already answered this round!", show_alert=True)
        return

    session["answered"].add(user_id)

    if chosen == session["answer"]:
        pts, streak, mult, final = database.reward_user(
            bot, chat_id, user_id, username, config.POINTS_TRIVIA)
        streak_txt = f" 🔥 Streak x{int(mult)}!" if streak > 1 else ""

        data = database.load_remote_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(user_id)
        u = database.get_user(data, chat_str, user_str, username)
        u["trivia_correct"] = u.get("trivia_correct", 0) + 1
        database.save_remote_json(config.GROUP_DATA_FILE, data)
        database.check_achievements(bot, chat_id, user_id, username)

        bot.answer_callback_query(call.id, f"✅ CORRECT! +{final} pts{streak_txt}", show_alert=True)

        import telebot
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(
            telebot.types.InlineKeyboardButton("⏭️ Next Game", callback_data=f"nextgame_{chat_id}_trivia"),
        )
        send_and_delete(
            bot,
            chat_id,
            f"🎉 *{username}* got it!\n"
            f"Answer: *{session['answer']}. {session['display']}*\n"
            f"+{final} pts{streak_txt}",
            reply_markup=markup, parse_mode="Markdown"
        )
        del active_games[chat_id]
    else:
        database.penalise_wrong(bot, chat_id, user_id, username)
        bot.answer_callback_query(call.id, "❌ Wrong! Streak broken.", show_alert=True)

# ---------------------------------------------------------------------------
# NEXT GAME HANDLER
# ---------------------------------------------------------------------------

def handle_next_game(bot, call):
    parts     = call.data.split("_")
    chat_id   = int(parts[1])
    game_type = parts[2]

    if chat_id in active_games:
        del active_games[chat_id]

    bot.answer_callback_query(call.id)
    send_and_delete(bot, chat_id, "⏭️ *Moving to the next round...*", parse_mode="Markdown")

    cat = last_game_category.get(chat_id, "random")

    if game_type == "character":
        start_character_game(bot, chat_id, category=cat)
    elif game_type == "year":
        start_year_game(bot, chat_id, category=cat)
    elif game_type == "trivia":
        start_trivia_game(bot, chat_id, category=cat)
    elif game_type == "picture":
        start_picture_game(bot, chat_id, category=cat)
    elif game_type == "guess":
        start_guess_game(bot, chat_id, user_id=None)
    elif game_type == "lightning":
        # Lightning doesn't support "next" in the same way
        send_and_delete(bot, chat_id, "⚡ Use /lightning to start a new Lightning Round!", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# VERSUS MODE
# ---------------------------------------------------------------------------

WIN_TARGET = 2

def start_versus(bot, chat_id, challenger_id, challenger_name, target_id, target_name):
    import telebot
    if chat_id in versus_games:
        send_and_delete(bot, chat_id, "⚔️ A versus match is already in progress!")
        return

    if database.is_muted(bot, chat_id, challenger_id):
        send_and_delete(bot, chat_id, "🔇 You are muted! You cannot start a versus match.")
        return
    if database.is_muted(bot, chat_id, target_id):
        send_and_delete(bot, chat_id, "🔇 That user is muted! They cannot accept a versus match.")
        return

    versus_games[chat_id] = {
        "state":             "pending",
        "challenger_id":     challenger_id,
        "challenger_name":   challenger_name,
        "target_id":         target_id,
        "target_name":       target_name,
        "bets":              {},
        "score":             {str(challenger_id): 0, str(target_id): 0},
        "round":             0,
        "consecutive_skips": 0,
        "expires":           time.time() + config.VERSUS_ACCEPT_TIMEOUT,
    }

    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("✅ Accept Challenge",
            callback_data=f"vs_accept_{chat_id}_{challenger_id}"),
        telebot.types.InlineKeyboardButton("❌ Decline",
            callback_data=f"vs_decline_{chat_id}"),
    )
    send_and_delete(bot, chat_id,
        f"⚔️ *VERSUS CHALLENGE!*\n\n"
        f"🥊 *{challenger_name}* has challenged *{target_name}* to a trivia duel!\n"
        f"First to *{WIN_TARGET} points* wins!\n\n"
        f"*{target_name}* — you have {config.VERSUS_ACCEPT_TIMEOUT}s to accept!",
        reply_markup=markup, parse_mode="Markdown")

    def expire():
        time.sleep(config.VERSUS_ACCEPT_TIMEOUT + 1)
        if chat_id in versus_games and versus_games[chat_id].get("state") == "pending":
            del versus_games[chat_id]
            send_and_delete(bot, chat_id,
                f"⏰ Challenge expired — *{target_name}* didn't respond.",
                parse_mode="Markdown")
    threading.Thread(target=expire, daemon=True).start()

def handle_versus_accept(bot, call):
    import telebot
    parts         = call.data.split("_")
    chat_id       = int(parts[2])
    challenger_id = int(parts[3])

    if chat_id not in versus_games:
        bot.answer_callback_query(call.id, "⏰ Challenge expired.", show_alert=True)
        return

    vs      = versus_games[chat_id]
    user_id = call.from_user.id

    if user_id != vs["target_id"]:
        bot.answer_callback_query(call.id, "⚠️ This challenge isn't for you!", show_alert=True)
        return

    vs["state"] = "betting"
    bot.answer_callback_query(call.id, "✅ Challenge accepted!")
    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except Exception:
        pass

    c_name, t_name = vs["challenger_name"], vs["target_name"]
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton(f"5pts → {c_name}",
            callback_data=f"vsbet_{chat_id}_challenger_5"),
        telebot.types.InlineKeyboardButton(f"5pts → {t_name}",
            callback_data=f"vsbet_{chat_id}_target_5"),
        telebot.types.InlineKeyboardButton(f"10pts → {c_name}",
            callback_data=f"vsbet_{chat_id}_challenger_10"),
        telebot.types.InlineKeyboardButton(f"10pts → {t_name}",
            callback_data=f"vsbet_{chat_id}_target_10"),
        telebot.types.InlineKeyboardButton(f"20pts → {c_name}",
            callback_data=f"vsbet_{chat_id}_challenger_20"),
        telebot.types.InlineKeyboardButton(f"20pts → {t_name}",
            callback_data=f"vsbet_{chat_id}_target_20"),
    )
    send_and_delete(bot, chat_id,
        f"✅ *Challenge Accepted!*\n\n"
        f"⚔️ *{c_name}* vs *{t_name}*\n"
        f"First to *{WIN_TARGET} points* wins — no round limit!\n\n"
        f"👥 *Spectators — place your bets!*\n"
        f"Bets lock before round 1. Max: *{config.VERSUS_MAX_BET} pts* | One per person\n"
        f"⏳ Betting closes in *{config.VERSUS_BET_WINDOW}s*...",
        reply_markup=markup, parse_mode="Markdown")

    def launch():
        time.sleep(config.VERSUS_BET_WINDOW)
        if chat_id in versus_games and versus_games[chat_id].get("state") == "betting":
            _countdown_then_round(bot, chat_id)
    threading.Thread(target=launch, daemon=True).start()

def handle_versus_bet(bot, call):
    parts    = call.data.split("_")
    chat_id  = int(parts[1])
    pick     = parts[2]
    amount   = int(parts[3])

    if chat_id not in versus_games or versus_games[chat_id]["state"] != "betting":
        bot.answer_callback_query(call.id, "⏰ Betting is closed.", show_alert=True)
        return

    vs       = versus_games[chat_id]
    user_id  = call.from_user.id
    username = call.from_user.username or call.from_user.first_name

    if user_id in [vs["challenger_id"], vs["target_id"]]:
        bot.answer_callback_query(call.id, "⚠️ Players can't bet on themselves!", show_alert=True)
        return
    if user_id in vs["bets"]:
        existing  = vs["bets"][user_id]
        pname     = vs["challenger_name"] if existing["pick"] == "challenger" else vs["target_name"]
        bot.answer_callback_query(call.id,
            f"⚠️ Already bet {existing['amount']} pts on {pname}!", show_alert=True)
        return

    data = database.load_remote_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = database.get_user(data, chat_str, user_str, username)
    user_pts = u.get("points", 0)
    if user_pts < amount:
        bot.answer_callback_query(call.id,
            f"❌ Not enough points! You have {user_pts} pts.", show_alert=True)
        return

    database.deduct_points(bot, chat_id, user_id, username, amount)
    vs["bets"][user_id] = {"amount": amount, "pick": pick, "username": username}
    pname = vs["challenger_name"] if pick == "challenger" else vs["target_name"]
    bot.answer_callback_query(call.id, f"🎰 Bet {amount} pts on {pname}!", show_alert=True)

def _score_display(vs):
    c = vs["score"].get(str(vs["challenger_id"]), 0)
    t = vs["score"].get(str(vs["target_id"]), 0)
    return f"*{vs['challenger_name']}* {c} — {t} *{vs['target_name']}*"

def _countdown_then_round(bot, chat_id):
    if chat_id not in versus_games:
        return
    for emoji in ["3️⃣", "2️⃣", "1️⃣"]:
        send_and_delete(bot, chat_id, emoji)
        time.sleep(1)
    _start_versus_round(bot, chat_id)

def _start_versus_round(bot, chat_id):
    import telebot
    if chat_id not in versus_games:
        return

    vs           = versus_games[chat_id]
    vs["state"]  = "active"
    vs["round"] += 1
    vs["answers"]      = {}
    vs["round_closed"] = False

    trivia_data = load_json_file(config.TRIVIA_DB) or []
    if not trivia_data:
        send_and_delete(bot, chat_id, "❌ Could not load trivia question.")
        _end_versus_match(bot, chat_id, winner_id=None, reason="error")
        return

    q       = random.choice(trivia_data)
    options = q["options"]
    answer  = q["answer"]

    vs["question_answer"]  = answer
    vs["question_display"] = options[ord(answer) - ord("A")]

    bet_summary = ""
    if vs["round"] == 1 and vs["bets"]:
        c_bets = sum(b["amount"] for b in vs["bets"].values() if b["pick"] == "challenger")
        t_bets = sum(b["amount"] for b in vs["bets"].values() if b["pick"] == "target")
        bet_summary = (
            f"\n💰 *Bets:* {vs['challenger_name']}: {c_bets} pts"
            f" | {vs['target_name']}: {t_bets} pts"
        )

    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[
        telebot.types.InlineKeyboardButton(
            f"{l}. {options[i]}", callback_data=f"vsans_{chat_id}_{l}"
        )
        for i, l in enumerate(["A","B","C","D"]) if i < len(options)
    ])

    send_and_delete(bot, chat_id,
        f"⚔️ *ROUND {vs['round']}*\n"
        f"{_score_display(vs)}\n\n"
        f"*{q['question']}*\n\n"
        f"A. {options[0]}\nB. {options[1]}\nC. {options[2]}\nD. {options[3]}\n\n"
        f"⏱️ *Both players have {config.VERSUS_QUESTION_TIME}s to answer!*{bet_summary}",
        reply_markup=markup, parse_mode="Markdown")

    round_num = vs["round"]

    def close_round():
        time.sleep(config.VERSUS_QUESTION_TIME)
        if (chat_id not in versus_games or
                versus_games[chat_id].get("state") != "active" or
                versus_games[chat_id].get("round") != round_num or
                versus_games[chat_id].get("round_closed")):
            return
        _evaluate_round(bot, chat_id)

    threading.Thread(target=close_round, daemon=True).start()

def handle_versus_answer(bot, call):
    parts   = call.data.split("_")
    chat_id = int(parts[1])
    chosen  = parts[2]

    if chat_id not in versus_games or versus_games[chat_id].get("state") != "active":
        bot.answer_callback_query(call.id, "⏰ No active versus round.", show_alert=True)
        return

    vs      = versus_games[chat_id]
    user_id = call.from_user.id

    if user_id not in [vs["challenger_id"], vs["target_id"]]:
        bot.answer_callback_query(call.id, "👀 Spectators watch only!", show_alert=True)
        return

    if user_id in vs["answers"]:
        bot.answer_callback_query(call.id, "⏳ Answer recorded! Waiting for opponent...",
                                  show_alert=False)
        return

    vs["answers"][user_id] = {"chosen": chosen, "timestamp": time.time()}
    bot.answer_callback_query(call.id, "✅ Answer recorded! Waiting for opponent...")

    if (vs["challenger_id"] in vs["answers"] and
            vs["target_id"] in vs["answers"]):
        _evaluate_round(bot, chat_id)

def _evaluate_round(bot, chat_id):
    if chat_id not in versus_games:
        return

    vs = versus_games[chat_id]

    if vs.get("round_closed"):
        return
    vs["round_closed"] = True
    vs["state"]        = "between_rounds"

    correct_answer = vs["question_answer"]
    c_id           = vs["challenger_id"]
    t_id           = vs["target_id"]
    c_ans          = vs["answers"].get(c_id)
    t_ans          = vs["answers"].get(t_id)

    c_correct = c_ans and c_ans["chosen"] == correct_answer
    t_correct = t_ans and t_ans["chosen"] == correct_answer

    winner_id   = None
    result_text = ""

    if c_correct and t_correct:
        if c_ans["timestamp"] <= t_ans["timestamp"]:
            winner_id   = c_id
            winner_name = vs["challenger_name"]
            loser_name  = vs["target_name"]
        else:
            winner_id   = t_id
            winner_name = vs["target_name"]
            loser_name  = vs["challenger_name"]
        result_text = (
            f"✅ Both players got it right!\n"
            f"⚡ *{winner_name}* was faster and wins the round!\n"
            f"Better speed next time, *{loser_name}*!"
        )
    elif c_correct:
        winner_id   = c_id
        winner_name = vs["challenger_name"]
        result_text = f"✅ *{winner_name}* got it right!"
        if not t_ans:
            result_text += f"\n⏰ *{vs['target_name']}* didn't answer in time."
        else:
            result_text += f"\n❌ *{vs['target_name']}* got it wrong."
    elif t_correct:
        winner_id   = t_id
        winner_name = vs["target_name"]
        result_text = f"✅ *{winner_name}* got it right!"
        if not c_ans:
            result_text += f"\n⏰ *{vs['challenger_name']}* didn't answer in time."
        else:
            result_text += f"\n❌ *{vs['challenger_name']}* got it wrong."
    else:
        if not c_ans and not t_ans:
            vs["consecutive_skips"] = vs.get("consecutive_skips", 0) + 1
            result_text = "⏰ Neither player answered in time! No point awarded."
        else:
            vs["consecutive_skips"] = 0
            result_text = (
                f"❌ Both players got it wrong! No point awarded.\n"
                f"Correct answer: *{correct_answer}. {vs['question_display']}*"
            )

    if winner_id:
        vs["score"][str(winner_id)] = vs["score"].get(str(winner_id), 0) + 1
        vs["consecutive_skips"]     = 0
        username = (vs["challenger_name"] if winner_id == c_id else vs["target_name"])
        database.reward_user(bot, chat_id, winner_id, username,
                             amount=config.POINTS_VERSUS_WIN // 3)

        if winner_id:
            data = database.load_remote_json(config.GROUP_DATA_FILE, {})
            chat_str = str(chat_id)
            user_str = str(winner_id)
            u = database.get_user(data, chat_str, user_str, username)
            u["versus_wins"] = u.get("versus_wins", 0) + 1
            database.save_remote_json(config.GROUP_DATA_FILE, data)
            database.check_achievements(bot, chat_id, winner_id, username)

    correct_reveal = ""
    if not (c_correct or t_correct):
        correct_reveal = f"\nCorrect answer: *{correct_answer}. {vs['question_display']}*"

    send_and_delete(bot, chat_id,
        f"{result_text}{correct_reveal}\n\n"
        f"📊 {_score_display(vs)}",
        parse_mode="Markdown")

    if vs.get("consecutive_skips", 0) >= 2:
        send_and_delete(bot, chat_id,
            "⚠️ No activity for 2 rounds — match ended due to inactivity.",
            parse_mode="Markdown")
        _end_versus_match(bot, chat_id, winner_id=None, reason="inactivity")
        return

    _next_round_or_end(bot, chat_id)

def _next_round_or_end(bot, chat_id):
    if chat_id not in versus_games:
        return

    vs      = versus_games[chat_id]
    c_score = vs["score"].get(str(vs["challenger_id"]), 0)
    t_score = vs["score"].get(str(vs["target_id"]), 0)

    if c_score >= WIN_TARGET:
        _end_versus_match(bot, chat_id, winner_id=vs["challenger_id"], reason="score")
        return
    if t_score >= WIN_TARGET:
        _end_versus_match(bot, chat_id, winner_id=vs["target_id"], reason="score")
        return

    def next_round():
        time.sleep(3)
        if chat_id in versus_games:
            _countdown_then_round(bot, chat_id)
    threading.Thread(target=next_round, daemon=True).start()

def _end_versus_match(bot, chat_id, winner_id, reason):
    if chat_id not in versus_games:
        return

    vs      = versus_games[chat_id]
    c_name  = vs["challenger_name"]
    t_name  = vs["target_name"]
    c_score = vs["score"].get(str(vs["challenger_id"]), 0)
    t_score = vs["score"].get(str(vs["target_id"]), 0)

    if reason == "draw":
        send_and_delete(bot, chat_id,
            f"🤝 *MATCH DRAWN!*\n\n"
            f"⚔️ {c_name} {c_score} — {t_score} {t_name}\n\n"
            f"A worthy battle! 💪",
            parse_mode="Markdown")
        _payout_bets(bot, chat_id, winner_pick=None)

    elif reason in ("inactivity", "error"):
        send_and_delete(bot, chat_id,
            f"⚠️ *Match ended.* Final: {c_name} {c_score} — {t_score} {t_name}",
            parse_mode="Markdown")
        _payout_bets(bot, chat_id, winner_pick=None)

    elif reason == "forfeit":
        winner_name = c_name if winner_id == vs["challenger_id"] else t_name
        loser_name  = t_name if winner_id == vs["challenger_id"] else c_name
        send_and_delete(bot, chat_id,
            f"🏳️ *{loser_name} has forfeited!*\n\n"
            f"🏆 *{winner_name}* wins by forfeit!\n"
            f"Final: {c_name} {c_score} — {t_score} {t_name}",
            parse_mode="Markdown")
        winner_pick = "challenger" if winner_id == vs["challenger_id"] else "target"
        _payout_bets(bot, chat_id, winner_pick)

    else:
        winner_name = c_name if winner_id == vs["challenger_id"] else t_name
        loser_name  = t_name if winner_id == vs["challenger_id"] else c_name
        winner_pick = "challenger" if winner_id == vs["challenger_id"] else "target"
        username    = winner_name

        pts, streak, mult, final = database.reward_user(
            bot, chat_id, winner_id, username, config.POINTS_VERSUS_WIN)
        streak_txt = f" 🔥 x{int(mult)}!" if streak > 1 else ""

        send_and_delete(bot, chat_id,
            f"⚔️ *MATCH OVER!*\n\n"
            f"🏆 *{winner_name}* wins!\n"
            f"Final score: *{c_name} {c_score} — {t_score} {t_name}*\n\n"
            f"+{final} pts to {winner_name}{streak_txt}\n"
            f"GG *{loser_name}*! 💪",
            parse_mode="Markdown")
        _payout_bets(bot, chat_id, winner_pick)

    if chat_id in versus_games:
        del versus_games[chat_id]

def handle_versus_forfeit(bot, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id not in versus_games:
        bot.reply_to(message, "❌ No active versus match to forfeit.")
        return

    vs = versus_games[chat_id]
    if user_id not in [vs["challenger_id"], vs["target_id"]]:
        bot.reply_to(message, "❌ You're not in this match.")
        return

    winner_id = vs["target_id"] if user_id == vs["challenger_id"] else vs["challenger_id"]
    _end_versus_match(bot, chat_id, winner_id=winner_id, reason="forfeit")

def _payout_bets(bot, chat_id, winner_pick):
    if chat_id not in versus_games:
        return
    bets = versus_games[chat_id].get("bets", {})
    if not bets:
        return

    if winner_pick is None:
        total = 0
        for uid, bet in bets.items():
            data = database.load_remote_json(config.GROUP_DATA_FILE, {})
            chat_str = str(chat_id)
            user_str = str(uid)
            u = database.get_user(data, chat_str, user_str, bet["username"])
            u["points"] += bet["amount"]
            database.save_remote_json(config.GROUP_DATA_FILE, data)
            total += bet["amount"]
        send_and_delete(bot, chat_id,
            f"🔁 *All bets refunded.* ({total} pts total)",
            parse_mode="Markdown")
        return

    winners = {uid: b for uid, b in bets.items() if b["pick"] == winner_pick}
    losers  = {uid: b for uid, b in bets.items() if b["pick"] != winner_pick}
    pot     = sum(b["amount"] for b in losers.values())

    if not winners:
        send_and_delete(bot, chat_id,
            f"💸 No one bet on the winner! Pot of *{pot} pts* is gone.",
            parse_mode="Markdown")
        return

    total_stake = sum(b["amount"] for b in winners.values())
    msg = "💰 *BET RESULTS*\n\n"
    for uid, bet in winners.items():
        winnings = bet["amount"] + int(pot * (bet["amount"] / total_stake))
        data = database.load_remote_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(uid)
        u = database.get_user(data, chat_str, user_str, bet["username"])
        u["points"] += winnings
        database.save_remote_json(config.GROUP_DATA_FILE, data)
        msg += f"✅ *{bet['username']}* — bet {bet['amount']} pts, won *{winnings} pts*!\n"
    for uid, bet in losers.items():
        msg += f"❌ *{bet['username']}* — lost {bet['amount']} pts\n"
    send_and_delete(bot, chat_id, msg, parse_mode="Markdown")

# ---------------------------------------------------------------------------
# DAILY CHALLENGE
# ---------------------------------------------------------------------------

def post_daily_challenge(bot):
    trivia_data = load_json_file(config.TRIVIA_DB) or []
    if not trivia_data:
        return

    state = database.load_remote_json(config.DAILY_FILE, {})
    today = str(datetime.date.today())
    if state.get("date") == today:
        return

    q       = random.choice(trivia_data)
    options = q["options"]
    answer  = q["answer"]

    state = {
        "date": today, "question": q["question"], "options": options,
        "answer": answer, "display": options[ord(answer) - ord("A")],
        "category": q["category"], "answered": [],
    }
    database.save_remote_json(config.DAILY_FILE, state)

    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[
        telebot.types.InlineKeyboardButton(f"{l}. {options[i]}", callback_data=f"daily_{l}")
        for i, l in enumerate(["A","B","C","D"]) if i < len(options)
    ])

    text = (
        f"🌟 *DAILY CHALLENGE — {today}*\n\n"
        f"Category: *{q['category']}*\n\n"
        f"*{q['question']}*\n\n"
        f"A. {options[0]}\nB. {options[1]}\nC. {options[2]}\nD. {options[3]}\n\n"
        f"🏆 First correct wins *{config.POINTS_DAILY_CHALLENGE} pts!*\n"
        f"⚠️ One attempt per person!"
    )

    groups = database.get_all_groups()
    for group_id in groups:
        try:
            members = database.get_all_members(group_id)
            tag_line = " ".join([f"[{name}](tg://user?id={uid})" for uid, name in members])
            full_msg = f"{text}\n\n🌱 _Tagging the whole family:_\n{tag_line}"
            bot.send_message(group_id, full_msg, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            print(f"Daily challenge post failed for {group_id}: {e}")

def handle_daily_answer(bot, call):
    chosen   = call.data.split("_")[1]
    user_id  = call.from_user.id
    chat_id  = call.message.chat.id
    username = call.from_user.username or call.from_user.first_name

    if database.is_muted(bot, chat_id, user_id):
        bot.answer_callback_query(call.id, "🔇 You are muted!", show_alert=True)
        return

    state = database.load_remote_json(config.DAILY_FILE, {})
    if not state or state.get("date") != str(datetime.date.today()):
        bot.answer_callback_query(call.id, "This challenge has expired.", show_alert=True)
        return
    if user_id in state.get("answered", []):
        bot.answer_callback_query(call.id, "❌ You already answered today's challenge!", show_alert=True)
        return

    state["answered"].append(user_id)
    database.save_remote_json(config.DAILY_FILE, state)

    if chosen == state["answer"]:
        pts, streak, mult, final = database.reward_user(
            bot, chat_id, user_id, username, config.POINTS_DAILY_CHALLENGE)

        data = database.load_remote_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(user_id)
        u = database.get_user(data, chat_str, user_str, username)
        u["daily_wins"] = u.get("daily_wins", 0) + 1
        database.save_remote_json(config.GROUP_DATA_FILE, data)
        database.check_achievements(bot, chat_id, user_id, username)

        bot.answer_callback_query(call.id, f"✅ CORRECT! +{final} pts!", show_alert=True)
        send_and_delete(bot, chat_id,
            f"🌟 *{username}* cracked the Daily Challenge!\n"
            f"Answer: *{state['answer']}. {state['display']}*\n+{final} pts! 🔥",
            parse_mode="Markdown")
    else:
        database.penalise_wrong(bot, chat_id, user_id, username)
        bot.answer_callback_query(call.id, "❌ Wrong! Better luck tomorrow.", show_alert=True)

# ---------------------------------------------------------------------------
# ANSWER CHECKER (UPDATED with GUESS and LIGHTNING support)
# ---------------------------------------------------------------------------

def check_user_answer(bot, message):
    chat_id  = message.chat.id
    user_id  = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    database.track_member(bot, chat_id, user_id, username)

    if database.is_muted(bot, chat_id, user_id):
        send_and_delete(bot, chat_id, "🔇 You are muted! Wait until your mute expires.")
        return True

    if chat_id not in active_games:
        return False

    if message.text and message.text.startswith('/'):
        cmd = message.text.split()[0].split('@')[0].lower()
        if cmd == '/stop':
            del active_games[chat_id]
            send_and_delete(bot, chat_id, "🛑 Game canceled.")
            return True
        if cmd == '/hint':
            process_hint(bot, message=message)
            return True
        return False

    if active_games[chat_id].get("type") == "trivia":
        return True

    user_guess = message.text.strip().lower()
    session    = active_games[chat_id]

    if session.get("type") == "year":
        send_and_delete(bot, chat_id, "⚠️ Please use the buttons to select a year!")
        return True

    # --- GUESS GAME handling ---
    if session.get("type") == "guess":
        aliases = session.get("aliases", [])
        if is_character_match(user_guess, session["answer"], aliases):
            hints_shown = session.get("hints_shown", 1)
            # Points: 100 - (hints_shown - 1) * 20, min 10
            points = max(10, config.POINTS_GUESS_BASE - (hints_shown - 1) * config.POINTS_GUESS_STEP)
            pts, streak, mult, final = database.reward_user(bot, chat_id, user_id, username, points)
            streak_txt = f"\n🔥 Streak: *{streak}* (x{mult})!" if streak > 1 else ""

            import telebot
            markup = telebot.types.InlineKeyboardMarkup()
            markup.row(telebot.types.InlineKeyboardButton("⏭️ Next Game", callback_data=f"nextgame_{chat_id}_guess"))

            send_and_delete(
                bot,
                chat_id,
                f"🎉 *CORRECT!* It was *{session['display']}*!\n"
                f"+{final} pts (Total: {pts}){streak_txt}\n"
                f"💡 Hints used: {hints_shown - 1}",
                reply_markup=markup, parse_mode="Markdown"
            )
            del active_games[chat_id]
            return True
        else:
            # Wrong answer – streak freeze logic is handled below
            pass

    double_down_active = False
    if session.get("type") in ("character", "picture"):
        data = database.load_remote_json(config.GROUP_DATA_FILE, {})
        chat_str = str(chat_id)
        user_str = str(user_id)
        u = database.get_user(data, chat_str, user_str, username)
        if u.get("powerups", {}).get("double_down", 0) > 0:
            double_down_active = True

    aliases = session.get("aliases", [])
    if is_character_match(user_guess, session["answer"], aliases):
        base_pts = config.POINTS_CHARACTER_GAME

        if double_down_active:
            base_pts *= 2
            database.use_powerup(bot, chat_id, user_id, username, "double_down")

        pts, streak, mult, final = database.reward_user(bot, chat_id, user_id, username, base_pts)
        streak_txt = f"\n🔥 Streak: *{streak}* (x{mult})!" if streak > 1 else ""

        if double_down_active:
            streak_txt += " ⬆️ *Double Down active!*"

        import telebot
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(telebot.types.InlineKeyboardButton(
            "⏭️ Next Game", callback_data=f"nextgame_{chat_id}_{session['type']}"))

        send_and_delete(
            bot,
            chat_id,
            f"🎉 *CORRECT!* It was *{session['display']}*!\n"
            f"+{final} pts (Total: {pts}){streak_txt}",
            reply_markup=markup, parse_mode="Markdown"
        )
        del active_games[chat_id]
        return True

    # Wrong answer – handle streak freeze
    data = database.load_remote_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = database.get_user(data, chat_str, user_str, username)
    if u.get("powerups", {}).get("streak_freeze", 0) > 0:
        database.use_powerup(bot, chat_id, user_id, username, "streak_freeze")
        send_and_delete(bot, chat_id, "🧊 *Streak Freeze activated!* Your streak is safe this time.")
    else:
        database.penalise_wrong(bot, chat_id, user_id, username)
        send_and_delete(bot, chat_id, "❌ Wrong! Keep trying or use /hint for a clue.")

    return True

# ---------------------------------------------------------------------------
# CALLBACK ROUTER (UPDATED with GUESS and LIGHTNING)
# ---------------------------------------------------------------------------

def handle_game_callback(bot, call):
    data = call.data

    if data.startswith("trivia_"):
        handle_trivia_answer(bot, call)
    elif data.startswith("year_ans_"):
        handle_year_answer(bot, call)
    elif data.startswith("vs_accept_"):
        handle_versus_accept(bot, call)
    elif data.startswith("vs_decline_"):
        chat_id = int(data.split("_")[2])
        if chat_id in versus_games:
            del versus_games[chat_id]
        bot.answer_callback_query(call.id)
        send_and_delete(bot, chat_id, "❌ Challenge declined.")
    elif data.startswith("vsbet_"):
        handle_versus_bet(bot, call)
    elif data.startswith("vsans_"):
        handle_versus_answer(bot, call)
    elif data.startswith("daily_"):
        handle_daily_answer(bot, call)
    elif data.startswith("hint_"):
        process_hint(bot, call=call)
        bot.answer_callback_query(call.id)
    elif data.startswith("nextgame_"):
        handle_next_game(bot, call)
    elif data.startswith("stopgame_"):
        chat_id = int(data.split("_")[1])
        if chat_id in active_games:
            del active_games[chat_id]
        bot.answer_callback_query(call.id)
        send_and_delete(bot, chat_id, "🛑 Game stopped.")
    elif data.startswith("guess_hint_"):
        handle_guess_hint(bot, call)
    elif data.startswith("lightning_ans_"):
        handle_lightning_answer(bot, call)

# ---------------------------------------------------------------------------
# GUESS GAME (Character Quiz) – NEW
# ---------------------------------------------------------------------------

def start_guess_game(bot, chat_id, user_id):
    """Text-based character quiz with progressive hints (max 5)."""
    if _check_one_game_lock(bot, chat_id, user_id, game_type="guess"):
        return None

    # Pick a random character from all char DBs
    characters = _load_all(config.CHAR_ALL_DBS)
    if not characters:
        send_and_delete(bot, chat_id, "⚠️ No character data found.")
        return None

    q = random.choice(characters)
    name = str(q.get('name', 'Unknown')).strip()
    hints = q.get('hints') or q.get('hint', 'No hints available.')
    hints_list = hints if isinstance(hints, list) else [hints]
    img_url = q.get('image') or q.get('image_url') or q.get('img')
    aliases = q.get('aliases', [])

    # Store session
    active_games[chat_id] = {
        "type": "guess",
        "answer": name.lower(),
        "display": name,
        "hints_list": hints_list,
        "hints_shown": 1,          # we already show hint 1
        "max_hints": config.MAX_GUESS_HINTS,
        "img_url": img_url,
        "aliases": [a.lower() for a in aliases],
        "answered": False,
    }

    last_game_type[chat_id] = "guess"
    last_game_category[chat_id] = "random"

    # Build initial message with hint 1
    hint_text = hints_list[0] if hints_list else "No extra hints available."
    text = f"🔍 *GUESS THE CHARACTER – QUIZ*\n\n"
    text += f"💡 *Hint 1:* {hint_text}\n\n"
    text += f"Type your answer as a text message.\n"
    text += f"Use the buttons below for more hints.\n\n"
    text += f"🏆 *Points:* 100 pts (0 hints) → 10 pts (5 hints)\n"
    text += f"❌ Wrong: -15 pts (streak breaks)"

    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("💡 Next Hint", callback_data=f"guess_hint_{chat_id}"),
        telebot.types.InlineKeyboardButton("⏭️ Next Game", callback_data=f"nextgame_{chat_id}_guess"),
        telebot.types.InlineKeyboardButton("🛑 Stop", callback_data=f"stopgame_{chat_id}"),
    )

    send_and_delete(bot, chat_id, text, reply_markup=markup, parse_mode="Markdown")
    return q

def handle_guess_hint(bot, call):
    chat_id = call.message.chat.id
    if chat_id not in active_games or active_games[chat_id].get("type") != "guess":
        bot.answer_callback_query(call.id, "No active guess game.")
        return

    session = active_games[chat_id]
    hints_shown = session.get("hints_shown", 1)
    max_hints = session.get("max_hints", config.MAX_GUESS_HINTS)

    if hints_shown >= max_hints:
        bot.answer_callback_query(call.id, f"Max {max_hints} hints already shown.", show_alert=True)
        return

    # Increment and show next hint
    hints_shown += 1
    session["hints_shown"] = hints_shown
    hints_list = session.get("hints_list", [])
    hint_text = hints_list[hints_shown - 1] if hints_shown - 1 < len(hints_list) else "No more hints."

    # If this is the 5th hint, send the image
    if hints_shown == max_hints:
        img_url = session.get("img_url")
        name = session.get("display", "Character")
        if img_url:
            img_data = get_image_bytes(bot, name, LOCAL_CHAR_DIR, img_url)
            if img_data:
                # Send the image as a new message
                bot.send_photo(chat_id, img_data, caption=f"🖼️ *Hint {hints_shown}:* Image of the character")
                bot.answer_callback_query(call.id, f"Hint {hints_shown} shown (image sent).")
                return

    # Send a new message with the hint
    bot.send_message(chat_id, f"💡 *Hint {hints_shown}:* {hint_text}", parse_mode="Markdown")
    bot.answer_callback_query(call.id, f"Hint {hints_shown} shown.")

# ---------------------------------------------------------------------------
# LIGHTNING ROUND – NEW
# ---------------------------------------------------------------------------

def start_lightning_round(bot, chat_id, user_id):
    """High-risk rapid-fire trivia. 5 questions, 15s each."""
    username = database.get_user_data_field(bot, chat_id, user_id, "username") or "Player"

    # Check if already in a lightning session
    if user_id in lightning_sessions:
        send_and_delete(bot, chat_id, "⚠️ You already have an active Lightning Round. Finish it first.")
        return

    # Check cooldown
    data = database.load_remote_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = database.get_user(data, chat_str, user_str, username)
    last = u.get("last_lightning", 0)
    if time.time() - last < config.LIGHTNING_COOLDOWN:
        remaining = int((last + config.LIGHTNING_COOLDOWN - time.time()) / 60)
        send_and_delete(bot, chat_id, f"⏳ Lightning Round on cooldown. Try again in {remaining} minutes.")
        return

    # Check entry fee
    if u.get("points", 0) < config.LIGHTNING_ENTRY_FEE:
        send_and_delete(bot, chat_id, f"❌ You need *{config.LIGHTNING_ENTRY_FEE}* points to enter the Lightning Round. You have {u.get('points',0)}.")
        return

    # Deduct fee
    database.deduct_points(bot, chat_id, user_id, username, config.LIGHTNING_ENTRY_FEE)

    # Load trivia and pick 5 questions (1 easy, 3 medium, 1 hard)
    all_q = database.load_trivia_from_github()
    if not all_q:
        # Refund
        database.reward_user(bot, chat_id, user_id, username, config.LIGHTNING_ENTRY_FEE)
        send_and_delete(bot, chat_id, "⚠️ Trivia database unavailable. Entry fee refunded.")
        return

    easy = [q for q in all_q if q.get("difficulty", "").lower() == "easy"]
    medium = [q for q in all_q if q.get("difficulty", "").lower() == "medium"]
    hard = [q for q in all_q if q.get("difficulty", "").lower() == "hard"]

    selected = []
    if easy: selected.append(random.choice(easy))
    else: selected.append(random.choice(all_q))
    for _ in range(3):
        if medium: selected.append(random.choice(medium))
        else: selected.append(random.choice(all_q))
    if hard: selected.append(random.choice(hard))
    else: selected.append(random.choice(all_q))

    # Shuffle the selected questions to avoid difficulty order being predictable
    random.shuffle(selected)

    # Build session
    lightning_sessions[user_id] = {
        "chat_id": chat_id,
        "username": username,
        "questions": selected,
        "current_q": 0,
        "correct": 0,
        "answers": [],         # store user's choices (for display)
        "finished": False,
        "start_time": time.time(),
        "timer_running": False,
    }

    # Update last_lightning
    u["last_lightning"] = time.time()
    database.save_remote_json(config.GROUP_DATA_FILE, data)

    # Start first question
    _send_lightning_question(bot, user_id)

def _send_lightning_question(bot, user_id):
    session = lightning_sessions.get(user_id)
    if not session or session.get("finished"):
        return

    chat_id = session["chat_id"]
    q_index = session["current_q"]
    if q_index >= len(session["questions"]):
        _finish_lightning_round(bot, user_id)
        return

    q = session["questions"][q_index]
    options = q["options"]
    letters = ["A", "B", "C", "D"]

    import telebot
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    for i, opt in enumerate(options):
        markup.add(telebot.types.InlineKeyboardButton(
            f"{letters[i]}. {opt}",
            callback_data=f"lightning_ans_{user_id}_{q_index}_{i}"
        ))

    text = f"⚡ *LIGHTNING ROUND* ⚡\n\n"
    text += f"Question {q_index+1} of {len(session['questions'])}\n"
    text += f"*{q['question']}*\n\n"
    for i, opt in enumerate(options):
        text += f"{letters[i]}. {opt}\n"
    text += f"\n⏱️ You have {config.LIGHTNING_QUESTION_TIME} seconds!"

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    # Start a timer for this question
    session["timer_running"] = True
    def timeout():
        time.sleep(config.LIGHTNING_QUESTION_TIME)
        if session.get("timer_running") and not session.get("finished"):
            # Mark as wrong and advance
            session["answers"].append(None)  # no answer
            session["current_q"] += 1
            session["timer_running"] = False
            # If we have more questions, send next; else finish
            if session["current_q"] < len(session["questions"]):
                _send_lightning_question(bot, user_id)
            else:
                _finish_lightning_round(bot, user_id)
    threading.Thread(target=timeout, daemon=True).start()

def handle_lightning_answer(bot, call):
    parts = call.data.split("_")
    user_id = int(parts[2])
    q_index = int(parts[3])
    choice = int(parts[4])

    if user_id not in lightning_sessions:
        bot.answer_callback_query(call.id, "Session expired.", show_alert=True)
        return

    session = lightning_sessions[user_id]
    if session.get("finished"):
        bot.answer_callback_query(call.id, "This round is already finished.", show_alert=True)
        return

    # Check if they already answered this question
    if len(session["answers"]) > q_index:
        bot.answer_callback_query(call.id, "You already answered this question!", show_alert=True)
        return

    # Validate q_index matches current
    if q_index != session["current_q"]:
        bot.answer_callback_query(call.id, "This question is outdated.", show_alert=True)
        return

    # Cancel the timer (by marking timer_running False)
    session["timer_running"] = False

    # Check correctness
    q = session["questions"][q_index]
    correct = (choice == ord(q["answer"]) - ord("A"))
    session["answers"].append(choice)
    if correct:
        session["correct"] += 1
        feedback = "✅ Correct!"
    else:
        feedback = "❌ Wrong!"

    bot.answer_callback_query(call.id, feedback, show_alert=False)

    # Advance to next question
    session["current_q"] += 1
    if session["current_q"] < len(session["questions"]):
        _send_lightning_question(bot, user_id)
    else:
        _finish_lightning_round(bot, user_id)

def _finish_lightning_round(bot, user_id):
    session = lightning_sessions.get(user_id)
    if not session or session.get("finished"):
        return
    session["finished"] = True

    chat_id = session["chat_id"]
    correct = session["correct"]
    total = len(session["questions"])
    reward = config.LIGHTNING_REWARDS.get(correct, 0)

    # Apply points
    username = session["username"]
    if reward > 0:
        database.reward_user(bot, chat_id, user_id, username, reward)
        points_text = f"+{reward} pts"
    else:
        database.deduct_points(bot, chat_id, user_id, username, abs(reward))
        points_text = f"{reward} pts (lost entry fee)"

    summary = f"⚡ *LIGHTNING ROUND COMPLETE*\n\n"
    summary += f"✅ Correct: {correct}/{total}\n"
    summary += f"💰 Points: {points_text}\n\n"
    for i, q in enumerate(session["questions"]):
        ans = session["answers"][i] if i < len(session["answers"]) else None
        correct_letter = q["answer"]
        correct_text = q["options"][ord(correct_letter) - ord("A")]
        if ans is not None:
            chosen_letter = chr(ord("A") + ans)
            chosen_text = q["options"][ans]
            status = "✅" if chosen_letter == correct_letter else "❌"
            summary += f"{status} Q{i+1}: Your answer: {chosen_letter}. {chosen_text} (Correct: {correct_letter}. {correct_text})\n"
        else:
            summary += f"⏰ Q{i+1}: Timeout (Correct: {correct_letter}. {correct_text})\n"

    bot.send_message(chat_id, summary, parse_mode="Markdown")

    # Clean up
    del lightning_sessions[user_id]
