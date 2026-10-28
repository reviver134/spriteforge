"""Director planning and dispatch policy; plans and changes are schema-checked artifacts."""

from .models import Changes, StageNode, StagePlan
from .roles import Role
from .runtime import AgentRuntime, AgentTask


class Director:
    def __init__(self, runtime: AgentRuntime, role: Role, roles: dict[str, Role]):
        self.runtime, self.role, self.roles = runtime, role, roles

    def validate_roles(self, plan: StagePlan) -> StagePlan:
        if any(s.role and s.role not in self.roles for s in plan.stages):
            raise ValueError("Plan names an unavailable role")
        return plan

    async def plan(self, task: AgentTask) -> tuple[StagePlan, str]:
        artifact = await self.runtime.run(self.role, task, StagePlan)
        return self.validate_roles(StagePlan.model_validate_json(artifact.data)), artifact.id

    async def feedback(self, task: AgentTask, asset_ids: set[str]) -> tuple[Changes, str]:
        artifact = await self.runtime.run(self.role, task, Changes)
        changes = Changes.model_validate_json(artifact.data)
        for change in changes.requests:
            if isinstance(change.target, list) and set(change.target) - asset_ids:
                raise ValueError("Feedback names unknown assets")
            if change.needs_regen and not isinstance(change.target, list):
                raise ValueError("Regeneration requires explicit asset ids")
        return changes, artifact.id

    async def replan(self, task: AgentTask, changes: Changes) -> tuple[StagePlan, str]:
        # The confirmed requests are passed as explicit artifacts in task.inputs.
        plan, ident = await self.plan(task)
        if any(s.gate for s in plan.stages):
            raise ValueError("A change plan must dispatch roles only")
        if not changes.requests:
            raise ValueError("Re-planning requires confirmed requests")
        return plan, ident

    async def dispatch(self, stage: StageNode, task: AgentTask, schema):
        if stage.role not in self.roles:
            raise ValueError("Cannot dispatch an unavailable role")
        return await self.runtime.run(self.roles[stage.role], task, schema)

    @staticmethod
    def ready(plan: StagePlan, completed: set[str]) -> StageNode | None:
        return next(
            (s for s in plan.stages if s.id not in completed and set(s.depends_on) <= completed),
            None,
        )
