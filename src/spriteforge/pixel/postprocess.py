import math

import numpy as np
from PIL import Image

from .palette import background_tinted_dark_colors, palette_rgb, palette_usage, quantize, rgb_to_lab
from .schemas import AssetSpec, Palette, ProcessingOptions, ProcessingReport, Style
from .source import mode_resize, prepare_source


def _record_palette(result: Image.Image, style: Style, report: ProcessingReport) -> None:
    palette = Palette(colors=style.palette)
    report.palette_size = len(palette.colors)
    report.palette_usage = palette_usage(result, palette)
    report.tinted_dark_colors = background_tinted_dark_colors(palette, report.background)
    report.warnings.extend(
        f"Unused palette color: {color}" for color, n in report.palette_usage.items() if n == 0
    )
    report.warnings.extend(
        f"Dark palette color {color} is tinted toward background {report.background}."
        for color in report.tinted_dark_colors
    )


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


def postprocess(
    raw: Image.Image,
    spec: AssetSpec,
    style: Style,
    *,
    options: ProcessingOptions | None = None,
    report: ProcessingReport | None = None,
) -> Image.Image:
    report = report if report is not None else ProcessingReport()
    if options is not None:
        raw = prepare_source(raw, options, report)
    pixels = np.array(raw.convert("RGBA"))
    key = palette_rgb([style.background])[0].astype(float)
    distance = np.linalg.norm(pixels[..., :3].astype(float) - key, axis=-1)
    if options is None:
        pixels[distance <= style.chroma_tolerance, 3] = 0
    # Transparent RGB must not bleed into BOX coverage/color averaging.
    pixels[pixels[..., 3] == 0] = 0
    keyed = Image.fromarray(pixels)
    bbox = keyed.getbbox()
    if bbox is None:
        empty = Image.new("RGBA", spec.size)
        _record_palette(empty, style, report)
        return empty
    cropped = keyed.crop(bbox)
    # Reserve one further output pixel for the external 4-neighbor outline.
    margin = spec.margin + int(style.outline and spec.type != "tile")
    available = (spec.size[0] - 2 * margin, spec.size[1] - 2 * margin)
    if min(available) < 1:
        raise ValueError("Canvas has no room for padding and outline")
    native = (
        options is not None
        and options.scale_mode != "fit"
        and report.logical_subject_size is not None
    )
    content = None
    if native:
        factor = max(1, math.ceil(max(cropped.width / available[0], cropped.height / available[1])))
        if factor == 1:
            content = cropped
            report.scale_mode, report.scale_factor = "native", 1.0
        else:
            suggested = max(cropped.width + 2 * margin, cropped.height + 2 * margin)
            report.warnings.append(
                f"Logical subject {cropped.width}x{cropped.height} exceeds usable canvas "
                f"{available[0]}x{available[1]}; downscaling. Use --size {suggested} or larger for 1:1 pixels."
            )
            if cropped.width % factor == 0 and cropped.height % factor == 0:
                content = mode_resize(cropped, (cropped.width // factor, cropped.height // factor))
                report.scale_mode, report.scale_factor = "native-integer-downscale", 1 / factor
            else:
                report.scale_mode = "native-mode-downscale"
    elif options is not None and options.scale_mode == "native":
        report.warnings.append(
            "No confident source grid detected; native placement unavailable, using fit."
        )
    if content is not None:
        canvas = Image.new("RGBA", spec.size)
        y = (
            spec.size[1] - margin - content.height
            if options.baseline == "bottom"
            else (spec.size[1] - content.height) // 2
        )
        canvas.paste(content, ((spec.size[0] - content.width) // 2, y))
        pixels = np.array(canvas)
    else:
        scale = max(1, math.ceil(max(cropped.width / available[0], cropped.height / available[1])))
        canvas_size = (spec.size[0] * scale, spec.size[1] * scale)
        fit = min(available[0] * scale / cropped.width, available[1] * scale / cropped.height)
        content_size = (max(1, round(cropped.width * fit)), max(1, round(cropped.height * fit)))
        report.scale_factor = fit / scale
        content = cropped.resize(content_size, Image.Resampling.NEAREST)
        canvas = Image.new("RGBA", canvas_size)
        y = (
            canvas.height - margin * scale - content.height
            if options and options.baseline == "bottom"
            else (canvas.height - content.height) // 2
        )
        canvas.paste(content, ((canvas.width - content.width) // 2, y))
        pixels = np.array(
            mode_resize(canvas, spec.size)
            if options is not None
            else canvas.resize(spec.size, Image.Resampling.BOX)
        )
    pixels[..., :3] = quantize(pixels[..., :3], style.palette)
    pixels[..., 3] = np.where(pixels[..., 3] >= style.alpha_threshold, 255, 0)
    pixels[..., 3] = remove_clusters(pixels[..., 3], style.min_cluster_size)
    if style.outline:
        mask = pixels[..., 3] > 0
        padded = np.pad(mask, 1)
        neighbors = padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:]
        if options is not None:
            # Fill diagonal corners too: a real-output outline must be 4-connected,
            # rather than four separate sides around a rectangular subject.
            neighbors |= padded[:-2, :-2] | padded[:-2, 2:] | padded[2:, :-2] | padded[2:, 2:]
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
    result = Image.fromarray(pixels)
    _record_palette(result, style, report)
    return result
