"""Reserve capacity before dispatch; settle successful calls or release failed attempts."""

import json
from contextlib import contextmanager
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from spriteforge.config import BudgetConfig
from spriteforge.core.state import Store


class BudgetExceeded(RuntimeError):
    pass


class Usage(BaseModel):
    attempt_id: str
    provider: str
    model: str
    image: bool
    status: Literal["pending", "succeeded", "failed"] = "pending"
    estimated_cost: float = 0
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


def ledger(db, run_id):
    calls = {}
    for row in db.execute(
        "SELECT id,type,payload FROM events WHERE run_id=? "
        "AND type IN ('cost.reserved','cost.succeeded','cost.failed') ORDER BY id",
        (run_id,),
    ):
        payload = json.loads(row["payload"])
        if row["type"] == "cost.reserved":
            if "attempt_id" not in payload:
                payload.update(attempt_id=f"legacy:{row['id']}", status="succeeded")
            usage = Usage.model_validate(payload)
        else:
            usage = Usage.model_validate(payload["usage"])
        calls[usage.attempt_id] = usage
    return calls


def spark_tokens(raw):
    if not isinstance(raw, dict):
        return {}
    prompt = raw.get("prompt_tokens_details") or {}
    completion = raw.get("completion_tokens_details") or {}
    if not isinstance(prompt, dict) or not isinstance(completion, dict):
        return {}
    values = {
        "input_tokens": raw.get("prompt_tokens"),
        "output_tokens": raw.get("completion_tokens"),
        "cached_input_tokens": prompt.get("cached_tokens", 0),
        "reasoning_tokens": completion.get("reasoning_tokens", 0),
    }
    if any(type(v) is not int or v < 0 for v in values.values()):
        return {}
    if values["cached_input_tokens"] > values["input_tokens"]:
        return {}
    if values["reasoning_tokens"] > values["output_tokens"]:
        return {}
    return values


class Attempt:
    def __init__(self):
        self.usage = None


class Budget:
    """Pending reservations hold capacity; failed attempts consume neither cap nor cost."""

    def __init__(self, store: Store, run_id: str, config: BudgetConfig):
        self.store, self.run_id, self.config = store, run_id, config

    def reserve(self, provider: str, model: str, image: bool = False) -> str:
        usage = Usage(
            attempt_id=uuid4().hex,
            provider=provider,
            model=model,
            image=image,
            estimated_cost=self.config.image_cost if image else 0,
        )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [r for r in ledger(db, self.run_id).values() if r.status != "failed"]
            if (
                sum(r.image for r in rows) + image > self.config.max_image_calls
                or (
                    self.config.max_total_calls is not None
                    and len(rows) >= self.config.max_total_calls
                )
                or sum(r.provider == provider and r.model == model for r in rows)
                >= self.config.max_calls_per_model
                or sum(r.estimated_cost for r in rows) + usage.estimated_cost
                > self.config.max_estimated_cost + 1e-9
            ):
                raise BudgetExceeded(f"Call budget exceeded for {provider}/{model}")
            self.store._event(db, self.run_id, provider, "cost.reserved", usage.model_dump())
        return usage.attempt_id

    @contextmanager
    def attempt(self, provider, model, image=False):
        ident = self.reserve(provider, model, image)
        attempt = Attempt()
        try:
            yield attempt
        except BaseException as exc:
            self._settle(ident, "failed", reason=type(exc).__name__)
            raise
        else:
            self._settle(ident, "succeeded", raw=attempt.usage)

    def _settle(self, ident, status, raw=None, reason=None, legacy=False):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            usage = ledger(db, self.run_id)[ident]
            if usage.status == status:
                return
            if usage.status != "pending" and not (legacy and ident.startswith("legacy:")):
                raise ValueError("Attempt already settled")
            usage.status = status
            if status == "failed":
                usage.estimated_cost = 0
            elif usage.provider == "meta" and not usage.image:
                values = spark_tokens(raw)
                usage = usage.model_copy(update=values)
                if values:
                    cfg = self.config
                    usage.estimated_cost = (
                        (usage.input_tokens - usage.cached_input_tokens)
                        * cfg.spark_input_per_million
                        + usage.cached_input_tokens * cfg.spark_cached_input_per_million
                        + usage.output_tokens * cfg.spark_output_per_million
                    ) / 1_000_000
            self.store._event(
                db,
                self.run_id,
                usage.provider,
                "cost." + status,
                {
                    "attempt_id": ident,
                    "usage": usage.model_dump(),
                    "reason": reason,
                },
            )

    def correct_legacy_failure(self, event_id: int, reason: str):
        """Append a documented correction without rewriting the original audit event."""
        if not reason:
            raise ValueError("Historical corrections require evidence")
        self._settle(f"legacy:{event_id}", "failed", reason=reason, legacy=True)
