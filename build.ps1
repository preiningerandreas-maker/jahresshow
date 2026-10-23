# Baut das Windows-Paket dist\Flashback (PyInstaller onedir).
# Aufruf im Projektordner: powershell -ExecutionPolicy Bypass -File .\build.ps1
# Voraussetzungen: .venv mit Python 3.12 und  pip install -e ".[dev,build]",
# in bin\ der FFmpeg-Build "full_build-shared" von gyan.dev: aus dem Archiv der Inhalt von
# bin\ (ohne ffplay.exe) sowie LICENSE und README.txt, siehe README.md.
# Die Datei enthaelt bewusst nur ASCII-Zeichen: Windows PowerShell 5.1 liest Skripte ohne BOM
# sonst in der falschen Codierung.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$PyInstallerVersion = '6.22.3'  # wie in pyproject.toml [project.optional-dependencies] build
$SizeLimitMB = 500
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$Dist = Join-Path $PSScriptRoot 'dist\Flashback'
$Work = Join-Path $PSScriptRoot 'build\pyinstaller'

function Fail($msg) { Write-Host "FEHLER: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $Python)) { Fail ".venv fehlt. Siehe README.md, Abschnitt Einrichtung." }
$pyVersion = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($pyVersion -ne '3.12') { Fail "Python 3.12 erwartet, gefunden: $pyVersion" }
$piVersion = & $Python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
if ($piVersion -ne $PyInstallerVersion) {
    Fail "PyInstaller $PyInstallerVersion erwartet, gefunden: '$piVersion'. Installieren mit: .venv\Scripts\python.exe -m pip install -e "".[dev,build]"""
}
foreach ($file in 'ffmpeg.exe', 'ffprobe.exe', 'LICENSE', 'README.txt') {
    if (-not (Test-Path "bin\$file")) { Fail "bin\$file fehlt (gyan.dev full_build-shared)." }
}
# ffmpeg.exe und ffprobe.exe teilen sich diese Bibliotheken (je genau eine Version)
$FFmpegDlls = foreach ($lib in 'avcodec', 'avdevice', 'avfilter', 'avformat', 'avutil', 'swresample', 'swscale') {
    $found = @(Get-ChildItem "bin\$lib-*.dll")
    if ($found.Count -ne 1) { Fail "bin\$lib-*.dll: genau eine Datei erwartet, gefunden: $($found.Count)." }
    $found[0].FullName
}
# Alles da, was Flashback braucht? (Farbumrechnung, Kodierer, HEVC von Handyvideos)
$ffVersion = (& 'bin\ffmpeg.exe' -hide_banner -version) -join "`n"
if ($ffVersion -notmatch '--enable-shared') { Fail "bin\ffmpeg.exe ist nicht der shared-Build." }
$checks = @(
    @('-filters', 'zscale'), @('-filters', 'tonemap'), @('-filters', 'xfade'),
    @('-encoders', 'h264_qsv'), @('-encoders', 'libx264'), @('-decoders', 'hevc')
)
foreach ($c in $checks) {
    $list = & 'bin\ffmpeg.exe' -hide_banner $c[0]
    if (-not ($list | Where-Object { ($_ -split '\s+')[2] -eq $c[1] })) {
        Fail "bin\ffmpeg.exe kann $($c[1]) nicht ($($c[0]))."
    }
}

# Sauber von vorn, damit jeder Lauf dasselbe Ergebnis liefert
foreach ($dir in $Dist, $Work) {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
}
$env:PYTHONHASHSEED = '0'
# Zeitstempel im Paket = Zeitpunkt des letzten Commits statt Bauzeit
$commitTime = git log -1 --format=%ct 2>$null
if ($LASTEXITCODE -eq 0 -and $commitTime) { $env:SOURCE_DATE_EPOCH = $commitTime }

Write-Host "PyInstaller $piVersion, Python $pyVersion"
& $Python -m PyInstaller --noconfirm --clean --log-level WARN `
    --distpath (Split-Path $Dist) --workpath $Work packaging\Flashback.spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller ist fehlgeschlagen ($LASTEXITCODE)." }

Copy-Item (@('bin\ffmpeg.exe', 'bin\ffprobe.exe') + $FFmpegDlls) $Dist
Copy-Item 'packaging\README.txt' $Dist
& $Python packaging\collect_licenses.py $Dist 'bin' | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Lizenzen konnten nicht gesammelt werden." }

$files = Get-ChildItem $Dist -Recurse -File
$sizeMB = [math]::Round(($files | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "Fertig: $Dist\Flashback.exe"
Write-Host "Dateien: $($files.Count), Groesse: $sizeMB MB"
$isFFmpeg = { $_.DirectoryName -eq $Dist -and ($_.Name -in 'ffmpeg.exe', 'ffprobe.exe' -or $_.Extension -eq '.dll') }
$isQt = { $_.FullName -match '\\(PySide6|shiboken6)\\' }
foreach ($part in @(
        @('FFmpeg (exe + DLLs)', ($files | Where-Object $isFFmpeg)),
        @('Qt (PySide6, shiboken6)', ($files | Where-Object $isQt)),
        @('Rest', ($files | Where-Object { -not (& $isFFmpeg) -and -not (& $isQt) }))
    )) {
    Write-Host ("  {0,-26} {1,8:N1} MB" -f $part[0], (($part[1] | Measure-Object Length -Sum).Sum / 1MB))
}
if ($sizeMB -ge $SizeLimitMB) {
    Write-Warning "Das Paket ist groesser als $SizeLimitMB MB."
}
