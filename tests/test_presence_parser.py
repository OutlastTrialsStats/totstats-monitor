from __future__ import annotations

import pytest

from totstats.presence.parser import parse_sample

_FIELDS = {
    "presenceState": "presence_state",
    "trialId": "trial_id",
    "programId": "program_id",
    "programDifficulty": "difficulty",
    "trialChain": "chain_step",
    "playerCount": "player_count",
}


def _body(online=True, **properties):
    props = {
        "crossplay": True,
        "presenceState": "trialchain",
        "region": "eu",
        "trialId": "CHA_MT02",
        "programId": "programCHAIN",
        "programDifficulty": 2.0,
        "trialChain": 3.0,
        "playerCount": 4.0,
        "invasionState": "-",
    }
    props.update(properties)
    return {"applicationId": "0e67af76", "online": online, "lastOnline": 1792786874, "properties": props}


def test_a_full_sample():
    sample = parse_sample(_body(), received_at=12.5)
    assert sample is not None
    assert sample.online is True
    assert sample.presence_state == "trialchain"
    assert sample.trial_id == "CHA_MT02"
    assert sample.program_id == "programCHAIN"
    assert sample.difficulty == 2
    assert sample.chain_step == 3
    assert sample.player_count == 4
    assert sample.received_at == 12.5


def test_a_profile_without_presence():
    body = {"applicationId": None, "online": False, "lastOnline": None, "properties": None}
    sample = parse_sample(body, received_at=0.0)
    assert sample is not None
    assert sample.online is False
    assert sample.presence_state is None
    assert sample.trial_id is None


@pytest.mark.parametrize(
    "properties",
    [
        pytest.param(
            {
                "presenceState": "",
                "trialId": "-",
                "programId": "-",
                "programDifficulty": -1.0,
                "trialChain": -1.0,
                "playerCount": -1.0,
            },
            id="placeholders",
        ),
        pytest.param({"programDifficulty": 9, "trialChain": 0, "playerCount": -1}, id="out-of-range"),
        pytest.param(
            {"programDifficulty": "2", "trialChain": True, "trialId": 7, "playerCount": "4"},
            id="wrong-types",
        ),
        pytest.param(
            {
                "programDifficulty": float("nan"),
                "trialChain": float("inf"),
                "playerCount": float("-inf"),
            },
            id="non-finite",
        ),
    ],
)
def test_unusable_values_degrade_to_none(properties):
    sample = parse_sample(_body(**properties), received_at=0.0)
    assert sample is not None
    for name, field in _FIELDS.items():
        if name in properties:
            assert getattr(sample, field) is None, name


def test_a_body_without_online_is_rejected():
    assert parse_sample(_body(online=None), received_at=0.0) is None
    assert parse_sample({"error": "NOT_FOUND"}, received_at=0.0) is None
    assert parse_sample([], received_at=0.0) is None
    assert parse_sample(None, received_at=0.0) is None
