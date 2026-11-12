from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageConfig(Schema):
    base_url: HttpUrl
    model: str
    generate_path: str
    edit_path: str
    # Sizes Muse Image has accepted live; requests pick the one nearest the subject's aspect.
    sizes: list[tuple[int, int]] = Field(default_factory=lambda: [(1024, 1024)], min_length=1)


Effort = Literal["minimal", "low", "medium", "high", "xhigh", "max"]


class LLMConfig(Schema):
    base_url: HttpUrl
    model: str
    chat_path: str
    # Spark `reasoning_effort` per task (intake, art_plan, revision, design, code); a task
    # not listed sends none and the model picks its own effort.
    reasoning: dict[str, Effort] = Field(default_factory=dict)


class BudgetConfig(Schema):
    max_image_calls: int = Field(default=150, ge=0)
    max_calls_per_model: int = Field(default=200, ge=0)
    max_total_calls: int | None = Field(default=None, ge=0)
    image_cost: float = Field(default=0.01, ge=0)
    # Meta standard Spark rates: https://dev.meta.ai/docs/pricing-rate-limits
    spark_input_per_million: float = Field(default=1.25, ge=0)
    spark_cached_input_per_million: float = Field(default=0.15, ge=0)
    spark_output_per_million: float = Field(default=4.25, ge=0)
    # Muse Spark web search: "$2.50 per 1,000 search queries, in addition to the request's
    # token cost" (https://dev.meta.ai/docs/pricing-rate-limits, 2026-11-02).
    web_search_cost: float = Field(default=0.0025, ge=0)
    max_estimated_cost: float = Field(default=5, ge=0)
    # True (config.yaml): each agent may spend `max_agent_cost` (USD, estimated) since the
    # human's last action (a gate answer, Go back, Continue); the caps above do not apply. Its
    # loops run until that is used up or a try brings no improvement (game.patience), and work
    # the user asks for always starts. False: the caps above, per run (the legacy concept and
    # production stages, budgets made in code).
    per_request: bool = False
    max_agent_cost: float = Field(default=10, ge=0)


class ResearchConfig(Schema):
    """Muse Spark web search for the Game Designer's research (L2.2), on the Responses API."""

    path: str = "/v1/responses"
    # Built-in tool calls per research (searches and page opens; `max_tool_calls`).
    max_searches: int = Field(default=8, ge=1, le=20)
    search_context_size: Literal["low", "medium", "high"] = "medium"


class MemoryConfig(Schema):
    min_score: float = Field(default=0, ge=0, allow_inf_nan=False)
    relative_score: float = Field(default=0.5, ge=0, le=1)


