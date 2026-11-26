"""Pick the strongest self-contained moments with Claude."""
from __future__ import annotations

import json
import re

import anthropic

PROMPT = """You are the editor of a YouTube Shorts channel.

CHANNEL ANGLE:
{angle}

SOURCE VIDEO
Title: {title}
Channel: {channel}
Description (truncated): {description}
Speaker hint: {speaker_hint}

TRANSCRIPT (each line = [start_seconds] text):
{transcript}

TASK
Find up to {n} moments that would perform best as standalone vertical Shorts.
Rules:
- Each clip must be between {min_s} and {max_s} seconds long.
- It must make complete sense without any earlier context.
- The first sentence must hook the viewer immediately. Start exactly where the hook starts, not before.
- End on a complete thought / punchline, not mid-sentence.
- Clips must not overlap.
- Skip intros, sponsor reads, Q&A logistics and applause.
- teaser_start/teaser_end (absolute seconds, inside the clip, 2-4 s long, NOT the first sentence): the single most
  striking line of the clip. It is played first as a cold open before the clip starts, so it must be punchy on its own.
- score = honest 1-10 estimate of viral potential for THIS channel's angle (money, business, success).
- If the video's topic does not fit the channel angle (e.g. crime, sex, celebrity gossip, politics), return [] .

Return ONLY a JSON array, no prose, like:
[{{"start": 123.4, "end": 171.0, "score": 9,
  "speaker": "Full Name of the person speaking in this clip",
  "hook_title": "MAX 6 WORDS, ALL CAPS, CURIOSITY HOOK",
  "hook_text": "The first thing the viewer reads, max 9 words: a bold, specific, TRUE claim or open loop from this clip that makes them need to keep watching (e.g. 'He turned down $1B and it saved his company')",
  "teaser_start": 141.2, "teaser_end": 144.0,
  "yt_title": "YouTube title under 70 chars, no clickbait lies, may include 1 emoji",
  "description": "1-2 sentence description of the lesson",
  "tags": ["tag1", "tag2", "tag3"],
  "why": "one line reason"}}]
"""


def _fmt_transcript(segs: list[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in segs)


def _snap(t: float, segs: list[dict], key: str) -> float:
    return min((s[key] for s in segs), key=lambda v: abs(v - t))


def pick(meta: dict, segs: list[dict], cfg: dict) -> list[dict]:
    sel = cfg["selection"]
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=sel["model"],
        max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT.format(
            angle=sel["channel_angle"].strip(),
            title=meta["title"], channel=meta["channel"],
            description=meta.get("description", "")[:800],
            speaker_hint=meta.get("speaker_hint") or "unknown",
            transcript=_fmt_transcript(segs),
            n=sel["clips_per_video"], min_s=sel["min_clip_sec"], max_s=sel["max_clip_sec"],
        )}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError(f"Claude returned no JSON: {text[:300]}")
    raw = json.loads(m.group(0))

    out, used = [], []
    for c in sorted(raw, key=lambda c: -float(c.get("score", 0))):
        if float(c.get("score", 0)) < sel["min_score"]:
            continue
        start = _snap(float(c["start"]), segs, "start")
        end = _snap(float(c["end"]), segs, "end")
        if end - start > sel["max_clip_sec"]:
            # trim to the last segment end that fits
            fits = [s["end"] for s in segs if start < s["end"] <= start + sel["max_clip_sec"]]
            end = max(fits) if fits else start + sel["max_clip_sec"]
        if end - start < sel["min_clip_sec"]:
            continue
        if any(start < u_end and end > u_start for u_start, u_end in used):
            continue
        used.append((start, end))
        c.update(start=round(start, 2), end=round(end, 2))
        out.append(c)
    return out
