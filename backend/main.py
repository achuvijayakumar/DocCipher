"""DocCipher Breaker -- FastAPI backend.

Serves the UI, accepts uploaded or local-path .docx files, strips editing
restrictions, records history in SQLite, and hands back a download.
"""

import argparse
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import database
from . import dispatch, updater
from .cracker import CrackError, human_size, unique_path
from .icons import icon
from .models import (
    BatchRequest,
    BatchResponse,
    CrackResponse,
    HistoryEntry,
    InspectResponse,
    StatsResponse,
)

APP_NAME = "DocCipher Breaker"
VERSION = "1.0.8"
AUTHOR = "Achu Vijayakumar"
YEAR = "2026"
EDU_NOTICE = "FOR EDUCATIONAL PURPOSES ONLY"
WINDOW_TITLE = f"{APP_NAME} — Created by {AUTHOR}"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
PAGE_SIZE = 10  # activity log rows per page

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def app_data_dir() -> Path:
    """Per-user writable directory. Program Files is read-only after install."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(root) / "DocCipherBreaker"
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = app_data_dir()
OUTPUT_DIR = DATA_DIR / "unlocked"      # fallback until the user picks a folder
DB_PATH = DATA_DIR / "history.db"


def manifest_path() -> Path:
    """version.txt sits next to the executable once installed."""
    exe = updater.running_exe()
    if exe:
        return exe.parent / "version.txt"
    return Path(__file__).resolve().parent.parent / "version.txt"


def default_save_dir() -> Path:
    """The folder suggested the first time the user is asked."""
    docs = Path(os.path.expanduser("~")) / "Documents"
    base = docs if docs.is_dir() else Path(os.path.expanduser("~"))
    return base / "DocCipher Breaker"


def save_dir() -> Path:
    """Where unlocked files are written.

    Read per request rather than cached at import, so changing it in Settings
    applies straight away. Falls back to the app data directory if the chosen
    folder has since been deleted or made read-only.
    """
    chosen = None
    try:
        chosen = database.get_setting("save_dir")
    except Exception:
        pass

    if chosen:
        path = Path(chosen)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".doccipher_write_test"
            probe.touch()
            probe.unlink()
            return path
        except OSError:
            pass          # unwritable now -- fall through to the safe default

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR

# token -> absolute path, for /download. Keeps real paths out of URLs.
_downloads: dict[str, str] = {}

# The splash screen plays once per server run, not on every page load.
_splash_shown = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    database.init(DB_PATH)
    # Background and non-blocking: with no internet this simply reports nothing
    # available, and the app starts exactly as it always does.
    updater.check_in_background(VERSION, manifest_path())
    yield


app = FastAPI(
    title=APP_NAME, version=VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------- helpers


def _register_download(path: str) -> str:
    token = secrets.token_urlsafe(16)
    _downloads[token] = path
    return token


def _run_crack(source: Path, output_dir: Path, cleanup_source: bool = False) -> dict:
    """Unlock one file (any supported format), record it, register a download."""
    result = dispatch.unlock(str(source), str(output_dir)).as_dict()

    if cleanup_source:
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass

    result["history_id"] = database.record(result)
    if result["status"] == "success" and result.get("output_path"):
        result["download_token"] = _register_download(result["output_path"])
    return result


_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(raw: Optional[str]) -> str:
    """Reduce an uploaded filename to something safe to create on Windows.

    Path(...).name alone is not enough: it strips directories but leaves
    characters like < > | ? * that make open() fail with EINVAL, and leaves
    reserved device names like CON.docx.
    """
    name = Path(raw or "").name
    name = _ILLEGAL_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "document.docx"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    stem = stem[:120] or "document"
    return f"{stem}.{ext}" if ext else stem


def _safe_local_path(raw: str) -> Path:
    """Resolve a user-supplied local path, rejecting anything that is not a .docx file."""
    path = Path(raw.strip().strip('"')).expanduser()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(400, f"Cannot resolve path: {raw}") from exc
    if not path.is_file():
        raise HTTPException(400, f"Not a file: {path}")
    if dispatch.detect_format(str(path)) is None:
        raise HTTPException(
            400, f"Unsupported file type: {path.name}. Only .docx, .pdf and .xlsx are supported."
        )
    return path


def current_theme() -> str:
    """The persisted theme, defaulting to light."""
    try:
        value = database.get_setting("theme", "light")
    except Exception:
        return "light"
    return value if value in ("light", "dark") else "light"


def _with_theme(html: str) -> str:
    """Stamp the persisted theme onto <html> before the page is sent.

    Doing this server-side means the correct palette is present in the very
    first byte the renderer sees -- no flash, and no dependency on localStorage.
    """
    return html.replace('<html lang="en" data-theme="light">',
                        f'<html lang="en" data-theme="{current_theme()}">', 1)


# ------------------------------------------------------------------ pages


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Splash screen on first visit; the app itself on every later visit.

    The splash is shown once per server run so that relaunching the app feels
    branded, but reloading the page during a session does not replay it.
    """
    global _splash_shown
    if _splash_shown:
        return HTMLResponse(_with_theme((STATIC_DIR / "index.html").read_text(encoding="utf-8")))
    _splash_shown = True
    return HTMLResponse(_with_theme((STATIC_DIR / "splash.html").read_text(encoding="utf-8")))


