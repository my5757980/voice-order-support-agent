"""T012 — turn state machine.

Every legal transition is exercised and every illegal one must raise. A voice pipeline
that silently absorbs an impossible transition produces bugs that only surface live,
mid-conversation, in front of a judge.
"""

from __future__ import annotations

import pytest

from src.core.clock import FakeClock
from src.core.turn_state import (
    CLOSE_S,
    FIRST_REPROMPT_S,
    SECOND_REPROMPT_S,
    IllegalTransition,
    SilenceStage,
    Trigger,
    TurnState,
    TurnStateMachine,
)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sm(clock: FakeClock) -> TurnStateMachine:
    return TurnStateMachine(clock)


# -- the happy path -------------------------------------------------------


def test_full_turn_cycle(sm: TurnStateMachine) -> None:
    assert sm.state is TurnState.IDLE
    assert sm.fire(Trigger.SPEECH_STARTED) is TurnState.LISTENING
    assert sm.fire(Trigger.TURN_COMMITTED) is TurnState.COMMITTED
    assert sm.fire(Trigger.DISPATCHED) is TurnState.THINKING
    assert sm.fire(Trigger.FIRST_AUDIO) is TurnState.SPEAKING
    assert sm.fire(Trigger.RESPONSE_COMPLETE) is TurnState.IDLE


def test_barge_in_from_speaking_returns_to_listening(sm: TurnStateMachine) -> None:
    for t in (Trigger.SPEECH_STARTED, Trigger.TURN_COMMITTED, Trigger.DISPATCHED, Trigger.FIRST_AUDIO):
        sm.fire(t)
    assert sm.fire(Trigger.BARGE_IN) is TurnState.INTERRUPTED
    assert sm.fire(Trigger.TRUNCATED) is TurnState.LISTENING


def test_barge_in_before_any_audio(sm: TurnStateMachine) -> None:
    """The shopper can interrupt while the agent is still thinking — before a single
    frame has been synthesized."""
    for t in (Trigger.SPEECH_STARTED, Trigger.TURN_COMMITTED, Trigger.DISPATCHED):
        sm.fire(t)
    assert sm.state is TurnState.THINKING
    assert sm.fire(Trigger.BARGE_IN) is TurnState.INTERRUPTED


def test_turn_can_complete_without_producing_audio(sm: TurnStateMachine) -> None:
    """A tool-only turn produces no speech and must still return to IDLE."""
    for t in (Trigger.SPEECH_STARTED, Trigger.TURN_COMMITTED, Trigger.DISPATCHED):
        sm.fire(t)
    assert sm.fire(Trigger.RESPONSE_COMPLETE) is TurnState.IDLE


def test_continued_speech_stays_in_listening(sm: TurnStateMachine) -> None:
    sm.fire(Trigger.SPEECH_STARTED)
    assert sm.fire(Trigger.SPEECH_STARTED) is TurnState.LISTENING


# -- illegal transitions --------------------------------------------------


@pytest.mark.parametrize(
    ("state", "trigger"),
    [
        (TurnState.IDLE, Trigger.TURN_COMMITTED),
        (TurnState.IDLE, Trigger.BARGE_IN),
        (TurnState.IDLE, Trigger.FIRST_AUDIO),
        (TurnState.LISTENING, Trigger.DISPATCHED),
        (TurnState.LISTENING, Trigger.BARGE_IN),
        (TurnState.COMMITTED, Trigger.FIRST_AUDIO),
        (TurnState.SPEAKING, Trigger.TURN_COMMITTED),
        (TurnState.INTERRUPTED, Trigger.FIRST_AUDIO),
        (TurnState.CLOSED, Trigger.SPEECH_STARTED),
    ],
)
def test_illegal_transitions_raise(clock: FakeClock, state: TurnState, trigger: Trigger) -> None:
    machine = TurnStateMachine(clock, state=state)
    assert machine.can(trigger) is False
    with pytest.raises(IllegalTransition):
        machine.fire(trigger)


def test_illegal_transition_reports_what_it_refused(clock: FakeClock) -> None:
    machine = TurnStateMachine(clock, state=TurnState.IDLE)
    with pytest.raises(IllegalTransition) as exc:
        machine.fire(Trigger.BARGE_IN)
    assert exc.value.state is TurnState.IDLE
    assert exc.value.trigger is Trigger.BARGE_IN


# -- session end ----------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [TurnState.IDLE, TurnState.LISTENING, TurnState.THINKING, TurnState.SPEAKING],
)
def test_session_end_is_legal_from_any_live_state(clock: FakeClock, state: TurnState) -> None:
    """The shopper can close the tab mid-sentence."""
    machine = TurnStateMachine(clock, state=state)
    assert machine.fire(Trigger.SESSION_END) is TurnState.CLOSING


def test_session_end_is_illegal_once_closed(clock: FakeClock) -> None:
    machine = TurnStateMachine(clock, state=TurnState.CLOSED)
    with pytest.raises(IllegalTransition):
        machine.fire(Trigger.SESSION_END)


# -- silence ladder -------------------------------------------------------


def test_silence_ladder_escalates_once_per_stage(sm: TurnStateMachine, clock: FakeClock) -> None:
    assert sm.silence_stage() is SilenceStage.NONE

    clock.advance(FIRST_REPROMPT_S)
    assert sm.silence_stage() is SilenceStage.FIRST_REPROMPT
    sm.record_reprompt()
    # Same stage must not fire twice — re-prompts escalate, they do not repeat.
    assert sm.silence_stage() is SilenceStage.NONE

    clock.advance(SECOND_REPROMPT_S - FIRST_REPROMPT_S)
    assert sm.silence_stage() is SilenceStage.SECOND_REPROMPT
    sm.record_reprompt()
    assert sm.silence_stage() is SilenceStage.NONE

    clock.advance(CLOSE_S - SECOND_REPROMPT_S)
    assert sm.silence_stage() is SilenceStage.CLOSE


def test_activity_resets_the_silence_ladder(sm: TurnStateMachine, clock: FakeClock) -> None:
    clock.advance(FIRST_REPROMPT_S)
    assert sm.silence_stage() is SilenceStage.FIRST_REPROMPT
    sm.fire(Trigger.SPEECH_STARTED)
    assert sm.silence_stage() is SilenceStage.NONE
    assert sm.reprompts_sent == 0


def test_no_silence_prompting_while_agent_is_working(sm: TurnStateMachine, clock: FakeClock) -> None:
    """Mid-response silence is the agent's processing time, covered by holding phrases —
    re-prompting there would have the agent interrupt itself."""
    for t in (Trigger.SPEECH_STARTED, Trigger.TURN_COMMITTED, Trigger.DISPATCHED):
        sm.fire(t)
    clock.advance(CLOSE_S * 2)
    assert sm.silence_stage() is SilenceStage.NONE
