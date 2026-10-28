"""Scene processing preserves the whole composition, including HUD and enemies."""

import io

import numpy as np
from PIL import Image

from spriteforge.pixel.palette import extract_palette, quantize
from spriteforge.pixel.preview import upscale


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def preview(data, screen):
    source = Image.open(io.BytesIO(data)).convert("RGB")
    palette = extract_palette(source).colors
    # Full-frame scene: do not chroma-key or isolate a single connected subject.
    native = source.resize(screen, Image.Resampling.NEAREST)
    native = Image.fromarray(quantize(np.asarray(native), palette))
    return png(native), png(upscale(native, 3)), palette
