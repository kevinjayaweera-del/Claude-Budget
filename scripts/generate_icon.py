"""One-off script that draws the desktop-shortcut icon (app-icon.ico) at the
repo root. Not part of the running app — re-run manually if the icon design
should ever change."""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
BG_COLOR = (37, 99, 235)  # matches the app's --accent blue
BAR_COLOR = (255, 255, 255)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

corner_radius = SIZE // 5
draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=corner_radius, fill=BG_COLOR)

# Three ascending bars, a simple universally-readable "finance/chart" glyph.
bar_width = SIZE // 7
gap = SIZE // 10
base_y = int(SIZE * 0.72)
heights = [0.28, 0.44, 0.60]
start_x = int(SIZE * 0.22)
for i, h in enumerate(heights):
    x0 = start_x + i * (bar_width + gap)
    x1 = x0 + bar_width
    y0 = int(base_y - SIZE * h)
    y1 = base_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_width // 3, fill=BAR_COLOR)

out_path = Path(__file__).resolve().parent.parent / "app-icon.ico"
img.save(out_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])
print(f"wrote {out_path}")
