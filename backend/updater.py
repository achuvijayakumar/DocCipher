"""In-app update check and download.

Design constraints, in order of importance:

1. **The app stays usable offline.** The check is a single short HTTPS request
   made in the background. Any failure -- no internet, DNS, timeout, bad JSON --
   is swallowed and the app carries on. Nothing here can block startup.

2. **A downloaded executable is never trusted.** The manifest must publish a
   SHA-256, the download must match it, and a mismatch deletes the file rather
   than installing it. Without this, the updater would be a way for whoever
   controls (or intercepts) the manifest to run code on the user's machine.

3. **HTTPS only.** Plain HTTP can be rewritten in transit, which would defeat
   point 2 by letting an attacker publish their own hash.

The swap itself cannot happen while the app is running -- Windows locks a
running .exe -- so a small batch script waits for exit, replaces the file and
relaunches. The previous version is kept for rollback.
"""

import hashlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

USER_AGENT = "DocCipherBreaker-Updater"
CHECK_TIMEOUT = 8         # seconds; the check must never feel like a hang
DOWNLOAD_TIMEOUT = 300
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024


@dataclass
class UpdateState:
    """What the UI needs to know. Shared between the checker thread and HTTP."""

    checked: bool = False
    available: bool = False
    current: str = ""
    latest: Optional[str] = None
    notes: Optional[str] = None
    error: Optional[str] = None
    downloading: bool = False
    progress: int = 0
    ready: bool = False
    staged_path: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "checked": self.checked,
                "available": self.available,
                "current": self.current,
                "latest": self.latest,
                "notes": self.notes,
                "error": self.error,
                "downloading": self.downloading,
                "progress": self.progress,
                "ready": self.ready,
            }

    def update(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)


state = UpdateState()


def _parse_manifest(text: str) -> dict:
    """Read the key=value manifest, ignoring blank and comment lines."""
    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip().lower()] = value.strip()
    return data


