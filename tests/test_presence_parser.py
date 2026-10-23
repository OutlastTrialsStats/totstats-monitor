from __future__ import annotations

from presence_fixtures import line

from totstats.presence.parser import PresenceParser, parse_sample


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
    assert sample.invasion_state is None
    assert sample.received_at == 12.5


def test_placeholders_become_none():
    sample = parse_sample(
        _body(
            presenceState="",
            trialId="-",
            programId="-",
            programDifficulty=-1.0,
            trialChain=-1.0,
            playerCount=-1.0,
        ),
        received_at=0.0,
    )
    assert sample is not None
    assert sample.presence_state is None
    assert sample.trial_id is None
    assert sample.program_id is None
    assert sample.difficulty is None
    assert sample.chain_step is None
    assert sample.player_count is None


def test_a_profile_without_presence():
    body = {"applicationId": None, "online": False, "lastOnline": None, "properties": None}
    sample = parse_sample(body, received_at=0.0)
    assert sample is not None
    assert sample.online is False
    assert sample.presence_state is None
    assert sample.trial_id is None


def test_out_of_range_values_degrade_to_none():
    sample = parse_sample(
        _body(programDifficulty=9, trialChain=0, playerCount=-1, presenceState=""), received_at=0.0
    )
    assert sample is not None
    assert sample.difficulty is None
    assert sample.chain_step is None
    assert sample.player_count is None
    assert sample.presence_state is None


def test_wrong_types_degrade_to_none():
    sample = parse_sample(
        _body(programDifficulty="2", trialChain=True, trialId=7, invasionState=["x"]), received_at=0.0
    )
    assert sample is not None
    assert sample.difficulty is None
    assert sample.chain_step is None
    assert sample.trial_id is None
    assert sample.invasion_state is None


def test_non_finite_numbers_degrade_to_none():
    sample = parse_sample(
        _body(programDifficulty=float("nan"), trialChain=float("inf"), playerCount=float("-inf")),
        received_at=0.0,
    )
    assert sample is not None
    assert sample.difficulty is None
    assert sample.chain_step is None
    assert sample.player_count is None


def test_a_body_without_online_is_rejected():
    assert parse_sample(_body(online=None), received_at=0.0) is None
    assert parse_sample({"error": "NOT_FOUND"}, received_at=0.0) is None
    assert parse_sample([], received_at=0.0) is None
    assert parse_sample(None, received_at=0.0) is None


def test_the_presence_post_log_line_is_no_longer_read():
    parser = PresenceParser()
    raw = (
        "OnlineCoreHttpLogs: Verbose: Operation FCoreUpdatePresenceOperation(83) request body: "
        '{"properties":{"presenceState":"trial"}}'
    )
    assert parser.parse(line("2026.08.05-12.00.00:000", raw)) is None
    assert not any("FCoreUpdatePresenceOperation" in needle for needle in PresenceParser.INTERESTS)
