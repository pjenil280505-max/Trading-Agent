"""Draw the Open Market OS app icon and write it in every format the installers need.

Run: python packaging/make_icons.py   (needs Pillow; the outputs are committed, so builds don't need it)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "icons"
S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # rounded-square background with a vertical gradient
    bg = Image.new("RGBA", (S, S))
    top, bottom = (29, 36, 54), (12, 16, 26)
    px = bg.load()
    for y in range(S):
        t = y / (S - 1)
        c = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    m = 64
    ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), radius=200, fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # grid lines
    for i in range(1, 4):
        y = m + (S - 2 * m) * i / 4
        d.line((m + 90, y, S - m - 90, y), fill=(42, 50, 72, 255), width=6)
    # candles
    candles = [(250, 600, 690, 540, 720, False), (360, 560, 640, 500, 680, True), (470, 470, 580, 430, 610, True),
               (580, 520, 600, 480, 650, False), (690, 400, 520, 350, 560, True), (800, 300, 430, 250, 470, True)]
    for x, o_top, o_bot, hi, lo, up in candles:
        col = (38, 166, 154, 255) if up else (239, 83, 80, 255)
        d.line((x, hi, x, lo), fill=col, width=10)
        d.rounded_rectangle((x - 34, o_top, x + 34, o_bot), radius=8, fill=col)
    # trend line with glow
    pts = [(220, 700), (360, 610), (470, 520), (580, 560), (690, 430), (820, 300)]
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=(61, 139, 253, 200), width=40, joint="curve")
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    d.line(pts, fill=(120, 180, 255, 255), width=18, joint="curve")
    # diamond mark (the app's ◆ logo)
    cx, cy, r = 300, 300, 92
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=(61, 139, 253, 255))
    d.polygon([(cx, cy - r + 36), (cx + r - 36, cy), (cx, cy + r - 36), (cx - r + 36, cy)], fill=(160, 200, 255, 255))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.save(OUT / "omos-1024.png")
    for size in (512, 256, 128, 64, 48, 32, 16):
        big.resize((size, size), Image.LANCZOS).save(OUT / f"omos-{size}.png")
    big.save(OUT / "omos.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(OUT / "omos.icns")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
