import os
import json
import time
import csv
import datetime
import threading
import requests
import base64
import atexit
import config

CSV_DATA_CACHE = {}
_file_locks = {}
GLOBAL_CHAT_ID = None
_trivia_cache = None

# ---------------------------------------------------------------------------
# MEMORY CACHE (to reduce GitHub I/O)
# ---------------------------------------------------------------------------

_group_data_cache = None
_group_data_dirty = False
_group_data_last_save = 0
_group_data_lock = threading.Lock()

_muted_cache = None
_muted_dirty = False
_muted_lock = threading.Lock()

_broadcast_cache = None
_broadcast_cache_time = 0
_broadcast_cache_lock = threading.Lock()
BROADCAST_CACHE_TTL = 30  # seconds

CACHE_SAVE_INTERVAL = 5  # seconds

def _load_group_data():
    """Load group data from GitHub or local fallback."""
    return load_remote_json(config.GROUP_DATA_FILE, {})

def _save_group_data():
    """Save group data to GitHub (if dirty) or local fallback."""
    global _group_data_cache, _group_data_dirty, _group_data_last_save
    with _group_data_lock:
        if not _group_data_dirty:
            return
        if _group_data_cache is None:
            return
        print("💾 Saving group_data to GitHub...")
        save_remote_json(config.GROUP_DATA_FILE, _group_data_cache)
        _group_data_dirty = False
        _group_data_last_save = time.time()

def get_group_data():
    """Get the cached group data, loading from GitHub if needed."""
    global _group_data_cache
    with _group_data_lock:
        if _group_data_cache is None:
            _group_data_cache = _load_group_data()
            if not isinstance(_group_data_cache, dict):
                _group_data_cache = {}
        return _group_data_cache

def mark_group_data_dirty():
    """Mark the group data as dirty so it will be saved on the next interval."""
    global _group_data_dirty
    with _group_data_lock:
        _group_data_dirty = True

def force_save_group_data():
    """Force an immediate save (e.g., before shutdown)."""
    _save_group_data()

def _load_muted_data():
    return load_remote_json(config.MUTE_FILE, {})

def _save_muted_data():
    global _muted_cache, _muted_dirty
    with _muted_lock:
        if not _muted_dirty or _muted_cache is None:
            return
        print("💾 Saving muted users to GitHub...")
        save_remote_json(config.MUTE_FILE, _muted_cache)
        _muted_dirty = False

def get_muted_data():
    global _muted_cache
    with _muted_lock:
        if _muted_cache is None:
            _muted_cache = _load_muted_data()
        return _muted_cache

def mark_muted_dirty():
    global _muted_dirty
    with _muted_lock:
        _muted_dirty = True

def force_save_muted_data():
    _save_muted_data()

def _cache_saver_loop():
    while True:
        time.sleep(CACHE_SAVE_INTERVAL)
        _save_group_data()
        _save_muted_data()

def init_cache():
    """Load cache and register shutdown handler."""
    get_group_data()
    get_muted_data()
    atexit.register(shutdown_cache)
    print("✅ Cache system initialized.")

def shutdown_cache():
    """Save all cached data before bot shuts down."""
    print("🔄 Flushing cache to GitHub before shutdown...")
    force_save_group_data()
    force_save_muted_data()
    print("✅ Cache flushed.")

# ---------------------------------------------------------------------------
# BROADCAST CACHING
# ---------------------------------------------------------------------------

def get_cached_broadcasts():
    """Get broadcasts from cache, refreshing if older than TTL."""
    global _broadcast_cache, _broadcast_cache_time
    now = time.time()
    with _broadcast_cache_lock:
        if _broadcast_cache is None or (now - _broadcast_cache_time) > BROADCAST_CACHE_TTL:
            print("🔄 Refreshing broadcast cache from GitHub...")
            _broadcast_cache = _load_broadcasts()
            _broadcast_cache_time = now
        return _broadcast_cache

def force_refresh_broadcasts():
    """Force an immediate refresh of the broadcast cache."""
    global _broadcast_cache, _broadcast_cache_time
    with _broadcast_cache_lock:
        print("🔄 Force-refreshing broadcast cache...")
        _broadcast_cache = _load_broadcasts()
        _broadcast_cache_time = time.time()

