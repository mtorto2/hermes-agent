import tarfile
from pathlib import Path

from hermes_cli import profiles


def _write_default_profile(root: Path) -> None:
    (root / "config.yaml").write_text("model: test\n", encoding="utf-8")
    (root / "SOUL.md").write_text("identity\n", encoding="utf-8")
    (root / ".env").write_text("OPENAI_API_KEY=sk-real-secret\n", encoding="utf-8")
    (root / "auth.json").write_text('{"token":"secret"}\n', encoding="utf-8")
    (root / "memories").mkdir()
    (root / "memories" / "MEMORY.md").write_text("stable fact\n", encoding="utf-8")


def _members(archive: Path) -> set[str]:
    with tarfile.open(archive, "r:gz") as tf:
        return set(tf.getnames())


def test_default_profile_export_skips_broken_symlinks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    _write_default_profile(home)
    broken_parent = home / "swiftbar-plugins"
    broken_parent.mkdir()
    (broken_parent / "repo-status.5m.py").symlink_to(home / "missing-target.py")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)

    archive = profiles.export_profile("default", str(tmp_path / "default.tar.gz"))

    names = _members(archive)
    assert "default/config.yaml" in names
    assert not any("repo-status.5m.py" in name for name in names)


def test_default_profile_export_excludes_nested_sensitive_and_runtime_artifacts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    _write_default_profile(home)
    (home / "state-snapshots" / "pre-update").mkdir(parents=True)
    (home / "state-snapshots" / "pre-update" / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (home / "backups" / "migration").mkdir(parents=True)
    (home / "backups" / "migration" / "auth.json").write_text('{"secret":"value"}\n', encoding="utf-8")
    (home / "sessions").mkdir()
    (home / "sessions" / "request_dump.json").write_text('{"api_key":"sk-real-secret"}\n', encoding="utf-8")
    (home / "canva").mkdir()
    (home / "canva" / "oauth_tokens.json").write_text('{"access_token":"secret"}\n', encoding="utf-8")
    (home / "skills").mkdir()
    (home / "skills" / "README.md").write_text("safe skill docs\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)

    archive = profiles.export_profile("default", str(tmp_path / "default.tar.gz"))

    names = _members(archive)
    assert "default/memories/MEMORY.md" in names
    assert "default/skills/README.md" in names
    assert not any(name.startswith("default/state-snapshots/") for name in names)
    assert not any(name.startswith("default/backups/") for name in names)
    assert not any(name.startswith("default/sessions/") for name in names)
    assert not any("oauth_tokens" in name for name in names)
    assert "default/.env" not in names
    assert "default/auth.json" not in names
