import os

import webview

from api import Api, BASE_WIDTH, COLLAPSED_WIDTH
from paths import app_data_dir, resource_dir

INDEX_HTML = os.path.join(resource_dir(), "ui", "index.html")

# 初期表示時の仮サイズ（ウィンドウ表示後、すぐに select_window_size(1) で
# ディスプレイ端に収まるサイズ・位置へ調整されるため、ここでは仮の値でよい）
INITIAL_HEIGHT = 680

# WebView2の永続プロファイル用フォルダ。private_mode=False にすると
# ここにCookie・キャッシュ等が保存される（ニコニコ動画のDRM保護された
# 動画再生に必要。詳細はwebview.start()呼び出し部のコメント参照）。
WEBVIEW_STORAGE_PATH = os.path.join(app_data_dir(), ".webview_data")


def main():
    api = Api()
    window = webview.create_window(
        title="NicoSide",
        url=INDEX_HTML,
        js_api=api,
        width=BASE_WIDTH,
        height=INITIAL_HEIGHT,
        # 「自動的に隠す」モードでウィンドウ幅を細いバー状(COLLAPSED_WIDTH)
        # まで縮めるため、min_sizeもそれを下回らない範囲で低く設定する。
        # ここを300pxのままにすると、pywebviewが強制的に300px未満への
        # リサイズを拒否してしまい、畳む処理が効かなくなる。
        min_size=(COLLAPSED_WIDTH, 200),
        resizable=True,
        on_top=True,
        background_color="#121212",
        # frameless=True: OSネイティブのタイトルバー（アイコン・最小化/
        # 最大化/閉じるボタン）を取り除く。ガジェットらしい見た目にする
        # ための変更。代わりにHTML側（ui/index.html の #title-bar）へ
        # 独自の最小化・閉じるボタンとドラッグ用の領域を用意している。
        #
        # easy_drag=False: pywebviewの easy_drag=True は、frameless時に
        # ウィンドウ全体（あらゆるmousedown）をドラッグ起点にしてしまい、
        # 検索ボックスやドロップダウン、各種ボタンなど普段のUI操作と
        # 衝突してしまうことが実装調査で判明した。そのため無効化し、
        # 代わりに `.pywebview-drag-region` というCSSクラスを付けた
        # 要素（独自タイトルバーなど）だけがドラッグでウィンドウを
        # 動かせるようにしている（この仕組み自体はpywebview標準機能）。
        frameless=True,
        easy_drag=False,
    )
    # ウィンドウ表示後、前回終了時の位置・サイズがあれば復元し、
    # 無ければ既定サイズ（x1、ディスプレイ端）を適用する。
    # イベントハンドラの戻り値はpywebview内部でset()に格納されるため、
    # ハッシュ不可能な値（dict等）を返すとエラーになる。
    # select_window_size()等はdictを返すため、直接コールバックにせず
    # 戻り値を破棄するラッパー関数を使う。
    def _apply_initial_size():
        api.apply_initial_window_state()

    window.events.shown += _apply_initial_size

    # ウィンドウが閉じられる直前に、位置・サイズを保存する
    def _save_state_on_close():
        api.save_window_state_on_close()

    window.events.closing += _save_state_on_close

    # ウィンドウをドラッグして画面端に近づけたときのスナップ処理
    window.events.moved += api.handle_window_moved

    # http_server=True: HTMLをfile://ではなくhttp://127.0.0.1:port/経由で配信する。
    # YouTubeの埋め込みプレーヤーは file:// (オリジンなし) から開かれると
    # 「動画を再生できません」となることがあるため、正規のオリジンを持たせる。
    #
    # private_mode=False + storage_path: デフォルトのprivate_mode=True
    # （プライベートブラウジング相当）だと、ニコニコ動画のDRM保護された
    # ストリームの再生に必要なコンポーネントが初期化されず、動画によらず
    # 一律で再生エラーになることが実機調査で判明したため無効化する。
    # これにより、Cookie・キャッシュ等はこのフォルダに永続化されるように
    # なる。ニコニコのログイン機能（nico_auth／open_niconico_login）は、
    # ログインセッションをディスクに残さない設計にしていたが、この変更で
    # ログイン用ウィンドウのCookieもWebView2自身が自動的にこのフォルダへ
    # 書き込むようになるため、ログイン処理側でセッション値を取り出した
    # 直後に該当ウィンドウの clear_cookies() を呼び、極力残さないように
    # している（完全にメモリ上のみと同等の保証ではない点に注意）。
    #
    # debug=True: 開発者ツール（右クリック→検証 / F12）を使えるようにする。
    # 埋め込みプレーヤーまわりの不具合調査のため一時的に有効化している。
    # 一般公開版をビルドする際はFalseに戻すことを検討する。
    # user_agent: pywebviewのデフォルトUser-Agentは、通常のEdgeブラウザとは
    # 異なる場合がある。ニコニコ動画の動画ストリーミング側が見慣れない
    # User-Agentを弾いている可能性を検証するため、一般的なWindows版Edgeの
    # User-Agentを明示的に指定して様子を見る（2026-08-14調査）。
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
    )

    # debug=True: 開発者ツール（右クリック→検証 / F12）を使えるようにする。
    # 埋め込みプレーヤーまわりの不具合調査のため一時的に有効化していたが、
    # 一連の調査が完了したため無効化した。今後また埋め込み関連の調査が
    # 必要になった場合は、下の行を True に戻せば再度使えるようになる。
    DEBUG_MODE = False

    webview.start(
        http_server=True,
        debug=DEBUG_MODE,
        private_mode=False,
        storage_path=WEBVIEW_STORAGE_PATH,
        user_agent=USER_AGENT,
    )


if __name__ == "__main__":
    main()
