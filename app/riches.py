#!/usr/bin/env python3
"""RICHES — YouTube Shorts kesit otomasyonu.

Kullanım:
  riches.py run           # yeni videoları bul, kesitleri üret (günlük iş)
  riches.py panel         # onay panelini aç (http://localhost:8642)
  riches.py login         # YouTube yükleme bağlantısı (bir kez)
  riches.py stats-login   # YouTube istatistik bağlantısı (bir kez)
  riches.py add URL       # belirli bir videoyu hemen işle
  riches.py apikey        # Anthropic API anahtarını gir / değiştir
  riches.py status        # özet
Argümansız açılırsa (ör. Windows'ta Riches.exe'ye çift tıklayınca) menü gösterir.
"""
import os
import sys

from riches.core import FROZEN, ROOT, DATA, SECRETS, load_config, load_env, load_state, log, self_cmd

PORT = 8642


def _port_up() -> bool:
    import socket
    s = socket.socket()
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def open_panel() -> None:
    """Start the panel in the background (survives closing the window) and open the browser."""
    import subprocess
    import time
    import webbrowser
    up = _port_up()
    if up and os.name != "nt":
        # restart a running background panel so it always uses the latest code (never during an upload)
        busy = any(c.get("status") == "uploading" for c in load_state()["clips"].values())
        if not busy:
            subprocess.run(["pkill", "-f", "riches.py panel-serve"], check=False)
            for _ in range(20):
                time.sleep(0.25)
                if not (up := _port_up()):
                    break
    if not up:
        log_f = open(DATA / "panel.log", "a", encoding="utf-8")
        kw = {"cwd": str(ROOT), "stdout": log_f, "stderr": log_f, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        else:
            kw["start_new_session"] = True
        subprocess.Popen(self_cmd("panel-serve"), **kw)
        for _ in range(80):
            time.sleep(0.25)
            if _port_up():
                break
    webbrowser.open(f"http://localhost:{PORT}")
    print(f"Riches paneli açık: http://localhost:{PORT}\nBu pencereyi kapatabilirsin.")


def set_api_key() -> None:
    key = input("Anthropic API anahtarını yapıştır (sk-ant-api...): ").strip()
    if not key.startswith("sk-ant-api"):
        print("Bu bir API anahtarı değil (sk-ant-api ile başlamalı). Değişiklik yapılmadı.")
        return
    (SECRETS / ".env").write_text(f"ANTHROPIC_API_KEY={key}\n", encoding="utf-8")
    os.environ["ANTHROPIC_API_KEY"] = key
    try:
        import anthropic
        anthropic.Anthropic(api_key=key).models.list(limit=1)
        print("✅ Anahtar kaydedildi ve çalışıyor.")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Anahtar kaydedildi ama doğrulanamadı: {e}")


def menu() -> None:
    print("\n=== RICHES ===")
    if not (SECRETS / ".env").exists():
        print("İlk kurulum: önce Anthropic API anahtarı gerekiyor.")
        set_api_key()
    if not (SECRETS / "youtube_token.json").exists():
        print("Not: YouTube bağlı değil. Mac'teki 'secrets' klasörünü buraya kopyala ya da 3'ü seç.")
    while True:
        print("\n1) Paneli aç\n2) Şimdi kesit üret\n3) YouTube yükleme bağlantısı\n"
              "4) YouTube istatistik bağlantısı\n5) API anahtarını değiştir\n6) Durum\n0) Çıkış")
        ch = input("Seçim: ").strip()
        if ch == "1":
            open_panel()
        elif ch == "2":
            from riches.pipeline import run_daily
            run_daily(load_config())
        elif ch == "3":
            from riches.youtube import login
            login()
        elif ch == "4":
            from riches.stats import login as stats_login
            stats_login()
        elif ch == "5":
            set_api_key()
        elif ch == "6":
            status()
        elif ch == "0":
            return


def status() -> None:
    from collections import Counter
    st = load_state()
    c = Counter(x["status"] for x in st["clips"].values())
    print(f"İşlenen video: {len(st['videos'])}")
    for k, v in c.items():
        print(f"  {k}: {v}")


def main() -> None:
    load_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ("menu" if FROZEN else "help")
    cfg = load_config()
    if cmd == "run":
        from riches.pipeline import run_daily
        run_daily(cfg)
    elif cmd == "panel":
        open_panel()
    elif cmd == "panel-serve":
        from riches.review import serve
        serve(open_browser=False)
    elif cmd == "login":
        from riches.youtube import login
        login()
    elif cmd == "stats-login":
        from riches.stats import login as stats_login
        stats_login()
    elif cmd == "apikey":
        set_api_key()
    elif cmd == "add" and len(sys.argv) > 2:
        from riches import source
        from riches.core import state_tx
        from riches.pipeline import process_video
        vid = source.video_id_from_url(sys.argv[2])
        meta = source.metadata(vid)
        meta["source_name"] = meta["channel"]
        n = process_video(meta, cfg)
        with state_tx() as s:
            s["videos"][vid] = {"title": meta["title"], "status": "done"}
        log(f"{n} kesit hazır → panel")
    elif cmd == "status":
        status()
    elif cmd == "menu":
        menu()
    elif cmd == "selftest":
        import subprocess
        import cv2, faster_whisper, yt_dlp, anthropic, flask, googleapiclient  # noqa: F401
        from riches import pipeline, review, youtube, stats, render  # noqa: F401
        from riches.core import FONTS
        print("fonts:", sorted(p.name for p in FONTS.glob("*")))
        print("cascade ok:", not render.FRONT.empty())
        print(subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout.splitlines()[0])
        print("SELFTEST OK")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
