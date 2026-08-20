# PyInstaller spec for DocCipher Breaker.
# Build with:  pyinstaller DocCipherBreaker.spec --noconfirm

from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    # The UI is served from disk at runtime, so it must ship inside the bundle.
    datas=[(str(ROOT / "backend" / "static"), "backend/static")],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # PyMuPDF ships a compiled extension PyInstaller cannot always see.
        "fitz",
        "pymupdf",
        # pywebview loads its GUI backend dynamically, so PyInstaller's import
        # scan never reaches it. edgechromium is the WebView2 backend used on
        # Windows 10/11; clr_loader and pythonnet are its .NET bridge.
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "pythonnet",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="DocCipherBreaker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False hides the window for the normal GUI launch. The CLI /
    # context-menu path writes to a log file instead of stdout.
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "icon.ico") if (ROOT / "assets" / "icon.ico").exists() else None,
)
