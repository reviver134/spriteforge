"""Big-cell grid recovery for image-model pixel art.

Muse Image draws "a 16x16 grid of big square pixels" as ~100 px cells, with a little jitter
and compression noise. `source.detect_grid` looks for blocks up to 64 px; this finds the cell
pitch of much larger blocks from the colour boundaries, then reads one colour per cell from
the cell's centre (median, so noise and soft edges do not matter).
"""

from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from PIL import Image

EDGE = 40  # RGB distance that counts as a boundary between two cells
MIN_PITCH = 4


@dataclass
class Grid:
    pitch: float
    # How much better these cells explain the image than cells shifted by half a cell:
    # ~1 for big-pixel art, ~0 for painted images (where any cut is as good as another).
    confidence: float
    lines: tuple[list[float], list[float]]  # x and y cell edges, in source pixels


def _boundaries(rgba: np.ndarray, axis: int) -> list[float]:
    """Positions (between pixels) where many rows (axis=1) or columns change colour."""
    rgb = rgba[..., :3].astype(np.int16)
    alpha = rgba[..., 3] > 0
    delta = np.linalg.norm(np.diff(rgb, axis=axis), axis=-1) >= EDGE
    flip = np.diff(alpha.astype(np.int8), axis=axis) != 0
    both = np.diff(alpha.astype(np.int8), axis=axis) == 0
    edges = (delta & both & alpha[:, 1:] if axis == 1 else delta & both & alpha[1:]) | flip
    profile = edges.sum(axis=1 - axis).astype(float)
    if not profile.any():
        return []
    marked = np.flatnonzero(profile >= max(2.0, 0.02 * profile.max()))
    groups = np.split(marked, np.flatnonzero(np.diff(marked) > 3) + 1)
    groups = [g for g in groups if len(g)]
    mass = np.array([profile[g].sum() for g in groups])
    # Short edges (half-cell details, soft shading) are much weaker than real cell edges.
    strong = mass >= 0.25 * np.percentile(mass, 75)
    return [float(np.average(g, weights=profile[g])) + 1 for g, k in zip(groups, strong) if k]


def _pitch(gaps: np.ndarray, top: float) -> float | None:
    """The most common one-cell gap: the pitch with the most gaps within 10% of it."""
    gaps = gaps[gaps >= MIN_PITCH * 0.9]
    if not len(gaps):
        return None
    # 0.5% steps from MIN_PITCH to `top`
    candidates = MIN_PITCH * 1.005 ** np.arange(int(np.log(top / MIN_PITCH) / np.log(1.005)) + 1)
    near = np.abs(gaps[None, :] - candidates[:, None]) <= 0.1 * candidates[:, None]
    best = candidates[near.sum(axis=1).argmax()]
    return float(np.median(gaps[np.abs(gaps - best) <= 0.1 * best]))


def detect_cells(
    image: Image.Image, min_boundaries: int = 8, pitch: float | None = None
) -> Grid | None:
    """Cell edges of a big-pixel image, or None. Image models draw cells of slightly uneven
    size, a little different per axis, with the odd half-cell detail, so the edges are the
    detected colour boundaries themselves, with a line added inside every gap that spans
    several cells and edges closer than half a cell skipped. `pitch` skips the estimate
    (frames of one sprite sheet share the pitch they were drawn at)."""
    rgba = np.asarray(image.convert("RGBA"))
    axes = [np.array(_boundaries(rgba, 1)), np.array(_boundaries(rgba, 0))]
    if min(len(a) for a in axes) < 2 or sum(len(a) for a in axes) < min_boundaries:
        return None
    gaps = np.concatenate([np.diff(a) for a in axes])
    pitch = pitch or _pitch(gaps, max(MIN_PITCH, min(image.size) / 2))
    if pitch is None:
        return None
    lines = tuple(
        _lines(np.concatenate([[0.0], a, [float(n)]]), _axis_pitch(a, pitch))
        for a, n in zip(axes, image.size, strict=True)
    )
    if min(len(ls) for ls in lines) < 3:
        return None  # fewer than two cells on an axis: nothing to read back
    aligned = _purity(rgba, lines)
    shifted = _purity(rgba, tuple([(a + b) / 2 for a, b in pairwise(ls)] for ls in lines))
    confidence = max(0.0, (aligned - shifted) / (1 - shifted)) if shifted < 1 else 0.0
    return Grid(pitch, confidence, lines)