# ---------------------------------------------------------------------------
# GLOBAL BOT INSTANCE
# ---------------------------------------------------------------------------

_bot = None

def set_bot(bot):
    global _bot
    _bot = bot

def get_bot():
    return _bot

# ---------------------------------------------------------------------------
# FILE LOCKS (for local JSON writes)
# ---------------------------------------------------------------------------

def get_lock(filepath):
    if filepath not in _file_locks:
        _file_locks[filepath] = threading.Lock()
    return _file_locks[filepath]

def save_json(bot, filepath, data):
    """Save JSON to local file. bot parameter is used for logging if needed."""
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
            if bot:
                log_error_to_admin(bot, "Atomic Save Fault", e)
            elif _bot:
                log_error_to_admin(_bot, "Atomic Save Fault", e)

def load_json(filepath, default_value):
    if not os.path.exists(filepath):
        with open(filepath, "w") as f:
            json.dump(default_value, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
    with open(filepath, "r") as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# REMOTE JSON (GitHub) – FOR PERSISTENT DATA
# ---------------------------------------------------------------------------

def load_remote_json(filename, default_value):
    """Downloads a JSON file from the generated branch's data folder."""
    if not _bot:
        return default_value
    url = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/{config.TRIVIA_BRANCH}/{config.GENERATED_DATA_PATH}/{filename}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"Failed to fetch {filename} (status {r.status_code}), using default.")
    except Exception as e:
        print(f"Error loading {filename} from GitHub: {e}")
    
    # Fallback to local file
    local_path = os.path.join(os.getcwd(), filename)
    if os.path.exists(local_path):
        try:
            with open(local_path, 'r') as f:
                return json.load(f)
        except:
            pass
    return default_value

