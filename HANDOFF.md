# RICHES — Claude Code devir dokümanı

> Bu dosyayı yeni Claude Code oturumunda ilk mesaj olarak ver (ya da "HANDOFF.md'yi oku ve uygula" de).
> Sahibi: Berkay Şengüneş. İletişim dili: Türkçe.

## 1. Hedef

Şu an Berkay'ın Intel MacBook'unda çalışan **Riches** YouTube Shorts otomasyonunu bir sunucuya taşımak ve
onay panelini **https://riches.berco.world** adresinden (telefon dahil her yerden) erişilebilir yapmak.
Berkay'ın günlük tek işi: panelden kesitleri **Onayla / Reddet**. Gerisi otomatik.

## 2. Sistem ne yapıyor (çalışan, test edilmiş akış)

1. **Keşif** (`riches/source.py`): `config.yaml` → `channels` listesindeki kanalların son videolarını yt-dlp ile tarar.
   Filtreler: süre 4 dk–3 sa, yaş ≤ 365 gün, izlenme 2.000–500.000 (çok bilinen/herkesin kestiği videoları atla).
   Sıralama: günlük izlenme hızı. Kanallar: Stanford eCorner, The Knowledge Project, Greylock, Stanford GSB.
2. **Transkript**: YouTube altyazısı (json3, manuel > otomatik). Yoksa sesi indirip faster-whisper.
3. **An seçimi** (`riches/moments.py`): Claude API (`claude-sonnet-5-5`) transkripti okur; her video için ≤3 kesit
   (20–58 sn), puan 1–10 (min 7), konu dışı videoları `[]` ile atlar. Dönen alanlar: start/end, hook_title,
   **hook_text**, **teaser_start/teaser_end**, yt_title, description, tags, speaker.
4. **İndirme**: sadece kesit aralığı (yt-dlp `download_ranges`).
5. **Cold open** (`pipeline._cold_open`): kesitin en çarpıcı 2–4 sn'lik cümlesi başa eklenir, sonra kesit baştan oynar.
6. **Kelime zamanlaması**: faster-whisper `small.en`, ses ffmpeg ile decode edilir (PyAV KULLANILMAZ — yeni PyAV
   sürümünde `metadata_errors` hatası veriyor).
