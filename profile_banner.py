import os
import io
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import config
import database
from github_uploader import upload_image_to_github

# --- Constants ---
BANNER_TEMPLATE_URL = f"{config.GITHUB_RAW_BASE_URL}templates/profile_banner.png"
BANNER_CACHE_DIR = "profile_banners"
TEMPLATE_CACHE_FILE = "banner_template.png"
PROFILES_REMOTE_FOLDER = "profiles"  # Not used anymore, kept for compatibility

# --- Template Measurements (YOUR EXACT COORDINATES) ---
CIRCLE_CENTER_X = 575
CIRCLE_CENTER_Y = 334
CIRCLE_RADIUS   = 130   # Outer radius – the profile picture fills this

TEXT_BOX_X = 738
TEXT_BOX_Y = 285
TEXT_BOX_WIDTH = 549
TEXT_BOX_HEIGHT = 93

USERNAME_FONT_SIZE = 55  # Max font size (auto-scales down if needed)
USERNAME_COLOR = "#FFFFFF"  # White text

# -------------------------------------------------------------------

def _ensure_dirs():
    os.makedirs(BANNER_CACHE_DIR, exist_ok=True)

def _get_template():
    """Downloads the template from GitHub or uses cached version."""
    _ensure_dirs()
    template_path = os.path.join(BANNER_CACHE_DIR, TEMPLATE_CACHE_FILE)
    
    if os.path.exists(template_path):
        return Image.open(template_path)
    
    try:
        response = requests.get(BANNER_TEMPLATE_URL, timeout=10)
        response.raise_for_status()
        with open(template_path, 'wb') as f:
            f.write(response.content)
        return Image.open(template_path)
    except Exception as e:
        print(f"Failed to download template: {e}")
        return None

def _get_profile_picture(bot, user_id):
    """Fetches the user's profile picture from Telegram."""
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count == 0:
            return None
        
        file_id = photos.photos[0][-1].file_id
        file_info = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{config.API_TOKEN}/{file_info.file_path}"
        
        response = requests.get(file_url, timeout=10)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content))
    except Exception as e:
        print(f"Failed to get profile picture for {user_id}: {e}")
        return None

def _create_circle_mask(size):
    """Creates a circular mask for the profile picture."""
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0) + size, fill=255)
    return mask

def _get_font_size(text, box_width, box_height, max_font_size):
    """Adjusts font size to fit text inside the box."""
    font_size = max_font_size
    while font_size > 10:
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'arial.ttf')
            font = ImageFont.truetype(font_path, font_size)
        except:
            font = ImageFont.load_default()
        
        draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        padding = 20
        if text_width <= box_width - padding and text_height <= box_height - padding:
            return font_size
        font_size -= 2
    
    return 20

def _add_text_to_banner(img, text, box_x, box_y, box_width, box_height, max_font_size, color):
    """Adds centered text inside the text box."""
    draw = ImageDraw.Draw(img)
    
    font_size = _get_font_size(text, box_width, box_height, max_font_size)
    
    try:
        font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'arial.ttf')
        font = ImageFont.truetype(font_path, font_size)
    except:
        font = ImageFont.load_default()
    
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = box_x + (box_width - text_width) // 2
    y = box_y + (box_height - text_height) // 2
    
    draw.text((x, y), text, fill=color, font=font)

def _get_existing_github_url(user_id):
    """Checks if the user already has a banner stored on GitHub."""
    data = database.load_json(config.GROUP_DATA_FILE, {})
    for chat_str, users in data.items():
        for user_str, u in users.items():
            if int(user_str) == user_id:
                return u.get("profile_banner_url")
    return None

def _save_github_url_to_user(bot, user_id, url):
    """Saves the GitHub URL to the user's profile data."""
    data = database.load_json(config.GROUP_DATA_FILE, {})
    for chat_str, users in data.items():
        for user_str, u in users.items():
            if int(user_str) == user_id:
                u["profile_banner_url"] = url
                database.save_json(bot, config.GROUP_DATA_FILE, data)
                return True
    return False

def generate_profile_banner(bot, user_id, username):
    """
    Generates a profile banner, uploads to GitHub (generated branch), and returns the raw URL.
    If the banner already exists on GitHub, returns that URL directly.
    """
    _ensure_dirs()
    
    # 1. Check if we already have a GitHub URL stored
    existing_url = _get_existing_github_url(user_id)
    if existing_url:
        return existing_url
    
    # 2. Load template
    template = _get_template()
    if template is None:
        return None
    
    img = template.copy()
    
    # 3. Get profile picture
    profile_pic = _get_profile_picture(bot, user_id)
    
    if profile_pic:
        size = (CIRCLE_RADIUS * 2, CIRCLE_RADIUS * 2)
        profile_pic = profile_pic.resize(size, Image.Resampling.LANCZOS)
        mask = _create_circle_mask(size)
        
        left = CIRCLE_CENTER_X - CIRCLE_RADIUS
        top = CIRCLE_CENTER_Y - CIRCLE_RADIUS
        img.paste(profile_pic, (left, top), mask)
    else:
        # Optional: Draw a default avatar (circle with initials) here.
        pass
    
    # 4. Add username centered in the text box
    username_display = f"@{username}"
    _add_text_to_banner(img, username_display, TEXT_BOX_X, TEXT_BOX_Y,
                        TEXT_BOX_WIDTH, TEXT_BOX_HEIGHT,
                        USERNAME_FONT_SIZE, USERNAME_COLOR)
    
    # 5. Save temporary local file
    local_path = os.path.join(BANNER_CACHE_DIR, f"profile_{user_id}.png")
    img.save(local_path, 'PNG')
    
    # 6. Upload to GitHub (generated branch, banners folder) <-- CHANGED
    with open(local_path, 'rb') as f:
        image_data = f.read()
    
    remote_filename = f"{user_id}.png"
    upload_success = upload_image_to_github(bot, image_data, remote_filename, "banners", branch="generated")
    
    if upload_success:
        # Construct raw URL for the generated branch <-- CHANGED
        raw_url = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/generated/images/banners/{remote_filename}"
        _save_github_url_to_user(bot, user_id, raw_url)
        # Optionally remove local file to save space
        # os.remove(local_path)
        return raw_url
    else:
        # Fallback: return local file path
        return local_path