def read_local_manifest(path: Path) -> dict:
    try:
        return _parse_manifest(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def _version_tuple(v: str) -> tuple:
    """Compare versions numerically: 1.0.10 is newer than 1.0.9."""
    parts = []
    for chunk in str(v).strip().split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    if not remote:
        return False
    try:
        return _version_tuple(remote) > _version_tuple(local)
    except Exception:
        return False


def _require_https(url: str, what: str) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError(
            f"The {what} is not HTTPS. Refusing to use it, because an update "
            "fetched over plain HTTP can be replaced in transit."
        )


def _fetch(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read(MAX_DOWNLOAD_BYTES + 1)


def check(current_version: str, manifest_path: Path) -> dict:
    """Ask the configured URL whether a newer build exists.

    Never raises. Errors are recorded in the state for display, not surfaced as
    exceptions, because a failed update check must not disturb the app.
    """
    state.update(current=current_version, error=None)

    local = read_local_manifest(manifest_path)
    update_url = local.get("update_url", "")

    if not update_url or "example.invalid" in update_url:
        # Not configured for updates. Silent by design -- this is the default
        # for a build that is not published anywhere.
        state.update(checked=True, available=False, error=None)
        return state.as_dict()

    try:
        _require_https(update_url, "update URL")
        raw = _fetch(update_url, CHECK_TIMEOUT).decode("utf-8", errors="replace")
        remote = _parse_manifest(raw)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        state.update(checked=True, available=False, error=f"{type(exc).__name__}: {exc}")
        return state.as_dict()

    latest = remote.get("version", "")
    available = is_newer(latest, current_version)

    state.update(
        checked=True,
        available=available,
        latest=latest or None,
        notes=remote.get("notes") or None,
        error=None,
    )
    return state.as_dict()


def check_in_background(current_version: str, manifest_path: Path) -> None:
    """Fire the check off the request path so startup is never delayed."""

    def run() -> None:
        try:
            check(current_version, manifest_path)
        except Exception as exc:      # belt and braces; must never crash the app
            state.update(checked=True, error=str(exc))

    threading.Thread(target=run, daemon=True).start()


def download(manifest_path: Path, dest_dir: Path) -> dict:
    """Download the published build and verify it against the manifest hash."""
    local = read_local_manifest(manifest_path)
    update_url = local.get("update_url", "")

    state.update(downloading=True, progress=0, ready=False, error=None)

    try:
        _require_https(update_url, "update URL")
        remote = _parse_manifest(
            _fetch(update_url, CHECK_TIMEOUT).decode("utf-8", errors="replace")
        )

        download_url = remote.get("download_url", "")
        expected = remote.get("sha256", "").strip().upper()

        if not download_url:
            raise ValueError("The update manifest has no download_url.")
        _require_https(download_url, "download URL")

        # A build with no published checksum cannot be verified, so it is not
        # installed. This is the single most important check in this file.
        if not expected:
            raise ValueError(
                "The update manifest carries no sha256 checksum. Refusing to "
                "install an unverified executable."
            )

        payload = _fetch(download_url, DOWNLOAD_TIMEOUT)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            raise ValueError("The update is larger than the 250 MB limit.")

        actual = hashlib.sha256(payload).hexdigest().upper()
        if actual != expected:
            raise ValueError(
                "Checksum mismatch. The downloaded file is not what the "
                "publisher released, so it has been discarded.\n\n"
                f"expected {expected}\nactual   {actual}"
            )

        dest_dir.mkdir(parents=True, exist_ok=True)
        staged = dest_dir / "DocCipherBreaker.new"
        staged.write_bytes(payload)

        state.update(
            downloading=False,
            progress=100,
            ready=True,
            staged_path=str(staged),
            latest=remote.get("version") or state.latest,
        )
    except Exception as exc:
        state.update(downloading=False, progress=0, ready=False, error=str(exc))

    return state.as_dict()


SWAP_SCRIPT = """@echo off
setlocal
:: Written by DocCipher Breaker to install a verified update.
:: The running executable is locked by Windows, so this waits for the app to
:: exit, swaps the file, and relaunches. The old build is kept for rollback.

set "TARGET=%~1"
set "STAGED=%~2"

:: Wait for the app to release the file (up to ~30 seconds).
set /a tries=0
:waitloop
set /a tries+=1
if %tries% gtr 60 goto :giveup
ping -n 2 127.0.0.1 >nul
2>nul (
  >>"%TARGET%" (call )
) || goto :waitloop

if exist "%TARGET%.old" del /q "%TARGET%.old" >nul 2>&1
move /y "%TARGET%" "%TARGET%.old" >nul 2>&1
move /y "%STAGED%" "%TARGET%" >nul 2>&1

if not exist "%TARGET%" (
    :: Swap failed -- put the previous build back.
    move /y "%TARGET%.old" "%TARGET%" >nul 2>&1
)

start "" "%TARGET%"
del /q "%~f0" >nul 2>&1
exit /b 0

:giveup
del /q "%STAGED%" >nul 2>&1
del /q "%~f0" >nul 2>&1
exit /b 1
"""


def apply_and_restart(target_exe: Path) -> dict:
    """Hand the swap to a detached script and ask the app to quit.

    Returns immediately; the caller is expected to shut down straight after.
    """
    staged = state.staged_path
    if not staged or not Path(staged).is_file():
        return {"started": False, "error": "No verified update has been downloaded."}

    script = Path(tempfile.gettempdir()) / "doccipher_update.bat"
    script.write_text(SWAP_SCRIPT, encoding="utf-8")

    try:
        subprocess.Popen(
            ["cmd", "/c", str(script), str(target_exe), str(staged)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
    except OSError as exc:
        return {"started": False, "error": str(exc)}

    return {"started": True}


def running_exe() -> Optional[Path]:
    """The .exe to replace, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None
