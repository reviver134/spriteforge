"""The level engine draws the design's background behind each level (live 2026-11-03: three Muse
backdrops were drawn for the run-and-gun game but no game code drew them: an empty sky)."""

from playwright.sync_api import sync_playwright
from test_levels import GATED, built, check, slime_design, slime_plan

from spriteforge.game.design import Entity
from spriteforge.game.record import READY, serve, wait_until

GAME_WITH_OWN_BACKGROUND = """
class Level extends Phaser.Scene {
  constructor() { super('level'); }
  create() {
    this.L = sf.level.build(this, {touch: (t, how) => { sf.level.open(t); }});
    this.add.image(0, 0, 'background_cave').setOrigin(0).setScrollFactor(0).setDepth(-50);
  }
  update() {}
}
sf.start({scenes: [Level]});
"""


def backdrop_of(build, query="autoplay=1&capture=1&god=1&level=1&only=1"):
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 320, "height": 180})
        page.goto(f"{url}/index.html?{query}")
        assert wait_until(page, READY)
        page.evaluate("window.sfCapture(10, 30)")
        out = page.evaluate("(() => { const L = sf.level.current; return {id: L.backdropId, "
                            "drawn: !!(L.backdrop && L.backdrop.active), errors: sf.errors}; })()")
        browser.close()
    return out


def design_with_backgrounds():
    design = slime_design()
    backs = [Entity(id=f"background_{w}", role="background", look=f"a {w} scene", behaviour="backdrop",
                    width=320, height=180, animations=[]) for w in ("meadow", "cave")]  # fmt: skip
    return design.model_copy(update={"entities": [*design.entities, *backs]})


def test_each_level_gets_the_background_named_like_it(tmp_path):
    design, plan = design_with_backgrounds(), slime_plan()
    rec = check(GATED, n=1, events=["level_complete"])
    rec = rec.model_copy(update={"spec": rec.spec.model_copy(update={"name": "Crystal Cave"})})
    build = built(tmp_path, design, plan, [rec])
    seen = backdrop_of(build)
    assert seen == {"id": "background_cave", "drawn": True, "errors": []}


def test_a_game_that_draws_its_own_background_keeps_it(tmp_path):
    design, plan = design_with_backgrounds(), slime_plan()
    build = built(tmp_path, design, plan, [check(GATED, n=1, events=["level_complete"])],
                  code=GAME_WITH_OWN_BACKGROUND)  # fmt: skip
    seen = backdrop_of(build)
    assert seen["drawn"] is False and seen["errors"] == []


def hazard_pixels(build):
    """The level's tile texture: how much redder than green each of the hazard's and the
    ground's cells is on average, and the white spike pixels in each cell's top quarter."""
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 320, "height": 180})
        page.goto(f"{url}/index.html?autoplay=1&capture=1&god=1&level=1&only=1")
        assert wait_until(page, READY)
        out = page.evaluate("""(() => {
          const L = sf.level.current, chars = Object.keys(L.data.tiles), T = L.tile;
          const ctx = L.layer.scene.textures.get('__sf_tiles_1').getSourceImage().getContext('2d');
          const cell = ch => { const i = chars.indexOf(ch), d = ctx.getImageData(i * T, 0, T, T).data;
            let red = 0, white = 0;
            for (let p = 0; p < d.length; p += 4) {
              red += d[p] - d[p + 1];
              if (p < T * 4 * Math.floor(T / 4) && d[p] > 220 && d[p + 1] > 220 && d[p + 2] > 220) white++;
            }
            return {red: red / (T * T), white}; };
          return {hazard: cell('^'), ground: cell('#')};
        })()""")
        browser.close()
    return out


def test_a_hazard_that_looks_like_the_ground_is_marked(tmp_path):
    """Live 2026-11-03: the foundry's hazard blocks were drawn with its floor tile; the hero
    stood on them and died. The engine tints such a hazard red and puts spikes on it."""
    from test_levels import LEGEND, L

    design, plan = slime_design(), slime_plan()
    legend = [L("^", "hazard", "ground") if e.char == "^" else e for e in LEGEND]
    rec = check(GATED, n=1, legend=legend, events=["level_complete"])
    assert next(e for e in rec.spec.legend if e.char == "^").entity == "ground"
    seen = hazard_pixels(built(tmp_path, design, plan, [rec]))
    # Art drawn at detail 2 (a 1080p screen) gave the mock ground a redder tile, so the margin
    # is smaller than at 320x180; the white spikes are the unmistakable part.
    assert seen["hazard"]["red"] > seen["ground"]["red"] + 12 and seen["hazard"]["white"] > 0
    assert seen["ground"]["white"] == 0  # the ground itself is drawn as it is
