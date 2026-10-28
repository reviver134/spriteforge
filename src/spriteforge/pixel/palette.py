"""D65 CIELAB conversion, deterministic weighted k-means, and palette files."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .schemas import Palette

WHITE = np.array([0.95047, 1.0, 1.08883])
RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = (linear @ RGB_TO_XYZ.T) / WHITE
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    xyz = np.where(f > 6 / 29, f**3, 3 * (6 / 29) ** 2 * (f - 4 / 29)) * WHITE
    linear = np.clip(xyz @ np.linalg.inv(RGB_TO_XYZ).T, 0, 1)
    rgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    return np.rint(rgb * 255).clip(0, 255).astype(np.uint8)


def palette_rgb(colors: list[str]) -> np.ndarray:
    return np.array([[int(c[i : i + 2], 16) for i in (1, 3, 5)] for c in colors], dtype=np.uint8)


def nearest_indices(points: np.ndarray, centers: np.ndarray) -> np.ndarray:
    labels = np.empty(len(points), dtype=np.int32)
    for start in range(0, len(points), 8192):
        chunk = points[start : start + 8192]
        labels[start : start + len(chunk)] = ((chunk[:, None] - centers) ** 2).sum(-1).argmin(-1)
    return labels


def quantize(rgb: np.ndarray, colors: list[str]) -> np.ndarray:
    table = palette_rgb(colors)
    flat = np.asarray(rgb).reshape(-1, 3)
    # Work on unique colors to keep large flat sheets inexpensive.
    unique, inverse = np.unique(flat, axis=0, return_inverse=True)
    labels = nearest_indices(rgb_to_lab(unique), rgb_to_lab(table))
    return table[labels[inverse]].reshape(np.asarray(rgb).shape)


def merge_near_duplicates(palette: Palette, threshold: float = 5) -> Palette:
    if threshold < 0:
        raise ValueError("threshold must be nonnegative")
    lab = rgb_to_lab(palette_rgb(palette.colors))
    kept = []
    for i, color in enumerate(lab):
        if not kept or np.linalg.norm(lab[kept] - color, axis=1).min() > threshold:
            kept.append(i)
    return Palette(name=palette.name, colors=[palette.colors[i] for i in kept])


def extract_palette(
    image: Image.Image, count: int = 16, merge_threshold: float = 5, max_iterations: int = 30
) -> Palette:
    if not 1 <= count <= 256 or max_iterations < 1:
        raise ValueError("count must be 1..256 and iterations must be positive")
    pixels = np.asarray(image.convert("RGBA"))
    rgb = pixels[..., :3][pixels[..., 3] > 0]
    if not len(rgb):
        raise ValueError("Cannot extract a palette from an entirely transparent image")
    # Fixed evenly-spaced sample, no random seed or process-dependent initialization.
    if len(rgb) > 16384:
        rgb = rgb[np.linspace(0, len(rgb) - 1, 16384, dtype=int)]
    unique, weights = np.unique(rgb, axis=0, return_counts=True)
    points = rgb_to_lab(unique)
    count = min(count, len(points))
    centers = [points[weights.argmax()]]
    while len(centers) < count:
        distance = ((points[:, None] - np.array(centers)) ** 2).sum(-1).min(-1)
        centers.append(points[(distance * weights).argmax()])
    centers = np.array(centers)
    for _ in range(max_iterations):
        labels = nearest_indices(points, centers)
        updated = centers.copy()
        for i in range(count):
            mask = labels == i
            if mask.any():
                updated[i] = np.average(points[mask], axis=0, weights=weights[mask])
        if np.allclose(updated, centers, atol=1e-5):
            break
        centers = updated
    colors = ["#" + "".join(f"{v:02X}" for v in row) for row in lab_to_rgb(centers)]
    return merge_near_duplicates(Palette(name="extracted", colors=colors), merge_threshold)


def load_palette(path_or_name: str | Path) -> Palette:
    path = Path(path_or_name)
    if not path.is_file() and isinstance(path_or_name, str) and path.name == path_or_name:
        path = Path(__file__).with_name("palettes") / (path_or_name + ".json")
    return Palette.model_validate_json(path.read_text(encoding="utf-8"))


def save_palette(palette: Palette, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(palette.model_dump(), indent=2) + "\n", encoding="utf-8")


def palette_usage(image: Image.Image, palette: Palette) -> dict[str, int]:
    """Count visible pixels, including zero counts for unused palette entries."""
    rgba = np.asarray(image.convert("RGBA"))
    colors, counts = np.unique(rgba[..., :3][rgba[..., 3] > 0], axis=0, return_counts=True)
    used = {
        "#" + "".join(f"{v:02X}" for v in rgb): int(n)
        for rgb, n in zip(colors, counts, strict=True)
    }
    return {color: used.get(color, 0) for color in palette.colors}


def background_tinted_dark_colors(
    palette: Palette,
    background: str | None,
    dark_lightness: float = 35,
    hue_similarity: float = 0.75,
) -> list[str]:
    """Flag chromatic dark colors whose LAB hue points toward the key background.

    This is a warning, not an automatic edit: legitimate purple outlines may qualify.
    Neutral colors (chroma < 10) and neutral backgrounds are excluded.
    """
    if background is None:
        return []
    bg = rgb_to_lab(palette_rgb([background]))[0, 1:]
    if np.linalg.norm(bg) < 15:
        return []
    lab = rgb_to_lab(palette_rgb(palette.colors))
    chroma = np.linalg.norm(lab[:, 1:], axis=1)
    cosine = (lab[:, 1:] @ bg) / (np.maximum(chroma, 1e-9) * np.linalg.norm(bg))
    return [
        color
        for color, value, magnitude, similarity in zip(
            palette.colors, lab, chroma, cosine, strict=True
        )
        if value[0] <= dark_lightness and magnitude >= 10 and similarity >= hue_similarity
    ]
