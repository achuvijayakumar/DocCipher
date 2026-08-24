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

import base64
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


# raw.githubusercontent.com is blocked on some corporate and ISP networks even
# where github.com and api.github.com resolve fine. When the manifest lives
# there, fall back to the Contents API, which serves the same bytes from a
# different host.
_RAW_PREFIX = "https://raw.githubusercontent.com/"


def _api_mirror(url: str) -> Optional[str]:
    """Translate a raw.githubusercontent.com URL into a Contents API URL."""
    if not url.startswith(_RAW_PREFIX):
        return None
    rest = url[len(_RAW_PREFIX):]
    parts = rest.split("/", 3)
    if len(parts) < 4:
        return None
    owner, repo, ref, path = parts
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"


class RateLimited(Exception):
    """The API mirror is temporarily refusing requests. Not a real failure."""


def _fetch_manifest(url: str, timeout: int) -> str:
    """Fetch the manifest, retrying via the GitHub API if the raw host fails."""
    try:
        return _fetch(url, timeout).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        mirror = _api_mirror(url)
        if not mirror:
            raise

        try:
            raw = _fetch(mirror, timeout).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # Unauthenticated API calls are capped at 60/hour per IP. Hitting
            # that is a "try later", not a broken update channel.
            if exc.code in (403, 429):
                raise RateLimited(
                    "GitHub is rate-limiting update checks from this network. "
                    "The next check should succeed."
                ) from exc
            raise

        payload = json.loads(raw)
        if "content" not in payload:
            raise ValueError(payload.get("message", "Unexpected response from GitHub."))
        return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")


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
        remote = _parse_manifest(_fetch_manifest(update_url, CHECK_TIMEOUT))
    except RateLimited:
        # Transient and self-correcting -- stay quiet rather than alarm the user.
        state.update(checked=True, available=False, error=None)
        return state.as_dict()
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
        remote = _parse_manifest(_fetch_manifest(update_url, CHECK_TIMEOUT))

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


