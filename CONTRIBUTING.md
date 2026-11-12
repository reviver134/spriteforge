# Contributing to SpriteForge

Thanks for taking a look. Bug reports, games you made, and small focused pull requests are all
welcome.

## Set up

```bash
git clone https://github.com/reviver134/spriteforge.git && cd spriteforge
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium
```

No Muse key is needed to develop: `--mock` runs the whole pipeline offline.

```bash
python -m spriteforge start --agents --mock --workspace workspace
```

## Before you open a pull request

```bash
python -m ruff check src tests
python -m pytest -q
```

The tests make no network calls. On Windows, if pytest cannot write its temp folder, pass a
short one: `--basetemp=%TEMP%\sfpt -p no:cacheprovider`.

## How the code is meant to grow

- **Models propose; code enforces.** When a model keeps getting something wrong, add a
  deterministic check that catches it and feeds the reason back, rather than a longer prompt.
  `game/playtest.py` (the HUD check) and `game/art.py` (`check_frame`) are examples.
- **Small modules, plain names.** One job per file; no speculative features.
- **Every schema is pydantic.** Every Muse Spark schema is tested against what Meta accepts.
- **Never log or store the API key.** Keys come from the environment only.
- **The UI build is committed.** If you change `ui/`, run `npm run build` there and commit
  `src/spriteforge/app/static/` with it.

## Reporting a bad game

If a build looks or plays wrong, the most useful report includes the genre, the sentence you
started from, a screenshot, and which gate it got through. The run folder
(`workspace/runs/RUN_ID/game/vN/`) has `report.json` and `qa.json` with what the checks saw.
Please do not attach `.env` or anything with your key in it.
