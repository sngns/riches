"""Local Whisper transcription (faster-whisper). Audio is decoded with ffmpeg, not PyAV."""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np


@lru_cache(maxsize=2)
def _model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8")


def _load_audio(path: Path, sr: int = 16000) -> np.ndarray:
    """Decode any media file to 16 kHz mono float32 using ffmpeg."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
                        "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def segments(audio: Path, model: str = "small.en") -> list[dict]:
    """Sentence-level segments for a whole video (fallback when no captions)."""
    segs, _ = _model(model).transcribe(_load_audio(audio), language="en", vad_filter=True)
    return [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]


def words(media: Path, model: str = "small.en") -> list[dict]:
    """Word-level timestamps for a clip: [{start,end,word}]."""
    segs, _ = _model(model).transcribe(_load_audio(media), language="en", word_timestamps=True,
                                       vad_filter=False, condition_on_previous_text=False)
    out = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                out.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": t})
    return out
