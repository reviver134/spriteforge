import pytest
from typer.testing import CliRunner

from spriteforge.cli import app
from spriteforge.config import load_config
from spriteforge.core.state import Store
from spriteforge.stages.workflow import start_run


@pytest.mark.parametrize("mock", [False, True])
def test_agents_cli_only_starts_phaser_without_legacy(tmp_path, monkeypatch, mock):
    monkeypatch.setattr("spriteforge.cli.serve", lambda *args: None)
    from spriteforge.agents import workflow as old

    monkeypatch.setattr(old, "start_run", lambda *args: pytest.fail("Legacy path reached"))
    result = CliRunner().invoke(
        app,
        [
            "start",
            "--agents",
            "--workspace",
            str(tmp_path),
            "--goal",
            "bike rider",
            *(["--mock"] if mock else []),
        ],
    )
    assert result.exit_code == 0, result.output
    run = Store(tmp_path / "state.sqlite3").runs()[0]
    assert run.config["gameplay_preview"] is True
    assert run.config["legacy_preview"] is False
    assert run.config["goal"] == "bike rider"
    assert run.config["mock"] is mock


def test_legacy_requires_flag_and_warns_on_start_and_resume(tmp_path, monkeypatch):
    from spriteforge.stages.concept import preview

    with pytest.raises(ValueError, match="--legacy"):
        preview(b"not an image", (320, 180))
    monkeypatch.setattr("spriteforge.cli.serve", lambda *args: None)
    store = Store(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError, match="--legacy"):
        start_run(store, load_config(), gameplay_preview=False)
    runner = CliRunner()
    result = runner.invoke(
        app, ["start", "--intake", "--mock", "--legacy", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0 and "DEPRECATED" in result.output
    run = store.runs()[0]
    assert not run.config["gameplay_preview"]
    assert any(e.type == "preview.deprecated" for e in store.events(run.id))
    args = ["resume", run.id, "--workspace", str(tmp_path)]
    assert runner.invoke(app, args).exit_code != 0
    result = runner.invoke(app, [*args, "--legacy"])
    assert result.exit_code == 0 and "DEPRECATED" in result.output
