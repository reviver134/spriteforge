"""A level's map preview, drawn by code: tiles and things (with the game's art when it is
given), enemy patrols, checkpoints, and the route the bot plays (code found it), with a column
ruler every 10 tiles. Shown at G4 and kept in the bundle (`/references/levels/<n>.png`)."""

import io

from PIL import Image, ImageDraw

from .design import GameDesign
from .levels import LevelRecord, parse

PX = 12  # pixels per tile
COLORS = {"solid": (96, 112, 128), "one_way": (160, 128, 96), "hazard": (208, 64, 64),
          "breakable": (150, 150, 160), "crate": (176, 120, 60), "pickup": (244, 211, 94),
          "enemy": (150, 70, 190), "checkpoint": (70, 140, 230), "exit": (90, 200, 120)}  # fmt: skip
SKY, ROUTE, RULER = (24, 28, 44), (255, 255, 255), (70, 76, 96)


def first_frame(png: bytes, frame: list | tuple | None) -> Image.Image:
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if frame:
        img = img.crop((0, 0, int(frame[0]), int(frame[1])))
    return img


def preview(record: LevelRecord, design: GameDesign, art: dict[str, Image.Image] | None = None) -> bytes:
    """PNG of the level. `art`: entity id -> its first frame."""
    art = art or {}
    grid = parse(record.spec, design)
    w, h = grid.cols * PX, grid.rows * PX
    img = Image.new("RGBA", (w, h + PX), SKY)
    draw = ImageDraw.Draw(img)
    tiles = {e.char: e for e in record.spec.legend}

    def put(entity: str, kind: str, c: int, r: int) -> None:
        box = (c * PX, r * PX, (c + 1) * PX - 1, (r + 1) * PX - 1)
        if entity in art:
            tile = art[entity].copy()
            tile.thumbnail((PX, PX), Image.NEAREST)
            img.alpha_composite(tile, (c * PX + (PX - tile.width) // 2, (r + 1) * PX - tile.height))
            if kind == "one_way":
                draw.line((box[0], box[1], box[2], box[1]), fill=COLORS[kind])
            return
        color = COLORS.get(kind, (200, 200, 200))
        if kind == "one_way":
            draw.rectangle((box[0], box[1], box[2], box[1] + PX // 4), fill=color)
        elif kind == "hazard":
            draw.polygon([(box[0], box[3]), ((box[0] + box[2]) // 2, box[1] + PX // 3), (box[2], box[3])], fill=color)
        elif kind in {"pickup"}:
            draw.ellipse((box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3), fill=color)
        else:
            draw.rectangle(box, fill=color)

    for r, row in enumerate(record.spec.rows):
        for c, ch in enumerate(row):
            e = tiles.get(ch)
            if e and e.kind in {"solid", "one_way", "hazard"}:
                put(e.entity, e.kind, c, r)
    for t in grid.things:
        if t.kind == "enemy" and t.patrol:
            a, b = t.patrol
            y = (t.row + 1) * PX - 2
            draw.line((a * PX + PX // 2, y, b * PX + PX // 2, y), fill=COLORS["enemy"], width=2)
        for c, r in t.cells:
            put(t.entity, t.kind, c, r)
    start = next((e.entity for e in record.spec.legend if e.kind == "start"), "")
    put(start, "start", *grid.start) if start in art else draw.rectangle(
        (grid.start[0] * PX + 2, grid.start[1] * PX + 2, (grid.start[0] + 1) * PX - 3,
         (grid.start[1] + 1) * PX - 3), outline=ROUTE)  # fmt: skip
    route_line(draw, record, grid.start)
    for c in range(0, grid.cols, 10):
        draw.line((c * PX, h, c * PX, h + PX - 1), fill=RULER)
        draw.text((c * PX + 2, h), str(c), fill=(170, 176, 196))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG")
    return out.getvalue()


def route_line(draw: ImageDraw.ImageDraw, record: LevelRecord, start: tuple[int, int]) -> None:
    """The bot's route: walks as lines on the floor, jumps as arcs through their peak."""
    x, feet = (start[0] + 0.5) * PX, (start[1] + 1) * PX
    for st in record.route:
        cell = st.get("cell")
        if st["do"] in {"jump", "fall"} and cell:
            x0 = st["x0"] * PX
            draw.line((x, feet - 2, x0, feet - 2), fill=ROUTE)
            x1, y1 = (cell[0] + 0.5) * PX, (cell[1] + 1) * PX
            peak = min(feet, y1) - (2.5 * PX if st["do"] == "jump" else 0)
            mid = (x0 + x1) / 2
            dash = (255, 200, 80) if st.get("dash", -1) >= 0 else ROUTE
            draw.line((x0, feet - 2, mid, peak, x1, y1 - 2), fill=dash, width=1)
            x, feet = x1, y1
        elif cell:
            nx, nf = st.get("x", cell[0] + 0.5) * PX, (cell[1] + 1) * PX
            draw.line((x, feet - 2, nx, nf - 2), fill=ROUTE)
            x, feet = nx, nf
