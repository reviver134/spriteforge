import os

import httpx

from spriteforge.core.security import redact


class ProviderError(RuntimeError):
    """HTTP status and raw response body, with credentials scrubbed."""


class MetaHTTP:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base_url = str(base_url).rstrip("/")
        # A Programmer repair with high reasoning took over 180 s live (2026-11-02, L2.3).
        self.client = client or httpx.Client(timeout=httpx.Timeout(420, connect=30))
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def post(self, path: str, body: dict) -> dict:
        key = os.environ.get("META_API_KEY")
        if not key:
            raise ProviderError("META_API_KEY is required for live Meta providers")
        try:
            response = self.client.post(
                self.base_url + path, json=body, headers={"Authorization": f"Bearer {key}"}
            )
        except httpx.HTTPError as exc:
            raise ProviderError(redact(str(exc))) from None
        if response.is_error:
            raise ProviderError(redact(f"Meta HTTP {response.status_code}: {response.text}"))
        try:
            return response.json()
        except ValueError:
            raise ProviderError(redact(f"Invalid Meta JSON: {response.text}")) from None
