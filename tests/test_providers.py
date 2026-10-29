import base64
import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from spriteforge.providers.base import ChatRequest, ImageRequest, Message, chat_json
from spriteforge.providers.http import ProviderError
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider
from spriteforge.providers.muse_image import MuseImageProvider
from spriteforge.providers.muse_spark import MuseSparkProvider


class Answer(BaseModel):
    value: int


def test_intake_schemas_meet_meta_strict_contract(context, monkeypatch):
    from spriteforge.stages.intake import Options, Questions, propose

    config, _, _, budget = context
    monkeypatch.setenv("META_API_KEY", "fake-test-key")
    responses = [
        {"questions": ["Where?", "How?"]},
        {
            "options": [
                {"name": name, "description": "Chunky pixels", "colors": ["#000000"] * 4}
                for name in ["Dark", "Bright"]
            ]
        },
    ]

    def handler(request):
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        for obj in [schema, *schema.get("$defs", {}).values()]:
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(responses.pop(0))}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = MuseSparkProvider(config.llm, budget, client)
        propose(provider, "Game Designer", "Ask followups", Questions)
        propose(provider, "Art Director", "Propose styles", Options)
    assert not responses


def test_mock_images_and_reference_limit(context):
    _, _, _, budget = context
    provider = MockImageProvider(budget)
    request = ImageRequest(prompt="slime", size=(32, 32))
    assert provider.generate(request).data == provider.generate(request).data
    with pytest.raises(ValidationError):
        ImageRequest(prompt="bad", references=[b"x"] * 11)
    with pytest.raises(ValueError):
        provider.edit(request)


def test_json_validation_retry_once(context):
    _, store, run, budget = context
    request = ChatRequest(messages=[Message(role="user", content="answer")])
    provider = MockLLMProvider(budget, [{"value": "bad"}, {"value": 7}])
    assert chat_json(provider, request, Answer).value == 7
    assert len(request.messages) == 1
    assert len(store.events(run.id)) == 5
    with pytest.raises(ValidationError):
        chat_json(MockLLMProvider(budget, [{}, {}, {"value": 1}]), request, Answer)


def test_live_key_guard(context, monkeypatch):
    config, store, run, budget = context
    monkeypatch.delenv("META_API_KEY", raising=False)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("network called"))
    ) as client:
        image = MuseImageProvider(config.image, budget, client)
        with pytest.raises(ProviderError, match="META_API_KEY"):
            image.generate(ImageRequest(prompt="slime"))
        with pytest.raises(ProviderError, match="META_API_KEY"):
            MuseSparkProvider(config.llm, budget, client).chat(ChatRequest(messages=[]))
    assert len([e for e in store.events(run.id) if e.type == "cost.failed"]) == 2


def test_meta_request_contracts(context, monkeypatch):
    config, _, _, budget = context
    monkeypatch.setenv("META_API_KEY", "fake-test-key")
    png = MockImageProvider(budget).generate(ImageRequest(prompt="test", size=(32, 32))).data
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        assert request.headers["authorization"] == "Bearer fake-test-key"
        if "images" in request.url.path:
            return httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]}
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "inspect", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = MuseImageProvider(config.image, budget, client)
        assert provider.generate(ImageRequest(prompt="hero")).data == png
        provider.edit(ImageRequest(prompt="pose", references=[png] * 10))
        result = MuseSparkProvider(config.llm, budget, client).chat(
            ChatRequest(
                messages=[Message(role="user", content="inspect", images=[png])],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "inspect", "parameters": {"type": "object"}},
                    }
                ],
                json_schema=Answer.model_json_schema(),
            )
        )
        assert result.tool_calls[0]["function"]["name"] == "inspect"
    assert seen[0][0] == config.image.generate_path
    assert len(seen[1][1]["images"]) == 10
    assert seen[2][1]["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert seen[2][1]["response_format"]["type"] == "json_schema"


def test_raw_error_redacted(context, monkeypatch, caplog):
    config, _, _, budget = context
    monkeypatch.setenv("META_API_KEY", "fake-private-key")
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(400, text="raw validation error echoed fake-private-key")
            )
        ) as client,
        pytest.raises(ProviderError) as caught,
    ):
        MuseImageProvider(config.image, budget, client).generate(ImageRequest(prompt="test"))
    assert "HTTP 400: raw validation error" in str(caught.value)
    assert "fake-private-key" not in str(caught.value) + caplog.text
