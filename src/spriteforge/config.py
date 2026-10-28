from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageConfig(Schema):
    base_url: HttpUrl
    model: str
    generate_path: str
    edit_path: str


class LLMConfig(Schema):
    base_url: HttpUrl
    model: str
    chat_path: str


class BudgetConfig(Schema):
    max_image_calls: int = Field(default=150, ge=0)
    max_calls_per_model: int = Field(default=200, ge=0)
    image_cost: float = Field(default=0.01, ge=0)
    max_estimated_cost: float = Field(default=5, ge=0)


class MemoryConfig(Schema):
    min_score: float = Field(default=0, ge=0, allow_inf_nan=False)
    relative_score: float = Field(default=0.5, ge=0, le=1)


class Config(Schema):
    workspace: Path
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
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
