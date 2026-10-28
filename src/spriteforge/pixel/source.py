"""Source-image cleanup before final pixel sizing. No provider dependencies."""

import numpy as np
from PIL import Image

from .palette import rgb_to_lab
from .schemas import DroppedComponent, ProcessingOptions, ProcessingReport


def components(
    mask: np.ndarray,
) -> tuple[np.ndarray, list[tuple[int, int, tuple[int, int, int, int]]]]:
    """4-connected components using scanline runs (avoids per-source-pixel Python objects)."""
    labels = np.zeros(mask.shape, dtype=np.int32)
    parents = [0]
    records = []
    previous = []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for y, row in enumerate(mask):
        changes = np.diff(np.pad(row.astype(np.int8), 1))
        starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
        current = []
        before = 0
        for left, right in zip(starts, ends, strict=True):
            label = len(parents)
            parents.append(label)
            while before < len(previous) and previous[before][1] <= left:
                before += 1
            j = before
            while j < len(previous) and previous[j][0] < right:
                a, b = root(label), root(previous[j][2])
                parents[max(a, b)] = min(a, b)
                j += 1
            labels[y, left:right] = label
            current.append((int(left), int(right), label))
            records.append((label, y, int(left), int(right)))
        previous = current
    lookup = np.array([root(i) for i in range(len(parents))], dtype=np.int32)
    labels = lookup[labels]
    groups = {}
    for label, y, left, right in records:
        ident = int(lookup[label])
        if ident not in groups:
            groups[ident] = [0, left, y, right, y + 1]
        group = groups[ident]
        group[0] += right - left
        group[1:] = [
            min(group[1], left),
            min(group[2], y),
            max(group[3], right),
            max(group[4], y + 1),
        ]
    return labels, sorted(
        [(ident, data[0], tuple(data[1:])) for ident, data in groups.items()],
        key=lambda c: (-c[1], c[0]),
    )


