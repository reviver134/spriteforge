"""Capped live gated run of a game prompt: G1 answered from flags, then stop at a gate.

    python tools/game_live.py --workspace workspace/l21-slime --goal "..." --until playtest
    python tools/game_live.py --workspace workspace/l21-story --goal "..." --story --until design
    python tools/game_live.py --workspace workspace/l22-slime --goal "..." --feedback "..."

G1 (genre, brief, follow-ups) is answered with the given values; `--until design` stops at G2;
`--until menus` approves G2 and stops at G3 (the menus, L2.5); `--until playtest` approves G2
and G3 (empty feedback) and stops at G4, so the human plays the build and answers G4 in the UI.
`--menus-feedback TEXT` answers the open G3 once with that text (the menus revised, the same
code and art built again). `--feedback TEXT` answers the open G4 once with that text (a new
build) and stops at the next G4; with `--level N` the text is about level N only (the G4 form). `--no-research` answers No to "Research similar games?".
Re-running resumes the same run (run.json); it never starts a second paid run in the same
workspace.
"""

import argparse
import json
from pathlib import Path

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.credentials import load_credentials
from spriteforge.core.gates import Feedback, Gates
from spriteforge.core.state import Store
from spriteforge.game.pipeline import call_summary
from spriteforge.stages.intake import NO_STORY, RESEARCH_NO, RESEARCH_YES, YES_STORY
from spriteforge.stages.workflow import start_run, workflow_step


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--genre", default="genres/side-scrolling-platformer")
    parser.add_argument("--perspective", default="side-scroll")
    parser.add_argument("--story", action="store_true", help="answer Yes to Story? at G1")
    parser.add_argument("--no-research", action="store_true", help="answer No to Research?")
    parser.add_argument("--until", choices=["design", "menus", "playtest"], default="playtest")
    parser.add_argument("--menus-feedback", default="", help="answer the open G3 with this text once")
    parser.add_argument("--feedback", default="", help="answer the open G4 with this text once")
    parser.add_argument("--level", type=int, default=0,
                        help="with --feedback: pick this level on the G4 form (that level only)")
    parser.add_argument("--max-cost", type=float, default=1.0)
    args = parser.parse_args()
    load_credentials()
    root = init_workspace(args.workspace)
    store = Store(root / "state.sqlite3")
    marker = root / "run.json"
    if marker.exists():
        run = store.get_run(json.loads(marker.read_text(encoding="utf-8"))["run_id"])
        budget = run.config["budget"]
        if args.max_cost > budget["max_estimated_cost"]:  # resuming with a higher cap
            config = {**run.config, "budget": {**budget, "max_estimated_cost": args.max_cost}}
            with store.connect() as db:
                db.execute("UPDATE runs SET config=? WHERE id=?", (json.dumps(config), run.id))
                store._event(db, run.id, "Human", "budget.raised",
                             {"max_estimated_cost": args.max_cost})  # fmt: skip
            run = store.get_run(run.id)
    else:
        cfg = load_config()
        cfg.workspace = root
        cfg.budget = BudgetConfig(max_image_calls=40, max_estimated_cost=args.max_cost)
        run = start_run(store, cfg, mock=False, goal=args.goal)
        marker.write_text(json.dumps({"run_id": run.id}), encoding="utf-8")
    forms = {
        "genre": {"genre": args.genre},
        "brief": {"perspective": args.perspective, "story": YES_STORY if args.story else NO_STORY,
                  "research": RESEARCH_NO if args.no_research else RESEARCH_YES},
        "followups": {},
    }
    while True:
        while workflow_step(store, run.id):
            pass
        gate = Gates(store).pending(run.id)
        if gate is None:
            break
        if gate.stage in forms:
            Gates(store).respond(run.id, gate.id, Feedback(action="approve", form=forms[gate.stage]))
            continue
        if gate.stage == "design" and args.until in {"menus", "playtest"}:
            print(f"approving as the test operator: {gate.title[:100]}", flush=True)
            Gates(store).respond(run.id, gate.id, Feedback(action="submit", text=""))
            continue
        if gate.stage == "menus" and args.menus_feedback:
            print(f"G3 feedback as the test operator: {args.menus_feedback[:100]}", flush=True)
            Gates(store).respond(run.id, gate.id, Feedback(action="submit", text=args.menus_feedback))
            args.menus_feedback = ""
            continue
        if gate.stage == "menus" and args.until == "playtest":
            print(f"approving as the test operator: {gate.title[:100]}", flush=True)
            Gates(store).respond(run.id, gate.id, Feedback(action="submit", text=""))
            continue
        if gate.stage == "playtest" and args.feedback:
            print(f"G4 feedback as the test operator: {args.feedback[:100]}", flush=True)
            picked = next((v for v in (gate.form_schema.get("properties", {}).get("level", {})
                                       .get("enum", [])) if v.startswith(f"Level {args.level}:")), None)
            form = {"level": picked} if args.level and picked else {}
            Gates(store).respond(run.id, gate.id, Feedback(action="submit", text=args.feedback, form=form))
            args.feedback = ""
            continue
        break
    gate = Gates(store).pending(run.id)
    print(json.dumps({"run": run.id, "status": store.get_run(run.id).status,
                      "gate": gate.title if gate else None,
                      "calls": call_summary(store, run.id)}, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
