"""Persist routing and observed usage across human confirmation/restarts."""

import json

from pydantic import Field, ValidationError

from spriteforge.core.security import redact

from .router import (
    Catalog,
    Contract,
    Decision,
    Selection,
    filter_selection,
    load_selected,
    routing_request,
)


class RoutingState(Contract):
    role: dict
    goal: str
    decision: Decision
    bodies: dict[str, str]
    used_skills: list[str] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    outcome_status: str | None = None


class RoutingStore:
    def __init__(self, store, run_id, task_id):
        self.store, self.run_id, self.task_id = store, run_id, task_id
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS agent_routing "
                "(run_id TEXT, task_id TEXT, body TEXT, PRIMARY KEY(run_id,task_id))"
            )
            row = db.execute(
                "SELECT body FROM agent_routing WHERE run_id=? AND task_id=?", (run_id, task_id)
            ).fetchone()
        self.state = RoutingState.model_validate_json(row[0]) if row else None

    def save(self, event=None, payload=None):
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO agent_routing VALUES(?,?,?)",
                (self.run_id, self.task_id, json.dumps(redact(self.state.model_dump()))),
            )
            if event:
                self.store._event(db, self.run_id, self.state.role["name"], event, payload)

    async def prepare(
        self, role, goal, catalog: Catalog, config, provider, invoke, skills, supplied
    ):
        if self.state:
            if self.state.role != role.model_dump() or self.state.goal != redact(goal):
                raise PermissionError("Role or goal changed while step was paused")
            decision = self.state.decision
            if set(decision.selected_skills) - {c.name for c in catalog.skills} or set(
                decision.selected_tools
            ) - {c.name for c in catalog.tools}:
                raise PermissionError("Selected capability permission was revoked")
            return self.state
        mode = "model"
        if config.enabled:
            response = await invoke(
                provider.chat, routing_request(goal, catalog, role.required_skills, config)
            )
            try:
                proposal = Selection.model_validate_json(response.content or "")
            except ValidationError:
                # Fail closed: pinned skills only, no tools, no retry/call amplification.
                proposal = Selection(
                    selected_skills=[], selected_tools=[], reasons={}, confidence=0
                )
                mode = "invalid-response"
        else:
            proposal = Selection(
                selected_skills=[c.name for c in catalog.skills],
                selected_tools=[c.name for c in catalog.tools],
                reasons={},
                confidence=1,
            )
            mode = "disabled"
        decision = filter_selection(proposal, catalog, role.required_skills, config)
        decision.mode = mode
        bodies = load_selected(decision, role, skills, supplied, config.token_budget)
        self.state = RoutingState(
            role=role.model_dump(),
            goal=redact(goal),
            decision=decision,
            bodies=bodies,
            used_skills=list(role.required_skills),
        )
        self.save(
            "router.decision",
            {
                "task_id": self.task_id,
                "candidates": catalog.model_dump(),
                "selected": {"skills": decision.selected_skills, "tools": decision.selected_tools},
                "reasons": decision.reasons,
                "confidence": decision.confidence,
                "dropped": decision.dropped,
                "mode": decision.mode,
            },
        )
        return self.state

    def used(self, kind, name):
        values = getattr(self.state, "used_" + kind)
        if name not in values:
            values.append(name)
            self.save()

    def outcome(self, status):
        if not self.state or self.state.outcome_status == status:
            return
        decision = self.state.decision
        used = {"skills": self.state.used_skills, "tools": self.state.used_tools}
        self.state.outcome_status = status
        self.save(
            "router.outcome",
            {
                "task_id": self.task_id,
                "status": status,
                "used": used,
                "ignored": {
                    "skills": [n for n in decision.selected_skills if n not in used["skills"]],
                    "tools": [n for n in decision.selected_tools if n not in used["tools"]],
                },
                "usage_definition": "Skills loaded into model context; tools successfully executed.",
            },
        )
