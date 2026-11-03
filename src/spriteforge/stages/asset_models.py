"""Manifest, dependency graph, and durable production contracts."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from spriteforge.agents.models import Contract

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Size = tuple[Annotated[int, Field(ge=4, le=256)], Annotated[int, Field(ge=4, le=256)]]


class AssetSpec(Contract):
    id: Identifier
    type: Literal["character", "tile", "item", "ui", "fx"]
    size: Size
    frames: dict[Identifier, Annotated[int, Field(ge=1, le=12)]]
    desc: str = Field(min_length=1, max_length=3000)
    depends_on: list[Identifier]
    refs: list[Identifier] = Field(max_length=6)
    locked: bool = False

    @model_validator(mode="after")
    def base_frame(self):
        if not self.frames or not self.frames.get("idle"):
            raise ValueError("Every asset needs an idle base frame")
        if sum(self.frames.values()) > 32:
            raise ValueError("At most 32 frames per asset")
        if self.id == "anchor":
            raise ValueError("anchor is a reserved reference")
        return self


class FrameState(Contract):
    state: Identifier
    count: int = Field(ge=1, le=12)


class ProposedAsset(Contract):
    """An array of states keeps Muse's strict JSON schema free of open objects."""

    id: Identifier
    type: Literal["character", "tile", "item", "ui", "fx"]
    size: Size
    frames: list[FrameState] = Field(min_length=1, max_length=12)
    desc: str
    depends_on: list[Identifier]
    refs: list[Identifier] = Field(max_length=6)


class ManifestProposal(Contract):
    assets: list[ProposedAsset] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_proposal(self):
        assets = []
        for item in self.assets:
            if len({f.state for f in item.frames}) != len(item.frames):
                raise ValueError("Duplicate animation states")
            assets.append(
                AssetSpec.model_validate(
                    {**item.model_dump(), "frames": {f.state: f.count for f in item.frames}}
                )
            )
        Manifest(assets=assets)
        return self


class Manifest(Contract):
    assets: list[AssetSpec] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_graph(self):
        self.tasks()
        return self

    def tasks(self):
        ids = [s.id for s in self.assets]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate asset ids")
        tasks = []
        for spec in self.assets:
            dependencies = list(
                dict.fromkeys(spec.depends_on + [r for r in spec.refs if r != "anchor"])
            )
            if set(dependencies) - set(ids):
                raise ValueError(f"Unknown dependencies/references for {spec.id}")
            tasks.append(
                AssetTask(
                    id=spec.id, spec=spec, state="idle", index=0, base=None, depends_on=dependencies
                )
            )
            for state, count in spec.frames.items():
                for index in range(1 if state == "idle" else 0, count):
                    tasks.append(
                        AssetTask(
                            id=f"{spec.id}__{state}_{index}",
                            spec=spec,
                            state=state,
                            index=index,
                            base=spec.id,
                            depends_on=list(dict.fromkeys([spec.id, *dependencies])),
                        )
                    )
        if len(tasks) > 256 or len({t.id for t in tasks}) != len(tasks):
            raise ValueError("Expanded manifest is too large or has colliding ids")
        ordered, ready = [], set()
        pending = list(tasks)
        while pending:
            batch = [t for t in pending if set(t.depends_on) <= ready]
            if not batch:
                raise ValueError("Cyclic asset dependencies")
            for task in batch:
                ordered.append(task)
                ready.add(task.id)
                pending.remove(task)
        return ordered


class AssetTask(Contract):
    id: str
    spec: AssetSpec
    state: str
    index: int
    base: str | None
    depends_on: list[str]


def dependents(tasks, selected):
    result = set(selected)
    if not result or result - {t.id for t in tasks}:
        raise ValueError("Select known asset ids for the change request")
    while True:
        expanded = result | {t.id for t in tasks if set(t.depends_on) & result}
        if expanded == result:
            return result
        result = expanded


class Critique(Contract):
    style_match: int = Field(ge=1, le=5)
    readability: int = Field(ge=1, le=5)
    matches_desc: bool
    issues: list[str]
    fix: str

    @property
    def passed(self):
        return (
            self.style_match >= 3
            and self.readability >= 3
            and self.matches_desc
            and not self.issues
        )


class ObjectReading(Contract):
    name: str
    confidence: int = Field(ge=1, le=5)


class SetIssue(Contract):
    asset_ids: list[str]
    issue: str
    fix: str


class SetReview(Contract):
    style_match: int = Field(ge=1, le=5)
    issues: list[SetIssue]


class AssetRecord(Contract):
    id: str
    version: int
    type: str
    parent: str
    state: str
    index: int
    artifact: str
    status: Literal["accepted", "placeholder"]
    attempts: int = Field(ge=0, le=3)
    deterministic_fix: bool
    scores: Critique | None
    issues: list[str]
    provenance: str
    locked: bool = True


class ProductionPlan(Contract):
    versions: dict[str, int]
    instructions: dict[str, str]
