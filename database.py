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
            # Ensure we have at least an empty dict
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
    force_refresh_broadcasts()  # <-- Refresh cache after save

def add_broadcast(bot, chat_id, message, send_time):
    with _broadcast_lock:
        broadcasts = get_cached_broadcasts()  # Use cache
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
    """Get pending broadcasts from cache."""
    broadcasts = get_cached_broadcasts()
    now = int(time.time()) + 5  # Add 5s buffer
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
    feedback = load_feedback()
    return feedback

# ---------------------------------------------------------------------------
# GROUP SCHEDULES (per-group override)
# ---------------------------------------------------------------------------

def load_group_schedules():
    data = load_remote_json(config.GROUP_SCHEDULES_FILE, {})
    if not isinstance(data, dict):
        data = {}
    return data

def save_group_schedules(schedules):
    save_remote_json(config.GROUP_SCHEDULES_FILE, schedules)

def get_group_schedule(group_id):
    schedules = load_group_schedules()
    return schedules.get(str(group_id))

def set_group_schedule(group_id, settings):
    schedules = load_group_schedules()
    group_id_str = str(group_id)
    
    try:
        if _bot:
            chat = _bot.get_chat(group_id)
            group_name = chat.title or f"Group {group_id}"
    except Exception:
        group_name = f"Group {group_id}"
    
    if group_id_str not in schedules:
        schedules[group_id_str] = {}
    
    schedules[group_id_str]["group_name"] = group_name
    for key, value in settings.items():
        schedules[group_id_str][key] = value
    
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
        # WEEK 1
        "kratos": {
            "morning": "🪓 Rise, warrior. The sun is already higher than your excuses. The day waits for no one – not even a God of War.\n\n*A true warrior finds strength in stillness.*",
            "goodnight": "The sun has set, warrior. Lay down your burdens and rest. You fought well today. *A true warrior knows that rest is not weakness – it is preparation.*\n\nTomorrow brings new battles. But tonight, you need only to breathe. Sleep deeply."
        },
        "luffy": {
            "morning": "🏴‍☠️ ZAAA SORAAA! Rise and shine, Nakama! The sun's up, the sea's waiting, and I'm starving! Let's set sail and find some adventure!\n\n*The sea is calling, and I'm hungry! Let's make today legendary!*",
            "goodnight": "The sun's going down, Nakama! But don't worry – the adventure doesn't end, it just takes a break. Tomorrow, we set sail again! The One Piece isn't going to find itself.\n\nSleep well. Dream of the Grand Line and all the treasure we'll find. I'll be here when you wake up. Goodnight, crew!"
        },
        "terminator": {
            "morning": "🤖 Wake up. The day has begun. Your mission, should you choose to accept it, is to make today count.\n\n*The future is not set. There is no fate but what we make.*",
            "goodnight": "Shutting down for the night. System check complete – you performed well today. Your mission is complete for now. The future is not set, but tonight, you have earned your rest.\n\nNo threats detected. No alarms. Just peace. We'll resume at dawn."
        },
        "gojo": {
            "morning": "🔥 Rise and shine! The world needs the honored one today, and that includes you! Don't keep reality waiting.\n\n*I don't care about the rules. I care about protecting my students. So go out there and be great!*",
            "goodnight": "The sun's down, and even the honored one needs to rest! Don't think you have to carry the world all day. Tomorrow, we'll continue protecting what matters. But tonight, protect yourself.\n\nSleep well. Being the strongest means knowing when to recharge. Goodnight!"
        },
        "iron_man": {
            "morning": "💎 Good morning, genius! The world doesn't save itself – that's our job! Put on your armor and get ready!\n\n*The suit makes me a hero, but the man inside is what matters. Now get out there and be brilliant!*",
            "goodnight": "Alright, crew – the arc reactor is powering down for the night. Even geniuses need sleep. Tomorrow, we'll save the world again. But tonight? We rest.\n\nThe suit doesn't make the hero – the heart does. And yours needs rest to keep beating strong. I am Iron Man... and I am going to sleep. Goodnight!"
        },
        "flash": {
            "morning": "⚡ Rise and shine! The speed force is buzzing, and so should you! Every second is a gift – let's make today legendary!\n\n*The world needs hope. That's what I give them. Now go out there and be faster than yesterday!*",
            "goodnight": "The speed force never takes a break, but you should! It's time to slow down and recharge. You were fast today – faster than yesterday. But even the fastest need to rest.\n\nThe world will be here tomorrow. And it'll need you at your fastest. So rest up, and dream of the finish line. Goodnight!"
        },
        "david": {
            "morning": "🙏 Good morning, family! \"The Lord is my shepherd; I shall not want.\" – Psalm 23:1\n\nDavid, the shepherd king, reminds us today: even in the valley of shadow, we are not alone. Walk with courage, for the Lord is with you.\n\n*I will fear no evil, for you are with me.*",
            "goodnight": "The Lord is my shepherd – He has led you through this day. Rest in His peace tonight. \"In peace I will lie down and sleep, for you alone, Lord, make me dwell in safety.\" – Psalm 4:8\n\nMay His angels watch over you. Sleep well, family."
        },
        "samson": {
            "morning": "💪 Good morning, family! \"The Lord gave me strength.\" – Judges 16:28\n\nSamson, the mighty warrior, teaches us that true strength comes from God. Even in our weakness, He is our power.\n\n*Lord, remember me and strengthen me.*",
            "goodnight": "The Lord who gave Samson strength is with you tonight. He who began a good work in you will carry it on to completion. Rest in His power, even when you feel weak.\n\nSleep well, family. Your strength comes from the Lord."
        },
        "isaiah": {
            "morning": "📜 Good morning, family! \"Here am I, send me.\" – Isaiah 6:8\n\nIsaiah, the prophet who answered God's call, challenges us to step forward – to be the light in the darkness.\n\n*I am a man of unclean lips, but you have cleansed me.*",
            "goodnight": "The word of the Lord stands forever. You have answered His call today, and He will not forget your labour of love. Rest in the knowledge that you are His.\n\nSleep well, family. His mercies are new every morning."
        },
        "moses": {
            "morning": "🔥 Good morning, family! \"Let my people go.\" – Exodus 3:10\n\nMoses, the liberator, shows us that God uses the humble to do great things. You are chosen for such a time as this.\n\n*I am not eloquent, but you are my voice.*",
            "goodnight": "The Lord who spoke to Moses from the burning bush is with you tonight. He has not forgotten His promises. Rest in the assurance that He who calls you is faithful.\n\nSleep well, family. Tomorrow, you will walk in purpose."
        },
        # WEEK 2
        "master_chief": {
            "morning": "💪 Wake up, soldier! The mission is already on the clock, and I need you at your best. No excuses – just victory.\n\n*Finish the fight. That's what heroes do. Now get out there and make it happen.*",
            "goodnight": "The mission is complete for today, soldier. Rest now. You've earned it. The battle doesn't end, but tonight, you are safe.\n\nI'll keep watch. Sleep well. Goodnight, soldier."
        },
        "goku": {
            "morning": "💥 GOOD MORNING! The sun's up and so is my power level! I can sense today's going to be amazing – let's train hard!\n\n*I will not let you destroy my world! That goes for today's challenges too – fight with everything you've got!*",
            "goodnight": "Alright, everyone! Training is over for the day! Good workout, everybody! Tomorrow, we'll power up even more! I can already feel today's gains!\n\nBut for now... sleep. Recharge. I'll be here, ready to train again. Goodnight!"
        },
        "thanos": {
            "morning": "💜 Awaken. The day has come to prove your worth. I have watched civilizations rise and fall – let us see if you can rise.\n\n*Perfectly balanced, as all things should be. Go forth and find your balance today... or be erased.*",
            "goodnight": "The day has found its balance. You have done what you could, and that is enough. Rest now. The night brings peace, and tomorrow brings new challenges.\n\nIn the stillness, find your center. In the silence, find your strength. Goodnight."
        },
        "beerus": {
            "morning": "🐱 Wake up, mortal! The God of Destruction is not known for his patience! The universe doesn't wait for you to finish dreaming.\n\n*Destruction comes to those who waste time. Now get moving!*",
            "goodnight": "Finally, some peace and quiet. I've been destroying planets all day – I need my rest. You should sleep too, while I'm in a good mood.\n\n*Perfectly balanced, as all things should be.*"
        },
        "genos": {
            "morning": "🔥 Good morning. I've already completed my morning training regimen. You should start yours – every second counts.\n\n*Efficiency is the path to strength. Don't waste time.*",
            "goodnight": "My systems are powering down for the night. Stay vigilant – the world doesn't sleep just because you do.\n\n*I will be stronger tomorrow. That is my promise.*"
        },
        "isagi": {
            "morning": "⚽ Rise and grind, team! The field is calling, and the goal is just ahead! Let's show them what we're made of!\n\n*The field is where I prove my worth. Today, you prove yours. Let's do this!*",
            "goodnight": "The final whistle has blown, team! Today was a good match – but tomorrow is the championship! Rest up. Dream of the goal. Visualize your victory.\n\nThe field will be there tomorrow. Goodnight, team. See you at the start!"
        },
        # WEEK 3
        "mario": {
            "morning": "🍄 WAHOO! It's-a me, Mario, and I'm here to-a start the day right! The sun is shining, the day is bright – let's-a go!\n\n*You can do anything if you believe in yourself! Now go out there and be the hero of your own story!*",
            "goodnight": "It's-a been a great day, everyone! But even the best plumber needs to sleep! Tomorrow, a new adventure awaits! The castle is waiting, and I'm-a ready!\n\nBuona notte, my friends! See you in the morning!"
        },
        "naruto": {
            "morning": "🍥 BELIEVE IT! The sun is up, and I'm more fired up than ever! Today's going to be our day – I can feel it!\n\n*I never go back on my word. That's my ninja way! So go out there and prove what you're made of!*",
            "goodnight": "BELIEVE IT! Even I need to rest! Today was incredible – thank you for all that you do. Tomorrow, we'll get stronger together.\n\nBut for tonight... just rest. You've done enough. Dattebayo! Goodnight!"
        },
        "gandalf": {
            "morning": "🧙‍♂️ The day is upon us, my friends! Let us go forth with courage and a merry heart. Darkness holds no power here.\n\n*Even the smallest person can change the course of the future. Remember that as you go today.*",
            "goodnight": "The day has ended, my friends. The light has faded, but the stars shine bright above. Rest now. Tomorrow will bring new journeys.\n\nMay the road always rise to meet you. Goodnight!"
        },
        "kakashi": {
            "morning": "📖 Morning, everyone. The hidden leaf is awake, and the day is full of possibilities. Let's not waste a moment.\n\n*The most important thing is to protect those you care about. Let that guide you today.*",
            "goodnight": "The sun is gone, and the hidden leaf is silent. Tonight is for rest and reflection. Tomorrow brings new challenges, new missions.\n\nClose your eyes and let go of the day's weight. You've carried enough. Goodnight, everyone."
        },
        "mr_terrific": {
            "morning": "🧠 Wake up! It's time to use that brilliant brain of yours! Today's a puzzle, and you're the one who's going to solve it!\n\n*Intelligence is the greatest superpower. And you've got it – now go out there and prove it!*",
            "goodnight": "Alright, team! Brain power down! You used a lot of it today, and I'm impressed. Tomorrow, we'll solve more problems.\n\nBut tonight, let your mind wander. Let it rest. Let it dream. Goodnight, thinkers!"
        },
        "hinata": {
            "morning": "🏐 Good morning! Let's fly high today! Even the shortest player can reach the sky – and so can you!\n\n*Even if I'm not the tallest, I can still fly the highest. That's what today is about – reaching your highest potential.*",
            "goodnight": "The sun has fallen, but our spirits are still high! Today was a good day – we flew high together. Tomorrow, we'll fly even higher.\n\nYou've earned this peace. Goodnight, team. See you in the morning!"
        },
        # WEEK 4
        "dante": {
            "morning": "⚔️ WAKE UP! Let's rock this day like a Devil May Cry boss fight! No holding back – go full throttle!\n\n*I fight for the ones who can't fight for themselves. So go out there and be someone's hero today.*",
            "goodnight": "Alright, party's over for tonight! We had a good run, but even the devil needs sleep. Tomorrow, we'll fight again – harder, faster, better.\n\nPut down your weapons, close your eyes, and let the quiet heal. Sleep well, my fellow fighters. Goodnight!"
        },
        "ash_ketchum": {
            "morning": "⚡ Pikachu! The sun is shining, and a new adventure is waiting! Let's get out there and explore the world!\n\n*Being a Pokémon Master isn't about winning – it's about friendship. So go out there and make some memories.*",
            "goodnight": "Pikachu's tired, I'm tired, and you should be too! Today was an amazing adventure! Tomorrow, there are more Pokémon to meet, more friends to make, and more battles to win.\n\nGotta rest 'em all! Goodnight!"
        },
        "timon_pumbaa": {
            "morning": "🦁🐗 Hakuna Matata! No worries, just wake up and enjoy the day! The sun is up, and so should you be!\n\n*When the world turns its back on you, you turn your back on the world. That's the spirit for today!*",
            "goodnight": "Hakuna Matata! No worries tonight, friends! The day was great, but now it's time to chill. Tomorrow, no worries – we'll face it together.\n\nJust peace. Sleep well! Hakuna Matata!"
        },
        "koro_sensei": {
            "morning": "🐙 TIME FOR CLASS! Wake up, students! A new day means new lessons to learn, and I'm not taking any excuses!\n\n*The greatest lesson is to find your own path. So go out there and forge your own destiny today.*",
            "goodnight": "CLASS DISMISSED, students! You did well today – I'm proud of you. Tomorrow, we'll learn more. We'll grow more.\n\nBut tonight, you are free. No homework. No lessons. Just rest. Goodnight, students! See you in class!"
        },
        "optimus_prime": {
            "morning": "🚛 Rise, Autobots! The day is upon us, and freedom is the right of all sentient beings! Let us protect it!\n\n*Freedom is the right of all sentient beings. Remember that as you go out and make a difference today.*",
            "goodnight": "The Autobots shall rest tonight, my friends. Today, we protected what mattered. Today, we were free. Tomorrow, the fight continues.\n\nBut tonight, let your spark dim and your mind find peace. Until we meet again, Autobots. Goodnight."
        },
        "tetsuya": {
            "morning": "🏀 Let's dance, team! The court is calling, and the ball is waiting! Today we're not just players – we're champions!\n\n*True strength isn't about being the best – it's about trusting your teammates. That's the spirit for today!*",
            "goodnight": "The final buzzer has sounded, team! Today was an epic match – you were incredible! Tomorrow, the court awaits. Tomorrow, we become champions.\n\nBut tonight... you've earned this rest. Dream of the ball, and the glory. Goodnight!"
        }
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
# MESSAGE KEEP PATTERNS (for /clean command) – FINAL VERSION
# ---------------------------------------------------------------------------

def get_keep_patterns():
    return [
        # Morning & Goodnight
        "☀️ *Good Morning",
        "🌅 *Good Morning",
        "🌙 *Goodnight",
        
        # Announcements & Broadcasts
        "📢 *ANNOUNCEMENT",
        "📢 *Tagging everyone",
        "BROADCAST",
        
        # Sunday Standings
        "📅 *SUNDAY STANDINGS",
        
        # Monthly & Yearly Results
        "🏆 *Monthly Results",
        "🎊 *Yearly Champion",
        
        # Welcome Messages
        "👋 *Welcome",
        "☠️ *KONO BOT WA!*",
        
        # Group Stats (admin)
        "📊 *GROUP STATS*",
        
        # Weekly Recap
        "📊 *WEEKLY RECAP*",
        
        # Feedback Confirmations
        "✅ Your feedback has been sent to the Captain",
        
        # Versus – keep challenge announcements and match over
        "⚔️ *VERSUS CHALLENGE*",
        "⚔️ *MATCH OVER*",
    ]
