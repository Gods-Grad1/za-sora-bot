import os
import json
import time
import csv
import datetime
import sqlite3
import threading
import requests
import config

CSV_DATA_CACHE = {}
_file_locks = {}
BROADCAST_DB = "broadcasts.db"
GLOBAL_CHAT_ID = None

# ---------------------------------------------------------------------------
# FILE LOCKS
# ---------------------------------------------------------------------------

def get_lock(filepath):
    if filepath not in _file_locks:
        _file_locks[filepath] = threading.Lock()
    return _file_locks[filepath]

def save_json(bot, filepath, data):
    lock = get_lock(filepath)
    with lock:
        tmp_file = filepath + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, filepath)
        except Exception as e:
            log_error_to_admin(bot, "Atomic Save Fault", e)

def load_json(filepath, default_value):
    if not os.path.exists(filepath):
        with open(filepath, "w") as f:
            json.dump(default_value, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
    with open(filepath, "r") as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# BROADCAST DATABASE (SQLite)
# ---------------------------------------------------------------------------

_broadcast_lock = threading.Lock()

def init_broadcast_db():
    with _broadcast_lock:
        conn = sqlite3.connect(BROADCAST_DB)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,  -- NULL means global
                message TEXT NOT NULL,
                send_time INTEGER NOT NULL,
                sent INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()

def add_broadcast(bot, chat_id, message, send_time):
    with _broadcast_lock:
        conn = sqlite3.connect(BROADCAST_DB)
        c = conn.cursor()
        c.execute(
            "INSERT INTO broadcasts (chat_id, message, send_time, sent) VALUES (?, ?, ?, 0)",
            (chat_id, message, send_time)
        )
        conn.commit()
        conn.close()

def get_pending_broadcasts():
    with _broadcast_lock:
        conn = sqlite3.connect(BROADCAST_DB)
        c = conn.cursor()
        now = int(time.time()) + 5  # 5-second grace window
        c.execute(
            "SELECT id, chat_id, message, send_time FROM broadcasts WHERE sent = 0 AND send_time <= ?",
            (now,)
        )
        rows = c.fetchall()
        conn.close()
        return [{"id": r[0], "chat_id": r[1], "message": r[2], "send_time": r[3]} for r in rows]

def mark_broadcast_sent(bot, broadcast_id):
    with _broadcast_lock:
        conn = sqlite3.connect(BROADCAST_DB)
        c = conn.cursor()
        c.execute("UPDATE broadcasts SET sent = 1 WHERE id = ?", (broadcast_id,))
        conn.commit()
        conn.close()

def get_all_broadcasts():
    with _broadcast_lock:
        conn = sqlite3.connect(BROADCAST_DB)
        c = conn.cursor()
        c.execute("SELECT id, chat_id, message, send_time, sent FROM broadcasts ORDER BY send_time")
        rows = c.fetchall()
        conn.close()
        return [{"id": r[0], "chat_id": r[1], "message": r[2], "send_time": r[3], "sent": r[4]} for r in rows]

# ---------------------------------------------------------------------------
# (Rest of database.py – unchanged)
# ---------------------------------------------------------------------------

def log_error_to_admin(bot, context, exception):
    error_msg = f"⚠️ *BOT ERROR*\n📌 Context: {context}\n💥 `{str(exception)}`"
    print(error_msg)
    try:
        bot.send_message(config.ADMIN_ID, error_msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Failed to alert admin: {e}")

def fetch_csv_cached(bot, url, duration=300):
    now = time.time()
    if url in CSV_DATA_CACHE:
        timestamp, data = CSV_DATA_CACHE[url]
        if now - timestamp < duration:
            return data
    try:
        response = requests.get(url, timeout=10, proxies={})
        response.encoding = 'utf-8'
        lines = response.text.splitlines()
        rows = list(csv.reader(lines))
        CSV_DATA_CACHE[url] = (now, rows)
        return rows
    except Exception as e:
        log_error_to_admin(bot, "CSV Fetch Error", e)
        if url in CSV_DATA_CACHE:
            return CSV_DATA_CACHE[url][1]
        return []

def _now_month_key():
    return datetime.datetime.now().strftime("%Y-%m")

def _now_year_key():
    return str(datetime.datetime.now().year)

def get_user(data, chat_str, user_str, username):
    if chat_str not in data:
        data[chat_str] = {}
    if user_str not in data[chat_str]:
        data[chat_str][user_str] = {
            "username":        username or "Player",
            "points":          0,
            "monthly_points":  {},
            "yearly_points":   {},
            "alltime_points":  0,
            "streak":          0,
            "best_streak":     0,
            "games_played":    0,
            "correct":         0,
            "title":           None,
            "title_expires":   None,
            "hint_tokens":     0,
            "double_xp_until": None,
            "last_spin":       0,
            "badges":          [],
            "trivia_correct":  0,
            "versus_wins":     0,
            "daily_wins":      0,
            "powerups":        {},
        }
    u = data[chat_str][user_str]
    defaults = {
        "monthly_points": {}, "yearly_points": {}, "alltime_points": 0,
        "streak": 0, "best_streak": 0, "games_played": 0, "correct": 0,
        "title": None, "title_expires": None, "hint_tokens": 0,
        "double_xp_until": None, "last_spin": 0, "badges": [],
        "trivia_correct": 0, "versus_wins": 0, "daily_wins": 0,
        "powerups": {},
    }
    for k, v in defaults.items():
        if k not in u:
            u[k] = v
    if username:
        u["username"] = username
    return u

def track_member(bot, chat_id, user_id, username):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    get_user(data, chat_str, user_str, username)
    save_json(bot, config.GROUP_DATA_FILE, data)

def get_all_members(chat_id):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    if chat_str not in data:
        return []
    return [(int(uid), u.get("username", "Player"))
            for uid, u in data[chat_str].items()]

def get_all_groups():
    try:
        data = load_json(config.GROUP_DATA_FILE, {})
        return [int(cid) for cid in data.keys()]
    except Exception:
        return []

def get_streak_multiplier(streak):
    multiplier = 1.0
    for threshold, mult in sorted(config.STREAK_MULTIPLIERS.items(), reverse=True):
        if streak >= threshold:
            multiplier = mult
            break
    return multiplier

def reward_user(bot, chat_id, user_id, username, amount=50):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)

    u["streak"] += 1
    u["correct"] += 1
    if u["streak"] > u["best_streak"]:
        u["best_streak"] = u["streak"]

    multiplier = get_streak_multiplier(u["streak"])
    if u.get("double_xp_until") and time.time() < u["double_xp_until"]:
        multiplier *= 2

    final = int(amount * multiplier)

    month_key = _now_month_key()
    year_key = _now_year_key()
    u["points"] += final
    u["alltime_points"] += final
    u["monthly_points"][month_key] = u["monthly_points"].get(month_key, 0) + final
    u["yearly_points"][year_key] = u["yearly_points"].get(year_key, 0) + final
    u["games_played"] += 1

    save_json(bot, config.GROUP_DATA_FILE, data)
    check_achievements(bot, chat_id, user_id, username)
    return u["points"], u["streak"], multiplier, final

def penalise_wrong(bot, chat_id, user_id, username):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    u["streak"] = 0
    u["games_played"] += 1
    save_json(bot, config.GROUP_DATA_FILE, data)

def deduct_points(bot, chat_id, user_id, username, amount):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    u["points"] = max(0, u["points"] - amount)
    save_json(bot, config.GROUP_DATA_FILE, data)
    return u["points"]

def use_powerup(bot, chat_id, user_id, username, powerup_id):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    if u.get("powerups", {}).get(powerup_id, 0) > 0:
        u["powerups"][powerup_id] -= 1
        save_json(bot, config.GROUP_DATA_FILE, data)
        return True
    return False

def add_powerup(bot, chat_id, user_id, username, powerup_id, count=1):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    u.setdefault("powerups", {})
    u["powerups"][powerup_id] = u["powerups"].get(powerup_id, 0) + count
    save_json(bot, config.GROUP_DATA_FILE, data)

def unlock_badge(bot, chat_id, user_id, username, badge_id):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    if badge_id not in u.get("badges", []):
        u.setdefault("badges", [])
        u["badges"].append(badge_id)
        save_json(bot, config.GROUP_DATA_FILE, data)
        return True
    return False

def check_achievements(bot, chat_id, user_id, username):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    unlocked = []
    for badge_id, badge_data in config.ACHIEVEMENTS.items():
        if badge_id in u.get("badges", []):
            continue
        condition = badge_data.get("condition", {})
        meets_condition = True
        for key, required in condition.items():
            if u.get(key, 0) < required:
                meets_condition = False
                break
        if meets_condition:
            u.setdefault("badges", [])
            u["badges"].append(badge_id)
            unlocked.append(badge_id)
    if unlocked:
        save_json(bot, config.GROUP_DATA_FILE, data)
        badge_names = [config.ACHIEVEMENTS[b]["icon"] + " " + config.ACHIEVEMENTS[b]["name"] for b in unlocked]
        bot.send_message(chat_id, f"🏅 *ACHIEVEMENT UNLOCKED!*\n\n{username} unlocked: {', '.join(badge_names)}!", parse_mode="Markdown")
    return unlocked

def load_mutes():
    return load_json(config.MUTE_FILE, {})

def save_mutes(bot, data):
    save_json(bot, config.MUTE_FILE, data)

def mute_user(bot, chat_id, user_id, username, duration_seconds):
    data = load_mutes()
    key = f"{chat_id}_{user_id}"
    data[key] = {
        "username": username,
        "expires": time.time() + duration_seconds,
        "chat_id": chat_id,
        "user_id": user_id,
    }
    save_mutes(bot, data)

def unmute_user(bot, chat_id, user_id):
    data = load_mutes()
    key = f"{chat_id}_{user_id}"
    if key in data:
        del data[key]
        save_mutes(bot, data)
        return True
    return False

def is_muted(chat_id, user_id):
    data = load_mutes()
    key = f"{chat_id}_{user_id}"
    if key not in data:
        return False
    if time.time() > data[key]["expires"]:
        del data[key]
        return False
    return True

def cleanup_expired_mutes(bot):
    data = load_mutes()
    changed = False
    now = time.time()
    for key, value in list(data.items()):
        if now > value["expires"]:
            del data[key]
            changed = True
    if changed:
        save_mutes(bot, data)

def get_leaderboard(chat_id, mode="monthly", top_n=10):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    if chat_str not in data:
        return []

    month_key = _now_month_key()
    year_key = _now_year_key()
    results = []

    for user_str, u in data[chat_str].items():
        if mode == "monthly":
            pts = u.get("monthly_points", {}).get(month_key, 0)
        elif mode == "yearly":
            pts = u.get("yearly_points", {}).get(year_key, 0)
        else:
            pts = u.get("alltime_points", 0)
        results.append({
            "username": u.get("username", "Player"),
            "points": pts,
            "streak": u.get("streak", 0),
            "title": _get_active_title(u),
        })

    results.sort(key=lambda x: x["points"], reverse=True)
    return [(i + 1, r["username"], r["points"], r["streak"], r["title"])
            for i, r in enumerate(results[:top_n])]

def _get_active_title(u):
    title = u.get("title")
    expires = u.get("title_expires")
    if title and expires and time.time() < expires:
        return title
    return None

def purchase_item(bot, chat_id, user_id, username, item_id):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)

    if item_id in config.POWERUPS:
        powerup = config.POWERUPS[item_id]
        if u["points"] < powerup["cost"]:
            return False, f"Not enough points. You need {powerup['cost']} but have {u['points']}."
        u["points"] -= powerup["cost"]
        u.setdefault("powerups", {})
        u["powerups"][item_id] = u["powerups"].get(item_id, 0) + 1
        save_json(bot, config.GROUP_DATA_FILE, data)
        return True, f"✅ Purchased *{powerup['emoji']} {powerup['name']}* for {powerup['cost']} points!"

    item = next((i for i in config.SHOP_TITLES if i["id"] == item_id), None)
    if not item:
        return False, "Item not found."

    if u["points"] < item["cost"]:
        return False, f"Not enough points. You need {item['cost']} but have {u['points']}."

    u["points"] -= item["cost"]

    if item_id == "hint_tokens":
        u["hint_tokens"] = u.get("hint_tokens", 0) + 3
    elif item_id == "double_xp":
        u["double_xp_until"] = time.time() + 3600
    elif item_id == "mystery_box":
        import random
        prize = random.randint(10, 200)
        u["points"] += prize
        save_json(bot, config.GROUP_DATA_FILE, data)
        return True, f"🎁 Mystery Box opened! You won *{prize} points*!"
    else:
        u["title"] = item["name"]
        u["title_expires"] = time.time() + (config.SHOP_TITLE_DURATION_DAYS * 86400)

    save_json(bot, config.GROUP_DATA_FILE, data)
    return True, f"✅ Purchased *{item['name']}* for {item['cost']} points!"

