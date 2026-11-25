# instapuller

Yerel uygulamalar (özellikle `autocript`) için **anonim** Instagram video/Reel indirme ve metrik çıkarma aracı.
JSON çıktılı CLI (`subprocess`) ve doğrudan import edilebilir Python API sunar; ayrı bir HTTP servisi gerektirmez.

> **Güvenlik ve Sınırlar:** Oturum/giriş kullanılmaz; **hesap riski yok, IP geçici olarak kısıtlanabilir.** Bu riski azaltmak için ardışık istekler arasında varsayılan **15 saniye** bekleme (cooldown) uygulanır.

## Kullanım ve Sorumluluk

- Bu araç **kişisel kullanım** içindir.
- Instagram'ın [Kullanım Şartları](https://help.instagram.com/581066165581870)'na ve içeriklerin **telif haklarına** uymak tamamen kullanıcının sorumluluğundadır. Hakkınız olmayan içeriği indirmeyin, yeniden yayımlamayın.
- Araç **hesap ya da çerez kullanmaz**: giriş yapılmaz, oturum bilgisi okunmaz; yalnızca herkese açık gönderilere erişilebilir.

## Kurulum

```bash
uv sync
```

> Süre ölçümü için sistemde `ffprobe` bulunmalı veya `--ffprobe-bin` / `INSTAPULLER_FFPROBE_BIN` ile yolu verilmelidir.

## Cooldown ve State Dosyası

- Ardışık istekler arasında en az **15 sn** beklenir. `--cooldown` / `cooldown_s` 15'in altına **inemez**: Python API'de düşük değer sessizce 15'e yükseltilir, CLI'da `--cooldown` 15'ten küçükse `ARG_GECERSIZ` döner. Daha büyük değerlere izin verilir.
- Bu kural **süreçler arası** korunur: state dosyasının yanındaki `<state>.lock` dosyası üzerinde işletim sistemi kilidi alınır, her süreç kendine bir zaman yuvası ayırır ve kilit dışında uyur. Aynı anda başlayan 3 süreç 15 sn arayla sıraya girer.
- Varsayılan state dosyası çalışma klasöründen bağımsızdır. Öncelik sırası:
  1. `--state-file` (CLI) / `state_file=` (API)
  2. `INSTAPULLER_STATE_FILE` ortam değişkeni
  3. Windows: `%LOCALAPPDATA%\instapuller\rate_limit.json`; diğer sistemler: `~/.local/state/instapuller/rate_limit.json`
- `keep_video=false` iken indirilen dosyayı silmek (`cleanup`) çağıranın sorumluluğundadır.

## `yt-dlp` Güncelleme (Tek Satır)

Instagram yanıt biçimi değiştiğinde `IG_ARAC_GUNCEL_DEGIL` hatası alırsanız `yt-dlp` sürümünü şu tek komutla güncelleyebilirsiniz:

```bash
uv add yt-dlp==$(uv pip list --outdated --format=json | python -c "import json,sys; p=[x for x in json.load(sys.stdin) if x['name']=='yt-dlp']; print(p[0]['latest_version'] if p else '2026.11.23')")
```

Veya belirli bir sürüme sabitlemek için:

```bash
uv add yt-dlp==<YENI_SURUM>
```

## CLI Kullanımı (JSON Çıktılı)

```bash
# Varsayılan: 15 sn cooldown (--wait), video saklama kapalı (geçici dosya)
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --out-dir "./tmp" --ffprobe-bin "C:\path\to\ffprobe.exe"

# Videoyu kalıcı olarak sakla
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --out-dir "./downloads" --keep-video

# Cooldown dolmadıysa beklemeden anında IG_COOLDOWN döndür (retry_after_s ile)
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --no-wait

# Link `-` ile başlayabiliyorsa (ör. kullanıcı girdisi) URL'den önce `--` verin
uv run instapuller fetch --out-dir "./tmp" -- "<link>"

# Transkript sonrası geçici videoyu sil
uv run instapuller cleanup "C:\path\to\video.mp4"
```

## Python API Kullanımı