def estimate_background(image: Image.Image) -> np.ndarray | None:
    rgba = np.asarray(image.convert("RGBA"))
    band = max(1, min(image.size) // 100)
    border = np.concatenate(
        [
            rgba[:band].reshape(-1, 4),
            rgba[-band:].reshape(-1, 4),
            rgba[:, :band].reshape(-1, 4),
            rgba[:, -band:].reshape(-1, 4),
        ]
    )
    # An already transparent border is not a chroma background.
    if np.count_nonzero(border[:, 3] > 0) < len(border) // 2:
        return None
    return np.median(border[border[:, 3] > 0, :3], axis=0).round().astype(np.uint8)


def key_background(
    raw: Image.Image, options: ProcessingOptions, report: ProcessingReport
) -> Image.Image:
    pixels = np.array(raw.convert("RGBA"))
    background = estimate_background(raw)
    if background is None:
        return Image.fromarray(pixels)
    report.background = "#" + "".join(f"{v:02X}" for v in background)
    reference = rgb_to_lab(background)
    distances = np.empty(pixels.shape[:2], dtype=np.float32)
    for y in range(0, len(pixels), 128):
        distances[y : y + 128] = np.linalg.norm(
            rgb_to_lab(pixels[y : y + 128, :, :3]) - reference, axis=-1
        )
    pixels[distances <= options.background_tolerance, 3] = 0
    # Decontaminate only background-like pixels adjoining removed background. Iterate
    # a narrow band so a genuine purple accent inside the subject is never globally erased.
    for _ in range(3):
        transparent = pixels[..., 3] == 0
        p = np.pad(transparent, 1, constant_values=True)
        edge = p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
        spill = edge & ~transparent & (distances <= options.despill_tolerance)
        report.despilled_pixels += int(spill.sum())
        pixels[spill, 3] = 0
    pixels[pixels[..., 3] == 0] = 0
    return Image.fromarray(pixels)


def isolate_subject(
    image: Image.Image, options: ProcessingOptions, report: ProcessingReport
) -> Image.Image:
    pixels = np.array(image.convert("RGBA"))
    labels, groups = components(pixels[..., 3] > 0)
    if not groups:
        return image.copy()
    largest = groups[0][1]
    keep = [groups[0][0]]
    for ident, area, bbox in groups[1:]:
        ratio = area / largest
        if options.keep_detached and ratio >= options.detached_min_area_ratio:
            keep.append(ident)
        else:
            report.dropped.append(DroppedComponent(area=area, bbox=bbox, area_ratio=ratio))
    pixels[~np.isin(labels, keep)] = 0
    return Image.fromarray(pixels)


def mode_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Area-footprint mode, with transparent coverage voting and deterministic RGB ties.

    Upscaling is nearest-neighbor. Shrinking chooses a source color, never an average.
    """
    rgba = np.asarray(image.convert("RGBA"))
    width, height = size
    if width >= image.width and height >= image.height:
        return image.resize(size, Image.Resampling.NEAREST)
    output = np.zeros((height, width, 4), dtype=np.uint8)
    for y in range(height):
        top, bottom = (
            y * image.height // height,
            max(y * image.height // height + 1, (y + 1) * image.height // height),
        )
        for x in range(width):
            left, right = (
                x * image.width // width,
                max(x * image.width // width + 1, (x + 1) * image.width // width),
            )
            block = rgba[top:bottom, left:right].reshape(-1, 4)
            visible = block[block[:, 3] > 0]
            if len(visible) * 2 < len(block) or not len(visible):
                continue
            colors, counts = np.unique(visible, axis=0, return_counts=True)
            output[y, x] = colors[counts.argmax()]
    return Image.fromarray(output)


def detect_grid(
    image: Image.Image, report: ProcessingReport | None = None
) -> tuple[int | None, tuple[int, int]]:
    """Estimate repeated horizontal/vertical color-run lengths, ignoring low-level texture."""
    rgba = np.asarray(image.convert("RGBA"))
    rgb = rgba[..., :3].astype(np.int16)
    candidates = []
    positions_by_axis = []
    confidence = []
    for axis in (1, 0):
        delta = np.linalg.norm(np.diff(rgb, axis=axis), axis=-1)
        alpha = rgba[..., 3] > 0
        valid = (alpha[:, 1:] & alpha[:, :-1]) if axis == 1 else (alpha[1:] & alpha[:-1])
        edges = (delta >= 30) & valid
        lines = edges if axis == 1 else edges.T
        gaps, positions = [], []
        # Sample bounded, evenly-spaced scanlines through the source.
        for row in lines[np.linspace(0, len(lines) - 1, min(128, len(lines)), dtype=int)]:
            found = np.flatnonzero(row)
            if len(found):
                found = found[np.r_[True, np.diff(found) > 1]] + 1
                positions.extend(found.tolist())
                gaps.extend(np.diff(found).tolist())
        gaps = np.array(gaps)
        useful = gaps[(gaps >= 2) & (gaps <= 64)]
        if len(useful) < 12:
            return None, (0, 0)
        histogram = np.bincount(useful, minlength=66)
        scores = histogram[2:65] + 0.4 * (histogram[1:64] + histogram[3:66])
        block = int(scores.argmax() + 2)
        nearby = useful[np.abs(useful - block) <= 1]
        block = int(np.median(nearby).round())
        # A grid needs a repeated fundamental, not just one long rectangle edge.
        if len(nearby) < 8 or len(nearby) / len(useful) < 0.2:
            return None, (0, 0)
        candidates.append(block)
        confidence.append(len(nearby) / len(useful))
        positions_by_axis.append(positions)
    if abs(candidates[0] - candidates[1]) > 1:
        return None, (0, 0)
    block = round(sum(candidates) / 2)
    if report is not None:
        report.grid_confidence = min(confidence)
    offsets = tuple(
        int(np.bincount(np.array(p) % block, minlength=block).argmax()) for p in positions_by_axis
    )
    return block, offsets


def snap_grid(image: Image.Image, block: int, offset: tuple[int, int]) -> Image.Image:
    """Pad to the detected lattice, then take the mode in each source grid cell."""
    x, y = offset
    left, top = (-x) % block, (-y) % block
    width = (image.width + left + block - 1) // block
    height = (image.height + top + block - 1) // block
    canvas = Image.new("RGBA", (width * block, height * block))
    canvas.paste(image, (left, top))
    return mode_resize(canvas, (width, height))


def prepare_source(
    raw: Image.Image, options: ProcessingOptions, report: ProcessingReport
) -> Image.Image:
    image = isolate_subject(key_background(raw, options, report), options, report)
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    if options.detect_grid and bbox:
        report.grid_size, report.grid_offset = detect_grid(image, report)
        if report.grid_size and report.grid_confidence >= options.native_grid_confidence:
            image = snap_grid(image, report.grid_size, report.grid_offset)
            logical_bbox = image.getbbox()
            if logical_bbox:
                image = image.crop(logical_bbox)
                report.logical_subject_size = image.size
        elif report.grid_size:
            report.warnings.append(
                f"Source grid confidence {report.grid_confidence:.2f} is too low; using unsnapped fit."
            )
    return image
