from pydantic import BaseModel

from spriteforge.config import BudgetConfig
from spriteforge.core.state import Store


class BudgetExceeded(RuntimeError):
    pass


class Usage(BaseModel):
    provider: str
    model: str
    image: bool
    estimated_cost: float


class Budget:
    """Reserve before dispatch; failed calls count. Journal survives process restarts."""

    def __init__(self, store: Store, run_id: str, config: BudgetConfig):
        self.store, self.run_id, self.config = store, run_id, config

    def reserve(self, provider: str, model: str, image: bool = False) -> None:
        usage = Usage(
            provider=provider,
            model=model,
            image=image,
            estimated_cost=self.config.image_cost if image else 0,
        )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            import json

            rows = [
                Usage.model_validate(json.loads(r[0]))
                for r in db.execute(
                    "SELECT payload FROM events WHERE run_id=? AND type='cost.reserved'",
                    (self.run_id,),
                )
            ]
            if (
                sum(r.image for r in rows) + image > self.config.max_image_calls
                or sum(r.provider == provider and r.model == model for r in rows)
                >= self.config.max_calls_per_model
                or sum(r.estimated_cost for r in rows) + usage.estimated_cost
                > self.config.max_estimated_cost + 1e-9
            ):
                raise BudgetExceeded(f"Call budget exceeded for {provider}/{model}")
            self.store._event(db, self.run_id, provider, "cost.reserved", usage.model_dump())