```python
from instapuller import fetch_video, cleanup_file

result = fetch_video(
    "https://www.instagram.com/reel/Chunk8-jurw/",
    out_dir="./tmp",
    keep_video=False,   # Varsayılan kapalı
    wait=True,          # Varsayılan --wait (15 sn cooldown)
    max_duration_s=185, # varsayılan 185 sn
    max_filesize_mb=200 # 200 MB sınırı
)
print(result.to_dict())

if not result.keep_video:
    cleanup_file(result.video_path)
```

## JSON Çıktı Sözleşmesi (`autocript` Uyumlu)

Başarılı yanıt (`exit code = 0`):

```json
{
  "ok": true,
  "video_path": "C:\\...\\Chunk8-jurw_a1b2c3d4.mp4",
  "keep_video": false,
  "meta": {
    "kind": "instagram",
    "url": "https://www.instagram.com/reel/Chunk8-jurw/",
    "platform_id": "Chunk8-jurw",
    "title": "Video açıklaması...",
    "author": "Instagram",
    "published_at": "2022-08-26T15:53:51Z",
    "duration_s": 28.4,
    "stats": {
      "views": null,
      "likes": 972107,
      "comments": 15704
    }
  }
}
```

Hata yanıtı (`exit code = 1`):

```json
{
  "ok": false,
  "error": {
    "code": "IG_ERISILEMEDI",
    "message": "Bu gönderiye erişilemedi (gizli, silinmiş ya da giriş gerekiyor olabilir). Videoyu dosya olarak yükleyin.",
    "upload_fallback": true,
    "detail": "..."
  }
}
```

`IG_COOLDOWN` hatası ayrıca `retry_after_s` (saniye, sayı) alanı taşır; `--no-wait` ile çağıran bu kadar sonra tekrar deneyebilir:

```json
{"ok": false, "error": {"code": "IG_COOLDOWN", "message": "...", "upload_fallback": true, "retry_after_s": 10.75}}
```

`message` son kullanıcıya gösterilebilir sade metindir; teknik ayrıntılar (araç adı, yol, ham hata) `detail` alanındadır.

## Çıkış Kodları

