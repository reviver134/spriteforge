"""The game SDK (sf.js) guards Phaser pitfalls that generated games hit."""

import io

from PIL import Image
from playwright.sync_api import sync_playwright

from spriteforge.game.build import build_game
from spriteforge.game.record import serve

# The pattern a live survival game used: velocity first, then group.add(); and a restart on
# action after losing.
GAME = """
class Main extends Phaser.Scene {
  constructor() { super('main'); }
  create() {
    this.bolts = this.physics.add.group();
    this.player = sf.sprite(this, 160, 90, 'dot');
    this.over = false;
  }
  fire() {
    const b = sf.sprite(this, 160, 90, 'dot');
    b.body.setVelocity(120, 0);
    this.bolts.add(b);
    return b;
  }
  update() {
    const inp = sf.input(this);
    if (this.over) { if (inp.action) this.scene.restart(); return; }
    this.player.body.setVelocity(inp.x * 90, inp.y * 90);
  }
}
sf.start({scenes: [Main], autoplay: () => ({})});
"""
MAIN = "sf.game.scene.getScene('main')"


def dot() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (240, 200, 60, 255)).save(buf, format="PNG")
    return buf.getvalue()


def frames(page, seconds: float) -> None:
    for _ in range(round(seconds * 30)):
        page.evaluate("sfRun(1/30, 30)")


def test_group_add_keeps_velocity_and_restart_keeps_input(tmp_path):
    assets = {
        "screen": [320, 180],
        "scale": 1,
        "background": "#000000",
        "sprites": {"dot": {"file": "assets/dot.png", "frame": [8, 8], "anims": {}}},
        "images": {},
    }
    build = build_game(tmp_path / "build", title="t", controls="-", files={"game.js": GAME},
                       assets=assets, images={"assets/dot.png": dot()}, credits=[])  # fmt: skip
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 320, "height": 180})
            page.goto(f"{url}/index.html?capture=1")
            page.wait_for_function("window.sf && window.sf.ready === true", timeout=30000)
            page.evaluate(f"window.bolt = {MAIN}.fire()")
            frames(page, 0.5)
            assert page.evaluate("window.bolt.x") > 200  # it flies; it used to stay at 160
            page.evaluate(f"{MAIN}.over = true")
            page.keyboard.down("Space")
            frames(page, 0.2)
            page.keyboard.up("Space")
            frames(page, 0.2)
            assert page.evaluate(f"{MAIN}.over") is False  # restarted
            x0 = page.evaluate(f"{MAIN}.player.x")
            page.keyboard.down("ArrowRight")
            frames(page, 0.5)
            page.keyboard.up("ArrowRight")
            assert page.evaluate(f"{MAIN}.player.x") - x0 > 20  # keys still work after restart
            assert page.evaluate("sf.errors") == []
        finally:
            browser.close()
