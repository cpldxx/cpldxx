"""Turn a picture into the ASCII portrait shown on the dashboard.

Run once whenever the picture changes; the result is committed as
assets/portrait.txt so the daily build needs no image libraries.

    pip install pillow
    python scripts/make_ascii.py assets/portrait.png
    python scripts/make_ascii.py assets/avatar.png --invert
"""

import argparse
from pathlib import Path

from PIL import Image, ImageOps

RAMP = " .:-=+*#%@"
COLS, ROWS = 46, 28
# On the dashboard each row is stretched to 228px and rows sit 7.6px apart,
# so crop to the shape the text block will have.
CELL_ASPECT = (228 / COLS) / 7.6


def to_ascii(path: Path, invert: bool) -> str:
    img = Image.open(path).convert("L")
    box_w, box_h = COLS * CELL_ASPECT, ROWS
    w, h = img.size
    if w / h > box_w / box_h:
        cw = round(h * box_w / box_h)
        img = img.crop(((w - cw) // 2, 0, (w + cw) // 2, h))
    else:
        ch = round(w * box_h / box_w)
        img = img.crop((0, (h - ch) // 2, w, (h + ch) // 2))
    img = img.resize((COLS, ROWS), Image.Resampling.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=2)
    if invert:
        img = ImageOps.invert(img)
    px = img.load()
    lines = []
    for y in range(ROWS):
        row = "".join(RAMP[px[x, y] * (len(RAMP) - 1) // 255] for x in range(COLS))
        lines.append(row.rstrip())
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--invert", action="store_true", help="dark parts of the picture become dense characters")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "assets" / "portrait.txt")
    args = ap.parse_args()
    args.out.write_text(to_ascii(args.image, args.invert))
    print(args.out.read_text())


if __name__ == "__main__":
    main()
