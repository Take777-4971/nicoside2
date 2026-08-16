import json
import logging
import os
import re
from typing import Optional

import requests

import nico_auth
from paths import app_data_dir
from .base import BaseVideoProvider, SearchPage, VideoItem

_debug_logger = logging.getLogger("nicoside.autohide")

# ニコニコ動画 スナップショット検索 API v2
# https://site.nicovideo.jp/search-api-docs/snapshot
API_URL = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"

# マイリスト取得用API（非公式）。ニコニコ動画には、YouTubeのOAuthに相当する
# 「アプリが自分のアカウントのマイリスト一覧を自動列挙する」ための公式な
# 手段が提供されていない。一方、公開設定のマイリストであればログイン無しで
# 中身を取得できる非公式APIが存在する（Web版ニコニコ動画自体が内部で
# 使用しているものと同種のエンドポイント）。将来ニコニコ側の仕様変更で
# 動かなくなる可能性がある点に注意。
MYLIST_API_URL = "https://nvapi.nicovideo.jp/v2/mylists/{mylist_id}"
# ログイン中アカウント自身のマイリストの中身を取得するAPI（非公式）。
# こちらは`me`を指定するため、非公開マイリストの中身も取得できる
# （2026-08-15、ユーザー自身の実機での開発者ツール調査により特定）。
# 上のMYLIST_API_URLは公開マイリスト専用で、非公開だと403になる。
MYLIST_ME_API_URL = "https://nvapi.nicovideo.jp/v1/users/me/mylists/{mylist_id}"
MYLIST_HEADERS = {
    "User-Agent": "NicoSide/1.0 (personal desktop app)",
    # nvapi.nicovideo.jp系のAPIは、この2つのヘッダーが無いと
    # 400 (INVALID_PARAMETER) を返すことがある
    "X-Frontend-Id": "6",
    "X-Frontend-Version": "0",
}

# ログイン中アカウント自身のマイリスト一覧取得用（非公式）。
# 2026-08-15、ユーザー自身がブラウザの開発者ツール(Networkタブ)で
# https://www.nicovideo.jp/my/mylist ページの実際の通信を確認し、
# 特定できたエンドポイント。以前使っていた
# `/v1/users/{数値ユーザーID}/mylists`（投稿動画一覧が返るだけの別物
# だった）とは異なり、`me` という文字列を直接指定する形になっている。
# ログインが必要なため、user_session Cookie（nico_auth）を使用する。
USER_MYLISTS_API_URL = "https://nvapi.nicovideo.jp/v1/users/me/mylists"


