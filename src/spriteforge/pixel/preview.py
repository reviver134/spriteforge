import math
from collections.abc import Mapping

from PIL import Image, ImageDraw


def upscale(image: Image.Image, factor: int = 8) -> Image.Image:
    if factor < 1:
        raise ValueError("factor must be a positive integer")
    return image.resize((image.width * factor, image.height * factor), Image.Resampling.NEAREST)


def contact_sheet(
    images: Mapping[str, Image.Image], columns: int = 4, factor: int = 8, gap: int = 12
) -> Image.Image:
    if not images or columns < 1 or factor < 1 or gap < 0:
        raise ValueError("Need images, positive columns/factor, and nonnegative gap")
    cell_width = max(160, max(image.width for image in images.values()) * factor)
    cell_height = max(image.height for image in images.values()) * factor + 24
    columns = min(columns, len(images))
    rows = math.ceil(len(images) / columns)
    sheet = Image.new(
        "RGBA", (columns * (cell_width + gap) + gap, rows * (cell_height + gap) + gap), "#202536"
    )
    draw = ImageDraw.Draw(sheet)
    for i, (name, image) in enumerate(images.items()):
        x = gap + (i % columns) * (cell_width + gap)
        y = gap + (i // columns) * (cell_height + gap)
        large = upscale(image.convert("RGBA"), factor)
        sheet.alpha_composite(large, (x + (cell_width - large.width) // 2, y))
        draw.text((x, y + cell_height - 18), name, fill="white")
    return sheet


def comparison(before: Image.Image, after: Image.Image, factor: int = 8) -> Image.Image:
    """Compare raw downscaled without correction against the clean sprite at equal scale."""
    naive = before.convert("RGBA").resize(after.size, Image.Resampling.BOX)
    return contact_sheet(
        {"Raw, BOX downscale": naive, "Harness output": after}, columns=2, factor=factor
    )