def _axis_pitch(found: np.ndarray, pitch: float) -> float:
    """This axis's own one-cell gap (models draw cells a little wider than tall, or not)."""
    gaps = np.diff(found)
    single = gaps[np.abs(gaps - pitch) <= 0.15 * pitch]
    return float(np.median(single)) if len(single) >= 3 else pitch


def _lines(found: np.ndarray, pitch: float) -> list[float]:
    """Cell edges from sorted boundaries (image edges included)."""
    found = np.unique(found)
    lines = [float(found[0])]
    for b in found[1:]:
        n = round((b - lines[-1]) / pitch)
        if n == 0:
            continue  # a detail inside a cell, or the image edge just past the last line
        start = lines[-1]
        lines += [start + (b - start) * i / n for i in range(1, n + 1)]
    if found[-1] - lines[-1] > pitch / 2:
        lines.append(float(found[-1]))
    return lines


def _patches(rgba: np.ndarray, lines, share: float) -> np.ndarray:
    """The centre patch of every cell, `share` of the smallest cell's side, as one array
    (rows, cols, pixels, 4); patch pixels outside the image are transparent."""
    xs, ys = (np.array(v) for v in lines)
    side = max(1, int(share * min(np.diff(xs).min(), np.diff(ys).min())))
    cx, cy = (xs[:-1] + xs[1:]) / 2, (ys[:-1] + ys[1:]) / 2
    offsets = np.arange(side) - (side - 1) / 2
    px = np.round(cx[:, None] + offsets[None, :]).astype(int)  # (cols, side)
    py = np.round(cy[:, None] + offsets[None, :]).astype(int)  # (rows, side)
    padded = np.pad(rgba, ((1, 1), (1, 1), (0, 0)))  # index -1 / size -> transparent border
    px = np.clip(px + 1, 0, padded.shape[1] - 1)
    py = np.clip(py + 1, 0, padded.shape[0] - 1)
    patch = padded[py[:, None, :, None], px[None, :, None, :]]  # (rows, cols, side, side, 4)
    return patch.reshape(len(cy), len(cx), side * side, 4)


def _purity(rgba: np.ndarray, lines) -> float:
    """Share of opaque pixels within a small distance of their cell's median colour, over
    cells that are mostly opaque."""
    patch = _patches(rgba, lines, 0.7)
    opaque = patch[..., 3] > 0
    cells = opaque.mean(axis=-1) > 0.5
    if not cells.any():
        return 0.0
    rgb = patch[cells][..., :3].astype(int)  # (cells, pixels, 3)
    keep = opaque[cells]
    median = np.nanmedian(np.where(keep[..., None], rgb, np.nan), axis=1)
    near = (np.abs(rgb - median[:, None, :]).sum(axis=-1) <= 45) & keep
    return float(near.sum() / keep.sum())


def sample_cells(image: Image.Image, grid: Grid) -> Image.Image:
    """One pixel per cell: the median colour of the cell's centre; transparent if mostly so."""
    patch = _patches(np.asarray(image.convert("RGBA")), grid.lines, 0.5)
    opaque = patch[..., 3] > 0
    cells = opaque.mean(axis=-1) > 0.5
    out = np.zeros((*patch.shape[:2], 4), dtype=np.uint8)
    rgb = np.where(opaque[..., None], patch[..., :3].astype(float), np.nan)
    with np.errstate(all="ignore"):
        median = np.nan_to_num(np.nanmedian(rgb[cells], axis=1)) if cells.any() else 0
    out[cells, :3] = np.round(median)
    out[cells, 3] = 255
    return Image.fromarray(merge_close(out))


def merge_close(rgba: np.ndarray, distance: float = 12) -> np.ndarray:
    """Cells of one drawn colour come back a few values apart (noise, JPEG): give each group
    of near-identical colours its most common member."""
    opaque = rgba[..., 3] > 0
    if not opaque.any():
        return rgba
    colors, inverse, counts = np.unique(
        rgba[opaque][:, :3], axis=0, return_inverse=True, return_counts=True
    )
    reps: list[np.ndarray] = []
    mapped = np.empty_like(colors)
    for i in np.argsort(-counts, kind="stable"):
        near = [r for r in reps if np.linalg.norm(colors[i].astype(float) - r) <= distance]
        if not near:
            reps.append(colors[i].astype(float))
            near = [reps[-1]]
        mapped[i] = near[0]
    out = rgba.copy()
    out[opaque, :3] = mapped[inverse.reshape(-1)]
    return out
