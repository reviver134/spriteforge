from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ImageRequest(BaseModel):
    prompt: str
    size: tuple[int, int] = (1024, 1024)
    reasoning_strength: Literal["low", "high"] = "low"
    references: list[bytes] = Field(default_factory=list, max_length=10)


class ImageResult(BaseModel):
    data: bytes
    mime: str = "image/png"
    metadata: dict = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    images: list[bytes] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    messages: list[Message]
    tools: list[dict] = Field(default_factory=list)
    json_schema: dict | None = None
    # Spark `reasoning_effort` (None: the model chooses) and `prompt_cache_key`, which routes
    # requests sharing a prefix to the same cache.
    reasoning_effort: str | None = None
    cache_key: str | None = None


class ChatResult(BaseModel):
    content: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ImageProvider(Protocol):
    def generate(self, request: ImageRequest) -> ImageResult: ...
    def edit(self, request: ImageRequest) -> ImageResult: ...


class LLMProvider(Protocol):
    def chat(self, request: ChatRequest) -> ChatResult: ...


def chat_json(provider: LLMProvider, request: ChatRequest, schema: type[BaseModel]) -> BaseModel:
    """Validate every structured answer; one corrective retry only."""
    from pydantic import ValidationError

    request = request.model_copy(deep=True)
    request.json_schema = schema.model_json_schema()
    for attempt in range(2):
        result = provider.chat(request)
        try:
            return schema.model_validate_json(result.content or "")
        except ValidationError as exc:
            if attempt:
                raise
            request.messages.append(Message(role="assistant", content=result.content or ""))
            request.messages.append(Message(role="user", content=f"Correct JSON errors: {exc}"))
    raise AssertionError("unreachable")
