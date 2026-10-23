from __future__ import annotations

from dataclasses import replace

from presence_fixtures import (
    PROGRAM_CHAIN,
    PROGRAM_HALLOWEEN,
    PROGRAM_INVASION,
    SESSION_2210_BACK_IN_LOBBY,
    SESSION_2210_FIRST_TRIAL,
    SESSION_2210_SECOND_TRIAL,
    SESSION_2210_SECOND_TRIAL_STARTED,
    Feeder,
    line,
    lobby_map,
    main_menu_map,
    phase,
    sample,
    stage,
    trial_map,
)

from totstats.presence.catalog import Catalog, TrialInfo
from totstats.presence.render import render
from totstats.presence.state import SAMPLE_GRACE, Activity, InvasionRole

AFTER_GRACE = SAMPLE_GRACE + 1.0

CATALOG = Catalog(
    trials={
        "PSA_MT01": TrialInfo("Teach the Police Officer", location="Police Station"),
        "ORI_MT02": TrialInfo("Some Orphanage Trial", location="Orphanage"),
        "CHA_MT01": TrialInfo("Escape the Courthouse", location="Courthouse"),
        "CHA_MT02": TrialInfo("Destroy the Evidence", location="Courthouse"),
        "PSB_MT01": TrialInfo("Cancel the Autopsy", location="Police Station"),
    }
)


# --- stale trial (22.10.) -------------------------------------------------------------------


def test_the_second_trial_of_a_session_replaces_the_first():
    feeder = Feeder()
    feeder.feed(*SESSION_2210_FIRST_TRIAL[:5])
    assert feeder.state.trial_id == "PSA_MT01"
    assert feeder.state.activity is Activity.TRIAL

    feeder.feed(*SESSION_2210_FIRST_TRIAL[5:])
    assert feeder.state.activity is Activity.RETURNING
    assert feeder.state.trial_id is None
    assert feeder.state.trial_started_at is None

    feeder.feed(*SESSION_2210_BACK_IN_LOBBY)
    assert feeder.state.activity is Activity.SLEEP_ROOM
    assert feeder.state.trial_id is None
    assert feeder.state.program_id is None

    feeder.feed(*SESSION_2210_SECOND_TRIAL)
    assert feeder.state.activity is Activity.TRIAL
    assert feeder.state.trial_id == "ORI_MT02"
    assert feeder.state.program_id == PROGRAM_HALLOWEEN
    assert feeder.state.difficulty == 4
    assert feeder.state.trial_started_at is None

    feeder.feed(*SESSION_2210_SECOND_TRIAL_STARTED)
    assert feeder.state.trial_started_at is not None

    payload = render(feeder.state, CATALOG)
    assert payload is not None
    assert payload.details == "Some Orphanage Trial"


def test_a_new_trial_descriptor_wins_even_without_a_lobby_in_between():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-17.05.35:493"),
        stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01", "Hard"),
        phase("2026.10.22-17.07.21:680", "StageStarted", "StageReady"),
    )
    assert feeder.state.trial_started_at is not None

    feeder.feed(stage("2026.10.22-17.27.05:372", PROGRAM_HALLOWEEN, "ORI_MT02", "Unknown"))
    assert feeder.state.trial_id == "ORI_MT02"
    # Set outright, not kept from the previous trial.
    assert feeder.state.difficulty is None
    assert feeder.state.trial_started_at is None


def test_the_main_menu_clears_everything_about_the_trial():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-17.05.35:493"),
        stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01"),
        phase("2026.10.22-17.07.21:680", "StageStarted", "StageReady"),
        main_menu_map("2026.10.22-17.10.00:000"),
    )
    state = feeder.state
    assert state.activity is Activity.MAIN_MENU
    assert (state.trial_id, state.program_id, state.difficulty) == (None, None, None)
    assert (state.chain_step, state.invasion_role, state.trial_started_at) == (None, None, None)


# --- Escalation -----------------------------------------------------------------------------


