# Baut das Windows-Paket dist\Jahresshow (PyInstaller onedir).
# Aufruf im Projektordner: powershell -ExecutionPolicy Bypass -File .\build.ps1
# Voraussetzungen: .venv mit Python 3.12 und  pip install -e ".[dev,build]",
# ffmpeg.exe und ffprobe.exe in bin\ (gyan.dev full build).
# Die Datei enthaelt bewusst nur ASCII-Zeichen: Windows PowerShell 5.1 liest Skripte ohne BOM
# sonst in der falschen Codierung.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$PyInstallerVersion = '6.22.3'  # wie in pyproject.toml [project.optional-dependencies] build
$SizeLimitMB = 500
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$Dist = Join-Path $PSScriptRoot 'dist\Jahresshow'
$Work = Join-Path $PSScriptRoot 'build\pyinstaller'

function Fail($msg) { Write-Host "FEHLER: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $Python)) { Fail ".venv fehlt. Siehe README.md, Abschnitt Einrichtung." }
$pyVersion = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($pyVersion -ne '3.12') { Fail "Python 3.12 erwartet, gefunden: $pyVersion" }
$piVersion = & $Python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
if ($piVersion -ne $PyInstallerVersion) {
    Fail "PyInstaller $PyInstallerVersion erwartet, gefunden: '$piVersion'. Installieren mit: .venv\Scripts\python.exe -m pip install -e "".[dev,build]"""
}
foreach ($tool in 'ffmpeg.exe', 'ffprobe.exe') {
    if (-not (Test-Path "bin\$tool")) { Fail "bin\$tool fehlt (gyan.dev full build)." }
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
    --distpath (Split-Path $Dist) --workpath $Work packaging\Jahresshow.spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller ist fehlgeschlagen ($LASTEXITCODE)." }

Copy-Item 'bin\ffmpeg.exe', 'bin\ffprobe.exe' $Dist
Copy-Item 'packaging\README.txt' $Dist
& $Python packaging\collect_licenses.py $Dist 'bin\ffmpeg.exe' | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Lizenzen konnten nicht gesammelt werden." }

$files = Get-ChildItem $Dist -Recurse -File
$sizeMB = [math]::Round(($files | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "Fertig: $Dist\Jahresshow.exe"
Write-Host "Dateien: $($files.Count), Groesse: $sizeMB MB"
foreach ($part in @(
        @('ffmpeg.exe + ffprobe.exe', ($files | Where-Object { $_.Name -in 'ffmpeg.exe', 'ffprobe.exe' })),
        @('Qt (PySide6, shiboken6)', ($files | Where-Object { $_.FullName -match '\\(PySide6|shiboken6)\\' })),
        @('Rest', ($files | Where-Object { $_.Name -notin 'ffmpeg.exe', 'ffprobe.exe' -and $_.FullName -notmatch '\\(PySide6|shiboken6)\\' }))
    )) {
    Write-Host ("  {0,-26} {1,8:N1} MB" -f $part[0], (($part[1] | Measure-Object Length -Sum).Sum / 1MB))
}
if ($sizeMB -ge $SizeLimitMB) {
    Write-Warning "Das Paket ist groesser als $SizeLimitMB MB."
}
