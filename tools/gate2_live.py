"""Capped live acceptance run. Stops at visual human gates; no implicit approvals."""

import argparse
import json
from pathlib import Path

from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.credentials import load_credentials
from spriteforge.core.gates import Feedback, Gates
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.stages.workflow import start_run, workflow_step

ROOT = Path("workspace/gate2-live")


def advance(store, ident):
    while workflow_step(store, ident):
        calls = [e for e in store.events(ident) if e.type == "cost.reserved"]
        print(
            f"Checkpoint saved. Provider calls: {len(calls)}/25; image calls: {sum(e.payload['image'] for e in calls)}/16",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    parser.add_argument(
        "--approve", choices=["mood_board", "sprite_anchor", "gameplay", "preview_revise"]
    )
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    load_credentials()
    init_workspace(ROOT)
    store = Store(ROOT / "state.sqlite3")
    marker = ROOT / "run.json"
    if args.start:
        if marker.exists():
            raise ValueError("Live run already exists; resume it instead of spending on a new run")
        cfg = load_config()
        cfg.budget = BudgetConfig(max_image_calls=16, max_total_calls=25, max_estimated_cost=0.25)
        run = start_run(store, cfg, mock=False)
        marker.write_text(json.dumps({"run_id": run.id, "max_total_calls": 25}), encoding="utf-8")
    else:
        run = store.get_run(json.loads(marker.read_text())["run_id"])
        if args.approve:
            gate = Gates(store).pending(run.id)
            if not gate or gate.stage != args.approve or not args.note:
                raise ValueError(
                    "Approval requires the matching pending gate and the human's approval note"
                )
            Gates(store).respond(run.id, gate.id, Feedback(action="approve", text=args.note))
    advance(store, run.id)
    # The user explicitly selected this acceptance brief. Visual gates still
    # require separate, concrete approval. This loop is restart-safe.
    forms = {
        "genre": {"genre": "genres/side-scrolling-platformer"},
        "brief": {
            "perspective": "side-scroll",
            "environment": "A ruined city street with broken asphalt, abandoned vehicles and fallen poles",
            "character": "A brave bike rider on a compact bicycle, readable side profile",
            "mood": "Muted blue-gray city ruins, warm amber highlights, green zombies",
            "core_loop": "Ride through ruined streets, dodge zombie hordes and obstacles, collect a key and reach the exit",
            "references": "Side-scrolling pixel arcade game",
            "controls": "keyboard",
        },
        "followups": {},
        "styles": {},
    }
    while (gate := Gates(store).pending(run.id)) and gate.stage in forms:
        Gates(store).respond(
            run.id,
            gate.id,
            Feedback(
                action="approve",
                form=forms[gate.stage],
                text="Acceptance brief explicitly selected by the user: side-scrolling ruined city with bike rider and zombies.",
            ),
        )
        advance(store, run.id)
    events = [e for e in store.events(run.id) if e.type == "cost.reserved"]
    gate = Gates(store).pending(run.id)
    summary = {
        "run_id": run.id,
        "status": store.get_run(run.id).status,
        "pending_gate": gate.stage if gate else None,
        "total_calls": len(events),
        "image_calls": sum(e.payload["image"] for e in events),
        "estimated_image_cost": round(sum(e.payload["estimated_cost"] for e in events), 4),
        "root": str((ROOT / "runs" / run.id).resolve()),
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- scrub all provider errors at the CLI boundary
        print(redact(str(exc)), flush=True)
        raise SystemExit(1) from None
