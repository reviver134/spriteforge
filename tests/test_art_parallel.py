"""M10.2: Muse Image calls in parallel under the cap, a request cache, and budget safety."""

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from spriteforge.config import BudgetConfig, load_config
from spriteforge.core.cost import Budget, BudgetExceeded, ledger
from spriteforge.core.state import Store
from spriteforge.game.art import MuseOptions, keyword_plan, make_art
from spriteforge.game.design import Entity, fallback_design
from spriteforge.game.image_cache import ImageCache
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.mock import MockImageProvider

IDENTITY = {"model": "m", "base_url": "u", "generate": "/gen", "edit": "/edit"}
# One image per sprite, no retries: each job is exactly one call.
SIMPLE = {"sheet_frames": 1, "retries": 0, "adapt_size": False}


def ent(eid, role, look) -> Entity:
    return Entity(id=eid, role=role, look=look, behaviour="-", width=16, height=16,
                  animations=[])  # fmt: skip


def six_jobs():
    """Hero + 5 entities; the library is empty in tests, so all 6 go to Muse.
    Priority order: hero, orc, bat, coin, crate, arrow."""
    d = fallback_design("arena")
    d.entities = [
        d.entities[0],
        ent("coin", "pickup", "a gold coin"),
        ent("orc", "enemy", "a green orc"),
        ent("arrow", "projectile", "a wooden arrow"),
        ent("bat", "enemy", "a purple bat"),
        ent("crate", "prop", "a wooden crate"),
    ]
    return d


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


class SlowImages(MockImageProvider):
    """Sleeps per call and records how many calls were in flight at once."""

    def __init__(self, budget, delay=0.4, fail_on=""):
        super().__init__(budget)
        self.delay, self.fail_on = delay, fail_on
        self.lock, self.active, self.peak, self.prompts = threading.Lock(), 0, 0, []

    def generate(self, request):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.prompts.append(request.prompt)
        try:
            time.sleep(self.delay)
            if self.fail_on and self.fail_on in request.prompt:
                raise RuntimeError("Meta HTTP 500: internal server error")
            return super().generate(request)
        finally:
            with self.lock:
                self.active -= 1

    def __call__(self, request, edit):
        return self.generate(request).data


def test_parallel_calls_bounded_and_ordered(budget):
    d = six_jobs()
    plan = keyword_plan(d)
    start = time.perf_counter()  # local work only (instant calls): reading back the frames
    sequential = make_art(d, plan, generate=SlowImages(budget, delay=0),
                          options=MuseOptions(**SIMPLE, workers=1))  # fmt: skip
    local = time.perf_counter() - start
    images = SlowImages(budget, delay=0.5)
    start = time.perf_counter()
    art = make_art(d, plan, generate=images, options=MuseOptions(**SIMPLE, workers=4))
    wall = time.perf_counter() - start
    assert len(images.prompts) == 6 and images.peak == 4
    # 6 jobs on 4 workers wait for two rounds of calls; one at a time they would wait six.
    assert wall < local + 3 * images.delay, (wall, local)
    assert list(art.filled) == list(sequential.filled) == ["hero", "orc", "bat", "coin",
                                                           "crate", "arrow"]  # fmt: skip
    assert {k: f.png for k, f in art.filled.items()} == {
        k: f.png for k, f in sequential.filled.items()
    }
    assert art.warnings == sequential.warnings


def test_cap_under_concurrency(budget):
    d = six_jobs()
    images = SlowImages(budget, delay=0.1)
    art = make_art(d, keyword_plan(d), generate=images, max_calls=3,
                   options=MuseOptions(**SIMPLE, workers=4))  # fmt: skip
    assert len(images.prompts) == 3
    sources = {k: f.source for k, f in art.filled.items()}
    # The three lowest-priority entities (pickup, prop, projectile) become greybox.
    assert sources == {"hero": "muse", "orc": "muse", "bat": "muse",
                       "coin": "placeholder", "crate": "placeholder", "arrow": "placeholder"}  # fmt: skip
    assert [w.split(":")[0] for w in art.warnings if "cap (3)" in w] == ["coin", "crate", "arrow"]
    with budget.store.connect() as db:
        calls = list(ledger(db, budget.run_id).values())
    assert sum(c.image for c in calls) == 3


