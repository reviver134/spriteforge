"""Live probe: several small frames in one Muse image (a sprite sheet / slide show).

Question: when Muse draws N frames in one image, is each frame drawn with fewer big pixels
(closer to the requested grid) than a single sprite filling the image? 4 image calls, about
$0.04. Saves raws, per-frame read-backs and a preview to workspace/probes/sheet/.
"""

import io
import json
from pathlib import Path

from PIL import Image

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget, BudgetExceeded
from spriteforge.core.credentials import load_credentials
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.game.art import ArtPick, art_spec, muse_frame, sheet_prompt
from spriteforge.game.design import Entity, fallback_design
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.http import ProviderError
from spriteforge.providers.muse_image import MuseImageProvider

HERO = "a small knight in silver armour with a red cape"
WALK = ["walk frame 1 (left foot forward)", "walk frame 2 (feet together)",
        "walk frame 3 (right foot forward)", "walk frame 4 (feet together)"]  # fmt: skip
DIRS = ["facing down (toward the camera)", "facing left", "facing right", "facing up (away)"]
CASES = [
    ("walk-2x2", "player", HERO, (16, 16), (1024, 1024), 2, 2, WALK),
    ("walk-4x1", "player", HERO, (16, 16), (2048, 512), 4, 1, WALK),
    (
        "dirs-4x4",
        "player",
        HERO,
        (16, 16),
        (1024, 1024),
        4,
        4,
        [f"{d}, walk frame {i + 1}" for d in DIRS for i in range(4)],
    ),
    ("gem-2x2", "pickup", "a small cyan gem", (8, 8), (1024, 1024), 2, 2,
     ["gem", "gem with a small sparkle", "gem with a big sparkle", "gem with a small sparkle"]),
]  # fmt: skip


def cells(image: Image.Image, cols: int, rows: int) -> list[Image.Image]:
    w, h = image.width / cols, image.height / rows
    return [
        image.crop((round(c * w), round(r * h), round((c + 1) * w), round((r + 1) * h)))
        for r in range(rows)
        for c in range(cols)
    ]


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    out = root / "probes" / "sheet"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root / "probe-sheet.sqlite3")
    cap = BudgetConfig(**{**cfg.budget.model_dump(), "max_image_calls": len(CASES)})
    run = store.create_run({"workflow": "probe-sheet", "budget": cap.model_dump()})
    image = MuseImageProvider(cfg.image, Budget(store, run.id, cap))
    design = fallback_design("dungeon arena")
    results = []
    try:
        for name, role, look, grid, size, cols, rows, frames in CASES:
            e = Entity(id=name, role=role, look=look, behaviour="-", width=grid[0],
                       height=grid[1], animations=[])  # fmt: skip
            spec = art_spec(e, design, grid, 16, [size])
            pick = ArtPick(entity=name, source="muse", asset="", prompt=look, reference="",
                           recolor="", reason="")  # fmt: skip
            prompt = sheet_prompt(spec, pick, cols, rows, frames)
            row = {"case": name, "request_size": list(size), "layout": [cols, rows]}
            try:
                raw = image.generate(ImageRequest(prompt=prompt, size=size)).data
            except ProviderError as exc:
                row["error"] = redact(str(exc))[:300]
                results.append(row)
                print(json.dumps(row), flush=True)
                continue
            (out / f"{name}-raw.png").write_bytes(raw)
            sheet = Image.open(io.BytesIO(raw)).convert("RGBA")
            row["returned_size"] = list(sheet.size)
            row["frames"] = []
            read = []
            for i, cell in enumerate(cells(sheet, cols, rows)):
                frame, info = muse_frame(spec, _png(cell), cfg.game.native_grid_confidence)
                read.append(frame)
                row["frames"].append(
                    {k: info.get(k) for k in ("mode", "cell", "confidence", "logical", "subject")}
                )
            preview = Image.new("RGBA", (cols * grid[0] * 8, rows * grid[1] * 8))
            for i, f in enumerate(read):
                big = f.resize((grid[0] * 8, grid[1] * 8), Image.Resampling.NEAREST)
                preview.paste(big, ((i % cols) * grid[0] * 8, (i // cols) * grid[1] * 8))
            preview.save(out / f"{name}-frames.png")
            results.append(row)
            print(json.dumps(row), flush=True)
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"Saved to {out}", flush=True)
        return 0
    except BudgetExceeded as exc:
        print(redact(str(exc)), flush=True)
        return 1
    finally:
        image.close()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
