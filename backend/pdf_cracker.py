"""Remove usage restrictions from PDF files.

A restricted PDF is encrypted with an EMPTY user password and a non-empty
owner password. Every reader opens it without prompting; the permission flags
(no printing, no copying, no editing) are advisory, and the decryption key is
derivable without knowing the owner password. Removing them is a decrypt with a
key the file already hands over, not a password attack.

A PDF with a non-empty USER password is different: the content is genuinely
unreadable without that password. This module detects those and refuses.

Two backends, tried in order:
  1. qpdf --decrypt   -- preferred when installed; byte-faithful, keeps
                         structure, forms and bookmarks exactly as they were.
  2. PyMuPDF          -- always available (pure wheel, no external binary).

The original file is never modified.
"""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import fitz

from .cracker import CrackError, CrackResult, human_size, unique_path

TOTAL_STEPS = 6

# Permission bits, mapped to the words a user would recognise.
PERMISSION_LABELS = [
    (fitz.PDF_PERM_PRINT, "printing"),
    (fitz.PDF_PERM_MODIFY, "editing"),
    (fitz.PDF_PERM_COPY, "copying text"),
    (fitz.PDF_PERM_ANNOTATE, "commenting"),
    (fitz.PDF_PERM_FORM, "filling forms"),
    (fitz.PDF_PERM_ASSEMBLE, "reorganising pages"),
    (fitz.PDF_PERM_PRINT_HQ, "high-quality printing"),
]


def find_qpdf() -> Optional[str]:
    """Locate a bundled or system qpdf, or None if there isn't one."""
    bundled = Path(__file__).resolve().parent.parent / "tools" / "qpdf" / "bin" / "qpdf.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("qpdf")


