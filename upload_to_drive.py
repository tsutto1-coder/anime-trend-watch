#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANIME TREND WATCH - Google Driveアップロード
outputs/<日付>/ の動画・投稿テキストを、Googleドライブの
「CM TREND WATCH」フォルダ内の日付フォルダへアップロードする。

認証: OAuth 2.0 リフレッシュトークン方式(あなた自身のGoogleアカウント)
      スコープは drive.file のみ = このスクリプトが作ったフォルダ/ファイルにしか
      アクセスできない最小権限。ドライブの他のファイルには一切触れません。

必要な環境変数(GitHub Secretsに登録):
  GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET / GDRIVE_REFRESH_TOKEN

使い方: python upload_to_drive.py            # 今日のフォルダを対象
        python upload_to_drive.py 2026-07-13 # 日付指定
"""

import json
import mimetypes
import os
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT_ROOT = BASE_DIR / "outputs"
JST = timezone(timedelta(hours=9))

ROOT_FOLDER_NAME = "ANIME TREND WATCH"
UPLOAD_FILES = ["reel.mp4", "feed.mp4", "digest.txt", "x.txt",
                "threads.txt", "instagram.txt", "note.md"]

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

FOLDER_MIME = "application/vnd.google-apps.folder"


def http(url: str, method="GET", data: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as res:
        body = res.read()
        return json.loads(body) if body else {}


def get_access_token() -> str:
    cid = os.environ.get("GDRIVE_CLIENT_ID")
    secret = os.environ.get("GDRIVE_CLIENT_SECRET")
    refresh = os.environ.get("GDRIVE_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        print("[warn] GDRIVE_* のSecretsが未設定のため、Driveアップロードをスキップします")
        sys.exit(0)  # 設定前でもワークフロー全体は失敗させない
    data = urllib.parse.urlencode({
        "client_id": cid,
        "client_secret": secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }).encode()
    res = http(TOKEN_URL, "POST", data,
               {"Content-Type": "application/x-www-form-urlencoded"})
    return res["access_token"]


def drive_list(token: str, query: str) -> list[dict]:
    url = f"{API}/files?" + urllib.parse.urlencode({
        "q": query, "fields": "files(id,name)", "pageSize": 100})
    return http(url, headers={"Authorization": f"Bearer {token}"}).get("files", [])


def find_or_create_folder(token: str, name: str, parent: str | None = None) -> str:
    esc = name.replace("'", "\\'")
    q = f"name = '{esc}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    found = drive_list(token, q)
    if found:
        return found[0]["id"]
    meta = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        meta["parents"] = [parent]
    res = http(f"{API}/files?fields=id", "POST",
               json.dumps(meta).encode(),
               {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    print(f"[info] フォルダ作成: {name}")
    return res["id"]


def upload_file(token: str, path: Path, folder_id: str):
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    esc = path.name.replace("'", "\\'")
    existing = drive_list(token, f"name = '{esc}' and '{folder_id}' in parents and trashed = false")

    if existing:
        # 同名ファイルがあれば中身を更新(再実行時の重複防止)
        fid = existing[0]["id"]
        http(f"{UPLOAD_API}/files/{fid}?uploadType=media", "PATCH",
             path.read_bytes(),
             {"Authorization": f"Bearer {token}", "Content-Type": mime})
        print(f"[info] 更新: {path.name}")
        return

    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": path.name, "parents": [folder_id]}).encode()
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + meta
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--".encode()
    )
    http(f"{UPLOAD_API}/files?uploadType=multipart&fields=id", "POST", body,
         {"Authorization": f"Bearer {token}",
          "Content-Type": f"multipart/related; boundary={boundary}"})
    print(f"[info] アップロード: {path.name}")


def main():
    date_s = sys.argv[1] if len(sys.argv) > 1 else datetime.now(JST).strftime("%Y-%m-%d")
    day_dir = OUT_ROOT / date_s
    if not day_dir.exists():
        sys.exit(f"{day_dir} がありません。先に cm_trend_watch.py を実行してください")

    targets = [day_dir / f for f in UPLOAD_FILES if (day_dir / f).exists()]
    if not targets:
        sys.exit("アップロード対象ファイルがありません")

    token = get_access_token()
    root_id = find_or_create_folder(token, ROOT_FOLDER_NAME)
    day_id = find_or_create_folder(token, date_s, root_id)
    for p in targets:
        upload_file(token, p, day_id)
    print(f"[info] Driveアップロード完了: {ROOT_FOLDER_NAME}/{date_s} に {len(targets)} ファイル")


if __name__ == "__main__":
    main()
