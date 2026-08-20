"""Entry point for the frozen (PyInstaller) build.

Two modes:
  DocCipherBreaker.exe                -> starts the local server, opens the UI
  DocCipherBreaker.exe file1.docx ...  -> headless crack, used by the Explorer
                                          context-menu entry

The frozen executable is built with console=False, so stdout/stderr may be
missing entirely. Output is mirrored to a log file, and headless results are
reported with a message box rather than printed.
"""

import ctypes
import os
import sys
from pathlib import Path


def _bundle_dir() -> Path:
    """Directory containing bundled data files (differs when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _ensure_streams() -> None:
    """A windowed build can have sys.stdout is None; give it somewhere to go."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _message_box(title: str, text: str, icon: int = 0x40) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, icon)
    except Exception:
        pass


def _headless(paths: list[str]) -> int:
    from backend import database
    from backend.cracker import DocCracker
    from backend.main import DB_PATH

    database.init(DB_PATH)
    ok, failed = [], []
    for raw in paths:
        source = Path(raw)
        result = DocCracker(str(source), str(source.parent)).unlock().as_dict()
        database.record(result)
        (ok if result["status"] == "success" else failed).append(result)

    lines = [f"Unlocked {len(ok)} of {len(paths)} file(s).", ""]
    lines += [f"OK   {Path(r['output_path']).name}" for r in ok]
    lines += [f"FAIL {Path(r['input_path']).name} -- {r['error']}" for r in failed]
    _message_box(
        "DocCipher Breaker",
        "\n".join(lines),
        0x10 if failed else 0x40,   # error icon vs information icon
    )
    return 1 if failed else 0


def _find_free_port(preferred: int = 8000) -> int:
    """Reuse the preferred port if free, otherwise let the OS pick one."""
    import socket

    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def main() -> int:
    _ensure_streams()

    # Make the bundled `backend` package importable when frozen.
    sys.path.insert(0, str(_bundle_dir()))

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        return _headless(args)

    import threading
    import time
    import webbrowser

    import uvicorn

    from backend.main import STATIC_DIR, app

    if not STATIC_DIR.exists():
        _message_box("DocCipher Breaker", f"UI assets missing:\n{STATIC_DIR}", 0x10)
        return 2

    port = _find_free_port(8000)
    url = f"http://127.0.0.1:{port}"

    def open_ui() -> None:
        time.sleep(1.4)
        webbrowser.open(url)

    threading.Thread(target=open_ui, daemon=True).start()

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception as exc:
        _message_box("DocCipher Breaker", f"Server failed to start:\n{exc}", 0x10)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
