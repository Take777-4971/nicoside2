from typing import Optional

import requests

import auth
from .base import BaseVideoProvider, SearchPage, VideoItem

# YouTube Data API v3
# https://developers.google.com/youtube/v3/docs/search/list
SEARCH_API_URL = "https://www.googleapis.com/youtube/v3/search"
# https://developers.google.com/youtube/v3/docs/playlists/list
PLAYLISTS_API_URL = "https://www.googleapis.com/youtube/v3/playlists"
# https://developers.google.com/youtube/v3/docs/playlistItems/list
PLAYLIST_ITEMS_API_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

NOT_LOGGED_IN_MESSAGE = "YouTubeにログインしていません。ログインボタンからログインしてください。"


class YouTubeProvider(BaseVideoProvider):
    """
    APIキーは使用せず、OAuth 2.0でユーザー自身のGoogleアカウントに
    ログインしてもらい、そのアクセストークンでYouTube Data APIを呼び出す。
    これにより、アプリ配布物にAPIキーを同梱する必要がなくなる
    （各ユーザーが自分自身のGoogleアカウント・クォータでAPIを利用する）。
    """

    pagination_mode = "token"

    @property
    def name(self) -> str:
        return "YouTube"

    @staticmethod
    def _auth_headers():
        """
        有効なOAuthアクセストークンがあれば認証ヘッダーを返す。
        期限切れの場合は auth.load_credentials() 内部でリフレッシュを試みる。
        ログインしていない・トークンが無効な場合は None を返す。
        """
        creds = auth.load_credentials()
        token = auth.get_access_token(creds)
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    # 検索結果のソートに指定できる値（YouTube Data API search.list の
    # order パラメータに準拠）
    VALID_SORTS = {"date", "rating", "relevance", "title", "viewCount"}
    # UIのドロップダウンに表示する選択肢（表示順）
    SORT_OPTIONS = [
        ("relevance", "関連度順"),
        ("date", "アップロード日時が新しい順"),
        ("viewCount", "再生回数が多い順"),
        ("rating", "評価が高い順"),
        ("title", "タイトル順"),
    ]

    def search_page(
        self,
        query: str,
        limit: int = 5,
        offset: int = 0,
        page_token: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> SearchPage:
        headers = self._auth_headers()
        if not headers:
            return self._error_page(NOT_LOGGED_IN_MESSAGE)

        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": limit,
        }
        if sort and sort in self.VALID_SORTS:
            params["order"] = sort
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(SEARCH_API_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            return self._error_page(f"YouTube検索エラー: {self._extract_error_message(e)}")
        except Exception as e:
            return self._error_page(f"YouTube検索に失敗しました: {e}")
        return self._parse_search_response(data)

    # ------------------------------------------------------------------
    # 再生リスト（ログイン中のアカウント自身の再生リストを選択して表示。
    # 編集は非対応）
    # ------------------------------------------------------------------
    def get_playlists(self, limit: int = 50):
        """
        ログイン中のアカウント自身の再生リスト一覧を返す
        （mine=true。非公開・限定公開のものも含めて取得できる）。
        戻り値: {"playlists": [{"id":..., "title":..., "thumbnail_url":...}], "error": Optional[str]}
        """
        headers = self._auth_headers()
        if not headers:
            return {"playlists": [], "error": NOT_LOGGED_IN_MESSAGE}

        params = {
            "part": "snippet",
            "mine": "true",
            "maxResults": limit,
        }
        try:
            resp = requests.get(PLAYLISTS_API_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            return {"playlists": [], "error": self._extract_error_message(e)}
        except Exception as e:
            return {"playlists": [], "error": str(e)}
        return self._parse_playlists_response(data)

    @staticmethod
    def _parse_playlists_response(data: dict):
        """
        playlists.list のレスポンス例:
        {
          "items": [
            {
              "id": "PL...",
              "snippet": {
                "title": "...",
                "thumbnails": {"medium": {"url": "..."}}
              }
            }
          ]
        }
        """
        playlists = []
        for entry in data.get("items") or []:
            playlist_id = entry.get("id")
            if not playlist_id:
                continue
            snippet = entry.get("snippet") or {}
            thumbnails = snippet.get("thumbnails") or {}
            thumb = (
                thumbnails.get("medium")
                or thumbnails.get("default")
                or thumbnails.get("high")
                or {}
            ).get("url", "")
            playlists.append(
                {
                    "id": playlist_id,
                    "title": snippet.get("title") or "(タイトル不明)",
                    "thumbnail_url": thumb,
                }
            )
        return {"playlists": playlists, "error": None}

    def playlist_page(
        self,
        playlist_id: str,
        limit: int = 5,
        offset: int = 0,
        page_token: Optional[str] = None,
    ) -> SearchPage:
        headers = self._auth_headers()
        if not headers:
            return self._error_page(NOT_LOGGED_IN_MESSAGE)
        if not playlist_id:
            return self._error_page("再生リストが指定されていません")

        params = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": limit,
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(PLAYLIST_ITEMS_API_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            return self._error_page(f"再生リスト取得エラー: {self._extract_error_message(e)}")
        except Exception as e:
            return self._error_page(f"再生リストの取得に失敗しました: {e}")
        return self._parse_playlist_items_response(data)

    @staticmethod
    def _parse_playlist_items_response(data: dict) -> SearchPage:
        """
        playlistItems.list のレスポンス例:
        {
          "nextPageToken": "...",
          "pageInfo": {"totalResults": 42, "resultsPerPage": 5},
          "items": [
            {
              "snippet": {
                "title": "...",
                "thumbnails": {"medium": {"url": "..."}},
                "resourceId": {"kind": "youtube#video", "videoId": "..."}
              }
            }
          ]
        }
        注意: items[].id は「再生リストへの登録」自体のID（playlistItemId）であり、
        動画そのもののIDではない。動画IDは snippet.resourceId.videoId から取得する。
        """
        items = []
        for entry in data.get("items") or []:
            snippet = entry.get("snippet") or {}
            resource_id = snippet.get("resourceId") or {}
            video_id = resource_id.get("videoId")
            if not video_id:
                continue
            thumbnails = snippet.get("thumbnails") or {}
            thumb = (
                thumbnails.get("medium")
                or thumbnails.get("default")
                or thumbnails.get("high")
                or {}
            ).get("url", "")
            items.append(
                VideoItem(
                    id=video_id,
                    title=snippet.get("title") or "(タイトル不明)",
                    thumbnail_url=thumb,
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    source_name="YouTube",
                )
            )
        page_info = data.get("pageInfo") or {}
        total_count = page_info.get("totalResults")
        next_page_token = data.get("nextPageToken")
        return SearchPage(items=items, total_count=total_count, next_page_token=next_page_token)

    @staticmethod
    def _extract_error_message(e: requests.exceptions.HTTPError) -> str:
        try:
            body = e.response.json()
            return body.get("error", {}).get("message", str(e))
        except Exception:
            return str(e)

    def _error_page(self, message: str) -> SearchPage:
        return SearchPage(
            items=[
                VideoItem(
                    id="",
                    title=message,
                    thumbnail_url="",
                    video_url="",
                    source_name=self.name,
                )
            ],
            total_count=0,
        )

    @staticmethod
    def _parse_search_response(data: dict) -> SearchPage:
        """
        search.list のレスポンス例:
        {
          "nextPageToken": "...",
          "pageInfo": {"totalResults": 123, "resultsPerPage": 5},
          "items": [
            {
              "id": {"videoId": "..."},
              "snippet": {
                "title": "...",
                "thumbnails": {"medium": {"url": "..."}}
              }
            }
          ]
        }
        """
        items = []
        for entry in data.get("items") or []:
            video_id = (entry.get("id") or {}).get("videoId")
            if not video_id:
                continue
            snippet = entry.get("snippet") or {}
            thumbnails = snippet.get("thumbnails") or {}
            thumb = (
                thumbnails.get("medium")
                or thumbnails.get("default")
                or thumbnails.get("high")
                or {}
            ).get("url", "")
            items.append(
                VideoItem(
                    id=video_id,
                    title=snippet.get("title") or "(タイトル不明)",
                    thumbnail_url=thumb,
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    source_name="YouTube",
                )
            )
        page_info = data.get("pageInfo") or {}
        total_count = page_info.get("totalResults")
        next_page_token = data.get("nextPageToken")
        return SearchPage(items=items, total_count=total_count, next_page_token=next_page_token)

    def get_embed_url(self, video_id: str, loop: bool = False) -> str:
        url = f"https://www.youtube.com/embed/{video_id}?autoplay=1&enablejsapi=1"
        if loop:
            # YouTube埋め込みで単一動画をループさせるには、
            # loop=1 に加えて playlist に同じ動画IDを指定する必要がある
            # （公式の仕様上の制約）
            url += f"&loop=1&playlist={video_id}"
        return url