def test_escalation_steps_come_from_the_sample():
    feeder = Feeder()
    feeder.feed(
        lobby_map("2026.10.22-18.00.00:000"),
        trial_map("2026.10.22-18.01.00:000"),
        stage("2026.10.22-18.01.01:000", PROGRAM_CHAIN, "CHA_MT01", "Normal"),
    )
    feeder.clock.advance(AFTER_GRACE)
    assert feeder.offer(sample(feeder.clock(), "trialchain", "CHA_MT01", PROGRAM_CHAIN, 2, 1))
    assert feeder.state.activity is Activity.CHAIN
    assert feeder.state.chain_step == 1

    # The next step loads the trial map again and names its trial.
    feeder.feed(
        phase("2026.10.22-18.10.00:000", "StageSuccess", "StageEnding"),
        trial_map("2026.10.22-18.10.30:000"),
        stage("2026.10.22-18.10.31:000", PROGRAM_CHAIN, "CHA_MT02", "Normal"),
    )
    assert feeder.state.activity is Activity.CHAIN
    assert feeder.state.trial_id == "CHA_MT02"
    # Same program, so the step survives until the sample catches up.
    assert feeder.state.chain_step == 1

    # Still within the grace: the sample may predate the switch.
    feeder.clock.advance(SAMPLE_GRACE / 2)
    assert not feeder.offer(sample(feeder.clock(), "trialchain", "CHA_MT01", PROGRAM_CHAIN, 2, 1))

    # After the grace a lagging sample of the previous step only refines; it never names the trial.
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "trialchain", "CHA_MT01", PROGRAM_CHAIN, 2, 1))
    assert feeder.state.trial_id == "CHA_MT02"

    assert feeder.offer(sample(feeder.clock(), "trialchain", "CHA_MT02", PROGRAM_CHAIN, 2, 2))
    assert feeder.state.chain_step == 2

    payload = render(feeder.state, CATALOG)
    assert payload is not None
    assert payload.details == "Escalation"
    assert "Destroy the Evidence" in payload.state
    assert "Step 2" in payload.state


def test_a_trial_map_load_does_not_end_a_chain():
    feeder = Feeder()
    feeder.feed(trial_map("2026.10.22-18.01.00:000"))
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "trialchain", "CHA_MT01", PROGRAM_CHAIN, 2, 3))
    assert feeder.state.activity is Activity.CHAIN

    feeder.feed(trial_map("2026.10.22-18.10.30:000"))
    assert feeder.state.activity is Activity.CHAIN
    assert feeder.state.chain_step == 3


def test_leaving_the_chain_for_another_program_drops_the_step():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-18.01.00:000"),
        stage("2026.10.22-18.01.01:000", PROGRAM_CHAIN, "CHA_MT01"),
    )
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "trialchain", "CHA_MT01", PROGRAM_CHAIN, 2, 2))
    assert feeder.state.chain_step == 2

    feeder.feed(stage("2026.10.22-18.20.00:000", PROGRAM_HALLOWEEN, "PSA_MT01"))
    assert feeder.state.chain_step is None


# --- Invasion -------------------------------------------------------------------------------


def test_from_invasion_to_a_normal_trial():
    feeder = Feeder()
    feeder.feed(
        lobby_map("2026.10.22-19.00.00:000"),
        trial_map("2026.10.22-19.01.00:000"),
        stage("2026.10.22-19.01.01:000", PROGRAM_INVASION, "PSB_MT01", "Hard"),
    )
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "invadingtrial", "PSB_MT01", PROGRAM_INVASION, 3))
    assert feeder.state.invasion_role is InvasionRole.IMPOSTER
    assert feeder.state.in_invasion

    feeder.feed(
        phase("2026.10.22-19.20.00:000", "ReturnToLobby", "StageSuccess"),
        lobby_map("2026.10.22-19.20.30:000"),
        trial_map("2026.10.22-19.25.00:000"),
        stage("2026.10.22-19.25.01:000", PROGRAM_HALLOWEEN, "PSA_MT01", "Normal"),
    )
    assert feeder.state.invasion_role is None
    assert not feeder.state.in_invasion

    # The site still reports the Invasion trial: it describes another program, so it is dropped.
    feeder.clock.advance(AFTER_GRACE)
    assert not feeder.offer(sample(feeder.clock(), "invadingtrial", "PSB_MT01", PROGRAM_INVASION, 3))
    assert feeder.state.invasion_role is None
    assert feeder.state.trial_id == "PSA_MT01"

    payload = render(feeder.state, CATALOG)
    assert payload is not None
    assert "Invasion" not in payload.state


