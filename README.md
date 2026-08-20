<div align="center">

<img src="assets/logo.svg" alt="DocCipher Breaker" width="180">

# DocCipher Breaker

**Document Security Analysis Tool**

`v1.0.0` · **Created by Achu Vijayakumar**

### **FOR EDUCATIONAL PURPOSES ONLY**

</div>

---

A desktop app that removes *editing restrictions* from `.docx` files and *usage restrictions* from `.pdf` files.

A Word document is a ZIP archive. When you use **Review → Restrict Editing**, Word writes a
`<w:documentProtection>` element into `word/settings.xml`. That element is the entire lock: it
tells Word to refuse edits, but the document text is stored in plain XML either way. Removing the
element removes the restriction. This app automates that eight-step process behind a terminal-styled UI.

---

## What this does and does not do

**Word documents (`.docx`)**

| | |
|---|---|
| **Removes** editing restrictions (`Restrict Editing`, read-only enforcement, form-field locks) | The lock is an instruction to Word, not encryption |
| **Removes** per-range `permStart` / `permEnd` markers | These lock individual regions of a document |
| **Does not** open password-encrypted documents | Those are AES-encrypted OLE containers; the content is genuinely unreadable without the password. The app detects them and stops with a clear message |

**PDF documents (`.pdf`)**

| | |
|---|---|
| **Removes** printing, copying, editing, commenting, form-filling and assembly restrictions | These are *owner password* restrictions. The file is encrypted with an **empty user password**, so any reader opens it without prompting and the decryption key is derivable without the owner password |
| **Does not** open PDFs that ask for a password | A *user password* genuinely encrypts the content. The app detects these and reports: *"This PDF is password-protected. You need the password to unlock it."* |

**Both formats**

| | |
|---|---|
| **Does not** remove digital signatures or DRM | Out of scope |
| **Does not** crack, brute-force, or guess any password | There is no password-attack code in this project |

Use this on documents you are entitled to edit — your own files, templates you need to adapt, or
documents where the restriction password has been lost. Removing a restriction does not grant
permission you did not already have.

---

## Safety design

The classic "ZIP trick" tutorial has you rename your original file to `.zip` and edit it in place.
If anything goes wrong halfway through, your original is gone. **This app never touches the original file.**

1. The source `.docx` is copied into a temporary directory
2. All work happens on the copy, entirely in memory
3. The result is written as `<name>_unlocked.docx` alongside the original
4. If a file with that name exists, `_2`, `_3`, … is appended — nothing is ever overwritten
5. Temporary files are removed even when a step fails

The rebuilt archive **preserves the original entry order and per-entry compression**.
This matters: `[Content_Types].xml` must come first, and rebuilding with `shutil.make_archive`
reshuffles entries — a common cause of Word's *"file is corrupt, do you want to repair it"* prompt.

### PDFs

PDFs are decrypted, never re-rendered. The text layer, forms, bookmarks and page count are
preserved — printing a PDF to a new PDF (a common "trick") flattens all of that and is not used here.

Two backends, tried in order:

1. **qpdf** (`qpdf --decrypt`) — preferred when present. Rewrites the file byte-faithfully.
   Optional; see [`tools/qpdf/README.txt`](tools/qpdf/README.txt) to bundle it.
2. **PyMuPDF** — always available, compiled into the executable. No external binary needed.

Every result is verified before delivery: the output must open without a password, keep the same
page count, and report no remaining restrictions.

---

## Install

### From the installer

Run `DocCipherBreaker_Setup_1.0.0.exe`. Optional tasks:

- **Desktop shortcut**
- **Context menu** — adds *"Unlock with DocCipher Breaker"* to the right-click menu **for `.docx` files only**.
  Right-clicking one or more `.docx` files unlocks them in place, no window needed.

### From the portable bundle

Unzip `release\` anywhere and run `DocCipherBreaker.exe`. Four files, no install:

| File | Purpose |
|---|---|
| `DocCipherBreaker.exe` | The application |
| `README.md` | This documentation |
| `LICENSE` | Legal terms and educational-use notice |
| `update.bat` | Updater (see below) |
| `version.txt` | Version manifest the updater reads |
| `SHA256SUMS.txt` | Checksum of the shipped executable |

### From source

```bash
pip install -r requirements.txt
python -m backend.main
```

The UI opens at <http://127.0.0.1:8000> automatically.

---

## Usage

### GUI

Drag one or more `.docx` or `.pdf` files onto the drop zone (or click to browse). Each file runs
through its format's steps (8 for DOCX, 6 for PDF) with live progress, then offers **Download**, **Open File**, and **New**.

Every operation is logged to SQLite and shown in the searchable **Activity Log** table.

### Command line

```bash
# Unlock one or more files in place (output written next to each input)
DocCipherBreaker.exe locked.docx another.docx