@app.get("/app", response_class=HTMLResponse)
def application() -> HTMLResponse:
    """The main UI, reachable directly and used as the splash screen's target."""
    return HTMLResponse(_with_theme((STATIC_DIR / "index.html").read_text(encoding="utf-8")))


@app.get("/splash", response_class=HTMLResponse)
def splash() -> HTMLResponse:
    """Replay the splash screen on demand."""
    return HTMLResponse(_with_theme((STATIC_DIR / "splash.html").read_text(encoding="utf-8")))


@app.get("/about", response_class=HTMLResponse)
def about() -> HTMLResponse:
    """About dialog content, loaded into the UI's modal by HTMX."""
    return HTMLResponse(_render_about())


@app.get("/api/update")
def api_update_status() -> JSONResponse:
    """Current update state. Cheap; the network call happens in the background."""
    return JSONResponse(updater.state.as_dict())


@app.post("/api/update/check")
def api_update_check() -> JSONResponse:
    """Re-run the check on demand (the Settings dialog offers this)."""
    return JSONResponse(updater.check(VERSION, manifest_path()))


@app.post("/api/update/download")
def api_update_download() -> JSONResponse:
    """Download the published build and verify its checksum before staging it."""
    return JSONResponse(updater.download(manifest_path(), DATA_DIR / "updates"))


@app.post("/api/update/apply")
def api_update_apply() -> JSONResponse:
    """Swap in the verified build and relaunch.

    Only meaningful in the packaged app -- running from source there is no
    single .exe to replace.
    """
    exe = updater.running_exe()
    if not exe:
        raise HTTPException(
            400, "Updates apply to the installed application, not a source checkout."
        )

    result = updater.apply_and_restart(exe)
    if not result.get("started"):
        raise HTTPException(500, result.get("error") or "Could not start the updater.")

    # Give the response time to reach the UI before the process exits.
    def quit_soon() -> None:
        """Exit so the swap script can replace the executable.

        A PyInstaller --onefile build runs as two processes: the bootloader
        that was launched, and the child it spawns to run this code. Do not use
        ``taskkill /T`` on that tree here: the swap script was launched by this
        child, so Windows considers it part of the same tree and kills the
        updater along with the app. The bootloader exits after this child does;
        if it lingers, the swap script's path-scoped Stop-Holders sweep handles
        it without touching powershell.exe.
        """
        time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=quit_soon, daemon=True).start()
    return JSONResponse({"restarting": True})


@app.get("/api/save-dir")
def api_get_save_dir() -> JSONResponse:
    """Current save folder, plus whether the user has ever chosen one."""
    chosen = database.get_setting("save_dir")
    return JSONResponse(
        {
            "path": str(save_dir()),
            "configured": bool(chosen),
            "suggested": str(default_save_dir()),
        }
    )


@app.post("/api/save-dir")
def api_set_save_dir(path: str = Form(...)) -> JSONResponse:
    """Persist the folder unlocked files are written to."""
    candidate = Path(path.strip()).expanduser()

    if not candidate.is_absolute():
        raise HTTPException(400, "Please choose a full folder path.")

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".doccipher_write_test"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise HTTPException(400, f"That folder cannot be written to: {exc}") from exc

    database.set_setting("save_dir", str(candidate))
    return JSONResponse({"path": str(candidate), "configured": True})


