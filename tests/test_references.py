"""Phase 4 of the studio (docs/ui-plan.md): reference images for the agents."""

import io

import pytest
from PIL import Image
from test_art_spec import ScriptedImages, hero_only
from test_chat_changes import answer_until

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import MuseOptions, keyword_plan, make_art
from spriteforge.game.pipeline import Models
from spriteforge.providers.base import ChatResult
from spriteforge.stages.references import (
    MAX_SIDE,
    ReferenceError,
    add_reference,
    images,
    normalize,
    style,
)


def picture(size=(64, 48), colour=(200, 40, 90), fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, fmt)
    return out.getvalue()


@pytest.fixture
def run(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    return store, store.create_run({"budget": load_config().budget.model_dump()})


def test_an_upload_must_be_an_image_within_the_caps():
    png = normalize(picture((3000, 1500), fmt="JPEG"))
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG" and max(image.size) == MAX_SIDE  # scaled down
    with pytest.raises(ReferenceError, match="not an image"):
        normalize(b"<script>alert(1)</script>")
    with pytest.raises(ReferenceError, match="larger than"):
        normalize(b"x" * (9 * 1024 * 1024))


def test_references_belong_to_an_agent_or_to_everyone(run):
    store, r = run
    with pytest.raises(ReferenceError, match="right to use"):
        add_reference(store, r.id, "Pixel Artist", "cave.png", picture(), rights=False)
    art = add_reference(store, r.id, "Pixel Artist", "Cave Mood!.png", picture(), rights=True)
    assert art["name"] == "references/1-cave-mood.png" and not art["everyone"]
    add_reference(store, r.id, "Director", "style.jpg", picture(colour=(10, 200, 10)), rights=True)
    refs = images(store, r.id, ["Pixel Artist", "Programmer", "Art Director"])
    assert len(refs["Pixel Artist"]) == 2  # its own and every agent's
    assert len(refs["Programmer"]) == 1 and len(refs["Art Director"]) == 1
    assert len(style(refs)) == 2  # no duplicates across the art agents


def test_spark_calls_carry_the_agents_references(run):
    store, r = run
    budget = Budget(store, r.id, load_config().budget)

    class Seen:
        def __init__(self):
            self.requests = []

        def chat(self, request):
            self.requests.append(request)
            with budget.attempt("mock", "seen"):
                return ChatResult(content='{"answer": "ok"}')

    from spriteforge.app.chat import ChatAnswer

    seen = Seen()
    models = Models(load_config(), budget, mock=True, provider=seen)
    models.references = {"Game Designer": [picture()]}
    models.ask("Game Designer", "Design it", ChatAnswer, ChatAnswer(answer="-"))
    models.ask("Programmer", "Code it", ChatAnswer, ChatAnswer(answer="-"))
    designer, programmer = (q.messages[-1] for q in seen.requests)
    assert len(designer.images) == 1 and "reference image" in designer.content
    assert not programmer.images and "reference image" not in programmer.content


def test_muse_draws_in_the_references_style(run):
    store, r = run
    budget = Budget(store, r.id, load_config().budget)
    d = hero_only()
    ref = picture()
    images_ = ScriptedImages(budget, [])
    make_art(d, keyword_plan(d), generate=images_, options=MuseOptions(style=[ref], retries=0))
    request = images_.requests[0]
    assert request.references[-1] == ref and "style reference" in request.prompt


def test_upload_reference_in_the_studio(studio):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    answer_until(client, run_id, "design", set())
    url = f"/api/runs/{run_id}/references"
    file = {"file": ("mood.png", picture(), "image/png")}
    refused = client.post(url, files=file, data={"agent": "Pixel Artist"})
    assert refused.status_code == 400 and "right to use" in refused.json()["detail"]
    junk = client.post(url, files={"file": ("x.png", b"nope", "image/png")},
                       data={"agent": "Pixel Artist", "rights": "true"})  # fmt: skip
    assert junk.status_code == 400
    assert client.post(url, files=file, data={"agent": "Nobody", "rights": "true"}).status_code == 404
    added = client.post(url, files=file, data={"agent": "Pixel Artist", "rights": "true"})
    assert added.status_code == 201
    everyone = client.post(url, files={"file": ("style.png", picture(colour=(0, 0, 0)), "image/png")},
                           data={"agent": "Director", "rights": "true"}).json()  # fmt: skip
    assert [r["file"] for r in client.get(url).json()["references"]] == ["mood.png", "style.png"]
    art = client.get(f"/api/runs/{run_id}/agents/Pixel Artist/thread").json()["items"]
    assert [i["reference"]["file"] for i in art if i["kind"] == "reference"] == ["mood.png", "style.png"]
    coder = client.get(f"/api/runs/{run_id}/agents/Programmer/thread").json()["items"]
    assert [i["reference"]["id"] for i in coder if i["kind"] == "reference"] == [everyone["id"]]
    assert client.get(f"/api/artifacts/{added.json()['id']}").headers["content-type"] == "image/png"
    # A cross-site page cannot upload (local-only guard).
    cross = client.post(url, files=file, data={"agent": "Director", "rights": "true"},
                        headers={"Origin": "https://evil.example"})  # fmt: skip
    assert cross.status_code == 403
    from spriteforge.stages.game import GameContext

    ctx = GameContext(store, run_id)
    assert len(ctx.models.references["Pixel Artist"]) == 2
    assert len(ctx.models.references["Programmer"]) == 1
