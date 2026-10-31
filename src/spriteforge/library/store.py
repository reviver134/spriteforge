"""Ingest downloaded packs into `assets/library/` and query them.

Layout (one folder per pack, files renamed to stable slugs):

    assets/library/
      index.json                      # flat catalog of every asset, rebuilt on each change
      <pack>/pack.json                # source, license, per-asset metadata
      <pack>/preview.png              # contact sheet for humans and for the Art Director
      <pack>/<category>/<slug>.png
"""

import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path

from PIL import Image

from .models import AssetEntry, Pack

IMAGE_EXT = {".png", ".gif", ".bmp", ".webp"}
DEFAULT_ROOT = Path(__file__).parents[3] / "assets" / "library"


def library_root(root: Path | None = None) -> Path:
    return Path(root or os.environ.get("SPRITEFORGE_LIBRARY") or DEFAULT_ROOT)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "asset"


def name_from_file(stem: str, subcategory: str) -> tuple[str, str]:
    """'armour04chainmail' -> ('chainmail', 'chainmail'); keeps any trailing words readable."""
    s = stem.lower()
    if subcategory:
        # Strip "armour04" / "tileset " prefixes, but not the "prop" in "props".
        s = re.sub(rf"^{re.escape(subcategory.lower())}(?:[\s_-]*\d+|[\s_-]+)[\s_-]*", "", s)
    s = re.sub(r"^\d+[\s_-]*", "", s)
    slug = slugify(s) or slugify(stem)
    return slug, slug.replace("-", " ")


STOPWORDS = {"animations", "base", "sheet", "export", "entities", "environment", "png"}


def name_from_path(rel: str, depth: int) -> str:
    """Name a file from its last `depth` path parts: 'Orc Crew/Orc - Warrior/Run/Run-Sheet.png'
    -> 'orc crew warrior run'. Packs that name every file 'Run-Sheet.png' need this."""
    parts = Path(rel.replace("\\", "/").replace("'", "")).with_suffix("").parts[-depth:]
    words, seen = [], set()
    for word in slugify(" ".join(parts)).split("-"):
        if word and word not in STOPWORDS and word not in seen:
            seen.add(word)
            words.append(word)
    return " ".join(words)


def _iter_images(source: Path):
    """Yield (relative name, bytes) for images in a zip or a folder; skip OS junk files."""
    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            for info in sorted(archive.infolist(), key=lambda i: i.filename):
                path = Path(info.filename)
                if info.is_dir() or "__MACOSX" in path.parts or path.name.startswith("."):
                    continue
                if path.suffix.lower() in IMAGE_EXT:
                    yield info.filename, archive.read(info)
    elif source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.suffix.lower() in IMAGE_EXT and not path.name.startswith("."):
                # POSIX separators, as in zips, so `include`/`categorize` match on Windows too.
                yield path.relative_to(source).as_posix(), path.read_bytes()
    elif source.suffix.lower() in IMAGE_EXT:
        yield source.name, source.read_bytes()
    else:
        raise ValueError(f"Expected a .zip, folder or image: {source}")


