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
    max_estimated_cost: float = Field(default=5, ge=0)


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

    max_repairs: int = Field(default=3, ge=0, le=6)  # Programmer fix rounds after failed checks
    max_art_calls: int = Field(default=16, ge=0, le=40)  # Muse Image calls per build
    image_workers: int = Field(default=4, ge=1, le=8)  # Muse Image calls in flight at once
    image_cache: bool = True  # identical Muse requests reuse workspace/cache/muse-image
    art_retry: int = Field(default=1, ge=0, le=2)  # redraws of a Muse frame that fails checks
    native_grid_confidence: float = Field(default=0.6, ge=0, le=1)  # big-pixel grid read-back
    # Muse sprites as sheets: frames per animation row (1 = one image per sprite)
    sheet_frames: int = Field(default=4, ge=1, le=6)
    adapt_size: bool = True  # a Muse sprite's frame takes the drawn art's shape
    # Art Reviewer (M10.3): one vision call after the playtest; blocking issues are fixed once
    # (at most review_max_fixes Muse Image edits at review_image_reasoning) before G4.
    review: bool = True
    review_max_fixes: int = Field(default=4, ge=0, le=8)
    review_image_reasoning: Literal["low", "high"] = "high"
    # Review call plus fixes stay within this estimate: fewer fixes when the review costs more.
    review_cost_cap: float = Field(default=0.08, ge=0)
    # QA tests (M10.4): the QA Playtester writes tests from each design version; every build runs
    # them (and earlier versions' tests) with real key presses after the bot playtest.
    qa: bool = True
    # Story bible and game database (M11.1): the Game Designer writes the story with each design
    # version; every game keeps its canon in an OKF corpus (runs/<id>/game/db).
    story: bool = True
    max_code_kb: int = Field(default=200, ge=10, le=1000)
    playtest_fps: int = Field(default=30, ge=10, le=60)
    record_seconds: int = Field(default=20, ge=5, le=60)


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
