"""Live probe for M10.2: can one Muse image hold several different sprites? 1 image call
(about $0.01). Run from the repo root. Measurement only: not wired into the build.

Asks for 6 different small top-down sprites in a 3x2 arrangement on one flat magenta
background, keys the magenta, slices the image by connected components (no equal cells: a
copy that drifts or a different count does not break the slicing) and reads each slice back
like a single Muse sprite (`read_sprite`). Prints and saves, per slice: its box, area, cell
count, grid confidence and read-back mode, plus how many slices were found against the 6
asked. Saves the raw image, each slice and results.json to workspace/probes/atlas/.
Never prints credentials.
"""

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget, BudgetExceeded
from spriteforge.core.credentials import load_credentials
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.game.art import LIGHT, VIEWS, read_sprite
from spriteforge.pixel.schemas import ProcessingOptions, ProcessingReport
from spriteforge.pixel.source import components, key_background
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.http import ProviderError
from spriteforge.providers.muse_image import MuseImageProvider

SUBJECTS = [
    "a small knight in silver armour with a red cape",
    "a green round slime with two eyes",
    "a purple bat with spread wings",
    "a cyan diamond gem",
    "a red heart",
    "a white skeleton warrior with a rusty sword",
]
SIZE = (1792, 1024)  # 3 columns x 2 rows of square slots, about 16:9
MIN_SHARE = 0.05  # components smaller than this share of the largest are specks, not sprites


def prompt() -> str:
    listing = "; ".join(f"{i + 1}. {s}" for i, s in enumerate(SUBJECTS))
    return (
        "Pixel art sprite atlas for a 2D game: six DIFFERENT small game sprites arranged in 3 "
        "columns by 2 rows, each sprite alone in its own slot with wide empty gaps between "
        f"them, all on one flat pure magenta (#FF00FF) background. In reading order: {listing}. "
        "Each sprite is drawn on a 16×16 grid of big square pixels, one solid colour per cell, "
        "no anti-aliasing; all six at the same pixel size. "
        f"View: {VIEWS['top-down']}. Lighting: {LIGHT['top-down']}. Outline: 1 px dark "
        "outline. No text, no lettering, no frame, no drop shadow, no grid lines."
    )


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    out = root / "probes" / "atlas"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root / "probe-atlas.sqlite3")
    cap = BudgetConfig(**{**cfg.budget.model_dump(), "max_image_calls": 1})
    run = store.create_run({"workflow": "probe-atlas", "budget": cap.model_dump()})
    image = MuseImageProvider(cfg.image, Budget(store, run.id, cap))
    try:
        raw = image.generate(ImageRequest(prompt=prompt(), size=SIZE)).data
    except (ProviderError, BudgetExceeded) as exc:
        print(redact(str(exc))[:400], flush=True)
        return 1
    finally:
        image.close()
    (out / "atlas-raw.png").write_bytes(raw)
    with Image.open(io.BytesIO(raw)) as img:
        atlas = img.convert("RGBA")
    keyed = key_background(atlas, ProcessingOptions(), ProcessingReport())
    alpha = np.asarray(keyed)[..., 3] > 0
    _, groups = components(alpha)
    largest = groups[0][1] if groups else 0
    slices = [g for g in groups if g[1] >= MIN_SHARE * largest]
    slices.sort(key=lambda g: (round(g[2][1] / (atlas.height / 2)), g[2][0]))  # reading order
    rows = []
    for i, (_, area, (x0, y0, x1, y1)) in enumerate(slices, 1):
        crop = keyed.crop((x0, y0, x1, y1))
        frame, info = read_sprite(crop, cfg.game.native_grid_confidence)
        crop.save(out / f"slice-{i}.png")
        if frame is not None:
            frame.save(out / f"slice-{i}-frame.png")
        rows.append({"slice": i, "box": [x0, y0, x1, y1], "area": area, **info})
    result = {
        "returned_size": list(atlas.size),
        "asked": len(SUBJECTS),
        "slices": len(slices),
        "specks_dropped": len(groups) - len(slices),
        "per_slice": rows,
    }
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "per_slice"}), flush=True)
    for row in rows:
        print(json.dumps(row), flush=True)
    print(f"Saved to {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
