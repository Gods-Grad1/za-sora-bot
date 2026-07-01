import base64
import requests
import config

def upload_image_to_github(bot, image_data, filename, folder, branch="main"):
    """
    Uploads an image to the GitHub repo.
    - branch: which branch to commit to (default 'main')
    """
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/images/{folder}/{filename}"
    headers = {
        "Authorization": f"token {config.GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }

    # 1. Check if file already exists (to get SHA for overwriting)
    response = requests.get(url, headers=headers)
    sha = None
    if response.status_code == 200:
        sha = response.json().get("sha")

    # 2. Prepare payload
    content = base64.b64encode(image_data).decode()
    payload = {
        "message": f"Add image {filename}",
        "content": content,
        "branch": branch,  # <-- NEW: allows pushing to branches other than main
    }
    if sha:
        payload["sha"] = sha

    # 3. Upload
    response = requests.put(url, headers=headers, json=payload)

    if response.status_code in (200, 201):
        return True
    else:
        print(f"GitHub upload failed: {response.text}")
        return False