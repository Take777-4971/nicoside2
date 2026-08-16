"""
リソースの場所を解決する共通ヘルパー。

`python main.py` で直接実行する場合と、PyInstallerでexe化した場合とで、
「読み取り専用の同梱リソース（ui/ フォルダなど）」と「書き込み可能な
ユーザーデータ（token.json・config.json・client_secret.jsonなど）」の
正しい置き場所が変わるため、両ケースを吸収する。

- resource_dir(): 同梱した静的リソース（ui/など）を読むためのディレクトリ。
  PyInstallerのonefileビルドでは、実行のたびに一時フォルダ（sys._MEIPASS）
  に展開されるため、そこを見る必要がある。
- app_data_dir(): token.json・config.json・client_secret.json など、
  「exeを配置したフォルダに置いてもらう／そこに保存し続けたい」ファイル用の
  ディレクトリ。exe化されている場合はexe自身があるフォルダ、そうでない
  場合はこのスクリプトがあるフォルダを返す。sys._MEIPASSを使うと実行の
  たびに消える一時フォルダになってしまうため、ここでは絶対に使わない。
"""
import os
import sys


def resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def app_data_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))
