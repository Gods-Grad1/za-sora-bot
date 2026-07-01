import os

# ============================================================
# ZA SORA BOT — MASTER CONFIGURATION
# ============================================================

# --- CORE API & SECURITY ---
API_TOKEN   = os.environ.get("API_TOKEN", "")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", 0))

# --- GOOGLE SHEETS ---
CURRENT_TABLE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSh6Ulx9_QZOrrAFNA4l1zj3Gv16HCpLilwhudvSJu4zUeMEoQDn5MM7UFe4c2hoUVSr0JYdNOggi-_/pub?gid=0&single=true&output=csv"
FIXTURES_CSV_URL      = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSh6Ulx9_QZOrrAFNA4l1zj3Gv16HCpLilwhudvSJu4zUeMEoQDn5MM7UFe4c2hoUVSr0JYdNOggi-_/pub?gid=745306980&single=true&output=csv"

# --- GITHUB IMAGE BACKUP ---
GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com/Gods-Grad1/za-sora-bot/main/images/"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "Gods-Grad1/za-sora-bot"
GITHUB_BRANCH = "main"

# --- TRIVIA (stored on generated branch) ---
TRIVIA_BRANCH = "generated"
TRIVIA_REMOTE_PATH = "trivia"
TRIVIA_CATEGORY_FILES = {
    "Gaming": "gaming.json",
    "Anime": "anime.json",
    "Movies": "movies.json",
    "General": "general.json",
    "Food": "food.json",
    "Sports": "sports.json",
    "Technology": "technology.json",
    "Bible": "bible.json",
}
TRIVIA_DB = "trivia_db.json"

# --- DATABASE FILE PATHS ---
GROUP_DATA_FILE   = "group_data.json"
GROUPS_FILE       = "groups_db.txt"
STATE_FILE        = "bot_state.json"
CHAR_DB           = "characters_db.json"
MEDIA_DB          = "media_db.json"
QUOTES_FILE       = "quotes.json"
SCHEDULER_FILE    = "scheduler.json"
SHOP_FILE         = "shop.json"
LEADERBOARD_FILE  = "leaderboard_history.json"
VERSUS_FILE       = "versus_state.json"
DAILY_FILE        = "daily_challenge.json"
MUTE_FILE         = "muted_users.json"
BROADCAST_FILE    = "broadcasts.json"

# --- IMAGE DIRECTORIES ---
IMAGE_CACHE_DIR        = "game_image_cache"
LOCAL_CHAR_IMAGES_DIR  = "local_images/characters"
LOCAL_MEDIA_IMAGES_DIR = "local_images/media"

# --- TIMEZONE ---
TIMEZONE         = "Africa/Johannesburg"
MORNING_MSG_HOUR = 8
MORNING_MSG_MIN  = 0
DAILY_CHALLENGE_HOUR = 14
DAILY_CHALLENGE_MIN  = 0

# --- SCHEDULER WINDOW — HARDCODED 10–23 ---
SCHEDULER_WINDOW_START = 10   # was 18/19 – now fixed
SCHEDULER_WINDOW_END   = 23

# --- POINTS ECONOMY ---
POINTS_CHARACTER_GAME  = 50
POINTS_YEAR_GAME       = 50
POINTS_TRIVIA          = 75
POINTS_VERSUS_WIN      = 100
POINTS_DAILY_CHALLENGE = 150
POINTS_HINT_PENALTY    = 15
POINTS_HINT_MAX        = 3

# --- STREAK MULTIPLIERS ---
STREAK_MULTIPLIERS = {0: 1.0, 3: 2.0, 5: 3.0, 10: 5.0}

# --- VERSUS MODE ---
VERSUS_ACCEPT_TIMEOUT  = 60
VERSUS_QUESTION_TIME   = 30
VERSUS_BET_WINDOW      = 30
VERSUS_MAX_BET         = 20

# --- TAG ALL ---
TAGALL_COOLDOWN_HOURS  = 6

