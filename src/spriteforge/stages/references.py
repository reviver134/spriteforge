"""Reference images the human gives the agents (docs/ui-plan.md, phase 4).

A reference belongs to one agent, or to every agent when it is added in the Director's chat. It
is stored as a locked artifact (`references/<n>-<name>.png`), only after the human confirms they
have the right to use it, and only when it is a real image within the size cap: code decodes it,
drops its metadata and scales it to at most `MAX_SIDE` pixels (fewer image tokens for Spark).

How the agents use it:
* Spark (vision): every Spark call of the agent carries its references (`Models.references`).
* Muse Image: the art agents' (Pixel Artist, Art Director) references steer the style of new and
  redrawn art (`MuseOptions.style`). Accepted art stays locked: a reference changes a picture
  only when a change asks for that picture.
"""

import io
import re
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from spriteforge.core.state import Artifact, Store

MAX_BYTES = 8 * 1024 * 1024  # upload cap
MAX_PIXELS = 40_000_000  # a decompression bomb is refused before it is decoded
MAX_SIDE = 1024  # stored size: the longest side
PER_CALL = 3  # references sent with one call (the newest)
EVERYONE = "Director"  # a reference added in the Director's chat is for every agent
ART = ("Pixel Artist", "Art Director")  # their references steer Muse Image's style
MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class ReferenceError(ValueError):
    pass


def normalize(data: bytes) -> bytes:
    """The upload as a clean PNG (no metadata), at most MAX_SIDE px; ReferenceError if it is
    not an image or is too big."""
    if len(data) > MAX_BYTES:
        raise ReferenceError(f"The image is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_PIXELS:
                raise ReferenceError("The image has too many pixels")
            image = probe.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ReferenceError("That file is not an image (PNG, JPEG, WebP or GIF)") from exc
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def slug(filename: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", filename.rsplit(".", 1)[0].lower()).strip("-")
    return stem[:40] or "reference"


def add_reference(store: Store, run_id: str, agent: str, filename: str, data: bytes,
                  rights: bool) -> dict:  # fmt: skip
    """Store an upload as a locked artifact for `agent` (the Director: every agent)."""
    if not rights:
        raise ReferenceError("Confirm that you have the right to use this image")
    png = normalize(data)
    n = len(references(store, run_id)) + 1
    artifact = Artifact(id=uuid4().hex, run_id=run_id, name=f"references/{n}-{slug(filename)}.png",
                        mime="image/png", data=png)  # fmt: skip
    store.save_artifact(artifact, "Human", "artifact.created")
    ref = {"id": artifact.id, "name": artifact.name, "file": filename[:120], "agent": agent,
           "everyone": agent == EVERYONE}  # fmt: skip
    store.append_event(run_id, "Human", "reference.added", ref)
    return ref


def references(store: Store, run_id: str) -> list[dict]:
    return [e.payload for e in store.events(run_id) if e.type == "reference.added"]


def for_agent(refs: list[dict], agent: str) -> list[dict]:
    """The references an agent uses: its own and every agent's, newest first."""
    return [r for r in reversed(refs) if r["agent"] in (agent, EVERYONE)]


def images(store: Store, run_id: str, agents) -> dict[str, list[bytes]]:
    """agent -> the PNGs of its newest references (at most PER_CALL)."""
    refs = references(store, run_id)
    if not refs:
        return {}
    with store.connect() as db:
        data = {r[0]: r[1] for r in db.execute(
            "SELECT id,data FROM artifacts WHERE run_id=? AND name GLOB 'references/*'", (run_id,))}  # fmt: skip
    out = {}
    for agent in agents:
        mine = [data[r["id"]] for r in for_agent(refs, agent) if r["id"] in data][:PER_CALL]
        if mine:
            out[agent] = mine
    return out


def style(refs: dict[str, list[bytes]]) -> list[bytes]:
    """The art agents' references for Muse Image (no duplicates, at most PER_CALL)."""
    out = []
    for agent in ART:
        out += [r for r in refs.get(agent, []) if r not in out]
    return out[:PER_CALL]
