"""Build PDF fixtures for testing: restricted, open, and encrypted.

A "restricted" PDF is encrypted with an EMPTY user password and a non-empty
owner password. Any reader can open it without a prompt; the permission flags
are advisory and the decryption key is derivable without the owner password.
That is the case this tool handles.

A PDF with a non-empty USER password genuinely cannot be opened without it.
Those are built here only so the tool can be tested for correctly refusing them.

    python tests/make_pdf_fixture.py out.pdf [--open|--user-password]
"""

import sys
from pathlib import Path

import fitz


def _page(doc: fitz.Document, text: str) -> None:
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=14)
    page.insert_text((72, 130), "Second line of the document body.", fontsize=11)


def build_restricted(path: Path, owner_password: str = "ownersecret") -> Path:
    """Opens without a prompt, but forbids printing, copying and editing."""
    doc = fitz.open()
    _page(doc, "This PDF has editing restrictions.")

    # Grant only what a locked-down document would: viewing and nothing else.
    perms = int(fitz.PDF_PERM_ACCESSIBILITY)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw=owner_password,
        user_pw="",           # empty -> opens without a prompt
        permissions=perms,
    )
    doc.close()
    return path


def build_open(path: Path) -> Path:
    """No encryption, no restrictions."""
    doc = fitz.open()
    _page(doc, "This PDF has no restrictions.")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def build_user_password(path: Path, user_password: str = "letmein") -> Path:
    """Genuinely encrypted: cannot be opened without the password."""
    doc = fitz.open()
    _page(doc, "This PDF needs a password to open.")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="ownersecret",
        user_pw=user_password,
        permissions=int(fitz.PDF_PERM_ACCESSIBILITY),
    )
    doc.close()
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/restricted.pdf")
    if "--open" in sys.argv:
        build_open(out)
    elif "--user-password" in sys.argv:
        build_user_password(out)
    else:
        build_restricted(out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
