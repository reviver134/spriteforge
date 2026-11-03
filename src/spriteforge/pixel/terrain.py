"""Deterministic seam cleanup and measured terrain/actor visual hierarchy."""

import numpy as np
from PIL import Image
from pydantic import BaseModel

from .palette import quantize


class VisualWeight(BaseModel):
    local_contrast: float
    distinct_colors: int


def visual_weight(image):
    rgba = np.asarray(image.convert("RGBA"))
    visible = rgba[..., 3] > 0
    luminance = rgba[..., :3] @ np.array([0.2126, 0.7152, 0.0722]) / 255
    deltas = []
    for axis in (0, 1):
        pairs = np.take(visible, range(visible.shape[axis] - 1), axis=axis) & np.take(
            visible, range(1, visible.shape[axis]), axis=axis
        )
        deltas.extend(np.abs(np.diff(luminance, axis=axis))[pairs].tolist())
    return VisualWeight(
        local_contrast=float(np.mean(deltas)) if deltas else 0,
        distinct_colors=len(set(map(tuple, rgba[..., :3][visible]))),
    )


def repair_seams(image, palette):
    """Blend opposing edges symmetrically, then snap to the approved palette."""
    pixels = np.asarray(image.convert("RGBA")).copy()
    rgb = pixels[..., :3].astype(float)
    edge = (rgb[:, 0] + rgb[:, -1]) / 2
    rgb[:, 0] = rgb[:, -1] = edge
    if rgb.shape[1] > 3:
        rgb[:, 1] = (3 * rgb[:, 1] + edge) / 4
        rgb[:, -2] = (3 * rgb[:, -2] + edge) / 4
    edge = (rgb[0] + rgb[-1]) / 2
    rgb[0] = rgb[-1] = edge
    if rgb.shape[0] > 3:
        rgb[1] = (3 * rgb[1] + edge) / 4
        rgb[-2] = (3 * rgb[-2] + edge) / 4
    pixels[..., :3] = quantize(np.rint(rgb).astype(np.uint8), palette)
    pixels[:, -1, 3] = pixels[:, 0, 3]
    pixels[-1, :, 3] = pixels[0, :, 3]
    return Image.fromarray(pixels)
