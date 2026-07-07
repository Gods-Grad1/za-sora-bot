import base64
import requests
import config

def upload_image_to_github(bot, image_data, filename, folder, branch=None):
    """
    Uploads an image to the GitHub repo.
    - bot: the Telegram bot instance (for logging)
    - image_data: bytes of the image
    - filename: e.g., "luffy.jpg"
    - folder: "characters", "media", "morning", "goodnight", "themes", "scrambled"
    - branch: which branch to commit to (defaults to config.TRIVIA_BRANCH)
    
    Returns: (bool, str) -> (success, message/URL)
    """
    if branch is None:
        branch = config.TRIVIA_BRANCH
    
    if not config.GITHUB_TOKEN:
        error_msg = "❌ GITHUB_TOKEN not set in config"
        print(error_msg)
        if bot:
            try:
                bot.send_message(config.ADMIN_ID, error_msg, parse_mode=None)
            except Exception:
                pass
        return False, error_msg
    
    # Ensure folder path is correct
    if folder.startswith("images/"):
        folder = folder.replace("images/", "")
    
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/images/{folder}/{filename}"
    headers = {
        "Authorization": f"token {config.GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json"
    }

    # 1. Check if file already exists (to get SHA for overwriting)
    try:
        response = requests.get(url, headers=headers)
        sha = None
        if response.status_code == 200:
            sha = response.json().get("sha")
        elif response.status_code == 404:
            # File doesn't exist – good, we'll create it
            pass
        else:
            print(f"⚠️ Unexpected response checking {filename}: {response.status_code}")
    except Exception as e:
        return False, f"Error checking file: {e}"

    # 2. Prepare payload
    content = base64.b64encode(image_data).decode('utf-8')
    payload = {
        "message": f"Add/update {filename}",
        "content": content,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    # 3. Upload
    try:
        response = requests.put(url, headers=headers, json=payload)
        if response.status_code in (200, 201):
            # Return the raw URL where the image is hosted
            raw_url = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/{branch}/images/{folder}/{filename}"
            print(f"✅ Uploaded {filename} to {raw_url}")
            return True, raw_url
        else:
            error_msg = f"GitHub upload failed: {response.status_code} - {response.text}"
            print(f"❌ {error_msg}")
            if bot:
                try:
                    bot.send_message(config.ADMIN_ID, f"❌ Upload failed: {filename}\n{response.text[:200]}", parse_mode=None)
                except Exception:
                    pass
            return False, error_msg
    except Exception as e:
        return False, f"Upload error: {e}"
