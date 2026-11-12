"""The playtest's HUD check: texts drawn over each other or off the screen fail a build."""

import io

from PIL import Image

from spriteforge.game.build import build_game
from spriteforge.game.playtest import failure_summary, playtest

# sf.text sizes are design pixels (times the screen scale), x and y are screen pixels: a live
# tower defense game used size 28 with lines 36 pixels apart on a 1920x1080 screen.
GAME = """
class Main extends Phaser.Scene {
  constructor() { super('main'); }
  create() {
    sf.event('level_start');
    %s
  }
  update() {
    // A number that floats for under a second is not HUD.
    if (Math.floor(sf.clock.now * 2) !== this.last) {
      this.last = Math.floor(sf.clock.now * 2);
      const pop = sf.text(this, 16, 12, '+5');
      this.time.delayedCall(400, () => pop.destroy());
    }
  }
}
sf.start({scenes: [Main], autoplay: () => ({})});
"""
GOOD = """
    this.gold = sf.text(this, 16, 12, 'GOLD 220', {size: 8});
    this.lives = sf.text(this, 16, 76, 'LIVES 15', {size: 8});
    this.msg = sf.text(this, 960, 500, '', {size: 16, originX: 0.5});
    sf.choose(this, 'PICK', ['One', 'Two'], () => {});
"""
BAD = """
    this.gold = sf.text(this, 16, 12, 'GOLD 220', {size: 28});
    this.lives = sf.text(this, 16, 48, 'LIVES 15', {size: 28});
    this.hint = sf.text(this, 16, 900, 'SPACE starts the next wave early for a bonus', {size: 22});
"""


def build(tmp_path, body: str):
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (240, 200, 60, 255)).save(buf, format="PNG")
    assets = {"screen": [1920, 1080], "scale": 6, "background": "#000000",
              "sprites": {"dot": {"file": "assets/dot.png", "frame": [8, 8], "anims": {}}},
              "images": {}}  # fmt: skip
    return build_game(tmp_path / "build", title="t", controls="-", files={"game.js": GAME % body},
                      assets=assets, images={"assets/dot.png": buf.getvalue()}, credits=[])  # fmt: skip


def test_a_tidy_hud_passes(tmp_path):
    report = playtest(build(tmp_path, GOOD), [], 6)
    assert report["hud"] == []
    assert report["passed"], report


def test_texts_over_each_other_or_off_the_screen_fail_the_build(tmp_path):
    report = playtest(build(tmp_path, BAD), [], 6)
    assert not report["passed"]
    assert not report["errors"]
    over = [m for m in report["hud"] if "is drawn over" in m]
    off = [m for m in report["hud"] if "runs off the 1920x1080 screen" in m]
    assert len(over) == 1 and '"GOLD 220" (x 16, y 12, size 28)' in over[0] and "LIVES 15" in over[0]
    assert len(off) == 1 and "SPACE starts" in off[0]
    told = failure_summary(report)
    assert over[0] in told and "size is in design pixels" in told


def test_the_check_can_be_turned_off(tmp_path):
    report = playtest(build(tmp_path, BAD), [], 6, hud=False)
    assert report["hud"] == [] and report["passed"]
