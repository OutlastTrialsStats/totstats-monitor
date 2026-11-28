from __future__ import annotations

import pytest

from totstats.presence.render import Payload
from totstats.ui.discord_card import card_lines, elapsed


def _payload(**changes) -> Payload:
    values = {"details": "Grind the Bad Apples", "state": "Core Program • Fun Park", "large_image": "x",
              "large_text": "y"}
    values.update(changes)
    return Payload(**values)


@pytest.mark.parametrize(
    ("start", "now", "shown"),
    [(None, 100.0, ""), (100, 754.9, "10:54"), (100, 100 + 3 * 3600 + 5, "3:00:05"), (200, 100.0, "00:00")],
)
def test_elapsed_time_reads_like_discord(start, now, shown):
    assert elapsed(start, now) == shown


def test_the_party_size_follows_the_state():
    lines = card_lines(_payload(party_size=(3, 4)))
    assert lines == ("Grind the Bad Apples", "Core Program • Fun Park (3/4)")


def test_no_party_size_without_a_state():
    assert card_lines(_payload(state="  ", party_size=(3, 4))) == ("Grind the Bad Apples", "")


def test_padding_from_the_two_character_minimum_is_dropped():
    assert card_lines(_payload(details="  ", state="Idle")) == ("", "Idle")
