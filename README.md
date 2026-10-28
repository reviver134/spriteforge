# SpriteForge

Local-first pixel-game builder. Milestone 1 supplies the foundation and a mock demo;
game generation, human approval gates, pixel enforcement, MCP integration, and
knowledge retrieval are later milestones.

Python 3.11+:

```powershell
python -m pip install -e ".[dev]"
spriteforge start --mock
spriteforge runs
```

The browser opens at http://127.0.0.1:8765. The demo takes about ten seconds and
shows a timeline and two synthetic images. Ctrl+C or kill the process, then use
`spriteforge resume <id>` to continue. Use `--no-open-browser` for headless sessions.
`start` without `--mock` opens the existing-run viewer. No live generation workflow
is started by the foundation UI.

The default workspace is `~/spriteforge-workspace`; use `--workspace PATH` to override
it. Initialization creates `runs/`, `memory/`, `skills/`, `mcp.json`, and
`state.sqlite3`. Existing workspace files are preserved. SQLite stores configuration,
checkpoints, append-only events, budgets, and image bytes, so a database backup alone
can restore a run and its artifacts. An interrupted provider call may consume a budget
reservation before its checkpoint commits; reservations are intentionally conservative.

Configuration lives in `src/spriteforge/config.yaml` (or `--config PATH`). Model IDs,
base URLs, endpoint paths and budgets are configurable. The image cap defaults to
150 per run; model call caps and an estimated image-cost cap also apply. LLM token
pricing is not estimated in this milestone.

Live provider adapters read `META_API_KEY` only from the environment. The CLI also
loads that variable from `.env` in the current directory when it is absent from the
environment; existing environment values take precedence. `.env` is ignored by Git,
and its values are never copied into configuration or the event journal. Never put keys
in configuration. HTTP errors expose status and response text with credentials
redacted. No telemetry or external frontend dependencies. Model-provided output
must pass the `chat_json` schema validator (one corrective retry).

Meta contracts checked against [image docs](https://dev.meta.ai/docs/image-generation),
[image understanding](https://dev.meta.ai/docs/image-understanding), and
[structured outputs](https://dev.meta.ai/docs/structured-output). Images use
base64 responses and JSON inline references for edits. Requested sizes are aspect
ratio hints, not guaranteed pixel dimensions. Live account access remains unverified
until a credentialed call succeeds. MCP and OKF are not implemented or assumed here.

To explicitly verify a live account, run `python tools/meta_smoke.py` from the repository
root. It checks Spark JSON, Image generation and editing, and Spark vision, with a cap
of two image calls. It writes test images under `workspace/runs/` and keeps its call
journal in `workspace/meta-smoke.sqlite3`. This is a paid connectivity check, separate
from the offline pytest suite and mock demo.

Live verification on 2026-10-28 succeeded for all four operations with the configured
models. The generated image was 1600×1600 for a 1024×1024 request. Spark identified
the edited slime as a jellyfish; connectivity passed, while semantic consistency still
needs the planned critic and repair loop. These test images are not accepted game assets.

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```