| Kod | Anlam |
| :---: | :--- |
| `0` | Başarı (stdout'ta tek satır JSON, `"ok": true`) |
| `1` | Hata (stdout'ta tek satır JSON, `"ok": false`); argüman hataları dahil |
| `130` | Kullanıcı iptali (Ctrl+C); `BEKLENMEYEN_HATA` JSON'u yazılır |

**`-h` / `--help` JSON üretmeyen tek istisnadır**: yardım metni basar ve `0` ile çıkar. Başka hiçbir durumda stdout'ta JSON dışı çıktı olmaz; geçersiz/eksik argüman, bilinmeyen seçenek ve eksik alt komut `ARG_GECERSIZ` JSON'u (çıkış `1`) döner.

> **autocript notu:** Link kullanıcıdan geldiği için `-` ile başlayabilir (ör. `-h`) ve seçenek sanılabilir. Çağırırken URL'den önce `--` geçirin: `instapuller fetch [seçenekler] -- <url>`. Böyle bir girdi `LINK_GECERSIZ` döner, yardım metni basmaz.

## Hata Kodları

Kod listesi tek bir tabloda tutulur (`instapuller/models.py` → `ERROR_CODES`); tabloda olmayan kodla `PullError` oluşturulamaz.

| Kod | Ne zaman | `upload_fallback` |
| :--- | :--- | :---: |
| `LINK_GECERSIZ` | İzin listesinde olmayan site, geçersiz/desteklenmeyen link | `true` |
| `IG_ERISILEMEDI` | Boş medya yanıtı, gizli/silinmiş gönderi veya giriş duvarı | `true` |
| `IG_RATE_LIMIT` | Instagram IP tabanlı geçici hız sınırı | `true` |
| `IG_ARAC_GUNCEL_DEGIL` | Instagram yanıtı ayrıştırılamadı (`yt-dlp` güncellemesi gerekebilir; ayrıntı `detail`'de) | `true` |
| `IG_VIDEO_DEGIL` | Çoklu gönderi (carousel) veya video içermeyen gönderi | `true` |
| `IG_COOLDOWN` | `--no-wait` verildi ve 15 sn bekleme dolmadı (`retry_after_s` içerir) | `true` |
| `DOSYA_BUYUK` | Video boyutu sınırı aşıyor (varsayılan 200 MB) | `true` |
| `VIDEO_UZUN` | Video süresi sınırı aşıyor (varsayılan **185 sn**; dosya silinir) | `true` |
| `DOSYA_BOZUK` | İndirilen dosya okunamadı veya süresi geçersiz | `true` |
| `KAYNAK_GECICI` | Ağ/aktarım hatası ve video dosyasının oluşmadığı diğer geçici hatalar | `true` |
| `FFPROBE_YOK` | `ffprobe` bulunamadı (kurulum eksik) | `false` |
| `ARG_GECERSIZ` | Geçersiz/eksik komut satırı argümanı, `--cooldown` < 15 | `false` |
| `SILME_HATASI` | `cleanup` dosyayı silemedi | `false` |
| `BEKLENMEYEN_HATA` | Yakalanmamış istisna (`detail`: `<İstisnaTürü>: <mesaj>`), iptal, kilit alınamadı | `false` |

## Davranış Notları

- **Boyut sınırı indirme sırasında uygulanır** (varsayılan 200 MB): `Content-Length` bildirilse de bildirilmese de, sınır aşıldığı anda indirme kesilir ve `DOSYA_BUYUK` döner; yarım dosya silinir.
- **Format seçimi:** `format` değeri `mp4/best`'tir; yalnızca ses ve görüntüsü **tek dosyada** olan formatlar seçilir, ffmpeg ile birleştirme yapılmaz. Yalnızca DASH (ayrı ses/görüntü) sunulan gönderiler `IG_VIDEO_DEGIL` döner.
- **Yalnızca Instagram çıkarıcısı** yüklenir; başka bir çıkarıcıdan yanıt gelirse `IG_ARAC_GUNCEL_DEGIL` döner.
- **Eski staging klasörleri:** her `fetch` başında hedef klasördeki 1 saatten eski `ip_*` klasörleri (öldürülmüş önceki çağrılardan kalanlar) sessizce silinir.
- **`cleanup`** yalnızca instapuller'ın ürettiği geçici adları (`<kod>_<8 hex>.<mp4|mov|webm|mkv|m4v>`) siler; başka dosyalar için `SILME_HATASI` döner. `--keep-video` ile saklanan dosyalar bu adı taşımaz, bu komutla silinmez.
- **Link denetimi:** kullanıcı bilgisi (`kullanici@...`), 80/443 dışı port ve yalnızca `-`/`_` içeren kodlar `LINK_GECERSIZ` döner.

- **yt-dlp uyarıları (tanı):** yt-dlp'nin `warning`/`error` satırları (en fazla 10 satır, her biri en çok 200 karakter, yerel yollar `[path]` ile maskeli) şu iki yere gider: (1) stderr'e `instapuller: yt-dlp uyarısı: ...` biçiminde (UTF-8); (2) hata olursa `detail` alanının sonuna ` | yt-dlp uyarıları: ...` olarak eklenir (orijinal hata metni öncelikli, toplam 500 karakteri aşmaz). Başarıda uyarılar yalnızca stderr'e yazılır; JSON çıktısı değişmez. **stdout'a asla uyarı yazılmaz** (yalnızca tek satır JSON). stderr yalnızca tanı içindir, `autocript` onu ayrıştırmamalı ama günlüğe (log) yazmalıdır. `INSTAPULLER_QUIET=1` stderr yazımını kapatır (`detail` eklemesi sürer).

## Yeni Site Ekleme (Genişletilebilirlik)

Şu anda izin listesinde (`instapuller/sites/__init__.py` → `ALLOWED_SITES`) yalnızca `instagram.com` yer alır. İleride yeni bir platform (ör. YouTube) eklemek için `instapuller/sites/` altına `SiteAdapter` protokolünü uygulayan yeni bir modül ekleyip `ALLOWED_SITES` sözlüğüne kaydetmek yeterlidir.

## Testler

```bash
uv run pytest
```

Video üreten testler için ffmpeg gerekir: `INSTAPULLER_TEST_FFMPEG`, `PATH` ya da `INSTAPULLER_FFPROBE_BIN` ile aynı klasördeki `ffmpeg(.exe)` aranır; bulunamazsa o testler atlanır.
