import os

import httpx

from spriteforge.config import LLMConfig
from spriteforge.core.cost import Budget

from .base import ChatRequest, ChatResult
from .http import MetaHTTP, ProviderError
from .muse_image import data_url


class MuseSparkProvider(MetaHTTP):
    def __init__(self, config: LLMConfig, budget: Budget, client: httpx.Client | None = None):
        super().__init__(str(config.base_url), client)
        self.config, self.budget = config, budget

    def chat(self, request: ChatRequest) -> ChatResult:
        with self.budget.attempt("meta", self.config.model) as attempt:
            if not os.environ.get("META_API_KEY"):
                raise ProviderError("META_API_KEY is required for live Meta providers")
            messages = []
            for message in request.messages:
                value = message.model_dump(exclude={"images"}, exclude_none=True)
                if not value["tool_calls"]:
                    del value["tool_calls"]
                if message.images:
                    value["content"] = [{"type": "text", "text": message.content or ""}] + [
                        {"type": "image_url", "image_url": {"url": data_url(image)}}
                        for image in message.images
                    ]
                messages.append(value)
            body = {"model": self.config.model, "messages": messages}
            if request.tools:
                body["tools"] = request.tools
            if request.json_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "result",
                        "strict": True,
                        "schema": request.json_schema,
                    },
                }
            result = self.post(self.config.chat_path, body)
            attempt.usage = result.get("usage")
            try:
                message = result["choices"][0]["message"]
                return ChatResult(
                    content=message.get("content"),
                    tool_calls=message.get("tool_calls", []),
                    metadata={"model": self.config.model, "usage": result.get("usage", {})},
                )
            except (KeyError, IndexError, TypeError):
                raise ProviderError("Meta returned an invalid chat response") from None