def use_hint_token(bot, chat_id, user_id, username):
    data = load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    if u.get("hint_tokens", 0) > 0:
        u["hint_tokens"] -= 1
        save_json(bot, config.GROUP_DATA_FILE, data)
        return True
    return False

def check_and_run_monthly_reset(bot):
    state = load_json(config.STATE_FILE, {})
    now = datetime.datetime.now()
    last = state.get("last_monthly_reset", "")
    curr = now.strftime("%Y-%m")

    if last == curr:
        return

    data = load_json(config.GROUP_DATA_FILE, {})
    prev_month = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")

    for chat_str, users in data.items():
        scores = []
        for user_str, u in users.items():
            pts = u.get("monthly_points", {}).get(prev_month, 0)
            if pts > 0:
                scores.append((u.get("username", "Player"), pts))
        scores.sort(key=lambda x: x[1], reverse=True)
        if scores:
            winner_name, winner_pts = scores[0]
            msg = (
                f"🏆 *Monthly Results — {prev_month}*\n\n"
                f"👑 Champion: *{winner_name}* with *{winner_pts} points*!\n\n"
                f"Top 3:\n"
            )
            for i, (name, pts) in enumerate(scores[:3], 1):
                medal = ["🥇", "🥈", "🥉"][i - 1]
                msg += f"{medal} {name} — {pts} pts\n"
            msg += "\nMonthly scores have been reset. New month, new battle! 🔥"
            try:
                bot.send_message(int(chat_str), msg, parse_mode="Markdown")
            except Exception as e:
                print(f"Monthly reset announcement failed for {chat_str}: {e}")

    state["last_monthly_reset"] = curr
    save_json(bot, config.STATE_FILE, state)

