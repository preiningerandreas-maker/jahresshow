"""Hilfen für Tests mit dem fertigen Paket: Prozesse, Fenster und Knöpfe unter Windows
(ctypes und UI Automation über PowerShell, ohne zusätzliche Pakete)."""

from __future__ import annotations

import base64
import ctypes
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
WM_CLOSE = 0x0010


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


@dataclass(frozen=True)
class Proc:
    pid: int
    parent: int
    name: str


def processes() -> list[Proc]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    out: list[Proc] = []
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append(Proc(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def descendants(pid: int) -> list[Proc]:
    """Alle laufenden Unterprozesse (auch Enkel) eines Prozesses."""
    procs = processes()
    found: list[Proc] = []
    parents = {pid}
    while True:
        new = [p for p in procs if p.parent in parents and p not in found and p.pid != pid]
        if not new:
            return found
        found += new
        parents |= {p.pid for p in new}


def alive(proc: Proc) -> bool:
    """Läuft der Prozess noch (gleiche PID und gleicher Programmname)?"""
    return any(p.pid == proc.pid and p.name == proc.name for p in processes())


def exit_code(pid: int) -> int | None:
    """Rückgabewert eines beendeten Prozesses, None solange er läuft."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0
    try:
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return None if code.value == STILL_ACTIVE else code.value
    finally:
        kernel32.CloseHandle(handle)


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    pid: int


_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def visible_windows(pids: set[int]) -> list[Window]:
    """Sichtbare Fenster der obersten Ebene, die zu einem der Prozesse gehören."""
    out: list[Window] = []

    def callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append(Window(hwnd, buf.value, pid.value))
        return True

    user32.EnumWindows(_ENUM(callback), 0)
    return out


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def powershell(script: str, *, env: dict[str, str] | None = None,
               timeout: float = 600) -> subprocess.CompletedProcess[str]:
    """Skript in einer frischen Windows PowerShell (ohne Profil). Als -EncodedCommand
    übergeben, damit Umlaute in Pfaden heil ankommen."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def invoke_button(pid: int, name: str, timeout: float = 20) -> bool:
    """Knopf eines Programms per UI Automation drücken (Qt meldet seine Knöpfe dort an)."""
    script = f"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$cond = New-Object System.Windows.Automation.AndCondition(
  (New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, {pid})),
  (New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, {ps_quote(name)})),
  (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button)))
$deadline = (Get-Date).AddSeconds({timeout})
while ((Get-Date) -lt $deadline) {{
  $b = $AE::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
  if ($b) {{
    $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    exit 0
  }}
  Start-Sleep -Milliseconds 200
}}
exit 1
"""
    return powershell(script, timeout=timeout + 30).returncode == 0


def wait_for(condition, timeout: float, interval: float = 0.25) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())