# --- SCHEDULER ---
SCHEDULE_INTERVALS = [30, 60, 120, 240, 360]
SHOP_TITLE_DURATION_DAYS = 30

# --- AUTO-DELETE DELAYS ---
AUTO_DELETE_DELAY      = 300
GAME_AUTO_DELETE_DELAY = 1800
ADMIN_PANEL_DELAY      = 600

# --- DESIGN PALETTE ---
THEME_BG            = "#0d0d0f"
THEME_BG_GRADIENT   = "#1a1a1f"
THEME_HEADER_BG     = "#111116"
THEME_UPCOMING_BG   = "#0f1a2e"
THEME_COMPLETED_BG  = "#0a1f14"
THEME_TEXT_PRIMARY  = "#ffffff"
THEME_TEXT_MUTED    = "#5c5c61"
THEME_TEXT_DIM      = "#3a3a3f"
THEME_ACCENT        = "#00E676"
THEME_ACCENT_GOLD   = "#FFD700"
THEME_ACCENT_SILVER = "#C0C0C0"
THEME_ACCENT_BRONZE = "#CD7F32"
THEME_ACCENT_RED    = "#FF1744"
THEME_ACCENT_AMBER  = "#FFB300"
THEME_ACCENT_BLUE   = "#0a84ff"
THEME_ACCENT_PURPLE = "#BF5FFF"
THEME_LINE          = "#1e1e24"
THEME_CARD_BG       = "#16161b"
THEME_ROW_GOLD      = "#2a2200"
THEME_ROW_SILVER    = "#1e1e1e"
THEME_ROW_BRONZE    = "#1f1200"

# --- SPLIT DATABASE FILE PATHS ---
MEDIA_DB          = "media_db.json"
ANIME_SERIES_DB   = "anime_series_db.json"
ANIME_FILMS_DB    = "anime_films_db.json"
ANIMATION_DB      = "animation_db.json"
MEDIA_ALL_DBS     = ["media_db.json", "anime_series_db.json", "anime_films_db.json", "animation_db.json"]

CHAR_DB              = "characters_anime_db.json"
CHAR_ANIME_DB        = "characters_anime_db.json"
CHAR_DC_DB           = "characters_dc_db.json"
CHAR_MARVEL_DB       = "characters_marvel_db.json"
CHAR_GAMING_DB       = "characters_gaming_db.json"
CHAR_ALL_DBS         = ["characters_anime_db.json", "characters_dc_db.json", "characters_marvel_db.json", "characters_gaming_db.json"]

TRIVIA_CATEGORIES = ["Gaming", "Anime", "Movies", "General", "Food", "Sports", "Technology", "Bible"]

YEAR_CATEGORIES = {
    "movies":       "media_db.json",
    "anime_series": "anime_series_db.json",
    "anime_films":  "anime_films_db.json",
    "animation":    "animation_db.json",
}

CHAR_CATEGORIES = {
    "anime":   "characters_anime_db.json",
    "dc":      "characters_dc_db.json",
    "marvel":  "characters_marvel_db.json",
    "gaming":  "characters_gaming_db.json",
}

DEFAULT_ANSWER_TIME = 60

# --- WELCOME MESSAGE ---
WELCOME_MSG = (
    "☠️ *KONO BOT WA!* ☠️\n\n"
    "Ore wa Monkey D. Luffy! Kaizoku-ou ni naru otoko da! 🏴‍☠️\n\n"
    "This isn't just a bot — it's our Nakama! A place where we\n"
    "sail together, challenge each other, and become stronger!\n\n"
    "🏆 *Our Dream:* To build the greatest crew of gamers,\n"
    "where everyone earns their place through skill and guts!\n\n"
    "⚔️ Ready to join the adventure? Type /help and let's go!\n\n"
    "*ZAAA SORAAA!* 🌊🔥"
)

