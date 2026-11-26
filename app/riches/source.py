"""YouTube side: find candidate videos, fetch transcripts, download clip sections."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

from .core import WORK, log

BASE_OPTS = {"quiet": True, "no_warnings": True, "noprogress": True}


def _ydl(extra: dict | None = None) -> yt_dlp.YoutubeDL:
    o = dict(BASE_OPTS)
    if extra:
        o.update(extra)
    return yt_dlp.YoutubeDL(o)


def video_id_from_url(url: str) -> str:
    with _ydl({"extract_flat": True}) as y:
        return y.extract_info(url, download=False, process=False)["id"]


def list_channel(url: str, n: int) -> list[str]:
    with _ydl({"extract_flat": "in_playlist", "playlistend": n}) as y:
        info = y.extract_info(url, download=False)
    return [e["id"] for e in (info.get("entries") or []) if e and e.get("id")]


def metadata(video_id: str) -> dict:
    with _ydl() as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    return {
        "id": video_id,
        "title": info.get("title", ""),
        "channel": info.get("channel") or info.get("uploader", ""),
        "duration": info.get("duration") or 0,
        "upload_date": info.get("upload_date") or "",
        "view_count": info.get("view_count") or 0,
        "description": (info.get("description") or "")[:1500],
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "is_live": bool(info.get("is_live") or info.get("was_live")),
    }


def discover(cfg: dict, state: dict) -> list[dict]:
    """Return up to videos_per_run fresh candidates, best first."""
    d = cfg["discovery"]
    seen = {k for k, v in state["videos"].items()
            if v.get("status") == "done" or v.get("tries", 1) >= 2}
    cands: list[dict] = []

    # manual links first
    for url in cfg.get("extra_videos") or []:
        try:
            vid = video_id_from_url(url)
            if vid not in seen:
                m = metadata(vid)
                m["priority"] = True
                cands.append(m)
        except Exception as e:  # noqa: BLE001
            log(f"  ! extra video failed {url}: {e}")

    cutoff = None
    if d.get("max_age_days"):
        cutoff = (datetime.now() - timedelta(days=d["max_age_days"])).strftime("%Y%m%d")

    for ch in cfg.get("channels") or []:
        try:
            ids = list_channel(ch["url"], d["scan_last_n_per_channel"])
        except Exception as e:  # noqa: BLE001
            log(f"  ! channel scan failed {ch.get('name')}: {e}")
            continue
        for vid in ids:
            if vid in seen or any(c["id"] == vid for c in cands):
                continue
            try:
                m = metadata(vid)
            except Exception as e:  # noqa: BLE001
                log(f"  ! metadata failed {vid}: {e}")
                continue
            if m["is_live"]:
                continue
            if not (d["min_duration_sec"] <= m["duration"] <= d["max_duration_sec"]):
                continue
            if cutoff and m["upload_date"] and m["upload_date"] < cutoff:
                continue
            if d.get("max_views") and m["view_count"] > d["max_views"]:
                continue  # already widely seen / heavily clipped
            if d.get("min_views") and m["view_count"] < d["min_views"]:
                continue
            m["source_name"] = ch.get("name", m["channel"])
            m["speaker_hint"] = ch.get("speaker", "")
            cands.append(m)
            time.sleep(0.5)

    # priority links first, then most viewed per day since upload
    def rank(m: dict) -> float:
        if m.get("priority"):
            return 1e18
        try:
            age = max(1, (datetime.now() - datetime.strptime(m["upload_date"], "%Y%m%d")).days)
        except ValueError:
            age = 30
        return m["view_count"] / age

    cands.sort(key=rank, reverse=True)
    return cands[: d["videos_per_run"]]


# ---------------- transcript ----------------

def _parse_json3(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segs = []
    for ev in data.get("events", []):
        if "segs" not in ev:
            continue
        start = ev.get("tStartMs", 0) / 1000
        dur = ev.get("dDurationMs", 0) / 1000
        text = "".join(s.get("utf8", "") for s in ev["segs"]).replace("\n", " ").strip()
        if not text:
            continue
        segs.append({"start": round(start, 2), "end": round(start + dur, 2), "text": text})
    # auto captions overlap; clamp ends to next start
    for a, b in zip(segs, segs[1:]):
        a["end"] = min(a["end"], b["start"]) if b["start"] > a["start"] else a["end"]
    return segs


def transcript(video_id: str) -> list[dict] | None:
    """YouTube captions (manual preferred, then auto) as [{start,end,text}]."""
    out = WORK / video_id
    out.mkdir(parents=True, exist_ok=True)
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB", "en-orig"],
        "subtitlesformat": "json3",
        "outtmpl": str(out / "subs.%(ext)s"),
    }
    try:
        with _ydl(opts) as y:
            y.download([f"https://www.youtube.com/watch?v={video_id}"])
    except Exception as e:  # noqa: BLE001
        log(f"  ! subtitle download failed: {e}")
    files = sorted(out.glob("subs*.json3"), key=lambda p: ("orig" in p.name, len(p.name)))
    for f in files:
        segs = _parse_json3(f)
        if len(segs) > 10:
            return segs
    return None


def download_audio(video_id: str) -> Path:
    out = WORK / video_id
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out / "audio.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
    }
    with _ydl(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={video_id}"])
    return next(out.glob("audio.*"))


def download_section(video_id: str, start: float, end: float, dest: Path) -> Path:
    """Download only [start,end] of the video (≤1080p) into dest (.mp4)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "download_ranges": download_range_func(None, [(max(0, start), end)]),
        "force_keyframes_at_cuts": True,
        "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
    }
    with _ydl(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={video_id}"])
    got = list(dest.parent.glob(dest.stem + ".*"))
    if not got:
        raise RuntimeError("section download produced no file")
    f = got[0]
    if f.suffix != ".mp4":
        f = f.rename(dest)
    return f
