"""Muse Image request cache: `workspace/cache/muse-image/<sha256>.png`.

The key covers the provider identity (model, base URL, endpoint), prompt, size, reasoning
strength, `n` and the SHA-256 of every reference image, the same scheme as the older
production cache. A hit makes no image call and costs nothing. References are part of the
key, so a cached image can only answer the exact request (and the exact pixels) it was made
from; the AI-allowed rule is decided before the request exists and cannot be crossed here.
"""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from spriteforge.providers.base import ImageRequest


class ImageCache:
    def __init__(self, root: Path, identity: dict[str, str]):
        """`identity`: {"model", "base_url", "generate", "edit"} (the two endpoint paths)."""
        self.root, self.identity = Path(root), identity

    def key(self, request: ImageRequest, edit: bool) -> str:
        edit = edit or bool(request.references)  # the provider sends these to the edit path
        payload = {
            "model": self.identity["model"],
            "base_url": self.identity["base_url"],
            "endpoint": self.identity["edit" if edit else "generate"],
            "prompt": request.prompt,
            "size": list(request.size),
            "reasoning_strength": request.reasoning_strength,
            "n": 1,
            "references": [hashlib.sha256(r).hexdigest() for r in request.references],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def path(self, request: ImageRequest, edit: bool) -> Path:
        return self.root / f"{self.key(request, edit)}.png"

    def get(self, request: ImageRequest, edit: bool) -> bytes | None:
        path = self.path(request, edit)
        try:
            return path.read_bytes() or None
        except OSError:
            return None

    def put(self, request: ImageRequest, edit: bool, data: bytes) -> bool:
        """Atomic write (temp file + replace), so parallel workers never see half a PNG.
        Best effort: a write that fails (disk full, a path over Windows' 260 characters) only
        means no cache entry; it never fails the image call that produced `data`."""
        path = self.path(request, edit)
        tmp = path.parent / f".{uuid4().hex[:12]}.tmp"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.replace(tmp, path)
            return True
        except OSError:
            tmp.unlink(missing_ok=True)
            return False
