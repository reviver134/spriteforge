"""Chatting with an agent: a message is a question (answered now, one Spark call with that
agent's own work as context) or a change (filed for the gated pipeline, never run freely)."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from spriteforge.config import BudgetConfig, Config
from spriteforge.core.cost import Budget, for_the_user
from spriteforge.core.state import Store
from spriteforge.stages.references import images

Intent = Literal["question", "change"]

# "can you make the bat purple?" asks for a change, though it ends with a question mark.
CHANGE_START = re.compile(
    r"^(please|can you|could you|would you|will you|i want|i'd like|i would like|let's|"
    r"make|change|add|remove|delete|use|set|turn|give|move|swap|replace|fix|try|keep|drop|"
    r"increase|decrease|raise|lower|speed|slow|shorten|lengthen|rename|recolou?r)\b"
)
QUESTION_START = re.compile(
    r"^(why|what|how|when|where|which|who|whose|is|are|was|were|does|do|did|has|have|"
    r"should|explain|tell me)\b"
)

CONTEXT_CHARS = 20000  # the agent's own artifacts sent with a question, newest first
TEXT_MIMES = {"application/json", "text/markdown", "text/plain"}


def classify(text: str) -> Intent:
    """Deterministic and free; the studio lets the user flip it when it guesses wrong."""
    t = " ".join(text.lower().split())
    if CHANGE_START.match(t):
        return "change"
    if t.endswith("?") or QUESTION_START.match(t):
        return "question"
    return "change"


class ChatAnswer(BaseModel):
    answer: str = Field(description="Your reply to the human, in plain words", max_length=4000)


def own_work(store: Store, run_id: str, agent: str, limit: int = CONTEXT_CHARS) -> str:
    """The agent's latest text artifacts (one version of each, Markdown over JSON when it wrote
    both), newest first, up to `limit` characters."""
    mine = {
        e.payload["id"]
        for e in store.events(run_id)
        if e.type == "artifact.created" and e.agent == agent
    }
    latest: dict[str, tuple[int, str, str]] = {}
    for a in store.artifacts(run_id):
        if a.id not in mine or a.mime not in TEXT_MIMES:
            continue
        family = re.sub(r"v\d+", "vN", a.name.rsplit(".", 1)[0])
        version = max(map(int, re.findall(r"v(\d+)", a.name)), default=0)
        rank = (version, a.mime == "text/markdown")
        if family not in latest or rank > latest[family][:2]:
            latest[family] = (*rank, a.name, a.data.decode("utf-8", "replace"))
    parts, used = [], 0
    for *_, name, text in sorted(latest.values(), key=lambda v: v[:2], reverse=True):
        block = f"### {name}\n{text}\n"
        if used + len(block) > limit:
            block = block[: max(0, limit - used)]
        if block:
            parts.append(block)
            used += len(block)
    return "\n".join(parts)


def history(store: Store, run_id: str, agent: str, turns: int = 6) -> str:
    lines = []
    for e in store.events(run_id):
        if e.type == "chat.user" and e.payload.get("to") == agent:
            lines.append("Human: " + e.payload.get("text", ""))
        elif e.type == "chat.agent" and e.agent == agent:
            lines.append(f"{agent}: " + e.payload.get("text", ""))
    return "\n".join(lines[-turns:])


def question_prompt(store: Store, run_id: str, agent: str, text: str) -> str:
    work = own_work(store, run_id, agent) or "(nothing yet: you have not run in this project)"
    talk = history(store, run_id, agent)
    return (
        "The human making this game asks you about your work. Answer from your work below, "
        "briefly and concretely; say so when the answer is not in it. Do not promise changes: "
        "the human asks for those separately and they go through review.\n\n"
        f"## Your work so far\n{work}\n\n"
        + (f"## The conversation so far\n{talk}\n\n" if talk else "")
        + f"## The question\n{text}"
    )


def answer_question(store: Store, run_id: str, agent: str, text: str, models=None) -> str:
    """One Spark call (offline: a stand-in answer) booked to this agent in the cost ledger.
    `models` (tests) replaces the run's own model access."""
    from spriteforge.game.pipeline import Models

    run = store.get_run(run_id)
    if models is None:
        cfg = Config.model_validate(run.config.get("settings") or {})
        budget = Budget(store, run_id, BudgetConfig.model_validate(run.config.get("budget", {})))
        models = Models(cfg, budget, mock=bool(run.config.get("mock")))
        models.references = images(store, run_id, [agent])
    fallback = ChatAnswer(
        answer=f"(Offline mock: no model is called.) I'm the {agent}. Ask again with a live "
        "Muse key for a real answer about my work."
    )
    with for_the_user():  # the human asked: no cap or stop request refuses it
        return models.ask(agent, question_prompt(store, run_id, agent, text), ChatAnswer,
                          fallback, "chat").answer  # fmt: skip