# --- POWER-UPS ---
POWERUPS = {
    "fifty_fifty": {"name": "50/50", "cost": 100, "emoji": "✂️", "description": "Removes two wrong answers in trivia."},
    "streak_freeze": {"name": "Streak Freeze", "cost": 200, "emoji": "🧊", "description": "Your streak won't break on one wrong answer."},
    "double_down": {"name": "Double Down", "cost": 150, "emoji": "⬆️", "description": "Double points on next correct."},
}

SHOP_TITLES = [
    {"id": "quiz_master",   "name": "🏆 Quiz Master",     "cost": 200},
    {"id": "on_fire",       "name": "🔥 On Fire",         "cost": 150},
    {"id": "game_god",      "name": "🎮 Game God",        "cost": 300},
    {"id": "bible_scholar", "name": "📖 Bible Scholar",   "cost": 200},
    {"id": "anime_king",    "name": "👑 Anime King",      "cost": 200},
    {"id": "movie_buff",    "name": "🎬 Movie Buff",      "cost": 150},
    {"id": "the_goat",      "name": "🐐 The GOAT",        "cost": 500},
    {"id": "night_owl",     "name": "🦉 Night Owl",       "cost": 150},
    {"id": "lightning",     "name": "⚡ Lightning",       "cost": 200},
    {"id": "chosen_one",    "name": "✨ The Chosen One",  "cost": 400},
    {"id": "grinder",       "name": "💪 The Grinder",     "cost": 150},
    {"id": "legend",        "name": "🌟 Legend",          "cost": 350},
    {"id": "mystery_box",   "name": "🎁 Mystery Box",     "cost": 75},
    {"id": "double_xp",     "name": "⚡ Double XP (1hr)", "cost": 150},
    {"id": "hint_tokens",   "name": "💡 Hint Tokens x3",  "cost": 100},
    {"id": "fifty_fifty",   "name": "✂️ 50/50",           "cost": 100},
    {"id": "streak_freeze", "name": "🧊 Streak Freeze",   "cost": 200},
    {"id": "double_down",   "name": "⬆️ Double Down",     "cost": 150},
]

WHEEL_SLOTS = [
    {"name": "50 pts", "points": 50, "weight": 10},
    {"name": "30 pts", "points": 30, "weight": 15},
    {"name": "20 pts", "points": 20, "weight": 15},
    {"name": "Hint Token x2", "hint_token": 2, "weight": 5},
    {"name": "Double XP (1h)", "double_xp": 3600, "weight": 5},
    {"name": "-5 pts", "points": -5, "weight": 15},
    {"name": "-10 pts", "points": -10, "weight": 15},
    {"name": "-20 pts", "points": -20, "weight": 8},
    {"name": "Bankrupt!", "bankrupt": True, "weight": 5},
    {"name": "Nothing!", "points": 0, "weight": 7},
]

ACHIEVEMENTS = {
    "first_blood": {"name": "First Blood", "icon": "🩸", "description": "First correct answer", "condition": {"correct": 1}},
    "streak_5": {"name": "Streak 5", "icon": "🔥", "description": "5 correct in a row", "condition": {"best_streak": 5}},
    "streak_10": {"name": "Streak 10", "icon": "🔥🔥", "description": "10 correct in a row", "condition": {"best_streak": 10}},
    "streak_25": {"name": "Streak 25", "icon": "⭐", "description": "25 correct in a row", "condition": {"best_streak": 25}},
    "centurion": {"name": "Centurion", "icon": "💯", "description": "100 correct answers", "condition": {"correct": 100}},
    "trivia_king": {"name": "Trivia King", "icon": "👑", "description": "50 trivia correct", "condition": {"trivia_correct": 50}},
    "versus_win": {"name": "Versus Champ", "icon": "⚔️", "description": "5 versus wins", "condition": {"versus_wins": 5}},
    "daily_champ": {"name": "Daily Champ", "icon": "🌞", "description": "10 daily wins", "condition": {"daily_wins": 10}},
    "quiz_master": {"name": "Quiz Master", "icon": "📚", "description": "200 total correct", "condition": {"correct": 200}},
}
