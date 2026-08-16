"""
JavaScript <-> Python ブリッジ。
pywebview の js_api として登録し、UI(JS)側から
window.pywebview.api.<メソッド名>(...) の形で呼び出す。
"""
import ctypes
import logging
import os
import sys
import threading
import time
import webbrowser

import webview

import auth
import config
import nico_auth
from paths import app_data_dir
from providers.niconico import NiconicoProvider
from providers.youtube import YouTubeProvider

NICO_LOGIN_URL = "https://account.nicovideo.jp/login?site=niconico"
NICO_LOGIN_DOMAINS = ("account.nicovideo.jp", "secure.nicovideo.jp")

DEFAULT_PAGE_SIZE = 5
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 20
MAX_RESULTS = 100  # 検索結果として保持・閲覧できる最大件数

# ウィンドウサイズのプリセット（幅の倍率）。高さは常にディスプレイの高さいっぱいにする。
BASE_WIDTH = 340
SIZE_MULTIPLIERS = [1, 2, 3]

# ウィンドウをドラッグした際、画面の左右端からこの距離(px)以内に
# 入ったら端にスナップさせる
SNAP_THRESHOLD = 40

# 自動的に隠すモード用の設定
COLLAPSED_WIDTH = 10  # 畳んだときの幅(px)
ANIMATION_STEPS = 14
ANIMATION_STEP_DELAY = 0.016  # 秒（1ステップあたり）

# 「自動的に隠す」まわりの調査用デバッグログ。
# app_data_dir()（exe化した場合はexeと同じフォルダ）に
# nicoside_debug.log として書き出す。
_debug_logger = logging.getLogger("nicoside.autohide")
_debug_logger.setLevel(logging.DEBUG)
if not _debug_logger.handlers:
    try:
        _log_path = os.path.join(app_data_dir(), "nicoside_debug.log")
        _handler = logging.FileHandler(_log_path, encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s [%(threadName)s] %(message)s"))
        _debug_logger.addHandler(_handler)
    except Exception:
        # ログ出力自体が失敗しても本体の動作には影響させない
        pass


def _extract_cookie_value(cookies, name: str, debug_log_names: bool = False):
    """
    pywebview の window.get_cookies() は、内部で使っているバックエンド
    （Windows/macOS/Linux、各OSのWebViewエンジン）によって戻り値の型が
    異なる。実機での確認で判明した形も含め、複数の形を想定して緩く対応する。

    確認できている形:
    - dict風オブジェクト（http.cookies.SimpleCookie等）が1つだけ渡ってくる場合
    - リストで、各要素が「1つ分のdict風オブジェクト（SimpleCookie）」
      になっている場合（Windows実機で確認。各要素は .key を持たず、
      要素自体が {クッキー名: Morsel} のミニ辞書になっている）
    - リストで、各要素が Morsel や {"name":..., "value":...} 形式の
      dict になっている場合
    """
    if not cookies:
        return None
    found_names = []
    try:
        if hasattr(cookies, "get") and not isinstance(cookies, (list, tuple)):
            morsel = cookies.get(name)
            if morsel is not None:
                return getattr(morsel, "value", morsel)
            if hasattr(cookies, "keys"):
                found_names.extend(list(cookies.keys()))

        for item in cookies:
            # 記述子形式: {"name":..., "value":...} や {"key":..., "value":...}
            # （SimpleCookie風の「キー＝クッキー名」なdictと区別するため、
            #  name/key と value を両方持つ記述子形式を先にチェックする）
            if isinstance(item, dict) and "value" in item and ("name" in item or "key" in item):
                item_name = item.get("name") or item.get("key")
                if item_name:
                    found_names.append(item_name)
                if item_name == name:
                    value = item.get("value")
                    if value:
                        return value
                continue
            # 要素自体がdict風（SimpleCookie 1個分、キー＝クッキー名）の場合
            if hasattr(item, "get") and hasattr(item, "keys"):
                item_keys = list(item.keys())
                found_names.extend(item_keys)
                if name in item_keys:
                    morsel = item.get(name)
                    value = getattr(morsel, "value", morsel)
                    if value:
                        return value
                continue
            # 要素自体がMorselの場合
            item_name = getattr(item, "key", None)
            if item_name:
                found_names.append(item_name)
            if item_name == name:
                value = getattr(item, "value", None)
                if value:
                    return value
    except Exception as exc:
        if debug_log_names:
            _debug_logger.debug("_extract_cookie_value: raised %r", exc)
        return None
    if debug_log_names:
        _debug_logger.debug("_extract_cookie_value: cookie names found=%s", found_names)
    return None


def _get_work_area_for_screen(screen):
    """
    Windows専用: 指定した screen（pywebviewのScreenオブジェクト、
    モニタ全体の座標・サイズ）に対応するモニタの「作業領域」
    （タスクバー等を除いた、実際にウィンドウを最大化したときに使われる
    範囲）を返す。

    pywebviewのScreenオブジェクトはモニタ全体の解像度しか持っておらず、
    タスクバーの位置・高さの情報が無いため、Win32 API
    （EnumDisplayMonitors + GetMonitorInfoW）を直接呼び出して取得する。

    戻り値: {"x":int, "y":int, "width":int, "height":int} または、
    Windows以外・取得失敗時は None（呼び出し側は screen そのものへ
    フォールバックする）。
    """
    if sys.platform != "win32" or screen is None:
        return None
    try:
        import ctypes.wintypes as wintypes

        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_double
        )

        monitors = []

        def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            monitors.append(hMonitor)
            return 1

        user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_callback), 0)

        for hmonitor in monitors:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                continue
            mx, my = info.rcMonitor.left, info.rcMonitor.top
            mw = info.rcMonitor.right - info.rcMonitor.left
            mh = info.rcMonitor.bottom - info.rcMonitor.top
            # pywebviewのscreenと同じモニタかどうかを座標・サイズで照合する
            if mx == int(screen.x) and my == int(screen.y) and mw == int(screen.width) and mh == int(screen.height):
                return {
                    "x": info.rcWork.left,
                    "y": info.rcWork.top,
                    "width": info.rcWork.right - info.rcWork.left,
                    "height": info.rcWork.bottom - info.rcWork.top,
                }
    except Exception as exc:
        _debug_logger.debug("_get_work_area_for_screen: raised %r", exc)
    return None


