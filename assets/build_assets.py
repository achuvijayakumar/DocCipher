"""Generate every raster asset from assets/logo.svg.

Produces:
    assets/logo.png            1024x1024 high-res master
    assets/icon.ico            app / installer / shortcut icon (16-256)
    assets/favicon.ico         web UI favicon (16, 32, 48)
    assets/wizard_large.bmp    Inno Setup welcome/finish panel (164x314)
    assets/wizard_small.bmp    Inno Setup page header (55x58)
    backend/static/img/logo.svg      copy served by the web UI
    backend/static/favicon.ico       served at /favicon.ico

The SVG is rasterized with Playwright's Chromium, which is already a project
dev dependency -- this avoids requiring cairosvg / Inkscape / ImageMagick.
SMIL animations are frozen at their first frame for the still exports.

    python assets/build_assets.py
"""

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVG = ASSETS / "logo.svg"
STATIC = ROOT / "backend" / "static"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
FAVICON_SIZES = [16, 32, 48]
MASTER = 1024


def render_png(out: Path, size: int) -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": size, "height": size}, device_scale_factor=1
        )
        page.goto(SVG.resolve().as_uri())
        # Freeze SMIL so the exported still is deterministic.
        page.evaluate("() => document.querySelector('svg')?.pauseAnimations?.()")
        page.wait_for_timeout(250)
        page.screenshot(path=str(out), omit_background=True)
        browser.close()
    return out


def write_ico(source: Image.Image, out: Path, sizes: list[int]) -> None:
    frames = [source.resize((n, n), Image.LANCZOS) for n in sizes]
    frames[-1].save(out, format="ICO", sizes=[(n, n) for n in sizes])
    print(f"  {out.relative_to(ROOT)}  ({out.stat().st_size:,} bytes, {len(sizes)} sizes)")


def _mono(size: int) -> "ImageFont.FreeTypeFont":
    for name in ("consola.ttf", "cour.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def write_wizard_bitmaps(logo: Image.Image) -> None:
    """Inno Setup requires BMP; it cannot read PNG or SVG."""
    bg = (10, 10, 15)

    # Welcome / finish panel.
    large = Image.new("RGB", (164, 314), bg)
    d = ImageDraw.Draw(large)
    for y in range(314):
        t = 1 - abs(y - 110) / 200
        if t > 0:
            d.line([(0, y), (164, y)], fill=(int(10 + 26 * t), 10, int(15 + 10 * t)))
    mark = logo.resize((132, 132), Image.LANCZOS)
    large.paste(mark, (16, 44), mark)
    d.text((82, 196), "DocCipher", font=_mono(15), fill=(0, 255, 65), anchor="mm")
    d.text((82, 214), "Breaker", font=_mono(15), fill=(0, 255, 65), anchor="mm")
    d.text((82, 236), "v1.0.0", font=_mono(10), fill=(75, 122, 92), anchor="mm")
    d.text((82, 268), "FOR EDUCATIONAL", font=_mono(8), fill=(255, 102, 0), anchor="mm")
    d.text((82, 280), "PURPOSES ONLY", font=_mono(8), fill=(255, 102, 0), anchor="mm")
    d.text((82, 298), "by Achu Vijayakumar", font=_mono(7), fill=(75, 122, 92), anchor="mm")
    large.save(ASSETS / "wizard_large.bmp", "BMP")

    # Inner-page header.
    small = Image.new("RGB", (55, 58), bg)
    m2 = logo.resize((46, 46), Image.LANCZOS)
    small.paste(m2, (4, 6), m2)
    small.save(ASSETS / "wizard_small.bmp", "BMP")

    for name in ("wizard_large.bmp", "wizard_small.bmp"):
        out = ASSETS / name
        print(f"  {out.relative_to(ROOT)}  ({out.stat().st_size:,} bytes)")


def main() -> int:
    if not SVG.exists():
        print(f"missing {SVG}", file=sys.stderr)
        return 1

    print("Rendering master PNG...")
    png = ASSETS / "logo.png"
    render_png(png, MASTER)
    master = Image.open(png).convert("RGBA")
    print(f"  {png.relative_to(ROOT)}  ({png.stat().st_size:,} bytes, {MASTER}x{MASTER})")

    print("Writing icons...")
    write_ico(master, ASSETS / "icon.ico", ICO_SIZES)
    write_ico(master, ASSETS / "favicon.ico", FAVICON_SIZES)

    print("Writing installer bitmaps...")
    write_wizard_bitmaps(master)

    print("Copying into the web UI...")
    (STATIC / "img").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SVG, STATIC / "img" / "logo.svg")
    shutil.copy2(ASSETS / "logo.png", STATIC / "img" / "logo.png")
    shutil.copy2(ASSETS / "favicon.ico", STATIC / "favicon.ico")
    print("  backend/static/img/logo.svg, logo.png, backend/static/favicon.ico")

    print("\nDone. Rebuild the exe (build.ps1) to embed the new icon.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
