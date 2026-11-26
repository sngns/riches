"""Shared helpers: paths, config, state, logging, notifications."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

FROZEN = getattr(sys, "frozen", False)          # running as the packaged Windows/Mac app
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
RES = Path(getattr(sys, "_MEIPASS", ROOT))       # bundled read-only resources (fonts, default config)
DATA = ROOT / "data"
WORK = DATA / "work"
CLIPS = DATA / "clips"
STATE_FILE = DATA / "state.json"
FONTS = RES / "fonts"
SECRETS = ROOT / "secrets"

for d in (DATA, WORK, CLIPS, SECRETS):
    d.mkdir(parents=True, exist_ok=True)

if not (ROOT / "config.yaml").exists() and (RES / "config.yaml").exists():
    import shutil
    shutil.copy(RES / "config.yaml", ROOT / "config.yaml")

for _s in (sys.stdout, sys.stderr):  # Windows consoles: never crash on Turkish chars / emoji
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def self_cmd(*args: str) -> list[str]:
    """Command line that re-runs this program with the given sub-command."""
    return [sys.executable, *args] if FROZEN else [sys.executable, str(ROOT / "riches.py"), *args]

# use the bundled ffmpeg/ffprobe in Riches/bin first (also picked up by yt-dlp)
if (ROOT / "bin").is_dir():
    os.environ["PATH"] = str(ROOT / "bin") + os.pathsep + os.environ.get("PATH", "")


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with open(DATA / "riches.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env() -> None:
    """Read secrets/.env (KEY=VALUE lines) into os.environ."""
    env = SECRETS / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------- state ----------------
# state = {"videos": {video_id: {...}}, "clips": {clip_id: {...}}}

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"videos": {}, "clips": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


@contextmanager
def state_tx():
    lock = DATA / ".state.lock"
    for _ in range(300):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL)
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 600:
                lock.unlink(missing_ok=True)
            time.sleep(0.2)
    try:
        st = load_state()
        yield st
        save_state(st)
    finally:
        lock.unlink(missing_ok=True)


def notify(title: str, text: str) -> None:
    """macOS notification (no-op elsewhere)."""
    if sys.platform != "darwin":
        return
    t = text.replace('"', "'")
    ti = title.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{ti}" sound name "Glass"'],
                   check=False)


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd[:4])}...\n{r.stderr[-2000:]}")
