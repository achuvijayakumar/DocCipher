"""Remove worksheet and workbook protection from .xlsx files.

Same "ZIP trick" as .docx: an .xlsx is a ZIP archive of XML. Sheet protection
lives in xl/worksheets/sheetN.xml as <sheetProtection>, and workbook structure
protection in xl/workbook.xml as <workbookProtection>. Neither encrypts
anything -- the cell values sit in readable XML either way. Removing the
elements removes the restriction.

A file that asks for a password to OPEN is different: that is an encrypted OLE
compound file, genuinely unreadable without the password, and is rejected up
front.

The original file is never modified. Work happens in a temp directory and a new
*_unlocked.xlsx is written alongside the input.
"""

import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .cracker import CrackError, CrackResult, human_size, unique_path

TOTAL_STEPS = 7

# Elements that enforce protection. Both self-closing and paired forms are
# matched: Excel writes the self-closing form, but hand-edited or
# third-party-generated files are not guaranteed to.
#
# Regex rather than ElementTree on purpose: ET.write() rewrites namespace
# prefixes across the whole document, and Excel is fussy enough about the
# result to show a repair prompt. A targeted textual removal changes only the
# bytes that matter.
SHEET_PATTERNS = [
    re.compile(r"<sheetProtection\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<sheetProtection\b.*?</sheetProtection>", re.IGNORECASE | re.DOTALL),
    # Protected ranges are a companion feature to sheetProtection.
    re.compile(r"<protectedRanges\b.*?</protectedRanges>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<protectedRanges\b[^>]*/>", re.IGNORECASE),
]

WORKBOOK_PATTERNS = [
    re.compile(r"<workbookProtection\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<workbookProtection\b.*?</workbookProtection>", re.IGNORECASE | re.DOTALL),
]

# Chartsheets can carry their own protection element.
CHARTSHEET_PATTERNS = [
    re.compile(r"<sheetProtection\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<sheetProtection\b.*?</sheetProtection>", re.IGNORECASE | re.DOTALL),
]


