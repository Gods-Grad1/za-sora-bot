import os
import io
import time
import random
import requests
import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import config
import database

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

# ============================================================
# UPDATED _get_font – loads from local or downloads from generated branch
# ============================================================

def _get_font(size, bold=False):
    try:
        font_name = "arial_bold.ttf" if bold else "arial.ttf"

        # 1. Try local fonts folder (if cloned with generated branch)
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", font_name)
        if os.path.exists(local_path):
            return ImageFont.truetype(local_path, size)

        # 2. Try to download from GitHub raw (generated branch)
        remote_url = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/generated/fonts/{font_name}"
        try:
            resp = requests.get(remote_url, timeout=5)
            if resp.status_code == 200:
                from io import BytesIO
                return ImageFont.truetype(BytesIO(resp.content), size)
        except Exception:
            pass

        # 3. System fonts fallback
        system_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:\\Windows\\Fonts\\Arial.ttf",
        ]
        for path in system_fonts:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    except Exception as e:
        print(f"Font load error: {e}")

    print("⚠️ No TrueType font found – using default bitmap font (may be pixelated).")
    return ImageFont.load_default()

# ---------------------------------------------------------------------------
# LEAGUE TABLE – unchanged
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
                gd = gf - ga
                pts = int(row[pts_idx]) if row[pts_idx].strip().isdigit() else 0
            except (ValueError, IndexError):
                continue
            table_data.append({
                "pos": pos, "team": team, "mp": mp, "w": w, "d": d, "l": l,
                "gf": gf, "ga": ga, "gd": gd, "pts": pts
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
    pos_idx, team_idx = 0, 1
    mp_idx = w_idx = d_idx = l_idx = gf_idx = ga_idx = gd_idx = pts_idx = form_idx = None
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
    cols = ["POS", "TEAM", "MP", "W", "D", "L", "GF", "GA", "GD", "PTS"]
    col_widths = [50, 220, 50, 50, 50, 50, 50, 50, 50, 60]
    row_height = 40
    header_height = 48
    padding = 12
    total_width = sum(col_widths) + padding * 2
    total_rows = len(table_data)
    total_height = header_height + total_rows * row_height + padding * 2

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)
    header_font = _get_font(18, bold=True)
    row_font = _get_font(15)

    x, y = padding, padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 8), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")
    y += header_height

    for idx, team in enumerate(table_data):
        row_y = y + idx * row_height
        is_champion = is_complete and idx == 0
        bg_color = config.THEME_ROW_GOLD if is_champion else (config.THEME_ROW_SILVER if (idx == 1 and is_complete) else (config.THEME_ROW_BRONZE if (idx == 2 and is_complete) else (config.THEME_CARD_BG if idx % 2 else config.THEME_BG)))
        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)
        draw.line([padding, row_y, padding + total_width - padding * 2, row_y], fill=config.THEME_LINE, width=1)

        gd = team["gd"]
        pts = team["pts"]
        gd_color = config.THEME_ACCENT if gd > 0 else config.THEME_ACCENT_RED if gd < 0 else config.THEME_TEXT_MUTED
        pts_color = config.THEME_ACCENT_GOLD if pts >= 99 else config.THEME_ACCENT if pts > 0 else config.THEME_TEXT_MUTED

        row_data = [str(team["pos"]), team["team"][:25], str(team["mp"]), str(team["w"]), str(team["d"]), str(team["l"]), str(team["gf"]), str(team["ga"]), str(gd), str(pts)]
        custom_colors = [None, None, None, None, None, None, None, None, gd_color, pts_color]

        for i, (col, value) in enumerate(zip(cols, row_data)):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            font = row_font
            color = config.THEME_TEXT_PRIMARY
            if is_champion:
                color = config.THEME_ACCENT_GOLD
                font = header_font if i == 0 else row_font
            if custom_colors[i] is not None:
                color = custom_colors[i]
            draw.text((cx, row_y + row_height // 2 - 8), value, fill=color, font=font, anchor="mt")
        if is_champion:
            draw.text((padding + 5, row_y + 4), "👑", font=_get_font(12))
        elif idx == 1 and is_complete:
            draw.text((padding + 5, row_y + 4), "🥈", font=_get_font(12))
        elif idx == 2 and is_complete:
            draw.text((padding + 5, row_y + 4), "🥉", font=_get_font(12))

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ---------------------------------------------------------------------------
# FIXTURES – HIGH RES & WINNER HIGHLIGHTING
# ---------------------------------------------------------------------------

def generate_fixtures_image(bot, rows, status, player, context, page):
    if not rows or len(rows) <= 1:
        return None
    home_idx, away_idx, matchday_idx, status_idx, home_score_idx, away_score_idx = detect_fixtures_columns(rows)
    header_offset = 1 if rows and len(rows) > 0 and (rows[0][0].lower() in ["md", "matchday", "round"]) else 0
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
        home_score = row[home_score_idx].strip() if home_score_idx is not None and len(row) > home_score_idx else ""
        away_score = row[away_score_idx].strip() if away_score_idx is not None and len(row) > away_score_idx else ""
        if status == "upcoming" and row_status in ["completed", "played", "finished"]:
            continue
        if status == "completed" and row_status not in ["completed", "played", "finished"]:
            continue
        fixtures.append({
            "home": home, "away": away, "matchday": matchday, "status": row_status,
            "home_score": home_score, "away_score": away_score
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
    home_score_idx, away_score_idx = None, None
    if not rows:
        return 0, 0, None, None, None, None
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
        elif "home score" in col_lower or "hscore" in col_lower or "home_goals" in col_lower:
            home_score_idx = i
        elif "away score" in col_lower or "ascore" in col_lower or "away_goals" in col_lower:
            away_score_idx = i
    return home_idx, away_idx, matchday_idx, status_idx, home_score_idx, away_score_idx

# ============================================================
# UPDATED HIGH-RES _render_fixtures_image
# ============================================================

def _render_fixtures_image(fixtures, player, context, status, page, total_pages):
    cols = ["MATCHDAY", "HOME", "SCORE", "AWAY"]
    col_widths = [140, 260, 140, 260]
    row_height = 52
    header_height = 60
    padding = 16

    total_width = sum(col_widths) + padding * 2
    total_height = header_height + len(fixtures) * row_height + padding * 2 + 60

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)

    header_font = _get_font(22, bold=True)
    row_font = _get_font(18)

    title = f"📋 {status.upper()} FIXTURES"
    draw.text((total_width // 2, 10), title, fill=config.THEME_ACCENT, font=header_font, anchor="mt")
    subtitle = f"👤 {player.upper()} | 🏟️ {context.upper()} | 📄 Page {page}/{total_pages}"
    draw.text((total_width // 2, 40), subtitle, fill=config.THEME_TEXT_MUTED, font=row_font, anchor="mt")
    y = 62

    x = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 10), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")
    y += header_height

    for idx, fixture in enumerate(fixtures):
        row_y = y + idx * row_height
        bg_color = config.THEME_CARD_BG if idx % 2 == 0 else config.THEME_BG
        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)

        home_score = fixture.get("home_score", "")
        away_score = fixture.get("away_score", "")
        score_text = f"{home_score} - {away_score}" if (home_score or away_score) else "–"

        # WINNER HIGHLIGHTING: Gold / Red / Amber
        score_color = config.THEME_TEXT_MUTED
        if home_score and away_score and home_score.isdigit() and away_score.isdigit():
            h, a = int(home_score), int(away_score)
            if h > a:
                score_color = config.THEME_ACCENT_GOLD
            elif a > h:
                score_color = config.THEME_ACCENT_RED
            else:
                score_color = config.THEME_ACCENT_AMBER

        row_data = [
            fixture.get("matchday", ""),
            fixture["home"][:22],
            score_text,
            fixture["away"][:22]
        ]

        for i, value in enumerate(row_data):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            color = config.THEME_TEXT_PRIMARY
            if i == 1 and player.lower() in value.lower():
                color = config.THEME_ACCENT
            elif i == 3 and player.lower() in value.lower():
                color = config.THEME_ACCENT
            elif i == 2:
                color = score_color
            draw.text((cx, row_y + row_height // 2 - 10), value, fill=color, font=row_font, anchor="mt")

    venue_text = "📍 Venue: Education Hall A"
    draw.text((padding, total_height - 22), venue_text, fill=config.THEME_TEXT_MUTED, font=row_font)

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ============================================================
# UPDATED HIGH-RES _render_matchday_image
# ============================================================

def generate_matchday_image(bot, rows, matchday):
    if not rows or len(rows) <= 1:
        return None
    home_idx, away_idx, matchday_idx, status_idx, home_score_idx, away_score_idx = detect_fixtures_columns(rows)
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
        home_score = row[home_score_idx].strip() if home_score_idx is not None and len(row) > home_score_idx else ""
        away_score = row[away_score_idx].strip() if away_score_idx is not None and len(row) > away_score_idx else ""
        fixtures.append({"home": home, "away": away, "matchday": md, "home_score": home_score, "away_score": away_score})
    if not fixtures:
        return None
    return _render_matchday_image(fixtures, matchday)

def _render_matchday_image(fixtures, matchday):
    cols = ["MATCHDAY", "HOME", "SCORE", "AWAY"]
    col_widths = [140, 260, 140, 260]
    row_height = 52
    header_height = 60
    padding = 16

    total_width = sum(col_widths) + padding * 2
    total_height = header_height + len(fixtures) * row_height + padding * 2 + 60

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)

    header_font = _get_font(22, bold=True)
    row_font = _get_font(18)

    title = f"📅 MATCHDAY {matchday} — ALL FIXTURES"
    draw.text((total_width // 2, 10), title, fill=config.THEME_ACCENT, font=header_font, anchor="mt")
    y = 50

    x = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 10), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")
    y += header_height

    for idx, fixture in enumerate(fixtures):
        row_y = y + idx * row_height
        bg_color = config.THEME_CARD_BG if idx % 2 == 0 else config.THEME_BG
        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)

        home_score = fixture.get("home_score", "")
        away_score = fixture.get("away_score", "")
        score_text = f"{home_score} - {away_score}" if (home_score or away_score) else "–"

        # WINNER HIGHLIGHTING: Gold / Red / Amber
        score_color = config.THEME_TEXT_MUTED
        if home_score and away_score and home_score.isdigit() and away_score.isdigit():
            h, a = int(home_score), int(away_score)
            if h > a:
                score_color = config.THEME_ACCENT_GOLD
            elif a > h:
                score_color = config.THEME_ACCENT_RED
            else:
                score_color = config.THEME_ACCENT_AMBER

        row_data = [
            fixture.get("matchday", ""),
            fixture["home"][:22],
            score_text,
            fixture["away"][:22]
        ]

        for i, value in enumerate(row_data):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            color = config.THEME_TEXT_PRIMARY if i != 2 else score_color
            draw.text((cx, row_y + row_height // 2 - 10), value, fill=color, font=row_font, anchor="mt")

    venue_text = "📍 Venue: Education Hall A"
    draw.text((padding, total_height - 22), venue_text, fill=config.THEME_TEXT_MUTED, font=row_font)

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ---------------------------------------------------------------------------
# LEADERBOARD – unchanged
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
    col_widths = [50, 240, 90, 90]
    row_height = 38
    header_height = 46
    padding = 12
    total_width = sum(col_widths) + padding * 2
    total_height = header_height + len(page_entries) * row_height + padding * 2 + 30

    img = Image.new('RGB', (total_width, total_height), color=config.THEME_BG)
    draw = ImageDraw.Draw(img)
    header_font = _get_font(16, bold=True)
    row_font = _get_font(14)

    title = f"🏆 LEADERBOARD — {mode.upper()}"
    draw.text((total_width // 2, 6), title, fill=config.THEME_ACCENT, font=header_font, anchor="mt")
    y = 32

    x = padding
    draw.rectangle([x, y, x + total_width - padding * 2, y + header_height], fill=config.THEME_HEADER_BG)
    for i, col in enumerate(cols):
        cx = x + sum(col_widths[:i]) + col_widths[i] // 2
        draw.text((cx, y + header_height // 2 - 8), col, fill=config.THEME_TEXT_PRIMARY, font=header_font, anchor="mt")
    y += header_height

    for idx, (rank, username, points, streak, title) in enumerate(page_entries):
        row_y = y + idx * row_height
        bg_color = config.THEME_ROW_GOLD if rank == 1 else config.THEME_ROW_SILVER if rank == 2 else config.THEME_ROW_BRONZE if rank == 3 else (config.THEME_CARD_BG if idx % 2 == 0 else config.THEME_BG)
        rank_color = config.THEME_ACCENT_GOLD if rank == 1 else config.THEME_ACCENT_SILVER if rank == 2 else config.THEME_ACCENT_BRONZE if rank == 3 else config.THEME_TEXT_MUTED
        draw.rectangle([padding, row_y, padding + total_width - padding * 2, row_y + row_height], fill=bg_color)

        row_data = [str(rank), username[:20], str(points), f"🔥 {streak}" if streak > 0 else "—"]
        for i, value in enumerate(row_data):
            cx = padding + sum(col_widths[:i]) + col_widths[i] // 2
            color = rank_color if i == 0 else config.THEME_ACCENT if i == 2 and points > 0 else config.THEME_TEXT_MUTED
            draw.text((cx, row_y + row_height // 2 - 8), value, fill=color, font=row_font, anchor="mt")

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ---------------------------------------------------------------------------
# CACHE
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
