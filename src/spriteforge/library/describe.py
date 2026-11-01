"""Descriptions of every library image, so the harness can choose art by what it shows.

`describe_library` gives every asset, and every named tile of an installed tile set, a measured
look (`looks.measure`: colours, brightness, detail; free). Images from packs that allow AI use
are also described by Muse Spark (vision): numbered contact sheets of up to 16 images, one
call each, answering what each image is, one sentence on what it shows and the camera it is
drawn for. Packs whose license forbids AI use never reach a model: their entry is the file
name and the measured look.

Entries are written to `<library>/descriptions/<pack>.json` (`{"assets": {id: entry},
"tiles": {tile id: entry}}`) with the image's sha256, so a changed image is described again
and an unchanged one is not sent twice. Model-written entries are `status: generated`; edit
the text, set `status: reviewed`, and later runs keep it. `rebuild_index` merges the entries
into index.json, so `search()` (the art step, the MCP tools, the catalog) returns them.
"""

import hashlib
import io
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import Field

from spriteforge.agents.models import Contract
from spriteforge.core.cost import BudgetExceeded
from spriteforge.providers.base import ChatRequest, LLMProvider, Message, chat_json

from .looks import Look, asset_image, measure
from .store import library_root, load_descriptions, load_packs, rebuild_index

PER_SHEET = 16
CELL = 128  # px per image on the contact sheet
LABEL = 18
SHEET_BACKGROUND = (128, 128, 132, 255)
View = Literal["top-down", "3/4 top-down", "side", "front", "flat", "unknown"]


class Seen(Contract):
    n: int = Field(description="the image's number on the sheet")
    what: str = Field(description="what it is, 1-4 words, e.g. 'hooded rogue', 'stone floor tile'")
    description: str = Field(
        description="one sentence, at most 30 words: the subject, its main colours by name and "
        "whether it is light or dark, and the details that matter when choosing game art"
    )
    view: View = Field(description="the camera the art is drawn for")


class SheetAnswer(Contract):
    images: list[Seen]


@dataclass
class Item:
    key: str  # asset id, or tile id (`<tileset>/<tile>`)
    section: Literal["assets", "tiles"]
    pack: str
    name: str
    category: str
    image: Image.Image
    sha256: str
    ai_allowed: bool
    hint: str  # size and frames, for the prompt


def library_items(root: Path, packs: list[str] | None = None) -> list[Item]:
    """Every asset (an animation sheet as its first frame) and every named tile of the
    installed tile sets, in a stable order."""
    from spriteforge.game.tilesets import load_tilesets, tile_image

    items = []
    installed = {p.id: p for p in load_packs(root) if not packs or p.id in packs}
    for pack in installed.values():
        for a in pack.assets:
            asset = a.model_dump(mode="json")
            frames = f", {a.frames} frames of {a.cell[0]}x{a.cell[1]}" if a.frames > 1 else ""
            items.append(Item(a.id, "assets", pack.id, a.name, a.category,
                              asset_image(asset, root), a.sha256, pack.ai_allowed,
                              f"{a.size[0]}x{a.size[1]}{frames}"))  # fmt: skip
    for ts in load_tilesets():
        if ts["pack"] not in installed:
            continue
        for name, spec in ts["tiles"].items():
            tile = tile_image(ts, name)
            buf = io.BytesIO()
            tile.save(buf, format="PNG")
            items.append(Item(f"{ts['id']}/{name}", "tiles", ts["pack"], spec["what"], "tile",
                              tile, hashlib.sha256(buf.getvalue()).hexdigest(),
                              installed[ts["pack"]].ai_allowed,
                              f"one {ts['tile_size']} px tile of a {ts['camera']} tile set"))  # fmt: skip
    return items


def measured_entry(item: Item, look: Look) -> dict:
    return {
        "sha256": item.sha256,
        "what": item.name,
        "text": f"{item.name} ({item.category}); {look.text}",
        "view": "unknown",
        "look": look.model_dump(),
        "by": "measured",
        "status": "measured",
    }


