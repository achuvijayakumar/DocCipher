"""Route a file to the right unlocker based on its format.

Keeps format detection in one place so the API, the CLI and the batch endpoint
all agree on what is supported and how it is named.
"""

from pathlib import Path
from typing import Callable, Optional

from .cracker import CrackError, CrackResult, DocCracker, inspect as inspect_docx
from .excel_cracker import ExcelCracker, inspect_xlsx
from .pdf_cracker import PDFCracker, inspect_pdf

SUPPORTED = {
    ".docx": "DOCX",
    ".pdf": "PDF",
    ".xlsx": "XLSX",
}

# Steps shown in the UI, per format. The counts differ because the work does.
STEP_COUNT = {"docx": 8, "pdf": 6, "xlsx": 7}


def detect_format(path: str) -> Optional[str]:
    """Return 'docx' or 'pdf', or None if the extension is not supported."""
    suffix = Path(path).suffix.lower()
    return suffix.lstrip(".") if suffix in SUPPORTED else None


def format_label(path: str) -> str:
    """Human-facing format name, e.g. 'DOCX'."""
    return SUPPORTED.get(Path(path).suffix.lower(), "UNKNOWN")


def unlock(
    input_path: str,
    output_dir: Optional[str] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
) -> CrackResult:
    """Unlock a .docx or .pdf, dispatching on extension."""
    fmt = detect_format(input_path)

    if fmt == "docx":
        return DocCracker(input_path, output_dir, on_log=on_log).unlock()
    if fmt == "pdf":
        return PDFCracker(input_path, output_dir, on_log=on_log).unlock()
    if fmt == "xlsx":
        return ExcelCracker(input_path, output_dir, on_log=on_log).unlock()

    suffix = Path(input_path).suffix or "(no extension)"
    return CrackResult(
        status="failed",
        input_path=str(input_path),
        error=f"Unsupported file type: {suffix}. Only .docx, .pdf and .xlsx are supported.",
        logs=[f"Failed: unsupported file type {suffix}"],
        failed_step=1,
        file_format=suffix.lstrip(".").lower() or "unknown",
    )


def inspect(path: str) -> dict:
    """Report a file's restrictions without modifying it."""
    fmt = detect_format(path)
    if fmt == "docx":
        info = inspect_docx(path)
        info.setdefault("format", "docx")
        return info
    if fmt == "pdf":
        return inspect_pdf(path)
    if fmt == "xlsx":
        return inspect_xlsx(path)
    raise CrackError(
        f"Unsupported file type: {Path(path).suffix or '(no extension)'}. "
        "Only .docx, .pdf and .xlsx are supported."
    )
