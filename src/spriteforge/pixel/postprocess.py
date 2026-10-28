import math

import numpy as np
from PIL import Image

from .palette import palette_rgb, quantize, rgb_to_lab
from .schemas import AssetSpec, Style


def remove_clusters(alpha: np.ndarray, minimum: int) -> np.ndarray:
    """Remove 4-connected components smaller than minimum; diagonal pixels are separate."""
    keep = alpha.copy()
    seen = np.zeros(alpha.shape, dtype=bool)
    height, width = alpha.shape
    for y, x in np.argwhere(alpha > 0):
        if seen[y, x]:
            continue
        todo, cluster = [(int(y), int(x))], []
        seen[y, x] = True
        while todo:
            cy, cx = todo.pop()
            cluster.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and alpha[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    todo.append((ny, nx))
        if len(cluster) < minimum:
            yy, xx = zip(*cluster, strict=True)
            keep[yy, xx] = 0
    return keep


def postprocess(raw: Image.Image, spec: AssetSpec, style: Style) -> Image.Image:
    pixels = np.array(raw.convert("RGBA"))
    key = palette_rgb([style.background])[0].astype(float)
    distance = np.linalg.norm(pixels[..., :3].astype(float) - key, axis=-1)
    pixels[distance <= style.chroma_tolerance, 3] = 0
    # Transparent RGB must not bleed into BOX coverage/color averaging.
    pixels[pixels[..., 3] == 0] = 0
    keyed = Image.fromarray(pixels)
    bbox = keyed.getbbox()
    if bbox is None:
        return Image.new("RGBA", spec.size)
    cropped = keyed.crop(bbox)
    # Reserve one further output pixel for the external 4-neighbor outline.
    margin = spec.margin + int(style.outline and spec.type != "tile")
    available = (spec.size[0] - 2 * margin, spec.size[1] - 2 * margin)
    if min(available) < 1:
        raise ValueError("Canvas has no room for padding and outline")
    scale = max(1, math.ceil(max(cropped.width / available[0], cropped.height / available[1])))
    canvas_size = (spec.size[0] * scale, spec.size[1] * scale)
    fit = min(available[0] * scale / cropped.width, available[1] * scale / cropped.height)
    content_size = (max(1, round(cropped.width * fit)), max(1, round(cropped.height * fit)))
    content = cropped.resize(content_size, Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", canvas_size)
    canvas.paste(
        content, ((canvas.width - content.width) // 2, (canvas.height - content.height) // 2)
    )
    pixels = np.array(canvas.resize(spec.size, Image.Resampling.BOX))
    pixels[..., :3] = quantize(pixels[..., :3], style.palette)
    pixels[..., 3] = np.where(pixels[..., 3] >= style.alpha_threshold, 255, 0)
    pixels[..., 3] = remove_clusters(pixels[..., 3], style.min_cluster_size)
    if style.outline:
        mask = pixels[..., 3] > 0
        padded = np.pad(mask, 1)
        neighbors = padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:]
        border = neighbors & ~mask
        colors = palette_rgb(style.palette)
        dark = (
            palette_rgb([style.outline_color])[0]
            if style.outline_color
            else colors[rgb_to_lab(colors)[:, 0].argmin()]
        )
        pixels[border, :3] = dark
        pixels[border, 3] = 255
    pixels[pixels[..., 3] == 0] = 0
    return Image.fromarray(pixels)