def test_one_failure_isolated(budget):
    d = six_jobs()
    images = SlowImages(budget, delay=0.05, fail_on="purple bat")
    art = make_art(d, keyword_plan(d), generate=images, options=MuseOptions(**SIMPLE))
    sources = {k: f.source for k, f in art.filled.items()}
    assert sources.pop("bat") == "placeholder"
    assert set(sources.values()) == {"muse"}
    assert any(w.startswith("bat: Muse Image failed (RuntimeError") for w in art.warnings)
    assert any(w.startswith("bat: no usable Muse image") for w in art.warnings)


def test_cache_hit_skips_call(budget, tmp_path):
    d = six_jobs()
    plan = keyword_plan(d)
    cache = ImageCache(tmp_path / "cache", IDENTITY)
    images = SlowImages(budget, delay=0)
    first = make_art(d, plan, generate=images, options=MuseOptions(**SIMPLE), cache=cache)
    assert len(images.prompts) == 6
    assert len(list((tmp_path / "cache").glob("*.png"))) == 6
    # Same requests again, even with a cap of 0: every image comes from the cache.
    again = make_art(d, plan, generate=images, max_calls=0, options=MuseOptions(**SIMPLE),
                     cache=cache)  # fmt: skip
    assert len(images.prompts) == 6
    assert {f.source for f in again.filled.values()} == {"muse (cached)"}
    hero = again.filled["hero"]
    assert hero.png == first.filled["hero"].png
    assert hero.record["request"]["calls"] == 0 and hero.record["request"]["cached"]
    assert any("from the cache" in r for r in hero.reasons)
    with budget.store.connect() as db:
        assert sum(c.image for c in ledger(db, budget.run_id).values()) == 6


def test_cache_key_includes_references_and_reasoning(tmp_path):
    cache = ImageCache(tmp_path, IDENTITY)
    base = ImageRequest(prompt="a bat", references=[b"\x89PNG one"])
    key = cache.key(base, True)
    assert cache.key(base.model_copy(), True) == key
    changed_ref = base.model_copy(update={"references": [b"\x89PNG onE"]})
    stronger = base.model_copy(update={"reasoning_strength": "high"})
    for other in (changed_ref, stronger, base.model_copy(update={"size": (1792, 1024)})):
        assert cache.key(other, True) != key
    plain = ImageRequest(prompt="a bat")
    assert cache.key(plain, False) != cache.key(plain, True)  # generate vs edit endpoint
    other_model = ImageCache(tmp_path, {**IDENTITY, "model": "m2"})
    assert other_model.key(plain, False) != cache.key(plain, False)
    cache.put(base, True, b"png")
    assert cache.get(base, True) == b"png" and cache.get(stronger, True) is None


def test_cache_write_failure_never_fails_the_call(budget, tmp_path):
    """A cache that cannot be written (here: its folder is a file) costs only the cache entry."""
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("x")
    cache = ImageCache(blocked / "muse-image", IDENTITY)
    d = fallback_design("arena")
    images = SlowImages(budget, delay=0)
    art = make_art(d, keyword_plan(d), generate=images, options=MuseOptions(**SIMPLE), cache=cache)
    assert {f.source for f in art.filled.values()} == {"muse"} and len(images.prompts) == 4
    assert not any("failed" in w for w in art.warnings)
    assert cache.put(ImageRequest(prompt="x"), False, b"png") is False


def tiny_png(shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), (shade, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_reference_change_makes_a_new_call(budget, tmp_path):
    """The previous image (an edit's reference) is in the key: new pixels, new call."""
    d = fallback_design("arena")
    d.entities = d.entities[:1]
    cache = ImageCache(tmp_path / "cache", IDENTITY)
    images = SlowImages(budget, delay=0)
    for shade in (10, 20, 20):
        old = {"prompt": "older look", "raw": tiny_png(shade), "version": 1}
        make_art(d, keyword_plan(d), generate=images, options=MuseOptions(**SIMPLE), cache=cache,
                 previous=lambda e, o=old: o)  # fmt: skip
    assert len(images.prompts) == 2


def test_budget_reservations_hold_under_concurrency(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cap = BudgetConfig(**{**load_config().budget.model_dump(), "max_image_calls": 5})
    run = store.create_run({"workflow": "t", "budget": cap.model_dump()})
    budget = Budget(store, run.id, cap)

    def reserve(_):
        try:
            budget.reserve("meta", "muse-image-1.0", image=True)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(12)))
    assert results.count(True) == 5
    with store.connect() as db:
        assert sum(c.image for c in ledger(db, run.id).values()) == 5
