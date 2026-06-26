import os
import time
import shutil
import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import config
import database

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cached_images")

# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------

def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)

def hex_to_rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"

def lerp_color(c1, c2, t):
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex(int(r1 + (r2 - r1) * t),
                      int(g1 + (g2 - g1) * t),
                      int(b1 + (b2 - b1) * t))

def get_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()

def text_w(draw, text, font):
    try:
        return draw.textlength(text, font=font)
    except Exception:
        try:
            return font.getbbox(text)[2]
        except Exception:
            return len(text) * 8

# ---------------------------------------------------------------------------
# GRADIENT BACKGROUND
# ---------------------------------------------------------------------------

def draw_gradient_bg(image, top_color, bottom_color):
    w, h   = image.size
    draw   = ImageDraw.Draw(image)
    r1,g1,b1 = hex_to_rgb(top_color)
    r2,g2,b2 = hex_to_rgb(bottom_color)
    for y in range(h):
        t = y / h
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return draw

# ---------------------------------------------------------------------------
# GEOMETRIC ILLUSTRATIONS
# ---------------------------------------------------------------------------

def draw_football(draw, cx, cy, radius, alpha=40):
    col  = (255, 255, 255, alpha)
    draw.ellipse([cx-radius, cy-radius, cx+radius, cy+radius],
                 outline=(255,255,255,alpha), width=2)
    pr = radius // 4
    draw.ellipse([cx-pr, cy-pr, cx+pr, cy+pr], fill=(255,255,255,alpha))
    import math
    for i in range(5):
        angle = math.radians(i * 72 - 90)
        px = int(cx + (radius * 0.55) * math.cos(angle))
        py = int(cy + (radius * 0.55) * math.sin(angle))
        sr = radius // 6
        draw.ellipse([px-sr, py-sr, px+sr, py+sr], fill=(255,255,255,alpha))