# From source
python -m backend.main locked.docx

# Run the server on a different port, without opening a browser
python -m backend.main --port 9000 --no-browser
```

### HTTP API

The local server exposes a JSON API for scripting:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Splash screen on first visit, then the main UI |
| `/app` | GET | Main UI (direct) |
| `/splash` | GET | Replay the splash screen |
| `/about` | GET | About dialog fragment |
| `/upload` | POST | Upload and unlock (multipart), returns an HTML fragment |
| `/download/{token}` | GET | Download an unlocked file |
| `/history` · `/stats` | GET | HTML fragments for the UI |
| `/api/crack` | POST | Unlock a file by local path (form field `path`) |
| `/api/batch` | POST | Unlock many paths — `{"paths": [...]}` |
| `/api/inspect` | GET | Report a file's protection state **without modifying it** |
| `/api/history` · `/api/stats` | GET | JSON history and counters |
| `/api/history` | DELETE | Clear history |
| `/api/docs` | GET | Interactive OpenAPI docs |

```bash
curl "http://127.0.0.1:8000/api/inspect?path=C:\docs\report.docx"
# {"protected":true,"edit_mode":"readOnly","enforced":true,"password_hashed":true}
```

---

## Updating

Run `update.bat` from the folder containing the app.

The updater is deliberately strict, because a self-updater that fetches and
executes a binary is the single most dangerous component in a desktop app:

1. Both the manifest URL and the download URL **must be HTTPS**. Plain HTTP is
   refused — an update fetched over HTTP can be swapped in transit.
2. The server's manifest **must carry a `sha256=` line**. An update with no
   published checksum is refused rather than installed unverified.
3. The download's actual SHA-256 **must match** that value. On any mismatch the
   file is deleted and your current version is left untouched.
4. The previous executable is kept as `DocCipherBreaker_old.exe` so you can roll
   back by renaming it.

**To publish an update**, host a `version.txt` in this format and point
`update_url` at it:

```ini
version=1.0.1
released=2026-09-01
download_url=https://your-host/DocCipherBreaker.exe
sha256=<the exact SHA-256 of that exe, uppercase hex>
```

`build.bat` prints the executable's SHA-256 and writes it to
`release\SHA256SUMS.txt` — paste that value into the `sha256=` line. If it does
not match the file you upload, the updater will refuse the update for everyone.

Never point `update_url` at a host you do not control. Anyone who can serve that
manifest can choose which binary your users run.

---

## Where files go

| | |
|---|---|
| Unlocked uploads | `%LOCALAPPDATA%\DocCipherBreaker\unlocked\` |
| History database | `%LOCALAPPDATA%\DocCipherBreaker\history.db` |
| CLI / context-menu output | Next to the input file |

The server binds to `127.0.0.1` only — it is not reachable from the network.

---

## Development

```bash
pip install -r requirements.txt
pip install pytest httpx

python -m pytest tests/ -q     # 97 tests
python -m backend.main         # run the app
```

`tests/make_fixture.py` builds a real, minimal, restricted `.docx` so the suite runs without Word installed.

### Build

```powershell
.\build.ps1                  # tests -> exe -> installer
.\build.ps1 -SkipTests       # skip the test run
.\build.ps1 -NoInstaller     # stop after the exe
```

Requires [PyInstaller](https://pyinstaller.org/) (installed automatically) and, for the installer,
[Inno Setup 6](https://jrsoftware.org/isdl.php).

To regenerate every logo asset (PNG, ICO, favicon, installer bitmaps) from
`assets/logo.svg`: `python assets/build_assets.py`.

---

## Project layout

```
DocCipher/
├── backend/
│   ├── main.py            FastAPI app, routes, HTML rendering
│   ├── cracker.py         Core unlocking logic (no web dependencies)
│   ├── database.py        SQLite history
│   ├── models.py          Pydantic models
│   ├── icons.py           Inline SVG icon set (no emoji anywhere)
│   └── static/            index.html, splash.html, css/, js/, img/, favicon.ico
├── tests/                 pytest suite + .docx fixture builder
├── assets/
│   ├── logo.svg           Master logo — skull with one glowing green eye
│   ├── build_assets.py    Generates logo.png, icon.ico, favicon.ico, wizard BMPs
│   └── ...                Generated raster assets
├── installer/
│   ├── setup.iss          Inno Setup script
│   └── EULA.txt           Licence and educational-use notice
├── launcher.py            Frozen-build entry point
├── DocCipherBreaker.spec  PyInstaller spec
└── build.ps1              Build pipeline
```

`cracker.py` has no web dependencies and can be imported directly:

```python
from backend.cracker import DocCracker, inspect

