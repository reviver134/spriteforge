"""Research: how good games of this kind work, studied with live web search before designing.

The Game Designer (Muse Spark with `web_search`, Responses API) names 3-6 reference games
with a page about each, the patterns that make the genre's levels interesting, the feel
numbers players expect (in tiles and seconds), and common pitfalls. References teach
patterns: the design is original and never copies a game's levels, characters, story or art.

Code checks what the model cannot be trusted with: a reference's URL counts as verified only
when one of the searches returned that page (the JSON answer may name a URL no search
found); URLs that are not http(s) are dropped; lists are bounded. Offline (mock runs, or the
human said no research at G1) a stand-in is read from the genre notes, with no references.
"""

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from spriteforge.agents.models import Contract
from spriteforge.providers.base import SearchResult, WebSource

MAX_REFERENCES, MAX_PATTERNS, MAX_FEEL, MAX_PITFALLS = 6, 8, 10, 8
PATTERN_KINDS = ("teaching a mechanic safely", "gating", "combining abilities",
                 "risk and reward", "pacing")  # fmt: skip


class Reference(Contract):
    title: str = Field(description="a published game of this genre")
    url: str = Field(description="a page about it that one of your searches returned")
    learn: str = Field(
        description="the pattern to learn from it, concretely; never its levels, characters, "
        "story or art to copy"
    )


class Pattern(Contract):
    name: str = Field(description="e.g. " + ", ".join(PATTERN_KINDS))
    how: str = Field(description="how good games of the genre do it, concretely")
    use: str = Field(description="how this game could use it")


class FeelNumber(Contract):
    name: str = Field(description="e.g. jump height, air time, move speed, dash length, coyote time")
    value: str = Field(description="the number players expect, in tiles and seconds")
    why: str


class Research(Contract):
    references: list[Reference] = Field(description="3 to 6 reference games")
    patterns: list[Pattern]
    feel: list[FeelNumber]
    pitfalls: list[str] = Field(description="common mistakes in games of this kind")


class ResearchRecord(BaseModel):
    """What the run keeps (`game/research.json`): the answer and how it was checked."""

    research: Research
    verified: list[bool]  # per reference: a search returned its URL
    sources: list[WebSource] = Field(default_factory=list)  # every page the searches returned
    queries: list[str] = Field(default_factory=list)
    searches: int = 0
    by: str = ""
    notes: list[str] = Field(default_factory=list)

    @property
    def references(self) -> list[tuple[Reference, bool]]:
        return list(zip(self.research.references, self.verified, strict=True))


def research_prompt(request: str, genre: str) -> str:
    return f"""Before we design this game, study how good published games of its kind work.
Use web search. Find 3 to 6 well-regarded games of this genre and, for each, a page about it
that a search returned (url) and what to learn from it. Then:
* patterns: what makes this genre's levels interesting, concretely: {", ".join(PATTERN_KINDS)};
  for each, how good games do it and how this game could use it;
* feel: the numbers players expect, in tiles (16 px) and seconds: move speed, jump height and
  air time, dash length and cooldown, coyote time, or what fits this genre;
* pitfalls: common mistakes in games of this kind.
References teach patterns only. Our game is original: never copy a specific game's levels,
characters, story or art. Short, concrete sentences.

Genre: {genre}

The human's request:
{request.strip()}"""


def structure_prompt(text: str, sources: list[WebSource]) -> str:
    """The second call when the searched answer did not fit the schema: structure it."""
    pages = "\n".join(f"- {s.url}" + (f" ({s.title})" if s.title else "") for s in sources[:40])
    return f"""Put these research notes into the schema. Use only these pages as reference
URLs (leave url '' when none fits); keep every number and pattern the notes give.

Pages the searches returned:
{pages or "- (none)"}

Notes:
{text.strip()[:12000]}"""


def section(body: str, heading: str) -> str:
    part = body.split("# " + heading + "\n", 1)
    return part[1].split("\n# ", 1)[0].strip() if len(part) == 2 else ""


def fallback_research(genre_body: str, side_view: bool) -> Research:
    """Offline stand-in from the genre notes: no references (nothing was searched)."""
    loop = section(genre_body, "Core mechanics template") or "the genre's core loop"
    pitfalls = [re.sub(r"^[*-]\s*", "", line).strip()
                for line in section(genre_body, "Common pitfalls").splitlines() if line.strip()]  # fmt: skip
    feel = [
        FeelNumber(name="move speed", value="6 tiles per second", why="crosses a screen in 3 s"),
    ]
    if side_view:
        feel += [
            FeelNumber(name="jump height", value="3 tiles", why="clears enemies and low walls"),
            FeelNumber(name="air time", value="0.7 s", why="time to steer without floating"),
            FeelNumber(name="coyote time", value="0.1 s", why="forgives a late jump at a ledge"),
        ]
    patterns = [
        Pattern(name="teaching a mechanic safely", how="show a new idea where failing costs "
                "nothing, then test it with a hazard", use=f"each level introduces one idea of {loop}"),
        Pattern(name="pacing", how="alternate tense stretches with calm ones and checkpoints",
                use="a checkpoint after every hard challenge"),
    ]  # fmt: skip
    return Research(references=[], patterns=patterns, feel=feel,
                    pitfalls=pitfalls[:MAX_PITFALLS] or ["difficulty spikes with no warning"])  # fmt: skip


