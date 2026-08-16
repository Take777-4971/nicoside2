"""
ニコニコ動画の非公式ログイン（user_session Cookie方式）。

YouTubeのOAuthと違い、ニコニコにはサードパーティアプリ向けの公式な
ログイン手段が提供されていない。そのため、別のpywebviewウィンドウで
通常のニコニコログイン画面を表示してもらい、ログイン完了後に
そのウィンドウのCookie（user_session）を取り出して使う、という
非公式な方式を取る。

重要な設計判断: user_session Cookieは実質的にパスワードと同等の
強さを持つ、アカウントへのフルアクセス権を持つ値である。
YouTubeのtoken.jsonのようにディスクへ保存すると、アプリを
使っていない時間帯にファイルごと漏洩するリスクを抱えることになる。
そのため、デフォルトでは意図的に **プロセスのメモリ上にのみ** 保持し、
一切ファイルに書き込まない。アプリを終了すればセッションは失われ、
次回起動時は必ず再ログインが必要になる。

============================================================
【開発用フラグ】PERSIST_SESSION_FOR_DEV
============================================================
下の PERSIST_SESSION_FOR_DEV を True にすると、ログインセッションを
ディスク（nico_session_dev.json）に保存し、次回起動時も自動的に
再利用するようになる（＝毎回ログインし直さなくて済む）。

開発中の動作確認を楽にするためだけの一時的なフラグであり、
**一般公開版をビルドする前に必ず False に戻すこと。** True のままだと、
アカウントへのフルアクセス権を持つCookieがファイルとしてディスクに
残り続けることになり、当初の設計方針（メモリ上のみ）が崩れる。
"""
import json
import os

from paths import app_data_dir

PERSIST_SESSION_FOR_DEV = True

_SESSION_FILE_PATH = os.path.join(app_data_dir(), "nico_session_dev.json")

_user_session = None
_user_id = None


def is_logged_in() -> bool:
    return bool(_user_session)


def set_session(user_session: str, user_id: str = None):
    global _user_session, _user_id
    _user_session = user_session
    _user_id = user_id
    if PERSIST_SESSION_FOR_DEV:
        _save_to_disk()


def clear_session():
    global _user_session, _user_id
    _user_session = None
    _user_id = None
    # フラグの現在値に関わらず、ログアウト時は残っていれば必ず消す
    # （フラグをOFFに戻した後の掃除漏れを防ぐため）
    _delete_from_disk()


def get_user_id():
    return _user_id


def set_user_id(user_id: str):
    global _user_id
    _user_id = user_id
    if PERSIST_SESSION_FOR_DEV:
        _save_to_disk()


def get_cookie_header() -> "dict | None":
    """requests用の Cookie ヘッダーを返す。未ログインならNone。"""
    if not _user_session:
        return None
    return {"Cookie": f"user_session={_user_session}"}


def load_persisted_session_if_enabled():
    """
    アプリ起動時に一度呼び出す。PERSIST_SESSION_FOR_DEV が True のときのみ、
    ディスクに保存済みのセッションがあれば読み込んでメモリに復元する。
    """
    global _user_session, _user_id
    if not PERSIST_SESSION_FOR_DEV:
        return
    if not os.path.exists(_SESSION_FILE_PATH):
        return
    try:
        with open(_SESSION_FILE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        _user_session = saved.get("user_session")
        _user_id = saved.get("user_id")
    except Exception:
        # 壊れている・読めない場合は無視して未ログイン状態のまま起動する
        pass


def _save_to_disk():
    try:
        with open(_SESSION_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump({"user_session": _user_session, "user_id": _user_id}, f)
    except Exception:
        pass


def _delete_from_disk():
    try:
        if os.path.exists(_SESSION_FILE_PATH):
            os.remove(_SESSION_FILE_PATH)
    except Exception:
        pass
