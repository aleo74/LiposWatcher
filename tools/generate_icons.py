from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "frontend" / "public"
root.mkdir(exist_ok=True)
for size in (192, 512):
    image = Image.new("RGB", (size, size), "#183c35")
    draw = ImageDraw.Draw(image)
    scale = size / 192
    def rect(box, **kwargs):
        draw.rounded_rectangle(tuple(round(v * scale) for v in box), radius=round(15 * scale), **kwargs)
    rect((32, 55, 148, 137), fill="#d9efe1")
    rect((145, 78, 163, 114), fill="#d9efe1")
    draw.polygon([(round(x * scale), round(y * scale)) for x, y in [(99, 69), (76, 104), (96, 104), (87, 126), (121, 89), (101, 89)]], fill="#207b55")
    image.save(root / f"icon-{size}.png")