def test_a_new_program_without_a_lobby_drops_the_role():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-19.01.00:000"),
        stage("2026.10.22-19.01.01:000", PROGRAM_INVASION, "PSB_MT01"),
    )
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "trial", "PSB_MT01", PROGRAM_INVASION, 2))
    assert feeder.state.invasion_role is InvasionRole.REAGENT

    feeder.feed(stage("2026.10.22-19.30.00:000", PROGRAM_HALLOWEEN, "PSA_MT01"))
    assert feeder.state.invasion_role is None


def test_the_role_stays_within_invasion():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-19.01.00:000"),
        stage("2026.10.22-19.01.01:000", PROGRAM_INVASION, "PSB_MT01"),
    )
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "invadingtrial", "PSB_MT01", PROGRAM_INVASION, 2))

    feeder.feed(stage("2026.10.22-19.30.00:000", PROGRAM_INVASION, "PSA_MT01"))
    assert feeder.state.invasion_role is InvasionRole.IMPOSTER


# --- late samples ---------------------------------------------------------------------------


def test_a_late_sample_after_return_to_lobby_changes_nothing():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-17.05.35:493"),
        stage("2026.10.22-17.05.36:459", PROGRAM_HALLOWEEN, "PSA_MT01"),
        phase("2026.10.22-17.07.21:680", "StageStarted", "StageReady"),
        phase("2026.10.22-17.22.52:648", "ReturnToLobby", "StageSuccess"),
    )
    returning = feeder.state
    assert returning.activity is Activity.RETURNING

    feeder.clock.advance(AFTER_GRACE)
    assert not feeder.offer(sample(feeder.clock(), "trial", "PSA_MT01", PROGRAM_HALLOWEEN, 2))
    assert feeder.state == returning
    assert not feeder.offer(sample(feeder.clock(), "returningtolobby", "PSA_MT01", PROGRAM_HALLOWEEN))
    assert feeder.state == returning

    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    # Back in the Sleep Room a trial sample is from before the map load.
    assert not feeder.offer(sample(feeder.clock(), "trial", "PSA_MT01", PROGRAM_HALLOWEEN, 2))
    assert feeder.state.activity is Activity.SLEEP_ROOM
    assert feeder.state.trial_id is None


def test_the_grace_counts_from_when_the_sample_was_current():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    event_at = feeder.clock()

    feeder.clock.advance(AFTER_GRACE + 4.0)
    # Arrived after the grace, but a cache in front of the API held it for five seconds.
    assert not feeder.offer(sample(event_at + SAMPLE_GRACE - 1.0, "findingparty"))
    assert feeder.state.activity is Activity.SLEEP_ROOM

    assert feeder.offer(sample(feeder.clock(), "findingparty"))
    assert feeder.state.activity is Activity.MATCHMAKING


def test_replayed_lines_do_not_hold_samples_back():
    feeder = Feeder()
    feeder.feed(replace(lobby_map("2026.10.22-17.23.40:033"), replay=True))
    assert feeder.state.activity is Activity.SLEEP_ROOM
    assert feeder.offer(sample(feeder.clock(), "findingparty"))


def test_offline_is_ignored_while_the_game_runs():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    assert not feeder.offer(sample(feeder.clock(), "lobby", online=False))
    assert not feeder.offer(sample(feeder.clock(), "findingparty", online=False))
    assert feeder.state.activity is Activity.SLEEP_ROOM


def test_unknown_presence_states_are_ignored():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    assert not feeder.offer(sample(feeder.clock(), "somethingnew"))
    assert not feeder.offer(sample(feeder.clock(), None))


def test_preparing_in_the_sleep_room_names_the_trial():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    assert feeder.offer(sample(feeder.clock(), "preparingtrial", "ORI_MT02", PROGRAM_HALLOWEEN, 4))
    assert feeder.state.activity is Activity.PREPARING
    assert feeder.state.trial_id == "ORI_MT02"
    assert feeder.state.difficulty == 4

    payload = render(feeder.state, CATALOG)
    assert payload is not None
    assert payload.state.startswith("Preparing")