def contact_sheet(items: list[Item]) -> bytes:
    """Numbered cells of CELL px on a neutral grey; pixel art scaled by whole numbers."""
    cols = min(4, len(items))
    rows = math.ceil(len(items) / cols)
    step = CELL + LABEL
    sheet = Image.new("RGBA", (cols * CELL, rows * step), SHEET_BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=LABEL - 4)
    for n, item in enumerate(items):
        image = item.image
        if bbox := image.getchannel("A").getbbox():
            image = image.crop(bbox)
        side = max(image.size)
        if side > CELL - 8:
            k = math.ceil(side / (CELL - 8))
            image = image.resize(
                (max(1, image.width // k), max(1, image.height // k)), Image.Resampling.NEAREST
            )
        else:
            k = max(1, (CELL - 8) // side)
            image = image.resize((image.width * k, image.height * k), Image.Resampling.NEAREST)
        x, y = (n % cols) * CELL, (n // cols) * step
        draw.text((x + 4, y + 1), str(n + 1), fill=(255, 255, 255, 255), font=font)
        sheet.alpha_composite(
            image, (x + (CELL - image.width) // 2, y + LABEL + (CELL - image.height) // 2)
        )
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def sheet_prompt(items: list[Item]) -> str:
    listed = "\n".join(
        f'{n}. "{item.name}" ({item.category}, {item.hint})' for n, item in enumerate(items, 1)
    )
    return (
        f"The image is a contact sheet of {len(items)} numbered pixel-art images from a game "
        "art library, each enlarged with square pixels on a neutral grey (an animation sheet "
        "shows its first frame; a tile set shows its whole sheet). For every number, say what "
        "the image shows, judged from its pixels: `what` in 1-4 words, `description` in one "
        "sentence of at most 30 words (the subject, its main colours by name and whether it is "
        "light or dark, and the details that matter when choosing art for a game), and `view` "
        "(the camera it is drawn for). The names below come from file names: they are hints "
        "and may be wrong or cryptic; trust the pixels.\n\n" + listed
    )


def role_text() -> str:
    from spriteforge.agents.roles import load_roles

    r = load_roles()["Librarian"]
    return f"You are the {r.name} of a small game studio. Goal: {r.goal}\n{r.prompt}"


def describe_library(
    root: Path | None = None,
    provider: LLMProvider | None = None,
    *,
    model: str = "",
    reasoning: str | None = None,
    packs: list[str] | None = None,
    force: bool = False,
    per_sheet: int = PER_SHEET,
    log: Callable[[str], None] = print,
) -> dict:
    """Describe the library (see the module doc). Without a `provider` only measured looks
    are written. A failed sheet keeps measured entries for its images; running out of budget
    stops the model calls. Returns counts."""
    root = library_root(root)
    old = load_descriptions(root)
    items = library_items(root, packs)
    out: dict[str, dict] = {p: {"assets": {}, "tiles": {}} for p in {i.pack for i in items}}
    for pack, data in old.items():  # packs not described this time keep their file as is
        out.setdefault(pack, data)
    todo: list[Item] = []
    counts = {"model": 0, "measured": 0, "kept": 0, "failed": 0, "sheets": 0}
    for item in items:
        look = measure(item.image)
        prev = old.get(item.pack, {}).get(item.section, {}).get(item.key)
        same = prev is not None and prev.get("sha256") == item.sha256
        if same and (prev["status"] == "reviewed" or (prev["by"] != "measured" and not force)):
            out[item.pack][item.section][item.key] = {**prev, "look": look.model_dump()}
            counts["kept"] += 1
            continue
        out[item.pack][item.section][item.key] = measured_entry(item, look)
        if item.ai_allowed and provider is not None:
            todo.append(item)
        else:
            counts["measured"] += 1
    batches = [todo[i : i + per_sheet] for i in range(0, len(todo), per_sheet)]
    for number, batch in enumerate(batches, 1):
        request = ChatRequest(
            messages=[
                Message(role="system", content=role_text()),
                Message(role="user", content=sheet_prompt(batch), images=[contact_sheet(batch)]),
            ],
            reasoning_effort=reasoning,
            cache_key="spriteforge-describe",
        )
        try:
            counts["sheets"] += 1
            answer = chat_json(provider, request, SheetAnswer)
        except BudgetExceeded as exc:
            log(f"  sheet {number}/{len(batches)}: {exc}; the rest keep measured entries")
            counts["failed"] += sum(len(b) for b in batches[number - 1 :])
            break
        except Exception as exc:  # noqa: BLE001 -- one bad sheet must not lose the others
            log(f"  sheet {number}/{len(batches)} failed ({type(exc).__name__}: {exc})"[:300])
            counts["failed"] += len(batch)
            continue
        seen = {s.n: s for s in answer.images if 1 <= s.n <= len(batch)}
        for n, item in enumerate(batch, 1):
            if n not in seen:
                counts["failed"] += 1
                continue
            s = seen[n]
            entry = out[item.pack][item.section][item.key]
            entry.update(what=s.what.strip(), text=s.description.strip(), view=s.view,
                         by=model or "model", status="generated")  # fmt: skip
            counts["model"] += 1
        log(f"  sheet {number}/{len(batches)}: {len(seen)} of {len(batch)} described")
    folder = root / "descriptions"
    folder.mkdir(parents=True, exist_ok=True)
    for pack, data in sorted(out.items()):
        if not data.get("assets") and not data.get("tiles"):
            continue
        data = {
            "pack": pack,
            **{k: dict(sorted(v.items())) for k, v in data.items() if k != "pack"},
        }
        (folder / f"{pack}.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    rebuild_index(root)
    return counts
