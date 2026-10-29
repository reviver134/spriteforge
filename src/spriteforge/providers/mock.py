import hashlib
import io
import json
from collections import deque

from PIL import Image, ImageDraw

from spriteforge.core.cost import Budget

from .base import ChatRequest, ChatResult, ImageRequest, ImageResult


class MockImageProvider:
    def __init__(self, budget: Budget):
        self.budget = budget

    def generate(self, request: ImageRequest) -> ImageResult:
        with self.budget.attempt("mock", "synthetic-image", image=True):
            digest = hashlib.sha256(request.prompt.encode() + b"".join(request.references)).digest()
            image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (7, 8, 24, 25),
                radius=8 if "round" in request.prompt.casefold() else 0,
                fill=tuple(digest[:3]) + (255,),
                outline="#181825",
                width=2,
            )
            draw.rectangle((11, 13, 12, 15), fill="white")
            draw.rectangle((19, 13, 20, 15), fill="white")
            if "concept" in request.prompt.casefold():
                # Synthetic full scene exercises the same preview/revision path offline.
                scene = Image.new("RGB", (320, 180), tuple(digest[3:6]))
                painter = ImageDraw.Draw(scene)
                painter.rectangle((0, 110, 320, 180), fill="#454452")
                for x in range(0, 320, 32):
                    painter.rectangle((x, 85, x + 20, 110), fill="#848078")
                scene.paste(image, (130, 100), image)
                for x in (50, 210, 270):
                    painter.rectangle((x, 120, x + 16, 136), fill=tuple(digest[6:9]))
                    painter.rectangle((x + 4, 123, x + 6, 125), fill="white")
                painter.rectangle((8, 8, 94, 20), fill="#181825")
                painter.rectangle((12, 12, 70, 16), fill="#E75B65")
                image = scene
            image = image.resize(request.size, Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return ImageResult(data=buffer.getvalue(), metadata={"provider": "mock"})

    def edit(self, request: ImageRequest) -> ImageResult:
        if not request.references:
            raise ValueError("edit requires 1–10 reference images")
        return self.generate(request)


class MockLLMProvider:
    def __init__(self, budget: Budget, responses: list[dict | ChatResult] | None = None):
        self.budget = budget
        self.responses = deque(responses or [{"message": "Mock planning complete"}])

    def chat(self, request: ChatRequest) -> ChatResult:
        with self.budget.attempt("mock", "scripted-llm"):
            if not self.responses:
                raise RuntimeError("Mock script exhausted")
            response = self.responses.popleft()
            return (
                response
                if isinstance(response, ChatResult)
                else ChatResult(content=json.dumps(response), metadata={"provider": "mock"})
            )
