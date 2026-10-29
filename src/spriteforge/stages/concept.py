"""Mood-board crops preserve proportions; these images are never gameplay previews."""

import io
import logging

import numpy as np
from PIL import Image

from spriteforge.pixel.palette import extract_palette, quantize
from spriteforge.pixel.preview import upscale


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def center_crop(image, screen=(16, 9)):
    # Exact rational ratio, including providers returning unexpected dimensions.
    from math import gcd

    divisor = gcd(*screen)
    ratio = (screen[0] // divisor, screen[1] // divisor)
    unit = min(image.width // ratio[0], image.height // ratio[1])
    if unit < 1:
        raise ValueError("Mood board is too small to crop")
    width, height = unit * ratio[0], unit * ratio[1]
    left, top = (image.width - width) // 2, (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


def preview(data, screen, *, legacy=False):
    if not legacy:
        raise ValueError("Deprecated PIL concept preview requires explicit --legacy")
    logging.getLogger(__name__).warning("DEPRECATED: rendering legacy PIL concept preview")
    source = center_crop(Image.open(io.BytesIO(data)).convert("RGB"), screen)
    palette = extract_palette(source).colors
    # Full-frame scene: do not chroma-key or isolate a single connected subject.
    native = source.resize(screen, Image.Resampling.NEAREST)
    native = Image.fromarray(quantize(np.asarray(native), palette))
    return png(native), png(upscale(native, 3)), palette
