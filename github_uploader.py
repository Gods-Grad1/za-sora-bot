import base64
import requests
import config

def upload_image_to_github(bot, image_data, filename, folder, branch=None):
    if branch is None:
        branch = config.TRIVIA_BRANCH
    
    if not config.GITHUB_TOKEN:
        error_msg = "❌ GITHUB_TOKEN not set in config"
        print(error_msg)
        return False, error_msg
    
    # Normalize folder path
    if folder.startswith("images/"):
        folder = folder.replace("images/", "")
    
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/images/{folder}/{filename}"
    headers = {
        "Authorization": f"token {config.GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json"
    }

    # Check if file exists and get its SHA
    sha = None
    file_exists = False
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            file_exists = True
            sha = resp.json().get("sha")
        elif resp.status_code != 404:
            # Any other error is a problem
            return False, f"Failed to check file: {resp.status_code} - {resp.text}"
    except Exception as e:
        return False, f"Error checking file: {e}"

    # Prepare upload payload
    content = base64.b64encode(image_data).decode('utf-8')
    payload = {
        "message": f"Add/update {filename}",
        "content": content,
        "branch": branch,
    }
    if file_exists and sha:
        payload["sha"] = sha
    elif file_exists and not sha:
        return False, "File exists but SHA could not be retrieved."

    # Upload
    try:
        resp = requests.put(url, headers=headers, json=payload)
        if resp.status_code in (200, 201):
            raw_url = f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/{branch}/images/{folder}/{filename}"
            return True, raw_url
        else:
            return False, f"Upload failed: {resp.status_code} - {resp.text}"
    except Exception as e:
        return False, f"Upload error: {e}"