class ExcelCracker:
    """Removes sheet and workbook protection from a single .xlsx file."""

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
        self._temp_dir: Optional[Path] = None
        self._order: list[str] = []
        self._infos: dict[str, zipfile.ZipInfo] = {}
        self.output_path = self.output_dir / f"{self.input_path.stem}_unlocked.xlsx"

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
        """Verify the input is a readable, non-encrypted .xlsx."""
        self._step(1, f"Analyzing Excel file structure: {self.input_path.name}")

        if not self.input_path.exists():
            raise CrackError(f"File not found: {self.input_path}")
        if not self.input_path.is_file():
            raise CrackError(f"Not a file: {self.input_path}")
        if self.input_path.suffix.lower() != ".xlsx":
            raise CrackError(f"Not an .xlsx file: {self.input_path.suffix or '(no extension)'}")

        size = self.input_path.stat().st_size
        if size == 0:
            raise CrackError("File is empty")

        with open(self.input_path, "rb") as fh:
            magic = fh.read(8)

        # Password-to-open workbooks are OLE compound files (D0CF11E0). That
        # includes the legacy .xls format saved under an .xlsx name.
        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            raise CrackError(
                "This workbook is password-protected and cannot be opened without "
                "the password. This tool removes sheet and workbook protection only."
            )
        if not magic.startswith(b"PK"):
            raise CrackError("Not a valid ZIP/OOXML container (bad magic bytes)")
        if not zipfile.is_zipfile(self.input_path):
            raise CrackError("File is not a readable ZIP archive")

        self._log(f"    File valid. Size: {human_size(size)}")

    def step2_extract(self) -> dict:
        """Read every entry into memory, preserving archive order."""
        self._step(2, "Extracting workbook contents...")
        self._temp_dir = Path(tempfile.mkdtemp(prefix="doccipher_xlsx_"))
        staged = self._temp_dir / "target.zip"
        shutil.copy2(self.input_path, staged)

        entries: dict[str, bytes] = {}
        with zipfile.ZipFile(staged, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise CrackError(f"Corrupt archive member: {bad}")
            self._order = zf.namelist()
            self._infos = {i.filename: i for i in zf.infolist()}
            for name in self._order:
                entries[name] = zf.read(name)

        if not any(n.lower() == "xl/workbook.xml" for n in entries):
            raise CrackError("xl/workbook.xml not found -- not an Excel workbook?")

        self._log(f"    {len(entries)} members extracted")
        return entries

    def step3_strip_sheets(self, entries: dict) -> int:
        """Remove <sheetProtection> from every worksheet and chartsheet."""
        self._step(3, "Removing worksheet protections...")
        removed = 0
        sheets_touched = 0

        for name in list(entries):
            lowered = name.lower()
            is_sheet = lowered.startswith("xl/worksheets/") and lowered.endswith(".xml")
            is_chart = lowered.startswith("xl/chartsheets/") and lowered.endswith(".xml")
            if not (is_sheet or is_chart):
                continue

            try:
                xml = entries[name].decode("utf-8")
            except UnicodeDecodeError:
                continue      # binary part inside the sheets folder; leave it

            patterns = SHEET_PATTERNS if is_sheet else CHARTSHEET_PATTERNS
            count = 0
            for pattern in patterns:
                xml, n = pattern.subn("", xml)
                count += n

            if count:
                entries[name] = xml.encode("utf-8")
                removed += count
                sheets_touched += 1

        if removed:
            self._log(f"    Unlocked {sheets_touched} sheet(s), {removed} element(s)", "success")
        else:
            self._log("    No worksheet protection found", "warn")
        return removed

    def step4_strip_workbook(self, entries: dict) -> int:
        """Remove <workbookProtection> from xl/workbook.xml."""
        self._step(4, "Removing workbook protections...")
        target = next((n for n in entries if n.lower() == "xl/workbook.xml"), None)
        if target is None:
            return 0

        xml = entries[target].decode("utf-8")
        removed = 0
        for pattern in WORKBOOK_PATTERNS:
            xml, n = pattern.subn("", xml)
            removed += n

        if removed:
            entries[target] = xml.encode("utf-8")
            self._log(f"    Removed {removed} workbook protection element(s)", "success")
        else:
            self._log("    No workbook structure protection found", "warn")
        return removed

    def step5_verify(self, entries: dict) -> None:
        """Confirm every edited part still parses and no protection survived."""
        self._step(5, "Verifying integrity...")

        for name, data in entries.items():
            lowered = name.lower()
            if not (
                lowered == "xl/workbook.xml"
                or (lowered.startswith(("xl/worksheets/", "xl/chartsheets/")) and lowered.endswith(".xml"))
            ):
                continue

            try:
                ET.fromstring(data)
            except ET.ParseError as exc:
                raise CrackError(f"{name} is malformed after edit: {exc}") from exc

            if b"<sheetProtection" in data or b"<workbookProtection" in data:
                raise CrackError(f"Protection survived removal in {name}")

        self._log("    XML well-formed. Restrictions confirmed removed.", "success")

    def step6_repack(self, entries: dict) -> Path:
        """Rebuild the ZIP preserving original entry order and compression.

        Order matters: Excel expects [Content_Types].xml first, and reshuffling
        entries is a common cause of the "we found a problem with some content"
        repair prompt. This is why shutil.make_archive is not used.
        """
        self._step(6, "Rebuilding spreadsheet...")
        rebuilt = self._temp_dir / "rebuilt.xlsx"

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

    def step7_deliver(self, rebuilt: Path) -> None:
        """Move the rebuilt workbook to its final name, without clobbering."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = unique_path(self.output_path)
        self._step(7, f"Writing output: {self.output_path.name}")
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
            entries = self.step2_extract()
            sheets = self.step3_strip_sheets(entries)
            book = self.step4_strip_workbook(entries)
            self.protections_found = sheets + book
            self.step5_verify(entries)
            rebuilt = self.step6_repack(entries)
            self.step7_deliver(rebuilt)

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
                file_format="xlsx",
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
                file_format="xlsx",
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
                file_format="xlsx",
            )
        finally:
            self.cleanup()


def inspect_xlsx(path: str) -> dict:
    """Report a workbook's protections without modifying it."""
    p = Path(path)
    try:
        with zipfile.ZipFile(p, "r") as zf:
            names = zf.namelist()
            sheets_locked = []
            workbook_locked = False

            for name in names:
                lowered = name.lower()
                if lowered.startswith(("xl/worksheets/", "xl/chartsheets/")) and lowered.endswith(".xml"):
                    if b"<sheetProtection" in zf.read(name):
                        sheets_locked.append(Path(name).stem)
                elif lowered == "xl/workbook.xml":
                    workbook_locked = b"<workbookProtection" in zf.read(name)
    except (OSError, zipfile.BadZipFile) as exc:
        raise CrackError(f"Workbook could not be read: {exc}") from exc

    restrictions = []
    if sheets_locked:
        restrictions.append(f"{len(sheets_locked)} protected sheet(s)")
    if workbook_locked:
        restrictions.append("workbook structure locked")

    return {
        "format": "xlsx",
        "protected": bool(sheets_locked or workbook_locked),
        "needs_password": False,
        "restrictions": restrictions,
        "can_unlock": True,
        "sheets": sheets_locked,
    }