def save_remote_json(filename, data):
    """Uploads a JSON file to the generated branch's data folder."""
    if not _bot:
        print(f"Bot not set, cannot save {filename}")
        return False
    
    if not config.GITHUB_TOKEN:
        print(f"⚠️ No GITHUB_TOKEN set, cannot save {filename}")
        # Fallback local save
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=4)
            print(f"✅ Saved {filename} locally as fallback")
            return True
        except Exception as e:
            print(f"❌ Failed to save {filename} locally: {e}")
            return False
    
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/{config.GENERATED_DATA_PATH}/{filename}"
    headers = {
        "Authorization": f"token {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Get existing file SHA if any
    resp = requests.get(url, headers=headers, params={"ref": config.TRIVIA_BRANCH})
    sha = None
    if resp.status_code == 200:
        sha = resp.json().get("sha")
    elif resp.status_code == 404:
        pass
    else:
        print(f"⚠️ Unexpected response checking {filename}: {resp.status_code}")
    
    content_str = json.dumps(data, indent=4, ensure_ascii=False)
    content_bytes = content_str.encode('utf-8')
    encoded = base64.b64encode(content_bytes).decode('ascii')
    
    payload = {
        "message": f"Update {filename}",
        "content": encoded,
        "branch": config.TRIVIA_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    
    response = requests.put(url, headers=headers, json=payload)
    if response.status_code in (200, 201):
        print(f"✅ Saved {filename} to GitHub")
        return True
    else:
        print(f"❌ Failed to save {filename} to GitHub: {response.text}")
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=4)
            print(f"✅ Saved {filename} locally as fallback")
            return True
        except Exception as e:
            print(f"❌ Failed to save {filename} locally: {e}")
            return False

# ---------------------------------------------------------------------------
# BROADCAST SYSTEM (Uses broadcasts.json)
# ---------------------------------------------------------------------------

_broadcast_lock = threading.Lock()

def _load_broadcasts():
    return load_remote_json(config.BROADCAST_FILE, [])

def _save_broadcasts(data):
    save_remote_json(config.BROADCAST_FILE, data)
    force_refresh_broadcasts()

def add_broadcast(bot, chat_id, message, send_time):
    with _broadcast_lock:
        broadcasts = get_cached_broadcasts()
        broadcast_id = max([b.get("id", 0) for b in broadcasts], default=0) + 1
        broadcasts.append({
            "id": broadcast_id,
            "chat_id": chat_id,
            "message": message,
            "send_time": send_time,
            "sent": 0
        })
        _save_broadcasts(broadcasts)

def get_pending_broadcasts():
    broadcasts = get_cached_broadcasts()
    now = int(time.time()) + 5
    pending = [b for b in broadcasts if b.get("sent", 0) == 0 and b.get("send_time", 0) <= now]
    return pending

def mark_broadcast_sent(broadcast_id):
    with _broadcast_lock:
        broadcasts = get_cached_broadcasts()
        for b in broadcasts:
            if b.get("id") == broadcast_id:
                b["sent"] = 1
                break
        _save_broadcasts(broadcasts)

def get_all_broadcasts():
    return get_cached_broadcasts()

# ---------------------------------------------------------------------------
# TRIVIA LOADER
# ---------------------------------------------------------------------------

def load_trivia_from_github():
    global _trivia_cache
    if _trivia_cache is not None:
        return _trivia_cache
    
    all_questions = []
    base_url = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/{config.TRIVIA_BRANCH}/{config.TRIVIA_REMOTE_PATH}/"
    
    for cat, filename in config.TRIVIA_CATEGORY_FILES.items():
        try:
            url = base_url + filename
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    all_questions.extend(data)
                else:
                    print(f"Warning: {filename} is not a list, skipping.")
            else:
                print(f"Failed to fetch {filename} (status {resp.status_code})")
        except Exception as e:
            print(f"Error loading trivia from GitHub: {e}")
    
    if all_questions:
        _trivia_cache = all_questions
        print(f"Loaded {len(all_questions)} trivia questions from generated branch.")
        return all_questions
    
    try:
        with open(config.TRIVIA_DB, 'r') as f:
            data = json.load(f)
        _trivia_cache = data
        print(f"Loaded {len(data)} trivia questions from local fallback.")
        return data
    except:
        print("No trivia loaded.")
        return []

def reload_trivia():
    global _trivia_cache
    _trivia_cache = None
    return load_trivia_from_github()

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

def log_error_to_admin(bot, context, exception):
    if not bot:
        print(f"⚠️ {context}: {exception}")
        return
    error_msg = f"⚠️ *BOT ERROR*\n📌 Context: {context}\n💥 `{str(exception)}`"
    print(error_msg)
    try:
        bot.send_message(config.ADMIN_ID, error_msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Failed to alert admin: {e}")

# ---------------------------------------------------------------------------
# CSV CACHE
# ---------------------------------------------------------------------------

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
        if bot:
            log_error_to_admin(bot, "CSV Fetch Error", e)
        elif _bot:
            log_error_to_admin(_bot, "CSV Fetch Error", e)
        if url in CSV_DATA_CACHE:
            return CSV_DATA_CACHE[url][1]
        return []

# ---------------------------------------------------------------------------
# USER DATA (cached)
# ---------------------------------------------------------------------------

def _now_month_key():
    return datetime.datetime.now().strftime("%Y-%m")

def _now_year_key():
    return str(datetime.datetime.now().year)

def get_user(data, chat_str, user_str, username):
    if chat_str not in data:
        data[chat_str] = {}
    if user_str not in data[chat_str]:
        data[chat_str][user_str] = {
            "username": username or "Player",
            "points": 0,
            "monthly_points": {},
            "yearly_points": {},
            "alltime_points": 0,
            "streak": 0,
            "best_streak": 0,
            "games_played": 0,
            "correct": 0,
            "title": None,
            "title_expires": None,
            "hint_tokens": 0,
            "double_xp_until": None,
            "last_spin": 0,
            "badges": [],
            "trivia_correct": 0,
            "versus_wins": 0,
            "daily_wins": 0,
            "powerups": {},
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

def get_user_data_field(bot, chat_id, user_id, field, default=None):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, "User")
    return u.get(field, default)

def track_member(bot, chat_id, user_id, username):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    get_user(data, chat_str, user_str, username)
    mark_group_data_dirty()

def get_all_members(chat_id):
    data = get_group_data()
    chat_str = str(chat_id)
    if chat_str not in data:
        return []
    return [(int(uid), u.get("username", "Player")) for uid, u in data[chat_str].items()]

def get_all_groups():
    try:
        data = get_group_data()
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
    data = get_group_data()
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

    print(f"💰 [POINTS] Reward: {username} gained {final} pts (base {amount}, multiplier {multiplier})")
    mark_group_data_dirty()
    check_achievements(bot, chat_id, user_id, username)
    return u["points"], u["streak"], multiplier, final

def penalise_wrong(bot, chat_id, user_id, username):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    u["streak"] = 0
    u["games_played"] += 1
    mark_group_data_dirty()

def deduct_points(bot, chat_id, user_id, username, amount):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    u["points"] = max(0, u["points"] - amount)
    mark_group_data_dirty()
    return u["points"]

def use_powerup(bot, chat_id, user_id, username, powerup_id):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    if u.get("powerups", {}).get(powerup_id, 0) > 0:
        u["powerups"][powerup_id] -= 1
        mark_group_data_dirty()
        return True
    return False

def add_powerup(bot, chat_id, user_id, username, powerup_id, count=1):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    u.setdefault("powerups", {})
    u["powerups"][powerup_id] = u["powerups"].get(powerup_id, 0) + count
    mark_group_data_dirty()

def unlock_badge(bot, chat_id, user_id, username, badge_id):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    if badge_id not in u.get("badges", []):
        u.setdefault("badges", [])
        u["badges"].append(badge_id)
        mark_group_data_dirty()
        return True
    return False

def check_achievements(bot, chat_id, user_id, username):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    unlocked = []
    for badge_id, badge_data in config.ACHIEVEMENTS.items():
        if badge_id in u.get("badges", []):
            continue
        condition = badge_data.get("condition", {})
        meets = True
        for key, required in condition.items():
            if u.get(key, 0) < required:
                meets = False
                break
        if meets:
            u.setdefault("badges", [])
            u["badges"].append(badge_id)
            unlocked.append(badge_id)
    if unlocked:
        mark_group_data_dirty()
        if bot:
            badge_names = [config.ACHIEVEMENTS[b]["icon"] + " " + config.ACHIEVEMENTS[b]["name"] for b in unlocked]
            bot.send_message(chat_id, f"🏅 *ACHIEVEMENT UNLOCKED!*\n\n{username} unlocked: {', '.join(badge_names)}!", parse_mode="Markdown")
    return unlocked

def load_mutes(bot=None):
    return get_muted_data()

def save_mutes(bot, data):
    global _muted_cache
    with _muted_lock:
        _muted_cache = data
        mark_muted_dirty()

def mute_user(bot, chat_id, user_id, username, duration_seconds):
    data = get_muted_data()
    key = f"{chat_id}_{user_id}"
    data[key] = {"username": username, "expires": time.time() + duration_seconds, "chat_id": chat_id, "user_id": user_id}
    mark_muted_dirty()

def unmute_user(bot, chat_id, user_id):
    data = get_muted_data()
    key = f"{chat_id}_{user_id}"
    if key in data:
        del data[key]
        mark_muted_dirty()
        return True
    return False

def is_muted(bot, chat_id, user_id):
    data = get_muted_data()
    key = f"{chat_id}_{user_id}"
    if key not in data:
        return False
    if time.time() > data[key]["expires"]:
        del data[key]
        mark_muted_dirty()
        return False
    return True

def cleanup_expired_mutes(bot):
    data = get_muted_data()
    changed = False
    now = time.time()
    for key, value in list(data.items()):
        if now > value["expires"]:
            del data[key]
            changed = True
    if changed:
        mark_muted_dirty()

def get_leaderboard(chat_id, mode="monthly", top_n=10):
    data = get_group_data()
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
        results.append({"username": u.get("username", "Player"), "points": pts, "streak": u.get("streak", 0), "title": _get_active_title(u)})
    results.sort(key=lambda x: x["points"], reverse=True)
    return [(i+1, r["username"], r["points"], r["streak"], r["title"]) for i, r in enumerate(results[:top_n])]

def _get_active_title(u):
    title = u.get("title")
    expires = u.get("title_expires")
    if title and expires and time.time() < expires:
        return title
    return None

def purchase_item(bot, chat_id, user_id, username, item_id):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)

    if item_id in config.POWERUPS:
        powerup = config.POWERUPS[item_id]
        if u["points"] < powerup["cost"]:
            return False, f"Not enough points. Need {powerup['cost']}, have {u['points']}."
        u["points"] -= powerup["cost"]
        u.setdefault("powerups", {})
        u["powerups"][item_id] = u["powerups"].get(item_id, 0) + 1
        mark_group_data_dirty()
        return True, f"✅ Purchased *{powerup['emoji']} {powerup['name']}*!"

    item = next((i for i in config.SHOP_TITLES if i["id"] == item_id), None)
    if not item:
        return False, "Item not found."
    if u["points"] < item["cost"]:
        return False, f"Not enough points. Need {item['cost']}, have {u['points']}."
    u["points"] -= item["cost"]

    if item_id == "hint_tokens":
        u["hint_tokens"] = u.get("hint_tokens", 0) + 3
    elif item_id == "double_xp":
        u["double_xp_until"] = time.time() + 3600
    elif item_id == "mystery_box":
        import random
        prize = random.randint(10, 200)
        u["points"] += prize
        mark_group_data_dirty()
        return True, f"🎁 Mystery Box opened! You won *{prize} points*!"
    else:
        u["title"] = item["name"]
        u["title_expires"] = time.time() + (config.SHOP_TITLE_DURATION_DAYS * 86400)

    mark_group_data_dirty()
    return True, f"✅ Purchased *{item['name']}*!"

def use_hint_token(bot, chat_id, user_id, username):
    data = get_group_data()
    chat_str = str(chat_id)
    user_str = str(user_id)
    u = get_user(data, chat_str, user_str, username)
    if u.get("hint_tokens", 0) > 0:
        u["hint_tokens"] -= 1
        mark_group_data_dirty()
        return True
    return False

def check_and_run_monthly_reset(bot):
    state = load_remote_json(config.STATE_FILE, {})
    now = datetime.datetime.now()
    last = state.get("last_monthly_reset", "")
    curr = now.strftime("%Y-%m")
    if last == curr:
        return
    data = get_group_data()
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
            msg = f"🏆 *Monthly Results — {prev_month}*\n\n👑 Champion: *{winner_name}* with *{winner_pts} points*!\n\nTop 3:\n"
            for i, (name, pts) in enumerate(scores[:3], 1):
                medal = ["🥇", "🥈", "🥉"][i-1]
                msg += f"{medal} {name} — {pts} pts\n"
            msg += "\nMonthly scores have been reset. New month, new battle! 🔥"
            if bot:
                try:
                    bot.send_message(int(chat_str), msg, parse_mode="Markdown")
                except Exception as e:
                    print(f"Monthly reset failed for {chat_str}: {e}")
    state["last_monthly_reset"] = curr
    save_remote_json(config.STATE_FILE, state)

def check_and_run_yearly_reset(bot):
    state = load_remote_json(config.STATE_FILE, {})
    now = datetime.datetime.now()
    curr = now.strftime("%Y")
    last = state.get("last_yearly_reset", "")
    if last == curr or now.month != 1 or now.day != 1:
        return
    data = get_group_data()
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
            msg = f"🎊 *Yearly Champion — {prev_year}*\n\n👑 *{winner_name}* dominated with *{winner_pts} points*!\n\nHappy New Year! Make it count! 🚀"
            if bot:
                try:
                    bot.send_message(int(chat_str), msg, parse_mode="Markdown")
                except Exception as e:
                    print(f"Yearly reset failed for {chat_str}: {e}")
    state["last_yearly_reset"] = curr
    save_remote_json(config.STATE_FILE, state)

def load_quotes(bot=None):
    return load_remote_json(config.QUOTES_FILE, [])

def save_quotes(bot, quotes):
    save_remote_json(config.QUOTES_FILE, quotes)

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

def get_quote(bot, quote_id):
    quotes = load_quotes()
    return next((q for q in quotes if q["id"] == quote_id), None)

def get_random_quote(bot=None):
    import random
    quotes = load_quotes()
    return random.choice(quotes) if quotes else None

# ---------------------------------------------------------------------------
# BLOCKLIST
# ---------------------------------------------------------------------------

def load_blocklist():
    data = load_remote_json(config.BLOCKLIST_FILE, [])
    if not isinstance(data, list):
        data = []
    return data

def save_blocklist(blocklist):
    save_remote_json(config.BLOCKLIST_FILE, blocklist)

def is_blocked(user_id):
    blocklist = load_blocklist()
    return user_id in blocklist

def block_user(user_id):
    blocklist = load_blocklist()
    if user_id not in blocklist:
        blocklist.append(user_id)
        save_blocklist(blocklist)
        return True
    return False

def unblock_user(user_id):
    blocklist = load_blocklist()
    if user_id in blocklist:
        blocklist.remove(user_id)
        save_blocklist(blocklist)
        return True
    return False

# ---------------------------------------------------------------------------
# FEEDBACK
# ---------------------------------------------------------------------------

def load_feedback():
    data = load_remote_json(config.FEEDBACK_FILE, [])
    if not isinstance(data, list):
        data = []
    return data

def save_feedback(feedback):
    save_remote_json(config.FEEDBACK_FILE, feedback)

def add_feedback(user_id, username, message, chat_id=None, group_name=None, timestamp=None):
    if timestamp is None:
        timestamp = time.time()
    feedback = load_feedback()
    entry = {
        "user_id": user_id,
        "username": username,
        "message": message,
        "timestamp": timestamp
    }
    if chat_id:
        entry["chat_id"] = chat_id
    if group_name:
        entry["group_name"] = group_name
    feedback.append(entry)
    save_feedback(feedback)

def get_feedback_for_admin():
    return load_feedback()

# ---------------------------------------------------------------------------
# GROUP SCHEDULES (per-group override)
# ---------------------------------------------------------------------------

# Cache for group schedules to reduce GitHub reads
_group_schedules_cache = None
_group_schedules_cache_time = 0
_group_schedules_cache_lock = threading.Lock()
GROUP_SCHEDULES_CACHE_TTL = 30  # seconds

def _load_group_schedules():
    return load_remote_json(config.GROUP_SCHEDULES_FILE, {})

def _get_cached_group_schedules():
    global _group_schedules_cache, _group_schedules_cache_time
    now = time.time()
    with _group_schedules_cache_lock:
        if _group_schedules_cache is None or (now - _group_schedules_cache_time) > GROUP_SCHEDULES_CACHE_TTL:
            _group_schedules_cache = _load_group_schedules()
            _group_schedules_cache_time = now
        return _group_schedules_cache

def load_group_schedules():
    return _get_cached_group_schedules()

def save_group_schedules(schedules):
    save_remote_json(config.GROUP_SCHEDULES_FILE, schedules)
    # Force cache refresh
    global _group_schedules_cache, _group_schedules_cache_time
    with _group_schedules_cache_lock:
        _group_schedules_cache = schedules
        _group_schedules_cache_time = time.time()

def get_group_schedule(group_id):
    schedules = load_group_schedules()
    return schedules.get(str(group_id), {})

def set_group_schedule(group_id, settings):
    schedules = load_group_schedules()
    group_id_str = str(group_id)
    try:
        if _bot:
            chat = _bot.get_chat(group_id)
            group_name = chat.title or f"Group {group_id}"
    except Exception:
        group_name = f"Group {group_id}"
    settings["group_name"] = group_name
    schedules[group_id_str] = settings
    save_group_schedules(schedules)
    return True

def remove_group_schedule(group_id):
    schedules = load_group_schedules()
    group_id_str = str(group_id)
    if group_id_str in schedules:
        del schedules[group_id_str]
        save_group_schedules(schedules)
        return True
    return False

# ---------------------------------------------------------------------------
# SCHEDULE PANEL MESSAGE ID HELPERS
# ---------------------------------------------------------------------------

def get_group_schedule_message_id(group_id):
    sched = get_group_schedule(group_id)
    return sched.get("schedule_message_id") if sched else None

def set_group_schedule_message_id(group_id, message_id):
    sched = get_group_schedule(group_id)
    if sched is None:
        sched = {}
    sched["schedule_message_id"] = message_id
    set_group_schedule(group_id, sched)

# ---------------------------------------------------------------------------
# THEME SYSTEM – Daily Themed Messages
# ---------------------------------------------------------------------------

def load_daily_themes():
    return load_remote_json(config.DAILY_THEMES_FILE, {})

def save_daily_themes(themes):
    save_remote_json(config.DAILY_THEMES_FILE, themes)

def get_current_week():
    themes = load_daily_themes()
    current_week = themes.get("current_week", 1)
    last_updated = themes.get("last_updated")
    if last_updated:
        try:
            start_date = datetime.datetime.fromisoformat(last_updated)
            days_diff = (datetime.datetime.now() - start_date).days
            weeks_passed = days_diff // 7
            current_week = ((themes.get("current_week", 1) + weeks_passed - 1) % 4) + 1
        except Exception:
            pass
    return current_week

def get_character_for_day(week, day):
    themes = load_daily_themes()
    weeks = themes.get("weeks", [])
    day_map = {
        "monday": "monday",
        "tuesday": "tuesday",
        "wednesday": "wednesday",
        "thursday": "thursday",
        "friday": "friday",
        "saturday": "saturday",
        "sunday": "sunday"
    }
    day_key = day_map.get(day.lower())
    if not day_key:
        return None, None
    for w in weeks:
        if w.get("week") == week:
            day_data = w.get(day_key, {})
            return day_data.get("character"), day_data.get("category")
    return None, None

def get_todays_character():
    now = datetime.datetime.now()
    day = now.strftime("%A").lower()
    week = get_current_week()
    return get_character_for_day(week, day)

# ---------------------------------------------------------------------------
# CHARACTER GREETINGS AND GOODNIGHT MESSAGES
# ---------------------------------------------------------------------------

def get_character_greeting(character, message_type="morning"):
    greetings = {
        # ... (full greeting dict – keep your existing one) ...
    }
    char_key = character.lower().strip()
    special_cases = {
        "timon & pumbaa": "timon_pumbaa",
        "koro-sensei": "koro_sensei",
        "mr. terrific": "mr_terrific",
        "master chief": "master_chief",
        "professor x": "beerus",
        "cyborg": "genos",
        "bible verse": "bible_verse"
    }
    char_key = special_cases.get(char_key, char_key)
    char_data = greetings.get(char_key, {})
    if message_type == "morning":
        return char_data.get("morning", "Good morning, family! Rise and shine! 🌅")
    elif message_type == "goodnight":
        return char_data.get("goodnight", "The day is done, family. Rest well. 🌙")
    return "Good morning, family! Rise and shine! 🌅"

# ---------------------------------------------------------------------------
# WEEKLY TABLE OPT-IN/OUT
# ---------------------------------------------------------------------------

def get_weekly_table_opt_in(group_id):
    group_sched = get_group_schedule(group_id)
    if group_sched:
        return group_sched.get("weekly_table_opt_in", config.WEEKLY_TABLE_DEFAULT_OPT_IN)
    return config.WEEKLY_TABLE_DEFAULT_OPT_IN

def set_weekly_table_opt_in(group_id, opt_in):
    group_sched = get_group_schedule(group_id)
    if group_sched is None:
        group_sched = {}
    group_sched["weekly_table_opt_in"] = opt_in
    set_group_schedule(group_id, group_sched)
    return True

# ---------------------------------------------------------------------------
# PRESETS SYSTEM (optional, keep if you want)
# ---------------------------------------------------------------------------

def load_presets():
    return load_remote_json(config.PRESETS_FILE, {})

def save_presets(presets):
    save_remote_json(config.PRESETS_FILE, presets)

def save_preset(name, settings):
    presets = load_presets()
    presets[name] = settings
    save_presets(presets)

def get_preset(name):
    presets = load_presets()
    return presets.get(name)

def delete_preset(name):
    presets = load_presets()
    if name in presets:
        del presets[name]
        save_presets(presets)
        return True
    return False

def list_presets():
    return load_presets()

# ---------------------------------------------------------------------------
# MESSAGE KEEP PATTERNS
# ---------------------------------------------------------------------------

def get_keep_patterns():
    return [
        "☀️ *Good Morning",
        "🌅 *Good Morning",
        "🌙 *Goodnight",
        "📢 *ANNOUNCEMENT",
        "📢 *Tagging everyone",
        "BROADCAST",
        "📅 *SUNDAY STANDINGS",
        "🏆 *Monthly Results",
        "🎊 *Yearly Champion",
        "👋 *Welcome",
        "☠️ *KONO BOT WA!*",
        "📊 *GROUP STATS*",
        "📊 *WEEKLY RECAP*",
        "✅ Your feedback has been sent to the Captain",
        "⚔️ *VERSUS CHALLENGE*",
        "⚔️ *MATCH OVER*",
    ]
