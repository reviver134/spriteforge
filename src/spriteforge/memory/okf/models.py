from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class Extensible(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Generated(Extensible):
    by: str = Field(min_length=1)
    at: AwareDatetime | None = None


class Verified(Extensible):
    by: str = Field(min_length=1)
    at: AwareDatetime


class Source(Extensible):
    resource: str = Field(min_length=1)
    id: str | None = None
    title: str | None = None
    author: str | None = None
    usage_count: int | None = Field(default=None, ge=0)
    last_modified: AwareDatetime | None = None


class Metadata(Extensible):
    model_config = ConfigDict(
        extra="allow", populate_by_name=False, validate_by_name=False, validate_by_alias=True
    )
    type: str = Field(min_length=1)
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    generated: Generated | None = None
    verified: list[Verified] = Field(default_factory=list)
    status: Literal["draft", "stable", "deprecated"] = "stable"
    stale_after: AwareDatetime | None = None
    scope: Literal["project", "private"] = Field(default="project", alias="x-spriteforge-scope")

    @field_validator("type")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("type must not be blank")
        return value

    @field_validator("verified", mode="before")
    @classmethod
    def verifier_mapping(cls, value):
        return [value] if isinstance(value, dict) else value

    @property
    def trust_tier(self) -> str:
        if any(v.by.startswith("human:") for v in self.verified):
            return "human-reviewed"
        return "machine-confirmed" if self.verified else "unverified"

    @property
    def stale(self) -> bool:
        return self.stale_after is not None and datetime.now(UTC) >= self.stale_after


class Entry(BaseModel):
    id: str
    metadata: Metadata
    body: str

    @property
    def private(self) -> bool:
        return self.id.split("/")[0] == "user-prefs" or self.metadata.scope == "private"

    @property
    def title(self) -> str:
        return self.metadata.title or self.id.rsplit("/", 1)[-1].replace("-", " ")


class Snippet(BaseModel):
    id: str
    title: str
    trust_tier: str
    stale: bool
    status: str
    scope: str
    score: float
    text: str
    tokens: int = 0


class ValidationReport(BaseModel):
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    external: str = "not installed"


class GenreSeed(BaseModel):
    id: str
    title: str
    perspective: str
    controls: str
    mechanics: str
    entities: list[str]
    palette: str
    assets: dict[str, int]
    animations: str
    pitfalls: list[str]
    questions: list[str] = Field(min_length=5, max_length=8)
    extra: dict[str, Any] = Field(default_factory=dict)
