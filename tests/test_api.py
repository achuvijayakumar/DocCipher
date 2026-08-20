"""Tests for the FastAPI layer, using an isolated temp data directory."""

import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.make_fixture import build  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Point the app's data dir at tmp_path before importing it."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith("backend"):
            del sys.modules[mod]

    from backend import database, main  # noqa: PLC0415

    database.init(main.DB_PATH)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def locked_bytes(tmp_path):
    return build(tmp_path / "src" / "locked.docx", protected=True).read_bytes()


def test_index_serves_ui(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "DocCipher Breaker" in res.text


def test_upload_cracks_and_returns_card(client, locked_bytes):
    res = client.post("/upload", files={"file": ("secret.docx", locked_bytes)})
    assert res.status_code == 200
    assert "Unlocked Successfully" in res.text
    assert "secret_unlocked.docx" in res.text
    assert "/download/" in res.text


def test_uploaded_output_is_actually_unlocked(client, locked_bytes, tmp_path):
    client.post("/upload", files={"file": ("secret.docx", locked_bytes)})
    out = next((tmp_path / "DocCipherBreaker" / "unlocked").glob("*_unlocked.docx"))
    with zipfile.ZipFile(out) as zf:
        assert "documentProtection" not in zf.read("word/settings.xml").decode()


def test_upload_rejects_non_docx(client):
    res = client.post("/upload", files={"file": ("payload.exe", b"MZ\x90\x00")})
    assert "Processing Failed" in res.text
    assert "Only .docx" in res.text


def test_upload_rejects_garbage_docx(client):
    res = client.post("/upload", files={"file": ("fake.docx", b"not a zip")})
    assert "Processing Failed" in res.text


def test_download_returns_the_file(client, locked_bytes):
    card = client.post("/upload", files={"file": ("secret.docx", locked_bytes)}).text
    token = card.split('/download/')[1].split('"')[0]
    res = client.get(f"/download/{token}")
    assert res.status_code == 200
    assert res.content[:2] == b"PK"


def test_download_rejects_unknown_token(client):
    assert client.get("/download/does-not-exist").status_code == 404


def test_history_records_the_operation(client, locked_bytes):
    client.post("/upload", files={"file": ("tracked.docx", locked_bytes)})
    rows = client.get("/api/history").json()
    assert len(rows) == 1
    assert rows[0]["original_filename"] == "tracked.docx"
    assert rows[0]["status"] == "success"


def test_history_fragment_renders_rows(client, locked_bytes):
    client.post("/upload", files={"file": ("tracked.docx", locked_bytes)})
    html = client.get("/history").text
    assert "tracked.docx" in html
    assert "Completed" in html


def test_history_empty_state(client):
    assert "No activity yet" in client.get("/history").text


def test_history_search_filters(client, locked_bytes):
    client.post("/upload", files={"file": ("alpha.docx", locked_bytes)})
    client.post("/upload", files={"file": ("beta.docx", locked_bytes)})
    html = client.get("/history", params={"search": "alpha"}).text
    assert "alpha.docx" in html
    assert "beta.docx" not in html


def test_history_status_filter(client, locked_bytes):
    client.post("/upload", files={"file": ("good.docx", locked_bytes)})
    client.post("/upload", files={"file": ("bad.docx", b"garbage")})
    only_failed = client.get("/history", params={"status": "failed"}).text
    assert "bad.docx" in only_failed
    assert "good.docx" not in only_failed


def test_stats_reflect_operations(client, locked_bytes):
    client.post("/upload", files={"file": ("a.docx", locked_bytes)})
    client.post("/upload", files={"file": ("b.docx", b"garbage")})
    stats = client.get("/api/stats").json()
    assert stats["total"] == 2
    assert stats["successes"] == 1
    assert stats["failures"] == 1


def test_api_crack_local_path(client, tmp_path):
    src = build(tmp_path / "local" / "doc.docx", protected=True)
    res = client.post("/api/crack", data={"path": str(src)})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert Path(body["output_path"]).exists()
    assert src.exists()      # original still there


def test_api_crack_rejects_directory(client, tmp_path):
    assert client.post("/api/crack", data={"path": str(tmp_path)}).status_code == 400


def test_api_crack_rejects_missing_path(client, tmp_path):
    assert client.post("/api/crack", data={"path": str(tmp_path / "ghost.docx")}).status_code == 400


def test_api_batch(client, tmp_path):
    paths = [str(build(tmp_path / "b" / f"doc{i}.docx", protected=True)) for i in range(3)]
    paths.append(str(tmp_path / "b" / "missing.docx"))
    body = client.post("/api/batch", json={"paths": paths}).json()
    assert body["total"] == 4
    assert body["succeeded"] == 3
    assert body["failed"] == 1


def test_api_inspect(client, tmp_path):
    locked = build(tmp_path / "i" / "locked.docx", protected=True)
    plain = build(tmp_path / "i" / "plain.docx", protected=False)
    assert client.get("/api/inspect", params={"path": str(locked)}).json()["protected"] is True
    assert client.get("/api/inspect", params={"path": str(plain)}).json()["protected"] is False


def test_clear_history(client, locked_bytes):
    client.post("/upload", files={"file": ("x.docx", locked_bytes)})
    assert client.request("DELETE", "/api/history").json()["deleted"] == 1
    assert client.get("/api/history").json() == []


def test_filename_is_not_injected_into_html(client, locked_bytes):
    """Illegal characters are stripped before they can reach disk or HTML."""
    res = client.post("/upload", files={"file": ('<img src=x onerror=alert(1)>.docx', locked_bytes)})
    assert res.status_code == 200
    assert "<img src=x" not in res.text
    assert "_img src=x onerror=alert(1)__unlocked.docx" in res.text


def test_escaping_survives_a_filename_html_cannot_swallow(client, locked_bytes):
    """Characters that are legal on disk but meaningful in HTML must be escaped."""
    res = client.post("/upload", files={"file": ("a&b'c.docx", locked_bytes)})
    assert "&amp;b" in res.text


def test_reserved_device_name_is_defused(client, locked_bytes):
    res = client.post("/upload", files={"file": ("CON.docx", locked_bytes)})
    assert res.status_code == 200
    assert "_CON_unlocked.docx" in res.text


def test_traversal_in_filename_cannot_escape_output_dir(client, locked_bytes, tmp_path):
    client.post("/upload", files={"file": ("../../pwned.docx", locked_bytes)})
    assert not (tmp_path / "pwned_unlocked.docx").exists()
    assert list((tmp_path / "DocCipherBreaker" / "unlocked").glob("pwned_unlocked.docx"))


# --------------------------------------------------------------- branding


def test_splash_shows_first_then_the_app(client):
    """The splash plays once per server run, not on every page load."""
    first = client.get("/")
    assert "Created by Achu Vijayakumar" in first.text
    assert "FOR EDUCATIONAL PURPOSES ONLY" in first.text
    assert "ACTIVITY LOG" not in first.text

    second = client.get("/")
    assert "ACTIVITY LOG" in second.text


def test_app_route_serves_the_ui_directly(client):
    res = client.get("/app")
    assert res.status_code == 200
    assert "ACTIVITY LOG" in res.text


def test_splash_route_replays_on_demand(client):
    assert "FOR EDUCATIONAL PURPOSES ONLY" in client.get("/splash").text


def test_about_dialog_carries_required_branding(client):
    text = client.get("/about").text
    assert "Achu Vijayakumar" in text
    assert "FOR EDUCATIONAL PURPOSES ONLY" in text
    assert "1.0.0" in text
    assert "2026" in text
    assert "not responsible for misuse" in text


def test_favicon_is_served(client):
    res = client.get("/favicon.ico")
    assert res.status_code == 200
    assert res.content[:4] == b"\x00\x00\x01\x00"   # ICO magic


def test_ui_carries_footer_branding_and_window_title(client):
    html = client.get("/app").text
    assert "<title>DocCipher Breaker \u2014 Created by Achu Vijayakumar</title>" in html
    assert "Created by Achu Vijayakumar" in html
    assert "FOR EDUCATIONAL PURPOSES ONLY" in html


def test_no_emoji_anywhere_in_served_html(client):
    """The UI uses inline SVG only -- emoji would break the terminal theme."""
    import re

    emoji = re.compile(
        "[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f]"
    )
    for route in ("/", "/app", "/splash", "/about", "/history"):
        found = emoji.findall(client.get(route).text)
        assert not found, f"{route} contains emoji: {found}"


def test_result_card_uses_svg_icons_not_glyphs(client, locked_bytes):
    html = client.post("/upload", files={"file": ("x.docx", locked_bytes)}).text
    assert "<svg class=\"icon" in html


# ------------------------------------------------------- modal JSON flow


def test_upload_json_format_returns_result(client, locked_bytes):
    res = client.post("/upload?format=json", files={"file": ("m.docx", locked_bytes)})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["input_name"] == "m.docx"
    assert body["output_name"] == "m_unlocked.docx"
    assert body["download_token"]
    assert body["history_id"]
    assert body["protections_found"] == 1
    assert body["failed_step"] is None


def test_upload_json_failure_names_the_failed_step(client):
    body = client.post("/upload?format=json", files={"file": ("bad.docx", b"junk")}).json()
    assert body["status"] == "failed"
    assert body["error"]
    # Validation is step 1, so the modal marks the first row as failed.
    assert body["failed_step"] == 1


def test_upload_json_rejects_non_docx(client):
    body = client.post("/upload?format=json", files={"file": ("x.exe", b"MZ")}).json()
    assert body["status"] == "failed"
    assert "Only .docx" in body["error"]


def test_upload_json_download_token_works(client, locked_bytes):
    body = client.post("/upload?format=json", files={"file": ("d.docx", locked_bytes)}).json()
    res = client.get("/download/" + body["download_token"])
    assert res.status_code == 200
    assert res.content[:2] == b"PK"


def test_upload_html_format_still_default(client, locked_bytes):
    res = client.post("/upload", files={"file": ("h.docx", locked_bytes)})
    assert "Unlocked Successfully" in res.text


def test_upload_rejects_unknown_format(client, locked_bytes):
    assert client.post("/upload?format=xml", files={"file": ("h.docx", locked_bytes)}).status_code == 422


# ------------------------------------------------------- Clean Hacker UI


def test_stats_render_four_cards(client, locked_bytes):
    client.post("/upload", files={"file": ("a.docx", locked_bytes)})
    html = client.get("/stats").text
    for label in ("TOTAL", "UNLOCKED", "FAILED", "SAVED"):
        assert label in html
    assert "LOCKS BROKEN" not in html
    assert "CRACKED" not in html


def test_saved_stat_sums_bytes_reclaimed(client, locked_bytes):
    """SAVED is the total shrinkage across successful runs only."""
    assert client.get("/api/stats").json()["bytes_saved"] == 0

    body = client.post("/upload?format=json", files={"file": ("s.docx", locked_bytes)}).json()
    expected = body["size_before"] - body["size_after"]
    assert expected > 0
    assert client.get("/api/stats").json()["bytes_saved"] == expected

    # A failure contributes nothing.
    client.post("/upload", files={"file": ("bad.docx", b"junk")})
    assert client.get("/api/stats").json()["bytes_saved"] == expected


def test_hacker_jargon_is_gone_from_every_page(client, locked_bytes):
    client.post("/upload", files={"file": ("j.docx", locked_bytes)})
    banned = [
        "CRACK HISTORY", "LOCKS BROKEN", "BYPASSING", "UNLOCK THE UNLOCKABLE",
        "MOUNTING OOXML", "ARMING BREAKER", "TARGET NEUTRALIZED", "INJECTING",
    ]
    for route in ("/", "/app", "/splash", "/about", "/history", "/stats"):
        text = client.get(route).text.upper()
        for phrase in banned:
            assert phrase not in text, f"{route} still contains {phrase!r}"


def test_activity_log_headers(client, locked_bytes):
    client.post("/upload", files={"file": ("h.docx", locked_bytes)})
    html = client.get("/history").text
    for header in ("ORIGINAL FILE", "OUTPUT FILE", "STATUS", "SIZE", "TIMESTAMP"):
        assert header in html


def test_splash_carries_the_subtitle(client):
    text = client.get("/splash").text
    assert "Document Security Analysis Tool" in text
    assert "Created by Achu Vijayakumar" in text
    assert "FOR EDUCATIONAL PURPOSES ONLY" in text


# ------------------------------------------------------------ theming


def test_light_is_the_default_theme(client):
    """The document must start in light mode, before any script runs."""
    html = client.get("/app").text
    assert '<html lang="en" data-theme="light">' in html
    assert client.get("/splash").text.count('data-theme="light"') >= 1


def test_theme_toggle_is_present_in_the_header(client):
    html = client.get("/app").text
    assert 'id="theme-toggle"' in html
    assert "LIGHT" in html and "DARK" in html


def test_stored_theme_is_applied_before_first_paint(client):
    """The inline script must run in <head>, ahead of the body, to avoid a flash."""
    for route in ("/app", "/splash"):
        html = client.get(route).text
        script_at = html.index("doccipher-theme")
        body_at = html.index("<body")
        assert script_at < body_at, f"{route} applies the theme after <body>"


def test_light_palette_is_defined(client):
    css = client.get("/static/css/style.css").text
    assert ':root[data-theme="light"]' in css
    # Neon green is unreadable on white; light mode must not reuse it as text.
    light_block = css.split(':root[data-theme="light"]')[1].split("}")[0]
    assert "--green:       #0f8a34;" in light_block
    assert "#00ff41" not in light_block


# --------------------------------------------------------- PDF support


@pytest.fixture
def restricted_pdf_bytes(tmp_path):
    from tests.make_pdf_fixture import build_restricted
    return build_restricted(tmp_path / "pdfsrc" / "r.pdf").read_bytes()


@pytest.fixture
def password_pdf_bytes(tmp_path):
    from tests.make_pdf_fixture import build_user_password
    return build_user_password(tmp_path / "pdfsrc" / "pw.pdf").read_bytes()


def test_upload_unlocks_a_pdf(client, restricted_pdf_bytes):
    body = client.post(
        "/upload?format=json", files={"file": ("report.pdf", restricted_pdf_bytes)}
    ).json()
    assert body["status"] == "success", body.get("error")
    assert body["format"] == "pdf"
    assert body["output_name"] == "report_unlocked.pdf"
    assert body["protections_found"] > 0
    assert body["method"] in ("qpdf", "pymupdf")


def test_uploaded_pdf_is_actually_unrestricted(client, restricted_pdf_bytes, tmp_path):
    import fitz

    client.post("/upload", files={"file": ("r.pdf", restricted_pdf_bytes)})
    out = next((tmp_path / "DocCipherBreaker" / "unlocked").glob("*_unlocked.pdf"))
    doc = fitz.open(out)
    try:
        assert not doc.needs_pass
        assert doc.permissions & fitz.PDF_PERM_PRINT
        assert doc.permissions & fitz.PDF_PERM_COPY
    finally:
        doc.close()


def test_password_protected_pdf_gets_a_friendly_message(client, password_pdf_bytes):
    body = client.post(
        "/upload?format=json", files={"file": ("secret.pdf", password_pdf_bytes)}
    ).json()
    assert body["status"] == "failed"
    assert "password-protected" in body["error"]
    assert "You need the password" in body["error"]


def test_upload_rejects_unsupported_extension(client):
    body = client.post("/upload?format=json", files={"file": ("a.txt", b"hello")}).json()
    assert body["status"] == "failed"
    assert ".docx and .pdf" in body["error"]


def test_history_records_the_format(client, restricted_pdf_bytes, locked_bytes):
    client.post("/upload", files={"file": ("a.pdf", restricted_pdf_bytes)})
    client.post("/upload", files={"file": ("b.docx", locked_bytes)})
    rows = client.get("/api/history").json()
    formats = {r["original_filename"]: r["file_format"] for r in rows}
    assert formats["a.pdf"] == "pdf"
    assert formats["b.docx"] == "docx"


def test_history_can_be_filtered_by_format(client, restricted_pdf_bytes, locked_bytes):
    client.post("/upload", files={"file": ("a.pdf", restricted_pdf_bytes)})
    client.post("/upload", files={"file": ("b.docx", locked_bytes)})

    only_pdf = client.get("/history", params={"file_format": "pdf"}).text
    assert "a.pdf" in only_pdf and "b.docx" not in only_pdf

    only_docx = client.get("/history", params={"file_format": "docx"}).text
    assert "b.docx" in only_docx and "a.pdf" not in only_docx


def test_history_table_has_a_format_column(client, restricted_pdf_bytes):
    client.post("/upload", files={"file": ("a.pdf", restricted_pdf_bytes)})
    html = client.get("/history").text
    assert "FORMAT" in html
    assert 'class="fmt fmt-pdf">PDF<' in html


def test_stats_count_each_format(client, restricted_pdf_bytes, locked_bytes):
    client.post("/upload", files={"file": ("a.pdf", restricted_pdf_bytes)})
    client.post("/upload", files={"file": ("b.docx", locked_bytes)})
    stats = client.get("/api/stats").json()
    assert stats["pdf_count"] == 1
    assert stats["docx_count"] == 1


def test_api_inspect_handles_pdf(client, tmp_path):
    from tests.make_pdf_fixture import build_restricted

    src = build_restricted(tmp_path / "i" / "r.pdf")
    info = client.get("/api/inspect", params={"path": str(src)}).json()
    assert info["format"] == "pdf"
    assert info["protected"] is True
    assert "printing" in info["restrictions"]


def test_api_batch_mixes_formats(client, tmp_path):
    from tests.make_pdf_fixture import build_restricted

    paths = [
        str(build(tmp_path / "mix" / "a.docx", protected=True)),
        str(build_restricted(tmp_path / "mix" / "b.pdf")),
    ]
    body = client.post("/api/batch", json={"paths": paths}).json()
    assert body["succeeded"] == 2
    assert {r["format"] for r in body["results"]} == {"docx", "pdf"}


def test_drop_zone_mentions_both_formats(client):
    html = client.get("/app").text
    assert ".docx or .pdf" in html
    assert 'accept=".docx,.pdf"' in html