class PDFCracker:
    """Removes owner-password usage restrictions from a single PDF."""

    def __init__(
        self,
        input_path: str,
        output_dir: Optional[str] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ):
        self.input_path = Path(input_path).resolve()
        self.output_dir = Path(output_dir).resolve() if output_dir else self.input_path.parent
        self.on_log = on_log
        self.logs: list[str] = []
        self.protections_found = 0
        self.current_step = 0
        self.method: Optional[str] = None
        self._temp_dir: Optional[Path] = None
        self.output_path = self.output_dir / f"{self.input_path.stem}_unlocked.pdf"

    # ---- logging -------------------------------------------------------

    def _log(self, message: str, level: str = "info") -> None:
        self.logs.append(message)
        if self.on_log:
            self.on_log(message, level)

    def _step(self, n: int, message: str) -> None:
        self.current_step = n
        self._log(f"[{n}/{TOTAL_STEPS}] {message}")

    # ---- steps ---------------------------------------------------------

    def step1_analyze(self) -> fitz.Document:
        """Open the PDF and reject anything we cannot legitimately process."""
        self._step(1, f"Analyzing PDF security: {self.input_path.name}")

        if not self.input_path.exists():
            raise CrackError(f"File not found: {self.input_path}")
        if not self.input_path.is_file():
            raise CrackError(f"Not a file: {self.input_path}")

        size = self.input_path.stat().st_size
        if size == 0:
            raise CrackError("File is empty")

        with open(self.input_path, "rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise CrackError("Not a valid PDF (missing %PDF- header)")

        try:
            doc = fitz.open(str(self.input_path))
        except Exception as exc:
            raise CrackError(f"PDF could not be read: {exc}") from exc

        if doc.needs_pass:
            doc.close()
            raise CrackError(
                "This PDF is password-protected. You need the password to unlock it."
            )

        self._log(f"    PDF valid. Size: {human_size(size)}, pages: {doc.page_count}")
        return doc

    def step2_detect(self, doc: fitz.Document) -> list[str]:
        """Report which actions the document currently forbids."""
        self._step(2, "Detecting restriction type...")

        perms = doc.permissions
        blocked = [label for bit, label in PERMISSION_LABELS if not perms & bit]

        if not blocked:
            self._log("    No restrictions found -- this PDF is already unrestricted", "warn")
        else:
            self._log(f"    Restricted: {', '.join(blocked)}", "success")

        self.protections_found = len(blocked)
        return blocked

    def step3_unlock(self, doc: fitz.Document) -> Path:
        """Write a decrypted copy, preferring qpdf and falling back to PyMuPDF."""
        self._step(3, "Removing restrictions...")
        self._temp_dir = Path(tempfile.mkdtemp(prefix="doccipher_pdf_"))
        staged = self._temp_dir / "unlocked.pdf"

        qpdf = find_qpdf()
        if qpdf:
            doc.close()
            if self._try_qpdf(qpdf, staged):
                self.method = "qpdf"
                self._log("    Removed with qpdf", "success")
                return staged
            self._log("    qpdf did not succeed; using PyMuPDF instead", "warn")
            doc = fitz.open(str(self.input_path))

        self._unlock_with_pymupdf(doc, staged)
        self.method = "pymupdf"
        self._log("    Removed with PyMuPDF", "success")
        return staged

    def _try_qpdf(self, qpdf: str, out: Path) -> bool:
        """qpdf --decrypt. Preserves the file's structure byte-for-byte."""
        try:
            proc = subprocess.run(
                [qpdf, "--decrypt", str(self.input_path), str(out)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._log(f"    qpdf unavailable: {exc}", "warn")
            return False

        # qpdf exits 3 on warnings but still writes valid output.
        if proc.returncode in (0, 3) and out.is_file() and out.stat().st_size > 0:
            if proc.returncode == 3:
                self._log("    qpdf reported warnings but produced a valid file")
            return True

        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        if detail:
            self._log(f"    qpdf: {detail[0]}", "warn")
        out.unlink(missing_ok=True)
        return False

    def _unlock_with_pymupdf(self, doc: fitz.Document, out: Path) -> None:
        """Re-save with encryption removed.

        The page content is copied as-is -- this is not a re-render, so text
        stays selectable and the document is not rasterised.
        """
        try:
            doc.save(
                str(out),
                encryption=fitz.PDF_ENCRYPT_NONE,
                garbage=3,
                deflate=True,
            )
        except Exception as exc:
            raise CrackError(f"Could not write the unlocked PDF: {exc}") from exc
        finally:
            doc.close()

    def step4_rebuild(self, staged: Path) -> None:
        self._step(4, "Rebuilding document...")
        if not staged.is_file() or staged.stat().st_size == 0:
            raise CrackError("The unlocked PDF was not produced")

    def step5_verify(self, staged: Path) -> None:
        """Confirm the result opens freely and keeps every page."""
        self._step(5, "Verifying integrity...")

        try:
            out = fitz.open(str(staged))
        except Exception as exc:
            raise CrackError(f"The unlocked PDF is not readable: {exc}") from exc

        try:
            if out.needs_pass:
                raise CrackError("The unlocked PDF still requires a password")

            original = fitz.open(str(self.input_path))
            try:
                if out.page_count != original.page_count:
                    raise CrackError(
                        f"Page count changed: {original.page_count} -> {out.page_count}"
                    )
                pages = out.page_count
            finally:
                original.close()

            missing = [
                label for bit, label in PERMISSION_LABELS if not out.permissions & bit
            ]
            if missing:
                raise CrackError(f"Restrictions remain after processing: {', '.join(missing)}")
        finally:
            out.close()

        self._log(f"    Verified. {pages} page(s), all restrictions removed.", "success")

    def step6_deliver(self, staged: Path) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = unique_path(self.output_path)
        self._step(6, f"Writing output: {self.output_path.name}")
        shutil.move(str(staged), str(self.output_path))

    def cleanup(self) -> None:
        if self._temp_dir and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    # ---- orchestration -------------------------------------------------

    def unlock(self) -> CrackResult:
        started = time.perf_counter()
        size_before = self.input_path.stat().st_size if self.input_path.is_file() else 0

        try:
            doc = self.step1_analyze()
            self.step2_detect(doc)
            staged = self.step3_unlock(doc)
            self.step4_rebuild(staged)
            self.step5_verify(staged)
            self.step6_deliver(staged)

            duration = time.perf_counter() - started
            self._log(f"Completed in {duration:.2f}s", "success")

            return CrackResult(
                status="success",
                input_path=str(self.input_path),
                output_path=str(self.output_path),
                logs=self.logs,
                size_before=size_before,
                size_after=self.output_path.stat().st_size,
                duration=duration,
                protections_found=self.protections_found,
                file_format="pdf",
                method=self.method,
            )
        except CrackError as exc:
            self._log(f"Failed: {exc}", "error")
            return CrackResult(
                status="failed",
                input_path=str(self.input_path),
                error=str(exc),
                logs=self.logs,
                size_before=size_before,
                duration=time.perf_counter() - started,
                failed_step=self.current_step or 1,
                file_format="pdf",
            )
        except Exception as exc:
            self._log(f"Failed: {type(exc).__name__}: {exc}", "error")
            return CrackResult(
                status="failed",
                input_path=str(self.input_path),
                error=f"{type(exc).__name__}: {exc}",
                logs=self.logs,
                size_before=size_before,
                duration=time.perf_counter() - started,
                failed_step=self.current_step or 1,
                file_format="pdf",
            )
        finally:
            self.cleanup()


def inspect_pdf(path: str) -> dict:
    """Report a PDF's restrictions without modifying anything."""
    p = Path(path)
    try:
        doc = fitz.open(str(p))
    except Exception as exc:
        raise CrackError(f"PDF could not be read: {exc}") from exc

    try:
        if doc.needs_pass:
            return {
                "format": "pdf",
                "protected": True,
                "needs_password": True,
                "restrictions": [],
                "can_unlock": False,
            }
        blocked = [label for bit, label in PERMISSION_LABELS if not doc.permissions & bit]
        return {
            "format": "pdf",
            "protected": bool(blocked),
            "needs_password": False,
            "restrictions": blocked,
            "can_unlock": True,
            "pages": doc.page_count,
        }
    finally:
        doc.close()
