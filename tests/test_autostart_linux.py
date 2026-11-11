from __future__ import annotations

import sys

import pytest

from totstats.shared import autostart_linux


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_enable_writes_a_desktop_entry(config_home):
    assert not autostart_linux.is_enabled()
    assert autostart_linux.enable()

    entry = config_home / "autostart" / "totstatsmonitor.desktop"
    text = entry.read_text(encoding="utf-8")
    assert autostart_linux.is_enabled()
    assert text.startswith("[Desktop Entry]\n")
    assert "Type=Application\n" in text
    assert "Name=TOTStatsMonitor\n" in text
    assert "--silent" in text.split("Exec=", 1)[1].splitlines()[0]


def test_disable_removes_the_entry(config_home):
    autostart_linux.enable()
    assert autostart_linux.disable()
    assert not autostart_linux.is_enabled()


def test_disable_without_an_entry_reports_nothing_removed(config_home):
    assert not autostart_linux.disable()


def test_quote_applies_the_exec_percent_and_string_layers():
    # Exec layer: \" and \$, field code: %%, string layer: every backslash doubled.
    quoted = autostart_linux._quote('/opt/my "apps"/$HOME/100%/tot')

    assert quoted == '"/opt/my \\\\"apps\\\\"/\\\\$HOME/100%%/tot"'


def test_exec_runs_the_frozen_binary_silently(config_home, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    autostart_linux.enable()

    exec_line = (config_home / "autostart" / "totstatsmonitor.desktop").read_text().split("Exec=", 1)[1]
    assert exec_line.splitlines()[0].endswith('" --silent')


def test_falls_back_to_dot_config_without_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(autostart_linux.Path, "home", classmethod(lambda cls: tmp_path))
    assert autostart_linux.desktop_file() == tmp_path / ".config" / "autostart" / "totstatsmonitor.desktop"
