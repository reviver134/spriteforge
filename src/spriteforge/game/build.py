"""Assemble a playable build folder from generated code, the SDK and the game's art.

build/
  index.html          loads phaser.min.js, sf.js, assets.js, then the generated files
  phaser.min.js       Phaser 3.90 (MIT, licence copied next to it)
  sf.js               the SDK (game/sdk/sf.js)
  assets.js/.json     art manifest: screen, scale, sprites (frames, animations), images
  assets/<id>.png     sprite sheets and images
  <generated>.js      the game itself; game.js is loaded last
  CREDITS.txt         where each piece of art came from and under which licence
"""

import html
import json
import shutil
from pathlib import Path

SDK_DIR = Path(__file__).parent / "sdk"
PHASER_DIR = Path(__file__).parents[1] / "templates" / "phaser" / "vendor"
# Generated code may load nothing from the network; the SDK reads only same-folder files.
CSP = (
    "default-src 'self'; script-src 'self'; img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self'"
)


def script_order(paths: list[str]) -> list[str]:
    """Helper files first (in the order given), game.js last."""
    return [p for p in paths if p != "game.js"] + ["game.js"]


def index_html(title: str, controls: str, scripts: list[str]) -> str:
    tags = "".join(f'<script src="{html.escape(s)}"></script>' for s in scripts)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<title>{html.escape(title)}</title>
<style>html,body{{margin:0;height:100%;background:#0b0c14;overflow:hidden}}
canvas{{display:block;image-rendering:pixelated}}
#help{{position:fixed;bottom:6px;left:0;right:0;text-align:center;
font:12px/1.4 system-ui,sans-serif;color:#94b0c2;pointer-events:none}}</style>
</head><body>
<div id="help">{html.escape(controls)} &middot; add <code>?autoplay=1</code> to watch the bot</div>
<script src="phaser.min.js"></script><script src="sf.js"></script><script src="assets.js"></script>
{tags}
</body></html>
"""


def build_game(
    out: Path,
    *,
    title: str,
    controls: str,
    files: dict[str, str],
    assets: dict,
    images: dict[str, bytes],
    credits: list[str],
) -> Path:
    """Write the build into `out` (replaced). `files` are generated scripts by name, `images`
    are asset PNGs by their path inside the build (e.g. "assets/player.png")."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy(PHASER_DIR / "phaser.min.js", out / "phaser.min.js")
    shutil.copy(PHASER_DIR / "LICENSE.md", out / "PHASER-LICENSE.md")
    shutil.copy(SDK_DIR / "sf.js", out / "sf.js")
    for path, data in images.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = json.dumps(assets, indent=1)
    (out / "assets.json").write_text(manifest + "\n", encoding="utf-8")
    (out / "assets.js").write_text(f"window.SF_ASSETS = {manifest};\n", encoding="utf-8")
    for name, code in files.items():
        (out / name).write_text(code, encoding="utf-8")
    (out / "index.html").write_text(
        index_html(title, controls, script_order(list(files))), encoding="utf-8"
    )
    (out / "CREDITS.txt").write_text(
        "Art sources and licences\n\n" + ("\n".join(credits) or "(generated art only)") + "\n",
        encoding="utf-8",
    )
    return out
