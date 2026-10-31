"""Live probe for M10.2: 4 small Muse Spark calls (about $0.01). Run from the repo root.

Every call sends the same long, stable prefix (system prompt + the Programmer's Phaser notes
and SDK reference, about 1.5k tokens) and a one-line question at the end, with a
`prompt_cache_key`:

1. `reasoning_effort: "low"`;
2. `reasoning_effort: "max"` (the strongest value Meta documents for muse-spark-1.3);
3. call 2 again, unchanged (does an identical request hit the cache?);
4. 60 s later, the same prefix with a different question (does a shared prefix hit?).

Prints, per call: whether the field was accepted, prompt/cached/reasoning/completion tokens and
the answer. `cached_tokens` > 0 on calls 2-3 means a repeated prompt is cached, on call 4 that a
shared prefix is; reasoning tokens differing between calls 1 and 2 mean the field has an
effect. Copy the findings into config.yaml (`llm`). Never prints credentials.
"""

import json
import time
from pathlib import Path

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget, spark_tokens
from spriteforge.core.credentials import load_credentials
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.game.codegen import SDK_API, SKILL
from spriteforge.game.pipeline import role_prompt
from spriteforge.providers.http import MetaHTTP, ProviderError

QUESTION = "What is 17 * 23 - 4? Answer with the number only."
OTHER = "What is 19 * 21 + 6? Answer with the number only."
# (reasoning effort, question, seconds to wait before the call)
CALLS = [("low", QUESTION, 0), ("max", QUESTION, 0), ("max", QUESTION, 0), ("max", OTHER, 60)]


def main() -> int:
    load_credentials()
    cfg = load_config()
    root = init_workspace(Path("workspace"))
    out = root / "probes" / "spark-reasoning"
    out.mkdir(parents=True, exist_ok=True)
    store = Store(root / "probe-spark-reasoning.sqlite3")
    cap = BudgetConfig(**{**cfg.budget.model_dump(), "max_calls_per_model": len(CALLS)})
    run = store.create_run({"workflow": "probe-spark-reasoning", "budget": cap.model_dump()})
    budget = Budget(store, run.id, cap)
    prefix = (
        "Reference for writing SpriteForge games.\n\nPhaser notes:\n"
        + SKILL.read_text(encoding="utf-8").split("---", 2)[-1].strip()
        + "\n\nSDK reference:\n"
        + SDK_API
    )
    http = MetaHTTP(str(cfg.llm.base_url))
    results = []
    try:
        for effort, question, wait in CALLS:
            time.sleep(wait)
            body = {
                "model": cfg.llm.model,
                "reasoning_effort": effort,
                "prompt_cache_key": "spriteforge-probe-programmer",
                "messages": [
                    {"role": "system", "content": role_prompt("Programmer")},
                    {"role": "user", "content": prefix + "\n\n# Task\n" + question},
                ],
            }
            row = {"reasoning_effort": effort, "question": question[:12], "waited_s": wait}
            try:
                with budget.attempt("meta", cfg.llm.model) as attempt:
                    result = http.post(cfg.llm.chat_path, body)
                    attempt.usage = result.get("usage")
            except ProviderError as exc:
                row["accepted"], row["error"] = False, redact(str(exc))[:400]
                results.append(row)
                print(json.dumps(row), flush=True)
                continue
            usage = result.get("usage", {})
            row["accepted"] = True
            row["tokens"] = spark_tokens(usage) or usage
            row["answer"] = (result["choices"][0]["message"].get("content") or "")[:80]
            results.append(row)
            print(json.dumps(row), flush=True)
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"Saved to {out}", flush=True)
        return 0
    finally:
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