7. **Render** (`riches/render.py`): 1080×1920, OpenCV Haar yüz takibiyle plan bazlı kırpma (yüz yoksa bulanık arka plan),
   ASS altyazı (3 kelimelik gruplar, aktif kelime #FFD23F), açılışta büyük hook yazısı, sonra üstte sarı kutulu başlık,
   altta "Konuşmacı · Kaynak", loudnorm −14 LUFS, x264 veryfast.
   **opencv 4.x olmalı** (`<5`): 5.x'te `CascadeClassifier` yok.
8. **Onay paneli** (`riches/review.py`, Flask, port 8642): sekmeler Onay bekleyen / Planlanan / Yayınlanan /
   Reddedilen / 📊 İstatistik. "Onayla" → YouTube'a yükler (private + `publishAt`), sıradaki boş yayın saatine planlar.
   "Yeni kesit üret" butonu üretimi **ayrı süreçte** başlatır (her zaman diskteki güncel kodla).
9. **Yükleme** (`riches/youtube.py`): YouTube Data API, scope `youtube.upload`. Varsayılan kota yeterli
   (videos.insert artık ayrı 100/gün kovasında, 1 birim). Denetim (audit) GEREKMEDİ, public yükleme çalışıyor.
10. **İstatistik** (`riches/stats.py`): ayrı token, scope `youtube.readonly`, 15 dk önbellek.

Günlük ayar: her gün 13:00 (Europe/Istanbul) üretim, günde en fazla 10 kesit (`discovery.clips_per_day`),
yayın saatleri 08:30, 10:00, 11:30, 13:00, 14:30, 16:00, 17:30, 19:00, 20:30, 22:00 (`upload.max_per_day: 10`),
`upload.mode: youtube_api`.

## 3. Kod ve hesaplar

- Kod: bu zip'teki `app/` = GitHub **sngns/riches** deposundaki `app/` (public repo, sırlar YOK).
  `app/riches/core.py`: yollar; `FROZEN` (PyInstaller) desteği; `self_cmd()` alt-komut çalıştırma.
- `docs/` = Google OAuth için gerekli site (GitHub Pages: https://sngns.github.io/riches/ — ana sayfa,
  privacy.html, terms.html). Google Cloud "Branding" bu adresleri gösteriyor; silme.
- `.github/workflows/windows.yml` = Windows yedek `.exe` derlemesi (Release: `windows-latest-build`).
- Google Cloud projesi: **riches-515810** (proje no 714696409881), OAuth uygulaması "Riches Uploader",
  tip Desktop, durum **In production**, yetkili alan adı `sngns.github.io`, kanal: **riches** (marka hesabı).
- Anthropic: Claude Max planının aylık API kredisi kullanılıyor.

## 4. Sırlar — ZIP'TE YOK, Mac'ten kopyalanacak

Mac: `~/Downloads/Riches/` altında (repo'ya ASLA commit etme):
- `secrets/.env` → `ANTHROPIC_API_KEY=sk-ant-api03-...`
- `secrets/client_secret.json` → OAuth Desktop istemcisi
- `secrets/youtube_token.json` → yükleme izni (refresh token, kalıcı)
- `secrets/youtube_stats_token.json` → istatistik izni
- `data/state.json` → tüm geçmiş (işlenen videolar, kesitler, yt_id'ler, planlar) — **mutlaka taşı**,
  yoksa aynı videolar tekrar işlenir ve planlanmış saatler çakışır.

Refresh token'lar sunucuda da çalışır; yeniden OAuth akışı gerekmez.

## 5. Taşırken dikkat (bilinen riskler)

1. **YouTube veri merkezi IP'lerini engelliyor.** Sunucuda yt-dlp büyük ihtimalle "Sign in to confirm you're not a bot"
   hatası verir. Seçenekler (Berkay'a sor, kendi başına seçme):
   a) yt-dlp `--cookies` ile Berkay'ın tarayıcı çerezleri (ayrı/yan bir Google hesabı önerilir), periyodik yenileme;
   b) indirme+render'ı Mac'te (ya da evdeki bir makinede) bir **worker** olarak bırakıp, sunucuyu sadece panel + yükleme
      + zamanlama için kullanmak (en sağlam yol);
   c) konut tipi proxy (ücretli).
2. **Panel şu an kimlik doğrulamasız** (sadece localhost'tu). İnternete açmadan önce mutlaka koruma ekle
   (ör. Cloudflare Access / Caddy basic auth / basit oturum girişi). Paneldeki "Onayla" doğrudan kanala yükler.
3. **Mac'teki otomatik işi kapat** (sunucu çalışmaya başlayınca, çift üretim/çift yükleme olmasın):
   `launchctl unload ~/Library/LaunchAgents/com.riches.daily.plist`
   Panel arka planda çalışıyorsa: `pkill -f "riches.py panel-serve"`.
4. Zamanlama: launchd yerine sunucuda systemd timer / cron (13:00 Europe/Istanbul) + günlük `pip install -U yt-dlp`.
5. Kaynak: faster-whisper `small.en` CPU'da ~1–2 GB RAM; 10 kesit/gün için 2 vCPU / 4 GB yeterli. ffmpeg'de **libass** olmalı.
6. Bildirimler: Mac'te `osascript` vardı; sunucuda yok. Üretim bitince/başarısız olunca e-posta ya da
   Telegram/WhatsApp bildirimi önerilir (Berkay'a sor).
7. Panel bağlantıları `localhost:8642` varsayıyor (`riches.py`); reverse proxy arkasında 127.0.0.1:8642'ye yönlendir.
8. Telif notu: başka kanalların içeriği yeniden yayınlanıyor; kaynak + konuşmacı açıklamada yazıyor. Kısıtlama yok ama
   risk Berkay'a anlatıldı.

## 6. Önerilen sıra

1. Berkay ile netleştir: hangi sunucu (VPS sağlayıcısı / mevcut berco.world sunucusu), indirme stratejisi (5.1 a/b/c),
   panel girişi nasıl olacak, bildirim kanalı.
2. Sunucuda Python 3.12 + ffmpeg (libass) kur, `pip install -r app/requirements.txt`.
3. Sırları ve `data/state.json`'u güvenli şekilde kopyala (scp), izinleri 600 yap.
4. `python riches.py selftest` → sonra tek bir videoyla `python riches.py add <url>` dene.
5. Paneli servis olarak çalıştır (systemd), reverse proxy + HTTPS + giriş koruması, DNS: `riches` A kaydı → sunucu IP.
6. Günlük zamanlayıcı kur; Mac'teki launchd işini kapat (5.3).
7. Bir gün boyunca izle: üretim, onay, yükleme, planlı yayın, istatistik.
