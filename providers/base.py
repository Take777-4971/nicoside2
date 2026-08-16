from abc import ABC, abstractmethod
from typing import List, Optional


class VideoItem:
    def __init__(self, id: str, title: str, thumbnail_url: str, video_url: str, source_name: str):
        self.id = id
        self.title = title
        self.thumbnail_url = thumbnail_url
        self.video_url = video_url
        self.source_name = source_name


class SearchPage:
    """
    1ページ分の検索結果。

    total_count: 総ヒット件数（不明な場合はNone）
    next_page_token: トークン方式のAPI（YouTube等）で次ページを取得するためのトークン。
                      オフセット方式のAPI（ニコニコ等）では常にNone。
    """

    def __init__(
        self,
        items: List[VideoItem],
        total_count: Optional[int] = None,
        next_page_token: Optional[str] = None,
    ):
        self.items = items
        self.total_count = total_count
        self.next_page_token = next_page_token


class BaseVideoProvider(ABC):
    # "offset": _offset/_limitでページ送りできるAPI（任意ページへジャンプ可能）
    # "token" : nextPageTokenで次ページのみ取得できるAPI（先頭ページへのリセットのみ可能）
    pagination_mode: str = "offset"

    @property
    @abstractmethod
    def name(self) -> str:
        """動画サイトの識別名"""
        pass

    @abstractmethod
    def search_page(
        self,
        query: str,
        limit: int = 5,
        offset: int = 0,
        page_token: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> SearchPage:
        """
        1ページ分の検索結果を返す。
        pagination_mode == "offset" の場合は offset を、
        pagination_mode == "token" の場合は page_token を使用する。
        sort: ソート方法の指定（値の意味・指定できる値はプロバイダごとに異なる）。
              Noneの場合は各プロバイダの既定値を使う。
        """
        pass

    @abstractmethod
    def get_embed_url(self, video_id: str, loop: bool = False) -> str:
        """埋め込み再生用URLを取得。loop=Trueでリピート再生を試みる。"""
        pass