def describe(data: bytes, cell: tuple[int, int] | str | None) -> dict:
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = image.size
    if cell == "auto":  # one row of square frames, e.g. 384x64 -> six 64x64 frames
        cell = (h, h) if w % h == 0 else (w, h)
    cell = cell or (w, h)
    if w % cell[0] or h % cell[1]:
        cell = (w, h)  # not a clean sheet for this grid; treat as a single sprite
    alpha = image.getchannel("A")
    return {
        "size": (w, h),
        "cell": cell,
        "frames": (w // cell[0]) * (h // cell[1]),
        "colors": len(image.getcolors(1 << 20) or []),
        "opaque_bbox": alpha.point(lambda a: 255 if a else 0).getbbox(),
        "transparent_background": alpha.getextrema()[0] == 0,
        "image": image,
    }


def contact_sheet(images: list[Image.Image], thumb: int = 96, pad: int = 6) -> Image.Image:
    """Grid of thumbnails, each fitted inside thumb x thumb with integer nearest scaling."""
    cells = []
    for img in images:
        factor = max(img.width, img.height) / thumb
        if factor > 1:  # shrink by a whole number so pixels stay square-ish
            k = int(-(-factor // 1))
            img = img.resize(
                (max(1, img.width // k), max(1, img.height // k)), Image.Resampling.NEAREST
            )
        else:
            k = max(1, int(thumb // max(img.width, img.height)))
            img = img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST)
        cells.append(img)
    cols = min(10, len(cells))
    rows = -(-len(cells) // cols)
    step = thumb + pad
    sheet = Image.new("RGBA", (cols * step + pad, rows * step + pad), (38, 40, 58, 255))
    for n, img in enumerate(cells):
        x = pad + (n % cols) * step + (thumb - img.width) // 2
        y = pad + (n // cols) * step + (thumb - img.height) // 2
        sheet.paste(img, (x, y), img)
    return sheet


def add_pack(
    source: Path,
    pack_id: str,
    *,
    title: str = "",
    category: str = "other",
    subcategory: str = "",
    tags: list[str] | None = None,
    cell: tuple[int, int] | str | None = None,
    author: str = "unknown",
    source_url: str = "",
    license: str = "UNKNOWN",
    license_url: str = "",
    license_verified: bool = False,
    notes: str = "",
    include: str = "",
    strip_prefix: str = "",
    categorize: dict[str, str] | None = None,
    redistributable: bool = True,
    ai_allowed: bool = True,
    faces: str = "right",
    name_depth: int = 0,
    root: Path | None = None,
) -> Pack:
    """Copy every image from `source` into the library and (re)write pack.json + index.json.

    `include` keeps only paths containing that text (e.g. "sprites/"); `categorize` maps a
    filename substring to a category for packs that mix tiles, characters and props; license,
    readme and manifest files are kept under `<pack>/meta/` as the pack's own evidence.
    """
    root = library_root(root)
    folder = root / pack_id
    entries, previews, seen = [], [], set()
    _copy_meta(Path(source), folder / "meta")
    images = list(_iter_images(Path(source)))
    # Drop a single shared top folder (e.g. "My Pack v1.2/") from names and filters.
    tops = {Path(rel).parts[0] for rel, _ in images if len(Path(rel).parts) > 1}
    if len(tops) == 1 and all(len(Path(rel).parts) > 1 for rel, _ in images):
        images = [(str(Path(*Path(rel).parts[1:])), data) for rel, data in images]
    for rel, data in images:
        if include and include.lower() not in rel.replace("\\", "/").lower():
            continue
        stem = Path(rel).stem
        if strip_prefix and stem.lower().startswith(strip_prefix.lower()):
            stem = stem[len(strip_prefix) :]
        if "extra/" in rel.replace("\\", "/").lower():
            stem += " extra"
        file_category = category
        for needle, mapped in (categorize or {}).items():
            if needle.lower() in rel.lower():
                file_category = mapped
                break
        if name_depth:
            name = name_from_path(rel, name_depth)
            slug = slugify(name)
        else:
            slug, name = name_from_file(stem, subcategory or file_category)
        base, n = slug, 2
        while slug in seen:
            slug, n = f"{base}-{n}", n + 1
        seen.add(slug)
        info = describe(data, cell)
        target = folder / file_category / f"{slug}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        info["image"].save(target)  # normalised to RGBA PNG
        image = info.pop("image")
        # Animation strips preview as their first frame; a whole strip shrinks to specks.
        previews.append(image.crop((0, 0, *info["cell"])) if info["frames"] > 1 else image)
        entries.append(
            AssetEntry(
                id=f"{pack_id}/{slug}",
                file=target.relative_to(root).as_posix(),
                category=file_category,
                subcategory=subcategory,
                name=name,
                tags=sorted({*(tags or []), *(t for t in [subcategory] if t), *name.split()}),
                sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                **info,
            )
        )
    if not entries:
        raise ValueError(f"No images found in {source}")
    pack = Pack(
        id=pack_id,
        title=title or pack_id.replace("-", " ").title(),
        author=author,
        source_url=source_url,
        license=license,
        license_url=license_url,
        license_verified=license_verified,
        notes=notes,
        original_archive=Path(source).name,
        redistributable=redistributable,
        ai_allowed=ai_allowed,
        faces=faces,
        assets=entries,
    )
    contact_sheet(previews).save(folder / "preview.png")
    write_pack(pack, root)
    return pack


META_NAMES = (
    "license",
    "licence",
    "terms",
    "readme",
    "links",
    "credits",
    "manifest",
    "assetindex",
    "changelog",
)


def _copy_meta(source: Path, target: Path) -> None:
    """Keep the pack's own license/readme/manifest text next to the art."""
    wanted = lambda name: (
        any(k in re.sub(r"[\s_-]", "", Path(name).name.lower()) for k in META_NAMES)
        and Path(name).suffix.lower() in {".txt", ".md", ".json", ".csv", ".url", ""}
    )
    files = []
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            files = [(i.filename, archive.read(i)) for i in archive.infolist()
                     if not i.is_dir() and "__MACOSX" not in i.filename and wanted(i.filename)]  # fmt: skip
    elif source.is_dir():
        files = [(str(p), p.read_bytes()) for p in source.rglob("*") if p.is_file() and wanted(p)]
    for name, data in files:
        target.mkdir(parents=True, exist_ok=True)
        (target / Path(name).name).write_bytes(data)


def write_gitignore(root: Path) -> None:
    """Packs whose license forbids redistribution stay on this machine, out of git."""
    private = [p.id for p in load_packs(root) if not p.redistributable]
    lines = ["# Generated by spriteforge library: packs that may not be redistributed.",
             *[f"/{pack_id}/" for pack_id in private]]  # fmt: skip
    (root / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pack(pack: Pack, root: Path | None = None) -> None:
    root = library_root(root)
    data = pack.model_dump(mode="json")
    data["usage"] = pack.usage
    (root / pack.id / "pack.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    rebuild_index(root)
    write_gitignore(root)


def load_packs(root: Path | None = None) -> list[Pack]:
    root = library_root(root)
    packs = []
    for path in sorted(root.glob("*/pack.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("usage", None)
        packs.append(Pack.model_validate(data))
    return packs


def rebuild_index(root: Path | None = None) -> dict:
    root = library_root(root)
    index = {
        "packs": {
            p.id: {
                "title": p.title,
                "license": p.license,
                "usage": p.usage,
                "ai_allowed": p.ai_allowed,
                "faces": p.faces,
                "assets": len(p.assets),
            }
            for p in load_packs(root)
        },
        "assets": [
            {
                **a.model_dump(mode="json"),
                "pack": p.id,
                "usage": p.usage,
                "ai_allowed": p.ai_allowed,
            }
            for p in load_packs(root)
            for a in p.assets
        ],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    return index


def set_license(
    pack_id: str,
    license: str,
    *,
    source_url: str = "",
    license_url: str = "",
    author: str = "",
    root: Path | None = None,
) -> Pack:
    """Record a license a human checked on the pack's download page."""
    pack = next((p for p in load_packs(root) if p.id == pack_id), None)
    if pack is None:
        raise KeyError(f"No pack {pack_id!r}")
    update = {"license": license, "license_verified": True}
    update |= {k: v for k, v in (("source_url", source_url), ("license_url", license_url),
                                 ("author", author)) if v}  # fmt: skip
    pack = pack.model_copy(update=update)
    write_pack(pack, root)
    return pack


def search(
    *,
    category: str = "",
    tag: str = "",
    size: tuple[int, int] | None = None,
    usage: str = "",
    ai: bool = False,
    root: Path | None = None,
) -> list[dict]:
    """Filter the catalog. `usage="ship"` returns only art a build may include as-is."""
    index_path = library_root(root) / "index.json"
    if not index_path.exists():
        return []
    assets = json.loads(index_path.read_text(encoding="utf-8"))["assets"]
    order = {"ship": 0, "ship-with-credit": 1, "reference-only": 2}
    out = []
    for asset in assets:
        if category and asset["category"] != category:
            continue
        if tag and tag.lower() not in asset["tags"]:
            continue
        if size and tuple(asset["cell"]) != tuple(size):
            continue
        if usage and order[asset["usage"]] > order[usage]:
            continue
        if ai and not asset.get("ai_allowed", True):  # e.g. picking Muse references
            continue
        out.append(asset)
    return out


def asset_path(asset_id: str, root: Path | None = None) -> Path:
    root = library_root(root)
    for asset in search(root=root):
        if asset["id"] == asset_id:
            return root / asset["file"]
    raise KeyError(asset_id)
