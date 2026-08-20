"""Core .docx editing-restriction removal.

The "ZIP trick": a .docx is a ZIP archive. Editing restrictions live in
word/settings.xml as a <w:documentProtection> element. Removing that element
removes the restriction. This is not encryption -- a password-encrypted .docx
is an entirely different (OLE compound) format and is rejected up front.

The original file is never modified. All work happens in a temp directory and
a new *_unlocked.docx is written alongside the input.
"""

import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

TOTAL_STEPS = 8

# Elements that enforce editing restrictions. w:documentProtection is the lock
# itself; w:writeProtection is the "recommend read-only" sibling.
PROTECTION_PATTERNS = [
    re.compile(r"<w:documentProtection\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<w:documentProtection\b.*?</w:documentProtection>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<w:writeProtection\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<w:writeProtection\b.*?</w:writeProtection>", re.IGNORECASE | re.DOTALL),
]

# Per-range editing permissions inside document.xml (form-field style locking).
PERM_PATTERNS = [
    re.compile(r"<w:permStart\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<w:permEnd\b[^>]*/>", re.IGNORECASE),
]


class CrackError(Exception):
    """Raised when a file cannot be processed."""


@dataclass
class CrackResult:
    status: str
    input_path: str
    output_path: Optional[str] = None
    error: Optional[str] = None
    logs: list = field(default_factory=list)
    size_before: int = 0
    size_after: int = 0
    duration: float = 0.0
    protections_found: int = 0
    failed_step: Optional[int] = None
    file_format: str = "docx"
    method: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "input_path": self.input_path,
            "input_name": Path(self.input_path).name if self.input_path else None,
            "output_path": self.output_path,
            "output_name": Path(self.output_path).name if self.output_path else None,
            "error": self.error,
            "logs": self.logs,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "duration": round(self.duration, 2),
            "protections_found": self.protections_found,
            "failed_step": self.failed_step,
            "format": self.file_format,
            "method": self.method,
        }