@app.post("/api/browse-folder")
def api_browse_folder() -> JSONResponse:
    """Open the native folder picker.

    Only works when running inside the desktop window; the browser fallback
    has no way to show an OS dialog, so it reports unavailable and the UI asks
    the user to type a path instead.
    """
    try:
        import webview
    except ImportError:
        return JSONResponse({"available": False, "path": None})

    windows = getattr(webview, "windows", None)
    if not windows:
        return JSONResponse({"available": False, "path": None})

    try:
        result = windows[0].create_file_dialog(
            webview.FOLDER_DIALOG, directory=str(save_dir())
        )
    except Exception:
        return JSONResponse({"available": False, "path": None})

    chosen = result[0] if result else None
    return JSONResponse({"available": True, "path": chosen})


@app.get("/api/theme")
def api_get_theme() -> JSONResponse:
    return JSONResponse({"theme": current_theme()})


@app.post("/api/theme")
def api_set_theme(theme: str = Form(...)) -> JSONResponse:
    """Persist the chosen theme server-side.

    Stored in SQLite rather than only in localStorage so the choice survives a
    WebView2 profile reset and is shared by the app window and the browser.
    """
    if theme not in ("light", "dark"):
        raise HTTPException(400, "theme must be 'light' or 'dark'")
    database.set_setting("theme", theme)
    return JSONResponse({"theme": theme})


@app.get("/favicon.ico")
def favicon() -> Response:
    icon = STATIC_DIR / "favicon.ico"
    if icon.exists():
        return FileResponse(icon, media_type="image/x-icon")
    return Response(status_code=204)