result = DocCracker("locked.docx").unlock()
print(result.status, result.output_path)
```

---

## Branding

The logo is a single hand-written SVG (`assets/logo.svg`) — a red skull with one glowing green
eye over a faint matrix-code plate. Every raster format is generated from it, so the SVG is the
only file to edit:

```bash
python assets/build_assets.py
```

| Output | Purpose |
|---|---|
| `assets/logo.png` | 1024×1024 high-res master |
| `assets/icon.ico` | App, shortcut, and installer icon (16–256) |
| `assets/favicon.ico` | Web UI favicon (16, 32, 48) |
| `assets/wizard_large.bmp` · `wizard_small.bmp` | Inno Setup wizard panels |
| `backend/static/img/` | Copies served by the web UI |

**The UI contains no emoji.** Emoji render in the platform's own colours and weight, which fights
the monochrome terminal theme and looks different on every machine. All glyphs are inline SVG that
inherits `currentColor` — see `backend/icons.py`.

**Colour scheme**

| Token | Hex | Use |
|---|---|---|
| Primary | `#0a0a0f` | Background |
| Secondary | `#00ff41` | Hacker green — text, borders, the eye |
| Accent | `#ff0040` | Blood red — the skull |
| Tertiary | `#ff6600` | Warm orange — glow, the educational notice |

---

## Tech stack

FastAPI · HTMX · SQLite · PyMuPDF · PyInstaller · Inno Setup. No CDN dependencies — HTMX is vendored and
the CSS is hand-written, so the app works fully offline.

---

## Legal and disclaimer

### FOR EDUCATIONAL PURPOSES ONLY

This software is published to demonstrate how the OOXML document format stores editing
restrictions. Read this section before using it.

**What the restriction actually is.** Word's *Restrict Editing* feature writes a
`<w:documentProtection>` element into `word/settings.xml`. It is an instruction to Word, not
encryption — the document's text sits in readable XML whether or not the element is present. It is
a courtesy lock that signals the author's intent, and it was never designed as a security boundary.

**What this tool cannot do.** It contains no password cracking, brute-force, or cryptographic
attack capability. Password-*encrypted* documents are a different file format entirely (an
encrypted OLE compound file) and are genuinely unreadable without the password. This tool detects
them and refuses to proceed.

**Permitted use.** Documents you created; documents you own or administer; documents you have the
owner's permission to edit; templates you are authorised to adapt; your own documents whose
restriction password has been lost; and study of the file format itself.

**Prohibited use.** Altering a document you have no right to alter — including contracts, records,
invoices, transcripts, or certificates that another party relies on being unaltered; defeating a
restriction where doing so breaches a contract, licence, or policy; misrepresenting an altered
document as an original; or any unlawful purpose.

**Removing a technical restriction does not grant permission.** If you were not entitled to edit a
document before using this tool, you are not entitled to edit it afterwards. The absence of a lock
is not consent.

**On the "educational purposes" label.** It states the author's intent in publishing this software.
It is a statement of purpose, not a legal exemption — it does not make an otherwise unlawful act
lawful, and it does not transfer responsibility from the user to the author. You are solely
responsible for ensuring your use complies with the laws, contracts, and policies that apply to you.

**No warranty.** This software is provided "AS IS", without warranty of any kind, express or
implied. Although it is designed never to modify your original files, no software is free of
defects — keep backups of documents that matter. In no event shall the author be liable for any
claim, damages, or data loss arising from its use. The author is not responsible for misuse.

The full licence text is in [`installer/EULA.txt`](installer/EULA.txt) and is presented for
acceptance during installation.

Microsoft, Word, and Windows are trademarks of Microsoft Corporation. This project is not
affiliated with, endorsed by, or sponsored by Microsoft Corporation.

---

<div align="center">

**Created by Achu Vijayakumar** · © 2026

**FOR EDUCATIONAL PURPOSES ONLY**

</div>