class DocCracker:
    """Removes editing restrictions from a single .docx file."""

    def __init__(
        self,
        input_path: str,
        output_dir: Optional[str] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        strip_perm_ranges: bool = True,
    ):
        self.input_path = Path(input_path).resolve()
        self.output_dir = Path(output_dir).resolve() if output_dir else self.input_path.parent
        self.on_log = on_log
        self.strip_perm_ranges = strip_perm_ranges
        self.logs: list[str] = []
        self.protections_found = 0
        self.current_step = 0
        self._temp_dir: Optional[Path] = None
        self._order: list[str] = []
        self._infos: dict[str, zipfile.ZipInfo] = {}
        self.output_path = self.output_dir / f"{self.input_path.stem}_unlocked.docx"

    # ---- logging -------------------------------------------------------

    def _log(self, message: str, level: str = "info") -> None:
        self.logs.append(message)
        if self.on_log:
            self.on_log(message, level)

    def _step(self, n: int, message: str) -> None:
        self.current_step = n
        self._log(f"[{n}/{TOTAL_STEPS}] {message}")

    # ---- steps ---------------------------------------------------------

    def step1_validate(self) -> None:
        """Verify the input is a readable, non-encrypted .docx."""
        self._step(1, f"Analyzing file structure: {self.input_path.name}")

        if not self.input_path.exists():
            raise CrackError(f"File not found: {self.input_path}")
        if not self.input_path.is_file():
            raise CrackError(f"Not a file: {self.input_path}")
        if self.input_path.suffix.lower() != ".docx":
            raise CrackError(f"Not a .docx file: {self.input_path.suffix or '(no extension)'}")

        size = self.input_path.stat().st_size
        if size == 0:
            raise CrackError("File is empty")

        with open(self.input_path, "rb") as fh:
            magic = fh.read(8)

        # ECMA-376 password-encrypted docs are OLE compound files (D0CF11E0).
        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            raise CrackError(
                "File is password-encrypted (OLE container), not merely restricted. "
                "This tool removes editing restrictions only and cannot open it."
            )
        if not magic.startswith(b"PK"):
            raise CrackError("Not a valid ZIP/OOXML container (bad magic bytes)")
        if not zipfile.is_zipfile(self.input_path):
            raise CrackError("File is not a readable ZIP archive")

        self._log(f"    File valid. Size: {human_size(size)}")

    def step2_stage_copy(self) -> Path:
        """Copy the original into a temp workspace. Original is never touched."""
        self._step(2, "Creating working copy (original untouched)...")
        self._temp_dir = Path(tempfile.mkdtemp(prefix="doccipher_"))
        staged = self._temp_dir / "target.zip"
        shutil.copy2(self.input_path, staged)
        return staged

    def step3_read_archive(self, staged: Path) -> dict:
        """Read every entry into memory, preserving archive order."""
        self._step(3, "Extracting document contents...")
        entries: dict[str, bytes] = {}
        with zipfile.ZipFile(staged, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise CrackError(f"Corrupt archive member: {bad}")
            self._order = zf.namelist()
            self._infos = {i.filename: i for i in zf.infolist()}
            for name in self._order:
                entries[name] = zf.read(name)
        self._log(f"    {len(entries)} members extracted")
        return entries

    def step4_locate_settings(self, entries: dict) -> str:
        """Find word/settings.xml (case-insensitive; OOXML casing varies)."""
        self._step(4, "Locating word/settings.xml...")
        for name in entries:
            if name.lower() == "word/settings.xml":
                self._log(f"    Found: {name}")
                return name
        raise CrackError("word/settings.xml not found -- not a Word document?")

    def step5_strip_protection(self, entries: dict, settings_name: str) -> None:
        """Remove documentProtection elements from settings.xml."""
        self._step(5, "Removing editing restrictions...")
        xml = entries[settings_name].decode("utf-8", errors="strict")

        found = 0
        for pattern in PROTECTION_PATTERNS:
            xml, n = pattern.subn("", xml)
            found += n

        if found == 0:
            self._log("    No documentProtection found -- file was already unlocked", "warn")
        else:
            self._log(f"    Removed {found} restriction element(s)", "success")

        self.protections_found = found
        entries[settings_name] = xml.encode("utf-8")

        if self.strip_perm_ranges:
            self._strip_perm_ranges(entries)

    def _strip_perm_ranges(self, entries: dict) -> None:
        """Remove permStart/permEnd editing-range markers from document.xml."""
        target = next((n for n in entries if n.lower() == "word/document.xml"), None)
        if target is None:
            return
        xml = entries[target].decode("utf-8", errors="strict")
        removed = 0
        for pattern in PERM_PATTERNS:
            xml, n = pattern.subn("", xml)
            removed += n
        if removed:
            entries[target] = xml.encode("utf-8")
            self._log(f"    Cleared {removed} editing-range marker(s)")

    def step6_verify(self, entries: dict, settings_name: str) -> None:
        """Confirm the XML still parses and the protection is really gone."""
        self._step(6, "Validating document integrity...")
        try:
            ET.fromstring(entries[settings_name])
        except ET.ParseError as exc:
            raise CrackError(f"settings.xml is malformed after edit: {exc}") from exc

        if b"documentProtection" in entries[settings_name]:
            raise CrackError("documentProtection element could not be removed")

        self._log("    XML well-formed. Restrictions confirmed removed.", "success")

    def step7_repack(self, entries: dict) -> Path:
        """Rebuild the ZIP preserving original entry order and compression.

        Order matters: Word expects [Content_Types].xml at the start of the
        archive, and reshuffling entries is a common cause of Word's
        "file is corrupt, do you want to repair it" prompt.
        """
        self._step(7, "Rebuilding document...")
        rebuilt = self._temp_dir / "rebuilt.docx"

        with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in self._order:
                original = self._infos[name]
                info = zipfile.ZipInfo(name, date_time=original.date_time)
                info.compress_type = original.compress_type
                info.external_attr = original.external_attr
                info.internal_attr = original.internal_attr
                info.create_system = original.create_system
                zf.writestr(info, entries[name])

        if not zipfile.is_zipfile(rebuilt):
            raise CrackError("Rebuilt archive failed validation")
        return rebuilt

    def step8_deliver(self, rebuilt: Path) -> None:
        """Move the rebuilt file to its final name, without clobbering."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = unique_path(self.output_path)
        self._step(8, f"Writing output: {self.output_path.name}")
        shutil.move(str(rebuilt), str(self.output_path))

    def cleanup(self) -> None:
        if self._temp_dir and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    # ---- orchestration -------------------------------------------------

    def unlock(self) -> CrackResult:
        started = time.perf_counter()
        size_before = self.input_path.stat().st_size if self.input_path.is_file() else 0

        try:
            self.step1_validate()
            staged = self.step2_stage_copy()
            entries = self.step3_read_archive(staged)
            settings_name = self.step4_locate_settings(entries)
            self.step5_strip_protection(entries, settings_name)
            self.step6_verify(entries, settings_name)
            rebuilt = self.step7_repack(entries)
            self.step8_deliver(rebuilt)

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
                file_format="docx",
                method="ooxml",
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
                file_format="docx",
            )
        except Exception as exc:  # unexpected -- must still clean up temp files
            self._log(f"Failed: {type(exc).__name__}: {exc}", "error")
            return CrackResult(
                status="failed",
                input_path=str(self.input_path),
                error=f"{type(exc).__name__}: {exc}",
                logs=self.logs,
                size_before=size_before,
                duration=time.perf_counter() - started,
                failed_step=self.current_step or 1,
                file_format="docx",
            )
        finally:
            self.cleanup()


def inspect(path: str) -> dict:
    """Report what protections a .docx carries, without modifying anything."""
    p = Path(path)
    if not zipfile.is_zipfile(p):
        raise CrackError("Not a readable ZIP/OOXML container")
    with zipfile.ZipFile(p) as zf:
        name = next((n for n in zf.namelist() if n.lower() == "word/settings.xml"), None)
        if name is None:
            raise CrackError("word/settings.xml not found")
        xml = zf.read(name).decode("utf-8", errors="replace")

    match = re.search(r"<w:documentProtection\b[^>]*", xml, re.IGNORECASE)
    if not match:
        return {"protected": False, "edit_mode": None, "enforced": False, "password_hashed": False}

    tag = match.group(0)
    edit = re.search(r'w:edit="([^"]*)"', tag)
    enforce = re.search(r'w:enforcement="([^"]*)"', tag)
    return {
        "protected": True,
        "edit_mode": edit.group(1) if edit else None,
        "enforced": (enforce.group(1) if enforce else "0") in ("1", "true", "on"),
        "password_hashed": "w:hash=" in tag or "w:cryptAlgorithmClass" in tag,
    }


def unique_path(path: Path) -> Path:
    """Return path, or path with a numeric suffix if it already exists."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(2, 1000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise CrackError("Could not find a free output filename")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
