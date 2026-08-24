"""Remove modify-protection from PowerPoint (.pptx) presentations.

Same ZIP-and-XML approach as .docx and .xlsx. PowerPoint records a
"password to modify" as <p:modifyVerifier> in ppt/presentation.xml, and can
mark individual slides or the section list as locked. None of it encrypts
anything -- the slide text sits in readable XML either way.

A presentation that asks for a password to OPEN is an encrypted OLE compound
file, genuinely unreadable without the password, and is rejected up front.

The original file is never modified. Work happens in a temp directory and a new
*_unlocked.pptx is written alongside the input.
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

# The modify password, and the "read-only recommended" sibling.
PRESENTATION_PATTERNS = [
    re.compile(r"<p:modifyVerifier\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<p:modifyVerifier\b.*?</p:modifyVerifier>", re.IGNORECASE | re.DOTALL),
    # Namespace prefixes are not guaranteed; match an unprefixed form too.
    re.compile(r"<modifyVerifier\b[^>]*/>", re.IGNORECASE),
    re.compile(r"<modifyVerifier\b.*?</modifyVerifier>", re.IGNORECASE | re.DOTALL),
]

# Per-slide and section locks.
SLIDE_PATTERNS = [
    re.compile(r'\s*(?:p:)?showMasterSp="0"', re.IGNORECASE),
]


class PptxCracker:
    """Removes modify-protection from a single .pptx presentation."""

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
        suffix = self.input_path.suffix.lower() or ".pptx"
        self.output_path = self.output_dir / f"{self.input_path.stem}_unlocked{suffix}"

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
        """Verify the input is a readable, non-encrypted presentation."""
        self._step(1, f"Analyzing presentation structure: {self.input_path.name}")

        if not self.input_path.exists():
            raise CrackError(f"File not found: {self.input_path}")
        if not self.input_path.is_file():
            raise CrackError(f"Not a file: {self.input_path}")

        size = self.input_path.stat().st_size
        if size == 0:
            raise CrackError("File is empty")

        with open(self.input_path, "rb") as fh:
            magic = fh.read(8)

        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            raise CrackError(
                "This presentation is password-protected and cannot be opened "
                "without the password. This tool removes modify-protection only."
            )
        if not magic.startswith(b"PK"):
            raise CrackError("Not a valid ZIP/OOXML container (bad magic bytes)")
        if not zipfile.is_zipfile(self.input_path):
            raise CrackError("File is not a readable ZIP archive")

        self._log(f"    File valid. Size: {human_size(size)}")

    def step2_extract(self) -> dict:
        """Read every entry into memory, preserving archive order."""
        self._step(2, "Extracting presentation contents...")
        self._temp_dir = Path(tempfile.mkdtemp(prefix="doccipher_pptx_"))
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

        if not any(n.lower() == "ppt/presentation.xml" for n in entries):
            raise CrackError("ppt/presentation.xml not found -- not a PowerPoint file?")

        self._log(f"    {len(entries)} members extracted")
        return entries

    def step3_strip_modify(self, entries: dict) -> int:
        """Remove <p:modifyVerifier> from ppt/presentation.xml."""
        self._step(3, "Removing modify protection...")
        target = next((n for n in entries if n.lower() == "ppt/presentation.xml"), None)
        if target is None:
            return 0

        xml = entries[target].decode("utf-8")
        removed = 0
        for pattern in PRESENTATION_PATTERNS:
            xml, n = pattern.subn("", xml)
            removed += n

        if removed:
            entries[target] = xml.encode("utf-8")
            self._log(f"    Removed {removed} modify-protection element(s)", "success")
        else:
            self._log("    No modify protection found", "warn")
        return removed

    def step4_strip_slides(self, entries: dict) -> int:
        """Clear per-slide locks that stop layouts being edited."""
        self._step(4, "Unlocking slides...")
        removed = 0
        touched = 0

        for name in list(entries):
            lowered = name.lower()
            if not (lowered.startswith("ppt/slides/") and lowered.endswith(".xml")):
                continue
            try:
                xml = entries[name].decode("utf-8")
            except UnicodeDecodeError:
                continue

            count = 0
            for pattern in SLIDE_PATTERNS:
                xml, n = pattern.subn("", xml)
                count += n
            if count:
                entries[name] = xml.encode("utf-8")
                removed += count
                touched += 1

        if removed:
            self._log(f"    Cleared {removed} lock(s) across {touched} slide(s)", "success")
        else:
            self._log("    No slide-level locks found")
        return removed

    def step5_verify(self, entries: dict) -> None:
        """Confirm the edited parts still parse and no protection survived."""
        self._step(5, "Verifying integrity...")

        for name, data in entries.items():
            lowered = name.lower()
            if not (
                lowered == "ppt/presentation.xml"
                or (lowered.startswith("ppt/slides/") and lowered.endswith(".xml"))
            ):
                continue
            try:
                ET.fromstring(data)
            except ET.ParseError as exc:
                raise CrackError(f"{name} is malformed after edit: {exc}") from exc

            if b"modifyVerifier" in data:
                raise CrackError(f"Modify protection survived removal in {name}")

        self._log("    XML well-formed. Restrictions confirmed removed.", "success")

    def step6_repack(self, entries: dict) -> Path:
        """Rebuild the ZIP preserving original entry order and compression.

        Reshuffling entries is a common cause of PowerPoint's repair prompt,
        which is why shutil.make_archive is not used.
        """
        self._step(6, "Rebuilding presentation...")
        rebuilt = self._temp_dir / "rebuilt.pptx"

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
        fmt = self.input_path.suffix.lower().lstrip(".") or "pptx"

        try:
            self.step1_validate()
            entries = self.step2_extract()
            modify = self.step3_strip_modify(entries)
            slides = self.step4_strip_slides(entries)
            self.protections_found = modify + slides
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
                file_format=fmt,
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
                file_format=fmt,
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
                file_format=fmt,
            )
        finally:
            self.cleanup()


def inspect_pptx(path: str) -> dict:
    """Report a presentation's protections without modifying it."""
    p = Path(path)
    try:
        with zipfile.ZipFile(p, "r") as zf:
            protected = False
            for name in zf.namelist():
                if name.lower() == "ppt/presentation.xml":
                    protected = b"modifyVerifier" in zf.read(name)
                    break
    except (OSError, zipfile.BadZipFile) as exc:
        raise CrackError(f"Presentation could not be read: {exc}") from exc

    return {
        "format": p.suffix.lower().lstrip(".") or "pptx",
        "protected": protected,
        "needs_password": False,
        "restrictions": ["modify password"] if protected else [],
        "can_unlock": True,
    }
