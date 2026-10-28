"""Durable human gates. No timeout, in-memory waiter, or implied approval."""

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from spriteforge.agents.models import Changes
from spriteforge.core.security import redact
from spriteforge.core.state import Store


class Feedback(BaseModel):
    action: Literal["approve", "request_changes", "abort"]
    tags: list[Literal["art", "gameplay", "controls", "difficulty", "bug"]] = Field(
        default_factory=list
    )
    text: str = Field(default="", max_length=10000)
    form: dict = Field(default_factory=dict)


class Gate(BaseModel):
    id: str
    title: str
    artifacts_to_show: list[str] = Field(default_factory=list)
    form_schema: dict = Field(default_factory=lambda: Feedback.model_json_schema())
    stage: str = ""
    kind: Literal["review", "confirm_changes"] = "review"
    parent_id: str | None = None
    changes: Changes | None = None
    status: Literal["pending", "approved", "changes_requested", "aborted"] = "pending"
    response: Feedback | None = None


class Gates:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS gates (
                run_id TEXT REFERENCES runs(id), id TEXT, body TEXT,
                PRIMARY KEY(run_id,id))""")

    def all(self, run_id: str) -> list[Gate]:
        with self.store.connect() as db:
            return [
                Gate.model_validate_json(r[0])
                for r in db.execute(
                    "SELECT body FROM gates WHERE run_id=? ORDER BY rowid", (run_id,)
                )
            ]

    def pending(self, run_id: str) -> Gate | None:
        return next((g for g in reversed(self.all(run_id)) if g.status == "pending"), None)

    def open(self, run_id: str, gate: Gate) -> Gate:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            status = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if status is None or status[0] in {"completed", "aborted"}:
                raise ValueError("Run cannot open a gate")
            existing = db.execute("SELECT body FROM gates WHERE run_id=?", (run_id,)).fetchall()
            if any(Gate.model_validate_json(r[0]).status == "pending" for r in existing):
                raise ValueError("A gate is already pending")
            db.execute("INSERT INTO gates VALUES(?,?,?)", (run_id, gate.id, gate.model_dump_json()))
            db.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
            self.store._event(db, run_id, "Director", "gate.opened", gate.model_dump(mode="json"))
        return gate

    def respond(self, run_id: str, ident: str, feedback: Feedback) -> Gate:
        # Validate custom questionnaire fields without executing arbitrary schema code.
        from .forms import validate_form

        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM gates WHERE run_id=? AND id=?", (run_id, ident)
            ).fetchone()
            if row is None:
                raise KeyError(ident)
            gate = Gate.model_validate_json(row[0])
            if gate.status != "pending":
                raise ValueError("Gate already answered")
            if gate.kind == "confirm_changes" and feedback.action == "request_changes":
                raise ValueError("Approve or abort the parsed requests")
            if feedback.action == "request_changes" and not feedback.text.strip():
                raise ValueError("Describe the requested changes")
            if feedback.action == "approve":
                validate_form(gate.form_schema, feedback.form)
            gate.response = Feedback.model_validate(redact(feedback.model_dump()))
            gate.status = {
                "approve": "approved",
                "request_changes": "changes_requested",
                "abort": "aborted",
            }[feedback.action]
            db.execute(
                "UPDATE gates SET body=? WHERE run_id=? AND id=?",
                (gate.model_dump_json(), run_id, ident),
            )
            if feedback.action == "abort":
                db.execute("UPDATE runs SET status='aborted' WHERE id=?", (run_id,))
            else:
                db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
            self.store._event(db, run_id, "Human", "gate.responded", gate.model_dump(mode="json"))
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?)",
                (
                    uuid4().hex,
                    run_id,
                    f"feedback/{gate.id}.json",
                    "application/json",
                    json.dumps(redact(gate.model_dump(mode="json"))).encode(),
                ),
            )
        return gate


class ScriptedHuman:
    """Explicit gate answers for offline acceptance tests, never used by the UI."""

    def __init__(self, answers: list[Feedback]):
        self.answers = iter(answers)

    def answer(self, gates: Gates, run_id: str) -> Gate:
        gate = gates.pending(run_id)
        if gate is None:
            raise ValueError("No pending gate")
        return gates.respond(run_id, gate.id, next(self.answers))


def new_gate(stage: str, title: str, artifacts: list[str], **kwargs) -> Gate:
    return Gate(id=uuid4().hex, stage=stage, title=title, artifacts_to_show=artifacts, **kwargs)
