"""Live probe for L2.2: Muse Spark web search (about $0.03). Run from the repo root.

Meta documents `{"type": "web_search"}` as a built-in server-side tool on the Responses API
(https://dev.meta.ai/docs/search-grounding, checked 2026-11-02) and says Chat Completions does
not support it; pricing is "$2.50 per 1,000 search queries, in addition to the request's token
cost" (https://dev.meta.ai/docs/pricing-rate-limits). This probe decides, before any code:

1. the path: Chat Completions with the tool (expected: rejected) vs `POST /v1/responses`;
2. the citation format: `url_citation` annotations on `output_text`, and the sources of each
   `web_search_call` (`include`);
3. whether `text.format` json_schema (strict) and `web_search` work in one call;
4. what a search costs: `web_search_call` items per response (Meta does not report searches in
   `usage`), times the documented price.

Prints one JSON line per call and saves every raw response to workspace/probes/web-search/.
Copy the findings into config.yaml (`research`). Never prints credentials.
"""

import json
from pathlib import Path

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget
from spriteforge.core.credentials import load_credentials
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.providers.http import MetaHTTP, ProviderError

QUESTION = (
    "Find 3 well-regarded 2D platformer games known for good level design (titles and a web "
    "page about each). For each, say in one sentence how its levels teach a new mechanic "
    "safely before testing it. Cite your sources."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["games"],
    "properties": {
        "games": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "url", "lesson"],
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "lesson": {"type": "string"},
                },
            },
        }
    },
}


def summary(result: dict) -> dict:
    """Output item types, searches, citations, structured text and usage of one response."""
    items = result.get("output", [])
    searches = [i for i in items if i.get("type") == "web_search_call"]
    texts, citations = [], []
    for item in items:
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
                citations += [
                    {k: a.get(k) for k in ("type", "url", "title", "start_index", "end_index")}
                    for a in part.get("annotations", [])
                ]
    return {
        "status": result.get("status"),
        "output_types": [i.get("type") for i in items],
        "searches": len(searches),
        "search_calls": [
            {k: s.get(k) for k in ("status", "action") if k in s}
            | {"sources": len((s.get("action") or {}).get("sources") or s.get("results") or [])}
            for s in searches
        ],
        "citations": citations[:12],
        "text": "\n".join(texts)[:1500],
        "usage": result.get("usage"),
    }


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    out = root / "probes" / "web-search"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root / "probe-web-search.sqlite3")
    cap = BudgetConfig(**{**cfg.budget.model_dump(), "max_calls_per_model": 5})
    run = store.create_run({"workflow": "probe-web-search", "budget": cap.model_dump()})
    budget = Budget(store, run.id, cap)
    http = MetaHTTP(str(cfg.llm.base_url))
    tool = {"type": "web_search", "search_context_size": "medium"}
    base = {"model": cfg.llm.model, "input": QUESTION, "tools": [tool],
            "reasoning": {"effort": "low"}, "max_tool_calls": 4, "store": False}  # fmt: skip
    calls = [
        (
            "chat-completions+web_search",
            cfg.llm.chat_path,
            {"model": cfg.llm.model, "messages": [{"role": "user", "content": QUESTION}],
             "tools": [tool], "reasoning_effort": "low"},
        ),
        ("responses+web_search", "/v1/responses",
         base | {"include": ["web_search_call.action.sources"]}),
        (
            "responses+web_search+json_schema",
            "/v1/responses",
            base
            | {"text": {"format": {"type": "json_schema", "name": "research", "strict": True,
                                   "schema": SCHEMA}}},
        ),
    ]
    results = []
    try:
        for i, (name, path, body) in enumerate(calls):
            row = {"call": name, "path": path}
            try:
                with budget.attempt("meta", cfg.llm.model) as attempt:
                    result = http.post(path, body)
                    attempt.usage = result.get("usage")
            except ProviderError as exc:
                row["accepted"], row["error"] = False, redact(str(exc))[:600]
                if name == "responses+web_search" and "include" in row["error"]:
                    # The search-grounding page names another include value; try it once.
                    calls.insert(i + 1, (name + " (results)", path,
                                         base | {"include": ["web_search_call.results"]}))  # fmt: skip
                results.append(row)
                print(json.dumps(row), flush=True)
                continue
            (out / f"{i + 1}-{name.replace('+', '-').replace(' ', '')}.json").write_text(
                redact(json.dumps(result, indent=2)), encoding="utf-8"
            )
            row["accepted"] = True
            row |= summary(result) if "output" in result else {"raw_keys": sorted(result)}
            if "choices" in result:
                message = result["choices"][0]["message"]
                row["text"] = (message.get("content") or "")[:600]
                row["tool_calls"] = message.get("tool_calls")
                row["usage"] = result.get("usage")
            if name.endswith("json_schema") and row.get("text"):
                try:
                    row["schema_valid"] = bool(json.loads(row["text"])["games"])
                except (ValueError, KeyError, TypeError):
                    row["schema_valid"] = False
            results.append(row)
            print(json.dumps(row), flush=True)
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"Saved to {out}", flush=True)
        return 0
    finally:
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