class NiconicoProvider(BaseVideoProvider):
    pagination_mode = "offset"

    def __init__(self, mylists_path=None):
        # 登録済みマイリスト（{id, title}のリスト）を保存するファイル。
        # ニコニコにはYouTubeのOAuthに相当する「自分のマイリスト一覧を
        # 自動列挙する」公式手段が無いため、ユーザーがマイリストの
        # URL/IDを手動で登録する方式にしている。
        self._mylists_path = mylists_path or os.path.join(app_data_dir(), "nico_mylists.json")

    @property
    def name(self) -> str:
        return "ニコニコ動画"

    # 検索結果のソートに指定できる値（スナップショット検索API v2の _sort
    # パラメータに準拠。頭に "+"=昇順 / "-"=降順 を付けて指定する）
    VALID_SORTS = {
        "+viewCounter", "-viewCounter",
        "+commentCounter", "-commentCounter",
        "+mylistCounter", "-mylistCounter",
        "+likeCounter", "-likeCounter",
        "+startTime", "-startTime",
        "+lengthSeconds", "-lengthSeconds",
        "+lastCommentTime", "-lastCommentTime",
    }
    DEFAULT_SORT = "-viewCounter"
    # UIのドロップダウンに表示する選択肢（表示順）
    SORT_OPTIONS = [
        ("-viewCounter", "再生数が多い順"),
        ("+viewCounter", "再生数が少ない順"),
        ("-startTime", "投稿が新しい順"),
        ("+startTime", "投稿が古い順"),
        ("-commentCounter", "コメントが多い順"),
        ("-mylistCounter", "マイリスト数が多い順"),
        ("-likeCounter", "いいねが多い順"),
        ("-lengthSeconds", "動画時間が長い順"),
        ("+lengthSeconds", "動画時間が短い順"),
        ("-lastCommentTime", "最終コメントが新しい順"),
    ]

    def search_page(
        self,
        query: str,
        limit: int = 5,
        offset: int = 0,
        page_token: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> SearchPage:
        params = {
            "q": query,
            "targets": "title,description,tags",
            "fields": "contentId,title,thumbnailUrl,viewCounter",
            "_sort": sort if sort in self.VALID_SORTS else self.DEFAULT_SORT,
            "_offset": offset,
            "_limit": limit,
            "_context": "NicoSide",
        }
        headers = {"User-Agent": "NicoSide/1.0 (personal desktop app)"}
        try:
            resp = requests.get(API_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return SearchPage(
                items=[
                    VideoItem(
                        id="",
                        title=f"ニコニコ動画の検索に失敗しました: {e}",
                        thumbnail_url="",
                        video_url="",
                        source_name=self.name,
                    )
                ],
                total_count=0,
            )
        return self._parse_response(data)

    @staticmethod
    def _parse_response(data: dict) -> SearchPage:
        """
        API仕様書のレスポンス例:
        {
          "meta": {"status": 200, "totalCount": 12673, "id": "..."},
          "data": [{"contentId": "sm9", "title": "...", "thumbnailUrl": "...", "viewCounter": 1}]
        }
        """
        meta = data.get("meta", {}) or {}
        if meta.get("status") not in (200, None):
            message = meta.get("errorMessage", "不明なエラー")
            return SearchPage(
                items=[
                    VideoItem(
                        id="",
                        title=f"ニコニコ動画API エラー: {message}",
                        thumbnail_url="",
                        video_url="",
                        source_name="ニコニコ動画",
                    )
                ],
                total_count=0,
            )

        items = []
        for entry in data.get("data") or []:
            content_id = entry.get("contentId")
            if not content_id:
                continue
            items.append(
                VideoItem(
                    id=content_id,
                    title=entry.get("title") or "(タイトル不明)",
                    thumbnail_url=entry.get("thumbnailUrl") or "",
                    video_url=f"https://www.nicovideo.jp/watch/{content_id}",
                    source_name="ニコニコ動画",
                )
            )
        total_count = meta.get("totalCount")
        return SearchPage(items=items, total_count=total_count)

    def get_embed_url(self, video_id: str, loop: bool = False) -> str:
        # ニコニコ動画の埋め込みプレーヤーには、ループ再生用の公式URL
        # パラメータが提供されていない（loop引数は現状無視される）。
        #
        # 2026-08-14の調査: jsapi=1（postMessage経由でのコメント表示
        # 切り替え等を有効にするパラメータ）を付けると、パラメータの
        # 組み合わせを変えても一貫して unexpected_error が発生し、
        # 再生できないことを実機で複数回確認した。jsapi=1モード自体が
        # この環境（pywebview + WebView2）と噛み合っていないと判断し、
        # 再生の安定性を優先して素の埋め込みURLに戻す
        # （= コメント表示のON/OFF切り替え機能は現状実現できていない）。
        return f"https://embed.nicovideo.jp/watch/{video_id}"

    # ------------------------------------------------------------------
    # マイリスト
    # ------------------------------------------------------------------
    # ニコニコ動画には、YouTubeのOAuthに相当する「アプリが自分の
    # アカウントのマイリスト一覧を自動で列挙する」公式な手段が無い。
    # そのため、ユーザーがマイリストのURL（またはID）を1つずつ手動で
    # 登録する方式にしている。登録内容はローカルのJSONファイルに保存し、
    # 次回起動時も引き継ぐ。
    #
    # また、ここで使う nvapi.nicovideo.jp/v2/mylists/{id} は非公式API
    # であり、かつ「公開」設定のマイリストしか中身を取得できない
    # （非公開マイリストの中身取得にはログインCookieが必要で、この
    # アプリの認証方式ではサポートしていない）。

    MYLIST_URL_PATTERN = re.compile(r"(?:^|/)mylist/(\d+)")

    @classmethod
    def _parse_mylist_id(cls, url_or_id: str) -> Optional[str]:
        text = (url_or_id or "").strip()
        if not text:
            return None
        if text.isdigit():
            return text
        m = cls.MYLIST_URL_PATTERN.search(text)
        if m:
            return m.group(1)
        return None

    def _load_registered_mylists(self) -> list:
        if not os.path.exists(self._mylists_path):
            return []
        try:
            with open(self._mylists_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def _save_registered_mylists(self, mylists: list):
        try:
            with open(self._mylists_path, "w", encoding="utf-8") as f:
                json.dump(mylists, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_registered_mylists(self):
        """
        登録済みマイリスト一覧を返す（ネットワークアクセスはしない）。
        戻り値: {"mylists": [{"id":..., "title":...}], "error": None}
        """
        return {"mylists": self._load_registered_mylists(), "error": None}

    def register_mylist(self, url_or_id: str):
        """
        マイリストのURLまたはIDを登録する。実在確認・タイトル取得を
        兼ねて一度APIを呼び出し、成功したもののみ保存する。
        戻り値: {"mylists": [...], "error": Optional[str]}
        """
        mylist_id = self._parse_mylist_id(url_or_id)
        if not mylist_id:
            return {
                "mylists": self._load_registered_mylists(),
                "error": "マイリストのURLまたはIDを正しく読み取れませんでした",
            }

        mylists = self._load_registered_mylists()
        if any(m.get("id") == mylist_id for m in mylists):
            return {"mylists": mylists, "error": "そのマイリストは登録済みです"}

        info, error = self._fetch_mylist_meta(mylist_id)
        if error:
            return {"mylists": mylists, "error": error}

        mylists.append({"id": mylist_id, "title": info.get("title") or f"マイリスト {mylist_id}"})
        self._save_registered_mylists(mylists)
        return {"mylists": mylists, "error": None}

    def remove_mylist(self, mylist_id: str):
        mylists = self._load_registered_mylists()
        mylists = [m for m in mylists if m.get("id") != mylist_id]
        self._save_registered_mylists(mylists)
        return {"mylists": mylists, "error": None}

    def _fetch_mylist_meta(self, mylist_id: str):
        """マイリストの存在確認とタイトル取得のみを行う（中身の動画は見ない）"""
        try:
            resp = requests.get(
                MYLIST_API_URL.format(mylist_id=mylist_id),
                params={"pageSize": 1, "page": 1},
                headers=MYLIST_HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None, "マイリストが見つかりませんでした（IDが違うか、削除された可能性があります）"
            return None, f"マイリストの取得に失敗しました: {e}"
        except Exception as e:
            return None, f"マイリストの取得に失敗しました: {e}"

        meta = data.get("meta", {}) or {}
        if meta.get("status") not in (200, None):
            return None, "このマイリストは非公開、または閲覧できません（公開設定を確認してください）"

        mylist = ((data.get("data") or {}).get("mylist")) or {}
        return {"title": mylist.get("name")}, None

    def playlist_page(
        self,
        playlist_id: str,
        limit: int = 5,
        offset: int = 0,
        page_token: Optional[str] = None,
    ) -> SearchPage:
        if not playlist_id:
            return SearchPage(items=[VideoItem(
                id="", title="マイリストが指定されていません",
                thumbnail_url="", video_url="", source_name=self.name,
            )], total_count=0)

        # nvapi側は offset ではなく 1始まりの page + pageSize を使うため変換する。
        # limit が途中で変わるケース（ウィンドウリサイズ等）を考慮し、
        # offsetがちょうどページ境界に乗らない場合は大きめに取得して切り出す。
        page = offset // limit + 1 if limit else 1
        headers = dict(MYLIST_HEADERS)
        cookie_header = nico_auth.get_cookie_header()

        # ログイン中は、まず自分のマイリスト用エンドポイント（非公開にも
        # 対応）を試す。ログインしていない、または自分のマイリストでは
        # ない（他人の公開マイリストをURL登録したもの等）場合は、
        # 従来の公開マイリスト専用エンドポイントにフォールバックする。
        if cookie_header:
            me_headers = dict(headers)
            me_headers.update(cookie_header)
            try:
                resp = requests.get(
                    MYLIST_ME_API_URL.format(mylist_id=playlist_id),
                    params={"pageSize": limit, "page": page},
                    headers=me_headers,
                    timeout=10,
                )
                if resp.status_code == 200:
                    return self._parse_mylist_response(resp.json())
            except Exception as e:
                _debug_logger.debug("playlist_page: me-endpoint raised %r, falling back", e)

        try:
            resp = requests.get(
                MYLIST_API_URL.format(mylist_id=playlist_id),
                params={"pageSize": limit, "page": page},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                message = "マイリストが見つかりませんでした"
            else:
                message = f"マイリスト取得エラー: {e}"
            return SearchPage(items=[VideoItem(
                id="", title=message, thumbnail_url="", video_url="", source_name=self.name,
            )], total_count=0)
        except Exception as e:
            return SearchPage(items=[VideoItem(
                id="", title=f"マイリストの取得に失敗しました: {e}",
                thumbnail_url="", video_url="", source_name=self.name,
            )], total_count=0)

        return self._parse_mylist_response(data)

    @staticmethod
    def _parse_mylist_response(data: dict) -> SearchPage:
        """
        nvapi.nicovideo.jp/v2/mylists/{id} のレスポンス例:
        {
          "meta": {"status": 200},
          "data": {
            "mylist": {
              "name": "...",
              "totalItemCount": 42,
              "items": [
                {"video": {"id": "sm9", "title": "...", "thumbnail": {"url": "..."}}}
              ]
            }
          }
        }
        """
        meta = data.get("meta", {}) or {}
        if meta.get("status") not in (200, None):
            return SearchPage(items=[VideoItem(
                id="", title="このマイリストは非公開、または閲覧できません",
                thumbnail_url="", video_url="", source_name="ニコニコ動画",
            )], total_count=0)

        mylist = ((data.get("data") or {}).get("mylist")) or {}
        items = []
        for entry in mylist.get("items") or []:
            video = entry.get("video") or {}
            video_id = video.get("id")
            if not video_id:
                continue
            thumbnail = video.get("thumbnail") or {}
            items.append(
                VideoItem(
                    id=video_id,
                    title=video.get("title") or "(タイトル不明)",
                    thumbnail_url=thumbnail.get("url") or thumbnail.get("largeUrl") or "",
                    video_url=f"https://www.nicovideo.jp/watch/{video_id}",
                    source_name="ニコニコ動画",
                )
            )
        total_count = mylist.get("totalItemCount")
        return SearchPage(items=items, total_count=total_count)

    # ------------------------------------------------------------------
    # ログイン中アカウント自身のマイリスト一覧（非公式・未検証）
    # ------------------------------------------------------------------
    def get_own_mylists(self):
        """
        ログイン中のアカウント自身のマイリスト一覧を返す。
        戻り値: {"mylists": [{"id":..., "title":...}], "error": Optional[str]}

        2026-08-15、ユーザー自身の実機での開発者ツール調査により、
        https://nvapi.nicovideo.jp/v1/users/me/mylists
        （`me` は文字列そのまま。数値のユーザーIDへ事前変換する必要は無い）
        というエンドポイントであることを特定できた。ただし正式な仕様書は
        無いため、レスポンスの正確なフィールド名は実機ログで確認しながら
        調整する。
        """
        cookie_header = nico_auth.get_cookie_header()
        if not cookie_header:
            return {"mylists": [], "error": "ニコニコ動画にログインしていません"}

        headers = dict(MYLIST_HEADERS)
        headers.update(cookie_header)
        try:
            resp = requests.get(
                USER_MYLISTS_API_URL,
                # sampleItemCount=0 だと500エラーになることが実機で判明した
                # ため、ユーザーが開発者ツールで確認した実際のパラメータ
                # （sampleItemCount=3, pageSize=50）にそのまま合わせる
                params={"sampleItemCount": 3, "page": 1, "pageSize": 50},
                headers=headers,
                timeout=10,
            )
            _debug_logger.debug("get_own_mylists: GET %s -> status=%s", USER_MYLISTS_API_URL, resp.status_code)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            _debug_logger.debug("get_own_mylists: request raised %r", e)
            return {"mylists": [], "error": f"マイリスト一覧の取得に失敗しました: {e}"}

        meta = data.get("meta", {}) or {}
        inner_data = data.get("data") or {}
        _debug_logger.debug(
            "get_own_mylists: response meta=%s data_keys=%s",
            meta, list(inner_data.keys()) if isinstance(inner_data, dict) else "<not a dict>",
        )
        if meta.get("status") not in (200, None):
            return {"mylists": [], "error": "マイリスト一覧を取得できませんでした（ログインが切れている可能性があります）"}

        # レスポンスの実際のキー名が "mylists" か "items" か等、実機ログで
        # 確認できていないため、代表的な候補を順に試す
        raw_mylists = (
            inner_data.get("mylists")
            or inner_data.get("items")
            or (inner_data if isinstance(inner_data, list) else None)
            or []
        )
        _debug_logger.debug(
            "get_own_mylists: raw entries=%s",
            [{"id": e.get("id"), "name": e.get("name")} for e in raw_mylists if isinstance(e, dict)],
        )
        mylists = []
        for entry in raw_mylists:
            if not isinstance(entry, dict):
                continue
            mylist_id = entry.get("id")
            if mylist_id is None:
                continue
            mylists.append({"id": str(mylist_id), "title": entry.get("name") or f"マイリスト {mylist_id}"})
        return {"mylists": mylists, "error": None}
