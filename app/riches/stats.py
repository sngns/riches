"""Read-only YouTube statistics (separate token, so uploads are never affected)."""
from __future__ import annotations

import json
import time

from .core import DATA, SECRETS, log

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT = SECRETS / "client_secret.json"
TOKEN = SECRETS / "youtube_stats_token.json"
CACHE = DATA / "stats.json"
TTL = 15 * 60


def connected() -> bool:
    return TOKEN.exists()


def login() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    log("İstatistik bağlantısı tamam.")


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def get(video_ids: list[str], force: bool = False) -> dict:
    """{'at': ts, 'channel': {...}, 'videos': {id: {views, likes, comments}}} — cached 15 min."""
    if not connected():
        return {}
    if CACHE.exists() and not force:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - c.get("at", 0) < TTL and set(video_ids) <= set(c.get("videos", {})):
            return c
    try:
        yt = _service()
        ch = yt.channels().list(part="statistics,snippet", mine=True).execute()["items"][0]
        out = {"at": time.time(), "channel": {
            "title": ch["snippet"]["title"],
            "subs": int(ch["statistics"].get("subscriberCount", 0)),
            "views": int(ch["statistics"].get("viewCount", 0)),
            "videos": int(ch["statistics"].get("videoCount", 0))}, "videos": {}}
        for i in range(0, len(video_ids), 50):
            res = yt.videos().list(part="statistics,status", id=",".join(video_ids[i:i + 50])).execute()
            for v in res.get("items", []):
                s = v.get("statistics", {})
                out["videos"][v["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                                          "comments": int(s.get("commentCount", 0)),
                                          "privacy": v.get("status", {}).get("privacyStatus", "")}
        CACHE.write_text(json.dumps(out), encoding="utf-8")
        return out
    except Exception as e:  # noqa: BLE001
        log(f"istatistik alınamadı: {e}")
        return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {"error": str(e)}
