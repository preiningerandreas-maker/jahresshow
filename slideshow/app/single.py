"""Nur eine Programminstanz gleichzeitig. Ein zweiter Start meldet sich bei der laufenden
Instanz (die ihr Fenster nach vorne holt und mitgegebene Dateien öffnet) und beendet sich.

Die Sperre ist eine QLockFile (Absturz der ersten Instanz wird über die Prozessnummer
erkannt), die Nachricht läuft über eine QLocalServer-Verbindung (Named Pipe unter Windows)."""

from __future__ import annotations

import contextlib
import ctypes
import getpass
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

KEY_ENV = "FLASHBACK_INSTANCE_KEY"  # für Tests: eigene, unabhängige Instanz-Kennung
ASFW_ANY = -1


def default_key() -> str:
    env = os.environ.get(KEY_ENV)
    if env:
        return env
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        user = "user"
    return hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]


def allow_foreground() -> None:
    """Windows lässt nur das Vordergrund-Programm ein Fenster nach vorne holen. Der zweite
    Start ist im Vordergrund und erlaubt es hiermit der laufenden Instanz."""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)


class SingleInstance(QObject):
    activated = Signal(list)  # Dateien (str), die der zweite Start mitgebracht hat

    def __init__(self, key: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        key = key or default_key()
        self.server_name = f"flashback-{key}"
        lock_path = Path(QDir.tempPath()) / f"{self.server_name}.lock"
        self._lock = QLockFile(str(lock_path))
        # Nie wegen des Alters als verwaist ansehen, nur wenn der Prozess nicht mehr läuft
        self._lock.setStaleLockTime(0)
        self._server: QLocalServer | None = None
        self._buffers: dict[QLocalSocket, QByteArray] = {}

    @property
    def is_primary(self) -> bool:
        return self._server is not None

    def acquire(self) -> bool:
        """True, wenn dies die erste Instanz ist (dann nimmt sie Nachrichten an)."""
        if self._server is not None:
            return True
        if not self._lock.tryLock(0):
            return False
        QLocalServer.removeServer(self.server_name)  # Rest eines Absturzes
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.server_name):
            self._lock.unlock()
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def notify_running(self, files: list[str] | None = None, timeout: float = 5.0) -> bool:
        """Zweite Instanz: die laufende benachrichtigen. False, wenn sie nicht antwortet."""
        allow_foreground()
        payload = json.dumps({"files": [str(Path(f).resolve()) for f in files or []]})
        data = (payload + "\n").encode("utf-8")
        deadline = time.monotonic() + timeout
        while True:
            sock = QLocalSocket()
            sock.connectToServer(self.server_name)
            if sock.waitForConnected(500):
                sock.write(data)
                sock.flush()
                # Auf die Bestätigung warten, sonst könnte die Nachricht beim Trennen verloren
                # gehen
                ok = False
                reply = b""
                end = time.monotonic() + 3.0
                while time.monotonic() < end and b"\n" not in reply:
                    if sock.waitForReadyRead(200):
                        reply += bytes(sock.readAll().data())
                    elif sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                        break
                ok = reply.startswith(b"ok")
                sock.disconnectFromServer()
                return ok
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)  # erste Instanz startet vielleicht gerade erst

    def _on_connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = QByteArray()
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            if sock.bytesAvailable():
                self._on_ready(sock)

    def _on_ready(self, sock: QLocalSocket) -> None:
        buf = self._buffers.get(sock)
        if buf is None:
            return
        buf.append(sock.readAll())
        if buf.contains(b"\n"):
            self._dispatch(sock)

    def _on_disconnected(self, sock: QLocalSocket) -> None:
        self._buffers.pop(sock, None)  # ohne vollständige Nachricht: nichts tun
        with contextlib.suppress(RuntimeError):  # beim Beenden schon mit dem Server gelöscht
            sock.deleteLater()

    def _dispatch(self, sock: QLocalSocket) -> None:
        raw = bytes(self._buffers.pop(sock, QByteArray()).data())
        line = raw.split(b"\n", 1)[0].strip()
        try:
            files = [str(f) for f in json.loads(line.decode("utf-8")).get("files", [])]
        except (ValueError, AttributeError):
            files = []
        sock.write(b"ok\n")
        sock.flush()
        self.activated.emit(files)

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock.isLocked():
            self._lock.unlock()
