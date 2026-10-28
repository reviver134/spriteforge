"""Two live image calls, no retries; durable redacted wire metadata."""
import json
import sys
from pathlib import Path

import httpx

from spriteforge.config import BudgetConfig, load_config
from spriteforge.core.cost import Budget
from spriteforge.core.credentials import load_credentials
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.muse_image import MuseImageProvider


def main():
    load_credentials()
    root = Path("workspace/milestone5-smoke")
    root.mkdir(parents=True, exist_ok=True)
    # Refuse accidental replay: the caller must review any existing attempt first.
    marker = root / "started.json"
    with marker.open("x") as f:
        json.dump({"maximum_calls": 2}, f)
    cfg = load_config()
    store = Store(root / "smoke.sqlite3")
    cap = BudgetConfig(max_image_calls=2)
    run = store.create_run({"budget": cap.model_dump()})
    records = []

    def capture(response):
        response.read()
        request = json.loads(response.request.content)
        if "images" in request:
            request["images"] = [{"image_url": "<reference image omitted>"} for _ in request["images"]]
        try:
            body = response.json()
            for item in body.get("data", []):
                if "b64_json" in item:
                    item["b64_json"] = "<image bytes saved separately>"
        except ValueError:
            body = response.text
        records.append(redact({"url": str(response.request.url), "parameters": request,
                               "status": response.status_code, "response": body}))
        (root / "responses.json").write_text(json.dumps(records, indent=2), encoding="utf-8")

    with httpx.Client(timeout=180, event_hooks={"response": [capture]}) as client:
        provider = MuseImageProvider(cfg.image, Budget(store, run.id, cap), client)
        previous = None
        for name, prompt in [
            ("sprite", "48x48-class pixel sprite: one centered green slime, chunky pixels, "
             "fits within approximately 40x40 logical pixels. Flat solid magenta background. "
             "No text, no watermark. Render at 1024x1024."),
            ("pose", "Only change the pose of this same slime to a small hop. Preserve all "
             "colors, chunky pixels, scale, character design and flat solid magenta background. "
             "No text, no watermark."),
        ]:
            request = ImageRequest(prompt=prompt, size=(1024, 1024),
                                   references=[previous] if previous else [])
            try:
                result = provider.edit(request) if previous else provider.generate(request)
            except Exception as exc:
                error = redact(str(exc))
                (root / "error.txt").write_text(error, encoding="utf-8")
                print(error, flush=True)
                return 1
            previous = result.data
            source = root / f"{name}.png"
            source.write_bytes(previous)
            sys.path.insert(0, str(Path("scripts").resolve()))
            from process_image import process
            process(source, 48, "auto", False, root / name)
    print(f"Metadata: {root / 'responses.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