def test_a_sample_fills_the_trial_when_the_log_has_none():
    feeder = Feeder()
    feeder.feed(trial_map("2026.10.22-17.27.04:258"))
    feeder.clock.advance(AFTER_GRACE)
    assert feeder.offer(sample(feeder.clock(), "trial", "ORI_MT02", PROGRAM_HALLOWEEN, 4))
    assert feeder.state.trial_id == "ORI_MT02"
    assert feeder.state.difficulty == 4


def test_samples_after_game_exit_are_ignored():
    feeder = Feeder()
    feeder.feed(
        lobby_map("2026.10.22-17.23.40:033"),
        main_menu_map("2026.10.22-17.58.17:991"),
    )
    feeder.feed(line("2026.10.22-18.05.28:261", "LogExit: Exiting."))
    feeder.clock.advance(AFTER_GRACE)
    assert not feeder.offer(sample(feeder.clock(), "mainmenu"))
    assert feeder.state.activity is Activity.OFFLINE


def test_a_stale_returning_sample_does_not_end_the_next_trial():
    feeder = Feeder()
    feeder.feed(*SESSION_2210_SECOND_TRIAL)
    feeder.clock.advance(AFTER_GRACE)
    assert feeder.offer(sample(feeder.clock(), "returningtolobby"))
    assert feeder.state.activity is Activity.RETURNING

    feeder.feed(*SESSION_2210_SECOND_TRIAL_STARTED)
    assert feeder.state.activity is Activity.TRIAL
    assert feeder.state.trial_started_at is not None


def test_a_cancelled_preparation_drops_the_trial_a_sample_named():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    assert feeder.offer(sample(feeder.clock(), "preparingtrial", "ORI_MT02", PROGRAM_HALLOWEEN, 4))
    assert feeder.offer(sample(feeder.clock(), "lobby"))
    state = feeder.state
    assert state.activity is Activity.SLEEP_ROOM
    assert (state.trial_id, state.program_id, state.difficulty) == (None, None, None)

    payload = render(state, CATALOG)
    assert payload is not None
    assert "Orphanage" not in (payload.large_text or "")


def test_preparing_another_trial_replaces_the_one_a_sample_named():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "preparingtrial", "ORI_MT02", PROGRAM_HALLOWEEN, 4))
    assert feeder.offer(sample(feeder.clock(), "preparingtrial", "PSA_MT01", PROGRAM_HALLOWEEN, 2))
    assert (feeder.state.trial_id, feeder.state.difficulty) == ("PSA_MT01", 2)

    assert feeder.offer(sample(feeder.clock(), "preparingtrial", "CHA_MT01", PROGRAM_CHAIN, 3))
    assert (feeder.state.trial_id, feeder.state.program_id) == ("CHA_MT01", PROGRAM_CHAIN)


def test_the_log_trial_wins_over_the_one_a_sample_named():
    feeder = Feeder()
    feeder.feed(lobby_map("2026.10.22-17.23.40:033"))
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "preparingtrial", "ORI_MT02", PROGRAM_HALLOWEEN, 4))
    feeder.feed(
        trial_map("2026.10.22-17.27.04:258"),
        stage("2026.10.22-17.27.05:372", PROGRAM_HALLOWEEN, "PSA_MT01", "Normal"),
    )
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "trial", "ORI_MT02", PROGRAM_HALLOWEEN, 4))
    assert (feeder.state.trial_id, feeder.state.difficulty) == ("PSA_MT01", 2)


def test_a_sample_of_another_invasion_round_keeps_the_role_out():
    feeder = Feeder()
    feeder.feed(
        trial_map("2026.10.22-19.01.00:000"),
        stage("2026.10.22-19.01.01:000", PROGRAM_INVASION, "PSB_MT01"),
    )
    feeder.clock.advance(AFTER_GRACE)
    feeder.offer(sample(feeder.clock(), "trial", "PSB_MT01", PROGRAM_INVASION, 2))
    assert feeder.state.invasion_role is InvasionRole.REAGENT

    feeder.offer(sample(feeder.clock(), "invadingtrial", "PSA_MT01", PROGRAM_INVASION, 2, chain_step=4))
    assert feeder.state.invasion_role is InvasionRole.REAGENT
    assert feeder.state.chain_step is None
    assert feeder.state.trial_id == "PSB_MT01"
