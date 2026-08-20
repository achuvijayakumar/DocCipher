"""Entry point for the desktop build.

DocCipher Breaker runs as a normal desktop application: a native window hosting
the UI, with no browser chrome, no address bar and no tab.

Under the hood the UI is served by a local HTTP server bound to 127.0.0.1 on an
ephemeral port. Nothing leaves the machine and no internet connection is needed
-- every asset (HTMX, CSS, fonts, icons) is bundled inside the executable. The
window is Edge WebView2, which ships with Windows 10 and 11.

Modes:
  DocCipherBreaker.exe                  -> native application window
  DocCipherBreaker.exe file1.docx ...   -> headless, used by the Explorer
                                           right-click entry
  DocCipherBreaker.exe --browser        -> serve only, open the default browser
                                           (fallback if WebView2 is missing)
"""

import ctypes
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP_TITLE = "DocCipher Breaker"
MIN_WIDTH, MIN_HEIGHT = 900, 640
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1180, 860


def _bundle_dir() -> Path:
    """Directory holding bundled data files (differs when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _ensure_streams() -> None:
    """A windowed build can have sys.stdout as None; give it somewhere to go."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _message_box(title: str, text: str, icon: int = 0x40) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, icon)
    except Exception:
        pass


def _free_port(attempts: int = 12) -> int:
    """Return a loopback port nothing else is using.

    Binding to port 0 makes the OS pick a free one, which is why the app no
    longer hardcodes 8000 and can never collide with another program (or a
    second copy of itself).

    There is a small race: the probe socket closes before uvicorn binds, so
    something else could take the port in between. SO_REUSEADDR is deliberately
    NOT set -- we want a second bind to fail rather than silently share the
    port -- and the port is re-probed to confirm it is still free before use.
    """
    for _ in range(attempts):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        # Confirm the port is genuinely free now that the probe has released it.
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", port))
            except OSError:
                continue      # someone grabbed it; ask for another
        return port

    raise RuntimeError("Could not find a free local port")


def _wait_until_serving(port: int, timeout: float = 25.0) -> bool:
    """Block until the server accepts connections, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.08)
    return False


def _start_server(port: int) -> threading.Thread:
    """Run uvicorn on a daemon thread so closing the window exits the process."""
    import uvicorn

    from backend.main import app

    def serve() -> None:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def _headless(paths: list[str]) -> int:
    """Unlock files with no UI. Used by the Explorer right-click entry."""
    from backend import database, dispatch
    from backend.main import DB_PATH

    database.init(DB_PATH)
    ok, failed = [], []
    for raw in paths:
        source = Path(raw)
        result = dispatch.unlock(str(source), str(source.parent)).as_dict()
        database.record(result)
        (ok if result["status"] == "success" else failed).append(result)

    lines = [f"Unlocked {len(ok)} of {len(paths)} file(s).", ""]
    lines += [f"OK    {Path(r['output_path']).name}" for r in ok]
    lines += [f"FAIL  {Path(r['input_path']).name} -- {r['error']}" for r in failed]
    _message_box(APP_TITLE, "\n".join(lines), 0x10 if failed else 0x40)
    return 1 if failed else 0


def _run_window(port: int) -> int:
    """Open the native application window. Returns non-zero if unavailable."""
    try:
        import webview
    except ImportError:
        return 2

    url = f"http://127.0.0.1:{port}/"

    try:
        webview.create_window(
            APP_TITLE,
            url,
            width=DEFAULT_WIDTH,
            height=DEFAULT_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            background_color="#0d0d12",
            text_select=True,          # users copy output paths out of the log
            confirm_close=False,
        )
        # edgechromium is the WebView2 backend that ships with Windows 10/11.
        # private_mode=False keeps localStorage between runs, and storage_path
        # pins it beside the app's own data instead of a temp directory Windows
        # is free to clear.
        from backend.main import DATA_DIR

        storage = DATA_DIR / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(storage),
        )
        return 0
    except Exception as exc:
        # Most likely: the WebView2 runtime is missing on an old Windows build.
        _message_box(
            APP_TITLE,
            "The application window could not be opened.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Opening in your browser instead. To restore the app window, "
            "install the Microsoft Edge WebView2 Runtime.",
            0x30,
        )
        return 3


def _run_browser(port: int) -> int:
    """Fallback: serve the UI and open the default browser."""
    import webbrowser

    webbrowser.open(f"http://127.0.0.1:{port}/")
    print(f"{APP_TITLE} is running at http://127.0.0.1:{port}/")
    print("Close this window to quit.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    _ensure_streams()

    # Make the bundled `backend` package importable when frozen.
    sys.path.insert(0, str(_bundle_dir()))

    argv = sys.argv[1:]
    files = [a for a in argv if not a.startswith("-")]
    force_browser = "--browser" in argv

    if files:
        return _headless(files)

    from backend.main import STATIC_DIR

    if not STATIC_DIR.exists():
        _message_box(APP_TITLE, f"UI assets are missing:\n{STATIC_DIR}", 0x10)
        return 2

    port = _free_port()
    _start_server(port)

    if not _wait_until_serving(port):
        _message_box(APP_TITLE, "The local server did not start in time.", 0x10)
        return 3

    if force_browser:
        return _run_browser(port)

    code = _run_window(port)
    if code == 0:
        return 0
    # The window could not open; fall back rather than leaving the user with
    # a process that appears to do nothing.
    return _run_browser(port)


if __name__ == "__main__":
    raise SystemExit(main())
