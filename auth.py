"""
YouTube Data API へのOAuth 2.0認証（デスクトップアプリ向け "installed app" フロー）。

APIキーをアプリに同梱する方式は、一般公開を考えると好ましくない
（キーの抜き取り・クォータ濫用のリスクがある）ため、
OAuthでユーザー自身のGoogleアカウントにログインしてもらい、
そのユーザー自身のクォータ・権限でAPIを呼び出す方式に対応する。

ポイント:
- ログインは埋め込みWebViewではなく、必ずシステムの既定ブラウザで行う
  （GoogleはOAuthログインを埋め込みブラウザ内で行うことを許可していない
  ため、`InstalledAppFlow.run_local_server()` を使う。これはローカルに
  一時的なHTTPサーバーを立て、既定ブラウザでログイン画面を開き、
  リダイレクトでトークンを受け取る、Google公式のデスクトップアプリ向け
  推奨パターン）。
- 取得したトークンは token.json にローカル保存し、次回起動時は
  再ログイン不要（リフレッシュトークンで自動更新）。
- client_secret.json はOAuthクライアントの識別情報で、APIキーとは違い
  「これ単体を持っているだけでは他人のデータにアクセスできない」もの
  （ユーザー自身の同意が必ず必要）なので、公開アプリへの同梱に適した
  性質を持つ。ただし、このアプリでは各自でGoogle Cloud Consoleから
  取得したファイルを配置してもらう前提とする。
"""
import os

from paths import app_data_dir

BASE_DIR = app_data_dir()
CLIENT_SECRET_PATH = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def client_secret_configured() -> bool:
    return os.path.exists(CLIENT_SECRET_PATH)


def load_credentials():
    """
    保存済みのtoken.jsonから認証情報を読み込む。
    期限切れの場合はリフレッシュトークンで自動更新を試みる。
    有効な認証情報が無ければNoneを返す（例外は投げない）。
    """
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)
            return creds
    except Exception:
        return None
    return None


def _save_credentials(creds):
    try:
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    except Exception:
        pass


def run_oauth_flow():
    """
    ブラウザでのログインフローを開始する（システムの既定ブラウザを使用）。
    成功したら認証情報を保存して返す。失敗したら例外を投げる
    （呼び出し側でメッセージに変換してJSに返す）。
    """
    if not client_secret_configured():
        raise FileNotFoundError(
            "client_secret.json が見つかりません。README の手順に従って"
            "Google Cloud ConsoleでOAuthクライアントIDを作成し、"
            "ダウンロードしたファイルをこのフォルダに配置してください。"
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_credentials(creds)
    return creds


def logout():
    """保存済みのトークンを削除する"""
    try:
        if os.path.exists(TOKEN_PATH):
            os.remove(TOKEN_PATH)
        return True
    except Exception:
        return False


def get_access_token(creds) -> "str | None":
    if not creds:
        return None
    try:
        return creds.token
    except Exception:
        return None