def draw_trophy(draw, cx, cy, size, color=(255,215,0), alpha=35):
    c = (*color, alpha)
    cup_w = size
    cup_h = int(size * 0.7)
    draw.rectangle([cx - cup_w//2, cy - cup_h, cx + cup_w//2, cy], fill=c)
    handle_w = size // 4
    handle_h = int(cup_h * 0.5)
    draw.rectangle([cx - cup_w//2 - handle_w, cy - cup_h + size//6,
                    cx - cup_w//2,             cy - cup_h + size//6 + handle_h], fill=c)
    draw.rectangle([cx + cup_w//2,             cy - cup_h + size//6,
                    cx + cup_w//2 + handle_w,  cy - cup_h + size//6 + handle_h], fill=c)
    stem_w = size // 6
    stem_h = size // 4
    draw.rectangle([cx - stem_w//2, cy, cx + stem_w//2, cy + stem_h], fill=c)
    base_w = int(size * 0.7)
    base_h = size // 8
    draw.rectangle([cx - base_w//2, cy + stem_h,
                    cx + base_w//2, cy + stem_h + base_h], fill=c)

def draw_crown(draw, cx, cy, size, color=(255,215,0), alpha=40):
    c = (*color, alpha)
    base_y  = cy + size // 2
    base_x1 = cx - size
    base_x2 = cx + size
    draw.polygon([(base_x1, base_y), (base_x1 + size//2, cy - size//2), (base_x1 + size, base_y)], fill=c)
    draw.polygon([(cx - size//2, base_y), (cx, cy - size), (cx + size//2, base_y)], fill=c)
    draw.polygon([(base_x2 - size, base_y), (base_x2 - size//2, cy - size//2), (base_x2, base_y)], fill=c)
    draw.rectangle([base_x1, base_y, base_x2, base_y + size//3], fill=c)

def draw_flame(draw, cx, cy, size, color=(255,100,0), alpha=50):
    c1 = (*color, alpha)
    c2 = (255, 200, 0, alpha)
    draw.polygon([
        (cx, cy - size),
        (cx - size//2, cy + size//3),
        (cx, cy),
        (cx + size//2, cy + size//3),
    ], fill=c1)
    draw.ellipse([cx - size//5, cy - size//4,
                  cx + size//5, cy + size//3], fill=c2)

def draw_controller(draw, cx, cy, size, color=(255,255,255), alpha=25):
    c = (*color, alpha)
    bw, bh = int(size * 1.4), size
    draw.rounded_rectangle([cx - bw//2, cy - bh//2, cx + bw//2, cy + bh//2],
                            radius=size//3, fill=c)
    pad_x = cx - bw//3
    pad_y = cy
    pad_s = size // 5
    draw.rectangle([pad_x - pad_s, pad_y - pad_s//2, pad_x + pad_s, pad_y + pad_s//2], fill=(*hex_to_rgb(config.THEME_BG), alpha+30))
    draw.rectangle([pad_x - pad_s//2, pad_y - pad_s, pad_x + pad_s//2, pad_y + pad_s], fill=(*hex_to_rgb(config.THEME_BG), alpha+30))
    btn_x  = cx + bw//3
    btn_r  = size // 8
    offsets = [(0,-size//5),(size//5,0),(0,size//5),(-size//5,0)]
    btn_colors = [(0,200,100,alpha),(255,50,50,alpha),(50,150,255,alpha),(255,200,0,alpha)]
    for (ox,oy), bc in zip(offsets, btn_colors):
        draw.ellipse([btn_x+ox-btn_r, cy+oy-btn_r, btn_x+ox+btn_r, cy+oy+btn_r], fill=bc)

def draw_lightning(draw, cx, cy, size, color=(255,180,0), alpha=40):
    c = (*color, alpha)
    draw.polygon([
        (cx + size//4, cy - size),
        (cx - size//4, cy),
        (cx + size//8, cy),
        (cx - size//4, cy + size),
        (cx + size//4, cy),
        (cx - size//8, cy),
    ], fill=c)

def draw_star(draw, cx, cy, size, color=(255,215,0), alpha=40):
    import math
    c   = (*color, alpha)
    pts = []
    for i in range(10):
        angle = math.radians(i * 36 - 90)
        r     = size if i % 2 == 0 else size // 2
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=c)

def draw_whistle(draw, cx, cy, size, color=(255,255,255), alpha=30):
    c = (*color, alpha)
    draw.ellipse([cx-size, cy-size//2, cx+size//2, cy+size//2], fill=c)
    draw.rectangle([cx+size//2, cy-size//5, cx+size, cy+size//5], fill=c)
    draw.ellipse([cx-size//3-size//8, cy-size//4,
                  cx-size//3+size//8, cy+size//4],
                 fill=(*hex_to_rgb(config.THEME_BG), 200))

def draw_confetti(draw, w, h, seed=42, count=30, alpha=60):
    import random
    random.seed(seed)
    colors = [
        config.THEME_ACCENT_GOLD, config.THEME_ACCENT,
        config.THEME_ACCENT_RED, config.THEME_ACCENT_BLUE,
        config.THEME_ACCENT_PURPLE, config.THEME_ACCENT_AMBER,
    ]
    for _ in range(count):
        x  = random.randint(0, w)
        y  = random.randint(0, h // 3)
        r  = random.randint(2, 5)
        c  = (*hex_to_rgb(random.choice(colors)), alpha)
        shape = random.randint(0, 1)
        if shape == 0:
            draw.ellipse([x-r, y-r, x+r, y+r], fill=c)
        else:
            draw.rectangle([x-r, y-r, x+r, y+r], fill=c)

def draw_dot_grid(draw, w, h, spacing=30, alpha=12):
    for x in range(0, w, spacing):
        for y in range(0, h, spacing):
            draw.ellipse([x-1, y-1, x+1, y+1],
                         fill=(255, 255, 255, alpha))

def draw_pill(draw, x, y, text, font, bg_color, text_color, padding_x=8, padding_y=3):
    tw     = int(text_w(draw, text, font))
    bx1    = x
    by1    = y
    bx2    = x + tw + padding_x * 2
    by2    = y + font.size + padding_y * 2
    radius = (by2 - by1) // 2
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=radius,
                            fill=hex_to_rgb(bg_color))
    draw.text((bx1 + padding_x, by1 + padding_y), text,
              fill=hex_to_rgb(text_color), font=font)
    return bx2, by2

def draw_accent_bar(draw, x, y, h, color, width=4):
    r = width // 2
    draw.rounded_rectangle([x, y + 4, x + width, y + h - 4],
                            radius=r, fill=hex_to_rgb(color))

def streak_color(streak):
    if streak >= 10: return config.THEME_ACCENT_RED
    if streak >= 5:  return "#FF6B00"
    if streak >= 3:  return config.THEME_ACCENT_AMBER
    return config.THEME_TEXT_MUTED

# ---------------------------------------------------------------------------
# DETECT FIXTURES COLUMNS
# ---------------------------------------------------------------------------

def detect_fixtures_columns(rows):
    """[Matchday, Home Team, Home Score, Away Score, Away Team, Status]"""
    return 1, 4, 2, 3, 5

# ---------------------------------------------------------------------------
# LEAGUE TABLE
# ---------------------------------------------------------------------------

def generate_table_image(bot):
    ensure_cache_dir()
    table_path = os.path.join(CACHE_DIR, "table.png")
    if os.path.exists(table_path):
        return open(table_path, "rb")
    rows_data = database.fetch_csv_cached(bot, config.CURRENT_TABLE_CSV_URL)
    if len(rows_data) <= 1:
        return None
    bio = build_table_image_raw(bot, rows_data)
    if bio:
        with open(table_path, "wb") as f:
            f.write(bio.getvalue())
        bio.seek(0)
        return bio
    return None

def build_table_image_raw(bot, rows_data):
    try:
        IMG_W  = 650
        ROW_H  = 44
        HDR_H  = 90
        COL_H  = 32
        rows   = rows_data[1:]
        IMG_H  = HDR_H + COL_H + len(rows) * ROW_H + 20 + 30

        # Dark theme with gradient background
        bg = Image.new("RGB", (IMG_W, IMG_H), hex_to_rgb(config.THEME_BG))
        bdraw = ImageDraw.Draw(bg)
        r1,g1,b1 = hex_to_rgb(config.THEME_BG)
        r2,g2,b2 = hex_to_rgb(config.THEME_BG_GRADIENT)
        for y in range(IMG_H):
            t = y / IMG_H
            bdraw.line([(0,y),(IMG_W,y)], fill=(
                int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t)
            ))

        # Top accent bar (gold)
        bdraw.rectangle([0, 0, IMG_W, 5], fill=hex_to_rgb(config.THEME_ACCENT_GOLD))

        # Header
        f_title = get_font(18, bold=True)
        f_label = get_font(11)
        f_name  = get_font(14)
        f_bold  = get_font(15, bold=True)
        f_foot  = get_font(12)

        bdraw.text((20, 14), "🏆  ZA SORA ZENITH LEAGUE",
                   fill=hex_to_rgb(config.THEME_ACCENT_GOLD), font=f_title)
        bdraw.text((20, 48), "STANDINGS",
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)
        date_str = datetime.datetime.now().strftime("%d %b %Y")
        tw = int(text_w(bdraw, date_str, f_label))
        bdraw.text((IMG_W - tw - 16, 14), date_str,
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)

        # Column headers
        cy = HDR_H + 7
        bdraw.rectangle([0, HDR_H, IMG_W, HDR_H + COL_H],
                        fill=hex_to_rgb(config.THEME_CARD_BG))
        cols = [
            (8,   "POS"), (50,  "TEAM"),
            (250, "P"),   (290, "W"),  (325, "D"),  (360, "L"),
            (398, "GF"),  (438, "GA"), (478, "GD"), (523, "PTS"),
        ]
        for cx_col, label in cols:
            bdraw.text((cx_col, cy), label,
                       fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)
        bdraw.line([(0, HDR_H + COL_H), (IMG_W, HDR_H + COL_H)],
                   fill=hex_to_rgb(config.THEME_LINE), width=2)

        rank_data = {
            1: (config.THEME_ACCENT_GOLD,   config.THEME_ROW_GOLD,   "🥇"),
            2: (config.THEME_ACCENT_SILVER,  config.THEME_ROW_SILVER, "🥈"),
            3: (config.THEME_ACCENT_BRONZE,  config.THEME_ROW_BRONZE, "🥉"),
        }

        for i, row in enumerate(rows):
            if not row or len(row) < 2:
                continue
            ry = HDR_H + COL_H + i * ROW_H

            rank_val = row[0].strip() if row[0].strip().isdigit() else str(i + 1)
            rank_int = int(rank_val) if rank_val.isdigit() else i + 1

            if rank_int in rank_data:
                accent_c, row_bg, medal = rank_data[rank_int]
                bdraw.rectangle([0, ry, IMG_W, ry + ROW_H],
                                fill=hex_to_rgb(row_bg))
                draw_accent_bar(bdraw, 0, ry, ROW_H, accent_c, width=5)
                if rank_int == 1:
                    draw_crown(bdraw, 28, ry - 10, 12,
                               color=hex_to_rgb(config.THEME_ACCENT_GOLD), alpha=60)
            else:
                accent_c = config.THEME_TEXT_DIM
                medal    = f"#{rank_int}"
                if i % 2 == 0:
                    bdraw.rectangle([0, ry, IMG_W, ry + ROW_H],
                                    fill=(*hex_to_rgb(config.THEME_CARD_BG), 50))
                draw_accent_bar(bdraw, 0, ry, ROW_H, accent_c, width=5)

            text_y = ry + (ROW_H - 14) // 2

            bdraw.text((10, text_y), medal,
                       fill=hex_to_rgb(accent_c), font=f_bold)

            name = row[1].strip() if len(row) > 1 else "?"
            name_color = accent_c if rank_int <= 3 else config.THEME_TEXT_PRIMARY
            bdraw.text((50, text_y), name,
                       fill=hex_to_rgb(name_color),
                       font=f_bold if rank_int <= 3 else f_name)

            def safe(idx):
                return row[idx].strip() if len(row) > idx else "0"

            for val, cx_col in [
                (safe(2), 250), (safe(3), 290), (safe(4), 325), (safe(5), 360),
                (safe(6), 398), (safe(7), 438),
            ]:
                bdraw.text((cx_col, text_y), val, fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_name)

            gd_val = safe(8)
            gd_int = int(gd_val) if gd_val.lstrip('-').isdigit() else 0
            gd_color = config.THEME_ACCENT if gd_int > 0 else config.THEME_ACCENT_RED if gd_int < 0 else config.THEME_TEXT_MUTED
            bdraw.text((478, text_y), gd_val, fill=hex_to_rgb(gd_color), font=f_bold)

            pts_color = accent_c if rank_int <= 3 else config.THEME_ACCENT
            bdraw.text((523, text_y), safe(9), fill=hex_to_rgb(pts_color), font=f_bold)

            bdraw.line([(8, ry + ROW_H - 1), (IMG_W - 8, ry + ROW_H - 1)],
                       fill=hex_to_rgb(config.THEME_LINE), width=1)

        # Footer
        foot_text = "ZA SORA GAME CLUB"
        tw = text_w(bdraw, foot_text, f_foot)
        bdraw.text(((IMG_W - tw)//2, IMG_H - 18), foot_text,
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_foot)

        bio = BytesIO()
        bg.save(bio, 'PNG')
        bio.seek(0)
        return bio

    except Exception as e:
        print(f"Table draw error: {e}")
        import traceback; traceback.print_exc()
        return None

# ---------------------------------------------------------------------------
# FIXTURES IMAGE
# ---------------------------------------------------------------------------

def generate_fixtures_image(bot, rows, status_filter, player_filter,
                             context_filter='all', page=1):
    ensure_cache_dir()
    filename  = (f"{status_filter.lower()}_{player_filter.lower()}_"
                 f"{context_filter.lower()}_page{page}.png").replace(" ", "_")
    file_path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(file_path):
        return open(file_path, "rb")

    if not rows or len(rows) <= 1:
        return None

    home_idx, away_idx, hs_idx, as_idx, status_idx = detect_fixtures_columns(rows)
    header_offset = 1 if (
        'home' in str(rows[0][home_idx]).lower() or
        rows[0][0].lower() in ['md', 'matchday']
    ) else 0

    filtered = []
    for row in rows[header_offset:]:
        if len(row) <= max(home_idx, away_idx, hs_idx, as_idx, status_idx):
            continue
        md, h, a  = row[0].strip() or "MD", row[home_idx].strip(), row[away_idx].strip()
        h_sc, a_sc = row[hs_idx].strip(), row[as_idx].strip()
        st        = row[status_idx].strip()
        if st.lower() != status_filter.lower():
            continue
        ok = ((context_filter == 'all'  and player_filter.lower() in [h.lower(), a.lower()]) or
              (context_filter == 'home' and h.lower() == player_filter.lower()) or
              (context_filter == 'away' and a.lower() == player_filter.lower()))
        if ok:
            filtered.append((md, h, a, h_sc, a_sc))

    if not filtered:
        return None

    chunks      = [filtered[i:i+10] for i in range(0, len(filtered), 10)]
    total_pages = len(chunks)
    if page < 1 or page > total_pages:
        return None

    bio = build_fixtures_image_page_raw(
        bot, chunks[page-1], status_filter, player_filter,
        context_filter, page, total_pages
    )
    if bio:
        with open(file_path, "wb") as f:
            f.write(bio.getvalue())
        bio.seek(0)
    return bio

def generate_matchday_image(bot, rows, matchday):
    ensure_cache_dir()
    filename  = f"matchday_{matchday.lower().replace(' ','_')}.png"
    file_path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(file_path):
        return open(file_path, "rb")

    if not rows or len(rows) <= 1:
        return None

    home_idx, away_idx, hs_idx, as_idx, status_idx = detect_fixtures_columns(rows)
    header_offset = 1 if (
        'home' in str(rows[0][home_idx]).lower() or
        rows[0][0].lower() in ['md', 'matchday']
    ) else 0

    matches = []
    for row in rows[header_offset:]:
        if len(row) <= max(home_idx, away_idx, hs_idx, as_idx, status_idx):
            continue
        md = row[0].strip()
        if md.lower() != matchday.lower():
            continue
        matches.append((
            md,
            row[home_idx].strip(), row[away_idx].strip(),
            row[hs_idx].strip(),   row[as_idx].strip(),
            row[status_idx].strip()
        ))

    if not matches:
        return None

    bio = build_matchday_image_raw(bot, matches, matchday)
    if bio:
        with open(file_path, "wb") as f:
            f.write(bio.getvalue())
        bio.seek(0)
    return bio

def _score_colors(h_sc, a_sc, status):
    WHITE  = config.THEME_TEXT_PRIMARY
    MUTED  = config.THEME_TEXT_MUTED
    GREEN  = config.THEME_ACCENT
    RED    = config.THEME_ACCENT_RED
    AMBER  = config.THEME_ACCENT_AMBER

    if not h_sc or not a_sc or status.lower() != 'completed':
        return WHITE, WHITE, MUTED, MUTED, MUTED

    try:
        hs, as_ = int(h_sc), int(a_sc)
        if hs > as_:
            return GREEN, MUTED, GREEN, RED, GREEN
        elif as_ > hs:
            return MUTED, GREEN, RED, GREEN, GREEN
        else:
            return AMBER, AMBER, AMBER, AMBER, AMBER
    except ValueError:
        return WHITE, WHITE, MUTED, MUTED, MUTED

def build_fixtures_image_page_raw(bot, matches, status_filter, player_filter,
                                   context_filter, page_num, total_pages):
    try:
        IMG_W  = 650
        ROW_H  = 58
        HDR_H  = 85
        IMG_H  = HDR_H + len(matches) * ROW_H + 16 + 30

        f_name  = get_font(14)
        f_label = get_font(11)
        f_bold  = get_font(15, bold=True)
        f_score = get_font(16, bold=True)
        f_title = get_font(17, bold=True)
        f_md    = get_font(10)
        f_foot  = get_font(12)

        is_completed = status_filter.lower() == 'completed'
        top_color    = config.THEME_COMPLETED_BG if is_completed else config.THEME_UPCOMING_BG
        bg_color     = config.THEME_BG

        bg    = Image.new("RGB", (IMG_W, IMG_H), hex_to_rgb(bg_color))
        bdraw = ImageDraw.Draw(bg)

        r1,g1,b1 = hex_to_rgb(top_color)
        r2,g2,b2 = hex_to_rgb(bg_color)
        for y in range(IMG_H):
            t = min(y / (HDR_H * 2), 1.0)
            bdraw.line([(0,y),(IMG_W,y)], fill=(
                int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t)
            ))

        if is_completed:
            draw_whistle(bdraw, IMG_W - 55, 42, 28, alpha=25)
        else:
            draw_football(bdraw, IMG_W - 55, 42, 30, alpha=20)
        draw_lightning(bdraw, IMG_W - 100, IMG_H - 30, 20,
                       color=hex_to_rgb(config.THEME_ACCENT_AMBER), alpha=20)

        accent_c = config.THEME_ACCENT if not is_completed else config.THEME_ACCENT_AMBER
        bdraw.rectangle([0, 0, IMG_W, 4], fill=hex_to_rgb(accent_c))

        bdraw.text((18, 14), f"{status_filter.upper()} MATCHES",
                   fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=f_title)
        bdraw.text((18, 42), f"👤 {player_filter.upper()}  •  🏟️ {context_filter.upper()}",
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)

        page_text = f"PAGE {page_num} / {total_pages}"
        draw_pill(bdraw, IMG_W - 110, 16, page_text, f_md,
                  config.THEME_CARD_BG, config.THEME_TEXT_MUTED)

        bdraw.line([(0, HDR_H - 2), (IMG_W, HDR_H - 2)],
                   fill=hex_to_rgb(config.THEME_LINE), width=1)

        center_x = IMG_W // 2

        for i, (md, home, away, h_sc, a_sc) in enumerate(matches):
            ry = HDR_H + i * ROW_H

            if i % 2 == 0:
                bdraw.rectangle([0, ry, IMG_W, ry + ROW_H],
                                fill=(*hex_to_rgb(config.THEME_CARD_BG), 40))

            text_y  = ry + (ROW_H - 14) // 2
            score_y = ry + (ROW_H - 16) // 2

            is_comp = h_sc and a_sc
            status_label = 'completed' if is_comp else 'upcoming'
            h_name_c, a_name_c, h_sc_c, a_sc_c, _ = _score_colors(h_sc, a_sc, status_label)

            draw_pill(bdraw, 10, ry + (ROW_H - 18) // 2, md.upper(), f_md,
                      "#2a2a32", "#ffffff", padding_x=5, padding_y=3)

            if is_comp:
                score_disp = f"{h_sc}  -  {a_sc}"
            else:
                score_disp = "VS"

            sw = text_w(bdraw, score_disp, f_score)
            sx = center_x - sw // 2

            if is_comp:
                h_s_str = str(h_sc)
                sep     = "  -  "
                a_s_str = str(a_sc)
                h_sw    = text_w(bdraw, h_s_str, f_score)
                sep_w   = text_w(bdraw, sep, f_score)
                bdraw.text((sx, score_y), h_s_str,
                           fill=hex_to_rgb(h_sc_c), font=f_score)
                bdraw.text((sx + h_sw, score_y), sep,
                           fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_score)
                bdraw.text((sx + h_sw + sep_w, score_y), a_s_str,
                           fill=hex_to_rgb(a_sc_c), font=f_score)
            else:
                bdraw.text((sx, score_y), score_disp,
                           fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_score)

            home_txt = home[:14]
            hw       = text_w(bdraw, home_txt, f_name)
            bdraw.text((sx - 18 - hw, text_y), home_txt,
                       fill=hex_to_rgb(h_name_c), font=f_name)

            away_txt = away[:14]
            full_sw  = text_w(bdraw, score_disp, f_score)
            bdraw.text((sx + full_sw + 18, text_y), away_txt,
                       fill=hex_to_rgb(a_name_c), font=f_name)

            bdraw.line([(10, ry + ROW_H - 1), (IMG_W - 10, ry + ROW_H - 1)],
                       fill=hex_to_rgb(config.THEME_LINE), width=1)

        # Footer
        foot_text = "ZA SORA GAME CLUB"
        tw = text_w(bdraw, foot_text, f_foot)
        bdraw.text(((IMG_W - tw)//2, IMG_H - 18), foot_text,
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_foot)

        bio = BytesIO()
        bg.save(bio, 'PNG')
        bio.seek(0)
        return bio

    except Exception as e:
        print(f"Fixtures draw error: {e}")
        import traceback; traceback.print_exc()
        return None

def build_matchday_image_raw(bot, matches, matchday):
    try:
        IMG_W  = 650
        ROW_H  = 58
        HDR_H  = 85
        IMG_H  = HDR_H + len(matches) * ROW_H + 16 + 30

        f_name  = get_font(14)
        f_label = get_font(11)
        f_bold  = get_font(15, bold=True)
        f_score = get_font(16, bold=True)
        f_title = get_font(17, bold=True)
        f_md    = get_font(10)
        f_foot  = get_font(12)

        bg    = Image.new("RGB", (IMG_W, IMG_H), hex_to_rgb(config.THEME_BG))
        bdraw = ImageDraw.Draw(bg)

        r1,g1,b1 = hex_to_rgb(config.THEME_HEADER_BG)
        r2,g2,b2 = hex_to_rgb(config.THEME_BG)
        for y in range(IMG_H):
            t = min(y / (HDR_H * 2), 1.0)
            bdraw.line([(0,y),(IMG_W,y)], fill=(
                int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t)
            ))
        draw_football(bdraw, IMG_W - 55, 42, 32, alpha=22)
        draw_star(bdraw, IMG_W - 110, IMG_H - 30, 15,
                  color=hex_to_rgb(config.THEME_ACCENT_GOLD), alpha=25)

        bdraw.rectangle([0, 0, IMG_W, 4], fill=hex_to_rgb(config.THEME_ACCENT_BLUE))

        bdraw.text((18, 14), f"📅  {matchday.upper()}",
                   fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=f_title)
        bdraw.text((18, 44), "ALL MATCHES THIS MATCHDAY",
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)

        total_text = f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
        draw_pill(bdraw, IMG_W - 100, 18, total_text, f_md,
                  config.THEME_CARD_BG, config.THEME_TEXT_MUTED)

        bdraw.line([(0, HDR_H - 2), (IMG_W, HDR_H - 2)],
                   fill=hex_to_rgb(config.THEME_LINE), width=1)

        center_x = IMG_W // 2

        for i, (md, home, away, h_sc, a_sc, status) in enumerate(matches):
            ry = HDR_H + i * ROW_H
            if i % 2 == 0:
                bdraw.rectangle([0, ry, IMG_W, ry + ROW_H],
                                fill=(*hex_to_rgb(config.THEME_CARD_BG), 40))

            text_y  = ry + (ROW_H - 14) // 2
            score_y = ry + (ROW_H - 16) // 2

            h_name_c, a_name_c, h_sc_c, a_sc_c, _ = _score_colors(h_sc, a_sc, status)

            st_color = (config.THEME_ACCENT if status.lower() == 'completed'
                        else config.THEME_ACCENT_BLUE)
            draw_pill(bdraw, IMG_W - 90, ry + (ROW_H - 18) // 2,
                      status.upper(), f_md, config.THEME_TEXT_DIM, st_color,
                      padding_x=5, padding_y=3)

            is_comp = h_sc and a_sc and status.lower() == 'completed'
            if is_comp:
                h_s, sep, a_s = str(h_sc), "  -  ", str(a_sc)
                h_sw  = text_w(bdraw, h_s, f_score)
                sep_w = text_w(bdraw, sep, f_score)
                a_sw  = text_w(bdraw, a_s, f_score)
                total_sw = h_sw + sep_w + a_sw
                sx = center_x - total_sw // 2
                bdraw.text((sx, score_y), h_s,
                           fill=hex_to_rgb(h_sc_c), font=f_score)
                bdraw.text((sx + h_sw, score_y), sep,
                           fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_score)
                bdraw.text((sx + h_sw + sep_w, score_y), a_s,
                           fill=hex_to_rgb(a_sc_c), font=f_score)
                score_w = total_sw
            else:
                vs_text = "VS"
                vs_w    = text_w(bdraw, vs_text, f_score)
                sx      = center_x - vs_w // 2
                bdraw.text((sx, score_y), vs_text,
                           fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_score)
                score_w = vs_w

            home_txt = home[:14]
            hw       = text_w(bdraw, home_txt, f_name)
            bdraw.text((sx - 18 - hw, text_y), home_txt,
                       fill=hex_to_rgb(h_name_c), font=f_name)
            bdraw.text((sx + score_w + 18, text_y), away[:14],
                       fill=hex_to_rgb(a_name_c), font=f_name)

            bdraw.line([(10, ry + ROW_H - 1), (IMG_W - 10, ry + ROW_H - 1)],
                       fill=hex_to_rgb(config.THEME_LINE), width=1)

        # Footer
        foot_text = "ZA SORA GAME CLUB"
        tw = text_w(bdraw, foot_text, f_foot)
        bdraw.text(((IMG_W - tw)//2, IMG_H - 18), foot_text,
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_foot)

        bio = BytesIO()
        bg.save(bio, 'PNG')
        bio.seek(0)
        return bio

    except Exception as e:
        print(f"Matchday draw error: {e}")
        import traceback; traceback.print_exc()
        return None

# ---------------------------------------------------------------------------
# CARD ENGINE
# ---------------------------------------------------------------------------

def draw_card(header_text, body_lines, footer_text="", accent_color="#00E676", width=650):
    """Draws a generic info card with a header, body, and footer."""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    IMG_W = width
    HEADER_H = 80
    LINE_H = 40
    FOOTER_H = 50 if footer_text else 0

    body_h = len(body_lines) * LINE_H
    IMG_H = HEADER_H + body_h + FOOTER_H + 20

    bg = Image.new("RGB", (IMG_W, IMG_H), hex_to_rgb(config.THEME_BG))
    draw = ImageDraw.Draw(bg)
    r1,g1,b1 = hex_to_rgb(config.THEME_BG)
    r2,g2,b2 = hex_to_rgb(config.THEME_BG_GRADIENT)
    for y in range(IMG_H):
        t = y / IMG_H
        draw.line([(0,y),(IMG_W,y)], fill=(
            int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t)
        ))

    draw.rectangle([0, 0, IMG_W, 5], fill=hex_to_rgb(accent_color))

    f_title = get_font(22, bold=True)
    tw = text_w(draw, header_text, f_title)
    draw.text(((IMG_W - tw)//2, 18), header_text, fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=f_title)

    f_body = get_font(16)
    y = HEADER_H + 10
    for line in body_lines:
        draw.text((20, y), line, fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=f_body)
        y += LINE_H

    if footer_text:
        f_foot = get_font(14)
        tw = text_w(draw, footer_text, f_foot)
        draw.text(((IMG_W - tw)//2, IMG_H - 30), footer_text, fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_foot)

    bio = BytesIO()
    bg.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ---------------------------------------------------------------------------
# BADGE ICON
# ---------------------------------------------------------------------------

def draw_badge_icon(draw, x, y, badge_id, size=24):
    """Draws a circular badge icon."""
    badge = config.ACHIEVEMENTS.get(badge_id, {})
    color = badge.get("color", "#888888")
    icon = badge.get("icon", "🏅")

    draw.ellipse([x, y, x+size, y+size], fill=hex_to_rgb(color))
    f = get_font(size-4)
    tw = text_w(draw, icon, f)
    draw.text((x + (size - tw)//2, y + 4), icon, fill=(255,255,255), font=f)
    return x + size + 4

# ---------------------------------------------------------------------------
# PROFILE CARD (Group-based, no avatar)
# ---------------------------------------------------------------------------

def build_profile_card(chat_id, user_id, username=None, bot=None):
    """Generates a profile card using group-based stats (no avatar)."""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    data = database.load_json(config.GROUP_DATA_FILE, {})
    chat_str = str(chat_id)
    user_str = str(user_id)
    if chat_str not in data or user_str not in data[chat_str]:
        return None
    u = data[chat_str][user_str]
    username = username or u.get("username", "Player")

    # Gather stats
    title = database._get_active_title(u) or "No Title"
    points = u.get("points", 0)
    alltime = u.get("alltime_points", 0)
    streak = u.get("streak", 0)
    best_streak = u.get("best_streak", 0)
    played = u.get("games_played", 0)
    correct = u.get("correct", 0)
    accuracy = f"{int((correct / played) * 100)}%" if played > 0 else "N/A"
    badges = u.get("badges", [])[:6]

    W = 650
    H = 650
    bg = Image.new("RGB", (W, H), hex_to_rgb(config.THEME_BG))
    draw = ImageDraw.Draw(bg)

    # Gradient background
    r1,g1,b1 = hex_to_rgb(config.THEME_BG)
    r2,g2,b2 = hex_to_rgb(config.THEME_BG_GRADIENT)
    for y in range(H):
        t = y / H
        draw.line([(0,y),(W,y)], fill=(
            int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t)
        ))

    # Top accent bar (gold)
    draw.rectangle([0, 0, W, 4], fill=hex_to_rgb(config.THEME_ACCENT_GOLD))

    # Avatar circle with initial
    avatar_radius = 45
    avatar_center = (70, 80)
    draw.ellipse(
        (avatar_center[0]-avatar_radius, avatar_center[1]-avatar_radius,
         avatar_center[0]+avatar_radius, avatar_center[1]+avatar_radius),
        fill=hex_to_rgb(config.THEME_ACCENT)
    )
    f_avatar = get_font(30, bold=True)
    tw = text_w(draw, username[0].upper(), f_avatar)
    draw.text(
        (avatar_center[0] - tw//2, avatar_center[1] - 15),
        username[0].upper(),
        fill=hex_to_rgb(config.THEME_TEXT_PRIMARY),
        font=f_avatar
    )

    # Username
    f_name = get_font(22, bold=True)
    draw.text((140, 55), username, fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=f_name)

    # Title
    f_title = get_font(14)
    draw.text((140, 90), f"🏅 {title}", fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_title)

    # Stats grid
    stats = [
        ("💰 Points", str(points)),
        ("🔥 Streak", f"{streak} (Best {best_streak})"),
        ("🎯 Accuracy", accuracy),
        ("🎮 Games", str(played)),
    ]
    box_w = 135
    box_h = 60
    gap = 10
    start_x = 30
    start_y = 150
    for i, (label, value) in enumerate(stats):
        x = start_x + i * (box_w + gap)
        draw.rounded_rectangle(
            [x, start_y, x+box_w, start_y+box_h],
            radius=8,
            fill=hex_to_rgb(config.THEME_CARD_BG)
        )
        f_val = get_font(16, bold=True)
        tw = text_w(draw, value, f_val)
        draw.text((x + (box_w - tw)//2, start_y + 8), value, fill=hex_to_rgb(config.THEME_ACCENT_GOLD), font=f_val)
        f_lbl = get_font(10)
        tw2 = text_w(draw, label, f_lbl)
        draw.text((x + (box_w - tw2)//2, start_y + box_h - 18), label, fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_lbl)

    # Badges section
    badge_y = 240
    draw.text((30, badge_y), "📛 BADGES", fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=get_font(14, bold=True))
    if badges:
        badge_x = 30
        for badge_id in badges[:6]:
            badge = config.ACHIEVEMENTS.get(badge_id, {})
            icon = badge.get("icon", "🏅")
            name = badge.get("name", badge_id)
            draw.ellipse(
                [badge_x, badge_y+30, badge_x+40, badge_y+70],
                fill=hex_to_rgb(config.THEME_ACCENT_PURPLE)
            )
            f_badge = get_font(18)
            tw = text_w(draw, icon, f_badge)
            draw.text((badge_x + (40 - tw)//2, badge_y + 36), icon, fill=(255,255,255), font=f_badge)
            f_name_small = get_font(9)
            tw2 = text_w(draw, name[:10], f_name_small)
            draw.text((badge_x + (40 - tw2)//2, badge_y + 72), name[:10], fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_name_small)
            badge_x += 55
    else:
        draw.text((30, badge_y+35), "None yet. Keep playing!", fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=get_font(14))

    # Status / Bio
    status_y = 340
    draw.rectangle([20, status_y, W-20, status_y+60], fill=hex_to_rgb(config.THEME_CARD_BG))
    quote = database.get_random_quote()
    if quote:
        status_text = f"💬 {quote['text'][:60]}"
    else:
        status_text = f"💬 {username} is ready to play!"
    f_status = get_font(12)
    tw = text_w(draw, status_text, f_status)
    if tw > W - 60:
        status_text = status_text[:55] + "..."
    draw.text((35, status_y+20), status_text, fill=hex_to_rgb(config.THEME_TEXT_PRIMARY), font=f_status)

    # Footer
    f_foot = get_font(12)
    foot_text = "ZA SORA GAME CLUB"
    tw = text_w(draw, foot_text, f_foot)
    draw.text(((W - tw)//2, H-25), foot_text, fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_foot)

    bio = BytesIO()
    bg.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ---------------------------------------------------------------------------
# LEADERBOARD
# ---------------------------------------------------------------------------

def build_leaderboard_image(chat_id, mode="monthly", page=1):
    try:
        ENTRIES_PER_PAGE = 10
        offset = (page - 1) * ENTRIES_PER_PAGE

        all_entries = database.get_leaderboard(chat_id, mode=mode, top_n=100)
        entries = all_entries[offset:offset + ENTRIES_PER_PAGE]
        total_pages = (len(all_entries) + ENTRIES_PER_PAGE - 1) // ENTRIES_PER_PAGE

        IMG_W  = 650
        ROW_H  = 56
        HDR_H  = 95
        FOOT_H = 20
        IMG_H  = HDR_H + max(len(entries), 1) * ROW_H + FOOT_H + 30

        f_name  = get_font(14)
        f_label = get_font(11)
        f_bold  = get_font(15, bold=True)
        f_title = get_font(18, bold=True)
        f_sub   = get_font(11)
        f_badge = get_font(10)
        f_foot  = get_font(12)

        mode_labels = {"monthly": "MONTHLY", "yearly": "YEARLY", "alltime": "ALL TIME"}

        bg    = Image.new("RGB", (IMG_W, IMG_H), hex_to_rgb(config.THEME_BG))
        bdraw = ImageDraw.Draw(bg)
        r1,g1,b1 = hex_to_rgb(config.THEME_BG)
        r2,g2,b2 = hex_to_rgb(config.THEME_BG_GRADIENT)
        for y in range(IMG_H):
            t = y / IMG_H
            bdraw.line([(0,y),(IMG_W,y)], fill=(
                int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t)
            ))

        draw_confetti(bdraw, IMG_W, IMG_H, count=35, alpha=55)
        draw_controller(bdraw, IMG_W - 65, IMG_H - 50, 45, alpha=18)
        draw_trophy(bdraw, IMG_W - 55, HDR_H // 2,
                    32, color=hex_to_rgb(config.THEME_ACCENT_GOLD), alpha=22)

        bdraw.rectangle([0, 0, IMG_W, HDR_H],
                        fill=hex_to_rgb(config.THEME_HEADER_BG))

        bar_colors = {
            "monthly":  config.THEME_ACCENT,
            "yearly":   config.THEME_ACCENT_GOLD,
            "alltime":  config.THEME_ACCENT_PURPLE,
        }
        bdraw.rectangle([0, 0, IMG_W, 5],
                        fill=hex_to_rgb(bar_colors.get(mode, config.THEME_ACCENT)))

        header_title = f"🏆  ZA SORA GAME CLUB"
        bdraw.text((20, 14), header_title,
                   fill=hex_to_rgb(config.THEME_ACCENT_GOLD), font=f_title)

        sub_header = f"LEADERBOARD — {mode_labels.get(mode, '').upper()}"
        if total_pages > 1:
            sub_header += f" (Page {page}/{total_pages})"
        bdraw.text((20, 48), sub_header,
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)

        date_str = datetime.datetime.now().strftime("%d %b %Y")
        bdraw.text((IMG_W - 100, 14), date_str,
                   fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_label)

        bdraw.line([(0, HDR_H - 1), (IMG_W, HDR_H - 1)],
                   fill=hex_to_rgb(config.THEME_LINE), width=1)

        rank_data = {
            1: (config.THEME_ACCENT_GOLD,   config.THEME_ROW_GOLD,   "🥇"),
            2: (config.THEME_ACCENT_SILVER,  config.THEME_ROW_SILVER, "🥈"),
            3: (config.THEME_ACCENT_BRONZE,  config.THEME_ROW_BRONZE, "🥉"),
        }

        if not entries:
            bdraw.text((20, HDR_H + 20), "No scores yet this period.",
                       fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_name)
        else:
            for i, (rank, username, points, streak, title) in enumerate(entries):
                actual_rank = offset + i + 1
                ry = HDR_H + i * ROW_H

                if actual_rank in rank_data:
                    accent_c, row_bg, medal = rank_data[actual_rank]
                    bdraw.rectangle([0, ry, IMG_W, ry + ROW_H],
                                    fill=hex_to_rgb(row_bg))
                    draw_accent_bar(bdraw, 0, ry, ROW_H, accent_c, width=5)
                    if actual_rank == 1:
                        draw_crown(bdraw, 28, ry - 10, 12,
                                   color=hex_to_rgb(config.THEME_ACCENT_GOLD), alpha=60)
                else:
                    accent_c = config.THEME_TEXT_DIM
                    medal    = f"#{actual_rank}"
                    if i % 2 == 0:
                        bdraw.rectangle([0, ry, IMG_W, ry + ROW_H],
                                        fill=(*hex_to_rgb(config.THEME_CARD_BG), 50))
                    draw_accent_bar(bdraw, 0, ry, ROW_H, accent_c, width=5)

                text_y = ry + (ROW_H - 14) // 2

                bdraw.text((12, text_y), medal,
                           fill=hex_to_rgb(accent_c), font=f_bold)

                name_x = 60
                bdraw.text((name_x, text_y), username[:20],
                           fill=hex_to_rgb(accent_c if actual_rank <= 3 else config.THEME_TEXT_PRIMARY),
                           font=f_bold if actual_rank <= 3 else f_name)

                if title:
                    draw_pill(bdraw, name_x, text_y + 18, title, f_badge,
                              config.THEME_TEXT_DIM, accent_c if actual_rank <= 3 else config.THEME_TEXT_MUTED,
                              padding_x=5, padding_y=2)

                pts_str = f"{points:,} pts"
                pw      = text_w(bdraw, pts_str, f_bold)
                bdraw.text((IMG_W - pw - 80, text_y), pts_str,
                           fill=hex_to_rgb(accent_c if actual_rank <= 3 else config.THEME_ACCENT),
                           font=f_bold)

                if streak > 0:
                    s_color = streak_color(streak)
                    s_str   = f"🔥 {streak}"
                    sw_     = text_w(bdraw, s_str, f_sub)
                    bdraw.text((IMG_W - sw_ - 12, text_y), s_str,
                               fill=hex_to_rgb(s_color), font=f_bold if streak >= 5 else f_sub)

                    if streak >= 5:
                        flame_c = hex_to_rgb(config.THEME_ACCENT_RED if streak >= 10 else "#FF6B00")
                        draw_flame(bdraw, IMG_W - sw_ - 28, text_y + 8, 8,
                                   color=flame_c, alpha=70)

                bdraw.line([(8, ry + ROW_H - 1), (IMG_W - 8, ry + ROW_H - 1)],
                           fill=hex_to_rgb(config.THEME_LINE), width=1)

        if total_pages > 1:
            nav_text = f"Page {page}/{total_pages}  —  Use /leaderboard {page+1} to go next"
            if page == total_pages:
                nav_text = f"Page {page}/{total_pages}  —  This is the last page"
            f_nav = get_font(12)
            tw = text_w(bdraw, nav_text, f_nav)
            bdraw.text(((IMG_W - tw)//2, IMG_H - 25), nav_text,
                       fill=hex_to_rgb(config.THEME_TEXT_MUTED), font=f_nav)

        bio = BytesIO()
        bg.save(bio, 'PNG')
        bio.seek(0)
        return bio

    except Exception as e:
        print(f"Leaderboard draw error: {e}")
        import traceback; traceback.print_exc()
        return None

# ---------------------------------------------------------------------------
# CACHE ENGINE
# ---------------------------------------------------------------------------

def clear_and_rebuild_disk_cache(bot):
    print("[CACHE ENGINE] Rebuilding cache...")
    try:
        if os.path.exists(CACHE_DIR):
            shutil.rmtree(CACHE_DIR)
        ensure_cache_dir()

        table_rows    = database.fetch_csv_cached(bot, config.CURRENT_TABLE_CSV_URL)
        fixtures_rows = database.fetch_csv_cached(bot, config.FIXTURES_CSV_URL)

        if table_rows and len(table_rows) > 1:
            img = build_table_image_raw(bot, table_rows)
            if img:
                with open(os.path.join(CACHE_DIR, "table.png"), "wb") as f:
                    f.write(img.getvalue())

        if fixtures_rows and len(fixtures_rows) > 1:
            home_idx, away_idx, hs_idx, as_idx, status_idx = detect_fixtures_columns(fixtures_rows)
            header_offset = 1 if (
                'home' in str(fixtures_rows[0][home_idx]).lower() or
                fixtures_rows[0][0].lower() in ['md', 'matchday']
            ) else 0

            teams     = set()
            matchdays = set()
            for row in fixtures_rows[header_offset:]:
                if len(row) > max(home_idx, away_idx):
                    h = row[home_idx].strip()
                    a = row[away_idx].strip()
                    if h and not h.isdigit(): teams.add(h)
                    if a and not a.isdigit(): teams.add(a)
                    md = row[0].strip()
                    if md: matchdays.add(md)

            for md in matchdays:
                img_bio = generate_matchday_image(bot, fixtures_rows, md)

            for team in teams:
                for status in ['upcoming', 'completed']:
                    for ctx in ['all', 'home', 'away']:
                        filtered = []
                        for row in fixtures_rows[header_offset:]:
                            if len(row) <= max(home_idx, away_idx, hs_idx, as_idx, status_idx): continue
                            md_r = row[0].strip() or "Matchday"
                            h, a = row[home_idx].strip(), row[away_idx].strip()
                            h_sc, a_sc = row[hs_idx].strip(), row[as_idx].strip()
                            st   = row[status_idx].strip()
                            if st.lower() != status.lower(): continue
                            ok = ((ctx == 'all' and team.lower() in [h.lower(), a.lower()]) or
                                  (ctx == 'home' and h.lower() == team.lower()) or
                                  (ctx == 'away' and a.lower() == team.lower()))
                            if ok: filtered.append((md_r, h, a, h_sc, a_sc))
                        if not filtered: continue
                        chunks      = [filtered[i:i+10] for i in range(0, len(filtered), 10)]
                        total_pages = len(chunks)
                        for idx, chunk in enumerate(chunks):
                            page_num = idx + 1
                            img_bio  = build_fixtures_image_page_raw(
                                bot, chunk, status, team, ctx, page_num, total_pages
                            )
                            if img_bio:
                                fn = f"{status.lower()}_{team.lower()}_{ctx.lower()}_page{page_num}.png".replace(" ", "_")
                                with open(os.path.join(CACHE_DIR, fn), "wb") as f:
                                    f.write(img_bio.getvalue())

        print("[CACHE ENGINE] Done!")
    except Exception as e:
        print(f"Cache rebuild error: {e}")
        import traceback; traceback.print_exc()
