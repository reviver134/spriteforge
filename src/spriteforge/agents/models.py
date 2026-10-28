"""Artifacts exchanged by the Director, roles, and human gates."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleOutput(Contract):
    summary: str
    asset_ids: list[str] = Field(default_factory=list)


class ChangeRequest(Contract):
    target: list[str] | Literal["style", "mechanic", "level"]
    instruction: str = Field(min_length=1)
    severity: Literal["minor", "major", "critical"]
    scope: str = Field(min_length=1)
    needs_regen: bool

    @model_validator(mode="after")
    def nonempty_targets(self):
        if isinstance(self.target, list) and not self.target:
            raise ValueError("Asset targets cannot be empty")
        return self


class Changes(Contract):
    requests: list[ChangeRequest] = Field(min_length=1)


class StageNode(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    role: str | None = None
    gate: str | None = None
    goal: str
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_kind(self):
        if (self.role is None) == (self.gate is None):
            raise ValueError("Stage needs exactly one role or gate")
        return self


class StagePlan(Contract):
    stages: list[StageNode] = Field(min_length=1)

    @model_validator(mode="after")
    def dag(self):
        ids = {stage.id for stage in self.stages}
        if len(ids) != len(self.stages):
            raise ValueError("Duplicate stage ids")
        done = set()
        while len(done) < len(ids):
            ready = {s.id for s in self.stages if s.id not in done and set(s.depends_on) <= done}
            if not ready:
                raise ValueError("Cyclic plan or unknown dependency")
            done.update(ready)
        return self
