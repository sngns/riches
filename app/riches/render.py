"""Turn a horizontal clip into a 1080x1920 Short with smart crop and word-timed captions."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
from pathlib import Path

import cv2

from .core import FONTS, run

HERE = Path(__file__).resolve().parent
FRONT = cv2.CascadeClassifier(str(HERE / "haarcascade_frontalface_default.xml"))
PROFILE = cv2.CascadeClassifier(str(HERE / "haarcascade_profileface.xml"))


# ---------------- analysis ----------------

def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    s = j["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {"w": int(s["width"]), "h": int(s["height"]), "fps": float(num) / float(den),
            "dur": float(j["format"]["duration"])}


def _faces(gray):
    h = gray.shape[0]
    min_sz = max(30, h // 12)
    f = FRONT.detectMultiScale(gray, 1.15, 5, minSize=(min_sz, min_sz))
    if len(f) == 0:
        f = PROFILE.detectMultiScale(gray, 1.15, 5, minSize=(min_sz, min_sz))
    return f


def analyse(path: Path, sample_fps: float = 3.0) -> dict:
    """Sample frames → shots (by histogram change) → per-shot face centre (0..1)."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(fps / sample_fps)))
    samples, prev_hist, idx = [], None, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = idx / fps
            small = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            cut = prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < 0.55
            prev_hist = hist
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = _faces(gray)
            cx = None
            if len(faces):
                x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
                cx = (x + w / 2) / small.shape[1]
            samples.append({"t": t, "cut": cut, "cx": cx})
        idx += 1
    cap.release()

    shots, cur = [], []
    for s in samples:
        if s["cut"] and cur:
            shots.append(cur)
            cur = []
        cur.append(s)
    if cur:
        shots.append(cur)

    # merge micro-shots (<0.7s) into previous to avoid jitter
    merged = []
    for sh in shots:
        if merged and (sh[-1]["t"] - sh[0]["t"]) < 0.7:
            merged[-1].extend(sh)
        else:
            merged.append(sh)

    out, face_samples = [], 0
    for sh in merged:
        xs = [s["cx"] for s in sh if s["cx"] is not None]
        face_samples += len(xs)
        out.append({"start": sh[0]["t"], "cx": statistics.median(xs) if xs else None})
    # shots with no face inherit neighbour centre
    last = 0.5
    for s in out:
        if s["cx"] is None:
            s["cx"] = last
        last = s["cx"]
    ratio = face_samples / max(1, len(samples))
    return {"shots": out, "face_ratio": ratio}


# ---------------- subtitles ----------------

