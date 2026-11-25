# instapuller bağımsız inceleme — opus-001

**Karar: Küçük düzeltmelerle hazır. Entegrasyondan önce kısa bir düzeltme turu gerekiyor, çünkü 15 sn bekleme kuralı aynı anda çalışan süreçlerde tutmuyor (autocript varsayılan olarak 3 işi paralel çalıştırıyor). Diğer bulgular yerel ve küçük kapsamlı; yeniden tasarım gerekmiyor.**

- İncelenen: `5724b7f` (master, temiz). yt-dlp 2026.11.23, Python 3.14.7, Windows 11.
- Bulgu sayısı: **1 kritik, 9 önemli, 9 küçük.**
- Birim testleri: `uv run pytest` → **34 passed**.
- Instagram'a hiç istek atılmadı. Ölçümlerin hepsi ağsız yapıldı: gerçek yt-dlp, yerel `file://` kaynağı ve yalnızca 127.0.0.1'de çalışan bir HTTP sunucusuyla. Test videoları autocript'in `tools\ffmpeg` kopyasıyla scratchpad'de üretildi. Repoda ve autocript'te hiçbir dosya değişmedi.

## Talimat ve sözleşme notu (başlamadan söylenmesi gereken)

Sözleşmenin iki kaynağı birbiriyle çelişiyor. İnceleme kararlaştırılan davranışı esas aldı, ancak entegrasyondan önce bu çelişkinin kapatılması gerekiyor:

- `C:\autocript\docs\superpowers\specs\2026-11-24-faz1-design.md` §6.4'te `IG_ERISILEMEDI`, `IG_RATE_LIMIT`, `IG_ARAC_GUNCEL_DEGIL` ve `IG_COOLDOWN` yok. Spec, hız sınırı ve ağ hataları için `KAYNAK_GECICI` kodunu öngörüyor. Ayrıca spec'e göre `DOSYA_BUYUK` sınırı 500 MB, `VIDEO_UZUN` ise "185 sn tolerans" ile tanımlı (`autocript/config.py:34` → `max_duration_s = 185.0`). Kararlaştırılan liste ise 200 MB ve 180 sn diyor.
- Kararlaştırılan kod listesinde `VIDEO_UZUN` sayılmamış, yalnızca davranış tarifinde geçiyor. Kodun ürettiği `IG_INDIRME_HATASI`, `FFPROBE_YOK`, `DOSYA_BOZUK` ve `SILME_HATASI` ise hiçbir listede yok (bkz. Ö5).

---

## Kritik

### K1. 15 sn bekleme, eşzamanlı süreçlerde korunmuyor; üçüncü süreç çöküyor
**Dosya:** `instapuller/rate_limit.py:60-79` (oku-hesapla-uyu-yaz), `:44-48` (ortak `.tmp` + `replace`)

**Ne bozuluyor:** Kilit yok. Aynı anda başlayan süreçler aynı `last_request_at` değerini okuyor, aynı kalan süreyi hesaplıyor ve **aynı anda** geçiyor. Hepsi aynı `rate_limit.tmp` dosyasına yazdığı için Windows'ta biri `replace` sırasında `PermissionError [WinError 32]` alıyor. CLI bunu "Beklenmeyen hata" olarak `IG_INDIRME_HATASI` koduyla döndürüyor.

**Ölçüm** (3 ayrı süreç, aynı state dosyası, cooldown 15):
- Tur 1: iki süreç aynı anda, beklemeden geçti; üçüncüsü WinError 32 ile çöktü.
- Tur 2: üçü de 13,46 sn bekledi ve **aynı anda** geçti.

autocript'te `job_concurrency = 3` (`autocript/config.py:35`). Kullanıcı üç Reel linkini art arda yapıştırırsa Instagram'a üç istek aynı saniyede gider. Bu, CLAUDE.md'deki "en az 15 sn beklenir, arka arkaya deneme yapılmaz" kuralını doğrudan çiğner.

**Önerilen düzeltme:**
- İşletim sistemi düzeyinde özel bir kilit kullan: kalıcı bir `.lock` dosyası üzerinde `msvcrt.locking` ya da `os.open(O_CREAT|O_EXCL)` ile kısa aralıklarla yeniden deneme.
- Kilit tutulurken bir "yuva" ayır: `slot = max(now, last + cooldown)`. `slot` değerini yaz, kilidi bırak, ardından `slot` anına kadar uyu. Böylece kilit uyku boyunca tutulmaz ve paralel süreçler 15 sn arayla sıraya girer.
- `--no-wait` verildiyse ve `slot > now` ise yuva ayırmadan `IG_COOLDOWN` dön.
- Geçici dosyanın adı süreçten süreç farklı olsun (pid ya da rastgele ek). `replace` için kısa bir yeniden deneme ekle.
- Gerçek çok süreçli bir test ekle.

---

## Önemli

### Ö1. Varsayılan state dosyası çalışma klasörüne göre çözülüyor
**Dosya:** `instapuller/downloader.py:22` (`Path(".state") / "rate_limit.json"`), `:106-108`

