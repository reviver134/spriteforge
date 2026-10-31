"""Live probe for M10.1 art specs: 3 Muse Image calls (about $0.03). Run from the repo root.

1. a square 16x16 sprite with the grid wording, at 1024x1024;
2. a 320x180 background at 1792x1024;
3. a tall 10x24 sprite at 1024x1536.

Saves each raw image and prints the size Muse returned, whether the size was accepted and the
detected cell pitch and grid confidence, then how `muse_frame` read it back. Copy the
accepted sizes into `image.sizes` in config.yaml. Never prints credentials.
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
from spriteforge.game.art import ArtPick, art_spec, muse_frame, muse_prompt
from spriteforge.game.design import Entity, fallback_design
from spriteforge.pixel.grid import detect_cells
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.http import ProviderError
from spriteforge.providers.muse_image import MuseImageProvider

CASES = [
    ("knight", "player", "a small knight in silver armour with a red cape", (16, 16), (1024, 1024)),
    ("dungeon", "background", "a dark stone dungeon hall with torches", (320, 180), (1792, 1024)),
    ("tall-mage", "enemy", "a tall thin purple-robed mage with a staff", (10, 24), (1024, 1536)),
]


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    out = root / "probes" / "art-spec"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root / "probe-art-spec.sqlite3")
    cap = BudgetConfig(**{**cfg.budget.model_dump(), "max_image_calls": len(CASES)})
    run = store.create_run({"workflow": "probe-art-spec", "budget": cap.model_dump()})
    image = MuseImageProvider(cfg.image, Budget(store, run.id, cap))
    design = fallback_design("dungeon arena")
    results = []
    try:
        for name, role, look, grid, size in CASES:
            e = Entity(id=name, role=role, look=look, behaviour="-", width=grid[0],
                       height=grid[1], animations=[])  # fmt: skip
            spec = art_spec(e, design, grid, 16, [size]).model_copy(update={"request_size": size})
            pick = ArtPick(entity=name, source="muse", asset="", prompt=look, reference="",
                           recolor="", reason="")  # fmt: skip
            row = {"case": name, "request_size": list(size)}
            try:
                raw = image.generate(ImageRequest(prompt=muse_prompt(spec, pick), size=size)).data
            except ProviderError as exc:
                row["accepted"], row["error"] = False, redact(str(exc))[:300]
                results.append(row)
                print(json.dumps(row), flush=True)
                continue
            (out / f"{name}-raw.png").write_bytes(raw)
            with Image.open(io.BytesIO(raw)) as img:
                row["returned_size"] = list(img.size)
                grid_found = detect_cells(img)
            row["accepted"] = True
            row["whole_image_cell"] = round(grid_found.pitch, 1) if grid_found else None
            frame, info = muse_frame(spec, raw, cfg.game.native_grid_confidence)
            frame.save(out / f"{name}-frame.png")
            row["read_back"] = info
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


if __name__ == "__main__":
    raise SystemExit(main())
