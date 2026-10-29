import base64
import io

import httpx
import pytest
from PIL import Image

from spriteforge.config import BudgetConfig
from spriteforge.core.cost import Budget, BudgetExceeded
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.core.usage import usage_report
from spriteforge.providers.base import ChatRequest, ImageRequest
from spriteforge.providers.http import ProviderError
from spriteforge.providers.muse_image import MuseImageProvider
from spriteforge.providers.muse_spark import MuseSparkProvider


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("failure", ["network", "blocked", "http", "invalid"])
def test_failed_attempt_releases_all_caps_and_cost(context, monkeypatch, image, failure):
    cfg, store, run, _ = context
    budget = Budget(
        store,
        run.id,
        BudgetConfig(
            max_image_calls=1, max_total_calls=1, max_calls_per_model=1, max_estimated_cost=0.01
        ),
    )
    monkeypatch.setenv("META_API_KEY", "test-key")
    png = io.BytesIO()
    Image.new("RGB", (16, 16)).save(png, "PNG")
    tries = 0

    def handler(request):
        nonlocal tries
        tries += 1
        if tries == 1:
            if failure == "network":
                raise httpx.ConnectError("network unavailable", request=request)
            if failure == "blocked":
                raise PermissionError(10013, "network blocked")
            if failure == "http":
                return httpx.Response(503)
            return httpx.Response(200, json={"invalid": True})
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}],
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = (
            MuseImageProvider(cfg.image, budget, client)
            if image
            else MuseSparkProvider(cfg.llm, budget, client)
        )
        call = lambda: (
            provider.generate(ImageRequest(prompt="hero"))
            if image
            else provider.chat(ChatRequest(messages=[]))
        )
        with pytest.raises((ProviderError, PermissionError)):
            call()
        first = usage_report(store, run.id)
        assert first.failed_attempts == 1
        assert first.successful_calls == first.in_flight_calls == 0
        assert first.estimated_total_cost == 0
        call()  # A single-call cap must still permit retry after a failed attempt.
        with pytest.raises(BudgetExceeded):
            call()
    report = usage_report(Store(store.path), run.id)
    assert report.successful_calls == 1 and report.failed_attempts == 1
    assert report.estimated_total_cost == pytest.approx(0.01 if image else 0.0001675)
    assert len([e for e in store.events(run.id) if e.type == "cost.failed"]) == 1


def test_spark_usage_cached_price_persists_without_exposing_tokens(context, monkeypatch):
    cfg, store, run, budget = context
    monkeypatch.setenv("META_API_KEY", "test-secret")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 200,
                        "prompt_tokens_details": {"cached_tokens": 400},
                        "completion_tokens_details": {"reasoning_tokens": 50},
                    },
                },
            )
        )
    ) as client:
        MuseSparkProvider(cfg.llm, budget, client).chat(ChatRequest(messages=[]))
    report = usage_report(Store(store.path), run.id)
    assert report.spark.input_tokens == 1000
    assert report.spark.output_tokens == 200
    assert report.spark.cached_input_tokens == 400
    assert report.spark.estimated_cost == pytest.approx(0.00166)
    assert report.estimated_total_cost == report.spark.estimated_cost
    assert redact({"input_tokens": "secret", "access_token": 123, "input_tokens_bad": 1}) == {
        "input_tokens": "[REDACTED]",
        "access_token": "[REDACTED]",
        "input_tokens_bad": "[REDACTED]",
    }
    assert "test-secret" not in str(store.events(run.id))
    # Completed token charges participate in the next reservation's cost cap.
    low = Budget(store, run.id, BudgetConfig(max_estimated_cost=0.001))
    with pytest.raises(BudgetExceeded):
        low.reserve("meta", cfg.llm.model)


def test_unknown_historical_usage_and_append_only_failed_correction(context):
    _, store, run, budget = context
    for image in [True, False, False]:
        store.append_event(
            run.id,
            "meta",
            "cost.reserved",
            {
                "provider": "meta",
                "model": "image" if image else "spark",
                "image": image,
                "estimated_cost": 0.01 if image else 0,
            },
        )
    blocked = store.events(run.id)[-1]
    budget.correct_legacy_failure(blocked.id, "Observed network block before dispatch")
    budget.correct_legacy_failure(blocked.id, "Idempotent correction")
    report = usage_report(store, run.id)
    assert report.successful_calls == 2 and report.failed_attempts == 1
    assert report.spark.missing_usage_calls == 1
    assert report.spark.input_tokens is report.spark.estimated_cost is None
    assert report.estimated_total_cost is None
    assert report.known_estimated_cost == 0.01
    assert next(e for e in store.events(run.id) if e.id == blocked.id) == blocked
