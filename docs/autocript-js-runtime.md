# Installing the JavaScript runtime from autocript's `baslat.cmd`

YouTube downloads in instapuller need a JavaScript runtime (see the README, "JavaScript runtime"). This note is for the `autocript` maintainer: how to fetch a pinned runtime from `baslat.cmd` while keeping the existing rules (everything stays inside autocript's own folder under `tools\`, nothing is written to `PATH`, the registry or the user profile).

## Pinned versions

| Option | Asset | SHA-256 | Size |
| :--- | :--- | :--- | ---: |
| **quickjs-ng v0.18.0** (default) | [`qjs-windows-x86_64.exe`](https://github.com/quickjs-ng/quickjs/releases/download/v0.18.0/qjs-windows-x86_64.exe) | `ff03398d74ea6e6bd3a8e58058f7003225abcac229d7d57775b7aaae812a49c7` | 1,873,152 bytes |
| deno v2.9.8 (alternative), zip | [`deno-x86_64-pc-windows-msvc.zip`](https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip) | `cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf` | 42,917,472 bytes |
| deno v2.9.8, extracted `deno.exe` | (inside the zip) | `924397800533d3eb0ed0c889b20ad70e18b970d93acd8135bc043f425e528266` | 98,081,568 bytes |

Install locations (the file name decides the runtime kind, so keep them exactly):

- quickjs-ng: save as `tools\js\qjs.exe`
- deno: extract to `tools\js\deno.exe`

## Helper: `dagitim\js-indir.ps1`

Same style as `dagitim\uv-indir.ps1`: downloads into a temp file inside `tools\js`, verifies SHA-256, deletes the temp file and fails on a mismatch, then renames atomically. It uses TLS 1.2 and writes nothing outside `tools\js`.

```powershell
# dagitim\js-indir.ps1
# Usage: js-indir.ps1 -Runtime qjs|deno -Dest <tools\js folder>
param(
    [ValidateSet('qjs', 'deno')][string]$Runtime = 'qjs',
    [Parameter(Mandatory = $true)][string]$Dest
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

if ($Runtime -eq 'qjs') {
    $url     = 'https://github.com/quickjs-ng/quickjs/releases/download/v0.18.0/qjs-windows-x86_64.exe'
    $sha     = 'ff03398d74ea6e6bd3a8e58058f7003225abcac229d7d57775b7aaae812a49c7'
    $final   = 'qjs.exe'
} else {
    $url     = 'https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip'
    $sha     = 'cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf'   # zip
    $exeSha  = '924397800533d3eb0ed0c889b20ad70e18b970d93acd8135bc043f425e528266'   # deno.exe
    $final   = 'deno.exe'
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Dest   = (Resolve-Path -LiteralPath $Dest).Path
$target = Join-Path $Dest $final
$tmp    = Join-Path $Dest ('.download-' + [guid]::NewGuid().ToString('N') + '.tmp')
$tmpDir = $null

function Test-Hash($path, $expected) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -eq $expected
}

try {
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
    if (-not (Test-Hash $tmp $sha)) { throw "SHA-256 mismatch for $url" }

    if ($Runtime -eq 'qjs') {
        Move-Item -LiteralPath $tmp -Destination $target -Force      # rename inside the same folder
    } else {
        $tmpDir = Join-Path $Dest ('.extract-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $tmp -DestinationPath $tmpDir -Force
        $exe = Join-Path $tmpDir 'deno.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw 'deno.exe not found in the archive' }
        if (-not (Test-Hash $exe $exeSha)) { throw 'SHA-256 mismatch for deno.exe' }
        Move-Item -LiteralPath $exe -Destination $target -Force
    }
}
catch {
    Write-Error $_
    exit 1
}
finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    if ($tmpDir -and (Test-Path -LiteralPath $tmpDir)) { Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
}
```

Note: `exit 1` inside `catch` still runs `finally`, so the temp files are always removed.

## `baslat.cmd` lines

Download once, only if the runtime is not already there:

```bat
if not exist "%~dp0tools\js\qjs.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dagitim\js-indir.ps1" -Runtime qjs -Dest "%~dp0tools\js"
    if errorlevel 1 (
        echo JavaScript runtime download failed. YouTube links will not work.
        rem decide here: abort, or continue (Instagram and file upload still work)
    )
)
```

For deno instead, check `tools\js\deno.exe` and pass `-Runtime deno`.

## How autocript passes it to instapuller

Pick one:

```bat
rem 1) argument
uv run instapuller fetch --js-runtime-bin "%~dp0tools\js\qjs.exe" --state-file "%~dp0data\instapuller-youtube.json" ... -- "<link>"

rem 2) environment variable (set once in baslat.cmd, inherited by the subprocess)
set "INSTAPULLER_JS_RUNTIME_BIN=%~dp0tools\js\qjs.exe"
```

Per-site cooldown state: give each site its own `--state-file` so the two never share a file (a shared file also works, since each site keeps its own record):

- Instagram links: `--state-file "%~dp0data\instapuller-instagram.json"`
- YouTube links: `--state-file "%~dp0data\instapuller-youtube.json"`

If the runtime is missing or unusable, instapuller returns `JS_ORTAMI_YOK` (`upload_fallback: true`) before taking a cooldown slot or making any network call, so autocript can fall back to file upload.

## Decision

Runtime choice and measurements are recorded after the live trial (pending).

> The `js-indir.ps1` example has not been executed yet; the hashes and sizes above were verified by downloading the assets with `gh release download` and `sha256sum` (2026-11-26).
