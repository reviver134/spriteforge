"""Live probe: does the sheet layout control how many pixels Muse draws per frame?

Each subject is drawn as N copies of one pose in layouts from 1x1 to 8x2; every copy is read
back and its pixel count compared with the requested grid. Also tells whether picking the
best copy of N is worth it. 3 subjects x 6 layouts = 18 image calls, about $0.18.
Saves raws, results.json and a preview per subject to workspace/probes/layouts/.
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
from spriteforge.game.art import ArtPick, art_spec, muse_sheet, sheet_prompt
from spriteforge.game.design import Entity, fallback_design
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.http import ProviderError
from spriteforge.providers.muse_image import MuseImageProvider

SUBJECTS = [
    ("runner", "enemy", "a lean grey wolf-like monster that runs fast, red eyes", (12, 10)),
    ("knight", "player", "a small knight in silver armour with a red cape", (16, 16)),
    ("gem", "pickup", "a small cyan gem", (8, 8)),
]
# cols, rows, request size (1536x512 is unverified: the probe tells)
LAYOUTS = [
    (1, 1, (1024, 1024)),
    (3, 2, (1792, 1024)),
    (4, 1, (2048, 512)),
    (6, 2, (1536, 512)),
    (4, 4, (1024, 1024)),
    (8, 2, (2048, 512)),
]


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    out = root / "probes" / "layouts"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root / "probe-layouts.sqlite3")
    cap = BudgetConfig(
        **{**cfg.budget.model_dump(), "max_image_calls": len(SUBJECTS) * len(LAYOUTS) + 2}
    )
    run = store.create_run({"workflow": "probe-layouts", "budget": cap.model_dump()})
    image = MuseImageProvider(cfg.image, Budget(store, run.id, cap))
    design = fallback_design("dungeon arena")
    results = []
    try:
        for name, role, look, grid in SUBJECTS:
            e = Entity(id=name, role=role, look=look, behaviour="-", width=grid[0],
                       height=grid[1], animations=[])  # fmt: skip
            pick = ArtPick(entity=name, source="muse", asset="", prompt=look, reference="",
                           recolor="", reason="")  # fmt: skip
            strips = []
            for cols, rows, size in LAYOUTS:
                spec = art_spec(e, design, grid, 16, [size])
                n = cols * rows
                poses = ["standing still, the same pose in every frame"] * n
                row = {"subject": name, "grid": list(grid), "layout": [cols, rows],
                       "request_size": list(size)}  # fmt: skip
                try:
                    raw = image.generate(
                        ImageRequest(prompt=sheet_prompt(spec, pick, cols, rows, poses), size=size)
                    ).data
                except ProviderError as exc:
                    row["error"] = redact(str(exc))[:300]
                    results.append(row)
                    print(json.dumps(row), flush=True)
                    continue
                (out / f"{name}-{cols}x{rows}-raw.png").write_bytes(raw)
                with Image.open(io.BytesIO(raw)) as img:
                    row["returned_size"] = list(img.size)
                frames, infos = muse_sheet(spec, raw, cols, rows, cfg.game.native_grid_confidence)
                cells = [i.get("logical") for i in infos]
                row["modes"] = [i["mode"] for i in infos]
                row["pixels_per_frame"] = cells
                sides = sorted(max(c) for c in cells if c)
                row["median_long_side"] = sides[len(sides) // 2] if sides else None
                row["best_long_side"] = sides[0] if sides else None
                row["target_long_side"] = max(grid)
                results.append(row)
                print(json.dumps({k: row[k] for k in row if k != "pixels_per_frame"}), flush=True)
                w, h = frames[0].size
                strip = Image.new("RGBA", (w * len(frames), h))
                for i, f in enumerate(frames):
                    strip.paste(f, (i * w, 0))
                strips.append(strip.resize((strip.width * 6, h * 6), Image.Resampling.NEAREST))
            if strips:
                width = max(s.width for s in strips)
                sheet = Image.new("RGBA", (width, sum(s.height + 6 for s in strips)), "#282830")
                y = 0
                for s in strips:
                    sheet.paste(s, (0, y), s)
                    y += s.height + 6
                sheet.save(out / f"{name}-preview.png")
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
