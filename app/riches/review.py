"""Local approval panel: http://localhost:8765"""
from __future__ import annotations

import html
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, request, send_file

from . import youtube
from .core import load_config, load_state, log, state_tx

app = Flask(__name__)
_busy = {"run": False}

PAGE = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riches · Onay</title>
<style>
:root{--bg:#0d0d0f;--card:#17171b;--line:#26262c;--txt:#ececf1;--mut:#8a8a96;--acc:#FFD23F;--ok:#3ecf8e;--no:#ff5d5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Inter",sans-serif}
header{display:flex;align-items:center;justify-content:space-between;padding:22px 32px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
h1{margin:0;font-size:22px;letter-spacing:.5px}h1 b{color:var(--acc)}
.tabs a{color:var(--mut);text-decoration:none;margin-left:18px}.tabs a.on{color:var(--txt);border-bottom:2px solid var(--acc);padding-bottom:4px}
main{padding:28px 32px;display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
video{width:100%;aspect-ratio:9/16;background:#000;display:block;max-height:560px}
.body{padding:16px;display:flex;flex-direction:column;gap:10px}
label{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px}
input,textarea{width:100%;background:#0f0f12;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:9px 10px;font:inherit}
textarea{min-height:110px;resize:vertical;font-size:13px}
.meta{font-size:13px;color:var(--mut)}.meta a{color:var(--mut)}
.score{display:inline-block;background:var(--acc);color:#111;font-weight:700;border-radius:6px;padding:1px 7px;margin-right:6px}
.row{display:flex;gap:8px}button{flex:1;border:0;border-radius:9px;padding:11px;font-weight:700;cursor:pointer;font:inherit;font-weight:700}
.ok{background:var(--ok);color:#06140d}.now{background:#2b2b33;color:var(--txt)}.no{background:transparent;color:var(--no);border:1px solid #3a2226}
.empty{color:var(--mut);padding:60px 0;grid-column:1/-1;text-align:center}
.run{background:var(--acc);color:#111;border:0;border-radius:9px;padding:10px 16px;font-weight:700;cursor:pointer}
.badge a{color:var(--acc)}.badge{font-size:12px;padding:2px 8px;border-radius:20px;background:#22222a;color:var(--mut)}
</style></head><body>
<header><h1><b>RICHES</b> · Kesit Onayı</h1>
<div class="tabs">%TABS%
<form method="post" action="/run" style="display:inline;margin-left:22px"><button class="run" %RUNDIS%>%RUNTXT%</button></form></div></header>
<main>%CARDS%</main>
<script>if(%AUTORELOAD%)setInterval(()=>{const playing=[...document.querySelectorAll('video')].some(v=>!v.paused);
const typing=['INPUT','TEXTAREA'].includes(document.activeElement.tagName);if(!playing&&!typing)location.reload()},20000)</script>
</body></html>"""


def _fmt_time(iso: str | None, tz: str) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")


def _card(c: dict, tab: str, tz: str) -> str:
    e = html.escape
    head = (f'<video controls preload="metadata" poster="/thumb/{c["id"]}" src="/video/{c["id"]}"></video>'
            f'<div class="body"><div class="meta"><span class="score">{c.get("score","?")}</span>'
            f'{e(c.get("speaker",""))} · <a href="{e(c["source_url"])}&t={int(c["start"])}" target="_blank">'
            f'{e(c["source_title"][:60])}</a> · {int(c["end"]-c["start"])} sn</div>'
            f'<div class="meta">💡 {e(c.get("why",""))}</div>')
    if tab == "pending":
        return (f'<div class="card">{head}<form method="post" action="/decide/{c["id"]}">'
                f'<label>Başlık</label><input name="title" value="{e(c["title"])}" maxlength="95">'
                f'<label>Açıklama</label><textarea name="description">{e(c["description"])}</textarea>'
                f'<div class="row" style="margin-top:6px"><button class="ok" name="act" value="approve">✓ Onayla & planla</button>'
                f'<button class="now" name="act" value="now">Hemen yayınla</button></div>'
                f'<div class="row" style="margin-top:8px"><button class="no" name="act" value="reject">✗ Reddet</button></div>'
                f'</form></div></div>')
    info = {"scheduled": f'📅 {_fmt_time(c.get("publish_at"), tz)} · <a href="https://youtu.be/{c.get("yt_id","")}" target="_blank">YouTube</a>',
            "published": f'✅ Yayında · <a href="https://youtu.be/{c.get("yt_id","")}" target="_blank">YouTube</a>',
            "uploading": "⏫ Yükleniyor…", "error": f'⚠️ {e(c.get("error","")[:200])}',
            "rejected": "Reddedildi",
            "ready": f'📂 Hazır · planlanan saat {_fmt_time(c.get("publish_at"), tz) or "hemen"}'}.get(c["status"], c["status"])
    retry = (f'<form method="post" action="/decide/{c["id"]}"><button class="now" name="act" value="approve">Tekrar dene</button></form>'
             if c["status"] == "error" else "")
    if c["status"] == "ready":
        retry = (f'<label>Başlık</label><input readonly value="{e(c["title"])}" onclick="this.select();navigator.clipboard.writeText(this.value)">'
                 f'<label>Açıklama</label><textarea readonly onclick="this.select();navigator.clipboard.writeText(this.value)">{e(c["description"])}</textarea>'
                 f'<div class="row"><form method="post" action="/reveal/{c["id"]}" style="flex:1;display:flex"><button class="now">Finder\'da göster</button></form>'
                 f'<form method="post" action="/decide/{c["id"]}" style="flex:1;display:flex"><button class="ok" name="act" value="uploaded">✓ Yükledim</button></form></div>')
    return f'<div class="card">{head}<div><b>{e(c["title"])}</b></div><span class="badge">{info}</span>{retry}</div></div>'


@app.get("/")
def index():
    cfg = load_config()
    tz = cfg["upload"]["timezone"]
    tab = request.args.get("tab", "pending")
    groups = {"pending": ["pending"], "queue": ["ready", "uploading", "scheduled", "error"],
              "done": ["published"], "rejected": ["rejected"]}
    names = {"pending": "Onay bekleyen", "queue": "Planlanan", "done": "Yayınlanan", "rejected": "Reddedilen"}
    st = load_state()
    _mark_published(st)
    clips = sorted(st["clips"].values(), key=lambda c: (-(c.get("score") or 0), -c["created"]))
    counts = {k: sum(1 for c in clips if c["status"] in v) for k, v in groups.items()}
    tabs = "".join(f'<a class="{"on" if k == tab else ""}" href="/?tab={k}">{names[k]} ({counts[k]})</a>' for k in groups)
    tabs += f'<a class="{"on" if tab == "stats" else ""}" href="/?tab=stats">📊 İstatistik</a>'
    if tab == "stats":
        return (PAGE.replace("%TABS%", tabs).replace("%CARDS%", _stats_html(clips, tz, request.args.get("refresh") == "1"))
                .replace("%RUNDIS%", "disabled" if _busy["run"] else "")
                .replace("%RUNTXT%", "⏳ Üretiliyor…" if _busy["run"] else "＋ Yeni kesit üret")
                .replace("%AUTORELOAD%", "false"))
    sel = [c for c in clips if c["status"] in groups.get(tab, [])]
    if tab == "queue":
        sel.sort(key=lambda c: c.get("publish_at") or "")
    cards = "".join(_card(c, tab, tz) for c in sel) or '<div class="empty">Burada şimdilik bir şey yok.</div>'
    return (PAGE.replace("%TABS%", tabs).replace("%CARDS%", cards)
            .replace("%RUNDIS%", "disabled" if _busy["run"] else "")
            .replace("%RUNTXT%", "⏳ Üretiliyor…" if _busy["run"] else "＋ Yeni kesit üret")
            .replace("%AUTORELOAD%", "true" if _busy["run"] or counts["queue"] else "false"))


def _fmt_n(n: int) -> str:
    return f"{n/1_000_000:.1f}M" if n >= 1_000_000 else (f"{n/1000:.1f}K" if n >= 1000 else str(n))


def _stats_html(clips: list, tz: str, force: bool) -> str:
    from . import stats
    e = html.escape
    if not stats.connected():
        return ('<div class="empty">İstatistikleri görmek için Riches klasöründeki '
                '<b>Istatistik Bagla.command</b> dosyasına bir kez çift tıkla '
                '(hesabını ve <b>riches</b> kanalını seçip izin ver), sonra bu sayfayı yenile.</div>')
    pub = [c for c in clips if c.get("yt_id") and c["status"] in ("published", "scheduled")]
    data = stats.get([c["yt_id"] for c in pub], force=force)
    if not data or "channel" not in data:
        return f'<div class="empty">İstatistik alınamadı: {e(str(data.get("error", "")))}</div>'
    ch, vs = data["channel"], data["videos"]
    tot_v = sum(vs.get(c["yt_id"], {}).get("views", 0) for c in pub)
    tot_l = sum(vs.get(c["yt_id"], {}).get("likes", 0) for c in pub)
    age = int((time.time() - data["at"]) / 60)
    box = lambda k, v: (f'<div style="background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px">'
                        f'<div class="meta">{k}</div><div style="font-size:30px;font-weight:800;margin-top:4px">{v}</div></div>')
    head = (f'<div style="grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px">'
            + box("Abone", _fmt_n(ch["subs"])) + box("Kanal toplam izlenme", _fmt_n(ch["views"]))
            + box("Riches shortları izlenme", _fmt_n(tot_v)) + box("Beğeni", _fmt_n(tot_l))
            + box("Yayınlanan / planlanan", f'{sum(1 for c in pub if c["status"]=="published")} / {sum(1 for c in pub if c["status"]=="scheduled")}')
            + '</div>')
    pub.sort(key=lambda c: vs.get(c["yt_id"], {}).get("views", -1), reverse=True)
    rows = ""
    for c in pub:
        v = vs.get(c["yt_id"], {})
        when = _fmt_time(c.get("publish_at"), tz)
        st = "📅 " + when if c["status"] == "scheduled" else when
        rows += (f'<tr><td><img src="/thumb/{c["id"]}" style="width:44px;border-radius:6px"></td>'
                 f'<td><a href="https://youtube.com/shorts/{c["yt_id"]}" target="_blank" style="color:var(--txt)">{e(c["title"][:70])}</a>'
                 f'<div class="meta">{e(c.get("speaker",""))}</div></td><td class="meta">{st}</td>'
                 f'<td style="text-align:right;font-weight:700">{_fmt_n(v.get("views",0)) if v else "–"}</td>'
                 f'<td style="text-align:right">{_fmt_n(v.get("likes",0)) if v else "–"}</td>'
                 f'<td style="text-align:right">{v.get("comments",0) if v else "–"}</td></tr>')
    table = ('<div style="grid-column:1/-1;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden">'
             '<table style="width:100%;border-collapse:collapse;font-size:14px">'
             '<tr style="text-align:left;color:var(--mut);font-size:12px;text-transform:uppercase"><th style="padding:12px"></th><th>Short</th><th>Yayın</th>'
             '<th style="text-align:right">İzlenme</th><th style="text-align:right">Beğeni</th><th style="text-align:right;padding-right:16px">Yorum</th></tr>'
             + rows.replace("<td", '<td style="padding:10px 12px;border-top:1px solid var(--line)"', ).replace('style="padding:10px 12px;border-top:1px solid var(--line)" style="', 'style="padding:10px 12px;border-top:1px solid var(--line);')
             + '</table></div>')
    foot = (f'<div class="meta" style="grid-column:1/-1">{age} dk önce güncellendi · 15 dakikada bir yenilenir · '
            f'<a href="/?tab=stats&refresh=1" style="color:var(--acc)">şimdi yenile</a></div>')
    return head + table + foot


def _mark_published(st: dict) -> None:
    now = datetime.now().astimezone()
    changed = False
    for c in st["clips"].values():
        if c["status"] == "scheduled" and c.get("publish_at") and datetime.fromisoformat(c["publish_at"]) <= now:
            changed = True
    if changed:
        with state_tx() as s:
            for c in s["clips"].values():
                if c["status"] == "scheduled" and datetime.fromisoformat(c["publish_at"]) <= now:
                    c["status"] = "published"


@app.get("/video/<cid>")
def video(cid):
    c = load_state()["clips"].get(cid) or abort(404)
    return send_file(c["file"], mimetype="video/mp4", conditional=True)


@app.get("/thumb/<cid>")
def thumb(cid):
    c = load_state()["clips"].get(cid) or abort(404)
    return send_file(c["thumb"], mimetype="image/jpeg")


def _prepare_manual(cid: str, now: bool) -> None:
    """Manual mode: copy the approved clip + texts into data/yayinla for hand upload."""
    import shutil
    from .core import DATA
    cfg = load_config()
    out_dir = DATA / "yayinla"
    out_dir.mkdir(exist_ok=True)
    with state_tx() as st:
        c = st["clips"][cid]
        if now:
            slot = None
        else:
            taken = [x["publish_at"] for x in st["clips"].values()
                     if x.get("publish_at") and x["id"] != cid and x["status"] in ("scheduled", "published", "ready")]
            slot = youtube.next_slot(cfg, taken)
            c["publish_at"] = slot.isoformat()
        stamp = slot.strftime("%Y-%m-%d_%H%M") if slot else "HEMEN"
        base = out_dir / f"{stamp}_{cid}"
        shutil.copy(c["file"], base.with_suffix(".mp4"))
        title = c["title"] if "#shorts" in c["title"].lower() else f"{c['title']} #shorts"
        base.with_suffix(".txt").write_text(
            f"BAŞLIK:\n{title}\n\nAÇIKLAMA:\n{c['description']}\n\nETİKETLER:\n{', '.join(c.get('tags', []))}\n\n"
            f"YAYIN: {slot.strftime('%d.%m.%Y %H:%M') if slot else 'hemen'}\n", encoding="utf-8")
        c["status"] = "ready"
        c["ready_file"] = str(base.with_suffix(".mp4"))
    log(f"📂 hazır (manuel yükleme): {base.name}")


def _do_upload(cid: str, now: bool) -> None:
    cfg = load_config()
    if cfg["upload"].get("mode", "manual") != "youtube_api":
        return _prepare_manual(cid, now)
    try:
        with state_tx() as st:
            c = st["clips"][cid]
            if now:
                slot = None
            else:
                taken = [x["publish_at"] for x in st["clips"].values()
                         if x.get("publish_at") and x["id"] != cid and x["status"] in ("scheduled", "published", "uploading")]
                slot = youtube.next_slot(cfg, taken)
                c["publish_at"] = slot.isoformat()
            c["status"] = "uploading"
            clip = dict(c)
        yt_id = youtube.upload(clip, cfg, slot)
        with state_tx() as st:
            c = st["clips"][cid]
            c["yt_id"] = yt_id
            c["status"] = "published" if now else "scheduled"
            if now:
                c["publish_at"] = datetime.now().astimezone().isoformat()
        log(f"⏫ yüklendi {cid} → youtu.be/{yt_id} ({'şimdi' if now else slot})")
    except Exception as e:  # noqa: BLE001
        log(f"✗ yükleme hatası {cid}: {e}\n{traceback.format_exc(limit=3)}")
        with state_tx() as st:
            st["clips"][cid]["status"] = "error"
            st["clips"][cid]["error"] = str(e)
            st["clips"][cid].pop("publish_at", None)


@app.post("/decide/<cid>")
def decide(cid):
    act = request.form.get("act")
    with state_tx() as st:
        c = st["clips"].get(cid) or abort(404)
        if "title" in request.form:
            c["title"] = request.form["title"].strip()[:95]
        if "description" in request.form:
            c["description"] = request.form["description"].strip()
        if act == "reject":
            c["status"] = "rejected"
        if act == "uploaded":
            c["status"] = "published" if not c.get("publish_at") or c["publish_at"] <= datetime.now().astimezone().isoformat() else "scheduled"
    if act in ("approve", "now"):
        threading.Thread(target=_do_upload, args=(cid, act == "now"), daemon=True).start()
        time.sleep(0.3)
    return redirect(request.referrer or "/")


@app.post("/reveal/<cid>")
def reveal(cid):
    import subprocess, sys
    c = load_state()["clips"].get(cid) or abort(404)
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", c.get("ready_file") or c["file"]], check=False)
    return redirect(request.referrer or "/")


@app.post("/run")
def run_now():
    if not _busy["run"]:
        def job():
            # run in a fresh process so the latest code on disk is always used
            import subprocess
            from .core import ROOT, self_cmd
            _busy["run"] = True
            try:
                subprocess.run(self_cmd("run"), cwd=str(ROOT))
            finally:
                _busy["run"] = False
        threading.Thread(target=job, daemon=True).start()
        time.sleep(0.3)
    return redirect("/")


@app.get("/api/status")
def status():
    st = load_state()
    return jsonify({k: sum(1 for c in st["clips"].values() if c["status"] == k)
                    for k in ("pending", "scheduled", "published", "error")})


def serve(open_browser: bool = True, port: int = 8642) -> None:
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