**Ne bozuluyor:** autocript `--state-file` vermezse state dosyası autocript'in çalışma klasöründe (`C:\autocript\.state\`) oluşur. Bu, "instapuller bağımsızdır" kuralına aykırı. Ayrıca elle yapılan bir CLI denemesi başka bir klasörden çalıştırıldığında ayrı bir sayaç tutar ve iki çağrı birbirini görmez. Bu da 15 sn kuralını sessizce deler.

**Önerilen düzeltme:** Varsayılanı mutlak ve kullanıcıya özgü bir yol yap, örneğin `%LOCALAPPDATA%\instapuller\rate_limit.json`. `INSTAPULLER_STATE_FILE` ortam değişkeni de kabul edilsin. Bu yolu README'de belgele.

### Ö2. 200 MB sınırı Content-Length yoksa indirme sırasında uygulanmıyor
**Dosya:** `instapuller/downloader.py:132` (`max_filesize`), `:173` (indirme sonrası kontrol). yt-dlp tarafı: `.venv/Lib/site-packages/yt_dlp/downloader/http.py:216-227`

**Ne bozuluyor:** yt-dlp `max_filesize` değerini yalnızca sunucu `Content-Length` gönderdiğinde, indirme başlamadan kontrol ediyor. Akış (chunked) yanıtta sınır yok. Dosya sonuna kadar iniyor ve ancak `:173`'te reddediliyor. Sunucu boyut bildirmezse disk ve süre sınırsız harcanabilir.

**Ölçüm:** 3 MB dosya, 1 MB sınır, chunked yerel sunucu. Dosyanın tamamı indi, ardından `DOSYA_BUYUK` döndü.

**Önerilen düzeltme:** yt-dlp `progress_hooks` içinde `downloaded_bytes` (ve varsa `total_bytes` / `total_bytes_estimate`) sınırı aştığı anda özel bir istisna fırlat. Bu istisnayı `map_error`'dan önce yakalayıp `DOSYA_BUYUK` koduna eşle. Kanca hem Content-Length olan hem olmayan durumu kapsar.

### Ö3. Boyut sınırına takılan indirme çoğu zaman yanlış kodla dönüyor
**Dosya:** `instapuller/downloader.py:156-170`

**Ne bozuluyor:** Content-Length sınırı aşınca yt-dlp istisna fırlatmıyor. Yalnızca ekrana bir mesaj yazıp `False` döndürüyor ve dosya oluşmuyor. Kod bu durumda `DOSYA_BUYUK` dönmek için bilgi sözlüğünde `filesize` ya da `filesize_approx` alanını arıyor, ancak Instagram formatlarında bu alanlar genellikle yok. Sonuç: `IG_INDIRME_HATASI` ve "boyut sınırına takılmış **veya** boş dönmüş olabilir" mesajı.

**Ölçüm:** `filesize` alanı olmadan 1 MB sınırla → `IG_INDIRME_HATASI`. Alan varken → `DOSYA_BUYUK`.

**Önerilen düzeltme:** Ö2'deki kanca bu sorunu da çözer. Ek olarak, yt-dlp'nin "File is larger than max-filesize" satırını `_SilentLogger.debug` içinde yakalayıp bir bayrak kaldırmak yedek yol olarak kullanılabilir.

### Ö4. Hata sınıflandırması alt dize eşleşmesine dayanıyor; gerçek mesajlar yanlış koda düşüyor
**Dosya:** `instapuller/sites/instagram.py:120-227`

