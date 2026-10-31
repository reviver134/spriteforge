# Tile sets

Named tiles cut from installed tileset sheets, for generated games (`spriteforge.game`). One
JSON file per tile set; a set is only offered when its `pack` is installed in the asset
library. Games refer to a tile as `<tileset id>/<tile name>`.

```json
{
  "id": "my-forest",
  "pack": "my-forest-pack",
  "camera": "top-down",
  "tile_size": 16,
  "summary": "16 px grass, dirt path and water.",
  "keywords": ["forest", "meadow"],
  "variants": {"normal": {"words": [], "row_offset": 0}},
  "tiles": {
    "grass": {"what": "short grass", "asset": "my-forest-pack/tiles", "cell": [2, 10]}
  },
  "background": "#2f5a2a"
}
```

* `camera`: `top-down` or `side-view`; `tile_size`: grid size of the sheet in pixels.
* `cell`: [column, row]; `"seasonal": true` adds the variant's `row_offset` to the row;
  `"flip_y": true` mirrors the tile vertically (e.g. stalactite tips used as floor spikes).
* `variants`: alternative rows chosen by trigger `words` (e.g. `winter`), plus an optional
  parallax `backdrop` tag.
* `backdrop`: `{"asset_pattern": "<pack>/{variant}-{layer}", "layers": [...], "factors": [...]}`,
  back to front; `factors` are parallax scroll speeds 0-1.

Included: `gandalf-seasons` (side-view, 32 px, normal/autumn/winter, parallax forest + castle)
and `pixel-crawler-dungeon` (top-down, 16 px stone floors, walls, water).
