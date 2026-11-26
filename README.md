# instapuller

An **anonymous** Instagram video/Reel download and metrics tool for local applications (in particular `autocript`).
It provides a CLI with JSON output (`subprocess`) and a directly importable Python API; no separate HTTP service is needed.

> **Safety and limits:** No session or login is used, so **there is no account risk; your IP may be temporarily rate limited.** To reduce that risk, a **15 second** cooldown is enforced between consecutive requests by default.

> **Note on error codes:** The error codes are mostly Turkish identifiers (for example `KAYNAK_GECICI`). They are a stable contract with `autocript` and are not translated; the [error code table](#error-codes) gives the English meaning of each one.

## Usage and Responsibility

- This tool is for **personal use**.
- Complying with Instagram's [Terms of Use](https://help.instagram.com/581066165581870) and with the **copyright** of any content is entirely the user's responsibility. Do not download or republish content you have no rights to.
- The tool **uses no account or cookies**: it does not log in or read session data; only publicly available posts can be accessed.

## Installation

```bash
uv sync
```

> Measuring duration requires `ffprobe` on the system, or its path given with `--ffprobe-bin` / `INSTAPULLER_FFPROBE_BIN`.

## Cooldown and State File

- At least **15 s** pass between consecutive requests. `--cooldown` / `cooldown_s` **cannot go below** 15: the Python API silently raises a lower value to 15, while the CLI returns `ARG_GECERSIZ` if `--cooldown` is below 15. Larger values are allowed.
- The rule holds **across processes**: an operating system lock is taken on the `<state>.lock` file next to the state file, and each process reserves its own time slot and sleeps outside the lock. Three processes started at the same moment queue up 15 s apart.
- The default state file does not depend on the working directory. Precedence:
  1. `--state-file` (CLI) / `state_file=` (API)
  2. The `INSTAPULLER_STATE_FILE` environment variable
  3. Windows: `%LOCALAPPDATA%\instapuller\rate_limit.json`; other systems: `~/.local/state/instapuller/rate_limit.json`
- With `keep_video=false`, deleting the downloaded file (`cleanup`) is the caller's responsibility.

## Updating `yt-dlp` (One Line)

If you get `IG_ARAC_GUNCEL_DEGIL` because Instagram changed its response format, update `yt-dlp` with this single command:

```bash
uv add yt-dlp==$(uv pip list --outdated --format=json | python -c "import json,sys; p=[x for x in json.load(sys.stdin) if x['name']=='yt-dlp']; print(p[0]['latest_version'] if p else '2026.11.23')")
```

Or pin a specific version:

```bash
uv add yt-dlp==<NEW_VERSION>
```

## CLI Usage (JSON Output)

```bash
# Default: 15 s cooldown (--wait), video not kept (temporary file)
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --out-dir "./tmp" --ffprobe-bin "C:\path\to\ffprobe.exe"

# Keep the video permanently
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --out-dir "./downloads" --keep-video

# If the cooldown has not elapsed, return IG_COOLDOWN immediately (with retry_after_s)
uv run instapuller fetch "https://www.instagram.com/reel/Chunk8-jurw/" --no-wait

# If the link may start with `-` (e.g. user input), put `--` before the URL
uv run instapuller fetch --out-dir "./tmp" -- "<link>"

# Delete the temporary video after transcription
uv run instapuller cleanup "C:\path\to\video.mp4"
```

## Python API Usage

```python
from instapuller import fetch_video, cleanup_file

result = fetch_video(
    "https://www.instagram.com/reel/Chunk8-jurw/",
    out_dir="./tmp",
    keep_video=False,   # off by default
    wait=True,          # default --wait (15 s cooldown)
    max_duration_s=185, # default 185 s
    max_filesize_mb=200 # 200 MB limit
)
print(result.to_dict())

if not result.keep_video:
    cleanup_file(result.video_path)
```

## JSON Output Contract (`autocript` Compatible)

Successful response (`exit code = 0`):

```json
{
  "ok": true,
  "video_path": "C:\\...\\Chunk8-jurw_a1b2c3d4.mp4",
  "keep_video": false,
  "meta": {
    "kind": "instagram",
    "url": "https://www.instagram.com/reel/Chunk8-jurw/",
    "platform_id": "Chunk8-jurw",
    "title": "Video caption...",
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

Error response (`exit code = 1`):

```json
{
  "ok": false,
  "error": {
    "code": "IG_ERISILEMEDI",
    "message": "This post could not be accessed (it may be private, deleted or require login). Upload the video as a file instead.",
    "upload_fallback": true,
    "detail": "..."
  }
}
```

The `IG_COOLDOWN` error also carries a `retry_after_s` field (seconds, number); a caller using `--no-wait` can retry after that long:

```json
{"ok": false, "error": {"code": "IG_COOLDOWN", "message": "...", "upload_fallback": true, "retry_after_s": 10.75}}
```

`message` is plain text that can be shown to end users; technical details (tool name, path, raw error) are in the `detail` field.

## Exit Codes

| Code | Meaning |
| :---: | :--- |
| `0` | Success (one line of JSON on stdout, `"ok": true`) |
| `1` | Error (one line of JSON on stdout, `"ok": false`), including argument errors |
| `130` | Cancelled by the user (Ctrl+C); a `BEKLENMEYEN_HATA` JSON is written |

**`-h` / `--help` is the only case that does not produce JSON**: it prints help text and exits with `0`. In no other case is there non-JSON output on stdout; an invalid or missing argument, an unknown option or a missing subcommand returns an `ARG_GECERSIZ` JSON (exit `1`).

> **Note for autocript:** Because the link comes from the user, it may start with `-` (e.g. `-h`) and be mistaken for an option. Pass `--` before the URL: `instapuller fetch [options] -- <url>`. Such input returns `LINK_GECERSIZ` and prints no help text.

## Error Codes

The code list lives in a single table (`instapuller/models.py` → `ERROR_CODES`); a `PullError` cannot be created with a code that is not in it.

| Code | English meaning | When | `upload_fallback` |
| :--- | :--- | :--- | :---: |
| `LINK_GECERSIZ` | Invalid link | Site not on the allowlist, or an invalid/unsupported link | `true` |
| `IG_ERISILEMEDI` | Instagram post inaccessible | Empty media response, private/deleted post or login wall | `true` |
| `IG_RATE_LIMIT` | Instagram rate limit | Temporary IP-based rate limit by Instagram | `true` |
| `IG_ARAC_GUNCEL_DEGIL` | Instagram tool out of date | Instagram's response could not be parsed (`yt-dlp` may need an update; see `detail`) | `true` |
| `IG_VIDEO_DEGIL` | Instagram post is not a video | Carousel post, or a post without video | `true` |
| `IG_COOLDOWN` | Instagram cooldown active | `--no-wait` was given and the 15 s cooldown has not elapsed (includes `retry_after_s`) | `true` |
| `DOSYA_BUYUK` | File too large | Video exceeds the size limit (default 200 MB) | `true` |
| `VIDEO_UZUN` | Video too long | Video exceeds the duration limit (default **185 s**; the file is deleted) | `true` |
| `DOSYA_BOZUK` | File corrupt | The downloaded file could not be read or its duration is invalid | `true` |
| `KAYNAK_GECICI` | Temporary source error | Network/transfer errors and other temporary failures where no video file is produced | `true` |
| `FFPROBE_YOK` | ffprobe missing | `ffprobe` was not found (incomplete setup) | `false` |
| `ARG_GECERSIZ` | Invalid argument | Invalid or missing command-line argument, `--cooldown` < 15 | `false` |
| `SILME_HATASI` | Deletion failed | `cleanup` could not delete the file | `false` |
| `BEKLENMEYEN_HATA` | Unexpected error | Uncaught exception (`detail`: `<ExceptionType>: <message>`), cancellation, lock not acquired | `false` |

## Behavior Notes

- **The size limit is enforced during the download** (default 200 MB): with or without a declared `Content-Length`, the download is aborted as soon as the limit is exceeded and `DOSYA_BUYUK` is returned; the partial file is deleted.
- **Format selection:** the `format` value is `mp4/best`; only formats with audio and video **in a single file** are selected, and nothing is merged with ffmpeg. Posts that offer only DASH (separate audio/video) return `IG_VIDEO_DEGIL`.
- **Only the Instagram extractor** is loaded; a response from any other extractor returns `IG_ARAC_GUNCEL_DEGIL`.
- **Stale staging directories:** at the start of each `fetch`, `ip_*` directories older than 1 hour in the target directory (left over from killed runs) are silently removed.
- **`cleanup`** only deletes temporary names generated by instapuller (`<code>_<8 hex>.<mp4|mov|webm|mkv|m4v>`); other files return `SILME_HATASI`. Files kept with `--keep-video` do not use this naming and are not deleted by this command.
- **Link checks:** user info (`user@...`), ports other than 80/443 and shortcodes consisting only of `-`/`_` return `LINK_GECERSIZ`.
- **yt-dlp warnings (diagnostics):** yt-dlp `warning`/`error` lines (at most 10 lines, each at most 200 characters, local paths masked as `[path]`) go to two places: (1) stderr, as `instapuller: yt-dlp warning: ...` (UTF-8); (2) on failure, appended to the end of `detail` as ` | yt-dlp warnings: ...` (the original error text takes priority, 500 characters total at most). On success warnings are written to stderr only; the JSON output does not change. **Warnings are never written to stdout** (a single JSON line only). stderr is for diagnostics only: `autocript` should not parse it, but should log it. `INSTAPULLER_QUIET=1` disables stderr output (appending to `detail` still happens).

## Adding a New Site (Extensibility)

The allowlist (`instapuller/sites/__init__.py` → `ALLOWED_SITES`) currently contains only `instagram.com`. To add another platform (e.g. YouTube) later, add a module under `instapuller/sites/` that implements the `SiteAdapter` protocol and register it in the `ALLOWED_SITES` dictionary.

## Tests

```bash
uv run pytest
```

Tests that generate video need ffmpeg: it is looked up via `INSTAPULLER_TEST_FFMPEG`, then next to `INSTAPULLER_FFPROBE_BIN` (`ffmpeg(.exe)` in the same directory), then on `PATH`. If ffmpeg or ffprobe is not found, those tests are skipped.

## License

[MIT](LICENSE)
