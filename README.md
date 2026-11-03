# SpriteForge

**Describe a game in a sentence; a team of AI agents builds a playable 2D pixel-art browser
game, and you approve every step.**

SpriteForge runs on your own machine. **Muse Spark** plans, designs, writes and repairs the
game; **Muse Image** draws and revises its art. A harness of role-based agents (Director, Game
Designer, Art Director, Pixel Artist, Level/Tile Designer, Menu Designer, Programmer, Sound
Director, QA Playtester, Art Reviewer, Librarian) carries the game from brief to finished build,
pausing at human gates. Each finished game teaches the harness: lessons go to a local,
auditable knowledge base that later builds read once a human promotes them.

Built for the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026).

## How a game gets made

| Gate | You see | What happens before it |
| --- | --- | --- |
| **G1 Brief** | A short questionnaire for your genre | The Director suggests a genre and the questions that matter for it |
| **G2 Design** | Research, the game plan, the design (and a story, if you asked) | The Game Designer studies similar games with Muse Spark web search, writes a numbered plan code checks, then the design |
| **G3 Menus** | Every screen of the game and its title art | The Menu Designer writes menus as data; Muse Image draws the title art |
| **G4 Playtest** | A play link, the art sheet, animations, the build report | Art comes from the local library or Muse Image; the Programmer writes Phaser code against a small SDK; a bot plays every level; the QA Playtester's tests run with real key presses; failures go back for bounded repairs; the Art Reviewer (Spark vision) checks the art in the running game |
| **G5 Final** | The whole game, its cost, and the Librarian's lessons | Approval exports the game and its full record |

At any gate, write feedback in plain words ("the slime should jump higher", "the splash is too
loud"). The Director routes it to the agent it is about, and only that part is redone.

## Principles

- **Models propose; code enforces.** Palettes, sprite sizes, tile seams, jump physics, menu
  reachability and QA tests are checked deterministically, not trusted.
- **Accepted art is locked.** Muse Image has no seed, so approved assets are never regenerated
  unless you ask for a change to them.
- **Hard cost caps.** Every Muse call is counted in a durable ledger with a per-run cap
  (`budget.max_image_calls`, `budget.max_total_calls`, `game.review_cost_cap` in
  `src/spriteforge/config.yaml`). `spriteforge report RUN_ID` shows calls, tokens and cost.
- **Local-first.** All data stays in a workspace folder. The API key is read from the
  environment and scrubbed from logs, memory and exports.
- **Auditable learning.** Knowledge is plain Markdown in a git repo (OKF format). Agent-written
  lessons stay "generated" until a human promotes them.
- **Runs offline.** Mock providers stand in for Muse, so the whole pipeline and the tests run
  without a key.

## Quick start

Requires Python 3.11+ and git. Node.js is optional (used to syntax-check generated code).

```bash
git clone <this repo> && cd <repo>
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium
```

**Offline (no key):**

```bash
python -m spriteforge start --agents --mock --workspace workspace
```

**Live with Muse:** put your key in the environment or in a `.env` file in the repo root
(`.env` is gitignored), then start without `--mock`:

```bash
echo META_API_KEY=your-key > .env
python -m spriteforge start --agents --workspace workspace --goal "a slime that squishes through a cave"
```

The UI opens at <http://127.0.0.1:8765>. Stop the process at any gate and pick up later:

```bash
python -m spriteforge runs --workspace workspace
python -m spriteforge resume RUN_ID --workspace workspace
```

**Without the UI:** design, art, code, playtest and repair in one command:

```bash
python -m spriteforge game make "a 10 minute top-down survival game" --out out/survival
python -m spriteforge game make "a slime arena" --out out/slime --mock
```

## What you get

Each run lives in `workspace/runs/RUN_ID/`. A finished game (`game/vN/`) is a static Phaser 3
site with no build step: open `index.html` over any static server, or host it anywhere.

- `export.zip`: the build, its QA tests (`pytest build/tests`) and its full record.
- `sounds.html`: a sound editor. All sound is synthesised in the browser (no audio files).
- `game/db/`: the game's record as an OKF bundle (design, entities, art, code, QA, builds,
  your decisions), readable by agents through read-only MCP tools
  (`spriteforge game db mcp workspace/runs/RUN_ID`).

In the game: arrows/WASD and Enter, Escape pauses, M toggles sound. `?play=1` skips the title,
`?reset=1` clears saved progress.

## Knowledge, skills and tools

```bash
python -m spriteforge memory --help                         # search, validate, promote lessons
python -m spriteforge memory promote lessons/<id> --by you  # let later games use a lesson
python -m spriteforge skills --help                         # add SKILL.md folders, grant per agent
python -m spriteforge mcp --help                            # add MCP servers, grant per agent
python -m spriteforge library --help                        # local art packs with license tracking
```

Agent roles are Markdown files in `src/spriteforge/agents/roles/`; built-in skills are in
`src/spriteforge/skills/builtin/`.

## Layout

```
src/spriteforge/
  app/        local UI (FastAPI + plain HTML/JS)
  agents/     roles, router, agent runtime
  core/       state (SQLite), gates, cost ledger, secret scrubbing
  providers/  Muse Spark, Muse Image, web search, and mock providers
  stages/     the gated workflow (brief, design, art, build, playtest, final)
  game/       code generation, SDK, levels, menus, sounds, QA, export
  pixel/      deterministic pixel tools: grid detection, palettes, seams, atlases
  memory/     OKF knowledge base
  skills/ mcp/ library/
tests/        pytest + Playwright
docs/         design notes for level design, QA checks and the Sound Director
```

## Tests

```bash
python -m pytest -q
```

Tests use mock providers and make no network calls.

## Art and licenses

Third-party art packs in `assets/library/` are tracked by license. Only packs verified as
redistributable (CC0 here) are committed; the rest stay on your machine and are listed in
`assets/library/.gitignore`. Phaser 3.90.0 (MIT) is bundled in `src/spriteforge/templates/phaser/vendor/`.
