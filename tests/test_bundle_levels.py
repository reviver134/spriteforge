"""The game record's level pages link only entities the design has (live 2026-11-03: a checkpoint
the SDK draws has no entity, and its link /entities/.md failed the bundle's validation, which
stopped a live run after its levels were written)."""

from test_levels import GATED, check, slime_design

from spriteforge.game.bundle import GameRecord, level_concepts


def test_a_level_thing_without_an_entity_is_not_linked():
    rec = check(GATED, n=1, events=["level_complete"])
    legend = [e.model_copy(update={"entity": ""}) if e.kind == "checkpoint" else
              e.model_copy(update={"entity": "made_up"}) if e.kind == "exit" else e
              for e in rec.spec.legend]  # fmt: skip
    rec = rec.model_copy(update={"spec": rec.spec.model_copy(update={"legend": legend})})
    entries, _ = level_concepts(GameRecord(design=slime_design(), design_source="t", levels=[rec]))
    body = next(e for e in entries if e.id == "levels/1").body
    assert "/entities/.md" not in body and "/entities/made_up.md" not in body
    assert "drawn by the SDK" in body and "`made_up`" in body
    assert "[crate](/entities/crate.md)" in body  # a real entity keeps its link
