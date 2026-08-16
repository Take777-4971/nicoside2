import json
import os

from paths import app_data_dir

BASE_DIR = app_data_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_youtube_api_key():
    """
    優先順位:
    1. 環境変数 YOUTUBE_API_KEY
    2. config.json の "youtube_api_key"
    どちらもなければ None（未設定として扱う）
    """
    env_key = os.environ.get("YOUTUBE_API_KEY")
    if env_key:
        return env_key

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            key = data.get("youtube_api_key")
            return key or None
        except Exception:
            return None

    return None


def load_youtube_channel_id():
    """
    再生リスト一覧の取得対象にするYouTubeチャンネルID。
    優先順位:
    1. 環境変数 YOUTUBE_CHANNEL_ID
    2. config.json の "youtube_channel_id"
    どちらもなければ None（再生リスト機能は無効として扱う）
    """
    env_id = os.environ.get("YOUTUBE_CHANNEL_ID")
    if env_id:
        return env_id

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            channel_id = data.get("youtube_channel_id")
            return channel_id or None
        except Exception:
            return None

    return None