def check_and_run_yearly_reset(bot):
    state = load_json(config.STATE_FILE, {})
    now = datetime.datetime.now()
    curr = now.strftime("%Y")
    last = state.get("last_yearly_reset", "")

    if last == curr:
        return

    if now.month != 1 or now.day != 1:
        return

    data = load_json(config.GROUP_DATA_FILE, {})
    prev_year = str(now.year - 1)

    for chat_str, users in data.items():
        scores = []
        for user_str, u in users.items():
            pts = u.get("yearly_points", {}).get(prev_year, 0)
            if pts > 0:
                scores.append((u.get("username", "Player"), pts))
        scores.sort(key=lambda x: x[1], reverse=True)
        if scores:
            winner_name, winner_pts = scores[0]
            msg = (
                f"🎊 *Yearly Champion — {prev_year}*\n\n"
                f"👑 *{winner_name}* dominated the year with *{winner_pts} points*!\n\n"
                f"Happy New Year! {now.year} begins now — make it count! 🚀"
            )
            try:
                bot.send_message(int(chat_str), msg, parse_mode="Markdown")
            except Exception as e:
                print(f"Yearly reset announcement failed for {chat_str}: {e}")

    state["last_yearly_reset"] = curr
    save_json(bot, config.STATE_FILE, state)

def load_quotes():
    return load_json(config.QUOTES_FILE, [])

def save_quotes(bot, quotes):
    save_json(bot, config.QUOTES_FILE, quotes)

def add_quote(bot, text, author="CHJN"):
    quotes = load_quotes()
    next_id = max((q["id"] for q in quotes), default=0) + 1
    quotes.append({"id": next_id, "text": text, "author": author})
    save_quotes(bot, quotes)
    return next_id

def delete_quote(bot, quote_id):
    quotes = load_quotes()
    before = len(quotes)
    quotes = [q for q in quotes if q["id"] != quote_id]
    if len(quotes) == before:
        return False
    save_quotes(bot, quotes)
    return True

def edit_quote(bot, quote_id, new_text):
    quotes = load_quotes()
    for q in quotes:
        if q["id"] == quote_id:
            q["text"] = new_text
            save_quotes(bot, quotes)
            return True
    return False

def get_quote(quote_id):
    quotes = load_quotes()
    return next((q for q in quotes if q["id"] == quote_id), None)

def get_random_quote():
    import random
    quotes = load_quotes()
    return random.choice(quotes) if quotes else None
