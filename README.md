# Flashback

© 2026 Andreas Preininger. Früher hieß das Projekt „Jahresshow“ (Repository und Projektordner heißen weiter so).

Flashback macht aus Fotos und Videos eine Slideshow mit eigener Musik und Texten und speichert
sie als MP4. Gedacht ist es für den Jahresrückblick: Ordner hineinziehen, ein paar Texte
ergänzen, Video erstellen. Es läuft unter Windows 10 und 11, ohne Python und ohne Internet.

Die Oberfläche ist auf Deutsch. Dieser erste Teil der README erklärt, was das Programm kann
und wie du ein Video machst. Ab „Für Entwickler“ geht es um Einrichtung, Tests und das Bauen
des Pakets.

## Was Flashback kann

- Fotos (JPG, PNG, HEIC) und Videos (MP4, MOV) aus Ordnern oder per Drag & Drop einlesen,
  auch HEVC- und HDR-Videos vom Handy
- Nach Aufnahmedatum sortieren und Fotos nach der Kameradrehung richtig stellen
- Für jeden Monat einen Zwischentitel einfügen, der sich ändern oder löschen lässt
- Fotos in 90-Grad-Schritten drehen und auf einen festen Ausschnitt zuschneiden
- Videos mit „Video von“ und „Video bis“ kürzen und den Originalton pro Clip lauter, leiser
  oder aus stellen
- Texte ins Bild setzen: Schrift, Größe, Farbe, halbtransparenter Balken, sanftes Ein- und
  Ausblenden, Position oben, Mitte, unten oder frei per Kreuz
- Eine Titelkarte am Anfang, auf Farbfläche oder auf einem Foto
- Neun Übergänge, darunter Überblenden, Wischen, Zoom, Kreis öffnen, Drehwischer und Pixel
- Musik aus eigenen Dateien (MP3, M4A, WAV, FLAC) als Playlist, jedes Stück auf einen
  Ausschnitt kürzbar, am Ende ausgeblendet; während Videos mit Originalton wird die Musik
  leiser
- Hochkant-Fotos wahlweise mit unscharfem Hintergrund, schwarzen Rändern oder zugeschnitten
- Vorschau eines Abschnitts in niedriger Auflösung, Export als MP4 in Full HD oder 4K
- Projekte speichern und später weiterbearbeiten

## Anleitung: In sieben Schritten zum Video

Das Programm startest du mit `Flashback.exe` (siehe „Programm starten“ weiter unten).

1. Material einlesen. Oben in der Leiste auf „Ordner“ klicken und den Ordner mit deinen
   Fotos und Videos wählen, oder einzelne Dateien mit „Dateien“ hinzufügen. Du kannst sie
   auch aus dem Explorer in das Fenster ziehen. Videos vom Handy werden nach dem Einlesen
   einmal umgewandelt, den Fortschritt siehst du unten im Fenster. Doppelte Dateien meldet
   das Programm.
2. Reihenfolge prüfen. Die Zeitleiste zeigt alles nach Aufnahmedatum sortiert, mit einem
   Zwischentitel vor jedem Monat. Bilder ziehst du mit der Maus an eine andere Stelle.
   „Markierte entfernen“ nimmt Bilder heraus, mit Strg+Z holst du sie zurück.
3. Einzelne Bilder bearbeiten. Klick ein Element an und wechsle rechts in den Reiter
   „Element“. Dort stellst du die Anzeigedauer ein, drehst Fotos, wählst einen Ausschnitt,
   kürzt Videos und gibst einen Text samt Position ein. Bei Hochkant-Bildern legst du unter
   „Bildformat“ fest, was neben dem Bild erscheint.
4. Die ganze Show einstellen. Im Reiter „Show“ legst du den Namen fest, wie lange Fotos
   stehen, welcher Übergang gilt, Schriftgröße und Farbe für alle Texte, die Lautstärke der
   Musik bei Originalton, ob es Monats-Zwischentitel gibt, und ob in Full HD oder 4K
   exportiert wird.
5. Titelkarte anlegen. Mit „Titelkarte“ in der Leiste setzt du einen Titel an den
   Anfang, auf einer Farbfläche oder auf einem Foto.
