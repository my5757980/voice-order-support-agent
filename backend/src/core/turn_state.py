"""The turn state machine.

Pure: no I/O, no vendor types, no direct clock reads. Every transition is explicit and
every illegal transition raises rather than being silently absorbed — a voice pipeline
that quietly accepts an impossible transition produces bugs that only appear live.

State diagram (data-model.md):

    IDLE --speech--> LISTENING --commit--> COMMITTED --dispatch--> THINKING
                        |                                             |
                        |                                       first_audio
                        |                                             v
                        |                                         SPEAKING
                        |                                          |     |
                        |                            complete <----+     +--> barge_in
                        |                                v                        |
                        +<-- truncated -- INTERRUPTED <---------------------------+
                        |
                     silence
                        v
                     CLOSING --> CLOSED
"""

from __future__ import annotations

from enum import Enum

from .clock import Clock

# Silence thresholds, in seconds since the agent stopped speaking (FR-006).
# Two re-prompts escalating in specificity, then a graceful close.
FIRST_REPROMPT_S = 6.0
SECOND_REPROMPT_S = 18.0
CLOSE_S = 30.0


class TurnState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    COMMITTED = "committed"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CLOSING = "closing"
    CLOSED = "closed"


class Trigger(str, Enum):
    SPEECH_STARTED = "speech_started"
    TURN_COMMITTED = "turn_committed"
    DISPATCHED = "dispatched"
    FIRST_AUDIO = "first_audio"
    RESPONSE_COMPLETE = "response_complete"
    BARGE_IN = "barge_in"
    TRUNCATED = "truncated"
    SILENCE_TIMEOUT = "silence_timeout"
    SESSION_END = "session_end"
    CLOSED = "closed"


class SilenceStage(str, Enum):
    NONE = "none"
    FIRST_REPROMPT = "first_reprompt"
    SECOND_REPROMPT = "second_reprompt"
    CLOSE = "close"


class IllegalTransition(RuntimeError):
    def __init__(self, state: TurnState, trigger: Trigger) -> None:
        super().__init__(f"cannot {trigger.value} from {state.value}")
        self.state = state
        self.trigger = trigger


# The complete legal transition table. Anything absent is illegal by construction,
# which is the point: adding a state means deciding its transitions explicitly.
_TRANSITIONS: dict[tuple[TurnState, Trigger], TurnState] = {
    (TurnState.IDLE, Trigger.SPEECH_STARTED): TurnState.LISTENING,
    (TurnState.IDLE, Trigger.SILENCE_TIMEOUT): TurnState.CLOSING,
    (TurnState.LISTENING, Trigger.SPEECH_STARTED): TurnState.LISTENING,
    (TurnState.LISTENING, Trigger.TURN_COMMITTED): TurnState.COMMITTED,
    (TurnState.LISTENING, Trigger.SILENCE_TIMEOUT): TurnState.CLOSING,
    (TurnState.COMMITTED, Trigger.DISPATCHED): TurnState.THINKING,
    (TurnState.THINKING, Trigger.FIRST_AUDIO): TurnState.SPEAKING,
    (TurnState.THINKING, Trigger.BARGE_IN): TurnState.INTERRUPTED,
    # A turn can finish without ever producing audio — e.g. a tool-only turn that
    # the shopper interrupted before synthesis, or an empty response.
    (TurnState.THINKING, Trigger.RESPONSE_COMPLETE): TurnState.IDLE,
    (TurnState.SPEAKING, Trigger.BARGE_IN): TurnState.INTERRUPTED,
    (TurnState.SPEAKING, Trigger.RESPONSE_COMPLETE): TurnState.IDLE,
    (TurnState.INTERRUPTED, Trigger.TRUNCATED): TurnState.LISTENING,
    (TurnState.CLOSING, Trigger.CLOSED): TurnState.CLOSED,
}

# SESSION_END is legal from any live state: the shopper can hang up mid-sentence.
_TERMINAL = {TurnState.CLOSED}


class TurnStateMachine:
    """One instance per session. Holds no conversation content — only the state."""

    def __init__(self, clock: Clock, state: TurnState = TurnState.IDLE) -> None:
        self._clock = clock
        self._state = state
        self._last_activity = clock.now()
        self._reprompts_sent = 0

    @property
    def state(self) -> TurnState:
        return self._state

    @property
    def reprompts_sent(self) -> int:
        return self._reprompts_sent

    def can(self, trigger: Trigger) -> bool:
        if trigger is Trigger.SESSION_END:
            return self._state not in _TERMINAL
        return (self._state, trigger) in _TRANSITIONS

    def fire(self, trigger: Trigger) -> TurnState:
        """Apply a trigger. Raises IllegalTransition rather than absorbing it."""
        if trigger is Trigger.SESSION_END:
            if self._state in _TERMINAL:
                raise IllegalTransition(self._state, trigger)
            self._state = TurnState.CLOSING
            return self._state

        try:
            self._state = _TRANSITIONS[(self._state, trigger)]
        except KeyError:
            raise IllegalTransition(self._state, trigger) from None

        # Any real progress resets the silence clock and the re-prompt ladder.
        if trigger is not Trigger.SILENCE_TIMEOUT:
            self._last_activity = self._clock.now()
            self._reprompts_sent = 0
        return self._state

    # -- silence handling -------------------------------------------------

    def silence_stage(self) -> SilenceStage:
        """What the session owes the shopper given how long they have been quiet.

        Only meaningful while waiting on the shopper. Mid-response silence is the
        agent's own processing time and is covered by holding phrases instead.
        """
        if self._state not in (TurnState.IDLE, TurnState.LISTENING):
            return SilenceStage.NONE

        elapsed = self._clock.now() - self._last_activity
        if elapsed >= CLOSE_S:
            return SilenceStage.CLOSE
        if elapsed >= SECOND_REPROMPT_S and self._reprompts_sent < 2:
            return SilenceStage.SECOND_REPROMPT
        if elapsed >= FIRST_REPROMPT_S and self._reprompts_sent < 1:
            return SilenceStage.FIRST_REPROMPT
        return SilenceStage.NONE

    def record_reprompt(self) -> None:
        """Mark a re-prompt as delivered so the ladder advances instead of repeating."""
        self._reprompts_sent += 1

    def touch(self) -> None:
        """Register shopper activity without changing state."""
        self._last_activity = self._clock.now()