def norm_url(url: str) -> str:
    """Comparable form of a URL: no scheme, no www., no fragment, no trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return host + path + (f"?{parts.query}" if parts.query else "")


def web_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def fit_research(research: Research, found: SearchResult | None, by: str) -> ResearchRecord:
    """The research as code allows it: bounded lists, unique references, http(s) URLs only,
    and each reference marked verified only when a search returned its page."""
    notes = []
    seen, refs = set(), []
    for r in research.references:
        key = r.title.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        if r.url and not web_url(r.url):
            notes.append(f"{r.title}: {r.url!r} is not a web page; dropped")
            r = r.model_copy(update={"url": ""})
        refs.append(r)
    if len(refs) > MAX_REFERENCES:
        notes.append(f"{len(refs)} references; kept the first {MAX_REFERENCES}")
        refs = refs[:MAX_REFERENCES]
    pages = {norm_url(s.url) for s in (found.sources if found else [])} - {""}
    verified = [bool(r.url) and norm_url(r.url) in pages for r in refs]
    for r, ok in zip(refs, verified, strict=True):
        if r.url and not ok:
            notes.append(f"{r.title}: {r.url} was not among the pages the searches returned")
    clean = lambda items, n: [re.sub(r"\s+", " ", i).strip() for i in items if i.strip()][:n]
    fitted = research.model_copy(update={
        "references": refs,
        "patterns": research.patterns[:MAX_PATTERNS],
        "feel": research.feel[:MAX_FEEL],
        "pitfalls": clean(research.pitfalls, MAX_PITFALLS),
    })  # fmt: skip
    return ResearchRecord(
        research=fitted,
        verified=verified,
        sources=[s for s in (found.sources if found else []) if web_url(s.url)],
        queries=found.queries if found else [],
        searches=found.searches if found else 0,
        by=by,
        notes=notes,
    )


def research_brief(record: ResearchRecord) -> str:
    """The research as the plan prompt reads it (no source lists)."""
    r = record.research
    refs = "\n".join(f"- {ref.title}: {ref.learn}" for ref in r.references)
    pats = "\n".join(f"- {p.name}: {p.how} For this game: {p.use}" for p in r.patterns)
    feel = "\n".join(f"- {f.name}: {f.value} ({f.why})" for f in r.feel)
    return (
        f"Reference games (patterns only, never copy):\n{refs or '- (none)'}\n"
        f"Patterns:\n{pats or '- (none)'}\nFeel numbers players expect:\n{feel or '- (none)'}\n"
        f"Pitfalls:\n" + ("\n".join(f"- {p}" for p in r.pitfalls) or "- (none)")
    )


def research_markdown(record: ResearchRecord) -> str:
    r = record.research
    refs = "\n".join(
        f"* **{ref.title}**: {ref.learn}"
        + (f" [{ref.url}]({ref.url})" if ref.url else "")
        + ("" if ok or not ref.url else " _(not among the pages the searches returned)_")
        for ref, ok in record.references
    )
    pats = "\n".join(f"* **{p.name}**: {p.how} _For this game:_ {p.use}" for p in r.patterns)
    feel = "\n".join(f"| {f.name} | {f.value} | {f.why} |" for f in r.feel)
    sources = "\n".join(f"* {s.url}" for s in record.sources[:30])
    return (
        f"# Research\n\n_{record.by}; {record.searches} search call(s)._\n\n"
        f"## Reference games (patterns only)\n{refs or '- (none)'}\n\n## Patterns\n{pats}\n\n"
        f"## Feel numbers\n| what | value | why |\n|---|---|---|\n{feel}\n\n"
        "## Pitfalls\n" + ("\n".join(f"* {p}" for p in r.pitfalls) or "- (none)")
        + ("\n\n## Searched for\n" + "\n".join(f"* {q}" for q in record.queries) if record.queries else "")
        + (f"\n\n## Sources the searches returned\n{sources}" if sources else "")
        + "".join(f"\n> {n}" for n in record.notes) + "\n"
    )  # fmt: skip
