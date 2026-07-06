import os
import io
import time
import random
import requests
import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import config
import database

# ---------------------------------------------------------------------------
# CACHE
# ---------------------------------------------------------------------------

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graphics_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _get_cached_image(key, max_age=3600):
    cache_path = os.path.join(CACHE_DIR, f"{key}.png")
    if os.path.exists(cache_path):
        if time.time() - os.path.getmtime(cache_path) < max_age:
            return cache_path
    return None

def _cache_image(key, image):
    cache_path = os.path.join(CACHE_DIR, f"{key}.png")
    image.save(cache_path, 'PNG')
    return cache_path

# ---------------------------------------------------------------------------
# FONTS
# ---------------------------------------------------------------------------

def _get_font(size, bold=False):
    try:
        if bold:
            font_path = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans-Bold.ttf")
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, size)
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
    except Exception:
        pass
    return ImageFont.load_default()

# ---------------------------------------------------------------------------
# LEAGUE TABLE IMAGE
# ---------------------------------------------------------------------------

def generate_table_image(bot):
    try:
        rows = database.fetch_csv_cached(bot, config.CURRENT_TABLE_CSV_URL)
        if not rows or len(rows) <= 1:
            return None

        header = rows[0]
        data = rows[1:]

        pos_idx, team_idx, mp_idx, w_idx, d_idx, l_idx, gf_idx, ga_idx, gd_idx, pts_idx, form_idx = _detect_table_columns(header)

        table_data = []
        for row in data:
            if len(row) <= max(pos_idx, team_idx, pts_idx):
                continue
            try:
                pos = int(row[pos_idx]) if row[pos_idx].strip().isdigit() else 0
                team = row[team_idx].strip()
                mp = int(row[mp_idx]) if mp_idx is not None and row[mp_idx].strip().isdigit() else 0
                w = int(row[w_idx]) if w_idx is not None and row[w_idx].strip().isdigit() else 0
                d = int(row[d_idx]) if d_idx is not None and row[d_idx].strip().isdigit() else 0
                l = int(row[l_idx]) if l_idx is not None and row[l_idx].strip().isdigit() else 0
                gf = int(row[gf_idx]) if gf_idx is not None and row[gf_idx].strip().isdigit() else 0
                ga = int(row[ga_idx]) if ga_idx is not None and row[ga_idx].strip().isdigit() else 0
                gd = gf - ga  # Calculate GD
                pts = int(row[pts_idx]) if row[pts_idx].strip().isdigit() else 0
                form = row[form_idx].strip() if form_idx is not None and len(row) > form_idx else ""
            except (ValueError, IndexError):
                continue
            table_data.append({
                "pos": pos, "team": team, "mp": mp, "w": w, "d": d, "l": l,
                "gf": gf, "ga": ga, "gd": gd, "pts": pts, "form": form
            })

        if not table_data:
            return None

        table_data.sort(key=lambda x: x["pos"])
        max_games = config.SEASON_MAX_GAMES
        is_complete = all(team["mp"] >= max_games for team in table_data)

        return _render_table_image(table_data, is_complete)

    except Exception as e:
        print(f"Table generation error: {e}")
        return None

def _detect_table_columns(header):
    pos_idx = 0
    team_idx = 1
    mp_idx = None
    w_idx = None
    d_idx = None
    l_idx = None
    gf_idx = None
    ga_idx = None
    gd_idx = None
    pts_idx = None
    form_idx = None

    for i, col in enumerate(header):
        col_lower = col.lower().strip()
        if col_lower in ["pos", "#", "position"]:
            pos_idx = i
        elif col_lower in ["team", "club", "name"]:
            team_idx = i
        elif col_lower in ["mp", "p", "played", "matches"]:
            mp_idx = i
        elif col_lower in ["w", "win", "won"]:
            w_idx = i
        elif col_lower in ["d", "draw", "drawn"]:
            d_idx = i
        elif col_lower in ["l", "loss", "lost"]:
            l_idx = i
        elif col_lower in ["gf", "goals for", "for"]:
            gf_idx = i
        elif col_lower in ["ga", "goals against", "against"]:
            ga_idx = i
        elif col_lower in ["gd", "goal diff", "diff", "goal difference"]:
            gd_idx = i
        elif col_lower in ["pts", "points"]:
            pts_idx = i
        elif col_lower in ["form", "recent"]:
            form_idx = i

    return pos_idx, team_idx, mp_idx, w_idx, d_idx, l_idx, gf_idx, ga_idx, gd_idx, pts_idx, form_idx

