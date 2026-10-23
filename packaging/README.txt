Jahresshow
==========

Jahresshow macht aus Fotos und Videos eine Slideshow mit Musik und Texten und speichert sie
als MP4-Video, zum Beispiel für einen Jahresrückblick am Fernseher.


Starten
-------

1. Den ganzen Ordner "Jahresshow" auf den Computer kopieren, zum Beispiel nach
   "Dokumente". Kommt das Programm als ZIP-Datei, diese zuerst entpacken
   (rechte Maustaste, "Alle extrahieren"). Aus der ZIP-Datei heraus startet es nicht.
2. Im Ordner doppelt auf "Jahresshow.exe" klicken.
3. Fotos und Videos mit "Ordner" oder "Dateien" oben in der Leiste hinzufügen oder
   aus dem Explorer in das Fenster ziehen. Mit "Video erstellen" entsteht das MP4.

Für eine Verknüpfung auf dem Desktop: rechte Maustaste auf "Jahresshow.exe",
"Senden an", "Desktop (Verknüpfung erstellen)".

Die Dateien im Ordner gehören zusammen. Bitte nichts daraus löschen oder verschieben,
auch nicht ffmpeg.exe und ffprobe.exe: Damit entstehen die Videos.


Systemanforderungen
-------------------

- Windows 10 oder Windows 11, 64 Bit
- 8 GB Arbeitsspeicher
- Freier Speicherplatz: etwa das Dreifache der verwendeten Fotos und Videos, für
  Zwischendateien und das fertige Video
- Eine Internetverbindung ist nicht nötig.

Auf älteren oder langsameren Computern funktioniert alles, dauert aber länger. Videos
vom Handy werden nach dem Einlesen einmal umgewandelt, der Fortschritt steht unten im
Fenster. Danach geht es schnell.


Windows-SmartScreen-Warnung
---------------------------

Beim ersten Start kann Windows melden: "Der Computer wurde durch Windows geschützt".
Das kommt bei Programmen, die Windows noch nicht kennt, weil sie nicht über einen
kostenpflichtigen Herausgeber-Nachweis verfügen. Es heißt nicht, dass ein Virus
gefunden wurde.

Wenn Sie das Programm von einer Person haben, der Sie vertrauen:
1. In der Meldung auf "Weitere Informationen" klicken.
2. Dann auf "Trotzdem ausführen" klicken.

Die Meldung erscheint danach nicht mehr. Kam das Programm per E-Mail oder Download und
startet trotzdem nicht: rechte Maustaste auf die ZIP-Datei, "Eigenschaften", unten bei
"Sicherheit" das Häkchen "Zulassen" setzen, "OK", dann neu entpacken.

Wenn Sie nicht wissen, woher das Programm stammt, starten Sie es nicht.


Wo landen die Daten?
--------------------

- Ihre Projekte speichern Sie selbst, zum Beispiel unter "Dokumente".
- Zwischendateien (umgewandelte Videos, Vorschaubilder) liegen in
  %LOCALAPPDATA%\Jahresshow. Sie können diesen Ordner jederzeit löschen, wenn das
  Programm geschlossen ist. Die Dateien werden bei Bedarf neu erstellt.


Entfernen
---------

Den Ordner "Jahresshow" löschen und, wenn gewünscht, %LOCALAPPDATA%\Jahresshow.
Das Programm ändert nichts an Windows und muss nicht deinstalliert werden.


Lizenzen
--------

Jahresshow verwendet freie Software, unter anderem FFmpeg, Qt und Python. Die
Lizenztexte stehen im Ordner "Lizenzen", eine Übersicht in Lizenzen\UEBERSICHT.txt.