class Api:
    def __init__(self):
        self.providers = {
            # 最初に表示されるソース（先頭のものが初期選択される）
            "YouTube": YouTubeProvider(),
            "ニコニコ動画": NiconicoProvider(),
        }
        # 現在の検索/再生リストセッション状態
        # （1ウィンドウ・1セッションのみを想定した簡易実装）
        self._session = None
        self._size_index = 0
        self._pin_mode = "off"  # "off" | "on" | "auto"
        self._is_collapsed = False
        self._is_animating = False
        self._expanded_width = None
        self._collapsed_anchor_left = True
        self._collapsed_anchor_x = 0
        self._collapsed_anchor_right = 0
        self._collapsed_anchor_y = 0

        # 開発用: PERSIST_SESSION_FOR_DEV が有効な場合のみ、前回保存された
        # ニコニコ動画のログインセッションを読み込む（詳細はnico_auth.py参照）
        nico_auth.load_persisted_session_if_enabled()

    # ------------------------------------------------------------------
    # 検索・再生リスト・ページング
    # ------------------------------------------------------------------
    def get_provider_names(self):
        return list(self.providers.keys())

    def get_sort_options(self, provider_name: str):
        """ソースごとに選べる並び替え方法の一覧を返す。
        戻り値: [{"value":..., "label":...}, ...]（対応していないソースは空リスト）"""
        provider = self.providers.get(provider_name)
        options = getattr(provider, "SORT_OPTIONS", None) if provider else None
        if not options:
            return []
        return [{"value": value, "label": label} for value, label in options]

    @staticmethod
    def _normalize_page_size(page_size):
        if not page_size:
            return DEFAULT_PAGE_SIZE
        try:
            page_size = int(page_size)
        except (TypeError, ValueError):
            return DEFAULT_PAGE_SIZE
        return max(MIN_PAGE_SIZE, min(MAX_PAGE_SIZE, page_size))

    def search(self, provider_name: str, query: str, page_size=None, sort=None):
        """新規キーワード検索を開始し、1ページ目を返す"""
        provider = self.providers.get(provider_name)
        if not provider or not query:
            return self._empty_result("検索元またはキーワードが未指定です")

        self._start_session(
            provider_name=provider_name,
            page_size=page_size,
            query=query,
            fetch=lambda limit, offset, page_token: provider.search_page(
                query, limit=limit, offset=offset, page_token=page_token, sort=sort
            ),
        )
        return self._fetch_current_page()

    # ------------------------------------------------------------------
    # YouTube再生リスト（既存の再生リストを選択して表示。編集は非対応）
    # ------------------------------------------------------------------
    def get_youtube_playlists(self):
        provider = self.providers.get("YouTube")
        if not provider or not hasattr(provider, "get_playlists"):
            return {"playlists": [], "error": "このソースは再生リストに対応していません"}
        return provider.get_playlists()

    # ------------------------------------------------------------------
    # ニコニコ動画マイリスト（公開マイリストのURL/IDを手動登録する方式。
    # YouTubeのようなOAuthでの自動列挙には対応していない）
    # ------------------------------------------------------------------
    def get_niconico_mylists(self):
        provider = self.providers.get("ニコニコ動画")
        if not provider:
            return {"mylists": [], "error": None}
        return provider.get_registered_mylists()

    def register_niconico_mylist(self, url_or_id: str):
        provider = self.providers.get("ニコニコ動画")
        if not provider:
            return {"mylists": [], "error": "ニコニコ動画プロバイダが利用できません"}
        return provider.register_mylist(url_or_id)

    def remove_niconico_mylist(self, mylist_id: str):
        provider = self.providers.get("ニコニコ動画")
        if not provider:
            return {"mylists": [], "error": None}
        return provider.remove_mylist(mylist_id)

    def open_playlist(self, provider_name: str, playlist_id: str, page_size=None):
        """既存の再生リストを開き、1ページ目を返す（検索結果と同じ一覧UIで表示）"""
        provider = self.providers.get(provider_name)
        if not provider or not hasattr(provider, "playlist_page"):
            return self._empty_result("このソースは再生リストに対応していません")
        if not playlist_id:
            return self._empty_result("再生リストが指定されていません")

        self._start_session(
            provider_name=provider_name,
            page_size=page_size,
            query=None,
            fetch=lambda limit, offset, page_token: provider.playlist_page(
                playlist_id, limit=limit, offset=offset, page_token=page_token
            ),
        )
        return self._fetch_current_page()

    def _start_session(self, provider_name, page_size, query, fetch):
        self._session = {
            "provider_name": provider_name,
            "query": query,
            "fetch": fetch,
            "page": 1,
            "offset": 0,
            "page_size": self._normalize_page_size(page_size),
            # token方式のAPI用: ページ番号(1始まり) -> そのページを取得するためのトークン
            "page_tokens": {1: None},
            "total_count": None,
            "next_page_token": None,
        }

    def _apply_page_size_if_changed(self, page_size):
        """
        ウィンドウサイズが変わって1ページあたりの表示件数が変化した場合、
        ページ送りの整合性を保つのが複雑になるため、単純に1ページ目から
        取得し直す。戻り値は変化があった場合のみ結果dict、無ければNone。
        """
        page_size = self._normalize_page_size(page_size)
        if page_size == self._session["page_size"]:
            return None
        self._session["page_size"] = page_size
        self._session["page"] = 1
        self._session["offset"] = 0
        self._session["page_tokens"] = {1: None}
        return self._fetch_current_page()

    def next_page(self, page_size=None):
        if not self._session:
            return self._empty_result("先に検索を実行してください")
        resized = self._apply_page_size_if_changed(page_size)
        if resized is not None:
            return resized
        provider = self.providers[self._session["provider_name"]]
        page_size_val = self._session["page_size"]
        if provider.pagination_mode == "offset":
            self._session["offset"] += page_size_val
            self._session["page"] += 1
        else:
            next_token = self._session.get("next_page_token")
            if not next_token:
                # これ以上次のページがない場合は何もしない
                return self._fetch_current_page()
            self._session["page"] += 1
            self._session["page_tokens"][self._session["page"]] = next_token
        return self._fetch_current_page()

    def prev_page(self, page_size=None):
        if not self._session:
            return self._empty_result("先に検索を実行してください")
        resized = self._apply_page_size_if_changed(page_size)
        if resized is not None:
            return resized
        if self._session["page"] <= 1:
            return self._fetch_current_page()
        provider = self.providers[self._session["provider_name"]]
        self._session["page"] -= 1
        if provider.pagination_mode == "offset":
            self._session["offset"] = max(0, self._session["offset"] - self._session["page_size"])
        return self._fetch_current_page()

    def first_page(self, page_size=None):
        if not self._session:
            return self._empty_result("先に検索を実行してください")
        resized = self._apply_page_size_if_changed(page_size)
        if resized is not None:
            return resized
        self._session["page"] = 1
        self._session["offset"] = 0
        return self._fetch_current_page()

    def last_page(self, page_size=None):
        if not self._session:
            return self._empty_result("先に検索を実行してください")
        resized = self._apply_page_size_if_changed(page_size)
        if resized is not None:
            return resized
        provider = self.providers[self._session["provider_name"]]
        total = self._session.get("total_count")
        page_size_val = self._session["page_size"]
        if provider.pagination_mode != "offset" or total is None:
            return {
                **self._fetch_current_page(),
                "error": "この検索元では末尾ページへのジャンプに対応していません",
            }
        effective_total = min(total, MAX_RESULTS)
        last_offset = max(0, ((max(effective_total, 1) - 1) // page_size_val) * page_size_val)
        self._session["offset"] = last_offset
        self._session["page"] = last_offset // page_size_val + 1
        return self._fetch_current_page()

    def _fetch_current_page(self):
        s = self._session
        provider = self.providers[s["provider_name"]]
        page_size_val = s["page_size"]
        page_token = s["page_tokens"].get(s["page"])
        page = s["fetch"](page_size_val, s["offset"], page_token)
        s["total_count"] = page.total_count
        s["next_page_token"] = page.next_page_token

        if provider.pagination_mode == "offset":
            effective_total = (
                min(page.total_count, MAX_RESULTS) if page.total_count is not None else MAX_RESULTS
            )
            has_next = (s["offset"] + page_size_val) < effective_total
            supports_last = page.total_count is not None
        else:
            reached_cap = (s["page"] * page_size_val) >= MAX_RESULTS
            has_next = bool(page.next_page_token) and not reached_cap
            supports_last = False

        return {
            "items": [self._item_to_dict(item) for item in page.items],
            "page": s["page"],
            "page_size": page_size_val,
            "total_count": page.total_count,
            "max_results": MAX_RESULTS,
            "has_prev": s["page"] > 1,
            "has_next": has_next,
            "supports_last": supports_last,
            "provider_name": s["provider_name"],
            "query": s["query"],
        }

    @staticmethod
    def _item_to_dict(item):
        return {
            "id": item.id,
            "title": item.title,
            "thumbnail_url": item.thumbnail_url,
            "video_url": item.video_url,
            "source_name": item.source_name,
        }

    @staticmethod
    def _empty_result(message: str):
        return {
            "items": [],
            "page": 1,
            "page_size": DEFAULT_PAGE_SIZE,
            "total_count": 0,
            "has_prev": False,
            "has_next": False,
            "supports_last": False,
            "error": message,
        }

    # ------------------------------------------------------------------
    # 再生・外部リンク
    # ------------------------------------------------------------------
    def get_embed_url(self, provider_name: str, video_id: str, loop: bool = False):
        provider = self.providers.get(provider_name)
        if not provider:
            return ""
        return provider.get_embed_url(video_id, loop=loop)

    def open_external(self, url: str):
        """ログインなど、アプリ内WebViewではなく通常のブラウザで開きたい処理用"""
        webbrowser.open(url)
        return True

    def minimize_window(self):
        """frameless化に伴い無くなったOSネイティブの最小化ボタンの代替"""
        try:
            webview.windows[0].minimize()
        except Exception as exc:
            _debug_logger.debug("minimize_window: raised %r", exc)
        return True

    def close_window(self):
        """frameless化に伴い無くなったOSネイティブの閉じるボタンの代替"""
        try:
            self.save_window_state_on_close()
        except Exception as exc:
            _debug_logger.debug("close_window: save_window_state_on_close raised %r", exc)
        try:
            webview.windows[0].destroy()
        except Exception as exc:
            _debug_logger.debug("close_window: raised %r", exc)
        return True

    def debug_log(self, message: str):
        """
        JS側から nicoside_debug.log に書き込むための橋渡し。
        devtoolsを開かなくても、埋め込みプレーヤーからのイベント内容等を
        ログファイルに残せるようにするための調査用途。
        """
        _debug_logger.debug("[JS] %s", message)
        return True

    # ------------------------------------------------------------------
    # YouTube OAuthログイン
    # （APIキーをアプリに同梱せず、ユーザー自身のGoogleアカウントで
    #   ログインしてもらい、そのアクセストークンでAPIを呼び出す）
    # ------------------------------------------------------------------
    def is_youtube_logged_in(self):
        return auth.load_credentials() is not None

    def login_youtube_oauth(self):
        """
        既定のブラウザでGoogleのログイン画面を開き、ログイン完了まで待つ。
        （embed WebView内でのログインはGoogle側の仕様でブロックされる
        ため、必ずシステムの既定ブラウザで行う）
        """
        try:
            auth.run_oauth_flow()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def logout_youtube(self):
        auth.logout()
        return {"success": True}

    # ------------------------------------------------------------------
    # ニコニコ動画ログイン（非公式・user_session Cookie方式）
    #
    # ニコニコにはYouTubeのOAuthに相当する公式なサードパーティ向け
    # ログイン手段が無い。別のpywebviewウィンドウで通常のログイン画面を
    # 表示してもらい、ログイン完了を検知したらそのウィンドウの
    # Cookie（user_session）を取り出して使う。
    #
    # user_session はアカウントへのフルアクセス権を持つ強い値のため、
    # 意図的に nico_auth モジュール（プロセスのメモリ上のみ）に保持し、
    # ディスクには一切保存しない。アプリを終了すれば消え、次回起動時は
    # 必ず再ログインが必要になる。
    # ------------------------------------------------------------------
    def is_niconico_logged_in(self):
        return nico_auth.is_logged_in()

    def logout_niconico(self):
        nico_auth.clear_session()
        return {"success": True}

    def open_niconico_login(self):
        """
        別ウィンドウでニコニコの通常ログイン画面を開き、ログイン完了
        （ログインページから離脱＝ドメインが変わる）を検知したら
        Cookieを取り出してメモリ上に保持し、ウィンドウを閉じる。
        ユーザーがウィンドウを閉じた場合は失敗として扱う。
        """
        state = {"done": False, "success": False, "error": None}
        login_window = webview.create_window(
            "ニコニコ動画にログイン",
            NICO_LOGIN_URL,
            width=440,
            height=640,
        )
        _debug_logger.debug("open_niconico_login: window created url=%s", NICO_LOGIN_URL)

        def _on_loaded():
            if state["done"]:
                return
            try:
                current_url = login_window.get_current_url() or ""
            except Exception as exc:
                _debug_logger.debug("open_niconico_login: get_current_url raised %r", exc)
                return
            is_login_domain = any(domain in current_url for domain in NICO_LOGIN_DOMAINS)
            _debug_logger.debug(
                "open_niconico_login: loaded url=%s is_login_domain=%s",
                current_url, is_login_domain,
            )
            # ログイン用ドメインに留まっている間は、まだログイン完了して
            # いない（フォーム再表示やエラー・2段階認証待ちなども含む）
            if is_login_domain:
                return
            try:
                cookies = login_window.get_cookies()
            except Exception as exc:
                _debug_logger.debug("open_niconico_login: get_cookies raised %r", exc)
                state["error"] = f"Cookieの取得に失敗しました: {exc}"
                state["done"] = True
                return
            # 値そのものはメモリ以外に残したくないため、種類とキー名だけ記録する
            try:
                cookie_names = (
                    list(cookies.keys()) if hasattr(cookies, "keys")
                    else [getattr(c, "key", c.get("name") if isinstance(c, dict) else "?") for c in cookies]
                )
            except Exception:
                cookie_names = "<読み取り不可>"
            _debug_logger.debug(
                "open_niconico_login: get_cookies type=%s names=%s",
                type(cookies).__name__, cookie_names,
            )
            session_value = _extract_cookie_value(cookies, "user_session", debug_log_names=True)
            _debug_logger.debug(
                "open_niconico_login: user_session found=%s", bool(session_value)
            )
            if not session_value:
                state["error"] = "ログインセッションを取得できませんでした"
                state["done"] = True
                return
            nico_auth.set_session(session_value)
            state["success"] = True
            state["done"] = True
            # private_mode=False（永続プロファイル）にしたことで、WebView2が
            # このウィンドウのCookieを自動的にディスクへ書き込んでいる。
            # 値はすでにメモリ上（nico_auth）に取り出せたので、ディスク側の
            # 残留を極力減らすため、このウィンドウのCookieを消しておく。
            # ただし、これはWebView2の内部プロファイルファイルまで完全に
            # 消し去る保証があるわけではない（ベストエフォート）。
            try:
                login_window.clear_cookies()
                _debug_logger.debug("open_niconico_login: clear_cookies done")
            except Exception as exc:
                _debug_logger.debug("open_niconico_login: clear_cookies raised %r", exc)

        closed_event = threading.Event()
        login_window.events.loaded += _on_loaded
        login_window.events.closed += lambda: (closed_event.set(), _debug_logger.debug("open_niconico_login: window closed by user"))

        # ログイン完了 or ウィンドウが閉じられるまで待つ
        # （js_apiの呼び出しはpywebview側で別スレッド実行されるため、
        # ここでブロッキングしてもUIは固まらない）
        while not state["done"] and not closed_event.is_set():
            time.sleep(0.2)

        if not state["done"]:
            state["error"] = "ログイン画面が閉じられました"

        _debug_logger.debug(
            "open_niconico_login: finished success=%s error=%s",
            state["success"], state["error"],
        )
        try:
            login_window.destroy()
        except Exception:
            pass

        return {"success": state["success"], "error": state["error"]}

    def get_niconico_own_mylists(self):
        """ログイン中のアカウント自身のマイリスト一覧を取得する（非公式・未検証のAPIを使用）"""
        provider = self.providers.get("ニコニコ動画")
        if not provider or not hasattr(provider, "get_own_mylists"):
            return {"mylists": [], "error": None}
        if not nico_auth.is_logged_in():
            return {"mylists": [], "error": "ニコニコ動画にログインしていません"}
        return provider.get_own_mylists()

    # ------------------------------------------------------------------
    # ウィンドウ操作（最前面表示・自動的に隠す・サイズ切り替え）
    # ------------------------------------------------------------------
    def set_pin_mode(self, mode):
        """
        mode: "off" | "on" | "auto"
        - off : 常に最前面ではない・自動で隠れない
        - on  : 常に最前面（従来のピン留めON相当）
        - auto: 常に最前面 かつ 操作がないと画面端に自動的に隠れる
        """
        if mode not in ("off", "on", "auto"):
            mode = "off"
        self._pin_mode = mode
        window = webview.windows[0]
        should_be_on_top = mode in ("on", "auto")

        done = threading.Event()

        def _apply():
            try:
                window.on_top = should_be_on_top
            finally:
                done.set()

        threading.Thread(target=_apply, daemon=True).start()
        done.wait(timeout=2)

        if mode != "auto" and self._is_collapsed:
            self.expand_window()

        return {"mode": self._pin_mode}

    def get_pin_mode(self):
        return self._pin_mode

    def is_window_collapsed(self):
        return self._is_collapsed

    def collapse_window(self):
        """
        自動的に隠すモード用: ウィンドウを画面端に「にゅるっと」畳んで
        細いバー状にする。

        アンカー先の座標は、ウィンドウ自身が直前に報告した位置ではなく
        「ディスプレイの絶対座標（screen.x / screen.x + screen.width）」
        を基準にする。ウィンドウの自己申告位置を基準にすると、実機での
        わずかな誤差が畳む/戻すを繰り返すたびに蓄積し、サイクルを重ねる
        ごとにウィンドウの位置がどんどんズレていく不具合があったため。
        """
        if self._pin_mode != "auto":
            return {"collapsed": False}
        if self._is_collapsed or self._is_animating:
            _debug_logger.debug(
                "collapse_window: skip (already collapsed=%s / animating=%s)",
                self._is_collapsed, self._is_animating,
            )
            return {"collapsed": self._is_collapsed}

        window = webview.windows[0]
        screen = self._find_current_screen(window)
        anchor_left = True
        anchor_x = window.x
        anchor_right = window.x + window.width
        anchor_y = window.y
        if screen is not None:
            window_center_x = window.x + window.width / 2
            screen_center_x = screen.x + screen.width / 2
            anchor_left = window_center_x <= screen_center_x
            anchor_x = screen.x
            anchor_right = screen.x + screen.width
            # タスクバー等を除いた作業領域が取得できれば、そちらのy座標を
            # 使う（select_window_size / handle_window_moved と一貫させる）
            work_area = _get_work_area_for_screen(screen)
            anchor_y = work_area["y"] if work_area else screen.y

        self._collapsed_anchor_left = anchor_left
        self._collapsed_anchor_x = anchor_x
        self._collapsed_anchor_right = anchor_right
        self._collapsed_anchor_y = anchor_y
        self._expanded_width = window.width
        _debug_logger.debug(
            "collapse_window: start window=(x=%s,y=%s,w=%s,h=%s) screen=%s "
            "anchor_left=%s anchor_x=%s anchor_right=%s anchor_y=%s",
            window.x, window.y, window.width, window.height,
            None if screen is None else (screen.x, screen.y, screen.width, screen.height),
            anchor_left, anchor_x, anchor_right, anchor_y,
        )
        self._animate_resize(
            start_width=window.width,
            target_width=COLLAPSED_WIDTH,
            anchor_left=anchor_left,
            anchor_x=anchor_x,
            anchor_right=anchor_right,
            anchor_y=anchor_y,
            on_done=lambda: (
                setattr(self, "_is_collapsed", True),
                _debug_logger.debug(
                    "collapse_window: done actual=(x=%s,y=%s,w=%s,h=%s)",
                    webview.windows[0].x, webview.windows[0].y,
                    webview.windows[0].width, webview.windows[0].height,
                ),
            ),
        )
        return {"collapsed": True}

    def expand_window(self):
        """
        自動的に隠すモード用: 畳んだウィンドウを元のサイズに戻す。
        collapse_window() 側で記録した「ディスプレイの絶対座標」を
        そのまま再利用することで、畳む前とまったく同じ位置に戻す。
        """
        if not self._is_collapsed or self._is_animating:
            _debug_logger.debug(
                "expand_window: skip (collapsed=%s / animating=%s)",
                self._is_collapsed, self._is_animating,
            )
            return {"collapsed": self._is_collapsed}

        target_width = self._expanded_width or BASE_WIDTH
        anchor_left = self._collapsed_anchor_left
        anchor_x = self._collapsed_anchor_x
        anchor_right = self._collapsed_anchor_right
        anchor_y = self._collapsed_anchor_y
        window = webview.windows[0]
        _debug_logger.debug(
            "expand_window: start window=(x=%s,y=%s,w=%s,h=%s) target_width=%s "
            "anchor_left=%s anchor_x=%s anchor_right=%s anchor_y=%s",
            window.x, window.y, window.width, window.height, target_width,
            anchor_left, anchor_x, anchor_right, anchor_y,
        )
        self._animate_resize(
            start_width=window.width,
            target_width=target_width,
            anchor_left=anchor_left,
            anchor_x=anchor_x,
            anchor_right=anchor_right,
            anchor_y=anchor_y,
            on_done=lambda: (
                setattr(self, "_is_collapsed", False),
                _debug_logger.debug(
                    "expand_window: done actual=(x=%s,y=%s,w=%s,h=%s)",
                    webview.windows[0].x, webview.windows[0].y,
                    webview.windows[0].width, webview.windows[0].height,
                ),
            ),
        )
        return {"collapsed": False}

    def _animate_resize(self, start_width, target_width, anchor_left, anchor_x, anchor_right, anchor_y, on_done):
        """
        window.resize()/move() を細かいステップで繰り返し呼び、
        幅がなめらかに変化する「にゅるっと」感のあるアニメーションを作る。
        ステップごとの実際の移動量はイーズアウトで減衰させる。

        x・y座標は毎ステップ、固定された anchor_x / anchor_right / anchor_y
        （ディスプレイの絶対座標）から計算し直す。ウィンドウの自己申告
        座標を積み上げて使わないことで、誤差の蓄積を防いでいる。
        """
        window = webview.windows[0]
        height = window.height

        if self._is_animating:
            _debug_logger.debug(
                "_animate_resize: WARNING already animating, new animation requested "
                "(start_width=%s target_width=%s anchor_left=%s) — this should not happen",
                start_width, target_width, anchor_left,
            )

        self._is_animating = True

        def _run():
            try:
                for step in range(1, ANIMATION_STEPS + 1):
                    t = step / ANIMATION_STEPS
                    eased = 1 - (1 - t) ** 3  # ease-out cubic
                    w = int(round(start_width + (target_width - start_width) * eased))
                    w = max(w, COLLAPSED_WIDTH)
                    try:
                        window.resize(w, height)
                        # resize()には狙った幅(w)を渡すが、OS側の制約
                        # （観測上、Windowsはネイティブタイトルバー付き
                        # ウィンドウを約136px未満には縮めてくれない）で、
                        # 実際に適用される幅がwより大きいまま頭打ちになる
                        # ことがある。
                        #
                        # 右端アンカーの場合は x = anchor_right - w という
                        # 計算式が、狙った幅と実際の幅の差分をそのまま
                        # 画面の外（右方向）に押し出す効果を持つため、
                        # 縮めきれない分は自然と画面外に隠れる。
                        # 左端アンカーではこれまで x を anchor_x に固定
                        # していたため、縮めきれない分がそのまま画面内
                        # （右方向）に見えてしまっていた。
                        # actual_w（実際に適用された幅）を読み直し、右端と
                        # 同じ理屈で、縮めきれない分を左方向（画面外）へ
                        # 押し出すようにする。
                        actual_w = window.width
                        if anchor_left:
                            x = anchor_x + (w - actual_w)
                        else:
                            x = anchor_right - w
                        window.move(int(x), int(anchor_y))
                    except Exception as exc:
                        _debug_logger.debug("_animate_resize: step %s raised %r, aborting", step, exc)
                        break
                    actual_w, actual_x, actual_y = window.width, window.x, window.y
                    _debug_logger.debug(
                        "_animate_resize: step=%02d/%d target=(w=%s,x=%s,y=%s) actual=(w=%s,x=%s,y=%s)",
                        step, ANIMATION_STEPS, w, x, anchor_y, actual_w, actual_x, actual_y,
                    )
                    time.sleep(ANIMATION_STEP_DELAY)
                else:
                    # ループが break されずに最後まで回った場合のみ、
                    # 目的の幅・位置にきっちり合わせ直す。
                    # 各ステップの計算値には丸め誤差が乗るため、最後の
                    # ステップの見た目上の値が target とわずかにズレて
                    # 止まってしまうことがある（特に左端など、ズレが
                    # 目立ちやすい位置で「畳みきれていない」ように見える
                    # 不具合の原因になり得るため、最後に一度、誤差なしの
                    # 確定値で resize/move をやり直して締める）。
                    final_w = max(target_width, COLLAPSED_WIDTH)
                    try:
                        window.resize(final_w, height)
                        actual_final_w = window.width
                        if anchor_left:
                            final_x = anchor_x + (final_w - actual_final_w)
                        else:
                            final_x = anchor_right - final_w
                        window.move(int(final_x), int(anchor_y))
                    except Exception as exc:
                        _debug_logger.debug("_animate_resize: final snap raised %r", exc)
                    _debug_logger.debug(
                        "_animate_resize: final snap target=(w=%s,x=%s,y=%s) actual=(w=%s,x=%s,y=%s)",
                        final_w, final_x, anchor_y, window.width, window.x, window.y,
                    )
            finally:
                self._is_animating = False
                if on_done:
                    on_done()

        threading.Thread(target=_run, daemon=True).start()

    def cycle_window_size(self):
        """後方互換用: 現在のインデックスから次のサイズへ切り替える"""
        self._size_index = (self._size_index + 1) % len(SIZE_MULTIPLIERS)
        return self.select_window_size(SIZE_MULTIPLIERS[self._size_index])

    def apply_initial_window_state(self):
        """
        アプリ起動直後に一度だけ呼ぶ。前回終了時に保存された位置・
        サイズがあればそれを復元し、無ければ従来通りの既定サイズ(x1)を
        画面端に適用する。

        保存された座標が、現在つないでいるディスプレイ構成では
        画面外にはみ出してしまう場合（保存後にモニタ構成を変えた等）は、
        安全のため既定サイズにフォールバックする。
        """
        saved = config.load_window_state()
        if not saved:
            self.select_window_size(1)
            return

        size_index = saved["size_index"]
        if size_index < 0 or size_index >= len(SIZE_MULTIPLIERS):
            size_index = 0
        multiplier = SIZE_MULTIPLIERS[size_index]
        width = BASE_WIDTH * multiplier
        x, y = saved["x"], saved["y"]

        # 保存された座標が、いま実際に繋がっているどれかのディスプレイの
        # 範囲内に収まっているかを確認する
        try:
            screens = webview.screens
        except Exception:
            screens = []
        fits = any(
            s.x <= x < s.x + s.width and s.y <= y < s.y + s.height
            for s in (screens or [])
        )
        if not fits:
            _debug_logger.debug("apply_initial_window_state: saved position out of bounds, using default")
            self.select_window_size(1)
            return

        window = webview.windows[0]
        # 高さは、保存後にモニタの解像度・タスクバー状況が変わっている
        # 可能性があるため、保存値をそのまま使わず、現在の作業領域から
        # 都度計算し直す（select_window_sizeと同じロジックを使うため、
        # 一度そちらを呼んでから、幅とx座標だけ保存値で上書きする）
        self.select_window_size(multiplier)
        try:
            window.move(int(x), int(y))
        except Exception as exc:
            _debug_logger.debug("apply_initial_window_state: move raised %r", exc)
        self._size_index = size_index
        _debug_logger.debug("apply_initial_window_state: restored x=%s y=%s size_index=%s", x, y, size_index)

    def save_window_state_on_close(self):
        """
        ウィンドウが閉じられる直前（events.closing）に呼ぶ。
        現在の位置・サイズ設定を保存する。

        「自動的に隠す」で畳んだ状態のまま終了した場合は、畳んだ後の
        細い幅ではなく、展開時の幅・位置を保存する（次回起動時に
        畳まれた状態のままにはしたくないため）。
        """
        try:
            window = webview.windows[0]
            x, y = window.x, window.y
            if self._is_collapsed:
                # 畳んだ状態のx座標は片側が画面外相当になっているため、
                # 展開時のアンカー位置を代わりに使う
                x = self._collapsed_anchor_x if self._collapsed_anchor_left else (
                    self._collapsed_anchor_right - (self._expanded_width or window.width)
                )
                y = self._collapsed_anchor_y
            config.save_window_state(x, y, self._size_index)
        except Exception as exc:
            _debug_logger.debug("save_window_state_on_close: raised %r", exc)

    def select_window_size(self, multiplier):
        """
        指定した倍率(1/2/3)へウィンドウサイズを直接切り替える。

        ガジェットとしてディスプレイの端に置いて収まるように、
        サイズに関わらず常に次の状態にする:
        - ウィンドウの高さ = ディスプレイの高さいっぱい
        - ウィンドウのy座標 = 0（ディスプレイ最上部）
        - ウィンドウの幅だけが倍率(1/2/3)に応じて変わる

        幅の拡大方向はウィンドウの現在位置で決める:
        - ディスプレイの左寄りにあれば、左端に揃えて右方向へ拡大
        - ディスプレイの右寄りにあれば、右端に揃えて左方向へ拡大
        """
        try:
            multiplier = int(multiplier)
        except (TypeError, ValueError):
            multiplier = 1
        if multiplier not in SIZE_MULTIPLIERS:
            multiplier = SIZE_MULTIPLIERS[0]
        self._size_index = SIZE_MULTIPLIERS.index(multiplier)

        window = webview.windows[0]
        desired_width = BASE_WIDTH * multiplier

        screen = self._find_current_screen(window)

        if screen is None:
            # 画面情報が取得できない場合は素直にリサイズするのみ
            new_width = desired_width
            new_height = window.height
            new_x, new_y = window.x, window.y
        else:
            # タスクバー等を除いた作業領域が取得できれば、ステータスバーが
            # 隠れないようそちらを基準にする（Windows以外や取得失敗時は
            # モニタ全体を使う、これまで通りの挙動にフォールバックする）
            work_area = _get_work_area_for_screen(screen)
            area_x = work_area["x"] if work_area else screen.x
            area_y = work_area["y"] if work_area else screen.y
            area_width = work_area["width"] if work_area else screen.width
            area_height = work_area["height"] if work_area else screen.height

            new_width = min(desired_width, area_width)
            new_height = area_height
            new_y = area_y

            window_center_x = window.x + window.width / 2
            screen_center_x = screen.x + screen.width / 2
            anchor_left = window_center_x <= screen_center_x

            if anchor_left:
                new_x = area_x
            else:
                new_x = area_x + area_width - new_width

        done = threading.Event()

        def _apply():
            try:
                window.resize(int(new_width), int(new_height))
                window.move(int(new_x), int(new_y))
            finally:
                done.set()

        threading.Thread(target=_apply, daemon=True).start()
        done.wait(timeout=2)
        return {
            "multiplier": multiplier,
            "width": int(new_width),
            "height": int(new_height),
        }

    @staticmethod
    def _find_current_screen(window):
        try:
            screens = webview.screens
        except Exception:
            return None
        if not screens:
            return None
        wx, wy = window.x, window.y
        for screen in screens:
            if (
                screen.x <= wx < screen.x + screen.width
                and screen.y <= wy < screen.y + screen.height
            ):
                return screen
        # どの画面にも一致しない場合は先頭の画面にフォールバック
        return screens[0]

    def handle_window_moved(self, x, y):
        """
        pywebviewの `events.moved` から呼ばれる。ウィンドウをドラッグして
        画面の端（左右・上下）に近づけたとき、キュッと端にくっつくように
        位置を補正する（ガジェットとして端に置きやすくするため）。

        横方向・縦方向は独立に判定する（例: 左端かつ上端に同時に
        近ければ両方スナップし、画面の角にぴったり収まる）。

        pywebviewはイベントハンドラを自動的に別スレッドで実行するため、
        ここでの window.move() 呼び出し自体に追加のスレッド対策は不要。

        ドラッグ中の一時的な状態でウィンドウのプロパティ取得が失敗する
        ことがあるため、例外は握りつぶして何もしない（次の moved イベント
        で再試行されるため実害はない）。

        「自動的に隠す」の畳む/戻すアニメーション中（_animate_resize内）
        も、毎ステップの window.move() 呼び出しのたびにこの moved イベント
        が発火してしまう。アニメーション側の移動とこのスナップ処理の
        window.move() が別スレッドから同じウィンドウへ同時に競合すると、
        アニメーションが途中の中途半端なサイズで止まって見える不具合の
        原因になり得るため、アニメーション中はスナップ処理自体を素通り
        させる。
        """
        if self._is_animating:
            _debug_logger.debug("handle_window_moved: skipped (animating) x=%s y=%s", x, y)
            return
        try:
            window = webview.windows[0]
            screen = self._find_current_screen(window)
            if screen is None:
                return

            width = window.width
            height = window.height

            # タスクバー等を除いた作業領域があればそちらを基準にスナップする
            work_area = _get_work_area_for_screen(screen)
            area_x = work_area["x"] if work_area else screen.x
            area_y = work_area["y"] if work_area else screen.y
            area_width = work_area["width"] if work_area else screen.width
            area_height = work_area["height"] if work_area else screen.height

            target_x = x
            left_dist = abs(x - area_x)
            right_dist = abs((x + width) - (area_x + area_width))
            if left_dist <= SNAP_THRESHOLD or right_dist <= SNAP_THRESHOLD:
                if left_dist <= right_dist:
                    target_x = area_x
                else:
                    target_x = area_x + area_width - width

            target_y = y
            top_dist = abs(y - area_y)
            bottom_dist = abs((y + height) - (area_y + area_height))
            if top_dist <= SNAP_THRESHOLD or bottom_dist <= SNAP_THRESHOLD:
                if top_dist <= bottom_dist:
                    target_y = area_y
                else:
                    target_y = area_y + area_height - height

            if target_x != x or target_y != y:
                window.move(int(target_x), int(target_y))
        except Exception:
            pass

