"""Muse Spark with live web search, on the Responses API (`POST /v1/responses`).

Meta's `web_search` is a built-in server-side tool of the Responses API; Chat Completions
rejects it (tools/probe_web_search.py, 2026-11-02). One call searches (at most
`max_searches` tool calls) and answers in the given JSON schema: the last `message` item holds
the JSON, earlier ones are progress lines. Every `web_search_call` lists the pages it returned
(`include: web_search_call.action.sources`); the answer's `url_citation` annotations name the
ones it cited. Searches are not in `usage`, so they are counted here and charged by `Budget`.
"""

import os

import httpx

from spriteforge.config import LLMConfig, ResearchConfig
from spriteforge.core.cost import Budget

from .base import SearchRequest, SearchResult, WebSource
from .http import MetaHTTP, ProviderError


def read_response(result: dict) -> SearchResult:
    """Messages, searches, sources and citations of a Responses API answer."""
    texts, cited, sources, queries, searches = [], [], {}, [], 0
    for item in result.get("output") or []:
        if item.get("type") == "web_search_call":
            searches += 1
            action = item.get("action") or {}
            if action.get("query"):
                queries.append(str(action["query"]))
            for found in action.get("sources") or item.get("results") or []:
                if found.get("url"):
                    sources.setdefault(found["url"], WebSource(url=found["url"],
                                                               title=found.get("title") or ""))  # fmt: skip
            if action.get("type") == "open_page" and action.get("url"):
                sources.setdefault(action["url"], WebSource(url=action["url"]))
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") != "output_text":
                    continue
                texts.append(part.get("text") or "")
                for a in part.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        cited.append(WebSource(url=a["url"], title=a.get("title") or ""))
    for c in cited:
        sources.setdefault(c.url, c)
    return SearchResult(
        content=texts[-1] if texts else None,
        text="\n".join(texts),
        queries=queries,
        sources=list(sources.values()),
        cited=cited,
        searches=searches,
    )


class MuseSearchProvider(MetaHTTP):
    def __init__(self, llm: LLMConfig, research: ResearchConfig, budget: Budget,
                 client: httpx.Client | None = None):  # fmt: skip
        super().__init__(str(llm.base_url), client)
        self.llm, self.cfg, self.budget = llm, research, budget

    def research(self, request: SearchRequest) -> SearchResult:
        with self.budget.attempt("meta", self.llm.model) as attempt:
            if not os.environ.get("META_API_KEY"):
                raise ProviderError("META_API_KEY is required for live Meta providers")
            body = {
                "model": self.llm.model,
                "instructions": request.instructions,
                "input": request.prompt,
                "tools": [{"type": "web_search", "search_context_size": self.cfg.search_context_size}],
                "max_tool_calls": request.max_searches,
                "include": ["web_search_call.action.sources"],
                "store": False,
            }
            if request.reasoning_effort:
                body["reasoning"] = {"effort": request.reasoning_effort}
            if request.cache_key:
                body["prompt_cache_key"] = request.cache_key
            if request.json_schema:
                body["text"] = {"format": {"type": "json_schema", "name": "result",
                                           "strict": True, "schema": request.json_schema}}  # fmt: skip
            result = self.post(self.cfg.path, body)
            if not isinstance(result.get("output"), list):
                raise ProviderError("Meta returned an invalid response (no output)")
            answer = read_response(result)
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            attempt.usage = {**usage, "web_searches": answer.searches}
            answer.metadata = {"model": self.llm.model, "usage": usage,
                               "status": result.get("status")}  # fmt: skip
            return answer
