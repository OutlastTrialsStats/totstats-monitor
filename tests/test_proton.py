from __future__ import annotations

from pathlib import Path

from totstats.shared import proton


def _make_prefix(library: Path) -> Path:
    logs = library / "steamapps" / "compatdata" / proton.STEAM_APP_ID / proton.PREFIX_LOGS
    logs.mkdir(parents=True)
    return logs


def _steam_home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    root = home / ".local" / "share" / "Steam"
    (root / "steamapps").mkdir(parents=True)
    return home, root


def test_finds_the_prefix_in_the_default_library(tmp_path):
    home, root = _steam_home(tmp_path)
    logs = _make_prefix(root)

    assert proton.find_logs_dir(home) == logs.resolve()


def test_finds_the_prefix_in_a_second_library_from_the_vdf(tmp_path):
    home, root = _steam_home(tmp_path)
    games = tmp_path / "games"
    logs = _make_prefix(games)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{root}"\n\t}}\n'
        f'\t"1"\n\t{{\n\t\t"path"\t\t"{games}"\n\t}}\n}}\n',
        encoding="utf-8",
    )

    assert proton.steam_libraries(root) == [root, games]
    assert proton.find_logs_dir(home) == logs


def test_no_steam_means_no_prefix(tmp_path):
    assert proton.steam_roots(tmp_path) == []
    assert proton.find_logs_dir(tmp_path) is None


def test_steam_without_the_game_means_no_prefix(tmp_path):
    home, _root = _steam_home(tmp_path)

    assert proton.find_logs_dir(home) is None


def test_without_a_prefix_the_default_location_is_returned(tmp_path):
    expected = (
        tmp_path / ".local/share/Steam/steamapps/compatdata" / proton.STEAM_APP_ID / proton.PREFIX_LOGS
    )

    assert proton.logs_dir(tmp_path) == expected
