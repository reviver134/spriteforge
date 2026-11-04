"""M10.1: every Muse Image request carries an art spec; results are read back and checked."""

import io

import numpy as np
import pytest
from PIL import Image

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import (
    ArtPick,
    MuseOptions,
    art_size,
    art_spec,
    keyword_plan,
    main_row,
    make_art,
    muse_frame,
    muse_prompt,
    muse_sheet,
    request_size,
    sheet_prompt,
    sheet_rows,
)
from spriteforge.game.design import Entity, fallback_design
from spriteforge.providers.base import ImageResult
from spriteforge.providers.mock import MockImageProvider

SIZES = [(1024, 1024), (1792, 1024), (1024, 1536)]


def ent(eid, role, look, w=16, h=16) -> Entity:
    return Entity(id=eid, role=role, look=look, behaviour="-", width=w, height=h, animations=[])


def muse_pick(eid, prompt) -> ArtPick:
    return ArtPick(
        entity=eid, source="muse", asset="", prompt=prompt, reference="", recolor="", reason=""
    )


def png(img: Image.Image, fmt="PNG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def test_spec_prompt_states_view_grid_scale_anchor_light():
    d = fallback_design("arena")
    d.look = "dark dungeon, accents #E0B030 and #3264D2"
    enemy = ent("skeleton", "enemy", "a skeleton warrior", 14, 12)
    spec = art_spec(enemy, d, (14, 12), 16, SIZES)
    prompt = muse_prompt(spec, muse_pick("skeleton", "a skeleton warrior"))
    for part in [
        "45°",
        "14×12 grid",
        "hero is 16 px",
        "feet (or base) at the bottom",
        "light from the top-left",
        "#E0B030",
        "#3264D2",
        "magenta",
    ]:
        assert part in prompt, part
    d.camera = "side-view"
    side = muse_prompt(art_spec(enemy, d, (14, 12), 16, SIZES), muse_pick("skeleton", "a bat"))
    assert "side view" in side and "top" not in side.lower()


def test_request_size_follows_aspect():
    d = fallback_design("arena")
    sky = art_spec(ent("sky", "background", "a sky"), d, (320, 180), 16, SIZES)
    assert sky.request_size == (1792, 1024)
    assert request_size((10, 24), SIZES) == (1024, 1536)
    assert request_size((16, 16), SIZES) == (1024, 1024)
    assert request_size((10, 24), [(1024, 1024)]) == (1024, 1024)  # only verified sizes
    for grid in [(8, 8), (128, 8), (8, 128), (320, 180)]:
        assert request_size(grid, load_config().image.sizes) in load_config().image.sizes


def blown_up(logical: Image.Image, cell=100, jitter=2, seed=1) -> bytes:
    """Muse-like rendering: big cells with uneven edges, a magenta field and JPEG noise."""
    rng = np.random.default_rng(seed)
    w, h = logical.size
    xs = [0] + [i * cell + int(rng.integers(-jitter, jitter + 1)) for i in range(1, w)] + [w * cell]
    ys = [0] + [j * cell + int(rng.integers(-jitter, jitter + 1)) for j in range(1, h)] + [h * cell]
    src = np.asarray(logical)
    big = np.zeros((h * cell, w * cell, 3), dtype=np.uint8)
    big[...] = (255, 0, 255)
    for j in range(h):
        for i in range(w):
            if src[j, i, 3]:
                big[ys[j] : ys[j + 1], xs[i] : xs[i + 1]] = src[j, i, :3]
    canvas = np.full((h * cell + 400, w * cell + 400, 3), (255, 0, 255), dtype=np.uint8)
    canvas[200:-200, 200:-200] = big
    noisy = canvas.astype(int) + rng.integers(-6, 7, canvas.shape)
    image = Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))
    return png(Image.open(io.BytesIO(png(image, "JPEG", quality=85))).convert("RGB"))


