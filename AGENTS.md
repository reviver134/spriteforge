# SpriteForge: local multi-agent pixel-game builder

## Vision
A harness that runs on the USER'S OWN MACHINE. It orchestrates multiple role-based agents (Muse models allowed) to build a playable 2D pixel-art browser game, with a human in the loop at defined gates. Each finished build teaches the harness: lessons are written to a local OKF (Open Knowledge Format) knowledge base that future builds retrieve.

## Core principles
1. Models propose; deterministic code enforces (palette, sizes, seams, tests).
2. Human gates are first-class: the run pauses, persists, and resumes. Nothing irreversible happens without approval.
3. Local-first: all data in a workspace dir (default ~/spriteforge-workspace). No telemetry. API keys come from env vars only and are NEVER written to logs, memory, or exports.
4. Muse Image output is non-deterministic and has no seed: accepted assets are locked in the registry and never regenerated unless the user or a change request targets them.
5. Everything model-facing sits behind provider interfaces with a MockProvider so the full pipeline and tests run offline.
6. Extensible: skills (folders with SKILL.md) and MCP servers can be added by the user and granted per-agent.
7. Learning is auditable: knowledge is plain Markdown in a git repo; agent-written knowledge is "generated" until a human promotes it.

## Agents (roles are markdown files, not hardcoded)
Director (plans and routes), Game Designer, Art Director, Pixel Artist, Level/Tile Designer, Programmer, QA Playtester, Librarian (memory curator).

## Human gates
G1 Brief (questionnaire) -> G2 Concept preview -> G3 Asset contact sheet -> G4 Playtest (repeatable) -> G5 Final review + knowledge promotion.

## Layout
src/spriteforge/{app,core,providers,agents,pixel,stages,genres,skills,mcp,memory,game}, tests/, workspace/ (runs, memory corpus, user skills)

## Working rules
- Work in milestones. After each: run pytest, commit, print what works and what doesn't. Stop at the acceptance criteria and wait for my go-ahead.
- Python 3.11+, pydantic for every schema, typer CLI, FastAPI + plain HTML/JS (HTMX ok) for the local UI, Playwright for headless tests, Phaser 3 (no build step) for generated games.
- Verify external APIs (Meta Model API, MCP SDK, OKF spec) against live docs/first real calls; keep endpoint paths and model ids in config.yaml.
- Cost caps and call counters are mandatory; default max 150 image calls per run.
- Prefer small readable modules. No speculative features.
- External editor/tool dependencies must be free to use; do not require paid editor licenses.
- Keep Muse API providers optional; the free-tools requirement does not remove them. Offline runs use mock providers.