def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[dict], n: int) -> list[list[dict]]:
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) >= n or w["start"] - cur[-1]["end"] > 0.45):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["word"][-1:] in ".?!," and len(cur) >= 2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], dur: float, clip: dict, rcfg: dict, path: Path) -> None:
    W, H = rcfg["width"], rcfg["height"]
    accent = _ass_color(rcfg["accent_color"])
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,Inter Display ExtraBold,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,1,0,1,7,3,2,80,80,{int(H * 0.30)},1",
        f"Style: Hook,Inter Display ExtraBold,76,&H00101010,&H00101010,{accent},&H00000000,"
        f"-1,0,0,0,100,100,0,0,3,22,0,8,110,110,{int(H * 0.115)},1",
        "Style: Credit,Inter SemiBold,40,&H22FFFFFF,&H22FFFFFF,&H00000000,&H90000000,"
        f"0,0,0,0,100,100,1,0,1,3,1,2,80,80,{int(H * 0.17)},1",
        f"Style: Big,Inter Display ExtraBold,98,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,8,4,8,90,90,{int(H * 0.22)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    hook_dur = float(clip.get("hook_dur") or 0)
    if clip.get("hook_text") and hook_dur > 0:
        words_h = _esc(clip["hook_text"]).upper().split()
        # highlight the last 2 words in the accent colour
        if len(words_h) > 2:
            words_h[-2] = f"{{\\c{accent}}}" + words_h[-2]
        lines.append(f"Dialogue: 3,{_ts(0)},{_ts(hook_dur)},Big,,0,0,0,,"
                     f"{{\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)\\fad(0,150)}}{' '.join(words_h)}")
    if rcfg.get("show_hook_title") and clip.get("hook_title"):
        hs = rcfg.get("hook_title_seconds") or 0
        hend = min(dur, hs) if hs else dur
        lines.append(f"Dialogue: 2,{_ts(hook_dur)},{_ts(hend)},Hook,,0,0,0,,"
                     f"{{\\fad(200,200)}}{_esc(clip['hook_title'].upper())}")

    if rcfg.get("show_credit") and clip.get("credit"):
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(dur)},Credit,,0,0,0,,{_esc(clip['credit'])}")

    chunks = chunk_words(words, rcfg.get("words_per_caption", 3))
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else dur
        chunk_end = min(nxt, ch[-1]["end"] + 0.35)
        texts = [_esc(w["word"]).upper() for w in ch]
        for wi, w in enumerate(ch):
            st = w["start"] if wi else min(w["start"], ch[0]["start"])
            en = ch[wi + 1]["start"] if wi + 1 < len(ch) else chunk_end
            if en - st < 0.04:
                continue
            parts = []
            for k, t in enumerate(texts):
                parts.append(f"{{\\c{accent}}}{t}{{\\c&H00FFFFFF&}}" if k == wi else t)
            pop = "{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{pop}{' '.join(parts)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------- render ----------------

def _crop_expr(shots: list[dict], src_w: int, crop_w: int) -> str:
    def x_for(cx: float) -> int:
        x = int(cx * src_w - crop_w / 2)
        return max(0, min(src_w - crop_w, x))
    xs = [(s["start"], x_for(s["cx"])) for s in shots]
    expr = str(xs[-1][1])
    for (t0, x), (t1, _) in reversed(list(zip(xs, xs[1:]))):
        expr = f"if(lt(t\\,{t1:.3f})\\,{x}\\,{expr})"
    return expr


def render(src: Path, words: list[dict], clip: dict, rcfg: dict, out: Path) -> dict:
    info = probe(src)
    W, H = rcfg["width"], rcfg["height"]
    ana = analyse(src)
    ass = out.with_suffix(".ass")
    build_ass(words, info["dur"], clip, rcfg, ass)

    crop_w = int(info["h"] * 9 / 16) // 2 * 2
    use_crop = ana["face_ratio"] >= 0.35 and crop_w <= info["w"]
    mode = "crop" if use_crop else "blur"

    cwd = out.parent
    ass_rel = os.path.relpath(ass, cwd).replace("\\", "/")
    fonts_rel = os.path.relpath(FONTS, cwd).replace("\\", "/")
    subs = f"ass={ass_rel}:fontsdir={fonts_rel}"

    if use_crop:
        x = _crop_expr(ana["shots"], info["w"], crop_w)
        vf = f"[0:v]crop={crop_w}:{info['h']}:{x}:0,scale={W}:{H}:flags=lanczos,setsar=1,{subs}[v]"
    else:
        vf = (f"[0:v]split[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:4,"
              f"eq=brightness=-0.12[bg];"
              f"[b]scale={W}:-2:flags=lanczos[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2-80,setsar=1,{subs}[v]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", os.path.relpath(src, cwd),
           "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
           "-af", f"loudnorm=I={rcfg['loudness_lufs']}:TP=-1.5:LRA=11",
           "-c:v", "libx264", "-preset", rcfg["preset"], "-crf", str(rcfg["crf"]),
           "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-movflags", "+faststart", os.path.relpath(out, cwd)]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg render failed:\n" + r.stderr[-2000:])

    # cover frame for the review page
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1",
         "-vf", "scale=360:-2", str(out.with_suffix(".jpg"))])
    return {"mode": mode, "face_ratio": round(ana["face_ratio"], 2), "duration": round(info["dur"], 2)}
