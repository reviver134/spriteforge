"""Report successful usage, failed attempts, and unknown historical usage separately."""

from pydantic import BaseModel

from spriteforge.config import BudgetConfig

from .cost import ledger


class SparkUsage(BaseModel):
    calls: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    known_input_tokens: int
    known_output_tokens: int
    known_cached_input_tokens: int
    missing_usage_calls: int
    estimated_cost: float | None
    known_estimated_cost: float


class RunUsage(BaseModel):
    successful_calls: int
    failed_attempts: int
    in_flight_calls: int
    image_calls: int
    estimated_image_cost: float
    spark: SparkUsage
    estimated_total_cost: float | None
    known_estimated_cost: float
    cap: int
    total_cap: int | None


def usage_report(store, run_id):
    cfg = BudgetConfig.model_validate(store.get_run(run_id).config.get("budget", {}))
    with store.connect() as db:
        rows = list(ledger(db, run_id).values())
    done = [r for r in rows if r.status == "succeeded"]
    images = [r for r in done if r.image]
    spark = [r for r in done if r.provider == "meta" and not r.image]
    missing = sum(r.input_tokens is None for r in spark)
    counts = {
        key: sum(getattr(r, key) or 0 for r in spark)
        for key in ("input_tokens", "output_tokens", "cached_input_tokens")
    }
    image_cost = sum(r.estimated_cost for r in images)
    spark_cost = sum(r.estimated_cost for r in spark)
    return RunUsage(
        successful_calls=len(done),
        failed_attempts=sum(r.status == "failed" for r in rows),
        in_flight_calls=sum(r.status == "pending" for r in rows),
        image_calls=len(images),
        estimated_image_cost=image_cost,
        spark=SparkUsage(
            calls=len(spark),
            missing_usage_calls=missing,
            **{k: None if missing else v for k, v in counts.items()},
            **{"known_" + k: v for k, v in counts.items()},
            estimated_cost=None if missing else spark_cost,
            known_estimated_cost=spark_cost,
        ),
        estimated_total_cost=None if missing else image_cost + spark_cost,
        known_estimated_cost=image_cost + spark_cost,
        cap=cfg.max_image_calls,
        total_cap=cfg.max_total_calls,
    )
