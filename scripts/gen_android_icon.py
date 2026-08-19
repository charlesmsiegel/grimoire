"""Generate the Android launcher icon from frontend/public/grimoire-512.png.

Run from the repo root: `backend/.venv/Scripts/python scripts/gen_android_icon.py`
(any interpreter with pillow will do). Writes into android/app/src/main/res/.

The source art is the book sitting on a dark rounded tile. An adaptive icon only
ever shows the centre 72dp of its 108dp canvas, so the tile cannot itself be the
picture: scaled small enough for the book to survive the mask, the tile's own
rounded edges land inside the visible window and the icon reads as a plaque
rather than as the book. So the book is cropped out of the tile interior,
feathered into transparency, and floated on a gradient matching the tile -- the
launcher's mask then cuts a dark field, and never the white plate Android 8+
pastes behind a legacy icon that has transparent corners.
"""
import os

from PIL import Image, ImageDraw

SRC = "frontend/public/grimoire-512.png"
RES = "android/app/src/main/res"

INK = (128, 107, 381, 437)   # measured: the gold/purple artwork ("the book")
CROP = (83, 60, 426, 458)    # tile interior around it; the feather stays on tile
TILE = ((30, 31, 38), (20, 21, 26))  # the tile's own top/bottom colours
# keep in step with drawable/ic_launcher_background.xml ---------^

DENSITIES = [("mdpi", 1), ("hdpi", 1.5), ("xhdpi", 2), ("xxhdpi", 3), ("xxxhdpi", 4)]

src = Image.open(SRC).convert("RGBA")


def book(ink_h, feather):
    """The book scaled to `ink_h` px tall, its edges faded out over `feather` px."""
    s = ink_h / (INK[3] - INK[1])
    crop = src.crop(CROP)
    w, h = round(crop.width * s), round(crop.height * s)
    img = crop.resize((w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rectangle([feather, feather, w - 1 - feather, h - 1 - feather], fill=255)
    for i in range(feather):
        d.rectangle([i, i, w - 1 - i, h - 1 - i], outline=round(255 * (i + 1) / feather))
    img.putalpha(mask)
    return img, ((INK[0] + INK[2]) / 2 - CROP[0]) * s, ((INK[1] + INK[3]) / 2 - CROP[1]) * s


def compose(size, ink_dp, canvas_dp, gradient=None):
    """`size` px of `canvas_dp`, with the book `ink_dp` tall, centred on its ink."""
    img, cx, cy = book(ink_dp * size / canvas_dp, max(1, round(size * 0.07)))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if gradient:
        top, bot = gradient
        d = ImageDraw.Draw(out)
        for y in range(size):
            t = y / (size - 1)
            d.line([(0, y), (size, y)],
                   fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
    out.alpha_composite(img, (round(size / 2 - cx), round(size / 2 - cy)))
    return out


for name, mult in DENSITIES:
    # Adaptive foreground: 108dp canvas, book 57dp tall -- the share of the
    # visible 72dp mask that the book has of the tile in the source art, and
    # comfortably inside the 66dp safe zone.
    d = f"{RES}/drawable-{name}"
    os.makedirs(d, exist_ok=True)
    compose(round(108 * mult), 57, 108).save(f"{d}/ic_launcher_foreground.png")

    # Legacy bitmap: opaque and full-bleed, so a launcher that ignores adaptive
    # icons masks the artwork itself instead of plating it on white.
    d = f"{RES}/mipmap-{name}"
    os.makedirs(d, exist_ok=True)
    compose(round(48 * mult), 36, 48, gradient=TILE).save(f"{d}/ic_launcher.png")

    print(f"wrote {name}")