def _render_table_image(table_data, is_complete):
    cols = ["POS", "TEAM", "MP", "W", "D", "L", "GF", "GA", "GD", "PTS", "FORM"]
    col_widths = [40, 180, 40, 40, 40, 40, 40, 40, 40, 50, 80]
    row_height = 30
    header_height = 35
    padding = 10
    total_width = sum(col_widths) + padding * 2
    total_rows = len(table_data)
    total_height = header_height + total_rows * row_height + padding * 2

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)

    header_font = _get_font(12, bold=True)
    row_font = _get_font(11)
    small_font = _get_font(9)

    x = padding
    y = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 6), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")

    y += header_height

    for idx, team in enumerate(table_data):
        row_y = y + idx * row_height
        is_champion = is_complete and idx == 0

        if is_champion:
            bg_color = config.THEME_ROW_GOLD
        elif idx == 1 and is_complete:
            bg_color = config.THEME_ROW_SILVER
        elif idx == 2 and is_complete:
            bg_color = config.THEME_ROW_BRONZE
        elif idx % 2 == 0:
            bg_color = config.THEME_BG
        else:
            bg_color = config.THEME_CARD_BG

        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)
        draw.line([padding, row_y, padding + total_width - padding * 2, row_y], fill=config.THEME_LINE, width=1)

        gd = team["gd"]
        pts = team["pts"]
        gd_color = config.THEME_ACCENT if gd > 0 else config.THEME_ACCENT_RED if gd < 0 else config.THEME_TEXT_MUTED
        pts_color = config.THEME_ACCENT_GOLD if pts >= 99 else config.THEME_ACCENT if pts > 0 else config.THEME_TEXT_MUTED

        row_data = [
            str(team["pos"]),
            team["team"][:25],
            str(team["mp"]),
            str(team["w"]),
            str(team["d"]),
            str(team["l"]),
            str(team["gf"]),
            str(team["ga"]),
            str(gd),
            str(pts),
            team["form"][:5]
        ]

        custom_colors = [None, None, None, None, None, None, None, None, gd_color, pts_color, None]

        for i, (col, value) in enumerate(zip(cols, row_data)):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            font = row_font
            color = config.THEME_TEXT_PRIMARY

            if is_champion:
                color = config.THEME_ACCENT_GOLD
                font = header_font if i == 0 else row_font

            if custom_colors[i] is not None:
                color = custom_colors[i]

            if i == 0:
                draw.text((cx - 5, row_y + row_height // 2 - 6), value, fill=color, font=font, anchor="rm")
            elif i == len(cols) - 1:
                _draw_form_indicators(draw, cx, row_y, value, small_font)
            else:
                draw.text((cx, row_y + row_height // 2 - 6), value, fill=color, font=font, anchor="mt")

        if is_champion:
            draw.text((padding + 5, row_y + 2), "👑", font=small_font)
        elif idx == 1 and is_complete:
            draw.text((padding + 5, row_y + 2), "🥈", font=small_font)
        elif idx == 2 and is_complete:
            draw.text((padding + 5, row_y + 2), "🥉", font=small_font)

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

def _draw_form_indicators(draw, x, y, form, font):
    colors = {"W": "#00E676", "D": "#FFD700", "L": "#FF1744"}
    box_size = 14
    spacing = 2
    total_width = len(form) * (box_size + spacing) - spacing
    start_x = x - total_width // 2

    for i, letter in enumerate(form[:5]):
        letter = letter.upper()
        bx = start_x + i * (box_size + spacing)
        by = y + (16 - box_size) // 2
        color = colors.get(letter, "#555555")
        draw.rectangle([bx, by, bx + box_size, by + box_size], fill=color)
        draw.text((bx + box_size // 2, by + 2), letter, fill="#FFFFFF", font=font, anchor="mt")

# ---------------------------------------------------------------------------
# FIXTURES IMAGE (UPDATED – Matchday, Status column, hardcoded venue)
# ---------------------------------------------------------------------------

def generate_fixtures_image(bot, rows, status, player, context, page):
    if not rows or len(rows) <= 1:
        return None

    home_idx, away_idx, matchday_idx, status_idx = detect_fixtures_columns(rows)

    header_offset = 1
    if rows and len(rows) > 0:
        first_row = rows[0]
        if len(first_row) > max(home_idx, away_idx):
            if first_row[0].lower() in ["md", "matchday", "round"]:
                header_offset = 1

    fixtures = []
    for row in rows[header_offset:]:
        if len(row) <= max(home_idx, away_idx):
            continue
        home = row[home_idx].strip() if home_idx < len(row) else ""
        away = row[away_idx].strip() if away_idx < len(row) else ""
        if not home or not away or home.isdigit() or away.isdigit():
            continue

        if context == "home" and player.lower() != home.lower():
            continue
        if context == "away" and player.lower() != away.lower():
            continue
        if context == "all" and player.lower() not in [home.lower(), away.lower()]:
            continue

        matchday = row[matchday_idx].strip() if matchday_idx is not None and len(row) > matchday_idx else ""
        row_status = row[status_idx].strip().lower() if status_idx is not None and len(row) > status_idx else ""

        if status == "upcoming" and row_status in ["completed", "played", "finished"]:
            continue
        if status == "completed" and row_status not in ["completed", "played", "finished"]:
            continue

        fixtures.append({
            "home": home,
            "away": away,
            "matchday": matchday,
            "status": row_status
        })

    per_page = 10
    start = (page - 1) * per_page
    end = start + per_page
    page_fixtures = fixtures[start:end]

    if not page_fixtures:
        return None

    total_pages = (len(fixtures) + per_page - 1) // per_page
    return _render_fixtures_image(page_fixtures, player, context, status, page, total_pages)

def detect_fixtures_columns(rows):
    home_idx, away_idx, matchday_idx, status_idx = 0, 0, None, None
    if not rows:
        return 0, 0, None, None

    header = rows[0]
    for i, col in enumerate(header):
        col_lower = col.lower().strip()
        if col_lower in ["home", "hometeam", "home team", "home_team"]:
            home_idx = i
        elif col_lower in ["away", "awayteam", "away team", "away_team"]:
            away_idx = i
        elif col_lower in ["matchday", "md", "round", "match day"]:
            matchday_idx = i
        elif col_lower in ["status", "result", "completed", "played", "score"]:
            status_idx = i

    return home_idx, away_idx, matchday_idx, status_idx

def _render_fixtures_image(fixtures, player, context, status, page, total_pages):
    cols = ["MATCHDAY", "HOME", "VS", "AWAY"]
    col_widths = [80, 150, 30, 150]
    row_height = 28
    header_height = 32
    padding = 10
    total_width = sum(col_widths) + padding * 2
    total_height = header_height + len(fixtures) * row_height + padding * 2 + 40

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)

    header_font = _get_font(12, bold=True)
    row_font = _get_font(11)

    title = f"📋 {status.upper()} FIXTURES"
    draw.text((total_width // 2, 5), title, fill=config.THEME_ACCENT, font=header_font, anchor="mt")

    subtitle = f"👤 {player.upper()} | 🏟️ {context.upper()} | 📄 Page {page}/{total_pages}"
    draw.text((total_width // 2, 22), subtitle, fill=config.THEME_TEXT_MUTED, font=row_font, anchor="mt")

    y = 40

    x = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 6), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")

    y += header_height

    for idx, fixture in enumerate(fixtures):
        row_y = y + idx * row_height
        bg_color = config.THEME_CARD_BG if idx % 2 == 0 else config.THEME_BG
        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)

        row_data = [
            fixture.get("matchday", ""),
            fixture["home"][:20],
            "VS",
            fixture["away"][:20]
        ]

        for i, value in enumerate(row_data):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            color = config.THEME_TEXT_PRIMARY
            if i == 1 and player.lower() in value.lower():
                color = config.THEME_ACCENT
            elif i == 3 and player.lower() in value.lower():
                color = config.THEME_ACCENT
            draw.text((cx, row_y + row_height // 2 - 6), value, fill=color, font=row_font, anchor="mt")

    venue_text = "📍 Venue: Education Hall A"
    draw.text((padding, total_height - 15), venue_text, fill=config.THEME_TEXT_MUTED, font=row_font)

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ---------------------------------------------------------------------------
# LEADERBOARD IMAGE
# ---------------------------------------------------------------------------

def build_leaderboard_image(chat_id, mode, page):
    entries = database.get_leaderboard(chat_id, mode=mode, top_n=100)
    if not entries:
        return None

    per_page = 10
    start = (page - 1) * per_page
    end = start + per_page
    page_entries = entries[start:end]

    if not page_entries:
        return None

    cols = ["#", "PLAYER", "PTS", "STREAK"]
    col_widths = [30, 180, 60, 60]
    row_height = 26
    header_height = 30
    padding = 10
    total_width = sum(col_widths) + padding * 2
    total_height = header_height + len(page_entries) * row_height + padding * 2 + 20

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)

    header_font = _get_font(11, bold=True)
    row_font = _get_font(10)

    title = f"🏆 LEADERBOARD — {mode.upper()}"
    draw.text((total_width // 2, 5), title, fill=config.THEME_ACCENT, font=header_font, anchor="mt")

    y = 30

    x = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 6), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")

    y += header_height

    for idx, (rank, username, points, streak, title) in enumerate(page_entries):
        row_y = y + idx * row_height
        if rank == 1:
            bg_color = config.THEME_ROW_GOLD
            rank_color = config.THEME_ACCENT_GOLD
        elif rank == 2:
            bg_color = config.THEME_ROW_SILVER
            rank_color = config.THEME_ACCENT_SILVER
        elif rank == 3:
            bg_color = config.THEME_ROW_BRONZE
            rank_color = config.THEME_ACCENT_BRONZE
        else:
            bg_color = config.THEME_CARD_BG if idx % 2 == 0 else config.THEME_BG
            rank_color = config.THEME_TEXT_MUTED

        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)

        row_data = [
            str(rank),
            username[:20],
            str(points),
            f"🔥 {streak}" if streak > 0 else "—"
        ]

        for i, value in enumerate(row_data):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            color = config.THEME_TEXT_PRIMARY
            if i == 0:
                color = rank_color
            elif i == 2:
                color = config.THEME_ACCENT if points > 0 else config.THEME_TEXT_MUTED
            draw.text((cx, row_y + row_height // 2 - 6), value, fill=color, font=row_font, anchor="mt")

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ---------------------------------------------------------------------------
# MATCHDAY IMAGE
# ---------------------------------------------------------------------------

def generate_matchday_image(bot, rows, matchday):
    if not rows or len(rows) <= 1:
        return None

    home_idx, away_idx, matchday_idx, status_idx = detect_fixtures_columns(rows)
    header_offset = 1 if ("home" in str(rows[0][home_idx]).lower() or rows[0][0].lower() in ["md", "matchday"]) else 0

    fixtures = []
    for row in rows[header_offset:]:
        if len(row) <= max(home_idx, away_idx):
            continue
        md = row[matchday_idx].strip() if matchday_idx is not None and len(row) > matchday_idx else ""
        if md.lower() != matchday.lower() and md != f"MD {matchday}" and md != f"Matchday {matchday}":
            continue
        home = row[home_idx].strip()
        away = row[away_idx].strip()
        if not home or not away or home.isdigit() or away.isdigit():
            continue
        fixtures.append({"home": home, "away": away, "matchday": md})

    if not fixtures:
        return None

    return _render_matchday_image(fixtures, matchday)

def _render_matchday_image(fixtures, matchday):
    cols = ["MATCHDAY", "HOME", "VS", "AWAY"]
    col_widths = [80, 150, 30, 150]
    row_height = 28
    header_height = 32
    padding = 10
    total_width = sum(col_widths) + padding * 2
    total_height = header_height + len(fixtures) * row_height + padding * 2 + 40

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)

    header_font = _get_font(12, bold=True)
    row_font = _get_font(11)

    title = f"📅 MATCHDAY {matchday} — ALL FIXTURES"
    draw.text((total_width // 2, 5), title, fill=config.THEME_ACCENT, font=header_font, anchor="mt")

    y = 30

    x = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 6), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")

    y += header_height

    for idx, fixture in enumerate(fixtures):
        row_y = y + idx * row_height
        bg_color = config.THEME_CARD_BG if idx % 2 == 0 else config.THEME_BG
        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)

        row_data = [
            fixture.get("matchday", ""),
            fixture["home"][:20],
            "VS",
            fixture["away"][:20]
        ]

        for i, value in enumerate(row_data):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            draw.text((cx, row_y + row_height // 2 - 6), value, fill=config.THEME_TEXT_PRIMARY, font=row_font, anchor="mt")

    venue_text = "📍 Venue: Education Hall A"
    draw.text((padding, total_height - 15), venue_text, fill=config.THEME_TEXT_MUTED, font=row_font)

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ---------------------------------------------------------------------------
# CACHE REBUILD
# ---------------------------------------------------------------------------

def clear_and_rebuild_disk_cache(bot):
    try:
        print("🔄 Clearing graphics cache...")
        for f in os.listdir(CACHE_DIR):
            if f.endswith('.png'):
                os.remove(os.path.join(CACHE_DIR, f))
        print("✅ Cache cleared.")
    except Exception as e:
        print(f"Cache clear error: {e}")
