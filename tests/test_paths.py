from __future__ import annotations

from totstats.shared import paths


def test_data_dir_follows_xdg_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "is_windows", lambda: False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert paths.data_dir() == tmp_path / "TOTStatsMonitor"


def test_xdg_dir_falls_back_to_the_default_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    assert paths.xdg_dir("XDG_CONFIG_HOME", ".config") == tmp_path / ".config"
