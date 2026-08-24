"""Tests for the core cracking logic."""

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.cracker import DocCracker, inspect, unique_path  # noqa: E402
from tests.make_fixture import build  # noqa: E402


@pytest.fixture
def locked(tmp_path):
    return build(tmp_path / "locked.docx", protected=True)


@pytest.fixture
def unlocked_src(tmp_path):
    return build(tmp_path / "plain.docx", protected=False)


def settings_of(path):
    with zipfile.ZipFile(path) as zf:
        return zf.read("word/settings.xml").decode()


def test_removes_protection(locked, tmp_path):
    result = DocCracker(str(locked), str(tmp_path / "out")).unlock()
    assert result.status == "success", result.error
    assert result.protections_found == 1
    assert "documentProtection" not in settings_of(result.output_path)


def test_original_is_untouched(locked, tmp_path):
    before = locked.read_bytes()
    DocCracker(str(locked), str(tmp_path / "out")).unlock()
    assert locked.exists()
    assert locked.read_bytes() == before


def test_output_is_valid_zip_with_all_members(locked, tmp_path):
    result = DocCracker(str(locked), str(tmp_path / "out")).unlock()
    with zipfile.ZipFile(locked) as src, zipfile.ZipFile(result.output_path) as out:
        assert src.namelist() == out.namelist()      # order preserved
        assert out.testzip() is None
        # Only settings.xml and document.xml should differ.
        for name in src.namelist():
            if name in ("word/settings.xml", "word/document.xml"):
                continue
            assert src.read(name) == out.read(name)


def test_content_types_stays_first(locked, tmp_path):
    result = DocCracker(str(locked), str(tmp_path / "out")).unlock()
    with zipfile.ZipFile(result.output_path) as zf:
        assert zf.namelist()[0] == "[Content_Types].xml"


def test_perm_ranges_stripped(locked, tmp_path):
    result = DocCracker(str(locked), str(tmp_path / "out")).unlock()
    with zipfile.ZipFile(result.output_path) as zf:
        doc = zf.read("word/document.xml").decode()
    assert "permStart" not in doc
    assert "permEnd" not in doc
    assert "Second paragraph." in doc      # content preserved


def test_already_unlocked_file_succeeds(unlocked_src, tmp_path):
    result = DocCracker(str(unlocked_src), str(tmp_path / "out")).unlock()
    assert result.status == "success"
    assert result.protections_found == 0


def test_rejects_missing_file(tmp_path):
    result = DocCracker(str(tmp_path / "nope.docx")).unlock()
    assert result.status == "failed"
    assert "not found" in result.error.lower()


def test_rejects_non_docx_extension(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    result = DocCracker(str(p)).unlock()
    assert result.status == "failed"
    assert "not a word document" in result.error.lower()


def test_rejects_empty_file(tmp_path):
    p = tmp_path / "empty.docx"
    p.touch()
    result = DocCracker(str(p)).unlock()
    assert result.status == "failed"
    assert "empty" in result.error.lower()


def test_rejects_encrypted_ole_container(tmp_path):
    p = tmp_path / "encrypted.docx"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    result = DocCracker(str(p)).unlock()
    assert result.status == "failed"
    assert "encrypted" in result.error.lower()


def test_rejects_garbage_that_is_not_a_zip(tmp_path):
    p = tmp_path / "garbage.docx"
    p.write_bytes(b"this is definitely not a zip file at all")
    result = DocCracker(str(p)).unlock()
    assert result.status == "failed"


def test_rejects_zip_without_settings_xml(tmp_path):
    p = tmp_path / "notword.docx"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("hello.txt", "not a word document")
    result = DocCracker(str(p)).unlock()
    assert result.status == "failed"
    assert "settings.xml" in result.error


def test_no_temp_directory_leaks(locked, tmp_path):
    cracker = DocCracker(str(locked), str(tmp_path / "out"))
    cracker.unlock()
    assert cracker._temp_dir is not None
    assert not cracker._temp_dir.exists()


def test_second_run_does_not_clobber(locked, tmp_path):
    out = tmp_path / "out"
    first = DocCracker(str(locked), str(out)).unlock()
    second = DocCracker(str(locked), str(out)).unlock()
    assert first.output_path != second.output_path
    assert Path(first.output_path).exists()
    assert Path(second.output_path).exists()


def test_unique_path_appends_suffix(tmp_path):
    p = tmp_path / "a.docx"
    p.touch()
    assert unique_path(p).name == "a_2.docx"


def test_inspect_reports_protection(locked, unlocked_src):
    info = inspect(str(locked))
    assert info["protected"] is True
    assert info["edit_mode"] == "readOnly"
    assert info["enforced"] is True
    assert info["password_hashed"] is True

    assert inspect(str(unlocked_src))["protected"] is False


def test_logs_cover_all_eight_steps(locked, tmp_path):
    result = DocCracker(str(locked), str(tmp_path / "out")).unlock()
    joined = "\n".join(result.logs)
    for n in range(1, 9):
        assert f"[{n}/8]" in joined


def test_on_log_callback_receives_lines(locked, tmp_path):
    seen = []
    DocCracker(str(locked), str(tmp_path / "out"), on_log=lambda m, l: seen.append((m, l))).unlock()
    assert seen
    assert any(level == "success" for _, level in seen)


def test_failed_step_is_recorded(tmp_path):
    """The UI marks the exact step that broke, so it must be reported."""
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    assert DocCracker(str(bad)).unlock().failed_step == 1

    notword = tmp_path / "notword.docx"
    with zipfile.ZipFile(notword, "w") as zf:
        zf.writestr("hello.txt", "no settings.xml here")
    # Locating settings.xml is step 4.
    assert DocCracker(str(notword)).unlock().failed_step == 4


def test_successful_run_reports_no_failed_step(locked, tmp_path):
    assert DocCracker(str(locked), str(tmp_path / "out")).unlock().failed_step is None