# The swap runs in PowerShell rather than a batch file for one specific
# reason: a .bat that loops calling ping spawns a visible console window on
# every iteration -- up to sixty of them flashing on screen, which looks
# exactly like something malicious. PowerShell can sleep in-process with no
# window at all.
#
# The wait also has to test the lock correctly. Opening the .exe for append
# succeeds even while it is running; only an exclusive open proves Windows has
# released it.
SWAP_SCRIPT = r"""param([string]$Target, [string]$Staged, [switch]$Elevated)

$ErrorActionPreference = 'Stop'

# The swap runs detached with no console, so a failure leaves nothing behind to
# look at. Record what happened next to the app's own data, where the user can
# find it and paste it into a bug report.
$LogPath = Join-Path $env:LOCALAPPDATA "DocCipherBreaker\update.log"
function Write-Log($message) {
    try {
        $dir = Split-Path -Parent $LogPath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        $tag = if ($Elevated) { "elevated" } else { "normal" }
        Add-Content -LiteralPath $LogPath -Value "$stamp [$tag] $message" -Encoding utf8
    } catch { }
}

Write-Log "--- swap started ---"
Write-Log "target: $Target"
Write-Log "staged: $Staged"

function Test-Unlocked($path) {
    # Open for READ with no sharing. A running image still refuses this, but a
    # merely read-only location does not.
    #
    # Opening for ReadWrite was wrong: in Program Files that fails with access
    # denied even when nothing is running, so the script reported "still
    # running" and exited before it could ever elevate.
    try {
        $fs = [IO.File]::Open($path, 'Open', 'Read', 'None')
        $fs.Close()
        return $true
    } catch [System.UnauthorizedAccessException] {
        # Permission, not a lock. Elevation handles this later.
        return $true
    } catch { return $false }
}

# A PyInstaller --onefile build runs as TWO processes: the bootloader that was
# launched, and a child it spawns to run the unpacked code. Exiting the child
# leaves the parent alive and still holding an exclusive handle on the .exe, so
# waiting on the file handle alone waits forever. Stop anything still running
# the image we are about to replace.
function Stop-Holders($path) {
    $name = [IO.Path]::GetFileNameWithoutExtension($path)
    Get-Process -Name $name -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $path } |
        ForEach-Object {
            try { $_.CloseMainWindow() | Out-Null } catch { }
        }
    Start-Sleep -Milliseconds 800
    Get-Process -Name $name -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $path } |
        ForEach-Object {
            try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
}

# Give the app a few seconds to close cleanly on its own before forcing it.
Start-Sleep -Seconds 3
Stop-Holders $Target

# Wait for the app to exit and release its own executable.
#
# 30 seconds proved too short in practice: the server thread and the WebView2
# host can hold the image briefly after the window closes, and when the wait
# expired the update was silently discarded. Three minutes is far longer than
# a normal shutdown and costs nothing, since this runs detached.
$deadline = (Get-Date).AddSeconds(180)
$lastSweep = Get-Date
while ((Get-Date) -lt $deadline) {
    if (Test-Unlocked $Target) { break }
    # A child process can outlive the parent; sweep again periodically.
    if (((Get-Date) - $lastSweep).TotalSeconds -ge 5) {
        Stop-Holders $Target
        $lastSweep = Get-Date
    }
    Start-Sleep -Milliseconds 400
}

if (-not (Test-Unlocked $Target)) {
    Write-Log "FAILED: target still locked after waiting"
    # Keep the download rather than deleting it -- the user can retry, and a
    # silent disappearance is worse than a file left behind.
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    [System.Windows.Forms.MessageBox]::Show(
        "The update could not be installed because DocCipher Breaker is still running." +
        [Environment]::NewLine + [Environment]::NewLine +
        "Close it completely and try again from Settings.",
        "DocCipher Breaker") | Out-Null
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 1
}

# Installing into Program Files needs elevation. Without it Move-Item fails
# with access denied, which previously surfaced as "still running" and sent
# the user hunting for a process that was not there.
function Test-Writable($path) {
    $dir = Split-Path -Parent $path
    $probe = Join-Path $dir ".doccipher_write_test"
    try {
        [IO.File]::WriteAllText($probe, "x")
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch { return $false }
}

if (-not (Test-Writable $Target)) {
    Write-Log "target directory not writable; elevation required"
    if (-not $Elevated) {
        # Re-launch this same script elevated. The UAC prompt is expected here:
        # replacing a file in Program Files genuinely requires it.
        try {
            Start-Process -FilePath "powershell" -Verb RunAs -WindowStyle Hidden -ArgumentList @(
                "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", "`"$PSCommandPath`"",
                "-Target", "`"$Target`"", "-Staged", "`"$Staged`"", "-Elevated"
            )
            Write-Log "relaunched elevated; handing off"
            exit 0
        } catch {
            Write-Log "FAILED: elevation refused"
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
            [System.Windows.Forms.MessageBox]::Show(
                "This update needs administrator permission because DocCipher " +
                "Breaker is installed in Program Files." + [Environment]::NewLine +
                [Environment]::NewLine +
                "Nothing was changed. Try again and choose Yes when Windows asks.",
                "DocCipher Breaker") | Out-Null
            Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
            exit 1
        }
    }
}

$backup = "$Target.old"
try {
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    Write-Log "swapping files"
    Move-Item -LiteralPath $Target -Destination $backup -Force
    Move-Item -LiteralPath $Staged -Destination $Target -Force
    Write-Log "swap complete"
} catch {
    Write-Log "FAILED during swap"
    # Put the previous build back rather than leaving no executable at all.
    if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $Target)) {
        Move-Item -LiteralPath $backup -Destination $Target -Force
    }
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    [System.Windows.Forms.MessageBox]::Show(
        "The update could not be installed:" + [Environment]::NewLine +
        $_.Exception.Message + [Environment]::NewLine + [Environment]::NewLine +
        "Your existing version is unchanged.",
        "DocCipher Breaker") | Out-Null
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 1
}

# The update is already installed at this point. A relaunch failure must not
# be treated as a failed update -- the user can start the app themselves.
try {
    Write-Log "relaunching the app"
    Start-Process -FilePath $Target
} catch {
    Write-Log "relaunch failed (update still installed)"
    # Nothing to do; the new build is in place either way.
}

# Tidy up after ourselves rather than leaving a script in the temp directory.
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
exit 0
"""


def apply_and_restart(target_exe: Path) -> dict:
    """Hand the swap to a detached script and ask the app to quit.

    Returns immediately; the caller is expected to shut down straight after.
    """
    staged = state.staged_path
    if not staged or not Path(staged).is_file():
        return {"started": False, "error": "No verified update has been downloaded."}

    script = Path(tempfile.gettempdir()) / "doccipher_update.ps1"
    script.write_text(SWAP_SCRIPT, encoding="utf-8")

    # -WindowStyle Hidden plus CREATE_NO_WINDOW: the user should never see a
    # console appear while the app updates itself.
    command = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script),
        "-Target", str(target_exe),
        "-Staged", str(staged),
    ]

    try:
        # CREATE_NO_WINDOW hides the console; CREATE_NEW_PROCESS_GROUP detaches
        # it from this process so it survives our exit.
        #
        # DETACHED_PROCESS is deliberately NOT used: it gives the child no
        # console at all, and powershell.exe silently does nothing without one.
        # That was the reason updates appeared to download and then never
        # install.
        subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {"started": False, "error": str(exc)}

    return {"started": True}


def running_exe() -> Optional[Path]:
    """The .exe to replace, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None