def sprite_14x12() -> Image.Image:
    rgba = np.zeros((12, 14, 4), dtype=np.uint8)
    colors = [(30, 30, 40), (200, 60, 60), (240, 220, 120), (60, 120, 220)]
    for y in range(12):
        for x in range(14):
            if (x - 6.5) ** 2 / 49 + (y - 5.5) ** 2 / 36 <= 1.05:
                rgba[y, x] = (*colors[(x // 3 + y // 2) % 4], 255)
    return Image.fromarray(rgba)


def test_native_grid_downscale_is_exact():
    logical = sprite_14x12()
    spec = art_spec(ent("blob", "enemy", "-", 14, 12), fallback_design("x"), (14, 12), 16, SIZES)
    frame, info = muse_frame(spec, blown_up(logical))
    assert info["mode"] == "native" and abs(info["cell"] - 100) < 2, info
    got, want = np.asarray(frame).astype(int), np.asarray(logical).astype(int)
    # Every cell lands on its own pixel; colours keep their count. JPEG shifts a colour by a
    # value or two, which no read-back can undo, so colours match within 3.
    assert np.array_equal(got[..., 3], want[..., 3])
    assert np.abs(got - want).max() <= 3
    count = lambda a: len(np.unique(a[a[..., 3] > 0][:, :3], axis=0))
    assert count(got) == count(want) == 4


def test_falls_back_to_fit_without_grid():
    y, x = np.mgrid[:600, :600]
    smooth = np.zeros((600, 600, 4), dtype=np.uint8)
    smooth[..., 0], smooth[..., 1], smooth[..., 3] = x * 255 // 600, y * 255 // 600, 255
    spec = art_spec(ent("blob", "pickup", "-", 12, 10), fallback_design("x"), (12, 10), 16, SIZES)
    frame, info = muse_frame(spec, png(Image.fromarray(smooth)))
    assert frame.size == (12, 10) and info["mode"] == "fit"
    tile = art_spec(ent("floor", "tile", "-"), fallback_design("x"), (16, 16), 16, SIZES)
    frame, info = muse_frame(tile, png(Image.fromarray(smooth)))
    assert frame.size == (16, 16) and info["mode"] == "fit"


class ScriptedImages(MockImageProvider):
    """Returns `images` in order (then the mock's own), recording every request."""

    def __init__(self, budget, images):
        super().__init__(budget)
        self.images, self.requests = list(images), []

    def generate(self, request):
        self.requests.append(request)
        if self.images:
            with self.budget.attempt("mock", "scripted-image", image=True):
                return ImageResult(data=self.images.pop(0))
        return super().generate(request)

    def __call__(self, request, edit):
        return self.generate(request).data


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


def hero_only():
    d = fallback_design("arena")
    d.entities = d.entities[:1]
    return d


def magenta() -> bytes:
    return png(Image.new("RGB", (64, 64), (255, 0, 255)))


def test_bad_frame_retried_once_with_high_reasoning(budget):
    d = hero_only()
    images = ScriptedImages(budget, [magenta()])
    art = make_art(d, keyword_plan(d), generate=images)
    first, second = images.requests
    assert first.reasoning_strength == "low" and "no subject" not in first.prompt
    assert second.reasoning_strength == "high" and "no subject" in second.prompt
    hero = art.filled["hero"]
    assert hero.source == "muse" and hero.record["request"]["calls"] == 2
    assert hero.record["request"]["size"] == [1024, 1024]
    assert hero.record["spec"]["grid"] == [16, 16]


def test_retry_respects_cap(budget):
    d = hero_only()
    images = ScriptedImages(budget, [magenta()])
    art = make_art(d, keyword_plan(d), generate=images, max_calls=1)
    assert len(images.requests) == 1
    assert any("no retry" in w and "cap (1)" in w for w in art.warnings), art.warnings
    assert art.filled["hero"].source == "placeholder"
    images = ScriptedImages(budget, [magenta()])
    art = make_art(d, keyword_plan(d), generate=images, options=MuseOptions(retries=0))
    assert len(images.requests) == 1 and art.filled["hero"].source == "placeholder"


def test_redraws_go_on_until_they_stop_improving(budget):
    d = hero_only()
    images = ScriptedImages(budget, [magenta()] * 8)  # the same problem every time
    art = make_art(d, keyword_plan(d), generate=images, options=MuseOptions(retries=None, patience=2))
    assert len(images.requests) == 3  # the first draw, then two redraws without improvement
    assert art.filled["hero"].source == "placeholder"


def test_empty_retry_keeps_first_frame_and_its_problems(budget):
    d = hero_only()
    wide = Image.new("RGB", (600, 300), (255, 0, 255))
    wide.paste((40, 90, 200), (50, 120, 550, 180))  # a flat bar: far too wide for 16x16
    images = ScriptedImages(budget, [png(wide), magenta()])
    single = MuseOptions(sheet_frames=1, adapt_size=False)  # the box is kept: aspect counts
    art = make_art(d, keyword_plan(d), generate=images, options=single)
    assert len(images.requests) == 2 and art.filled["hero"].source == "muse"
    assert any("accepted with problems: the subject is too wide" in w for w in art.warnings)


# --- sprite sheets: several small frames in one Muse image ----------------------------------


def test_sheet_prompt_lists_layout_and_poses():
    d = fallback_design("arena")
    e = Entity(id="orc", role="enemy", look="a green orc", behaviour="-", width=16, height=16,
               animations=["run", "attack"])  # fmt: skip
    rows = sheet_rows(e, 4)
    assert [name for name, _ in rows] == ["idle", "run", "attack"]
    assert rows[1][1][0] == "run: left foot forward"  # run uses the walk poses
    spec = art_spec(e, d, (16, 16), 16, SIZES)
    prompt = sheet_prompt(
        spec, muse_pick("orc", "a green orc"), 4, 3, [p for _, r in rows for p in r]
    )
    for part in ["4 columns by 3 rows", "12 frames", "16×16 grid", "attack: striking", "45°"]:
        assert part in prompt, part
    gem = sheet_rows(ent("gem", "pickup", "a gem"), 4)
    assert gem == [("idle", ["idle: still", "idle: a small glint", "idle: a bright glint",
                             "idle: a small glint"])]  # fmt: skip
    assert request_size((16 * 4, 16), SIZES + [(2048, 512)]) == (2048, 512)


def block_sprite(w, h, color) -> Image.Image:
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    shades = [color, tuple(c // 2 for c in color), (240, 240, 240)]
    for y in range(h):
        for x in range(w):
            rgba[y, x] = (*shades[(x + 2 * y) % 3], 255)
    rgba[0, :, :3] = rgba[-1, :, :3] = rgba[:, 0, :3] = rgba[:, -1, :3] = (20, 20, 30)
    return Image.fromarray(rgba)


def sheet_of(sprites, cols=None, cell=30, slot=1000) -> bytes:
    """A Muse-like sheet: each sprite blown up x`cell` in its own magenta slot, row by row."""
    cols = cols or len(sprites)
    rows = -(-len(sprites) // cols)
    sheet = Image.new("RGB", (slot * cols, slot * rows), (255, 0, 255))
    for i, s in enumerate(sprites):
        if s is None:
            continue
        big = s.resize((s.width * cell, s.height * cell), Image.Resampling.NEAREST)
        x, y = (i % cols) * slot, (i // cols) * slot
        sheet.paste(big, (x + (slot - big.width) // 2, y + (slot - big.height) // 2), big)
    return png(sheet)


def test_sheet_frames_share_one_scale():
    spec = art_spec(ent("orc", "enemy", "-"), fallback_design("x"), (16, 16), 16, SIZES)
    frames, infos = muse_sheet(
        spec, sheet_of([block_sprite(14, 14, (60, 160, 60)), block_sprite(14, 20, (60, 160, 60))]),
        2, 1,
    )  # fmt: skip
    assert [i["logical"] for i in infos] == [[14, 14], [14, 20]]
    heights = [f.getchannel("A").getbbox()[3] - f.getchannel("A").getbbox()[1] for f in frames]
    assert heights == [11, 16]  # both shrunk by 16/20, not each to fill its frame
    assert all(f.size == (16, 16) for f in frames)
    fits, _ = muse_sheet(spec, sheet_of([block_sprite(12, 12, (60, 160, 60))] * 2), 2, 1)
    assert np.array_equal(np.asarray(fits[0]), np.asarray(fits[1]))  # 1:1, no rescale


def test_sheet_sprite_animates_and_retries_empty_cells(budget):
    d = hero_only()
    d.entities[0].animations = ["idle", "walk"]
    knight = block_sprite(12, 14, (180, 180, 200))
    gappy = sheet_of([knight, knight, None, knight, knight, knight, knight, knight], 4, 30, 500)
    images = ScriptedImages(budget, [gappy])
    art = make_art(d, keyword_plan(d), generate=images, options=MuseOptions(sizes=SIZES))
    first, second = images.requests
    assert "4 columns by 2 rows" in first.prompt and "walk: right foot forward" in first.prompt
    assert first.size == (1792, 1024)
    assert (
        second.reasoning_strength == "high"
        and "frames 3 of the 4x2 sheet are empty" in second.prompt
    )
    hero = art.filled["hero"]
    assert hero.meta["anims"] == {"idle": [0, 4], "walk": [4, 4]} and hero.meta["frame"] == [16, 16]
    assert Image.open(io.BytesIO(hero.png)).size == (16 * 8, 16)


def test_frame_follows_the_art_shape(budget):
    assert art_size((12, 8), (45, 23)) == (14, 7)  # spread wings: wider, about the same area
    assert art_size((16, 16), (15, 16)) == (16, 16)  # within 15%: the design box stays
    assert art_size((12, 10), (17, 26)) == (9, 14)
    bat = Entity(id="bat", role="enemy", look="-", behaviour="-", width=12, height=8,
                 animations=["fly", "death"])  # fmt: skip
    assert main_row(sheet_rows(bat, 4)) == 1  # sized by its flying pose, not idle or death
    assert main_row(sheet_rows(ent("rock", "prop", "-"), 4)) == 0
    d = hero_only()
    d.entities[0].animations = ["idle", "walk"]
    bat = block_sprite(24, 12, (90, 60, 40))  # drawn 2:1 in a 16x16 box
    sheet = sheet_of([bat] * 8, 4, 30, 800)
    images = ScriptedImages(budget, [sheet])
    art = make_art(d, keyword_plan(d), generate=images, options=MuseOptions(sizes=SIZES))
    hero = art.filled["hero"]
    assert len(images.requests) == 1  # no aspect retry: the frame took the art's shape
    assert hero.meta["frame"] == [23, 11] and hero.record["frame_size"] == [23, 11]
    assert Image.open(io.BytesIO(hero.png)).size == (23 * 8, 11)
    assert any("follows the art" in r for r in hero.reasons)


def test_tiny_sprites_get_one_simple_row():
    gem = Entity(id="gem", role="pickup", look="a gem", behaviour="-", width=8, height=8,
                 animations=["spin", "collect"])  # fmt: skip
    rows = sheet_rows(gem, 4)
    assert [name for name, _ in rows] == ["idle"] and "sparkle" not in str(rows)
    spec = art_spec(gem, fallback_design("x"), (8, 8), 16, SIZES)
    prompt = sheet_prompt(spec, muse_pick("gem", "a gem"), 4, 1, rows[0][1])
    assert "tiny icons" in prompt and "no sparkles" in prompt
    big = art_spec(ent("orc", "enemy", "-"), fallback_design("x"), (16, 16), 16, SIZES)
    assert "tiny icons" not in sheet_prompt(big, muse_pick("orc", "an orc"), 4, 1, ["a", "b"])


def test_failed_call_is_sent_again_unchanged(budget):
    class FlakyOnce(ScriptedImages):
        def generate(self, request):
            if not self.requests:
                self.requests.append(request)
                raise RuntimeError("Meta HTTP 500: internal server error")
            return super().generate(request)

    d = hero_only()
    images = FlakyOnce(budget, [])
    art = make_art(d, keyword_plan(d), generate=images)
    first, second = images.requests
    assert second.prompt == first.prompt and second.reasoning_strength == "low"
    assert art.filled["hero"].source == "muse"
    assert any("Muse Image failed" in w for w in art.warnings)


# --- colours, coarse grids, seams (after the M10.3 live run) --------------------------------


def test_named_colours_win_over_the_reference_palette():
    from spriteforge.game.art import look_palette

    tan = Image.new("RGBA", (32, 32), (200, 160, 120, 255))
    tan.paste((120, 80, 50, 255), (0, 0, 16, 32))
    slime = ent("slime", "enemy", "a red slime with white eyes")
    spec = art_spec(slime, fallback_design("x"), (16, 16), 16, SIZES, tan)
    assert spec.palette_from == "look" and spec.palette[:2] == ["#C83232", "#F0F0F0"]
    prompt = muse_prompt(spec, muse_pick("slime", slime.look), has_ref=True)
    assert "do not use its colours" in prompt and "use only these colours" not in prompt
    # No colour named: the style example's palette keeps the set consistent.
    plain = art_spec(ent("blob", "enemy", "a blob"), fallback_design("x"), (16, 16), 16, SIZES, tan)
    assert plain.palette_from == "reference" and "#C8A078" in plain.palette
    assert "use only these colours" in muse_prompt(plain, muse_pick("blob", "a blob"), True)
    # "dark" darkens; a colour after "remove" in its clause is left out.
    fix = "Dark purple-grey tile. Fix: near-black base, remove tan stone pattern, keep size"
    assert look_palette(fix) == ["#3A1D56", "#808088", "#141418"]


def test_coarse_grid_on_a_background_is_fitted_not_read():
    rng = np.random.default_rng(3)
    blocks = Image.fromarray(rng.integers(0, 255, (10, 18, 3), dtype=np.uint8))
    raw = png(blocks.resize((1800, 1000), Image.Resampling.NEAREST))
    sky = art_spec(ent("sky", "background", "a sky"), fallback_design("x"), (320, 180), 16, SIZES)
    frame, info = muse_frame(sky, raw)
    assert frame.size == (320, 180) and info["mode"] == "fit" and "too coarse" in info["note"]
    # The same blocks as a 16x16 tile's grid are the tile's pixels.
    tile = art_spec(ent("floor", "tile", "-"), fallback_design("x"), (16, 16), 16, SIZES)
    _, info = muse_frame(tile, png(blocks.resize((1600, 1600), Image.Resampling.NEAREST)))
    assert info["mode"].startswith("native")


def test_tile_that_does_not_repeat_is_blended(budget):
    from spriteforge.game.art import ArtPlan, make_tileable, seams_match

    ramp = np.zeros((16, 16, 3), dtype=np.uint8)
    ramp[..., 0] = np.arange(16) * 15  # dark on the left, bright on the right
    ramp[..., 2] = 60
    tile = Image.fromarray(ramp)
    assert not seams_match(tile)
    fixed = make_tileable(tile)
    assert seams_match(fixed)
    assert np.array_equal(np.asarray(fixed)[6:10, 6:10, :3], ramp[6:10, 6:10])  # middle kept
    d = fallback_design("arena")
    d.entities = [e for e in d.entities if e.role in {"player", "tile"}]
    plan = ArtPlan(variant="normal", picks=[muse_pick("floor", "a red stone floor")])
    raw = png(tile.resize((1024, 1024), Image.Resampling.NEAREST))
    mock, asked = MockImageProvider(budget), []

    def generate(request, edit):  # the ramp for the tile; the hero gets the mock's art
        if "seamless square game tile" not in request.prompt:
            return mock.generate(request).data
        asked.append(request)
        return raw

    floor = make_art(d, plan, generate=generate).filled["floor"]
    assert floor.source == "muse" and len(asked) == 2  # retried once, then blended
    assert "edges blended by code: the drawn tile did not repeat" in floor.reasons
    assert floor.record["problems"] == []
    assert seams_match(Image.open(io.BytesIO(floor.png)))
