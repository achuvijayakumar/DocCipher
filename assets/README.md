# Logo and icon assets

`logo.svg` is the master. Every other file here is generated from it — edit the
SVG, run the build script, and all formats regenerate together.

```bash
python assets/build_assets.py
```

| File | Size | Used by |
|---|---|---|
| `logo.svg` | vector | Web UI (header, splash, modals, About) |
| `logo.png` | 1024×1024 | High-resolution master, store listings, README |
| `icon.ico` | 16, 24, 32, 48, 64, 128, 256 | The `.exe`, desktop and Start-menu shortcuts, installer, right-click menu |
| `favicon.ico` | 16, 32, 48 | Browser tab |
| `wizard_large.bmp` | 164×314 | Inno Setup welcome/finish panel |
| `wizard_small.bmp` | 55×58 | Inno Setup page header |

Generated files are copied into `backend/static/img/` and `backend/static/` so
the app serves them directly.

---

## How the conversion works

`build_assets.py` rasterises the SVG with Playwright's bundled Chromium, then
resizes with Pillow. Both are already dev dependencies, so there is nothing
extra to install — and no ImageMagick, Inkscape, or cairosvg required.

SMIL animation (the pulsing eye) is paused before capture, so every still is
deterministic rather than catching a random animation frame.

```python
page.evaluate("() => document.querySelector('svg')?.pauseAnimations?.()")
page.screenshot(path=out, omit_background=True)
```

---

## Doing it by hand

If you would rather not run the script, any of these produce the same outputs.

### SVG → PNG (1024×1024)

**Inkscape** (free, cross-platform):
```bash
inkscape logo.svg --export-type=png --export-filename=logo.png -w 1024 -h 1024
```

**ImageMagick** (needs a working SVG delegate such as librsvg):
```bash
magick -background none -density 384 logo.svg -resize 1024x1024 logo.png
```

**rsvg-convert**:
```bash
rsvg-convert -w 1024 -h 1024 -o logo.png logo.svg
```

**Browser, no tools at all**: open `logo.svg`, screenshot at a large zoom, crop
square. Fine for a one-off; the script is better for repeatability.

### PNG → ICO (multi-resolution)

A Windows `.ico` should carry **several sizes in one file** — Windows picks the
right one per context (16px in the title bar, 256px on the desktop). A single
256px image scaled down by the OS looks muddy at 16px.

**ImageMagick** — all seven sizes in one command:
```bash
magick logo.png -define icon:auto-resize=256,128,64,48,32,24,16 icon.ico
```

**Pillow** (Python, no external tools):
```python
from PIL import Image

img = Image.open("logo.png").convert("RGBA")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save("icon.ico", format="ICO", sizes=sizes)
```

**Online**: [realfavicongenerator.net](https://realfavicongenerator.net) or
[icoconvert.com](https://icoconvert.com). Upload the 1024px PNG and ask for a
multi-size ICO. Only use these for assets you are happy to upload.

### Favicon

Same as above, but 16/32/48 only — a browser tab never needs 256px:
```bash
magick logo.png -define icon:auto-resize=48,32,16 favicon.ico
```

### Installer bitmaps

Inno Setup reads **BMP only** — it cannot load PNG or SVG. Exact sizes matter;
anything else is stretched:

- `WizardImageFile` → 164×314
- `WizardSmallImageFile` → 55×58

```bash
magick logo.png -resize 132x132 -background "#0d0d12" -gravity center \
       -extent 164x314 BMP3:wizard_large.bmp
magick logo.png -resize 46x46 -background "#0d0d12" -gravity center \
       -extent 55x58 BMP3:wizard_small.bmp
```

`build_assets.py` also draws the app name and the educational-use notice onto
the large panel, which the command above does not — check `write_wizard_bitmaps()`
if you are reproducing it manually.

---

## After regenerating

The `.exe` embeds `icon.ico` at build time, so a new icon needs a rebuild:

```powershell
.\build.ps1
```

Windows also caches shortcut icons aggressively. If an old icon persists after
reinstalling, clear the cache:

```powershell
ie4uinit.exe -show
```

---

## Editing the logo

The mark is a red skull with one glowing green eye, over a faint matrix-code
plate. Colours are defined as gradient stops near the top of `logo.svg`:

| Element | Colour |
|---|---|
| Skull | `#cc0033` → `#ff2d55` gradient |
| Live eye | `#00ff41` with a `#ccffdd` highlight |
| Plate | `#0a0a0f` |
| Rim light | `#ff6600` |

Two constraints worth keeping:

1. **It must read at 16px.** Fine detail disappears in the taskbar. Squint at
   `icon.ico` after any change.
2. **XML comments cannot contain `--`.** A decorative `<!-- ---- section ---- -->`
   is a parse error, and the SVG will silently fail to render.

---

Created by Achu Vijayakumar · FOR EDUCATIONAL PURPOSES ONLY