# ------------------------------------------------------------- HTMX routes


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    format: str = Query("html", pattern="^(html|json)$"),
) -> Response:
    """Accept an uploaded .docx and crack it.

    The modal-driven UI asks for `?format=json`; the plain HTMX fallback (and
    anything scripted against this endpoint) still gets a rendered card.
    """

    def fail(message: str, step: int = 1) -> Response:
        if format == "json":
            return JSONResponse(
                {
                    "status": "failed",
                    "input_name": name,
                    "error": message,
                    "failed_step": step,
                    "duration": 0.0,
                }
            )
        return HTMLResponse(_render_error(name, message))

    name = safe_filename(file.filename)
    if dispatch.detect_format(name) is None:
        return fail("Only .docx, .pdf and .xlsx files are accepted.")

    staged = unique_path(OUTPUT_DIR / f".incoming_{name}")
    written = 0
    try:
        with open(staged, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File exceeds 100 MB limit")
                out.write(chunk)
    except HTTPException as exc:
        staged.unlink(missing_ok=True)
        return fail(str(exc.detail))
    finally:
        await file.close()

    # The staged copy carries the ".incoming_" prefix; give the output the real stem.
    renamed = staged.with_name(name)
    renamed = unique_path(renamed)
    staged.replace(renamed)

    result = _run_crack(renamed, save_dir(), cleanup_source=True)
    if format == "json":
        return JSONResponse(result)
    return HTMLResponse(_render_result(result))


@app.post("/crack-path", response_class=HTMLResponse)
def crack_path(path: str = Form(...), in_place_dir: bool = Form(True)) -> HTMLResponse:
    """Crack a file already on disk, writing output next to it."""
    try:
        source = _safe_local_path(path)
    except HTTPException as exc:
        return HTMLResponse(_render_error(Path(path).name, str(exc.detail)))

    out_dir = source.parent if in_place_dir else OUTPUT_DIR
    result = _run_crack(source, out_dir)
    return HTMLResponse(_render_result(result))


@app.get("/history", response_class=HTMLResponse)
def history_fragment(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query("all"),
    file_format: Optional[str] = Query("all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(PAGE_SIZE, ge=5, le=100),
) -> HTMLResponse:
    total = database.count_history(search=search, status=status, file_format=file_format)
    pages = max(1, -(-total // per_page))          # ceiling division
    page = min(page, pages)                        # a filter change can shrink the range

    rows = database.list_history(
        search=search,
        status=status,
        file_format=file_format,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    return HTMLResponse(
        _render_history(rows)
        + _render_pagination(page, pages, total, per_page, len(rows))
    )


@app.get("/stats", response_class=HTMLResponse)
def stats_fragment() -> HTMLResponse:
    s = database.stats()
    return HTMLResponse(
        f'<div class="stat"><span class="k">TOTAL</span>'
        f'<span class="v">{s["total"]}</span></div>'
        f'<div class="stat ok"><span class="k">UNLOCKED</span>'
        f'<span class="v">{s["successes"]}</span></div>'
        f'<div class="stat bad"><span class="k">FAILED</span>'
        f'<span class="v">{s["failures"]}</span></div>'
        f'<div class="stat"><span class="k">SAVED</span>'
        f'<span class="v">{human_size(s["bytes_saved"])}</span></div>'
    )


# -------------------------------------------------------------- JSON API


@app.post("/api/crack", response_model=CrackResponse)
def api_crack(path: str = Form(...), output_dir: Optional[str] = Form(None)) -> CrackResponse:
    source = _safe_local_path(path)
    out_dir = Path(output_dir) if output_dir else source.parent
    return CrackResponse(**_run_crack(source, out_dir))


@app.post("/api/batch", response_model=BatchResponse)
def api_batch(req: BatchRequest) -> BatchResponse:
    results = []
    for raw in req.paths:
        try:
            source = _safe_local_path(raw)
        except HTTPException as exc:
            results.append(
                CrackResponse(
                    status="failed",
                    input_name=Path(raw).name,
                    input_path=raw,
                    error=str(exc.detail),
                )
            )
            continue
        out_dir = Path(req.output_dir) if req.output_dir else source.parent
        results.append(CrackResponse(**_run_crack(source, out_dir)))

    succeeded = sum(r.status == "success" for r in results)
    return BatchResponse(
        total=len(results), succeeded=succeeded, failed=len(results) - succeeded, results=results
    )


@app.get("/api/inspect", response_model=InspectResponse)
def api_inspect(path: str = Query(...)) -> InspectResponse:
    source = _safe_local_path(path)
    try:
        return InspectResponse(**dispatch.inspect(str(source)))
    except CrackError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/history", response_model=list[HistoryEntry])
def api_history(
    search: Optional[str] = None,
    status: Optional[str] = "all",
    file_format: Optional[str] = "all",
    limit: int = 100,
    offset: int = 0,
) -> list[HistoryEntry]:
    rows = database.list_history(
        search=search, status=status, file_format=file_format, limit=limit, offset=offset
    )
    return [HistoryEntry(**{k: v for k, v in row.items() if k != "logs"}) for row in rows]


@app.get("/api/stats", response_model=StatsResponse)
def api_stats() -> StatsResponse:
    return StatsResponse(**database.stats())


@app.delete("/api/history")
def api_clear_history() -> JSONResponse:
    return JSONResponse({"deleted": database.clear()})


@app.get("/download/{token}")
def download(token: str) -> FileResponse:
    path = _downloads.get(token)
    if not path or not Path(path).is_file():
        raise HTTPException(404, "Download expired or file moved")
    return FileResponse(
        path,
        filename=Path(path).name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _recorded_output(history_id: int) -> Path:
    entry = database.get(history_id)
    if not entry or not entry.get("unlocked_path"):
        raise HTTPException(404, "No output file recorded for that entry")
    target = Path(entry["unlocked_path"])
    if not target.exists():
        raise HTTPException(404, "File no longer exists")
    return target


@app.post("/reveal/{history_id}")
def reveal(history_id: int) -> JSONResponse:
    """Open the containing folder with the file selected. Desktop app only."""
    target = _recorded_output(history_id)
    if sys.platform == "win32":
        # /select, highlights the file rather than just opening the folder.
        # The path is passed as a separate argument, never through a shell.
        subprocess.Popen(["explorer", f"/select,{target}"])  # noqa: S603
    return JSONResponse({"opened": str(target.parent)})


@app.post("/open/{history_id}")
def open_output(history_id: int) -> JSONResponse:
    """Open the unlocked file in its default application."""
    target = _recorded_output(history_id)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 -- opening the user's own output is the feature
    return JSONResponse({"opened": str(target)})


@app.post("/api/reveal-save-dir")
def reveal_save_dir() -> JSONResponse:
    """Open the folder unlocked files are written to."""
    folder = save_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606
    return JSONResponse({"opened": str(folder)})


# ------------------------------------------------------------- rendering


def _render_result(result: dict) -> str:
    if result["status"] != "success":
        return _render_error(
            result.get("input_name") or "target", result.get("error") or "Unknown failure",
            result.get("logs", []),
        )

    logs = _render_logs(result.get("logs", []))
    token = result.get("download_token")
    out_name = escape(result.get("output_name") or "")
    in_name = escape(result.get("input_name") or "")
    out_path = escape(result.get("output_path") or "")
    hid = result.get("history_id")
    locks = result.get("protections_found", 0)
    fmt_label = escape((result.get("format") or "docx").upper())
    banner = "Restrictions removed" if locks else "No restrictions found"

    return f"""
<div class="result success" data-status="success">
  <div class="result-head">
    <span class="badge ok">{icon("check")} Unlocked Successfully!</span>
    <span class="timer">{result.get('duration', 0)}s</span>
  </div>
  <div class="terminal">{logs}</div>
  <div class="result-grid">
    <div><span class="k">FILE</span><span class="v">{in_name}</span></div>
    <div><span class="k">FORMAT</span><span class="v">{fmt_label}</span></div>
    <div><span class="k">STATUS</span><span class="v ok">{banner}</span></div>
    <div><span class="k">OUTPUT</span><span class="v">{out_name}</span></div>
    <div><span class="k">SIZE</span><span class="v">{human_size(result.get('size_before',0))} &rarr; {human_size(result.get('size_after',0))}</span></div>
  </div>
  <div class="path-line" title="{out_path}">{out_path}</div>
  <div class="actions">
    <a class="btn primary" href="/download/{token}" download>{icon("download")} DOWNLOAD</a>
    <button class="btn" onclick="revealFile({hid})">{icon("folder")} OPEN FILE</button>
    <button class="btn" onclick="resetZone()">{icon("bolt")} NEW</button>
  </div>
</div>
<script>refreshSidePanels();</script>
"""


def _render_error(name: str, error: str, logs: Optional[list] = None) -> str:
    log_html = f'<div class="terminal">{_render_logs(logs)}</div>' if logs else ""
    return f"""
<div class="result failure" data-status="failed">
  <div class="result-head">
    <span class="badge bad">{icon("alert")} Processing Failed</span>
  </div>
  {log_html}
  <div class="error-box">
    <span class="k">TARGET</span><span class="v">{escape(name)}</span>
    <span class="k">REASON</span><span class="v bad">{escape(error)}</span>
  </div>
  <div class="actions">
    <button class="btn" onclick="resetZone()">{icon("refresh")} RETRY</button>
  </div>
</div>
<script>refreshSidePanels();</script>
"""


def _render_logs(logs: Optional[list]) -> str:
    if not logs:
        return ""
    out = []
    for line in logs:
        cls = "log-line"
        if line.startswith("!!!"):
            cls += " error"
        elif line.startswith(">>>") or "Removed" in line or "confirmed" in line:
            cls += " success"
        elif "already unlocked" in line:
            cls += " warn"
        out.append(f'<div class="{cls}">{escape(line)}</div>')
    return "".join(out)


def _render_history(rows: list) -> str:
    """The activity log table.

    Status uses inline SVG rather than emoji so it takes the theme colour and
    renders identically on every machine.
    """
    if not rows:
        return '<div class="empty">No activity yet. Drop a document above to get started.</div>'

    body = []
    for r in rows:
        ok = r["status"] == "success"
        fmt = (r.get("file_format") or "docx").upper()
        fmt_badge = f'<span class="fmt fmt-{fmt.lower()}">{escape(fmt)}</span>'
        pill = (
            f'<span class="pill ok">{icon("check")}Completed</span>'
            if ok
            else f'<span class="pill bad">{icon("close")}Failed</span>'
        )
        out = escape(r.get("unlocked_filename") or "—")
        detail = escape(r.get("error") or "")
        sizes = (
            f'{human_size(r.get("file_size_before", 0))} &rarr; {human_size(r.get("file_size_after", 0))}'
            if ok
            else "—"
        )
        stamp = escape((r.get("timestamp") or "").replace("T", " "))
        body.append(
            "<tr>"
            f'<td>{escape(r["original_filename"])}</td>'
            f"<td>{fmt_badge}</td>"
            f'<td class="dim">{out}</td>'
            f"<td>{pill}</td>"
            f'<td class="dim">{sizes}</td>'
            f'<td class="dim">{stamp}</td>'
            f'<td class="bad small">{detail}</td>'
            "</tr>"
        )

    return f"""
<table class="history">
  <thead><tr>
    <th>ORIGINAL FILE</th><th>FORMAT</th><th>OUTPUT FILE</th><th>STATUS</th>
    <th>SIZE</th><th>TIMESTAMP</th><th>NOTE</th>
  </tr></thead>
  <tbody>{''.join(body)}</tbody>
</table>
"""


def _render_pagination(
    page: int, pages: int, total: int, per_page: int, showing: int
) -> str:
    """Page controls for the activity log.

    Each control re-requests /history through HTMX and includes the filter
    inputs, so paging never silently drops the current search or filters.
    """
    if total == 0:
        return ""

    first = (page - 1) * per_page + 1
    last = first + showing - 1

    def button(target: int, label: str, disabled: bool, aria: str) -> str:
        if disabled:
            return (
                f'<button class="btn ghost page-btn" disabled aria-label="{aria}">{label}</button>'
            )
        return (
            f'<button class="btn ghost page-btn" aria-label="{aria}" '
            f'hx-get="/history?page={target}" hx-target="#history-table" '
            f'hx-include="#history-search,#history-status,#history-format">{label}</button>'
        )

    prev_icon = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>'
    next_icon = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg>'

    return f"""
<div class="pager">
  <span class="pager-count">
    Showing <b>{first}–{last}</b> of <b>{total}</b>
  </span>
  <div class="pager-controls">
    {button(1, "First", page <= 1, "First page")}
    {button(page - 1, prev_icon + "Prev", page <= 1, "Previous page")}
    <span class="pager-page">Page {page} of {pages}</span>
    {button(page + 1, "Next" + next_icon, page >= pages, "Next page")}
    {button(pages, "Last", page >= pages, "Last page")}
  </div>
</div>
"""


def _render_about() -> str:
    """About dialog body, injected into the UI modal."""
    return f"""
<div class="about">
  <button class="modal-close" onclick="closeAbout()" aria-label="Close">{icon("close")}</button>

  <img class="about-logo" src="/static/img/logo.svg" alt="">

  <h2 class="about-name">{APP_NAME}</h2>
  <p class="about-version">Version {VERSION}</p>

  <p class="about-desc">
    A document security analysis tool that removes editing restrictions from
    <code>.docx</code> files. It deletes the <code>w:documentProtection</code>
    element that Word writes into <code>word/settings.xml</code>. Original files
    are never modified, and password-encrypted documents are not supported.
  </p>

  <div class="about-credit">
    <span class="author">Created by {AUTHOR}</span>
    <span class="edu">{EDU_NOTICE}</span>
  </div>

  <div class="about-warn">
    {icon("alert")}
    <span>This tool is intended for learning and for documents you are
    entitled to edit. Removing a restriction does not grant permission you did
    not already have. The author is not responsible for misuse.</span>
  </div>

  <p class="about-year">&copy; {YEAR} {AUTHOR}</p>

  <div class="actions center">
    <button class="btn" onclick="closeAbout()">CLOSE</button>
  </div>
</div>
"""


# ----------------------------------------------------------------- launch


def _open_browser(url: str, delay: float = 1.2) -> None:
    def go() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=go, daemon=True).start()


def _cli_crack(paths: list[str]) -> int:
    """Headless mode, used by the Explorer context-menu entry."""
    database.init(DB_PATH)
    failures = 0
    for raw in paths:
        source = Path(raw)
        print(f"\n>>> {source.name}")
        result = dispatch.unlock(str(source), str(source.parent)).as_dict()
        for line in result["logs"]:
            print(f"    {line}")
        database.record(result)
        if result["status"] != "success":
            failures += 1
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="DocCipherBreaker", description=APP_NAME)
    parser.add_argument("files", nargs="*", help=".docx, .pdf or .xlsx files to unlock headlessly")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.files:
        code = _cli_crack(args.files)
        if sys.stdout.isatty():
            input("\nPress Enter to close...")
        return code

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        _open_browser(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
