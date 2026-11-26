"""YouTube Data API upload + publish-slot scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import SECRETS, log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT = SECRETS / "client_secret.json"
TOKEN = SECRETS / "youtube_token.json"


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT.exists():
                raise RuntimeError("secrets/client_secret.json yok — KURULUM.md 4. adıma bak.")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def login() -> None:
    """Always run a fresh consent so the stored token is a new (non-expiring) one."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    log("YouTube bağlantısı tamam.")


def next_slot(cfg: dict, taken: list[str]) -> datetime:
    """First publish time (from config) that is in the future, not taken, and within max_per_day."""
    up = cfg["upload"]
    tz = ZoneInfo(up["timezone"])
    now = datetime.now(tz) + timedelta(minutes=20)
    taken_dt = {datetime.fromisoformat(t).astimezone(tz).replace(second=0, microsecond=0) for t in taken}
    for day in range(0, 60):
        d = (now + timedelta(days=day)).date()
        per_day = sum(1 for t in taken_dt if t.date() == d)
        for hm in up["publish_times"]:
            h, m = map(int, hm.split(":"))
            slot = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
            if slot <= now or slot in taken_dt:
                continue
            if per_day >= up["max_per_day"]:
                break
            return slot
    raise RuntimeError("60 gün içinde boş yayın saati yok")


def upload(clip: dict, cfg: dict, publish_at: datetime | None) -> str:
    from googleapiclient.http import MediaFileUpload

    yt = _service()
    title = clip["title"].strip()
    if "#shorts" not in title.lower() and len(title) <= 90:
        title = f"{title} #shorts"
    status = {"selfDeclaredMadeForKids": False, "embeddable": True}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        status["privacyStatus"] = cfg["upload"]["privacy_after_publish"]
    body = {
        "snippet": {"title": title[:100], "description": clip["description"][:4900],
                    "tags": clip.get("tags", []), "categoryId": cfg["upload"]["category_id"],
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": status,
    }
    media = MediaFileUpload(clip["file"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]
