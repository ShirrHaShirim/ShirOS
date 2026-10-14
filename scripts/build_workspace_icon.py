"""Rasterize the code-drawn ShirOS desktop mark at Windows icon sizes."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCALE = 3
SIZE = 512


def bezier(points: list[tuple[int, int]]) -> list[tuple[float, float]]:
    result = []
    for i in range(101):
        t = i / 100
        weights = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3)
        result.append(
            (
                sum(w * p[0] for w, p in zip(weights, points, strict=True)) * SCALE,
                sum(w * p[1] for w, p in zip(weights, points, strict=True)) * SCALE,
            )
        )
    return result


image = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE))
mask = Image.new("L", image.size)
ImageDraw.Draw(mask).rounded_rectangle((48, 48, 1488, 1488), radius=336, fill=255)
background = Image.new("RGBA", image.size)
draw = ImageDraw.Draw(background)
for y in range(SIZE * SCALE):
    t = y / (SIZE * SCALE - 1)
    color = tuple(round(a + (b - a) * t) for a, b in zip((38, 60, 81), (16, 25, 35), strict=False))
    draw.line((0, y, SIZE * SCALE, y), fill=color)
image.paste(background, (0, 0), mask)
draw = ImageDraw.Draw(image)
draw.ellipse(tuple(v * SCALE for v in (75, 75, 437, 437)), outline=(85, 85, 79), width=2 * SCALE)
curves = [
    [(331, 157), (265, 113), (170, 146), (173, 210)],
    [(173, 210), (176, 251), (229, 255), (266, 270)],
    [(266, 270), (327, 295), (349, 335), (305, 367)],
    [(305, 367), (268, 394), (211, 383), (181, 357)],
]
for curve in curves:
    for point_x, point_y in bezier(curve):
        radius = 15 * SCALE
        draw.ellipse(
            (point_x - radius, point_y - radius, point_x + radius, point_y + radius),
            fill=(241, 214, 163),
        )
for x, y in [(331, 157), (181, 357)]:
    draw.ellipse(tuple(v * SCALE for v in (x - 15, y - 15, x + 15, y + 15)), fill=(241, 214, 163))
draw.polygon(
    [
        (x * SCALE, y * SCALE)
        for x, y in [
            (368, 99),
            (376, 121),
            (398, 129),
            (376, 137),
            (368, 159),
            (360, 137),
            (338, 129),
            (360, 121),
        ]
    ],
    fill=(247, 228, 173),
)
for x, y1, y2 in [(114, 279, 324), (133, 268, 335), (152, 286, 317)]:
    draw.rounded_rectangle(
        tuple(v * SCALE for v in (x - 3, y1, x + 3, y2)), radius=3 * SCALE, fill=(178, 208, 205)
    )
image = image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
image.save(ROOT / "desktop" / "shiros-icon.png")
image.save(
    ROOT / "desktop" / "shiros-icon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
