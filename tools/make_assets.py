"""Render the brand SVGs in doc/branding/ into every file the app, installer and README use.

    python tools/make_assets.py

The SVGs carry embedded fonts, masks and filters, so they are rendered with a headless Chrome or
Chromium rather than a Python SVG library. Run it again after changing anything in doc/branding/.

Writes:
    assets/icon.ico               exe, installer, tray icon and window icons (16-256 px)
    assets/logo.png               512 px, window icon on Linux
    installer/wizard-small.bmp    top-right image of the setup wizard
    doc/banner.png                README header
    doc/social-preview.png        GitHub social preview, uploaded by hand in the repo settings
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "doc" / "branding"
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
LOGO_SIZE = 512
WIZARD_SIZE = (138, 140)  # Inno Setup's largest WizardSmallImageFile size, it scales down itself.
WIZARD_BACKGROUND = (255, 255, 255)
CHROME_CANDIDATES = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        expanded = os.path.expandvars(candidate)
        found = expanded if Path(expanded).is_file() else shutil.which(expanded)
        if found:
            return found
    raise SystemExit("no Chrome, Chromium or Edge found to render the SVGs")


def render(chrome: str, svg: Path, width: int, height: int, work: Path) -> Image.Image:
    page = work / f"{svg.stem}.html"
    shot = work / f"{svg.stem}.png"
    page.write_text(
        "<html><head><style>html,body{margin:0;background:transparent}"
        f"img{{display:block;width:{width}px;height:{height}px}}</style></head>"
        f'<body><img src="{svg.as_uri()}"></body></html>',
        encoding="utf-8",
    )
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            f"--screenshot={shot}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    with Image.open(shot) as image:
        return image.convert("RGBA")


def trimmed_square(image: Image.Image, margin: float = 0.04) -> Image.Image:
    # The mark sits off-centre with a wide transparent border; trimming it makes 16 px readable.
    box = image.getchannel("A").point(lambda alpha: 255 if alpha > 8 else 0).getbbox()
    if box:
        image = image.crop(box)
    side = round(max(image.size) * (1 + 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def save_icons(mark: Image.Image) -> None:
    mark.save(ROOT / "assets" / "icon.ico", sizes=ICO_SIZES)
    mark.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS).save(ROOT / "assets" / "logo.png", optimize=True)


def save_wizard_image(mark: Image.Image) -> None:
    # Inno Setup wants a BMP without alpha, so flatten onto the wizard's white page.
    side = min(WIZARD_SIZE)
    small = mark.resize((side, side), Image.LANCZOS)
    canvas = Image.new("RGB", WIZARD_SIZE, WIZARD_BACKGROUND)
    canvas.paste(small, ((WIZARD_SIZE[0] - side) // 2, (WIZARD_SIZE[1] - side) // 2), small)
    canvas.save(ROOT / "installer" / "wizard-small.bmp")


def main() -> None:
    chrome = find_chrome()
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        mark = trimmed_square(render(chrome, BRAND / "mark.svg", 1024, 1024, work))
        banner = render(chrome, BRAND / "banner.svg", 1500, 500, work)
        social = render(chrome, BRAND / "og.svg", 1200, 630, work)

    save_icons(mark)
    save_wizard_image(mark)
    banner.convert("RGB").save(ROOT / "doc" / "banner.png", optimize=True)
    social.convert("RGB").save(ROOT / "doc" / "social-preview.png", optimize=True)
    print(
        "written: assets/icon.ico, assets/logo.png, installer/wizard-small.bmp, "
        "doc/banner.png, doc/social-preview.png"
    )


if __name__ == "__main__":
    main()
