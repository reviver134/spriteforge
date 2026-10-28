import asyncio
import json
import sys

from PIL import Image

from spriteforge.mcp.client import MCPClient
from spriteforge.mcp.registry import Server


def test_free_pixel_tools_real_sdk_draw_process_and_phaser_export(tmp_path):
    server = Server(
        command=sys.executable, args=["-m", "spriteforge.mcp.pixel_tools", str(tmp_path)]
    )

    async def exercise():
        client = MCPClient()
        tools = await client.discover(server)
        assert {t["name"] for t in tools} == {
            "draw_grid",
            "inspect_sprite",
            "process_sprite",
            "export_atlas",
        }
        assert all(
            not t["annotations"]["readOnlyHint"] for t in tools if t["name"] != "inspect_sprite"
        )
        result = await client.call(
            server,
            "draw_grid",
            {"grid": {"rows": ["....", ".gg.", ".gg.", "...."], "legend": {"g": "#00AA00"}}},
        )
        assert not result["isError"]
        files = result["structuredContent"]["files"]
        sprite = files["sprite.png"]
        original = (tmp_path / sprite).read_bytes()
        with Image.open(tmp_path / sprite) as image:
            assert image.size == (4, 4)
            assert image.getpixel((0, 0))[3] == 0
            assert image.getpixel((1, 1)) == (0, 170, 0, 255)
        inspected = await client.call(server, "inspect_sprite", {"path": sprite})
        assert not inspected["isError"]
        cleaned = await client.call(
            server,
            "process_sprite",
            {
                "path": sprite,
                "spec": {"size": [8, 8]},
                "style": {"palette": ["#00AA00"], "max_colors": 1},
            },
        )
        assert not cleaned["isError"]
        assert not cleaned["structuredContent"]["report"]["failures"]
        atlas = await client.call(
            server,
            "export_atlas",
            {
                "request": {
                    "frames": {"hero/idle/0": sprite},
                    "animations": {"idle": ["hero/idle/0"]},
                }
            },
        )
        assert not atlas["isError"]
        data = json.loads(
            (tmp_path / atlas["structuredContent"]["files"]["atlas.json"]).read_text()
        )
        assert "hero/idle/0" in data["frames"]
        assert data["meta"]["animations"] == {"idle": ["hero/idle/0"]}
        assert (tmp_path / sprite).read_bytes() == original
        assert (await client.call(server, "inspect_sprite", {"path": "../outside.png"}))["isError"]
        before = set((tmp_path / "tool-output").iterdir())
        invalid = await client.call(
            server, "draw_grid", {"grid": {"rows": ["x"], "legend": {"g": "#00AA00"}}}
        )
        assert invalid["isError"]
        assert set((tmp_path / "tool-output").iterdir()) == before
        (tmp_path / "runs").mkdir()
        Image.new("RGBA", (257, 1)).save(tmp_path / "runs/oversized.png")
        oversized = await client.call(
            server, "export_atlas", {"request": {"frames": {"bad": "runs/oversized.png"}}}
        )
        assert oversized["isError"]
        assert set((tmp_path / "tool-output").iterdir()) == before

    asyncio.run(exercise())
