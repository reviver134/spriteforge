import base64
import io
import os

import httpx
from PIL import Image

from spriteforge.config import ImageConfig
from spriteforge.core.cost import Budget

from .base import ImageRequest, ImageResult
from .http import MetaHTTP, ProviderError


def data_url(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME.get(image.format, "image/png")
    return f"data:{mime};base64," + base64.b64encode(data).decode()


class MuseImageProvider(MetaHTTP):
    def __init__(self, config: ImageConfig, budget: Budget, client: httpx.Client | None = None):
        super().__init__(str(config.base_url), client)
        self.config, self.budget = config, budget

    def _call(self, request: ImageRequest, edit: bool) -> ImageResult:
        if not os.environ.get("META_API_KEY"):
            raise ProviderError("META_API_KEY is required for live Meta providers")
        body = {
            "model": self.config.model,
            "prompt": request.prompt,
            "n": 1,
            "size": "x".join(map(str, request.size)),
            "response_format": "b64_json",
            "output_format": "png",
            "reasoning_strength": request.reasoning_strength,
        }
        if edit:
            body["images"] = [{"image_url": data_url(ref)} for ref in request.references]
        self.budget.reserve("meta", self.config.model, image=True)
        result = self.post(self.config.edit_path if edit else self.config.generate_path, body)
        try:
            data = base64.b64decode(result["data"][0]["b64_json"], validate=True)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except (KeyError, IndexError, ValueError, OSError):
            raise ProviderError("Meta returned invalid image data") from None
        return ImageResult(
            data=data, metadata={"model": self.config.model, "usage": result.get("usage", {})}
        )

    def generate(self, request: ImageRequest) -> ImageResult:
        if request.references:
            return self.edit(request)
        return self._call(request, False)

    def edit(self, request: ImageRequest) -> ImageResult:
        if not request.references:
            raise ValueError("edit requires 1–10 reference images")
        return self._call(request, True)
