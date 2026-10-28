from collections.abc import Mapping

import numpy as np
from PIL import Image

from .palette import palette_rgb
from .schemas import AssetSpec, Failure, Style


def seam_scores(img: Image.Image) -> tuple[float, float]:
    """Normalized premultiplied RGBA differences (0..1).

    Edge score measures opposite edges. Wrap score moves the original seam to
    the middle with a half-offset roll and measures the join there. Both use
    the maximum channel difference along the seam, so isolated defects count.
    """
    rgba = np.asarray(img.convert("RGBA"), dtype=float) / 255
    rgba[..., :3] *= rgba[..., 3:4]
    height, width = rgba.shape[:2]
    edge = max(np.abs(rgba[:, 0] - rgba[:, -1]).max(), np.abs(rgba[0] - rgba[-1]).max())
    wrapped = np.roll(rgba, (height // 2, width // 2), axis=(0, 1))
    scores = [0.0]
    if width > 1:
        middle = width // 2
        scores.append(np.abs(wrapped[:, middle - 1] - wrapped[:, middle]).max())
    if height > 1:
        middle = height // 2
        scores.append(np.abs(wrapped[middle - 1] - wrapped[middle]).max())
    return float(edge), float(max(scores))


def validate(img: Image.Image, spec: AssetSpec, style: Style) -> list[Failure]:
    failures = []

    def fail(code, message, fix_hint):
        failures.append(Failure(code=code, message=message, fix_hint=fix_hint))

    if img.size != spec.size:
        fail(
            "dimensions",
            f"Expected {spec.size}, got {img.size}.",
            "Fit and pad to the target canvas.",
        )
    rgba = np.asarray(img.convert("RGBA"))
    alpha = rgba[..., 3]
    visible = alpha > 0
    colors = set(map(tuple, rgba[..., :3][visible]))
    allowed = set(map(tuple, palette_rgb(style.palette)))
    if colors - allowed:
        fail(
            "palette",
            f"{len(colors - allowed)} off-palette colors.",
            "Quantize visible RGB to the style palette without dithering.",
        )
    if np.any((alpha != 0) & (alpha != 255)):
        fail("alpha", "Alpha contains intermediate values.", "Snap alpha to 0 or 255.")
    limit = spec.max_colors or style.max_colors
    if len(colors) > limit:
        fail(
            "max_colors",
            f"Uses {len(colors)} colors; limit is {limit}.",
            "Use a smaller approved palette or simplify the sprite shading.",
        )
    if not spec.edges_allowed and (
        visible[0].any() or visible[-1].any() or visible[:, 0].any() or visible[:, -1].any()
    ):
        fail(
            "clipped",
            "Silhouette touches the canvas edge.",
            "Add transparent padding and leave room for the outline.",
        )
    fill = float(visible.mean())
    if fill < spec.min_fill_ratio:
        fail(
            "fill_ratio",
            f"Fill ratio {fill:.3f} is below {spec.min_fill_ratio:.3f}.",
            "Enlarge the subject or remove excess transparent space.",
        )
    if spec.type == "tile":
        edge, wrap = seam_scores(img)
        if edge > spec.seam_threshold:
            fail(
                "tile_edges",
                f"Opposite-edge difference {edge:.3f} exceeds threshold.",
                "Make opposite tile edges match in RGB and alpha.",
            )
        if wrap > spec.seam_threshold:
            fail(
                "tile_wrap",
                f"Half-offset join difference {wrap:.3f} exceeds threshold.",
                "Repair the central horizontal and vertical joins in the half-offset tile.",
            )
    return failures


def validate_animation(
    frames: Mapping[str, Image.Image], spec: AssetSpec, style: Style
) -> list[Failure]:
    """Check every frame, consistent canvases and a common approved palette.

    Frames may use different subsets of the style palette. An animation need not
    show every color in every pose; a new unapproved color is palette drift.
    """
    if not frames:
        return [
            Failure(
                code="animation_empty",
                message="Animation has no frames.",
                fix_hint="Supply at least one named frame.",
            )
        ]
    failures = []
    for name, frame in frames.items():
        failures.extend(
            f.model_copy(update={"message": f"{name}: {f.message}"})
            for f in validate(frame, spec, style)
        )
    if len({frame.size for frame in frames.values()}) > 1:
        failures.append(
            Failure(
                code="animation_size",
                message="Frame canvases differ.",
                fix_hint="Fit every frame to the same spec.size canvas.",
            )
        )
    allowed = set(map(tuple, palette_rgb(style.palette)))
    for frame in frames.values():
        rgba = np.asarray(frame.convert("RGBA"))
        if set(map(tuple, rgba[..., :3][rgba[..., 3] > 0])) - allowed:
            failures.append(
                Failure(
                    code="animation_palette",
                    message="Frame palette drift detected.",
                    fix_hint="Quantize every frame to the same style palette.",
                )
            )
            break
    return failures
