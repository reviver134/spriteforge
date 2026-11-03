import os

from spriteforge.core.credentials import load_credentials


def test_dotenv_only_loads_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    path = tmp_path / ".env"
    path.write_text('META_API_KEY="fake-credential"\nOTHER_SECRET=ignore\n', encoding="utf-8-sig")
    load_credentials(path)
    assert os.environ["META_API_KEY"] == "fake-credential"
    assert "OTHER_SECRET" not in os.environ
    assert not capsys.readouterr().out
    monkeypatch.delenv("META_API_KEY")


def test_environment_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "existing")
    path = tmp_path / ".env"
    path.write_text("META_API_KEY=override")
    load_credentials(path)
    assert os.environ["META_API_KEY"] == "existing"


def test_absent_dotenv_is_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("META_API_KEY", raising=False)
    load_credentials(tmp_path / ".env")
    assert "META_API_KEY" not in os.environ


def test_repo_dotenv_loads_from_any_folder(tmp_path, monkeypatch):
    """The studio started outside the repo (ui/, home) still finds the repo's .env."""
    from spriteforge.core import credentials

    monkeypatch.delenv("META_API_KEY", raising=False)
    repo_env = tmp_path / "repo" / ".env"
    repo_env.parent.mkdir()
    repo_env.write_text("META_API_KEY=from-repo", encoding="utf-8")
    monkeypatch.setattr(credentials, "REPO_ENV", repo_env)
    monkeypatch.chdir(tmp_path)
    load_credentials()
    assert os.environ["META_API_KEY"] == "from-repo"
    monkeypatch.delenv("META_API_KEY")
