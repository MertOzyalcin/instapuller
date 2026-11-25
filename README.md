# instapuller

Yerel uygulamalar (özellikle `autocript`) için **anonim** Instagram video/Reel indirme ve metrik çıkarma aracı.
JSON çıktılı CLI (`subprocess`) ve doğrudan import edilebilir Python API sunar; ayrı bir HTTP servisi gerektirmez.

> **Güvenlik ve Sınırlar:** Oturum/giriş kullanılmaz; **hesap riski yok, IP geçici olarak kısıtlanabilir.** Bu riski azaltmak için ardışık istekler arasında varsayılan **15 saniye** bekleme (cooldown) uygulanır.

## Kurulum

```bash
uv sync
```

> Süre ölçümü için sistemde `ffprobe` bulunmalı veya `--ffprobe-bin` / `INSTAPULLER_FFPROBE_BIN` ile yolu verilmelidir.

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

# Cooldown dolmadıysa beklemeden anında IG_COOLDOWN döndür
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --no-wait

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
    max_duration_s=180, # 3 dakika sınırı
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

## Hata Kodları

| Kod | Açıklama | `upload_fallback` |
| :--- | :--- | :---: |
| `LINK_GECERSIZ` | İzin listesinde olmayan site veya geçersiz link biçimi | `true` |
| `IG_COOLDOWN` | `--no-wait` verildiğinde 15 sn bekleme süresi dolmamış (`retry_after_s` içerir) | `true` |
| `IG_ERISILEMEDI` | Boş medya yanıtı, gizli/silinmiş gönderi veya giriş duvarı | `true` |
| `IG_RATE_LIMIT` | Instagram IP tabanlı geçici hız sınırı | `true` |
| `IG_VIDEO_DEGIL` | Çoklu gönderi (carousel) veya video içermeyen gönderi | `true` |
| `DOSYA_BUYUK` | Video boyutu sınırı aşıyor (varsayılan 200 MB) | `true` |
| `VIDEO_UZUN` | Video süresi sınırı aşıyor (varsayılan 180 sn; dosya silinir) | `true` |
| `IG_ARAC_GUNCEL_DEGIL` | Beklenmeyen ayrıştırma hatası (`yt-dlp` güncellemesi gerekir) | `true` |
| `FFPROBE_YOK` | `ffprobe` yürütülebilir dosyası bulunamadı | `true` |
| `DOSYA_BOZUK` | İndirilen dosya `ffprobe` ile okunamadı | `true` |
| `IG_INDIRME_HATASI` | Diğer ağ veya indirme hataları | `true` |

## Yeni Site Ekleme (Genişletilebilirlik)

Şu anda izin listesinde (`instapuller/sites/__init__.py` → `ALLOWED_SITES`) yalnızca `instagram.com` yer alır. İleride yeni bir platform (ör. YouTube) eklemek için `instapuller/sites/` altına `SiteAdapter` protokolünü uygulayan yeni bir modül ekleyip `ALLOWED_SITES` sözlüğüne kaydetmek yeterlidir.

## Testler

```bash
uv run pytest
```
