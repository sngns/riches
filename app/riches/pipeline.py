"""Daily job: discover → transcript → pick moments → cut → caption → queue for review."""
from __future__ import annotations

import re
import time
import traceback

from . import moments, render, source, transcribe
from .core import CLIPS, WORK, log, notify, state_tx, load_state


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def process_video(meta: dict, cfg: dict, limit: int | None = None) -> int:
    vid = meta["id"]
    log(f"▶ {meta['title']}  ({meta['channel']}, {meta['duration']//60} dk)")

    segs = source.transcript(vid)
    if segs:
        log(f"  altyazı bulundu: {len(segs)} satır")
    else:
        log("  altyazı yok → ses indiriliyor, Whisper ile çözülüyor (uzun sürebilir)")
        audio = source.download_audio(vid)
        segs = transcribe.segments(audio, cfg["render"]["whisper_model"])

    picks = moments.pick(meta, segs, cfg)
    if limit is not None:
        picks = picks[:max(0, limit)]
    log(f"  Claude {len(picks)} an seçti")

    made = 0
    for i, c in enumerate(picks, 1):
        clip_id = f"{time.strftime('%Y%m%d')}-{vid}-{i}"
        folder = CLIPS / clip_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            raw = source.download_section(vid, c["start"], c["end"], WORK / vid / f"raw_{i}.mp4")
            raw, c["hook_dur"] = _cold_open(raw, c, cfg)
            words = transcribe.words(raw, cfg["render"]["whisper_model"])
            speaker = c.get("speaker") or meta.get("speaker_hint") or ""
            src_name = meta.get("source_name") or meta["channel"]
            c["credit"] = f"{speaker} · {src_name}" if speaker else src_name
            out = folder / f"{_slug(c.get('hook_title', 'clip'))}.mp4"
            info = render.render(raw, words, c, cfg["render"], out)
            desc = cfg["upload"]["description_template"].format(
                description=c.get("description", ""), speaker=speaker or src_name,
                source_url=meta["url"])
            tags = list(dict.fromkeys((c.get("tags") or []) + cfg["upload"]["default_tags"]))
            with state_tx() as st:
                st["clips"][clip_id] = {
                    "id": clip_id, "status": "pending", "video_id": vid,
                    "source_title": meta["title"], "source_url": meta["url"],
                    "start": c["start"], "end": c["end"], "score": c.get("score"),
                    "why": c.get("why", ""), "speaker": speaker,
                    "hook_title": c.get("hook_title", ""),
                    "title": (c.get("yt_title") or c.get("hook_title", ""))[:95],
                    "description": desc, "tags": tags[:15],
                    "file": str(out), "thumb": str(out.with_suffix(".jpg")),
                    "words": words, "render": info, "created": time.time(),
                }
            made += 1
            log(f"  ✓ kesit {i}: {c.get('hook_title')} ({info['duration']} sn, {info['mode']})")
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ kesit {i} başarısız: {e}")
            log(traceback.format_exc(limit=3))
    return made


def _cold_open(raw, c: dict, cfg: dict):
    """Put the clip's most striking 2-4 s line in front of the clip (cold open). Returns (file, hook_seconds)."""
    default = float(cfg["render"].get("hook_seconds", 2.5))
    try:
        ts = float(c.get("teaser_start")) - c["start"]
        te = float(c.get("teaser_end")) - c["start"]
    except (TypeError, ValueError):
        return raw, default
    length = te - ts
    if not cfg["render"].get("cold_open", True) or ts < 1.0 or not (1.5 <= length <= 4.5) or te > c["end"] - c["start"]:
        return raw, default
    out = raw.with_name(raw.stem + "_co.mp4")
    fc = (f"[0:v]trim={ts:.3f}:{te:.3f},setpts=PTS-STARTPTS[v1];[0:a]atrim={ts:.3f}:{te:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={length-0.12:.3f}:d=0.12[a1];"
          f"[0:v]trim=0,setpts=PTS-STARTPTS[v2];[0:a]asetpts=PTS-STARTPTS,afade=t=in:d=0.08[a2];"
          f"[v1][a1][v2][a2]concat=n=2:v=1:a=1[v][a]")
    from .core import run
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)])
    return out, round(length, 2)


def run_daily(cfg: dict) -> int:
    log("=== Riches günlük çalışma başladı ===")
    st = load_state()
    cands = source.discover(cfg, st)
    if not cands:
        log("Yeni uygun video bulunamadı.")
        return 0
    total = 0
    cap = cfg["discovery"].get("clips_per_day", 10)
    for meta in cands:
        if total >= cap:
            break
        try:
            n = process_video(meta, cfg, limit=cap - total)
            total += n
            status = "done" if n else "failed"
        except Exception as e:  # noqa: BLE001
            log(f"✗ video başarısız: {e}")
            log(traceback.format_exc(limit=4))
            status = "failed"
        with state_tx() as s:
            prev = s["videos"].get(meta["id"], {})
            s["videos"][meta["id"]] = {"title": meta["title"], "status": status, "at": time.time(),
                                       "tries": prev.get("tries", 0) + 1}
    log(f"=== Bitti: {total} yeni kesit onay bekliyor ===")
    if total:
        notify("Riches", f"{total} yeni kesit onayını bekliyor")
    else:
        notify("Riches ⚠️", "Bugün kesit üretilemedi — data/riches.log dosyasına bakılmalı")
    return total
