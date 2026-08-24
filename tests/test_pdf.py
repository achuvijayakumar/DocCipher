"""Tests for PDF unlocking and format dispatch."""

import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import dispatch  # noqa: E402
from backend.cracker import CrackError  # noqa: E402
from backend.pdf_cracker import PDFCracker, inspect_pdf  # noqa: E402
from tests.make_fixture import build as build_docx  # noqa: E402
from tests.make_pdf_fixture import build_open, build_restricted, build_user_password  # noqa: E402


@pytest.fixture
def restricted(tmp_path):
    return build_restricted(tmp_path / "restricted.pdf")


@pytest.fixture
def unrestricted(tmp_path):
    return build_open(tmp_path / "open.pdf")


@pytest.fixture
def locked_with_password(tmp_path):
    return build_user_password(tmp_path / "userpw.pdf")


# ---------------------------------------------------------------- core


def test_removes_pdf_restrictions(restricted, tmp_path):
    result = PDFCracker(str(restricted), str(tmp_path / "out")).unlock()
    assert result.status == "success", result.error
    assert result.file_format == "pdf"
    assert result.protections_found > 0

    out = fitz.open(result.output_path)
    try:
        # Every permission bit must now be granted.
        for bit, _label in [
            (fitz.PDF_PERM_PRINT, "print"),
            (fitz.PDF_PERM_MODIFY, "modify"),
            (fitz.PDF_PERM_COPY, "copy"),
        ]:
            assert out.permissions & bit, "restriction survived"
        assert not out.needs_pass
    finally:
        out.close()


def test_original_pdf_is_untouched(restricted, tmp_path):
    before = restricted.read_bytes()
    PDFCracker(str(restricted), str(tmp_path / "out")).unlock()
    assert restricted.exists()
    assert restricted.read_bytes() == before


def test_pdf_content_survives(restricted, tmp_path):
    """Unlocking must not rasterise or drop the text layer."""
    result = PDFCracker(str(restricted), str(tmp_path / "out")).unlock()
    out = fitz.open(result.output_path)
    try:
        assert out.page_count == 1
        text = out[0].get_text()
        assert "This PDF has editing restrictions." in text
        assert "Second line of the document body." in text
    finally:
        out.close()


def test_pdf_needing_a_password_is_refused(locked_with_password, tmp_path):
    result = PDFCracker(str(locked_with_password), str(tmp_path / "out")).unlock()
    assert result.status == "failed"
    assert "password-protected" in result.error
    assert "You need the password" in result.error
    assert result.failed_step == 1


def test_unrestricted_pdf_still_succeeds(unrestricted, tmp_path):
    result = PDFCracker(str(unrestricted), str(tmp_path / "out")).unlock()
    assert result.status == "success"
    assert result.protections_found == 0
    assert "No restrictions found" in "\n".join(result.logs)


def test_rejects_non_pdf_bytes(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"this is not a pdf")
    result = PDFCracker(str(p)).unlock()
    assert result.status == "failed"
    assert "%PDF-" in result.error


def test_rejects_empty_pdf(tmp_path):
    p = tmp_path / "empty.pdf"
    p.touch()
    result = PDFCracker(str(p)).unlock()
    assert result.status == "failed"
    assert "empty" in result.error.lower()


def test_rejects_missing_pdf(tmp_path):
    result = PDFCracker(str(tmp_path / "ghost.pdf")).unlock()
    assert result.status == "failed"
    assert "not found" in result.error.lower()


def test_logs_cover_all_six_steps(restricted, tmp_path):
    result = PDFCracker(str(restricted), str(tmp_path / "out")).unlock()
    joined = "\n".join(result.logs)
    for n in range(1, 7):
        assert f"[{n}/6]" in joined


def test_no_temp_directory_leaks(restricted, tmp_path):
    cracker = PDFCracker(str(restricted), str(tmp_path / "out"))
    cracker.unlock()
    assert cracker._temp_dir is not None
    assert not cracker._temp_dir.exists()


def test_second_run_does_not_clobber(restricted, tmp_path):
    out = tmp_path / "out"
    first = PDFCracker(str(restricted), str(out)).unlock()
    second = PDFCracker(str(restricted), str(out)).unlock()
    assert first.output_path != second.output_path
    assert Path(first.output_path).exists()
    assert Path(second.output_path).exists()


def test_method_is_reported(restricted, tmp_path):
    result = PDFCracker(str(restricted), str(tmp_path / "out")).unlock()
    assert result.method in ("qpdf", "pymupdf")


# ------------------------------------------------------------- inspect


def test_inspect_reports_pdf_restrictions(restricted, unrestricted):
    info = inspect_pdf(str(restricted))
    assert info["format"] == "pdf"
    assert info["protected"] is True
    assert info["needs_password"] is False
    assert info["can_unlock"] is True
    assert "printing" in info["restrictions"]

    clean = inspect_pdf(str(unrestricted))
    assert clean["protected"] is False
    assert clean["restrictions"] == []


def test_inspect_flags_password_protected_pdf(locked_with_password):
    info = inspect_pdf(str(locked_with_password))
    assert info["needs_password"] is True
    assert info["can_unlock"] is False


# ------------------------------------------------------------ dispatch


def test_detect_format(tmp_path):
    assert dispatch.detect_format("a.docx") == "docx"
    assert dispatch.detect_format("a.PDF") == "pdf"
    assert dispatch.detect_format("a.txt") is None
    assert dispatch.detect_format("a") is None


def test_format_label():
    assert dispatch.format_label("a.docx") == "DOCX"
    assert dispatch.format_label("a.pdf") == "PDF"
    assert dispatch.format_label("a.txt") == "UNKNOWN"


def test_dispatch_routes_docx(tmp_path):
    src = build_docx(tmp_path / "d.docx", protected=True)
    result = dispatch.unlock(str(src), str(tmp_path / "out"))
    assert result.status == "success"
    assert result.file_format == "docx"
    assert result.method == "ooxml"


def test_dispatch_routes_pdf(restricted, tmp_path):
    result = dispatch.unlock(str(restricted), str(tmp_path / "out"))
    assert result.status == "success"
    assert result.file_format == "pdf"


def test_dispatch_rejects_unsupported_type(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    result = dispatch.unlock(str(p), str(tmp_path / "out"))
    assert result.status == "failed"
    assert "Unsupported file type" in result.error
    assert ".pptx" in result.error and ".docm" in result.error


def test_dispatch_inspect_both_formats(restricted, tmp_path):
    docx = build_docx(tmp_path / "d.docx", protected=True)
    assert dispatch.inspect(str(docx))["format"] == "docx"
    assert dispatch.inspect(str(restricted))["format"] == "pdf"

    p = tmp_path / "x.txt"
    p.write_text("hi")
    with pytest.raises(CrackError):
        dispatch.inspect(str(p))
