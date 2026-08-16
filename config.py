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


def save_window_state(x: int, y: int, size_index: int):
    """
    前回終了時のウィンドウ位置・サイズを保存する。
    x, y: ウィンドウ左上のディスプレイ絶対座標
    size_index: SIZE_MULTIPLIERS（api.py）のインデックス（0=x1, 1=x2, 2=x3）
    """
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["window_state"] = {"x": int(x), "y": int(y), "size_index": int(size_index)}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_window_state():
    """
    保存済みのウィンドウ位置・サイズを返す。
    戻り値: {"x":int, "y":int, "size_index":int} または、保存が無い/壊れて
    いる場合は None（呼び出し側はデフォルトのサイズ・位置を使う）。
    """
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        state = data.get("window_state")
        if not isinstance(state, dict):
            return None
        return {
            "x": int(state["x"]),
            "y": int(state["y"]),
            "size_index": int(state["size_index"]),
        }
    except Exception:
        return None