class PreviewConfig(Schema):
    max_image_calls: int = Field(default=16, ge=0, le=150)
    mood_board: bool = True
    # game: after G1 Spark writes a design (G2 gate) and then the game's code (G4 playtest
    # gate); art comes from the library first, Muse Image for the rest (`game` settings).
    # muse: the original Muse Image building-block preview.
    mode: Literal["game", "muse"] = "game"

    @model_validator(mode="before")
    @classmethod
    def legacy_kit_settings(cls, data):
        """Runs saved before kits were removed: mode 'kit' and preview.max_art_calls."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "max_art_calls"}
            if data.get("mode") == "kit":
                data["mode"] = "game"
        return data


class GameConfig(Schema):
    """Prompt-driven games: Spark writes the code, the harness checks and playtests it."""

    # Agent loops (Programmer repairs, Muse redraws, QA check fixes, Menu and Level retries,
    # Art Reviewer rounds) try again while they improve: they stop after `patience` tries in a
    # row without improvement, or when the agent's allowance (budget.max_agent_cost) is used
    # up. The counts below are optional fixed limits on top (None: no fixed limit).
    patience: int = Field(default=2, ge=1, le=10)
    max_repairs: int | None = Field(default=None, ge=0)  # Programmer fix rounds after failed checks
    max_art_calls: int | None = Field(default=None, ge=0)  # Muse Image calls per build
    image_workers: int = Field(default=4, ge=1, le=8)  # Muse Image calls in flight at once
    image_cache: bool = True  # identical Muse requests reuse workspace/cache/muse-image
    # The game's screen: 320x180 design pixels times screen_scale (6: 1920x1080, a laptop's
    # full-HD screen). Every sprite, tile, text and distance is that many times bigger, so a
    # sprite keeps more of the detail Muse drew and words stay readable.
    screen_scale: int = Field(default=6, ge=1, le=8)
    # The Level/Tile Designer studies the level design of similar platformers with web search
    # (one search call per game, before the first level) and its level prompts read it.
    level_research: bool = True
    # Muse Image's strength for every first draw (the API has low and high; redraws and the
    # Art Reviewer's fixes use high too).
    image_reasoning: Literal["low", "high"] = "high"
    art_retry: int | None = Field(default=None, ge=0)  # redraws of a Muse frame that fails checks
    native_grid_confidence: float = Field(default=0.6, ge=0, le=1)  # big-pixel grid read-back
    # Muse sprites as sheets: frames per animation row (1 = one image per sprite)
    sheet_frames: int = Field(default=4, ge=1, le=6)
    adapt_size: bool = True  # a Muse sprite's frame takes the drawn art's shape
    # Art Reviewer (M10.3): one vision call after the playtest; blocking issues are fixed once
    # (at most review_max_fixes Muse Image edits at review_image_reasoning) before G4.
    review: bool = True
    review_max_fixes: int | None = Field(default=None, ge=0)
    review_image_reasoning: Literal["low", "high"] = "high"
    # Review call plus fixes stay within this estimate: fewer fixes when the review costs more.
    review_cost_cap: float | None = Field(default=None, ge=0)
    # QA tests (M10.4): the QA Playtester writes tests from each design version; every build runs
    # them (and earlier versions' tests) with real key presses after the bot playtest.
    qa: bool = True
    # Code checks (L2.8): after the Programmer's first answer the QA Playtester reads the design
    # and the code and writes checks of this game's own rules as JavaScript run in the game.
    qa_checks: bool = True
    # The bot playtest also looks at the HUD: texts drawn over each other or off the screen
    # fail the build and go to the Programmer.
    hud_check: bool = True
    motion: bool = True  # the Art Director's motion design: rows, forms, effects (L2.4)
    # Menus and flow (L2.5): the Menu Designer's screens rendered by the SDK (title, pause,
    # results, game over, victory, saved progress) and G3; title_art: Muse draws the title
    # background and emblem (2 image calls; false or offline: code draws them).
    menus: bool = True
    title_art: bool = True
    # Sounds (L2.7): the Sound Director's synthesised sounds (one Spark call per game), played by
    # the SDK on the game's moments and edited in the build's sound editor or at G4.
    sounds: bool = True
    # Agent tools: in a live build each agent's Spark call may look things up with the tools its
    # role grants (the built-in library and game-record MCP servers, its skills, and the user's
    # MCP tools granted to it that need no confirmation), for up to `tool_rounds` rounds.
    agent_tools: bool = True
    tool_rounds: int = Field(default=3, ge=0, le=10)
    max_code_kb: int = Field(default=200, ge=10, le=1000)
    playtest_fps: int = Field(default=30, ge=10, le=60)
    record_seconds: int = Field(default=20, ge=5, le=60)

    @model_validator(mode="before")
    @classmethod
    def legacy_story_switch(cls, data):
        """Runs saved before L2.1 had `game.story`; the G1 brief now decides (a story only
        when the human asks for one)."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "story"}
        return data


class RouterConfig(Schema):
    enabled: bool = True
    max_skills: int = Field(default=3, ge=0, le=100)
    max_tools: int = Field(default=5, ge=0, le=100)
    token_budget: int = Field(default=4096, ge=0)


class Config(Schema):
    workspace: Path
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    game: GameConfig = Field(default_factory=GameConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    image: ImageConfig
    llm: LLMConfig
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


def load_config(path: Path | None = None) -> Config:
    return Config.model_validate(
        yaml.safe_load(
            (path or Path(__file__).with_name("config.yaml")).read_text(encoding="utf-8")
        )
    )


def init_workspace(path: Path) -> Path:
    root = path.expanduser().resolve()
    for name in ("runs", "memory", "skills"):
        (root / name).mkdir(parents=True, exist_ok=True)
    mcp = root / "mcp.json"
    if not mcp.exists():
        mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    return root
