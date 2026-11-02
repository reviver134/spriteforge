"""Probe for M10.4 (no model calls): do Playwright key presses reach the game while the harness
steps it frame by frame?

Builds a fixture game (a hero that moves with `sf.input`, an `sf.choose` menu at 1 s, Enter
to restart after the menu) and opens it like the playtest does (`?headless=1`, the game loop
stopped, `sfRun` stepping fixed frames), but without autoplay, so input comes from the keyboard.
Then: holds ArrowRight (Playwright `keyboard.down`) across `sfRun` calls and checks the hero
moved right; releases it and checks it stopped; presses "2" while the menu is open and checks
option 2 was chosen (`sf.choose` listens to Phaser's keydown); presses Enter and checks the
scene restarted and keys still move the hero afterwards.

Prints one line per check. If key presses do not arrive, the SDK needs `sf.inject(keys)`, set
by the runner per step.
"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.build import build_game
from spriteforge.game.design import fallback_design
from spriteforge.game.record import READY, serve, wait_until

GAME = """
class Probe extends Phaser.Scene {
  constructor() { super('probe'); }
  create() {
    const [w, h] = sf.screen();
    this.hero = sf.sprite(this, w / 2, h / 2, 'hero');
    this.menuAt = sf.clock.now + 1; this.picked = null; this.paused = false;
    this.restarted = (window.probeCreates = (window.probeCreates || 0) + 1) > 1;
    this.enter = this.input.keyboard.addKey('ENTER');
  }
  update() {
    if (this.paused) return;
    const inp = sf.input(this);
    this.hero.setVelocity(inp.x * 60, inp.y * 60);
    if (this.menuAt && sf.clock.now >= this.menuAt) {
      this.menuAt = 0; this.paused = true; this.physics.world.pause();
      sf.choose(this, 'PICK', ['one', 'two', 'three'], i => {
        this.picked = i; this.paused = false; this.physics.world.resume();
      });
    }
    if (this.picked !== null && Phaser.Input.Keyboard.JustDown(this.enter)) this.scene.restart();
    sf.state = {x: Math.round(this.hero.x), y: Math.round(this.hero.y), picked: this.picked,
                restarted: this.restarted};
  }
}
sf.start({scenes: [Probe], autoplay: () => ({})});
"""


def main() -> int:
    design = fallback_design("probe")
    art = make_art(design, keyword_plan(design), generate=None)
    results = []
    with TemporaryDirectory() as tmp:
        build = build_game(Path(tmp) / "build", title="probe", controls="-",
                           files={"game.js": GAME}, assets=art.manifest(), images=art.files(),
                           credits=[])  # fmt: skip
        with serve(build) as url, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 320, "height": 180})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{url}/index.html?headless=1")
            assert wait_until(page, READY), "the game never finished booting"
            run = lambda s: page.evaluate(f"window.sfRun({s}, 30)")
            state = lambda: page.evaluate("window.sf.state")

            run(0.2)
            x0 = state()["x"]
            page.keyboard.down("ArrowRight")
            run(0.5)
            x1 = state()["x"]
            results.append(("hold ArrowRight moves the hero", x1 > x0 + 5, f"x {x0} -> {x1}"))
            page.keyboard.up("ArrowRight")
            run(0.2)
            x2 = state()["x"]
            run(0.1)  # the menu opens at 1 s of game time
            x3 = state()["x"]
            results.append(("release stops it", abs(x3 - x2) <= 1, f"x {x2} -> {x3}"))
            run(0.3)
            page.keyboard.press("Digit2")
            run(0.1)
            s = state()
            results.append(("'2' picks option 2 of an sf.choose menu", s["picked"] == 1,
                            f"picked {s['picked']}, counts {page.evaluate('window.sf.counts.choice')}"))  # fmt: skip
            page.keyboard.down("Enter")
            run(0.1)
            page.keyboard.up("Enter")
            run(0.2)
            s = state()
            results.append(("Enter restarts the scene", s["restarted"] is True, json.dumps(s)))
            xa = s["x"]
            page.keyboard.down("ArrowLeft")
            run(0.5)
            page.keyboard.up("ArrowLeft")
            xb = state()["x"]
            results.append(
                ("keys still move the hero after restart", xb < xa - 5, f"x {xa} -> {xb}")
            )
            # A press shorter than one frame: down and up before the next step.
            page.keyboard.press("ArrowRight")
            xc = state()["x"]
            run(0.2)
            xd = state()["x"]
            results.append(("a press between two steps is seen by sf.input", xd > xc,
                            f"x {xc} -> {xd} (expected: not seen; isDown is polled)"))  # fmt: skip
            errors += page.evaluate("window.sf.errors")
            browser.close()
    for name, ok, detail in results:
        print(f"{'ok ' if ok else 'NO '} {name}: {detail}")
    print("errors:", errors or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
