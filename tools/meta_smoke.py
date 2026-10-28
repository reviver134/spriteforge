"""Explicit, bounded live verification. Run from the repo root; never prints credentials."""

import io
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget, BudgetExceeded
from spriteforge.core.credentials import load_credentials
from spriteforge.core.security import redact
from spriteforge.core.state import Artifact, Store
from spriteforge.providers.base import ChatRequest, ImageRequest, Message, chat_json
from spriteforge.providers.http import ProviderError
from spriteforge.providers.muse_image import MuseImageProvider
from spriteforge.providers.muse_spark import MuseSparkProvider


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_name: str


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    store = Store(root / "meta-smoke.sqlite3")
    cap = BudgetConfig(
        **{
            **cfg.budget.model_dump(),
            "max_image_calls": min(2, cfg.budget.max_image_calls),
            "max_calls_per_model": min(4, cfg.budget.max_calls_per_model),
        }
    )
    run = store.create_run({"workflow": "provider-smoke", "budget": cap.model_dump()})
    budget = Budget(store, run.id, cap)
    image = MuseImageProvider(cfg.image, budget)
    llm = MuseSparkProvider(cfg.llm, budget)
    try:
        answer = chat_json(
            llm,
            ChatRequest(
                messages=[
                    Message(role="user", content='Return JSON with object_name set to "slime".')
                ]
            ),
            Check,
        )
        store.advance(run.id, 0, "Spark structured JSON verified")
        print(f"Spark JSON: PASS ({cfg.llm.model})", flush=True)
        result = image.generate(
            ImageRequest(
                prompt=(
                    "A single small green slime monster, centered full-body, chunky pixel art, "
                    "dark outline, flat magenta background, no text, no gradients."
                )
            )
        )
        store.advance(
            run.id,
            1,
            "Muse Image generation verified",
            Artifact(id=uuid4().hex, run_id=run.id, name="slime.png", data=result.data),
        )
        with Image.open(io.BytesIO(result.data)) as png:
            print(f"Image generation: PASS ({png.size[0]}x{png.size[1]})", flush=True)
        edited = image.edit(
            ImageRequest(
                prompt=(
                    "Keep the same green slime and pixel art style. Only change its pose to a small hop. "
                    "Keep the flat magenta background."
                ),
                references=[result.data],
            )
        )
        store.advance(
            run.id,
            2,
            "Muse Image edit verified",
            Artifact(id=uuid4().hex, run_id=run.id, name="slime-hop.png", data=edited.data),
        )
        print("Image edit: PASS", flush=True)
        answer = chat_json(
            llm,
            ChatRequest(
                messages=[
                    Message(
                        role="user",
                        content="Name the main object in this image in object_name.",
                        images=[edited.data],
                    )
                ]
            ),
            Check,
        )
        store.advance(run.id, 3, "Spark vision verified", final=True)
        print(f"Spark vision: PASS ({redact(answer.object_name)})", flush=True)
        output = root / "runs" / run.id
        output.mkdir(parents=True, exist_ok=True)
        for artifact in store.artifacts(run.id):
            (output / artifact.name).write_bytes(artifact.data)
        print(f"Images: {output}", flush=True)
        return 0
    except (ProviderError, BudgetExceeded, ValueError) as exc:
        store.fail(run.id, str(exc))
        print(redact(str(exc)), flush=True)
        return 1
    finally:
        image.close()
        llm.close()


if __name__ == "__main__":
    raise SystemExit(main())