**Ne bozuluyor** (yt-dlp'nin gerçek mesaj kalıplarıyla ölçüldü):

| Mesaj | Dönen kod | Olması gereken |
|---|---|---|
| `Unable to download JSON metadata: <urlopen error [Errno 11001] getaddrinfo failed>` (internet yok) | `IG_ARAC_GUNCEL_DEGIL` | ağ hatası |
| `... JSON metadata: HTTP Error 500` | `IG_ARAC_GUNCEL_DEGIL` | ağ / geçici |
| Kısa kodunda `429` geçen bir gönderinin herhangi bir hatası (`[Instagram] Cx429zAB: ...`) | `IG_RATE_LIMIT` | — |
| Kısa kodunda `403` geçen bir gönderinin hatası | `IG_ERISILEMEDI` | — |
| `Requested format is not available` | `IG_ERISILEMEDI` | `IG_VIDEO_DEGIL` ya da `IG_ARAC_GUNCEL_DEGIL` |

- İlk iki satırda kullanıcıya internet yokken "yt-dlp'yi güncelleyin" deniyor. Nedeni, `json` anahtar kelimesinin "JSON metadata" ifadesine takılması.
- Kısa kodlu satırlarda `429`, `403`, `404` ve `401` sayıları bağlamdan bağımsız aranıyor. yt-dlp her hatanın başına `[Instagram] <kısa kod>:` ekliyor.

**Önerilen düzeltme:**
- Önce istisna türüne bak: `yt_dlp.utils.DownloadError.exc_info`, `ExtractorError` ve onun `cause` alanı, `yt_dlp.networking.exceptions.HTTPError.status`, `TransportError`, `TimeoutError`.
- Metne başvurmak gerekirse önce `ERROR: [Instagram] <id>:` önekini at. HTTP kodlarını yalnızca `HTTP Error (\d{3})` kalıbıyla al.
- Ağ hataları için ayrı bir kod kullan (Ö5'teki karara göre).
- Sınıflandırma testlerini yt-dlp'nin gerçek mesaj kalıplarıyla yaz. Örneğin "redirected to the login page ... rate-limit" mesajı şu an `IG_RATE_LIMIT` dönüyor. Bu kabul edilebilir, ama bir testle sabitlenmeli.

### Ö5. Kararlaştırılmamış hata kodları ve tutarsız `upload_fallback`
**Dosya:** `instapuller/__main__.py:121-129`, `:136-144`; `instapuller/sites/instagram.py:221-227`; `instapuller/probe.py:35-40`, `:68-99`; `instapuller/downloader.py:165-170`

**Ne bozuluyor:**
- Kod, kararlaştırılan listede olmayan dört kod üretiyor: `IG_INDIRME_HATASI`, `FFPROBE_YOK`, `DOSYA_BOZUK` ve `SILME_HATASI`. Sonuncusu README tablosunda da yok.
- Her beklenmeyen istisna `IG_INDIRME_HATASI` ve `upload_fallback=true` ile dönüyor. autocript spec'i ise beklenmeyen istisna için `BEKLENMEYEN_HATA` ve `upload_fallback=False` istiyor. Örneğin K1'deki `PermissionError` kullanıcıya "dosya yükle" diye gösterilir.
- `IG_COOLDOWN` hatası `SourceError`'da olmayan bir `retry_after_s` alanı taşıyor.

**Önerilen düzeltme:** Kod listesini tek bir yerde kesinleştir. Ağ ve indirme hatası için bir kod seç (ya `IG_INDIRME_HATASI` ya autocript'teki `KAYNAK_GECICI`). İç ve beklenmeyen hatayı ondan ayır (`BEKLENMEYEN_HATA`, `upload_fallback=false`). `FFPROBE_YOK` bir kurulum hatası olduğu için onun da `upload_fallback=false` olması daha doğru. Listeyi README'ye ve autocript spec §6.4'e aynı biçimde işle.

### Ö6. CLI her durumda JSON üretmiyor; çıkış kodları belgeyle uyuşmuyor
**Dosya:** `instapuller/__main__.py:99-101` (argparse), `:103-129` (`KeyboardInterrupt` yakalanmıyor)

**Ölçüm:**
- `--cooldown abc` gibi geçersiz bir argüman → **rc=2**, stdout boş, kullanım metni stderr'e gidiyor.
- `fetch -h` (ya da `-` ile başlayan herhangi bir "link") → **rc=0**, stdout'a 26 satır yardım metni yazılıyor; JSON yok. autocript bunu "başarılı ama ayrıştırılamadı" olarak görür.
- Ctrl+C ya da `KeyboardInterrupt` → Python traceback'i stderr'e gidiyor, JSON yok.

Belge "hata = exit 1" diyor.

**Önerilen düzeltme:**
- `ArgumentParser.error`'ı ezip `{"ok": false, "error": {"code": "ARG_GECERSIZ", ...}}` yaz ve sabit bir kodla çık.
- `fetch` için yardımı JSON dışı tek istisna olarak belgele ya da kapat.
- `main` içinde `KeyboardInterrupt`'ı yakalayıp JSON yaz ve 130 ile çık.
- Çıkış kodu tablosunu (0, 1, 2, 130) README'ye ekle.
- autocript tarafı için: URL'den önce `--` geçirmek bu sorunu çözüyor. `fetch -- -h` denendi → `LINK_GECERSIZ`.

### Ö7. Süreç öldürülürse geçici dosyalar kalıyor; varsayılan geçici klasör hiç temizlenmiyor
**Dosya:** `instapuller/downloader.py:113-125`, `:207-212`

**Ne bozuluyor:**
- Hata ve `KeyboardInterrupt` durumlarında staging klasörü `finally` ile siliniyor. Bu, VIDEO_UZUN ve DOSYA_BUYUK için ölçülerek doğrulandı.
- Ancak autocript zaman aşımında alt süreci öldürürse (Windows'ta `TerminateProcess`) `finally` çalışmıyor. Yarım `ip_<kod>_*` klasörü ve `.part` dosyası hedef klasörde kalıyor.
- `--out-dir` verilmezse saklanmayan videolar bile `%TEMP%\instapuller` içine bırakılıyor. Silme işi tamamen çağırana kalıyor; çağıran çökerse dosyalar birikir.

**Önerilen düzeltme:**
- Her çağrının başında hedef klasördeki eski `ip_*` klasörlerini sil (örneğin 1 saatten eski olanları).
- README'de "`keep_video=false` iken dosyayı silmek çağıranın sorumluluğu" cümlesini açıkça yaz.
- autocript tarafında `--out-dir` iş klasörünü göstersin (bkz. Entegrasyonda dikkat, madde 3).

### Ö8. Anonim erişimi etkileyen `curl_cffi` bağımlılığı kurulu değil
**Dosya:** `pyproject.toml:11-13` (`yt-dlp==2026.11.23`, ek paket yok); `uv.lock` içinde `curl_cffi` yok. yt-dlp tarafı: `.venv/Lib/site-packages/yt_dlp/extractor/instagram.py:471-488`

**Ne bozuluyor:** yt-dlp'nin Instagram çıkarıcısı anonim GraphQL isteğini `impersonate=True` ile atıyor. İkinci sorguyu ise `if self._can_impersonate else None` koşuluyla, tarayıcı taklidi yoksa **hiç atmıyor**. Bu ortamda taklit kütüphanesi kurulu olmadığı için o yol kapalı. Uyarı da `_SilentLogger` tarafından yutuluyor. Bu sorun ağsız doğrulanamadı, ancak CLAUDE.md'deki açık risk ("anonim modda Reel indirebiliyor mu?") ile doğrudan ilgili.

**Önerilen düzeltme:** 5 Reel'lik deneme `yt-dlp` ve `yt-dlp[default,curl-cffi]` ile ayrı ayrı yapılsın. Başarı farkı varsa ek paket sabit sürümle eklensin. `_SilentLogger.warning` çağrıları stderr'e ya da `detail`'e aktarılsın; şu an tanı için gereken her uyarı kayboluyor.

### Ö9. Testler sözleşmenin riskli kısımlarını doğrulamıyor
**Dosya:** `tests/test_contract.py:17-36` (FakeYDL), `:152-158`; `tests/test_errors.py`; `tests/test_rate_limit.py`

**Değerlendirme:**
- Gerçek mantığı doğrulayan testler: `test_parser.py` (16 test), `test_errors.py` içindeki sanitize testleri ve `test_rate_limit.py`. Bunlar mock değil, gerçekten bir şey doğruluyor.
- `test_contract.py` sahte bir yt-dlp kullanıyor. Bu sahte nesne dosyayı her koşulda yazıyor. Bu yüzden instapuller'ın kendi akışını (temizlik, son kontroller, sözleşme alanları) doğruluyor, ancak yt-dlp seçeneklerinin gerçek etkisini hiç sınamıyor: `max_filesize`'ın sessiz davranışı, `format` seçimi, logger'ın stdout'u temiz tutması. Ö2 ve Ö3 bu yüzden testlerden geçmiş.
- `map_error` testleri elle yazılmış mesajlar kullanıyor; gerçek yt-dlp mesaj kalıpları yok. Ö4 bu yüzden görünmemiş.
- Rate limit testleri tek süreçli ve sahte saatli. K1 bu testlerle görünemez.
- CLI için tek bir test var (izinsiz site).
- Eksik testler: çok süreçli cooldown, stdout'un tek satır ve geçerli JSON olması, alt süreç üzerinden Türkçe yol, argparse hatası, `instagram.com.kotu.com` ve `kullanici@` saldırıları, iptal sonrası temizlik.

**Önerilen düzeltme:**
- Bu raporda yapılan ağsız ölçümleri kalıcı teste çevir: gerçek `YoutubeDL`, `enable_file_urls` ile yerel dosya, `127.0.0.1` üzerinde chunked sunucu ve `subprocess` ile CLI çağrısı.
- `map_error` için yt-dlp kaynağından alınmış mesajlarla parametreli bir test yaz.

---

## Küçük

**S1. `--cooldown` ve `cooldown_s` 15'in altına (0 dahil) indirilebiliyor** — `__main__.py:62-67`, `rate_limit.py:28`, `:56-58`. CLAUDE.md "en az 15 sn" diyor. Düzeltme: alt sınırı 15'e sabitle. Testler için yalnızca Python API'de açık bir `_unsafe_cooldown_for_tests` parametresi bırak.

**S2. Saat geri alınırsa uzun uyku** — `rate_limit.py:65-75`. State dosyasındaki zaman gelecekteyse `remaining` 15 sn'yi aşar ve saatlerce uyuyabilir. Düzeltme: `remaining = min(remaining, cooldown_s)`. Mümkünse süreç içi beklemeyi `time.monotonic` ile ölç.

**S3. `cleanup` komutu verilen her dosyayı siliyor** — `cleanup.py:8-16`, `__main__.py:131-134`. `instapuller cleanup C:\herhangi\dosya.txt` dosyayı siler. Düzeltme: yalnızca instapuller'ın ürettiği adlarla eşleşen dosyaları (`<kod>_<hex>.<ext>`) ya da bilinen hedef klasörün içindekileri sil.

**S4. Site listesi atlatılamıyor, ancak gevşek** — `sites/__init__.py:62-65`, `sites/instagram.py:64`. Denenenler:
- **Reddedildi:** `instagram.com.kotu.com`, `kotu.com/instagram.com/...`, `instagram.com@kotu.com`, `INSTAGRAM.COM.`, `www.www.`, `\@`, IDN benzerleri, `ftp/javascript://`, `//instagram.com`.
- **Kabul edildi ama zararsız:** `kullanici@instagram.com`, `a:b@kotu.com@instagram.com` (gerçek host instagram.com), `:8080` / `:99999` portları.
- Zararsız olmasının nedeni: istek her zaman `https://www.instagram.com/<tür>/<kod>/` olarak **kısa koddan yeniden kuruluyor** (`instagram.py:83/90/97`). Bu yüzden kullanıcı bilgisi, port, sorgu ve parça hiçbir zaman ağa gitmiyor.
- Düzeltme: açıklık için kullanıcı bilgisi ve standart dışı portu reddet. `--------` gibi yalnızca tireden oluşan kodları da reddet.

**S5. Çıkarım sonrası site listesi uygulanmıyor** — `downloader.py:129-152`. yt-dlp bir `url` ya da `url_transparent` sonucu dönerse `process_ie_result` başka bir çıkarıcıya (generic dahil) geçebilir. Bugünkü Instagram çıkarıcısı bunu yapmıyor; bu bir derinlemesine savunma eksiği. Düzeltme: `ydl_opts` içine `allowed_extractors: ["Instagram"]` ekle. `extract_info` sonrası `info["extractor_key"]` değerinin `Instagram` olduğunu doğrula. Medyanın CDN host'una yönlendirmesi beklenen bir davranış.

**S6. Hedef klasör yolu yt-dlp şablonuna giriyor** — `downloader.py:130`. Klasör adında `%(` geçerse yt-dlp bunu alan olarak genişletiyor. Ölçüm: `C:\x\a %(title)s b\...` yolunda `title` değeri yola girdi. Yalnız `%50` sorun çıkarmıyor. Düzeltme: `paths={"home": str(staging_dir)}` kullan ve `outtmpl` yalnızca `"<kod>.%(ext)s"` olsun.

**S7. Birden çok dosya oluşursa rastgele biri seçiliyor** — `downloader.py:156`, `:172`. `iterdir()` sırası tanımsız; `.ytdl`, `.temp` ya da `.part-Frag*` dosyaları süzülmüyor. Düzeltme: yt-dlp'nin döndürdüğü `requested_downloads[0]["filepath"]` değerini kullan.

**S8. `format: "mp4/best"` yalnızca ses ve görüntüsü tek dosyada olan formatları seçiyor; ffmpeg yolu yt-dlp'ye verilmiyor** — `downloader.py:131`. Instagram yalnızca DASH (ayrı ses ve görüntü) sunarsa "Requested format is not available" hatası alınır ve Ö4 nedeniyle `IG_ERISILEMEDI` döner. Şimdilik doğru tercih, çünkü birleştirme ffmpeg gerektiriyor. Ancak bu durum belgelenmeli. İleride `ffmpeg_location` parametresi `--ffprobe-bin` gibi dışarıdan alınabilir.

**S9. README güncelleme tek satırı kırılgan ve hata mesajı son kullanıcıya uymuyor** — `README.md` "yt-dlp Güncelleme"; `instagram.py:213-216`, `downloader.py:55-57`. Tek satırlık komut, güncel sürüm bulunamazsa sabit `2026.11.23`'e düşüyor. `IG_ARAC_GUNCEL_DEGIL` mesajı autocript kullanıcısına "README'deki komutu çalıştırın" diyor. Düzeltme: kullanıcı mesajını "Araç güncel değil; videoyu dosya olarak yükleyin" yap. Komut bilgisini `detail` alanına ya da KURULUM.md'ye taşı.

### Olumlu tespitler (kontrol edildi, sorun yok)
- **stdout temizliği:** Gerçek yt-dlp ile başarı ve hata yollarında stdout **tek satır, geçerli JSON**. `_SilentLogger` ve `quiet` birlikte ilerleme ve uyarı çıktısını tamamen kesiyor. yt-dlp, logger tanımlıysa `to_screen` çıktısını `logger.debug`'a yönlendiriyor.
- **Türkçe yol:** `çıktı klasörü ĞÜŞİÖÇ ığüşöç` adlı hedef klasör ve Türkçe başlık ya da yazar alanları sorunsuz çalıştı. Dosya oluştu, JSON UTF-8 olarak doğru ayrıştı. `_emit` stdout'u UTF-8'e çeviriyor. **Not:** Aynı çıktı cp1254 ile çözülünce `UnicodeDecodeError` veriyor (bkz. entegrasyon madde 2).
- **Dosya adı güvenliği:** Dosya adına ve geçici klasör önekine yalnızca `[A-Za-z0-9_-]{5,}` kalıbına uyan kısa kod giriyor. Yazar adı `_safe_slug` ile süzülüyor. ffprobe liste argümanı ve mutlak yolla, kabuk olmadan çağrılıyor. `-` ile başlayan kısa kod seçenek enjeksiyonu yapamıyor. Windows'un ayrılmış adları (CON, AUX vb.) 5 karakter alt sınırı yüzünden oluşamıyor.
- **Anonimlik:** `cookiefile`, `cookiesfrombrowser`, `usenetrc` ve `username` hiçbir yerde ayarlanmıyor. Python API'si yt-dlp yapılandırma dosyalarını okumuyor. Çıktıda çerez ya da anahtar yok.
- **Süre kontrolü:** ffprobe ile ölçüldü. 200 sn'lik video `VIDEO_UZUN` ile reddedildi ve dosya silindi.
- **Sabit yol yok:** ffprobe sırasıyla `--ffprobe-bin`, `INSTAPULLER_FFPROBE_BIN`, `FFPROBE_BIN` ve son olarak PATH'ten aranıyor. Yol bulunamazsa `FFPROBE_YOK` dönüyor (ölçüldü). Bu makinede PATH'te ffprobe **yok**.
- **yt-dlp sabit:** Sürüm `pyproject.toml` içinde `==2026.11.23` olarak sabit; `uv.lock` ile tutarlı.
- **detail temizliği:** Yerel yollar `[path]` ile maskeleniyor, alan 500 karakterle sınırlı.

---

## Entegrasyonda dikkat (autocript tarafı)

1. **Çağrı biçimi:** Komut yolu ayardan gelsin. Örnek: `INSTAPULLER_CMD=C:\instapuller\.venv\Scripts\instapuller.exe`. `uv run` ilk çalıştırmada ortamı eşitleyip gecikebilir; doğrudan venv içindeki exe daha öngörülebilir. Argümanlar liste olarak verilsin (`shell=False`), URL'den önce `--` olsun: `[cmd, "fetch", "--out-dir", ..., "--", url]`.
2. **Kodlama:** `subprocess.run(..., capture_output=True, encoding="utf-8", errors="replace")` kullanılsın. Varsayılan `text=True` bu makinede cp1254 ile çözüyor ve Türkçe başarı çıktısında `UnicodeDecodeError` veriyor (ölçüldü). Yalnızca stdout ayrıştırılsın, stderr günlüğe yazılsın. rc 0 ya da 1 dışında bir değer geldiğinde ya da JSON ayrıştırılamadığında sonuç `BEKLENMEYEN_HATA` sayılsın.
3. **Klasörler:** `--out-dir` iş klasörünü göstersin (`data/media/jobs/<job_id>/`). İş bitince ya da iptal edilince klasör silindiğinde öldürülen süreçlerin artıkları da gider (Ö7). `--keep-video` yalnızca kullanıcı açıkça "videoyu sakla" dediğinde ve repo dışındaki bir klasörle verilsin.
4. **Cooldown:** `--state-file` her zaman mutlak ve tek bir ortak yol olsun, örneğin `data/state/instapuller_rate_limit.json` (Ö1). `--cooldown` hiç geçirilmesin. K1 düzeltilene kadar, düzeltildikten sonra da ek güvenlik olarak autocript Instagram işlerini **tek bir kilitle sıraya** koysun. `job_concurrency=3` olsa bile aynı anda en fazla bir instapuller süreci çalışsın.
5. **Bekleme ve zaman aşımı:** `--wait` (varsayılan) kullanılırsa süreç 15 sn'ye kadar sessizce bekler. Zaman aşımı en az 15 sn + indirme payı olsun (örneğin 180 sn). Arayüzde iş durumu "kaynak" aşamasında beklerken "Instagram için sıra bekleniyor" yazsın. `--no-wait` seçilirse `IG_COOLDOWN` hatasındaki `retry_after_s` alanı okunsun; bu alan `SourceError`'da yok, adaptörde ele alınmalı.
6. **ffprobe:** `--ffprobe-bin` her zaman `settings.ffprobe_bin` değerinin **mutlak** haliyle verilsin (`C:\autocript\tools\ffmpeg\bin\ffprobe.exe`). autocript `.env`'deki `FFPROBE_BIN=tools/...` göreli bir yol. Bu değer bir gün ortam değişkeni olarak alt sürece geçerse instapuller onu kendi çalışma klasörüne göre çözer ve `FFPROBE_YOK` döner. Bu makinede PATH'te ffprobe yok.
7. **Hata kodu eşlemesi:** Spec §6.4 ile kararlaştırılan liste birleştirilsin (bkz. sözleşme notu). instapuller kodları `SourceError(code, message, upload_fallback, detail)` nesnesine bire bir geçirilsin. `detail` kullanıcıya gösterilmesin, yalnızca günlüğe yazılsın. `IG_ARAC_GUNCEL_DEGIL` için kullanıcıya "dosya yükle" yönlendirmesi, geliştiriciye ise günlükte "yt-dlp güncellenmeli" notu düşsün.
8. **Süre sınırı:** instapuller 180,0 sn'de, autocript ise 185 sn toleransla kesiyor. Bu fark kaldırılmazsa 181-185 sn'lik bir Reel link olarak reddedilir, ama dosya olarak yüklenince kabul edilir. Ya autocript `--max-duration 185` geçsin ya da karar 180'de birleştirilsin.
9. **Ses izi:** instapuller yalnızca süreyi ölçüyor, ses izi olup olmadığına bakmıyor. autocript'in Instagram kaynağı da `UploadSource` gibi indirilen dosyayı probe edip `DOSYA_SES_YOK` kontrolü yapsın.
10. **Link türleri:** instapuller `/tv/<kod>` linklerini kabul ediyor. autocript'in `links/parse.py` dosyası ve `ParsedLink.kind` tipi `tv` türünü tanımıyor. autocript linki önce kendisi doğruladığı için pratikte sorun çıkmaz, ancak kabul kümelerinin aynı olduğu bir testle sabitlenmeli.
11. **Sözleşme eşlemesi:** Başarılı çıktının `meta` alanı `SourceMeta` ile alan alan aynı ve testle doğrulanmış. `video_path`, `SourceResult.audio_path`'e eşlenecek; dosya `--out-dir` iş klasöründe olduğu için "work_dir içinde ham medya" koşulu sağlanır. `published_at` alanı `Z` ekli UTC olarak geliyor.
12. **Kullanıcıya bildirim:** Instagram'ın kullanım şartları uyarısı KURULUM.md'de ve arayüzde yer alsın (CLAUDE.md gereği). Bu instapuller'ın değil, autocript'in işi.

---

## Düzeltme durumu (2026-11-25)

İki tur halinde düzeltildi. Kodu Sonnet alt ajanı yazdı. Her turun diff'i okundu ve testler bağımsız olarak yeniden çalıştırıldı.

- Tur 1: `8779944`, 63 test.
- Tur 2: `a555a38`, 118 test.
  - `INSTAPULLER_TEST_FFMPEG` ve `INSTAPULLER_FFPROBE_BIN` verildiğinde `118 passed`.
  - Bu değişkenler verilmezse `116 passed, 2 skipped`: gerçek ffprobe isteyen iki test temiz biçimde atlanır.

Bulgu ilk kez kapanmadıysa, kapanış turu "Durum" sütununda belirtildi. Testlerde dosya adı yazılmadıysa test `tests/test_download_integration.py` içindedir.

| Bulgu | Durum | Kapsayan test(ler) |
|---|---|---|
| K1 eşzamanlı cooldown | Düzeltildi (T1). Dosya kilidi altında yuva ayrılıyor, uyku kilit dışında. Paralel süreçler 0/15/30 sn yuvalarına diziliyor. | `test_rate_limit_multiprocess.py::test_three_processes_are_serialized_15s_apart_and_no_wait_is_side_effect_free` (3 gerçek süreç, gerçek saat, aralar ≥ 14,8 sn); `test_user_messages_and_limits.py::test_parallel_queue_slots_advance_15s_apart` |
| Ö1 state yolu cwd'ye bağlı | Düzeltildi (T1). `INSTAPULLER_STATE_FILE` → `%LOCALAPPDATA%\instapuller\rate_limit.json` → `~/.local/state/...` | `test_default_state_file_env_override`, `..._windows_uses_localappdata`, `..._posix_uses_home_state`, `..._independent_of_cwd` |
| Ö2 boyut sınırı indirme sırasında | Düzeltildi (T2). `progress_hooks` kullanılıyor. | `test_chunked_stream_without_content_length_is_aborted_midway` (40 MB akışın en fazla 13 MB'ı gönderilebilir) |
| Ö3 boyut aşımında yanlış kod | Düzeltildi (T2) | `test_content_length_over_limit_is_dosya_buyuk_not_kaynak_gecici` |
| Ö4 alt dize sınıflandırması | Düzeltildi (T2). Sınıflandırma istisna türüne ve `HTTP Error NNN` kalıbına göre yapılıyor; ağ hataları ve 5xx → `KAYNAK_GECICI`. | `test_map_error_real_messages_and_exception_types` (27 vaka, gerçek yt-dlp mesajları ve türleri) |
| Ö5 kod listesi / `upload_fallback` | Düzeltildi (T1). `ERROR_CODES` tek kaynak, 14 kod. `IG_INDIRME_HATASI` kaldırıldı. | `test_cli.py::test_error_code_table_is_exactly_the_agreed_fourteen`, `::test_every_pullerror_code_in_source_is_in_table_and_old_code_is_gone`, `::test_unknown_code_raises_value_error_and_fallback_param_is_gone`, `::test_unexpected_exception_is_beklenmeyen_hata_without_fallback` |
| Ö6 her durumda JSON | Düzeltildi (T1). Çıkış kodları: 0 / 1 / 130. `-h` belgelenmiş tek istisna. | `test_cli.py::test_invalid_cooldown_value_is_json_arg_error`, `::test_missing_subcommand_is_json_arg_error`, `::test_unknown_option_is_json_arg_error`, `::test_double_dash_protects_dash_prefixed_link`, `::test_help_exits_zero_with_text_not_json`, `::test_keyboard_interrupt_gives_json_and_130` |
| Ö7 öldürülen süreç artıkları | Düzeltildi (T2). 1 saatten eski `ip_*` klasörleri siliniyor. README'ye silme sorumluluğu notu eklendi. | `test_stale_staging_dirs_are_purged_but_fresh_and_unprefixed_stay`, `test_keyboard_interrupt_during_download_leaves_no_staging` |
| Ö8 `curl_cffi` / anonim erişim | **Ertelendi.** Gerçek Instagram denemesi gerektiriyor; kullanıcıyla ayrıca konuşulacak. | — |
| Ö9 testler mock'a dayalı | Düzeltildi (T2). Gerçek yt-dlp, yerel `file://` ve chunked sunucu, alt süreç CLI ve gerçek ffprobe ile testler eklendi. | `test_cli_success_single_json_line_utf8_with_turkish_out_dir` (tek satır, UTF-8, Türkçe yol, stderr boş), `test_video_uzun_with_real_ffprobe_and_file_removed`, `test_real_video_success_with_real_ffprobe` ve bu tablodaki T2 testleri |
| S1 cooldown < 15 | Düzeltildi (T1). API 15'e kıstırıyor, CLI `ARG_GECERSIZ` dönüyor. | `test_cli.py::test_cooldown_below_15_is_rejected`, `::test_cooldown_below_min_in_process`; `test_cooldown_cannot_go_below_15`, `test_fetch_video_clamps_cooldown_to_15` |
| S2 saat geri alınınca uzun uyku | Düzeltildi (T1). `written_at` alanıyla güven kontrolü yapılıyor; güvenilmeyen state'te en fazla 15 sn uyunuyor. | `test_future_state_never_sleeps_longer_than_cooldown` |
| S3 `cleanup` her dosyayı siliyor | Düzeltildi (T2). Yalnızca `<kod>_<8hex>.<ext>` biçimli dosyalar siliniyor. | `test_cleanup_refuses_arbitrary_files_but_deletes_generated_names`, `test_cli_cleanup_of_random_file_is_silme_hatasi` |
| S4 gevşek link denetimi | Düzeltildi (T2). Kullanıcı bilgisi (`@`), 80/443 dışı port ve yalnızca tireden oluşan kod reddediliyor. | `test_rejected_links`, `test_accepted_links`, `test_adapter_parse_url_also_checks_authority` |
| S5 çıkarım sonrası site listesi | Düzeltildi (T2). `allowed_extractors=["Instagram"]` ve `extractor_key` kontrolü eklendi. | `test_only_instagram_extractor_is_loaded_in_real_ytdlp`, `test_foreign_extractor_key_is_ig_arac_guncel_degil` |
| S6 klasör yolu şablona giriyor | Düzeltildi (T2). Klasör `paths` ile veriliyor, `outtmpl` artık klasör içermiyor. | `test_percent_in_out_dir_name_does_not_break_template_and_file_lands_in_staging`, `test_opts_carry_paths_allowed_extractors_and_hook` |
| S7 rastgele dosya seçimi | Düzeltildi (T2). `requested_downloads` kullanılıyor, yarım dosyalar süzülüyor. | `test_find_downloaded_file_prefers_reported_path_and_ignores_partials`, `test_reported_filepath_outside_staging_is_ignored` |
| S8 `mp4/best` / DASH | Yalnızca README'de belgelendi ("Davranış Notları"), karar gereği. "Requested format is not available" artık `IG_VIDEO_DEGIL` dönüyor. | `test_map_error_real_messages_and_exception_types` |
| S9 teknik kullanıcı mesajları | Düzeltildi (T1). Teknik bilgi artık `detail` alanında. | `test_user_messages_have_no_technical_terms_and_fallback_ones_point_to_upload`, `test_tool_outdated_message_and_detail`, `test_ffprobe_missing_details_go_to_detail` |
| Karar: süre varsayılanı 185 sn | Uygulandı (T1) | `test_default_max_duration_is_185`, `test_duration_184_9_passes_and_185_2_rejected_with_number_from_limit` |

### Tasarım notları
- **`written_at` ve kuyruk derinliği:** S2 için ilk tarif "slot en fazla now + cooldown" idi. Bu kural K1 ile çelişiyordu: üçüncü süreç 30. sn yerine 15. sn'de geçti, gerçek süreç testi bunu yakaladı. Çözüm olarak state'e `written_at` alanı eklendi. Güvenilir kuyrukta yuva en fazla `now + 20 × cooldown` (300 sn) ileride olabilir; güvenilmeyen state'te sınır `now + cooldown`.
- **`LINK_GECERSIZ` mesajları:** Mesaj metinleri değişti; hepsi artık "dosya olarak yükleyin" yönlendirmesi içeriyor. autocript mesaj metnine değil koda bakmalı.
- **POSIX kilidi:** `fcntl.flock` yolu yalnızca Windows'ta dolaylı olarak sınandı, POSIX'te doğrulanmadı.
- **Staging temizliğinin kapsamı:** `--keep-video` ile kullanıcının kendi klasörü `--out-dir` olarak verilirse oradaki 1 saatten eski `ip_*` klasörleri de silinir. Bunlar yalnızca instapuller'ın ürettiği klasörlerdir.
- **Entegrasyonda dikkat:** Bu bölümün maddeleri sonraki aşamaya kaldı; autocript'e dokunulmadı. 4. madde (Instagram işlerini tek kilitle sıraya koyma) artık zorunlu değil, ancak derinlemesine savunma olarak önerilmeye devam ediyor.