6. Musik hinzufügen. Im Reiter „Musik“ fügst du mit „Musik hinzufügen …“ Stücke hinzu und
   ordnest sie mit „Nach oben“ und „Nach unten“. Für das markierte Stück wählst du einen
   Ausschnitt, falls es kürzer laufen soll.
7. Vorschau und Export. „Vorschau“ rechnet den gewählten Abschnitt in niedriger Qualität,
   damit du Übergänge und Texte prüfen kannst. Mit „Video erstellen“ (Strg+E) entsteht das
   fertige MP4. Vorher zeigt das Programm die geschätzte Dateigröße und warnt ab 4 GB, weil
   FAT32-USB-Sticks größere Dateien nicht aufnehmen. Der Export läuft im Hintergrund und lässt
   sich abbrechen.

Zwischendurch speicherst du mit Strg+S. Das Projekt ist eine JSON-Datei, die sich später
wieder öffnen lässt. Ältere Projekte laden weiterhin.

Kleine Tipps: Mit Strg und Mausrad änderst du die Größe der Bilder in der Zeitleiste. Fährst
du mit der Maus über ein Feld, steht dort meist eine kurze Erklärung.

## Programm starten

Das fertige Programm ist ein Ordner „Flashback“ mit `Flashback.exe`. Der ganze Ordner wird
gebraucht, nichts daraus darf gelöscht oder verschoben werden. Kommt er als ZIP, entpackst du
ihn zuerst mit „Alle extrahieren“. Beim ersten Start meldet Windows eventuell eine
SmartScreen-Warnung, weil das Programm nicht signiert ist. Wie du damit umgehst, steht in
`packaging/README.txt`, die dem Programm beiliegt.

Das fertige Paket lädst du unter „Releases“ auf der rechten Seite dieser GitHub-Seite
(Datei `Flashback-2.0-Windows.zip`). Du kannst es auch nach der Anleitung unter
„Windows-Paket bauen“ selbst erzeugen.

Voraussetzungen: Windows 10 oder 11 (64 Bit), 8 GB Arbeitsspeicher und freier Speicherplatz
von etwa dem Dreifachen deines Materials. Auf langsamen Rechnern dauert alles länger, geht
aber.

## Für Entwickler

Der Rest dieser Datei beschreibt Einrichtung, Kommandozeile, Paketbau und Tests.

## Voraussetzungen

- **Python 3.12.10** von python.org (64 Bit, mit dem Python-Launcher `py`); damit ist
  `.venv` angelegt und alles getestet. Prüfen in PowerShell: `py -3.12 --version` muss
  `Python 3.12.10` melden.
- **FFmpeg**: `ffmpeg-9.0.2-full_build-shared.zip` von gyan.dev (GitHub `GyanD/codexffmpeg`,
  Release 9.0.2, SHA-256 `8d31e162f1616e37aab3fa2db991b97e1b8dbeb1c8465fd81a81be16ff91b328`).
  In den Ordner `bin\` im Projektordner (nicht im Git) kommen aus dem Archiv der Inhalt von
  `bin\` ohne `ffplay.exe` (`ffmpeg.exe`, `ffprobe.exe` und sieben DLLs) sowie `LICENSE` und
  `README.txt`. Der shared-Build kann dasselbe wie der statische Full Build, ist aber nur halb
  so groß, weil sich ffmpeg.exe und ffprobe.exe die Bibliotheken teilen.
- **Kurzer Projektpfad**, z. B. `C:\dev\jahresshow`. PySide6 enthält sehr tief
  verschachtelte Dateien. In einem langen Pfad bricht die Installation mit
  „No such file or directory … Long Path support“ ab, solange Windows lange Pfade nicht
  erlaubt. Alternative: lange Pfade einmalig erlauben (PowerShell als Administrator):

  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
      -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
  ```

## Einmalig einrichten

In einer normalen PowerShell im Projektordner:

```powershell
cd C:\dev\jahresshow
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Das legt die virtuelle Umgebung `.venv` an und installiert alle Abhängigkeiten aus
`pyproject.toml`: Pillow, pillow-heif, PySide6 sowie für die Entwicklung pytest, pytest-qt
und ruff. Dauer: einige Minuten; PySide6 belegt installiert rund 650 MB. Mit `-e` wirken
Änderungen am Code sofort, ohne neu zu installieren.

## Starten

```powershell
.venv\Scripts\python.exe -m slideshow
```

Oder kürzer: `.venv\Scripts\flashback.exe`. Mit einer Projektdatei oder Fotos/Videos:
`.venv\Scripts\python.exe -m slideshow gui "D:\Fotos\Projekt 2026.json"`.

Es läuft immer nur ein Fenster. Ein zweiter Start holt das vorhandene Fenster nach vorne und
öffnet die mitgegebenen Dateien dort.

### „No module named PIL“?

Dann wurde das System-Python gestartet (`python -m slideshow`) statt dem aus `.venv`. Die
Pakete sind nur in `.venv` installiert. Abhilfe: wie oben mit `.venv\Scripts\python.exe`
starten.

Die Umgebung zu „aktivieren“ (`.venv\Scripts\Activate.ps1`) ist nicht nötig. In einer
normalen PowerShell verhindert die Windows-Voreinstellung (Ausführungsrichtlinie
„Restricted“) das meist ohnehin. Wer es trotzdem will, erlaubt es einmalig für den eigenen
Benutzer: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Kommandozeile

```powershell
# Projekt als MP4 rendern (Kodierer wird automatisch erkannt)
.venv\Scripts\python.exe -m slideshow render tests\demo.json -o out\demo.mp4
# schnelle Vorschau in 480p, nur Elemente 3 bis 8
.venv\Scripts\python.exe -m slideshow render projekt.json --preview --from 3 --to 8
# welche Kodierer (Grafikkarte/Prozessor) funktionieren hier?
.venv\Scripts\python.exe -m slideshow encoders   # mit Einstellung und gemerkter Panne der Grafikkarte
```

Zwischendateien (umgewandelte Videos, Segmente, Vorschaubilder) liegen in
`%LOCALAPPDATA%\Flashback\cache` und können jederzeit gelöscht werden, dann wird neu
berechnet.

## Windows-Paket bauen

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev,build]"   # einmalig: PyInstaller
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Ergebnis ist der Ordner `dist\Flashback` (PyInstaller onedir) mit `Flashback.exe`,
`ffmpeg.exe`, `ffprobe.exe` samt FFmpeg-DLLs, `README.txt` für Endnutzer und dem Ordner
`Lizenzen`. `build.ps1` prüft vorher, dass FFmpeg zscale, tonemap, xfade, h264_qsv, libx264
und den HEVC-Decoder enthält. Weitergeben
wird der ganze Ordner, zum Beispiel als ZIP. Jeder Lauf baut von vorn; die Spezifikation steht
in `packaging\Flashback.spec`, die Endnutzer-README in `packaging\README.txt`.

Die gepackte exe ist ein Fensterprogramm. Auf der Kommandozeile rendert sie mit
`Flashback.exe --render projekt.json -o video.mp4`. PowerShell wartet bei Fensterprogrammen
nur, wenn die Ausgabe weitergeleitet wird, also mit `| Out-Host` am Ende.

Die Tests in `tests\test_package.py` prüfen das gebaute Paket (frische PowerShell ohne
Python, 50 Fotos, Hilfsprozesse, Render mit ffprobe-Prüfung). Ohne `dist\Flashback` werden
sie übersprungen.

## Prüfen vor jedem Commit

```powershell
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m pytest
```

- Nur die schnellen Tests (ohne echte Render-Läufe): `-m "not slow"`
- Testmedien in `tests\media\synthetic\` entstehen beim ersten Lauf automatisch per FFmpeg.
- Tests für echte Beispieldateien laufen nur, wenn `tests\media\real\` existiert.
- Die Screenshot-Tests legen Bilder des Fensters (1366×768 und 150 % Skalierung, Windows-10-
  und Windows-11-Stil) in `out\screens\` ab.

## Lizenz

Flashback steht unter der GNU General Public License Version 3 (siehe `LICENSE`).
Das fertige Paket enthält Fremdsoftware (u. a. FFmpeg, PySide6); deren Lizenzen liegen im
Ordner `Lizenzen` des Pakets und in `packaging/licenses/`.
