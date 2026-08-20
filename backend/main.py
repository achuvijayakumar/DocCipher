"""DocCipher Breaker -- FastAPI backend.

Serves the UI, accepts uploaded or local-path .docx files, strips editing
restrictions, records history in SQLite, and hands back a download.
"""

import argparse
import os
import re
import secrets
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
from . import dispatch
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
VERSION = "1.0.0"
AUTHOR = "Achu Vijayakumar"
YEAR = "2026"
EDU_NOTICE = "FOR EDUCATIONAL PURPOSES ONLY"
WINDOW_TITLE = f"{APP_NAME} — Created by {AUTHOR}"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def app_data_dir() -> Path:
    """Per-user writable directory. Program Files is read-only after install."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(root) / "DocCipherBreaker"
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = app_data_dir()
OUTPUT_DIR = DATA_DIR / "unlocked"
DB_PATH = DATA_DIR / "history.db"

# token -> absolute path, for /download. Keeps real paths out of URLs.
_downloads: dict[str, str] = {}

# The splash screen plays once per server run, not on every page load.
_splash_shown = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    database.init(DB_PATH)
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
            400, f"Unsupported file type: {path.name}. Only .docx and .pdf are supported."
        )
    return path


# ------------------------------------------------------------------ pages


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Splash screen on first visit; the app itself on every later visit.

    The splash is shown once per server run so that relaunching the app feels
    branded, but reloading the page during a session does not replay it.
    """
    global _splash_shown
    if _splash_shown:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    _splash_shown = True
    return HTMLResponse((STATIC_DIR / "splash.html").read_text(encoding="utf-8"))


@app.get("/app", response_class=HTMLResponse)
def application() -> HTMLResponse:
    """The main UI, reachable directly and used as the splash screen's target."""
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/splash", response_class=HTMLResponse)
def splash() -> HTMLResponse:
    """Replay the splash screen on demand."""
    return HTMLResponse((STATIC_DIR / "splash.html").read_text(encoding="utf-8"))


@app.get("/about", response_class=HTMLResponse)
def about() -> HTMLResponse:
    """About dialog content, loaded into the UI's modal by HTMX."""
    return HTMLResponse(_render_about())


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
        return fail("Only .docx and .pdf files are accepted.")

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

    result = _run_crack(renamed, OUTPUT_DIR, cleanup_source=True)
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
) -> HTMLResponse:
    rows = database.list_history(
        search=search, status=status, file_format=file_format, limit=100
    )
    return HTMLResponse(_render_history(rows))


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


@app.post("/reveal/{history_id}")
def reveal(history_id: int) -> JSONResponse:
    """Open the output file's folder in Explorer. Local desktop app only."""
    entry = database.get(history_id)
    if not entry or not entry.get("unlocked_path"):
        raise HTTPException(404, "No output file recorded for that entry")
    target = Path(entry["unlocked_path"])
    if not target.exists():
        raise HTTPException(404, "File no longer exists")
    if sys.platform == "win32":
        os.startfile(target.parent)  # noqa: S606 -- opening a folder locally is the feature
    return JSONResponse({"opened": str(target.parent)})


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
        return '<div class="empty">No activity yet. Drop a .docx or .pdf file above to get started.</div>'

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
    parser.add_argument("files", nargs="*", help=".docx or .pdf files to unlock headlessly")
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
